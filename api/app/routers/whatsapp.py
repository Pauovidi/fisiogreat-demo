import datetime as dt
import urllib.parse as up
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..db.db import get_db
from ..services import supabase_repo
from ..config.settings import settings
from ..services.booking_service import (
    cancel_appointment,
    confirm_slot,
    list_future_appointments,
    parse_slot_label,
    propose_slots as booking_propose_slots,
    reschedule_appointment,
    service_duration_minutes,
)
from ..services.pelu_nlu import analyze_message
from ..utils.date_parser import parse_spanish_day, parse_time_pref
from ..utils.faq import get_faq_answer
from ..utils.intent_router import detect_service, normalize_text, route_message
from ..utils.emergency_guard import detect_emergency, emergency_reply
from ..utils.booking_requirements import clean_consultation_reason, extract_contact, is_physiotherapy_session
from ..utils.confirmation import (
    cancel_selection_implies_confirmation,
    is_cancel_confirmation_no,
    is_cancel_confirmation_yes,
    is_confirmation_no,
    is_confirmation_yes,
)
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.patient_name import first_name, parse_patient_name
from ..utils.appointment_options import (
    format_appointment_option_whatsapp,
    parse_appointment_start,
    pick_appointment_option,
)
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


def _latest_appointment_id(external_user_id: str) -> Optional[str]:
    appointments = [
        item
        for item in supabase_repo.STORE.appointments.values()
        if item.get("external_user_id") == external_user_id and item.get("status") != "cancelled"
    ]
    if not appointments:
        return None
    appointments.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return appointments[0]["id"]


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


def _build_slot_labels(
    service: str,
    date_pref: dt.date,
    time_pref: Optional[str],
    count: int = 6,
    *,
    preferred_dt: Optional[dt.datetime] = None,
    ignore_calendar_event_id: Optional[str] = None,
    allow_dummy_fallback: bool = True,
) -> List[str]:
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = preferred_dt or dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))

    try:
        raw_slots = booking_propose_slots(
            preferred_dt,
            service,
            ignore_calendar_event_id=ignore_calendar_event_id,
        )
    except Exception as exc:
        logger.warning(f"WA slot generation fallback for {service}: {exc}")
        if settings.USE_REAL_CALENDAR:
            return []
        raw_slots = []

    labels: List[str] = []
    for slot in raw_slots:
        start = slot["start"]
        if start.date() != date_pref:
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
        if settings.USE_REAL_CALENDAR or not allow_dummy_fallback:
            return []
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


def _faq_with_reengagement(key: str, faq_id: str = "hours") -> str:
    answer = get_faq_answer(faq_id, channel="whatsapp") or copy.main_menu_soft()
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}

    if stage == "awaiting_service":
        if faq_id == "services":
            return f"{answer}\n\nSi quieres reservar, dime qué servicio necesitas."
        return answer
    if stage == "awaiting_date":
        return f"{answer}\n\n{copy.ask_date(ctx.get('service'))}"
    if stage == "awaiting_patient_name":
        return f"{answer}\n\n{copy.ask_patient_name(ctx.get('service'))}"
    if stage == "awaiting_consultation_reason":
        return f"{answer}\n\n{copy.ask_consultation_reason_retry()}"
    if stage == "offering_slots":
        current = _current_slots(key)
        if current:
            return f"{answer}\n\n{copy.propose_slots(current)}"
        return f"{answer}\n\n{copy.ask_date(ctx.get('service'))}"
    return answer


def _is_state_interrupt(route_type: str) -> bool:
    return route_type in {"faq", "cancel", "reschedule", "out_of_scope", "greeting", "booking", "more_options", "unsupported_service"}


RESET_COMMANDS = {
    "reiniciar",
    "reiniciar conversacion",
    "reset",
    "resetear",
    "empezar de nuevo",
    "volver a empezar",
    "borrar conversacion",
    "limpiar conversacion",
    "cancelar flujo",
    "salir",
    "olvida lo anterior",
}

DEMO_NAME_CLEAR_COMMANDS = {
    "olvida mi nombre",
    "borrar mi nombre",
    "borrar mis datos",
    "reiniciar demo",
    "reset demo",
    "resetear demo",
    "demo reset",
}

