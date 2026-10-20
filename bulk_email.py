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


def parse_email_addresses(
    value: str | list[str] | None,
) -> list[str]:
    """Parse and validate comma-, semicolon-, or newline-separated emails."""
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
    """Load the saved Bulk email recipients from Supabase."""
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
    """Validate and save the Bulk email recipients."""
    to_addresses = parse_email_addresses(to_email)

    if len(to_addresses) != 1:
        raise ValueError(
            "Enter exactly one main recipient email address."
        )

    cc_addresses = parse_email_addresses(cc_emails)

    # Do not duplicate the main recipient in CC.
    main_recipient = to_addresses[0].casefold()

    cc_addresses = [
        address
        for address in cc_addresses
        if address.casefold() != main_recipient
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


def format_delivery_email(
    delivery: dict[str, Any],
) -> tuple[str, str]:
    """
    Build the POD email subject and body.

    The load information is shared between the subject and body.
    """

    def first_value(*keys: str) -> str:
        for key in keys:
            value = delivery.get(key)

            if value is not None and str(value).strip():
                return str(value).strip()

        return ""

    # Prefer the load information currently used by the Bulk entry.
    load_info = first_value(
        "load",
        "load_description",
        "description",
        "title",
    )

    # Fallbacks in case the entry uses a reference field instead.
    if not load_info:
        load_info = first_value(
            "reference",
            "load_ref",
            "load_reference",
            "booking_reference",
            "ref",
            "id",
        )

    if not load_info:
        load_info = "Delivery details"

    # Keep the subject on one line.
    load_info = " ".join(load_info.split())

    subject = f"POD - {load_info}"

    body = (
        "Hi,\n\n"
        "Please take this email as proof of receipt of the following load:\n\n"
        f"Load: {load_info}\n\n"
        "Kind regards,\n"
        "KP Ponte"
    )

    return subject, body


def send_bulk_delivery_email(
    sb: Client,
    delivery: dict[str, Any],
    delivery_id: str,
) -> tuple[bool, str]:
    """Send a Bulk POD email and record the outcome in Supabase."""

    if not delivery_id or not str(delivery_id).strip():
        return False, "The delivery has no stable identifier."

    stable_id = str(delivery_id).strip()

    try:
        # Prevent sending a second email for an already-confirmed delivery.
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

        # Load recipients saved in the Management settings.
        settings = load_bulk_email_settings(sb)

        to_email = settings["to_email"]
        cc_emails = settings["cc_emails"]

        recipients = parse_email_addresses(to_email)

        if len(recipients) != 1:
            return (
                False,
                "Configure a valid To address in Management.",
            )

        # Read the Gmail app password from Streamlit Cloud Secrets.
        password = str(
            st.secrets.get("GMAIL_APP_PASSWORD", "")
        ).strip()

        if not password:
            return (
                False,
                "GMAIL_APP_PASSWORD is missing from Streamlit secrets.",
            )

        subject, body = format_delivery_email(delivery)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = EMAIL_SENDER
        message["To"] = recipients[0]

        if cc_emails:
            message["Cc"] = ", ".join(cc_emails)

        message.set_content(body)

        smtp_recipients = list(
            dict.fromkeys(recipients + cc_emails)
        )

        # Send using the existing Gmail SMTP configuration.
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

        # Record successful delivery so the app can remember it
        # after a refresh.
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

        return True, "Bulk POD email sent successfully."

    except Exception as exc:
        error_text = str(exc)[:1000]

        # Log the failure without marking the delivery as sent.
        # A later retry can update this same log entry.
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

        return (
            False,
            f"Could not send Bulk POD email: {error_text}",
        )


def get_bulk_email_status(
    sb: Client,
    delivery_id: str,
) -> dict[str, Any] | None:
    """Return the saved email status for a delivery, if available."""
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