import datetime as dt
import json
import time
import unicodedata
from html import escape
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Form, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from ..config.settings import settings
from ..services.conversation_relay import (
    ConversationRelaySession,
    handle_dtmf_message,
    handle_interrupt_message,
    handle_prompt_message,
    handle_setup_message,
)
from ..services import supabase_repo
from ..services.booking_service import confirm_slot, parse_slot_label
from ..services.natural_turn import maybe_handle_natural_turn
from ..services.salon_knowledge import out_of_scope_answer
from ..services.slots import BusinessRules, propose_slots
from ..utils.date_parser import parse_spanish_day, parse_time_pref
from ..utils.intent_router import detect_service, route_message
from ..utils.emergency_guard import detect_emergency, emergency_reply
from ..utils.logger import logger
from ..utils.mini_context import CTX
from ..utils.slot_picker import pick_slot
from ..utils.voice_copy import VOICE_COPY as copy


router = APIRouter(prefix="/webhook/voice", tags=["voice"])

VOICE_PAGE_SIZE = 2
VOICE_HINTS = (
    "fisioterapia, fisio, primera visita, valoracion inicial, valoracion, seguimiento, sesion, "
    "lunes, martes, miércoles, miercoles, jueves, viernes, sábado, sabado, domingo, "
    "mañana, manana, pasado mañana, pasado manana, tarde, primera, segunda, uno, dos"
)
WEEKDAY_LABELS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]
VOICE_CALL_STATE: Dict[str, Dict[str, float]] = {}
TTS_CACHE_TTL_SECONDS = 1800
TTS_CACHE_MAX_ITEMS = 64
TTS_CACHE: Dict[str, Dict[str, Any]] = {}


def _twiml(body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
        media_type="application/xml",
    )


def _say(text: str) -> str:
    return f'<Say language="es-ES" voice="Polly.Conchita">{text}</Say>'


def _normalize_voice_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def _looks_like_lavado_only(text: str) -> bool:
    normalized = _normalize_voice_text(text)
    return "lavado" in normalized and "corte" not in normalized


def _mark_prompt(call_sid: str):
    VOICE_CALL_STATE.setdefault(call_sid, {})["last_prompt_at"] = time.perf_counter()


def _since_last_prompt_ms(call_sid: str) -> Optional[float]:
    last_prompt_at = VOICE_CALL_STATE.get(call_sid, {}).get("last_prompt_at")
    if last_prompt_at is None:
        return None
    return round((time.perf_counter() - last_prompt_at) * 1000, 1)


def _log_turn(call_sid: str, stats: Dict[str, Any]):
    payload = {
        "call_sid": call_sid,
        "stage_before": stats.get("stage_before"),
        "stage_after": stats.get("stage_after"),
        "branch": stats.get("branch"),
        "raw": stats.get("raw", ""),
        "normalized": stats.get("normalized", ""),
        "speech_present": stats.get("speech_present", False),
        "speech_available_ms": round(stats.get("speech_available_ms", 0.0), 1),
        "turn_gap_ms": stats.get("turn_gap_ms"),
        "intent_ms": round(stats.get("intent_ms", 0.0), 1),
        "slot_ms": round(stats.get("slot_ms", 0.0), 1),
        "tts_mode": stats.get("tts_mode", "say"),
        "twiml_ms": round(stats.get("twiml_ms", 0.0), 1),
        "total_ms": round(stats.get("total_ms", 0.0), 1),
    }
    logger.info(
        "voice_turn "
        + " ".join(f"{key}={payload[key]!r}" for key in payload)
    )


