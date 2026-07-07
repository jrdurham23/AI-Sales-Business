import os
import tempfile
import unittest
from unittest import mock

from lead_generator import LeadGenerator


class FakeResponse:
    def __init__(self, text, url="https://example.com", status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code


def make_generator():
    tmp = tempfile.mkdtemp()
    return LeadGenerator(
        api_key="test",
        db_path=os.path.join(tmp, "t.db"),
        csv_path=os.path.join(tmp, "t.csv"),
    )


MODERN_HTML = """<html><head><title>Acme Plumbing</title>
<meta name="viewport" content="width=device-width"></head><body>ok</body></html>"""

BAD_HTML = "<html><head></head><body>hello</body></html>"


class AnalyzeWebsiteTests(unittest.TestCase):
    def test_modern_site_passes(self):
        gen = make_generator()
        with mock.patch.object(gen, "robust_request",
                               return_value=FakeResponse(MODERN_HTML)):
            status, signals = gen.analyze_website("https://example.com")
        self.assertEqual(status, "modern")
        self.assertEqual(signals, [])

    def test_flags_http_missing_title_and_viewport(self):
        gen = make_generator()
        with mock.patch.object(gen, "robust_request",
                               return_value=FakeResponse(BAD_HTML, url="http://example.com")):
            status, signals = gen.analyze_website("http://example.com")
        self.assertEqual(status, "outdated")
        joined = " ".join(signals)
        self.assertIn("no HTTPS", joined)
        self.assertIn("no mobile responsiveness", joined)
        self.assertIn("missing page title", joined)

    def test_http_error_is_error_status(self):
        gen = make_generator()
        with mock.patch.object(gen, "robust_request",
                               return_value=FakeResponse("", status_code=500)):
            status, signals = gen.analyze_website("https://example.com")
        self.assertEqual(status, "error")

    def test_connection_failure(self):
        gen = make_generator()
        with mock.patch.object(gen, "robust_request", return_value=None):
            status, signals = gen.analyze_website("https://example.com")
        self.assertEqual(status, "error")


class FranchiseFilterTests(unittest.TestCase):
    def test_expanded_blocklist(self):
        gen = make_generator()
        for name in ("McDonald's #4411", "Planet Fitness Savannah",
                     "Roto-Rooter Plumbing", "Hampton Inn & Suites",
                     "Wells Fargo Bank"):
            self.assertTrue(gen.is_franchise(name), name)
        for name in ("Joe's Plumbing", "Savannah Fitness Studio",
                     "Riverside Diner"):
            self.assertFalse(gen.is_franchise(name), name)


if __name__ == "__main__":
    unittest.main()
