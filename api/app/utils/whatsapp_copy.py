import random
from typing import List, Optional


class WhatsAppCopy:
    @staticmethod
    def greet_and_offer() -> str:
        return (
            "Buenas, soy el asistente de FisioGreat. Puedo ayudarte a pedir, cambiar o cancelar una cita, "
            "o resolver dudas sobre servicios y horarios. ¿Qué necesitas?"
        )

    @staticmethod
    def ask_service() -> str:
        variants = [
            "Cuéntame, ¿qué cita necesitas? Puedo mirar primera visita, sesión de fisioterapia o seguimiento.",
            "¿Qué tipo de cita necesitas? Por ejemplo valoración inicial o sesión de fisioterapia.",
            "Perfecto. ¿Qué necesitas reservar?",
        ]
        return random.choice(variants)

    @staticmethod
    def ask_service_retry() -> str:
        return "No te seguí del todo. ¿Qué tipo de cita quieres reservar?"

    @staticmethod
    def ask_date(service: Optional[str] = None) -> str:
        service_text = f" para {service}" if service else ""
        return f"Perfecto{service_text}. ¿Qué día te viene bien?"

    @staticmethod
    def ask_patient_name(service: Optional[str] = None) -> str:
        if service:
            return f"Perfecto para {service}. ¿A qué nombre dejamos la cita?"
        return "Perfecto. ¿A qué nombre dejamos la cita?"

    @staticmethod
    def ask_consultation_reason() -> str:
        return (
            "Perfecto para sesión de fisioterapia. Para que el fisioterapeuta pueda preparar mejor la sesión, "
            "¿cuál es el motivo de la consulta? Por ejemplo: me duele la rodilla, tengo una contractura "
            "o vengo por una sobrecarga."
        )

    @staticmethod
    def ask_consultation_reason_retry() -> str:
        return "¿Puedes contarme brevemente el motivo de la consulta?"

    @staticmethod
    def consultation_reason_then_patient_name() -> str:
        return "De acuerdo, con esta información podemos preparar mejor tu sesión. ¿A qué nombre dejamos la cita?"

    @staticmethod
    def consultation_reason_then_date() -> str:
        return "De acuerdo, con esta información podemos preparar mejor tu sesión. ¿Qué día te viene bien?"

    @staticmethod
    def ask_patient_name_before_slots() -> str:
        return "Antes de buscar huecos, ¿a qué nombre dejamos la cita?"

    @staticmethod
    def ask_patient_name_before_confirmation() -> str:
        return "Antes de confirmar, ¿a qué nombre dejamos la cita?"

    @staticmethod
    def thanks_name_then_date(first_name: str) -> str:
        return f"Gracias, {first_name}. ¿Qué día te viene bien?"

    @staticmethod
    def ask_date_retry() -> str:
        return "Dime un día que te venga bien, por ejemplo jueves o mañana."

    @staticmethod
    def propose_slots(slots: List[str]) -> str:
        if not slots:
            return "No veo huecos ahora mismo para ese día. Si quieres, miramos otro."
        lines = ["Te puedo ofrecer estas opciones:"]
        for index, slot in enumerate(slots, start=1):
            lines.append(f"{index}. {slot}")
        lines.append("Si te encaja una, dime el número.")
        return "\n".join(lines)

    @staticmethod
    def confirm_booking(slot: str, service: Optional[str] = None, patient_name: Optional[str] = None) -> str:
        first_name = _first_name(patient_name)
        prefix = f"Perfecto, {first_name}. " if first_name else "Perfecto. "
        service_text = f" para {service}" if service else ""
        return f"{prefix}Te dejo apuntada la cita{service_text} el {slot}."

    @staticmethod
    def thanks_after_booking(slot: str, patient_name: Optional[str] = None) -> str:
        first_name = _first_name(patient_name)
        prefix = f"Gracias a ti, {first_name}. " if first_name else "Gracias a ti. "
        return f"{prefix}Te esperamos el {slot}."

    @staticmethod
    def thanks_generic() -> str:
        return "Gracias a ti. Si necesitas pedir, cambiar o cancelar una cita, aquí estoy."

    @staticmethod
    def farewell_after_booking(slot: str, patient_name: Optional[str] = None) -> str:
        first_name = _first_name(patient_name)
        prefix = f"Hasta luego, {first_name}. " if first_name else "Hasta luego. "
        return f"{prefix}Nos vemos el {slot}."

    @staticmethod
    def farewell_generic() -> str:
        return "Hasta luego. Aquí estoy si necesitas ayuda con tus citas."

    @staticmethod
    def main_menu_soft() -> str:
        return (
            "Puedo ayudarte a pedir, cambiar o cancelar una cita, o resolver dudas sobre servicios y horarios. "
            "¿Qué necesitas?"
        )

    @staticmethod
    def reset_done() -> str:
        return (
            "Perfecto, empezamos de nuevo. Puedo ayudarte a pedir, cambiar o cancelar una cita, "
            "o resolver dudas sobre servicios y horarios. ¿Qué necesitas?"
        )

    @staticmethod
    def cancel_pending_flow() -> str:
        return "De acuerdo, cancelo esta gestión y empezamos de nuevo. ¿En qué puedo ayudarte?"

    @staticmethod
    def out_of_scope() -> str:
        return "Ahora mismo te ayudo con citas, horarios y dudas básicas de FisioGreat."

    @staticmethod
    def unsupported_service() -> str:
        return (
            "En esta demo puedo gestionar valoración inicial, sesión de fisioterapia y consulta de seguimiento. "
            "Para otros servicios, puedo derivarte a una persona del equipo. ¿Quieres reservar uno de estos servicios?"
        )

    @staticmethod
    def technical_error() -> str:
        return "He tenido un fallo puntual. Si quieres, seguimos paso a paso: primero dime qué cita necesitas."


WA_COPY = WhatsAppCopy()


def _first_name(patient_name: Optional[str]) -> Optional[str]:
    if not patient_name:
        return None
    return patient_name.strip().split()[0] if patient_name.strip() else None
