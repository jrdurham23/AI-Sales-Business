"""Outreach compliance guardrails.

Encodes the hard rules from legal/outreach-compliance.md so they are
enforced in code, not just documented:

- Suppression list is checked before every outbound touch. A suppressed
  contact blocks the send — no override flag exists on purpose.
- Calls and SMS are restricted to the TCPA-safe window (8am-9pm in the
  recipient's local time). The tool compares against the machine's local
  clock, so keep that in mind when contacting leads in other time zones.
- Every outreach email must carry a footer with a physical postal address
  and a working opt-out, per CAN-SPAM. EMAIL_FOOTER_TEMPLATE is the
  canonical footer; required_footer_problems() checks copy against it.

None of this is legal advice — see legal/README.md.
"""

import datetime

import db

QUIET_HOURS_START = 8   # earliest hour (inclusive) to call/text
QUIET_HOURS_END = 21    # latest hour (exclusive) to call/text, per TCPA

EMAIL_FOOTER_TEMPLATE = """--
{sender_name} | {company_name}
{postal_address}

You're receiving this one-time note because your business is publicly
listed without a website. If you'd rather not hear from us, reply
"unsubscribe" or click here and we'll never contact you again:
{unsubscribe_link}
"""

# Substrings whose absence from an outreach email means it is not
# CAN-SPAM compliant. Postal address can't be pattern-matched reliably,
# so it is covered by the checklist in legal/outreach-compliance.md.
_REQUIRED_FOOTER_HINTS = ("unsubscribe", "opt out", "opt-out", "stop")


def is_suppressed(conn, *values):
    """Return the first suppressed contact value among *values, else None."""
    candidates = [v.strip().lower() for v in values if v and v.strip()]
    if not candidates:
        return None
    placeholders = ",".join("?" for _ in candidates)
    row = conn.execute(
        f"SELECT contact_value FROM suppression_list "
        f"WHERE lower(contact_value) IN ({placeholders})",
        candidates,
    ).fetchone()
    return row["contact_value"] if row else None


def suppress(conn, value, contact_type, reason="MANUAL"):
    conn.execute(
        "INSERT OR IGNORE INTO suppression_list "
        "(contact_value, contact_type, reason, suppressed_at) VALUES (?, ?, ?, ?)",
        (value.strip(), contact_type, reason, db.now()),
    )
    conn.commit()


def outside_quiet_hours(when=None):
    """True if `when` (default: now, machine-local) is outside the 8am-9pm window."""
    when = when or datetime.datetime.now()
    return not (QUIET_HOURS_START <= when.hour < QUIET_HOURS_END)


def required_footer_problems(email_body):
    """Return problems with an outreach email body's compliance footer."""
    lowered = email_body.lower()
    if not any(hint in lowered for hint in _REQUIRED_FOOTER_HINTS):
        return ["No opt-out language found (CAN-SPAM requires a clear "
                "unsubscribe mechanism in every commercial email)."]
    return []


def check_outbound(conn, lead, channel):
    """Validate an outbound touch before it is logged/sent.

    Returns (blockers, warnings). Any blocker means: do not send.
    """
    blockers = []
    warnings = []

    hit = is_suppressed(conn, lead["email"], lead["phone"])
    if hit:
        blockers.append(
            f"Contact '{hit}' is on the suppression list — outreach is not allowed."
        )

    if channel in ("sms", "call") and outside_quiet_hours():
        blockers.append(
            f"Current local time is outside the {QUIET_HOURS_START}:00-"
            f"{QUIET_HOURS_END}:00 TCPA-safe window for calls/SMS. "
            "Schedule this touch for business hours instead."
        )

    if channel == "sms":
        warnings.append(
            "SMS to a lead with no prior business relationship generally "
            "requires prior express written consent under the TCPA — prefer "
            "email or a manual call for cold outreach."
        )
    if channel == "call":
        warnings.append(
            "Check the number against the National Do-Not-Call registry "
            "before cold-calling (see legal/outreach-compliance.md)."
        )

    return blockers, warnings
