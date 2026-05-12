import datetime as dt
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config.settings import settings
from ..utils.date_parser import VoiceDateParse, parse_voice_date
from ..utils.emergency_guard import detect_emergency, emergency_reply
from ..utils.intent_router import detect_service, normalize_text, route_message
from ..utils.booking_requirements import (
    clean_consultation_reason,
    confirms_current_phone,
    extract_contact,
    is_physiotherapy_session,
    requests_email_contact,
)
from ..utils.confirmation import (
    cancel_selection_implies_confirmation,
    is_cancel_confirmation_no,
    is_cancel_confirmation_yes,
    is_confirmation_no,
    is_confirmation_yes,
)
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.patient_name import first_name, extract_patient_name_from_voice
from ..utils.appointment_options import (
    format_appointment_option_voice,
    pick_appointment_option,
)
from ..utils.slot_picker import pick_slot_with_index
from ..utils.voice_copy import VOICE_COPY as copy
from .natural_turn import NaturalTurnResult, maybe_handle_natural_turn
from .salon_knowledge import out_of_scope_answer
from .booking_service import (
    cancel_appointment,
    confirm_slot,
    list_future_appointments,
    parse_slot_label,
    propose_slots as booking_propose_slots,
    reschedule_appointment,
    service_duration_minutes,
)
from . import supabase_repo


CR_SLOT_PAGE_SIZE = 3
ACTIVE_STAGE_NATURAL_ROUTE_TYPES = {"faq", "human_handoff", "uncertain", "acknowledgement"}
WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]


