from typing import Optional

from ..services.salon_knowledge import faq_answer


FAQ_IDS = {"hours", "location", "pricing", "services", "human_handoff"}


def get_faq_answer(faq_id: str, channel: str) -> Optional[str]:
    if faq_id not in FAQ_IDS:
        return None
    return faq_answer(faq_id, channel)
