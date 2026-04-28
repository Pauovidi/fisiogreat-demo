import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.utils.mini_context import CTX


def main():
    client = TestClient(app)
    call_sid = "smoke-voice-1"
    CTX.clear(call_sid)

    sequence = [
        {},
        {"SpeechResult": "corte"},
        {"SpeechResult": "sábado"},
        {"Digits": "1"},
        {"SpeechResult": "cual es el horario"},
        {"SpeechResult": "ruido raro"},
    ]

    for step, payload in enumerate(sequence, start=1):
        data = {"CallSid": call_sid}
        data.update(payload)
        response = client.post("/webhook/voice/agent", data=data)
        print(f"\nSTEP {step}")
        print(f"STATUS: {response.status_code}")
        print(response.text)


if __name__ == "__main__":
    main()
