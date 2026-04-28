# FisioGreat Environment Setup

Local demo mode does not require real secrets. Keep these flags disabled:

```env
USE_REAL_CALENDAR=false
USE_REAL_SUPABASE=false
USE_REAL_TWILIO=false
USE_REAL_ELEVENLABS=false
```

Required later for real integrations:

- OpenAI: `OPENAI_API_KEY` only if `VOICE_AI_ENABLED=true`.
- ElevenLabs: `ELEVEN_API_KEY`, `ELEVEN_VOICE_ID`, optional `ELEVEN_MODEL_ID`.
- Twilio: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `TWILIO_VOICE_FROM`.
- Google Calendar: `GOOGLE_CREDENTIALS_JSON_BASE64`, `GOOGLE_CALENDAR_ID`, `GOOGLE_CALENDAR_TIMEZONE=Europe/Madrid`.
- Supabase: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, optional `SUPABASE_ANON_KEY`.
- Clinic copy: `CLINIC_NAME`, `CLINIC_LOCATION`, `CLINIC_HOURS`, `CLINIC_PHONE`, `HUMAN_HANDOFF_PHONE`, `HUMAN_HANDOFF_EMAIL`.

Put variables in Railway project variables for deployment. For local runs, copy `.env.example` into a local `.env` inside `api/` or export the variables in your shell. Do not commit `.env`.

Google Calendar must use a base64-encoded service-account JSON in `GOOGLE_CREDENTIALS_JSON_BASE64`; do not mount local JSON files. Share the target Google Calendar with the service-account email and set `GOOGLE_CALENDAR_ID`.

To turn on real services, set the relevant `USE_REAL_*` flag to `true` only after its credentials are present.
