import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..utils.date_parser import parse_spanish_day, parse_time_pref
from ..utils.intent_router import detect_service, route_message
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.slot_picker import pick_slot
from ..utils.voice_copy import VOICE_COPY as copy
from .natural_turn import maybe_handle_natural_turn
from .salon_knowledge import out_of_scope_answer


CR_SLOT_PAGE_SIZE = 2
WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]


@dataclass
class ConversationRelaySession:
    session_id: Optional[str] = None
    call_sid: Optional[str] = None
    prompt_buffer: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_interrupt: Optional[Dict[str, Any]] = None

    @property
    def key(self) -> str:
        return self.call_sid or self.session_id or "conversationrelay"


def handle_setup_message(session: ConversationRelaySession, payload: Dict[str, Any]) -> str:
    session.session_id = payload.get("sessionId") or session.session_id
    session.call_sid = payload.get("callSid") or session.call_sid
    session.prompt_buffer.clear()
    CTX.clear_flow(session.key)
    CTX.set_stage(session.key, "awaiting_service")
    logger.info(
        "conversationrelay_setup "
        f"call_sid={session.call_sid!r} session_id={session.session_id!r}"
    )
    return copy.opening_greeting()


async def handle_prompt_message(session: ConversationRelaySession, payload: Dict[str, Any]) -> Optional[str]:
    chunk = (payload.get("voicePrompt") or "").strip()
    if chunk:
        session.prompt_buffer.append(chunk)

    if not payload.get("last", True):
        return None

    utterance = " ".join(part for part in session.prompt_buffer if part).strip()
    session.prompt_buffer.clear()
    if not utterance:
        return _reprompt_for_stage(session.key)

    logger.info(
        "conversationrelay_prompt "
        f"call_sid={session.call_sid!r} session_id={session.session_id!r} utterance={utterance!r}"
    )
    return await _handle_user_input(session.key, utterance)


async def handle_dtmf_message(session: ConversationRelaySession, payload: Dict[str, Any]) -> str:
    digit = str(payload.get("digit") or "").strip()
    logger.info(
        "conversationrelay_dtmf "
        f"call_sid={session.call_sid!r} session_id={session.session_id!r} digit={digit!r}"
    )
    if not digit:
        return _reprompt_for_stage(session.key)
    return await _handle_user_input(session.key, digit)


def handle_interrupt_message(session: ConversationRelaySession, payload: Dict[str, Any]):
    session.last_interrupt = {
        "utteranceUntilInterrupt": payload.get("utteranceUntilInterrupt"),
        "durationUntilInterruptMs": payload.get("durationUntilInterruptMs"),
    }
    logger.info(
        "conversationrelay_interrupt "
        f"call_sid={session.call_sid!r} session_id={session.session_id!r} "
        f"partial={session.last_interrupt['utteranceUntilInterrupt']!r}"
    )


