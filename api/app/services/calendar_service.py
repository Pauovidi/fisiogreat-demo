import base64
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..config.settings import settings
from ..utils.logger import logger


SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    start_at: dt.datetime
    end_at: dt.datetime
    summary: str
    status: str = "confirmed"


class InMemoryCalendarStore:
    def __init__(self):
        self.events: Dict[str, CalendarEvent] = {}

    def reset(self):
        self.events.clear()


CALENDAR_STORE = InMemoryCalendarStore()


def calendar_configured() -> bool:
    return bool(
        settings.USE_REAL_CALENDAR
        and settings.GOOGLE_CREDENTIALS_JSON_BASE64
        and settings.GOOGLE_CALENDAR_ID
    )


def _get_service():
    if not calendar_configured():
        return None
    data = base64.b64decode(settings.GOOGLE_CREDENTIALS_JSON_BASE64).decode("utf-8")
    info = json.loads(data)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _rfc3339(value: dt.datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.isoformat()


def _as_dt(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _overlaps(start_a: dt.datetime, end_a: dt.datetime, start_b: dt.datetime, end_b: dt.datetime) -> bool:
    return start_a < end_b and start_b < end_a


def free_busy(start_at: dt.datetime, end_at: dt.datetime) -> List[Dict[str, str]]:
    svc = _get_service()
    if not svc:
        return [
            {"start": _rfc3339(event.start_at), "end": _rfc3339(event.end_at)}
            for event in CALENDAR_STORE.events.values()
            if event.status != "cancelled" and _overlaps(start_at, end_at, event.start_at, event.end_at)
        ]

    body = {
        "timeMin": _rfc3339(start_at),
        "timeMax": _rfc3339(end_at),
        "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE,
        "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
    }
    result = svc.freebusy().query(body=body).execute()
    return result.get("calendars", {}).get(settings.GOOGLE_CALENDAR_ID, {}).get("busy", [])


def build_deterministic_event_id(
    clinic_id: str,
    external_user_id: str,
    service_type: str,
    start_at: dt.datetime,
) -> str:
    seed = f"{clinic_id}|{external_user_id}|{service_type}|{start_at.isoformat()}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return re.sub(r"[^a-z0-9]", "", f"fg{digest}".lower())


def create_event(
    *,
    event_id: str,
    summary: str,
    start_at: dt.datetime,
    end_at: dt.datetime,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    svc = _get_service()
    if not svc:
        CALENDAR_STORE.events[event_id] = CalendarEvent(event_id, start_at, end_at, summary)
        return event_id

    body = {
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": _rfc3339(start_at), "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE},
        "end": {"dateTime": _rfc3339(end_at), "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE},
        "extendedProperties": {"private": {k: str(v) for k, v in (metadata or {}).items()}},
        "reminders": {"useDefault": True},
    }
    try:
        result = svc.events().insert(calendarId=settings.GOOGLE_CALENDAR_ID, body=body).execute()
    except Exception as exc:
        logger.warning(f"calendar_create_event_failed event_id={event_id!r} error={exc!r}")
        raise
    return result.get("id", event_id)


def update_event(event_id: str, *, start_at: dt.datetime, end_at: dt.datetime, summary: Optional[str] = None) -> bool:
    svc = _get_service()
    if not svc:
        event = CALENDAR_STORE.events.get(event_id)
        if not event:
            return False
        CALENDAR_STORE.events[event_id] = CalendarEvent(
            event_id,
            start_at,
            end_at,
            summary or event.summary,
            event.status,
        )
        return True

    event = svc.events().get(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    event["start"] = {"dateTime": _rfc3339(start_at), "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE}
    event["end"] = {"dateTime": _rfc3339(end_at), "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE}
    if summary:
        event["summary"] = summary
    svc.events().update(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id, body=event).execute()
    return True


def delete_event(event_id: str) -> bool:
    svc = _get_service()
    if not svc:
        event = CALENDAR_STORE.events.get(event_id)
        if event:
            CALENDAR_STORE.events[event_id] = CalendarEvent(
                event.id, event.start_at, event.end_at, event.summary, "cancelled"
            )
        return bool(event)

    svc.events().delete(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    return True


def find_event_by_id(event_id: str) -> Optional[Dict[str, Any]]:
    svc = _get_service()
    if not svc:
        event = CALENDAR_STORE.events.get(event_id)
        if not event:
            return None
        return {
            "id": event.id,
            "summary": event.summary,
            "status": event.status,
            "start": {"dateTime": _rfc3339(event.start_at)},
            "end": {"dateTime": _rfc3339(event.end_at)},
        }

    try:
        return svc.events().get(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    except Exception:
        return None


def event_time_from_google(event: Dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    return (
        _as_dt(event["start"]["dateTime"]),
        _as_dt(event["end"]["dateTime"]),
    )
