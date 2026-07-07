import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import db
import drafts
import site_builder
import workflow


GOOD_BRIEF = {
    "business_name": "Joe's Plumbing",
    "tagline": "Fast, honest plumbing",
    "about": "Family-owned since 2009. <script>alert(1)</script>",
    "services": ["Repairs", "Water heaters & drains"],
    "phone": "(912) 555-0101",
    "email": "joe@example.com",
    "address": "123 Main St, Savannah, GA 31401",
    "hours": "Mon-Fri: 7am-6pm",
    "cta_text": "Call Now",
}


class SiteBuilderTests(unittest.TestCase):
    def _build(self, brief):
        tmp = tempfile.mkdtemp()
        brief_path = os.path.join(tmp, "brief.json")
        with open(brief_path, "w", encoding="utf-8") as f:
            json.dump(brief, f)
        return site_builder.build_site(brief_path, os.path.join(tmp, "out"))

    def test_builds_all_pages_with_no_leftover_tokens(self):
        out = self._build(GOOD_BRIEF)
        pages = {p.name for p in pathlib.Path(out).glob("*.html")}
        self.assertEqual(pages, {"index.html", "privacy.html", "terms.html"})
        for page in pages:
            content = (pathlib.Path(out) / page).read_text(encoding="utf-8")
            self.assertNotRegex(content, r"\{\{[A-Z_]+\}\}")
            self.assertIn("Joe&#x27;s Plumbing", content)

    def test_content_is_html_escaped(self):
        out = self._build(GOOD_BRIEF)
        index = (pathlib.Path(out) / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", index)
        self.assertIn("&lt;script&gt;", index)
        self.assertIn("Water heaters &amp; drains", index)

    def test_default_cta_is_tel_link(self):
        out = self._build(GOOD_BRIEF)
        index = (pathlib.Path(out) / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="tel:+19125550101"', index)

    def test_missing_fields_rejected(self):
        brief = dict(GOOD_BRIEF)
        del brief["phone"]
        with self.assertRaises(site_builder.BriefError):
            self._build(brief)

    def test_empty_services_rejected(self):
        brief = dict(GOOD_BRIEF, services=[])
        with self.assertRaises(site_builder.BriefError):
            self._build(brief)

    def test_slugify(self):
        self.assertEqual(site_builder.slugify("Joe's Plumbing & Sons!"),
                         "joe-s-plumbing-sons")
        self.assertEqual(site_builder.slugify("!!!"), "site")


IDENTITY = {
    "SENDER_NAME": "Jo Durham",
    "COMPANY_NAME": "Arxon Solutions",
    "POSTAL_ADDRESS": "1 Main St, Savannah, GA",
    "UNSUBSCRIBE_LINK": "",
}


def make_lead(**overrides):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    fields = {
        "business_name": "Joes Plumbing", "category": "Plumbing",
        "address": "123 Main St, Savannah, GA 31401, United States",
        "phone": "912-555-0101", "contact_name": "Joe",
        "website_status": "none",
    }
    fields.update(overrides)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    conn.execute(f"INSERT INTO leads ({cols}) VALUES ({marks})",
                 tuple(fields.values()))
    return conn.execute("SELECT * FROM leads").fetchone()


class DraftTests(unittest.TestCase):
    def test_all_touches_render_and_pass_compliance(self):
        lead = make_lead()
        with mock.patch.dict(os.environ, IDENTITY):
            for touch in range(1, workflow.MAX_TOUCHES + 1):
                draft = drafts.build_draft(lead, touch)
                self.assertIn("Joes Plumbing", draft)
                self.assertIn("1 Main St, Savannah, GA", draft)  # postal address
                self.assertIn("unsubscribe", draft.lower())

    def test_area_extracted_from_address(self):
        lead = make_lead()
        with mock.patch.dict(os.environ, IDENTITY):
            draft = drafts.build_draft(lead, 1)
        self.assertIn("Savannah", draft)

    def test_area_heuristics(self):
        cases = {
            "123 Main St, Savannah, GA 31401, United States": "Savannah",
            "5 Oak St, Savannah, GA": "Savannah",
            "Savannah, GA": "Savannah",
            "1 Elm St": "your area",
            "": "your area",
        }
        for address, expected in cases.items():
            lead = make_lead(address=address or None,
                             business_name=f"b-{address}")
            self.assertEqual(drafts._area(lead), expected, address)

    def test_plural_category_singularized(self):
        lead = make_lead(category="Restaurants")
        with mock.patch.dict(os.environ, IDENTITY):
            draft = drafts.build_draft(lead, 1)
        self.assertIn("restaurant business", draft)
        self.assertNotIn("restaurants business", draft)

    def test_missing_identity_fails_loudly(self):
        lead = make_lead()
        empty = {k: "" for k in IDENTITY}
        with mock.patch.dict(os.environ, empty):
            with self.assertRaises(drafts.DraftError):
                drafts.build_draft(lead, 1)

    def test_outdated_gap_line(self):
        lead = make_lead(website_status="outdated",
                         outdated_signals="copyright year 2009")
        with mock.patch.dict(os.environ, IDENTITY):
            draft = drafts.build_draft(lead, 1)
        self.assertIn("copyright year 2009", draft)

    def test_invalid_touch_rejected(self):
        lead = make_lead()
        with mock.patch.dict(os.environ, IDENTITY):
            for bad in (0, 5):
                with self.assertRaises(drafts.DraftError):
                    drafts.build_draft(lead, bad)


class DraftDueTests(unittest.TestCase):
    def _conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db.init_db(conn)
        return conn

    def _add(self, conn, **overrides):
        fields = {
            "business_name": "Biz", "category": "plumbing",
            "address": "1 Main St, Savannah, GA", "phone": "912-555-0100",
            "email": "a@b.c", "website_status": "none", "status": "QUALIFIED",
        }
        fields.update(overrides)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO leads ({cols}) VALUES ({marks})",
                           tuple(fields.values()))
        conn.commit()
        return cur.lastrowid

    def test_drafts_qualified_and_due_followups(self):
        conn = self._conn()
        self._add(conn, business_name="Fresh", address="a1")  # touch 1 ready
        self._add(conn, business_name="Due", address="a2", status="FOLLOW_UP",
                  next_action_at="2020-01-01")
        conn.execute("INSERT INTO outreach_log (lead_id, channel, direction, summary) "
                     "VALUES (2, 'email', 'out', 't1')")
        conn.commit()
        with mock.patch.dict(os.environ, IDENTITY):
            drafted, skipped = drafts.build_due_drafts(conn)
        by_name = {lead["business_name"]: touch for lead, touch, _ in drafted}
        self.assertEqual(by_name, {"Fresh": 1, "Due": 2})
        self.assertEqual(skipped, [])

    def test_skips_leads_without_email(self):
        conn = self._conn()
        self._add(conn, email="")
        with mock.patch.dict(os.environ, IDENTITY):
            drafted, skipped = drafts.build_due_drafts(conn)
        self.assertEqual(drafted, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("no email", skipped[0][1])


if __name__ == "__main__":
    unittest.main()
