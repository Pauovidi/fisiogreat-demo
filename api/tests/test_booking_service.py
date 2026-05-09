import datetime as dt
import asyncio

from googleapiclient.errors import HttpError

from app.config.settings import settings
from app.services import supabase_repo
from app.services.booking_service import (
    cancel_appointment,
    confirm_slot,
    get_appointment_status,
    list_future_appointments,
    propose_slots,
    reschedule_appointment,
)
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE


class _HttpResponse:
    status = 400
    reason = "Bad Request"

    def get(self, _key, default=None):
        return default


def test_booking_service_confirms_mock_appointment():
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert result.ok
    assert result.appointment["status"] == "confirmed"
    assert get_appointment_status(result.appointment["id"])["calendar_event_id"]


def test_real_calendar_success_creates_event_before_confirmed_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    calls = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        calls.append(("calendar", kwargs["event_id"]))
        assert not STORE.appointments
        return "real-event-123"

    async def fake_create_appointment(**payload):
        calls.append(("appointment", payload["calendar_event_id"], payload["status"]))
        STORE.appointments[payload["id"]] = payload
        return payload

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)
    monkeypatch.setattr("app.services.booking_service.supabase_repo.create_appointment", fake_create_appointment)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert result.ok
    assert calls == [
        ("calendar", calls[0][1]),
        ("appointment", "real-event-123", "confirmed"),
    ]
    assert result.appointment["calendar_event_id"] == "real-event-123"
    assert result.appointment["status"] == "confirmed"


def test_confirm_slot_without_patient_name_does_not_create_calendar_or_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    create_event_calls = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        create_event_calls.append(kwargs)
        return "should-not-happen"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
    ))

    assert not result.ok
    assert result.reason == "patient_name_required"
    assert create_event_calls == []
    assert not STORE.appointments
    assert all(lock["status"] == "released" for lock in STORE.locks.values())


def test_voice_confirm_slot_requires_contact_before_calendar(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    create_event_calls = []

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **kwargs: create_event_calls.append(kwargs))

    result = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id="CA-voice-missing-contact",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
        consultation_reason="me duele la rodilla",
    ))

    assert not result.ok
    assert result.reason == "missing_contact"
    assert create_event_calls == []
    assert not STORE.appointments


def test_voice_confirm_slot_accepts_phone_only_contact(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime.now() + dt.timedelta(days=7)
    create_event_calls = []

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **kwargs: create_event_calls.append(kwargs) or "voice-phone-event")

    result = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id="CA-voice-phone-only",
        service_type="valoracion inicial",
        start_at=start,
        patient_name="Pau Marco",
        contact_phone="640505050",
    ))

    assert result.ok
    assert create_event_calls
    assert result.appointment["metadata"]["contact_phone"] == "640505050"
    assert result.appointment["metadata"]["contact_channel_preference"] == "phone"
    assert "contact_email" not in result.appointment["metadata"]


def test_confirm_slot_metadata_and_calendar_description_include_reason_and_contact(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    calls = []

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        calls.append(kwargs)
        return "event-with-reason-contact"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    result = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id="CA-voice-contact",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
        consultation_reason="me duele la rodilla",
        contact_email="pau@example.com",
    ))

    assert result.ok
    description = calls[0]["description"]
    assert "Motivo de consulta: me duele la rodilla" in description
    assert "Contacto para recordatorio: email pau@example.com" in description
    assert result.appointment["metadata"]["consultation_reason"] == "me duele la rodilla"
    assert result.appointment["metadata"]["contact_email"] == "pau@example.com"
    assert result.appointment["metadata"]["channel"] == "voice"


def test_real_calendar_failure_does_not_create_confirmed_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fail_create_event(**_kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fail_create_event)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert not result.ok
    assert result.reason == "integration_error"
    assert not [item for item in STORE.appointments.values() if item.get("status") == "confirmed"]
    assert all(lock["status"] == "released" for lock in STORE.locks.values())


def test_real_calendar_freebusy_http_error_does_not_confirm_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")

    def fail_free_busy(*_args):
        raise HttpError(_HttpResponse(), b'{"error":{"message":"Bad Request"}}')

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", fail_free_busy)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert not result.ok
    assert result.reason == "integration_error"
    assert not STORE.appointments
    assert all(lock["status"] == "released" for lock in STORE.locks.values())


def test_patient_failure_does_not_create_calendar_or_confirmed_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    create_event_calls = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    async def fail_patient(**_kwargs):
        raise RuntimeError("patient write failed")

    def fake_create_event(**kwargs):
        create_event_calls.append(kwargs)
        return "should-not-happen"

    monkeypatch.setattr("app.services.booking_service.supabase_repo.upsert_patient_by_phone", fail_patient)
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert not result.ok
    assert result.reason == "integration_error"
    assert create_event_calls == []
    assert not [item for item in STORE.appointments.values() if item.get("status") == "confirmed"]
    assert all(lock["status"] == "released" for lock in STORE.locks.values())