PENDING_FLOW_STAGES = {
    "awaiting_service",
    "awaiting_consultation_reason",
    "awaiting_patient_name",
    "awaiting_date",
    "offering_slots",
}


def _is_reset_command(body: str) -> bool:
    return normalize_text(body) in RESET_COMMANDS


def _is_demo_name_clear_command(body: str) -> bool:
    return normalize_text(body) in DEMO_NAME_CLEAR_COMMANDS


def _is_pending_flow_cancel(body: str, stage: str) -> bool:
    return normalize_text(body) in {"cancelar", "anular", "salir"} and stage in PENDING_FLOW_STAGES


async def _clear_conversation_flow(key: str, *, reason: str) -> None:
    CTX.clear_flow(key)
    CTX.set_last_confirmed_slot(key, None)
    try:
        await supabase_repo.update_conversation_session(
            key,
            channel="whatsapp",
            external_user_id=key,
            stage="idle",
            reset_reason=reason,
        )
    except Exception as exc:
        logger.warning("WA reset session update failed for %s: %r", key, exc)


async def _clear_demo_patient_name(key: str) -> None:
    CTX.clear_flow(key)
    CTX.set_last_confirmed_slot(key, None)
    try:
        await supabase_repo.clear_patient_name_by_phone(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone=key,
        )
    except Exception as exc:
        logger.warning("WA demo patient name clear failed for %s: %r", key, exc)
    try:
        await supabase_repo.update_conversation_session(
            key,
            channel="whatsapp",
            external_user_id=key,
            stage="idle",
            reset_reason="demo_name_clear",
        )
    except Exception as exc:
        logger.warning("WA demo name clear session update failed for %s: %r", key, exc)


def _is_reliable_patient_name(ctx: dict) -> bool:
    return bool(ctx.get("patient_name")) and ctx.get("patient_name_source") in {"manual", "supabase"}


async def _resolve_patient_name(key: str) -> tuple[Optional[str], Optional[str]]:
    patient = await supabase_repo.get_or_create_patient(
        clinic_id=settings.DEMO_CLINIC_ID,
        phone=key,
        name=None,
    )
    if patient.get("name"):
        CTX.set_patient_name(key, patient["name"], "supabase")
        logger.info("patient_name_collected=true patient_name_source=supabase")
        return patient["name"], "supabase"
    logger.info("patient_name_collected=false patient_name_source=missing")
    return None, None


async def _ensure_patient_name_before_slots_or_confirmation(
    key: str,
    *,
    service: Optional[str],
    reason: str,
) -> Optional[str]:
    if service:
        CTX.set_service(key, service)
    ctx = CTX.get(key) or {}
    if _is_reliable_patient_name(ctx):
        return ctx["patient_name"]
    patient_name, _source = await _resolve_patient_name(key)
    if patient_name:
        return patient_name
    CTX.set_stage(key, "awaiting_patient_name")
    logger.info("patient_name_required_before_booking reason=%s has_service=%s", reason, bool(service))
    return None


def _patient_name_prompt(*, service: Optional[str], reason: str) -> str:
    if reason == "slots":
        return copy.ask_patient_name_before_slots()
    if reason == "confirmation":
        return copy.ask_patient_name_before_confirmation()
    return copy.ask_patient_name(service)


def _needs_consultation_reason(ctx: dict, service: Optional[str]) -> bool:
    return is_physiotherapy_session(service) and not ctx.get("consultation_reason")


async def _store_manual_patient_name(key: str, patient_name: str) -> None:
    CTX.set_patient_name(key, patient_name, "manual")
    try:
        await supabase_repo.get_or_create_patient(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone=key,
            name=patient_name,
        )
    except Exception as exc:
        logger.warning("patient_name_store_failed phone_present=%s error=%r", bool(key), exc)
    logger.info("patient_name_collected=true patient_name_source=manual")


def _recent_booking_reply(key: str, *, farewell: bool = False) -> str:
    ctx = CTX.get(key) or {}
    slot = ctx.get("last_confirmed_slot")
    patient_name = ctx.get("last_confirmed_patient_name")
    if slot:
        return copy.farewell_after_booking(slot, patient_name) if farewell else copy.thanks_after_booking(slot, patient_name)
    return copy.farewell_generic() if farewell else copy.thanks_generic()


