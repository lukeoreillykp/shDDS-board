
from __future__ import annotations

import hmac
import os
import re
import smtplib
import uuid
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
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

EMAIL_SENDER = "KP.ponte.csv@gmail.com"
RECIPIENTS_FILE = Path(__file__).resolve().parent / "email_recipients.txt"

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
# If Down To Do Lists
# ---------------------------------------------------------------------

def load_if_down_tasks(sb: Client) -> list[dict]:
    """Load tasks for all shifts."""
    return (
        sb.table("if_down_tasks")
        .select("*")
        .order("sort_order")
        .execute()
        .data
        or []
    )


def load_if_down_completions(sb: Client) -> list[dict]:
    """Load permanent completion records."""
    return (
        sb.table("if_down_completions")
        .select("*")
        .execute()
        .data
        or []
    )


def load_email_recipients() -> list[str]:
    """Read unique email addresses from email_recipients.txt."""
    if not RECIPIENTS_FILE.exists():
        return []

    recipients = []
    for line in RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "@" not in line:
            continue
        if line not in recipients:
            recipients.append(line)

    return recipients


def send_if_down_completion_email(
    task_text: str,
    shift_name: str,
    completed_at: datetime,
) -> None:
    """Email the configured recipients about a completed task."""
    password = str(st.secrets.get("GMAIL_APP_PASSWORD", "")).strip()
    recipients = load_email_recipients()

    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing from Streamlit secrets."
        )
    if not recipients:
        raise RuntimeError(
            f"No recipients found in {RECIPIENTS_FILE.name}."
        )

    formatted_time = completed_at.astimezone(UK).strftime(
        "%A %d %B %Y at %H:%M:%S UK time"
    )

    message = EmailMessage()
    message["Subject"] = f"If Down task completed — {shift_name}"
    message["From"] = EMAIL_SENDER
    message["To"] = ", ".join(recipients)
    message.set_content(
        "An If Down To Do List task has been completed.\n\n"
        f"Shift: {shift_name}\n"
        f"Completed task: {task_text}\n"
        f"Completion time: {formatted_time}\n"
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(EMAIL_SENDER, password)
        smtp.send_message(message)


def mark_if_down_task_complete(sb: Client, task: dict) -> tuple[bool, str]:
    """
    Save completion before attempting email.

    A unique task_id in the completion table prevents a task being completed
    twice. A failed email does not make the task reappear on the active list.
    """
    task_id = str(task["id"])
    task_text = str(task["task"])
    shift_name = str(task["shift_name"])
    completed_at = datetime.now(UK)

    try:
        # Save the completion first so it remains gone even if email fails.
        sb.table("if_down_completions").insert({
            "task_id": task_id,
            "task_text": task_text,
            "shift_name": shift_name,
            "completed_at": completed_at.isoformat(),
            "email_sent": False,
            "email_error": None,
        }).execute()
    except Exception:
        # It may already have been completed by another user/session.
        existing = (
            sb.table("if_down_completions")
            .select("task_id")
            .eq("task_id", task_id)
            .execute()
            .data
            or []
        )
        if existing:
            return True, "This task has already been recorded as completed."
        raise

    try:
        send_if_down_completion_email(
            task_text,
            shift_name,
            completed_at,
        )
        sb.table("if_down_completions").update({
            "email_sent": True,
            "email_error": None,
        }).eq("task_id", task_id).execute()

        return True, "Task completed and email notification sent."

    except Exception as exc:
        error_text = str(exc)[:1000]
        sb.table("if_down_completions").update({
            "email_sent": False,
            "email_error": error_text,
        }).eq("task_id", task_id).execute()

        return True, (
            "Task completed and removed from the list, but the email "
            f"could not be sent: {error_text}"
        )


def render_if_down_list(sb: Client, selected_shift: dict):
    """Show only the list assigned to the selected shift."""
    shift_name = str(selected_shift["shift_name"])

    st.divider()
    st.subheader("If Down To Do List")
    st.caption(
        f"Tasks for **{shift_name}** only. Tick a task when it is complete."
    )

    try:
        tasks = load_if_down_tasks(sb)
        completions = load_if_down_completions(sb)
    except Exception as exc:
        st.error(f"Could not load the If Down To Do List: {exc}")
        st.info(
            "Check that the if_down_tasks and if_down_completions tables "
            "have been created in Supabase."
        )
        return

    completed_ids = {
        str(item.get("task_id"))
        for item in completions
    }

    active_tasks = [
        task for task in tasks
        if task.get("is_active", True)
        and str(task.get("shift_name", "")) == shift_name
        and str(task.get("id")) not in completed_ids
    ]
    active_tasks.sort(
        key=lambda item: (
            int(item.get("sort_order", 0)),
            str(item.get("created_at", "")),
        )
    )

    if not active_tasks:
        st.success("No outstanding tasks for this shift.")
        return

    for task in active_tasks:
        task_id = str(task["id"])
        task_text = str(task.get("task", ""))

        if st.checkbox(
            task_text,
            key=f"if_down_complete_{task_id}",
            value=False,
        ):
            try:
                saved, message = mark_if_down_task_complete(sb, task)
                if saved:
                    if "could not be sent" in message:
                        st.warning(message)
                    elif "already been recorded" in message:
                        st.info(message)
                    else:
                        st.success(message)
                    st.rerun()
            except Exception as exc:
                st.error(f"Could not save task completion: {exc}")


def render_if_down_management(sb: Client):
    """Management controls for the three shift-specific task lists."""
    st.subheader("Manage If Down To Do Lists")
    st.caption(
        "Add tasks to the relevant shift. Completed tasks remain recorded "
        "in Supabase and do not return to the active list."
    )

    shift_names = [str(name) for name in SHIFT_ORDER]
    if not shift_names:
        st.error("No shift names were found in SHIFT_ORDER.")
        return

    try:
        tasks = load_if_down_tasks(sb)
        completions = load_if_down_completions(sb)
    except Exception as exc:
        st.error(f"Could not load If Down tasks: {exc}")
        st.info(
            "Create the if_down_tasks and if_down_completions tables in "
            "Supabase using the setup SQL provided with this app."
        )
        return

    completed_ids = {
        str(item.get("task_id"))
        for item in completions
    }

    with st.expander("Add a task", expanded=True):
        with st.form("add_if_down_task_form", clear_on_submit=True):
            new_task_shift = st.selectbox(
                "Which shift is this task for?",
                shift_names,
                key="new_if_down_shift",
            )
            new_task_text = st.text_input(
                "Task description",
                placeholder="Describe the task to complete",
            )
            next_order = max(
                [int(task.get("sort_order", 0)) for task in tasks] + [0]
            ) + 10
            new_task_order = st.number_input(
                "Display order",
                min_value=0,
                max_value=100000,
                value=next_order,
                step=10,
            )
            add_task = st.form_submit_button("Add task")

        if add_task:
            if not new_task_text.strip():
                st.error("Enter a task description.")
            else:
                try:
                    sb.table("if_down_tasks").insert({
                        "shift_name": new_task_shift,
                        "task": new_task_text.strip(),
                        "sort_order": int(new_task_order),
                        "is_active": True,
                    }).execute()
                    st.success(f"Task added to {new_task_shift}.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not add task: {exc}")

    st.markdown("### Existing tasks")

    shift_tabs = st.tabs(shift_names)

    for shift_tab, shift_name in zip(shift_tabs, shift_names):
        with shift_tab:
            shift_tasks = [
                task for task in tasks
                if str(task.get("shift_name", "")) == shift_name
            ]
            shift_tasks.sort(
                key=lambda item: (
                    int(item.get("sort_order", 0)),
                    str(item.get("created_at", "")),
                )
            )

            if not shift_tasks:
                st.caption("No tasks have been added to this shift.")
                continue

            for task in shift_tasks:
                task_id = str(task["id"])
                task_text = str(task.get("task", ""))
                is_completed = task_id in completed_ids
                is_active = bool(task.get("is_active", True))

                if is_completed:
                    status = "Completed"
                elif is_active:
                    status = "Active"
                else:
                    status = "Inactive"

                with st.expander(f"{status} · {task_text}"):
                    if is_completed:
                        st.info(
                            "This task has already been completed. It remains "
                            "in the completion history and cannot be reset "
                            "from this screen."
                        )

                    with st.form(f"edit_if_down_{task_id}"):
                        edited_text = st.text_input(
                            "Task description",
                            value=task_text,
                        )
                        edited_order = st.number_input(
                            "Display order",
                            min_value=0,
                            max_value=100000,
                            value=int(task.get("sort_order", 0)),
                            step=10,
                        )
                        edited_active = st.checkbox(
                            "Show on the active list",
                            value=is_active,
                            disabled=is_completed,
                        )
                        save_task = st.form_submit_button("Save task")

                    if save_task:
                        if not edited_text.strip():
                            st.error("Task description cannot be blank.")
                        elif is_completed and edited_text.strip() != task_text:
                            st.error(
                                "Completed task text is kept as a historical "
                                "record. Add a new task if the task needs to "
                                "be changed."
                            )
                        else:
                            try:
                                sb.table("if_down_tasks").update({
                                    "task": edited_text.strip(),
                                    "sort_order": int(edited_order),
                                    "is_active": (
                                        is_active
                                        if is_completed
                                        else edited_active
                                    ),
                                }).eq("id", task_id).execute()
                                st.success("Task updated.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Could not update task: {exc}")

                    if not is_completed:
                        if st.button(
                            "Delete task",
                            key=f"delete_if_down_{task_id}",
                        ):
                            try:
                                sb.table("if_down_tasks").delete().eq(
                                    "id", task_id
                                ).execute()
                                st.success("Task deleted.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Could not delete task: {exc}")

    st.divider()
    st.subheader("Email notification failures")

    failed_emails = [
        item for item in completions
        if not item.get("email_sent", False)
    ]

    if not failed_emails:
        st.success("All recorded task completion emails are marked as sent.")
    else:
        st.caption(
            "These tasks are already completed. Retry sends the notification "
            "again without restoring the task to the active list."
        )

        for completion in sorted(
            failed_emails,
            key=lambda item: str(item.get("completed_at", "")),
            reverse=True,
        ):
            completion_id = str(completion.get("id"))
            task_id = str(completion.get("task_id"))
            task_text = str(completion.get("task_text", "Task"))
            shift_name = str(completion.get("shift_name", "Shift"))
            completed_raw = completion.get("completed_at", "")

            try:
                completed_at = parse_dt(completed_raw) or datetime.now(UK)
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=UK)
                completed_display = completed_at.astimezone(UK).strftime(
                    "%d %b %Y %H:%M:%S"
                )
            except Exception:
                completed_display = str(completed_raw)

            st.markdown(
                f"**{shift_name}: {task_text}**  \n"
                f"Completed: {completed_display} UK time"
            )

            if completion.get("email_error"):
                st.caption(f"Last email error: {completion['email_error']}")

            if st.button(
                "Retry email notification",
                key=f"retry_if_down_email_{completion_id}",
            ):
                try:
                    send_if_down_completion_email(
                        task_text,
                        shift_name,
                        (
                            parse_dt(completed_raw)
                            or datetime.now(UK)
                        ),
                    )
                    sb.table("if_down_completions").update({
                        "email_sent": True,
                        "email_error": None,
                    }).eq("id", completion_id).execute()
                    st.success("Notification email sent.")
                    st.rerun()
                except Exception as exc:
                    sb.table("if_down_completions").update({
                        "email_sent": False,
                        "email_error": str(exc)[:1000],
                    }).eq("id", completion_id).execute()
                    st.error(f"Email retry failed: {exc}")


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


