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
        end