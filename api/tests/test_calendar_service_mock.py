import datetime as dt

from app.config.settings import settings
from app.services import calendar_service
from app.services.calendar_service import CALENDAR_STORE, create_event, find_event_by_id, free_busy


def test_calendar_mock_create_and_free_busy():
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    end = dt.datetime(2026, 5, 4, 10, 45)

    create_event(event_id="fgtest1", summary="FisioGreat", start_at=start, end_at=end)

    busy = free_busy(start, end)
    assert busy == [{"start": start.isoformat(), "end": end.isoformat()}]
    assert find_event_by_id("fgtest1")["id"] == "fgtest1"


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
