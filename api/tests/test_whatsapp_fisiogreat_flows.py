import asyncio
import datetime as dt

from test_demo_flows import post_whatsapp, reset_state
from app.config.settings import settings
from app.services import supabase_repo
from app.services.booking_service import confirm_slot
from app.services.calendar_service import CALENDAR_STORE
from app.services.supabase_repo import STORE
from app.utils.mini_context import CTX
from app.utils.date_parser import parse_spanish_day


WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


def future_start(days: int = 7, hour: int = 10) -> dt.datetime:
    return (dt.datetime.now() + dt.timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def slot_day_prefix(value: dt.date) -> str:
    return f"{WEEKDAY_LABELS[value.weekday()]} {value.strftime('%d/%m')}"


def create_future_appointment(
    user: str,
    service: str,
    start,
    *,
    patient_name: str = "Pau Marco",
    consultation_reason: str | None = None,
):
    result = asyncio.run(confirm_slot(
        channel="whatsapp",
        external_user_id=user,
        service_type=service,
        start_at=start,
        patient_name=patient_name,
        consultation_reason=consultation_reason,
        contact_phone=user,
    ))
    assert result.ok
    return result.appointment


def test_whatsapp_fisiogreat_create_flow():
    user = "+34600000003"
    reset_state(user)

    post_whatsapp(user, "quiero una cita")
    post_whatsapp(user, "valoracion inicial")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" in response.text.lower()
    assert len(STORE.appointments) == 1


def test_whatsapp_new_booking_asks_patient_name_before_date():
    user = "+34600000020"
    reset_state(user)

    post_whatsapp(user, "quiero pedir una cita")
    service = post_whatsapp(user, "sesión de fisio")
    reason = post_whatsapp(user, "me duele la rodilla")
    patient = next(iter(STORE.patients.values()))

    assert "motivo" in service.text.lower()
    assert "nombre" in reason.text.lower()
    assert CTX.get(user)["consultation_reason"] == "me duele la rodilla"
    assert patient["phone"] == user
    assert patient["name"] is None
    name = post_whatsapp(user, "Pau Marco")
    assert "gracias, pau" in name.text.lower()
    assert "qué día" in name.text.lower()
    assert CTX.get(user)["patient_name"] == "Pau Marco"
    assert patient["name"] == "Pau Marco"


def test_whatsapp_me_llamo_updates_patient_name():
    user = "+34600000021"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    response = post_whatsapp(user, "me llamo Pau")

    assert "gracias, pau" in response.text.lower()
    patient = next(iter(STORE.patients.values()))
    assert patient["name"] == "Pau"


def test_whatsapp_existing_named_patient_skips_name_prompt():
    user = "+34600000022"
    reset_state(user)
    asyncio.run(
        supabase_repo.create_patient(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone=user,
            name="Ana Marco",
        )
    )

    post_whatsapp(user, "quiero cita")
    response = post_whatsapp(user, "fisioterapia")
    after_reason = post_whatsapp(user, "sobrecarga lumbar")

    assert "motivo" in response.text.lower()
    assert "qué día" in after_reason.text.lower()
    assert "nombre" not in after_reason.text.lower()
    assert CTX.get(user)["patient_name"] == "Ana Marco"


def test_whatsapp_profile_name_does_not_skip_patient_name_prompt():
    user = "+34600000023"
    reset_state(user)

    post_whatsapp(user, "quiero cita", profile_name="Pau Marco")
    response = post_whatsapp(user, "fisioterapia", profile_name="Pau Marco")
    after_reason = post_whatsapp(user, "contractura cervical", profile_name="Pau Marco")

    assert "motivo" in response.text.lower()
    assert "nombre" in after_reason.text.lower()
    assert not CTX.get(user).get("patient_name")
    patient = next(iter(STORE.patients.values()))
    assert patient["phone"] == user
    assert patient["name"] is None


def test_whatsapp_necesito_sesion_fisio_asks_reason_before_name():
    user = "+34600000029"
    reset_state(user)

    response = post_whatsapp(user, "necesito una sesión de fisio")

    assert "motivo" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_consultation_reason"
    assert CTX.get(user)["service"] == "sesion de fisioterapia"


def test_whatsapp_date_before_patient_name_does_not_offer_slots():
    user = "+34600000030"
    reset_state(user)

    post_whatsapp(user, "quiero sesión de fisio")
    post_whatsapp(user, "me duele la rodilla")
    response = post_whatsapp(user, "el miércoles que viene")

    assert "antes de buscar huecos" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_patient_name"
    assert CTX.get(user)["offered_slots"] == []


def test_whatsapp_slot_number_before_patient_name_does_not_confirm():
    user = "+34600000031"
    reset_state(user)

    post_whatsapp(user, "quiero sesión de fisio")
    post_whatsapp(user, "me duele la rodilla")
    response = post_whatsapp(user, "2")

    assert "antes de confirmar" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_patient_name"
    assert not STORE.appointments


def test_whatsapp_ambiguous_consultation_reason_reprompts():
    user = "+34600000032"
    reset_state(user)

    post_whatsapp(user, "quiero sesión de fisio")
    response = post_whatsapp(user, "no se")

    assert "motivo de la consulta" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_consultation_reason"
    assert CTX.get(user)["consultation_reason"] is None


def test_whatsapp_emergency_during_consultation_reason_cuts_flow():
    user = "+34600000033"
    reset_state(user)

    post_whatsapp(user, "quiero sesión de fisio")
    response = post_whatsapp(user, "dolor torácico")

    assert "112" in response.text
    assert CTX.get_stage(user) == "emergency_detected"
    assert not STORE.appointments


def test_whatsapp_second_option_confirms_only_after_calendar_ok(monkeypatch):
    user = "+34600000004"
    reset_state(user)
    calls = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        calls.append(("calendar", kwargs))
        assert not STORE.appointments
        return "wa-real-event-2"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    post_whatsapp(user, "quiero cita")
    assert "motivo" in post_whatsapp(user, "fisioterapia").text.lower()
    assert "nombre" in post_whatsapp(user, "me duele la rodilla").text.lower()
    assert "día" in post_whatsapp(user, "Pau Marco").text.lower()
    assert "te puedo ofrecer" in post_whatsapp(user, "jueves").text.lower()
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" in response.text.lower()
    assert calls and calls[0][0] == "calendar"
    assert "Pau Marco" in calls[0][1]["summary"]
    assert "Paciente: Pau Marco" in calls[0][1]["description"]
    assert "Motivo de consulta: me duele la rodilla" in calls[0][1]["description"]
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["status"] == "confirmed"
    assert appointment["calendar_event_id"] == "wa-real-event-2"
    assert appointment["metadata"]["slot_label"].startswith("2.") is False
    assert appointment["metadata"]["patient_name"] == "Pau Marco"
    assert appointment["metadata"]["consultation_reason"] == "me duele la rodilla"
    assert appointment["metadata"]["contact_phone"] == user
    assert appointment["metadata"]["contact_channel_preference"] == "whatsapp"
    assert "Contacto para recordatorio: WhatsApp +34600000004" in calls[0][1]["description"]
    assert "perfecto, pau" in response.text.lower()
    assert "sesion de fisioterapia" in response.text.lower()
    assert "confirmación y el recordatorio por este whatsapp" in response.text.lower()


def test_whatsapp_optional_email_before_confirmation_is_saved_without_blocking(monkeypatch):
    user = "+34600000024"
    reset_state(user)
    calls = []

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        calls.append(kwargs)
        return "event-with-optional-wa-email"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    post_whatsapp(user, "quiero sesión de fisio")
    post_whatsapp(user, "contractura cervical")
    post_whatsapp(user, "Marcos Castellano")
    email = post_whatsapp(user, "mi email es marcos@example.com")
    offered = post_whatsapp(user, "jueves")
    confirmed = post_whatsapp(user, "1")

    assert "lo dejo anotado también con ese email" in email.text.lower()
    assert "qué día" in email.text.lower()
    assert "te puedo ofrecer" in offered.text.lower()
    assert "te dejo apuntada" in confirmed.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["patient_name"] == "Marcos Castellano"
    assert appointment["metadata"]["contact_phone"] == user
    assert appointment["metadata"]["contact_email"] == "marcos@example.com"
    assert appointment["metadata"]["contact_channel_preference"] == "whatsapp"
    assert "WhatsApp +34600000024" in calls[0]["description"]
    assert "email marcos@example.com" in calls[0]["description"]


def test_whatsapp_optional_email_after_confirmation_updates_appointment():
    user = "+34600000025"
    reset_state(user)
    appointment = create_future_appointment(
        user,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=7),
    )

    response = post_whatsapp(user, "mi email es marcos@example.com")

    assert "lo dejo anotado también con ese email" in response.text.lower()
    metadata = STORE.appointments[appointment["id"]]["metadata"]
    assert metadata["contact_email"] == "marcos@example.com"
    assert metadata["contact_channel_preference"] == "whatsapp"


