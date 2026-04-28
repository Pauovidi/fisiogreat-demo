import re
import unicodedata
from typing import List, Optional


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def pick_slot(text: str, offered_slots: List[str]) -> Optional[str]:
    if not offered_slots:
        return None

    normalized = _normalize(text)
    selected_index = _pick_slot_index(normalized, len(offered_slots))
    if selected_index is not None:
        return offered_slots[selected_index]

    time_match = re.search(r"(?:a\s+las\s+)?(\d{1,2})(?::(\d{2}))?", normalized)
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = time_match.group(2)
    if minute is not None:
        target = f"{hour:02d}:{minute}"
        for slot in offered_slots:
            if target in slot:
                return slot
        return None

    hour_prefix = f"{hour:02d}:"
    for slot in offered_slots:
        if hour_prefix in slot:
            return slot
    return None


def _pick_slot_index(normalized: str, slot_count: int) -> Optional[int]:
    token_patterns = [
        (0, [r"\b1\b", r"\bprimera\b", r"\bla primera\b", r"\bopcion primera\b", r"\buno\b", r"\bla uno\b"]),
        (1, [r"\b2\b", r"\bsegunda\b", r"\bla segunda\b", r"\bopcion segunda\b", r"\bdos\b", r"\bla dos\b"]),
        (2, [r"\b3\b", r"\btercera\b", r"\bla tercera\b", r"\bopcion tercera\b", r"\btres\b", r"\bla tres\b"]),
    ]

    for idx, patterns in token_patterns:
        if idx >= slot_count:
            continue
        if any(re.search(pattern, normalized) for pattern in patterns):
            return idx

    explicit_number = re.search(r"\bopcion\s*([123])\b", normalized)
    if explicit_number:
        idx = int(explicit_number.group(1)) - 1
        if idx < slot_count:
            return idx

    return None
