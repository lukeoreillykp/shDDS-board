
# if_down.py
"""
If Down To Do List feature.

This module is used by app.py and shares its existing Supabase connection.
It does not start a separate Streamlit application.
"""

import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import streamlit as st
from supabase import Client

from warehouse_core.shifts import UK
from warehouse_core.transport import parse_dt


EMAIL_SENDER = "KP.ponte.csv@gmail.com"
RECIPIENTS_FILE = Path(__file__).resolve().parent / "email_recipients.txt"


# ---------------------------------------------------------------------
# Load tasks and completions
# ---------------------------------------------------------------------

def load_if_down_tasks(sb: Client) -> list[dict]:
    """Load all If Down tasks from Supabase."""
    return (
        sb.table("if_down_tasks")
        .select("*")
        .order("sort_order")
        .execute()
        .data
        or []
    )


def load_if_down_completions(sb: Client) -> list[dict]:
    """Load all permanent completion records from Supabase."""
    return (
        sb.table("if_down_completions")
        .select("*")
        .execute()
        .data
        or []
    )


# ---------------------------------------------------------------------
# Email notifications
# ---------------------------------------------------------------------

def load_email_recipients() -> list[str]:
    """Read unique email addresses from email_recipients.txt."""
    if not RECIPIENTS_FILE.exists():
        return []

    recipients = []

    for line in RECIPIENTS_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        line = line.strip()

        if (
            not line
            or line.startswith("#")
            or "@" not in line
        ):
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
    password = str(
        st.secrets.get("GMAIL_APP_PASSWORD", "")
    ).strip()

    recipients = load_email_recipients()

    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing from Streamlit secrets."
        )

    if not recipients:
        raise RuntimeError(
            f"No recipients found in {RECIPIENTS_FILE.name}."
        )

    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UK)

    formatted_time = completed_at.astimezone(UK).strftime(
        "%A %d %B %Y at %H:%M:%S UK time"
    )

    message = EmailMessage()
    message["Subject"] = (
        f"If Down task completed — {shift_name}"
    )
    message["From"] = EMAIL_SENDER
    message["To"] = ", ".join(recipients)

    message.set_content(
        "An If Down To Do List task has been completed.\n\n"
        f"Shift/team: {shift_name}\n"
        f"Completed task: {task_text}\n"
        f"Completion time: {formatted_time}\n"
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=20,
    ) as smtp:
        smtp.login(EMAIL_SENDER, password)
        smtp.send_message(message)


# ---------------------------------------------------------------------
# Complete a task
# ---------------------------------------------------------------------

def mark_if_down_task_complete(
    sb: Client,
    task: dict,
    selected_shift: dict,
) -> tuple[bool, str]:
    """
    Record a task completion globally.

    Completion is keyed by task_id, so the task disappears from every
    team's active list, including when it is shared across colours.
    """
    task_id = str(task["id"])
    task_text = str(task["task"])
    shift_name = str(selected_shift["shift_name"])
    team_colour = str(selected_shift["team_colour"])
    completed_at = datetime.now(UK)

    try:
        sb.table("if_down_completions").insert({
            "task_id": task_id,
            "task_text": task_text,
            "shift_name": shift_name,
            "team_colour": team_colour,
            "completed_at": completed_at.isoformat(),
            "email_sent": False,
            "email_error": None,
        }).execute()

    except Exception:
        # If the task has already been completed, do not create
        # another completion record.
        existing = (
            sb.table("if_down_completions")
            .select("task_id")
            .eq("task_id", task_id)
            .execute()
            .data
            or []
        )

        if existing:
            return (
                True,
                "This task has already been recorded as completed.",
            )

        raise

    email_shift_label = (
        f"{team_colour} team ({shift_name})"
    )

    try:
        send_if_down_completion_email(
            task_text,
            email_shift_label,
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
            "Task completed and removed from every team's list, "
            f"but the email could not be sent: {error_text}"
        )


# ---------------------------------------------------------------------
# Display the If Down list in the shift workspace
# ---------------------------------------------------------------------

def render_if_down_list(
    sb: Client,
    selected_shift: dict,
) -> None:
    """Show tasks for the selected rotating team colour."""
    selected_colour = str(selected_shift["team_colour"])

    st.divider()
    st.subheader("If Down To Do List")
    st.caption(
        f"Tasks for the **{selected_colour} team**. "
        "Completing a task removes it from all team lists."
    )

    try:
        tasks = load_if_down_tasks(sb)
        completions = load_if_down_completions(sb)

    except Exception as exc:
        st.error(
            f"Could not load the If Down To Do List: {exc}"
        )
        st.info(
            "Check that the if_down_tasks and "
            "if_down_completions tables exist in Supabase."
        )
        return

    completed_ids = {
        str(item.get("task_id"))
        for item in completions
    }

    active_tasks = [
        task
        for task in tasks
        if task.get("is_active", True)
        and str(task.get("id")) not in completed_ids
        and (
            bool(task.get("all_colours", False))
            or str(task.get("team_colour") or "")
            == selected_colour
        )
    ]

    active_tasks.sort(
        key=lambda item: (
            int(item.get("sort_order", 0)),
            str(item.get("created_at", "")),
        )
    )

    if not active_tasks:
        st.success(
            f"No outstanding tasks for the {selected_colour} team."
        )
        return

    for task in active_tasks:
        task_id = str(task["id"])
        task_text = str(task.get("task", ""))

        task_label = (
            f"{task_text} 🌐"
            if task.get("all_colours", False)
            else task_text
        )

        if st.checkbox(
            task_label,
            key=f"if_down_complete_{task_id}",
            value=False,
        ):
            try:
                saved, message = mark_if_down_task_complete(
                    sb,
                    task,
                    selected_shift,
                )

                if saved:
                    if "could not be sent" in message:
                        st.warning(message)
                    elif "already been recorded" in message:
                        st.info(message)
                    else:
                        st.success(message)

                    st.rerun()

            except Exception as exc:
                st.error(
                    f"Could not save task completion: {exc}"
                )