def test_whatsapp_optional_spoken_email_after_confirmation_updates_appointment():
    user = "+34600000038"
    reset_state(user)
    appointment = create_future_appointment(
        user,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=7),
    )

    response = post_whatsapp(user, "antonio arroba gmail punto com")

    assert "lo dejo anotado también con ese email" in response.text.lower()
    metadata = STORE.appointments[appointment["id"]]["metadata"]
    assert metadata["contact_email"] == "antonio@gmail.com"


def test_whatsapp_accepts_natural_second_option_text(monkeypatch):
    user = "+34600000007"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "event-option-2")

    post_whatsapp(user, "quiero pedir una cita")
    post_whatsapp(user, "sesion de fisio")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    offered = CTX.get(user)["offered_slots"]
    response = post_whatsapp(user, "Vale, pues 2")

    assert "te dejo apuntada" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["calendar_event_id"] == "event-option-2"
    assert appointment["metadata"]["slot_label"] == offered[1]


def test_whatsapp_accepts_natural_third_option_text(monkeypatch):
    user = "+34600000008"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "event-option-3")

    post_whatsapp(user, "quiero reservar")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    offered = CTX.get(user)["offered_slots"]
    response = post_whatsapp(user, "me quedo con la 3")

    assert "te dejo apuntada" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["calendar_event_id"] == "event-option-3"
    assert appointment["metadata"]["slot_label"] == offered[2]


