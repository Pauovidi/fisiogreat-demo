import xml.etree.ElementTree as ET
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.booking_service import BookingResult, confirm_slot
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


def future_start(days: int = 7, hour: int = 10) -> dt.datetime:
    return (dt.datetime.now() + dt.timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


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
    assert "qué día te va bien" in name.text.lower()

    day = post_voice(call_sid, SpeechResult="jueves por la tarde")
    assert "tengo" in day.text.lower()

    pick = post_voice(call_sid, SpeechResult="segunda")
    assert "teléfono" in pick.text.lower() or "telefono" in pick.text.lower() or "email" in pick.text.lower()
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

    assert "teléfono" in contact_prompt.text.lower() or "telefono" in contact_prompt.text.lower() or "email" in contact_prompt.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert not STORE.appointments


def test_voice_jueves_que_viene_morning_uses_current_turn_date(monkeypatch):
    call_sid = "CA-voice-date-jueves-morning"
    reset_state(call_sid)
    monkeypatch.setattr("app.utils.date_parser._today", lambda _tz: dt.date(2026, 5, 11))

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    CTX.set_date(call_sid, dt.date(2026, 5, 12))
    response = post_voice(call_sid, SpeechResult="el jueves que viene por la mañana")

    ctx = CTX.get(call_sid)
    assert "para el jueves por la mañana" in response.text.lower()
    assert "primera, segunda o tercera" in response.text.lower()
    assert ctx["date_pref"] == dt.date(2026, 5, 14)
    assert ctx["time_pref"] == "morning"
    assert all(slot.startswith("jueves 14/05") for slot in ctx["offered_slots"])


def test_voice_unparsed_date_does_not_offer_slots_from_stored_state(monkeypatch):
    call_sid = "CA-voice-date-unparsed"
    reset_state(call_sid)
    monkeypatch.setattr("app.utils.date_parser._today", lambda _tz: dt.date(2026, 5, 11))

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    CTX.set_date(call_sid, dt.date(2026, 5, 12))
    response = post_voice(call_sid, SpeechResult="cuando puedas")

    assert "no he entendido bien el día" in response.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_date"
    assert CTX.get(call_sid)["offered_slots"] == []


def test_voice_date_retry_uses_human_accents_not_normalized_text():
    call_sid = "CA-voice-human-accents"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    response = post_voice(call_sid, SpeechResult="por la mañana")
    say_text = parse_xml(response.text).find(".//Say").text

    assert "mañana" in say_text
    assert "manana" not in say_text
    assert "día" in say_text


def test_voice_weekday_mismatch_does_not_offer_wrong_day(monkeypatch):
    call_sid = "CA-voice-date-weekday-mismatch"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    monkeypatch.setattr("app.utils.date_parser.parse_spanish_day", lambda *_args, **_kwargs: dt.date(2026, 5, 13))
    CTX.set_date(call_sid, dt.date(2026, 5, 12))
    response = post_voice(call_sid, SpeechResult="el jueves por la mañana")

    assert "creo que he entendido jueves" in response.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_date"
    assert CTX.get(call_sid)["date_pref"] is None
    assert CTX.get(call_sid)["offered_slots"] == []


def test_voice_offering_slots_selects_la_primera_without_repeating():
    call_sid = "CA-voice-slot-la-primera"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    contact_prompt = post_voice(call_sid, SpeechResult="la primera")

    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert "tengo estas opciones" not in contact_prompt.text.lower()
    assert "email" in contact_prompt.text.lower() or "teléfono" in contact_prompt.text.lower()


@pytest.mark.parametrize(
    ("selection", "expected_slot"),
    [
        ("la primera", "jueves 14/05 a las 10:00"),
        ("primera", "jueves 14/05 a las 10:00"),
        ("opción uno", "jueves 14/05 a las 10:00"),
        ("la segunda", "jueves 14/05 a las 10:15"),
    ],
)
def test_voice_offering_slots_selection_variants_choose_slot_before_fallback(selection, expected_slot):
    call_sid = f"CA-voice-slot-variant-{selection.replace(' ', '-')}"
    reset_state(call_sid)
    CTX.set_service(call_sid, "valoracion inicial")
    CTX.set_slots(
        call_sid,
        [
            "jueves 14/05 a las 10:00",
            "jueves 14/05 a las 10:15",
            "jueves 14/05 a las 10:30",
        ],
    )
    CTX.set_stage(call_sid, "offering_slots")

    response = post_voice(call_sid, SpeechResult=selection)

    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert CTX.get(call_sid)["pending_slot"] == expected_slot
    assert "tengo estas opciones" not in response.text.lower()


@pytest.mark.parametrize(
    ("spoken_name", "expected_name"),
    [
        ("Ah, Marcos Castellano.", "Marcos Castellano"),
        ("Marcos Castellano", "Marcos Castellano"),
    ],
)
def test_voice_accepts_normal_patient_names_and_continues_to_date(spoken_name, expected_name):
    call_sid = f"CA-voice-name-{len(spoken_name)}"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="quiero sesion de fisioterapia")
    ask_name = post_voice(call_sid, SpeechResult="me duele la rodilla")
    assert "nombre" in ask_name.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_patient_name"

    ask_date = post_voice(call_sid, SpeechResult=spoken_name)
    assert "qué día te va bien" in ask_date.text.lower()
    assert "no he entendido" not in ask_date.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_date"
    assert CTX.get(call_sid)["patient_name"] == expected_name

    slots = post_voice(call_sid, SpeechResult="el miércoles")
    assert "tengo" in slots.text.lower()
    assert CTX.get_stage(call_sid) == "offering_slots"


