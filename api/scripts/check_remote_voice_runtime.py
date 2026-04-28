import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings


def post_form(client: httpx.Client, url: str, data: dict) -> str:
    response = client.post(url, data=data)
    response.raise_for_status()
    return response.text


def main():
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    agent_url = f"{base_url}/webhook/voice/agent"
    openapi_url = f"{base_url}/openapi.json"

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        openapi = client.get(openapi_url)
        openapi.raise_for_status()
        schema = openapi.json()
        required = (
            schema.get("components", {})
            .get("schemas", {})
            .get("Body_agent_entry_webhook_voice_agent_post", {})
            .get("required", [])
        )

        start = post_form(client, agent_url, {"CallSid": "runtime-check", "From": "+34000000000"})
        second = post_form(
            client,
            agent_url,
            {"CallSid": "runtime-check", "From": "+34000000000", "SpeechResult": "corte"},
        )

    print("PUBLIC_BASE_URL:", base_url)
    print("REMOTE_REQUIRED_FIELDS:", required)
    print("\nSTART_RESPONSE:\n", start)
    print("\nSECOND_RESPONSE:\n", second)

    drift = []
    if "From" in required:
        drift.append("remote_requires_from")
    if "Soy Pelu Agent" in second or "soy Pelu Agent" in second:
        drift.append("remote_repeats_branding")
    if "<Play>" in start or "<Play>" in second:
        drift.append("remote_uses_play_tts")

    print("\nDRIFT_FLAGS:", ", ".join(drift) if drift else "none")


if __name__ == "__main__":
    main()
