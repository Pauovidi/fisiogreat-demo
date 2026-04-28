import datetime as dt

from app.services.calendar_service import CALENDAR_STORE, create_event, find_event_by_id, free_busy


def test_calendar_mock_create_and_free_busy():
    CALENDAR_STORE.reset()
    start = dt.datetime(2026, 5, 4, 10, 0)
    end = dt.datetime(2026, 5, 4, 10, 45)

    create_event(event_id="fgtest1", summary="FisioGreat", start_at=start, end_at=end)

    busy = free_busy(start, end)
    assert busy == [{"start": start.isoformat(), "end": end.isoformat()}]
    assert find_event_by_id("fgtest1")["id"] == "fgtest1"
