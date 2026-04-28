import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.main import app
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE
from app.utils.mini_context import CTX


client = TestClient(app)


def parse_xml(text: str) -> ET.Element:
    return ET.fromstring(text)


def reset_state(*keys: str):
    STORE.reset()
    CALENDAR_STORE.reset()
    for key in keys:
        CTX.clear(key)


def post_whatsapp(user: str, body: str):
    response = client.post("/webhook/whatsapp", data={"From": f"whatsapp:{user}", "Body": body})
    assert response.status_code == 200
    parse_xml(response.text)
    return response


def post_voice(call_sid: str, **data):
    payload = {"CallSid": call_sid}
    payload.update(data)
    response = client.post("/webhook/voice/agent", data=payload)
    assert response.status_code == 200
    parse_xml(response.text)
    return response


def test_health_returns_ok():
    response = client.get("/__health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_whatsapp_creates_reschedules_and_cancels_fisiogreat_appointment():
    user = "+34600000001"
    reset_state(user)

    assert "fisio" in post_whatsapp(user, "hola").text.lower()
    assert "día" in post_whatsapp(user, "sesión de fisioterapia").text.lower()
    offered = post_whatsapp(user, "jueves")
    assert "te puedo ofrecer" in offered.text.lower()
    confirmed = post_whatsapp(user, "1")
    assert "te dejo apuntada" in confirmed.text.lower()
    assert len(STORE.appointments) == 1

    post_whatsapp(user, "quiero cambiar la cita")
    changed = post_whatsapp(user, "viernes")
    assert "he cambiado la cita" in changed.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["status"] == "rescheduled"

    post_whatsapp(user, "quiero cancelar la cita")
    cancelled = post_whatsapp(user, "viernes a las 10")
    assert "cancelada" in cancelled.text.lower()
    assert next(iter(STORE.appointments.values()))["status"] == "cancelled"


def test_voice_basic_booking_and_faq_are_fisiogreat():
    call_sid = "CA-voice-fisio-1"
    reset_state(call_sid)

    start = post_voice(call_sid)
    assert "fisiogreat" in start.text.lower()

    service = post_voice(call_sid, SpeechResult="primera visita")
    assert "que dia te va bien" in service.text.lower()

    day = post_voice(call_sid, SpeechResult="jueves por la tarde")
    assert "tengo" in day.text.lower()

    pick = post_voice(call_sid, SpeechResult="segunda")
    assert "perfecto" in pick.text.lower()
    assert len(STORE.appointments) == 1

    thanks = post_voice(call_sid, SpeechResult="gracias")
    assert "fisiogreat" in thanks.text.lower()


def test_voice_reschedule_and_faq_prompts():
    call_sid = "CA-voice-fisio-2"
    reset_state(call_sid)

    post_voice(call_sid)
    response = post_voice(call_sid, SpeechResult="quiero reprogramar mi cita")
    assert "dia nuevo" in response.text.lower() or "día nuevo" in response.text.lower()

    CTX.clear(call_sid)
    post_voice(call_sid)
    faq = post_voice(call_sid, SpeechResult="que servicios teneis")
    assert "fisioterapia" in faq.text.lower() or "valoracion" in faq.text.lower()


def test_emergency_cuts_whatsapp_and_voice_without_booking():
    user = "+34600000002"
    call_sid = "CA-emergency-1"
    reset_state(user, call_sid)

    wa = post_whatsapp(user, "me duele el pecho y no puedo respirar")
    assert "112" in wa.text
    assert "no voy a agendar" in wa.text.lower()

    voice = post_voice(call_sid, SpeechResult="dolor torácico")
    assert "112" in voice.text
    assert not STORE.appointments
