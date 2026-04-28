from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_ENV: str = "demo"
    TZ: str = "Europe/Madrid"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_API_KEY: Optional[str] = None
    VOICE_AI_ENABLED: bool = True

    # ElevenLabs TTS
    ELEVEN_API_KEY: Optional[str] = None
    ELEVEN_VOICE_ID: Optional[str] = None
    ELEVEN_MODEL_ID: str = "eleven_multilingual_v2"

    # URL pública del backend (Cloud Run)
    # Puedes sobreescribirla con la env var PUBLIC_BASE_URL si cambia la URL.
    PUBLIC_BASE_URL: str = "https://pelu-agent-api-504875836299.europe-southwest1.run.app"

    # Twilio ConversationRelay
    CONVERSATIONRELAY_TTS_PROVIDER: str = "ElevenLabs"
    CONVERSATIONRELAY_TTS_PROVIDER_FALLBACK: str = "Google"
    CONVERSATIONRELAY_TTS_VOICE: Optional[str] = None
    CONVERSATIONRELAY_TTS_LANGUAGE: str = "es-ES"
    CONVERSATIONRELAY_TRANSCRIPTION_LANGUAGE: str = "es-ES"
    CONVERSATIONRELAY_TRANSCRIPTION_PROVIDER: str = "Google"
    CONVERSATIONRELAY_SPEECH_MODEL: str = "telephony"
    CONVERSATIONRELAY_INTERRUPTIBLE: str = "speech"
    CONVERSATIONRELAY_REPORT_INPUT_DURING_AGENT_SPEECH: str = "none"
    CONVERSATIONRELAY_DTMF_DETECTION: bool = True

    # Salon copy
    SALON_HOURS: str = "Lunes a viernes 09:00-19:00 | Sabado 09:00-14:00 | Domingo cerrado"
    SALON_LOCATION: str = "Calle Mayor 25, Madrid"
    SALON_SERVICES: str = "corte, corte y lavado, color, color raiz, mechas y peinado"
    SALON_PRICE_GUIDANCE: str = "Depende del servicio y del largo del pelo."
    SALON_PHONE: Optional[str] = None

    # Google Calendar
    GOOGLE_CREDENTIALS_JSON_BASE64: Optional[str] = None
    GOOGLE_CALENDAR_ID: Optional[str] = None

    # DB / CORS
    DATABASE_URL: Optional[str] = None
    CORS_ORIGINS: str = "http://localhost:3000"


settings = Settings()