def test_whatsapp_slot_offer_filters_internal_confirmed_appointment(monkeypatch):
    user = "+34600000077"
    reset_state(user)
    target_date = parse_spanish_day("jueves")
    blocked_start = dt.datetime.combine(target_date, dt.time(10, 0))
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args, **_kwargs: [])
    asyncio.run(supabase_repo.create_appointment(
        clinic_id=settings.DEMO_CLINIC_ID,
        patient_id="patient-busy-whatsapp",
        service_type="valoracion inicial",
        start_at=blocked_start.isoformat(),
        end_at=(blocked_start + dt.timedelta(hours=1)).isoformat(),
        status="confirmed",
        calendar_event_id="internal-whatsapp-busy",
        channel="voice",
        external_user_id="CA-internal-whatsapp",
        metadata={"patient_name": "Busy Patient"},
    ))

    post_whatsapp(user, "quiero pedir cita")
    post_whatsapp(user, "valoracion inicial")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    offered = CTX.get(user)["offered_slots"]

    assert offered
    assert f"{slot_day_prefix(target_date)} a las 10:00" not in offered
    assert offered[0].endswith("a las 11:00")


def test_whatsapp_second_option_does_not_confirm_when_calendar_fails(monkeypatch):
    user = "+34600000005"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fail_create_event(**_kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fail_create_event)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" not in response.text.lower()
    assert not [item for item in STORE.appointments.values() if item.get("status") == "confirmed"]
    assert all(lock["status"] == "released" for lock in STORE.locks.values())
    assert CTX.get_stage(user) == "offering_slots"


def test_whatsapp_real_calendar_freebusy_failure_does_not_offer_dummy_slots(monkeypatch):
    user = "+34600000019"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")

    def fail_free_busy(*_args):
        raise RuntimeError("freebusy unavailable")

    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", fail_free_busy)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    response = post_whatsapp(user, "jueves")

    assert "no veo huecos" in response.text.lower()
    assert "lunes" not in response.text.lower()
    assert CTX.get(user)["offered_slots"] == []


def test_whatsapp_can_retry_third_option_after_calendar_failure(monkeypatch):
    user = "+34600000009"
    reset_state(user)
    attempts = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def flaky_create_event(**kwargs):
        attempts.append(kwargs["start_at"])
        if len(attempts) == 1:
            raise RuntimeError("calendar unavailable")
        return "event-after-retry"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", flaky_create_event)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    failed = post_whatsapp(user, "2")
    retried = post_whatsapp(user, "3")

    assert "te dejo apuntada" not in failed.text.lower()
    assert "te dejo apuntada" in retried.text.lower()
    assert len(attempts) == 2
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["calendar_event_id"] == "event-after-retry"


def test_whatsapp_thanks_after_confirmed_booking_is_human(monkeypatch):
    user = "+34600000024"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "event-thanks")

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "2")
    response = post_whatsapp(user, "gracias")

    assert "gracias a ti, pau" in response.text.lower()
    assert "te esperamos el" in response.text.lower()
    assert "pedir, cambiar o cancelar" not in response.text.lower()


def test_whatsapp_thanks_without_recent_booking_is_kind_not_menu():
    user = "+34600000025"
    reset_state(user)

    response = post_whatsapp(user, "gracias")

    assert "gracias a ti" in response.text.lower()
    assert "aquí estoy" in response.text.lower()
    assert "qué necesitas" not in response.text.lower()


def test_whatsapp_farewell_after_confirmed_booking_mentions_slot(monkeypatch):
    user = "+34600000026"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "event-bye")

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "2")
    response = post_whatsapp(user, "adiós")

    assert "hasta luego, pau" in response.text.lower()
    assert "nos vemos el" in response.text.lower()


def test_whatsapp_farewell_without_recent_booking_is_generic():
    user = "+34600000027"
    reset_state(user)

    response = post_whatsapp(user, "hasta luego")

    assert "hasta luego" in response.text.lower()
    assert "aquí estoy si necesitas ayuda" in response.text.lower()


def test_whatsapp_services_and_hours_faq_are_distinct():
    user = "+34600000006"
    reset_state(user)

    services = post_whatsapp(user, "qué servicios tenéis").text
    hours = post_whatsapp(user, "qué horario tenéis").text

    assert "valoración inicial, sesión de fisioterapia y consulta de seguimiento" in services
    assert "pedir, cambiar o cancelar una cita" in services
    assert "nuestro horario" not in services.lower()
    assert "nuestro horario" in hours.lower()


