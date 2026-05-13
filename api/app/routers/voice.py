import datetime as dt
import json
import time
import unicodedata
from html import escape
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from ..config.settings import settings
from ..services.conversation_relay import (
    ConversationRelaySession,
    handle_dtmf_message,
    handle_interrupt_message,
    handle_prompt_message,
    handle_setup_message,
    mask_sensitive_text,
)
from ..services import supabase_repo
from ..services.booking_service import (
    cancel_appointment,
    confirm_slot,
    list_future_appointments,
    parse_slot_label,
    propose_slots as booking_propose_slots,
    reschedule_appointment,
    service_duration_minutes,
)
from ..services.natural_turn import maybe_handle_natural_turn
from ..services.salon_knowledge import out_of_scope_answer
from ..utils.date_parser import VoiceDateParse, parse_voice_date
from ..utils.intent_router import detect_service, route_message
from ..utils.emergency_guard import detect_emergency, emergency_reply
from ..utils.booking_requirements import (
    clean_consultation_reason,
    confirms_current_phone,
    extract_contact,
    extract_email,
    extract_phone,
    is_physiotherapy_session,
    requests_email_contact,
    requests_phone_contact,
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


router = APIRouter(prefix="/webhook/voice", tags=["voice"])

VOICE_PAGE_SIZE = 3
VOICE_HINTS = (
    "fisioterapia, fisio, primera visita, valoracion inicial, valoracion, seguimiento, sesion, "
    "lunes, martes, miércoles, miercoles, jueves, viernes, sábado, sabado, domingo, "
    "mañana, manana, pasado mañana, pasado manana, tarde, primera, segunda, tercera, uno, dos, tres, "
    "correo, correo electrónico, email, arroba, punto, gmail, hotmail, outlook, com, es, "
    "teléfono, telefono, móvil, movil, número, numero, más, mas, seis, siete, ocho, nueve, cero"
)
WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]
VOICE_CALL_STATE: Dict[str, Dict[str, float]] = {}
TTS_CACHE_TTL_SECONDS = 1800
TTS_CACHE_MAX_ITEMS = 64
TTS_CACHE: Dict[str, Dict[str, Any]] = {}


def _twiml(body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
        media_type="application/xml",
    )


def _say(text: str) -> str:
    return f'<Say language="es-ES" voice="Polly.Conchita">{text}</Say>'


def _normalize_voice_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def _looks_like_lavado_only(text: str) -> bool:
    normalized = _normalize_voice_text(text)
    return "lavado" in normalized and "corte" not in normalized


def _needs_consultation_reason(ctx: Dict[str, Any], service: Optional[str]) -> bool:
    return is_physiotherapy_session(service) and not ctx.get("consultation_reason")


def _contact_prompt_for_call(call_sid: str, from_value: Optional[str]) -> str:
    ctx = CTX.get(call_sid) or {}
    phone, _email = extract_contact(from_value or "")
    if phone:
        CTX.set_suggested_contact_phone(call_sid, phone)
        return copy.ask_contact_with_phone_suggestion()
    if ctx.get("suggested_contact_phone"):
        return copy.ask_contact_with_phone_suggestion()
    return copy.ask_contact()


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


async def _start_appointment_action(call_sid: str, *, action: str, stats: Dict[str, Any]) -> Response:
    appointments = await list_future_appointments(patient_key=call_sid)
    CTX.clear_flow(call_sid)
    if not appointments:
        stats["stage_after"] = "idle"
        if action == "cancel":
            return _respond_gather(call_sid, "No encuentro citas futuras asociadas a este teléfono.", stats)
        return _respond_gather(call_sid, "No encuentro citas futuras asociadas a este teléfono. Si quieres, puedo ayudarte a pedir una nueva cita.", stats)

    CTX.set_pending_appointments(call_sid, action=action, appointments=appointments)
    if len(appointments) == 1:
        appointment = appointments[0]
        CTX.set_selected_appointment(call_sid, appointment)
        if action == "cancel":
            CTX.set_stage(call_sid, "awaiting_cancel_confirmation")
            stats["stage_after"] = "awaiting_cancel_confirmation"
            return _respond_gather(call_sid, f"He encontrado tu cita de {format_appointment_option_voice(appointment)}. ¿Quieres cancelarla?", stats)
        CTX.set_stage(call_sid, "awaiting_reschedule_confirmation")
        stats["stage_after"] = "awaiting_reschedule_confirmation"
        return _respond_gather(call_sid, f"He encontrado tu cita de {format_appointment_option_voice(appointment)}. ¿Quieres cambiar esa cita?", stats)

    CTX.set_stage(call_sid, "awaiting_cancel_selection" if action == "cancel" else "awaiting_reschedule_selection")
    stats["stage_after"] = CTX.get_stage(call_sid)
    action_text = "cancelar" if action == "cancel" else "cambiar"
    options = "; ".join(format_appointment_option_voice(appointment, index) for index, appointment in enumerate(appointments, start=1))
    return _respond_gather(call_sid, f"Veo varias citas asociadas a este teléfono. {options}. ¿Cuál quieres {action_text}?", stats)


def _pending_appointments(call_sid: str, *, action: str) -> List[Dict[str, Any]]:
    ctx = CTX.get(call_sid) or {}
    field = "pending_cancel_appointments" if action == "cancel" else "pending_reschedule_appointments"
    return list(ctx.get(field) or [])


def _selected_appointment(call_sid: str, *, action: str) -> Optional[Dict[str, Any]]:
    ctx = CTX.get(call_sid) or {}
    selected_id = ctx.get("selected_appointment_id")
    for appointment in _pending_appointments(call_sid, action=action):
        if appointment.get("id") == selected_id:
            return appointment
    if selected_id:
        return supabase_repo.STORE.appointments.get(selected_id)
    return None


async def _cancel_selected_appointment(call_sid: str, appointment: Dict[str, Any], stats: Dict[str, Any]) -> Response:
    result = await cancel_appointment(appointment.get("id"))
    if not result.ok:
        CTX.clear_flow(call_sid)
        stats["stage_after"] = "idle"
        return _respond_gather(call_sid, "No he podido cancelar la cita ahora mismo. Tu cita sigue igual.", stats)
    patient_name = (appointment.get("metadata") or {}).get("patient_name")
    first = first_name(patient_name)
    prefix = f"De acuerdo, {first}. " if first else "De acuerdo. "
    service = copy._display_service(appointment.get("service_type") or "cita")
    when = format_appointment_option_voice(appointment)
    CTX.clear_flow(call_sid)
    stats["stage_after"] = "idle"
    return _respond_gather(call_sid, f"{prefix}He cancelado tu cita de {service} {when.split(' el ', 1)[-1]}.", stats)


def _confirm_cancel_selected_prompt(appointment: Dict[str, Any]) -> str:
    label = format_appointment_option_voice(appointment)
    return f"¿Quieres cancelar la cita de {label}?"


def _ask_new_day_for_selected(call_sid: str, appointment: Dict[str, Any], stats: Dict[str, Any]) -> Response:
    CTX.set_selected_appointment(call_sid, appointment)
    CTX.set_service(call_sid, appointment.get("service_type"))
    CTX.set_stage(call_sid, "awaiting_reschedule_date")
    stats["stage_after"] = "awaiting_reschedule_date"
    return _respond_gather(call_sid, "Perfecto. ¿Qué nuevo día te viene bien?", stats)


def _offer_reschedule_slots(call_sid: str, service: str, parsed_date: dt.date, time_pref: Optional[str], stats: Dict[str, Any]) -> Response:
    slot_start = time.perf_counter()
    appointment = _selected_appointment(call_sid, action="reschedule")
    CTX.set_date(call_sid, parsed_date)
    CTX.set_time_pref(call_sid, time_pref)
    CTX.set_service(call_sid, service)
    CTX.set_slots(
        call_sid,
        _build_slot_labels(
            service,
            parsed_date,
            time_pref,
            ignore_calendar_event_id=(appointment or {}).get("calendar_event_id"),
            original_start_at=(appointment or {}).get("start_at"),
            allow_dummy_fallback=False,
        ),
    )
    CTX.set_stage(call_sid, "offering_reschedule_slots")
    stats["slot_ms"] += (time.perf_counter() - slot_start) * 1000
    stats["stage_after"] = "offering_reschedule_slots"
    visible_slots = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
    if not visible_slots:
        return _respond_gather(call_sid, copy.no_slots_for_day(parsed_date, time_pref=time_pref), stats)
    return _respond_gather(call_sid, copy.propose_slots(visible_slots, time_pref=time_pref), stats)


