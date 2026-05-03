import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE
from app.utils.mini_context import CTX


client = TestClient(app)


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


def reset_state(key: str):
    STORE.reset()
    CALENDAR_STORE.reset()
    CTX.clear(key)


def test_conversationrelay_twiml_exposes_websocket_without_cloud_run_coupling():
    response = client.post("/webhook/voice/conversationrelay")
    assert response.status_code == 200
    relay = parse_xml(response.text).find("Connect/ConversationRelay")
    assert relay is not None
    expected_ws_url = (
        settings.PUBLIC_BASE_URL.rstrip("/")
        .replace("https://", "wss://", 1)
        .replace("http://", "ws://", 1)
        + "/webhook/voice/conversationrelay/ws"
    )
    assert relay.attrib.get("url") == expected_ws_url
    assert relay.attrib.get("ttsLanguage") == "es-ES"
    assert relay.attrib.get("transcriptionLanguage") == "es-ES"


def test_conversationrelay_booking_keeps_text_last_shape():
    call_sid = "CA-conversationrelay-fisio-1"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-1", "callSid": call_sid})
        opening = websocket.receive_json()
        assert opening["type"] == "text"
        assert opening["last"] is True
        assert "fisiogreat" in opening["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "sesion de fisioterapia", "last": True})
        ask_name = websocket.receive_json()
        assert ask_name["type"] == "text"
        assert ask_name["last"] is True
        assert "nombre" in ask_name["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        ask_date = websocket.receive_json()
        assert ask_date["type"] == "text"
        assert ask_date["last"] is True
        assert "que dia te va bien" in ask_date["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
        offer = websocket.receive_json()
        assert offer["type"] == "text"
        assert offer["last"] is True
        assert "tengo" in offer["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "primera", "last": True})
        confirm = websocket.receive_json()
        assert confirm["type"] == "text"
        assert confirm["last"] is True
        assert "perfecto" in confirm["token"].lower()
        assert "pau" in confirm["token"].lower()


def test_conversationrelay_calendar_failure_does_not_confirm(monkeypatch):
    call_sid = "CA-conversationrelay-calendar-fail-1"
    reset_state(call_sid)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fail_create_event(**_kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fail_create_event)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-calendar-fail", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "sesion de fisioterapia", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "primera", "last": True})
        response = websocket.receive_json()

    assert response["type"] == "text"
    assert response["last"] is True
    assert "perfecto" not in response["token"].lower()
    assert not STORE.appointments


def test_conversationrelay_emergency_cuts_flow():
    call_sid = "CA-conversationrelay-emergency-1"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-emergency", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "sintomas de ictus", "last": True})
        response = websocket.receive_json()
        assert response["type"] == "text"
        assert response["last"] is True
        assert "112" in response["token"]
        assert not STORE.appointments
