import argparse
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

import compliance
import db
import workflow
from lead_generator import generate_leads

console = Console()

DEFAULT_CATEGORIES = ("HVAC, auto repair, landscaping, restaurants, retail, "
                      "plumbing, electrician, roofing")


# ── generate ──────────────────────────────────────────────────────────────────

def cmd_generate(args):
    load_dotenv()
    api_key = os.getenv("GEOAPIFY_API_KEY")
    if not api_key:
        console.print("[red]Error: GEOAPIFY_API_KEY is not set.[/red]")
        console.print("Create a .env file based on .env.example and add your Geoapify API key.")
        sys.exit(1)

    zip_code = args.zip
    if not zip_code:
        zip_code = console.input("[bold]ZIP code to search (e.g. 31401): [/bold]").strip()
        while not zip_code:
            zip_code = console.input("[yellow]ZIP code cannot be empty:[/yellow] ").strip()

    console.rule(f"Generating leads for ZIP {zip_code}")
    generate_leads(
        zip_code=zip_code,
        categories=args.categories,
        api_key=api_key,
        limit=args.limit,
        db_path=args.db,
        csv_path=args.csv,
    )
    # Ensure workflow columns/tables exist on the freshly written database.
    db.connect(args.db).close()
    console.print("\n[dim]Next: review new leads with[/dim] python main.py leads list --status NEW")


# ── lead display helpers ──────────────────────────────────────────────────────

def _lead_table(leads, title):
    table = Table(title=title, show_lines=False)
    for col in ("ID", "Business", "Status", "Phone", "Email", "Next action", "Due"):
        table.add_column(col)
    for l in leads:
        table.add_row(
            str(l["id"]), l["business_name"] or "", l["status"] or "NEW",
            l["phone"] or "", l["email"] or "",
            l["next_action"] or "", l["next_action_at"] or "",
        )
    return table


def cmd_leads_list(args):
    conn = db.connect(args.db)
    leads = workflow.list_leads(conn, status=args.status, due=args.due)
    if not leads:
        console.print("[yellow]No leads match.[/yellow]")
        return
    label = f"status={args.status}" if args.status else ("due today" if args.due else "all")
    console.print(_lead_table(leads, f"Leads ({label}) — {len(leads)}"))


def cmd_leads_show(args):
    conn = db.connect(args.db)
    lead = workflow.get_lead(conn, args.id)
    for key in lead.keys():
        console.print(f"[bold]{key}[/bold]: {lead[key] if lead[key] is not None else ''}")
    history = conn.execute(
        "SELECT * FROM outreach_log WHERE lead_id = ? ORDER BY logged_at", (args.id,)
    ).fetchall()
    if history:
        console.print("\n[bold]Outreach history:[/bold]")
        for h in history:
            arrow = "→" if h["direction"] == "out" else "←"
            outcome = f" [{h['outcome']}]" if h["outcome"] else ""
            console.print(f"  {h['logged_at']} {arrow} {h['channel']}{outcome}: {h['summary'] or ''}")


def cmd_leads_set(args):
    conn = db.connect(args.db)
    workflow.update_lead(
        conn, args.id,
        status=args.status,
        email=args.email,
        contact_name=args.contact,
        notes=args.note,
        next_action=args.next,
        next_action_at=args.due,
    )
    console.print(f"[green]Lead {args.id} updated.[/green]")


# ── outreach ──────────────────────────────────────────────────────────────────

def cmd_outreach_log(args):
    conn = db.connect(args.db)
    touch, next_due, warnings = workflow.log_outreach(
        conn, args.id, args.channel, args.summary
    )
    for w in warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    console.print(f"[green]Touch {touch}/{workflow.MAX_TOUCHES} logged for lead {args.id}.[/green]")
    if next_due:
        console.print(f"Next touch scheduled for [bold]{next_due}[/bold].")
    else:
        console.print("[dim]Sequence complete — lead marked DEAD (no response after "
                      f"{workflow.MAX_TOUCHES} touches).[/dim]")


