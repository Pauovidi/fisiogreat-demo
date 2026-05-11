import xml.etree.ElementTree as ET
import datetime as dt
import asyncio
import logging

import pytest
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


def future_start(days: int = 7, hour: int = 10) -> dt.datetime:
    return (dt.datetime.now() + dt.timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


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


def drive_conversationrelay_to_contact(websocket, call_sid: str, *, setup_payload=None):
    setup = {"type": "setup", "sessionId": f"VX-{call_sid}", "callSid": call_sid}
    if setup_payload:
        setup.update(setup_payload)
    websocket.send_json(setup)
    websocket.receive_json()
    websocket.send_json({"type": "prompt", "voicePrompt": "quiero sesion de fisioterapia", "last": True})
    websocket.receive_json()
    websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
    websocket.receive_json()
    websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
    websocket.receive_json()
    websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
    websocket.receive_json()
    websocket.send_json({"type": "prompt", "voicePrompt": "la segunda", "last": True})
    ask_contact = websocket.receive_json()
    assert "telefono" in ask_contact["token"].lower() or "email" in ask_contact["token"].lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    return ask_contact


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


def test_conversationrelay_booking_keeps_whatsapp_stage_order_and_masks_contact_logs(caplog):
    call_sid = "CA-conversationrelay-fisio-1"
    reset_state(call_sid)
    caplog.set_level(logging.INFO, logger="pelu-agent")

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-1", "callSid": call_sid})
        opening = websocket.receive_json()
        assert opening["type"] == "text"
        assert opening["last"] is True
        assert "fisiogreat" in opening["token"].lower()

        websocket.send_json({"type": "prompt", "voicePrompt": "quiero sesion de fisioterapia", "last": True})
        ask_reason = websocket.receive_json()
        assert ask_reason["type"] == "text"
        assert ask_reason["last"] is True
        assert "motivo" in ask_reason["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_consultation_reason"

        websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
        ask_name = websocket.receive_json()
        assert ask_name["type"] == "text"
        assert ask_name["last"] is True
        assert "nombre" in ask_name["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_patient_name"

        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        ask_date = websocket.receive_json()
        assert ask_date["type"] == "text"
        assert ask_date["last"] is True
        assert "que dia te va bien" in ask_date["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_date"

        websocket.send_json({"type": "prompt", "voicePrompt": "jueves", "last": True})
        offer = websocket.receive_json()
        assert offer["type"] == "text"
        assert offer["last"] is True
        assert "tengo" in offer["token"].lower()
        assert CTX.get_stage(call_sid) == "offering_slots"

        websocket.send_json({"type": "prompt", "voicePrompt": "la segunda", "last": True})
        ask_contact = websocket.receive_json()
        assert ask_contact["type"] == "text"
        assert ask_contact["last"] is True
        assert "telefono" in ask_contact["token"].lower() or "correo" in ask_contact["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_contact"

        websocket.send_json({"type": "prompt", "voicePrompt": "mi email es pau@example.com", "last": True})
        confirm = websocket.receive_json()
        assert confirm["type"] == "text"
        assert confirm["last"] is True
        assert "gracias" in confirm["token"].lower()
        assert STORE.appointments
        assert CTX.get_stage(call_sid) == "completed"

        websocket.send_json({"type": "prompt", "voicePrompt": "gracias", "last": True})
        thanks = websocket.receive_json()
        assert thanks["type"] == "text"
        assert thanks["last"] is True
        assert "gracias a ti" in thanks["token"].lower()
        assert "te esperamos" in thanks["token"].lower()

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "conversationrelay_turn" in logs
    assert "pau@example.com" not in logs
    assert "pau example com" not in logs
    assert "p***@example.com" in logs


def test_conversationrelay_active_stage_uses_stage_retry_not_generic_natural_fallback(monkeypatch):
    call_sid = "CA-conversationrelay-no-generic-fallback"
    reset_state(call_sid)

    async def fail_natural_turn(*_args, **_kwargs):
        raise AssertionError("natural turn fallback should not run during awaiting_date")

    monkeypatch.setattr("app.services.conversation_relay.maybe_handle_natural_turn", fail_natural_turn)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-no-fallback", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero sesion de fisioterapia", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "cuando puedas", "last": True})
        retry = websocket.receive_json()

    assert retry["type"] == "text"
    assert retry["last"] is True
    assert "no he entendido bien el dia" in retry["token"].lower()
    assert CTX.get_stage(call_sid) == "awaiting_date"


def test_conversationrelay_jueves_que_viene_morning_uses_current_turn_date(monkeypatch):
    call_sid = "CA-conversationrelay-date-jueves-morning"
    reset_state(call_sid)
    monkeypatch.setattr("app.utils.date_parser._today", lambda _tz: dt.date(2026, 5, 11))

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-date-jueves", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "valoracion inicial", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "Pau Marco", "last": True})
        websocket.receive_json()
        CTX.set_date(call_sid, dt.date(2026, 5, 12))
        websocket.send_json({"type": "prompt", "voicePrompt": "el jueves que viene por la mañana", "last": True})
        offered = websocket.receive_json()

    ctx = CTX.get(call_sid)
    assert "para el jueves por la manana" in offered["token"].lower()
    assert "martes" not in offered["token"].lower()
    assert "primera, segunda o tercera" in offered["token"].lower()
    assert ctx["date_pref"] == dt.date(2026, 5, 14)
    assert ctx["time_pref"] == "morning"
    assert all(slot.startswith("jueves 14/05") for slot in ctx["offered_slots"])


@pytest.mark.parametrize(
    ("spoken_name", "expected_name"),
    [
        ("Ah, Marcos Castellano.", "Marcos Castellano"),
        ("Marcos Castellano", "Marcos Castellano"),
    ],
)
def test_conversationrelay_accepts_normal_patient_names_and_continues_to_date(spoken_name, expected_name):
    call_sid = f"CA-conversationrelay-name-{expected_name.replace(' ', '-').lower()}-{len(spoken_name)}"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": f"VX-{call_sid}", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero sesion de fisioterapia", "last": True})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "me duele la rodilla", "last": True})
        ask_name = websocket.receive_json()
        assert "nombre" in ask_name["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_patient_name"

        websocket.send_json({"type": "prompt", "voicePrompt": spoken_name, "last": True})
        ask_date = websocket.receive_json()
        assert ask_date["type"] == "text"
        assert ask_date["last"] is True
        assert "que dia te va bien" in ask_date["token"].lower()
        assert "no he entendido" not in ask_date["token"].lower()
        assert CTX.get_stage(call_sid) == "awaiting_date"
        assert CTX.get(call_sid)["patient_name"] == expected_name

        websocket.send_json({"type": "prompt", "voicePrompt": "el miércoles", "last": True})
        slots = websocket.receive_json()
        assert slots["type"] == "text"
        assert slots["last"] is True
        assert "tengo" in slots["token"].lower()
        assert CTX.get_stage(call_sid) == "offering_slots"


def test_conversationrelay_accepts_spoken_email_contact_and_confirms():
    call_sid = "CA-conversationrelay-spoken-email-contact"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        drive_conversationrelay_to_contact(websocket, call_sid)
        websocket.send_json({
            "type": "prompt",
            "voicePrompt": "mi email es marcos arroba ejemplo punto com",
            "last": True,
        })
        confirm = websocket.receive_json()

    assert confirm["type"] == "text"
    assert "gracias" in confirm["token"].lower()
    assert "no he entendido" not in confirm["token"].lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_email"] == "marcos@ejemplo.com"
    assert appointment["metadata"]["contact_channel_preference"] == "email"
    assert CTX.get_stage(call_sid) == "completed"


def test_conversationrelay_contact_retry_copy_is_helpful_and_not_duplicated():
    call_sid = "CA-conversationrelay-contact-retry-copy"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        drive_conversationrelay_to_contact(websocket, call_sid)
        websocket.send_json({"type": "prompt", "voicePrompt": "Marcos Castellano", "last": True})
        first_retry = websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "marcos arroba ejemplo", "last": True})
        second_retry = websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "gracias", "last": True})
        pending_contact = websocket.receive_json()

    assert "marcos arroba ejemplo punto com" in first_retry["token"].lower()
    assert "sigo sin entenderlo" in second_retry["token"].lower()
    assert "no lo he no lo he" not in second_retry["token"].lower()
    assert "necesito un telefono o email" in pending_contact["token"].lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert not STORE.appointments