def mask_sensitive_text(value: Any) -> str:
    text = "" if value is None else str(value)
    contact_phone, contact_email = extract_contact(text)
    normalized_for_mask = normalize_text(text)
    if (contact_email and "@" not in text) or ("arroba" in normalized_for_mask and "punto" in normalized_for_mask):
        return "[email dictado]"
    if contact_phone and not re.search(r"(?<!\w)\+?(?:\d[\s().-]?){9,15}(?!\w)", text):
        return "[telefono dictado]"
    text = re.sub(
        r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
        r"\1***@\2",
        text,
    )

    def _mask_phone(match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 9:
            return raw
        return f"***{digits[-2:]}"

    return re.sub(r"(?<!\w)\+?(?:\d[\s().-]?){9,15}(?!\w)", _mask_phone, text)


def _turn_branch(stage_before: str, stage_after: str, route_type: str) -> str:
    if stage_before and stage_before != "idle":
        return f"{stage_before}:{route_type}->{stage_after}"
    return route_type


def _log_conversationrelay_turn(
    *,
    key: str,
    user_text: str,
    normalized_text: str,
    stage_before: str,
    stage_after: str,
    intent_detected: str,
    bot_reply: str,
    ctx_before: Optional[Dict[str, Any]] = None,
) -> None:
    ctx_before = ctx_before or {}
    ctx = CTX.get(key) or {}
    contact_phone, contact_email = extract_contact(user_text)
    masked_user_text = mask_sensitive_text(user_text)
    masked_normalized_text = masked_user_text if (contact_phone or contact_email) else mask_sensitive_text(normalized_text)
    date_parse = parse_voice_date(user_text)
    parsed_date = date_parse.raw_target_date
    slot_selection = ctx.pop("_last_slot_selection", {}) if ctx else {}
    payload = {
        "call_sid": key,
        "user_text": masked_user_text,
        "normalized_text": masked_normalized_text,
        "stage_before": stage_before,
        "stage_after": stage_after,
        "intent_detected": intent_detected,
        "selected_service": ctx.get("service") or ctx_before.get("service"),
        "consultation_reason_present": bool(ctx.get("consultation_reason") or ctx_before.get("consultation_reason")),
        "patient_name_present": bool(ctx.get("patient_name") or ctx_before.get("patient_name")),
        "contact_present": bool(
            ctx.get("contact_phone")
            or ctx.get("contact_email")
            or ctx_before.get("contact_phone")
            or ctx_before.get("contact_email")
            or contact_phone
            or contact_email
        ),
        "branch": _turn_branch(stage_before, stage_after, intent_detected),
        "bot_reply": mask_sensitive_text(bot_reply),
        "voice_date_raw": masked_user_text,
        "voice_date_normalized": masked_normalized_text,
        "explicit_weekday": _weekday_label(date_parse.explicit_weekday),
        "parsed_target_date": _date_iso(parsed_date),
        "parsed_weekday": _weekday_name(parsed_date),
        "date_validation_result": date_parse.validation_result,
        "date_source": _date_source_for(date_parse, ctx, ctx_before),
        "time_preference": date_parse.time_pref or ctx.get("time_pref") or ctx_before.get("time_pref"),
        "slot_selection_raw": mask_sensitive_text(slot_selection.get("raw", "")),
        "selected_slot_index": slot_selection.get("index"),
        "selected_slot_label": slot_selection.get("label"),
    }
    logger.info("conversationrelay_turn " + " ".join(f"{key}={payload[key]!r}" for key in payload))


def _date_iso(value: Optional[dt.date]) -> Optional[str]:
    return value.isoformat() if value else None


def _weekday_name(value: Optional[dt.date]) -> Optional[str]:
    return WEEKDAY_LABELS[value.weekday()] if value else None


def _weekday_label(weekday: Optional[int]) -> Optional[str]:
    return WEEKDAY_LABELS[weekday] if weekday is not None else None


def _date_source_for(
    result: VoiceDateParse,
    ctx: Optional[Dict[str, Any]] = None,
    ctx_before: Optional[Dict[str, Any]] = None,
) -> str:
    if result.target_date or result.explicit_weekday is not None:
        return "current_turn"
    ctx = ctx or {}
    ctx_before = ctx_before or {}
    if ctx.get("date_pref") or ctx_before.get("date_pref"):
        return "stored_state"
    return "fallback"


def _date_clarification(result: VoiceDateParse) -> str:
    return copy.clarify_weekday(_weekday_label(result.explicit_weekday))


def _remember_slot_selection(key: str, raw: str, selected_index: int, selected_label: str) -> None:
    ctx = CTX.get(key) or {}
    ctx["_last_slot_selection"] = {
        "raw": raw,
        "index": selected_index + 1,
        "label": selected_label,
    }


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
    phone, _email = extract_contact(payload.get("from") or payload.get("From") or "")
    if phone:
        CTX.set_suggested_contact_phone(session.key, phone)
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
        f"call_sid={session.call_sid!r} session_id={session.session_id!r} utterance={mask_sensitive_text(utterance)!r}"
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


async def _resolve_patient_name(key: str) -> Optional[str]:
    patient = await supabase_repo.get_or_create_patient(
        clinic_id=settings.DEMO_CLINIC_ID,
        phone=key,
        name=None,
    )
    if patient.get("name"):
        CTX.set_patient_name(key, patient["name"], "supabase")
        logger.info("conversationrelay_patient_name_collected=true patient_name_source=supabase")
        return patient["name"]
    logger.info("conversationrelay_patient_name_collected=false patient_name_source=missing")
    return None


async def _store_manual_patient_name(key: str, patient_name: str) -> None:
    CTX.set_patient_name(key, patient_name, "manual")
    try:
        await supabase_repo.get_or_create_patient(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone=key,
            name=patient_name,
        )
    except Exception as exc:
        logger.warning("conversationrelay_patient_name_store_failed key_present=%s error=%r", bool(key), exc)
    logger.info("conversationrelay_patient_name_collected=true patient_name_source=manual")


def _recent_booking_reply(key: str, *, farewell: bool = False) -> str:
    ctx = CTX.get(key) or {}
    slot = ctx.get("last_confirmed_slot")
    patient_name = ctx.get("last_confirmed_patient_name")
    if slot:
        return copy.farewell_after_booking(slot, patient_name) if farewell else copy.thanks_after_booking(slot, patient_name)
    return copy.farewell_generic() if farewell else copy.thanks_generic()


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


def _is_yes(text: str) -> bool:
    return is_confirmation_yes(text)


def _is_no(text: str) -> bool:
    return is_confirmation_no(text)


async def _start_appointment_action(key: str, *, action: str) -> str:
    appointments = await list_future_appointments(patient_key=key)
    CTX.clear_flow(key)
    if not appointments:
        if action == "cancel":
            return "No encuentro citas futuras asociadas a este telefono."
        return "No encuentro citas futuras asociadas a este telefono. Si quieres, puedo ayudarte a pedir una nueva cita."

    CTX.set_pending_appointments(key, action=action, appointments=appointments)
    if len(appointments) == 1:
        appointment = appointments[0]
        CTX.set_selected_appointment(key, appointment)
        if action == "cancel":
            CTX.set_stage(key, "awaiting_cancel_confirmation")
            return f"He encontrado tu cita de {format_appointment_option_voice(appointment)}. Quieres cancelarla?"
        CTX.set_stage(key, "awaiting_reschedule_confirmation")
        return f"He encontrado tu cita de {format_appointment_option_voice(appointment)}. Quieres cambiar esa cita?"

    CTX.set_stage(key, "awaiting_cancel_selection" if action == "cancel" else "awaiting_reschedule_selection")
    action_text = "cancelar" if action == "cancel" else "cambiar"
    options = "; ".join(format_appointment_option_voice(appointment, index) for index, appointment in enumerate(appointments, start=1))
    return f"Veo varias citas asociadas a este telefono. {options}. Cual quieres {action_text}?"


def _pending_appointments(key: str, *, action: str) -> List[Dict[str, Any]]:
    ctx = CTX.get(key) or {}
    field = "pending_cancel_appointments" if action == "cancel" else "pending_reschedule_appointments"
    return list(ctx.get(field) or [])


def _selected_appointment(key: str, *, action: str) -> Optional[Dict[str, Any]]:
    ctx = CTX.get(key) or {}
    selected_id = ctx.get("selected_appointment_id")
    for appointment in _pending_appointments(key, action=action):
        if appointment.get("id") == selected_id:
            return appointment
    if selected_id:
        return supabase_repo.STORE.appointments.get(selected_id)
    return None


async def _cancel_selected_appointment(key: str, appointment: Dict[str, Any]) -> str:
    result = await cancel_appointment(appointment.get("id"))
    if not result.ok:
        CTX.clear_flow(key)
        return "No he podido cancelar la cita ahora mismo. Tu cita sigue igual."
    patient_name = (appointment.get("metadata") or {}).get("patient_name")
    first = first_name(patient_name)
    prefix = f"De acuerdo, {first}. " if first else "De acuerdo. "
    service = appointment.get("service_type") or "cita"
    when = format_appointment_option_voice(appointment)
    CTX.clear_flow(key)
    return f"{prefix}He cancelado tu cita de {service} {when.split(' el ', 1)[-1]}."


def _confirm_cancel_selected_prompt(appointment: Dict[str, Any]) -> str:
    label = format_appointment_option_voice(appointment)
    return f"Quieres cancelar la cita de {label}?"


def _ask_new_day_for_selected(key: str, appointment: Dict[str, Any]) -> str:
    CTX.set_selected_appointment(key, appointment)
    CTX.set_service(key, appointment.get("service_type"))
    CTX.set_stage(key, "awaiting_reschedule_date")
    return "Perfecto. Que nuevo dia te viene bien?"


async def _confirm_reschedule_slot(key: str, user_text: str) -> str:
    current = _current_slots(key)
    picked = pick_slot_with_index(user_text, current)
    selected = picked[1] if picked else None
    if not selected:
        ctx = CTX.get(key) or {}
        return copy.propose_slots(current, time_pref=ctx.get("time_pref"))
    _remember_slot_selection(key, user_text, picked[0], selected)
    appointment = _selected_appointment(key, action="reschedule")
    selected_dt = parse_slot_label(selected)
    if not appointment or not selected_dt:
        CTX.clear_flow(key)
        return "No he podido leer bien ese hueco. Empezamos de nuevo."
    service = appointment.get("service_type") or "sesion de fisioterapia"
    result = await reschedule_appointment(
        appointment.get("id"),
        new_start_at=selected_dt,
        new_end_at=selected_dt + dt.timedelta(minutes=service_duration_minutes(service)),
    )
    if not result.ok:
        CTX.clear_flow(key)
        return "No he podido cambiar la cita ahora mismo. Tu cita original sigue igual."
    patient_name = ((result.appointment or appointment).get("metadata") or {}).get("patient_name")
    first = first_name(patient_name)
    prefix = f"Perfecto, {first}. " if first else "Perfecto. "
    CTX.clear_flow(key)
    CTX.set_last_confirmed_slot(key, selected, service=service, patient_name=patient_name)
    return f"{prefix}He cambiado tu cita de {service} al {selected}."


def _needs_consultation_reason(ctx: Dict[str, Any], service: Optional[str]) -> bool:
    return is_physiotherapy_session(service) and not ctx.get("consultation_reason")


def _contact_prompt(key: str) -> str:
    ctx = CTX.get(key) or {}
    if ctx.get("suggested_contact_phone"):
        return copy.ask_contact_with_phone_suggestion()
    return copy.ask_contact()


def _patient_name_retry_prompt(key: str) -> str:
    ctx = CTX.get(key) or {}
    attempts = int(ctx.get("patient_name_parse_failures") or 0) + 1
    ctx["patient_name_parse_failures"] = attempts
    return copy.ask_patient_name_retry(attempts)


def _contact_retry_prompt(key: str) -> str:
    ctx = CTX.get(key) or {}
    attempts = int(ctx.get("contact_parse_failures") or 0) + 1
    ctx["contact_parse_failures"] = attempts
    return copy.ask_contact_retry(attempts)


async def _handle_user_input(key: str, user_text: str) -> str:
    stage_before = CTX.get_stage(key)
    ctx_before = dict(CTX.get(key) or {})
    route = route_message(user_text)
    reply = await _handle_user_input_core(key, user_text)
    _log_conversationrelay_turn(
        key=key,
        user_text=user_text,
        normalized_text=normalize_text(user_text),
        stage_before=stage_before,
        stage_after=CTX.get_stage(key),
        intent_detected=route["type"],
        bot_reply=reply,
        ctx_before=ctx_before,
    )
    return reply


async def _maybe_handle_active_stage_natural_turn(
    key: str,
    user_text: str,
    route_type: str,
) -> NaturalTurnResult:
    if route_type not in ACTIVE_STAGE_NATURAL_ROUTE_TYPES:
        return NaturalTurnResult(False, route_type=route_type)
    return await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)


