import pytest

from app.utils.emergency_guard import detect_emergency
from app.utils.patient_name import extract_patient_name_from_voice


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Marcos Castellano", "Marcos Castellano"),
        ("Ah, Marcos Castellano.", "Marcos Castellano"),
        ("Eh Marcos Castellano", "Marcos Castellano"),
        ("Soy Marcos Castellano", "Marcos Castellano"),
        ("Me llamo Marcos Castellano", "Marcos Castellano"),
        ("A nombre de Marcos Castellano", "Marcos Castellano"),
        ("El nombre es Marcos Castellano", "Marcos Castellano"),
        ("Marcos", "Marcos"),
        ("marcos castellano", "Marcos Castellano"),
        ("Marcos C.", "Marcos C"),
    ],
)
def test_extract_patient_name_from_voice_accepts_clear_names(raw, expected):
    assert extract_patient_name_from_voice(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "mañana",
        "el miércoles",
        "la segunda",
        "gracias",
        "reiniciar",
        "me duele la rodilla",
        "mi email es pau@example.com",
        "640 78 67 65",
    ],
)
def test_extract_patient_name_from_voice_rejects_non_names(raw):
    assert extract_patient_name_from_voice(raw) is None


def test_extract_patient_name_from_voice_rejects_emergency_as_name():
    raw = "me duele el pecho y me cuesta respirar"

    assert detect_emergency(raw).detected
    assert extract_patient_name_from_voice(raw) is None
