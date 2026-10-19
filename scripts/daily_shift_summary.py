
from __future__ import annotations

import os
import smtplib
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from supabase import create_client
from warehouse_core.shifts import shifts_between
from warehouse_core.transport import (
    events_for_shift,
    fetch_all_pages,
    normalize_events,
)

UK = ZoneInfo("Europe/London")
SENDER = "KP.ponte.csv@gmail.com"
ROOT = Path(__file__).resolve().parents[1]
RECIPIENTS_FILE = ROOT / "email_recipients.txt"

NUMERIC_TYPES = {"number", "percentage", "auto"}
RANK = {"L": 1, "M": 2, "H": 3}


def recipients():
    addresses = []

    for line in RECIPIENTS_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        line = line.strip()

        if line and not line.startswith("#"):
            addresses.append(line)

    addresses = list(dict.fromkeys(addresses))

    if not addresses:
        raise RuntimeError(
            "Add recipient addresses to email_recipients.txt first."
        )

    return addresses


def previous_shifts(now):
    shifts = shifts_between(
        now.date() - timedelta(days=21),
        now.date(),
    )

    completed = [
        shift for shift in shifts
        if shift["end"] <= now
    ]

    if len(completed) < 3:
        raise RuntimeError(
            "Could not find three completed shifts."
        )

    return completed[-3:]


def load_events(sb):
    rows = fetch_all_pages(
        lambda start, end: (
            sb.table("events")
            .select("*")
            .range(start, end)
            .execute()
            .data or []
        ),
        1000,
    )

    return normalize_events(rows)


def load_submissions(sb, shifts):
    return (
        sb.table("shift_submissions")
        .select("*")
        .gte(
            "shift_start",
            shifts[0]["start"].isoformat(),
        )
        .lte(
            "shift_start",
            shifts[-1]["start"].isoformat(),
        )
        .execute()
        .data or []
    )


def parse_start(value):
    if not value:
        return None

    try:
        dt = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        )
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UK)

    return dt.astimezone(UK)


def by_shift(submissions):
    result = {}

    for row in submissions:
        dt = parse_start(row.get("shift_start"))

        if dt:
            result[dt] = row

    return result


def answer(row, key):
    item = ((row or {}).get("answers") or {}).get(key, "")

    return (
        item.get("value", "")
        if isinstance(item, dict)
        else item
    )


def decimal_value(value):
    if value is None or isinstance(value, bool):
        return None

    raw = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("%", "")
        .strip()
    )

    if not raw or raw.casefold() in {
        "n/a", "na", "none", "null", "blank"
    }:
        return None

    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def show_number(value):
    if value == value.to_integral_value():
        return str(int(value))

    return format(value.normalize(), "f")


def heading(shift):
    return (
        f"{shift['start'].strftime('%a %d %b %Y')} — "
        f"{shift['shift_name']} "
        f"({shift['start']:%H:%M}–"
        f"{shift['end']:%H:%M} UK)"
    )


def cil_count(events, shift):
    return sum(
        1
        for event in events_for_shift(events, shift)
        if (
            "cil" in str(
                event.get("title") or ""
            ).casefold()
            or "cil" in str(
                event.get("source") or ""
            ).casefold()
        )
    )


def highest_trigger(shifts, rows, key):
    values = [
        str(
            answer(rows.get(shift["start"]), key) or ""
        ).strip().upper()
        for shift in shifts
    ]

    values = [
        value for value in values
        if value in RANK
    ]

    return (
        max(values, key=lambda value: RANK[value])
        if values
        else "N/A"
    )


