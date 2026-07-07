import socket
import unittest
from unittest import mock

import requests

import site_check


class FakeResponse:
    def __init__(self, text="<title>Acme</title>", status_code=200):
        self.text = text
        self.status_code = status_code


class CheckSiteTests(unittest.TestCase):
    def test_healthy_site(self):
        with mock.patch.object(site_check.requests, "get",
                               return_value=FakeResponse()), \
             mock.patch.object(site_check, "ssl_days_left", return_value=80):
            r = site_check.check_site("https://example.com")
        self.assertTrue(r["ok"])
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["title"], "Acme")
        self.assertEqual(r["ssl_days_left"], 80)

    def test_scheme_added_when_missing(self):
        with mock.patch.object(site_check.requests, "get",
                               return_value=FakeResponse()), \
             mock.patch.object(site_check, "ssl_days_left", return_value=80):
            r = site_check.check_site("example.com")
        self.assertEqual(r["url"], "https://example.com")

    def test_expiring_ssl_flagged(self):
        with mock.patch.object(site_check.requests, "get",
                               return_value=FakeResponse()), \
             mock.patch.object(site_check, "ssl_days_left", return_value=5):
            r = site_check.check_site("https://example.com")
        self.assertTrue(any("SSL expires in 5 days" in p for p in r["problems"]))

    def test_http_error_and_missing_title(self):
        with mock.patch.object(site_check.requests, "get",
                               return_value=FakeResponse(text="", status_code=500)), \
             mock.patch.object(site_check, "ssl_days_left", return_value=80):
            r = site_check.check_site("https://example.com")
        self.assertFalse(r["ok"])
        joined = " ".join(r["problems"])
        self.assertIn("HTTP 500", joined)
        self.assertIn("missing <title>", joined)

    def test_unreachable(self):
        with mock.patch.object(site_check.requests, "get",
                               side_effect=requests.ConnectionError()):
            r = site_check.check_site("https://nope.example")
        self.assertFalse(r["ok"])
        self.assertIn("unreachable", r["problems"][0])


class DomainTests(unittest.TestCase):
    def test_suggestions_shape(self):
        domains = site_check.suggest_domains(
            "Joe's Plumbing LLC", category="plumbing", city="Savannah")
        self.assertIn("joesplumbing.com", domains)
        self.assertIn("joesplumbingsavannah.com", domains)
        self.assertIn("savannahplumbing.com", domains)
        self.assertEqual(len(domains), len(set(domains)))
        for d in domains:
            self.assertTrue(d.endswith(".com"))

    def test_no_words_no_suggestions(self):
        self.assertEqual(site_check.suggest_domains("!!!"), [])

    def test_domain_taken_signals(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=[("ok",)]):
            self.assertTrue(site_check.domain_taken("taken.com"))
        with mock.patch.object(socket, "getaddrinfo",
                               side_effect=socket.gaierror(socket.EAI_NONAME, "")):
            self.assertFalse(site_check.domain_taken("free.com"))
        with mock.patch.object(socket, "getaddrinfo",
                               side_effect=OSError("network down")):
            self.assertIsNone(site_check.domain_taken("unknown.com"))


if __name__ == "__main__":
    unittest.main()