def test_existing_patient_and_calendar_ok_confirms_appointment(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 11, 0)
    existing = asyncio.run(
        supabase_repo.create_patient(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone="+34600000001",
            name="Ada",
        )
    )

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "real-event-existing-patient")

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert result.ok
    assert result.appointment["patient_id"] == existing["id"]
    assert result.appointment["calendar_event_id"] == "real-event-existing-patient"
    assert result.appointment["status"] == "confirmed"


def test_mock_calendar_allows_confirmation(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", False)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", None)

    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))

    assert result.ok
    assert result.appointment["status"] == "confirmed"
    assert result.appointment["calendar_event_id"] in CALENDAR_STORE.events


def test_booking_service_proposes_slots_without_secrets():
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    slots = propose_slots(start, "sesion de fisioterapia")
    assert slots
    assert slots[0]["service"] == "sesion de fisioterapia"


def test_list_future_appointments_filters_patient_status_past_and_calendar_event():
    STORE.reset()
    CALENDAR_STORE.reset()
    user = "+34600001000"
    patient = asyncio.run(supabase_repo.create_patient(clinic_id=settings.DEMO_CLINIC_ID, phone=user, name="Pau Marco"))
    future = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    past = (dt.datetime.now() - dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)

    asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id=patient["id"],
        service_type="sesion de fisioterapia",
        start_at=future.isoformat(),
        end_at=(future + dt.timedelta(minutes=45)).isoformat(),
        status="confirmed",
        calendar_event_id="future-event",
        external_user_id=user,
    ))
    asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id=patient["id"],
        service_type="valoracion inicial",
        start_at=(future + dt.timedelta(hours=1)).isoformat(),
        end_at=(future + dt.timedelta(hours=2)).isoformat(),
        status="cancelled",
        calendar_event_id="cancelled-event",
        external_user_id=user,
    ))
    asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id=patient["id"],
        service_type="consulta de seguimiento",
        start_at=past.isoformat(),
        end_at=(past + dt.timedelta(minutes=30)).isoformat(),
        status="confirmed",
        calendar_event_id="past-event",
        external_user_id=user,
    ))
    asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id=patient["id"],
        service_type="valoracion inicial",
        start_at=(future + dt.timedelta(hours=2)).isoformat(),
        end_at=(future + dt.timedelta(hours=3)).isoformat(),
        status="confirmed",
        external_user_id=user,
    ))

    appointments = asyncio.run(list_future_appointments(patient_key=user))

    assert [appointment["calendar_event_id"] for appointment in appointments] == ["future-event"]
    assert appointments[0]["patient_name"] == "Pau Marco"


def test_reschedule_updates_calendar_before_supabase_and_preserves_confirmed_status(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 7, 10, 0)
    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600001001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
        consultation_reason="me duele la rodilla",
    ))
    appointment_id = result.appointment["id"]
    original_event_id = result.appointment["calendar_event_id"]
    new_start = dt.datetime(2026, 5, 8, 10, 15)

    changed = asyncio.run(reschedule_appointment(
        appointment_id,
        new_start_at=new_start,
        new_end_at=new_start + dt.timedelta(minutes=45),
    ))

    assert changed.ok
    appointment = STORE.appointments[appointment_id]
    assert appointment["status"] == "confirmed"
    assert appointment["calendar_event_id"] == original_event_id
    assert appointment["metadata"]["consultation_reason"] == "me duele la rodilla"
    assert CALENDAR_STORE.events[original_event_id].start_at.replace(tzinfo=None) == new_start


def test_reschedule_calendar_failure_keeps_supabase_original(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 7, 10, 0)
    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600001002",
        service_type="valoracion inicial",
        start_at=start,
        patient_name="Pau Marco",
    ))
    appointment_id = result.appointment["id"]
    monkeypatch.setattr("app.services.booking_service.calendar_service.update_event", lambda *_args, **_kwargs: False)

    changed = asyncio.run(reschedule_appointment(
        appointment_id,
        new_start_at=dt.datetime(2026, 5, 8, 10, 15),
        new_end_at=dt.datetime(2026, 5, 8, 11, 15),
    ))

    assert not changed.ok
    assert changed.reason == "calendar_update_failed"
    assert STORE.appointments[appointment_id]["start_at"] == start.isoformat()
    assert STORE.appointments[appointment_id]["status"] == "confirmed"


def test_cancel_calendar_failure_keeps_supabase_confirmed(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 7, 10, 0)
    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600001003",
        service_type="valoracion inicial",
        start_at=start,
        patient_name="Pau Marco",
    ))
    appointment_id = result.appointment["id"]
    monkeypatch.setattr("app.services.booking_service.calendar_service.delete_event", lambda *_args, **_kwargs: False)

    cancelled = asyncio.run(cancel_appointment(appointment_id))

    assert not cancelled.ok
    assert cancelled.reason == "calendar_delete_failed"
    assert STORE.appointments[appointment_id]["status"] == "confirmed"
