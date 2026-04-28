# FisioGreat Demo Checklist

- `/__health` returns `{"ok": true}`.
- WhatsApp can create, reschedule, and cancel a demo appointment without secrets.
- Voice answers in Spanish for FisioGreat services.
- ConversationRelay keeps WebSocket route and `type=text`, `last=true` responses.
- Emergency messages return the mandatory 112 reply and do not create appointments.
- Double booking is blocked by the booking lock.
- `python -m pytest -q` passes from `api/`.
- Smoke scripts run in demo mode without real credentials.
- Railway uses `api/Dockerfile` and healthcheck `/__health`.