def format_shift_heading(shift: dict) -> str:
    return (
        f"{shift['start'].strftime('%a %d %b %Y')} · "
        f"{shift.get('shift_name', 'Shift')}"
    )


def render_historical_answer(
    question: dict,
    snapshot: dict,
):
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


def render_question_table(
    questions: list[dict],
    saved_answers: dict,
    previous_shifts: list[dict],
    submissions: dict[str, dict],
    planned: dict,
    cil_planned: bool,
) -> dict:
    previous_shifts = sorted(
        previous_shifts,
        key=lambda shift: shift["start"],
    )

    while len(previous_shifts) < 3:
        previous_shifts.insert(0, None)

    headings = ["Question"]
    for index, shift in enumerate(previous_shifts):
        if shift is None:
            headings.append(f"Previous shift {3 - index}")
        else:
            headings.append(format_shift_heading(shift))

    headings.append(
        format_shift_heading({
            "start": st.session_state.get("_current_shift_start"),
            "shift_name": st.session_state.get(
                "_current_shift_name", "Current shift"
            ),
        })
        if st.session_state.get("_current_shift_start")
        else "Current shift"
    )

    widths = [2.0, 1.0, 1.0, 1.0, 1.35]
    header_columns = st.columns(widths, gap="small")

    for column, heading in zip(header_columns, headings):
        with column:
            st.markdown(f"**{heading}**")

    st.divider()

    values = {}
    sections = list(dict.fromkeys(
        question.get("section", "General")
        for question in questions
    ))

    for section in sections:
        st.markdown(f"### {section}")

        for question in questions:
            if question.get("section", "General") != section:
                continue

            key = question["question_key"]
            columns = st.columns(widths, gap="small")

            with columns[0]:
                st.markdown(f"**{question['label']}**")

            for index, past_shift in enumerate(previous_shifts):
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

                value = render_question_input(
                    question,
                    old_value,
                    planned,
                    cil_planned,
                    compact=True,
                )
                values[key] = value

    return values


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

    questions_tab, links_tab, if_down_tab = st.tabs([
        "Questions",
        "Sidebar links",
        "If Down To Do Lists",
    ])

    with if_down_tab:
        if st.button("Lock management area", key="lock_if_down_management"):
            st.session_state.management_authenticated = False
            st.rerun()

        render_if_down_management(sb)

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

                    if save_link:
                        if not edit_label.strip():
                            st.error("Enter a name for the link.")
                        elif not valid_navigation_url(edit_url.strip()):
                            st.error(
                                "Enter a URL starting with https:// or http://."
                            )
                        else:
                            sb.table("navigation_links").update({
                                "label": edit_label.strip(),
                                "url": edit_url.strip(),
                                "sort_order": int(edit_order),
                                "is_active": active,
                            }).eq("id", link["id"]).execute()
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

        except Exception as exc:
            st.error(f"Could not manage sidebar links: {exc}")
            st.info(
                "Check that the navigation_links table exists in Supabase."
            )

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

    if kind == "auto":
        value = planned.get(key, 0)
        st.text_input(
            label,
            value=str(value),
            disabled=True,
            key=f"auto_{key}_{value}",
            label_visibility=label_visibility,
        )
        return value

    if key == "cil_completion" and not cil_planned:
        st.text_input(
            label,
            value="N/A — no CIL planned",
            disabled=True,
            key=f"cil_na_{key}",
            label_visibility=label_visibility,
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
            label_visibility=label_visibility,
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
            label_visibility=label_visibility,
        )

    if kind == "choice":
        options = question.get("options") or ["L", "M", "H"]
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
            label_visibility=label_visibility,
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
            label_visibility=label_visibility,
        )
        return "" if selected == "— Select —" else selected

    return st.text_area(
        label,
        value=str(current_value or ""),
        key=f"input_{key}",
        disabled=disabled,
        height=80,
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

        if st.button(
            "Refresh calendar and records",
            use_container_width=True,
        ):
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

    previous_button, next_button = st.columns(2)

    with previous_button:
        if shift_index > 0 and st.button("← Previous shift"):
            st.session_state.selected_shift_key = (
                all_shifts[shift_index - 1]["key"]
            )
            st.rerun()

    with next_button:
        if shift_index + 1 < len(all_shifts) and st.button("Next shift →"):
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
        shift for shift in all_shifts[:shift_index]
        if shift["end"] <= selected["start"]
    ][-3:]

    left, right = st.columns([1.2, 1.0], gap="large")

    with left:
        st.subheader("Shift questions and answers")
        st.caption(
            "Compare the previous three shifts with the current shift. "
            "Previous answers are read-only."
        )

        with st.form(f"shift_form_{selected['key']}"):
            submitter = st.text_input(
                "Recorded by (optional)",
                value=(saved or {}).get("submitted_by") or "",
            )

            st.session_state["_current_shift_start"] = selected["start"]
            st.session_state["_current_shift_name"] = selected["shift_name"]

            historical = sorted(
                previous_shifts,
                key=lambda shift: shift["start"],
            )
            while len(historical) < 3:
                historical.insert(0, None)

            headings = ["Question"]
            for index, past_shift in enumerate(historical):
                headings.append(
                    format_shift_heading(past_shift)
                    if past_shift
                    else f"Previous shift {3 - index}"
                )
            headings.append(format_shift_heading(selected))

            widths = [2.0, 1.0, 1.0, 1.0, 1.35]
            header_columns = st.columns(widths, gap="small")
            for column, heading in zip(header_columns, headings):
                with column:
                    st.markdown(f"**{heading}**")

            st.divider()

            values = {}
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

                            past_submission = submission_for(
                                submissions, past_shift
                            )
                            past_answers = (
                                (past_submission or {}).get("answers") or {}
                            )
                            render_historical_answer(
                                question, past_answers
                            )

                    with columns[4]:
                        old = saved_answers.get(key) or {}
                        old_value = old.get("value", "")

                        if question.get("input_type") == "auto":
                            old_value = planned.get(key, 0)

                        value = render_question_input(
                            question,
                            old_value,
                            planned,
                            cil_planned,
                            compact=True,
                        )
                        values[key] = value

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

        # Only show tasks assigned to the selected shift.
        render_if_down_list(sb, selected)

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
