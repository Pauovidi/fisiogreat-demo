import re
import unicodedata
from typing import Any, Dict, Optional


SUPPORTED_SERVICES = {
    "valoracion inicial",
    "sesion de fisioterapia",
    "consulta de seguimiento",
}

UNSUPPORTED_SERVICE_TERMS = {
    "pilates",
    "masaje",
    "osteopatia",
    "osteopata",
    "readaptacion",
    "entrenamiento",
    "nutricion",
    "suelo pelvico",
    "ondas de choque",
    "ecografia",
    "podologia",
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_service(text: str) -> Optional[str]:
    normalized = normalize_text(text)

    if any(phrase in normalized for phrase in ["primera visita", "primera cita"]):
        return "valoracion inicial"
    if any(phrase in normalized for phrase in ["valoracion inicial", "evaluacion inicial"]):
        return "valoracion inicial"
    if any(phrase in normalized for phrase in ["seguimiento", "consulta de seguimiento", "revision"]):
        return "consulta de seguimiento"
    if any(phrase in normalized for phrase in ["fisioterapia", "fisio", "sesion de fisio", "sesion de fisioterapia"]):
        return "sesion de fisioterapia"
    return None


def detect_unsupported_service(text: str) -> Optional[str]:
    normalized = normalize_text(text)
    for term in UNSUPPORTED_SERVICE_TERMS:
        if term in normalized:
            return term
    return None


def route_message(text: str) -> Dict[str, Any]:
    normalized = normalize_text(text)

    if not normalized:
        return {"type": "fallback"}

    if any(
        phrase in normalized
        for phrase in ["mas opciones", "mas huecos", "otro hueco", "otra opcion", "otro horario", "mas horas"]
    ):
        return {"type": "more_options"}

    if re.fullmatch(r"[123]", normalized) or normalized in {
        "primera",
        "la primera",
        "opcion primera",
        "uno",
        "la uno",
        "segunda",
        "la segunda",
        "opcion segunda",
        "dos",
        "la dos",
        "tercera",
        "la tercera",
        "opcion tercera",
        "tres",
        "la tres",
        "primer hueco",
        "segundo hueco",
        "tercer hueco",
    }:
        return {"type": "pick_slot"}

    if _matches_later_slots(normalized):
        return {"type": "later_slots"}

    if _matches_earlier_slots(normalized):
        return {"type": "earlier_slots"}

    if _matches_another_day(normalized):
        return {"type": "another_day"}

    if _matches_slot_rejection(normalized):
        return {"type": "reject_slot"}

    if _matches_thanks(normalized):
        return {"type": "thanks"}

    if _matches_acknowledgement(normalized):
        return {"type": "acknowledgement"}

    if _matches_farewell(normalized):
        return {"type": "farewell"}

    if _matches_hours(normalized):
        return {"type": "faq", "faq_id": "hours"}

    if _matches_services(normalized):
        return {"type": "faq", "faq_id": "services"}

    if _matches_pricing(normalized):
        return {"type": "faq", "faq_id": "pricing"}

    if _matches_location(normalized):
        return {"type": "faq", "faq_id": "location"}

    if _matches_human_handoff(normalized):
        return {"type": "human_handoff", "faq_id": "human_handoff"}

    if _matches_uncertainty(normalized):
        return {"type": "uncertain"}

    if any(token in normalized for token in ["cuenta bancaria", "banco", "transferencia", "dni", "nif", "password", "contrasena"]):
        return {"type": "out_of_scope"}

    if any(token in normalized for token in ["cancelar", "anular"]):
        return {"type": "cancel"}

    if any(token in normalized for token in ["cambiar", "modificar", "mover", "reprogramar"]):
        return {"type": "reschedule"}

    if normalized in {"hola", "buenas", "buenos dias", "buenas tardes", "hey"}:
        return {"type": "greeting"}

    service = detect_service(text)
    if service:
        return {"type": "booking", "service": service}

    unsupported_service = detect_unsupported_service(text)
    if unsupported_service and any(token in normalized for token in ["quiero", "necesito", "reservar", "cita", "sesion"]):
        return {"type": "unsupported_service", "service": unsupported_service}

    if any(token in normalized for token in ["reservar", "reserva", "cita", "quiero", "pedir hora", "sacar turno"]):
        return {"type": "booking"}

    return {"type": "fallback"}


def _matches_hours(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "horario",
            "horarios",
            "a que hora",
            "abris",
            "abierto",
            "abiertos",
            "estais abiertos",
            "estais abiertas",
            "abren",
            "sabados",
            "cuando abris",
            "que horario teneis",
        ]
    )


def _matches_services(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "que servicios",
            "que servicios teneis",
            "servicios",
            "que haceis",
            "que tratais",
            "servicios haceis",
            "que me puedo hacer",
            "que tratamientos",
            "tratamientos",
            "teneis color",
            "trabajais color",
            "haceis color",
            "haceis mechas",
            "poneis mechas",
            "tratamientos haceis",
            "haceis fisioterapia",
            "servicios de fisio",
        ]
    )


def _matches_pricing(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "precio",
            "precios",
            "cuanto cuesta",
            "cuanto vale",
            "cuanto saldria",
            "mas o menos cuanto",
        ]
    )


def _matches_location(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "donde estais",
            "donde estais ubicados",
            "ubicacion",
            "direccion",
            "como llego",
            "donde esta el salon",
            "donde estais exactamente",
        ]
    )


def _matches_human_handoff(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "hablar con una persona",
            "hablar con alguien",
            "pasame con una persona",
            "pasame con alguien",
            "quiero hablar con una persona",
            "quiero hablar con alguien",
            "atencion humana",
            "una persona de verdad",
        ]
    )


def _matches_uncertainty(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "no se",
            "no lo se",
            "ni idea",
            "no estoy segura",
            "no estoy seguro",
            "me da igual",
        ]
    )


def _matches_later_slots(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "mas tarde",
            "un poco mas tarde",
            "algo mas tarde",
            "mas por la tarde",
        ]
    )


def _matches_earlier_slots(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "antes",
            "mas temprano",
            "mas pronto",
        ]
    )


def _matches_another_day(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "otro dia",
            "mejor otro dia",
            "prefiero otro dia",
        ]
    )


def _matches_slot_rejection(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "no puedo a esa hora",
            "esa hora no puedo",
            "esa hora no me va bien",
            "no me va bien esa hora",
            "no puedo en ese horario",
        ]
    )


def _matches_thanks(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in [
            "gracias",
            "muchas gracias",
            "vale gracias",
            "perfecto gracias",
            "de acuerdo gracias",
        ]
    )


def _matches_acknowledgement(normalized: str) -> bool:
    return normalized in {
        "perfecto",
        "vale",
        "ok",
        "genial",
        "de acuerdo",
        "bien",
    }


def _matches_farewell(normalized: str) -> bool:
    return normalized in {
        "adios",
        "hasta luego",
        "nos vemos",
        "chao",
        "bye",
    }
