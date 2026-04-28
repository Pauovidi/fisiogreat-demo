import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from ..config.settings import settings


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


async def _insert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(_url(table), headers=_headers(), json=payload)
        response.raise_for_status()
        rows = response.json()
        return rows[0] if isinstance(rows, list) and rows else payload


async def _patch(table: str, row_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.patch(
            f"{_url(table)}?id=eq.{row_id}",
            headers=_headers(),
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
        return await _insert("patients", payload)
    STORE.patients[payload["id"]] = payload
    return payload


async def upsert_patient_by_phone(*, clinic_id: str, phone: str, name: Optional[str] = None) -> Dict[str, Any]:
    for patient in STORE.patients.values():
        if patient.get("clinic_id") == clinic_id and patient.get("phone") == phone:
            if name:
                patient["name"] = name
            return patient
    if supabase_configured():
        # Supabase schema has a unique key on clinic_id+phone.
        payload = {"clinic_id": clinic_id, "phone": phone, "name": name}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _url("patients"),
                headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=representation"},
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if rows else payload
    return await create_patient(clinic_id=clinic_id, phone=phone, name=name)


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
        return await _insert("conversation_sessions", payload)
    STORE.sessions[payload["id"]] = payload
    return payload


async def update_conversation_session(session_id: str, **updates: Any) -> Dict[str, Any]:
    updates["updated_at"] = _now()
    if supabase_configured():
        return await _patch("conversation_sessions", session_id, updates)
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


async def create_reminder_job(**payload: Any) -> Dict[str, Any]:
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("status", "pending")
    payload.setdefault("created_at", _now())
    if supabase_configured():
        return await _insert("reminder_jobs", payload)
    STORE.reminder_jobs[payload["id"]] = payload
    return payload