async def _confirm_reschedule_slot(call_sid: str, user_text: str, stats: Dict[str, Any]) -> Response:
    current = _current_slots(call_sid)
    picked = pick_slot_with_index(user_text, current)
    selected = picked[1] if picked else None
    if not selected:
        stats["stage_after"] = "offering_reschedule_slots"
        ctx = CTX.get(call_sid) or {}
        return _respond_gather(call_sid, copy.propose_slots(current, time_pref=ctx.get("time_pref")), stats)
    _record_slot_selection(stats, user_text, picked[0], selected)
    appointment = _selected_appointment(call_sid, action="reschedule")
    selected_dt = parse_slot_label(selected)
    if not appointment or not selected_dt:
        CTX.clear_flow(call_sid)
        stats["stage_after"] = "idle"
        return _respond_gather(call_sid, "No he podido leer bien ese hueco. Empezamos de nuevo.", stats)
    service = appointment.get("service_type") or "sesion de fisioterapia"
    result = await reschedule_appointment(
        appointment.get("id"),
        new_start_at=selected_dt,
        new_end_at=selected_dt + dt.timedelta(minutes=service_duration_minutes(service)),
    )
    if not result.ok:
        CTX.clear_flow(call_sid)
        stats["stage_after"] = "idle"
        return _respond_gather(call_sid, "No he podido cambiar la cita ahora mismo. Tu cita original sigue igual.", stats)
    patient_name = ((result.appointment or appointment).get("metadata") or {}).get("patient_name")
    first = first_name(patient_name)
    prefix = f"Perfecto, {first}. " if first else "Perfecto. "
    CTX.clear_flow(call_sid)
    CTX.set_last_confirmed_slot(call_sid, selected, service=service, patient_name=patient_name)
    stats["stage_after"] = "idle"
    return _respond_gather(call_sid, f"{prefix}He cambiado tu cita de {copy._display_service(service)} al {selected}.", stats)


async def _resolve_patient_name(key: str) -> Optional[str]:
    patient = await supabase_repo.get_or_create_patient(
        clinic_id=settings.DEMO_CLINIC_ID,
        phone=key,
        name=None,
    )
    if patient.get("name"):
        CTX.set_patient_name(key, patient["name"], "supabase")
        logger.info("voice_patient_name_collected=true patient_name_source=supabase")
        return patient["name"]
    logger.info("voice_patient_name_collected=false patient_name_source=missing")
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
        logger.warning("voice_patient_name_store_failed key_present=%s error=%r", bool(key), exc)
    logger.info("voice_patient_name_collected=true patient_name_source=manual")


def _recent_booking_reply(key: str, *, farewell: bool = False) -> str:
    ctx = CTX.get(key) or {}
    slot = ctx.get("last_confirmed_slot")
    patient_name = ctx.get("last_confirmed_patient_name")
    if slot:
        return copy.farewell_after_booking(slot, patient_name) if farewell else copy.thanks_after_booking(slot, patient_name)
    return copy.farewell_generic() if farewell else copy.thanks_generic()


def _mark_prompt(call_sid: str):
    VOICE_CALL_STATE.setdefault(call_sid, {})["last_prompt_at"] = time.perf_counter()


def _since_last_prompt_ms(call_sid: str) -> Optional[float]:
    last_prompt_at = VOICE_CALL_STATE.get(call_sid, {}).get("last_prompt_at")
    if last_prompt_at is None:
        return None
    return round((time.perf_counter() - last_prompt_at) * 1000, 1)


def _log_turn(call_sid: str, stats: Dict[str, Any]):
    ctx_before = stats.get("ctx_before") or {}
    ctx = CTX.get(call_sid) or {}
    contact_phone, contact_email = extract_contact(stats.get("raw", ""))
    masked_user_text = mask_sensitive_text(stats.get("raw", ""))
    masked_normalized_text = (
        masked_user_text
        if (contact_phone or contact_email)
        else mask_sensitive_text(stats.get("normalized", ""))
    )
    payload = {
        "call_sid": call_sid,
        "user_text": masked_user_text,
        "normalized_text": masked_normalized_text,
        "stage_before": stats.get("stage_before"),
        "stage_after": stats.get("stage_after"),
        "intent_detected": stats.get("intent_detected"),
        "selected_service": ctx.get("service") or ctx_before.get("service"),
        "consultation_reason_present": bool(ctx.get("consultation_reason") or ctx_before.get("consultation_reason")),
        "patient_name_present": bool(ctx.get("patient_name") or ctx_before.get("patient_name")),
        "contact_present": bool(
            stats.get("contact_present")
            or ctx.get("contact_phone")
            or ctx.get("contact_email")
            or ctx_before.get("contact_phone")
            or ctx_before.get("contact_email")
        ),
        "branch": stats.get("branch"),
        "bot_reply": mask_sensitive_text(stats.get("bot_reply", "")),
        "speech_present": stats.get("speech_present", False),
        "speech_available_ms": round(stats.get("speech_available_ms", 0.0), 1),
        "turn_gap_ms": stats.get("turn_gap_ms"),
        "intent_ms": round(stats.get("intent_ms", 0.0), 1),
        "slot_ms": round(stats.get("slot_ms", 0.0), 1),
        "tts_mode": stats.get("tts_mode", "say"),
        "twiml_ms": round(stats.get("twiml_ms", 0.0), 1),
        "total_ms": round(stats.get("total_ms", 0.0), 1),
        "voice_date_raw": mask_sensitive_text(stats.get("voice_date_raw", stats.get("raw", ""))),
        "voice_date_normalized": mask_sensitive_text(stats.get("voice_date_normalized", stats.get("normalized", ""))),
        "explicit_weekday": stats.get("explicit_weekday"),
        "parsed_target_date": stats.get("parsed_target_date"),
        "parsed_weekday": stats.get("parsed_weekday"),
        "date_validation_result": stats.get("date_validation_result"),
        "date_source": stats.get("date_source"),
        "time_preference": stats.get("time_preference"),
        "slot_selection_raw": mask_sensitive_text(stats.get("slot_selection_raw", "")),
        "selected_slot_index": stats.get("selected_slot_index"),
        "selected_slot_label": stats.get("selected_slot_label"),
        "selected_slot_start": stats.get("selected_slot_start"),
        "selected_slot_end": stats.get("selected_slot_end"),
        "selected_slot_preserved": stats.get("selected_slot_preserved"),
        "contact_stage": stats.get("contact_stage"),
        "contact_parse_attempt": stats.get("contact_parse_attempt"),
        "contact_parse_result": stats.get("contact_parse_result"),
        "contact_stage_before": stats.get("contact_stage_before"),
        "contact_stage_after": stats.get("contact_stage_after"),
        "selected_slot_present": stats.get("selected_slot_present"),
        "contact_present_before_confirm": stats.get("contact_present_before_confirm"),
        "confirm_attempt_after_contact": stats.get("confirm_attempt_after_contact"),
        "confirm_slot_revalidation_result": stats.get("confirm_slot_revalidation_result"),
        "slot_reoffer_reason": stats.get("slot_reoffer_reason"),
        "reoffer_due_to_slot_taken": stats.get("reoffer_due_to_slot_taken"),
        "failed_slot_start": stats.get("failed_slot_start"),
        "excluded_failed_slot": stats.get("excluded_failed_slot"),
        "reoffer_slots_count": stats.get("reoffer_slots_count"),
        "contact_preserved_after_reoffer": stats.get("contact_preserved_after_reoffer"),
        "selected_slot_after_reoffer": stats.get("selected_slot_after_reoffer"),
    }
    logger.info(
        "voice_turn "
        + " ".join(f"{key}={payload[key]!r}" for key in payload)
    )


def _respond_gather(call_sid: str, prompt: str, stats: Dict[str, Any]) -> Response:
    twiml_start = time.perf_counter()
    if stats.get("contact_parse_attempt") and not stats.get("contact_stage_after"):
        stats["contact_stage_after"] = CTX.get_stage(call_sid)
    body = (
        f'<Gather input="speech dtmf" action="/webhook/voice/agent" actionOnEmptyResult="true" '
        f'method="POST" language="es-ES" timeout="3" speechTimeout="auto" hints="{VOICE_HINTS}">'
        f"{_say(prompt)}</Gather>"
    )
    response = _twiml(body)
    stats["bot_reply"] = prompt
    stats["tts_mode"] = "say"
    stats["twiml_ms"] = (time.perf_counter() - twiml_start) * 1000
    stats["total_ms"] = (time.perf_counter() - stats["start"]) * 1000
    _log_turn(call_sid, stats)
    _mark_prompt(call_sid)
    return response


def _format_slot_label(slot_dt: dt.datetime) -> str:
    return f"{WEEKDAY_LABELS[slot_dt.weekday()]} {slot_dt.strftime('%d/%m')} a las {slot_dt.strftime('%H:%M')}"


def _dummy_slot_labels(date_pref: dt.date, time_pref: Optional[str], count: int) -> List[str]:
    if time_pref == "morning":
        times = [dt.time(10, 0), dt.time(10, 15), dt.time(10, 30)]
    elif time_pref == "afternoon":
        times = [dt.time(16, 0), dt.time(16, 15), dt.time(16, 30)]
    else:
        times = [dt.time(10, 0), dt.time(10, 15), dt.time(10, 30)]
    return [_format_slot_label(dt.datetime.combine(date_pref, value)) for value in times[:count]]


def _record_voice_date_parse(
    stats: Dict[str, Any],
    raw: str,
    result: VoiceDateParse,
    *,
    source: str,
) -> None:
    parsed_date = result.raw_target_date
    stats["voice_date_raw"] = raw
    stats["voice_date_normalized"] = _normalize_voice_text(raw)
    stats["explicit_weekday"] = _weekday_label(result.explicit_weekday)
    stats["parsed_target_date"] = parsed_date.isoformat() if parsed_date else None
    stats["parsed_weekday"] = WEEKDAY_LABELS[parsed_date.weekday()] if parsed_date else None
    stats["date_validation_result"] = result.validation_result
    stats["date_source"] = source
    stats["time_preference"] = result.time_pref


def _weekday_label(weekday: Optional[int]) -> Optional[str]:
    return WEEKDAY_LABELS[weekday] if weekday is not None else None


