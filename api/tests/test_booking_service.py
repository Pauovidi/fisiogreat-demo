import datetime as dt
import asyncio

from googleapiclient.errors import HttpError

from app.config.settings import settings
from app.services import supabase_repo
from app.services.booking_service import confirm_slot, get_appointment_status, propose_slots
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
    ))

    assert result.ok
    assert calls == [
        ("calendar", calls[0][1]),
        ("appointment", "real-event-123", "confirmed"),
    ]
    assert result.appointment["calendar_event_id"] == "real-event-123"
    assert result.appointment["status"] == "confirmed"


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
    ))

    assert result.ok
    assert result.appointment["status"] == "confirmed"
    assert result.appointment["calendar_event_id"] in CALENDAR_STORE.events


def test_booking_service_proposes_slots_without_secrets():
    CALENDAR_STORE.reset()
    slots = propose_slots(dt.datetime(2026, 5, 4, 10, 0), "sesion de fisioterapia")
    assert slots
    assert slots[0]["service"] == "sesion de fisioterapia"
