import asyncio
import datetime as dt

import httpx

from app.config.settings import settings
from app.services import supabase_repo
from app.services.supabase_repo import STORE


class _Response:
    def __init__(self, status_code=409, text="conflict"):
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("POST", "https://example.test")


def test_get_or_create_patient_returns_existing_local_patient():
    STORE.reset()
    existing = asyncio.run(
        supabase_repo.create_patient(
            clinic_id="fisiogreat-demo",
            phone="+34600000001",
            name="Ada",
        )
    )

    found = asyncio.run(
        supabase_repo.get_or_create_patient(
            clinic_id="fisiogreat-demo",
            phone="+34600000001",
            name="Ada Updated",
        )
    )

    assert found["id"] == existing["id"]
    assert len(STORE.patients) == 1
    assert found["name"] == "Ada Updated"


def test_get_or_create_patient_recovers_after_supabase_409(monkeypatch):
    STORE.reset()
    recovered = {"id": "patient-1", "clinic_id": "fisiogreat-demo", "phone": "+34600000001"}
    select_calls = []
    insert_calls = []

    monkeypatch.setattr(settings, "USE_REAL_SUPABASE", True)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-key")

    async def fake_select(table, params, *, limit=1):
        select_calls.append((table, params, limit))
        return [] if len(select_calls) == 1 else [recovered]

    async def fake_insert(table, payload, *, params=None, headers=None):
        insert_calls.append((table, payload, params, headers))
        response = _Response()
        raise httpx.HTTPStatusError("409 Conflict", request=response.request, response=response)

    monkeypatch.setattr(supabase_repo, "_select", fake_select)
    monkeypatch.setattr(supabase_repo, "_insert", fake_insert)

    patient = asyncio.run(
        supabase_repo.get_or_create_patient(
            clinic_id="fisiogreat-demo",
            phone="+34600000001",
        )
    )

    assert patient == recovered
    assert insert_calls[0][2] == {"on_conflict": "clinic_id,phone"}
    assert select_calls[0][1]["phone"] == "eq.+34600000001"


def test_update_conversation_session_upserts_phone_id_without_patch(monkeypatch):
    captured = {}

    monkeypatch.setattr(settings, "USE_REAL_SUPABASE", True)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-key")

    async def fake_insert(table, payload, *, params=None, headers=None):
        captured["table"] = table
        captured["payload"] = payload
        captured["params"] = params
        return payload

    monkeypatch.setattr(supabase_repo, "_insert", fake_insert)

    session = asyncio.run(
        supabase_repo.update_conversation_session(
            "+34600000001",
            channel="whatsapp",
            external_user_id="+34600000001",
            stage="idle",
            reset_reason="reset_command",
        )
    )

    assert captured["table"] == "conversation_sessions"
    assert captured["params"] == {"on_conflict": "id"}
    assert captured["payload"]["id"] == "+34600000001"
    assert captured["payload"]["state"] == {"stage": "idle", "reset_reason": "reset_command"}
    assert session["external_user_id"] == "+34600000001"


def test_booking_lock_release_patches_only_valid_status(monkeypatch):
    STORE.reset()
    patched = {}
    start = dt.datetime(2026, 5, 4, 10, 0)
    end = dt.datetime(2026, 5, 4, 10, 45)

    asyncio.run(
        supabase_repo.acquire_booking_lock(
            clinic_id="fisiogreat-demo",
            resource_id="calendar-real",
            start_at=start,
            end_at=end,
        )
    )

    monkeypatch.setattr(settings, "USE_REAL_SUPABASE", True)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "test-key")

    async def fake_patch(table, row_id, payload):
        patched["table"] = table
        patched["row_id"] = row_id
        patched["payload"] = payload
        return payload

    monkeypatch.setattr(supabase_repo, "_patch", fake_patch)

    asyncio.run(
        supabase_repo.release_booking_lock(
            clinic_id="fisiogreat-demo",
            resource_id="calendar-real",
            start_at=start,
            end_at=end,
        )
    )

    assert patched["table"] == "booking_locks"
    assert patched["payload"] == {"status": "released"}
