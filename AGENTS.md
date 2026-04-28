# Agent Notes

- Backend root is `api/`; run tests and smoke scripts from that directory unless `PYTHONPATH=api` is set.
- The demo must run without secrets. Keep `USE_REAL_CALENDAR=false`, `USE_REAL_SUPABASE=false`, `USE_REAL_TWILIO=false`, and `USE_REAL_ELEVENLABS=false` for local work.
- Do not commit `.env`, service-account JSON files, `openai.txt`, private keys, caches, or generated logs.
- Voice latency is protected: do not replace ConversationRelay/WebSocket with request-response, and keep LLM/network calls out of the first voice turn.
