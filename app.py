Python
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import Client, create_client

st.set_page_config(page_title="Warehouse Shift Hub", page_icon="🏭", layout="wide")

UK = ZoneInfo("Europe/London")
ROTATION_ANCHOR_MONDAY = date(2026, 10, 12)
BASE_COLOURS = {"Days": "Yellow", "Afters": "Red", "Nights": "Blue"}
COLOUR_HEX = {"Yellow": "#E0B323", "Red": "#D64545", "Blue": "#2878C8"}
SHIFT_ORDER = ["Days", "Afters", "Nights"]
SHIFT_LABELS = {"Days": "Days", "Afters": "Afters", "Nights": "Nights"}

DEFAULT_QUESTIONS = [
    {"question_key": "safety_trigger", "label": "Safety trigger", "section": "Safety & quality", "input_type": "choice", "rules": {"colour_rule": "severity_lmh"}, "options": ["L", "M", "H"], "sort_order": 10, "is_active": True, "is_system": True},
    {"question_key": "near_miss", "label": "Near miss / unsafe condition", "section": "Safety & quality", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 20, "is_active": True, "is_system": True},
    {"question_key": "sbos", "label": "SBOs", "section": "Safety & quality", "input_type": "number", "rules": {"colour_rule": "positive_green"}, "options": [], "sort_order": 30, "is_active": True, "is_system": True},
    {"question_key": "quality_trigger", "label": "Quality trigger", "section": "Safety & quality", "input_type": "choice", "rules": {"colour_rule": "severity_lmh"}, "options": ["L", "M", "H"], "sort_order": 40, "is_active": True, "is_system": True},
    {"question_key": "qbos", "label": "QBOs", "section": "Safety & quality", "input_type": "number", "rules": {"colour_rule": "positive_green"}, "options": [], "sort_order": 50, "is_active": True, "is_system": True},
    {"question_key": "quality_holds", "label": "Quality holds", "section": "Safety & quality", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 60, "is_active": True, "is_system": True},
    {"question_key": "six_s_tour", "label": "6S tour", "section": "Operational checks", "input_type": "number", "rules": {"colour_rule": "positive_green"}, "options": [], "sort_order": 70, "is_active": True, "is_system": True},
    {"question_key": "defects_found", "label": "Defects found", "section": "Operational checks", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 80, "is_active": True, "is_system": True},
    {"question_key": "defects_fixed", "label": "Defects fixed", "section": "Operational checks", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 90, "is_active": True, "is_system": True},
    {"question_key": "breakdowns", "label": "Breakdowns", "section": "Operational checks", "input_type": "number", "rules": {"colour_rule": "zero_green"}, "options": [], "sort_order": 95, "is_active": True, "is_system": True},
    {"question_key": "ips_completed", "label": "IPS completed", "section": "Operational checks", "input_type": "number", "rules": {"colour_rule": "ips_positive_or_no_breakdowns"}, "options": [], "sort_order": 97, "is_active": True, "is_system": True},
    {"question_key": "cil_completion", "label": "CIL completion", "section": "Operational checks", "input_type": "percentage", "rules": {"colour_rule": "hundred_green"}, "options": [], "sort_order": 100, "is_active": True, "is_system": True},
    {"question_key": "inbound_planned", "label": "Inbound planned", "section": "Transport", "input_type": "auto", "rules": {"colour_rule": "none", "auto_source": "Bulk"}, "options": [], "sort_order": 110, "is_active": True, "is_system": True},
    {"question_key": "inbound_actual", "label": "Inbound actual", "section": "Transport", "input_type": "number", "rules": {"colour_rule": "matches_inbound_planned"}, "options": [], "sort_order": 120, "is_active": True, "is_system": True},
    {"question_key": "outbound_planned_campeys", "label": "Outbound planned — Campeys", "section": "Transport", "input_type": "auto", "rules": {"colour_rule": "none", "auto_source": "Campey"}, "options": [], "sort_order": 130, "is_active": True, "is_system": True},
    {"question_key": "outbound_actual_campeys", "label": "Outbound actual — Campeys", "section": "Transport", "input_type": "number", "rules": {"colour_rule": "matches_campey_planned"}, "options": [], "sort_order": 140, "is_active": True, "is_system": True},
    {"question_key": "outbound_planned_dhl", "label": "Outbound planned — DHL", "section": "Transport", "input_type": "auto", "rules": {"colour_rule": "none", "auto_source": "DHL"}, "options": [], "sort_order": 150, "is_active": True, "is_system": True},
    {"question_key": "outbound_actual_dhl", "label": "Outbound actual — DHL", "section": "Transport", "input_type": "number", "rules": {"colour_rule": "matches_dhl_planned"}, "options": [], "sort_order": 160, "is_active": True, "is_system": True},
    {"question_key": "bom_changes", "label": "BOM changes", "section": "Handover notes", "input_type": "text", "rules": {"colour_rule": "none"}, "options": [], "sort_order": 170, "is_active": True, "is_system": True},
    {"question_key": "priority_loading", "label": "Priority loading", "section": "Handover notes", "input_type": "text", "rules": {"colour_rule": "none"}, "options": [], "sort_order": 180, "is_active": True, "is_system": True},
    {"question_key": "staff_on_shift", "label": "Staff on shift", "section": "Handover notes", "input_type": "number", "rules": {"colour_rule": "staff_under_three_red", "min": 1, "max": 6}, "options": [], "sort_order": 190, "is_active": True, "is_system": True},
]


