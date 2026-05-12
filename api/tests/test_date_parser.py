import datetime as dt

import pytest

from app.utils.date_parser import extract_explicit_weekday, parse_spanish_day, parse_time_pref, parse_voice_date


MONDAY_2026_05_11 = dt.date(2026, 5, 11)


@pytest.mark.parametrize(
    ("text", "expected_date", "expected_pref"),
    [
        ("el jueves que viene por la mañana", dt.date(2026, 5, 14), "morning"),
        ("jueves", dt.date(2026, 5, 14), None),
        ("jueves por la mañana", dt.date(2026, 5, 14), "morning"),
        ("martes", dt.date(2026, 5, 12), None),
        ("miércoles", dt.date(2026, 5, 13), None),
        ("mañana", dt.date(2026, 5, 12), None),
        ("mañana por la mañana", dt.date(2026, 5, 12), "morning"),
        ("el viernes por la tarde", dt.date(2026, 5, 15), "afternoon"),
    ],
)
def test_parse_spanish_day_distinguishes_tomorrow_from_morning(text, expected_date, expected_pref):
    assert parse_spanish_day(text, today=MONDAY_2026_05_11) == expected_date
    assert parse_time_pref(text) == expected_pref


def test_parse_spanish_day_does_not_treat_morning_only_as_tomorrow():
    assert parse_spanish_day("por la mañana", today=MONDAY_2026_05_11) is None
    assert parse_time_pref("por la mañana") == "morning"


@pytest.mark.parametrize(
    ("text", "expected_weekday"),
    [
        ("jueves", 3),
        ("el jueves que viene", 3),
        ("el jueves por la mañana", 3),
        ("miércoles", 2),
        ("el miércoles por la tarde", 2),
    ],
)
def test_extract_explicit_weekday(text, expected_weekday):
    assert extract_explicit_weekday(text) == expected_weekday


@pytest.mark.parametrize(
    ("text", "expected_date", "expected_pref"),
    [
        ("jueves", dt.date(2026, 5, 14), None),
        ("el jueves que viene", dt.date(2026, 5, 14), None),
        ("el jueves por la mañana", dt.date(2026, 5, 14), "morning"),
        ("miércoles", dt.date(2026, 5, 13), None),
        ("martes", dt.date(2026, 5, 12), None),
        ("mañana", dt.date(2026, 5, 12), None),
    ],
)
def test_parse_voice_date_validates_explicit_weekday(text, expected_date, expected_pref):
    result = parse_voice_date(text, today=MONDAY_2026_05_11)

    assert result.target_date == expected_date
    assert result.raw_target_date == expected_date
    assert result.time_pref == expected_pref
    assert result.validation_result == "ok"


def test_parse_voice_date_rejects_weekday_mismatch(monkeypatch):
    monkeypatch.setattr("app.utils.date_parser.parse_spanish_day", lambda *_args, **_kwargs: dt.date(2026, 5, 13))

    result = parse_voice_date("el jueves por la mañana", today=MONDAY_2026_05_11)

    assert result.target_date is None
    assert result.raw_target_date == dt.date(2026, 5, 13)
    assert result.explicit_weekday == 3
    assert result.time_pref == "morning"
    assert result.validation_result == "mismatch"