def test_whatsapp_valid_services_are_closed_catalog_and_ask_name_or_reason():
    cases = [
        ("quiero valoración inicial", "valoracion inicial"),
        ("quiero una primera visita", "valoracion inicial"),
        ("quiero seguimiento", "consulta de seguimiento"),
        ("quiero una revisión", "consulta de seguimiento"),
        ("quiero sesión de fisio", "sesion de fisioterapia"),
    ]
    for index, (message, expected_service) in enumerate(cases, start=1):
        user = f"+346000001{index:02d}"
        reset_state(user)

        response = post_whatsapp(user, message)

        if expected_service == "sesion de fisioterapia":
            assert "motivo" in response.text.lower()
            assert CTX.get_stage(user) == "awaiting_consultation_reason"
        else:
            assert "nombre" in response.text.lower()
            assert CTX.get_stage(user) == "awaiting_patient_name"
        assert CTX.get(user)["service"] == expected_service


def test_whatsapp_unsupported_services_do_not_start_booking():
    cases = [
        ("quiero pilates", "En esta demo no puedo reservar pilates."),
        ("quiero masaje", "En esta demo no puedo reservar masaje."),
        ("quiero osteopatía", "En esta demo no puedo reservar osteopatia."),
        ("quiero nutrición", "En esta demo no puedo reservar nutricion."),
        ("quiero podología", "En esta demo no puedo reservar podologia."),
        ("quiero suelo pélvico", "En esta demo no puedo reservar suelo pelvico."),
        ("quiero readaptación", "En esta demo no puedo reservar readaptacion."),
        ("quiero entrenamiento", "En esta demo no puedo reservar entrenamiento."),
    ]
    for index, (message, expected_intro) in enumerate(cases, start=1):
        user = f"+346000002{index:02d}"
        reset_state(user)

        response = post_whatsapp(user, message)

        assert expected_intro in response.text
        assert "valoración inicial, sesión de fisioterapia y consulta de seguimiento" in response.text
        assert "derivarte a una persona" in response.text.lower()
        assert CTX.get_stage(user) == "idle"
        assert not STORE.appointments


def test_whatsapp_greeting_is_neutral_and_does_not_start_booking():
    user = "+34600000010"
    reset_state(user)

    response = post_whatsapp(user, "Hola")

    assert "pedir, cambiar o cancelar una cita" in response.text
    assert "servicios y horarios" in response.text
    assert "qué tipo de sesión" not in response.text.lower()
    assert CTX.get_stage(user) == "idle"


def test_whatsapp_services_faq_during_greeting_does_not_force_booking():
    user = "+34600000011"
    reset_state(user)

    post_whatsapp(user, "Hola")
    response = post_whatsapp(user, "No lo se ¿qué servicios tenéis?")

    assert "valoración inicial, sesión de fisioterapia y consulta de seguimiento" in response.text
    assert "qué tipo de cita necesitas" not in response.text.lower()
    assert CTX.get_stage(user) == "idle"


def test_whatsapp_booking_request_starts_service_step():
    user = "+34600000012"
    reset_state(user)

    response = post_whatsapp(user, "quiero pedir una cita")

    assert "cita" in response.text.lower() or "reservar" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_service"


def test_whatsapp_service_and_day_inside_booking_flow():
    user = "+34600000013"
    reset_state(user)

    post_whatsapp(user, "quiero reservar")
    service = post_whatsapp(user, "sesion de fisio")
    reason = post_whatsapp(user, "me duele la rodilla")
    name = post_whatsapp(user, "Pau Marco")
    slots = post_whatsapp(user, "jueves")

    assert "motivo" in service.text.lower()
    assert "nombre" in reason.text.lower()
    assert "día" in name.text.lower()
    assert "te puedo ofrecer" in slots.text.lower()
    assert CTX.get_stage(user) == "offering_slots"


def test_whatsapp_reset_clears_state_and_slots():
    user = "+34600000014"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    assert CTX.get(user)["offered_slots"]

    response = post_whatsapp(user, "reiniciar")

    assert "empezamos de nuevo" in response.text.lower()
    assert CTX.get_stage(user) == "idle"
    assert CTX.get(user)["offered_slots"] == []
    assert CTX.get(user)["consultation_reason"] is None
    assert CTX.get(user)["pending_reschedule_appointments"] == []
    assert CTX.get(user)["pending_cancel_appointments"] == []


def test_whatsapp_reiniciar_keeps_saved_patient_name():
    user = "+34600000034"
    reset_state(user)

    post_whatsapp(user, "quiero valoración inicial")
    post_whatsapp(user, "Pau Marco")
    patient = next(iter(STORE.patients.values()))
    assert patient["name"] == "Pau Marco"

    response = post_whatsapp(user, "reiniciar")

    assert "empezamos de nuevo" in response.text.lower()
    assert patient["name"] == "Pau Marco"
    assert CTX.get_stage(user) == "idle"