def _date_source_for(result: VoiceDateParse) -> str:
    if result.target_date or result.explicit_weekday is not None:
        return "current_turn"
    return "fallback"


def _date_clarification(result: VoiceDateParse) -> str:
    return copy.clarify_weekday(_weekday_label(result.explicit_weekday))


def _record_slot_selection(stats: Dict[str, Any], raw: str, selected_index: int, selected_label: str) -> None:
    stats["slot_selection_raw"] = raw
    stats["selected_slot_index"] = selected_index + 1
    stats["selected_slot_label"] = selected_label


def _selected_slot_payload(label: str, service: str) -> Optional[Dict[str, Any]]:
    start_at = parse_slot_label(label or "")
    if not start_at:
        return None
    end_at = start_at + dt.timedelta(minutes=service_duration_minutes(service))
    return {
        "start_at": start_at,
        "end_at": end_at,
        "label": label,
        "original_label": label,
        "service_type": service,
        "date": start_at.date(),
    }


def _store_selected_slot(
    call_sid: str,
    label: str,
    service: str,
    *,
    stats: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    payload = _selected_slot_payload(label, service)
    if not payload:
        return None
    CTX.set_pending_slot(call_sid, label)
    CTX.set_selected_slot(call_sid, payload)
    if stats is not None:
        stats["selected_slot_start"] = payload["start_at"].isoformat()
        stats["selected_slot_end"] = payload["end_at"].isoformat()
        stats["selected_slot_label"] = label
        stats["selected_slot_preserved"] = True
    return payload


def _selected_slot_details(call_sid: str) -> Optional[Dict[str, Any]]:
    ctx = CTX.get(call_sid) or {}
    stored = ctx.get("selected_slot") or {}
    label = stored.get("label") or ctx.get("pending_slot")
    service = stored.get("service_type") or ctx.get("service") or "sesion de fisioterapia"
    start_at = stored.get("start_at")
    if not isinstance(start_at, dt.datetime):
        start_at = _parse_start(start_at)
    if not start_at and label:
        start_at = parse_slot_label(label)
    if not start_at or not label:
        return None
    end_at = stored.get("end_at")
    if not isinstance(end_at, dt.datetime):
        end_at = _parse_start(end_at)
    if not end_at:
        end_at = start_at + dt.timedelta(minutes=service_duration_minutes(service))
    return {
        "start_at": start_at,
        "end_at": end_at,
        "label": label,
        "original_label": stored.get("original_label") or label,
        "service_type": service,
        "date": stored.get("date") or start_at.date(),
    }


def _contact_present(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("contact_phone") or ctx.get("contact_email"))


def _clear_selected_slot(call_sid: str) -> None:
    CTX.set_pending_slot(call_sid, None)
    CTX.set_selected_slot(call_sid, None)


def _filter_failed_slot_labels(labels: List[str], failed_start: Optional[dt.datetime]) -> List[str]:
    if not failed_start:
        return labels[:VOICE_PAGE_SIZE]
    failed = failed_start.replace(tzinfo=None)
    filtered: List[str] = []
    for label in labels:
        label_start = parse_slot_label(label)
        if label_start and label_start.replace(tzinfo=None) == failed:
            continue
        if label not in filtered:
            filtered.append(label)
        if len(filtered) >= VOICE_PAGE_SIZE:
            break
    return filtered


def _contact_email_retry_prompt(call_sid: str) -> str:
    ctx = CTX.get(call_sid) or {}
    ctx["contact_parse_failures"] = int(ctx.get("contact_parse_failures") or 0) + 1
    return copy.ask_contact_email_retry()


def _contact_phone_retry_prompt(call_sid: str) -> str:
    ctx = CTX.get(call_sid) or {}
    ctx["contact_parse_failures"] = int(ctx.get("contact_parse_failures") or 0) + 1
    return copy.ask_contact_phone_retry()


def _parse_start(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _build_slot_labels(
    service: str,
    date_pref: dt.date,
    time_pref: Optional[str],
    count: int = VOICE_PAGE_SIZE,
    *,
    ignore_calendar_event_id: Optional[str] = None,
    original_start_at: Optional[Any] = None,
    allow_dummy_fallback: bool = True,
) -> List[str]:
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))
    requested_count = count
    search_count = max(count * 8, 12)
    original_start = _parse_start(original_start_at)
    skipped_original_slot_count = 0
    skipped_busy_count = 0

    try:
        raw_slots = booking_propose_slots(
            preferred_dt,
            service,
            count=search_count,
            ignore_calendar_event_id=ignore_calendar_event_id,
            time_pref=time_pref,
        )
    except Exception as exc:
        logger.warning(f"voice_slot_generation_fallback service={service!r} error={exc!r}")
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

    if not labels:
        if settings.USE_REAL_CALENDAR or not allow_dummy_fallback:
            logger.info(
                "voice_slot_search slots_requested_count=%s slots_found_count=%s slot_search_date=%s "
                "slot_search_window_start=%s slot_search_window_end=%s busy_intervals_count=%s "
                "skipped_busy_count=%s skipped_original_slot_count=%s",
                requested_count,
                0,
                date_pref.isoformat(),
                preferred_dt.isoformat(),
                (preferred_dt + dt.timedelta(days=1)).isoformat(),
                0,
                skipped_busy_count,
                skipped_original_slot_count,
            )
            return []
        labels = _dummy_slot_labels(date_pref, time_pref, count)
    logger.info(
        "voice_slot_search slots_requested_count=%s slots_found_count=%s slot_search_date=%s "
        "slot_search_window_start=%s slot_search_window_end=%s busy_intervals_count=%s "
        "skipped_busy_count=%s skipped_original_slot_count=%s",
        requested_count,
        len(labels),
        date_pref.isoformat(),
        preferred_dt.isoformat(),
        (preferred_dt + dt.timedelta(days=1)).isoformat(),
        0,
        skipped_busy_count,
        skipped_original_slot_count,
    )
    return labels


def _current_slots(key: str, page_size: int = VOICE_PAGE_SIZE) -> List[str]:
    ctx = CTX.get(key) or {}
    offered_slots = ctx.get("offered_slots", [])
    offered_offset = ctx.get("offered_offset", 0)
    if not offered_slots:
        return []
    start = max(offered_offset - page_size, 0)
    current = offered_slots[start:offered_offset]
    return current or offered_slots[:page_size]


def _reprompt_for_stage(call_sid: str) -> str:
    stage = CTX.get_stage(call_sid)
    ctx = CTX.get(call_sid) or {}
    if stage == "awaiting_cancel_confirmation":
        return "Dime si quieres cancelar esa cita."
    if stage == "awaiting_cancel_selection":
        return "Dime cuál quieres cancelar, por ejemplo la primera o la segunda."
    if stage == "awaiting_cancel_date":
        return "Dime la fecha y la hora aproximada de la cita que quieres cancelar."
    if stage == "awaiting_reschedule_confirmation":
        return "Dime si quieres cambiar esa cita."
    if stage == "awaiting_reschedule_selection":
        return "Dime cuál quieres cambiar, por ejemplo la primera o la segunda."
    if stage == "awaiting_reschedule_date":
        return copy.ask_date_retry()
    if stage == "awaiting_patient_name":
        return copy.ask_patient_name(ctx.get("service"))
    if stage == "awaiting_consultation_reason":
        return copy.ask_consultation_reason_retry()
    if stage == "awaiting_contact":
        return _contact_retry_prompt(call_sid)
    if stage == "awaiting_contact_email":
        return copy.ask_contact_email()
    if stage == "awaiting_contact_phone":
        return copy.ask_contact_phone()
    if stage == "awaiting_date":
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_slots":
        current = _current_slots(call_sid)
        if current:
            return copy.propose_slots(current, time_pref=ctx.get("time_pref"))
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_reschedule_slots":
        current = _current_slots(call_sid)
        if current:
            return copy.propose_slots(current, time_pref=ctx.get("time_pref"))
        return "Dime otro dia y miro huecos."
    return copy.ask_service_retry()


def _offer_slots(call_sid: str, service: str, parsed_date: dt.date, time_pref: Optional[str], stats: Dict[str, Any]) -> Response:
    slot_start = time.perf_counter()
    CTX.set_date(call_sid, parsed_date)
    CTX.set_time_pref(call_sid, time_pref)
    _clear_selected_slot(call_sid)
    CTX.set_slots(call_sid, _build_slot_labels(service, parsed_date, time_pref))
    CTX.set_stage(call_sid, "offering_slots")
    stats["slot_ms"] += (time.perf_counter() - slot_start) * 1000
    stats["stage_after"] = "offering_slots"
    visible_slots = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
    if not visible_slots:
        return _respond_gather(call_sid, copy.no_slots_for_day(parsed_date, time_pref=time_pref), stats)
    return _respond_gather(call_sid, copy.propose_slots(visible_slots, time_pref=time_pref), stats)