def cmd_outreach_reply(args):
    conn = db.connect(args.db)
    workflow.record_reply(conn, args.id, args.outcome, args.summary)
    lead = workflow.get_lead(conn, args.id)
    console.print(f"[green]Reply recorded — lead {args.id} is now {lead['status']}.[/green]")
    if lead["status"] == "INTERESTED":
        console.print(f"[bold red]HOT LEAD:[/bold red] respond within "
                      f"{workflow.INTERESTED_SLA_HOURS} hours. "
                      "Then: python main.py project create " + str(args.id))


def cmd_outreach_checkemail(args):
    body = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    problems = compliance.required_footer_problems(body)
    if problems:
        for p in problems:
            console.print(f"[red]✗ {p}[/red]")
        sys.exit(1)
    console.print("[green]✓ Opt-out language present. Also confirm the footer has your "
                  "physical postal address and a truthful subject line (CAN-SPAM).[/green]")


# ── suppression ───────────────────────────────────────────────────────────────

def cmd_suppress_add(args):
    conn = db.connect(args.db)
    compliance.suppress(conn, args.value, args.type, args.reason)
    console.print(f"[green]'{args.value}' added to the suppression list ({args.reason}).[/green]")


def cmd_suppress_check(args):
    conn = db.connect(args.db)
    hit = compliance.is_suppressed(conn, args.value)
    if hit:
        console.print(f"[red]SUPPRESSED — do not contact '{hit}'.[/red]")
        sys.exit(1)
    console.print(f"[green]'{args.value}' is not suppressed.[/green]")


# ── projects ──────────────────────────────────────────────────────────────────

def cmd_project_create(args):
    conn = db.connect(args.db)
    project_id = workflow.create_project(conn, args.lead_id)
    console.print(f"[green]Project {project_id} created (status INTAKE); "
                  f"lead {args.lead_id} promoted to CLIENT.[/green]")
    console.print("[dim]Next: send the services agreement (legal/client-services-agreement.md) "
                  "and the intake checklist (docs/WORKFLOW.md §4).[/dim]")


def cmd_project_list(args):
    conn = db.connect(args.db)
    projects = workflow.list_projects(conn, status=args.status)
    if not projects:
        console.print("[yellow]No projects match.[/yellow]")
        return
    table = Table(title=f"Projects — {len(projects)}")
    for col in ("ID", "Business", "Status", "Revisions", "Staging", "Production", "Updated"):
        table.add_column(col)
    for p in projects:
        table.add_row(
            str(p["id"]), p["business_name"] or "", p["status"],
            str(p["revision_count"]), p["staging_url"] or "",
            p["production_url"] or "", p["updated_at"] or "",
        )
    console.print(table)


def cmd_project_set(args):
    conn = db.connect(args.db)
    row = workflow.update_project(
        conn, args.id,
        status=args.status,
        staging_url=args.staging,
        production_url=args.production,
        revision_count=args.revisions,
        notes=args.note,
    )
    console.print(f"[green]Project {args.id} updated — status {row['status']}.[/green]")


# ── today ─────────────────────────────────────────────────────────────────────