def _is_yes(body: str) -> bool:
    return is_confirmation_yes(body)


def _is_no(body: str) -> bool:
    return is_confirmation_no(body)


async def _store_optional_contact_email(key: str, email: str) -> bool:
    CTX.set_contact(key, contact_email=email)
    appointment_id = _latest_appointment_id(key)
    if not appointment_id:
        return False
    appointment = await supabase_repo.get_appointment(appointment_id)
    if not appointment:
        return False
    metadata = dict(appointment.get("metadata") or {})
    metadata["contact_email"] = email
    if metadata.get("contact_phone"):
        metadata["contact_channel_preference"] = "whatsapp"
    await supabase_repo.update_appointment(appointment_id, metadata=metadata)
    return True


async def _maybe_handle_optional_contact_email(key: str, body: str) -> Optional[str]:
    _phone, email = extract_contact(body)
    if not email:
        return None
    updated_existing = await _store_optional_contact_email(key, email)
    ctx = CTX.get(key) or {}
    stage = CTX.get_stage(key)
    if updated_existing and stage in {"idle", "completed"}:
        return "Perfecto, lo dejo anotado también con ese email."
    if stage == "awaiting_date":
        return f"Perfecto, lo dejo anotado también con ese email. {copy.ask_date(ctx.get('service'))}"
    if stage == "offering_slots":
        current = _current_slots(key)
        if current:
            return f"Perfecto, lo dejo anotado también con ese email.\n\n{copy.propose_slots(current)}"
    if stage == "awaiting_patient_name":
        return f"Perfecto, lo dejo anotado también con ese email. {copy.ask_patient_name(ctx.get('service'))}"
    if stage == "awaiting_consultation_reason":
        return f"Perfecto, lo dejo anotado también con ese email. {copy.ask_consultation_reason_retry()}"
    return "Perfecto, lo dejo anotado también con ese email."


async def _start_appointment_action(key: str, *, action: str) -> str:
    appointments = await list_future_appointments(patient_key=key)
    CTX.clear_flow(key)
    if not appointments:
        if action == "cancel":
            return "No encuentro citas futuras asociadas a este WhatsApp."
        return "No encuentro citas futuras asociadas a este WhatsApp. Si quieres, puedo ayudarte a pedir una nueva cita."

    CTX.set_pending_appointments(key, action=action, appointments=appointments)
    if len(appointments) == 1:
        appointment = appointments[0]
        CTX.set_selected_appointment(key, appointment)
        if action == "cancel":
            CTX.set_stage(key, "awaiting_cancel_confirmation")
            return f"He encontrado tu cita de {format_appointment_option_whatsapp(appointment).lower()}. ¿Quieres cancelarla?"
        CTX.set_stage(key, "awaiting_reschedule_confirmation")
        return f"He encontrado tu cita de {format_appointment_option_whatsapp(appointment).lower()}. ¿Quieres cambiar esa cita?"

    CTX.set_stage(key, "awaiting_cancel_selection" if action == "cancel" else "awaiting_reschedule_selection")
    lines = ["He encontrado estas citas futuras asociadas a este WhatsApp:"]
    for index, appointment in enumerate(appointments, start=1):
        lines.append(format_appointment_option_whatsapp(appointment, index))
    question = "¿Cuál quieres cancelar?" if action == "cancel" else "¿Cuál quieres cambiar? Dime el número."
    lines.append("")
    lines.append(question)
    return "\n".join(lines)


def _pending_appointments(key: str, *, action: str) -> List[dict]:
    ctx = CTX.get(key) or {}
    field = "pending_cancel_appointments" if action == "cancel" else "pending_reschedule_appointments"
    return list(ctx.get(field) or [])


def _selected_appointment(key: str, *, action: str) -> Optional[dict]:
    ctx = CTX.get(key) or {}
    selected_id = ctx.get("selected_appointment_id")
    for appointment in _pending_appointments(key, action=action):
        if appointment.get("id") == selected_id:
            return appointment
    if selected_id:
        return supabase_repo.STORE.appointments.get(selected_id)
    return None


