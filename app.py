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


def get_supabase() -> Client:
    if "supabase_client" not in st.session_state:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "")

        if not url or not key:
            raise RuntimeError(
                "Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
                "to your Streamlit secrets."
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
    return url.startswith(("https://", "http://"))def load_events(sb: Client) -> list[dict]:
    """
    Load events from Supabase.

    FIX: Events with a start but no end are no longer discarded.
    A missing end gets a one-hour fallback window.
    """
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

        # Campeys and other events with NULL end values are retained.
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
    """
    Match a provider using source, title, load header and other
    descriptive fields, rather than relying on source alone.
    """
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
        breakdowns = answers.get("breakdowns", 0)

        try:
            breakdown_count = (
                float(breakdowns)
                if breakdowns not in (None, "")
                else 0
            )
        except (TypeError, ValueError):
            breakdown_count = 0

        # IPS is green if there were no breakdowns.
        if breakdown_count <= 0:
            return "green"

        try:
            ips_count = (
                float(value)
                if value not in (None, "")
                else 0
            )
        except (TypeError, ValueError):
            ips_count = 0

        # If breakdowns occurred, IPS must be greater than zero.
        return "green" if ips_count > 0 else "red"

    if value is None or value == "":
        if rule == "zero_green":
            return "green"

        if rule in ("positive_green", "staff_under_three_red"):
            return "red"

        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None

    if rule == "severity_lmh":
        return {
            "L": "green",
            "M": "amber",
            "H": "red",
        }.get(str(value).upper())

    if rule == "zero_green" and number is not None:
        return "green" if number == 0 else "red"

    if rule == "positive_green" and number is not None:
        return "green" if number >= 1 else "red"

    if rule == "hundred_green" and number is not None:
        return "green" if number >= 100 else "red"

    if rule == "staff_under_three_red" and number is not None:
        return "red" if number < 3 else "green"

    matching_rules = {
        "matches_inbound_planned": "inbound_planned",
        "matches_campey_planned": "outbound_planned_campeys",
        "matches_dhl_planned": "outbound_planned_dhl",
    }

    if rule in matching_rules and number is not None:
        target = planned.get(matching_rules[rule], 0)
        return "green" if number == target else "red"

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
    snapshot = {}

    for question in questions:
        key = question["question_key"]

        if key == "cil_completion" and not cil_planned:
            value = "N/A"
        elif question.get("input_type") == "auto":
            value = planned.get(key, 0)
        else:
            value = values.get(key, "")

        snapshot[key] = {
            "label": question["label"],
            "value": value,
            "section": question.get("section", "General"),
            "rules": question.get("rules") or {},
            "input_type": question.get("input_type", "text"),
        }

    return snapshot


def render_saved_answers(snapshot: dict, planned: dict | None = None):
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
            )def slugify(text: str) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "_",
        text.casefold(),
    ).strip("_")

    return slug[:48] or "question"


def require_management_password() -> bool:
    expected = str(st.secrets.get("MANAGEMENT_PASSWORD", ""))

    if not expected:
        st.error(
            "Set MANAGEMENT_PASSWORD in Streamlit secrets first."
        )
        return False

    if st.session_state.get("management_authenticated"):
        return True

    with st.form("management_login"):
        st.write("Enter the management password.")
        password = st.text_input(
            "Management password",
            type="password",
        )
        submitted = st.form_submit_button("Unlock")

    if submitted:
        if hmac.compare_digest(password, expected):
            st.session_state.management_authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


