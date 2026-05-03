import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config.settings import settings
from ..utils.logger import logger
from . import calendar_service
from . import supabase_repo
from .slots import BusinessRules, ServiceCatalog, propose_slots as local_propose_slots


FISIO_SERVICE_DURATIONS = {
    "primera visita de fisioterapia": 60,
    "sesion de fisioterapia": 45,
    "sesión de fisioterapia": 45,
    "valoracion inicial": 60,
    "valoración inicial": 60,
    "consulta de seguimiento": 30,
}


@dataclass(frozen=True)
class BookingResult:
    ok: bool
    appointment: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


def start_booking(*, channel: str, external_user_id: str, service_type: Optional[str] = None) -> Dict[str, Any]:
    return {
        "clinic_id": settings.DEMO_CLINIC_ID,
        "channel": channel,
        "external_user_id": external_user_id,
        "service_type": service_type,
        "status": "started",
    }


def service_duration_minutes(service_type: str) -> int:
    normalized = _normalize_service(service_type)
    return FISIO_SERVICE_DURATIONS.get(
        normalized,
        ServiceCatalog.durations.get(normalized, 45),
    )


def propose_slots(preferred: dt.datetime, service_type: str, *, count: int = 6) -> List[Dict[str, Any]]:
    duration = service_duration_minutes(service_type)
    candidates = local_propose_slots(preferred, _normalize_service(service_type), BusinessRules())
    slots: List[Dict[str, Any]] = []
    for candidate in candidates:
        start_at = candidate["start"]
        end_at = start_at + dt.timedelta(minutes=duration)
        if calendar_service.free_busy(start_at, end_at):
            continue
        slots.append({"start": start_at, "end": end_at, "service": service_type})
        if len(slots) >= count:
            break
    return slots


async def confirm_slot(
    *,
    channel: str,
    external_user_id: str,
    service_type: str,
    start_at: dt.datetime,
    end_at: Optional[dt.datetime] = None,
    patient_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> BookingResult:
    clinic_id = settings.DEMO_CLINIC_ID
    resource_id = settings.GOOGLE_CALENDAR_ID or "demo-calendar"
    end_at = end_at or start_at + dt.timedelta(minutes=service_duration_minutes(service_type))
    logger.info(
        "booking_confirm_start use_real_calendar=%s channel=%s proposed_slot_start=%s proposed_slot_end=%s",
        settings.USE_REAL_CALENDAR,
        channel,
        start_at.isoformat(),
        end_at.isoformat(),
    )

    lock_ok = await supabase_repo.acquire_booking_lock(
        clinic_id=clinic_id,
        resource_id=resource_id,
        start_at=start_at,
        end_at=end_at,
    )
    if not lock_ok:
        return BookingResult(False, reason="double_booking")

    if calendar_service.free_busy(start_at, end_at):
        await supabase_repo.release_booking_lock(
            clinic_id=clinic_id,
            resource_id=resource_id,
            start_at=start_at,
            end_at=end_at,
        )
        return BookingResult(False, reason="calendar_busy")

    try:
        patient = await supabase_repo.upsert_patient_by_phone(
            clinic_id=clinic_id,
            phone=external_user_id,
            name=patient_name,
        )
        event_id = calendar_service.build_deterministic_event_id(
            clinic_id,
            external_user_id,
            service_type,
            start_at,
        )
        logger.info(
            "calendar_create_attempt use_real_calendar=%s proposed_slot_start=%s proposed_slot_end=%s",
            settings.USE_REAL_CALENDAR,
            start_at.isoformat(),
            end_at.isoformat(),
        )
        calendar_event_id = calendar_service.create_event(
            event_id=event_id,
            summary=f"{settings.CLINIC_NAME} - {service_type}",
            start_at=start_at,
            end_at=end_at,
            description=f"Cita creada por {channel} para {external_user_id}",
            metadata={"channel": channel, "clinic_id": clinic_id},
        )
        logger.info(
            "calendar_create_success use_real_calendar=%s calendar_event_id=%s",
            settings.USE_REAL_CALENDAR,
            calendar_event_id,
        )
        appointment = await supabase_repo.create_appointment(
            id=str(uuid.uuid4()),
            clinic_id=clinic_id,
            patient_id=patient["id"],
            service_type=service_type,
            start_at=start_at.isoformat(),
            end_at=end_at.isoformat(),
            status="confirmed",
            calendar_event_id=calendar_event_id,
            channel=channel,
            external_user_id=external_user_id,
            metadata=metadata or {},
        )
        logger.info(
            "booking_confirm_success use_real_calendar=%s calendar_event_id=%s appointment_id=%s",
            settings.USE_REAL_CALENDAR,
            calendar_event_id,
            appointment.get("id"),
        )
        return BookingResult(True, appointment=appointment)
    except Exception as exc:
        logger.warning(
            "calendar_create_error use_real_calendar=%s proposed_slot_start=%s proposed_slot_end=%s error=%r",
            settings.USE_REAL_CALENDAR,
            start_at.isoformat(),
            end_at.isoformat(),
            exc,
        )
        logger.warning("booking_confirm_failed external_user_id=%r error=%r", external_user_id, exc)
        await supabase_repo.release_booking_lock(
            clinic_id=clinic_id,
            resource_id=resource_id,
            start_at=start_at,
            end_at=end_at,
        )
        return BookingResult(False, reason="integration_error")


async def reschedule_appointment(appointment_id: str, *, new_start_at: dt.datetime, new_end_at: dt.datetime) -> BookingResult:
    appointment = supabase_repo.STORE.appointments.get(appointment_id)
    if not appointment:
        return BookingResult(False, reason="not_found")
    calendar_event_id = appointment.get("calendar_event_id")
    if calendar_event_id:
        calendar_service.update_event(calendar_event_id, start_at=new_start_at, end_at=new_end_at)
    updated = await supabase_repo.update_appointment(
        appointment_id,
        start_at=new_start_at.isoformat(),
        end_at=new_end_at.isoformat(),
        status="rescheduled",
    )
    return BookingResult(True, appointment=updated)


async def cancel_appointment(appointment_id: str) -> BookingResult:
    appointment = supabase_repo.STORE.appointments.get(appointment_id)
    if not appointment:
        return BookingResult(False, reason="not_found")
    calendar_event_id = appointment.get("calendar_event_id")
    if calendar_event_id:
        calendar_service.delete_event(calendar_event_id)
    updated = await supabase_repo.cancel_appointment(appointment_id)
    return BookingResult(True, appointment=updated)


def get_appointment_status(appointment_id: str) -> Optional[Dict[str, Any]]:
    return supabase_repo.STORE.appointments.get(appointment_id)


def parse_slot_label(label: str, *, year: Optional[int] = None) -> Optional[dt.datetime]:
    match = re.search(r"(\d{2})/(\d{2}).*?(\d{1,2}):(\d{2})", label)
    if not match:
        return None
    day, month, hour, minute = map(int, match.groups())
    return dt.datetime(year or dt.date.today().year, month, day, hour, minute)


def _normalize_service(service_type: str) -> str:
    return (service_type or "").strip().lower()