def build_report(now, shifts, submissions, questions, events):
    rows = by_shift(submissions)

    questions = [
        question for question in questions
        if question.get("is_active", True)
    ]

    numeric = [
        question for question in questions
        if question.get("input_type") in NUMERIC_TYPES
        and question.get("question_key") != "cil_completion"
    ]

    text_fields = [
        question for question in questions
        if question.get("input_type") == "text"
    ]

    lines = [
        "DDS — Previous Three Shifts Summary",
        f"Generated: {now:%a %d %b %Y %H:%M} UK time",
        (
            f"Period: {shifts[0]['start']:%d %b %Y} "
            f"to {shifts[-1]['end']:%d %b %Y}"
        ),
        "",
        "TRIGGERS — HIGHEST ACROSS THE THREE SHIFTS",
        (
            "Safety trigger: "
            f"{highest_trigger(shifts, rows, 'safety_trigger')}"
        ),
        (
            "Quality trigger: "
            f"{highest_trigger(shifts, rows, 'quality_trigger')}"
        ),
        "",
        "CIL — PLANNED CALENDAR ENTRIES",
    ]

    counts = []

    for shift in shifts:
        count = cil_count(events, shift)
        counts.append(count)

        lines.append(
            f"{heading(shift)}: "
            f"{count if count else 'N/A'}"
        )

    lines.append(
        "Total planned CIL entries: "
        f"{sum(counts) if sum(counts) else 'N/A'}"
    )

    lines.extend([
        "",
        "NUMERIC TOTALS — SUM OF SAVED ANSWERS",
    ])

    any_record = any(
        rows.get(shift["start"])
        for shift in shifts
    )

    for question in numeric:
        total = Decimal("0")
        found = False

        for shift in shifts:
            value = decimal_value(
                answer(
                    rows.get(shift["start"]),
                    question["question_key"],
                )
            )

            if value is not None:
                total += value
                found = True

        label = (
            question.get("label")
            or question["question_key"]
        )

        displayed_total = (
            show_number(total)
            if found or any_record
            else "N/A"
        )

        lines.append(f"{label}: {displayed_total}")

    lines.extend([
        "",
        "TEXT ENTRIES — AS SAVED, BY SHIFT",
    ])

    for shift in shifts:
        row = rows.get(shift["start"])

        lines.extend([
            "",
            heading(shift),
        ])

        if not row:
            lines.append("No saved shift record.")
            continue

        for question in text_fields:
            value = answer(
                row,
                question["question_key"],
            )

            label = (
                question.get("label")
                or question["question_key"]
            )

            lines.append(f"{label}:")
            lines.append(
                "" if value is None else str(value)
            )

    lines.extend([
        "",
        "SHIFT RECORD STATUS",
    ])

    for shift in shifts:
        status = (
            "Saved"
            if rows.get(shift["start"])
            else "NO SAVED RECORD"
        )

        lines.append(f"{heading(shift)}: {status}")

    lines.extend([
        "",
        "Notes:",
        (
            "- Numeric totals add saved numeric answers; "
            "blank/N/A values contribute nothing."
        ),
        (
            "- Missing shift records are marked above and "
            "are not treated as submitted answers."
        ),
        (
            "- CIL counts calendar events overlapping a "
            "shift whose title or source contains 'CIL'."
        ),
    ])

    subject = (
        f"DDS Shift Summary — "
        f"{shifts[0]['start']:%d %b} to "
        f"{shifts[-1]['end']:%d %b %Y}"
    )

    return subject, "\n".join(lines)


def main():
    now = datetime.now(UK)

    force = (
        os.getenv("FORCE_RUN", "false").lower() == "true"
    )

    if now.hour != 7 and not force:
        print(
            f"Skipping: UK local time is "
            f"{now:%Y-%m-%d %H:%M}; "
            "only sends at 07:xx."
        )
        return

    to = recipients()

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY", ""
    ).strip()
    password = os.getenv(
        "GMAIL_APP_PASSWORD", ""
    ).strip()

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or "
            "SUPABASE_SERVICE_ROLE_KEY Actions secret."
        )

    if not password:
        raise RuntimeError(
            "Missing GMAIL_APP_PASSWORD Actions secret."
        )

    sb = create_client(url, key)

    shifts = previous_shifts(now)
    submissions = load_submissions(sb, shifts)

    questions = (
        sb.table("shift_questions")
        .select(
            "question_key,label,input_type,sort_order,is_active"
        )
        .order("sort_order")
        .execute()
        .data or []
    )

    events = load_events(sb)

    subject, body = build_report(
        now,
        shifts,
        submissions,
        questions,
        events,
    )

    message = EmailMessage()
    message["From"] = SENDER
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30,
    ) as smtp:
        smtp.login(SENDER, password)
        smtp.send_message(message)

    print(
        f"Sent '{subject}' to {len(to)} recipient(s)."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Daily shift summary failed: {exc}",
            file=sys.stderr,
        )
        raise
