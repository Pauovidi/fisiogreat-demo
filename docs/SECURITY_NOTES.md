# Security Notes

No secret values are required for this phase, and no real tokens should be committed.

The repository ignores:

- `.env`
- `.env.*` except `.env.example`
- `openai.txt`
- `*service-account*.json`
- `pelu-agent-*.json`
- Python caches and pytest caches

If any key from old Pelu Agent ZIPs, service-account JSON files, `.env` files, or `openai.txt` was ever real or shared, rotate it before using this demo in production. Do not rely on local Google JSON files; use `GOOGLE_CREDENTIALS_JSON_BASE64` in the deployment environment.
