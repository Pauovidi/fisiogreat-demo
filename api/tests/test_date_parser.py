import datetime as dt

import pytest

from app.utils.date_parser import parse_spanish_day, parse_time_pref


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