def _respond_gather(call_sid: str, prompt: str, stats: Dict[str, Any]) -> Response:
    twiml_start = time.perf_counter()
    body = (
        f'<Gather input="speech dtmf" action="/webhook/voice/agent" actionOnEmptyResult="true" '
        f'method="POST" language="es-ES" timeout="3" speechTimeout="auto" hints="{VOICE_HINTS}">'
        f"{_say(prompt)}</Gather>"
    )
    response = _twiml(body)
    stats["tts_mode"] = "say"
    stats["twiml_ms"] = (time.perf_counter() - twiml_start) * 1000
    stats["total_ms"] = (time.perf_counter() - stats["start"]) * 1000
    _log_turn(call_sid, stats)
    _mark_prompt(call_sid)
    return response


def _format_slot_label(slot_dt: dt.datetime) -> str:
    return f"{WEEKDAY_LABELS[slot_dt.weekday()]} {slot_dt.strftime('%d/%m')} a las {slot_dt.strftime('%H:%M')}"


def _dummy_slot_labels(date_pref: dt.date, time_pref: Optional[str], count: int) -> List[str]:
    if time_pref == "morning":
        times = [dt.time(10, 0), dt.time(11, 30)]
    elif time_pref == "afternoon":
        times = [dt.time(16, 0), dt.time(17, 30)]
    else:
        times = [dt.time(11, 0), dt.time(17, 0)]
    return [_format_slot_label(dt.datetime.combine(date_pref, value)) for value in times[:count]]


def _build_slot_labels(service: str, date_pref: dt.date, time_pref: Optional[str], count: int = 4) -> List[str]:
    preferred_hour = 11 if time_pref == "morning" else 16 if time_pref == "afternoon" else 12
    preferred_dt = dt.datetime.combine(date_pref, dt.time(preferred_hour, 0))

    try:
        raw_slots = propose_slots(preferred_dt, service, BusinessRules())
    except Exception:
        raw_slots = []

    labels: List[str] = []
    for slot in raw_slots:
        start = slot["start"]
        if start.date() < date_pref:
            continue
        if time_pref == "morning" and start.hour >= 15:
            continue
        if time_pref == "afternoon" and start.hour < 15:
            continue
        label = _format_slot_label(start)
        if label not in labels:
            labels.append(label)
        if len(labels) >= count:
            break

    if not labels:
        return _dummy_slot_labels(date_pref, time_pref, count)
    return labels


def _current_slots(key: str, page_size: int = VOICE_PAGE_SIZE) -> List[str]:
    ctx = CTX.get(key) or {}
    offered_slots = ctx.get("offered_slots", [])
    offered_offset = ctx.get("offered_offset", 0)
    if not offered_slots:
        return []
    start = max(offered_offset - page_size, 0)
    current = offered_slots[start:offered_offset]
    return current or offered_slots[:page_size]


def _reprompt_for_stage(call_sid: str) -> str:
    stage = CTX.get_stage(call_sid)
    ctx = CTX.get(call_sid) or {}
    if stage == "awaiting_date":
        return copy.ask_date(ctx.get("service"))
    if stage == "offering_slots":
        current = _current_slots(call_sid)
        if current:
            return copy.propose_slots(current)
        return copy.ask_date(ctx.get("service"))
    return copy.ask_service_retry()


def _offer_slots(call_sid: str, service: str, parsed_date: dt.date, time_pref: Optional[str], stats: Dict[str, Any]) -> Response:
    slot_start = time.perf_counter()
    CTX.set_date(call_sid, parsed_date)
    CTX.set_time_pref(call_sid, time_pref)
    CTX.set_slots(call_sid, _build_slot_labels(service, parsed_date, time_pref))
    CTX.set_stage(call_sid, "offering_slots")
    stats["slot_ms"] += (time.perf_counter() - slot_start) * 1000
    stats["stage_after"] = "offering_slots"
    return _respond_gather(call_sid, copy.propose_slots(CTX.next_slots(call_sid, VOICE_PAGE_SIZE)), stats)


