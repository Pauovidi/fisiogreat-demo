import datetime as dt

from app.config.settings import settings
from app.services import calendar_service
from app.services.calendar_service import CALENDAR_STORE, create_event, find_event_by_id, free_busy


class _FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _FakeFreebusy:
    def __init__(self, calls):
        self.calls = calls

    def query(self, *, body):
        self.calls["freebusy_body"] = body
        return _FakeRequest({"calendars": {"calendar-real": {"busy": []}}})


class _FakeEvents:
    def __init__(self, calls):
        self.calls = calls

    def insert(self, *, calendarId, body):
        self.calls["insert_calendar_id"] = calendarId
        self.calls["insert_body"] = body
        return _FakeRequest({"id": "real-event-id"})


class _FakeCalendarService:
    def __init__(self, calls):
        self.calls = calls

    def freebusy(self):
        return _FakeFreebusy(self.calls)

    def events(self):
        return _FakeEvents(self.calls)


def test_calendar_mock_create_and_free_busy():
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    end = dt.datetime(2026, 5, 4, 10, 45)

    create_event(event_id="fgtest1", summary="FisioGreat", start_at=start, end_at=end)

    busy = free_busy(start, end)
    assert busy == [{"start": "2026-05-04T10:00:00+02:00", "end": "2026-05-04T10:45:00+02:00"}]
    assert find_event_by_id("fgtest1")["id"] == "fgtest1"


def test_naive_datetime_becomes_europe_madrid_offset_in_may(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_TIMEZONE", "Europe/Madrid")

    value = calendar_service.to_google_rfc3339(dt.datetime(2026, 5, 4, 10, 0), "Europe/Madrid")

    assert value == "2026-05-04T10:00:00+02:00"


def test_naive_iso_string_becomes_europe_madrid_offset_in_may(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_TIMEZONE", "Europe/Madrid")

    value = calendar_service.to_google_rfc3339("2026-05-04T10:45:00", "Europe/Madrid")

    assert value == "2026-05-04T10:45:00+02:00"


def test_real_free_busy_sends_rfc3339_offsets(monkeypatch):
    calls = {}
    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_TIMEZONE", "Europe/Madrid")
    monkeypatch.setattr(calendar_service, "_get_service", lambda: _FakeCalendarService(calls))

    result = free_busy(dt.datetime(2026, 5, 4, 10, 0), dt.datetime(2026, 5, 4, 10, 45))

    assert result == []
    assert calls["freebusy_body"] == {
        "timeMin": "2026-05-04T10:00:00+02:00",
        "timeMax": "2026-05-04T10:45:00+02:00",
        "timeZone": "Europe/Madrid",
        "items": [{"id": "calendar-real"}],
    }


def test_real_create_event_sends_rfc3339_offsets(monkeypatch):
    calls = {}
    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_TIMEZONE", "Europe/Madrid")
    monkeypatch.setattr(calendar_service, "_get_service", lambda: _FakeCalendarService(calls))

    event_id = create_event(
        event_id="fgreal1",
        summary="FisioGreat",
        start_at=dt.datetime(2026, 5, 4, 10, 0),
        end_at=dt.datetime(2026, 5, 4, 10, 45),
    )

    assert event_id == "real-event-id"
    assert calls["insert_calendar_id"] == "calendar-real"
    assert calls["insert_body"]["start"] == {
        "dateTime": "2026-05-04T10:00:00+02:00",
        "timeZone": "Europe/Madrid",
    }
    assert calls["insert_body"]["end"] == {
        "dateTime": "2026-05-04T10:45:00+02:00",
        "timeZone": "Europe/Madrid",
    }


def test_calendar_real_mode_uses_adc_when_base64_credentials_missing(monkeypatch):
    calls = {}

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON_BASE64", None)

    def fake_default(*, scopes):
        calls["scopes"] = scopes
        return object(), "demo-project"

    def fake_build(api_name, api_version, *, credentials, cache_discovery):
        calls["build"] = (api_name, api_version, credentials, cache_discovery)
        return "calendar-service"

    monkeypatch.setattr(calendar_service.google.auth, "default", fake_default)
    monkeypatch.setattr(calendar_service, "build", fake_build)

    assert calendar_service.calendar_configured()
    assert calendar_service._get_service() == "calendar-service"
    assert calls["scopes"] == calendar_service.SCOPES
    assert calls["build"][0:2] == ("calendar", "v3")