def test_voice_patient_name_retry_copy_changes_after_first_failure():
    call_sid = "CA-voice-name-retry-copy"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="quiero sesion de fisioterapia")
    post_voice(call_sid, SpeechResult="me duele la rodilla")

    first_retry = post_voice(call_sid, SpeechResult="640 78 67 65")
    assert "dime solo el nombre y apellidos" in first_retry.text.lower()
    assert "marcos castellano" in first_retry.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_patient_name"

    second_retry = post_voice(call_sid, SpeechResult="mañana")
    assert "dime solo el nombre y apellidos" in second_retry.text.lower()
    assert "marcos castellano" not in second_retry.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_patient_name"


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


def test_voice_confirms_with_spoken_email_and_stores_normalized_contact():
    call_sid = "CA-voice-spoken-email-1"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="sesion de fisioterapia")
    post_voice(call_sid, SpeechResult="me duele la rodilla")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="mi email es marcos arroba ejemplo punto com")

    assert "gracias" in response.text.lower()
    assert "no he entendido" not in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_email"] == "marcos@ejemplo.com"
    assert appointment["metadata"]["contact_channel_preference"] == "email"


@pytest.mark.parametrize("contact_request", ["un email", "un correo", "el correo", "correo electrónico", "mail", "mi mail", "otro teléfono"])
def test_voice_awaiting_contact_accepts_email_request_then_spoken_email(contact_request):
    call_sid = f"CA-voice-email-request-{contact_request.replace(' ', '-')}"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")

    ask_contact = post_voice(call_sid, SpeechResult=contact_request)
    if contact_request == "otro teléfono":
        assert "perfecto, dime el teléfono móvil" in ask_contact.text.lower()
        assert CTX.get_stage(call_sid) == "awaiting_contact_phone"
        response = post_voice(call_sid, SpeechResult="640 50 50 50")
    else:
        assert "perfecto, dime solo el correo electrónico" in ask_contact.text.lower()
        assert CTX.get_stage(call_sid) == "awaiting_contact_email"
        response = post_voice(call_sid, SpeechResult="mi email es marcos arroba ejemplo punto com")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    if contact_request == "otro teléfono":
        assert appointment["metadata"]["contact_phone"] == "640505050"
    else:
        assert appointment["metadata"]["contact_email"] == "marcos@ejemplo.com"


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("mi mail es marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
        ("mi correo es marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
        ("marcos arroba fisiobrade punto com", "marcos@fisiobrade.com"),
        ("marcos arroba uno punto com", "marcos@uno.com"),
        ("marcos punto castellano arroba gmail punto com", "marcos.castellano@gmail.com"),
    ],
)
def test_voice_awaiting_contact_accepts_direct_spoken_email(spoken, expected):
    call_sid = f"CA-voice-direct-email-{expected.split('@', 1)[1].replace('.', '-')}"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult=spoken)

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_email"] == expected
    assert CTX.get_stage(call_sid) == "completed"


def test_voice_awaiting_contact_phone_substage_confirms_with_phone():
    call_sid = "CA-voice-phone-method-substage"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    ask_phone = post_voice(call_sid, SpeechResult="un teléfono")

    assert "perfecto, dime el teléfono móvil" in ask_phone.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact_phone"
    response = post_voice(call_sid, SpeechResult="640 50 50 50")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "640505050"


def test_voice_awaiting_contact_email_reprompts_email_without_slot_fallback():
    call_sid = "CA-voice-email-substage-retry"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    post_voice(call_sid, SpeechResult="un correo")
    retry = post_voice(call_sid, SpeechResult="opción dos")

    assert "correo completo" in retry.text.lower()
    assert "hueco acaba de ocuparse" not in retry.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact_email"
    assert not STORE.appointments