async def _handle_user_input(key: str, user_text: str) -> str:
    current_stage = CTX.get_stage(key)
    route_peek = route_message(user_text)

    if current_stage == "completed":
        if route_peek["type"] in {"thanks", "acknowledgement"}:
            return copy.thanks_closing()
        if route_peek["type"] == "booking":
            CTX.clear_flow(key)
            service = route_peek.get("service")
            if service:
                CTX.set_service(key, service)
                CTX.set_stage(key, "awaiting_date")
                return copy.ask_date(service)
            CTX.set_stage(key, "awaiting_service")
            return copy.ask_service_for_booking()
        if route_peek["type"] == "reschedule":
            CTX.clear_flow(key)
            return "Claro. Dime la cita actual y el dia nuevo."
        if route_peek["type"] == "cancel":
            CTX.clear_flow(key)
            return "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada."
        return copy.thanks_closing()

    if current_stage == "awaiting_service":
        service = detect_service(user_text)
        parsed_date = parse_spanish_day(user_text)
        time_pref = parse_time_pref(user_text)

        if route_peek["type"] in {"faq", "human_handoff", "uncertain"}:
            natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
            if natural.handled:
                return natural.reply or copy.ask_service()

        if route_peek["type"] == "greeting":
            return copy.ask_service()
        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        if route_peek["type"] == "cancel":
            return "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada."
        if route_peek["type"] == "reschedule":
            return "Claro. Dime la cita actual y el dia nuevo."
        if route_peek["type"] == "booking" and not service:
            return copy.ask_service_for_booking()

        if service and parsed_date:
            CTX.set_service(key, service)
            return _offer_slots(key, service, parsed_date, time_pref)

        if service:
            CTX.set_service(key, service)
            CTX.set_stage(key, "awaiting_date")
            return copy.ask_date(service)

        natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
        if natural.handled:
            return natural.reply or copy.ask_service()
        return copy.ask_service_retry()

    if current_stage == "awaiting_date":
        parsed_date = parse_spanish_day(user_text)
        time_pref = parse_time_pref(user_text)
        route_peek = route_message(user_text)

        if parsed_date:
            ctx = CTX.get(key) or {}
            service = ctx.get("service")
            if not service:
                CTX.set_stage(key, "awaiting_service")
                return copy.ask_service()
            return _offer_slots(key, service, parsed_date, time_pref)

        if time_pref:
            ctx = CTX.get(key) or {}
            CTX.set_time_pref(key, time_pref)
            return copy.ask_date_with_time_pref(ctx.get("service"), time_pref)

        natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
        if natural.handled:
            return natural.reply or copy.ask_date()

        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        if route_peek["type"] == "reschedule":
            return "Claro. Dime el dia que te va mejor."
        if time_pref:
            return copy.ask_date_retry()
        return copy.ask_date_retry()

    if current_stage == "offering_slots":
        current = _current_slots(key)
        selected = pick_slot(user_text, current)
        reparsed_date = parse_spanish_day(user_text)
        reparsed_time = parse_time_pref(user_text)
        route_peek = route_message(user_text)

        if selected:
            CTX.clear_flow(key)
            CTX.set_last_confirmed_slot(key, selected)
            CTX.set_stage(key, "completed")
            return copy.confirm_booking(selected)

        ctx = CTX.get(key) or {}
        service = ctx.get("service")
        date_pref = ctx.get("date_pref")
        current_pref = ctx.get("time_pref")

        if reparsed_date and service:
            return _offer_slots(key, service, reparsed_date, reparsed_time or ctx.get("time_pref"))

        if reparsed_time and service and date_pref:
            return _offer_slots(key, service, date_pref, reparsed_time)

        if route_peek["type"] == "later_slots":
            if current_pref != "afternoon" and service and date_pref:
                return _offer_slots(key, service, date_pref, "afternoon")
            next_batch = CTX.next_slots(key, CR_SLOT_PAGE_SIZE)
            if next_batch:
                return copy.propose_slots(next_batch)
            if service and date_pref:
                return _offer_slots(key, service, date_pref, "afternoon")

        if route_peek["type"] == "earlier_slots" and service and date_pref:
            return _offer_slots(key, service, date_pref, "morning")

        if route_peek["type"] == "another_day":
            CTX.set_stage(key, "awaiting_date")
            return copy.another_day()

        if route_peek["type"] == "reject_slot":
            return copy.earlier_or_later()

        natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
        if natural.handled:
            return natural.reply or copy.propose_slots(current)

        if route_peek["type"] == "more_options":
            next_batch = CTX.next_slots(key, CR_SLOT_PAGE_SIZE)
            if next_batch:
                return copy.propose_slots(next_batch)
            return "No tengo mas huecos para ese dia. Dime otro."
        if route_peek["type"] == "reschedule":
            return "Vale. Dime otro dia o si prefieres manana o tarde."
        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        return copy.propose_slots(current)

    route = route_message(user_text)
    if route["type"] == "greeting":
        CTX.clear_flow(key)
        CTX.set_stage(key, "awaiting_service")
        return copy.ask_service()

    if route["type"] == "booking":
        CTX.clear_flow(key)
        service = route.get("service")
        if service:
            CTX.set_service(key, service)
            CTX.set_stage(key, "awaiting_date")
            return copy.ask_date(service)
        CTX.set_stage(key, "awaiting_service")
        return copy.ask_service_for_booking()

    natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
    if natural.handled:
        return natural.reply or copy.ask_service()

    return copy.ask_service_retry()


def _offer_slots(key: str, service: str, parsed_date: dt.date, time_pref: Optional[str]) -> str:
    CTX.set_date(key, parsed_date)
    CTX.set_time_pref(key, time_pref)
    CTX.set_slots(key, _build_short_slot_labels(service, parsed_date, time_pref))
    CTX.set_stage(key, "offering_slots")
    return copy.propose_slots(CTX.next_slots(key, CR_SLOT_PAGE_SIZE))


def _reprompt_for_stage(key: str) -> str:
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    if stage == "awaiting_date":
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_slots":
        current = _current_slots(key)
        if current:
            return copy.propose_slots(current)
        return copy.ask_date(ctx.get("service"))
    return copy.ask_service_retry()


def _current_slots(key: str, page_size: int = CR_SLOT_PAGE_SIZE) -> List[str]:
    ctx = CTX.get(key) or {}
    offered_slots = ctx.get("offered_slots", [])
    offered_offset = ctx.get("offered_offset", 0)
    if not offered_slots:
        return []
    start = max(offered_offset - page_size, 0)
    current = offered_slots[start:offered_offset]
    return current or offered_slots[:page_size]


def _build_short_slot_labels(
    service: str,
    date_pref: dt.date,
    time_pref: Optional[str],
    count: int = 4,
) -> List[str]:
    del service
    selected = [
        dt.datetime.combine(date_pref, slot_time)
        for slot_time in _fallback_slot_times(time_pref, count)
    ]
    return [_format_slot_label(slot_dt) for slot_dt in selected]


def _fallback_slot_times(time_pref: Optional[str], count: int) -> List[dt.time]:
    if time_pref == "morning":
        times = [dt.time(10, 0), dt.time(11, 30)]
    elif time_pref == "afternoon":
        times = [dt.time(16, 0), dt.time(17, 0)]
    else:
        times = [dt.time(10, 0), dt.time(17, 0)]
    return times[:count]


def _format_slot_label(slot_dt: dt.datetime) -> str:
    return (
        f"{WEEKDAY_LABELS[slot_dt.weekday()]} "
        f"{slot_dt.strftime('%d/%m')} a las {slot_dt.strftime('%H:%M')}"
    )