# ---------------------------------------------------------------------
# Management interface
# ---------------------------------------------------------------------

def render_if_down_management(sb: Client) -> None:
    """Manage team-colour tasks and shared tasks."""
    st.subheader("Manage If Down To Do Lists")
    st.caption(
        "Assign tasks to Blue, Yellow, Red, or all three teams. "
        "A completed task stays completed across every team list."
    )

    team_colours = ["Blue", "Yellow", "Red"]

    management_tabs = [
        "All tasks",
        "Blue",
        "Yellow",
        "Red",
        "Unassigned",
    ]

    try:
        tasks = load_if_down_tasks(sb)
        completions = load_if_down_completions(sb)

    except Exception as exc:
        st.error(f"Could not load If Down tasks: {exc}")
        st.info(
            "Check that the if_down_tasks and "
            "if_down_completions tables exist in Supabase."
        )
        return

    completed_ids = {
        str(item.get("task_id"))
        for item in completions
    }

    # -------------------------------------------------------------
    # Add a task
    # -------------------------------------------------------------

    with st.expander("Add a task", expanded=True):
        with st.form(
            "add_if_down_task_form",
            clear_on_submit=True,
        ):
            new_task_text = st.text_input(
                "Task description",
                placeholder="Describe the task to complete",
            )

            new_task_colour = st.selectbox(
                "Assign to team",
                team_colours,
                key="new_if_down_colour",
            )

            new_task_all_colours = st.checkbox(
                "Show this task on all three team lists",
                value=False,
                key="new_if_down_all_colours",
            )

            next_order = max(
                [
                    int(task.get("sort_order", 0))
                    for task in tasks
                ] + [0]
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
                        "task": new_task_text.strip(),
                        "team_colour": (
                            None
                            if new_task_all_colours
                            else new_task_colour
                        ),
                        "all_colours": bool(
                            new_task_all_colours
                        ),
                        "sort_order": int(new_task_order),
                        "is_active": True,
                    }).execute()

                    if new_task_all_colours:
                        st.success(
                            "Task added to all three team lists."
                        )
                    else:
                        st.success(
                            f"Task added to the "
                            f"{new_task_colour} team list."
                        )

                    st.rerun()

                except Exception as exc:
                    st.error(f"Could not add task: {exc}")

    # -------------------------------------------------------------
    # Existing tasks
    # -------------------------------------------------------------

    st.markdown("### Existing tasks")
    tabs = st.tabs(management_tabs)

    def belongs_in_tab(
        task: dict,
        tab_name: str,
    ) -> bool:
        colour = str(task.get("team_colour") or "")
        all_colours = bool(task.get("all_colours", False))

        if tab_name == "All tasks":
            return True

        if tab_name == "Unassigned":
            return (
                not all_colours
                and colour not in team_colours
            )

        if tab_name in team_colours:
            return all_colours or colour == tab_name

        return False

    for tab, tab_name in zip(tabs, management_tabs):
        with tab:
            tab_tasks = [
                task
                for task in tasks
                if belongs_in_tab(task, tab_name)
            ]

            tab_tasks.sort(
                key=lambda item: (
                    int(item.get("sort_order", 0)),
                    str(item.get("created_at", "")),
                )
            )

            if not tab_tasks:
                st.caption(
                    "No unassigned tasks."
                    if tab_name == "Unassigned"
                    else "No tasks in this category."
                )
                continue

            for task in tab_tasks:
                task_id = str(task["id"])
                task_text = str(task.get("task", ""))
                task_colour = str(
                    task.get("team_colour") or ""
                )
                all_colours = bool(
                    task.get("all_colours", False)
                )
                is_completed = task_id in completed_ids
                is_active = bool(
                    task.get("is_active", True)
                )

                if is_completed:
                    status = "Completed"
                elif is_active:
                    status = "Active"
                else:
                    status = "Inactive"

                if all_colours:
                    assignment_label = "All teams"
                elif task_colour in team_colours:
                    assignment_label = f"{task_colour} team"
                else:
                    assignment_label = "Unassigned"

                with st.expander(
                    f"{status} · {assignment_label} · {task_text}"
                ):
                    if is_completed:
                        st.info(
                            "This task has already been completed. "
                            "It is removed from all team lists and "
                            "cannot be reset from this screen."
                        )

                    with st.form(f"edit_if_down_{task_id}"):
                        edited_text = st.text_input(
                            "Task description",
                            value=task_text,
                        )

                        colour_index = (
                            team_colours.index(task_colour)
                            if task_colour in team_colours
                            else 0
                        )

                        edited_colour = st.selectbox(
                            "Assigned team",
                            team_colours,
                            index=colour_index,
                            disabled=all_colours,
                        )

                        edited_all_colours = st.checkbox(
                            "Show on all three team lists",
                            value=all_colours,
                        )

                        edited_order = st.number_input(
                            "Display order",
                            min_value=0,
                            max_value=100000,
                            value=int(
                                task.get("sort_order", 0)
                            ),
                            step=10,
                        )

                        edited_active = st.checkbox(
                            "Show on the active list",
                            value=is_active,
                            disabled=is_completed,
                        )

                        save_task = st.form_submit_button(
                            "Save task"
                        )

                    if save_task:
                        if not edited_text.strip():
                            st.error(
                                "Task description cannot be blank."
                            )

                        elif (
                            is_completed
                            and edited_text.strip() != task_text
                        ):
                            st.error(
                                "Completed task text is kept as a "
                                "historical record. Add a new task "
                                "if it needs changing."
                            )

                        else:
                            try:
                                sb.table(
                                    "if_down_tasks"
                                ).update({
                                    "task": edited_text.strip(),
                                    "team_colour": (
                                        None
                                        if edited_all_colours
                                        else edited_colour
                                    ),
                                    "all_colours": bool(
                                        edited_all_colours
                                    ),
                                    "sort_order": int(
                                        edited_order
                                    ),
                                    "is_active": (
                                        is_active
                                        if is_completed
                                        else edited_active
                                    ),
                                }).eq(
                                    "id", task_id
                                ).execute()

                                st.success("Task updated.")
                                st.rerun()

                            except Exception as exc:
                                st.error(
                                    f"Could not update task: {exc}"
                                )

                    if not is_completed:
                        if st.button(
                            "Delete task",
                            key=f"delete_if_down_{task_id}",
                        ):
                            try:
                                sb.table(
                                    "if_down_tasks"
                                ).delete().eq(
                                    "id", task_id
                                ).execute()

                                st.success("Task deleted.")
                                st.rerun()

                            except Exception as exc:
                                st.error(
                                    f"Could not delete task: {exc}"
                                )

    # -------------------------------------------------------------
    # Retry failed email notifications
    # -------------------------------------------------------------

    st.divider()
    st.subheader("Email notification failures")

    failed_emails = [
        item
        for item in completions
        if not item.get("email_sent", False)
    ]

    if not failed_emails:
        st.success(
            "All recorded task completion emails "
            "are marked as sent."
        )

    else:
        st.caption(
            "These tasks are already completed. Retrying sends "
            "the email again without restoring the task to an "
            "active list."
        )

        for completion in sorted(
            failed_emails,
            key=lambda item: str(
                item.get("completed_at", "")
            ),
            reverse=True,
        ):
            completion_id = str(completion.get("id"))
            task_text = str(
                completion.get("task_text", "Task")
            )
            shift_name = str(
                completion.get("shift_name", "Shift")
            )
            team_colour = str(
                completion.get("team_colour") or ""
            )
            completed_raw = completion.get(
                "completed_at", ""
            )

            try:
                completed_at = (
                    parse_dt(completed_raw)
                    or datetime.now(UK)
                )

                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(
                        tzinfo=UK
                    )

                completed_display = (
                    completed_at.astimezone(UK).strftime(
                        "%d %b %Y %H:%M:%S"
                    )
                )

            except Exception:
                completed_at = datetime.now(UK)
                completed_display = str(completed_raw)

            completion_label = (
                f"{team_colour} team ({shift_name})"
                if team_colour
                else shift_name
            )

            st.markdown(
                f"**{completion_label}: {task_text}**  \n"
                f"Completed: {completed_display} UK time"
            )

            if completion.get("email_error"):
                st.caption(
                    "Last email error: "
                    f"{completion['email_error']}"
                )

            if st.button(
                "Retry email notification",
                key=f"retry_if_down_email_{completion_id}",
            ):
                try:
                    send_if_down_completion_email(
                        task_text,
                        completion_label,
                        completed_at,
                    )

                    sb.table(
                        "if_down_completions"
                    ).update({
                        "email_sent": True,
                        "email_error": None,
                    }).eq(
                        "id", completion_id
                    ).execute()

                    st.success("Notification email sent.")
                    st.rerun()

                except Exception as exc:
                    sb.table(
                        "if_down_completions"
                    ).update({
                        "email_sent": False,
                        "email_error": str(exc)[:1000],
                    }).eq(
                        "id", completion_id
                    ).execute()

                    st.error(
                        f"Email retry failed: {exc}"
                    )
