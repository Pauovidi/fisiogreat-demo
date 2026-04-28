import datetime as dt
import urllib.parse as up
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..db.db import get_db
from ..services.pelu_nlu import analyze_message
from ..services.slots import BusinessRules, propose_slots
from ..utils.date_parser import parse_spanish_day, parse_time_pref
from ..utils.faq import get_faq_answer
from ..utils.intent_router import detect_service, route_message
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.slot_picker import pick_slot
from ..utils.whatsapp_copy import WA_COPY as copy


router = APIRouter(prefix="/webhook", tags=["webhooks"])

WA_PAGE_SIZE = 3
WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


def _twiml(msg: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{msg}</Message></Response>',
        media_type="application/xml",
    )


def _normalize_key(raw_from: str) -> str:
    return (raw_from or "").replace("whatsapp:", "").strip()


def _format_slot_label(slot_dt: dt.datetime) -> str:
    return f"{WEEKDAY_LABELS[slot_dt.weekday()]} {slot_dt.strftime('%d/%m')} a las {slot_dt.strftime('%H:%M')}"


def _dummy_slot_labels(date_pref: dt.date, time_pref: Optional[str], count: int) -> List[str]:
    if time_pref == "morning":
        times = [dt.time(10, 0), dt.time(11, 30), dt.time(13, 0)]
    elif time_pref == "afternoon":
        times = [dt.time(16, 0), dt.time(17, 30), dt.time(18, 30)]
    else:
        times = [dt.time(10, 30), dt.time(12, 0), dt.time(17, 0)]
    return [_format_slot_label(dt.datetime.combine(date_pref, value)) for value in times[:count]]


def _build_slot_labels(service: str, date_pref: dt.date, time_pref: Optional[str], count: int = 6) -> List[str]:
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))

    try:
        raw_slots = propose_slots(preferred_dt, service, BusinessRules())
    except Exception as exc:
        logger.warning(f"WA slot generation fallback for {service}: {exc}")
        raw_slots = []

    labels: List[str] = []
    for slot in raw_slots:
        start = slot["start"]
        if start.date() < date_pref:
            continue
        if time_pref == "morning" and start.hour >= 15:
            continue
        if time_pref == "afternoon" and start.hour < 15:
            continue
        label = _format_slot_label(start)
        if label not in labels:
            labels.append(label)
        if len(labels) >= count:
            break

    if not labels:
        return _dummy_slot_labels(date_pref, time_pref, count)
    return labels


def _current_slots(key: str, page_size: int = WA_PAGE_SIZE) -> List[str]:
    ctx = CTX.get(key) or {}
    offered_slots = ctx.get("offered_slots", [])
    offered_offset = ctx.get("offered_offset", 0)
    if not offered_slots:
        return []
    start = max(offered_offset - page_size, 0)
    current = offered_slots[start:offered_offset]
    return current or offered_slots[:page_size]


def _faq_with_reengagement(key: str) -> str:
    answer = get_faq_answer("hours", channel="whatsapp") or copy.main_menu_soft()
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}

    if stage == "awaiting_service":
        return f"{answer}\n\n{copy.ask_service()}"
    if stage == "awaiting_date":
        return f"{answer}\n\n{copy.ask_date(ctx.get('service'))}"
    if stage == "offering_slots":
        current = _current_slots(key)
        if current:
            return f"{answer}\n\n{copy.propose_slots(current)}"
        return f"{answer}\n\n{copy.ask_date(ctx.get('service'))}"
    return answer


