import base64, json, datetime as dt
from typing import List, Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from ..config.settings import settings
from ..utils.logger import logger

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def _get_service():
    if not settings.GOOGLE_CREDENTIALS_JSON_BASE64 or not settings.GOOGLE_CALENDAR_ID:
        return None
    data = base64.b64decode(settings.GOOGLE_CREDENTIALS_JSON_BASE64).decode("utf-8")
    info = json.loads(data)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def create_event(summary: str, start_iso: str, end_iso: str | None, description: str = "") -> Optional[str]:
    svc = _get_service()
    if not svc:
        logger.warning("Google Calendar no configurado; saltando creación de evento")
        return None
    start = {"dateTime": start_iso}
    end = {"dateTime": end_iso or start_iso}
    event = {"summary": summary, "description": description, "start": start, "end": end, "reminders": {"useDefault": True}}
    res = svc.events().insert(calendarId=settings.GOOGLE_CALENDAR_ID, body=event).execute()
    return res.get("id")

def list_events_between(start_iso: str, end_iso: str) -> List[dict]:
    svc = _get_service()
    if not svc:
        logger.warning("Google Calendar no configurado; list_events_between vacío")
        return []
    events, page_token = [], None
    while True:
        res = svc.events().list(calendarId=settings.GOOGLE_CALENDAR_ID, timeMin=start_iso, timeMax=end_iso,
                                singleEvents=True, orderBy="startTime", pageToken=page_token).execute()
        events.extend(res.get("items", []))
        page_token = res.get("nextPageToken")
        if not page_token: break
    return events

def find_event_around(ts: dt.datetime, window_minutes: int = 120) -> Optional[dict]:
    start = (ts - dt.timedelta(minutes=window_minutes)).isoformat() + "Z"
    end = (ts + dt.timedelta(minutes=window_minutes)).isoformat() + "Z"
    candidates = list_events_between(start, end)
    best, best_delta = None, None
    for ev in candidates:
        s = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
        if not s: continue
        sdt = dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        delta = abs((sdt - ts.replace(tzinfo=dt.timezone.utc)).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = ev, delta
    return best

def delete_event(event_id: str) -> bool:
    svc = _get_service()
    if not svc: 
        logger.warning("Google Calendar no configurado; no se borra evento")
        return False
    svc.events().delete(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    return True

def update_event_time(event_id: str, new_start_iso: str, new_end_iso: str) -> bool:
    svc = _get_service()
    if not svc: 
        logger.warning("Google Calendar no configurado; no se actualiza evento")
        return False
    ev = svc.events().get(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    ev["start"] = {"dateTime": new_start_iso}; ev["end"] = {"dateTime": new_end_iso}
    svc.events().update(calendarId=settings.GOOGLE_CALENDAR_ID, eventId=event_id, body=ev).execute()
    return True