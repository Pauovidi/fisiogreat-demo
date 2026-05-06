import re
import unicodedata
from typing import Optional, Tuple


PHYSIO_SESSION = "sesion de fisioterapia"


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def is_physiotherapy_session(service: Optional[str]) -> bool:
    return normalize_text(service or "") == PHYSIO_SESSION


def clean_consultation_reason(value: str) -> Optional[str]:
    reason = " ".join((value or "").strip().split())
    normalized = normalize_text(reason)
    if not reason or len(normalized) < 4:
        return None
    if normalized in {"no se", "nose", "no lo se", "ni idea", "no estoy seguro", "no estoy segura"}:
        return None
    return reason


def extract_contact(value: str) -> Tuple[Optional[str], Optional[str]]:
    text = (value or "").strip()
    email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    if email_match:
        return None, email_match.group(0)

    digits = re.sub(r"\D", "", text)
    if digits.startswith("00"):
        digits = digits[2:]
    if 9 <= len(digits) <= 15:
        return digits, None
    return None, None


def confirms_current_phone(value: str) -> bool:
    normalized = normalize_text(value)
    return normalized in {
        "si",
        "si a este numero",
        "si en este numero",
        "a este numero",
        "ese numero",
        "al mismo numero",
        "vale",
        "correcto",
        "perfecto",
    }
