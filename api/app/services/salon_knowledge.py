from typing import Optional

from ..config.settings import settings


DEFAULT_LOCATION = "ubicacion pendiente de configurar"
DEFAULT_HOURS = "Lunes a viernes 09:00-20:00 | Sabado 10:00-14:00 | Domingo cerrado"
DEFAULT_SERVICES = "primera visita de fisioterapia, sesion de fisioterapia, valoracion inicial, consulta de seguimiento"
DEFAULT_PRICE_GUIDANCE = "Los precios orientativos se configuraran en la ficha de la clinica."

SERVICE_DURATION_HINTS = {
    "primera visita de fisioterapia": "La primera visita suele durar unos 60 minutos.",
    "sesion de fisioterapia": "Una sesion de fisioterapia suele durar unos 45 minutos.",
    "sesión de fisioterapia": "Una sesion de fisioterapia suele durar unos 45 minutos.",
    "valoracion inicial": "La valoracion inicial suele durar unos 60 minutos.",
    "valoración inicial": "La valoracion inicial suele durar unos 60 minutos.",
    "consulta de seguimiento": "La consulta de seguimiento suele durar unos 30 minutos.",
    "corte": "Un corte suele tardar entre 30 y 45 minutos.",
    "corte + lavado": "Un corte con lavado suele tardar entre 30 y 45 minutos.",
    "corte y lavado": "Un corte con lavado suele tardar entre 30 y 45 minutos.",
    "color": "El color suele necesitar mas tiempo que un corte.",
    "color raiz": "El color de raiz suele necesitar mas tiempo que un corte.",
    "mechas": "Las mechas suelen necesitar bastante mas tiempo.",
    "peinado": "El peinado depende del acabado que quieras.",
}


def salon_hours() -> str:
    return (settings.CLINIC_HOURS or settings.SALON_HOURS or DEFAULT_HOURS).strip()


def salon_location() -> str:
    return (settings.CLINIC_LOCATION or settings.SALON_LOCATION or DEFAULT_LOCATION).strip()


def salon_phone() -> Optional[str]:
    phone = (settings.CLINIC_PHONE or settings.HUMAN_HANDOFF_PHONE or settings.SALON_PHONE or "").strip()
    return phone or None


def salon_services() -> str:
    return (settings.FISIO_SERVICES or settings.SALON_SERVICES or DEFAULT_SERVICES).strip()


def salon_price_guidance() -> str:
    guidance = (settings.FISIO_PRICE_GUIDANCE or settings.SALON_PRICE_GUIDANCE or DEFAULT_PRICE_GUIDANCE).strip()
    return guidance or DEFAULT_PRICE_GUIDANCE


def service_duration_hint(service: Optional[str]) -> str:
    if not service:
        return "Las sesiones suelen durar entre 30 y 60 minutos segun el tipo de cita."
    return SERVICE_DURATION_HINTS.get(
        service,
        "Las sesiones suelen durar entre 30 y 60 minutos segun el tipo de cita.",
    )


def faq_answer(faq_id: str, channel: str) -> Optional[str]:
    handlers = {
        "hours": _hours_answer,
        "location": _location_answer,
        "pricing": _pricing_answer,
        "services": _services_answer,
        "human_handoff": _human_answer,
    }
    handler = handlers.get(faq_id)
    if not handler:
        return None
    return handler(channel)


def out_of_scope_answer(channel: str) -> str:
    if channel == "voice":
        return "Puedo ayudarte con citas de fisioterapia, horarios y dudas basicas de la clinica."
    return "Puedo ayudarte con citas de fisioterapia, horarios, servicios y dudas basicas de la clinica."


def uncertain_service_answer(channel: str) -> str:
    if channel == "voice":
        return "No pasa nada. Podemos empezar por primera visita, sesion de fisioterapia o seguimiento."
    return "No pasa nada. Podemos empezar por primera visita, sesion de fisioterapia o seguimiento."


def structured_facts() -> dict[str, str]:
    return {
        "hours": salon_hours(),
        "location": salon_location(),
        "services": salon_services(),
        "price_guidance": salon_price_guidance(),
        "phone": salon_phone() or "no disponible",
    }


def _hours_answer(channel: str) -> str:
    if channel == "voice":
        return "Abrimos de lunes a viernes de nueve a ocho, y sabados por la manana."
    return f"Nuestro horario es {salon_hours()}."


def _location_answer(channel: str) -> str:
    location = salon_location()
    if channel == "voice":
        return f"Estamos en {location}. Si quieres, te paso la direccion exacta por WhatsApp."
    return f"Estamos en {location}. Si quieres, te paso la direccion exacta."


def _pricing_answer(channel: str) -> str:
    if channel == "voice":
        return "Los precios orientativos dependen del tipo de sesion. Si quieres, dime que necesitas y te oriento."
    return f"{salon_price_guidance()} Para orientarte mejor, dime que necesitas."


def _services_answer(channel: str) -> str:
    if channel == "voice":
        return "Atendemos primera visita, sesion de fisioterapia, valoracion inicial y seguimiento."
    return f"Hacemos {salon_services()}."


def _human_answer(channel: str) -> str:
    del channel
    phone = salon_phone()
    if phone:
        return f"Sin problema. Te dejo el telefono de la clinica, {phone}, o aviso para que te llamen."
    return "Sin problema. Te dejo el telefono de la clinica o aviso para que te llamen."
