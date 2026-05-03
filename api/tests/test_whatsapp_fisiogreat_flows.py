from test_demo_flows import post_whatsapp, reset_state
from app.config.settings import settings
from app.services.supabase_repo import STORE


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


def test_whatsapp_services_and_hours_faq_are_distinct():
    user = "+34600000006"
    reset_state(user)

    services = post_whatsapp(user, "qué servicios tenéis").text
    hours = post_whatsapp(user, "qué horario tenéis").text

    assert "valoración inicial, sesión de fisioterapia y consulta de seguimiento" in services
    assert "pedir, cambiar o cancelar una cita" in services
    assert "nuestro horario" not in services.lower()
    assert "nuestro horario" in hours.lower()
