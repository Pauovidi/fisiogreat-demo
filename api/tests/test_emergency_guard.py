from app.utils.emergency_guard import detect_emergency, emergency_reply


def test_detects_required_emergency_phrases():
    phrases = [
        "dolor torácico",
        "dolor en el pecho",
        "dificultad respiratoria",
        "no puedo respirar",
        "pérdida de consciencia",
        "desmayo",
        "síntomas de ictus",
        "cara torcida",
        "pérdida de fuerza súbita",
        "parálisis",
        "sangrado grave",
        "dolor severo repentino",
        "urgencia médica",
    ]
    for phrase in phrases:
        assert detect_emergency(phrase).detected, phrase


def test_emergency_replies_are_channel_specific():
    assert "112" in emergency_reply("voice")
    assert "No voy a agendar" in emergency_reply("whatsapp")
