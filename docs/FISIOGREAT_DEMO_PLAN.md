# FisioGreat Demo Plan

The demo keeps the current low-latency voice architecture and adds a shared booking layer for voice and WhatsApp.

Flow:

1. Emergency guard runs before normal routing or any LLM fallback.
2. Intent routing and slot proposal stay deterministic.
3. Calendar and Supabase calls happen only at slot confirmation.
4. Local tests use in-memory adapters.
5. Real Google Calendar and Supabase activate through flags and credentials.

Channels:

- Voice: Twilio Voice and ConversationRelay WebSocket remain active.
- WhatsApp: Twilio Sandbox inbound webhook at `/webhook/whatsapp`.
- Reminders: demo queue in `reminder_service`, ready to back with Supabase jobs.

Latency compromise:

Calendar/Supabase writes are only in the confirm step. If production latency exceeds the current baseline, switch confirm to two-step: immediate voice reply, then WhatsApp/log/job final confirmation after Calendar commits.