def get_supabase() -> Client:
    if "supabase_client" not in st.session_state:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to Streamlit secrets. "
                "See README.md."
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
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UK)
    return dt.astimezone(UK)


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def team_colours_for_week(day: date) -> dict[str, str]:
    weeks = (week_monday(day) - ROTATION_ANCHOR_MONDAY).days // 7
    shift_colours = dict(BASE_COLOURS)
    for _ in range(weeks % 3):
        shift_colours = {
            "Days": shift_colours["Afters"],
            "Afters": shift_colours["Nights"],
            "Nights": shift_colours["Days"],
        }
    return shift_colours


def make_shift(start_day: date, shift_name: str) -> dict:
    if shift_name == "Days":
        start_t = time(6, 0)
        end_t = time(13, 0) if start_day.weekday() == 4 else time(14, 0)
        end_day = start_day
    elif shift_name == "Afters":
        start_t = time(13, 0) if start_day.weekday() == 4 else time(14, 0)
        end_t = time(20, 0) if start_day.weekday() == 4 else time(22, 0)
        end_day = start_day
    else:
        start_t = time(20, 0) if start_day.weekday() == 4 else time(22, 0)
        end_t = time(3, 0) if start_day.weekday() == 4 else time(6, 0)
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
        "label": f"{start_day.strftime('%a %d %b')} · {shift_name} · {colour} team",
    }


def shifts_between(start_day: date, end_day: date) -> list[dict]:
    shifts = []
    d = start_day
    while d <= end_day:
        if d.weekday() < 5:
            shifts.extend(make_shift(d, name) for name in SHIFT_ORDER)
        d += timedelta(days=1)
    return sorted(shifts, key=lambda s: s["start"])


def choose_default_shift(now: datetime) -> dict:
    all_shifts = shifts_between(now.date() - timedelta(days=14), now.date() + timedelta(days=14))
    active = [s for s in all_shifts if s["start"] <= now < s["end"]]
    if active:
        return active[0]
    previous = [s for s in all_shifts if s["end"] <= now]
    return previous[-1] if previous else all_shifts[0]


def adjacent_shifts(selected: dict) -> tuple[list[dict], int]:
    shifts = shifts_between(
        selected["start"].date() - timedelta(days=21),
        selected["start"].date() + timedelta(days=21),
    )
    idx = next((i for i, s in enumerate(shifts) if s["key"] == selected["key"]), 0)
    return shifts, idx


def load_questions(sb: Client) -> list[dict]:
    rows = sb.table("shift_questions").select("*").order("sort_order").execute().data or []
    if not rows:
        payload = [{**q, "id": str(uuid.uuid4())} for q in DEFAULT_QUESTIONS]
        sb.table("shift_questions").insert(payload).execute()
        rows = sb.table("shift_questions").select("*").order("sort_order").execute().data or payload
    for row in rows:
        row["rules"] = row.get("rules") or {}
        row["options"] = row.get("options") or []
    return rows


def load_navigation_links(sb: Client) -> list[dict]:
    """Load management-configured sidebar links."""
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
    return url.startswith("https://") or url.startswith("http://")


def load_events(sb: Client) -> list[dict]:
    try:
        rows = (
            sb.table("events")
            .select("id,title,start,end,colour,source,status,load_header")
            .execute()
            .data
            or []
        )
    except Exception as exc:
        st.warning(f"Could not load the transport calendar's events table: {exc}")
        return []

    out = []
    for event in rows:
        start_dt = parse_dt(event.get("start"))
        end_dt = parse_dt(event.get("end"))
        if not start_dt or not end_dt:
            continue
        event["_start"] = start_dt
        event["_end"] = end_dt
        out.append(event)
    return out


def overlaps(event: dict, shift: dict) -> bool:
    return event["_start"] < shift["end"] and event["_end"] > shift["start"]


