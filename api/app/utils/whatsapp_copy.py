import random
from typing import List, Optional


class WhatsAppCopy:
    @staticmethod
    def greet_and_offer() -> str:
        variants = [
            "Hola, encantada. ¿Qué servicio te gustaría reservar?",
            "Hola, bienvenida. ¿Qué te apetece hacerte hoy?",
            "Buenas, te ayudo con la cita. ¿Qué servicio quieres?",
        ]
        return random.choice(variants)

    @staticmethod
    def ask_service() -> str:
        variants = [
            "Cuéntame, ¿qué servicio quieres? Puedo mirar corte, color, mechas o peinado.",
            "¿Qué servicio necesitas? Por ejemplo corte, color o peinado.",
            "Perfecto. ¿Qué te gustaría hacerte?",
        ]
        return random.choice(variants)

    @staticmethod
    def ask_service_retry() -> str:
        return "No te seguí del todo. ¿Qué servicio quieres reservar?"

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
        return f"Perfecto, te dejo apuntada {slot}. Si quieres, seguimos con la reserva."

    @staticmethod
    def main_menu_soft() -> str:
        return "Puedo ayudarte con una cita. Si quieres, dime qué servicio necesitas y lo vemos."

    @staticmethod
    def out_of_scope() -> str:
        return "Ahora mismo te ayudo con citas y horario. Si quieres reservar, dime qué servicio buscas."

    @staticmethod
    def technical_error() -> str:
        return "He tenido un fallo puntual. Si quieres, seguimos paso a paso: primero dime qué servicio necesitas."


WA_COPY = WhatsAppCopy()
