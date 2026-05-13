import re
import unicodedata
from typing import Optional, Tuple


PHYSIO_SESSION = "sesion de fisioterapia"


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def _contact_intent_text(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"[¿?¡!.,;:]+", " ", normalized)
    return " ".join(normalized.split())


def is_physiotherapy_session(service: Optional[str]) -> bool:
    return normalize_text(service or "") == PHYSIO_SESSION


def clean_consultation_reason(value: str) -> Optional[str]:
    reason = " ".join((value or "").strip().split())
    normalized = normalize_text(reason)
    if not reason or len(normalized) < 4:
        return None
    if normalized in {"no se", "nose", "no lo se", "ni idea", "no estoy seguro", "no estoy segura"}:
        return None
    return reason


def extract_contact(value: str) -> Tuple[Optional[str], Optional[str]]:
    email = extract_email(value)
    if email:
        return None, email

    phone = extract_phone(value)
    if phone:
        return phone, None
    return None, None


def extract_email(value: str) -> Optional[str]:
    text = (value or "").strip()
    email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    if email_match:
        return email_match.group(0).rstrip(".,;:!?").lower()

    candidate = _spoken_email_candidate(text)
    if _is_valid_email(candidate):
        return candidate
    return None


def extract_phone(value: str) -> Optional[str]:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if 9 <= len(digits) <= 15:
        return digits

    spoken_digits = _spoken_phone_digits(value)
    if spoken_digits and 9 <= len(spoken_digits) <= 15:
        return spoken_digits
    return None


def confirms_current_phone(value: str) -> bool:
    normalized = _contact_intent_text(value)
    return normalized in {
        "si",
        "si a este numero",
        "si este numero",
        "si en este numero",
        "este numero",
        "a este numero",
        "ese numero",
        "al mismo numero",
        "vale",
        "correcto",
        "perfecto",
    }


def requests_email_contact(value: str) -> bool:
    normalized = _contact_intent_text(value)
    if extract_email(value):
        return False
    return normalized in {
        "email",
        "el email",
        "un email",
        "e-mail",
        "e mail",
        "el e-mail",
        "el e mail",
        "mail",
        "mi mail",
        "correo",
        "el correo",
        "un correo",
        "correo electronico",
        "el correo electronico",
        "un correo electronico",
        "prefiero email",
        "prefiero mail",
        "prefiero correo",
        "por email",
        "por mail",
        "mejor email",
        "por correo",
        "mejor por correo",
    }


def requests_phone_contact(value: str) -> bool:
    normalized = _contact_intent_text(value)
    if extract_phone(value):
        return False
    return normalized in {
        "telefono",
        "un telefono",
        "telefono movil",
        "un telefono movil",
        "movil",
        "un movil",
        "numero",
        "un numero",
        "otro telefono",
        "prefiero telefono",
        "por telefono",
        "mejor telefono",
        "mejor por telefono",
    }


def looks_like_incomplete_email(value: str) -> bool:
    if extract_email(value):
        return False
    normalized = _contact_intent_text(value)
    padded = f" {normalized} "
    if "@" in (value or "") or " arroba " in padded:
        return True
    return normalized.startswith(
        (
            "mi email es ",
            "mi e-mail es ",
            "mi e mail es ",
            "mi mail es ",
            "mi correo es ",
            "mi correo electronico es ",
        )
    )


EMAIL_PREFIX_PATTERNS = [
    r"^(?:mi\s+)?(?:correo\s+electronico|e\s*-?\s*mail|email|mail|correo)\s+(?:es\s+)?",
    r"^(?:el\s+)?(?:correo\s+electronico|e\s*-?\s*mail|email|mail|correo)\s+(?:es\s+)?",
    r"^apuntalo\s+en\s+",
    r"^apuntamelo\s+en\s+",
    r"^envialo\s+a\s+",
    r"^enviamelo\s+a\s+",
]

SPOKEN_EMAIL_REPLACEMENTS = [
    (r"\bguion bajo\b", "_"),
    (r"\bbarra baja\b", "_"),
    (r"\barroba\b", "@"),
    (r"\bpunto\b", "."),
    (r"\bdot\b", "."),
    (r"\bguion\b", "-"),
]

PHONE_SINGLE_WORDS = {
    "cero": 0,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
}

PHONE_NUMBER_WORDS = {
    **PHONE_SINGLE_WORDS,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}

PHONE_TENS_WORDS = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}


def _spoken_email_candidate(value: str) -> str:
    text = _deaccent(value)
    text = re.sub(r"[^a-z0-9@._%+\-\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:!?")
    for pattern in EMAIL_PREFIX_PATTERNS:
        text = re.sub(pattern, "", text, count=1)
    for pattern, replacement in SPOKEN_EMAIL_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s*([@._%+\-])\s*", r"\1", text)
    text = re.sub(r"\s+", "", text)
    return text.strip(" .,;:!?").lower()


def _is_valid_email(value: str) -> bool:
    if not value or "@" not in value:
        return False
    if value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    if not local or "." not in domain:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False
    return bool(re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", value))


def _spoken_phone_digits(value: str) -> str:
    normalized = normalize_text(value)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    chunks = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isdigit():
            chunks.append(token)
            index += 1
            continue
        if (
            token in PHONE_TENS_WORDS
            and index + 2 < len(tokens)
            and tokens[index + 1] == "y"
            and tokens[index + 2] in PHONE_SINGLE_WORDS
        ):
            chunks.append(str(PHONE_TENS_WORDS[token] + PHONE_SINGLE_WORDS[tokens[index + 2]]))
            index += 3
            continue
        if token in PHONE_NUMBER_WORDS:
            value_int = PHONE_NUMBER_WORDS[token]
            chunks.append(str(value_int) if value_int < 10 else f"{value_int:02d}")
        index += 1
    return "".join(chunks)


def _deaccent(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))
