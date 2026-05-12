import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...config.settings import settings
from ...utils.appointment_options import format_appointment_option_voice
from ...utils.booking_requirements import (
    clean_consultation_reason,
    confirms_current_phone,
    extract_contact,
    is_physiotherapy_session,
    normalize_text,
)
from ...utils.date_parser import WEEKDAYS, parse_voice_date
from ...utils.emergency_guard import detect_emergency, emergency_reply
from ...utils.logger import logger
from .. import booking_service
from .instructions import VALID_SERVICES, VALID_SERVICES_DISPLAY


@dataclass
class RealtimeToolContext:
    session_id: str
    call_phone: Optional[str] = None
    write_enabled: Optional[bool] = None
    emergency_blocked: bool = False
    consultation_reason: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    offered_slots: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def patient_key(self) -> str:
        return self.call_phone or self.session_id

    @property
    def can_write(self) -> bool:
        if self.write_enabled is not None:
            return self.write_enabled
        return bool(settings.OPENAI_REALTIME_WRITE_ENABLED)


def get_services() -> Dict[str, Any]:
    return {
        "ok": True,
        "services": VALID_SERVICES_DISPLAY,
        "service_keys": VALID_SERVICES,
        "closed_catalog": True,
    }


def collect_consultation_reason(context: RealtimeToolContext, reason: str) -> Dict[str, Any]:
    cleaned = clean_consultation_reason(reason)
    if not cleaned:
        return {"ok": False, "reason": "invalid_consultation_reason"}
    context.consultation_reason = cleaned
    return {"ok": True, "consultation_reason": cleaned, "diagnosis": None}


def get_available_slots(
    context: RealtimeToolContext,
    *,
    service: str,
    date_text: str,
    today: Optional[dt.date | str] = None,
    count: int = 3,
) -> Dict[str, Any]:
    service_key = _canonical_service(service)
    if not service_key:
        return _out_of_catalog()

    today_date = _coerce_date(today)
    parsed = parse_voice_date(date_text, settings.GOOGLE_CALENDAR_TIMEZONE, today=today_date)
    if parsed.validation_result == "mismatch":
        weekday = _weekday_name(parsed.explicit_weekday)
        raw = parsed.raw_target_date.isoformat() if parsed.raw_target_date else None
        return {
            "ok": False,
            "reason": "weekday_mismatch",
            "message": f"Creo que he entendido {weekday}, pero la fecha calculada no coincide. Dime el dia otra vez.",
            "explicit_weekday": weekday,
            "raw_target_date": raw,
            "slots": [],
        }
    if not parsed.target_date:
        return {
            "ok": False,
            "reason": "ambiguous_date",
            "message": "No he entendido bien el dia. Puedes decirme, por ejemplo, jueves por la manana.",
            "slots": [],
        }

    preferred = _preferred_datetime(parsed.target_date, parsed.time_pref)
    slots = booking_service.propose_slots(preferred, service_key, count=count)
    if parsed.explicit_weekday is not None:
        slots = [slot for slot in slots if slot["start"].weekday() == parsed.explicit_weekday]
    slots = slots[:count]
    context.offered_slots = slots
    return {
        "ok": True,
        "service": service_key,
        "date": parsed.target_date.isoformat(),
        "explicit_weekday": _weekday_name(parsed.explicit_weekday),
        "slots": [_serialize_slot(slot) for slot in slots],
        "message": _slot_message(slots),
    }