def cmd_today(args):
    conn = db.connect(args.db)
    work = workflow.today_worklist(conn)
    console.rule("[bold]Today's worklist[/bold]")

    if work["hot_leads"]:
        console.print(f"\n[bold red]🔥 HOT — reply within {workflow.INTERESTED_SLA_HOURS}h "
                      f"({len(work['hot_leads'])}):[/bold red]")
        console.print(_lead_table(work["hot_leads"], "Interested leads"))
    if work["due_followups"]:
        console.print(f"\n[bold yellow]⏰ Follow-ups due ({len(work['due_followups'])}):[/bold yellow]")
        console.print(_lead_table(work["due_followups"], "Due follow-ups"))
    if work["stalled_projects"]:
        console.print(f"\n[bold magenta]🛑 Stalled projects — nudge the client "
                      f"({len(work['stalled_projects'])}):[/bold magenta]")
        for p in work["stalled_projects"]:
            console.print(f"  #{p['id']} {p['business_name']} — {p['status']} "
                          f"(last touched {p['updated_at']})")
    if work["active_projects"]:
        console.print(f"\n[bold cyan]🔨 Builds in progress ({len(work['active_projects'])}):[/bold cyan]")
        for p in work["active_projects"]:
            console.print(f"  #{p['id']} {p['business_name']} — {p['status']}")
    if work["new_to_review"]:
        console.print(f"\n[bold green]🆕 New leads to qualify "
                      f"(showing up to 25 of status NEW):[/bold green]")
        console.print(_lead_table(work["new_to_review"], "New leads"))

    if not any(work.values()):
        console.print("[green]Nothing due — run a generation pass? "
                      "python main.py generate --zip <ZIP>[/green]")


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        description="Arxon Solutions — lead-to-launch pipeline. "
                    "See docs/WORKFLOW.md for the full playbook."
    )
    parser.add_argument("--db", default="leads.db", help="SQLite database file (default: leads.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="Find new leads for a ZIP code")
    p.add_argument("--zip", help="Target ZIP code (prompted if omitted)")
    p.add_argument("--categories", default=DEFAULT_CATEGORIES)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--csv", default="leads.csv")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("today", help="Show today's worklist (start here every morning)")
    p.set_defaults(func=cmd_today)

    leads = sub.add_parser("leads", help="Manage leads").add_subparsers(
        dest="subcommand", required=True)
    p = leads.add_parser("list", help="List leads")
    p.add_argument("--status", choices=[s for s in db.LEAD_STATUSES] +
                   [s.lower() for s in db.LEAD_STATUSES])
    p.add_argument("--due", action="store_true", help="Only leads with an action due today")
    p.set_defaults(func=cmd_leads_list)
    p = leads.add_parser("show", help="Show one lead with full outreach history")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_leads_show)
    p = leads.add_parser("set", help="Update a lead's fields")
    p.add_argument("id", type=int)
    p.add_argument("--status")
    p.add_argument("--email")
    p.add_argument("--contact", help="Contact/owner name")
    p.add_argument("--note")
    p.add_argument("--next", help="Next action description")
    p.add_argument("--due", help="Next action date (YYYY-MM-DD)")
    p.set_defaults(func=cmd_leads_set)

    outreach = sub.add_parser("outreach", help="Log outreach and replies").add_subparsers(
        dest="subcommand", required=True)
    p = outreach.add_parser("log", help="Record an outbound touch (compliance-checked)")
    p.add_argument("id", type=int)
    p.add_argument("--channel", required=True, choices=("email", "sms", "call", "in_person"))
    p.add_argument("--summary", required=True)
    p.set_defaults(func=cmd_outreach_log)
    p = outreach.add_parser("reply", help="Record an inbound reply")
    p.add_argument("id", type=int)
    p.add_argument("--outcome", required=True,
                   choices=("interested", "not_interested", "unsubscribe", "neutral"))
    p.add_argument("--summary", required=True)
    p.set_defaults(func=cmd_outreach_reply)
    p = outreach.add_parser("check-email", help="Check an email draft for CAN-SPAM basics")
    p.add_argument("file", help="Path to the draft, or - for stdin")
    p.set_defaults(func=cmd_outreach_checkemail)

    suppress = sub.add_parser("suppress", help="Manage the do-not-contact list").add_subparsers(
        dest="subcommand", required=True)
    p = suppress.add_parser("add")
    p.add_argument("value", help="Email address or phone number")
    p.add_argument("--type", required=True, choices=("email", "phone"))
    p.add_argument("--reason", default="MANUAL",
                   choices=("MANUAL", "UNSUBSCRIBE", "HARD_BOUNCE"))
    p.set_defaults(func=cmd_suppress_add)
    p = suppress.add_parser("check")
    p.add_argument("value")
    p.set_defaults(func=cmd_suppress_check)

    project = sub.add_parser("project", help="Manage website projects").add_subparsers(
        dest="subcommand", required=True)
    p = project.add_parser("create", help="Convert an interested lead into a client + project")
    p.add_argument("lead_id", type=int)
    p.set_defaults(func=cmd_project_create)
    p = project.add_parser("list")
    p.add_argument("--status")
    p.set_defaults(func=cmd_project_list)
    p = project.add_parser("set", help="Update a project's status/fields")
    p.add_argument("id", type=int)
    p.add_argument("--status")
    p.add_argument("--staging", help="Staging URL")
    p.add_argument("--production", help="Production URL")
    p.add_argument("--revisions", type=int, help="Revision count")
    p.add_argument("--note")
    p.set_defaults(func=cmd_project_set)

    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except workflow.WorkflowError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
