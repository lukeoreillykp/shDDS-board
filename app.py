from __future__ import annotations

import hmac
import re
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_calendar import calendar
from supabase import Client, create_client

from warehouse_core.shifts import (
    UK,
    COLOUR_HEX,
    SHIFT_ORDER,
    make_shift,
    shifts_between,
    team_colours_for_week,
)
from warehouse_core.rules import answer_colour
from warehouse_core.transport import (
    parse_dt,
    normalize_events,
    overlaps,
    events_for_shift,
    source_matches,
    planned_counts,
    fetch_all_pages,
)

st.set_page_config(
    page_title="Warehouse Shift Hub",
    page_icon="🏭",
    layout="wide",
)


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


# ---------------------------------------------------------------------
# Supabase and time helpers
# ---------------------------------------------------------------------

def get_supabase() -> Client:
    if "supabase_client" not in st.session_state:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to Streamlit secrets."
            )
        st.session_state.supabase_client = create_client(url, key)
    return st.session_state.supabase_client


def choose_default_shift(
    now: datetime,
    events: list[dict] | None = None,
) -> dict:
    """
    Choose the active shift where possible.

    If no shift is active, choose the next future shift with transport
    bookings. If no future shift has bookings, use the latest completed
    shift. This makes future bookings visible when opening the app.
    """
    shifts = shifts_between(
        now.date() - timedelta(days=14),
        now.date() + timedelta(days=35),
    )

    active = [s for s in shifts if s["start"] <= now < s["end"]]
    if active:
        return active[0]

    if events:
        future_with_events = [
            shift
            for shift in shifts
            if shift["start"] > now and events_for_shift(events, shift)
        ]
        if future_with_events:
            return future_with_events[0]

    previous = [s for s in shifts if s["end"] <= now]
    return previous[-1] if previous else shifts[0]


def adjacent_shifts(selected: dict) -> tuple[list[dict], int]:
    shifts = shifts_between(
        selected["start"].date() - timedelta(days=21),
        selected["start"].date() + timedelta(days=21),
    )
    idx = next(
        (i for i, s in enumerate(shifts) if s["key"] == selected["key"]),
        0,
    )
    return shifts, idx


# ---------------------------------------------------------------------
# Questions and navigation
# ---------------------------------------------------------------------