def _shift_slot_offer(call_sid: str, *, direction: str, stats: Dict[str, Any]) -> Optional[Response]:
    ctx = CTX.get(call_sid) or {}
    service = ctx.get("service")
    date_pref = ctx.get("date_pref")
    current_pref = ctx.get("time_pref")
    if not service or not date_pref:
        return None

    if direction == "later":
        if current_pref != "afternoon":
            return _offer_slots(call_sid, service, date_pref, "afternoon", stats)
        next_batch = CTX.next_slots(call_sid, VOICE_PAGE_SIZE)
        if next_batch:
            stats["branch"] = "later_slots_page"
            stats["stage_after"] = "offering_slots"
            return _respond_gather(call_sid, copy.propose_slots(next_batch), stats)
        return _offer_slots(call_sid, service, date_pref, "afternoon", stats)

    if current_pref != "morning":
        return _offer_slots(call_sid, service, date_pref, "morning", stats)
    return _offer_slots(call_sid, service, date_pref, "morning", stats)


def _absolute_url(path: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def _conversationrelay_ws_url() -> str:
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    ws_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    ws_url = f"{ws_url}/webhook/voice/conversationrelay/ws"
    logger.info(f"conversationrelay_ws_url={ws_url}")
    return ws_url


def _resolve_tts_provider() -> str:
    providers = {
        "amazon": "Amazon",
        "elevenlabs": "ElevenLabs",
        "google": "Google",
    }
    requested = providers.get((settings.CONVERSATIONRELAY_TTS_PROVIDER or "").strip().lower())
    if requested:
        return requested

    fallback = providers.get((settings.CONVERSATIONRELAY_TTS_PROVIDER_FALLBACK or "").strip().lower(), "Google")
    logger.warning(
        "conversationrelay_invalid_tts_provider "
        f"requested={settings.CONVERSATIONRELAY_TTS_PROVIDER!r} fallback={fallback!r}"
    )
    return fallback


def _resolve_conversationrelay_voice() -> Optional[str]:
    configured = (settings.CONVERSATIONRELAY_TTS_VOICE or "").strip()
    return configured or None


def _resolve_transcription_provider() -> str:
    providers = {
        "deepgram": "Deepgram",
        "google": "Google",
    }
    requested = providers.get((settings.CONVERSATIONRELAY_TRANSCRIPTION_PROVIDER or "").strip().lower())
    if requested:
        return requested
    logger.warning(
        "conversationrelay_invalid_transcription_provider "
        f"requested={settings.CONVERSATIONRELAY_TRANSCRIPTION_PROVIDER!r} fallback='Google'"
    )
    return "Google"


def _conversationrelay_speech_model(provider: str) -> str:
    configured = (settings.CONVERSATIONRELAY_SPEECH_MODEL or "").strip()
    if configured:
        return configured
    return "nova-2-general" if provider == "Deepgram" else "telephony"


def _conversationrelay_twiml() -> Response:
    ws_url = _conversationrelay_ws_url()
    action_url = _absolute_url("/webhook/voice/conversationrelay/action")
    tts_provider = _resolve_tts_provider()
    voice_id = _resolve_conversationrelay_voice()

    attrs = {
        "url": ws_url,
        "ttsLanguage": settings.CONVERSATIONRELAY_TTS_LANGUAGE,
        "ttsProvider": tts_provider,
        "transcriptionLanguage": settings.CONVERSATIONRELAY_TRANSCRIPTION_LANGUAGE,
        "interruptible": settings.CONVERSATIONRELAY_INTERRUPTIBLE,
        "reportInputDuringAgentSpeech": settings.CONVERSATIONRELAY_REPORT_INPUT_DURING_AGENT_SPEECH,
        "hints": VOICE_HINTS,
    }

    if settings.CONVERSATIONRELAY_DTMF_DETECTION:
        attrs["dtmfDetection"] = "true"

    if voice_id:
        attrs["voice"] = voice_id

    attr_string = " ".join(
        f'{name}="{escape(str(value), quote=True)}"'
        for name, value in attrs.items()
        if value not in (None, "")
    )
    body = (
        f'<Connect action="{escape(action_url, quote=True)}">'
        f"<ConversationRelay {attr_string} />"
        f"</Connect>"
    )
    logger.info(
        "conversationrelay_config "
        + " ".join(
            f"{name}={value!r}"
            for name, value in {
                "ws_url": ws_url,
                "ttsProvider": tts_provider,
                "voice": voice_id,
                "ttsLanguage": settings.CONVERSATIONRELAY_TTS_LANGUAGE,
                "transcriptionLanguage": settings.CONVERSATIONRELAY_TRANSCRIPTION_LANGUAGE,
            }.items()
        )
    )
    return _twiml(body)


async def _send_conversationrelay_text(websocket: WebSocket, text: str):
    payload = {
        "type": "text",
        "token": str(text),
        "last": True,
    }
    logger.info(f"conversationrelay_ws_send payload={json.dumps(payload, ensure_ascii=False)}")
    await websocket.send_json(payload)


@router.post("/agent")
async def agent_entry(
    request: Request,
    From: str | None = Form(None),
    CallSid: str = Form(...),
    SpeechResult: str | None = Form(None),
    Digits: str | None = Form(None),
):
    del request, From
    start = time.perf_counter()
    stats: Dict[str, Any] = {
        "start": start,
        "branch": "unknown",
        "stage_before": CTX.get_stage(CallSid),
        "stage_after": CTX.get_stage(CallSid),
        "speech_present": bool(SpeechResult or Digits),
        "speech_available_ms": 0.0,
        "turn_gap_ms": _since_last_prompt_ms(CallSid),
        "intent_ms": 0.0,
        "slot_ms": 0.0,
        "raw": "",
        "normalized": "",
    }

    try:
        if Digits and not SpeechResult:
            SpeechResult = Digits

        current_stage = CTX.get_stage(CallSid)

        if not SpeechResult:
            stats["branch"] = "start" if current_stage == "idle" else "empty_result"
            if current_stage == "idle":
                CTX.clear_flow(CallSid)
                CTX.set_stage(CallSid, "awaiting_service")
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.opening_greeting(), stats)

            stats["stage_after"] = current_stage
            return _respond_gather(CallSid, _reprompt_for_stage(CallSid), stats)

        stats["speech_available_ms"] = (time.perf_counter() - start) * 1000
        user_text = (SpeechResult or "").strip()
        normalized = _normalize_voice_text(user_text)
        stats["raw"] = user_text
        stats["normalized"] = normalized
        logger.info(f"voice_input call_sid={CallSid!r} raw={user_text!r} normalized={normalized!r}")

        current_stage = CTX.get_stage(CallSid)
        emergency = detect_emergency(user_text)
        if emergency.detected:
            stats["branch"] = "emergency_detected"
            CTX.clear_flow(CallSid)
            CTX.set_stage(CallSid, "emergency_detected")
            try:
                await supabase_repo.update_conversation_session(
                    CallSid,
                    channel="voice",
                    external_user_id=CallSid,
                    emergency_detected=True,
                    emergency_match=emergency.matched,
                )
            except Exception as exc:
                logger.warning(f"voice_emergency_session_mark_failed call_sid={CallSid!r} error={exc!r}")
            stats["stage_after"] = "emergency_detected"
            return _respond_gather(CallSid, emergency_reply("voice"), stats)

        route = route_message(user_text)

        if current_stage == "completed":
            if route["type"] in {"thanks", "acknowledgement"}:
                stats["branch"] = route["type"]
                stats["stage_after"] = "completed"
                return _respond_gather(CallSid, copy.thanks_closing(), stats)
            if route["type"] == "booking":
                CTX.clear_flow(CallSid)
                service = route.get("service")
                if service:
                    CTX.set_service(CallSid, service)
                    CTX.set_stage(CallSid, "awaiting_date")
                    stats["branch"] = "completed_restart_booking_service"
                    stats["stage_after"] = "awaiting_date"
                    return _respond_gather(CallSid, copy.ask_date(service), stats)
                CTX.set_stage(CallSid, "awaiting_service")
                stats["branch"] = "completed_restart_booking"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)
            if route["type"] == "reschedule":
                CTX.clear_flow(CallSid)
                stats["branch"] = "completed_reschedule"
                stats["stage_after"] = "idle"
                return _respond_gather(CallSid, "Claro. Dime la cita actual y el dia nuevo.", stats)
            if route["type"] == "cancel":
                CTX.clear_flow(CallSid)
                stats["branch"] = "completed_cancel"
                stats["stage_after"] = "idle"
                return _respond_gather(CallSid, "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada.", stats)
            stats["branch"] = "completed_close"
            stats["stage_after"] = "completed"
            return _respond_gather(CallSid, copy.thanks_closing(), stats)

        if current_stage == "awaiting_service":
            parse_start = time.perf_counter()
            route_peek = route
            service = detect_service(user_text)
            parsed_date = parse_spanish_day(user_text)
            time_pref = parse_time_pref(user_text)
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000

            if route_peek["type"] in {"faq", "human_handoff", "uncertain"}:
                natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
                if natural.handled:
                    stats["branch"] = natural.route_type
                    stats["stage_after"] = CTX.get_stage(CallSid)
                    return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

            if route_peek["type"] == "greeting":
                stats["branch"] = "greeting"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service(), stats)
            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                stats["stage_after"] = current_stage
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "cancel":
                stats["branch"] = "cancel"
                return _respond_gather(CallSid, "Puedo ayudarte a cancelarla. Dime la fecha y la hora aproximada.", stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return _respond_gather(CallSid, "Claro. Dime la cita actual y el dia nuevo.", stats)
            if route_peek["type"] == "booking" and not service:
                stats["branch"] = "booking_without_service"
                stats["stage_after"] = "awaiting_service"
                return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)

            if not service and _looks_like_lavado_only(user_text):
                service = "corte + lavado"
                stats["branch"] = "lavado_heuristic"

            if service and parsed_date:
                CTX.set_service(CallSid, service)
                stats["branch"] = "service_and_date"
                return _offer_slots(CallSid, service, parsed_date, time_pref, stats)

            if service:
                CTX.set_service(CallSid, service)
                CTX.set_stage(CallSid, "awaiting_date")
                stats["branch"] = "service_ok"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, copy.ask_date(service), stats)

            natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
            if natural.handled:
                stats["branch"] = natural.route_type
                stats["stage_after"] = CTX.get_stage(CallSid)
                return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

            stats["branch"] = "service_retry"
            return _respond_gather(CallSid, copy.ask_service_retry(), stats)

        if current_stage == "awaiting_date":
            parse_start = time.perf_counter()
            parsed_date = parse_spanish_day(user_text)
            time_pref = parse_time_pref(user_text)
            route_peek = route
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000

            if parsed_date:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                if not service:
                    CTX.set_stage(CallSid, "awaiting_service")
                    stats["branch"] = "missing_service"
                    stats["stage_after"] = "awaiting_service"
                    return _respond_gather(CallSid, copy.ask_service(), stats)

                stats["branch"] = "date_ok"
                return _offer_slots(CallSid, service, parsed_date, time_pref, stats)

            if time_pref:
                ctx = CTX.get(CallSid) or {}
                CTX.set_time_pref(CallSid, time_pref)
                stats["branch"] = "time_pref_captured"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, copy.ask_date_with_time_pref(ctx.get("service"), time_pref), stats)

            natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
            if natural.handled:
                stats["branch"] = natural.route_type
                stats["stage_after"] = CTX.get_stage(CallSid)
                return _respond_gather(CallSid, natural.reply or copy.ask_date(), stats)

            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return _respond_gather(CallSid, "Claro. Dime el dia que te va mejor.", stats)

            stats["branch"] = "date_retry"
            stats["stage_after"] = "awaiting_date"
            return _respond_gather(CallSid, copy.ask_date_retry(), stats)

        if current_stage == "offering_slots":
            parse_start = time.perf_counter()
            current = _current_slots(CallSid)
            selected = pick_slot(user_text, current)
            reparsed_date = parse_spanish_day(user_text)
            reparsed_time = parse_time_pref(user_text)
            route_peek = route
            stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000

            if selected:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service") or "sesion de fisioterapia"
                selected_dt = parse_slot_label(selected)
                if selected_dt:
                    booking_result = await confirm_slot(
                        channel="voice",
                        external_user_id=CallSid,
                        service_type=service,
                        start_at=selected_dt,
                        metadata={"slot_label": selected},
                    )
                    if not booking_result.ok:
                        stats["branch"] = f"slot_confirm_failed_{booking_result.reason}"
                        return _respond_gather(
                            CallSid,
                            "Ese hueco acaba de ocuparse. Te digo otras opciones.",
                            stats,
                        )
                CTX.clear_flow(CallSid)
                CTX.set_last_confirmed_slot(CallSid, selected)
                stats["branch"] = "slot_selected"
                CTX.set_stage(CallSid, "completed")
                stats["stage_after"] = "completed"
                return _respond_gather(CallSid, copy.confirm_booking(selected), stats)

            if reparsed_date:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                if service:
                    stats["branch"] = "date_changed"
                    return _offer_slots(CallSid, service, reparsed_date, reparsed_time or ctx.get("time_pref"), stats)

            if reparsed_time:
                ctx = CTX.get(CallSid) or {}
                service = ctx.get("service")
                date_pref = ctx.get("date_pref")
                if service and date_pref:
                    stats["branch"] = "time_changed"
                    return _offer_slots(CallSid, service, date_pref, reparsed_time, stats)

            if route_peek["type"] == "later_slots":
                shifted = _shift_slot_offer(CallSid, direction="later", stats=stats)
                if shifted:
                    return shifted

            if route_peek["type"] == "earlier_slots":
                shifted = _shift_slot_offer(CallSid, direction="earlier", stats=stats)
                if shifted:
                    return shifted

            if route_peek["type"] == "another_day":
                stats["branch"] = "another_day"
                stats["stage_after"] = "awaiting_date"
                CTX.set_stage(CallSid, "awaiting_date")
                return _respond_gather(CallSid, copy.another_day(), stats)

            if route_peek["type"] == "reject_slot":
                stats["branch"] = "reject_slot"
                stats["stage_after"] = "offering_slots"
                return _respond_gather(CallSid, copy.earlier_or_later(), stats)

            natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
            if natural.handled:
                stats["branch"] = natural.route_type
                stats["stage_after"] = CTX.get_stage(CallSid)
                return _respond_gather(CallSid, natural.reply or copy.propose_slots(current), stats)

            if route_peek["type"] == "more_options":
                next_batch = CTX.next_slots(CallSid, VOICE_PAGE_SIZE)
                stats["branch"] = "more_options"
                stats["stage_after"] = "offering_slots"
                if next_batch:
                    return _respond_gather(CallSid, copy.propose_slots(next_batch), stats)
                return _respond_gather(CallSid, "No tengo más huecos para ese día. Dime otro.", stats)

            if route_peek["type"] == "out_of_scope":
                stats["branch"] = "out_of_scope"
                return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)
            if route_peek["type"] == "reschedule":
                stats["branch"] = "reschedule"
                return _respond_gather(CallSid, "Vale. Dime otro día o si prefieres mañana o tarde.", stats)

            stats["branch"] = "slot_retry"
            return _respond_gather(CallSid, copy.propose_slots(current), stats)

        parse_start = time.perf_counter()
        stats["intent_ms"] += (time.perf_counter() - parse_start) * 1000
        route_type = route["type"]

        if route_type == "greeting":
            CTX.clear_flow(CallSid)
            CTX.set_stage(CallSid, "awaiting_service")
            stats["branch"] = "greeting"
            stats["stage_after"] = "awaiting_service"
            return _respond_gather(CallSid, copy.ask_service(), stats)

        if route_type == "booking":
            CTX.clear_flow(CallSid)
            service = route.get("service")
            if service:
                CTX.set_service(CallSid, service)
                CTX.set_stage(CallSid, "awaiting_date")
                stats["branch"] = "booking_service"
                stats["stage_after"] = "awaiting_date"
                return _respond_gather(CallSid, copy.ask_date(service), stats)
            CTX.set_stage(CallSid, "awaiting_service")
            stats["branch"] = "booking_no_service"
            stats["stage_after"] = "awaiting_service"
            return _respond_gather(CallSid, copy.ask_service_for_booking(), stats)

        natural = await maybe_handle_natural_turn(CallSid, user_text, page_size=VOICE_PAGE_SIZE)
        if natural.handled:
            stats["branch"] = natural.route_type
            stats["stage_after"] = CTX.get_stage(CallSid)
            return _respond_gather(CallSid, natural.reply or copy.ask_service(), stats)

        if route_type == "out_of_scope":
            stats["branch"] = "out_of_scope"
            return _respond_gather(CallSid, out_of_scope_answer("voice"), stats)

        stats["branch"] = "fallback"
        return _respond_gather(CallSid, copy.ask_service(), stats)

    except Exception as exc:
        logger.exception(f"voice_error call_sid={CallSid!r} error={exc!r}")
        stats["branch"] = "exception"
        stats["stage_after"] = CTX.get_stage(CallSid)
        return _respond_gather(CallSid, copy.technical_error(), stats)


