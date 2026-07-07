"""Lead-to-launch workflow logic.

State machine on top of db.py: leads move NEW -> QUALIFIED -> CONTACTED ->
FOLLOW_UP -> INTERESTED -> CLIENT (or NOT_INTERESTED / SUPPRESSED / DEAD),
and each client gets a project that moves INTAKE -> PAYMENT_PENDING ->
BUILDING -> REVIEW -> APPROVED -> LIVE -> MAINTENANCE.

The cadence rules live here so every touch automatically schedules the
next one — the daily worklist (`today`) is computed, never hand-maintained.
"""

import csv
import datetime

import compliance
import db
from blocklist import FRANCHISE_BLOCKLIST

# Days to wait after touch N before touch N+1 (4-touch sequence).
TOUCH_GAPS_DAYS = {1: 3, 2: 4, 3: 7}
MAX_TOUCHES = 4

# Respond to an INTERESTED reply within this window.
INTERESTED_SLA_HOURS = 4

# Sentinel for update_lead/update_project: None means "leave unchanged"
# (so CLI flags can be optional), CLEAR means "set the column to NULL".
CLEAR = object()

# High-ticket local trades score above walk-in retail: a plumber's site
# pays for itself on one job, so they close faster and churn less.
_HIGH_VALUE_CATEGORIES = (
    "hvac", "plumb", "electric", "roof", "landscap", "auto repair",
    "contractor", "remodel", "pest", "tree",
)


def compute_score(website_status, phone, email, category):
    """0-100 priority score: how workable and valuable is this lead?"""
    score = {"none": 40, "error": 35, "outdated": 30}.get(website_status or "", 20)
    if phone and str(phone).strip():
        score += 20
    if email and str(email).strip():
        score += 25
    cat = (category or "").lower()
    if any(term in cat for term in _HIGH_VALUE_CATEGORIES):
        score += 15
    elif cat:
        score += 8
    return min(score, 100)


def rescore_lead(conn, lead_id):
    lead = get_lead(conn, lead_id)
    score = compute_score(lead["website_status"], lead["phone"],
                          lead["email"], lead["category"])
    conn.execute("UPDATE leads SET score = ? WHERE id = ?", (score, lead_id))
    conn.commit()
    return score


class WorkflowError(Exception):
    """Raised for invalid transitions or blocked (non-compliant) actions."""


def _touch_date(days_from_now):
    return (datetime.date.today() + datetime.timedelta(days=days_from_now)).isoformat()


def get_lead(conn, lead_id):
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise WorkflowError(f"No lead with id {lead_id}.")
    return row


def list_leads(conn, status=None, due=False):
    query = "SELECT * FROM leads"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status.upper())
    if due:
        clauses.append("next_action_at IS NOT NULL AND date(next_action_at) <= date('now')")
        clauses.append("status NOT IN ('SUPPRESSED', 'DEAD', 'NOT_INTERESTED')")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY next_action_at IS NULL, next_action_at, id"
    return conn.execute(query, params).fetchall()


def update_lead(conn, lead_id, **fields):
    get_lead(conn, lead_id)  # existence check
    if "status" in fields and fields["status"] is not None:
        fields["status"] = fields["status"].upper()
        if fields["status"] not in db.LEAD_STATUSES:
            raise WorkflowError(
                f"Invalid status '{fields['status']}'. "
                f"Valid: {', '.join(db.LEAD_STATUSES)}"
            )
    fields = {k: (None if v is CLEAR else v)
              for k, v in fields.items() if v is not None}
    if not fields:
        return
    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE leads SET {assignments} WHERE id = ?",
        (*fields.values(), lead_id),
    )
    conn.commit()
    if "email" in fields:  # contactability changed — priority changes with it
        rescore_lead(conn, lead_id)


def outbound_touch_count(conn, lead_id):
    return conn.execute(
        "SELECT COUNT(*) FROM outreach_log WHERE lead_id = ? AND direction = 'out'",
        (lead_id,),
    ).fetchone()[0]


