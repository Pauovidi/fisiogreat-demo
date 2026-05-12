import datetime as dt

from app.routers import voice
from app.utils.voice_copy import VOICE_COPY


def _slot(hour: int, minute: int = 0):
    return {"start": dt.datetime(2026, 5, 14, hour, minute), "end": dt.datetime(2026, 5, 14, hour, minute) + dt.timedelta(minutes=45)}


def test_voice_slot_offer_copy_mentions_day_and_three_options():
    slots = [
        "jueves 14/05 a las 10:00",
        "jueves 14/05 a las 10:15",
        "jueves 14/05 a las 10:30",
    ]

    text = VOICE_COPY.propose_slots(slots, time_pref="morning").lower()

    assert "para el jueves por la mañana" in text
    assert "primera, segunda o tercera" in text


def test_voice_slot_offer_copy_for_single_slot_is_explicit():
    text = VOICE_COPY.propose_slots(["jueves 14/05 a las 11:15"], time_pref="morning").lower()

    assert "solo veo este hueco" in text
    assert "di primera si te encaja" in text


def test_voice_no_slots_copy_mentions_validated_day():
    text = VOICE_COPY.no_slots_for_day(dt.date(2026, 5, 14), time_pref="morning").lower()

    assert "para el jueves por la mañana no veo huecos libres" in text
    assert "otro día" in text


def test_voice_contact_prompt_is_complete():
    text = VOICE_COPY.ask_contact_with_phone_suggestion()

    assert text == (
        "Para enviarte la confirmación y el recordatorio, ¿uso este número "
        "de llamada o me das otro teléfono o tu correo electrónico?"
    )
    assert "correo electrónico" in text
    assert not text.rstrip(" ?").endswith(("un", "o un", "un email", "email"))
    assert "un email" not in text
    assert "un..." not in text


def test_voice_copy_uses_human_accents_for_speech():
    assert "mañana" in VOICE_COPY.ask_date_retry()
    assert "manana" not in VOICE_COPY.ask_date_retry()
    assert "confirmación" in VOICE_COPY.ask_contact()
    assert "sesión" in VOICE_COPY.ask_consultation_reason()
    assert "teléfono" in VOICE_COPY.ask_contact()
    assert "correo electrónico" in VOICE_COPY.ask_contact()


def test_voice_copy_mentions_miercoles_with_accent():
    text = VOICE_COPY.propose_slots(["miércoles 13/05 a las 10:00"], time_pref="morning").lower()

    assert "miércoles" in text
    assert "miercoles" not in text


def test_voice_slot_builder_offers_three_when_available(monkeypatch):
    monkeypatch.setattr("app.routers.voice.booking_propose_slots", lambda *_args, **_kwargs: [_slot(10), _slot(10, 15), _slot(10, 30)])

    labels = voice._build_slot_labels("sesion de fisioterapia", dt.date(2026, 5, 14), "morning")

    assert labels == [
        "jueves 14/05 a las 10:00",
        "jueves 14/05 a las 10:15",
        "jueves 14/05 a las 10:30",
    ]


def test_voice_slot_builder_offers_two_when_only_two_are_available(monkeypatch):
    monkeypatch.setattr("app.routers.voice.booking_propose_slots", lambda *_args, **_kwargs: [_slot(10), _slot(10, 15)])

    labels = voice._build_slot_labels("sesion de fisioterapia", dt.date(2026, 5, 14), "morning")

    assert labels == ["jueves 14/05 a las 10:00", "jueves 14/05 a las 10:15"]


def test_voice_slot_builder_keeps_later_candidates_after_filtering(monkeypatch):
    calls = []

    def fake_propose(*_args, **kwargs):
        calls.append(kwargs)
        return [_slot(14), _slot(14, 15), _slot(15), _slot(15, 15), _slot(15, 30)]

    monkeypatch.setattr("app.routers.voice.booking_propose_slots", fake_propose)

    labels = voice._build_slot_labels("sesion de fisioterapia", dt.date(2026, 5, 14), "afternoon")

    assert calls[0]["count"] >= 12
    assert labels == [
        "jueves 14/05 a las 15:00",
        "jueves 14/05 a las 15:15",
        "jueves 14/05 a las 15:30",
    ]


def test_voice_reschedule_slot_builder_skips_original_start(monkeypatch):
    original = dt.datetime(2026, 5, 14, 10, 0)
    monkeypatch.setattr(
        "app.routers.voice.booking_propose_slots",
        lambda *_args, **_kwargs: [_slot(10), _slot(10, 15), _slot(10, 30)],
    )

    labels = voice._build_slot_labels(
        "sesion de fisioterapia",
        dt.date(2026, 5, 14),
        "morning",
        original_start_at=original.isoformat(),
        allow_dummy_fallback=False,
    )

    assert labels == ["jueves 14/05 a las 10:15", "jueves 14/05 a las 10:30"]