def test_voice_reprompts_invalid_contact_without_booking():
    call_sid = "CA-voice-invalid-contact-1"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="no lo se")

    assert "marcos arroba ejemplo punto com" in response.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert not STORE.appointments


@pytest.mark.parametrize(
    "spoken",
    [
        "marcos arroba ejemplo",
        "mi mail es marcos arroba ejemplo dos como",
        "marcos arroba fisiograde",
        "marcos arroba ejemplo punto",
    ],
)
def test_voice_incomplete_email_stays_in_email_stage_without_confirming(spoken):
    call_sid = f"CA-voice-incomplete-email-{abs(hash(spoken))}"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    post_voice(call_sid, SpeechResult="el correo")
    response = post_voice(call_sid, SpeechResult=spoken)

    text = response.text.lower()
    assert "correo completo" in text
    assert "hueco acaba de ocuparse" not in text
    assert "no he podido confirmar" not in text
    assert CTX.get_stage(call_sid) == "awaiting_contact_email"
    assert not STORE.appointments


def test_voice_contact_retry_copy_changes_and_requires_contact_before_thanks():
    call_sid = "CA-voice-contact-retry-copy"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")

    first_retry = post_voice(call_sid, SpeechResult="Marcos Castellano")
    assert "marcos arroba ejemplo punto com" in first_retry.text.lower()
    second_retry = post_voice(call_sid, SpeechResult="marcos arroba ejemplo")
    assert "sigo sin entender el correo" in second_retry.text.lower()
    assert "no lo he no lo he" not in second_retry.text.lower()
    thanks = post_voice(call_sid, SpeechResult="gracias")
    assert "necesito un teléfono o correo electrónico" in thanks.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_contact_email"
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
    assert "este número" in prompt.text.lower() or "este numero" in prompt.text.lower()
    response = post_voice(from_sid, SpeechResult="este número")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "34640786765"


def test_voice_confirms_with_spoken_phone_words():
    call_sid = "CA-voice-spoken-phone"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="seis cuarenta cincuenta cincuenta cincuenta")

    assert "gracias" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["contact_phone"] == "640505050"


def test_voice_preserves_selected_slot_through_contact():
    call_sid = "CA-voice-selected-slot-preserved"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="la primera")

    selected = CTX.get(call_sid)["selected_slot"]
    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert CTX.get(call_sid)["pending_slot"] == "jueves 14/05 a las 10:00"
    assert selected["label"] == "jueves 14/05 a las 10:00"
    assert selected["start_at"] == dt.datetime(2026, 5, 14, 10, 0)
    assert selected["end_at"] == dt.datetime(2026, 5, 14, 11, 0)


