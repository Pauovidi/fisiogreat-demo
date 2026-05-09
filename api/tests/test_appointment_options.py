from app.utils.appointment_options import (
    format_appointment_option_voice,
    format_appointment_option_whatsapp,
)


def test_appointment_options_show_patient_name_and_voice_time_naturally():
    appointment = {
        "service_type": "sesion de fisioterapia",
        "start_at": "2026-05-11T10:45:00",
        "metadata": {"patient_name": "Marcos Castellano"},
    }

    whatsapp = format_appointment_option_whatsapp(appointment, 1)
    voice = format_appointment_option_voice(appointment, 1).lower()

    assert whatsapp == "1. Marcos Castellano — Sesión de fisioterapia — lunes 11/05 a las 10:45"
    assert "primera, marcos castellano" in voice
    assert "sesión de fisioterapia el lunes a las diez y cuarenta y cinco" in voice
    assert "10:45" not in voice
