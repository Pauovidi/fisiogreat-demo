import datetime as dt
import re
import unicodedata
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


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_spanish_day(text: str, tz: str = "Europe/Madrid") -> Optional[dt.date]:
    normalized = _normalize(text)
    today = dt.datetime.now(ZoneInfo(tz)).date()

    if re.search(r"\bpasado man(?:a|i)na\b", normalized):
        return today + dt.timedelta(days=2)
    if re.search(r"\bman(?:a|i)na\b", normalized):
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
