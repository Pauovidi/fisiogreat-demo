import re
from typing import Optional

from .date_parser import parse_spanish_day
from .emergency_guard import detect_emergency
from .intent_router import detect_service, normalize_text, route_message
from .booking_requirements import extract_contact
from .logger import logger


def first_name(patient_name: Optional[str]) -> Optional[str]:
    if not patient_name:
        return None
    stripped = patient_name.strip()
    return stripped.split()[0] if stripped else None


def is_generic_profile_name(profile_name: str) -> bool:
    normalized = normalize_text(profile_name)
    return not normalized or normalized in {"whatsapp", "usuario", "user", "cliente", "fisio", "fisiogreat"}


def parse_patient_name(body: str) -> Optional[str]:
    return extract_patient_name_from_voice(body)


def extract_patient_name_from_voice(text: str) -> Optional[str]:
    normalized = normalize_text(text)
    result: Optional[str] = None
    words_count = 0

    def finish(value: Optional[str]) -> Optional[str]:
        logger.info(
            "patient_name_parse_attempt=true patient_name_parse_result=%s patient_name_words_count=%s",
            "accepted" if value else "rejected",
            words_count,
        )
        return value

    if not normalized:
        return finish(None)
    if detect_emergency(text).detected:
        return finish(None)
    if parse_spanish_day(text) or detect_service(text):
        return finish(None)
    if extract_contact(text) != (None, None):
        return finish(None)
    if _looks_like_consultation_reason(normalized):
        return finish(None)

    route = route_message(text)
    if route["type"] != "fallback":
        return finish(None)
    if normalized in RESET_OR_CONTROL_PHRASES:
        return finish(None)

    cleaned = _strip_voice_name_prefixes(text)
    cleaned = _normalize_name_punctuation(cleaned)
    words_count = len(cleaned.split())
    if not _looks_like_patient_name(cleaned):
        return finish(None)

    result = _format_patient_name(cleaned)
    return finish(result)


RESET_OR_CONTROL_PHRASES = {
    "reiniciar",
    "reiniciar conversacion",
    "reset",
    "resetear",
    "empezar de nuevo",
    "volver a empezar",
    "borrar conversacion",
    "limpiar conversacion",
    "cancelar flujo",
    "salir",
    "olvida lo anterior",
}

VOICE_FILLERS = {
    "ah",
    "eh",
    "pues",
    "vale",
    "si",
    "sí",
    "claro",
    "correcto",
}

NAME_PREFIX_PATTERNS = [
    r"^me llamo\s+(.+)$",
    r"^soy\s+(.+)$",
    r"^a nombre de\s+(.+)$",
    r"^el nombre es\s+(.+)$",
]

CONSULTATION_REASON_PATTERNS = [
    r"\bme duele\b",
    r"\bme molesta\b",
    r"\btengo\b.*\b(dolor|contractura|sobrecarga|molestia|lesion|lesión)\b",
    r"\bvengo por\b",
    r"\bdolor\b",
    r"\bcontractura\b",
    r"\bsobrecarga\b",
    r"\brodilla\b",
    r"\bespalda\b",
    r"\bcervical\b",
    r"\blumbar\b",
    r"\bhombro\b",
    r"\btobillo\b",
    r"\bcadera\b",
    r"\bpecho\b",
    r"\brespirar\b",
]


def _strip_voice_name_prefixes(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    while True:
        match = re.match(r"^\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)[,.\s]+(.+)$", cleaned)
        if not match:
            break
        filler = normalize_text(match.group(1))
        if filler not in {normalize_text(item) for item in VOICE_FILLERS}:
            break
        cleaned = match.group(2).strip()

    for pattern in NAME_PREFIX_PATTERNS:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
            break
    return cleaned


def _normalize_name_punctuation(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^[,.;:¡!¿?\s]+", "", cleaned)
    cleaned = re.sub(r"[,.;:¡!¿?\s]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([.])", r"\1", cleaned)
    cleaned = re.sub(r"\.(?=\s|$)", "", cleaned)
    return cleaned.strip()


def _looks_like_patient_name(value: str) -> bool:
    if not value:
        return False
    if len(value) > 60:
        return False
    if not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]{2,60}", value):
        return False
    parts = value.split()
    if not 1 <= len(parts) <= 4:
        return False
    normalized_parts = [normalize_text(part) for part in parts]
    if any(part in RESET_OR_CONTROL_PHRASES for part in normalized_parts):
        return False
    if any(len(part.replace("'", "").replace("-", "")) < 1 for part in parts):
        return False
    return True


def _looks_like_consultation_reason(normalized: str) -> bool:
    return any(re.search(pattern, normalized) for pattern in CONSULTATION_REASON_PATTERNS)


def _format_patient_name(value: str) -> str:
    formatted = []
    for token in value.split():
        pieces = token.split("-")
        formatted.append("-".join(_format_name_piece(piece) for piece in pieces))
    return " ".join(formatted)


def _format_name_piece(piece: str) -> str:
    if len(piece) == 1:
        return piece.upper()
    if "'" in piece:
        return "'".join(_format_name_piece(part) for part in piece.split("'"))
    return piece[:1].upper() + piece[1:].lower()
