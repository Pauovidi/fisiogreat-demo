import datetime as dt
import re
import unicodedata
from typing import Any, Dict, List, Optional


WEEKDAYS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]

VOICE_ORDINALS = ["primera", "segunda", "tercera", "cuarta", "quinta", "sexta"]


def parse_appointment_start(appointment: Dict[str, Any]) -> Optional[dt.datetime]:
    value = appointment.get("start_at")
    if isinstance(value, dt.datetime):
        return value
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def format_appointment_option_whatsapp(appointment: Dict[str, Any], index: Optional[int] = None) -> str:
    service = _display_service(appointment.get("service_type"))
    start_at = parse_appointment_start(appointment)
    if start_at:
        label = f"{service} - {WEEKDAYS[start_at.weekday()]} {start_at.strftime('%d/%m')} a las {start_at.strftime('%H:%M')}"
    else:
        label = service
    if index is not None:
        return f"{index}. {label}"
    return label


def format_appointment_option_voice(appointment: Dict[str, Any], index: Optional[int] = None) -> str:
    service = _display_service(appointment.get("service_type"))
    start_at = parse_appointment_start(appointment)
    if start_at:
        label = f"{service} el {WEEKDAYS[start_at.weekday()]} a las {start_at.strftime('%H:%M')}"
    else:
        label = service
    if index is not None:
        ordinal = VOICE_ORDINALS[index - 1] if 0 < index <= len(VOICE_ORDINALS) else str(index)
        return f"{ordinal}, {label}"
    return label


def pick_appointment_option(text: str, appointments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not appointments:
        return None
    normalized = _normalize(text)

    index = _pick_index(normalized, len(appointments))
    if index is not None:
        return appointments[index]

    time_matches = re.findall(r"\b(\d{1,2})(?::(\d{2}))?\b", normalized)
    for hour_text, minute_text in time_matches:
        hour = int(hour_text)
        minute = int(minute_text or "0")
        if hour < 7:
            continue
        matches = []
        for appointment in appointments:
            start_at = parse_appointment_start(appointment)
            if start_at and start_at.hour == hour and (not minute_text or start_at.minute == minute):
                matches.append(appointment)
        if len(matches) == 1:
            return matches[0]

    service_matches = [
        appointment
        for appointment in appointments
        if _service_matches(normalized, appointment.get("service_type"))
    ]
    if len(service_matches) == 1:
        return service_matches[0]

    weekday_matches = [
        appointment
        for appointment in appointments
        if _weekday_matches(normalized, appointment)
    ]
    if len(weekday_matches) == 1:
        return weekday_matches[0]

    return None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _display_service(service: Optional[str]) -> str:
    normalized = _normalize(service or "")
    if "valoracion" in normalized:
        return "Valoración inicial"
    if "seguimiento" in normalized:
        return "Consulta de seguimiento"
    if "fisio" in normalized or "fisioterapia" in normalized:
        return "Sesión de fisioterapia"
    return (service or "Cita").strip().capitalize()


def _pick_index(normalized: str, count: int) -> Optional[int]:
    patterns = [
        (0, [r"\b1\b", r"\buno\b", r"\buna\b", r"\bprimera\b", r"\bla primera\b"]),
        (1, [r"\b2\b", r"\bdos\b", r"\bsegunda\b", r"\bla segunda\b"]),
        (2, [r"\b3\b", r"\btres\b", r"\btercera\b", r"\bla tercera\b"]),
        (3, [r"\b4\b", r"\bcuatro\b", r"\bcuarta\b", r"\bla cuarta\b"]),
    ]
    for index, candidates in patterns:
        if index < count and any(re.search(pattern, normalized) for pattern in candidates):
            return index
    explicit = re.search(r"\b(?:opcion|cita|cambiar|cancelar)\s+([1-4])\b", normalized)
    if explicit:
        index = int(explicit.group(1)) - 1
        if index < count:
            return index
    return None


def _service_matches(normalized: str, service: Optional[str]) -> bool:
    normalized_service = _normalize(service or "")
    if "valoracion" in normalized and "valoracion" in normalized_service:
        return True
    if "seguimiento" in normalized and "seguimiento" in normalized_service:
        return True
    if ("fisio" in normalized or "fisioterapia" in normalized) and (
        "fisio" in normalized_service or "fisioterapia" in normalized_service
    ):
        return True
    return False


def _weekday_matches(normalized: str, appointment: Dict[str, Any]) -> bool:
    start_at = parse_appointment_start(appointment)
    if not start_at:
        return False
    weekday = _normalize(WEEKDAYS[start_at.weekday()])
    return re.search(rf"\b{weekday}\b", normalized) is not None