def log_outreach(conn, lead_id, channel, summary):
    """Record an outbound touch, enforcing compliance and scheduling the next one.

    Returns (touch_number, next_action_date_or_None, warnings).
    """
    lead = get_lead(conn, lead_id)
    if lead["status"] in ("SUPPRESSED", "NOT_INTERESTED", "DEAD", "CLIENT"):
        raise WorkflowError(
            f"Lead {lead_id} has status {lead['status']} — no further outreach."
        )

    blockers, warnings = compliance.check_outbound(conn, lead, channel)
    if blockers:
        raise WorkflowError(" ".join(blockers))

    conn.execute(
        "INSERT INTO outreach_log (lead_id, channel, direction, summary, logged_at) "
        "VALUES (?, ?, 'out', ?, ?)",
        (lead_id, channel, summary, db.now()),
    )
    conn.commit()

    touch = outbound_touch_count(conn, lead_id)
    if touch >= MAX_TOUCHES:
        update_lead(
            conn, lead_id,
            status="DEAD",
            next_action=CLEAR, next_action_at=CLEAR,
            notes=append_note(lead, f"Sequence complete after {touch} touches, no response."),
        )
        return touch, None, warnings

    gap = TOUCH_GAPS_DAYS[touch]
    due = _touch_date(gap)
    update_lead(
        conn, lead_id,
        status="CONTACTED" if touch == 1 else "FOLLOW_UP",
        next_action=f"Send touch {touch + 1} of {MAX_TOUCHES}",
        next_action_at=due,
    )
    return touch, due, warnings


def record_reply(conn, lead_id, outcome, summary):
    """Record an inbound reply and route the lead accordingly."""
    lead = get_lead(conn, lead_id)
    outcome = outcome.upper()
    valid = ("INTERESTED", "NOT_INTERESTED", "UNSUBSCRIBE", "NEUTRAL")
    if outcome not in valid:
        raise WorkflowError(f"Invalid outcome '{outcome}'. Valid: {', '.join(valid)}")

    conn.execute(
        "INSERT INTO outreach_log (lead_id, channel, direction, summary, outcome, logged_at) "
        "VALUES (?, 'email', 'in', ?, ?, ?)",
        (lead_id, summary, outcome, db.now()),
    )
    conn.commit()

    if outcome == "INTERESTED":
        update_lead(
            conn, lead_id,
            status="INTERESTED",
            next_action=f"HOT: reply within {INTERESTED_SLA_HOURS}h — send intake form + proposal",
            next_action_at=datetime.date.today().isoformat(),
        )
    elif outcome == "NOT_INTERESTED":
        update_lead(conn, lead_id, status="NOT_INTERESTED",
                    next_action=CLEAR, next_action_at=CLEAR)
    elif outcome == "UNSUBSCRIBE":
        for value, ctype in ((lead["email"], "email"), (lead["phone"], "phone")):
            if value:
                compliance.suppress(conn, value, ctype, reason="UNSUBSCRIBE")
        update_lead(conn, lead_id, status="SUPPRESSED",
                    next_action=CLEAR, next_action_at=CLEAR)


def create_project(conn, lead_id):
    """Promote an INTERESTED lead to CLIENT and open its website project."""
    lead = get_lead(conn, lead_id)
    existing = conn.execute(
        "SELECT id FROM projects WHERE lead_id = ?", (lead_id,)
    ).fetchone()
    if existing:
        raise WorkflowError(f"Lead {lead_id} already has project {existing['id']}.")

    cur = conn.execute(
        "INSERT INTO projects (lead_id, business_name, status, created_at, updated_at) "
        "VALUES (?, ?, 'INTAKE', ?, ?)",
        (lead_id, lead["business_name"], db.now(), db.now()),
    )
    conn.commit()
    update_lead(
        conn, lead_id,
        status="CLIENT",
        next_action="Send services agreement + intake form (see legal/ and docs/WORKFLOW.md)",
        next_action_at=datetime.date.today().isoformat(),
    )
    return cur.lastrowid


def list_projects(conn, status=None):
    if status:
        return conn.execute(
            "SELECT * FROM projects WHERE status = ? ORDER BY id",
            (status.upper(),),
        ).fetchall()
    return conn.execute("SELECT * FROM projects ORDER BY id").fetchall()


