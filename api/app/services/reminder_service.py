import datetime as dt
from typing import Any, Dict, List

from . import supabase_repo


async def enqueue_demo_reminder(*, appointment_id: str, due_at: dt.datetime, channel: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await supabase_repo.create_reminder_job(
        appointment_id=appointment_id,
        due_at=due_at.isoformat(),
        channel=channel,
        payload=payload,
    )


async def send_due_reminders_once(now: dt.datetime | None = None) -> List[Dict[str, Any]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    sent: List[Dict[str, Any]] = []
    for job in supabase_repo.STORE.reminder_jobs.values():
        if job.get("status") != "pending":
            continue
        due_at_raw = job.get("due_at")
        if not due_at_raw:
            continue
        due_at = dt.datetime.fromisoformat(due_at_raw.replace("Z", "+00:00"))
        if due_at <= now:
            job["status"] = "sent"
            job["sent_at"] = now.isoformat()
            sent.append(job)
    return sent
