import datetime
import sqlite3
import unittest
from unittest import mock

import compliance
import db
import workflow


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    return conn


def add_lead(conn, **overrides):
    fields = {
        "business_name": "Test Biz", "category": "plumbing",
        "address": "1 Main St, Savannah, GA 31401, United States",
        "phone": "912-555-0100", "email": "owner@test.biz",
        "website_status": "none", "status": "QUALIFIED",
    }
    fields.update(overrides)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO leads ({cols}) VALUES ({marks})",
                       tuple(fields.values()))
    conn.commit()
    return cur.lastrowid


def in_hours():
    """Patch quiet-hours so tests pass regardless of wall-clock time."""
    return mock.patch.object(compliance, "outside_quiet_hours", return_value=False)


class MigrationTests(unittest.TestCase):
    def test_legacy_db_gains_workflow_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_name TEXT, category TEXT, address TEXT, phone TEXT,
                website_url TEXT, website_status TEXT, outdated_signals TEXT,
                source TEXT, date_found TEXT, UNIQUE(business_name, address))
        """)
        conn.execute("INSERT INTO leads (business_name) VALUES ('Old Biz')")
        conn.commit()

        db.init_db(conn)
        row = conn.execute("SELECT * FROM leads").fetchone()
        self.assertEqual(row["business_name"], "Old Biz")
        self.assertEqual(row["status"], "NEW")
        self.assertIn("score", row.keys())

    def test_init_is_idempotent(self):
        conn = make_conn()
        db.init_db(conn)
        db.init_db(conn)


class CadenceTests(unittest.TestCase):
    def test_four_touches_then_dead(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        with in_hours():
            for touch in range(1, 4):
                n, due, _ = workflow.log_outreach(conn, lead_id, "email", f"t{touch}")
                self.assertEqual(n, touch)
                self.assertIsNotNone(due)
                expected = (datetime.date.today() + datetime.timedelta(
                    days=workflow.TOUCH_GAPS_DAYS[touch])).isoformat()
                self.assertEqual(due, expected)
            n, due, _ = workflow.log_outreach(conn, lead_id, "email", "t4")
        self.assertEqual(n, 4)
        self.assertIsNone(due)
        lead = workflow.get_lead(conn, lead_id)
        self.assertEqual(lead["status"], "DEAD")
        self.assertIsNone(lead["next_action"])
        self.assertIsNone(lead["next_action_at"])

    def test_first_touch_sets_contacted(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        with in_hours():
            workflow.log_outreach(conn, lead_id, "email", "t1")
        self.assertEqual(workflow.get_lead(conn, lead_id)["status"], "CONTACTED")

    def test_no_outreach_to_terminal_statuses(self):
        conn = make_conn()
        for status in ("SUPPRESSED", "NOT_INTERESTED", "DEAD", "CLIENT"):
            lead_id = add_lead(conn, status=status,
                               business_name=f"biz-{status}",
                               address=f"addr-{status}")
            with in_hours(), self.assertRaises(workflow.WorkflowError):
                workflow.log_outreach(conn, lead_id, "email", "nope")


class ComplianceTests(unittest.TestCase):
    def test_suppressed_contact_blocks_send(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        compliance.suppress(conn, "owner@test.biz", "email", "UNSUBSCRIBE")
        with in_hours(), self.assertRaises(workflow.WorkflowError):
            workflow.log_outreach(conn, lead_id, "email", "t1")

    def test_suppression_is_case_insensitive(self):
        conn = make_conn()
        compliance.suppress(conn, "Owner@Test.BIZ", "email")
        self.assertIsNotNone(compliance.is_suppressed(conn, "owner@test.biz"))

    def test_quiet_hours_blocks_sms_and_call(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        with mock.patch.object(compliance, "outside_quiet_hours", return_value=True):
            for channel in ("sms", "call"):
                with self.assertRaises(workflow.WorkflowError):
                    workflow.log_outreach(conn, lead_id, channel, "late")
            # email is exempt from quiet hours
            workflow.log_outreach(conn, lead_id, "email", "fine")

    def test_outside_quiet_hours_boundaries(self):
        d = datetime.datetime(2026, 7, 7)
        self.assertTrue(compliance.outside_quiet_hours(d.replace(hour=7)))
        self.assertFalse(compliance.outside_quiet_hours(d.replace(hour=8)))
        self.assertFalse(compliance.outside_quiet_hours(d.replace(hour=20)))
        self.assertTrue(compliance.outside_quiet_hours(d.replace(hour=21)))

    def test_footer_check(self):
        self.assertTrue(compliance.required_footer_problems("buy my stuff"))
        self.assertFalse(compliance.required_footer_problems(
            "buy my stuff\nreply unsubscribe to opt out"))

    def test_build_footer_contains_required_elements(self):
        footer = compliance.build_footer("Jo", "Arxon", "1 Main St", "")
        self.assertIn("1 Main St", footer)
        self.assertFalse(compliance.required_footer_problems(footer))


class ReplyRoutingTests(unittest.TestCase):
    def test_interested_flags_hot(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        workflow.record_reply(conn, lead_id, "interested", "wants pricing")
        lead = workflow.get_lead(conn, lead_id)
        self.assertEqual(lead["status"], "INTERESTED")
        self.assertEqual(lead["next_action_at"], datetime.date.today().isoformat())

    def test_unsubscribe_suppresses_email_and_phone(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        workflow.record_reply(conn, lead_id, "unsubscribe", "stop")
        self.assertEqual(workflow.get_lead(conn, lead_id)["status"], "SUPPRESSED")
        self.assertIsNotNone(compliance.is_suppressed(conn, "owner@test.biz"))
        self.assertIsNotNone(compliance.is_suppressed(conn, "912-555-0100"))

    def test_not_interested_clears_next_action(self):
        conn = make_conn()
        lead_id = add_lead(conn, next_action="follow up", next_action_at="2026-01-01")
        workflow.record_reply(conn, lead_id, "not_interested", "no thanks")
        lead = workflow.get_lead(conn, lead_id)
        self.assertEqual(lead["status"], "NOT_INTERESTED")
        self.assertIsNone(lead["next_action"])

    def test_invalid_outcome_rejected(self):
        conn = make_conn()
        lead_id = add_lead(conn)
        with self.assertRaises(workflow.WorkflowError):
            workflow.record_reply(conn, lead_id, "maybe", "??")


class ProjectTests(unittest.TestCase):
    def test_create_promotes_lead(self):
        conn = make_conn()
        lead_id = add_lead(conn, status="INTERESTED")
        project_id = workflow.create_project(conn, lead_id)
        self.assertEqual(workflow.get_lead(conn, lead_id)["status"], "CLIENT")
        projects = workflow.list_projects(conn)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["id"], project_id)
        self.assertEqual(projects[0]["status"], "INTAKE")

    def test_no_duplicate_projects(self):
        conn = make_conn()
        lead_id = add_lead(conn, status="INTERESTED")
        workflow.create_project(conn, lead_id)
        with self.assertRaises(workflow.WorkflowError):
            workflow.create_project(conn, lead_id)

    def test_invalid_project_status_rejected(self):
        conn = make_conn()
        lead_id = add_lead(conn, status="INTERESTED")
        pid = workflow.create_project(conn, lead_id)
        with self.assertRaises(workflow.WorkflowError):
            workflow.update_project(conn, pid, status="SHIPPED")


class ScoringTests(unittest.TestCase):
    def test_no_website_trade_with_full_contact_scores_high(self):
        score = workflow.compute_score("none", "912-555-0100", "a@b.c", "HVAC")
        self.assertEqual(score, 100)

    def test_modern_site_no_contact_scores_low(self):
        low = workflow.compute_score("modern", "", "", "")
        self.assertLess(low, 30)

    def test_adding_email_rescores(self):
        conn = make_conn()
        lead_id = add_lead(conn, email=None)
        workflow.rescore_lead(conn, lead_id)
        before = workflow.get_lead(conn, lead_id)["score"]
        workflow.update_lead(conn, lead_id, email="owner@test.biz")
        after = workflow.get_lead(conn, lead_id)["score"]
        self.assertGreater(after, before)


class WorklistTests(unittest.TestCase):
    def test_new_leads_ordered_by_score(self):
        conn = make_conn()
        low = add_lead(conn, status="NEW", business_name="low", address="a1",
                       phone="", email=None, website_status="outdated")
        high = add_lead(conn, status="NEW", business_name="high", address="a2")
        for lid in (low, high):
            workflow.rescore_lead(conn, lid)
        work = workflow.today_worklist(conn)
        names = [l["business_name"] for l in work["new_to_review"]]
        self.assertEqual(names, ["high", "low"])

    def test_qualified_leads_surface_for_first_touch(self):
        conn = make_conn()
        lead_id = add_lead(conn)  # QUALIFIED, no next_action_at
        work = workflow.today_worklist(conn)
        self.assertEqual([l["id"] for l in work["ready_for_first_touch"]], [lead_id])
        # once touch 1 is logged, it moves to the scheduled follow-up track
        with in_hours():
            workflow.log_outreach(conn, lead_id, "email", "t1")
        work = workflow.today_worklist(conn)
        self.assertEqual(work["ready_for_first_touch"], [])

    def test_due_excludes_terminal_statuses(self):
        conn = make_conn()
        add_lead(conn, status="DEAD", next_action_at="2020-01-01",
                 business_name="dead", address="d")
        due = workflow.list_leads(conn, due=True)
        self.assertEqual(due, [])


if __name__ == "__main__":
    unittest.main()