def update_project(conn, project_id, **fields):
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise WorkflowError(f"No project with id {project_id}.")
    if "status" in fields and fields["status"] is not None:
        fields["status"] = fields["status"].upper()
        if fields["status"] not in db.PROJECT_STATUSES:
            raise WorkflowError(
                f"Invalid status '{fields['status']}'. "
                f"Valid: {', '.join(db.PROJECT_STATUSES)}"
            )
    fields = {k: (None if v is CLEAR else v)
              for k, v in fields.items() if v is not None}
    if not fields:
        return row
    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE projects SET {assignments} WHERE id = ?",
        (*fields.values(), project_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def import_finder_csv(conn, csv_path):
    """Import a finder.py CSV (free OSM no-website scan) into the pipeline.

    Rows where Google verification found a real website are skipped, as are
    franchises and duplicates already in the database. Returns a dict of
    counts: added / has_site / franchise / duplicate.
    """
    counts = {"added": 0, "has_site": 0, "franchise": 0, "duplicate": 0}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise WorkflowError(
                f"{csv_path} doesn't look like a finder.py export "
                "(missing 'name' column)."
            )
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            found = (row.get("google_website_found") or "").strip()
            if found.startswith("http"):
                counts["has_site"] += 1
                continue
            if any(term in name.lower() for term in FRANCHISE_BLOCKLIST):
                counts["franchise"] += 1
                continue

            phone = (row.get("phone") or "").strip()
            email = (row.get("email") or "").strip()
            category = (row.get("type") or "").replace("_", " ").strip()
            address = (row.get("address") or "").strip()
            score = compute_score("none", phone, email, category)

            cur = conn.execute(
                "INSERT OR IGNORE INTO leads "
                "(business_name, category, address, phone, email, "
                " website_status, outdated_signals, source, date_found, "
                " status, score) "
                "VALUES (?, ?, ?, ?, ?, 'none', 'No website found', "
                "        'finder.py (OpenStreetMap)', ?, 'NEW', ?)",
                (name, category, address, phone, email, db.now(), score),
            )
            counts["added" if cur.rowcount else "duplicate"] += 1
    conn.commit()
    return counts


EXPORT_FIELDS = ("id", "business_name", "category", "contact_name", "email",
                 "phone", "address", "website_status", "outdated_signals",
                 "status", "score", "next_action", "next_action_at")


def export_leads_csv(conn, csv_path, status=None, require_email=False):
    """Write leads to CSV for mail merge / external tools.

    Suppressed and dead leads are never exported — an exported list must be
    safe to feed to a sending tool as-is. Returns the number of rows written.
    """
    leads = [l for l in list_leads(conn, status=status)
             if l["status"] not in ("SUPPRESSED", "DEAD", "NOT_INTERESTED")]
    if require_email:
        leads = [l for l in leads if (l["email"] or "").strip()]
    leads.sort(key=lambda l: (l["score"] or 0), reverse=True)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_FIELDS)
        for lead in leads:
            writer.writerow([lead[field] for field in EXPORT_FIELDS])
    return len(leads)


def today_worklist(conn):
    """Everything that needs a human action today, hottest first."""
    hot = conn.execute(
        "SELECT * FROM leads WHERE status = 'INTERESTED' ORDER BY updated_at"
    ).fetchall()
    due = [l for l in list_leads(conn, due=True) if l["status"] != "INTERESTED"]
    to_review = conn.execute(
        "SELECT * FROM leads WHERE status = 'NEW' "
        "ORDER BY score DESC, id LIMIT 25"
    ).fetchall()
    ready = conn.execute(
        "SELECT * FROM leads WHERE status = 'QUALIFIED' AND next_action_at IS NULL "
        "ORDER BY score DESC, id"
    ).fetchall()
    stalled_cutoff = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    stalled = conn.execute(
        "SELECT * FROM projects "
        "WHERE status IN ('INTAKE', 'PAYMENT_PENDING', 'REVIEW') "
        "AND date(updated_at) <= ? ORDER BY updated_at",
        (stalled_cutoff,),
    ).fetchall()
    active = conn.execute(
        "SELECT * FROM projects WHERE status IN ('BUILDING', 'APPROVED') ORDER BY id"
    ).fetchall()
    return {
        "hot_leads": hot,
        "due_followups": due,
        "ready_for_first_touch": ready,
        "new_to_review": to_review,
        "stalled_projects": stalled,
        "active_projects": active,
    }


def append_note(row, note):
    """Timestamped note appended to a lead's/project's existing notes."""
    existing = row["notes"] or ""
    stamp = datetime.date.today().isoformat()
    return (existing + "\n" if existing else "") + f"[{stamp}] {note}"


# Included revision rounds per the services agreement; the next one is a
# paid change order.
INCLUDED_REVISIONS = 2


def record_revision(conn, project_id, note):
    """Log a client revision request: bump the count, append the note,
    and put the project back into BUILDING.

    Returns (project_row, change_order) — change_order is True when this
    request exceeds the included rounds and should be quoted, not absorbed.
    """
    row = conn.execute("SELECT * FROM projects WHERE id = ?",
                       (project_id,)).fetchone()
    if not row:
        raise WorkflowError(f"No project with id {project_id}.")
    count = (row["revision_count"] or 0) + 1
    updated = update_project(
        conn, project_id,
        status="BUILDING",
        revision_count=count,
        notes=append_note(row, f"Revision {count}: {note}"),
    )
    return updated, count > INCLUDED_REVISIONS
