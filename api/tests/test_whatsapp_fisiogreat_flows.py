from test_demo_flows import post_whatsapp, reset_state
from app.config.settings import settings
from app.services.supabase_repo import STORE
from app.utils.mini_context import CTX


def test_whatsapp_fisiogreat_create_flow():
    user = "+34600000003"
    reset_state(user)

    post_whatsapp(user, "quiero una cita")
    post_whatsapp(user, "valoracion inicial")
    post_whatsapp(user, "jueves")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" in response.text.lower()
    assert len(STORE.appointments) == 1


def test_whatsapp_second_option_confirms_only_after_calendar_ok(monkeypatch):
    user = "+34600000004"
    reset_state(user)
    calls = []

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])

    def fake_create_event(**kwargs):
        calls.append(("calendar", kwargs["start_at"]))
        assert not STORE.appointments
        return "wa-real-event-2"

    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", fake_create_event)

    post_whatsapp(user, "quiero cita")
    assert "día" in post_whatsapp(user, "fisioterapia").text.lower()
    assert "te puedo ofrecer" in post_whatsapp(user, "mañana").text.lower()
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" in response.text.lower()
    assert calls and calls[0][0] == "calendar"
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["status"] == "confirmed"
    assert appointment["calendar_event_id"] == "wa-real-event-2"
    assert appointment["metadata"]["slot_label"].startswith("2.") is False


def test_whatsapp_accepts_natural_second_option_text(monkeypatch):
    user = "+34600000007"
    reset_state(user)

    monkeypatch.setattr(settings, "USE_REAL_CALENDAR", True)
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_ID", "calendar-real")
    monkeypatch.setattr("app.services.booking_service.calendar_service.free_busy", lambda *_args: [])
    monkeypatch.setattr("app.services.booking_service.calendar_service.create_event", lambda **_kwargs: "event-option-2")

    post_whatsapp(user, "quiero pedir una cita")
    post_whatsapp(user, "sesion de fisio")
    post_whatsapp(user, "mañana mismo")
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
    post_whatsapp(user, "mañana")
    offered = CTX.get(user)["offered_slots"]
    response = post_whatsapp(user, "me quedo con la 3")

    assert "te dejo apuntada" in response.text.lower()
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["calendar_event_id"] == "event-option-3"
    assert appointment["metadata"]["slot_label"] == offered[2]


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
    post_whatsapp(user, "mañana")
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
    response = post_whatsapp(user, "mañana")

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
    post_whatsapp(user, "mañana")
    failed = post_whatsapp(user, "2")
    retried = post_whatsapp(user, "3")

    assert "te dejo apuntada" not in failed.text.lower()
    assert "te dejo apuntada" in retried.text.lower()
    assert len(attempts) == 2
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["calendar_event_id"] == "event-after-retry"


def test_whatsapp_services_and_hours_faq_are_distinct():
    user = "+34600000006"
    reset_state(user)

    services = post_whatsapp(user, "qué servicios tenéis").text
    hours = post_whatsapp(user, "qué horario tenéis").text

    assert "valoración inicial, sesión de fisioterapia y consulta de seguimiento" in services
    assert "pedir, cambiar o cancelar una cita" in services
    assert "nuestro horario" not in services.lower()
    assert "nuestro horario" in hours.lower()


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
    slots = post_whatsapp(user, "mañana mismo")

    assert "día" in service.text.lower()
    assert "te puedo ofrecer" in slots.text.lower()
    assert CTX.get_stage(user) == "offering_slots"


def test_whatsapp_reset_clears_state_and_slots():
    user = "+34600000014"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "mañana")
    assert CTX.get(user)["offered_slots"]

    response = post_whatsapp(user, "reiniciar")

    assert "empezamos de nuevo" in response.text.lower()
    assert CTX.get_stage(user) == "idle"
    assert CTX.get(user)["offered_slots"] == []


def test_whatsapp_reset_prevents_old_slot_selection():
    user = "+34600000015"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "mañana")
    post_whatsapp(user, "empezar de nuevo")
    response = post_whatsapp(user, "2")

    assert "te dejo apuntada" not in response.text.lower()
    assert not STORE.appointments


def test_whatsapp_reset_does_not_delete_confirmed_appointments():
    user = "+34600000016"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    post_whatsapp(user, "mañana")
    post_whatsapp(user, "1")
    assert len(STORE.appointments) == 1

    post_whatsapp(user, "reset")

    assert len(STORE.appointments) == 1
    assert next(iter(STORE.appointments.values()))["status"] == "confirmed"


def test_whatsapp_cancelar_cita_is_real_cancellation_flow_not_reset():
    user = "+34600000017"
    reset_state(user)

    response = post_whatsapp(user, "cancelar cita")

    assert "para cancelar una cita" in response.text.lower()
    assert CTX.get_stage(user) == "awaiting_cancel_date"


def test_whatsapp_cancelar_inside_pending_booking_cancels_only_flow():
    user = "+34600000018"
    reset_state(user)

    post_whatsapp(user, "quiero cita")
    post_whatsapp(user, "fisioterapia")
    response = post_whatsapp(user, "cancelar")

    assert "cancelo esta gestión" in response.text
    assert CTX.get_stage(user) == "idle"
    assert not STORE.appointments