def management_page(sb: Client, questions: list[dict]):
    st.title("Management")

    questions_tab, links_tab = st.tabs(
        ["Questions", "Sidebar links"]
    )

    with links_tab:
        st.subheader("Manage sidebar links")
        links = load_navigation_links(sb)

        with st.form("add_link_form", clear_on_submit=True):
            label = st.text_input("Link name")
            url = st.text_input(
                "Website URL",
                placeholder="https://...",
            )
            order = st.number_input(
                "Display order",
                min_value=0,
                max_value=10000,
                value=100,
                step=10,
            )
            add_link = st.form_submit_button("Add link")

        if add_link:
            if not label.strip():
                st.error("Enter a link name.")
            elif not valid_navigation_url(url.strip()):
                st.error("Enter a valid http:// or https:// URL.")
            else:
                sb.table("navigation_links").insert(
                    {
                        "label": label.strip(),
                        "url": url.strip(),
                        "sort_order": int(order),
                        "is_active": True,
                    }
                ).execute()
                st.success("Link added.")
                st.rerun()

        for link in links:
            with st.expander(link.get("label", "Link")):
                with st.form(f"edit_link_{link['id']}"):
                    new_label = st.text_input(
                        "Name",
                        value=link.get("label", ""),
                    )
                    new_url = st.text_input(
                        "URL",
                        value=link.get("url", ""),
                    )
                    new_order = st.number_input(
                        "Display order",
                        min_value=0,
                        max_value=10000,
                        value=int(link.get("sort_order", 100)),
                        step=10,
                    )
                    active = st.checkbox(
                        "Show in sidebar",
                        value=bool(link.get("is_active", True)),
                    )
                    save = st.form_submit_button("Save link")

                if save:
                    if not new_label.strip():
                        st.error("Enter a link name.")
                    elif not valid_navigation_url(new_url.strip()):
                        st.error("Enter a valid URL.")
                    else:
                        sb.table("navigation_links").update(
                            {
                                "label": new_label.strip(),
                                "url": new_url.strip(),
                                "sort_order": int(new_order),
                                "is_active": active,
                            }
                        ).eq("id", link["id"]).execute()
                        st.success("Link updated.")
                        st.rerun()

                if st.button(
                    "Delete link",
                    key=f"delete_link_{link['id']}",
                ):
                    sb.table("navigation_links").delete().eq(
                        "id", link["id"]
                    ).execute()
                    st.success("Link deleted.")
                    st.rerun()

    with questions_tab:
        st.subheader("Manage shift questions")

        if st.button("Lock management area"):
            st.session_state.management_authenticated = False
            st.rerun()

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
            rule = st.selectbox(
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
                "Choice options (comma-separated)"
            )
            required = st.checkbox("Required")
            add_question = st.form_submit_button("Add question")

        if add_question:
            if not label.strip():
                st.error("Enter question text.")
            else:
                existing = {
                    q["question_key"] for q in questions
                }
                base = slugify(label)
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

                sb.table("shift_questions").insert(
                    {
                        "id": str(uuid.uuid4()),
                        "question_key": key,
                        "label": label.strip(),
                        "section": section,
                        "input_type": input_type,
                        "rules": {
                            "colour_rule": rule,
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
                ).execute()

                st.success("Question added.")
                st.rerun()

        st.subheader("Existing questions")

        for question in questions:
            with st.expander(question["label"]):
                with st.form(f"question_{question['id']}"):
                    new_label = st.text_input(
                        "Question",
                        value=question["label"],
                    )
                    active = st.checkbox(
                        "Active",
                        value=bool(question.get("is_active", True)),
                    )
                    order = st.number_input(
                        "Display order",
                        min_value=0,
                        max_value=10000,
                        value=int(question.get("sort_order", 0)),
                        step=10,
                    )
                    save = st.form_submit_button("Save")

                if save:
                    if not new_label.strip():
                        st.error("Question cannot be blank.")
                    else:
                        sb.table("shift_questions").update(
                            {
                                "label": new_label.strip(),
                                "is_active": active,
                                "sort_order": int(order),
                            }
                        ).eq("id", question["id"]).execute()
                        st.success("Question updated.")
                        st.rerun()


def render_question_input(
    question: dict,
    current_value,
    planned: dict,
    cil_planned: bool,
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
            key=f"input_{key}",
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
        )

    if kind in ("choice", "yes_no"):
        options = (
            ["Yes", "No"]
            if kind == "yes_no"
            else question.get("options") or ["L", "M", "H"]
        )

        choices = ["— Select —"] + options
        existing = (
            str(current_value)
            if current_value not in (None, "")
            else "— Select —"
        )

        selected = st.selectbox(
            label,
            choices,
            index=choices.index(existing)
            if existing in choices
            else 0,
            key=f"input_{key}",
        )

        return "" if selected == "— Select —" else selected

    return st.text_area(
        label,
        value=str(current_value or ""),
        key=f"input_{key}",
        height=80,
    )


def main():
    st.title("🏭 Warehouse Shift Hub")
    st.caption(
        "Shift handover, performance checks and transport bookings"
    )

    try:
        sb = get_supabase()
        questions = load_questions(sb)
    except Exception as exc:
        st.error(f"App setup problem: {exc}")
        st.info("Check your Streamlit secrets and database setup.")
        st.stop()

    with st.sidebar:
        st.header("Navigation")

        page = st.radio(
            "Open",
            ["Shift workspace", "Management"],
            label_visibility="collapsed",
        )

        links = load_navigation_links(sb)
        active_links = [
            link for link in links
            if link.get("is_active", True)
        ]

        if active_links:
            st.divider()
            st.subheader("Team links")

            for link in active_links:
                url = str(link.get("url") or "")
                label = str(link.get("label") or "Open link")

                if valid_navigation_url(url):
                    st.markdown(f"- [{label}]({url})")

        st.divider()

        if st.button("Refresh app", use_container_width=True):
            st.rerun()

    if page == "Management":
        if require_management_password():
            management_page(sb, questions)
        return

    active_questions = sorted(
        [
            q for q in questions
            if q.get("is_active", True)
        ],
        key=lambda q: int(q.get("sort_order", 0)),
    )

    events = load_events(sb)
    now = datetime.now(UK)

    if "selected_shift_key" not in st.session_state:
        st.session_state.selected_shift_key = (
            choose_default_shift(now)["key"]
        )

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
        selected = choose_default_shift(now)
        st.session_state.selected_shift_key = selected["key"]

    all_shifts, shift_index = adjacent_shifts(selected)

    previous_col, next_col = st.columns(2)

    with previous_col:
        if shift_index > 0 and st.button("← Previous shift"):
            st.session_state.selected_shift_key = (
                all_shifts[shift_index - 1]["key"]
            )
            st.rerun()

    with next_col:
        if (
            shift_index + 1 < len(all_shifts)
            and st.button("Next shift →")
        ):
            st.session_state.selected_shift_key = (
                all_shifts[shift_index + 1]["key"]
            )
            st.rerun()

    team_hex = COLOUR_HEX.get(
        selected["team_colour"],
        "#666666",
    )

    st.markdown(
        f"<div style='padding:12px 16px;border-radius:10px;"
        f"background:{team_hex}18;border-left:6px solid {team_hex};"
        f"margin-bottom:14px'>"
        f"<b>{selected['label']}</b><br>"
        f"{selected['start'].strftime('%a %d %b %Y, %H:%M')} – "
        f"{selected['end'].strftime('%a %d %b %Y, %H:%M')} UK time"
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
        shift for shift in all_shifts[:shift_index]
        if shift["end"] <= selected["start"]
    ][-3:]

    left, right = st.columns([0.92, 1.45], gap="large")

    with left:
        st.subheader("Transport calendar")
        st.caption("Bookings overlapping this shift are shown.")

        calendar_events = []

        for event in shift_events:
            title = str(event.get("title") or "Booking")
            source = str(
                event.get("source")
                or event.get("load_header")
                or event.get("category")
                or ""
            )

            text = (
                source + " " + title + " "
                + str(event.get("load_header") or "")
            ).casefold()

            if "campey" in text:
                source = "Campey"
            elif "dhl" in text:
                source = "DHL"
            elif "bulk" in text:
                source = "Bulk"
            elif not source:
                source = "Manual"

            colour = event.get("colour") or {
                "Campey": COLOUR_HEX["Blue"],
                "Bulk": COLOUR_HEX["Yellow"],
                "DHL": COLOUR_HEX["Blue"],
            }.get(source, "#777777")

            calendar_events.append(
                {
                    "id": str(event.get("id", "")),
                    "title": f"{title} · {source}",
                    "start": event["_start"].isoformat(),
                    "end": event["_end"].isoformat(),
                    "backgroundColor": colour,
                    "borderColor": colour,
                }
            )

        calendar_options = {
            "initialView": "timeGridDay",
            "initialDate": selected["start"].date().isoformat(),
            "headerToolbar": {
                "left": "",
                "center": "title",
                "right": "",
            },
            "height": 560,
            "allDaySlot": False,
            "nowIndicator": True,
            "timeZone": "local",
            "firstDay": 1,
        }

        try:
            calendar(
                events=calendar_events,
                options=calendar_options,
                key=f"calendar_{selected['key']}",
            )
        except Exception as exc:
            st.warning(f"Calendar could not render: {exc}")

        with st.expander(
            f"Bookings in this shift ({len(shift_events)})",
            expanded=True,
        ):
            if not shift_events:
                st.caption("No bookings overlap this shift.")

            for event in sorted(
                shift_events,
                key=lambda item: item["_start"],
            ):
                st.markdown(
                    f"**{event.get('title') or 'Booking'}**  \n"
                    f"{event['_start'].strftime('%a %H:%M')}–"
                    f"{event['_end'].strftime('%a %H:%M')} · "
                    f"{event.get('source') or event.get('load_header') or 'Transport'}"
                )

        st.markdown("**Planned transport totals**")
        st.metric("Bulk inbound", planned["inbound_planned"])
        st.metric(
            "Campeys outbound",
            planned["outbound_planned_campeys"],
        )
        st.metric(
            "DHL outbound",
            planned["outbound_planned_dhl"],
        )

    with right:
        st.subheader("Shift questions and answers")

        with st.form(f"shift_form_{selected['key']}"):
            submitter = st.text_input(
                "Recorded by (optional)",
                value=(saved or {}).get("submitted_by") or "",
            )

            values = {}
            sections = list(
                dict.fromkeys(
                    q.get("section", "General")
                    for q in active_questions
                )
            )

            for section in sections:
                st.markdown(f"### {section}")

                for question in active_questions:
                    if question.get("section", "General") != section:
                        continue

                    key = question["question_key"]
                    old = saved_answers.get(key, {})
                    old_value = old.get("value", "")

                    if question.get("input_type") == "auto":
                        old_value = planned.get(key, 0)

                    value = render_question_input(
                        question,
                        old_value,
                        planned,
                        cil_planned,
                    )

                    values[key] = value

                    if not (
                        key == "cil_completion"
                        and not cil_planned
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
                q["label"]
                for q in active_questions
                if (q.get("rules") or {}).get("required")
                and values.get(q["question_key"]) in (None, "")
            ]

            if missing:
                st.error(
                    "Complete the required questions: "
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
                        sb.table("shift_submissions").insert(
                            row
                        ).execute()

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
            st.caption("No earlier shifts found.")

        for past_shift in reversed(previous_shifts):
            past_submission = submission_for(
                submissions,
                past_shift,
            )

            title = (
                f"{past_shift['label']} · "
                f"{past_shift['start'].strftime('%H:%M')}–"
                f"{past_shift['end'].strftime('%H:%M')}"
            )

            with st.expander(title, expanded=True):
                if past_submission:
                    st.caption(
                        f"Recorded by: "
                        f"{past_submission.get('submitted_by') or 'Not specified'}"
                    )
                    render_saved_answers(
                        past_submission.get("answers") or {}
                    )
                else:
                    st.caption("No record saved for this shift.")

    st.caption(
        "Team rotation is anchored to the week beginning 12 October 2026. "
        "Bookings with a missing end time use a one-hour fallback window."
    )


if __name__ == "__main__":
    main()