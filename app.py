
from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime, timedelta

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
from if_down import render_if_down_list, render_if_down_management
from bulk_email import (
    load_bulk_email_settings,
    save_bulk_email_settings,
    send_bulk_delivery_email,
)

st.set_page_config(
    page_title="Warehouse Shift Hub",
    page_icon="🏭",
    layout="wide",
)

# Compact landscape layout: smaller header, reduced top whitespace,
# shorter widgets, tighter spacing, and maximum usable screen width.
st.markdown(
    """
    <style>
    /* Main page width and top spacing */
    .stApp [data-testid="stMainBlockContainer"],
    .main .block-container,
    .block-container {
        max-width: 100% !important;
        padding-top: 0rem !important;
        padding-bottom: .45rem !important;
        padding-left: .8rem !important;
        padding-right: .8rem !important;
    }

    /* Reduce the Streamlit top toolbar height */
    [data-testid="stHeader"] {
        height: 1.5rem !important;
        min-height: 1.5rem !important;
        background: transparent !important;
    }

    /* Compact all headings */
    h1, [data-testid="stHeading"] h1 {
        font-size: 1.15rem !important;
        line-height: 1.25 !important;
        margin-top: 0 !important;
        margin-bottom: .2rem !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }

    h2, [data-testid="stHeading"] h2 {
        font-size: 1rem !important;
        line-height: 1.25 !important;
        margin-top: .15rem !important;
        margin-bottom: .2rem !important;
    }

    h3, [data-testid="stHeading"] h3 {
        font-size: .9rem !important;
        line-height: 1.25 !important;
        margin-top: .15rem !important;
        margin-bottom: .15rem !important;
    }

    /* Reduce vertical whitespace */
    div[data-testid="stVerticalBlock"] {
        gap: .25rem !important;
    }

    div[data-testid="stHorizontalBlock"] {
        gap: .5rem !important;
    }

    div[data-testid="stMarkdownContainer"] p {
        margin-top: 0 !important;
        margin-bottom: .12rem !important;
        font-size: .86rem;
    }

    [data-testid="stCaptionContainer"] {
        font-size: .72rem !important;
        line-height: 1.25 !important;
        margin-top: 0 !important;
        margin-bottom: .1rem !important;
    }

    [data-testid="stWidgetLabel"] {
        padding-bottom: 0 !important;
    }

    [data-testid="stWidgetLabel"] p {
        font-size: .75rem !important;
        margin-bottom: 0 !important;
    }

    /* Shorter text, number and selection fields */
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input {
        min-height: 1.8rem !important;
        height: 1.8rem !important;
        font-size: .85rem !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] {
        min-height: 1.8rem !important;
        font-size: .82rem !important;
    }

    div[data-testid="stTextArea"] textarea {
        min-height: 2.5rem !important;
        font-size: .82rem !important;
    }

    /* Smaller buttons and less padding */
    .stButton button,
    .stFormSubmitButton button {
        min-height: 1.8rem !important;
        padding: .2rem .5rem !important;
        font-size: .8rem !important;
    }

    [data-testid="stMetric"] {
        padding: .1rem .25rem !important;
    }

    [data-testid="stMetricValue"] {
        font-size: 1.15rem !important;
    }

    [data-testid="stMetricLabel"] {
        font-size: .72rem !important;
    }

    /* Less whitespace around separators and tabs */
    hr {
        margin: .25rem 0 !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: .25rem !important;
    }

    .stTabs [data-baseweb="tab"] {
        padding: .3rem .55rem !important;
        font-size: .8rem !important;
    }

    [data-testid="stExpander"] details {
        padding: .25rem .5rem !important;
    }

    /* Compact sidebar */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: .25rem !important;
    }

    /* Landscape tablet and smaller desktop screens */
    @media (min-width: 900px) {
        .stApp [data-testid="stMainBlockContainer"],
        .block-container {
            padding-top: 0 !important;
            padding-left: .6rem !important;
            padding-right: .6rem !important;
        }
    }

    @media (max-width: 1100px) {
        h1, [data-testid="stHeading"] h1 {
            font-size: 1.05rem !important;
        }

        h2, [data-testid="stHeading"] h2 {
            font-size: .95rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
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


def choose_default_shift(now: datetime, events: list[dict] | None = None) -> dict:
    shifts = shifts_between(
        now.date() - timedelta(days=14),
        now.date() + timedelta(days=35),
    )
    active = [s for s in shifts if s["start"] <= now < s["end"]]
    if active:
        return active[0]

    if events:
        future_with_events = [
            shift for shift in shifts
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


def submission_for(submissions: dict[str, dict], shift: dict) -> dict | None:
    target = shift["start"].isoformat()
    for key, row in submissions.items():
        dt = parse_dt(key)
        if dt and dt == shift["start"]:
            return row
    return submissions.get(target)


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


def autosave_shift_answers():
    """Persist the current shift snapshot whenever an editable field changes."""
    sb = st.session_state.get("supabase_client")
    selected = st.session_state.get("_autosave_selected_shift")
    questions = st.session_state.get("_autosave_questions") or []
    planned = st.session_state.get("_autosave_planned") or {}
    cil_planned = bool(st.session_state.get("_autosave_cil_planned", False))
    shift_key = st.session_state.get("_active_shift_key")

    if not sb or not selected or not questions or not shift_key:
        return

    values = {}

    for question in questions:
        key = question["question_key"]
        kind = question.get("input_type", "text")

        if kind == "auto":
            values[key] = planned.get(key, 0)
        elif key == "cil_completion" and not cil_planned:
            values[key] = "N/A"
        else:
            widget_key = f"input_{shift_key}_{key}"
            value = st.session_state.get(widget_key, "")

            if value == "— Select —":
                value = ""

            values[key] = value

    snapshot = snapshot_answers(questions, values, planned, cil_planned)
    submitter = str(
        st.session_state.get(f"submitter_{shift_key}", "") or ""
    ).strip()

    row = {
        "shift_date": selected["shift_date"],
        "shift_name": selected["shift_name"],
        "shift_start": selected["start"].isoformat(),
        "shift_end": selected["end"].isoformat(),
        "team_colour": selected["team_colour"],
        "answers": snapshot,
        "submitted_by": submitter or None,
        "updated_at": datetime.now(UK).isoformat(),
    }

    try:
        row_id_key = f"_autosave_row_id_{shift_key}"
        row_id = st.session_state.get(row_id_key)

        # Recover an existing row before inserting. This also helps if the
        # insert succeeded but the previous response did not return its ID.
        if not row_id:
            matches = (
                sb.table("shift_submissions")
                .select("id")
                .eq("shift_start", selected["start"].isoformat())
                .limit(1)
                .execute()
                .data
                or []
            )
            if matches and matches[0].get("id"):
                row_id = matches[0]["id"]
                st.session_state[row_id_key] = row_id

        if row_id:
            sb.table("shift_submissions").update(row).eq(
                "id", row_id
            ).execute()
        else:
            result = sb.table("shift_submissions").insert(row).execute()
            inserted = result.data or []

            if inserted and inserted[0].get("id"):
                st.session_state[row_id_key] = inserted[0]["id"]

        st.session_state["_autosave_last_error"] = ""
        st.session_state["_autosave_last_saved"] = datetime.now(UK).strftime(
            "%H:%M:%S"
        )
    except Exception as exc:
        st.session_state["_autosave_last_error"] = str(exc)


# ---------------------------------------------------------------------
# Answer colours and historical display
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


def format_shift_heading(shift: dict) -> str:
    return (
        f"{shift['start'].strftime('%a %d %b')} · "
        f"{shift.get('shift_name', 'Shift')}"
    )


def render_historical_answer(question: dict, snapshot: dict):
    key = question["question_key"]
    item = snapshot.get(key) or {}

    if not item:
        st.caption("—")
        return

    value = item.get("value", "")
    if value is None or value == "":
        value = "Blank"

    answer_values = {
        answer_key: (answer_item or {}).get("value", "")
        for answer_key, answer_item in snapshot.items()
    }

    planned = {}
    for planned_key in (
        "inbound_planned",
        "outbound_planned_campeys",
        "outbound_planned_dhl",
    ):
        planned_item = snapshot.get(planned_key) or {}
        planned_value = planned_item.get("value")
        if planned_value not in (None, "", "N/A"):
            try:
                planned[planned_key] = int(planned_value)
            except (TypeError, ValueError):
                pass

    colour = answer_colour(
        {"rules": item.get("rules") or question.get("rules") or {}},
        item.get("value", ""),
        planned,
        answer_values,
    )

    if item.get("value") == "N/A":
        colour = "grey"

    st.markdown(badge_text(value, colour))


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
            "Enter the management password to manage questions, sidebar "
            "links and If Down tasks."
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

    questions_tab, links_tab, if_down_tab, bulk_email_tab = st.tabs([
        "Questions",
        "Sidebar links",
        "If Down To Do Lists",
        "Bulk email",
    ])

    with if_down_tab:
        if st.button("Lock management area", key="lock_if_down_management"):
            st.session_state.management_authenticated = False
            st.rerun()
        render_if_down_management(sb)

    with bulk_email_tab:
        st.subheader("Bulk delivery email settings")
        st.caption(
            "Configure the main recipient and optional CC recipients for "
            "emails sent from Bulk schedule deliveries."
        )

        try:
            settings = load_bulk_email_settings(sb)

            with st.form("bulk_email_settings_form"):
                to_email = st.text_input(
                    "Main recipient (To)",
                    value=settings.get("to_email", ""),
                    placeholder="recipient@example.com",
                )
                cc_emails = st.text_area(
                    "CC recipients",
                    value=", ".join(settings.get("cc_emails", [])),
                    placeholder="person1@example.com, person2@example.com",
                    help="Separate multiple addresses with commas, semicolons, or new lines.",
                )
                save_settings = st.form_submit_button(
                    "Save Bulk email settings",
                    type="primary",
                )

            if save_settings:
                try:
                    saved_settings = save_bulk_email_settings(
                        sb, to_email, cc_emails
                    )
                    st.success("Bulk email settings saved.")
                    st.write(f"**To:** {saved_settings['to_email']}")
                    st.write(
                        "**CC:** "
                        + (", ".join(saved_settings["cc_emails"]) or "None")
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Could not save Bulk email settings: {exc}")
        except Exception as exc:
            st.error(f"Could not load Bulk email settings: {exc}")
            st.info(
                "Check that bulk_email_settings exists in Supabase and that "
                "the app uses the Supabase service-role key."
            )

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
                        st.error("Enter a URL starting with https:// or http://.")
                    else:
                        sb.table("navigation_links").insert({
                            "label": label.strip(),
                            "url": url.strip(),
                            "sort_order": int(order),
                            "is_active": True,
                        }).execute()
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
                            "Link name", value=link.get("label", "")
                        )
                        edit_url = st.text_input(
                            "Website URL", value=link.get("url", "")
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

                    if save_link:
                        if not edit_label.strip():
                            st.error("Enter a name for the link.")
                        elif not valid_navigation_url(edit_url.strip()):
                            st.error("Enter a URL starting with https:// or http://.")
                        else:
                            sb.table("navigation_links").update({
                                "label": edit_label.strip(),
                                "url": edit_url.strip(),
                                "sort_order": int(edit_order),
                                "is_active": active,
                            }).eq("id", link["id"]).execute()
                            st.success("Link updated.")
                            st.rerun()

                    if st.button("Delete link", key=f"delete_link_{link['id']}"):
                        sb.table("navigation_links").delete().eq(
                            "id", link["id"]
                        ).execute()
                        st.success("Link deleted.")
                        st.rerun()

        except Exception as exc:
            st.error(f"Could not manage sidebar links: {exc}")
            st.info("Check that the navigation_links table exists in Supabase.")

    with questions_tab:
        st.subheader("Manage shift questions")

        if st.button("Lock management area", key="lock_questions_management"):
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
                            [int(q.get("sort_order", 0)) for q in questions]
                            + [0]
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
                        "Question text", value=question["label"]
                    )
                    old_section = question.get("section", "General")
                    section_index = (
                        sections.index(old_section)
                        if old_section in sections else 4
                    )
                    new_section = st.selectbox(
                        "Section", sections, index=section_index
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
                        if old_rule in rules_list else 0,
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
                        "Active", value=bool(question.get("is_active", True))
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
                            options = old_options if new_type == "auto" else []

                        rules = dict(old_rules)
                        rules["colour_rule"] = new_rule

                        sb.table("shift_questions").update({
                            "label": new_label.strip(),
                            "section": new_section,
                            "input_type": new_type,
                            "rules": rules,
                            "options": options,
                            "sort_order": int(order),
                            "is_active": active,
                        }).eq("id", question["id"]).execute()
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
    compact: bool = False,
):
    key = question["question_key"]
    label = question["label"]
    kind = question.get("input_type", "text")
    rules = question.get("rules") or {}
    label_visibility = "collapsed" if compact else "visible"
    shift_key = st.session_state.get("_active_shift_key", "unknown")
    widget_key = f"input_{shift_key}_{key}"

    if kind == "auto":
        value = planned.get(key, 0)
        st.text_input(
            label,
            value=str(value),
            disabled=True,
            key=f"auto_{shift_key}_{key}_{value}",
            label_visibility=label_visibility,
        )
        return value

    if key == "cil_completion" and not cil_planned:
        st.text_input(
            label,
            value="N/A — no CIL planned",
            disabled=True,
            key=f"cil_na_{shift_key}_{key}",
            label_visibility=label_visibility,
        )
        return "N/A"

    if kind == "number":
        value = (
            current_value
            if isinstance(current_value, (int, float))
            else None
        )
        return st.number_input(
            label,
            min_value=int(rules.get("min", 0)),
            max_value=int(rules.get("max", 999999)),
            value=value,
            step=1,
            key=widget_key,
            disabled=disabled,
            on_change=autosave_shift_answers,
            help="Leave blank if nothing was recorded.",
            label_visibility=label_visibility,
        )

    if kind == "percentage":
        value = current_value if isinstance(current_value, (int, float)) else None
        return st.number_input(
            label,
            min_value=0,
            max_value=100,
            value=value,
            step=1,
            key=widget_key,
            disabled=disabled,
            on_change=autosave_shift_answers,
            help="Enter 0–100%. 100% is green; below 100% is red.",
            label_visibility=label_visibility,
        )

    if kind in ("choice", "yes_no"):
        options = question.get("options") or ["L", "M", "H"]
        if kind == "yes_no":
            options = ["Yes", "No"]

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
            key=widget_key,
            disabled=disabled,
            on_change=autosave_shift_answers,
            label_visibility=label_visibility,
        )
        return "" if selected == "— Select —" else selected

    return st.text_area(
        label,
        value=str(current_value or ""),
        key=widget_key,
        disabled=disabled,
        on_change=autosave_shift_answers,
        height=48 if compact else 72,
        label_visibility=label_visibility,
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
        active_links = [link for link in links if link.get("is_active", True)]

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

        if st.button("Refresh calendar and records", use_container_width=True):
            st.session_state.pop("selected_shift_key", None)
            st.rerun()

    if page == "Management":
        if require_management_password():
            management_page(sb, questions)
        return

    active_questions = sorted(
        [
            question for question in questions
            if question.get("is_active", True)
        ],
        key=lambda question: int(question.get("sort_order", 0)),
    )

    events = load_events(sb)
    now = datetime.now(UK)

    if "selected_shift_key" not in st.session_state:
        st.session_state.selected_shift_key = choose_default_shift(
            now, events
        )["key"]

    candidates = shifts_between(
        now.date() - timedelta(days=35),
        now.date() + timedelta(days=35),
    )
    selected = next(
        (
            shift for shift in candidates
            if shift["key"] == st.session_state.selected_shift_key
        ),
        None,
    )

    if selected is None:
        selected = choose_default_shift(now, events)
        st.session_state.selected_shift_key = selected["key"]

    all_shifts, shift_index = adjacent_shifts(selected)

    previous_button, next_button = st.columns(2, gap="small")

    with previous_button:
        if shift_index > 0 and st.button(
            "← Previous shift", use_container_width=True
        ):
            st.session_state.selected_shift_key = all_shifts[shift_index - 1]["key"]
            st.rerun()

    with next_button:
        if shift_index + 1 < len(all_shifts) and st.button(
            "Next shift →", use_container_width=True
        ):
            st.session_state.selected_shift_key = all_shifts[shift_index + 1]["key"]
            st.rerun()

    # Simplified team tag: date, shift name and team only.
    team_name = str(selected.get("team_colour") or "Team")
    team_hex = COLOUR_HEX.get(team_name, "#666666")
    shift_date_label = selected["start"].strftime("%a %d %b %Y")
    shift_name_label = str(selected.get("shift_name") or "Shift")

    st.markdown(
        f"""
        <div style="
            display:inline-flex;
            align-items:center;
            gap:7px;
            padding:4px 9px;
            border-radius:5px;
            background:{team_hex}18;
            border-left:4px solid {team_hex};
            margin:0 0 4px 0;
            font-size:.78rem;
            line-height:1.25;
        ">
            <span style="font-weight:600">{shift_date_label}</span>
            <span>·</span>
            <span>{shift_name_label}</span>
            <span>·</span>
            <span style="font-weight:700;color:{team_hex}">{team_name} Team</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    shift_events = events_for_shift(events, selected)
    planned = planned_counts(events, selected)
    cil_planned = cil_is_planned(events, selected)
    submissions = load_submissions(sb, selected["start"])
    saved = submission_for(submissions, selected)
    saved_answers = (saved or {}).get("answers") or {}

    previous_shifts = [
        shift for shift in all_shifts[:shift_index]
        if shift["end"] <= selected["start"]
    ][-3:]

    # Context used by the autosave callback.
    st.session_state["_active_shift_key"] = selected["key"]
    st.session_state["_autosave_selected_shift"] = selected
    st.session_state["_autosave_questions"] = active_questions
    st.session_state["_autosave_planned"] = planned
    st.session_state["_autosave_cil_planned"] = cil_planned

    row_id_key = f"_autosave_row_id_{selected['key']}"
    if (saved or {}).get("id"):
        st.session_state[row_id_key] = saved["id"]

    left, right = st.columns([1.35, 1.0], gap="small")

    with left:
        st.subheader("Shift questions and answers")
        st.caption(
            "Answers save automatically when you change a field. "
            "Previous answers are read-only."
        )

        submitter_key = f"submitter_{selected['key']}"
        if submitter_key not in st.session_state:
            st.session_state[submitter_key] = (
                (saved or {}).get("submitted_by") or ""
            )

        st.text_input(
            "Recorded by (optional)",
            key=submitter_key,
            on_change=autosave_shift_answers,
        )

        historical = sorted(previous_shifts, key=lambda shift: shift["start"])
        while len(historical) < 3:
            historical.insert(0, None)

        headings = ["Question"]
        for index, past_shift in enumerate(historical):
            headings.append(
                format_shift_heading(past_shift)
                if past_shift else f"Previous {3 - index}"
            )
        headings.append(format_shift_heading(selected))

        widths = [1.55, .78, .78, .78, 1.0]
        header_columns = st.columns(widths, gap="small")
        for column, heading in zip(header_columns, headings):
            with column:
                st.markdown(f"**{heading}**")

        st.divider()

        sections = list(dict.fromkeys(
            question.get("section", "General")
            for question in active_questions
        ))

        for section in sections:
            st.markdown(f"### {section}")

            for question in active_questions:
                if question.get("section", "General") != section:
                    continue

                key = question["question_key"]
                columns = st.columns(widths, gap="small")

                with columns[0]:
                    st.markdown(f"**{question['label']}**")

                for index, past_shift in enumerate(historical):
                    with columns[index + 1]:
                        if past_shift is None:
                            st.caption("—")
                            continue

                        past_submission = submission_for(submissions, past_shift)
                        past_answers = (
                            (past_submission or {}).get("answers") or {}
                        )
                        render_historical_answer(question, past_answers)

                with columns[4]:
                    old = saved_answers.get(key) or {}
                    old_value = old.get("value", "")

                    if question.get("input_type") == "auto":
                        old_value = planned.get(key, 0)

                    widget_key = f"input_{selected['key']}_{key}"

                    # Initialise each shift's widget once from the saved data.
                    if widget_key not in st.session_state:
                        kind = question.get("input_type", "text")
                        if kind in ("choice", "yes_no") and old_value in (None, ""):
                            st.session_state[widget_key] = "— Select —"
                        elif kind in ("number", "percentage") and not isinstance(
                            old_value, (int, float)
                        ):
                            st.session_state[widget_key] = None
                        else:
                            st.session_state[widget_key] = old_value

                    render_question_input(
                        question,
                        old_value,
                        planned,
                        cil_planned,
                        compact=True,
                    )

        last_saved = st.session_state.get("_autosave_last_saved")
        last_error = st.session_state.get("_autosave_last_error", "")

        if last_error:
            st.error(f"Automatic save failed: {last_error}")
        elif last_saved:
            st.caption(f"Last automatically saved at {last_saved} (UK time).")

    # -----------------------------------------------------------------
    # Transport list, If Down list and totals — RIGHT
    # -----------------------------------------------------------------

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
                source + " " + title_text + " "
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

            if source == "Campey":
                display_title = "Campeys"
            elif source == "DHL":
                display_title = "DHL"
            else:
                display_title = f"{title_text or 'Booking'} · {source}"

            calendar_events.append({
                "id": str(event.get("id", "")),
                "title": display_title,
                "start": event["_start"].isoformat(),
                "end": event["_end"].isoformat(),
                "backgroundColor": colour,
                "borderColor": colour,
            })

        overnight_shift = selected["end"].date() != selected["start"].date()
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
            "height": 350,
            "nowIndicator": True,
            "timeZone": "local",
            "firstDay": 1,
        }

        st.caption(
            "Bookings are displayed in one chronological column. "
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

        if not calendar_rendered:
            if not shift_events:
                st.caption("No calendar bookings overlap this shift.")

            for event in sorted(shift_events, key=lambda item: item["_start"]):
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

        # Bulk delivery email controls.
        st.markdown("### Bulk delivery emails")

        bulk_events = []
        for event in shift_events:
            source_text = " ".join(
                str(event.get(field) or "")
                for field in ("source", "load_header", "category", "title")
            ).casefold()
            if "bulk" in source_text:
                bulk_events.append(event)

        if not bulk_events:
            st.caption("No Bulk deliveries overlap this shift.")
        else:
            for event in sorted(
                bulk_events,
                key=lambda item: item.get("_start") or selected["start"],
            ):
                event_id = str(event.get("id") or "").strip()
                title_text = str(event.get("title") or "Bulk delivery")
                event_start = event.get("_start")
                time_label = (
                    event_start.astimezone(UK).strftime("%a %d %b %Y, %H:%M")
                    if event_start else "Time not available"
                )

                with st.container(border=True):
                    st.markdown(f"**{title_text}**")
                    st.caption(time_label)

                    if not event_id:
                        st.error(
                            "This booking has no event ID and cannot be "
                            "emailed safely."
                        )
                        continue

                    if st.button(
                        "Email delivery details",
                        key=f"email_bulk_delivery_{event_id}",
                        use_container_width=True,
                    ):
                        with st.spinner("Sending Bulk delivery email..."):
                            success, message = send_bulk_delivery_email(
                                sb, event, event_id
                            )

                        if success:
                            st.success(message)
                        elif "already been emailed" in message.casefold():
                            st.info(message)
                        else:
                            st.error(message)

        render_if_down_list(sb, selected)

        st.markdown("**Planned transport totals**")
        metric_columns = st.columns(3, gap="small")
        with metric_columns[0]:
            st.metric("Bulk inbound", planned["inbound_planned"])
        with metric_columns[1]:
            st.metric("Campeys outbound", planned["outbound_planned_campeys"])
        with metric_columns[2]:
            st.metric("DHL outbound", planned["outbound_planned_dhl"])

    st.caption(
        "Team-colour rotation is anchored to the week beginning "
        "12 October 2026. Transport booking counts include bookings "
        "overlapping the selected shift. Events without an end time "
        "use a one-hour fallback window."
    )


if __name__ == "__main__":
    main()