def events_for_shift(events: list[dict], shift: dict) -> list[dict]:
    return [event for event in events if overlaps(event, shift)]


def source_matches(event: dict, source: str) -> bool:
    val = str(event.get("source") or "").strip().casefold()
    title = str(event.get("title") or "").strip().casefold()
    if source == "Bulk":
        return val == "bulk" or "bulk inbound" in val or "bulk" in title
    if source == "Campey":
        return val == "campey" or "campey" in title
    if source == "DHL":
        return val == "dhl" or "dhl" in title
    return False


def planned_counts(events: list[dict], shift: dict) -> dict[str, int]:
    relevant = events_for_shift(events, shift)
    return {
        "inbound_planned": sum(source_matches(e, "Bulk") for e in relevant),
        "outbound_planned_campeys": sum(source_matches(e, "Campey") for e in relevant),
        "outbound_planned_dhl": sum(source_matches(e, "DHL") for e in relevant),
    }


def cil_is_planned(events: list[dict], shift: dict) -> bool:
    for event in events_for_shift(events, shift):
        if (
            "cil" in str(event.get("title") or "").casefold()
            or "cil" in str(event.get("source") or "").casefold()
        ):
            return True
    return False


def load_submissions(sb: Client, selected_start: datetime | None = None) -> dict[str, dict]:
    query = sb.table("shift_submissions").select("*")
    if selected_start is not None:
        query = (
            query.gte("shift_start", (selected_start - timedelta(days=14)).isoformat())
            .lte("shift_start", (selected_start + timedelta(days=1)).isoformat())
        )
    rows = query.execute().data or []
    return {str(row.get("shift_start")): row for row in rows}


def submission_for(submissions: dict[str, dict], shift: dict) -> dict | None:
    target = shift["start"].isoformat()
    for key, row in submissions.items():
        dt = parse_dt(key)
        if dt and dt == shift["start"]:
            return row
    return submissions.get(target)


def answer_colour(
    question: dict,
    value,
    planned: dict,
    answers: dict | None = None,
) -> str | None:
    answers = answers or {}
    rule = (question.get("rules") or {}).get("colour_rule", "none")

    if rule == "ips_positive_or_no_breakdowns":
        breakdowns = answers.get("breakdowns")
        try:
            breakdown_count = float(breakdowns) if breakdowns not in (None, "") else 0
        except (TypeError, ValueError):
            breakdown_count = 0

        if breakdown_count <= 0:
            return "green"

        try:
            ips_count = float(value) if value not in (None, "") else 0
        except (TypeError, ValueError):
            ips_count = 0

        return "green" if ips_count > 0 else "red"

    if value is None or value == "":
        if rule == "zero_green":
            return "green"
        if rule in ("positive_green", "staff_under_three_red"):
            return "red"
        if rule in (
            "matches_inbound_planned",
            "matches_campey_planned",
            "matches_dhl_planned",
            "hundred_green",
            "severity_lmh",
        ):
            return None
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None

    if rule == "severity_lmh":
        return {"L": "green", "M": "amber", "H": "red"}.get(str(value).upper())

    if rule == "zero_green" and number is not None:
        return "green" if number == 0 else "red"

    if rule == "positive_green" and number is not None:
        return "green" if number >= 1 else "red"

    if rule == "hundred_green" and number is not None:
        return "green" if number >= 100 else "red"

    if rule == "staff_under_three_red" and number is not None:
        return "red" if number < 3 else "green"

    match_map = {
        "matches_inbound_planned": "inbound_planned",
        "matches_campey_planned": "outbound_planned_campeys",
        "matches_dhl_planned": "outbound_planned_dhl",
    }
    if rule in match_map and number is not None:
        return "green" if number == planned.get(match_map[rule], 0) else "red"

    return None


def badge_text(value, colour: str | None) -> str:
    shown = "Blank" if value is None or value == "" else str(value)
    if colour == "green":
        return f"🟢 {shown}"
    if colour == "red":
        return f"🔴 {shown}"
    if colour == "amber":
        return f"🟠 {shown}"
    if colour == "grey":
        return f"⚪ {shown}"
    return shown


def snapshot_answers(
    questions: list[dict],
    values: dict,
    planned: dict,
    cil_planned: bool,
) -> dict:
    result = {}
    for question in questions:
        key = question["question_key"]
        if key == "cil_completion" and not cil_planned:
            value = "N/A"
        elif question.get("input_type") == "auto":
            value = planned.get(key, 0)
        else:
            value = values.get(key, "")

        result[key] = {
            "label": question["label"],
            "value": value,
            "section": question.get("section", "General"),
            "rules": question.get("rules") or {},
            "input_type": question.get("input_type", "text"),
        }
    return result


