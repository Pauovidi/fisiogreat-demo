import datetime as dt

class ServiceCatalog:
    durations = {
        "corte": 30, "corte mujer": 30, "corte hombre": 30,
        "corte + lavado": 45, "color": 90, "color raíz": 75, "color raiz": 75,
        "mechas": 90, "peinado": 30,
        "primera visita de fisioterapia": 60,
        "sesion de fisioterapia": 45,
        "sesión de fisioterapia": 45,
        "valoracion inicial": 60,
        "valoración inicial": 60,
        "consulta de seguimiento": 30,
    }

class BusinessRules:
    open_week = {
        0: ("10:00","20:00"), 1: ("10:00","20:00"), 2: ("10:00","20:00"),
        3: ("10:00","20:00"), 4: ("10:00","20:00"), 5: ("10:00","14:00"),
        6: None
    }
    buffer_min = 10
    window_days = 14
    min_lead_minutes = 120

def _parse_hhmm(hhmm: str) -> dt.time:
    h,m = map(int, hhmm.split(':')); return dt.time(hour=h, minute=m)

def _is_open(when: dt.datetime, rules: BusinessRules) -> bool:
    conf = rules.open_week.get(when.weekday())
    if not conf: return False
    start_t = _parse_hhmm(conf[0]); end_t = _parse_hhmm(conf[1])
    return start_t <= when.time() <= end_t

def propose_slots(preferred: dt.datetime, service_key: str, rules: BusinessRules):
    now = dt.datetime.now()
    if (preferred - now).total_seconds() < rules.min_lead_minutes*60:
        preferred = now + dt.timedelta(minutes=rules.min_lead_minutes)
    duration = ServiceCatalog.durations.get(service_key, 30)
    slots, start, end, step = [], preferred - dt.timedelta(hours=2), preferred + dt.timedelta(days=1), dt.timedelta(minutes=15)
    cursor = start
    while cursor <= end and len(slots) < 6:
        if _is_open(cursor, rules):
            slot_end = cursor + dt.timedelta(minutes=duration + rules.buffer_min)
            if _is_open(slot_end, rules):
                slots.append({"start": cursor, "end": slot_end, "service": service_key})
        cursor += step
    return slots
