
"""Email notifications for deliveries from the Bulk schedule."""

import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Any

import streamlit as st
from supabase import Client

from if_down import EMAIL_SENDER


SETTINGS_TABLE = "bulk_email_settings"
SEND_LOG_TABLE = "bulk_email_send_log"

EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


def parse_email_addresses(value: str | list[str] | None) -> list[str]:
    """Parse comma-, semicolon-, or newline-separated email addresses."""
    if not value:
        return []

    if isinstance(value, list):
        raw_addresses = value
    else:
        raw_addresses = re.split(r"[,;\n]+", value)

    addresses = []
    seen = set()

    for item in raw_addresses:
        address = str(item).strip()

        if not address:
            continue

        if not EMAIL_PATTERN.fullmatch(address):
            raise ValueError(f"Invalid email address: {address}")

        key = address.casefold()
        if key not in seen:
            addresses.append(address)
            seen.add(key)

    return addresses


def load_bulk_email_settings(sb: Client) -> dict[str, Any]:
    """Load the saved Bulk email recipients from Supabase.

    Requires a settings table with a single row whose id is 1.
    """
    rows = (
        sb.table(SETTINGS_TABLE)
        .select("id,to_email,cc_emails")
        .eq("id", 1)
        .limit(1)
        .execute()
        .data
        or []
    )

    if not rows:
        return {"to_email": "", "cc_emails": []}

    row = rows[0]
    cc_value = row.get("cc_emails") or []

    # The database may store CC as a JSON array or a text field.
    if isinstance(cc_value, str):
        cc_addresses = parse_email_addresses(cc_value)
    else:
        cc_addresses = parse_email_addresses(cc_value)

    return {
        "to_email": str(row.get("to_email") or ""),
        "cc_emails": cc_addresses,
    }


def save_bulk_email_settings(
    sb: Client,
    to_email: str,
    cc_emails: str | list[str],
) -> dict[str, Any]:
    """Validate and persist the Bulk email To and CC recipients."""
    to_addresses = parse_email_addresses(to_email)
    if len(to_addresses) != 1:
        raise ValueError("Enter exactly one main recipient email address.")

    cc_addresses = parse_email_addresses(cc_emails)

    if to_addresses[0].casefold() in {
        address.casefold() for address in cc_addresses
    }:
        cc_addresses = [
            address
            for address in cc_addresses
            if address.casefold() != to_addresses[0].casefold()
        ]

    payload = {
        "id": 1,
        "to_email": to_addresses[0],
        "cc_emails": cc_addresses,
        "updated_at": datetime.now().astimezone().isoformat(),
    }

    sb.table(SETTINGS_TABLE).upsert(
        payload,
        on_conflict="id",
    ).execute()

    return {
        "to_email": to_addresses[0],
        "cc_emails": cc_addresses,
    }


def format_delivery_email(delivery: dict[str, Any]) -> tuple[str, str]:
    """Create an email subject and readable body from a Bulk delivery.

    Pass the original Bulk-schedule delivery record. Common fields are
    included when available; unknown fields are not fabricated.
    """
    def first_value(*keys: str) -> str:
        for key in keys:
            value = delivery.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    reference = first_value(
        "reference", "load_ref", "load_reference",
        "booking_reference", "ref", "id",
    )
    delivery_date = first_value(
        "delivery_date", "date", "planned_date", "start",
    )
    delivery_time = first_value(
        "delivery_time", "time", "planned_time",
    )
    vehicle = first_value("vehicle", "vehicle_type", "registration")
    load = first_value("load", "load_description", "description", "title")
    customer = first_value("customer", "supplier", "site", "destination")
    notes = first_value("notes", "comments", "instructions")

    subject_reference = reference or "Delivery details"
    subject = f"Bulk delivery — {subject_reference}"

    lines = [
        "Bulk schedule delivery details",
        "",
    ]

    fields = [
        ("Reference", reference),
        ("Date", delivery_date),
        ("Time", delivery_time),
        ("Vehicle", vehicle),
        ("Load", load),
        ("Customer / destination", customer),
        ("Notes", notes),
    ]

    for label, value in fields:
        if value:
            lines.append(f"{label}: {value}")

    # Include other source fields so useful delivery details are not lost.
    known_keys = {
        "reference", "load_ref", "load_reference",
        "booking_reference", "ref", "id",
        "delivery_date", "date", "planned_date", "start",
        "delivery_time", "time", "planned_time",
        "vehicle", "vehicle_type", "registration",
        "load", "load_description", "description", "title",
        "customer", "supplier", "site", "destination",
        "notes", "comments", "instructions",
        "source", "category",
    }

    extras = [
        (str(key), str(value).strip())
        for key, value in delivery.items()
        if key not in known_keys
        and value is not None
        and str(value).strip()
        and not isinstance(value, (dict, list))
    ]

    if extras:
        lines.extend(["", "Additional delivery information:"])
        for label, value in extras:
            lines.append(f"{label.replace('_', ' ').title()}: {value}")

    lines.extend(["", "Sent from SHDDS."])

    return subject, "\n".join(lines)