def render_saved_answers(snapshot: dict, planned: dict | None = None):
    planned = dict(planned or {})
    for key in ("inbound_planned", "outbound_planned_campeys", "outbound_planned_dhl"):
        item = snapshot.get(key) or {}
        value = item.get("value")
        if value not in (None, "", "N/A"):
            try:
                planned[key] = int(value)
            except (TypeError, ValueError):
                pass

    if not snapshot:
        st.caption("No answers have been saved for this shift yet.")
        return

    answer_values = {
        key: (item or {}).get("value", "")
        for key, item in snapshot.items()
    }

    for section in dict.fromkeys(
        (item.get("section") or "General") for item in snapshot.values()
    ):
        st.markdown(f"**{section}**")
        for key, item in snapshot.items():
            if (item.get("section") or "General") != section:
                continue
            value = item.get("value", "")
            question = {"rules": item.get("rules") or {}}
            colour = answer_colour(question, value, planned, answer_values)
            if value == "N/A":
                colour = "grey"
            st.markdown(
                f"- **{item.get('label', key)}:** {badge_text(value, colour)}"
            )


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return slug[:48] or "question"


def require_management_password() -> bool:
    expected = str(st.secrets.get("MANAGEMENT_PASSWORD", ""))
    if not expected:
        st.error("Set MANAGEMENT_PASSWORD in Streamlit secrets before using management.")
        return False

    if st.session_state.get("management_authenticated"):
        return True

    with st.form("management_login"):
        st.write("Enter the management password to manage questions and sidebar links.")
        password = st.text_input("Management password", type="password")
        submitted = st.form_submit_button("Unlock management area")

    if submitted:
        if hmac.compare_digest(password, expected):
            st.session_state.management_authenticated = True
            st.rerun()
        st.error("Incorrect password.")

    return False