def _is_same_day_reschedule_request(body: str) -> bool:
    normalized = normalize_text(body)
    return any(
        phrase in normalized
        for phrase in {
            "el mismo dia",
            "mismo dia",
            "otra hora ese dia",
            "ese mismo dia",
            "mas tarde ese dia",
            "antes ese dia",
            "el mismo dia a otra hora",
        }
    )


def _parse_reschedule_target_date(body: str, appointment: dict) -> tuple[Optional[dt.date], bool]:
    if _is_same_day_reschedule_request(body):
        original_start = parse_appointment_start(appointment)
        return (original_start.date() if original_start else None), True

    normalized = normalize_text(body)
    if "semana que viene" in normalized and not any(day in normalized for day in ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]):
        today = dt.date.today()
        days_until_next_monday = (7 - today.weekday()) % 7 or 7
        return today + dt.timedelta(days=days_until_next_monday), False

    return parse_spanish_day(body), False


def _preferred_reschedule_datetime(
    target_date: dt.date,
    time_pref: Optional[str],
    body: str,
    appointment: dict,
    *,
    same_day: bool,
) -> dt.datetime:
    if same_day:
        original_start = parse_appointment_start(appointment)
        if original_start:
            normalized = normalize_text(body)
            if "antes" in normalized:
                preferred = original_start - dt.timedelta(hours=1)
            elif "mas tarde" in normalized or "tarde" in normalized:
                preferred = original_start + dt.timedelta(hours=1)
            else:
                preferred = original_start + dt.timedelta(hours=1)
            return preferred.replace(tzinfo=None)
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    return dt.datetime.combine(target_date, dt.time(preferred_hour, 0))


def _without_original_slot(labels: List[str], appointment: dict) -> List[str]:
    original_start = parse_appointment_start(appointment)
    if not original_start:
        return labels
    original_label = _format_slot_label(original_start.replace(tzinfo=None))
    return [label for label in labels if label != original_label]


def _propose_reschedule_slots(slots: List[str], *, same_day: bool) -> str:
    if not slots:
        return "Ese día no veo huecos libres. ¿Quieres que miremos otro día?"
    lines = ["Te puedo ofrecer estas opciones para ese mismo día:" if same_day else "Te puedo ofrecer estas opciones:"]
    for index, slot in enumerate(slots, start=1):
        lines.append(f"{index}. {slot}")
    lines.append("Si te encaja una, dime el número.")
    return "\n".join(lines)


async def _cancel_selected_appointment(key: str, appointment: dict) -> str:
    result = await cancel_appointment(appointment.get("id"))
    if not result.ok:
        CTX.clear_flow(key)
        return "No he podido cancelar la cita ahora mismo. Tu cita sigue igual."
    patient_name = (appointment.get("metadata") or {}).get("patient_name")
    service = (appointment.get("service_type") or "cita").lower()
    when = format_appointment_option_whatsapp(appointment).rsplit("—", 1)[-1].strip()
    CTX.clear_flow(key)
    first = first_name(patient_name)
    prefix = f"De acuerdo, {first}. " if first else "De acuerdo. "
    return f"{prefix}He cancelado tu cita de {service} del {when}."


def _confirm_cancel_selected_prompt(appointment: dict) -> str:
    label = format_appointment_option_whatsapp(appointment).lower()
    return f"¿Quieres cancelar la cita de {label}?"


def _ask_new_day_for_selected(key: str, appointment: dict) -> str:
    CTX.set_selected_appointment(key, appointment)
    CTX.set_service(key, appointment.get("service_type"))
    CTX.set_stage(key, "awaiting_reschedule_date")
    logger.info(
        "reschedule_selected_appointment_id=%s reschedule_original_start_at=%s",
        appointment.get("id"),
        appointment.get("start_at"),
    )
    return "Perfecto. ¿Qué nuevo día te viene bien?"


