from typing import Optional

from ..config.settings import settings


DEFAULT_LOCATION = "Calle Mayor 25, Madrid"
DEFAULT_HOURS = "Lunes a viernes 09:00-19:00 | Sabado 09:00-14:00 | Domingo cerrado"
DEFAULT_SERVICES = "corte, corte y lavado, color, color raiz, mechas y peinado"
DEFAULT_PRICE_GUIDANCE = "Depende del servicio y del largo del pelo."

SERVICE_DURATION_HINTS = {
    "corte": "Un corte suele tardar entre 30 y 45 minutos.",
    "corte + lavado": "Un corte con lavado suele tardar entre 30 y 45 minutos.",
    "corte y lavado": "Un corte con lavado suele tardar entre 30 y 45 minutos.",
    "color": "El color suele necesitar mas tiempo que un corte.",
    "color raiz": "El color de raiz suele necesitar mas tiempo que un corte.",
    "mechas": "Las mechas suelen necesitar bastante mas tiempo.",
    "peinado": "El peinado depende del acabado que quieras.",
}


def salon_hours() -> str:
    return (settings.SALON_HOURS or DEFAULT_HOURS).strip()


def salon_location() -> str:
    return (settings.SALON_LOCATION or DEFAULT_LOCATION).strip()


def salon_phone() -> Optional[str]:
    phone = (settings.SALON_PHONE or "").strip()
    return phone or None


def salon_services() -> str:
    return (settings.SALON_SERVICES or DEFAULT_SERVICES).strip()


def salon_price_guidance() -> str:
    guidance = (settings.SALON_PRICE_GUIDANCE or DEFAULT_PRICE_GUIDANCE).strip()
    return guidance or DEFAULT_PRICE_GUIDANCE


def service_duration_hint(service: Optional[str]) -> str:
    if not service:
        return "Un corte suele tardar entre 30 y 45 minutos, y color o mechas suelen necesitar mas tiempo."
    return SERVICE_DURATION_HINTS.get(
        service,
        "Un corte suele tardar entre 30 y 45 minutos, y color o mechas suelen necesitar mas tiempo.",
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
        return "Puedo ayudarte con citas, horarios y servicios."
    return "Puedo ayudarte con citas, horarios, servicios y dudas basicas del salon."


def uncertain_service_answer(channel: str) -> str:
    if channel == "voice":
        return "No pasa nada. Podemos empezar por corte, color, mechas o peinado."
    return "No pasa nada. Podemos empezar por corte, color, mechas o peinado."


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
        return "Abrimos de lunes a viernes de nueve a siete, y sabados por la manana."
    return f"Nuestro horario es {salon_hours()}."


def _location_answer(channel: str) -> str:
    location = salon_location()
    if channel == "voice":
        return f"Estamos en {location}. Si quieres, te paso la direccion exacta por WhatsApp."
    return f"Estamos en {location}. Si quieres, te paso la direccion exacta."


def _pricing_answer(channel: str) -> str:
    if channel == "voice":
        return "Depende del servicio y del largo del pelo. Para orientarte mejor, dime que te quieres hacer."
    return f"{salon_price_guidance()} Para orientarte mejor, dime que te quieres hacer."


def _services_answer(channel: str) -> str:
    if channel == "voice":
        return "Hacemos corte, color, mechas y peinado."
    return f"Hacemos {salon_services()}."


def _human_answer(channel: str) -> str:
    del channel
    phone = salon_phone()
    if phone:
        return f"Sin problema. Te dejo el telefono del salon, {phone}, o aviso para que te llamen."
    return "Sin problema. Te dejo el telefono del salon o aviso para que te llamen."
