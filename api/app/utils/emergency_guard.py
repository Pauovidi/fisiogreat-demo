import re
import unicodedata
from dataclasses import dataclass


VOICE_EMERGENCY_REPLY = (
    "Por lo que me indicas, esto puede requerir atencion urgente. "
    "Llama al 112 o acude a urgencias de inmediato."
)

WHATSAPP_EMERGENCY_REPLY = (
    "Por lo que me indicas, esto puede requerir atencion urgente. "
    "Llama al 112 o acude a urgencias de inmediato. "
    "No voy a agendar ninguna cita desde aqui para este caso."
)


@dataclass(frozen=True)
class EmergencyDetection:
    detected: bool
    matched: str | None = None


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


EMERGENCY_PATTERNS = {
    "dolor toracico": ("dolor toracico",),
    "dolor en el pecho": ("dolor en el pecho", "me duele el pecho"),
    "dificultad respiratoria": ("dificultad respiratoria",),
    "no puedo respirar": ("no puedo respirar", "me cuesta respirar", "no respiro"),
    "perdida de consciencia": ("perdida de consciencia", "perdida de conciencia"),
    "desmayo": ("desmayo", "me he desmayado", "se ha desmayado"),
    "sintomas de ictus": ("sintomas de ictus", "ictus"),
    "cara torcida": ("cara torcida",),
    "perdida de fuerza subita": ("perdida de fuerza subita", "perdida subita de fuerza"),
    "paralisis": ("paralisis", "paralizado", "paralizada"),
    "sangrado grave": ("sangrado grave", "sangra mucho", "hemorragia"),
    "dolor severo repentino": ("dolor severo repentino", "dolor muy fuerte de repente"),
    "urgencia medica": ("urgencia medica", "emergencia medica"),
}


def detect_emergency(text: str) -> EmergencyDetection:
    normalized = _normalize(text)
    for label, variants in EMERGENCY_PATTERNS.items():
        if any(variant in normalized for variant in variants):
            return EmergencyDetection(True, label)
    return EmergencyDetection(False)


def emergency_reply(channel: str) -> str:
    if channel == "whatsapp":
        return WHATSAPP_EMERGENCY_REPLY
    return VOICE_EMERGENCY_REPLY
