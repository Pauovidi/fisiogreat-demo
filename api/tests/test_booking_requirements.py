import pytest

from app.utils.booking_requirements import (
    confirms_current_phone,
    extract_contact,
    looks_like_incomplete_email,
    requests_email_contact,
    requests_phone_contact,
)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("marcos@example.com", "marcos@example.com"),
        ("Marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
        ("mi email es marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
        ("mi mail es marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
        ("mi correo es marcos punto castellano arroba gmail punto com", "marcos.castellano@gmail.com"),
        ("marcos guion bajo prueba arroba hotmail punto es", "marcos_prueba@hotmail.es"),
        ("marcos guion prueba arroba gmail punto com", "marcos-prueba@gmail.com"),
        ("apuntalo en marcos arroba ejemplo punto com", "marcos@ejemplo.com"),
    ],
)
def test_extract_contact_accepts_spoken_voice_emails(spoken, expected):
    phone, email = extract_contact(spoken)

    assert phone is None
    assert email == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "Marcos Castellano",
        "me duele la rodilla",
        "gracias",
        "mañana",
        "marcos arroba ejemplo",
        "mi mail es marcos arroba ejemplo dos como",
        "marcos arroba fisiograde",
        "marcos arroba ejemplo punto",
    ],
)
def test_extract_contact_rejects_non_contacts_as_email(spoken):
    assert extract_contact(spoken) == (None, None)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("640 50 50 50", "640505050"),
        ("mi teléfono es 640 50 50 50", "640505050"),
        ("seis cuarenta cincuenta cincuenta cincuenta", "640505050"),
    ],
)
def test_extract_contact_accepts_digit_and_spoken_phones(spoken, expected):
    phone, email = extract_contact(spoken)

    assert phone == expected
    assert email is None


@pytest.mark.parametrize(
    "spoken",
    [
        "un email",
        "el email",
        "un correo",
        "el correo",
        "email",
        "mail",
        "mi mail",
        "e-mail",
        "correo electronico",
        "el correo electrónico",
        "prefiero email",
        "prefiero mail",
        "prefiero correo",
        "correo.",
        "un correo.",
        "correo electrónico.",
        "por correo.",
    ],
)
def test_requests_email_contact_detects_intent_without_treating_it_as_email(spoken):
    assert extract_contact(spoken) == (None, None)
    assert requests_email_contact(spoken) is True


def test_requests_email_contact_ignores_real_email():
    assert requests_email_contact("mi email es marcos arroba ejemplo punto com") is False


@pytest.mark.parametrize(
    "spoken",
    [
        "marcos arroba ejemplo",
        "mi mail es marcos arroba ejemplo dos como",
        "marcos arroba fisiograde",
        "marcos arroba ejemplo punto",
    ],
)
def test_incomplete_email_is_detected_without_becoming_contact(spoken):
    assert extract_contact(spoken) == (None, None)
    assert looks_like_incomplete_email(spoken) is True


@pytest.mark.parametrize("spoken", ["un teléfono", "telefono", "móvil", "prefiero telefono", "por telefono", "teléfono."])
def test_requests_phone_contact_detects_intent_without_treating_it_as_phone(spoken):
    assert extract_contact(spoken) == (None, None)
    assert requests_phone_contact(spoken) is True


@pytest.mark.parametrize("spoken", ["sí, a este número", "sí, a este número.", "si a este numero.", "este número", "sí, este número"])
def test_confirms_current_phone_accepts_punctuated_stt(spoken):
    assert confirms_current_phone(spoken) is True
