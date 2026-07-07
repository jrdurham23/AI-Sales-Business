"""System health checks (`python main.py doctor`).

The framework's Phase 0 rule: discover a broken configuration at startup,
not mid-pipeline after a client is already waiting. Run this after setup
changes and before a work session.

Levels: FAIL = something will break when used; WARN = works but degraded;
OK = informational.
"""

import datetime
import os
import pathlib
import sqlite3

import db

_ROOT = pathlib.Path(__file__).parent

REQUIRED_TEMPLATES = (
    "templates/outreach/touch1.txt",
    "templates/outreach/touch2.txt",
    "templates/outreach/touch3.txt",
    "templates/outreach/touch4.txt",
    "templates/site/index.html",
    "templates/site/privacy.html",
    "templates/site/terms.html",
    "templates/site/brief.example.json",
)

BACKUP_MAX_AGE_DAYS = 7


def run_checks(db_path="leads.db", backups_dir="backups"):
    """Return a list of (level, message) tuples; levels FAIL/WARN/OK."""
    results = []

    # Credentials & identity
    if os.getenv("GEOAPIFY_API_KEY"):
        results.append(("OK", "GEOAPIFY_API_KEY is set (lead generation ready)."))
    else:
        results.append(("WARN", "GEOAPIFY_API_KEY not set — `generate` won't run "
                                "(finder.py + `leads import` still work)."))

    identity_missing = [v for v in ("SENDER_NAME", "COMPANY_NAME", "POSTAL_ADDRESS")
                        if not os.getenv(v, "").strip()]
    if identity_missing:
        results.append(("WARN", f"{', '.join(identity_missing)} not set in .env — "
                                "`outreach draft` will refuse to run (CAN-SPAM "
                                "identity required)."))
    else:
        results.append(("OK", "Sender identity configured (drafting ready)."))

    # Templates
    missing = [t for t in REQUIRED_TEMPLATES if not (_ROOT / t).exists()]
    if missing:
        results.append(("FAIL", f"Missing templates: {', '.join(missing)}"))
    else:
        results.append(("OK", f"All {len(REQUIRED_TEMPLATES)} templates present."))

    # Database
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            db.init_db(conn)
            leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            suppressed = conn.execute(
                "SELECT COUNT(*) FROM suppression_list").fetchone()[0]
            due = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE next_action_at IS NOT NULL "
                "AND date(next_action_at) <= date('now') "
                "AND status NOT IN ('SUPPRESSED','DEAD','NOT_INTERESTED')"
            ).fetchone()[0]
            results.append(("OK", f"Database {db_path}: {leads} leads, "
                                  f"{suppressed} suppressed, {due} actions due."))
            conn.close()
        except sqlite3.Error as e:
            results.append(("FAIL", f"Database {db_path} unreadable: {e}"))
    else:
        results.append(("WARN", f"No database at {db_path} yet — run `generate` "
                                "or `leads import` to create it."))

    # Backups
    backups = sorted(pathlib.Path(backups_dir).glob("*.db")) \
        if os.path.isdir(backups_dir) else []
    if not backups:
        if os.path.exists(db_path):
            results.append(("WARN", "No backups found — run `python main.py backup` "
                                    "(the DB holds the legally-required "
                                    "suppression list)."))
    else:
        newest = max(backups, key=lambda p: p.stat().st_mtime)
        age = (datetime.datetime.now()
               - datetime.datetime.fromtimestamp(newest.stat().st_mtime)).days
        if age > BACKUP_MAX_AGE_DAYS:
            results.append(("WARN", f"Newest backup is {age} days old "
                                    f"({newest.name}) — run `backup`."))
        else:
            results.append(("OK", f"Backup current ({newest.name}, {age}d old)."))

    return results


def has_failures(results):
    return any(level == "FAIL" for level, _ in results)
