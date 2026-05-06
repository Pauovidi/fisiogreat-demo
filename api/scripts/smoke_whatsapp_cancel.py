import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.utils.mini_context import CTX


def main():
    client = TestClient(app)
    user = "+34600999003"
    CTX.clear(user)
    last_response = None
    for body in ["hola", "primera visita", "Pau Marco", "jueves", "1", "quiero cancelar la cita", "sí"]:
        response = client.post("/webhook/whatsapp", data={"From": f"whatsapp:{user}", "Body": body})
        last_response = response
        print(f"\nSENT: {body}")
        print(f"STATUS: {response.status_code}")
        print(response.text)
    assert last_response is not None
    assert "he cancelado" in last_response.text.lower()


if __name__ == "__main__":
    main()