async def book_appointment(
    context: RealtimeToolContext,
    *,
    service: str,
    patient_name: str,
    start_at: str | dt.datetime,
    end_at: Optional[str | dt.datetime] = None,
    consultation_reason: Optional[str] = None,
    contact_phone: Optional[str] = None,
    contact_email: Optional[str] = None,
) -> Dict[str, Any]:
    if context.emergency_blocked:
        return {"ok": False, "reason": "emergency_blocked", "message": emergency_reply("voice")}

    service_key = _canonical_service(service)
    if not service_key:
        return _out_of_catalog()

    reason = clean_consultation_reason(consultation_reason or context.consultation_reason or "")
    if is_physiotherapy_session(service_key) and not reason:
        return {"ok": False, "reason": "consultation_reason_required"}

    phone = contact_phone or context.contact_phone
    email = contact_email or context.contact_email
    if phone and email:
        return {"ok": False, "reason": "single_contact_required"}
    if not (phone or email):
        return {"ok": False, "reason": "missing_contact"}

    start = _coerce_datetime(start_at)
    end = _coerce_datetime(end_at) if end_at else start + dt.timedelta(minutes=booking_service.service_duration_minutes(service_key))
    if not context.can_write:
        logger.info("openai_realtime_v2_shadow_booking session_id=%s service=%s start_at=%s", context.session_id, service_key, start.isoformat())
        return {
            "ok": True,
            "shadow": True,
            "write_enabled": False,
            "appointment": {
                "id": f"shadow-{context.session_id}",
                "service_type": service_key,
                "patient_name": patient_name,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "status": "shadow_confirmed",
                "metadata": _contact_metadata(phone, email, reason),
            },
        }

    result = await booking_service.confirm_slot(
        channel="voice",
        external_user_id=context.patient_key,
        service_type=service_key,
        start_at=start,
        end_at=end,
        patient_name=patient_name,
        consultation_reason=reason,
        contact_phone=phone,
        contact_email=email,
        metadata={"transport": "openai_realtime_v2"},
    )
    return {
        "ok": result.ok,
        "shadow": False,
        "reason": result.reason,
        "appointment": result.appointment,
    }


async def list_future_appointments(context: RealtimeToolContext) -> Dict[str, Any]:
    appointments = await booking_service.list_future_appointments(patient_key=context.patient_key)
    return {
        "ok": True,
        "count": len(appointments),
        "requires_selection": len(appointments) > 1,
        "appointments": [_serialize_appointment(item, index + 1) for index, item in enumerate(appointments)],
    }


async def reschedule_appointment(
    context: RealtimeToolContext,
    *,
    appointment_id: str,
    new_start_at: str | dt.datetime,
    new_end_at: str | dt.datetime,
) -> Dict[str, Any]:
    if context.emergency_blocked:
        return {"ok": False, "reason": "emergency_blocked", "message": emergency_reply("voice")}

    new_start = _coerce_datetime(new_start_at)
    new_end = _coerce_datetime(new_end_at)
    if not context.can_write:
        logger.info("openai_realtime_v2_shadow_reschedule session_id=%s appointment_id=%s", context.session_id, appointment_id)
        return {
            "ok": True,
            "shadow": True,
            "write_enabled": False,
            "appointment": {
                "id": appointment_id,
                "start_at": new_start.isoformat(),
                "end_at": new_end.isoformat(),
                "status": "shadow_rescheduled",
            },
        }

    result = await booking_service.reschedule_appointment(
        appointment_id,
        new_start_at=new_start,
        new_end_at=new_end,
    )
    return {"ok": result.ok, "shadow": False, "reason": result.reason, "appointment": result.appointment}


async def cancel_appointment(context: RealtimeToolContext, *, appointment_id: str) -> Dict[str, Any]:
    if context.emergency_blocked:
        return {"ok": False, "reason": "emergency_blocked", "message": emergency_reply("voice")}
    if not context.can_write:
        logger.info("openai_realtime_v2_shadow_cancel session_id=%s appointment_id=%s", context.session_id, appointment_id)
        return {
            "ok": True,
            "shadow": True,
            "write_enabled": False,
            "appointment": {"id": appointment_id, "status": "shadow_cancelled"},
        }

    result = await booking_service.cancel_appointment(appointment_id)
    return {"ok": result.ok, "shadow": False, "reason": result.reason, "appointment": result.appointment}