async def _offer_reschedule_slots(key: str, body: str) -> str:
    appointment = _selected_appointment(key, action="reschedule")
    if not appointment:
        CTX.clear_flow(key)
        return "No he podido identificar la cita que quieres cambiar. Empezamos de nuevo si te parece."
    parsed_date, same_day = _parse_reschedule_target_date(body, appointment)
    logger.info(
        "reschedule_requested_day_raw=%r reschedule_target_date=%s",
        body,
        parsed_date.isoformat() if parsed_date else None,
    )
    if not parsed_date:
        return "Dime el nuevo día, por ejemplo mañana, jueves o viernes."
    time_pref = parse_time_pref(body)
    service = appointment.get("service_type") or "sesion de fisioterapia"
    preferred_dt = _preferred_reschedule_datetime(
        parsed_date,
        time_pref,
        body,
        appointment,
        same_day=same_day,
    )
    slots = _build_slot_labels(
        service,
        parsed_date,
        time_pref,
        preferred_dt=preferred_dt,
        ignore_calendar_event_id=appointment.get("calendar_event_id"),
        allow_dummy_fallback=False,
    )
    slots = _without_original_slot(slots, appointment)
    logger.info(
        "reschedule_slots_found_count=%s reschedule_selected_appointment_id=%s",
        len(slots),
        appointment.get("id"),
    )
    CTX.set_date(key, parsed_date)
    CTX.set_time_pref(key, time_pref)
    CTX.set_service(key, service)
    CTX.set_slots(key, slots)
    if not slots:
        CTX.set_stage(key, "awaiting_reschedule_date")
        return _propose_reschedule_slots([], same_day=same_day)
    CTX.set_stage(key, "offering_reschedule_slots")
    return _propose_reschedule_slots(CTX.next_slots(key, WA_PAGE_SIZE), same_day=same_day)


