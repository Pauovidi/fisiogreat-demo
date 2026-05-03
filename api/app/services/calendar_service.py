import base64
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import google.auth
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build

from ..config.settings import settings
from ..utils.logger import logger


SCOPES = ["https://www.googleapis.com/auth/calendar"]
DateLike = Union[dt.datetime, str]


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
    return bool(settings.USE_REAL_CALENDAR and settings.GOOGLE_CALENDAR_ID)


def _get_service():
    if not settings.USE_REAL_CALENDAR:
        return None
    if not settings.GOOGLE_CALENDAR_ID:
        raise RuntimeError("GOOGLE_CALENDAR_ID is required when USE_REAL_CALENDAR=true")
    if settings.GOOGLE_CREDENTIALS_JSON_BASE64:
        data = base64.b64decode(settings.GOOGLE_CREDENTIALS_JSON_BASE64).decode("utf-8")
        info = json.loads(data)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds, _ = google.auth.default(scopes=SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def ensure_aware(value: DateLike, timezone: str = "Europe/Madrid") -> dt.datetime:
    parsed = _as_dt(value) if isinstance(value, str) else value
    zone = ZoneInfo(timezone)
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def to_google_rfc3339(value: DateLike, timezone: str = "Europe/Madrid") -> str:
    return ensure_aware(value, timezone).isoformat()


def _as_dt(value: DateLike) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _mask_calendar_id(calendar_id: Optional[str]) -> str:
    if not calendar_id:
        return "unset"
    if len(calendar_id) <= 12:
        return f"{calendar_id[:3]}..."
    return f"{calendar_id[:6]}...{calendar_id[-6:]}"


def _http_error_details(exc: Exception) -> tuple[Optional[int], str]:
    if not isinstance(exc, HttpError):
        return None, repr(exc)
    status = getattr(exc.resp, "status", None)
    content = exc.content.decode("utf-8", errors="replace") if isinstance(exc.content, bytes) else str(exc.content)
    return status, content[:1000]


def _safe_freebusy_body(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "timeMin": body.get("timeMin"),
        "timeMax": body.get("timeMax"),
        "timeZone": body.get("timeZone"),
        "items": [{"id": _mask_calendar_id(item.get("id"))} for item in body.get("items", [])],
    }


def _safe_event_body(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": body.get("id"),
        "summary": body.get("summary"),
        "start": body.get("start"),
        "end": body.get("end"),
        "extendedProperties": {"private_keys": sorted(body.get("extendedProperties", {}).get("private", {}).keys())},
        "reminders": body.get("reminders"),
    }


def _is_date_only(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""))


def _filter_google_busy_periods(busy: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        period
        for period in busy
        if not (_is_date_only(period.get("start", "")) or _is_date_only(period.get("end", "")))
    ]


def _overlaps(start_a: dt.datetime, end_a: dt.datetime, start_b: dt.datetime, end_b: dt.datetime) -> bool:
    return start_a < end_b and start_b < end_a


