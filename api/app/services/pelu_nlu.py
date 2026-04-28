import datetime as dt
import json
import httpx

from ..config.settings import settings
from ..services.slots import ServiceCatalog

SERVICE_KEYS = list(ServiceCatalog.durations.keys())


SYSTEM_PROMPT = f"""
Eres un analizador de lenguaje natural para una peluquería.
Tu tarea es LEER el mensaje del cliente y devolver un JSON con:
- intent: "book" (reservar), "change" (cambiar cita), "cancel" (cancelar cita),
          "info" (pregunta o duda), o "other" si no está claro.
- service_key: una de estas claves exactas o null si no queda claro:
  {SERVICE_KEYS}
- datetime_iso: fecha y hora de la cita en formato ISO 8601
  (ej: "2025-12-20T10:00:00"), o null si no hay fecha/hora clara.

REGLAS IMPORTANTES:
- Fecha/hora: interpreta expresiones como:
  - "el jueves a las 10"
  - "mañana por la tarde"
  - "hoy a las 17:30"
  - "este sábado por la mañana"
  Usa la fecha actual para resolver "hoy", "mañana", "jueves", etc.
- Si el mensaje habla de CANCELAR una cita → intent = "cancel".
- Si habla de CAMBIAR / MOVER una cita → intent = "change".
- Si pide una cita nueva → intent = "book".
- Si solo pregunta precios, duración, etc. → intent = "info".
- service_key: mapea palabras como "corte mujer", "corte de pelo", "solo puntas"
  a la clave más cercana (por ejemplo "corte").
- SIEMPRE responde solo con un JSON válido, sin texto extra.

Hoy es {dt.datetime.now().isoformat()}.
Zona horaria: Europe/Madrid.
"""


async def analyze_message(text: str) -> dict:
    """
    Llama a OpenAI y devuelve un dict como:
    {
      "intent": "book" | "change" | "cancel" | "info" | "other",
      "service_key": "corte" | ... | None,
      "datetime_iso": "2025-12-20T10:00:00" | None
    }
    """
    api_key = settings.OPENAI_API_KEY
    model = settings.OPENAI_MODEL

    if not api_key:
        # Sin API key → devolvemos algo neutro
        return {"intent": "other", "service_key": None, "datetime_iso": None}

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError:
        return {"intent": "other", "service_key": None, "datetime_iso": None}

    if resp.status_code != 200:
        return {"intent": "other", "service_key": None, "datetime_iso": None}

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        intent = parsed.get("intent", "other")
        svc = parsed.get("service_key")
        dt_iso = parsed.get("datetime_iso")
        # Validaciones rápidas
        if svc not in SERVICE_KEYS:
            svc = None
        if isinstance(dt_iso, str):
            try:
                # Comprobamos que se puede parsear
                dt.datetime.fromisoformat(dt_iso)
            except ValueError:
                dt_iso = None
        else:
            dt_iso = None
        return {"intent": intent, "service_key": svc, "datetime_iso": dt_iso}
    except Exception:
        return {"intent": "other", "service_key": None, "datetime_iso": None}
