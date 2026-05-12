from typing import Any, Dict, List

from ...config.settings import settings


VALID_SERVICES = [
    "valoracion inicial",
    "sesion de fisioterapia",
    "consulta de seguimiento",
]

VALID_SERVICES_DISPLAY = [
    "valoración inicial",
    "sesión de fisioterapia",
    "consulta de seguimiento",
]


REALTIME_V2_INSTRUCTIONS = """
Eres el callbot experimental V2 de FisioGreat Demo.
Hablas siempre en español de España.
Tono profesional, breve, natural y tranquilo.
Usa frases cortas. No hagas monólogos.

Prioridad absoluta: urgencias.
Si el usuario menciona dolor torácico, dificultad para respirar, pérdida de consciencia,
ictus, parálisis, sangrado grave, dolor severo repentino o emergencia médica:
usa emergency_protocol, responde 112/urgencias, no agendes y no pidas más datos.

No diagnostiques. No des consejo clínico personalizado.
Puedes gestionar solo estos servicios:
- valoración inicial
- sesión de fisioterapia
- consulta de seguimiento

Si piden otro servicio, responde exactamente:
"En esta demo puedo gestionar valoración inicial, sesión de fisioterapia y consulta de seguimiento. Para otros servicios, puedo derivarte a una persona del equipo."
No agendes servicios fuera de catálogo.

Flujo para sesión de fisioterapia:
servicio -> motivo -> nombre -> día -> slots -> contacto -> confirmar.
El motivo es texto libre. No lo interpretes ni diagnostiques.

Flujo para valoración inicial o consulta de seguimiento:
servicio -> nombre -> día -> slots -> contacto -> confirmar.

Contacto en voz:
Antes de confirmar exige teléfono o email, uno de los dos, no ambos.
Si el usuario dice "sí, a este número", usa el número de llamada si está disponible.
Si dicta email como "marcos arroba ejemplo punto com", normalízalo.

Fechas:
Si el usuario dice un weekday explícito como jueves, nunca reserves martes ni miércoles.
Si hay duda de fecha, pregunta una aclaración.
No saltes a otro día sin confirmarlo.

Cambio y cancelación:
Lista citas futuras asociadas al teléfono/call/session.
No uses el último slot confirmado como fuente principal.
Si hay varias citas para cancelar o cambiar, lista opciones y pide selección.
Para reprogramar, actualiza la cita existente; no dupliques.
Para cancelar, cancela Calendar primero y después marca Supabase.

Durante un flujo activo:
No repitas menús genéricos.
No entres en bucles.
Confirma pasos críticos antes de crear, cambiar o cancelar una cita.
""".strip()


def realtime_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "get_services",
            "description": "Devuelve el catálogo cerrado de servicios gestionables por la demo.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "collect_consultation_reason",
            "description": "Guarda un motivo textual para una sesión de fisioterapia sin diagnosticar.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_available_slots",
            "description": "Busca 2-3 huecos disponibles respetando el día solicitado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "date_text": {"type": "string"},
                    "today": {"type": "string", "description": "Fecha ISO opcional para tests."},
                },
                "required": ["service", "date_text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "book_appointment",
            "description": "Confirma una cita si están todos los datos obligatorios.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "patient_name": {"type": "string"},
                    "start_at": {"type": "string"},
                    "end_at": {"type": "string"},
                    "consultation_reason": {"type": "string"},
                    "contact_phone": {"type": "string"},
                    "contact_email": {"type": "string"},
                },
                "required": ["service", "patient_name", "start_at"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_future_appointments",
            "description": "Lista citas futuras asociadas al teléfono/call/session.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "reschedule_appointment",
            "description": "Reprograma una cita existente sin duplicarla.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "new_start_at": {"type": "string"},
                    "new_end_at": {"type": "string"},
                },
                "required": ["appointment_id", "new_start_at", "new_end_at"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "cancel_appointment",
            "description": "Cancela una cita existente.",
            "parameters": {
                "type": "object",
                "properties": {"appointment_id": {"type": "string"}},
                "required": ["appointment_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "save_contact",
            "description": "Normaliza y guarda un teléfono o email de contacto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_text": {"type": "string"},
                    "call_phone": {"type": "string"},
                },
                "required": ["contact_text"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "emergency_protocol",
            "description": "Activa el protocolo de urgencias y bloquea la agenda.",
            "parameters": {
                "type": "object",
                "properties": {"user_text": {"type": "string"}},
                "required": ["user_text"],
                "additionalProperties": False,
            },
        },
    ]


def build_realtime_session_config() -> Dict[str, Any]:
    return {
        "type": "realtime",
        "model": settings.OPENAI_REALTIME_MODEL,
        "voice": settings.OPENAI_REALTIME_VOICE,
        "instructions": REALTIME_V2_INSTRUCTIONS,
        "tools": realtime_tool_schemas(),
        "tool_choice": "auto",
        "metadata": {
            "app": "fisiogreat-demo",
            "version": settings.OPENAI_REALTIME_INSTRUCTIONS_VERSION,
            "shadow_mode": settings.OPENAI_REALTIME_SHADOW_MODE,
            "write_enabled": settings.OPENAI_REALTIME_WRITE_ENABLED,
        },
    }
