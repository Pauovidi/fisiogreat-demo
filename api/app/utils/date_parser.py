import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Optional
from zoneinfo import ZoneInfo


WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sabao": 5,
    "savao": 5,
    "domingo": 6,
}


@dataclass(frozen=True)
class VoiceDateParse:
    target_date: Optional[dt.date]
    raw_target_date: Optional[dt.date]
    time_pref: Optional[Literal["morning", "afternoon"]]
    explicit_weekday: Optional[int]
    validation_result: Literal["ok", "mismatch", "ambiguous"]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_spanish_day(text: str, tz: str = "Europe/Madrid", *, today: Optional[dt.date] = None) -> Optional[dt.date]:
    normalized = _normalize(text)
    today = today or _today(tz)

    if _has_day_after_tomorrow(normalized):
        return today + dt.timedelta(days=2)
    if _has_tomorrow(normalized):
        return today + dt.timedelta(days=1)
    if re.search(r"\bhoy\b", normalized):
        return today

    for day_name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{day_name}\b", normalized):
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + dt.timedelta(days=days_ahead)

    return None


def extract_explicit_weekday(text: str) -> Optional[int]:
    normalized = _normalize(text)
    for day_name, weekday in WEEKDAYS.items():
        if re.search(rf"\b{day_name}\b", normalized):
            return weekday
    return None


def parse_voice_date(
    text: str,
    tz: str = "Europe/Madrid",
    *,
    today: Optional[dt.date] = None,
) -> VoiceDateParse:
    raw_target_date = parse_spanish_day(text, tz, today=today)
    explicit_weekday = extract_explicit_weekday(text)
    time_pref = parse_time_pref(text)

    if raw_target_date and explicit_weekday is not None and raw_target_date.weekday() != explicit_weekday:
        return VoiceDateParse(
            target_date=None,
            raw_target_date=raw_target_date,
            time_pref=time_pref,
            explicit_weekday=explicit_weekday,
            validation_result="mismatch",
        )

    if raw_target_date:
        return VoiceDateParse(
            target_date=raw_target_date,
            raw_target_date=raw_target_date,
            time_pref=time_pref,
            explicit_weekday=explicit_weekday,
            validation_result="ok",
        )

    return VoiceDateParse(
        target_date=None,
        raw_target_date=None,
        time_pref=time_pref,
        explicit_weekday=explicit_weekday,
        validation_result="ambiguous",
    )


def _today(tz: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(tz)).date()


def _has_day_after_tomorrow(normalized: str) -> bool:
    return re.search(r"\bpasado\s+manana\b", normalized) is not None


def _has_tomorrow(normalized: str) -> bool:
    tokens = normalized.split()
    for index, token in enumerate(tokens):
        if token != "manana":
            continue
        previous = tokens[index - 1] if index > 0 else ""
        previous_previous = tokens[index - 2] if index > 1 else ""
        if previous == "pasado":
            continue
        if previous == "la":
            continue
        if previous in {"por", "de"}:
            continue
        return True
    return False


def parse_time_pref(text: str) -> Optional[Literal["morning", "afternoon"]]:
    normalized = _normalize(text)
    if any(phrase in normalized for phrase in ["mas tarde", "mas temprano", "mas pronto"]):
        return None
    if any(phrase in normalized for phrase in ["por la manana", "de manana", "a la manana", "temprano", "a primera hora"]):
        return "morning"
    if any(
        phrase in normalized
        for phrase in ["por la tarde", "de tarde", "a la tarde", "por tarde", "despues de comer", "a ultima hora"]
    ):
        return "afternoon"
    if re.search(r"\btarde\b", normalized) and "manana" not in normalized and "mas tarde" not in normalized:
        return "afternoon"
    return None
