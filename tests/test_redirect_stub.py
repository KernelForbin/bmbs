"""
features.html isn't just a link target -- it's a static redirect stub that has
to actually get a visitor from the bare /features path to /features/, and it
carries the visitor's query string and hash along while doing it (so a
bookmarked /features?sport=football lands back on the football tab instead of
resetting to baseball). test_site_links.py only checks that this file EXISTS
and is where the resolver expects it; nothing exercises the redirect itself.

Two redirect mechanisms are shipped together, on purpose: a <meta
http-equiv="refresh"> for the no-JS/crawler case (which can only target a
fixed URL, so it deliberately drops any query/hash), and a synchronous
<script> location.replace() for everyone else, which appends
location.search + location.hash so the destination keeps them. This checks
both, plus that the script wins the race in a real browser (it runs during
head parsing, before the 0-second meta timer fires).

    python tests/test_redirect_stub.py
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = Path(__file__).resolve().parent.parent / "features.html"
SRC = PAGE.read_text(encoding="utf-8")
ORIGIN = "http://bmbs.test"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- A. the static fallback target, straight from source ----------------
# The no-JS case can only ever hit a fixed URL -- no way to read the current
# query/hash without script -- so this MUST be the trailing-slash form, never
# bare /features (which would just reload this same file forever, see CLAUDE.md).

meta = re.search(r'<meta\s+http-equiv="refresh"\s+content="0;\s*url=([^"]+)"', SRC)
check("A1 the <meta refresh> fallback target is exactly /features/", meta is not None and meta.group(1) == "/features/",
      meta.group(1) if meta else None)
canonical = re.search(r'<link\s+rel="canonical"\s+href="([^"]+)"', SRC)
check("A2 the canonical link agrees: /features/", canonical is not None and canonical.group(1) == "/features/",
      canonical.group(1) if canonical else None)
check("A3 the script-driven redirect appends the CURRENT search and hash (the meta tag alone can't)",
      'location.replace("/features/" + location.search + location.hash)' in SRC)


def serve(p, target_path):
    browser = p.chromium.launch()
    page = browser.new_page()
    requests = []

    def handler(route):
        url = route.request.url
        requests.append(url)
        path = url.split("?", 1)[0].split("#", 1)[0]
        if path == ORIGIN + "/features":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=SRC)
        if path == ORIGIN + "/features/":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<title>features landing</title>")
        return route.abort()

    page.route("**/*", handler)
    page.goto(ORIGIN + target_path)
    page.wait_for_timeout(200)
    return browser, page, requests


with sync_playwright() as p:
    # ---------------- B. a bare hit, no query or hash ----------------
    browser, page, requests = serve(p, "/features")
    check("B1 a bare /features lands on /features/", page.url == ORIGIN + "/features/", page.url)
    check("B2 it actually made two requests -- the stub, then the real destination",
          requests == [ORIGIN + "/features", ORIGIN + "/features/"], requests)
    browser.close()

    # ---------------- C. the case this test exists for: query + hash survive ----------------
    browser, page, requests = serve(p, "/features?sport=football#somesection")
    check("C1 the redirect preserves the query string AND the hash",
          page.url == ORIGIN + "/features/?sport=football#somesection", page.url)
    browser.close()

    # ---------------- D. query alone, no hash ----------------
    browser, page, requests = serve(p, "/features?sport=football")
    check("D1 query alone (no hash) still survives the redirect",
          page.url == ORIGIN + "/features/?sport=football", page.url)
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all redirect-stub checks passed")