@router.api_route("/conversationrelay", methods=["GET", "POST"])
async def conversationrelay_entry(request: Request):
    del request
    return _conversationrelay_twiml()


@router.api_route("/conversationrelay/action", methods=["GET", "POST"])
async def conversationrelay_action(
    request: Request,
    CallSid: str | None = Form(None),
    SessionId: str | None = Form(None),
    SessionStatus: str | None = Form(None),
    ErrorCode: str | None = Form(None),
    ErrorMessage: str | None = Form(None),
    HandoffData: str | None = Form(None),
):
    del request, SessionId, HandoffData
    if SessionStatus == "failed" and CallSid:
        logger.warning(
            "conversationrelay_action_fallback "
            f"call_sid={CallSid!r} error_code={ErrorCode!r} error_message={ErrorMessage!r}"
        )
        return _twiml(
            f'<Redirect method="POST">{escape(_absolute_url("/webhook/voice/agent"), quote=True)}</Redirect>'
        )

    return _twiml("")


@router.websocket("/conversationrelay/ws")
async def conversationrelay_ws(websocket: WebSocket):
    await websocket.accept()
    session = ConversationRelaySession()

    try:
        while True:
            raw_message = await websocket.receive_text()
            ws_received_at = time.perf_counter()
            logger.info(f"conversationrelay_ws_recv raw={raw_message}")

            try:
                payload = json.loads(raw_message)
                message_type = payload.get("type")
                logger.info(
                    "conversationrelay_ws_event "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} type={message_type!r}"
                )

                if message_type == "setup":
                    route_started_at = time.perf_counter()
                    greeting = handle_setup_message(session, payload)
                    route_finished_at = time.perf_counter()
                    await _send_conversationrelay_text(websocket, greeting)
                    response_sent_at = time.perf_counter()
                    logger.info(
                        "conversationrelay_latency "
                        f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                        f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                        f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                    )
                    continue

                if message_type == "prompt":
                    route_started_at = time.perf_counter()
                    reply = await handle_prompt_message(session, payload)
                    route_finished_at = time.perf_counter()
                    if reply:
                        await _send_conversationrelay_text(websocket, reply)
                        response_sent_at = time.perf_counter()
                        logger.info(
                            "conversationrelay_latency "
                            f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                            f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                            f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                        )
                    continue

                if message_type == "dtmf":
                    route_started_at = time.perf_counter()
                    await _send_conversationrelay_text(websocket, await handle_dtmf_message(session, payload))
                    route_finished_at = response_sent_at = time.perf_counter()
                    logger.info(
                        "conversationrelay_latency "
                        f"ws_received_at={ws_received_at:.6f} route_started_at={route_started_at:.6f} "
                        f"route_finished_at={route_finished_at:.6f} response_sent_at={response_sent_at:.6f} "
                        f"total_turn_latency_ms={(response_sent_at - ws_received_at) * 1000:.1f}"
                    )
                    continue

                if message_type == "interrupt":
                    handle_interrupt_message(session, payload)
                    continue

                if message_type == "error":
                    logger.warning(
                        "conversationrelay_ws_error "
                        f"call_sid={session.call_sid!r} session_id={session.session_id!r} payload={json.dumps(payload, ensure_ascii=False)}"
                    )
                    continue

                logger.info(
                    "conversationrelay_ws_unhandled "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} payload={json.dumps(payload, ensure_ascii=False)}"
                )
            except Exception as exc:
                logger.exception(
                    "conversationrelay_ws_loop_exception "
                    f"call_sid={session.call_sid!r} session_id={session.session_id!r} raw={raw_message!r} error={exc!r}"
                )
                raise
    except WebSocketDisconnect:
        logger.info(
            "conversationrelay_ws_disconnect "
            f"call_sid={session.call_sid!r} session_id={session.session_id!r}"
        )
    except Exception as exc:
        logger.exception(
            "conversationrelay_ws_exception "
            f"call_sid={session.call_sid!r} session_id={session.session_id!r} error={exc!r}"
        )
        await websocket.close(code=1011)


