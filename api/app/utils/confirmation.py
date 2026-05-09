from .intent_router import normalize_text


GENERIC_CONFIRMATIONS = {
    "si",
    "vale",
    "ok",
    "de acuerdo",
    "confirmo",
    "correcto",
    "adelante",
}

GENERIC_DECLINES = {
    "no",
    "mejor no",
    "dejalo",
    "deja lo",
    "cancelar",
}

CANCEL_CONFIRMATIONS = {
    *GENERIC_CONFIRMATIONS,
    "si cancelala",
    "si cancela",
    "si cancelar",
    "cancelala",
    "cancelar",
    "confirmo cancelacion",
}

CANCEL_DECLINES = {
    "no",
    "espera",
    "mejor no",
    "no la canceles",
    "no la anules",
    "no canceles",
    "no anules",
    "no lo canceles",
    "dejalo",
    "deja la cita",
    "mantener",
    "mantenla",
}


def is_confirmation_yes(text: str) -> bool:
    return normalize_text(text) in GENERIC_CONFIRMATIONS


def is_confirmation_no(text: str) -> bool:
    return normalize_text(text) in GENERIC_DECLINES


def is_cancel_confirmation_no(text: str) -> bool:
    normalized = normalize_text(text)
    if normalized in CANCEL_DECLINES:
        return True
    return normalized.startswith("no ") and any(term in normalized for term in {"cancel", "anul"})


def is_cancel_confirmation_yes(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or is_cancel_confirmation_no(normalized):
        return False
    if normalized in CANCEL_CONFIRMATIONS:
        return True
    return normalized.startswith("si ") and any(term in normalized for term in {"cancel", "anul"})


def has_cancel_intent(text: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in {"cancel", "anul"})


def cancel_selection_implies_confirmation(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized or is_cancel_confirmation_no(normalized):
        return False
    return has_cancel_intent(normalized)
