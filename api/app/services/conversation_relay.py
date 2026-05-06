import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config.settings import settings
from ..utils.date_parser import parse_spanish_day, parse_time_pref
from ..utils.emergency_guard import detect_emergency, emergency_reply
from ..utils.intent_router import detect_service, route_message
from ..utils.booking_requirements import (
    clean_consultation_reason,
    confirms_current_phone,
    extract_contact,
    is_physiotherapy_session,
)
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.patient_name import first_name, parse_patient_name
from ..utils.slot_picker import pick_slot
from ..utils.voice_copy import VOICE_COPY as copy
from .natural_turn import maybe_handle_natural_turn
from .salon_knowledge import out_of_scope_answer
from .booking_service import (
    cancel_appointment,
    confirm_slot,
    parse_slot_label,
    propose_slots as booking_propose_slots,
    reschedule_appointment,
)
from . import supabase_repo


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


def _needs_consultation_reason(ctx: Dict[str, Any], service: Optional[str]) -> bool:
    return is_physiotherapy_session(service) and not ctx.get("consultation_reason")


def _contact_prompt(key: str) -> str:
    ctx = CTX.get(key) or {}
    if ctx.get("suggested_contact_phone"):
        return copy.ask_contact_with_phone_suggestion()
    return copy.ask_contact()


async def _handle_user_input(key: str, user_text: str) -> str:
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
            CTX.clear_flow(key)
            CTX.set_stage(key, "awaiting_reschedule_date")
            return "Claro. Dime la cita actual y el dia nuevo."
        if route_peek["type"] == "cancel":
            CTX.clear_flow(key)
            CTX.set_stage(key, "awaiting_cancel_date")
            return "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada."
        return copy.thanks_closing()

    if current_stage == "awaiting_cancel_date":
        appointment_id = _latest_appointment_id(key)
        CTX.clear_flow(key)
        if appointment_id:
            appointment = supabase_repo.STORE.appointments.get(appointment_id) or {}
            patient_name = (appointment.get("metadata") or {}).get("patient_name")
            await cancel_appointment(appointment_id)
            first = first_name(patient_name)
            prefix = f"Listo, {first}, " if first else "Listo, "
            return f"{prefix}he dejado la cita cancelada."
        return "No encuentro una cita activa para cancelar desde esta llamada."

    if current_stage == "awaiting_reschedule_date":
        parsed_date = parse_spanish_day(user_text)
        appointment_id = _latest_appointment_id(key)
        if parsed_date and appointment_id:
            new_start = dt.datetime.combine(parsed_date, dt.time(10, 0))
            await reschedule_appointment(
                appointment_id,
                new_start_at=new_start,
                new_end_at=new_start + dt.timedelta(minutes=45),
            )
            CTX.clear_flow(key)
            appointment = supabase_repo.STORE.appointments.get(appointment_id) or {}
            first = first_name((appointment.get("metadata") or {}).get("patient_name"))
            prefix = f"Listo, {first}, " if first else "Listo, "
            return f"{prefix}he cambiado la cita. Te queda confirmada para el nuevo dia a las 10."
        return "Dime el nuevo dia, por ejemplo manana o jueves."

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
            CTX.set_stage(key, "awaiting_cancel_date")
            return "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada."
        if route_peek["type"] == "reschedule":
            CTX.set_stage(key, "awaiting_reschedule_date")
            return "Claro. Dime la cita actual y el dia nuevo."
        if route_peek["type"] == "booking" and not service:
            return copy.ask_service_for_booking()

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

        natural = await maybe_handle_natural_turn(key, user_text, page_size=CR_SLOT_PAGE_SIZE)
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
        patient_name = parse_patient_name(user_text)
        if not patient_name:
            return "No he entendido bien el nombre. A que nombre dejamos la cita?"
        await _store_manual_patient_name(key, patient_name)
        ctx = CTX.get(key) or {}
        service = ctx.get("service")
        date_pref = ctx.get("date_pref")
        time_pref = ctx.get("time_pref")
        if service and date_pref:
            return _offer_slots(key, service, date_pref, time_pref)
        CTX.set_stage(key, "awaiting_date")
        return copy.thanks_name_then_date(first_name(patient_name) or patient_name)

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
            if _needs_consultation_reason(ctx, service):
                CTX.set_date(key, parsed_date)
                CTX.set_time_pref(key, time_pref)
                CTX.set_stage(key, "awaiting_consultation_reason")
                return copy.ask_consultation_reason()
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
            CTX.set_stage(key, "awaiting_reschedule_date")
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
        ctx = CTX.get(key) or {}

        if selected:
            service = ctx.get("service") or "sesion de fisioterapia"
            selected_dt = parse_slot_label(selected)
            if selected_dt:
                CTX.set_pending_slot(key, selected)
                CTX.set_stage(key, "awaiting_contact")
                return _contact_prompt(key)

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
            CTX.set_stage(key, "awaiting_reschedule_date")
            return "Vale. Dime otro dia o si prefieres manana o tarde."
        if route_peek["type"] == "out_of_scope":
            return out_of_scope_answer("voice")
        return copy.propose_slots(current)

    if current_stage == "awaiting_contact":
        ctx = CTX.get(key) or {}
        phone, email = extract_contact(user_text)
        if not phone and not email and confirms_current_phone(user_text) and ctx.get("suggested_contact_phone"):
            phone = ctx.get("suggested_contact_phone")
        if not phone and not email:
            return copy.ask_contact_retry()
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
                return copy.ask_contact_retry()
            return "Ese hueco acaba de ocuparse. Te digo otras opciones."
        appointment = booking_result.appointment
        patient_name = (appointment or {}).get("metadata", {}).get("patient_name") or ctx.get("patient_name")
        CTX.clear_flow(key)
        CTX.set_last_confirmed_slot(key, selected, service=service, patient_name=patient_name)
        CTX.set_stage(key, "completed")
        return copy.confirm_booking(selected, service, patient_name)

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
        CTX.clear_flow(key)
        CTX.set_stage(key, "awaiting_cancel_date")
        return "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada."

    if route["type"] == "reschedule":
        CTX.clear_flow(key)
        CTX.set_stage(key, "awaiting_reschedule_date")
        return "Claro. Dime la cita actual y el dia nuevo."

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
    if stage == "awaiting_cancel_date":
        return "Dime la fecha y la hora aproximada de la cita que quieres cancelar."
    if stage == "awaiting_reschedule_date":
        return "Dime el nuevo dia que te vendria mejor."
    if stage == "awaiting_patient_name":
        return copy.ask_patient_name(ctx.get("service"))
    if stage == "awaiting_consultation_reason":
        return copy.ask_consultation_reason_retry()
    if stage == "awaiting_contact":
        return copy.ask_contact_retry()
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
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))
    try:
        raw_slots = booking_propose_slots(preferred_dt, service, count=count)
    except Exception as exc:
        logger.warning(f"conversationrelay_slot_generation_fallback service={service!r} error={exc!r}")
        if settings.USE_REAL_CALENDAR:
            return []
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

    if labels:
        return labels
    if settings.USE_REAL_CALENDAR:
        return []
    selected = [dt.datetime.combine(date_pref, slot_time) for slot_time in _fallback_slot_times(time_pref, count)]
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
