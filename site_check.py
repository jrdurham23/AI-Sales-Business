"""Live-site health checks for care-plan clients.

`python main.py site check --all` verifies every LIVE/MAINTENANCE client
site in one pass: reachable, HTTPS certificate validity window, load time,
and page title. Feeds the monthly health report
(templates/outreach/health-report.txt) with real numbers, and catches an
expiring certificate before the client's customers see a browser warning.
"""

import datetime
import re
import socket
import ssl
import time
import urllib.parse

import requests

TIMEOUT = 15
SSL_WARN_DAYS = 21
SLOW_SECONDS = 4.0

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def ssl_days_left(hostname, port=443):
    """Days until the certificate expires, or None if unavailable."""
    context = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=TIMEOUT) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as tls:
            cert = tls.getpeercert()
    expires = ssl.cert_time_to_seconds(cert["notAfter"])
    return int((expires - time.time()) // 86400)


def check_site(url):
    """Return a result dict; never raises. Keys:
    url, ok, status_code, load_seconds, title, ssl_days_left, problems."""
    result = {"url": url, "ok": False, "status_code": None,
              "load_seconds": None, "title": None,
              "ssl_days_left": None, "problems": []}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
        result["url"] = url

    try:
        start = time.monotonic()
        res = requests.get(url, timeout=TIMEOUT,
                           headers={"User-Agent": "ArxonSiteCheck/1.0"})
        result["load_seconds"] = round(time.monotonic() - start, 2)
        result["status_code"] = res.status_code
        if res.status_code >= 400:
            result["problems"].append(f"HTTP {res.status_code}")
        else:
            result["ok"] = True
        match = _TITLE_RE.search(res.text or "")
        result["title"] = match.group(1).strip()[:80] if match else None
        if not result["title"]:
            result["problems"].append("missing <title>")
        if result["load_seconds"] > SLOW_SECONDS:
            result["problems"].append(f"slow load ({result['load_seconds']}s)")
    except requests.RequestException as e:
        result["problems"].append(f"unreachable: {e.__class__.__name__}")
        return result

    if url.startswith("https://"):
        hostname = urllib.parse.urlparse(url).hostname
        try:
            days = ssl_days_left(hostname)
            result["ssl_days_left"] = days
            if days is not None and days <= SSL_WARN_DAYS:
                result["problems"].append(f"SSL expires in {days} days")
        except (OSError, ssl.SSLError, KeyError) as e:
            result["problems"].append(f"SSL check failed: {e.__class__.__name__}")

    return result


# ── Domain suggestions ────────────────────────────────────────────────────────

def _slug_words(business_name):
    words = re.sub(r"[^a-z0-9 ]", "", business_name.lower()).split()
    return [w for w in words
            if w not in ("the", "and", "of", "llc", "inc", "co", "company")]


def suggest_domains(business_name, category="", city=""):
    """Candidate .com domains, most brandable first (deduplicated)."""
    words = _slug_words(business_name)
    cat = "".join(_slug_words(category))[:12] if category else ""
    town = "".join(_slug_words(city)) if city else ""
    base = "".join(words)

    candidates = [base]
    if town:
        candidates.append(base + town)
        if cat:
            candidates.append(town + cat)
    if cat and not base.endswith(cat):
        candidates.append(base + cat)
    if len(words) > 1:
        candidates.append("-".join(words))

    seen, out = set(), []
    for c in candidates:
        c = c.strip("-")
        if c and 3 <= len(c) <= 40 and c not in seen:
            seen.add(c)
            out.append(c + ".com")
    return out


def domain_taken(domain):
    """True if DNS resolves (definitely taken), False if NXDOMAIN (likely
    available), None if the check itself failed."""
    try:
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror as e:
        if e.errno == socket.EAI_NONAME:
            return False
        return None
    except OSError:
        return None