def _is_state_interrupt(route_type: str) -> bool:
    return route_type in {"faq", "cancel", "reschedule", "out_of_scope", "greeting", "booking", "more_options"}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    del db
    try:
        params = up.parse_qs((await request.body()).decode())
        body = (params.get("Body", [""])[0] or "").strip()
        wa_from = _normalize_key(params.get("From", [""])[0])
        logger.info(f"WA from {wa_from}: {body}")

        current_stage = CTX.get_stage(wa_from)

        if current_stage == "awaiting_service":
            service = detect_service(body)
            if service:
                CTX.set_service(wa_from, service)
                CTX.set_stage(wa_from, "awaiting_date")
                return _twiml(copy.ask_date(service))

            route_peek = route_message(body)
            if not _is_state_interrupt(route_peek["type"]):
                return _twiml(copy.ask_service_retry())

        elif current_stage == "awaiting_date":
            parsed_date = parse_spanish_day(body)
            time_pref = parse_time_pref(body)
            if parsed_date:
                ctx = CTX.get(wa_from) or {}
                service = ctx.get("service")
                if not service:
                    CTX.set_stage(wa_from, "awaiting_service")
                    return _twiml(copy.ask_service_retry())

                CTX.set_date(wa_from, parsed_date)
                CTX.set_time_pref(wa_from, time_pref)
                CTX.set_slots(wa_from, _build_slot_labels(service, parsed_date, time_pref))
                CTX.set_stage(wa_from, "offering_slots")
                return _twiml(copy.propose_slots(CTX.next_slots(wa_from, WA_PAGE_SIZE)))

            route_peek = route_message(body)
            if not _is_state_interrupt(route_peek["type"]):
                return _twiml(copy.ask_date_retry())

        elif current_stage == "offering_slots":
            current = _current_slots(wa_from)
            selected = pick_slot(body, current)
            if selected:
                CTX.clear_flow(wa_from)
                return _twiml(copy.confirm_booking(selected))

            route_peek = route_message(body)
            if route_peek["type"] == "more_options":
                next_batch = CTX.next_slots(wa_from, WA_PAGE_SIZE)
                if next_batch:
                    return _twiml(copy.propose_slots(next_batch))
                return _twiml("No tengo más huecos para ese día. Si quieres, dime otro y lo miro.")

            if route_peek["type"] not in {"faq", "out_of_scope", "cancel", "reschedule", "greeting", "booking"}:
                return _twiml(copy.propose_slots(current))

        route = route_message(body)
        route_type = route["type"]

        if route_type == "greeting":
            CTX.clear_flow(wa_from)
            CTX.set_stage(wa_from, "awaiting_service")
            return _twiml(copy.greet_and_offer())

        if route_type == "booking":
            CTX.clear_flow(wa_from)
            service = route.get("service")
            if service:
                CTX.set_service(wa_from, service)
                CTX.set_stage(wa_from, "awaiting_date")
                return _twiml(copy.ask_date(service))
            CTX.set_stage(wa_from, "awaiting_service")
            return _twiml(copy.ask_service())

        if route_type == "faq":
            return _twiml(_faq_with_reengagement(wa_from))

        if route_type == "more_options":
            next_batch = CTX.next_slots(wa_from, WA_PAGE_SIZE)
            if next_batch:
                return _twiml(copy.propose_slots(next_batch))
            return _twiml(copy.main_menu_soft())

        if route_type == "cancel":
            return _twiml("Para cancelar una cita, dime la fecha y la hora aproximada y te ayudo.")

        if route_type == "reschedule":
            return _twiml("Para cambiar la cita, dime la cita actual y el nuevo día que te vendría mejor.")

        if route_type == "out_of_scope":
            return _twiml(copy.out_of_scope())

        nlu = await analyze_message(body)
        if nlu.get("intent") == "cancel":
            return _twiml("Para cancelar una cita, dime la fecha y la hora aproximada y te ayudo.")
        if nlu.get("intent") == "change":
            return _twiml("Para cambiar la cita, dime la cita actual y el nuevo día que te vendría mejor.")

        if CTX.get_stage(wa_from) == "awaiting_service":
            return _twiml(copy.ask_service_retry())
        if CTX.get_stage(wa_from) == "awaiting_date":
            ctx = CTX.get(wa_from) or {}
            return _twiml(copy.ask_date(ctx.get("service")))
        if CTX.get_stage(wa_from) == "offering_slots":
            return _twiml(copy.propose_slots(_current_slots(wa_from)))

        return _twiml(copy.main_menu_soft())

    except Exception as exc:
        logger.error(f"Error in WhatsApp webhook: {exc}")
        return _twiml(copy.technical_error())
