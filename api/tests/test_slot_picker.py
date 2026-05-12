import pytest

from app.utils.slot_picker import pick_slot, pick_slot_with_index


SLOTS = [
    "jueves 14/05 a las 10:00",
    "jueves 14/05 a las 10:15",
    "jueves 14/05 a las 10:30",
]


@pytest.mark.parametrize(
    ("text", "expected_index"),
    [
        ("primera", 0),
        ("la primera", 0),
        ("opción primera", 0),
        ("opción uno", 0),
        ("uno", 0),
        ("la uno", 0),
        ("primer hueco", 0),
        ("la segunda", 1),
        ("segunda", 1),
        ("la tercera", 2),
    ],
)
def test_pick_slot_selection_variants(text, expected_index):
    assert pick_slot(text, SLOTS) == SLOTS[expected_index]
    assert pick_slot_with_index(text, SLOTS) == (expected_index, SLOTS[expected_index])
