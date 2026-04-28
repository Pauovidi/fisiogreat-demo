import time
from datetime import date
from typing import Any, Dict, List, Optional


class MiniContext:
    def __init__(self, ttl_minutes: int = 30):
        self._store: Dict[str, Dict[str, Any]] = {}
        self.ttl_seconds = ttl_minutes * 60

    def _fresh_entry(self) -> Dict[str, Any]:
        return {
            "expires_at": time.time() + self.ttl_seconds,
            "stage": "idle",
            "service": None,
            "date_pref": None,
            "time_pref": None,
            "offered_slots": [],
            "offered_offset": 0,
            "last_confirmed_slot": None,
            "last_confirmed_at": None,
        }

    def _ensure_entry(self, key: str) -> Dict[str, Any]:
        entry = self.get(key)
        if entry is None:
            entry = self._fresh_entry()
            self._store[key] = entry
        return entry

    def _touch(self, key: str) -> Dict[str, Any]:
        entry = self._ensure_entry(key)
        entry["expires_at"] = time.time() + self.ttl_seconds
        return entry

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(key)
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            self._store.pop(key, None)
            return None
        return entry

    def get_stage(self, key: str) -> str:
        ctx = self.get(key)
        return ctx["stage"] if ctx else "idle"

    def set_stage(self, key: str, stage: str):
        entry = self._touch(key)
        entry["stage"] = stage

    def set_service(self, key: str, service: Optional[str]):
        entry = self._touch(key)
        entry["service"] = service

    def set_date(self, key: str, date_pref: Optional[date]):
        entry = self._touch(key)
        entry["date_pref"] = date_pref

    def set_time_pref(self, key: str, time_pref: Optional[str]):
        entry = self._touch(key)
        entry["time_pref"] = time_pref

    def set_slots(self, key: str, slots: List[str]):
        entry = self._touch(key)
        entry["offered_slots"] = list(slots)
        entry["offered_offset"] = 0

    def set_last_confirmed_slot(self, key: str, slot: Optional[str]):
        entry = self._touch(key)
        entry["last_confirmed_slot"] = slot
        entry["last_confirmed_at"] = time.time() if slot else None

    def next_slots(self, key: str, n: int = 3) -> List[str]:
        entry = self.get(key)
        if not entry:
            return []
        start = entry["offered_offset"]
        batch = entry["offered_slots"][start : start + n]
        entry["offered_offset"] = start + len(batch)
        entry["expires_at"] = time.time() + self.ttl_seconds
        return batch

    def clear_flow(self, key: str):
        previous = self.get(key) or {}
        entry = self._fresh_entry()
        entry["last_confirmed_slot"] = previous.get("last_confirmed_slot")
        entry["last_confirmed_at"] = previous.get("last_confirmed_at")
        self._store[key] = entry

    def clear(self, key: str):
        self._store.pop(key, None)


CTX = MiniContext()
