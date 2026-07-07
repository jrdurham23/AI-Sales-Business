"""Lead-to-launch workflow logic.

State machine on top of db.py: leads move NEW -> QUALIFIED -> CONTACTED ->
FOLLOW_UP -> INTERESTED -> CLIENT (or NOT_INTERESTED / SUPPRESSED / DEAD),
and each client gets a project that moves INTAKE -> PAYMENT_PENDING ->
BUILDING -> REVIEW -> APPROVED -> LIVE -> MAINTENANCE.

The cadence rules live here so every touch automatically schedules the
next one — the daily worklist (`today`) is computed, never hand-maintained.
"""

import datetime

import compliance
import db

# Days to wait after touch N before touch N+1 (4-touch sequence).
TOUCH_GAPS_DAYS = {1: 3, 2: 4, 3: 7}
MAX_TOUCHES = 4

# Respond to an INTERESTED reply within this window.
INTERESTED_SLA_HOURS = 4

# Sentinel for update_lead/update_project: None means "leave unchanged"
# (so CLI flags can be optional), CLEAR means "set the column to NULL".
CLEAR = object()


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
            notes=_append_note(lead, f"Sequence complete after {touch} touches, no response."),
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


def today_worklist(conn):
    """Everything that needs a human action today, hottest first."""
    hot = conn.execute(
        "SELECT * FROM leads WHERE status = 'INTERESTED' ORDER BY updated_at"
    ).fetchall()
    due = [l for l in list_leads(conn, due=True) if l["status"] != "INTERESTED"]
    to_review = conn.execute(
        "SELECT * FROM leads WHERE status = 'NEW' ORDER BY id LIMIT 25"
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
        "new_to_review": to_review,
        "stalled_projects": stalled,
        "active_projects": active,
    }


def _append_note(lead, note):
    existing = lead["notes"] or ""
    stamp = datetime.date.today().isoformat()
    return (existing + "\n" if existing else "") + f"[{stamp}] {note}"