def management_page(sb: Client, questions: list[dict]):
    st.title("Management")
    questions_tab, links_tab = st.tabs(["Questions", "Sidebar links"])

    with links_tab:
        st.subheader("Sidebar links")
        st.caption(
            "Add useful team resources to the collapsible sidebar. "
            "Use full http:// or https:// URLs."
        )

        try:
            links = load_navigation_links(sb)

            with st.expander("Add a sidebar link", expanded=True):
                with st.form("add_navigation_link_form", clear_on_submit=True):
                    link_label = st.text_input(
                        "Link name",
                        placeholder="e.g. Shift procedures",
                    )
                    link_url = st.text_input(
                        "Website URL",
                        placeholder="https://...",
                    )
                    link_order = st.number_input(
                        "Display order",
                        min_value=0,
                        max_value=10000,
                        value=max(
                            [int(item.get("sort_order", 0)) for item in links] + [0]
                        ) + 10,
                        step=10,
                    )
                    add_link = st.form_submit_button("Add link")

                if add_link:
                    if not link_label.strip():
                        st.error("Enter a name for the link.")
                    elif not valid_navigation_url(link_url.strip()):
                        st.error("Enter a URL beginning with https:// or http://.")
                    else:
                        sb.table("navigation_links").insert(
                            {
                                "label": link_label.strip(),
                                "url": link_url.strip(),
                                "sort_order": int(link_order),
                                "is_active": True,
                            }
                        ).execute()
                        st.success("Sidebar link added.")
                        st.rerun()

            if not links:
                st.info(
                    "No sidebar links yet. Add one above. "
                    "If the link table has not been created, run the SQL setup."
                )

            for link in links:
                with st.expander(
                    f"{'🔗' if link.get('is_active', True) else '⚪'} "
                    f"{link.get('label', 'Link')}"
                ):
                    with st.form(f"edit_link_{link['id']}"):
                        edit_label = st.text_input(
                            "Link name",
                            value=link.get("label", ""),
                        )
                        edit_url = st.text_input(
                            "Website URL",
                            value=link.get("url", ""),
                        )
                        edit_order = st.number_input(
                            "Display order",
                            min_value=0,
                            max_value=10000,
                            value=int(link.get("sort_order", 0)),
                            step=10,
                        )
                        edit_active = st.checkbox(
                            "Show in sidebar",
                            value=bool(link.get("is_active", True)),
                        )
                        save_link = st.form_submit_button("Save link")

                    col1, col2 = st.columns(2)

                    with col1:
                        if save_link:
                            if not edit_label.strip():
                                st.error("Enter a name for the link.")
                            elif not valid_navigation_url(edit_url.strip()):
                                st.error("Enter a URL beginning with https:// or http://.")
                            else:
                                sb.table("navigation_links").update(
                                    {
                                        "label": edit_label.strip(),
                                        "url": edit_url.strip(),
                                        "sort_order": int(edit_order),
                                        "is_active": edit_active,
                                    }
                                ).eq("id", link["id"]).execute()
                                st.success("Link updated.")
                                st.rerun()

                    with col2:
                        if st.button(
                            "Delete link",
                            key=f"delete_link_{link['id']}",
                        ):
                            sb.table("navigation_links").delete().eq(
                                "id", link["id"]
                            ).execute()
                            st.success("Link deleted.")
                            st.rerun()

        except Exception as exc:
            st.error(f"Could not manage sidebar links: {exc}")
            st.info(
                "Run the SQL setup in Supabase to create the navigation_links table."
            )

    with questions_tab:
        st.subheader("Manage shift questions")

        if st.button("Lock management area"):
            st.session_state.management_authenticated = False
            st.rerun()

        st.caption(
            "Changes affect future/current displays. Existing saved answers retain "
            "their question labels as a snapshot."
        )

        with st.expander("Add a question", expanded=True):
            with st.form("add_question_form", clear_on_submit=True):
                label = st.text_input(
                    "Question text",
                    placeholder="e.g. Forklift checks completed",
                )
                section = st.selectbox(
                    "Section",
                    [
                        "Safety & quality",
                        "Operational checks",
                        "Transport",
                        "Handover notes",
                        "General",
                    ],
                )
                input_type = st.selectbox(
                    "Answer type",
                    ["text", "number", "percentage", "choice", "yes_no"],
                )
                colour_rule = st.selectbox(
                    "Colour rule",
                    [
                        "none",
                        "zero_green",
                        "positive_green",
                        "severity_lmh",
                        "hundred_green",
                        "staff_under_three_red",
                        "ips_positive_or_no_breakdowns",
                    ],
                )
                options_text = st.text_input(
                    "Choice options (comma-separated; only used for Choice)",
                    placeholder="Yes, No, Not applicable",
                )
                required = st.checkbox("Required question")
                submitted = st.form_submit_button("Add question")

            if submitted:
                if not label.strip():
                    st.error("Enter question text.")
                else:
                    base = slugify(label)
                    existing = {q["question_key"] for q in questions}
                    key = base
                    suffix = 2
                    while key in existing:
                        key = f"{base}_{suffix}"
                        suffix += 1

                    options = (
                        [x.strip() for x in options_text.split(",") if x.strip()]
                        if input_type == "choice"
                        else ["Yes", "No"] if input_type == "yes_no" else []
                    )

                    row = {
                        "id": str(uuid.uuid4()),
                        "question_key": key,
                        "label": label.strip(),
                        "section": section,
                        "input_type": input_type,
                        "rules": {
                            "colour_rule": colour_rule,
                            "required": required,
                        },
                        "options": options,
                        "sort_order": max(
                            [int(q.get("sort_order", 0)) for q in questions] + [0]
                        ) + 10,
                        "is_active": True,
                        "is_system": False,
                    }
                    sb.table("shift_questions").insert(row).execute()
                    st.success("Question added.")
                    st.rerun()

        st.subheader("Existing questions")

        for question in questions:
            with st.expander(
                f"{'🟢' if question.get('is_active', True) else '⚪'} "
                f"{question['label']} · {question['section']}"
            ):
                with st.form(f"edit_{question['question_key']}"):
                    new_label = st.text_input(
                        "Question text",
                        value=question["label"],
                    )
                    section_options = [
                        "Safety & quality",
                        "Operational checks",
                        "Transport",
                        "Handover notes",
                        "General",
                    ]
                    old_section = question.get("section", "General")
                    new_section = st.selectbox(
                        "Section",
                        section_options,
                        index=(
                            section_options.index(old_section)
                            if old_section in section_options
                            else 4
                        ),
                    )

                    types = ["text", "number", "percentage", "choice", "yes_no", "auto"]
                    current_type = question.get("input_type", "text")
                    new_type = st.selectbox(
                        "Answer type",
                        types,
                        index=types.index(current_type) if current_type in types else 0,
                        disabled=bool(question.get("is_system")),
                    )

                    rules_list = [
                        "none",
                        "zero_green",
                        "positive_green",
                        "severity_lmh",
                        "hundred_green",
                        "staff_under_three_red",
                        "ips_positive_or_no_breakdowns",
                        "matches_inbound_planned",
                        "matches_campey_planned",
                        "matches_dhl_planned",
                    ]
                    old_rule = (question.get("rules") or {}).get("colour_rule", "none")
                    new_rule = st.selectbox(
                        "Colour rule",
                        rules_list,
                        index=rules_list.index(old_rule) if old_rule in rules_list else 0,
                    )

                    old_options = question.get("options") or []
                    new_options = st.text_input(
                        "Choice options (comma-separated)",
                        value=", ".join(old_options),
                    )
                    new_order = st.number_input(
                        "Display order",
                        min_value=0,
                        max_value=10000,
                        value=int(question.get("sort_order", 0)),
                        step=10,
                    )
                    active = st.checkbox(
                        "Active",
                        value=bool(question.get("is_active", True)),
                    )
                    save = st.form_submit_button("Save question settings")

                if save:
                    if not new_label.strip():
                        st.error("Question text cannot be blank.")
                    else:
                        if new_type == "choice":
                            opts = [x.strip() for x in new_options.split(",") if x.strip()]
                        elif new_type == "yes_no":
                            opts = ["Yes", "No"]
                        elif new_type == "auto":
                            opts = old_options
                        else:
                            opts = []

                        rules = dict(question.get("rules") or {})
                        rules["colour_rule"] = new_rule

                        sb.table("shift_questions").update(
                            {
                                "label": new_label.strip(),
                                "section": new_section,
                                "input_type": new_type,
                                "rules": rules,
                                "options": opts,
                                "sort_order": int(new_order),
                                "is_active": active,
                            }
                        ).eq("id", question["id"]).execute()

                        st.success("Question updated.")
                        st.rerun()