def test_conversationrelay_accepts_phone_contact_and_suggested_from_number():
    call_sid = "CA-conversationrelay-phone-contact"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        drive_conversationrelay_to_contact(websocket, call_sid)
        websocket.send_json({"type": "prompt", "voicePrompt": "640 50 50 50", "last": True})
        confirm = websocket.receive_json()

    assert "gracias" in confirm["token"].lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "640505050"

    from_sid = "CA-conversationrelay-from-number"
    reset_state(from_sid)
    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        ask_contact = drive_conversationrelay_to_contact(
            websocket,
            from_sid,
            setup_payload={"from": "+34640505050"},
        )
        assert "este numero" in ask_contact["token"].lower()
        websocket.send_json({"type": "prompt", "voicePrompt": "si a este numero", "last": True})
        confirm_from = websocket.receive_json()

    assert "gracias" in confirm_from["token"].lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "34640505050"


def test_conversationrelay_emergency_in_contact_does_not_confirm():
    call_sid = "CA-conversationrelay-contact-emergency"
    reset_state(call_sid)

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        drive_conversationrelay_to_contact(websocket, call_sid)
        websocket.send_json({"type": "prompt", "voicePrompt": "me duele el pecho y me cuesta respirar", "last": True})
        emergency = websocket.receive_json()

    assert "112" in emergency["token"]
    assert CTX.get_stage(call_sid) == "emergency_detected"
    assert not STORE.appointments


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
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", future_start(7))
    second = create_voice_appointment(call_sid, "valoracion inicial", future_start(8))
    original_first_start = first["start_at"]

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-reschedule-many", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero cambiar mi cita", "last": True})
        listed = websocket.receive_json()
        assert listed["type"] == "text"
        assert listed["last"] is True
        assert "varias citas asociadas a este telefono" in listed["token"].lower()

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
    assert STORE.appointments[first["id"]]["start_at"] == original_first_start
    assert STORE.appointments[second["id"]]["status"] == "confirmed"


