import random
from typing import List, Optional


class WhatsAppCopy:
    @staticmethod
    def greet_and_offer() -> str:
        variants = [
            "Hola, soy el asistente de FisioGreat. ¿Qué cita necesitas?",
            "Hola, te ayudo con FisioGreat. ¿Quieres reservar una cita de fisioterapia?",
            "Buenas, te ayudo con la cita. ¿Qué tipo de sesión necesitas?",
        ]
        return random.choice(variants)

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
    def confirm_booking(slot: str) -> str:
        return f"Perfecto, te dejo apuntada la cita para {slot}."

    @staticmethod
    def main_menu_soft() -> str:
        return "Puedo ayudarte con una cita de fisioterapia. Si quieres, dime qué necesitas y lo vemos."

    @staticmethod
    def out_of_scope() -> str:
        return "Ahora mismo te ayudo con citas, horarios y dudas básicas de FisioGreat."

    @staticmethod
    def technical_error() -> str:
        return "He tenido un fallo puntual. Si quieres, seguimos paso a paso: primero dime qué cita necesitas."


WA_COPY = WhatsAppCopy()
