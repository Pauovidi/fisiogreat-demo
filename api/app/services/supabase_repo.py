import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

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

BLOCKING_APPOINTMENT_STATUSES = {"confirmed", "scheduled", "pending"}
BLOCKING_LOCK_STATUSES = {"held"}


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


async def get_appointment(appointment_id: str) -> Optional[Dict[str, Any]]:
    if appointment_id in STORE.appointments:
        return STORE.appointments[appointment_id]
    if supabase_configured():
        rows = await _select("appointments", {"id": f"eq.{appointment_id}"})
        return rows[0] if rows else None
    return None


def list_internal_busy_intervals(
    *,
    clinic_id: str,
    resource_id: str,
    start_at: dt.datetime,
    end_at: dt.datetime,
    ignore_calendar_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    intervals: List[Dict[str, str]] = []
    ignored_ranges: List[Tuple[dt.datetime, dt.datetime]] = []
    appointment_busy_count = 0
    booking_locks_busy_count = 0

    for appointment in _list_busy_appointment_rows(
        clinic_id=clinic_id,
        start_at=start_at,
        end_at=end_at,
    ):
        appt_start = _parse_datetime(appointment.get("start_at"))
        appt_end = _parse_datetime(appointment.get("end_at"))
        if not appt_start or not appt_end or not _overlaps(start_at, end_at, appt_start, appt_end):
            continue
        if ignore_calendar_event_id and appointment.get("calendar_event_id") == ignore_calendar_event_id:
            ignored_ranges.append((appt_start, appt_end))
            continue
        appointment_busy_count += 1
        intervals.append({"start": appt_start.isoformat(), "end": appt_end.isoformat(), "source": "appointment"})

    for lock in _list_busy_lock_rows(
        clinic_id=clinic_id,
        resource_id=resource_id,
        start_at=start_at,
        end_at=end_at,
    ):
        if not _is_active_lock(lock):
            continue
        lock_start = _parse_datetime(lock.get("start_at"))
        lock_end = _parse_datetime(lock.get("end_at"))
        if not lock_start or not lock_end or not _overlaps(start_at, end_at, lock_start, lock_end):
            continue
        if any(_same_interval(lock_start, lock_end, ignored_start, ignored_end) for ignored_start, ignored_end in ignored_ranges):
            continue
        booking_locks_busy_count += 1
        intervals.append({"start": lock_start.isoformat(), "end": lock_end.isoformat(), "source": "booking_lock"})

    return {
        "intervals": intervals,
        "appointment_busy_count": appointment_busy_count,
        "booking_locks_busy_count": booking_locks_busy_count,
    }


def _list_busy_appointment_rows(
    *,
    clinic_id: str,
    start_at: dt.datetime,
    end_at: dt.datetime,
) -> List[Dict[str, Any]]:
    if supabase_configured():
        return _select_sync(
            "appointments",
            {
                "clinic_id": f"eq.{clinic_id}",
                "status": f"in.({','.join(sorted(BLOCKING_APPOINTMENT_STATUSES))})",
                "start_at": f"lt.{end_at.isoformat()}",
                "end_at": f"gt.{start_at.isoformat()}",
                "order": "start_at.asc",
            },
            limit=200,
        )
    return [
        appointment
        for appointment in STORE.appointments.values()
        if appointment.get("clinic_id") == clinic_id
        and appointment.get("status") in BLOCKING_APPOINTMENT_STATUSES
    ]


def _list_busy_lock_rows(
    *,
    clinic_id: str,
    resource_id: str,
    start_at: dt.datetime,
    end_at: dt.datetime,
) -> List[Dict[str, Any]]:
    if supabase_configured():
        return _select_sync(
            "booking_locks",
            {
                "clinic_id": f"eq.{clinic_id}",
                "resource_id": f"eq.{resource_id}",
                "status": f"in.({','.join(sorted(BLOCKING_LOCK_STATUSES))})",
                "start_at": f"lt.{end_at.isoformat()}",
                "end_at": f"gt.{start_at.isoformat()}",
                "order": "start_at.asc",
            },
            limit=200,
        )
    return [
        lock
        for lock in STORE.locks.values()
        if lock.get("clinic_id") == clinic_id
        and lock.get("resource_id") == resource_id
    ]


def _select_sync(table: str, params: Dict[str, str], *, limit: int = 1) -> List[Dict[str, Any]]:
    query = {"select": "*", "limit": str(limit), **params}
    with httpx.Client(timeout=10.0) as client:
        response = client.get(_url(table), headers=_headers(), params=query)
        response.raise_for_status()
        rows = response.json()
        return rows if isinstance(rows, list) else []


def _is_active_lock(lock: Dict[str, Any]) -> bool:
    if lock.get("status") not in BLOCKING_LOCK_STATUSES:
        return False
    expires_at = _parse_datetime(lock.get("expires_at"))
    if expires_at and _as_aware(expires_at) <= _as_aware(dt.datetime.now()):
        return False
    return True


def _overlaps(
    left_start: dt.datetime,
    left_end: dt.datetime,
    right_start: dt.datetime,
    right_end: dt.datetime,
) -> bool:
    return _as_aware(left_start) < _as_aware(right_end) and _as_aware(right_start) < _as_aware(left_end)


def _same_interval(
    left_start: dt.datetime,
    left_end: dt.datetime,
    right_start: dt.datetime,
    right_end: dt.datetime,
) -> bool:
    return _as_aware(left_start) == _as_aware(right_start) and _as_aware(left_end) == _as_aware(right_end)


def _as_aware(value: dt.datetime) -> dt.datetime:
    zone = ZoneInfo(settings.GOOGLE_CALENDAR_TIMEZONE)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


async def list_future_appointments_for_patient(
    *,
    clinic_id: str,
    patient_key: str,
    now: Optional[dt.datetime] = None,
    statuses: Sequence[str] = ("confirmed", "scheduled"),
) -> List[Dict[str, Any]]:
    now = now or dt.datetime.now()
    patient_ids = {
        patient.get("id")
        for patient in STORE.patients.values()
        if patient.get("clinic_id") == clinic_id and patient.get("phone") == patient_key
    }
    memory_patient_names = {
        patient.get("id"): patient.get("name")
        for patient in STORE.patients.values()
        if patient.get("clinic_id") == clinic_id and patient.get("phone") == patient_key
    }

    if supabase_configured():
        rows: List[Dict[str, Any]] = []
        filters = {
            "clinic_id": f"eq.{clinic_id}",
            "external_user_id": f"eq.{patient_key}",
            "status": f"in.({','.join(statuses)})",
            "start_at": f"gte.{now.isoformat()}",
            "order": "start_at.asc",
        }
        rows.extend(await _select("appointments", filters, limit=100))
        patients = await _select("patients", {"clinic_id": f"eq.{clinic_id}", "phone": f"eq.{patient_key}"}, limit=20)
        patient_names = {patient.get("id"): patient.get("name") for patient in patients}
        for patient in patients:
            patient_id = patient.get("id")
            if not patient_id:
                continue
            patient_rows = await _select(
                "appointments",
                {
                    "clinic_id": f"eq.{clinic_id}",
                    "patient_id": f"eq.{patient_id}",
                    "status": f"in.({','.join(statuses)})",
                    "start_at": f"gte.{now.isoformat()}",
                    "order": "start_at.asc",
                },
                limit=100,
            )
            rows.extend(patient_rows)
        deduped = {row.get("id"): row for row in rows if row.get("id")}
        enriched = [
            _with_patient_name(row, patient_names.get(row.get("patient_id")))
            for row in deduped.values()
        ]
        return sorted(enriched, key=lambda item: item.get("start_at") or "")

    appointments = []
    for appointment in STORE.appointments.values():
        if appointment.get("clinic_id") != clinic_id:
            continue
        if appointment.get("status") not in statuses:
            continue
        if appointment.get("external_user_id") != patient_key and appointment.get("patient_id") not in patient_ids:
            continue
        start_at = _parse_datetime(appointment.get("start_at"))
        if not start_at:
            continue
        comparable_now = _coerce_now_for(start_at, now)
        if start_at >= comparable_now:
            appointments.append(_with_patient_name(appointment, memory_patient_names.get(appointment.get("patient_id"))))
    return sorted(appointments, key=lambda item: item.get("start_at") or "")


def _with_patient_name(appointment: Dict[str, Any], patient_name: Optional[str]) -> Dict[str, Any]:
    metadata = appointment.get("metadata") or {}
    if metadata.get("patient_name") or not patient_name:
        return appointment
    enriched = dict(appointment)
    enriched["patient_name"] = patient_name
    return enriched


def _parse_datetime(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _coerce_now_for(value: dt.datetime, now: dt.datetime) -> dt.datetime:
    if value.tzinfo and not now.tzinfo:
        return now.replace(tzinfo=dt.timezone.utc)
    if now.tzinfo and not value.tzinfo:
        return now.replace(tzinfo=None)
    return now


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