def free_busy(start_at: dt.datetime, end_at: dt.datetime) -> List[Dict[str, str]]:
    svc = _get_service()
    timezone = settings.GOOGLE_CALENDAR_TIMEZONE
    start_rfc3339 = to_google_rfc3339(start_at, timezone)
    end_rfc3339 = to_google_rfc3339(end_at, timezone)
    if not svc:
        start_aware = ensure_aware(start_at, timezone)
        end_aware = ensure_aware(end_at, timezone)
        return [
            {"start": to_google_rfc3339(event.start_at, timezone), "end": to_google_rfc3339(event.end_at, timezone)}
            for event in CALENDAR_STORE.events.values()
            if event.status != "cancelled"
            and _overlaps(
                start_aware,
                end_aware,
                ensure_aware(event.start_at, timezone),
                ensure_aware(event.end_at, timezone),
            )
        ]

    body = {
        "timeMin": start_rfc3339,
        "timeMax": end_rfc3339,
        "timeZone": timezone,
        "items": [{"id": settings.GOOGLE_CALENDAR_ID}],
    }
    logger.info(
        "calendar_freebusy_request freebusy_time_min=%s freebusy_time_max=%s timezone=%s calendar_id=%s freebusy_request_body=%s",
        start_rfc3339,
        end_rfc3339,
        timezone,
        _mask_calendar_id(settings.GOOGLE_CALENDAR_ID),
        _safe_freebusy_body(body),
    )
    try:
        result = svc.freebusy().query(body=body).execute()
    except Exception as exc:
        status, content = _http_error_details(exc)
        logger.warning(
            "calendar_freebusy_error freebusy_error_status=%s freebusy_error_content=%r timezone=%s calendar_id=%s",
            status,
            content,
            timezone,
            _mask_calendar_id(settings.GOOGLE_CALENDAR_ID),
        )
        raise
    busy = result.get("calendars", {}).get(settings.GOOGLE_CALENDAR_ID, {}).get("busy", [])
    return _filter_google_busy_periods(busy)


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
    timezone = settings.GOOGLE_CALENDAR_TIMEZONE
    start_rfc3339 = to_google_rfc3339(start_at, timezone)
    end_rfc3339 = to_google_rfc3339(end_at, timezone)
    if not svc:
        logger.info(
            "calendar_create_attempt use_real_calendar=%s mode=mock event_id=%s",
            settings.USE_REAL_CALENDAR,
            event_id,
        )
        CALENDAR_STORE.events[event_id] = CalendarEvent(
            event_id,
            ensure_aware(start_at, timezone),
            ensure_aware(end_at, timezone),
            summary,
        )
        logger.info(
            "calendar_create_success use_real_calendar=%s mode=mock calendar_event_id=%s",
            settings.USE_REAL_CALENDAR,
            event_id,
        )
        return event_id

    body = {
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_rfc3339, "timeZone": timezone},
        "end": {"dateTime": end_rfc3339, "timeZone": timezone},
        "extendedProperties": {"private": {k: str(v) for k, v in (metadata or {}).items()}},
        "reminders": {"useDefault": True},
    }
    try:
        logger.info(
            "calendar_create_attempt use_real_calendar=%s mode=real event_id=%s calendar_id=%s create_event_start=%s create_event_end=%s timezone=%s create_event_request_body=%s",
            settings.USE_REAL_CALENDAR,
            event_id,
            _mask_calendar_id(settings.GOOGLE_CALENDAR_ID),
            start_rfc3339,
            end_rfc3339,
            timezone,
            _safe_event_body(body),
        )
        result = svc.events().insert(calendarId=settings.GOOGLE_CALENDAR_ID, body=body).execute()
    except Exception as exc:
        status, content = _http_error_details(exc)
        logger.warning(
            "calendar_create_error use_real_calendar=%s event_id=%s create_event_error_status=%s create_event_error_content=%r error=%r",
            settings.USE_REAL_CALENDAR,
            event_id,
            status,
            content,
            exc,
        )
        raise
    calendar_event_id = result.get("id", event_id)
    logger.info(
        "calendar_create_success use_real_calendar=%s calendar_event_id=%s",
        settings.USE_REAL_CALENDAR,
        calendar_event_id,
    )
    return calendar_event_id


def update_event(event_id: str, *, start_at: dt.datetime, end_at: dt.datetime, summary: Optional[str] = None) -> bool:
    svc = _get_service()
    timezone = settings.GOOGLE_CALENDAR_TIMEZONE
    start_rfc3339 = to_google_rfc3339(start_at, timezone)
    end_rfc3339 = to_google_rfc3339(end_at, timezone)
    if not svc:
        event = CALENDAR_STORE.events.get(event_id)
        if not event:
            return False
        CALENDAR_STORE.events[event_id] = CalendarEvent(
            event_id,
            ensure_aware(start_at, timezone),
            ensure_aware(end_at, timezone),
            summary or event.summary,
            event.status,
        )
        return True

    event = svc.events().get(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    event["start"] = {"dateTime": start_rfc3339, "timeZone": timezone}
    event["end"] = {"dateTime": end_rfc3339, "timeZone": timezone}
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
            "start": {"dateTime": to_google_rfc3339(event.start_at, settings.GOOGLE_CALENDAR_TIMEZONE)},
            "end": {"dateTime": to_google_rfc3339(event.end_at, settings.GOOGLE_CALENDAR_TIMEZONE)},
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