def send_bulk_delivery_email(
    sb: Client,
    delivery: dict[str, Any],
    delivery_id: str,
) -> tuple[bool, str]:
    """Send one Bulk delivery email and record the outcome.

    This function expects the send-log table described in the SQL
    setup. It refuses to resend a delivery already marked as sent.

    Note: the log check and SMTP send are not an atomic operation.
    """
    if not delivery_id or not str(delivery_id).strip():
        return False, "The delivery has no stable identifier."

    stable_id = str(delivery_id).strip()

    try:
        existing = (
            sb.table(SEND_LOG_TABLE)
            .select("delivery_id,status")
            .eq("delivery_id", stable_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if existing and existing[0].get("status") == "sent":
            return False, "This delivery has already been emailed."

        settings = load_bulk_email_settings(sb)
        to_email = settings["to_email"]
        cc_emails = settings["cc_emails"]

        recipients = parse_email_addresses(to_email)
        if len(recipients) != 1:
            return False, "Configure a valid To address in Management."

        if not st.secrets.get("GMAIL_APP_PASSWORD", "").strip():
            return False, "GMAIL_APP_PASSWORD is missing from Streamlit secrets."

        subject, body = format_delivery_email(delivery)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = EMAIL_SENDER
        message["To"] = recipients[0]

        if cc_emails:
            message["Cc"] = ", ".join(cc_emails)

        message.set_content(body)

        smtp_recipients = list(dict.fromkeys(recipients + cc_emails))
        password = str(st.secrets["GMAIL_APP_PASSWORD"]).strip()

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=20,
        ) as smtp:
            smtp.login(EMAIL_SENDER, password)
            smtp.send_message(
                message,
                from_addr=EMAIL_SENDER,
                to_addrs=smtp_recipients,
            )

        sb.table(SEND_LOG_TABLE).upsert(
            {
                "delivery_id": stable_id,
                "status": "sent",
                "email_to": recipients[0],
                "email_cc": cc_emails,
                "email_subject": subject,
                "sent_at": datetime.now().astimezone().isoformat(),
                "error_message": None,
            },
            on_conflict="delivery_id",
        ).execute()

        return True, "Bulk delivery email sent successfully."

    except Exception as exc:
        error_text = str(exc)[:1000]

        try:
            sb.table(SEND_LOG_TABLE).upsert(
                {
                    "delivery_id": stable_id,
                    "status": "failed",
                    "error_message": error_text,
                },
                on_conflict="delivery_id",
            ).execute()
        except Exception:
            pass

        return False, f"Could not send Bulk delivery email: {error_text}"


def get_bulk_email_status(
    sb: Client,
    delivery_id: str,
) -> dict[str, Any] | None:
    """Return the send-log entry for a delivery, if one exists."""
    rows = (
        sb.table(SEND_LOG_TABLE)
        .select("*")
        .eq("delivery_id", str(delivery_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None