def render_question_input(
    question: dict,
    current_value,
    planned: dict,
    cil_planned: bool,
    disabled: bool = False,
):
    key = question["question_key"]
    label = question["label"]
    kind = question.get("input_type", "text")
    rules = question.get("rules") or {}

    if kind == "auto":
        value = planned.get(key, 0)
        st.text_input(label, value=str(value), disabled=True, key=f"auto_{key}")
        return value

    if key == "cil_completion" and not cil_planned:
        st.text_input(
            label,
            value="N/A — no CIL planned",
            disabled=True,
            key=f"cil_na_{key}",
        )
        return "N/A"

    if kind == "number":
        lo = int(rules.get("min", 0))
        hi = int(rules.get("max", 999999))
        value = current_value if isinstance(current_value, (int, float)) else None
        return st.number_input(
            label,
            min_value=lo,
            max_value=hi,
            value=value,
            step=1,
            key=f"input_{key}",
            disabled=disabled,
            placeholder="Leave blank if not recorded",
        )

    if kind == "percentage":
        value = current_value if isinstance(current_value, (int, float)) else None
        return st.number_input(
            label,
            min_value=0,
            max_value=100,
            value=value,
            step=1,
            key=f"input_{key}",
            disabled=disabled,
            help="Enter 0–100%. 100% is green; below 100% is red.",
        )

    if kind == "choice":
        options = question.get("options") or ["L", "M", "H"]
        if rules.get("colour_rule") == "severity_lmh":
            st.caption("L = Low 🟢 · M = Medium 🟠 · H = High 🔴")
        choices = ["— Select —"] + options
        existing = (
            str(current_value)
            if current_value not in (None, "")
            else "— Select —"
        )
        idx = choices.index(existing) if existing in choices else 0
        selected = st.selectbox(
            label,
            choices,
            index=idx,
            key=f"input_{key}",
            disabled=disabled,
        )
        return "" if selected == "— Select —" else selected

    if kind == "yes_no":
        choices = ["— Select —", "Yes", "No"]
        existing = (
            str(current_value)
            if current_value not in (None, "")
            else "— Select —"
        )
        idx = choices.index(existing) if existing in choices else 0
        selected = st.selectbox(
            label,
            choices,
            index=idx,
            key=f"input_{key}",
            disabled=disabled,
        )
        return "" if selected == "— Select —" else selected

    return st.text_area(
        label,
        value=str(current_value or ""),
        key=f"input_{key}",
        disabled=disabled,
        height=80,
    )