async def _handle_user_input_core(key: str, user_text: str) -> str:
    current_stage = CTX.get_stage(key)
    emergency = detect_emergency(user_text)
    if emergency.detected:
        CTX.clear_flow(key)
        CTX.set_stage(key, "emergency_detected")
        try:
            await supabase_repo.update_conversation_session(
                key,
                channel="voice",
                external_user_id=key,
                emergency_detected=True,
                emergency_match=emergency.matched,
            )
        except Exception as exc:
            logger.warning(f"conversationrelay_emergency_session_mark_failed key={key!r} error={exc!r}")
        return emergency_reply("voice")

    route_peek = route_message(user_text)

    if current_stage == "awaiting_contact" and route_peek["type"] in {"thanks", "farewell"}:
        return copy.contact_required_before_closing()

    if route_peek["type"] == "thanks":
        return _recent_booking_reply(key)
    if route_peek["type"] == "farewell":
        return _recent_booking_reply(key, farewell=True)

    if current_stage == "completed":
        if route_peek["type"] in {"thanks", "acknowledgement"}:
            return copy.thanks_closing()
        if route_peek["type"] == "booking":
            CTX.clear_flow(key)
            service = route_peek.get("service")
            if service:
                CTX.set_service(key, service)
                if _needs_consultation_reason(CTX.get(key) or {}, service):
                    CTX.set_stage(key, "awaiting_consultation_reason")
                    return copy.ask_consultation_reason()
                patient_name = await _resolve_patient_name(key)
                if patient_name:
                    CTX.set_stage(key, "awaiting_date")
                    return copy.ask_date(service)
                CTX.set_stage(key, "awaiting_patient_name")
                return copy.ask_patient_name(service)
            CTX.set_stage(key, "awaiting_service")
            return copy.ask_service_for_booking()
        if route_peek["type"] == "reschedule":
            return await _start_appointment_action(key, action="reschedule")
        if route_peek["type"] == "cancel":
            return await _start_appointment_action(key, action="cancel")
        return copy.thanks_closing()

    if current_stage == "awaiting_cancel_confirmation":
        appointment = _selected_appointment(key, action="cancel")
        if is_cancel_confirmation_yes(user_text) and appointment:
            return await _cancel_selected_appointment(key, appointment)
        if is_cancel_confirmation_no(user_text):
            CTX.clear_flow(key)
            return "De acuerdo, mantengo tu cita como estaba."
        return "Dime si quieres cancelar esa cita."

    if current_stage == "awaiting_cancel_selection":
        if is_cancel_confirmation_no(user_text):
            CTX.clear_flow(key)
            return "De acuerdo, mantengo tu cita como estaba."
        appointment = pick_appointment_option(user_text, _pending_appointments(key, action="cancel"))
        if not appointment:
            return "No he identificado cual quieres cancelar. Dime primera, segunda o el servicio."
        CTX.set_selected_appointment(key, appointment)
        if cancel_selection_implies_confirmation(user_text):
            return await _cancel_selected_appointment(key, appointment)
        CTX.set_stage(key, "awaiting_cancel_confirmation")
        return _confirm_cancel_selected_prompt(appointment)

    if current_stage == "awaiting_reschedule_confirmation":
        appointment = _selected_appointment(key, action="reschedule")
        if _is_yes(user_text) and appointment:
            return _ask_new_day_for_selected(key, appointment)
        if _is_no(user_text):
            CTX.clear_flow(key)
            return "De acuerdo, no cambio nada."
        return "Dime si quieres cambiar esa cita."

    if current_stage == "awaiting_reschedule_selection":
        appointment = pick_appointment_option(user_text, _pending_appointments(key, action="reschedule"))
        if not appointment:
            return "No he identificado cual quieres cambiar. Dime primera, segunda o el servicio."
        return _ask_new_day_for_selected(key, appointment)

    if current_stage == "awaiting_reschedule_date":
        date_parse = parse_voice_date(user_text)
        appointment = _selected_appointment(key, action="reschedule")
        if date_parse.target_date and appointment:
            return _offer_slots(
                key,
                appointment.get("service_type") or "sesion de fisioterapia",
                date_parse.target_date,
                date_parse.time_pref,
                stage="offering_reschedule_slots",
            )
        if date_parse.validation_result == "mismatch":
            CTX.set_date(key, None)
            CTX.set_time_pref(key, None)
            return _date_clarification(date_parse)
        return copy.ask_date_retry()

    if current_stage == "offering_reschedule_slots":
        return await _confirm_reschedule_slot(key, user_text)

    if current_stage == "awaiting_service":
        service = detect_service(user_text)
        date_parse = parse_voice_date(user_text)
        parsed_date = date_parse.target_date
        time_pref = date_parse.time_pref

        if route_peek["type"] in {"faq", "human_handoff", "uncertain"}:
            natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
            if natural.handled:
                return natural.reply or copy.ask_service()

        if route_peek["type"] == "greeting":
            return copy.ask_service()
        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        if route_peek["type"] == "unsupported_service":
            return copy.unsupported_service(route_peek.get("service"))
        if route_peek["type"] == "cancel":
            return await _start_appointment_action(key, action="cancel")
        if route_peek["type"] == "reschedule":
            return await _start_appointment_action(key, action="reschedule")
        if route_peek["type"] == "booking" and not service:
            return copy.ask_service_for_booking()

        if service and date_parse.validation_result == "mismatch":
            CTX.set_service(key, service)
            CTX.set_stage(key, "awaiting_date")
            return _date_clarification(date_parse)

        if service and parsed_date:
            CTX.set_service(key, service)
            if _needs_consultation_reason(CTX.get(key) or {}, service):
                CTX.set_date(key, parsed_date)
                CTX.set_time_pref(key, time_pref)
                CTX.set_stage(key, "awaiting_consultation_reason")
                return copy.ask_consultation_reason()
            patient_name = await _resolve_patient_name(key)
            if patient_name:
                return _offer_slots(key, service, parsed_date, time_pref)
            CTX.set_date(key, parsed_date)
            CTX.set_time_pref(key, time_pref)
            CTX.set_stage(key, "awaiting_patient_name")
            return copy.ask_patient_name(service)

        if service:
            CTX.set_service(key, service)
            if _needs_consultation_reason(CTX.get(key) or {}, service):
                CTX.set_stage(key, "awaiting_consultation_reason")
                return copy.ask_consultation_reason()
            patient_name = await _resolve_patient_name(key)
            if patient_name:
                CTX.set_stage(key, "awaiting_date")
                return copy.ask_date(service)
            CTX.set_stage(key, "awaiting_patient_name")
            return copy.ask_patient_name(service)

        natural = await _maybe_handle_active_stage_natural_turn(key, user_text, route_peek["type"])
        if natural.handled:
            return natural.reply or copy.ask_service()
        return copy.ask_service_retry()

    if current_stage == "awaiting_consultation_reason":
        reason = clean_consultation_reason(user_text)
        if not reason:
            return copy.ask_consultation_reason_retry()
        CTX.set_consultation_reason(key, reason)
        ctx = CTX.get(key) or {}
        service = ctx.get("service")
        date_pref = ctx.get("date_pref")
        time_pref = ctx.get("time_pref")
        patient_name = await _resolve_patient_name(key)
        if not patient_name:
            CTX.set_stage(key, "awaiting_patient_name")
            return copy.consultation_reason_then_patient_name()
        if service and date_pref:
            return _offer_slots(key, service, date_pref, time_pref)
        CTX.set_stage(key, "awaiting_date")
        return copy.consultation_reason_then_date()

    if current_stage == "awaiting_patient_name":
        patient_name = extract_patient_name_from_voice(user_text)
        if not patient_name:
            return _patient_name_retry_prompt(key)
        await _store_manual_patient_name(key, patient_name)
        ctx_for_attempts = CTX.get(key) or {}
        ctx_for_attempts["patient_name_parse_failures"] = 0
        ctx = CTX.get(key) or {}
        service = ctx.get("service")
        date_pref = ctx.get("date_pref")
        time_pref = ctx.get("time_pref")
        if service and date_pref:
            return _offer_slots(key, service, date_pref, time_pref)
        CTX.set_stage(key, "awaiting_date")
        return copy.thanks_name_then_date(first_name(patient_name) or patient_name)

    if current_stage == "awaiting_date":
        date_parse = parse_voice_date(user_text)
        parsed_date = date_parse.target_date
        time_pref = date_parse.time_pref
        route_peek = route_message(user_text)

        if parsed_date:
            ctx = CTX.get(key) or {}
            service = ctx.get("service")
            if not service:
                CTX.set_stage(key, "awaiting_service")
                return copy.ask_service()
            if _needs_consultation_reason(ctx, service):
                CTX.set_date(key, parsed_date)
                CTX.set_time_pref(key, time_pref)
                CTX.set_stage(key, "awaiting_consultation_reason")
                return copy.ask_consultation_reason()
            return _offer_slots(key, service, parsed_date, time_pref)

        if date_parse.validation_result == "mismatch":
            CTX.set_date(key, None)
            CTX.set_time_pref(key, None)
            return _date_clarification(date_parse)

        if time_pref:
            ctx = CTX.get(key) or {}
            CTX.set_time_pref(key, time_pref)
            return copy.ask_date_retry()

        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        if route_peek["type"] == "reschedule":
            return await _start_appointment_action(key, action="reschedule")
        return copy.ask_date_retry()

    if current_stage == "offering_slots":
        current = _current_slots(key)
        picked = pick_slot_with_index(user_text, current)
        selected = picked[1] if picked else None
        date_parse = parse_voice_date(user_text)
        reparsed_date = date_parse.target_date
        reparsed_time = date_parse.time_pref
        route_peek = route_message(user_text)
        ctx = CTX.get(key) or {}

        if selected:
            service = ctx.get("service") or "sesion de fisioterapia"
            selected_dt = parse_slot_label(selected)
            if selected_dt:
                CTX.set_pending_slot(key, selected)
                CTX.set_stage(key, "awaiting_contact")
                _remember_slot_selection(key, user_text, picked[0], selected)
                return _contact_prompt(key)

        service = ctx.get("service")
        date_pref = ctx.get("date_pref")
        current_pref = ctx.get("time_pref")

        if date_parse.validation_result == "mismatch":
            CTX.set_date(key, None)
            CTX.set_time_pref(key, None)
            CTX.set_stage(key, "awaiting_date")
            return _date_clarification(date_parse)

        if reparsed_date and service:
            return _offer_slots(key, service, reparsed_date, reparsed_time or ctx.get("time_pref"))

        if reparsed_time and service and date_pref:
            return _offer_slots(key, service, date_pref, reparsed_time)

        if route_peek["type"] == "later_slots":
            if current_pref != "afternoon" and service and date_pref:
                return _offer_slots(key, service, date_pref, "afternoon")
            next_batch = CTX.next_slots(key, CR_SLOT_PAGE_SIZE)
            if next_batch:
                return copy.propose_slots(next_batch, time_pref=ctx.get("time_pref"))
            if service and date_pref:
                return _offer_slots(key, service, date_pref, "afternoon")

        if route_peek["type"] == "earlier_slots" and service and date_pref:
            return _offer_slots(key, service, date_pref, "morning")

        if route_peek["type"] == "another_day":
            CTX.set_stage(key, "awaiting_date")
            return copy.another_day()

        if route_peek["type"] == "reject_slot":
            return copy.earlier_or_later()

        natural = await _maybe_handle_active_stage_natural_turn(key, user_text, route_peek["type"])
        if natural.handled:
            return natural.reply or copy.propose_slots(current, time_pref=ctx.get("time_pref"))

        if route_peek["type"] == "more_options":
            next_batch = CTX.next_slots(key, CR_SLOT_PAGE_SIZE)
            if next_batch:
                return copy.propose_slots(next_batch, time_pref=ctx.get("time_pref"))
            return "No tengo mas huecos para ese dia. Dime otro."
        if route_peek["type"] == "reschedule":
            return await _start_appointment_action(key, action="reschedule")
        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        return copy.propose_slots(current, time_pref=ctx.get("time_pref"))

    if current_stage == "awaiting_contact":
        ctx = CTX.get(key) or {}
        phone, email = extract_contact(user_text)
        if not phone and not email and confirms_current_phone(user_text) and ctx.get("suggested_contact_phone"):
            phone = ctx.get("suggested_contact_phone")
        if not phone and not email and requests_email_contact(user_text):
            return copy.ask_contact_email()
        if not phone and not email:
            return _contact_retry_prompt(key)
        ctx["contact_parse_failures"] = 0
        CTX.set_contact(
            key,
            contact_phone=phone,
            contact_email=email,
            contact_channel_preference="phone" if phone else "email",
        )
        ctx = CTX.get(key) or {}
        selected = ctx.get("pending_slot")
        service = ctx.get("service") or "sesion de fisioterapia"
        selected_dt = parse_slot_label(selected or "")
        if not selected_dt:
            CTX.set_stage(key, "awaiting_date")
            return copy.ask_date(service)
        booking_result = await confirm_slot(
            channel="voice",
            external_user_id=key,
            service_type=service,
            start_at=selected_dt,
            patient_name=ctx.get("patient_name"),
            consultation_reason=ctx.get("consultation_reason"),
            contact_phone=ctx.get("contact_phone"),
            contact_email=ctx.get("contact_email"),
            metadata={"slot_label": selected, "transport": "conversationrelay"},
        )
        if not booking_result.ok:
            if booking_result.reason == "missing_contact":
                return _contact_retry_prompt(key)
            return "Ese hueco acaba de ocuparse. Te digo otras opciones."
        appointment = booking_result.appointment
        patient_name = (appointment or {}).get("metadata", {}).get("patient_name") or ctx.get("patient_name")
        CTX.clear_flow(key)
        CTX.set_last_confirmed_slot(key, selected, service=service, patient_name=patient_name)
        CTX.set_stage(key, "completed")
        return copy.confirm_booking(selected, service, patient_name)

    route = route_message(user_text)
    if route["type"] == "pick_slot":
        pending_cancel = _pending_appointments(key, action="cancel")
        if pending_cancel:
            appointment = pick_appointment_option(user_text, pending_cancel)
            if appointment:
                CTX.set_selected_appointment(key, appointment)
                CTX.set_stage(key, "awaiting_cancel_confirmation")
                return _confirm_cancel_selected_prompt(appointment)
        pending_reschedule = _pending_appointments(key, action="reschedule")
        if pending_reschedule:
            appointment = pick_appointment_option(user_text, pending_reschedule)
            if appointment:
                return _ask_new_day_for_selected(key, appointment)
        return "Ya no tengo activa esa seleccion. Di cancelar cita y te vuelvo a mostrar tus citas."

    if route["type"] == "greeting":
        CTX.clear_flow(key)
        CTX.set_stage(key, "awaiting_service")
        return copy.ask_service()

    if route["type"] == "booking":
        CTX.clear_flow(key)
        service = route.get("service")
        if service:
            CTX.set_service(key, service)
            if _needs_consultation_reason(CTX.get(key) or {}, service):
                CTX.set_stage(key, "awaiting_consultation_reason")
                return copy.ask_consultation_reason()
            patient_name = await _resolve_patient_name(key)
            if patient_name:
                CTX.set_stage(key, "awaiting_date")
                return copy.ask_date(service)
            CTX.set_stage(key, "awaiting_patient_name")
            return copy.ask_patient_name(service)
        CTX.set_stage(key, "awaiting_service")
        return copy.ask_service_for_booking()

    if route["type"] == "cancel":
        return await _start_appointment_action(key, action="cancel")

    if route["type"] == "reschedule":
        return await _start_appointment_action(key, action="reschedule")

    if route["type"] == "unsupported_service":
        return copy.unsupported_service(route.get("service"))

    natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
    if natural.handled:
        return natural.reply or copy.ask_service()

    return copy.ask_service_retry()


