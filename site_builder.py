"""Client website builder.

Turns an intake brief (JSON — see templates/site/brief.example.json) into a
complete, deployable static site: index, privacy policy, and terms pages
with the client's content, contact details, brand color, and LocalBusiness
structured data filled in. No build tooling, no dependencies — the output
directory uploads as-is to any static host.

    python main.py site brief                 # write a brief to fill in
    python main.py site build brief.json      # build to builds/<slug>/
"""

import datetime
import html
import json
import pathlib
import re
import urllib.parse

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates" / "site"

REQUIRED_FIELDS = (
    "business_name", "tagline", "about", "services",
    "phone", "email", "address", "hours", "cta_text",
)

_TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


class BriefError(Exception):
    pass


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "site"


def _phone_digits(phone):
    digits = re.sub(r"[^\d+]", "", phone)
    if digits and not digits.startswith("+"):
        digits = "+1" + digits.lstrip("1") if len(digits.lstrip("1")) == 10 else digits
    return digits


def load_brief(brief_path):
    try:
        brief = json.loads(pathlib.Path(brief_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BriefError(f"Brief not found: {brief_path}")
    except json.JSONDecodeError as e:
        raise BriefError(f"Brief is not valid JSON: {e}")

    missing = [f for f in REQUIRED_FIELDS if not brief.get(f)]
    if missing:
        raise BriefError(
            f"Brief is missing required fields: {', '.join(missing)}. "
            "Start from templates/site/brief.example.json."
        )
    if not isinstance(brief["services"], list) or not brief["services"]:
        raise BriefError("Brief field 'services' must be a non-empty list.")
    return brief


def _tokens(brief):
    esc = lambda key: html.escape(str(brief[key]).strip())
    phone = str(brief["phone"]).strip()
    services = "\n".join(
        f'        <li>{html.escape(str(s).strip())}</li>'
        for s in brief["services"]
    )
    meta = str(brief.get("meta_description", "")).strip() or (
        f"{brief['tagline']}. Contact {brief['business_name']} at {phone}."
    )
    return {
        "BUSINESS_NAME": esc("business_name"),
        "TAGLINE": esc("tagline"),
        "ABOUT": esc("about"),
        "META_DESCRIPTION": html.escape(meta),
        "SERVICES_ITEMS": services,
        "PHONE": html.escape(phone),
        "PHONE_DIGITS": _phone_digits(phone),
        "EMAIL": esc("email"),
        "ADDRESS": esc("address"),
        "MAPS_QUERY": urllib.parse.quote_plus(str(brief["address"]).strip()),
        "HOURS": esc("hours"),
        "CTA_TEXT": esc("cta_text"),
        "CTA_HREF": html.escape(
            str(brief.get("cta_href", "")).strip()
            or f"tel:{_phone_digits(phone)}"
        ),
        "BRAND_COLOR": html.escape(
            str(brief.get("brand_color", "")).strip() or "#1266d8"
        ),
        "YEAR": str(datetime.date.today().year),
        "EFFECTIVE_DATE": datetime.date.today().strftime("%B %d, %Y"),
    }


def build_site(brief_path, out_dir=None):
    """Build the site. Returns the output directory path."""
    brief = load_brief(brief_path)
    tokens = _tokens(brief)

    out = pathlib.Path(out_dir) if out_dir else (
        pathlib.Path("builds") / slugify(brief["business_name"])
    )
    out.mkdir(parents=True, exist_ok=True)

    pages = sorted(TEMPLATE_DIR.glob("*.html"))
    if not pages:
        raise BriefError(f"No page templates found in {TEMPLATE_DIR}")

    for page in pages:
        content = page.read_text(encoding="utf-8")
        content = _TOKEN_RE.sub(
            lambda m: tokens.get(m.group(1), m.group(0)), content
        )
        leftover = _TOKEN_RE.search(content)
        if leftover:
            raise BriefError(
                f"Unknown template token {leftover.group(0)} in {page.name} — "
                "template and builder are out of sync."
            )
        (out / page.name).write_text(content, encoding="utf-8")

    return out
