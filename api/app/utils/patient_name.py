import re
from typing import Optional

from .date_parser import parse_spanish_day
from .intent_router import detect_service, normalize_text, route_message


def first_name(patient_name: Optional[str]) -> Optional[str]:
    if not patient_name:
        return None
    stripped = patient_name.strip()
    return stripped.split()[0] if stripped else None


def is_generic_profile_name(profile_name: str) -> bool:
    normalized = normalize_text(profile_name)
    return not normalized or normalized in {"whatsapp", "usuario", "user", "cliente", "fisio", "fisiogreat"}


def parse_patient_name(body: str) -> Optional[str]:
    normalized = normalize_text(body)
    if not normalized:
        return None
    if parse_spanish_day(body) or detect_service(body):
        return None
    route = route_message(body)
    if route["type"] in {
        "faq",
        "cancel",
        "reschedule",
        "booking",
        "more_options",
        "thanks",
        "acknowledgement",
        "farewell",
        "out_of_scope",
        "greeting",
        "pick_slot",
        "unsupported_service",
    }:
        return None

    cleaned = body.strip()
    patterns = [
        r"^\s*me llamo\s+(.+)$",
        r"^\s*soy\s+(.+)$",
        r"^\s*a nombre de\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
            break

    cleaned = re.sub(r"\s+", " ", cleaned)
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]{2,60}", cleaned):
        return None
    return " ".join(part.capitalize() for part in cleaned.split())
