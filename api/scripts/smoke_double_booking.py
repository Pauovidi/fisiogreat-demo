import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.booking_service import confirm_slot
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE


async def run():
    STORE.reset()
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    first = await confirm_slot(
        channel="whatsapp",
        external_user_id="+34600999004",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Pau Marco",
    )
    second = await confirm_slot(
        channel="voice",
        external_user_id="CA-smoke-double",
        service_type="sesion de fisioterapia",
        start_at=start,
        patient_name="Ana Marco",
    )
    print(f"FIRST_OK: {first.ok}")
    print(f"SECOND_OK: {second.ok}")
    print(f"SECOND_REASON: {second.reason}")


if __name__ == "__main__":
    asyncio.run(run())