def _shift_slot_offer(call_sid: str, *, direction: str, stats: Dict[str, Any]) -> Optional[Response]:
    ctx = CTX.get(call_sid) or {}
    service = ctx.get("service")
    date_pref = ctx.get("date_pref")
    current_pref = ctx.get("time_pref")
    if not service or not date_pref:
        return None

    if direction == "later":
        if current_pref != "afternoon":
            return _offer_slots(call_sid, service, date_pref, "afternoon", stats)
        next_batch = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
        if next_batch:
            stats["branch"] = "later_slots_page"
            stats["stage_after"] = "offering_slots"
            return _respond_gather(call_sid, copy.propose_slots(next_batch, time_pref=ctx.get("time_pref")), stats)
        return _offer_slots(call_sid, service, date_pref, "afternoon", stats)

    if current_pref != "morning":
        return _offer_slots(call_sid, service, date_pref, "morning", stats)
    return _offer_slots(call_sid, service, date_pref, "morning", stats)


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


def _set_contact_from_parse(
    call_sid: str,
    *,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> None:
    CTX.set_contact(
        call_sid,
        contact_phone=phone,
        contact_email=email,
        contact_channel_preference="phone" if phone else "email",
    )
    ctx = CTX.get(call_sid) or {}
    ctx["contact_parse_failures"] = 0
    if stats is not None:
        stats["contact_present"] = True
        stats["contact_parse_result"] = "accepted"


def _reoffer_after_slot_taken(
    call_sid: str,
    details: Dict[str, Any],
    stats: Dict[str, Any],
) -> Response:
    ctx = CTX.get(call_sid) or {}
    service = details.get("service_type") or ctx.get("service") or "sesion de fisioterapia"
    date_pref = details.get("date") or ctx.get("date_pref")
    time_pref = ctx.get("time_pref")
    failed_start = details.get("start_at")
    stats["reoffer_due_to_slot_taken"] = True
    stats["slot_reoffer_reason"] = "calendar_busy"
    stats["failed_slot_start"] = failed_start.isoformat() if isinstance(failed_start, dt.datetime) else None
    stats["excluded_failed_slot"] = bool(failed_start)
    stats["contact_preserved_after_reoffer"] = _contact_present(ctx)
    _clear_selected_slot(call_sid)
    if not isinstance(date_pref, dt.date):
        CTX.set_stage(call_sid, "awaiting_date")
        stats["stage_after"] = "awaiting_date"
        stats["reoffer_slots_count"] = 0
        return _respond_gather(
            call_sid,
            "Ese hueco acaba de ocuparse. Conservo tu contacto. ¿Qué día te va bien?",
            stats,
        )
    reoffer_slots = _filter_failed_slot_labels(
        _build_slot_labels(
            service,
            date_pref,
            time_pref,
            count=VOICE_PAGE_SIZE + 1,
            original_start_at=failed_start,
        ),
        failed_start,
    )
    CTX.set_slots(call_sid, reoffer_slots)
    CTX.set_stage(call_sid, "offering_slots")
    visible_slots = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
    stats["stage_after"] = "offering_slots"
    stats["reoffer_slots_count"] = len(visible_slots)
    stats["selected_slot_after_reoffer"] = visible_slots[0] if visible_slots else None
    prefix = "Ese hueco acaba de ocuparse. Conservo tu contacto y te doy otras opciones para el mismo día."
    if not visible_slots:
        return _respond_gather(call_sid, f"{prefix} {copy.no_slots_for_day(date_pref, time_pref=time_pref)}", stats)
    return _respond_gather(call_sid, f"{prefix} {copy.propose_slots(visible_slots, time_pref=time_pref)}", stats)


def _reoffer_after_lost_selection(call_sid: str, stats: Dict[str, Any]) -> Response:
    ctx = CTX.get(call_sid) or {}
    service = ctx.get("service") or "sesion de fisioterapia"
    date_pref = ctx.get("date_pref")
    time_pref = ctx.get("time_pref")
    _clear_selected_slot(call_sid)
    stats["slot_reoffer_reason"] = "selection_lost"
    stats["confirm_slot_revalidation_result"] = "not_called"
    stats["confirm_attempt_after_contact"] = False
    stats["selected_slot_present"] = False
    stats["contact_preserved_after_reoffer"] = _contact_present(ctx)
    prefix = "Perdona, he perdido la selección del horario. Te vuelvo a mostrar las opciones."
    if isinstance(date_pref, dt.date):
        CTX.set_slots(call_sid, _build_slot_labels(service, date_pref, time_pref))
        CTX.set_stage(call_sid, "offering_slots")
        visible_slots = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
        stats["stage_after"] = "offering_slots"
        stats["reoffer_slots_count"] = len(visible_slots)
        stats["selected_slot_after_reoffer"] = visible_slots[0] if visible_slots else None
        if visible_slots:
            return _respond_gather(call_sid, f"{prefix} {copy.propose_slots(visible_slots, time_pref=time_pref)}", stats)
    CTX.set_stage(call_sid, "awaiting_date")
    stats["stage_after"] = "awaiting_date"
    stats["reoffer_slots_count"] = 0
    return _respond_gather(call_sid, f"{prefix} {copy.ask_date(service)}", stats)


async def _confirm_selected_slot_with_contact(
    call_sid: str,
    stats: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Response:
    ctx = CTX.get(call_sid) or {}
    details = _selected_slot_details(call_sid)
    service = (details or {}).get("service_type") or ctx.get("service") or "sesion de fisioterapia"
    if not details:
        stats["branch"] = "contact_missing_selected_slot"
        stats["selected_slot_preserved"] = False
        return _reoffer_after_lost_selection(call_sid, stats)

    stats["selected_slot_start"] = details["start_at"].isoformat()
    stats["selected_slot_end"] = details["end_at"].isoformat()
    stats["selected_slot_label"] = details["label"]
    stats["selected_slot_present"] = True
    stats["selected_slot_preserved"] = True
    stats["contact_present_before_confirm"] = _contact_present(ctx)
    stats["confirm_attempt_after_contact"] = _contact_present(ctx)

    booking_result = await confirm_slot(
        channel="voice",
        external_user_id=call_sid,
        service_type=service,
        start_at=details["start_at"],
        end_at=details["end_at"],
        patient_name=ctx.get("patient_name"),
        consultation_reason=ctx.get("consultation_reason"),
        contact_phone=ctx.get("contact_phone"),
        contact_email=ctx.get("contact_email"),
        metadata={"slot_label": details["label"], **(metadata or {})},
    )
    if not booking_result.ok:
        stats["branch"] = f"slot_confirm_failed_{booking_result.reason}"
        if booking_result.reason == "missing_contact":
            CTX.set_stage(call_sid, "awaiting_contact")
            stats["stage_after"] = "awaiting_contact"
            stats["confirm_slot_revalidation_result"] = "missing_contact"
            return _respond_gather(call_sid, _contact_retry_prompt(call_sid), stats)
        if booking_result.reason == "calendar_busy":
            stats["confirm_slot_revalidation_result"] = "busy"
            return _reoffer_after_slot_taken(call_sid, details, stats)
        stats["confirm_slot_revalidation_result"] = "error"
        stats["stage_after"] = CTX.get_stage(call_sid)
        return _respond_gather(call_sid, "No he podido confirmar la cita ahora mismo. Tu cita no se ha cerrado.", stats)

    appointment = booking_result.appointment
    patient_name = (appointment or {}).get("metadata", {}).get("patient_name") or ctx.get("patient_name")
    CTX.clear_flow(call_sid)
    CTX.set_last_confirmed_slot(call_sid, details["label"], service=service, patient_name=patient_name)
    CTX.set_stage(call_sid, "completed")
    stats["branch"] = "contact_then_slot_confirmed"
    stats["stage_after"] = "completed"
    stats["confirm_slot_revalidation_result"] = "free"
    return _respond_gather(call_sid, copy.confirm_booking(details["label"], service, patient_name), stats)


async def _handle_contact_stage(
    call_sid: str,
    user_text: str,
    from_value: Optional[str],
    stats: Dict[str, Any],
) -> Response:
    current_stage = CTX.get_stage(call_sid)
    ctx = CTX.get(call_sid) or {}
    stats["contact_stage"] = current_stage
    stats["contact_stage_before"] = current_stage
    stats["contact_parse_attempt"] = True

    if current_stage == "awaiting_contact_email":
        email = extract_email(user_text)
        if email:
            _set_contact_from_parse(call_sid, email=email, stats=stats)
            stats["contact_parse_result"] = "email"
            stats["branch"] = "contact_email_then_confirm"
            return await _confirm_selected_slot_with_contact(call_sid, stats)
        stats["contact_parse_result"] = "invalid"
        stats["confirm_slot_revalidation_result"] = "not_called"
        stats["confirm_attempt_after_contact"] = False
        stats["selected_slot_present"] = bool(_selected_slot_details(call_sid))
        stats["branch"] = "contact_email_retry"
        stats["stage_after"] = "awaiting_contact_email"
        return _respond_gather(call_sid, _contact_email_retry_prompt(call_sid), stats)

    if current_stage == "awaiting_contact_phone":
        phone = extract_phone(user_text)
        if phone:
            _set_contact_from_parse(call_sid, phone=phone, stats=stats)
            stats["contact_parse_result"] = "phone"
            stats["branch"] = "contact_phone_then_confirm"
            return await _confirm_selected_slot_with_contact(call_sid, stats)
        stats["contact_parse_result"] = "invalid"
        stats["confirm_slot_revalidation_result"] = "not_called"
        stats["confirm_attempt_after_contact"] = False
        stats["selected_slot_present"] = bool(_selected_slot_details(call_sid))
        stats["branch"] = "contact_phone_retry"
        stats["stage_after"] = "awaiting_contact_phone"
        return _respond_gather(call_sid, _contact_phone_retry_prompt(call_sid), stats)

    phone, email = extract_contact(user_text)
    used_from = False
    if not phone and not email and confirms_current_phone(user_text):
        if ctx.get("suggested_contact_phone"):
            phone = ctx.get("suggested_contact_phone")
            used_from = True
        else:
            phone, _ = extract_contact(from_value or "")
            used_from = bool(phone)
    if phone or email:
        _set_contact_from_parse(call_sid, phone=phone, email=email, stats=stats)
        stats["contact_parse_result"] = "from" if used_from else "phone" if phone else "email"
        stats["branch"] = "contact_direct_then_confirm"
        return await _confirm_selected_slot_with_contact(call_sid, stats)
    if requests_email_contact(user_text):
        CTX.set_stage(call_sid, "awaiting_contact_email")
        stats["contact_parse_result"] = "method_only"
        stats["confirm_slot_revalidation_result"] = "not_called"
        stats["confirm_attempt_after_contact"] = False
        stats["selected_slot_present"] = bool(_selected_slot_details(call_sid))
        stats["branch"] = "contact_email_requested"
        stats["stage_after"] = "awaiting_contact_email"
        return _respond_gather(call_sid, copy.ask_contact_email(), stats)
    if requests_phone_contact(user_text):
        CTX.set_stage(call_sid, "awaiting_contact_phone")
        stats["contact_parse_result"] = "method_only"
        stats["confirm_slot_revalidation_result"] = "not_called"
        stats["confirm_attempt_after_contact"] = False
        stats["selected_slot_present"] = bool(_selected_slot_details(call_sid))
        stats["branch"] = "contact_phone_requested"
        stats["stage_after"] = "awaiting_contact_phone"
        return _respond_gather(call_sid, copy.ask_contact_phone(), stats)

    stats["contact_parse_result"] = "invalid"
    stats["confirm_slot_revalidation_result"] = "not_called"
    stats["confirm_attempt_after_contact"] = False
    stats["selected_slot_present"] = bool(_selected_slot_details(call_sid))
    stats["branch"] = "contact_retry"
    stats["stage_after"] = "awaiting_contact"
    return _respond_gather(call_sid, _contact_retry_prompt(call_sid), stats)


def _absolute_url(path: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def _conversationrelay_ws_url() -> str:
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    ws_url = f"{ws_url}/webhook/voice/conversationrelay/ws"
    logger.info(f"conversationrelay_ws_url={ws_url}")
    return ws_url


def _resolve_tts_provider() -> str:
    providers = {
        "amazon": "Amazon",
        "elevenlabs": "ElevenLabs",
        "google": "Google",
    }
    requested = providers.get((settings.CONVERSATIONRELAY_TTS_PROVIDER or "").strip().lower())
    if requested:
        return requested

    fallback = providers.get((settings.CONVERSATIONRELAY_TTS_PROVIDER_FALLBACK or "").strip().lower(), "Google")
    logger.warning(
        "conversationrelay_invalid_tts_provider "
        f"requested={settings.CONVERSATIONRELAY_TTS_PROVIDER!r} fallback={fallback!r}"
    )
    return fallback


def _resolve_conversationrelay_voice() -> Optional[str]:
    configured = (settings.CONVERSATIONRELAY_TTS_VOICE or "").strip()
    return configured or None


def _resolve_transcription_provider() -> str:
    providers = {
        "deepgram": "Deepgram",
        "google": "Google",
    }
    requested = providers.get((settings.CONVERSATIONRELAY_TRANSCRIPTION_PROVIDER or "").strip().lower())
    if requested:
        return requested
    logger.warning(
        "conversationrelay_invalid_transcription_provider "
        f"requested={settings.CONVERSATIONRELAY_TRANSCRIPTION_PROVIDER!r} fallback='Google'"
    )
    return "Google"


def _conversationrelay_speech_model(provider: str) -> str:
    configured = (settings.CONVERSATIONRELAY_SPEECH_MODEL or "").strip()
    if configured:
        return configured
    return "nova-2-general" if provider == "Deepgram" else "telephony"


def _conversationrelay_twiml() -> Response:
    ws_url = _conversationrelay_ws_url()
    action_url = _absolute_url("/webhook/voice/conversationrelay/action")
    tts_provider = _resolve_tts_provider()
    voice_id = _resolve_conversationrelay_voice()

    attrs = {
        "url": ws_url,
        "ttsLanguage": settings.CONVERSATIONRELAY_TTS_LANGUAGE,
        "ttsProvider": tts_provider,
        "transcriptionLanguage": settings.CONVERSATIONRELAY_TRANSCRIPTION_LANGUAGE,
        "interruptible": settings.CONVERSATIONRELAY_INTERRUPTIBLE,
        "reportInputDuringAgentSpeech": settings.CONVERSATIONRELAY_REPORT_INPUT_DURING_AGENT_SPEECH,
        "hints": VOICE_HINTS,
    }

    if settings.CONVERSATIONRELAY_DTMF_DETECTION:
        attrs["dtmfDetection"] = "true"

    if voice_id:
        attrs["voice"] = voice_id

    attr_string = " ".join(
        f'{name}="{escape(str(value), quote=True)}"'
        for name, value in attrs.items()
        if value not in (None, "")
    )
    body = (
        f'<Connect action="{escape(action_url, quote=True)}">'
        f"<ConversationRelay {attr_string} />"
        f"</Connect>"
    )
    logger.info(
        "conversationrelay_config "
        + " ".join(
            f"{name}={value!r}"
            for name, value in {
                "ws_url": ws_url,
                "ttsProvider": tts_provider,
                "voice": voice_id,
                "ttsLanguage": settings.CONVERSATIONRELAY_TTS_LANGUAGE,
                "transcriptionLanguage": settings.CONVERSATIONRELAY_TRANSCRIPTION_LANGUAGE,
            }.items()
        )
    )
    return _twiml(body)


async def _send_conversationrelay_text(websocket: WebSocket, text: str):
    payload = {
        "type": "text",
        "token": str(text),
        "last": True,
    }
    logger.info(f"conversationrelay_ws_send payload={json.dumps(payload, ensure_ascii=False)}")
    await websocket.send_json(payload)


@router.post("/agent")
async def agent_entry(
    request: Request,
    From: str | None = Form(None),
    CallSid: str = Form(...),
    SpeechResult: str | None = Form(None),
    Digits: str | None = Form(None),
):
    del request
    start = time.perf_counter()
    stats: Dict[str, Any] = {
        "start": start,
        "branch": "unknown",
        "stage_before": CTX.get_stage(CallSid),
        "stage_after": CTX.get_stage(CallSid),
        "ctx_before": dict(CTX.get(CallSid) or {}),
        "speech_present": bool(SpeechResult or Digits),
        "speech_available_ms": 0.0,
        "turn_gap_ms": _since_last_prompt_ms(CallSid),
        "intent_ms": 0.0,
        "slot_ms": 0.0,
        "raw": "",
        "normalized": "",
    }

    try:
        if Digits and not SpeechResult:
            SpeechResult = Digits

        current_stage = CTX.get_stage(CallSid)

        if not SpeechResult:
            stats["branch"] = "start" if current_stage == "idle" else "empty_result"
            if current_stage == "idle":
                CTX.clear_flow(CallSid)
                CTX.set_stage(CallSid, "awaiting_service")
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.opening_greeting(), stats)

            stats["stage_after"] = current_stage
            return _respond_gather(CallSid, _reprompt_for_stage(CallSid), stats)

        stats["speech_available_ms"] = (time.perf_counter() - start) * 1000
        user_text = (SpeechResult or "").strip()
        normalized = _normalize_voice_text(user_text)
        stats["raw"] = user_text
        stats["normalized"] = normalized
        logger.info(
            f"voice_input call_sid={CallSid!r} raw={mask_sensitive_text(user_text)!r} "
            f"normalized={mask_sensitive_text(normalized)!r}"
        )

        current_stage = CTX.get_stage(CallSid)
        emergency = detect_emergency(user_text)
        if emergency.detected:
            stats["branch"] = "emergency_detected"
            CTX.clear_flow(CallSid)
            CTX.set_stage(CallSid, "emergency_detected")
            try:
                await supabase_repo.update_conversation_session(
                    CallSid,
                    channel="voice",
                    external_user_id=CallSid,
                    emergency_detected=True,
                    emergency_match=emergency.matched,
                )
            except Exception as exc:
                logger.warning(f"voice_emergency_session_mark_failed call_sid={CallSid!r} error={exc!r}")
            stats["stage_after"] = "emergency_detected"
            return _respond_gather(CallSid, emergency_reply("voice"), stats)

        route = route_message(user_text)
        stats["intent_detected"] = route["type"]

        if current_stage in {"awaiting_contact", "awaiting_contact_email", "awaiting_contact_phone"} and route["type"] in {"thanks", "farewell"}:
            stats["branch"] = "contact_required_before_close"
            stats["stage_after"] = current_stage
            return _respond_gather(CallSid, copy.contact_required_before_closing(), stats)

        if route["type"] == "thanks":
            stats["branch"] = "thanks"
            stats["stage_after"] = current_stage
            return _respond_gather(CallSid, _recent_booking_reply(CallSid), stats)
        if route["type"] == "farewell":
            stats["branch"] = "farewell"
            stats["stage_after"] = current_stage
            return _respond_gather(CallSid, _recent_booking_reply(CallSid, farewell=True), stats)

        if current_stage == "completed":
            if route["type"] in {"thanks", "acknowledgement"}:
                stats["branch"] = route["type"]
                stats["stage_after"] = "completed"
                return _respond_gather(CallSid, copy.thanks_closing(), stats)
            if route["type"] == "booking":
                CTX.clear_flow(CallSid)
                service = route.get("service")
                if service:
                    CTX.set_service(CallSid, service)
                    if _needs_consultation_reason(CTX.get(CallSid) or {}, service):
                        CTX.set_stage(CallSid, "awaiting_consultation_reason")
                        stats["branch"] = "completed_restart_booking_service_ask_reason"
                        stats["stage_after"] = "awaiting_consultation_reason"
                        return _respond_gather(CallSid, copy.ask_consultation_reason(), stats)
                    patient_name = await _resolve_patient_name(CallSid)
                    if patient_name:
                        CTX.set_stage(CallSid, "awaiting_date")
                        stats["branch"] = "completed_restart_booking_service_patient_known"
                        stats["stage_after"] = "awaiting_date"
                        return _respond_gather(CallSid, copy.ask_date(service), stats)
                    CTX.set_stage(CallSid, "awaiting_patient_name")
                    stats["branch"] = "completed_restart_booking_service_ask_patient_name"
                    stats["stage_after"] = "awaiting_patient_name"
                    return _respond_gather(CallSid, copy.ask_patient_name(service), stats)
                CTX.set_stage(CallSid, "awaiting_service")
                stats["branch"] = "completed_restart_booking"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)
            if route["type"] == "reschedule":
                stats["branch"] = "completed_reschedule"
                return await _start_appointment_action(CallSid, action="reschedule", stats=stats)
            if route["type"] == "cancel":
                stats["branch"] = "completed_cancel"
                return await _start_appointment_action(CallSid, action="cancel", stats=stats)
            stats["branch"] = "completed_close"
            stats["stage_after"] = "completed"
            return _respond_gather(CallSid, copy.thanks_closing(), stats)

        if current_stage == "awaiting_cancel_confirmation":
            appointment = _selected_appointment(CallSid, action="cancel")
            if is_cancel_confirmation_yes(user_text) and appointment:
                stats["branch"] = "cancel_confirmed"
                return await _cancel_selected_appointment(CallSid, appointment, stats)
            if is_cancel_confirmation_no(user_text):
                CTX.clear_flow(CallSid)
                stats["branch"] = "cancel_declined"
                stats["stage_after"] = "idle"
                return _respond_gather(CallSid, "De acuerdo, mantengo tu cita como estaba.", stats)
            stats["branch"] = "cancel_confirmation_retry"
            stats["stage_after"] = "awaiting_cancel_confirmation"
            return _respond_gather(CallSid, "Dime si quieres cancelar esa cita.", stats)

        if current_stage == "awaiting_cancel_selection":
            if is_cancel_confirmation_no(user_text):
                CTX.clear_flow(CallSid)
                stats["branch"] = "cancel_selection_declined"
                stats["stage_after"] = "idle"
                return _respond_gather(CallSid, "De acuerdo, mantengo tu cita como estaba.", stats)
            appointment = pick_appointment_option(user_text, _pending_appointments(CallSid, action="cancel"))
            if not appointment:
                stats["branch"] = "cancel_selection_retry"
                stats["stage_after"] = "awaiting_cancel_selection"
                return _respond_gather(CallSid, "No he identificado cuál quieres cancelar. Dime primera, segunda o el servicio.", stats)
            CTX.set_selected_appointment(CallSid, appointment)
            if cancel_selection_implies_confirmation(user_text):
                stats["branch"] = "cancel_selected_confirmed"
                return await _cancel_selected_appointment(CallSid, appointment, stats)
            CTX.set_stage(CallSid, "awaiting_cancel_confirmation")
            stats["branch"] = "cancel_selected_ask_confirmation"
            stats["stage_after"] = "awaiting_cancel_confirmation"
            return _respond_gather(CallSid, _confirm_cancel_selected_prompt(appointment), stats)

        if current_stage == "awaiting_reschedule_confirmation":
            appointment = _selected_appointment(CallSid, action="reschedule")
            if _is_yes(user_text) and appointment:
                stats["branch"] = "reschedule_confirmed_for_single"
                return _ask_new_day_for_selected(CallSid, appointment, stats)
            if _is_no(user_text):
                CTX.clear_flow(CallSid)
                stats["branch"] = "reschedule_declined"
                stats["stage_after"] = "idle"
                return _respond_gather(CallSid, "De acuerdo, no cambio nada.", stats)
            stats["branch"] = "reschedule_confirmation_retry"
            stats["stage_after"] = "awaiting_reschedule_confirmation"
            return _respond_gather(CallSid, "Dime si quieres cambiar esa cita.", stats)

        if current_stage == "awaiting_reschedule_selection":
            appointment = pick_appointment_option(user_text, _pending_appointments(CallSid, action="reschedule"))
            if not appointment:
                stats["branch"] = "reschedule_selection_retry"
                stats["stage_after"] = "awaiting_reschedule_selection"
                return _respond_gather(CallSid, "No he identificado cuál quieres cambiar. Dime primera, segunda o el servicio.", stats)
            stats["branch"] = "reschedule_selected"
            return _ask_new_day_for_selected(CallSid, appointment, stats)

        if current_stage == "awaiting_reschedule_date":
            date_parse = parse_voice_date(user_text)
            _record_voice_date_parse(stats, user_text, date_parse, source=_date_source_for(date_parse))
            if date_parse.target_date:
                appointment = _selected_appointment(CallSid, action="reschedule")
                if appointment:
                    stats["branch"] = "reschedule_date_ok"
                    return _offer_reschedule_slots(
                        CallSid,
                        appointment.get("service_type") or "sesion de fisioterapia",
                        date_parse.target_date,
                        date_parse.time_pref,
                        stats,
                    )
            if date_parse.validation_result == "mismatch":
                CTX.set_date(CallSid, None)
                CTX.set_time_pref(CallSid, None)
                stats["branch"] = "reschedule_date_weekday_mismatch"
                stats["stage_after"] = "awaiting_reschedule_date"
                return _respond_gather(CallSid, _date_clarification(date_parse), stats)
            stats["branch"] = "reschedule_retry"
            stats["stage_after"] = "awaiting_reschedule_date"
            return _respond_gather(CallSid, copy.ask_date_retry(), stats)

        if current_stage == "offering_reschedule_slots":
            stats["branch"] = "reschedule_slot_pick"
            return await _confirm_reschedule_slot(CallSid, user_text, stats)

        if current_stage == "awaiting_service":
            parse_start = time.perf_counter()
            route_peek = route
            service = detect_service(user_text)
            date_parse = parse_voice_date(user_text)
            parsed_date = date_parse.target_date
            time_pref = date_parse.time_pref
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000
            _record_voice_date_parse(stats, user_text, date_parse, source=_date_source_for(date_parse))

            if route_peek["type"] in {"faq", "human_handoff", "uncertain"}:
                natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
                if natural.handled:
                    stats["branch"] = natural.route_type
                    stats["stage_after"] = CTX.get_stage(CallSid)
                    return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

            if route_peek["type"] == "greeting":
                stats["branch"] = "greeting"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service(), stats)
            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                stats["stage_after"] = current_stage
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "cancel":
                stats["branch"] = "cancel"
                return await _start_appointment_action(CallSid, action="cancel", stats=stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return await _start_appointment_action(CallSid, action="reschedule", stats=stats)
            if route_peek["type"] == "booking" and not service:
                stats["branch"] = "booking_without_service"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)

            if not service and _looks_like_lavado_only(user_text):
                service = "corte + lavado"
                stats["branch"] = "lavado_heuristic"

            if service and date_parse.validation_result == "mismatch":
                CTX.set_service(CallSid, service)
                CTX.set_stage(CallSid, "awaiting_date")
                stats["branch"] = "service_date_weekday_mismatch"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, _date_clarification(date_parse), stats)

            if service and parsed_date:
                CTX.set_service(CallSid, service)
                if _needs_consultation_reason(CTX.get(CallSid) or {}, service):
                    CTX.set_date(CallSid, parsed_date)
                    CTX.set_time_pref(CallSid, time_pref)
                    CTX.set_stage(CallSid, "awaiting_consultation_reason")
                    stats["branch"] = "service_date_ask_reason"
                    stats["stage_after"] = "awaiting_consultation_reason"
                    return _respond_gather(CallSid, copy.ask_consultation_reason(), stats)
                patient_name = await _resolve_patient_name(CallSid)
                if patient_name:
                    stats["branch"] = "service_date_patient_known"
                    return _offer_slots(CallSid, service, parsed_date, time_pref, stats)
                CTX.set_date(CallSid, parsed_date)
                CTX.set_time_pref(CallSid, time_pref)
                CTX.set_stage(CallSid, "awaiting_patient_name")
                stats["branch"] = "service_date_ask_patient_name"
                stats["stage_after"] = "awaiting_patient_name"
                return _respond_gather(CallSid, copy.ask_patient_name(service), stats)

            if service:
                CTX.set_service(CallSid, service)
                if _needs_consultation_reason(CTX.get(CallSid) or {}, service):
                    CTX.set_stage(CallSid, "awaiting_consultation_reason")
                    stats["branch"] = "service_ok_ask_reason"
                    stats["stage_after"] = "awaiting_consultation_reason"
                    return _respond_gather(CallSid, copy.ask_consultation_reason(), stats)
                patient_name = await _resolve_patient_name(CallSid)
                if patient_name:
                    CTX.set_stage(CallSid, "awaiting_date")
                    stats["branch"] = "service_ok_patient_known"
                    stats["stage_after"] = "awaiting_date"
                    return _respond_gather(CallSid, copy.ask_date(service), stats)
                CTX.set_stage(CallSid, "awaiting_patient_name")
                stats["branch"] = "service_ok_ask_patient_name"
                stats["stage_after"] = "awaiting_patient_name"
                return _respond_gather(CallSid, copy.ask_patient_name(service), stats)

            natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
            if natural.handled:
                stats["branch"] = natural.route_type
                stats["stage_after"] = CTX.get_stage(CallSid)
                return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

            stats["branch"] = "service_retry"
            return _respond_gather(CallSid, copy.ask_service_retry(), stats)

        if current_stage == "awaiting_consultation_reason":
            reason = clean_consultation_reason(user_text)
            if not reason:
                stats["branch"] = "consultation_reason_retry"
                stats["stage_after"] = "awaiting_consultation_reason"
                return _respond_gather(CallSid, copy.ask_consultation_reason_retry(), stats)
            CTX.set_consultation_reason(CallSid, reason)
            ctx = CTX.get(CallSid) or {}
            service = ctx.get("service")
            date_pref = ctx.get("date_pref")
            time_pref = ctx.get("time_pref")
            patient_name = await _resolve_patient_name(CallSid)
            if not patient_name:
                CTX.set_stage(CallSid, "awaiting_patient_name")
                stats["branch"] = "consultation_reason_then_patient_name"
                stats["stage_after"] = "awaiting_patient_name"
                return _respond_gather(CallSid, copy.consultation_reason_then_patient_name(), stats)
            if service and date_pref:
                stats["branch"] = "consultation_reason_then_slots"
                return _offer_slots(CallSid, service, date_pref, time_pref, stats)
            CTX.set_stage(CallSid, "awaiting_date")
            stats["branch"] = "consultation_reason_then_date"
            stats["stage_after"] = "awaiting_date"
            return _respond_gather(CallSid, copy.consultation_reason_then_date(), stats)

        if current_stage == "awaiting_patient_name":
            patient_name = extract_patient_name_from_voice(user_text)
            if not patient_name:
                stats["branch"] = "patient_name_retry"
                stats["stage_after"] = "awaiting_patient_name"
                return _respond_gather(CallSid, _patient_name_retry_prompt(CallSid), stats)
            await _store_manual_patient_name(CallSid, patient_name)
            ctx_for_attempts = CTX.get(CallSid) or {}
            ctx_for_attempts["patient_name_parse_failures"] = 0
            ctx = CTX.get(CallSid) or {}
            service = ctx.get("service")
            date_pref = ctx.get("date_pref")
            time_pref = ctx.get("time_pref")
            if service and date_pref:
                stats["branch"] = "patient_name_then_slots"
                return _offer_slots(CallSid, service, date_pref, time_pref, stats)
            CTX.set_stage(CallSid, "awaiting_date")
            stats["branch"] = "patient_name_then_date"
            stats["stage_after"] = "awaiting_date"
            return _respond_gather(CallSid, copy.thanks_name_then_date(first_name(patient_name) or patient_name), stats)

        if current_stage == "awaiting_date":
            parse_start = time.perf_counter()
            date_parse = parse_voice_date(user_text)
            parsed_date = date_parse.target_date
            time_pref = date_parse.time_pref
            route_peek = route
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000
            _record_voice_date_parse(stats, user_text, date_parse, source=_date_source_for(date_parse))

            if parsed_date:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                if not service:
                    CTX.set_stage(CallSid, "awaiting_service")
                    stats["branch"] = "missing_service"
                    stats["stage_after"] = "awaiting_service"
                    return _respond_gather(CallSid, copy.ask_service(), stats)
                if _needs_consultation_reason(ctx, service):
                    CTX.set_date(CallSid, parsed_date)
                    CTX.set_time_pref(CallSid, time_pref)
                    CTX.set_stage(CallSid, "awaiting_consultation_reason")
                    stats["branch"] = "date_ok_ask_reason"
                    stats["stage_after"] = "awaiting_consultation_reason"
                    return _respond_gather(CallSid, copy.ask_consultation_reason(), stats)

                stats["branch"] = "date_ok"
                return _offer_slots(CallSid, service, parsed_date, time_pref, stats)

            if date_parse.validation_result == "mismatch":
                CTX.set_date(CallSid, None)
                CTX.set_time_pref(CallSid, None)
                stats["branch"] = "date_weekday_mismatch"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, _date_clarification(date_parse), stats)

            if time_pref:
                CTX.set_time_pref(CallSid, time_pref)
                stats["branch"] = "date_retry_time_pref_only"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, copy.ask_date_retry(), stats)

            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return await _start_appointment_action(CallSid, action="reschedule", stats=stats)

            stats["branch"] = "date_retry"
            stats["stage_after"] = "awaiting_date"
            return _respond_gather(CallSid, copy.ask_date_retry(), stats)

        if current_stage == "offering_slots":
            parse_start = time.perf_counter()
            current = _current_slots(CallSid)
            picked = pick_slot_with_index(user_text, current)
            selected = picked[1] if picked else None
            date_parse = parse_voice_date(user_text)
            reparsed_date = date_parse.target_date
            reparsed_time = date_parse.time_pref
            route_peek = route
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000
            if date_parse.target_date or date_parse.explicit_weekday is not None or date_parse.time_pref:
                _record_voice_date_parse(stats, user_text, date_parse, source=_date_source_for(date_parse))

            if selected:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service") or "sesion de fisioterapia"
                selected_dt = parse_slot_label(selected)
                if selected_dt:
                    _store_selected_slot(CallSid, selected, service, stats=stats)
                    _record_slot_selection(stats, user_text, picked[0], selected)
                    if _contact_present(CTX.get(CallSid) or {}):
                        stats["branch"] = "slot_selected_contact_present"
                        return await _confirm_selected_slot_with_contact(CallSid, stats)
                    CTX.set_stage(CallSid, "awaiting_contact")
                    stats["branch"] = "slot_selected_ask_contact"
                    stats["stage_after"] = "awaiting_contact"
                    return _respond_gather(CallSid, _contact_prompt_for_call(CallSid, From), stats)

            if date_parse.validation_result == "mismatch":
                CTX.set_date(CallSid, None)
                CTX.set_time_pref(CallSid, None)
                CTX.set_stage(CallSid, "awaiting_date")
                stats["branch"] = "slot_date_weekday_mismatch"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, _date_clarification(date_parse), stats)

            if reparsed_date:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                if service:
                    stats["branch"] = "date_changed"
                    return _offer_slots(CallSid, service, reparsed_date, reparsed_time or ctx.get("time_pref"), stats)

            if reparsed_time:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                date_pref = ctx.get("date_pref")
                if service and date_pref:
                    stats["branch"] = "time_changed"
                    return _offer_slots(CallSid, service, date_pref, reparsed_time, stats)

            if route_peek["type"] == "later_slots":
                shifted = _shift_slot_offer(CallSid, direction="later", stats=stats)
                if shifted:
                    return shifted

            if route_peek["type"] == "earlier_slots":
                shifted = _shift_slot_offer(CallSid, direction="earlier", stats=stats)
                if shifted:
                    return shifted

            if route_peek["type"] == "another_day":
                stats["branch"] = "another_day"
                stats["stage_after"] = "awaiting_date"
                CTX.set_stage(CallSid, "awaiting_date")
                return _respond_gather(CallSid, copy.another_day(), stats)

            if route_peek["type"] == "reject_slot":
                stats["branch"] = "reject_slot"
                stats["stage_after"] = "offering_slots"
                return _respond_gather(CallSid, copy.earlier_or_later(), stats)

            natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
            if natural.handled:
                stats["branch"] = natural.route_type
                stats["stage_after"] = CTX.get_stage(CallSid)
                ctx = CTX.get(CallSid) or {}
                return _respond_gather(CallSid, natural.reply or copy.propose_slots(current, time_pref=ctx.get("time_pref")), stats)

            if route_peek["type"] == "more_options":
                next_batch = CTX.next_slots(CallSid, VOICE_PAGE_SIZE)
                stats["branch"] = "more_options"
                stats["stage_after"] = "offering_slots"
                if next_batch:
                    ctx = CTX.get(CallSid) or {}
                    return _respond_gather(CallSid, copy.propose_slots(next_batch, time_pref=ctx.get("time_pref")), stats)
                return _respond_gather(CallSid, "No tengo más huecos para ese día. Dime otro.", stats)

            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return await _start_appointment_action(CallSid, action="reschedule", stats=stats)

            stats["branch"] = "slot_retry"
            ctx = CTX.get(CallSid) or {}
            return _respond_gather(CallSid, copy.propose_slots(current, time_pref=ctx.get("time_pref")), stats)

        if current_stage in {"awaiting_contact", "awaiting_contact_email", "awaiting_contact_phone"}:
            return await _handle_contact_stage(CallSid, user_text, From, stats)

        parse_start = time.perf_counter()
        stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000
        route_type = route["type"]

        if route_type == "pick_slot":
            pending_cancel = _pending_appointments(CallSid, action="cancel")
            if pending_cancel:
                appointment = pick_appointment_option(user_text, pending_cancel)
                if appointment:
                    CTX.set_selected_appointment(CallSid, appointment)
                    CTX.set_stage(CallSid, "awaiting_cancel_confirmation")
                    stats["branch"] = "stale_stage_cancel_selection_recovered"
                    stats["stage_after"] = "awaiting_cancel_confirmation"
                    return _respond_gather(CallSid, _confirm_cancel_selected_prompt(appointment), stats)
            pending_reschedule = _pending_appointments(CallSid, action="reschedule")
            if pending_reschedule:
                appointment = pick_appointment_option(user_text, pending_reschedule)
                if appointment:
                    stats["branch"] = "stale_stage_reschedule_selection_recovered"
                    return _ask_new_day_for_selected(CallSid, appointment, stats)
            stats["branch"] = "stale_selection"
            stats["stage_after"] = CTX.get_stage(CallSid)
            return _respond_gather(CallSid, "Ya no tengo activa esa seleccion. Di cancelar cita y te vuelvo a mostrar tus citas.", stats)

        if route_type == "greeting":
            CTX.clear_flow(CallSid)
            CTX.set_stage(CallSid, "awaiting_service")
            stats["branch"] = "greeting"
            stats["stage_after"] = "awaiting_service"
            return _respond_gather(CallSid, copy.ask_service(), stats)

        if route_type == "booking":
            CTX.clear_flow(CallSid)
            service = route.get("service")
            if service:
                CTX.set_service(CallSid, service)
                if _needs_consultation_reason(CTX.get(CallSid) or {}, service):
                    CTX.set_stage(CallSid, "awaiting_consultation_reason")
                    stats["branch"] = "booking_service_ask_reason"
                    stats["stage_after"] = "awaiting_consultation_reason"
                    return _respond_gather(CallSid, copy.ask_consultation_reason(), stats)
                patient_name = await _resolve_patient_name(CallSid)
                if patient_name:
                    CTX.set_stage(CallSid, "awaiting_date")
                    stats["branch"] = "booking_service_patient_known"
                    stats["stage_after"] = "awaiting_date"
                    return _respond_gather(CallSid, copy.ask_date(service), stats)
                CTX.set_stage(CallSid, "awaiting_patient_name")
                stats["branch"] = "booking_service_ask_patient_name"
                stats["stage_after"] = "awaiting_patient_name"
                return _respond_gather(CallSid, copy.ask_patient_name(service), stats)
            CTX.set_stage(CallSid, "awaiting_service")
            stats["branch"] = "booking_no_service"
            stats["stage_after"] = "awaiting_service"
            return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)

        if route_type == "cancel":
            stats["branch"] = "cancel"
            return await _start_appointment_action(CallSid, action="cancel", stats=stats)

        if route_type == "reschedule":
            stats["branch"] = "reschedule"
            return await _start_appointment_action(CallSid, action="reschedule", stats=stats)

        natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
        if natural.handled:
            stats["branch"] = natural.route_type
            stats["stage_after"] = CTX.get_stage(CallSid)
            return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

        if route_type == "out_of_scope":
            stats["branch"] = "out_of_scope"
            return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)

        stats["branch"] = "fallback"
        return _respond_gather(CallSid, copy.ask_service(), stats)

    except Exception as exc:
        logger.exception(f"voice_error call_sid={CallSid!r} error={exc!r}")
        stats["branch"] = "exception"
        stats["stage_after"] = CTX.get_stage(CallSid)
        return _respond_gather(CallSid, copy.technical_error(), stats)


