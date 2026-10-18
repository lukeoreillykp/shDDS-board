from __future__ import annotations

import hmac
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_calendar import calendar
from supabase import Client, create_client

st.set_page_config(
    page_title="Warehouse Shift Hub",
    page_icon="🏭",
    layout="wide",
)

UK = ZoneInfo("Europe/London")
ROTATION_ANCHOR_MONDAY = date(2026, 10, 12)

BASE_COLOURS = {
    "Days": "Yellow",
    "Afters": "Red",
    "Nights": "Blue",
}

COLOUR_HEX = {
    "Yellow": "#E0B323",
    "Red": "#D64545",
    "Blue": "#2878C8",
}

SHIFT_ORDER = ["Days", "Afters", "Nights"]


# ------------------------------------------------------------
# SUPABASE
# ------------------------------------------------------------

def get_supabase() -> Client:
    if "supabase_client" not in st.session_state:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")

        if not url or not key:
            raise RuntimeError(
                "Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
                "to Streamlit secrets."
            )

        st.session_state.supabase_client = create_client(url, key)

    return st.session_state.supabase_client


def parse_dt(value) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UK)

    return dt.astimezone(UK)


# ------------------------------------------------------------
# SHIFT SCHEDULE AND TEAM ROTATION
# ------------------------------------------------------------

def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def team_colours_for_week(day: date) -> dict[str, str]:
    weeks = (
        week_monday(day) - ROTATION_ANCHOR_MONDAY
    ).days // 7

    colours = dict(BASE_COLOURS)

    for _ in range(weeks % 3):
        colours = {
            "Days": colours["Afters"],
            "Afters": colours["Nights"],
            "Nights": colours["Days"],
        }

    return colours


def make_shift(start_day: date, shift_name: str) -> dict:
    if shift_name == "Days":
        start_t = time(6, 0)
        end_t = (
            time(13, 0)
            if start_day.weekday() == 4
            else time(14, 0)
        )
        end_day = start_day

    elif shift_name == "Afters":
        start_t = (
            time(13, 0)
            if start_day.weekday() == 4
            else time(14, 0)
        )
        end_t = (
            time(20, 0)
            if start_day.weekday() == 4
            else time(22, 0)
        )
        end_day = start_day

    else:
        start_t = (
            time(20, 0)
            if start_day.weekday() == 4
            else time(22, 0)
        )
        end_t = (
            time(3, 0)
            if start_day.weekday() == 4
            else time(6, 0)
        )
        end_day = start_day + timedelta(days=1)

    start_dt = datetime.combine(start_day, start_t, tzinfo=UK)
    end_dt = datetime.combine(end_day, end_t, tzinfo=UK)
    colour = team_colours_for_week(start_day)[shift_name]

    return {
        "shift_date": start_day.isoformat(),
        "shift_name": shift_name,
        "start": start_dt,
        "end": end_dt,
        "team_colour": colour,
        "key": start_dt.isoformat(),
        "label": (
            f"{start_day.strftime('%a %d %b')} · "
            f"{shift_name} · {colour} team"
        ),
    }


def shifts_between(start_day: date, end_day: date) -> list[dict]:
    shifts = []
    current_day = start_day

    while current_day <= end_day:
        if current_day.weekday() < 5:
            for name in SHIFT_ORDER:
                shifts.append(make_shift(current_day, name))

        current_day += timedelta(days=1)

    return sorted(shifts, key=lambda s: s["start"])


def choose_default_shift(now: datetime) -> dict:
    shifts = shifts_between(
        now.date() - timedelta(days=14),
        now.date() + timedelta(days=14),
    )

    active = [
        shift for shift in shifts
        if shift["start"] <= now < shift["end"]
    ]

    if active:
        return active[0]

    previous = [
        shift for shift in shifts
        if shift["end"] <= now
    ]

    return previous[-1] if previous else shifts[0]


def adjacent_shifts(selected: dict) -> tuple[list[dict], int]:
    shifts = shifts_between(
        selected["start"].date() - timedelta(days=21),
        selected["start"].date() + timedelta(days=21),
    )

    index = next(
        (
            i for i, shift in enumerate(shifts)
            if shift["key"] == selected["key"]
        ),
        0,
    )

    return shifts, index


# ------------------------------------------------------------
# QUESTIONS AND SIDEBAR LINKS
# ------------------------------------------------------------