def load_questions(sb: Client) -> list[dict]:
    rows = (
        sb.table("shift_questions")
        .select("*")
        .order("sort_order")
        .execute()
        .data
        or []
    )

    if not rows:
        payload = [
            {**question, "id": str(uuid.uuid4())}
            for question in DEFAULT_QUESTIONS
        ]
        sb.table("shift_questions").insert(payload).execute()
        rows = (
            sb.table("shift_questions")
            .select("*")
            .order("sort_order")
            .execute()
            .data
            or payload
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
    return url.startswith("https://") or url.startswith("http://")


# ---------------------------------------------------------------------
# Transport calendar
# ---------------------------------------------------------------------

def load_events(sb: Client) -> list[dict]:
    """Fetch all event pages and normalize transport events."""
    try:
        rows = fetch_all_pages(
            lambda start, end: (
                sb.table("events")
                .select("*")
                .range(start, end)
                .execute()
                .data
                or []
            ),
            page_size=1000,
        )
    except Exception as exc:
        st.warning(f"Could not load transport calendar events: {exc}")
        return []

    return normalize_events(rows)


def cil_is_planned(events: list[dict], shift: dict) -> bool:
    return any(
        "cil" in str(event.get("title") or "").casefold()
        or "cil" in str(event.get("source") or "").casefold()
        for event in events_for_shift(events, shift)
    )


# ---------------------------------------------------------------------
# Shift submissions
# ---------------------------------------------------------------------

def load_submissions(
    sb: Client,
    selected_start: datetime | None = None,
) -> dict[str, dict]:
    query = sb.table("shift_submissions").select("*")

    if selected_start is not None:
        query = (
            query.gte(
                "shift_start",
                (selected_start - timedelta(days=14)).isoformat(),
            )
            .lte(
                "shift_start",
                (selected_start + timedelta(days=1)).isoformat(),
            )
        )

    rows = query.execute().data or []
    return {str(row.get("shift_start")): row for row in rows}


def submission_for(
    submissions: dict[str, dict],
    shift: dict,
) -> dict | None:
    target = shift["start"].isoformat()

    for key, row in submissions.items():
        dt = parse_dt(key)
        if dt and dt == shift["start"]:
            return row

    return submissions.get(target)


# ---------------------------------------------------------------------
# Answer colours and display
# ---------------------------------------------------------------------

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


def render_saved_answers(
    snapshot: dict,
    planned: dict | None = None,
):
    planned = dict(planned or {})

    for key in (
        "inbound_planned",
        "outbound_planned_campeys",
        "outbound_planned_dhl",
    ):
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

    sections = dict.fromkeys(
        (item.get("section") or "General")
        for item in snapshot.values()
    )

    for section in sections:
        st.markdown(f"**{section}**")

        for key, item in snapshot.items():
            if (item.get("section") or "General") != section:
                continue

            value = item.get("value", "")
            colour = answer_colour(
                {"rules": item.get("rules") or {}},
                value,
                planned,
                answer_values,
            )

            if value == "N/A":
                colour = "grey"

            st.markdown(
                f"- **{item.get('label', key)}:** "
                f"{badge_text(value, colour)}"
            )


# ---------------------------------------------------------------------
# Management access
# ---------------------------------------------------------------------

def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return slug[:48] or "question"


def require_management_password() -> bool:
    expected = str(st.secrets.get("MANAGEMENT_PASSWORD", ""))

    if not expected:
        st.error("Set MANAGEMENT_PASSWORD in Streamlit secrets first.")
        return False

    if st.session_state.get("management_authenticated"):
        return True

    with st.form("management_login"):
        st.write(
            "Enter the management password to manage questions and sidebar links."
        )
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
        st.caption("Add team resources to the collapsible sidebar.")

        try:
            links = load_navigation_links(sb)

            with st.expander("Add a sidebar link", expanded=True):
                with st.form("add_navigation_link_form", clear_on_submit=True):
                    label = st.text_input(
                        "Link name",
                        placeholder="e.g. Shift procedures",
                    )
                    url = st.text_input(
                        "Website URL",
                        placeholder="https://...",
                    )
                    order = st.number_input(
                        "Display order",
                        min_value=0,
                        max_value=10000,
                        value=max(
                            [int(item.get("sort_order", 0)) for item in links]
                            + [0]
                        ) + 10,
                        step=10,
                    )
                    add = st.form_submit_button("Add link")

                if add:
                    if not label.strip():
                        st.error("Enter a name for the link.")
                    elif not valid_navigation_url(url.strip()):
                        st.error(
                            "Enter a URL starting with https:// or http://."
                        )
                    else:
                        sb.table("navigation_links").insert(
                            {
                                "label": label.strip(),
                                "url": url.strip(),
                                "sort_order": int(order),
                                "is_active": True,
                            }
                        ).execute()
                        st.success("Sidebar link added.")
                        st.rerun()

            if not links:
                st.info("No sidebar links have been added yet.")

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
                        active = st.checkbox(
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
                                st.error(
                                    "Enter a URL starting with https:// or http://."
                                )
                            else:
                                sb.table("navigation_links").update(
                                    {
                                        "label": edit_label.strip(),
                                        "url": edit_url.strip(),
                                        "sort_order": int(edit_order),
                                        "is_active": active,
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
                "Check that the navigation_links table exists in Supabase."
            )

    with questions_tab:
        st.subheader("Manage shift questions")

        if st.button("Lock management area"):
            st.session_state.management_authenticated = False
            st.rerun()

        st.caption(
            "Changes affect current and future displays. "
            "Saved answers keep their saved labels."
        )

        with st.expander("Add a question", expanded=True):
            with st.form("add_question_form", clear_on_submit=True):
                label = st.text_input("Question text")
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
                    "Choice options (comma-separated)",
                    placeholder="Yes, No",
                )
                required = st.checkbox("Required question")
                submitted = st.form_submit_button("Add question")

            if submitted:
                if not label.strip():
                    st.error("Enter question text.")
                else:
                    base = slugify(label)
                    existing = {
                        question["question_key"] for question in questions
                    }
                    key = base
                    suffix = 2

                    while key in existing:
                        key = f"{base}_{suffix}"
                        suffix += 1

                    if input_type == "choice":
                        options = [
                            item.strip()
                            for item in options_text.split(",")
                            if item.strip()
                        ]
                    elif input_type == "yes_no":
                        options = ["Yes", "No"]
                    else:
                        options = []

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
                            [
                                int(q.get("sort_order", 0))
                                for q in questions
                            ] + [0]
                        ) + 10,
                        "is_active": True,
                        "is_system": False,
                    }

                    sb.table("shift_questions").insert(row).execute()
                    st.success("Question added.")
                    st.rerun()

        st.subheader("Existing questions")

        sections = [
            "Safety & quality",
            "Operational checks",
            "Transport",
            "Handover notes",
            "General",
        ]
        types = ["text", "number", "percentage", "choice", "yes_no", "auto"]
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

        for question in questions:
            with st.expander(
                f"{'🟢' if question.get('is_active', True) else '⚪'} "
                f"{question['label']} · {question.get('section', 'General')}"
            ):
                with st.form(f"edit_{question['question_key']}"):
                    new_label = st.text_input(
                        "Question text",
                        value=question["label"],
                    )

                    old_section = question.get("section", "General")
                    section_index = (
                        sections.index(old_section)
                        if old_section in sections
                        else 4
                    )
                    new_section = st.selectbox(
                        "Section",
                        sections,
                        index=section_index,
                    )

                    old_type = question.get("input_type", "text")
                    new_type = st.selectbox(
                        "Answer type",
                        types,
                        index=types.index(old_type) if old_type in types else 0,
                        disabled=bool(question.get("is_system")),
                    )

                    old_rules = question.get("rules") or {}
                    old_rule = old_rules.get("colour_rule", "none")
                    new_rule = st.selectbox(
                        "Colour rule",
                        rules_list,
                        index=rules_list.index(old_rule)
                        if old_rule in rules_list
                        else 0,
                    )

                    old_options = question.get("options") or []
                    new_options = st.text_input(
                        "Choice options (comma-separated)",
                        value=", ".join(old_options),
                    )

                    order = st.number_input(
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
                            options = [
                                item.strip()
                                for item in new_options.split(",")
                                if item.strip()
                            ]
                        elif new_type == "yes_no":
                            options = ["Yes", "No"]
                        else:
                            options = (
                                old_options if new_type == "auto" else []
                            )

                        rules = dict(old_rules)
                        rules["colour_rule"] = new_rule

                        sb.table("shift_questions").update(
                            {
                                "label": new_label.strip(),
                                "section": new_section,
                                "input_type": new_type,
                                "rules": rules,
                                "options": options,
                                "sort_order": int(order),
                                "is_active": active,
                            }
                        ).eq("id", question["id"]).execute()

                        st.success("Question updated.")
                        st.rerun()


# ---------------------------------------------------------------------
# Shift question inputs
# ---------------------------------------------------------------------

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
        st.text_input(
            label,
            value=str(value),
            disabled=True,
            key=f"auto_{key}",
        )
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
        minimum = int(rules.get("min", 0))
        maximum = int(rules.get("max", 999999))
        value = (
            current_value
            if isinstance(current_value, (int, float))
            else None
        )

        return st.number_input(
            label,
            min_value=minimum,
            max_value=maximum,
            value=value,
            step=1,
            key=f"input_{key}",
            disabled=disabled,
            help="Leave at the default if nothing was recorded.",
        )

    if kind == "percentage":
        value = (
            current_value
            if isinstance(current_value, (int, float))
            else None
        )

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
        selected = st.selectbox(
            label,
            choices,
            index=choices.index(existing) if existing in choices else 0,
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
        selected = st.selectbox(
            label,
            choices,
            index=choices.index(existing) if existing in choices else 0,
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


# ---------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------

def main():
    st.title("🏭 Warehouse Shift Hub")
    st.caption("Shift handover, performance checks and transport bookings")

    try:
        sb = get_supabase()
        questions = load_questions(sb)
    except Exception as exc:
        st.error(f"App setup problem: {exc}")
        st.info("Check your Streamlit secrets and database setup.")
        st.stop()

    with st.sidebar:
        st.header("Navigation")
        st.caption(
            "Use the arrow at the top of this panel to collapse or reopen it."
        )

        page = st.radio(
            "Open",
            ["Shift workspace", "Management"],
            label_visibility="collapsed",
        )

        links = load_navigation_links(sb)
        active_links = [
            link for link in links if link.get("is_active", True)
        ]

        if active_links:
            st.divider()
            st.subheader("Team links")

            for link in active_links:
                label = str(link.get("label") or "Open link")
                url = str(link.get("url") or "")

                if valid_navigation_url(url):
                    st.markdown(f"- [{label}]({url})")

        st.divider()
        st.caption("Shift rotation is calculated automatically.")

        # Clear the remembered shift so the default is recalculated
        # using the latest events from Supabase.
        if st.button("Refresh calendar and records", use_container_width=True):
            st.session_state.pop("selected_shift_key", None)
            st.rerun()

    if page == "Management":
        if require_management_password():
            management_page(sb, questions)
        return

    active_questions = sorted(
        [
            question
            for question in questions
            if question.get("is_active", True)
        ],
        key=lambda question: int(question.get("sort_order", 0)),
    )

    events = load_events(sb)
    now = datetime.now(UK)

    # Choose the default shift using both the current time and calendar data.
    if "selected_shift_key" not in st.session_state:
        st.session_state.selected_shift_key = choose_default_shift(
            now,
            events,
        )["key"]

    candidates = shifts_between(
        now.date() - timedelta(days=35),
        now.date() + timedelta(days=35),
    )

    selected = next(
        (
            shift
            for shift in candidates
            if shift["key"] == st.session_state.selected_shift_key
        ),
        None,
    )

    if selected is None:
        selected = choose_default_shift(now, events)
        st.session_state.selected_shift_key = selected["key"]

    all_shifts, shift_index = adjacent_shifts(selected)

    previous_button, next_button = st.columns(2)

    with previous_button:
        if shift_index > 0 and st.button("← Previous shift"):
            st.session_state.selected_shift_key = (
                all_shifts[shift_index - 1]["key"]
            )
            st.rerun()

    with next_button:
        if (
            shift_index + 1 < len(all_shifts)
            and st.button("Next shift →")
        ):
            st.session_state.selected_shift_key = (
                all_shifts[shift_index + 1]["key"]
            )
            st.rerun()

    team_hex = COLOUR_HEX.get(selected["team_colour"], "#666666")

    st.markdown(
        f"<div style='padding:12px 16px;border-radius:10px;"
        f"background:{team_hex}18;border-left:6px solid {team_hex};"
        f"margin-bottom:14px'>"
        f"<b>{selected['label']}</b><br>"
        f"{selected['start'].strftime('%a %d %b %Y, %H:%M')} – "
        f"{selected['end'].strftime('%a %d %b %Y, %H:%M')} (UK time)"
        f"</div>",
        unsafe_allow_html=True,
    )

    shift_events = events_for_shift(events, selected)
    planned = planned_counts(events, selected)
    cil_planned = cil_is_planned(events, selected)
    submissions = load_submissions(sb, selected["start"])
    saved = submission_for(submissions, selected)
    saved_answers = (saved or {}).get("answers") or {}

    previous_shifts = [
        shift
        for shift in all_shifts[:shift_index]
        if shift["end"] <= selected["start"]
    ][-3:]

    # Questions and historical shift records occupy the wider left column.
    # The chronological transport list is positioned on the right.
    left, right = st.columns([1.2, 1.0], gap="large")

    # ---------------------------------------------------------------
    # Shift questions, saving, and historical records — LEFT
    # ---------------------------------------------------------------

    with left:
        st.subheader("Shift questions and answers")
        st.caption("Answers save to the shared database.")

        with st.form(f"shift_form_{selected['key']}"):
            submitter = st.text_input(
                "Recorded by (optional)",
                value=(saved or {}).get("submitted_by") or "",
            )

            values = {}
            sections = list(
                dict.fromkeys(
                    question.get("section", "General")
                    for question in active_questions
                )
            )

            for section in sections:
                st.markdown(f"### {section}")

                for question in active_questions:
                    if question.get("section", "General") != section:
                        continue

                    old = saved_answers.get(
                        question["question_key"],
                        {},
                    )
                    old_value = old.get("value", "")

                    if question.get("input_type") == "auto":
                        old_value = planned.get(
                            question["question_key"],
                            0,
                        )

                    value = render_question_input(
                        question,
                        old_value,
                        planned,
                        cil_planned,
                    )
                    values[question["question_key"]] = value

                    if (
                        question.get("input_type") != "auto"
                        and not (
                            question["question_key"] == "cil_completion"
                            and not cil_planned
                        )
                    ):
                        colour = answer_colour(
                            question,
                            value,
                            planned,
                            values,
                        )

                        if colour:
                            st.caption(
                                f"Status: {badge_text(value, colour)}"
                            )

            submitted = st.form_submit_button(
                "Save shift record",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            missing = [
                question["label"]
                for question in active_questions
                if (question.get("rules") or {}).get("required")
                and values.get(question["question_key"]) in (None, "")
            ]

            if missing:
                st.error(
                    "Please complete required questions: "
                    + ", ".join(missing)
                )
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
                        sb.table("shift_submissions").update(
                            row
                        ).eq("id", saved["id"]).execute()
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
            render_saved_answers(saved_answers, planned)
        else:
            st.info("This shift has not been saved yet.")

        st.divider()
        st.subheader("Previous three shifts")

        if not previous_shifts:
            st.caption("No earlier shifts found in the generated schedule.")

        for past_shift in reversed(previous_shifts):
            past_submission = submission_for(submissions, past_shift)

            title = (
                f"{past_shift['label']} · "
                f"{past_shift['start'].strftime('%H:%M')}–"
                f"{past_shift['end'].strftime('%H:%M')}"
            )

            with st.expander(title, expanded=True):
                if past_submission:
                    st.caption(
                        f"Recorded by: "
                        f"{past_submission.get('submitted_by') or 'Not specified'} · "
                        f"Updated: {past_submission.get('updated_at', 'unknown')}"
                    )
                    render_saved_answers(
                        past_submission.get("answers") or {}
                    )
                else:
                    st.caption("No record saved for this shift yet.")

    # ---------------------------------------------------------------
    # Transport list and totals — RIGHT
    # ---------------------------------------------------------------

    with right:
        st.subheader("Transport bookings")
        st.caption(
            "Bookings overlapping the selected shift, ordered by start time."
        )

        calendar_events = []

        for event in shift_events:
            source = str(
                event.get("source")
                or event.get("load_header")
                or event.get("category")
                or ""
            ).strip()

            title_text = str(event.get("title") or "")
            search_text = (
                source
                + " "
                + title_text
                + " "
                + str(event.get("load_header") or "")
            ).casefold()

            if "campey" in search_text:
                source = "Campey"
            elif "dhl" in search_text:
                source = "DHL"
            elif "bulk" in search_text:
                source = "Bulk"
            elif not source:
                source = "Manual"

            colour = event.get("colour")

            if not colour:
                colour = {
                    "Campey": COLOUR_HEX["Blue"],
                    "Bulk": COLOUR_HEX["Yellow"],
                    "DHL": COLOUR_HEX["Blue"],
                }.get(source, "#777777")

            # Keep Bulk's descriptive title. Simplify DHL and Campeys.
            if source == "Campey":
                display_title = "Campeys"
            elif source == "DHL":
                display_title = "DHL"
            else:
                display_title = f"{title_text or 'Booking'} · {source}"

            calendar_events.append(
                {
                    "id": str(event.get("id", "")),
                    "title": display_title,
                    "start": event["_start"].isoformat(),
                    "end": event["_end"].isoformat(),
                    "backgroundColor": colour,
                    "borderColor": colour,
                }
            )

        # All shifts use one chronological list view.
        # Night shifts cover both dates across midnight.
        overnight_shift = (
            selected["end"].date() != selected["start"].date()
        )
        calendar_days = 2 if overnight_shift else 1

        calendar_options = {
            "initialView": "listShift",
            "views": {
                "listShift": {
                    "type": "list",
                    "duration": {"days": calendar_days},
                },
            },
            "initialDate": selected["start"].date().isoformat(),
            "headerToolbar": {
                "left": "",
                "center": "title",
                "right": "",
            },
            "height": 560,
            "nowIndicator": True,
            "timeZone": "local",
            "firstDay": 1,
        }

        st.caption(
            "All bookings are displayed in one chronological column. "
            "Night shifts continue across midnight."
        )

        calendar_rendered = False

        try:
            calendar(
                events=calendar_events,
                options=calendar_options,
                key=f"calendar_{selected['key']}",
            )
            calendar_rendered = True
        except Exception as exc:
            st.info(
                "The chronological booking list could not render. "
                f"Showing the text fallback instead. Details: {exc}"
            )

        # Only show the text-list fallback if the main list fails to render.
        # This avoids showing a duplicate booking list during normal use.
        if not calendar_rendered:
            if not shift_events:
                st.caption("No calendar bookings overlap this shift.")

            for event in sorted(
                shift_events,
                key=lambda item: item["_start"],
            ):
                title_text = str(event.get("title") or "Booking")
                source = str(
                    event.get("source")
                    or event.get("load_header")
                    or event.get("category")
                    or "Transport"
                ).strip()

                search_text = f"{source} {title_text}".casefold()

                if "campey" in search_text:
                    fallback_title = "Campeys"
                elif "dhl" in search_text:
                    fallback_title = "DHL"
                elif "bulk" in search_text:
                    fallback_title = f"{title_text} · Bulk"
                else:
                    fallback_title = f"{title_text} · {source}"

                st.markdown(
                    f"**{fallback_title}**  \n"
                    f"{event['_start'].strftime('%a %H:%M')}–"
                    f"{event['_end'].strftime('%a %H:%M')}"
                )

        st.markdown("**Planned transport totals**")
        st.metric("Bulk inbound", planned["inbound_planned"])
        st.metric("Campeys outbound", planned["outbound_planned_campeys"])
        st.metric("DHL outbound", planned["outbound_planned_dhl"])

    st.caption(
        "Team-colour rotation is anchored to the week beginning "
        "12 October 2026. Transport booking counts include bookings "
        "overlapping the selected shift. Events without an end time "
        "use a one-hour fallback window."
    )


if __name__ == "__main__":
    main()