from dataclasses import dataclass
from typing import List, Optional

from .salon_knowledge import out_of_scope_answer
from ..utils.faq import get_faq_answer
from ..utils.intent_router import route_message
from ..utils.mini_context import CTX
from ..utils.voice_copy import VOICE_COPY as copy
from .voice_ai import classify_and_draft_reply


@dataclass
class NaturalTurnResult:
    handled: bool
    reply: Optional[str] = None
    route_type: str = "fallback"


async def maybe_handle_natural_turn(
    key: str,
    user_text: str,
    *,
    channel: str = "voice",
    page_size: int = 2,
) -> NaturalTurnResult:
    route = route_message(user_text)

    if route["type"] == "faq":
        return NaturalTurnResult(True, _faq_with_reengagement(key, route["faq_id"], channel, page_size), "faq")

    if route["type"] == "human_handoff":
        return NaturalTurnResult(True, _handoff_with_reengagement(key, channel, page_size), "human_handoff")

    if route["type"] == "uncertain":
        return NaturalTurnResult(True, _uncertain_with_reengagement(key, page_size), "uncertain")

    if route["type"] == "thanks":
        return NaturalTurnResult(True, _thanks_with_reengagement(key), "thanks")

    if route["type"] == "farewell":
        return NaturalTurnResult(True, _farewell_with_reengagement(key), "farewell")

    if route["type"] == "acknowledgement":
        return NaturalTurnResult(True, _acknowledgement_with_reengagement(key), "acknowledgement")

    if route["type"] != "fallback":
        return NaturalTurnResult(False, route_type=route["type"])

    ctx = CTX.get(key) or {}
    ai_result = await classify_and_draft_reply(
        user_text,
        stage=CTX.get_stage(key),
        service=ctx.get("service"),
        channel=channel,
    )
    if not ai_result:
        return NaturalTurnResult(False, route_type="fallback")

    if ai_result.intent == "faq" and ai_result.faq_id:
        answer = ai_result.answer or get_faq_answer(ai_result.faq_id, channel)
        return NaturalTurnResult(True, _with_reengagement(key, answer, page_size), "faq_ai")

    if ai_result.intent == "human_handoff":
        answer = ai_result.answer or get_faq_answer("human_handoff", channel)
        return NaturalTurnResult(True, _with_reengagement(key, answer, page_size), "human_handoff_ai")

    if ai_result.intent == "uncertain":
        return NaturalTurnResult(True, _uncertain_with_reengagement(key, page_size), "uncertain_ai")

    if ai_result.intent == "out_of_scope":
        answer = ai_result.answer or out_of_scope_answer(channel)
        return NaturalTurnResult(True, _with_reengagement(key, answer, page_size), "out_of_scope_ai")

    return NaturalTurnResult(False, route_type="fallback")


def _faq_with_reengagement(key: str, faq_id: str, channel: str, page_size: int) -> str:
    answer = get_faq_answer(faq_id, channel) or copy.out_of_scope()
    return _with_reengagement(key, answer, page_size)


def _handoff_with_reengagement(key: str, channel: str, page_size: int) -> str:
    answer = get_faq_answer("human_handoff", channel) or "Sin problema. Te paso con el salon."
    return _with_reengagement(key, answer, page_size)


def _uncertain_with_reengagement(key: str, page_size: int) -> str:
    stage = CTX.get_stage(key)
    if stage == "awaiting_date":
        return copy.no_idea_date()
    if stage == "offering_slots":
        return copy.no_idea_slot()
    if stage == "awaiting_service":
        return copy.no_idea_service()
    return "No pasa nada. Si quieres, empezamos por el servicio."


def _with_reengagement(key: str, answer: Optional[str], page_size: int) -> str:
    if not answer:
        return copy.out_of_scope()

    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    answer = answer.strip()

    if stage == "awaiting_service":
        return _join_answers(answer, copy.ask_service())
    if stage == "awaiting_date":
        return _join_answers(answer, copy.resume_date(ctx.get("service")))
    if stage == "awaiting_patient_name":
        return _join_answers(answer, copy.ask_patient_name(ctx.get("service")))
    if stage == "offering_slots":
        current = _current_slots(key, page_size)
        if current:
            return _join_answers(answer, copy.propose_slots(current))
        return _join_answers(answer, copy.resume_date(ctx.get("service")))
    return answer


def _thanks_with_reengagement(key: str) -> str:
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    last_confirmed_slot = ctx.get("last_confirmed_slot")

    if stage in {"idle", "completed"} and last_confirmed_slot:
        return copy.thanks_after_booking(last_confirmed_slot, ctx.get("last_confirmed_patient_name"))
    if stage == "awaiting_service":
        return copy.thanks_with_followup(copy.ask_service_for_booking())
    if stage == "awaiting_patient_name":
        return copy.thanks_with_followup(copy.ask_patient_name(ctx.get("service")))
    if stage == "awaiting_date":
        return copy.thanks_with_followup(copy.resume_date(ctx.get("service")))
    if stage == "offering_slots":
        return copy.thanks_with_followup(copy.propose_slots(_current_slots(key, 2)))
    return copy.thanks_generic()


def _farewell_with_reengagement(key: str) -> str:
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    last_confirmed_slot = ctx.get("last_confirmed_slot")
    if stage in {"idle", "completed"} and last_confirmed_slot:
        return copy.farewell_after_booking(last_confirmed_slot, ctx.get("last_confirmed_patient_name"))
    return copy.farewell_generic()


def _acknowledgement_with_reengagement(key: str) -> str:
    stage = CTX.get_stage(key)
    ctx = CTX.get(key) or {}
    last_confirmed_slot = ctx.get("last_confirmed_slot")

    if stage in {"idle", "completed"} and last_confirmed_slot:
        return copy.thanks_after_booking(last_confirmed_slot, ctx.get("last_confirmed_patient_name"))
    if stage == "awaiting_service":
        return copy.ask_service_for_booking()
    if stage == "awaiting_patient_name":
        return copy.ask_patient_name(ctx.get("service"))
    if stage == "awaiting_date":
        return copy.resume_date(ctx.get("service"))
    if stage == "offering_slots":
        return copy.propose_slots(_current_slots(key, 2))
    return copy.thanks_closing()


def _join_answers(answer: str, follow_up: Optional[str]) -> str:
    if not follow_up:
        return answer
    return f"{answer} {follow_up}".strip()


def _current_slots(key: str, page_size: int) -> List[str]:
    ctx = CTX.get(key) or {}
    offered_slots = ctx.get("offered_slots", [])
    offered_offset = ctx.get("offered_offset", 0)
    if not offered_slots:
        return []
    start = max(offered_offset - page_size, 0)
    current = offered_slots[start:offered_offset]
    return current or offered_slots[:page_size]
