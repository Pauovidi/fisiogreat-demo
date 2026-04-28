import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.reminder_service import enqueue_demo_reminder, send_due_reminders_once
from app.services.supabase_repo import STORE


async def run():
    STORE.reset()
    now = dt.datetime.now(dt.timezone.utc)
    await enqueue_demo_reminder(
        appointment_id="demo-appointment",
        due_at=now - dt.timedelta(minutes=1),
        channel="whatsapp",
        payload={"message": "Recordatorio demo FisioGreat"},
    )
    sent = await send_due_reminders_once(now)
    print(f"SENT_COUNT: {len(sent)}")


if __name__ == "__main__":
    asyncio.run(run())
