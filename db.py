"""SQLite persistence for the Arxon lead-to-launch pipeline.

One database (leads.db by default) holds the entire workflow state:
raw leads from generation, outreach history, the suppression list, and
website projects. init_db() is migration-safe — it can run against a
database created by an older version of the lead generator and will add
any missing tables and columns without touching existing rows.
"""

import datetime
import sqlite3

LEAD_STATUSES = (
    "NEW",             # freshly generated, not yet reviewed
    "QUALIFIED",       # reviewed and worth contacting
    "CONTACTED",       # first outbound touch sent
    "FOLLOW_UP",       # in the drip sequence, awaiting next touch
    "INTERESTED",      # replied positively — act within the SLA
    "CLIENT",          # converted; has a project record
    "NOT_INTERESTED",  # replied negatively — no further outreach
    "SUPPRESSED",      # opted out or bounced — never contact again
    "DEAD",            # sequence exhausted with no response
)

PROJECT_STATUSES = (
    "INTAKE",           # waiting on client intake form / requirements
    "PAYMENT_PENDING",  # brief done, deposit not yet received
    "BUILDING",         # site in progress
    "REVIEW",           # staging link sent, awaiting client feedback
    "APPROVED",         # client signed off, ready to go live
    "LIVE",             # deployed on production domain
    "MAINTENANCE",      # live and on a recurring care plan
    "STALLED",          # client unresponsive — needs human decision
    "CANCELLED",
)

# Workflow columns bolted onto the original lead-generator table.
_LEAD_WORKFLOW_COLUMNS = {
    "email": "TEXT",
    "contact_name": "TEXT",
    "status": "TEXT DEFAULT 'NEW'",
    "notes": "TEXT",
    "next_action": "TEXT",
    "next_action_at": "TEXT",
    "updated_at": "TEXT",
}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def connect(db_path="leads.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn):
    cur = conn.cursor()

    # Base table, identical to what lead_generator.py creates, so either
    # entry point can initialize a fresh database.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT,
            category TEXT,
            address TEXT,
            phone TEXT,
            website_url TEXT,
            website_status TEXT,
            outdated_signals TEXT,
            source TEXT,
            date_found TEXT,
            UNIQUE(business_name, address)
        )
    """)

    existing = {row[1] for row in cur.execute("PRAGMA table_info(leads)")}
    for col, decl in _LEAD_WORKFLOW_COLUMNS.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outreach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id),
            channel TEXT NOT NULL,        -- 'email' | 'sms' | 'call' | 'in_person'
            direction TEXT NOT NULL,      -- 'out' | 'in'
            summary TEXT,
            outcome TEXT,                 -- replies: INTERESTED | NOT_INTERESTED |
                                          -- UNSUBSCRIBE | NEUTRAL
            logged_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppression_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_value TEXT NOT NULL UNIQUE,   -- email address or phone number
            contact_type TEXT,                    -- 'email' | 'phone'
            reason TEXT,                          -- 'UNSUBSCRIBE' | 'HARD_BOUNCE' | 'MANUAL'
            suppressed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id),
            business_name TEXT,
            status TEXT DEFAULT 'INTAKE',
            staging_url TEXT,
            production_url TEXT,
            revision_count INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
