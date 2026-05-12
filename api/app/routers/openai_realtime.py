from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from ..config.settings import settings
from ..services.openai_realtime import (
    RealtimeToolContext,
    book_appointment,
    build_realtime_session_config,
    cancel_appointment,
    collect_consultation_reason,
    emergency_protocol,
    get_available_slots,
    get_services,
    list_future_appointments,
    reschedule_appointment,
    save_contact,
)


router = APIRouter(tags=["openai-realtime-v2"])


@router.get("/__health/openai-realtime-v2", include_in_schema=False)
def openai_realtime_v2_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "enabled": settings.OPENAI_REALTIME_ENABLED,
        "shadow_mode": settings.OPENAI_REALTIME_SHADOW_MODE,
        "write_enabled": settings.OPENAI_REALTIME_WRITE_ENABLED,
        "transport": settings.OPENAI_REALTIME_TRANSPORT,
        "model": settings.OPENAI_REALTIME_MODEL,
        "instructions_version": settings.OPENAI_REALTIME_INSTRUCTIONS_VERSION,
    }


@router.post("/webhook/voice/openai-realtime-v2")
async def openai_realtime_v2_webhook(request: Request) -> Dict[str, Any]:
    payload = await _safe_json(request)
    event_type = payload.get("type")
    call_id = ((payload.get("data") or {}).get("call_id") or payload.get("call_id") or "openai-realtime-v2")
    call_phone = _extract_sip_phone(payload)
    session_config = build_realtime_session_config()
    return {
        "ok": True,
        "mode": "shadow" if settings.OPENAI_REALTIME_SHADOW_MODE else "active",
        "enabled": settings.OPENAI_REALTIME_ENABLED,
        "write_enabled": settings.OPENAI_REALTIME_WRITE_ENABLED,
        "event_type": event_type,
        "call_id": call_id,
        "call_phone_present": bool(call_phone),
        "recommended_action": "manual_accept_call_when_ready" if event_type == "realtime.call.incoming" else "monitor_or_ignore",
        "session_config": session_config,
    }


@router.post("/webhook/voice/openai-realtime-v2/tools/{tool_name}")
async def openai_realtime_v2_tool(tool_name: str, request: Request) -> Dict[str, Any]:
    payload = await _safe_json(request)
    context = RealtimeToolContext(
        session_id=str(payload.get("session_id") or payload.get("call_id") or "openai-realtime-v2-test"),
        call_phone=payload.get("call_phone"),
        write_enabled=payload.get("write_enabled"),
    )
    args = payload.get("arguments") or payload
    if tool_name == "get_services":
        return get_services()
    if tool_name == "collect_consultation_reason":
        return collect_consultation_reason(context, str(args.get("reason") or ""))
    if tool_name == "get_available_slots":
        return get_available_slots(
            context,
            service=str(args.get("service") or ""),
            date_text=str(args.get("date_text") or ""),
            today=args.get("today"),
        )
    if tool_name == "book_appointment":
        return await book_appointment(
            context,
            service=str(args.get("service") or ""),
            patient_name=str(args.get("patient_name") or ""),
            start_at=args.get("start_at"),
            end_at=args.get("end_at"),
            consultation_reason=args.get("consultation_reason"),
            contact_phone=args.get("contact_phone"),
            contact_email=args.get("contact_email"),
        )
    if tool_name == "list_future_appointments":
        return await list_future_appointments(context)
    if tool_name == "reschedule_appointment":
        return await reschedule_appointment(
            context,
            appointment_id=str(args.get("appointment_id") or ""),
            new_start_at=args.get("new_start_at"),
            new_end_at=args.get("new_end_at"),
        )
    if tool_name == "cancel_appointment":
        return await cancel_appointment(context, appointment_id=str(args.get("appointment_id") or ""))
    if tool_name == "save_contact":
        return save_contact(
            context,
            str(args.get("contact_text") or ""),
            call_phone=args.get("call_phone"),
        )
    if tool_name == "emergency_protocol":
        return emergency_protocol(context, str(args.get("user_text") or ""))
    return {"ok": False, "reason": "unknown_tool", "tool_name": tool_name}


async def _safe_json(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_sip_phone(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") or {}
    for header in data.get("sip_headers") or []:
        if str(header.get("name") or "").lower() == "from":
            value = str(header.get("value") or "")
            if "+" in value:
                return value.split("+", 1)[1].split("@", 1)[0]
    return None