@router.api_route("/conversationrelay", methods=["GET", "POST"])
async def conversationrelay_entry(request: Request):
    del request
    return _conversationrelay_twiml()


@router.api_route("/conversationrelay/action", methods=["GET", "POST"])
async def conversationrelay_action(
    request: Request,
    CallSid: str | None = Form(None),
    SessionId: str | None = Form(None),
    SessionStatus: str | None = Form(None),
    ErrorCode: str | None = Form(None),
    ErrorMessage: str | None = Form(None),
    HandoffData: str | None = Form(None),
):
    del request, SessionId, HandoffData
    if SessionStatus == "failed" and CallSid:
        logger.warning(
            "conversationrelay_action_fallback "
            f"call_sid={CallSid!r} error_code={ErrorCode!r} error_message={ErrorMessage!r}"
        )
        return _twiml(
            f'<Redirect method="POST">{escape(_absolute_url("/webhook/voice/agent"), quote=True)}</Redirect>'
        )

    return _twiml("")


@router.websocket("/conversationrelay/ws")
async def conversationrelay_ws(websocket: WebSocket):
    await websocket.accept()
    session = ConversationRelaySession()

    try:
        while True:
            raw_message = await websocket.receive_text()
            ws_received_at = time.perf_counter()
            logger.info(f"conversationrelay_ws_recv raw={mask_sensitive_text(raw_message)}")

            try:
                payload = json.loads(raw_message)
                message_type = payload.get("type")
                logger.info(
                    "conversationrelay_ws_event "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} type={message_type!r}"
                )

                if message_type == "setup":
                    route_started_at = time.perf_counter()
                    greeting = handle_setup_message(session, payload)
                    route_finished_at = time.perf_counter()
                    await _send_conversationrelay_text(websocket, greeting)
                    response_sent_at = time.perf_counter()
                    logger.info(
                        "conversationrelay_latency "
                        f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                        f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                        f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                    )
                    continue

                if message_type == "prompt":
                    route_started_at = time.perf_counter()
                    reply = await handle_prompt_message(session, payload)
                    route_finished_at = time.perf_counter()
                    if reply:
                        await _send_conversationrelay_text(websocket, reply)
                        response_sent_at = time.perf_counter()
                        logger.info(
                            "conversationrelay_latency "
                            f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                            f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                            f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                        )
                    continue

                if message_type == "dtmf":
                    route_started_at = time.perf_counter()
                    await _send_conversationrelay_text(websocket, await handle_dtmf_message(session, payload))
                    route_finished_at = response_sent_at = time.perf_counter()
                    logger.info(
                        "conversationrelay_latency "
                        f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                        f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                        f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                    )
                    continue

                if message_type == "interrupt":
                    handle_interrupt_message(session, payload)
                    continue

                if message_type == "error":
                    logger.warning(
                        "conversationrelay_ws_error "
                        f"call_sid={session.call_sid!r} session_id={session.session_id!r} "
                        f"payload={mask_sensitive_text(json.dumps(payload, ensure_ascii=False))}"
                    )
                    continue

                logger.info(
                    "conversationrelay_ws_unhandled "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} "
                    f"payload={mask_sensitive_text(json.dumps(payload, ensure_ascii=False))}"
                )
            except Exception as exc:
                logger.exception(
                    "conversationrelay_ws_loop_exception "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} "
                    f"raw={mask_sensitive_text(raw_message)!r} error={exc!r}"
                )
                raise
    except WebSocketDisconnect:
        logger.info(
            "conversationrelay_ws_disconnect "
            f"call_sid={session.call_sid!r} session_id={session.session_id!r}"
        )
    except Exception as exc:
        logger.exception(
            "conversationrelay_ws_exception "
            f"call_sid={session.call_sid!r} session_id={session.session_id!r} error={exc!r}"
        )
        await websocket.close(code=1011)


