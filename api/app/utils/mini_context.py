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
            "consultation_reason": None,
            "contact_phone": None,
            "contact_email": None,
            "contact_channel_preference": None,
            "suggested_contact_phone": None,
            "pending_slot": None,
            "pending_reschedule_appointments": [],
            "pending_cancel_appointments": [],
            "selected_appointment_id": None,
            "selected_calendar_event_id": None,
            "selected_original_start_at": None,
            "selected_original_service_type": None,
            "patient_name": None,
            "patient_name_source": None,
            "offered_slots": [],
            "offered_offset": 0,
            "last_confirmed_slot": None,
            "last_confirmed_service": None,
            "last_confirmed_patient_name": None,
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

    def set_consultation_reason(self, key: str, consultation_reason: Optional[str]):
        entry = self._touch(key)
        entry["consultation_reason"] = consultation_reason

    def set_contact(
        self,
        key: str,
        *,
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
        contact_channel_preference: Optional[str] = None,
    ):
        entry = self._touch(key)
        if contact_phone:
            entry["contact_phone"] = contact_phone
        if contact_email:
            entry["contact_email"] = contact_email
        if contact_channel_preference:
            entry["contact_channel_preference"] = contact_channel_preference

    def set_suggested_contact_phone(self, key: str, contact_phone: Optional[str]):
        entry = self._touch(key)
        entry["suggested_contact_phone"] = contact_phone

    def set_pending_slot(self, key: str, slot: Optional[str]):
        entry = self._touch(key)
        entry["pending_slot"] = slot

    def set_pending_appointments(self, key: str, *, action: str, appointments: List[Dict[str, Any]]):
        entry = self._touch(key)
        field = "pending_cancel_appointments" if action == "cancel" else "pending_reschedule_appointments"
        entry[field] = list(appointments)
        if action == "cancel":
            entry["pending_reschedule_appointments"] = []
        else:
            entry["pending_cancel_appointments"] = []

    def set_selected_appointment(self, key: str, appointment: Optional[Dict[str, Any]]):
        entry = self._touch(key)
        if not appointment:
            entry["selected_appointment_id"] = None
            entry["selected_calendar_event_id"] = None
            entry["selected_original_start_at"] = None
            entry["selected_original_service_type"] = None
            return
        entry["selected_appointment_id"] = appointment.get("id")
        entry["selected_calendar_event_id"] = appointment.get("calendar_event_id")
        entry["selected_original_start_at"] = appointment.get("start_at")
        entry["selected_original_service_type"] = appointment.get("service_type")

    def set_patient_name(self, key: str, patient_name: Optional[str], source: Optional[str] = None):
        entry = self._touch(key)
        entry["patient_name"] = patient_name
        entry["patient_name_source"] = source

    def set_slots(self, key: str, slots: List[str]):
        entry = self._touch(key)
        entry["offered_slots"] = list(slots)
        entry["offered_offset"] = 0

    def set_last_confirmed_slot(
        self,
        key: str,
        slot: Optional[str],
        *,
        service: Optional[str] = None,
        patient_name: Optional[str] = None,
    ):
        entry = self._touch(key)
        entry["last_confirmed_slot"] = slot
        entry["last_confirmed_service"] = service if slot else None
        entry["last_confirmed_patient_name"] = patient_name if slot else None
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
        entry["last_confirmed_service"] = previous.get("last_confirmed_service")
        entry["last_confirmed_patient_name"] = previous.get("last_confirmed_patient_name")
        entry["last_confirmed_at"] = previous.get("last_confirmed_at")
        self._store[key] = entry

    def clear(self, key: str):
        self._store.pop(key, None)


CTX = MiniContext()