def test_whatsapp_olvida_mi_nombre_clears_patient_name_only():
    user = "+34600000035"
    reset_state(user)

    post_whatsapp(user, "quiero valoración inicial")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "1")
    appointment = next(iter(STORE.appointments.values()))
    calendar_event_id = appointment["calendar_event_id"]
    assert calendar_event_id in CALENDAR_STORE.events
    patient = next(iter(STORE.patients.values()))
    assert patient["name"] == "Pau Marco"

    response = post_whatsapp(user, "olvida mi nombre")

    assert "he limpiado el nombre guardado" in response.text.lower()
    assert patient["name"] is None
    assert len(STORE.appointments) == 1
    assert appointment["calendar_event_id"] == calendar_event_id
    assert calendar_event_id in CALENDAR_STORE.events


def test_whatsapp_after_olvida_mi_nombre_asks_name_again():
    user = "+34600000036"
    reset_state(user)

    post_whatsapp(user, "quiero valoración inicial")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "olvida mi nombre")
    response = post_whatsapp(user, "quiero valoración inicial")

    assert "nombre" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_patient_name"


def test_whatsapp_demo_name_clear_command_variants():
    commands = [
        "borrar mi nombre",
        "borrar mis datos",
        "reiniciar demo",
        "reset demo",
        "resetear demo",
        "demo reset",
    ]
    for index, command in enumerate(commands, start=1):
        user = f"+3460000004{index}"
        reset_state(user)
        asyncio.run(
            supabase_repo.create_patient(
                clinic_id=settings.DEMO_CLINIC_ID,
                phone=user,
                name="Pau Marco",
            )
        )

        response = post_whatsapp(user, command)
        patient = next(item for item in STORE.patients.values() if item["phone"] == user)

        assert response.text.count("he limpiado el nombre guardado") == 1
        assert patient["name"] is None
        assert not STORE.appointments


def test_whatsapp_reset_demo_clears_name_and_fisio_flow_asks_reason_then_name():
    user = "+34600000047"
    reset_state(user)
    asyncio.run(
        supabase_repo.create_patient(
            clinic_id=settings.DEMO_CLINIC_ID,
            phone=user,
            name="Pau Marco",
        )
    )

    response = post_whatsapp(user, "reset demo")
    patient = next(item for item in STORE.patients.values() if item["phone"] == user)
    service = post_whatsapp(user, "quiero sesión de fisio")
    assert CTX.get_stage(user) == "awaiting_consultation_reason"
    reason = post_whatsapp(user, "me duele la rodilla")

    assert "he limpiado el nombre guardado" in response.text.lower()
    assert patient["name"] is None
    assert "motivo" in service.text.lower()
    assert "nombre" in reason.text.lower()
    assert CTX.get_stage(user) == "awaiting_patient_name"


def test_whatsapp_reset_prevents_old_slot_selection():
    user = "+34600000015"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "empezar de nuevo")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" not in response.text.lower()
    assert not STORE.appointments


def test_whatsapp_reset_does_not_delete_confirmed_appointments():
    user = "+34600000016"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "1")
    assert len(STORE.appointments) == 1

    post_whatsapp(user, "reset")

    assert len(STORE.appointments) == 1
    assert next(iter(STORE.appointments.values()))["status"] == "confirmed"


def test_whatsapp_reset_after_confirmed_booking_does_not_invent_recent_slot_reply():
    user = "+34600000028"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "me duele la rodilla")
    post_whatsapp(user, "Pau Marco")
    post_whatsapp(user, "jueves")
    post_whatsapp(user, "1")
    post_whatsapp(user, "reiniciar")
    response = post_whatsapp(user, "gracias")

    assert "te esperamos el" not in response.text.lower()
    assert "si necesitas pedir, cambiar o cancelar" in response.text.lower()


def test_whatsapp_cancelar_cita_is_real_cancellation_flow_not_reset():
    user = "+34600000017"
    reset_state(user)

    response = post_whatsapp(user, "cancelar cita")

    assert "no encuentro citas futuras" in response.text.lower()
    assert CTX.get_stage(user) == "idle"


def test_whatsapp_cancelar_inside_pending_booking_cancels_only_flow():
    user = "+34600000018"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    response = post_whatsapp(user, "cancelar")

    assert "cancelo esta gestión" in response.text
    assert CTX.get_stage(user) == "idle"
    assert not STORE.appointments


def test_whatsapp_reschedule_zero_future_appointments():
    user = "+34600000050"
    reset_state(user)

    response = post_whatsapp(user, "quiero cambiar mi cita")

    assert "no encuentro citas futuras" in response.text.lower()
    assert "pedir una nueva cita" in response.text.lower()
    assert CTX.get_stage(user) == "idle"