@router.post("")
async def entry(request: Request):
    del request
    return _twiml(_say("Por favor usa el endpoint agent."))


@router.post("/handle_intent")
async def handle_intent(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_name")
async def collect_name(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_service")
async def collect_service(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.post("/collect_datetime")
async def collect_datetime(request: Request):
    del request
    return _twiml(_say("Endpoint obsoleto."))


@router.head("/tts")
async def tts_head():
    return Response(status_code=200, media_type="audio/mpeg")


@router.get("/tts")
async def tts(text: str):
    tts_start = time.perf_counter()
    api_key = settings.ELEVEN_API_KEY
    if not api_key:
        raise HTTPException(status_code=501, detail="Missing API Key")

    now = time.time()
    cached = TTS_CACHE.get(text)
    if cached and now < cached["expires_at"]:
        total_ms = round((time.perf_counter() - tts_start) * 1000, 1)
        logger.info(f"voice_tts provider='cache' status=200 total_ms={total_ms} chars={len(text)}")
        return Response(content=cached["audio"], media_type="audio/mpeg")

    voice_id = (settings.ELEVEN_VOICE_ID or _resolve_conversationrelay_voice() or "").strip()
    if not voice_id:
        raise HTTPException(status_code=501, detail="Missing ElevenLabs voice_id")
    model_id = settings.ELEVEN_MODEL_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
    payload = {"text": text, "model_id": model_id, "voice_settings": {"stability": 0.4, "similarity_boost": 0.7}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=headers, json=payload)

    total_ms = round((time.perf_counter() - tts_start) * 1000, 1)
    logger.info(f"voice_tts provider='elevenlabs' status={r.status_code} total_ms={total_ms} chars={len(text)}")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="ElevenLabs Error")

    if len(TTS_CACHE) >= TTS_CACHE_MAX_ITEMS:
        oldest_key = min(TTS_CACHE.items(), key=lambda item: item[1]["expires_at"])[0]
        TTS_CACHE.pop(oldest_key, None)
    TTS_CACHE[text] = {
        "audio": r.content,
        "expires_at": now + TTS_CACHE_TTL_SECONDS,
    }

    return Response(content=r.content, media_type="audio/mpeg")