def _offer_slots(
    key: str,
    service: str,
    parsed_date: dt.date,
    time_pref: Optional[str],
    *,
    stage: str = "offering_slots",
) -> str:
    CTX.set_date(key, parsed_date)
    CTX.set_time_pref(key, time_pref)
    appointment = _selected_appointment(key, action="reschedule") if stage == "offering_reschedule_slots" else None
    CTX.set_slots(
        key,
        _build_short_slot_labels(
            service,
            parsed_date,
            time_pref,
            ignore_calendar_event_id=(appointment or {}).get("calendar_event_id"),
            original_start_at=(appointment or {}).get("start_at"),
            allow_dummy_fallback=stage != "offering_reschedule_slots",
        ),
    )
    CTX.set_stage(key, stage)
    visible_slots = CTX.next_slots(key, CR_SLOT_PAGE_SIZE)
    if not visible_slots:
        return copy.no_slots_for_day(parsed_date, time_pref=time_pref)
    return copy.propose_slots(visible_slots, time_pref=time_pref)


def _reprompt_for_stage(key: str) -> str:
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    if stage == "awaiting_cancel_confirmation":
        return "Dime si quieres cancelar esa cita."
    if stage == "awaiting_cancel_selection":
        return "Dime cual quieres cancelar, por ejemplo la primera o la segunda."
    if stage == "awaiting_cancel_date":
        return "Dime la fecha y la hora aproximada de la cita que quieres cancelar."
    if stage == "awaiting_reschedule_confirmation":
        return "Dime si quieres cambiar esa cita."
    if stage == "awaiting_reschedule_selection":
        return "Dime cual quieres cambiar, por ejemplo la primera o la segunda."
    if stage == "awaiting_reschedule_date":
        return copy.ask_date_retry()
    if stage == "awaiting_patient_name":
        return copy.ask_patient_name(ctx.get("service"))
    if stage == "awaiting_consultation_reason":
        return copy.ask_consultation_reason_retry()
    if stage == "awaiting_contact":
        return _contact_retry_prompt(key)
    if stage == "awaiting_date":
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_slots":
        current = _current_slots(key)
        if current:
            return copy.propose_slots(current, time_pref=ctx.get("time_pref"))
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_reschedule_slots":
        current = _current_slots(key)
        if current:
            return copy.propose_slots(current, time_pref=ctx.get("time_pref"))
        return "Dime otro dia y miro huecos."
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
    count: int = CR_SLOT_PAGE_SIZE,
    *,
    ignore_calendar_event_id: Optional[str] = None,
    original_start_at: Optional[Any] = None,
    allow_dummy_fallback: bool = True,
) -> List[str]:
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))
    search_count = max(count * 8, 12)
    original_start = _parse_start(original_start_at)
    skipped_original_slot_count = 0
    try:
        raw_slots = booking_propose_slots(
            preferred_dt,
            service,
            count=search_count,
            ignore_calendar_event_id=ignore_calendar_event_id,
        )
    except Exception as exc:
        logger.warning(f"conversationrelay_slot_generation_fallback service={service!r} error={exc!r}")
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
        if original_start and start.replace(tzinfo=None) == original_start:
            skipped_original_slot_count += 1
            continue
        label = _format_slot_label(start)
        if label not in labels:
            labels.append(label)
        if len(labels) >= count:
            break

    if labels:
        _log_slot_search(
            prefix="conversationrelay",
            requested_count=count,
            found_count=len(labels),
            date_pref=date_pref,
            preferred_dt=preferred_dt,
            skipped_original_slot_count=skipped_original_slot_count,
        )
        return labels
    if settings.USE_REAL_CALENDAR or not allow_dummy_fallback:
        _log_slot_search(
            prefix="conversationrelay",
            requested_count=count,
            found_count=0,
            date_pref=date_pref,
            preferred_dt=preferred_dt,
            skipped_original_slot_count=skipped_original_slot_count,
        )
        return []
    selected = [dt.datetime.combine(date_pref, slot_time) for slot_time in _fallback_slot_times(time_pref, count)]
    labels = [_format_slot_label(slot_dt) for slot_dt in selected]
    _log_slot_search(
        prefix="conversationrelay",
        requested_count=count,
        found_count=len(labels),
        date_pref=date_pref,
        preferred_dt=preferred_dt,
        skipped_original_slot_count=skipped_original_slot_count,
    )
    return labels


