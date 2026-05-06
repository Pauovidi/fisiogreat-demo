import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config.settings import settings
from ..utils.logger import logger


@dataclass
class InMemorySupabaseStore:
    patients: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    appointments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sessions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    locks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reminder_jobs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def reset(self):
        self.patients.clear()
        self.appointments.clear()
        self.sessions.clear()
        self.locks.clear()
        self.reminder_jobs.clear()


STORE = InMemorySupabaseStore()


def supabase_configured() -> bool:
    return bool(settings.USE_REAL_SUPABASE and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)


def _headers() -> Dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table: str) -> str:
    return f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


def _response_excerpt(response: httpx.Response) -> str:
    try:
        return response.text[:1000]
    except Exception:
        return "<unavailable>"


async def _select(table: str, params: Dict[str, str], *, limit: int = 1) -> List[Dict[str, Any]]:
    query = {"select": "*", "limit": str(limit), **params}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_url(table), headers=_headers(), params=query)
        response.raise_for_status()
        rows = response.json()
        return rows if isinstance(rows, list) else []


async def _insert(table: str, payload: Dict[str, Any], *, params: Optional[Dict[str, str]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(_url(table), headers=headers or _headers(), params=params, json=payload)
        response.raise_for_status()
        rows = response.json()
        return rows[0] if isinstance(rows, list) and rows else payload


async def _patch(table: str, row_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _patch_filter(table, {"id": f"eq.{row_id}"}, payload)


async def _patch_filter(table: str, filters: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.patch(
            _url(table),
            headers=_headers(),
            params=filters,
            json=payload,
        )
        response.raise_for_status()
        rows = response.json()
        return rows[0] if isinstance(rows, list) and rows else payload


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def create_patient(*, clinic_id: str, phone: str, name: Optional[str] = None) -> Dict[str, Any]:
    payload = {"id": str(uuid.uuid4()), "clinic_id": clinic_id, "phone": phone, "name": name, "created_at": _now()}
    if supabase_configured():
        return await get_or_create_patient(clinic_id=clinic_id, phone=phone, name=name)
    STORE.patients[payload["id"]] = payload
    return payload


async def get_or_create_patient(*, clinic_id: str, phone: str, name: Optional[str] = None) -> Dict[str, Any]:
    logger.info("supabase_patient_get_or_create_start clinic_id=%s phone_present=%s", clinic_id, bool(phone))
    for patient in STORE.patients.values():
        if patient.get("clinic_id") == clinic_id and patient.get("phone") == phone:
            if name:
                patient["name"] = name
            logger.info("supabase_patient_existing patient_id=%s source=memory", patient.get("id"))
            return patient
    if supabase_configured():
        filters = {"clinic_id": f"eq.{clinic_id}", "phone": f"eq.{phone}"}
        existing = await _select("patients", filters)
        if existing:
            patient = existing[0]
            logger.info("supabase_patient_existing patient_id=%s source=supabase", patient.get("id"))
            if name and patient.get("name") != name:
                try:
                    patient = await _patch("patients", patient["id"], {"name": name, "updated_at": _now()})
                except Exception as exc:
                    logger.warning("supabase_patient_name_update_failed patient_id=%s error=%r", patient.get("id"), exc)
            return patient

        payload = {"clinic_id": clinic_id, "phone": phone, "name": name}
        try:
            patient = await _insert(
                "patients",
                payload,
                params={"on_conflict": "clinic_id,phone"},
                headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            )
            logger.info("supabase_patient_created patient_id=%s", patient.get("id"))
            return patient
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            recovered = await _select("patients", filters)
            if recovered:
                logger.info("supabase_patient_conflict_recovered patient_id=%s", recovered[0].get("id"))
                return recovered[0]
            raise
    return await create_patient(clinic_id=clinic_id, phone=phone, name=name)


async def upsert_patient_by_phone(*, clinic_id: str, phone: str, name: Optional[str] = None) -> Dict[str, Any]:
    return await get_or_create_patient(clinic_id=clinic_id, phone=phone, name=name)


async def clear_patient_name_by_phone(*, clinic_id: str, phone: str) -> Optional[Dict[str, Any]]:
    for patient in STORE.patients.values():
        if patient.get("clinic_id") == clinic_id and patient.get("phone") == phone:
            patient["name"] = None
            patient["updated_at"] = _now()
            logger.info("supabase_patient_name_cleared patient_id=%s source=memory", patient.get("id"))
            return patient

    if supabase_configured():
        filters = {"clinic_id": f"eq.{clinic_id}", "phone": f"eq.{phone}"}
        existing = await _select("patients", filters)
        if not existing:
            logger.info("supabase_patient_name_clear_skipped reason=not_found phone_present=%s", bool(phone))
            return None
        patient = await _patch("patients", existing[0]["id"], {"name": None, "updated_at": _now()})
        logger.info("supabase_patient_name_cleared patient_id=%s source=supabase", patient.get("id"))
        return patient

    logger.info("supabase_patient_name_clear_skipped reason=not_found phone_present=%s", bool(phone))
    return None


async def create_appointment(**payload: Any) -> Dict[str, Any]:
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("created_at", _now())
    payload.setdefault("updated_at", _now())
    if supabase_configured():
        return await _insert("appointments", payload)
    STORE.appointments[payload["id"]] = payload
    return payload


async def update_appointment(appointment_id: str, **updates: Any) -> Dict[str, Any]:
    updates["updated_at"] = _now()
    if supabase_configured():
        return await _patch("appointments", appointment_id, updates)
    appointment = STORE.appointments[appointment_id]
    appointment.update(updates)
    return appointment


async def cancel_appointment(appointment_id: str) -> Dict[str, Any]:
    return await update_appointment(appointment_id, status="cancelled", cancelled_at=_now())


async def create_conversation_session(**payload: Any) -> Dict[str, Any]:
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("created_at", _now())
    payload.setdefault("updated_at", _now())
    if supabase_configured():
        return await _insert(
            "conversation_sessions",
            payload,
            params={"on_conflict": "id"},
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
        )
    STORE.sessions[payload["id"]] = payload
    return payload


async def update_conversation_session(session_id: str, **updates: Any) -> Dict[str, Any]:
    updates["updated_at"] = _now()
    if supabase_configured():
        known_columns = {
            "clinic_id",
            "channel",
            "external_user_id",
            "state",
            "emergency_detected",
            "emergency_match",
            "updated_at",
        }
        payload = {"id": session_id}
        payload.update({key: value for key, value in updates.items() if key in known_columns})
        state_updates = {key: value for key, value in updates.items() if key not in known_columns}
        if state_updates:
            state = dict(payload.get("state") or {})
            state.update(state_updates)
            payload["state"] = state
        try:
            session = await _insert(
                "conversation_sessions",
                payload,
                params={"on_conflict": "id"},
                headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
            )
            logger.info("reset_session_success session_id=%s", session_id)
            return session
        except Exception as exc:
            body = _response_excerpt(exc.response) if isinstance(exc, httpx.HTTPStatusError) else repr(exc)
            logger.warning("reset_session_failure session_id=%s response_body=%r", session_id, body)
            raise
    session = STORE.sessions.setdefault(session_id, {"id": session_id})
    session.update(updates)
    return session


def _lock_key(clinic_id: str, resource_id: str, start_at: dt.datetime, end_at: dt.datetime) -> str:
    return f"{clinic_id}|{resource_id}|{start_at.isoformat()}|{end_at.isoformat()}"


async def acquire_booking_lock(*, clinic_id: str, resource_id: str, start_at: dt.datetime, end_at: dt.datetime) -> bool:
    key = _lock_key(clinic_id, resource_id, start_at, end_at)
    existing = STORE.locks.get(key)
    if existing and existing.get("status") == "held":
        return False
    STORE.locks[key] = {
        "id": str(uuid.uuid4()),
        "clinic_id": clinic_id,
        "resource_id": resource_id,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "status": "held",
        "created_at": _now(),
    }
    if supabase_configured():
        try:
            await _insert("booking_locks", STORE.locks[key])
        except httpx.HTTPStatusError:
            STORE.locks.pop(key, None)
            return False
    return True


async def release_booking_lock(*, clinic_id: str, resource_id: str, start_at: dt.datetime, end_at: dt.datetime) -> None:
    key = _lock_key(clinic_id, resource_id, start_at, end_at)
    lock = STORE.locks.get(key)
    if lock:
        lock["status"] = "released"
        lock["released_at"] = _now()
        if supabase_configured():
            try:
                await _patch("booking_locks", lock["id"], {"status": "released"})
                logger.info("booking_lock_release_success lock_id=%s status=released", lock.get("id"))
            except Exception as exc:
                body = _response_excerpt(exc.response) if isinstance(exc, httpx.HTTPStatusError) else repr(exc)
                logger.warning("booking_lock_release_failure lock_id=%s response_body=%r", lock.get("id"), body)


async def create_reminder_job(**payload: Any) -> Dict[str, Any]:
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("status", "pending")
    payload.setdefault("created_at", _now())
    if supabase_configured():
        return await _insert("reminder_jobs", payload)
    STORE.reminder_jobs[payload["id"]] = payload
    return payload
