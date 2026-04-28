import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.utils.mini_context import CTX


def main():
    client = TestClient(app)
    call_sid = "smoke-conversationrelay-1"
    CTX.clear(call_sid)

    twiml_response = client.post("/webhook/voice/conversationrelay")
    print("TwiML route")
    print(f"STATUS: {twiml_response.status_code}")
    print(twiml_response.text)

    print("\nWebSocket flow")
    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        steps = [
            {"type": "setup", "sessionId": "VX-smoke-conversationrelay-1", "callSid": call_sid},
            {"type": "prompt", "voicePrompt": "peinado", "last": True},
            {"type": "prompt", "voicePrompt": "jueves", "last": True},
            {"type": "prompt", "voicePrompt": "segunda", "last": True},
        ]

        for index, payload in enumerate(steps, start=1):
            websocket.send_json(payload)
            print(f"\nSTEP {index}")
            print(f"SENT: {payload}")
            print(f"RECV: {websocket.receive_json()}")


if __name__ == "__main__":
    main()
