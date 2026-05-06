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


def propose_slots(
    preferred: dt.datetime,
    service_type: str,
    *,
    count: int = 6,
    ignore_calendar_event_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    duration = service_duration_minutes(service_type)
    candidates = local_propose_slots(preferred, _normalize_service(service_type), BusinessRules())
    slots: List[Dict[str, Any]] = []
    for candidate in candidates:
        start_at = candidate["start"]
        end_at = start_at + dt.timedelta(minutes=duration)
        busy = (
            calendar_service.free_busy(start_at, end_at, ignore_event_id=ignore_calendar_event_id)
            if ignore_calendar_event_id
            else calendar_service.free_busy(start_at, end_at)
        )
        if busy:
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
    consultation_reason: Optional[str] = None,
    contact_phone: Optional[str] = None,
    contact_email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> BookingResult:
    clinic_id = settings.DEMO_CLINIC_ID
    resource_id = settings.GOOGLE_CALENDAR_ID or "demo-calendar"
    end_at = end_at or start_at + dt.timedelta(minutes=service_duration_minutes(service_type))
    channel = (channel or "").strip().lower()
    if channel == "whatsapp" and not contact_phone and external_user_id:
        contact_phone = external_user_id
    contact_channel_preference = None
    if contact_email and contact_phone:
        contact_channel_preference = "phone_or_email"
    elif contact_email:
        contact_channel_preference = "email"
    elif contact_phone:
        contact_channel_preference = "phone"
    if channel == "voice" and not (contact_phone or contact_email):
        logger.warning(
            "booking_confirm_missing_contact channel=%s external_user_present=%s",
            channel,
            bool(external_user_id),
        )
        return BookingResult(False, reason="missing_contact")
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

    try:
        patient = await supabase_repo.upsert_patient_by_phone(
            clinic_id=clinic_id,
            phone=contact_phone or external_user_id,
            name=patient_name,
        )
        if not patient.get("name"):
            logger.warning(
                "booking_confirm_missing_patient_name channel=%s external_user_present=%s use_real_calendar=%s",
                channel,
                bool(external_user_id),
                settings.USE_REAL_CALENDAR,
            )
            await supabase_repo.release_booking_lock(
                clinic_id=clinic_id,
                resource_id=resource_id,
                start_at=start_at,
                end_at=end_at,
            )
            return BookingResult(False, reason="patient_name_required")

        if calendar_service.free_busy(start_at, end_at):
            await supabase_repo.release_booking_lock(
                clinic_id=clinic_id,
                resource_id=resource_id,
                start_at=start_at,
                end_at=end_at,
            )
            return BookingResult(False, reason="calendar_busy")

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
        event_summary = f"{settings.CLINIC_NAME} - {service_type}"
        if patient.get("name"):
            event_summary = f"{event_summary} - {patient['name']}"
        appointment_metadata = {
            **(metadata or {}),
            "patient_name": patient.get("name"),
            "channel": channel,
        }
        if consultation_reason:
            appointment_metadata["consultation_reason"] = consultation_reason
        if contact_phone:
            appointment_metadata["contact_phone"] = contact_phone
        if contact_email:
            appointment_metadata["contact_email"] = contact_email
        if contact_channel_preference:
            appointment_metadata["contact_channel_preference"] = contact_channel_preference

        description_lines = [
            f"Paciente: {patient.get('name') or 'No indicado'}",
            f"Servicio: {service_type}",
        ]
        if consultation_reason:
            description_lines.append(f"Motivo de consulta: {consultation_reason}")
        description_lines.append(f"Canal: {channel}")
        contact_values = []
        if contact_phone:
            contact_values.append(f"telefono {contact_phone}")
        if contact_email:
            contact_values.append(f"email {contact_email}")
        if contact_values:
            description_lines.append(f"Contacto para recordatorio: {', '.join(contact_values)}")
        event_description = "\n".join(description_lines)
        calendar_event_id = calendar_service.create_event(
            event_id=event_id,
            summary=event_summary,
            start_at=start_at,
            end_at=end_at,
            description=event_description,
            metadata={
                "channel": channel,
                "clinic_id": clinic_id,
                "patient_name_present": bool(patient.get("name")),
                **{
                    key: value
                    for key, value in appointment_metadata.items()
                    if key
                    in {
                        "consultation_reason",
                        "contact_phone",
                        "contact_email",
                        "contact_channel_preference",
                    }
                    and value
                },
            },
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
            metadata=appointment_metadata,
        )
        logger.info(
            "booking_confirm_success use_real_calendar=%s calendar_event_id=%s appointment_id=%s appointment_confirmed_with_patient_name=%s",
            settings.USE_REAL_CALENDAR,
            calendar_event_id,
            appointment.get("id"),
            bool(patient.get("name")),
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
    appointment = await supabase_repo.get_appointment(appointment_id)
    if not appointment:
        return BookingResult(False, reason="not_found")
    calendar_event_id = appointment.get("calendar_event_id")
    if not calendar_event_id:
        return BookingResult(False, reason="missing_calendar_event")
    logger.info(
        "calendar_update_attempt appointment_id=%s calendar_event_id=%s new_start_at=%s new_end_at=%s",
        appointment_id,
        calendar_event_id,
        new_start_at.isoformat(),
        new_end_at.isoformat(),
    )
    try:
        updated_event = calendar_service.update_event(calendar_event_id, start_at=new_start_at, end_at=new_end_at)
    except Exception as exc:
        logger.warning(
            "calendar_update_error appointment_id=%s calendar_event_id=%s error=%r",
            appointment_id,
            calendar_event_id,
            exc,
        )
        return BookingResult(False, reason="calendar_update_failed")
    if not updated_event:
        logger.warning(
            "calendar_update_error appointment_id=%s calendar_event_id=%s error=%r",
            appointment_id,
            calendar_event_id,
            "update_returned_false",
        )
        return BookingResult(False, reason="calendar_update_failed")
    logger.info(
        "calendar_update_success appointment_id=%s calendar_event_id=%s",
        appointment_id,
        calendar_event_id,
    )
    updated = await supabase_repo.update_appointment(
        appointment_id,
        start_at=new_start_at.isoformat(),
        end_at=new_end_at.isoformat(),
        status=appointment.get("status") or "confirmed",
    )
    return BookingResult(True, appointment=updated)


async def cancel_appointment(appointment_id: str) -> BookingResult:
    appointment = await supabase_repo.get_appointment(appointment_id)
    if not appointment:
        return BookingResult(False, reason="not_found")
    calendar_event_id = appointment.get("calendar_event_id")
    if not calendar_event_id:
        return BookingResult(False, reason="missing_calendar_event")
    try:
        deleted_event = calendar_service.delete_event(calendar_event_id)
    except Exception as exc:
        logger.warning("calendar_delete_failed appointment_id=%s calendar_event_id=%s error=%r", appointment_id, calendar_event_id, exc)
        return BookingResult(False, reason="calendar_delete_failed")
    if not deleted_event:
        return BookingResult(False, reason="calendar_delete_failed")
    updated = await supabase_repo.cancel_appointment(appointment_id)
    return BookingResult(True, appointment=updated)


async def list_future_appointments(*, patient_key: str) -> List[Dict[str, Any]]:
    appointments = await supabase_repo.list_future_appointments_for_patient(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_key=patient_key,
    )
    return [appointment for appointment in appointments if appointment.get("calendar_event_id")]


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