def test_voice_reoffers_when_selected_slot_gets_busy_and_preserves_contact(monkeypatch):
    call_sid = "CA-voice-reoffer-preserve-contact"
    reset_state(call_sid)
    calls = []

    async def fake_confirm_slot(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return BookingResult(False, reason="calendar_busy")
        return BookingResult(True, appointment={"metadata": {"patient_name": kwargs.get("patient_name")}})

    def fake_slots(*_args, **_kwargs):
        return [
            {"start": dt.datetime(2026, 5, 14, 10, 0), "end": dt.datetime(2026, 5, 14, 11, 0)},
            {"start": dt.datetime(2026, 5, 14, 10, 15), "end": dt.datetime(2026, 5, 14, 11, 15)},
            {"start": dt.datetime(2026, 5, 14, 10, 30), "end": dt.datetime(2026, 5, 14, 11, 30)},
        ]

    monkeypatch.setattr("app.routers.voice.confirm_slot", fake_confirm_slot)
    monkeypatch.setattr("app.routers.voice.booking_propose_slots", fake_slots)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    reoffer = post_voice(call_sid, SpeechResult="marcos arroba ejemplo punto com")

    assert "conservo tu contacto" in reoffer.text.lower()
    assert CTX.get_stage(call_sid) == "offering_slots"
    assert CTX.get(call_sid)["contact_email"] == "marcos@ejemplo.com"
    assert CTX.get(call_sid)["offered_slots"] == [
        "jueves 14/05 a las 10:15",
        "jueves 14/05 a las 10:30",
    ]

    confirmed = post_voice(call_sid, SpeechResult="opción dos")

    assert "gracias" in confirmed.text.lower()
    assert len(calls) == 2
    assert calls[1]["contact_email"] == "marcos@ejemplo.com"
    assert calls[1]["start_at"] == dt.datetime(2026, 5, 14, 10, 30)
    assert CTX.get_stage(call_sid) == "completed"


def test_voice_reoffer_without_contact_still_asks_contact_after_slot_selection():
    call_sid = "CA-voice-reoffer-no-contact"
    reset_state(call_sid)
    CTX.set_service(call_sid, "valoracion inicial")
    CTX.set_patient_name(call_sid, "Pau Marco", "manual")
    CTX.set_date(call_sid, dt.date(2026, 5, 14))
    CTX.set_time_pref(call_sid, "morning")
    CTX.set_slots(
        call_sid,
        [
            "jueves 14/05 a las 10:15",
            "jueves 14/05 a las 10:30",
            "jueves 14/05 a las 10:45",
        ],
    )
    CTX.set_stage(call_sid, "offering_slots")
    CTX.next_slots(call_sid, 3)

    response = post_voice(call_sid, SpeechResult="opción dos")

    assert CTX.get_stage(call_sid) == "awaiting_contact"
    assert CTX.get(call_sid)["pending_slot"] == "jueves 14/05 a las 10:30"
    assert "correo electrónico" in response.text.lower() or "teléfono" in response.text.lower()


def test_voice_emergency_in_contact_does_not_confirm():
    call_sid = "CA-voice-contact-emergency"
    reset_state(call_sid)

    post_voice(call_sid)
    post_voice(call_sid, SpeechResult="valoracion inicial")
    post_voice(call_sid, SpeechResult="Pau Marco")
    post_voice(call_sid, SpeechResult="jueves")
    post_voice(call_sid, SpeechResult="primera")
    response = post_voice(call_sid, SpeechResult="me duele el pecho y me cuesta respirar")

    assert "112" in response.text
    assert CTX.get_stage(call_sid) == "emergency_detected"
    assert not STORE.appointments


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
    cancelled = post_voice(cancel_sid, SpeechResult="sí, cancélala")
    assert "he cancelado" in cancelled.text.lower()
    assert "dime si quieres cancelar" not in cancelled.text.lower()
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


def test_voice_pending_cancel_accepts_cancelala_and_decline():
    call_sid = "CA-voice-cancel-natural"
    reset_state(call_sid)
    appointment = create_voice_appointment(
        call_sid,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=7),
    )

    prompt = post_voice(call_sid, SpeechResult="quiero cancelar mi cita")
    cancelled = post_voice(call_sid, SpeechResult="cancélala")

    assert "he encontrado tu cita" in prompt.text.lower()
    assert "he cancelado" in cancelled.text.lower()
    assert "dime si quieres cancelar" not in cancelled.text.lower()
    assert STORE.appointments[appointment["id"]]["status"] == "cancelled"

    decline_sid = "CA-voice-cancel-decline"
    reset_state(decline_sid)
    kept_appointment = create_voice_appointment(
        decline_sid,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=8),
    )
    post_voice(decline_sid, SpeechResult="quiero cancelar mi cita")
    kept = post_voice(decline_sid, SpeechResult="no la canceles")

    assert "de acuerdo, mantengo tu cita como estaba" in kept.text.lower()
    assert STORE.appointments[kept_appointment["id"]]["status"] == "confirmed"


def test_voice_lists_multiple_future_appointments_and_uses_selection_not_last_slot():
    call_sid = "CA-voice-multiple-appointments"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", future_start(7))
    second = create_voice_appointment(call_sid, "valoracion inicial", future_start(8))
    original_first_start = first["start_at"]
    original_second_start = second["start_at"]

    listed = post_voice(call_sid, SpeechResult="quiero cambiar mi cita")
    assert "varias citas asociadas a este teléfono" in listed.text.lower()
    assert "primera" in listed.text.lower()
    assert "segunda" in listed.text.lower()
    assert CTX.get_stage(call_sid) == "awaiting_reschedule_selection"

    selected = post_voice(call_sid, SpeechResult="la segunda")
    assert "nuevo dia" in selected.text.lower() or "nuevo día" in selected.text.lower()
    assert CTX.get(call_sid)["selected_appointment_id"] == second["id"]

    post_voice(call_sid, SpeechResult="viernes")
    changed = post_voice(call_sid, SpeechResult="primera")
    assert "he cambiado tu cita" in changed.text.lower()
    assert STORE.appointments[first["id"]]["start_at"] == original_first_start
    assert STORE.appointments[second["id"]]["start_at"] != original_second_start


def test_voice_lists_multiple_future_appointments_for_cancel_and_understands_first():
    call_sid = "CA-voice-cancel-multiple"
    reset_state(call_sid)
    first = create_voice_appointment(call_sid, "sesion de fisioterapia", future_start(7))
    second = create_voice_appointment(call_sid, "valoracion inicial", future_start(8))

    listed = post_voice(call_sid, SpeechResult="quiero cancelar mi cita")
    assert "varias citas asociadas a este teléfono" in listed.text.lower()
    selected = post_voice(call_sid, SpeechResult="la primera")
    cancelled = post_voice(call_sid, SpeechResult="sí")

    assert "quieres cancelar la cita" in selected.text.lower()
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