def test_whatsapp_reschedule_one_future_appointment_requires_confirmation_then_offers_slots():
    user = "+34600000051"
    reset_state(user)
    create_future_appointment(user, "valoracion inicial", future_start(7))

    found = post_whatsapp(user, "modificar cita")
    assert "he encontrado tu cita" in found.text.lower()
    assert "quieres cambiar" in found.text.lower()
    assert CTX.get_stage(user) == "awaiting_reschedule_confirmation"

    ask_day = post_whatsapp(user, "sí")
    assert "nuevo día" in ask_day.text.lower()
    assert CTX.get_stage(user) == "awaiting_reschedule_date"

    offered = post_whatsapp(user, "viernes")
    assert "te puedo ofrecer" in offered.text.lower()
    assert CTX.get_stage(user) == "offering_reschedule_slots"


def test_whatsapp_reschedule_multiple_lists_and_selection_updates_only_selected():
    user = "+34600000052"
    reset_state(user)
    first = create_future_appointment(user, "sesion de fisioterapia", future_start(7, 10), patient_name="Marcos Castellano", consultation_reason="rodilla")
    second = create_future_appointment(user, "sesion de fisioterapia", future_start(7, 11), patient_name="Antonio Ruiz", consultation_reason="espalda")
    third = create_future_appointment(user, "valoracion inicial", future_start(8, 10), patient_name="Pau Marco")
    original_first_start = first["start_at"]
    original_second_start = second["start_at"]
    original_event_id = second["calendar_event_id"]

    listed = post_whatsapp(user, "quiero cambiar mi cita")
    assert "asociadas a este whatsapp" in listed.text.lower()
    assert "1. marcos castellano" in listed.text.lower()
    assert "2. antonio ruiz" in listed.text.lower()
    assert "3. pau marco" in listed.text.lower()
    assert "sesión de fisioterapia" in listed.text.lower()
    assert "valoración inicial" in listed.text.lower()
    assert CTX.get_stage(user) == "awaiting_reschedule_selection"

    selected = post_whatsapp(user, "quiero cambiar la 2")
    assert "nuevo día" in selected.text.lower()
    assert CTX.get(user)["selected_appointment_id"] == second["id"]

    post_whatsapp(user, "viernes")
    changed = post_whatsapp(user, "1")

    assert "he cambiado tu cita" in changed.text.lower()
    assert len(STORE.appointments) == 3
    assert STORE.appointments[first["id"]]["start_at"] == original_first_start
    assert STORE.appointments[second["id"]]["start_at"] != original_second_start
    assert STORE.appointments[second["id"]]["calendar_event_id"] == original_event_id
    assert STORE.appointments[second["id"]]["metadata"]["consultation_reason"] == "espalda"
    assert STORE.appointments[third["id"]]["status"] == "confirmed"


def test_whatsapp_reschedule_selects_unique_service_naturally():
    user = "+34600000053"
    reset_state(user)
    create_future_appointment(user, "sesion de fisioterapia", future_start(7), consultation_reason="rodilla")
    valuation = create_future_appointment(user, "valoracion inicial", future_start(8))

    post_whatsapp(user, "reprogramar cita")
    response = post_whatsapp(user, "la valoración")

    assert "nuevo día" in response.text.lower()
    assert CTX.get(user)["selected_appointment_id"] == valuation["id"]


def test_whatsapp_reschedule_calendar_failure_keeps_original():
    user = "+34600000054"
    reset_state(user)
    appointment = create_future_appointment(user, "valoracion inicial", future_start(7))
    original_start = appointment["start_at"]
    original_event = appointment["calendar_event_id"]

    post_whatsapp(user, "cambiar hora")
    post_whatsapp(user, "sí")
    post_whatsapp(user, "viernes")

    from app.services import booking_service

    previous_update = booking_service.calendar_service.update_event
    booking_service.calendar_service.update_event = lambda *_args, **_kwargs: False
    try:
        response = post_whatsapp(user, "1")
    finally:
        booking_service.calendar_service.update_event = previous_update

    assert "tu cita original sigue igual" in response.text.lower()
    assert STORE.appointments[appointment["id"]]["start_at"] == original_start
    assert STORE.appointments[appointment["id"]]["calendar_event_id"] == original_event
    assert STORE.appointments[appointment["id"]]["status"] == "confirmed"


def test_whatsapp_reschedule_same_day_other_time_uses_original_date_and_updates_existing():
    user = "+34600000059"
    reset_state(user)
    appointment_start = future_start(7)
    appointment = create_future_appointment(user, "valoracion inicial", appointment_start)
    original_start = appointment["start_at"]
    original_event_id = appointment["calendar_event_id"]
    expected_date = appointment_start.date()
    expected_prefix = slot_day_prefix(expected_date)

    post_whatsapp(user, "quiero cambiar mi cita")
    post_whatsapp(user, "sí")
    offered = post_whatsapp(user, "el mismo día a otra hora")
    offered_slots = CTX.get(user)["offered_slots"]

    assert "ese mismo día" in offered.text.lower()
    assert CTX.get(user)["date_pref"] == expected_date
    assert offered_slots
    assert all(expected_prefix in slot for slot in offered_slots)
    assert f"{expected_prefix} a las {appointment_start.strftime('%H:%M')}" not in offered_slots

    changed = post_whatsapp(user, "1")

    assert "he cambiado tu cita" in changed.text.lower()
    assert len(STORE.appointments) == 1
    assert STORE.appointments[appointment["id"]]["start_at"] != original_start
    assert STORE.appointments[appointment["id"]]["calendar_event_id"] == original_event_id
    assert original_event_id in CALENDAR_STORE.events


