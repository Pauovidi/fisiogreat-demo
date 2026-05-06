import xml.etree.ElementTree as ET
import datetime as dt
import asyncio

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services.booking_service import confirm_slot
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


def create_voice_appointment(call_sid: str, service: str, start: dt.datetime):
    result = asyncio.run(confirm_slot(
        channel="voice",
        external_user_id=call_sid,
        service_type=service,
        start_at=start,
        patient_name="Pau Marco",
        contact_email="pau@example.com",
        consultation_reason="rodilla" if "fisio" in service or "fisioterapia" in service else None,
        metadata={"transport": "conversationrelay"},
    ))
    assert result.ok
    return result.appointment


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
        ask_reason = websocket.receive_json()
        assert ask_reason["type"] == "text"
        assert ask_reason["last"] is True
        assert "motivo" in ask_reason["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
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
        ask_contact = websocket.receive_json()
        assert ask_contact["type"] == "text"
        assert ask_contact["last"] is True
        assert "telefono" in ask_contact["token"].lower() or "correo" in ask_contact["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "mi email es pau@example.com", "last": True})
        confirm = websocket.receive_json()
        assert confirm["type"] == "text"
        assert confirm["last"] is True
        assert "gracias" in confirm["token"].lower()
        assert STORE.appointments


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
        websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "primera", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "mi email es pau@example.com", "last": True})
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


def test_conversationrelay_lists_and_selects_multiple_reschedule_options():
    call_sid = "CA-conversationrelay-reschedule-multiple"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", dt.datetime(2026, 5, 7, 10, 0))
    second = create_voice_appointment(call_sid, "valoracion inicial", dt.datetime(2026, 5, 8, 10, 0))

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-reschedule-many", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero cambiar mi cita", "last": True})
        listed = websocket.receive_json()
        assert listed["type"] == "text"
        assert listed["last"] is True
        assert "varias citas futuras" in listed["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "la segunda", "last": True})
        selected = websocket.receive_json()
        assert selected["type"] == "text"
        assert selected["last"] is True
        assert "nuevo dia" in selected["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "viernes", "last": True})
        offer = websocket.receive_json()
        assert offer["type"] == "text"
        assert offer["last"] is True
        assert "tengo" in offer["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "primera", "last": True})
        changed = websocket.receive_json()

    assert changed["type"] == "text"
    assert changed["last"] is True
    assert "he cambiado tu cita" in changed["token"].lower()
    assert STORE.appointments[first["id"]]["start_at"].startswith("2026-05-07")
    assert STORE.appointments[second["id"]]["status"] == "confirmed"
