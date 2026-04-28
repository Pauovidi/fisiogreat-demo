# FisioGreat Production Roadmap

1. Rotate any historical keys that may have existed in old ZIPs or local files.
2. Provision Supabase and run `supabase/migrations/202604280001_fisiogreat_schema.sql`.
3. Create a Google service account, share the clinic calendar, and set `GOOGLE_CREDENTIALS_JSON_BASE64`.
4. Configure Twilio Voice, WhatsApp Sandbox or approved sender, and ConversationRelay URLs with the Railway public URL.
5. Add background execution for reminder jobs.
6. Add idempotency/retry policy around Calendar and Supabase writes.
7. Add monitoring for `conversationrelay_latency` and booking errors.
8. Load real FAQ items and pricing policy into Supabase.
9. Add human handoff routing to a real team phone/email.