def save_contact(
    context: RealtimeToolContext,
    contact_text: str,
    *,
    call_phone: Optional[str] = None,
) -> Dict[str, Any]:
    active_call_phone = call_phone or context.call_phone
    if active_call_phone and confirms_current_phone(contact_text):
        context.contact_phone = _normalize_phone(active_call_phone)
        context.contact_email = None
        return {"ok": True, "contact_phone": context.contact_phone, "contact_email": None, "source": "call_phone"}

    phone, email = extract_contact(contact_text)
    if phone:
        context.contact_phone = phone
        context.contact_email = None
        return {"ok": True, "contact_phone": phone, "contact_email": None, "source": "spoken_phone"}
    if email:
        context.contact_phone = None
        context.contact_email = email
        return {"ok": True, "contact_phone": None, "contact_email": email, "source": "spoken_email"}
    return {"ok": False, "reason": "invalid_contact"}


def emergency_protocol(context: RealtimeToolContext, user_text: str) -> Dict[str, Any]:
    detection = detect_emergency(user_text)
    context.emergency_blocked = True
    return {
        "ok": True,
        "emergency_detected": detection.detected,
        "matched": detection.matched,
        "booking_blocked": True,
        "message": emergency_reply("voice"),
    }


def _canonical_service(service: str) -> Optional[str]:
    normalized = normalize_text(service or "")
    if "valoracion" in normalized or "primera visita" in normalized:
        return "valoracion inicial"
    if "seguimiento" in normalized:
        return "consulta de seguimiento"
    if "sesion" in normalized and "fisio" in normalized:
        return "sesion de fisioterapia"
    if normalized in VALID_SERVICES:
        return normalized
    return None


def _out_of_catalog() -> Dict[str, Any]:
    return {
        "ok": False,
        "reason": "out_of_catalog",
        "booking_blocked": True,
        "message": "En esta demo puedo gestionar valoración inicial, sesión de fisioterapia y consulta de seguimiento. Para otros servicios, puedo derivarte a una persona del equipo.",
    }


def _coerce_date(value: Optional[dt.date | str]) -> Optional[dt.date]:
    if isinstance(value, dt.date):
        return value
    if not value:
        return None
    return dt.date.fromisoformat(value)


def _coerce_datetime(value: str | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _preferred_datetime(target_date: dt.date, time_pref: Optional[str]) -> dt.datetime:
    hour = 16 if time_pref == "afternoon" else 10
    return dt.datetime.combine(target_date, dt.time(hour=hour, minute=0))


def _serialize_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "start_at": slot["start"].isoformat(),
        "end_at": slot["end"].isoformat(),
        "service": slot["service"],
        "weekday": _weekday_name(slot["start"].weekday()),
        "label": f"{_weekday_name(slot['start'].weekday())} {slot['start'].strftime('%d/%m')} a las {slot['start'].strftime('%H:%M')}",
    }


def _slot_message(slots: List[Dict[str, Any]]) -> str:
    if not slots:
        return "No veo huecos disponibles para ese dia."
    labels = [_serialize_slot(slot)["label"] for slot in slots]
    return "Te puedo ofrecer " + "; ".join(labels[:3]) + "."


def _serialize_appointment(appointment: Dict[str, Any], index: int) -> Dict[str, Any]:
    return {
        "id": appointment.get("id"),
        "service_type": appointment.get("service_type"),
        "start_at": appointment.get("start_at"),
        "status": appointment.get("status"),
        "label": format_appointment_option_voice(appointment, index),
    }


def _weekday_name(index: Optional[int]) -> Optional[str]:
    if index is None:
        return None
    for name, weekday in WEEKDAYS.items():
        if weekday == index:
            return name
    return None


def _normalize_phone(value: str) -> str:
    phone = re.sub(r"\D", "", value or "")
    return phone[2:] if phone.startswith("00") else phone


def _contact_metadata(phone: Optional[str], email: Optional[str], reason: Optional[str]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"channel": "voice", "transport": "openai_realtime_v2"}
    if phone:
        metadata["contact_phone"] = phone
        metadata["contact_channel_preference"] = "phone"
    if email:
        metadata["contact_email"] = email
        metadata["contact_channel_preference"] = "email"
    if reason:
        metadata["consultation_reason"] = reason
    return metadata
