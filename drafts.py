"""Personalized outreach draft generation.

Fills the templates in templates/outreach/ from a lead's record and the
sender identity in .env, and appends the CAN-SPAM footer automatically.
Drafting never sends and never logs — you send the email yourself, then
record it with `outreach log`, which is what advances the cadence.

Sender identity env vars (see .env.example):
    SENDER_NAME, COMPANY_NAME, POSTAL_ADDRESS, UNSUBSCRIBE_LINK (optional)
"""

import os
import pathlib

import compliance
import workflow

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates" / "outreach"


class DraftError(Exception):
    pass


def _sender_identity():
    sender = os.getenv("SENDER_NAME", "").strip()
    company = os.getenv("COMPANY_NAME", "").strip()
    address = os.getenv("POSTAL_ADDRESS", "").strip()
    link = os.getenv("UNSUBSCRIBE_LINK", "").strip()
    missing = [name for name, val in
               (("SENDER_NAME", sender), ("COMPANY_NAME", company),
                ("POSTAL_ADDRESS", address)) if not val]
    if missing:
        raise DraftError(
            f"Set {', '.join(missing)} in .env before drafting — "
            "CAN-SPAM requires a real sender identity and postal address "
            "in every commercial email."
        )
    return sender, company, address, link


def _gap_line(lead):
    status = lead["website_status"] or ""
    if status == "outdated":
        signals = lead["outdated_signals"] or ""
        detail = f" ({signals})" if signals else ""
        return f"has a website that looks like it hasn't been touched in years{detail}"
    if status == "error":
        return "has a website link that doesn't load"
    return "doesn't seem to have a website"


def _area(lead):
    """Best-effort locality from the formatted address: '123 Main St,
    Savannah, GA 31401, United States' -> 'Savannah'."""
    address = (lead["address"] or "").strip()
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[-3]
    return "your area"


def build_draft(lead, touch):
    if not 1 <= touch <= workflow.MAX_TOUCHES:
        raise DraftError(f"Touch must be 1-{workflow.MAX_TOUCHES}, got {touch}.")
    template_path = TEMPLATE_DIR / f"touch{touch}.txt"
    if not template_path.exists():
        raise DraftError(f"Template missing: {template_path}")

    sender, company, address, link = _sender_identity()
    footer = compliance.build_footer(sender, company, address, link)

    draft = template_path.read_text(encoding="utf-8").format(
        business_name=lead["business_name"] or "your business",
        contact_name=lead["contact_name"] or "there",
        category=(lead["category"] or "local").strip().lower(),
        area=_area(lead),
        gap_line=_gap_line(lead),
        footer=footer.rstrip(),
    )

    problems = compliance.required_footer_problems(draft)
    if problems:  # can only happen if a template was edited badly
        raise DraftError("Draft failed compliance check: " + " ".join(problems))
    return draft