def test_whatsapp_reschedule_thursday_parses_target_date():
    user = "+34600000060"
    reset_state(user)
    expected_date = parse_spanish_day("jueves")
    expected_prefix = slot_day_prefix(expected_date)
    create_future_appointment(user, "valoracion inicial", future_start(7))

    post_whatsapp(user, "quiero cambiar mi cita")
    post_whatsapp(user, "sí")
    offered = post_whatsapp(user, "jueves")

    assert "te puedo ofrecer" in offered.text.lower()
    assert CTX.get(user)["date_pref"] == expected_date
    assert all(expected_prefix in slot for slot in CTX.get(user)["offered_slots"])


def test_whatsapp_reschedule_unparsed_day_asks_clarification_not_no_slots():
    user = "+34600000061"
    reset_state(user)
    create_future_appointment(user, "valoracion inicial", future_start(7))

    post_whatsapp(user, "quiero cambiar mi cita")
    post_whatsapp(user, "sí")
    response = post_whatsapp(user, "cuando puedas")

    assert "dime el nuevo día" in response.text.lower()
    assert "no veo huecos" not in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_reschedule_date"


def test_whatsapp_reset_clears_pending_reschedule_selection():
    user = "+34600000062"
    reset_state(user)
    create_future_appointment(user, "valoracion inicial", future_start(7))
    create_future_appointment(user, "consulta de seguimiento", future_start(8))

    post_whatsapp(user, "quiero cambiar mi cita")
    assert CTX.get(user)["pending_reschedule_appointments"]

    post_whatsapp(user, "reiniciar")

    assert CTX.get_stage(user) == "idle"
    assert CTX.get(user)["pending_reschedule_appointments"] == []
    assert len(STORE.appointments) == 2


def test_whatsapp_cancel_zero_one_multiple_and_calendar_failure():
    user_zero = "+34600000055"
    reset_state(user_zero)
    zero = post_whatsapp(user_zero, "quiero cancelar mi cita")
    assert "no encuentro citas futuras" in zero.text.lower()

    user_one = "+34600000056"
    reset_state(user_one)
    only = create_future_appointment(user_one, "valoracion inicial", future_start(7))
    found = post_whatsapp(user_one, "cancelar cita")
    assert "quieres cancelarla" in found.text.lower()
    cancelled = post_whatsapp(user_one, "sí")
    assert "he cancelado tu cita" in cancelled.text.lower()
    assert STORE.appointments[only["id"]]["status"] == "cancelled"
    assert CALENDAR_STORE.events[only["calendar_event_id"]].status == "cancelled"

    user_many = "+34600000057"
    reset_state(user_many)
    first = create_future_appointment(
        user_many,
        "sesion de fisioterapia",
        future_start(7),
        patient_name="Marcos Castellano",
        consultation_reason="rodilla",
    )
    second = create_future_appointment(
        user_many,
        "valoracion inicial",
        future_start(8),
        patient_name="Antonio Ruiz",
    )
    listed = post_whatsapp(user_many, "anular cita")
    assert "asociadas a este whatsapp" in listed.text.lower()
    assert "1. marcos castellano" in listed.text.lower()
    assert "2. antonio ruiz" in listed.text.lower()
    selected = post_whatsapp(user_many, "2")
    assert "quieres cancelar la cita" in selected.text.lower()
    assert "puedo ayudarte a pedir" not in selected.text.lower()
    assert CTX.get(user_many)["selected_appointment_id"] == second["id"]
    cancelled = post_whatsapp(user_many, "sí, cancélala")
    assert "he cancelado tu cita" in cancelled.text.lower()
    assert "dime si quieres cancelar" not in cancelled.text.lower()
    assert STORE.appointments[first["id"]]["status"] == "confirmed"
    assert STORE.appointments[second["id"]]["status"] == "cancelled"

    user_fail = "+34600000058"
    reset_state(user_fail)
    appointment = create_future_appointment(user_fail, "valoracion inicial", future_start(7))
    post_whatsapp(user_fail, "cancelar cita")
    from app.services import booking_service

    previous_delete = booking_service.calendar_service.delete_event
    booking_service.calendar_service.delete_event = lambda *_args, **_kwargs: False
    try:
        failed = post_whatsapp(user_fail, "sí")
    finally:
        booking_service.calendar_service.delete_event = previous_delete

    assert "tu cita sigue igual" in failed.text.lower()
    assert STORE.appointments[appointment["id"]]["status"] == "confirmed"