@router.post("")
async def entry(request: Request):
    del request
    return _twiml(_say("Por favor usa el endpoint agent."))


@router.post("/handle_intent")
async def handle_intent(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_name")
async def collect_name(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_service")
async def collect_service(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_datetime")
async def collect_datetime(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.head("/tts")
async def tts_head():
    return Response(status_code=200, media_type="audio/mpeg")


@router.get("/tts")
async def tts(text: str):
    tts_start = time.perf_counter()
    api_key = settings.ELEVEN_API_KEY
    if not api_key:
        raise HTTPException(status_code=501, detail="Missing API Key")

    now = time.time()
    cached = TTS_CACHE.get(text)
    if cached and now < cached["expires_at"]:
        total_ms = round((time.perf_counter() - tts_start) * 1000, 1)
        logger.info(f"voice_tts provider='cache' status=200 total_ms={total_ms} chars={len(text)}")
        return Response(content=cached["audio"], media_type="audio/mpeg")

    voice_id = (settings.ELEVEN_VOICE_ID or _resolve_conversationrelay_voice() or "").strip()
    if not voice_id:
        raise HTTPException(status_code=501, detail="Missing ElevenLabs voice_id")
    model_id = settings.ELEVEN_MODEL_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
    payload = {"text": text, "model_id": model_id, "voice_settings": {"stability": 0.4, "similarity_boost": 0.7}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=headers, json=payload)

    total_ms = round((time.perf_counter() - tts_start) * 1000, 1)
    logger.info(f"voice_tts provider='elevenlabs' status={r.status_code} total_ms={total_ms} chars={len(text)}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="ElevenLabs Error")

    if len(TTS_CACHE) >= TTS_CACHE_MAX_ITEMS:
        oldest_key = min(TTS_CACHE.items(), key=lambda item: item[1]["expires_at"])[0]
        TTS_CACHE.pop(oldest_key, None)
    TTS_CACHE[text] = {
        "audio": r.content,
        "expires_at": now + TTS_CACHE_TTL_SECONDS,
    }

    return Response(content=r.content, media_type="audio/mpeg")
