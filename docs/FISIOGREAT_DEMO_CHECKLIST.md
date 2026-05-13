# FisioGreat Demo Checklist

- `/__health` returns `{"ok": true}`.
- WhatsApp can create, reschedule, and cancel a demo appointment without secrets.
- Voice answers in Spanish for FisioGreat services.
- ConversationRelay keeps WebSocket route and `type=text`, `last=true` responses.
- Emergency messages return the mandatory 112 reply and do not create appointments.
- Double booking is blocked by the booking lock.
- `python -m pytest -q` passes from `api/`.
- Smoke scripts run in demo mode without real credentials.
- If an external ConversationRelay simulator is run in a full mode that creates real appointments, use a configurable future date and clean the created appointment/event after the run; the default smoke scripts should remain in mock/demo mode.
- Railway uses `api/Dockerfile` and healthcheck `/__health`.
