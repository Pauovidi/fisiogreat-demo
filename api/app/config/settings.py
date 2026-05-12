from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    APP_ENV: str = "demo_local"
    TZ: str = "Europe/Madrid"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_API_KEY: Optional[str] = None
    VOICE_AI_ENABLED: bool = False

    # OpenAI Realtime V2 (experimental/shadow)
    OPENAI_REALTIME_ENABLED: bool = False
    OPENAI_REALTIME_MODEL: str = "gpt-realtime-2"
    OPENAI_REALTIME_VOICE: str = "marin"
    OPENAI_REALTIME_INSTRUCTIONS_VERSION: str = "fisiogreat-v2"
    OPENAI_REALTIME_TRANSPORT: str = "sip"
    OPENAI_REALTIME_WEBHOOK_SECRET: Optional[str] = None
    OPENAI_REALTIME_LOG_LEVEL: str = "info"
    OPENAI_REALTIME_SHADOW_MODE: bool = True
    OPENAI_REALTIME_WRITE_ENABLED: bool = False

    USE_REAL_CALENDAR: bool = False
    USE_REAL_SUPABASE: bool = False
    USE_REAL_TWILIO: bool = False
    USE_REAL_ELEVENLABS: bool = False

    # ElevenLabs TTS
    ELEVEN_API_KEY: Optional[str] = None
    ELEVEN_VOICE_ID: Optional[str] = None
    ELEVEN_MODEL_ID: str = "eleven_multilingual_v2"

    # URL publica del backend (Railway/ngrok/local tunnel en demo).
    PUBLIC_BASE_URL: str = "http://localhost:8080"

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

    # FisioGreat copy
    CLINIC_NAME: str = "FisioGreat Demo"
    CLINIC_LOCATION: str = ""
    CLINIC_HOURS: str = "Lunes a viernes 09:00-20:00 | Sabado 10:00-14:00 | Domingo cerrado"
    CLINIC_PHONE: Optional[str] = None
    DEMO_CLINIC_ID: str = "fisiogreat-demo"
    HUMAN_HANDOFF_PHONE: Optional[str] = None
    HUMAN_HANDOFF_EMAIL: Optional[str] = None
    FISIO_SERVICES: str = "primera visita de fisioterapia, sesion de fisioterapia, valoracion inicial, consulta de seguimiento"
    FISIO_PRICE_GUIDANCE: str = ""

    # Backwards-compatible names used by older helpers.
    SALON_HOURS: Optional[str] = None
    SALON_LOCATION: Optional[str] = None
    SALON_SERVICES: Optional[str] = None
    SALON_PRICE_GUIDANCE: Optional[str] = None
    SALON_PHONE: Optional[str] = None

    # Google Calendar
    GOOGLE_CREDENTIALS_JSON_BASE64: Optional[str] = None
    GOOGLE_CALENDAR_ID: Optional[str] = None
    GOOGLE_CALENDAR_TIMEZONE: str = "Europe/Madrid"

    # Twilio
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_WHATSAPP_FROM: Optional[str] = None
    TWILIO_VOICE_FROM: Optional[str] = None

    # Supabase
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    SUPABASE_ANON_KEY: Optional[str] = None

    # DB / CORS
    DATABASE_URL: Optional[str] = None
    CORS_ORIGINS: str = "http://localhost:3000"


settings = Settings()

