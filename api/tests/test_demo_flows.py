import xml.etree.ElementTree as ET
import datetime as dt

from fastapi.testclient import TestClient

from app.main import app
from app.services.booking_service import confirm_slot
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


def post_whatsapp(user: str, body: str, profile_name: str = ""):
    response = client.post(
        "/webhook/whatsapp",
        data={"From": f"whatsapp:{user}", "Body": body, "ProfileName": profile_name},
    )
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


def create_voice_appointment(call_sid: str, service: str, start: dt.datetime):
    result = __import__("asyncio").run(confirm_slot(
        channel="voice",
        external_user_id=call_sid,
        service_type=service,
        start_at=start,
        patient_name="Pau Marco",
        contact_email="pau@example.com",
        consultation_reason="rodilla" if "fisio" in service or "fisioterapia" in service else None,
    ))
    assert result.ok
    return result.appointment


def test_health_returns_ok():
    response = client.get("/__health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_whatsapp_creates_reschedules_and_cancels_fisiogreat_appointment():
    user = "+34600000001"
    reset_state(user)

    assert "fisio" in post_whatsapp(user, "hola").text.lower()
    assert "motivo" in post_whatsapp(user, "sesión de fisioterapia").text.lower()
    assert "nombre" in post_whatsapp(user, "me duele la rodilla").text.lower()
    assert "día" in post_whatsapp(user, "Pau Marco").text.lower()
    offered = post_whatsapp(user, "jueves")
    assert "te puedo ofrecer" in offered.text.lower()
    confirmed = post_whatsapp(user, "1")
    assert "te dejo apuntada" in confirmed.text.lower()
    assert len(STORE.appointments) == 1

    found = post_whatsapp(user, "quiero cambiar la cita")
    assert "he encontrado tu cita" in found.text.lower()
    assert "quieres cambiar" in found.text.lower()
    post_whatsapp(user, "sí")
    offered_change = post_whatsapp(user, "viernes")
    assert "te puedo ofrecer" in offered_change.text.lower()
    changed = post_whatsapp(user, "1")
    assert "he cambiado tu cita" in changed.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["status"] == "confirmed"

    found_cancel = post_whatsapp(user, "quiero cancelar la cita")
    assert "he encontrado tu cita" in found_cancel.text.lower()
    cancelled = post_whatsapp(user, "sí")
    assert "he cancelado" in cancelled.text.lower()
    assert next(iter(STORE.appointments.values()))["status"] == "cancelled"


def test_voice_basic_booking_and_faq_are_fisiogreat():
    call_sid = "CA-voice-fisio-1"
    reset_state(call_sid)

    start = post_voice(call_sid)
    assert "fisiogreat" in start.text.lower()

    service = post_voice(call_sid, SpeechResult="primera visita")
    assert "nombre" in service.text.lower()

    name = post_voice(call_sid, SpeechResult="Pau Marco")
    assert "que dia te va bien" in name.text.lower()

    day = post_voice(call_sid, SpeechResult="jueves por la tarde")
    assert "tengo" in day.text.lower()

    pick = post_voice(call_sid, SpeechResult="segunda")
    assert "telefono" in pick.text.lower() or "correo" in pick.text.lower()
    contact = post_voice(call_sid, SpeechResult="mi email es pau@example.com")
    assert "gracias" in contact.text.lower()
    assert len(STORE.appointments) == 1

    thanks = post_voice(call_sid, SpeechResult="gracias")
    assert "te esperamos" in thanks.text.lower()


def test_voice_physiotherapy_requires_reason_and_contact_before_confirmation():
    call_sid = "CA-voice-reason-contact-1"
    reset_state(call_sid)

    post_voice(call_sid)
    service = post_voice(call_sid, SpeechResult="sesion de fisioterapia")
    assert "motivo" in service.text.lower()

    reason = post_voice(call_sid, SpeechResult="me duele la rodilla")
    assert "nombre" in reason.text.lower()
    assert CTX.get(call_sid)["consultation_reason"] == "me duele la rodilla"

    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    contact_prompt = post_voice(call_sid, SpeechResult="primera")

    assert "telefono" in contact_prompt.text.lower() or "correo" in contact_prompt.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert not STORE.appointments


def test_voice_confirms_with_valid_email_and_stores_contact():
    call_sid = "CA-voice-email-1"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="sesion de fisioterapia")
    post_voice(call_sid, SpeechResult="me duele la rodilla")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="mi email es pau@example.com")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_email"] == "pau@example.com"
    assert appointment["metadata"]["consultation_reason"] == "me duele la rodilla"


def test_voice_reprompts_invalid_contact_without_booking():
    call_sid = "CA-voice-invalid-contact-1"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="no lo se")

    assert "repetir" in response.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert not STORE.appointments