def _parse_start(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _log_slot_search(
    *,
    prefix: str,
    requested_count: int,
    found_count: int,
    date_pref: dt.date,
    preferred_dt: dt.datetime,
    skipped_original_slot_count: int,
) -> None:
    logger.info(
        "%s_slot_search slots_requested_count=%s slots_found_count=%s slot_search_date=%s "
        "slot_search_window_start=%s slot_search_window_end=%s busy_intervals_count=%s "
        "skipped_busy_count=%s skipped_original_slot_count=%s",
        prefix,
        requested_count,
        found_count,
        date_pref.isoformat(),
        preferred_dt.isoformat(),
        (preferred_dt + dt.timedelta(days=1)).isoformat(),
        0,
        0,
        skipped_original_slot_count,
    )


def _fallback_slot_times(time_pref: Optional[str], count: int) -> List[dt.time]:
    if time_pref == "morning":
        times = [dt.time(10, 0), dt.time(10, 15), dt.time(10, 30)]
    elif time_pref == "afternoon":
        times = [dt.time(16, 0), dt.time(16, 15), dt.time(16, 30)]
    else:
        times = [dt.time(10, 0), dt.time(10, 15), dt.time(10, 30)]
    return times[:count]


def _format_slot_label(slot_dt: dt.datetime) -> str:
    return (
        f"{WEEKDAY_LABELS[slot_dt.weekday()]} "
        f"{slot_dt.strftime('%d/%m')} a las {slot_dt.strftime('%H:%M')}"
    )