def test_whatsapp_pending_cancel_accepts_natural_confirmations_and_decline():
    confirmations = ["sí, cancélala", "cancélala", "cancelar", "confirmo", "adelante", "vale", "ok", "correcto"]
    for index, reply in enumerate(confirmations, start=1):
        user = f"+3460000007{index}"
        reset_state(user)
        appointment = create_future_appointment(
            user,
            "valoracion inicial",
            dt.datetime.now() + dt.timedelta(days=index + 2),
        )

        found = post_whatsapp(user, "quiero cancelar mi cita")
        cancelled = post_whatsapp(user, reply)

        assert "quieres cancelarla" in found.text.lower()
        assert "he cancelado tu cita" in cancelled.text.lower()
        assert "dime si quieres cancelar" not in cancelled.text.lower()
        assert STORE.appointments[appointment["id"]]["status"] == "cancelled"
        assert CALENDAR_STORE.events[appointment["calendar_event_id"]].status == "cancelled"

    user = "+34600000089"
    reset_state(user)
    appointment = create_future_appointment(
        user,
        "valoracion inicial",
        dt.datetime.now() + dt.timedelta(days=14),
    )
    post_whatsapp(user, "quiero cancelar mi cita")
    kept = post_whatsapp(user, "no la canceles")

    assert "de acuerdo, mantengo tu cita como estaba" in kept.text.lower()
    assert STORE.appointments[appointment["id"]]["status"] == "confirmed"
    assert CALENDAR_STORE.events[appointment["calendar_event_id"]].status == "confirmed"


def test_whatsapp_multi_cancel_accepts_la_segunda_and_decline():
    user = "+34600000301"
    reset_state(user)
    first = create_future_appointment(user, "sesion de fisioterapia", future_start(7), patient_name="Marcos Castellano")
    second = create_future_appointment(user, "valoracion inicial", future_start(8), patient_name="Antonio Ruiz")

    listed = post_whatsapp(user, "quiero cancelar cita")
    selected = post_whatsapp(user, "la segunda")
    kept = post_whatsapp(user, "no la canceles")

    assert "asociadas a este whatsapp" in listed.text.lower()
    assert "quieres cancelar la cita" in selected.text.lower()
    assert "de acuerdo, mantengo tu cita como estaba" in kept.text.lower()
    assert STORE.appointments[first["id"]]["status"] == "confirmed"
    assert STORE.appointments[second["id"]]["status"] == "confirmed"


def test_whatsapp_multi_cancel_phrase_with_cancel_intent_cancels_selected_directly():
    user = "+34600000302"
    reset_state(user)
    first = create_future_appointment(user, "sesion de fisioterapia", future_start(7), patient_name="Marcos Castellano")
    second = create_future_appointment(user, "valoracion inicial", future_start(8), patient_name="Antonio Ruiz")

    post_whatsapp(user, "quiero cancelar cita")
    cancelled = post_whatsapp(user, "quiero cancelar la segunda")

    assert "he cancelado tu cita" in cancelled.text.lower()
    assert STORE.appointments[first["id"]]["status"] == "confirmed"
    assert STORE.appointments[second["id"]]["status"] == "cancelled"


def test_whatsapp_stale_numeric_selection_does_not_show_generic_menu():
    user = "+34600000303"
    reset_state(user)

    response = post_whatsapp(user, "2")

    assert "ya no tengo activa esa selección" in response.text.lower()
    assert "puedo ayudarte a pedir" not in response.text.lower()


def test_whatsapp_recovers_pending_cancel_selection_if_stage_was_lost():
    user = "+34600000304"
    reset_state(user)
    first = create_future_appointment(user, "sesion de fisioterapia", future_start(7), patient_name="Marcos Castellano")
    second = create_future_appointment(user, "valoracion inicial", future_start(8), patient_name="Antonio Ruiz")

    post_whatsapp(user, "quiero cancelar cita")
    CTX.set_stage(user, "idle")
    selected = post_whatsapp(user, "2")
    cancelled = post_whatsapp(user, "cancélala")

    assert "quieres cancelar la cita" in selected.text.lower()
    assert "he cancelado tu cita" in cancelled.text.lower()
    assert STORE.appointments[first["id"]]["status"] == "confirmed"
    assert STORE.appointments[second["id"]]["status"] == "cancelled"


def test_whatsapp_reset_clears_pending_cancel_selection():
    user = "+34600000305"
    reset_state(user)
    create_future_appointment(user, "valoracion inicial", future_start(7))
    create_future_appointment(user, "consulta de seguimiento", future_start(8))

    post_whatsapp(user, "quiero cancelar cita")
    assert CTX.get(user)["pending_cancel_appointments"]

    post_whatsapp(user, "reiniciar")

    assert CTX.get_stage(user) == "idle"
    assert CTX.get(user)["pending_cancel_appointments"] == []