def test_conversationrelay_pending_cancel_accepts_natural_confirmation():
    call_sid = "CA-conversationrelay-cancel-natural"
    reset_state(call_sid)
    appointment = create_voice_appointment(
        call_sid,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=7),
    )

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-cancel-natural", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero cancelar mi cita", "last": True})
        found = websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "sí, cancélala", "last": True})
        cancelled = websocket.receive_json()

    assert found["type"] == "text"
    assert "he encontrado tu cita" in found["token"].lower()
    assert cancelled["type"] == "text"
    assert "he cancelado" in cancelled["token"].lower()
    assert "dime si quieres cancelar" not in cancelled["token"].lower()
    assert STORE.appointments[appointment["id"]]["status"] == "cancelled"


def test_conversationrelay_multi_cancel_numeric_selection_requires_confirmation():
    call_sid = "CA-conversationrelay-cancel-multiple-numeric"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", future_start(7))
    second = create_voice_appointment(call_sid, "valoracion inicial", future_start(8))

    with client.websocket_connect("/webhook/voice/conversationrelay/ws") as websocket:
        websocket.send_json({"type": "setup", "sessionId": "VX-cancel-many", "callSid": call_sid})
        websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "quiero cancelar mi cita", "last": True})
        listed = websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "2", "last": True})
        selected = websocket.receive_json()
        websocket.send_json({"type": "prompt", "voicePrompt": "sí, cancélala", "last": True})
        cancelled = websocket.receive_json()

    assert "varias citas asociadas a este telefono" in listed["token"].lower()
    assert "quieres cancelar la cita" in selected["token"].lower()
    assert "he cancelado" in cancelled["token"].lower()
    assert STORE.appointments[first["id"]]["status"] == "confirmed"
    assert STORE.appointments[second["id"]]["status"] == "cancelled"