async def _confirm_reschedule_slot(key: str, body: str) -> str:
    current = _current_slots(key)
    selected = pick_slot(body, current)
    if not selected:
        return copy.propose_slots(current)
    appointment = _selected_appointment(key, action="reschedule")
    selected_dt = parse_slot_label(selected)
    if not appointment or not selected_dt:
        CTX.clear_flow(key)
        return "No he podido leer bien ese hueco. Empezamos de nuevo si te parece."
    service = appointment.get("service_type") or "sesion de fisioterapia"
    result = await reschedule_appointment(
        appointment.get("id"),
        new_start_at=selected_dt,
        new_end_at=selected_dt + dt.timedelta(minutes=service_duration_minutes(service)),
    )
    if not result.ok:
        CTX.clear_flow(key)
        return "No he podido cambiar la cita ahora mismo. Tu cita original sigue igual."
    updated = result.appointment or appointment
    patient_name = (updated.get("metadata") or {}).get("patient_name")
    first = first_name(patient_name)
    prefix = f"Perfecto, {first}. " if first else "Perfecto. "
    CTX.clear_flow(key)
    CTX.set_last_confirmed_slot(key, selected, service=service, patient_name=patient_name)
    return f"{prefix}He cambiado tu cita de {service} al {selected}."


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    del db
    try:
        params = up.parse_qs((await request.body()).decode())
        body = (params.get("Body", [""])[0] or "").strip()
        wa_from = _normalize_key(params.get("From", [""])[0])
        logger.info(f"WA from {wa_from}: {body}")

        current_stage = CTX.get_stage(wa_from)
        emergency = detect_emergency(body)
        if emergency.detected:
            CTX.clear_flow(wa_from)
            CTX.set_stage(wa_from, "emergency_detected")
            try:
                await supabase_repo.update_conversation_session(
                    wa_from,
                    channel="whatsapp",
                    external_user_id=wa_from,
                    emergency_detected=True,
                    emergency_match=emergency.matched,
                )
            except Exception as exc:
                logger.warning(f"WA emergency session mark failed for {wa_from}: {exc}")
            return _twiml(emergency_reply("whatsapp"))

        if _is_reset_command(body):
            await _clear_conversation_flow(wa_from, reason="reset_command")
            return _twiml(copy.reset_done())

        if _is_demo_name_clear_command(body):
            await _clear_demo_patient_name(wa_from)
            return _twiml(copy.demo_name_cleared())

        if _is_pending_flow_cancel(body, current_stage):
            await _clear_conversation_flow(wa_from, reason="pending_flow_cancel")
            return _twiml(copy.cancel_pending_flow())

        optional_contact_reply = await _maybe_handle_optional_contact_email(wa_from, body)
        if optional_contact_reply:
            return _twiml(optional_contact_reply)

        route_early = route_message(body)
        if route_early["type"] == "thanks":
            return _twiml(_recent_booking_reply(wa_from))
        if route_early["type"] == "farewell":
            return _twiml(_recent_booking_reply(wa_from, farewell=True))

        if current_stage == "awaiting_service":
            service = detect_service(body)
            if service:
                CTX.set_service(wa_from, service)
                ctx = CTX.get(wa_from) or {}
                if _needs_consultation_reason(ctx, service):
                    CTX.set_stage(wa_from, "awaiting_consultation_reason")
                    return _twiml(copy.ask_consultation_reason())
                patient_name = await _ensure_patient_name_before_slots_or_confirmation(
                    wa_from,
                    service=service,
                    reason="service",
                )
                if not patient_name:
                    return _twiml(_patient_name_prompt(service=service, reason="service"))
                CTX.set_stage(wa_from, "awaiting_date")
                return _twiml(copy.ask_date(service))

            route_peek = route_message(body)
            if route_peek["type"] == "unsupported_service":
                return _twiml(copy.unsupported_service(route_peek.get("service")))
            if not _is_state_interrupt(route_peek["type"]):
                return _twiml(copy.ask_service_retry())

        elif current_stage == "awaiting_consultation_reason":
            reason = clean_consultation_reason(body)
            if not reason:
                return _twiml(copy.ask_consultation_reason_retry())
            CTX.set_consultation_reason(wa_from, reason)
            ctx = CTX.get(wa_from) or {}
            service = ctx.get("service")
            patient_name = await _ensure_patient_name_before_slots_or_confirmation(
                wa_from,
                service=service,
                reason="service",
            )
            if not patient_name:
                return _twiml(copy.consultation_reason_then_patient_name())
            CTX.set_stage(wa_from, "awaiting_date")
            return _twiml(copy.consultation_reason_then_date())

        elif current_stage == "awaiting_patient_name":
            route_peek = route_message(body)
            if parse_spanish_day(body):
                return _twiml(copy.ask_patient_name_before_slots())
            if route_peek["type"] == "pick_slot":
                return _twiml(copy.ask_patient_name_before_confirmation())
            if route_peek["type"] == "unsupported_service":
                return _twiml(copy.unsupported_service(route_peek.get("service")))
            patient_name = parse_patient_name(body)
            if not patient_name:
                return _twiml("No he entendido bien el nombre. ¿A qué nombre dejamos la cita?")
            await _store_manual_patient_name(wa_from, patient_name)
            CTX.set_stage(wa_from, "awaiting_date")
            return _twiml(copy.thanks_name_then_date(first_name(patient_name) or patient_name))

        elif current_stage == "awaiting_date":
            parsed_date = parse_spanish_day(body)
            time_pref = parse_time_pref(body)
            if parsed_date:
                ctx = CTX.get(wa_from) or {}
                service = ctx.get("service")
                if not service:
                    CTX.set_stage(wa_from, "awaiting_service")
                    return _twiml(copy.ask_service_retry())
                if _needs_consultation_reason(ctx, service):
                    CTX.set_stage(wa_from, "awaiting_consultation_reason")
                    return _twiml(copy.ask_consultation_reason())
                patient_name = await _ensure_patient_name_before_slots_or_confirmation(
                    wa_from,
                    service=service,
                    reason="slots",
                )
                if not patient_name:
                    return _twiml(_patient_name_prompt(service=service, reason="slots"))

                CTX.set_date(wa_from, parsed_date)
                CTX.set_time_pref(wa_from, time_pref)
                CTX.set_slots(wa_from, _build_slot_labels(service, parsed_date, time_pref))
                CTX.set_stage(wa_from, "offering_slots")
                return _twiml(copy.propose_slots(CTX.next_slots(wa_from, WA_PAGE_SIZE)))

            route_peek = route_message(body)
            if route_peek["type"] == "unsupported_service":
                return _twiml(copy.unsupported_service(route_peek.get("service")))
            if not _is_state_interrupt(route_peek["type"]):
                return _twiml(copy.ask_date_retry())

        elif current_stage == "offering_slots":
            current = _current_slots(wa_from)
            selected = pick_slot(body, current)
            if selected:
                ctx = CTX.get(wa_from) or {}
                service = ctx.get("service") or "sesion de fisioterapia"
                if _needs_consultation_reason(ctx, service):
                    CTX.set_stage(wa_from, "awaiting_consultation_reason")
                    return _twiml(copy.ask_consultation_reason())
                patient_name = await _ensure_patient_name_before_slots_or_confirmation(
                    wa_from,
                    service=service,
                    reason="confirmation",
                )
                if not patient_name:
                    return _twiml(_patient_name_prompt(service=service, reason="confirmation"))
                selected_dt = parse_slot_label(selected)
                logger.info(
                    "wa_slot_selected selected_option=%s proposed_slot_label=%s use_real_calendar=%s",
                    body,
                    selected,
                    settings.USE_REAL_CALENDAR,
                )
                if not selected_dt:
                    logger.warning("wa_slot_selected_parse_failed selected_option=%s slot_label=%s", body, selected)
                    return _twiml("No he podido leer bien ese hueco. Dime el número otra vez y lo reviso.")
                booking_result = await confirm_slot(
                    channel="whatsapp",
                    external_user_id=wa_from,
                    service_type=service,
                    start_at=selected_dt,
                    patient_name=patient_name,
                    consultation_reason=ctx.get("consultation_reason"),
                    contact_phone=wa_from,
                    contact_email=ctx.get("contact_email"),
                    metadata={"slot_label": selected},
                )
                if not booking_result.ok:
                    logger.warning(
                        "wa_booking_not_confirmed selected_option=%s proposed_slot_start=%s reason=%s use_real_calendar=%s",
                        body,
                        selected_dt.isoformat(),
                        booking_result.reason,
                        settings.USE_REAL_CALENDAR,
                    )
                    return _twiml("No he podido confirmar ese hueco ahora mismo. Dime otro día y lo reviso.")
                patient_name = (booking_result.appointment or {}).get("metadata", {}).get("patient_name") or patient_name
                CTX.clear_flow(wa_from)
                CTX.set_last_confirmed_slot(
                    wa_from,
                    selected,
                    service=service,
                    patient_name=patient_name,
                )
                return _twiml(copy.confirm_booking(selected, service, patient_name))

            route_peek = route_message(body)
            if route_peek["type"] == "unsupported_service":
                return _twiml(copy.unsupported_service(route_peek.get("service")))
            if route_peek["type"] == "more_options":
                next_batch = CTX.next_slots(wa_from, WA_PAGE_SIZE)
                if next_batch:
                    return _twiml(copy.propose_slots(next_batch))
                return _twiml("No tengo más huecos para ese día. Si quieres, dime otro y lo miro.")

            if route_peek["type"] not in {"faq", "out_of_scope", "cancel", "reschedule", "greeting", "booking"}:
                return _twiml(copy.propose_slots(current))

        elif current_stage == "awaiting_cancel_confirmation":
            appointment = _selected_appointment(wa_from, action="cancel")
            if is_cancel_confirmation_yes(body) and appointment:
                return _twiml(await _cancel_selected_appointment(wa_from, appointment))
            if is_cancel_confirmation_no(body):
                CTX.clear_flow(wa_from)
                return _twiml("De acuerdo, mantengo tu cita como estaba.")
            return _twiml("Dime si quieres cancelar esa cita, por favor.")

        elif current_stage == "awaiting_cancel_selection":
            if is_cancel_confirmation_no(body):
                CTX.clear_flow(wa_from)
                return _twiml("De acuerdo, mantengo tu cita como estaba.")
            selected_appointment = pick_appointment_option(body, _pending_appointments(wa_from, action="cancel"))
            if not selected_appointment:
                return _twiml("No he identificado cuál quieres cancelar. Dime el número de la cita.")
            CTX.set_selected_appointment(wa_from, selected_appointment)
            if cancel_selection_implies_confirmation(body):
                return _twiml(await _cancel_selected_appointment(wa_from, selected_appointment))
            CTX.set_stage(wa_from, "awaiting_cancel_confirmation")
            return _twiml(_confirm_cancel_selected_prompt(selected_appointment))

        elif current_stage == "awaiting_reschedule_confirmation":
            appointment = _selected_appointment(wa_from, action="reschedule")
            if _is_yes(body) and appointment:
                return _twiml(_ask_new_day_for_selected(wa_from, appointment))
            if _is_no(body):
                CTX.clear_flow(wa_from)
                return _twiml("De acuerdo, no cambio nada. ¿En qué puedo ayudarte?")
            return _twiml("Dime si quieres cambiar esa cita, por favor.")

        elif current_stage == "awaiting_reschedule_selection":
            selected_appointment = pick_appointment_option(body, _pending_appointments(wa_from, action="reschedule"))
            if not selected_appointment:
                return _twiml("No he identificado cuál quieres cambiar. Dime el número de la cita.")
            return _twiml(_ask_new_day_for_selected(wa_from, selected_appointment))

        elif current_stage == "awaiting_reschedule_date":
            return _twiml(await _offer_reschedule_slots(wa_from, body))

        elif current_stage == "offering_reschedule_slots":
            return _twiml(await _confirm_reschedule_slot(wa_from, body))

        route = route_message(body)
        route_type = route["type"]

        if route_type == "pick_slot":
            pending_cancel = _pending_appointments(wa_from, action="cancel")
            if pending_cancel:
                selected_appointment = pick_appointment_option(body, pending_cancel)
                if selected_appointment:
                    CTX.set_selected_appointment(wa_from, selected_appointment)
                    CTX.set_stage(wa_from, "awaiting_cancel_confirmation")
                    return _twiml(_confirm_cancel_selected_prompt(selected_appointment))
            pending_reschedule = _pending_appointments(wa_from, action="reschedule")
            if pending_reschedule:
                selected_appointment = pick_appointment_option(body, pending_reschedule)
                if selected_appointment:
                    return _twiml(_ask_new_day_for_selected(wa_from, selected_appointment))
            return _twiml("Ya no tengo activa esa selección. Dime ‘cancelar cita’ y te vuelvo a mostrar tus citas.")

        if route_type == "greeting":
            CTX.clear_flow(wa_from)
            return _twiml(copy.greet_and_offer())

        if route_type == "booking":
            CTX.clear_flow(wa_from)
            service = route.get("service")
            if service:
                CTX.set_service(wa_from, service)
                ctx = CTX.get(wa_from) or {}
                if _needs_consultation_reason(ctx, service):
                    CTX.set_stage(wa_from, "awaiting_consultation_reason")
                    return _twiml(copy.ask_consultation_reason())
                patient_name = await _ensure_patient_name_before_slots_or_confirmation(
                    wa_from,
                    service=service,
                    reason="service",
                )
                if not patient_name:
                    return _twiml(_patient_name_prompt(service=service, reason="service"))
                CTX.set_stage(wa_from, "awaiting_date")
                return _twiml(copy.ask_date(service))
            CTX.set_stage(wa_from, "awaiting_service")
            return _twiml(copy.ask_service())

        if route_type == "unsupported_service":
            return _twiml(copy.unsupported_service(route.get("service")))

        if route_type == "faq":
            return _twiml(_faq_with_reengagement(wa_from, route.get("faq_id", "hours")))

        if route_type == "more_options":
            next_batch = CTX.next_slots(wa_from, WA_PAGE_SIZE)
            if next_batch:
                return _twiml(copy.propose_slots(next_batch))
            return _twiml(copy.main_menu_soft())

        if route_type == "cancel":
            return _twiml(await _start_appointment_action(wa_from, action="cancel"))

        if route_type == "reschedule":
            return _twiml(await _start_appointment_action(wa_from, action="reschedule"))

        if route_type == "out_of_scope":
            return _twiml(copy.out_of_scope())

        nlu = await analyze_message(body)
        if nlu.get("intent") == "cancel":
            return _twiml(await _start_appointment_action(wa_from, action="cancel"))
        if nlu.get("intent") == "change":
            return _twiml(await _start_appointment_action(wa_from, action="reschedule"))

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
