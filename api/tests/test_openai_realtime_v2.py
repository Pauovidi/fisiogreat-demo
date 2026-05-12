import asyncio
import datetime as dt

from fastapi.testclient import TestClient

from app.main import app
from app.services import booking_service, supabase_repo
from app.services.calendar_service import CALENDAR_STORE
from app.services.openai_realtime import (
    REALTIME_V2_INSTRUCTIONS,
    RealtimeToolContext,
    book_appointment,
    cancel_appointment,
    emergency_protocol,
    get_available_slots,
    get_services,
    list_future_appointments,
    reschedule_appointment,
    save_contact,
)
from app.services.supabase_repo import STORE


client = TestClient(app)


def reset_state():
    STORE.reset()
    CALENDAR_STORE.reset()


def future_start(days: int = 7, hour: int = 10) -> dt.datetime:
    return (dt.datetime.now() + dt.timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def create_voice_appointment(session_id: str, start: dt.datetime, service: str = "valoracion inicial"):
    result = asyncio.run(
        booking_service.confirm_slot(
            channel="voice",
            external_user_id=session_id,
            service_type=service,
            start_at=start,
            patient_name="Pau Marco",
            contact_phone="640505050",
            consultation_reason="rodilla" if service == "sesion de fisioterapia" else None,
        )
    )
    assert result.ok
    return result.appointment


def test_openai_realtime_v2_health_endpoint_ok():
    response = client.get("/__health/openai-realtime-v2")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["shadow_mode"] is True
    assert body["write_enabled"] is False


def test_realtime_instructions_contain_valid_services():
    text = REALTIME_V2_INSTRUCTIONS.lower()

    assert "valoración inicial" in text
    assert "sesión de fisioterapia" in text
    assert "consulta de seguimiento" in text
    assert "no diagnostiques" in text
    assert "112" in text


def test_get_services_returns_closed_catalog():
    result = get_services()

    assert result["ok"] is True
    assert result["closed_catalog"] is True
    assert result["services"] == [
        "valoración inicial",
        "sesión de fisioterapia",
        "consulta de seguimiento",
    ]


def test_out_of_catalog_tool_does_not_schedule():
    reset_state()
    context = RealtimeToolContext(session_id="rt-out-of-catalog")

    result = get_available_slots(context, service="masaje deportivo", date_text="jueves", today="2026-05-11")

    assert not result["ok"]
    assert result["reason"] == "out_of_catalog"
    assert result["booking_blocked"] is True
    assert not STORE.appointments


def test_emergency_protocol_blocks_booking():
    reset_state()
    context = RealtimeToolContext(session_id="rt-emergency")

    emergency = emergency_protocol(context, "me duele el pecho y no puedo respirar")
    result = asyncio.run(
        book_appointment(
            context,
            service="valoracion inicial",
            patient_name="Pau Marco",
            start_at=future_start().isoformat(),
            contact_phone="640505050",
        )
    )

    assert emergency["booking_blocked"] is True
    assert "112" in result["message"]
    assert result["reason"] == "emergency_blocked"
    assert not STORE.appointments


def test_get_available_slots_respects_explicit_jueves():
    reset_state()
    context = RealtimeToolContext(session_id="rt-jueves")

    result = get_available_slots(context, service="valoracion inicial", date_text="jueves", today="2026-05-11")

    assert result["ok"] is True
    assert result["explicit_weekday"] == "jueves"
    assert result["slots"]
    assert all(slot["weekday"] == "jueves" for slot in result["slots"])


def test_jueves_never_generates_tuesday_or_wednesday():
    reset_state()
    context = RealtimeToolContext(session_id="rt-jueves-no-wrong-day")

    result = get_available_slots(context, service="sesion de fisioterapia", date_text="el jueves por la manana", today="2026-05-11")

    weekdays = {slot["weekday"] for slot in result["slots"]}
    assert "martes" not in weekdays
    assert "miercoles" not in weekdays
    assert weekdays == {"jueves"}


def test_book_appointment_shadow_does_not_write_supabase_or_calendar():
    reset_state()
    context = RealtimeToolContext(session_id="rt-shadow", write_enabled=False)

    result = asyncio.run(
        book_appointment(
            context,
            service="valoracion inicial",
            patient_name="Pau Marco",
            start_at=future_start().isoformat(),
            contact_phone="640505050",
        )
    )

    assert result["ok"] is True
    assert result["shadow"] is True
    assert not STORE.appointments
    assert not CALENDAR_STORE.events


def test_book_appointment_write_enabled_creates_booking_with_mock_backends():
    reset_state()
    context = RealtimeToolContext(session_id="rt-write", write_enabled=True)

    result = asyncio.run(
        book_appointment(
            context,
            service="sesion de fisioterapia",
            patient_name="Pau Marco",
            start_at=future_start().isoformat(),
            consultation_reason="me duele la rodilla",
            contact_email="pau@example.com",
        )
    )

    assert result["ok"] is True
    assert result["shadow"] is False
    assert len(STORE.appointments) == 1
    appointment = next(iter(STORE.appointments.values()))
    assert appointment["metadata"]["transport"] == "openai_realtime_v2"
    assert appointment["metadata"]["contact_email"] == "pau@example.com"
    assert appointment["calendar_event_id"] in CALENDAR_STORE.events


def test_realtime_v2_requires_phone_or_email_before_confirming():
    reset_state()
    context = RealtimeToolContext(session_id="rt-missing-contact", write_enabled=False)

    result = asyncio.run(
        book_appointment(
            context,
            service="valoracion inicial",
            patient_name="Pau Marco",
            start_at=future_start().isoformat(),
        )
    )

    assert not result["ok"]
    assert result["reason"] == "missing_contact"
    assert not STORE.appointments


def test_spoken_email_is_normalized():
    context = RealtimeToolContext(session_id="rt-email")

    result = save_contact(context, "mi email es marcos arroba ejemplo punto com")

    assert result["ok"] is True
    assert result["contact_email"] == "marcos@ejemplo.com"
    assert context.contact_email == "marcos@ejemplo.com"


def test_cancel_with_multiple_appointments_lists_options():
    reset_state()
    create_voice_appointment("rt-multiple", future_start(7), "valoracion inicial")
    create_voice_appointment("rt-multiple", future_start(8), "consulta de seguimiento")
    context = RealtimeToolContext(session_id="rt-multiple", write_enabled=True)

    result = asyncio.run(list_future_appointments(context))

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["requires_selection"] is True
    assert "primera" in result["appointments"][0]["label"]
    assert "segunda" in result["appointments"][1]["label"]


def test_reschedule_does_not_duplicate_appointment():
    reset_state()
    appointment = create_voice_appointment("rt-reschedule", future_start(7), "valoracion inicial")
    context = RealtimeToolContext(session_id="rt-reschedule", write_enabled=True)
    new_start = future_start(9, 11)

    result = asyncio.run(
        reschedule_appointment(
            context,
            appointment_id=appointment["id"],
            new_start_at=new_start.isoformat(),
            new_end_at=(new_start + dt.timedelta(minutes=60)).isoformat(),
        )
    )

    assert result["ok"] is True
    assert len(STORE.appointments) == 1
    assert STORE.appointments[appointment["id"]]["start_at"] == new_start.isoformat()


def test_cancel_tool_uses_existing_booking_service_contract():
    reset_state()
    appointment = create_voice_appointment("rt-cancel", future_start(7), "valoracion inicial")
    context = RealtimeToolContext(session_id="rt-cancel", write_enabled=True)

    result = asyncio.run(cancel_appointment(context, appointment_id=appointment["id"]))

    assert result["ok"] is True
    assert STORE.appointments[appointment["id"]]["status"] == "cancelled"


def test_v1_endpoints_remain_registered():
    paths = {route.path for route in app.routes}

    assert "/webhook/voice/conversationrelay" in paths
    assert "/webhook/voice/conversationrelay/ws" in paths
    assert "/webhook/voice/agent" in paths
    assert "/webhook/whatsapp" in paths