def main():
    st.title("🏭 Warehouse Shift Hub")
    st.caption("Shift handover, performance checks and transport bookings")

    try:
        sb = get_supabase()
        questions = load_questions(sb)
    except Exception as exc:
        st.error(f"App setup problem: {exc}")
        st.info(
            "Follow the setup steps in README.md and run sql/setup.sql "
            "in your Supabase SQL Editor."
        )
        st.stop()

    # Streamlit supplies the collapsible sidebar control at the top of the panel.
    with st.sidebar:
        st.header("Navigation")
        st.caption("Use the arrow at the top of this panel to collapse or reopen it.")
        page = st.radio(
            "Open",
            ["Shift workspace", "Management"],
            label_visibility="collapsed",
        )

        nav_links = load_navigation_links(sb)
        if nav_links:
            st.divider()
            st.subheader("Team links")
            for nav_link in nav_links:
                if nav_link.get("is_active", True):
                    label = str(nav_link.get("label") or "Open link")
                    url = str(nav_link.get("url") or "")
                    if valid_navigation_url(url):
                        st.markdown(f"- [{label}]({url})")

        st.divider()
        st.caption("Shift rotation is calculated automatically. Team colours stay with the teams.")
        if st.button("Refresh calendar and records", use_container_width=True):
            st.rerun()

    if page == "Management":
        if require_management_password():
            management_page(sb, questions)
        return

    active_questions = sorted(
        [q for q in questions if q.get("is_active", True)],
        key=lambda q: int(q.get("sort_order", 0)),
    )
    events = load_events(sb)
    now = datetime.now(UK)

    if "selected_shift_key" not in st.session_state:
        st.session_state.selected_shift_key = choose_default_shift(now)["key"]

    candidates = shifts_between(
        now.date() - timedelta(days=35),
        now.date() + timedelta(days=35),
    )
    selected = next(
        (s for s in candidates if s["key"] == st.session_state.selected_shift_key),
        None,
    )
    if selected is None:
        selected = choose_default_shift(now)
        st.session_state.selected_shift_key = selected["key"]

    all_shifts, shift_idx = adjacent_shifts(selected)

    if shift_idx > 0 and st.button("← Previous shift", key="prev_shift_top"):
        st.session_state.selected_shift_key = all_shifts[shift_idx - 1]["key"]
        st.rerun()

    if shift_idx + 1 < len(all_shifts) and st.button(
        "Next shift →",
        key="next_shift_top",
    ):
        st.session_state.selected_shift_key = all_shifts[shift_idx + 1]["key"]
        st.rerun()

    team_hex = COLOUR_HEX.get(selected["team_colour"], "#666666")
    st.markdown(
        f"<div style='padding:12px 16px;border-radius:10px;"
        f"background:{team_hex}18;border-left:6px solid {team_hex};"
        f"margin-bottom:14px'><b>{selected['label']}</b><br>"
        f"{selected['start'].strftime('%a %d %b %Y, %H:%M')} – "
        f"{selected['end'].strftime('%a %d %b %Y, %H:%M')} (UK time)</div>",
        unsafe_allow_html=True,
    )

    shift_events = events_for_shift(events, selected)
    planned = planned_counts(events, selected)
    cil_planned = cil_is_planned(events, selected)
    submissions = load_submissions(sb, selected["start"])
    saved = submission_for(submissions, selected)
    saved_snapshot = (saved or {}).get("answers") or {}
    previous = [
        s for s in all_shifts[:shift_idx]
        if s["end"] <= selected["start"]
    ][-3:]

    left, right = st.columns([0.92, 1.45], gap="large")

    with left:
        st.subheader("Transport calendar")
        st.caption(
            "Bookings overlapping this shift are shown. Previous/next shift controls "
            "change both the calendar and the shift log."
        )

        cal_events = []
        for event in shift_events:
            source = str(event.get("source") or "Manual")
            cal_events.append(
                {
                    "id": str(event.get("id", "")),
                    "title": f"{event.get('title') or 'Booking'} · {source}",
                    "start": event["_start"].isoformat(),
                    "end": event["_end"].isoformat(),
                    "backgroundColor": event.get("colour") or COLOUR_HEX.get(
                        {"Campey": "Blue", "Bulk": "Yellow", "DHL": "Blue"}.get(
                            source, "Red"
                        ),
                        "#777777",
                    ),
                    "borderColor": event.get("colour") or "#777777",
                }
            )

        cal_options = {
            "initialView": "timeGridDay",
            "initialDate": selected["start"].date().isoformat(),
            "headerToolbar": {"left": "", "center": "title", "right": ""},
            "slotMinTime": selected["start"].strftime("%H:%M:%S"),
            "slotMaxTime": (
                selected["end"].strftime("%H:%M:%S")
                if selected["end"].date() == selected["start"].date()
                else "24:00:00"
            ),
            "height": 560,
            "allDaySlot": False,
            "nowIndicator": True,
            "timeZone": "local",
            "firstDay": 1,
        }

        if selected["end"].date() != selected["start"].date():
            st.caption(
                "This shift crosses midnight; bookings are filtered by actual "
                "overlap with the shift window."
            )
            cal_options["slotMaxTime"] = "24:00:00"

        try:
            calendar(
                events=cal_events,
                options=cal_options,
                key=f"calendar_{selected['key']}",
            )
        except Exception:
            st.info(
                "Calendar widget could not render. The shift's bookings "
                "are listed below instead."
            )

        with st.expander(
            f"Bookings in this shift ({len(shift_events)})",
            expanded=True,
        ):
            if not shift_events:
                st.caption("No calendar bookings overlap this shift.")
            for event in sorted(shift_events, key=lambda x: x["_start"]):
                st.markdown(
                    f"**{event.get('title') or 'Booking'}**  \n"
                    f"{event['_start'].strftime('%a %H:%M')}–"
                    f"{event['_end'].strftime('%a %H:%M')} · "
                    f"{event.get('source') or 'Manual'}"
                )

        st.markdown("**Planned transport totals**")
        st.metric("Bulk inbound", planned["inbound_planned"])
        st.metric("Campeys outbound", planned["outbound_planned_campeys"])
        st.metric("DHL outbound", planned["outbound_planned_dhl"])

    with right:
        st.subheader("Shift questions and answers")
        st.caption(
            "Answers save to the shared database. The colour beside each saved "
            "answer follows the rule for that question."
        )

        with st.form(f"shift_form_{selected['key']}"):
            submitter = st.text_input(
                "Recorded by (optional)",
                value=(saved or {}).get("submitted_by") or "",
            )
            values = {}
            sections = list(
                dict.fromkeys(q.get("section", "General") for q in active_questions)
            )

            for section in sections:
                st.markdown(f"### {section}")
                for question in active_questions:
                    if question.get("section", "General") != section:
                        continue

                    old = saved_snapshot.get(question["question_key"], {})
                    old_value = old.get("value", "")
                    if question.get("input_type") == "auto":
                        old_value = planned.get(question["question_key"], 0)

                    value = render_question_input(
                        question,
                        old_value,
                        planned,
                        cil_planned,
                    )
                    values[question["question_key"]] = value

                    if question.get("input_type") != "auto" and not (
                        question["question_key"] == "cil_completion" and not cil_planned
                    ):
                        colour = answer_colour(question, value, planned, values)
                        if colour:
                            st.caption(f"Status: {badge_text(value, colour)}")

            submitted = st.form_submit_button(
                "Save shift record",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            missing = [
                q["label"]
                for q in active_questions
                if (q.get("rules") or {}).get("required")
                and values.get(q["question_key"]) in (None, "")
            ]

            if missing:
                st.error("Please complete required questions: " + ", ".join(missing))
            else:
                snapshot = snapshot_answers(
                    active_questions,
                    values,
                    planned,
                    cil_planned,
                )
                row = {
                    "shift_date": selected["shift_date"],
                    "shift_name": selected["shift_name"],
                    "shift_start": selected["start"].isoformat(),
                    "shift_end": selected["end"].isoformat(),
                    "team_colour": selected["team_colour"],
                    "answers": snapshot,
                    "submitted_by": submitter.strip() or None,
                    "updated_at": datetime.now(UK).isoformat(),
                }

                try:
                    if saved and saved.get("id"):
                        sb.table("shift_submissions").update(row).eq(
                            "id", saved["id"]
                        ).execute()
                    else:
                        sb.table("shift_submissions").insert(row).execute()

                    st.success("Shift record saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save shift record: {exc}")

        st.divider()
        st.subheader("Current shift record")

        if saved:
            st.caption(
                f"Last saved: {saved.get('updated_at', 'unknown')} · "
                f"Recorded by: {saved.get('submitted_by') or 'Not specified'}"
            )
            render_saved_answers(saved_snapshot, planned)
        else:
            st.info(
                "This shift has not been saved yet. Complete the questions above "
                "and save the record."
            )

        st.divider()
        st.subheader("Previous three shifts")

        if not previous:
            st.caption("No earlier shifts found in the generated schedule.")

        for past_shift in reversed(previous):
            past_submission = submission_for(submissions, past_shift)
            title = (
                f"{past_shift['label']} · "
                f"{past_shift['start'].strftime('%H:%M')}–"
                f"{past_shift['end'].strftime('%H:%M')}"
            )
            with st.expander(title, expanded=True):
                if past_submission:
                    st.caption(
                        f"Recorded by: {past_submission.get('submitted_by') or 'Not specified'} "
                        f"· Updated: {past_submission.get('updated_at', 'unknown')}"
                    )
                    render_saved_answers(past_submission.get("answers") or {})
                else:
                    st.caption("No record saved for this shift yet.")

    st.caption(
        "Shift colour rotation is anchored to the confirmed week of 12 October 2026. "
        "Calendar booking counts include any booking that overlaps the selected shift."
    )


if __name__ == "__main__":
    main()