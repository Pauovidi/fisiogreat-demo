from urllib.parse import quote_plus

from ..config.settings import settings


def tts_url_or_none(text: str) -> str | None:
    """
    Devuelve la URL absoluta al endpoint de TTS de ElevenLabs si está configurado.
    Si falta la API key o la URL pública del backend, devuelve None y el código
    de voz usará el <Say> nativo de Twilio.
    """
    api_key = settings.ELEVEN_API_KEY
    base_url = getattr(settings, "PUBLIC_BASE_URL", None)

    if not api_key or not base_url:
        return None

    base = base_url.rstrip("/")
    return f"{base}/webhook/voice/tts?text={quote_plus(text)}"
