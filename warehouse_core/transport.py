"""Transport event normalization, overlap, counts and generic pagination."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections.abc import Callable, Iterable
UK = ZoneInfo("Europe/London")

def parse_dt(value) -> datetime | None:
    if not value: return None
    if isinstance(value, datetime): dt = value
    else:
        try: dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError): return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=UK)
    return dt.astimezone(UK)

def normalize_events(rows: Iterable[dict]) -> list[dict]:
    result = []
    for raw in rows:
        event = dict(raw)
        start = parse_dt(event.get("start") or event.get("start_time") or event.get("starts_at"))
        end = parse_dt(event.get("end") or event.get("end_time") or event.get("ends_at"))
        if not start: continue
        if not end: end = start + timedelta(hours=1)
        if end <= start: continue
        event["_start"], event["_end"] = start, end
        result.append(event)
    return result

def overlaps(event: dict, shift: dict) -> bool:
    return event["_start"] < shift["end"] and event["_end"] > shift["start"]

def events_for_shift(events: list[dict], shift: dict) -> list[dict]:
    return [event for event in events if overlaps(event, shift)]

def source_matches(event: dict, source: str) -> bool:
    fields = [event.get(k) for k in ("source", "title", "load_header", "description", "category", "customer", "carrier")]
    text = " ".join(str(v) for v in fields if v is not None).casefold()
    return source.casefold() in text if source in ("Bulk", "Campey", "DHL") else False

def planned_counts(events: list[dict], shift: dict) -> dict[str, int]:
    relevant = events_for_shift(events, shift)
    return {"inbound_planned": sum(source_matches(e, "Bulk") for e in relevant),
            "outbound_planned_campeys": sum(source_matches(e, "Campey") for e in relevant),
            "outbound_planned_dhl": sum(source_matches(e, "DHL") for e in relevant)}

def fetch_all_pages(fetch_page: Callable[[int, int], list[dict]], page_size: int = 1000) -> list[dict]:
    """Fetch inclusive ranges until a short page is returned (e.g. Supabase range)."""
    if page_size < 1: raise ValueError("page_size must be positive")
    rows, start = [], 0
    while True:
        page = fetch_page(start, start + page_size - 1) or []
        rows.extend(page)
        if len(page) < page_size: return rows
        start += page_size
