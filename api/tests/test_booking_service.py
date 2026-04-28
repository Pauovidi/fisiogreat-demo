import datetime as dt
import asyncio

from app.services.booking_service import confirm_slot, get_appointment_status, propose_slots
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE


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


def test_booking_service_proposes_slots_without_secrets():
    CALENDAR_STORE.reset()
    slots = propose_slots(dt.datetime(2026, 5, 4, 10, 0), "sesion de fisioterapia")
    assert slots
    assert slots[0]["service"] == "sesion de fisioterapia"