def test_voice_confirms_with_valid_phone_and_can_confirm_from_number():
    phone_sid = "CA-voice-phone-1"
    reset_state(phone_sid)

    post_voice(phone_sid)
    post_voice(phone_sid, SpeechResult="valoracion inicial")
    post_voice(phone_sid, SpeechResult="Pau Marco")
    post_voice(phone_sid, SpeechResult="jueves")
    post_voice(phone_sid, SpeechResult="primera")
    response = post_voice(phone_sid, SpeechResult="mi telefono es 640 78 67 65")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "640786765"

    from_sid = "CA-voice-from-1"
    reset_state(from_sid)
    post_voice(from_sid)
    post_voice(from_sid, SpeechResult="valoracion inicial")
    post_voice(from_sid, SpeechResult="Pau Marco")
    post_voice(from_sid, SpeechResult="jueves")
    prompt = post_voice(from_sid, SpeechResult="primera", From="+34640786765")
    assert "este numero" in prompt.text.lower()
    response = post_voice(from_sid, SpeechResult="si a este numero")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "34640786765"


def test_voice_reschedule_and_faq_prompts():
    call_sid = "CA-voice-fisio-2"
    reset_state(call_sid)

    post_voice(call_sid)
    response = post_voice(call_sid, SpeechResult="quiero reprogramar mi cita")
    assert "no encuentro citas futuras" in response.text.lower()

    CTX.clear(call_sid)
    post_voice(call_sid)
    faq = post_voice(call_sid, SpeechResult="que servicios teneis")
    assert "fisioterapia" in faq.text.lower() or "valoracion" in faq.text.lower()


def test_voice_can_cancel_and_reschedule_existing_booking():
    cancel_sid = "CA-voice-cancel-1"
    reset_state(cancel_sid)

    post_voice(cancel_sid)
    post_voice(cancel_sid, SpeechResult="primera visita")
    post_voice(cancel_sid, SpeechResult="Pau Marco")
    post_voice(cancel_sid, SpeechResult="jueves")
    post_voice(cancel_sid, SpeechResult="primera")
    post_voice(cancel_sid, SpeechResult="mi telefono es 640 78 67 65")

    prompt = post_voice(cancel_sid, SpeechResult="quiero cancelar la cita")
    assert "he encontrado tu cita" in prompt.text.lower()
    cancelled = post_voice(cancel_sid, SpeechResult="si")
    assert "he cancelado" in cancelled.text.lower()
    assert next(iter(STORE.appointments.values()))["status"] == "cancelled"

    reschedule_sid = "CA-voice-reschedule-1"
    reset_state(reschedule_sid)

    post_voice(reschedule_sid)
    post_voice(reschedule_sid, SpeechResult="sesion de fisioterapia")
    post_voice(reschedule_sid, SpeechResult="me duele la rodilla")
    post_voice(reschedule_sid, SpeechResult="Pau Marco")
    post_voice(reschedule_sid, SpeechResult="jueves")
    post_voice(reschedule_sid, SpeechResult="primera")
    post_voice(reschedule_sid, SpeechResult="mi email es pau@example.com")

    prompt = post_voice(reschedule_sid, SpeechResult="quiero cambiar la cita")
    assert "he encontrado tu cita" in prompt.text.lower()
    post_voice(reschedule_sid, SpeechResult="si")
    offered = post_voice(reschedule_sid, SpeechResult="viernes")
    assert "tengo" in offered.text.lower()
    changed = post_voice(reschedule_sid, SpeechResult="primera")
    assert "he cambiado tu cita" in changed.text.lower()
    assert next(iter(STORE.appointments.values()))["status"] == "confirmed"


def test_voice_lists_multiple_future_appointments_and_uses_selection_not_last_slot():
    call_sid = "CA-voice-multiple-appointments"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", dt.datetime(2026, 5, 7, 10, 0))
    second = create_voice_appointment(call_sid, "valoracion inicial", dt.datetime(2026, 5, 8, 10, 0))

    listed = post_voice(call_sid, SpeechResult="quiero cambiar mi cita")
    assert "varias citas futuras" in listed.text.lower()
    assert "primera" in listed.text.lower()
    assert "segunda" in listed.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_reschedule_selection"

    selected = post_voice(call_sid, SpeechResult="la segunda")
    assert "nuevo dia" in selected.text.lower() or "nuevo día" in selected.text.lower()
    assert CTX.get(call_sid)["selected_appointment_id"] == second["id"]

    post_voice(call_sid, SpeechResult="viernes")
    changed = post_voice(call_sid, SpeechResult="primera")
    assert "he cambiado tu cita" in changed.text.lower()
    assert STORE.appointments[first["id"]]["start_at"].startswith("2026-05-07")
    assert STORE.appointments[second["id"]]["start_at"].startswith("2026-05-08")


def test_voice_lists_multiple_future_appointments_for_cancel_and_understands_first():
    call_sid = "CA-voice-cancel-multiple"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", dt.datetime(2026, 5, 7, 10, 0))
    second = create_voice_appointment(call_sid, "valoracion inicial", dt.datetime(2026, 5, 8, 10, 0))

    listed = post_voice(call_sid, SpeechResult="quiero cancelar mi cita")
    assert "varias citas futuras" in listed.text.lower()
    cancelled = post_voice(call_sid, SpeechResult="la primera")

    assert "he cancelado" in cancelled.text.lower()
    assert STORE.appointments[first["id"]]["status"] == "cancelled"
    assert STORE.appointments[second["id"]]["status"] == "confirmed"


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
