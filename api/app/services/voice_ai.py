import json
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config.settings import settings
from .salon_knowledge import structured_facts
from ..utils.logger import logger


@dataclass
class VoiceAIResult:
    intent: str = "unknown"
    faq_id: Optional[str] = None
    service: Optional[str] = None
    date_text: Optional[str] = None
    time_pref: Optional[str] = None
    answer: Optional[str] = None
    should_continue_booking: bool = False
    next_question: Optional[str] = None


def is_voice_ai_enabled() -> bool:
    return bool(settings.VOICE_AI_ENABLED and settings.OPENAI_API_KEY)


async def classify_and_draft_reply(
    user_text: str,
    *,
    stage: str,
    service: Optional[str],
    channel: str,
) -> Optional[VoiceAIResult]:
    if not is_voice_ai_enabled():
        return None

    facts = structured_facts()

    prompt = f"""
Eres una capa AUXILIAR para un callbot de peluqueria.
La reserva la controla otra maquina de estados. Tu NO decides reservas ni confirmas citas.

Debes devolver JSON valido con esta forma exacta:
{{
  "intent": "faq" | "human_handoff" | "uncertain" | "out_of_scope" | "unknown",
  "faq_id": "hours" | "location" | "pricing" | "services" | null,
  "service": string | null,
  "date_text": string | null,
  "time_pref": "morning" | "afternoon" | null,
  "answer": string,
  "should_continue_booking": boolean,
  "next_question": string | null
}}

Reglas:
- Respuesta muy corta. Maximo 24 palabras.
- Si dudas, usa "unknown".
- No confirmas citas.
- No inventas disponibilidad real.
- No prometes cancelaciones ni cambios hechos.
- Puedes explicar, clasificar y reconducir.
- No inventes datos. Usa solo estos hechos:
  - horario: {facts["hours"]}
  - ubicacion: {facts["location"]}
  - servicios: {facts["services"]}
  - precio: {facts["price_guidance"]}
  - telefono: {facts["phone"]}
- Si el usuario pide una persona, usa intent "human_handoff".
- Si el usuario dice "no se" o duda, usa intent "uncertain".
- Si es una FAQ, usa intent "faq" y el faq_id correcto.
- Si esta fuera de alcance, usa "out_of_scope".
- Si ayuda a continuar la reserva, pon should_continue_booking=true y una next_question breve.
- No anadas saludo ni despedida.

Contexto:
- canal: {channel}
- etapa actual: {stage}
- servicio actual: {service or "sin servicio"}
""".strip()

    payload = {
        "model": settings.OPENAI_MODEL,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "voice_ai_result",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["faq", "human_handoff", "uncertain", "out_of_scope", "unknown"],
                        },
                        "faq_id": {
                            "type": ["string", "null"],
                            "enum": ["hours", "location", "pricing", "services", None],
                        },
                        "service": {"type": ["string", "null"]},
                        "date_text": {"type": ["string", "null"]},
                        "time_pref": {"type": ["string", "null"], "enum": ["morning", "afternoon", None]},
                        "answer": {"type": "string"},
                        "should_continue_booking": {"type": "boolean"},
                        "next_question": {"type": ["string", "null"]},
                    },
                    "required": [
                        "intent",
                        "faq_id",
                        "service",
                        "date_text",
                        "time_pref",
                        "answer",
                        "should_continue_booking",
                        "next_question",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text},
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(f"voice_ai_http_error error={exc!r}")
        return None

    if response.status_code != 200:
        logger.warning(f"voice_ai_bad_status status={response.status_code}")
        return None

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        logger.warning(f"voice_ai_parse_error error={exc!r}")
        return None

    intent = parsed.get("intent") or "unknown"
    faq_id = parsed.get("faq_id")
    service_value = parsed.get("service")
    date_text = parsed.get("date_text")
    time_pref = parsed.get("time_pref")
    answer = parsed.get("answer")
    should_continue_booking = bool(parsed.get("should_continue_booking"))
    next_question = parsed.get("next_question")

    if intent not in {"faq", "human_handoff", "uncertain", "out_of_scope", "unknown"}:
        intent = "unknown"
    if faq_id not in {"hours", "location", "pricing", "services", None}:
        faq_id = None
    if not isinstance(service_value, str) or not service_value.strip():
        service_value = None
    if not isinstance(date_text, str) or not date_text.strip():
        date_text = None
    if time_pref not in {"morning", "afternoon", None}:
        time_pref = None
    if not isinstance(answer, str) or not answer.strip():
        answer = None
    if not isinstance(next_question, str) or not next_question.strip():
        next_question = None

    return VoiceAIResult(
        intent=intent,
        faq_id=faq_id,
        service=service_value.strip() if service_value else None,
        date_text=date_text.strip() if date_text else None,
        time_pref=time_pref,
        answer=answer.strip() if answer else None,
        should_continue_booking=should_continue_booking,
        next_question=next_question.strip() if next_question else None,
    )