def load_questions(sb: Client) -> list[dict]:
    rows = (
        sb.table("shift_questions")
        .select("*")
        .order("sort_order")
        .execute()
        .data
        or []
    )

    for row in rows:
        row["rules"] = row.get("rules") or {}
        row["options"] = row.get("options") or []

    return rows


def load_navigation_links(sb: Client) -> list[dict]:
    try:
        return (
            sb.table("navigation_links")
            .select("id,label,url,sort_order,is_active")
            .order("sort_order")
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def valid_navigation_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


# ------------------------------------------------------------
# TRANSPORT EVENTS
# FIXED: EVENTS WITHOUT END TIMES ARE NOT DISCARDED
# ------------------------------------------------------------

def load_events(sb: Client) -> list[dict]:
    try:
        rows = (
            sb.table("events")
            .select("*")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        st.warning(f"Could not load calendar events: {exc}")
        return []

    result = []

    for event in rows:
        start_value = (
            event.get("start")
            or event.get("start_time")
            or event.get("starts_at")
        )

        end_value = (
            event.get("end")
            or event.get("end_time")
            or event.get("ends_at")
        )

        start_dt = parse_dt(start_value)
        end_dt = parse_dt(end_value)

        if not start_dt:
            continue

        # Keep Campeys and other events with NULL end values.
        # A missing end is assigned a one-hour display window.
        if not end_dt:
            end_dt = start_dt + timedelta(hours=1)

        if end_dt <= start_dt:
            continue

        event["_start"] = start_dt
        event["_end"] = end_dt
        result.append(event)

    return result


def overlaps(event: dict, shift: dict) -> bool:
    return (
        event["_start"] < shift["end"]
        and event["_end"] > shift["start"]
    )


def events_for_shift(events: list[dict], shift: dict) -> list[dict]:
    return [
        event for event in events
        if overlaps(event, shift)
    ]


def source_matches(event: dict, source: str) -> bool:
    fields = [
        event.get("source"),
        event.get("title"),
        event.get("load_header"),
        event.get("description"),
        event.get("category"),
        event.get("customer"),
        event.get("carrier"),
    ]

    text = " ".join(
        str(value)
        for value in fields
        if value is not None
    ).casefold()

    if source == "Bulk":
        return "bulk" in text

    if source == "Campey":
        return "campey" in text

    if source == "DHL":
        return "dhl" in text

    return False


def planned_counts(events: list[dict], shift: dict) -> dict[str, int]:
    relevant = events_for_shift(events, shift)

    return {
        "inbound_planned": sum(
            source_matches(event, "Bulk")
            for event in relevant
        ),
        "outbound_planned_campeys": sum(
            source_matches(event, "Campey")
            for event in relevant
        ),
        "outbound_planned_dhl": sum(
            source_matches(event, "DHL")
            for event in relevant
        ),
    }


def cil_is_planned(events: list[dict], shift: dict) -> bool:
    for event in events_for_shift(events, shift):
        title = str(event.get("title") or "").casefold()
        source = str(event.get("source") or "").casefold()

        if "cil" in title or "cil" in source:
            return True

    return False


# ------------------------------------------------------------
# SAVED SHIFT RECORDS
# ------------------------------------------------------------

def load_submissions(
    sb: Client,
    selected_start: datetime | None = None,
) -> dict[str, dict]:
    query = sb.table("shift_submissions").select("*")

    if selected_start is not None:
        query = (
            query.gte(
                "shift_start",
                (
                    selected_start - timedelta(days=14)
                ).isoformat(),
            )
            .lte(
                "shift_start",
                (
                    selected_start + timedelta(days=1)
                ).isoformat(),
            )
        )

    rows = query.execute().data or []

    return {
        str(row.get("shift_start")): row
        for row in rows
    }


def submission_for(
    submissions: dict[str, dict],
    shift: dict,
) -> dict | None:
    for key, row in submissions.items():
        dt = parse_dt(key)

        if dt and dt == shift["start"]:
            return row

    return submissions.get(shift["start"].isoformat())


# ------------------------------------------------------------
# ANSWER COLOURS
# ------------------------------------------------------------

def answer_colour(
    question: dict,
    value,
    planned: dict,
    answers: dict | None = None,
) -> str | None:
    answers = answers or {}
    rules = question.get("rules") or {}
    rule = rules.get("colour_rule", "none")

    if rule == "ips_positive_or_no_breakdowns":
        breakdowns = answers.get("break