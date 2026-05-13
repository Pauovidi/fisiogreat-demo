import datetime as dt
import asyncio

from googleapiclient.errors import HttpError

from app.config.settings import settings
from app.services import calendar_service, supabase_repo
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


def _busy(day: dt.date, start_time: dt.time, end_time: dt.time):
    return {
        "start": dt.datetime.combine(day, start_time).isoformat(),
        "end": dt.datetime.combine(day, end_time).isoformat(),
    }


def _create_internal_appointment(
    start: dt.datetime,
    end: dt.datetime,
    *,
    status: str = "confirmed",
    calendar_event_id: str = "internal-event",
):
    return asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id="patient-internal",
        service_type="sesion de fisioterapia",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        status=status,
        calendar_event_id=calendar_event_id,
        channel="voice",
        external_user_id="CA-internal",
        metadata={"patient_name": "Pau Marco"},
    ))


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


def test_booking_service_keeps_searching_after_busy_candidates(monkeypatch):
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    calls = []

    def fake_free_busy(start_at, end_at, **kwargs):
        calls.append((start_at, end_at, kwargs))
        return [_busy(start.date(), dt.time(10, 0), dt.time(11, 0))]

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", fake_free_busy)

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert len(slots) == 3
    assert len(calls) == 1
    assert slots[0]["start"].time() == dt.time(11, 0)


def test_booking_service_propose_slots_uses_single_freebusy_window(monkeypatch):
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=11, minute=0, second=0, microsecond=0)
    calls = []

    def fake_free_busy(start_at, end_at, **kwargs):
        calls.append((start_at, end_at, kwargs))
        return []

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", fake_free_busy)

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert len(slots) == 3
    assert len(calls) == 1
    assert calls[0][0] == dt.datetime.combine(start.date(), dt.time(10, 0))
    assert calls[0][1] == dt.datetime.combine(start.date(), dt.time(15, 0))


def test_booking_service_propose_slots_returns_two_one_or_zero_from_busy_window(monkeypatch):
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=11, minute=0, second=0, microsecond=0)

    monkeypatch.setattr(
        "app.services.booking_service.calendar_service.free_busy",
        lambda *_args, **_kwargs: [_busy(start.date(), dt.time(11, 0), dt.time(15, 0))],
    )
    assert [slot["start"].time() for slot in propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")] == [
        dt.time(10, 0),
        dt.time(10, 15),
    ]

    monkeypatch.setattr(
        "app.services.booking_service.calendar_service.free_busy",
        lambda *_args, **_kwargs: [_busy(start.date(), dt.time(10, 45), dt.time(15, 0))],
    )
    assert [slot["start"].time() for slot in propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")] == [
        dt.time(10, 0),
    ]

    monkeypatch.setattr(
        "app.services.booking_service.calendar_service.free_busy",
        lambda *_args, **_kwargs: [_busy(start.date(), dt.time(10, 0), dt.time(15, 0))],
    )
    assert propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning") == []


def test_booking_service_propose_slots_ignores_original_event_and_cancelled_events():
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    original_id = calendar_service.create_event(
        event_id="original-event",
        summary="Original",
        start_at=start,
        end_at=start + dt.timedelta(minutes=45),
    )
    blocked_without_ignore = propose_slots(start, "sesion de fisioterapia", count=1, time_pref="morning")
    ignored = propose_slots(
        start,
        "sesion de fisioterapia",
        count=1,
        ignore_calendar_event_id=original_id,
        time_pref="morning",
    )

    assert blocked_without_ignore[0]["start"].time() == dt.time(10, 45)
    assert ignored[0]["start"].time() == dt.time(10, 0)

    CALENDAR_STORE.reset()
    cancelled_id = calendar_service.create_event(
        event_id="cancelled-event",
        summary="Cancelled",
        start_at=start,
        end_at=start + dt.timedelta(minutes=45),
    )
    calendar_service.delete_event(cancelled_id)
    slots = propose_slots(start, "sesion de fisioterapia", count=1, time_pref="morning")
    assert slots[0]["start"].time() == dt.time(10, 0)


def test_booking_service_propose_slots_filters_confirmed_internal_appointments(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    _create_internal_appointment(start, start + dt.timedelta(minutes=45), status="confirmed")

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert slots
    assert slots[0]["start"].time() == dt.time(10, 45)


def test_booking_service_propose_slots_filters_active_booking_locks(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    asyncio.run(supabase_repo.acquire_booking_lock(
        clinic_id=settings.DEMO_CLINIC_ID,
        resource_id=settings.GOOGLE_CALENDAR_ID or "demo-calendar",
        start_at=start,
        end_at=start + dt.timedelta(minutes=45),
    ))

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert slots
    assert slots[0]["start"].time() == dt.time(10, 45)


def test_booking_service_propose_slots_ignores_cancelled_appointments_and_released_or_expired_locks(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    resource_id = settings.GOOGLE_CALENDAR_ID or "demo-calendar"
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    _create_internal_appointment(start, start + dt.timedelta(minutes=45), status="cancelled")
    asyncio.run(supabase_repo.acquire_booking_lock(
        clinic_id=settings.DEMO_CLINIC_ID,
        resource_id=resource_id,
        start_at=start,
        end_at=start + dt.timedelta(minutes=45),
    ))
    asyncio.run(supabase_repo.release_booking_lock(
        clinic_id=settings.DEMO_CLINIC_ID,
        resource_id=resource_id,
        start_at=start,
        end_at=start + dt.timedelta(minutes=45),
    ))
    asyncio.run(supabase_repo.acquire_booking_lock(
        clinic_id=settings.DEMO_CLINIC_ID,
        resource_id=resource_id,
        start_at=start + dt.timedelta(hours=1),
        end_at=start + dt.timedelta(hours=1, minutes=45),
    ))
    for lock in STORE.locks.values():
        if lock.get("start_at") == (start + dt.timedelta(hours=1)).isoformat():
            lock["status"] = "expired"

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert slots[0]["start"].time() == dt.time(10, 0)


def test_booking_service_propose_slots_returns_two_after_internal_filter(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    _create_internal_appointment(
        start.replace(hour=11),
        start.replace(hour=15),
        status="confirmed",
        calendar_event_id="internal-late-event",
    )

    slots = propose_slots(start, "sesion de fisioterapia", count=3, time_pref="morning")

    assert [slot["start"].time() for slot in slots] == [dt.time(10, 0), dt.time(10, 15)]


def test_confirm_slot_rejects_existing_internal_appointment_even_if_calendar_is_free(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 14, 10, 0)
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    _create_internal_appointment(start, start + dt.timedelta(minutes=45), status="confirmed")

    result = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id="CA-internal-double",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Ana Marco",
        contact_email="ana@example.com",
    ))

    assert not result.ok
    assert result.reason == "double_booking"
    assert len(STORE.appointments) == 1


def test_cancel_releases_lock_so_slot_can_be_offered_again(monkeypatch):
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 14, 10, 0)
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])

    created = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))
    assert created.ok
    cancelled = asyncio.run(cancel_appointment(created.appointment["id"]))
    assert cancelled.ok

    slots = propose_slots(start, "sesion de fisioterapia", count=1, time_pref="morning")

    assert slots[0]["start"].time() == dt.time(10, 0)


def test_booking_service_propose_slots_does_not_jump_to_another_day():
    start = (dt.datetime.now() + dt.timedelta(days=7)).replace(hour=16, minute=0, second=0, microsecond=0)

    slots = propose_slots(start, "sesion de fisioterapia", count=24, time_pref="afternoon")

    assert slots
    assert {slot["start"].date() for slot in slots} == {start.date()}


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
