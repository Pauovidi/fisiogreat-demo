import datetime as dt
import asyncio

from app.services.booking_service import confirm_slot
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE


def test_double_booking_lock_blocks_same_slot():
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)

    first = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id="+34600000001",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    ))
    second = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id="CA-double",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Ana Marco",
        contact_phone="640786765",
    ))

    assert first.ok
    assert not second.ok
    assert second.reason in {"double_booking", "calendar_busy"}
