import datetime as dt
import re
from typing import List, Optional

from ..services.salon_knowledge import uncertain_service_answer


NUMBER_WORDS = {
    0: "cero",
    1: "una",
    2: "dos",
    3: "tres",
    4: "cuatro",
    5: "cinco",
    6: "seis",
    7: "siete",
    8: "ocho",
    9: "nueve",
    10: "diez",
    11: "once",
    12: "doce",
    13: "trece",
    14: "catorce",
    15: "quince",
    16: "dieciseis",
    17: "diecisiete",
    18: "dieciocho",
    19: "diecinueve",
    20: "veinte",
    21: "veintiuna",
    22: "veintidos",
    23: "veintitres",
    24: "veinticuatro",
    25: "veinticinco",
    26: "veintiseis",
    27: "veintisiete",
    28: "veintiocho",
    29: "veintinueve",
}


class VoiceCopy:
    @staticmethod
    def opening_greeting() -> str:
        return "Hola, soy el asistente de FisioGreat. ¿En qué puedo ayudarte?"

    @staticmethod
    def ask_service() -> str:
        return "¿Qué cita necesitas?"

    @staticmethod
    def ask_service_for_booking() -> str:
        return "Vale, claro. ¿Qué tipo de cita necesitas?"

    @staticmethod
    def ask_service_retry() -> str:
        return "No te seguí. ¿Quieres primera visita, sesión de fisioterapia o seguimiento?"

    @staticmethod
    def clarify_lavado() -> str:
        return "¿Quieres una sesión de fisioterapia?"

    @staticmethod
    def ask_date(service: Optional[str] = None) -> str:
        if service:
            return f"Perfecto, para {VoiceCopy._display_service(service)}. ¿Qué día te va bien?"
        return "Perfecto. ¿Qué día te va bien?"

    @staticmethod
    def ask_patient_name(service: Optional[str] = None) -> str:
        if service:
            return f"Perfecto, para {VoiceCopy._display_service(service)}. ¿A qué nombre dejamos la cita?"
        return "Perfecto. ¿A qué nombre dejamos la cita?"

    @staticmethod
    def ask_patient_name_retry(attempt: int = 1) -> str:
        if attempt <= 1:
            return "Perdona, no lo he entendido. Dime solo el nombre y apellidos, por ejemplo Marcos Castellano."
        return "Dime solo el nombre y apellidos, por favor."

    @staticmethod
    def ask_consultation_reason() -> str:
        return "Para que el fisioterapeuta pueda preparar mejor la sesión, ¿cuál es el motivo de la consulta?"

    @staticmethod
    def ask_consultation_reason_retry() -> str:
        return "Puedes contarme brevemente el motivo de la consulta?"

    @staticmethod
    def consultation_reason_then_patient_name() -> str:
        return "De acuerdo, con esta información podemos preparar mejor tu sesión. ¿A qué nombre dejamos la cita?"

    @staticmethod
    def consultation_reason_then_date() -> str:
        return "De acuerdo, con esta información podemos preparar mejor tu sesión. ¿Qué día te va bien?"

    @staticmethod
    def ask_contact() -> str:
        return "Para enviarte la confirmación y el recordatorio, dime una opción: este número, otro teléfono o correo electrónico."

    @staticmethod
    def ask_contact_with_phone_suggestion() -> str:
        return "Para enviarte la confirmación y el recordatorio, dime una opción: este número, otro teléfono o correo electrónico."

    @staticmethod
    def ask_contact_email() -> str:
        return "Perfecto, dime solo el correo electrónico. Por ejemplo: marcos arroba ejemplo punto com."

    @staticmethod
    def ask_contact_email_retry(attempt: int = 1) -> str:
        if attempt <= 1:
            return "No he captado el correo completo. Dímelo solo así: marcos arroba ejemplo punto com."
        return "Sigo sin entender el correo. Si prefieres, dime un teléfono móvil."

    @staticmethod
    def ask_contact_phone() -> str:
        return "Perfecto, dime el teléfono móvil."

    @staticmethod
    def ask_contact_phone_retry() -> str:
        return "Perdona, no he entendido bien el teléfono. Dime solo un teléfono móvil, por ejemplo 640 50 50 50."

    @staticmethod
    def ask_contact_retry(attempt: int = 1) -> str:
        if attempt <= 1:
            return (
                "Perdona, no lo he entendido bien. Puedes decir tu correo electrónico como "
                "marcos arroba ejemplo punto com, o un teléfono móvil."
            )
        return "Sigo sin entenderlo. Si quieres, dime solo un teléfono móvil, por ejemplo 640 50 50 50."

    @staticmethod
    def contact_required_before_closing() -> str:
        return "Antes de cerrar la cita necesito un teléfono o correo electrónico para enviarte la confirmación."

    @staticmethod
    def thanks_name_then_date(first_name: str) -> str:
        return f"Gracias, {first_name}. ¿Qué día te va bien?"

    @staticmethod
    def resume_date(service: Optional[str] = None) -> str:
        if service:
            return f"Para {VoiceCopy._display_service(service)}, ¿qué día te va bien?"
        return "¿Qué día te va bien?"

    @staticmethod
    def ask_date_with_time_pref(service: Optional[str] = None, time_pref: Optional[str] = None) -> str:
        pref_text = "por la tarde" if time_pref == "afternoon" else "por la mañana"
        if service:
            return f"Perfecto, mejor {pref_text}. Para {VoiceCopy._display_service(service)}, ¿qué día te va bien?"
        return f"Perfecto, mejor {pref_text}. ¿Qué día te va bien?"

    @staticmethod
    def ask_date_retry() -> str:
        return "No he entendido bien el día. Puedes decirme, por ejemplo, mañana, jueves o el viernes por la mañana."

    @staticmethod
    def clarify_weekday(weekday: Optional[str]) -> str:
        if weekday:
            return f"Creo que he entendido {weekday}. ¿Te refieres al próximo {weekday}?"
        return VoiceCopy.ask_date_retry()

    @staticmethod
    def ask_time_pref() -> str:
        return "¿Te va mejor por la mañana o por la tarde?"

    @staticmethod
    def propose_slots(slots: List[str], *, time_pref: Optional[str] = None) -> str:
        if not slots:
            return "No tengo hueco ahí. Dime otro día."

        spoken = [VoiceCopy._slot_to_voice_text(slot, include_day=False, include_prefix=False) for slot in slots[:3]]
        day_context = VoiceCopy._slot_day_context(slots[0], time_pref)
        if len(spoken) == 1:
            if day_context:
                return f"Para {day_context} solo veo este hueco: {spoken[0]}. Di primera si te encaja."
            return f"Solo veo este hueco: {spoken[0]}. Di primera si te encaja."
        if len(spoken) == 2:
            if day_context:
                return f"Para {day_context} tengo estas opciones: {spoken[0]} o {spoken[1]}. Di primera o segunda."
            return f"Tengo {spoken[0]} o {spoken[1]}. Di primera o segunda."
        if day_context:
            return f"Para {day_context} tengo estas opciones: {spoken[0]}, {spoken[1]} y {spoken[2]}. Di primera, segunda o tercera."
        return f"Tengo {spoken[0]}, {spoken[1]} y {spoken[2]}. Di primera, segunda o tercera."

    @staticmethod
    def no_slots_for_day(date_pref: dt.date, *, time_pref: Optional[str] = None) -> str:
        weekday = [
            "lunes",
            "martes",
            "miércoles",
            "jueves",
            "viernes",
            "sábado",
            "domingo",
        ][date_pref.weekday()]
        pref = ""
        if time_pref == "morning":
            pref = " por la mañana"
        elif time_pref == "afternoon":
            pref = " por la tarde"
        return f"Para el {weekday}{pref} no veo huecos libres. ¿Quieres que miremos otro día?"

    @staticmethod
    def confirm_booking(slot: str, service: Optional[str] = None, patient_name: Optional[str] = None) -> str:
        service_part = f" para {VoiceCopy._display_service(service)}" if service else ""
        return f"Gracias. Te dejo apuntada la cita{service_part} {VoiceCopy._slot_to_confirmation_text(slot)}."

    @staticmethod
    def out_of_scope() -> str:
        return "Puedo ayudarte con citas, horarios y servicios."

    @staticmethod
    def unsupported_service(service: Optional[str] = None) -> str:
        if service:
            return (
                f"En esta demo no puedo reservar {service}. "
                "Puedo gestionar valoración inicial, sesión de fisioterapia o seguimiento. "
                "Si necesitas otro servicio, te derivamos a una persona del equipo."
            )
        return (
            "En esta demo no puedo reservar ese servicio. "
            "Puedo gestionar valoración inicial, sesión de fisioterapia o seguimiento. "
            "Si necesitas otro servicio, te derivamos a una persona del equipo."
        )

    @staticmethod
    def no_idea_service() -> str:
        return f"{uncertain_service_answer('voice')} ¿Qué te apetece?"

    @staticmethod
    def no_idea_date() -> str:
        return "No pasa nada. Dime un día que te vaya bien."

    @staticmethod
    def no_idea_slot() -> str:
        return "Sin problema. ¿Te va mejor la primera o la segunda? Si no, te busco otro día."

    @staticmethod
    def technical_error() -> str:
        return "Se ha cortado un momento. Me lo repites?"

    @staticmethod
    def earlier_or_later() -> str:
        return "Sin problema. ¿Prefieres antes, más tarde u otro día?"

    @staticmethod
    def another_day() -> str:
        return "Sin problema. Dime otro día y lo miro."

    @staticmethod
    def thanks_closing() -> str:
        return "Gracias a ti por confiar en FisioGreat. Te esperamos."

    @staticmethod
    def thanks_after_booking(slot: str, patient_name: Optional[str] = None) -> str:
        name = VoiceCopy._first_name(patient_name)
        name_part = f", {name}" if name else ""
        return f"Gracias a ti{name_part}. Te esperamos {VoiceCopy._slot_to_confirmation_text(slot)}."

    @staticmethod
    def thanks_generic() -> str:
        return "Gracias a ti. Si necesitas pedir, cambiar o cancelar una cita, aquí estoy."

    @staticmethod
    def farewell_after_booking(slot: str, patient_name: Optional[str] = None) -> str:
        name = VoiceCopy._first_name(patient_name)
        name_part = f", {name}" if name else ""
        return f"Hasta luego{name_part}. Nos vemos {VoiceCopy._slot_to_confirmation_text(slot)}."

    @staticmethod
    def farewell_generic() -> str:
        return "Hasta luego. Aquí estoy si necesitas ayuda con tus citas."

    @staticmethod
    def thanks_with_followup(follow_up: str) -> str:
        return f"Gracias a ti. {follow_up}".strip()

    @staticmethod
    def _display_service(service: Optional[str]) -> str:
        normalized = (service or "").strip().lower()
        if "valoracion" in normalized or "valoración" in normalized:
            return "valoración inicial"
        if "seguimiento" in normalized:
            return "consulta de seguimiento"
        if "fisio" in normalized or "fisioterapia" in normalized:
            return "sesión de fisioterapia"
        return (service or "cita").strip()

    @staticmethod
    def _first_name(patient_name: Optional[str]) -> Optional[str]:
        if not patient_name:
            return None
        stripped = patient_name.strip()
        return stripped.split()[0] if stripped else None

    @staticmethod
    def _slot_day_context(slot: str, time_pref: Optional[str] = None) -> Optional[str]:
        weekday_match = re.search(r"([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+\d{2}/\d{2}", slot)
        if not weekday_match:
            return None
        pref = ""
        if time_pref == "morning":
            pref = " por la mañana"
        elif time_pref == "afternoon":
            pref = " por la tarde"
        return f"el {weekday_match.group(1)}{pref}"

    @staticmethod
    def _slot_to_voice_text(slot: str, *, include_day: bool, include_prefix: bool) -> str:
        weekday_match = re.search(r"([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+\d{2}/\d{2}\s+a las\s+(\d{1,2}:\d{2})", slot)
        weekday = weekday_match.group(1) if weekday_match else None

        match = re.search(r"(\d{1,2}:\d{2})", slot)
        if not match:
            return slot
        spoken_time = VoiceCopy._time_to_voice_text(match.group(1))
        if include_day and weekday:
            if include_prefix:
                return f"el {weekday} {VoiceCopy._with_a_prefix(spoken_time)}"
            return f"el {weekday} {spoken_time}"
        if include_prefix:
            return VoiceCopy._with_a_prefix(spoken_time)
        return spoken_time

    @staticmethod
    def _slot_to_confirmation_text(slot: str) -> str:
        weekday_match = re.search(r"([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+\d{2}/\d{2}\s+a las\s+(\d{1,2}:\d{2})", slot)
        weekday = weekday_match.group(1) if weekday_match else None
        match = re.search(r"(\d{1,2}:\d{2})", slot)
        if not match:
            return slot
        spoken_time = VoiceCopy._time_to_voice_text(match.group(1))
        time_phrase = VoiceCopy._with_a_prefix(spoken_time)
        if weekday:
            return f"el {weekday} {time_phrase}"
        return time_phrase

    @staticmethod
    def _time_to_voice_text(value: str) -> str:
        hour_str, minute_str = value.split(":")
        hour_24 = int(hour_str)
        minute = int(minute_str)
        hour_12 = hour_24 % 12 or 12
        next_hour_12 = (hour_24 + 1) % 12 or 12

        base = VoiceCopy._hour_with_article(hour_12)
        next_base = VoiceCopy._hour_with_article(next_hour_12)

        if minute == 0:
            spoken = base
        elif minute == 15:
            spoken = f"{base} y cuarto"
        elif minute == 30:
            spoken = f"{base} y media"
        elif minute == 45:
            spoken = f"{next_base} menos cuarto"
        elif minute < 30:
            spoken = f"{base} y {VoiceCopy._minute_words(minute)}"
        else:
            spoken = f"{next_base} menos {VoiceCopy._minute_words(60 - minute)}"

        period = VoiceCopy._day_period(hour_24)
        if period:
            spoken = f"{spoken} {period}"
        return spoken

    @staticmethod
    def _hour_with_article(hour_12: int) -> str:
        if hour_12 == 1:
            return "la una"
        return f"las {NUMBER_WORDS[hour_12]}"

    @staticmethod
    def _minute_words(value: int) -> str:
        if value in NUMBER_WORDS:
            return NUMBER_WORDS[value]
        tens = {
            30: "treinta",
            40: "cuarenta",
            50: "cincuenta",
        }
        if value in tens:
            return tens[value]
        if value < 40:
            return f"treinta y {NUMBER_WORDS[value - 30]}"
        if value < 50:
            return f"cuarenta y {NUMBER_WORDS[value - 40]}"
        return f"cincuenta y {NUMBER_WORDS[value - 50]}"

    @staticmethod
    def _day_period(hour_24: int) -> str:
        if 13 <= hour_24 < 20:
            return "de la tarde"
        if hour_24 >= 20:
            return "de la noche"
        return ""

    @staticmethod
    def _with_a_prefix(spoken_time: str) -> str:
        if spoken_time.startswith("las "):
            return f"a {spoken_time}"
        if spoken_time.startswith("la "):
            return f"a {spoken_time}"
        return f"a las {spoken_time}"


VOICE_COPY = VoiceCopy()
