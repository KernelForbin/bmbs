"""
Every page, loaded at the URL it is actually served from, with nothing but
the data files it is supposed to ask for.

The page tests serve each tracker at a convenient URL and match data requests
loosely, so a page reading "data/tickets.json" where it meant "../data/..."
would pass them all and break in production -- from /mlb/ that request lands
on /mlb/data/... and 404s, which the page reads as "no picks today". This
loads all seven trackers and both history pages at their real paths, answers
only EXACT data paths (with 404, the normal no-slate state), aborts every
other request, and requires: no uncaught page error, the expected <h1>, and
precisely the expected data requests.

It also checks the MLB engine's FEED_FIELDS allow-list is the same in every
page that carries a copy. test_feed_fields.py proves one copy complete
against the live feed; a copy that drifts from it silently loses a field.

    python tests/test_smoke_pages.py
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
ORIGIN = "http://bmbs.test"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# url -> (file, <h1>, the data paths it must request)
PAGES = {
    "/": ("index.html", "BMBS Tracker", {"/data/combined/tickets.json", "/data/combined/tickets-previous.json"}),
    "/mlb/": ("mlb/index.html", "BMBS Tracker — MLB", {"/data/tickets.json", "/data/tickets-previous.json"}),
    "/football/": ("football/index.html", "BMBS Tracker — NFL",
                   {"/data/football/tickets.json", "/data/football/tickets-previous.json"}),
    "/hockey/": ("hockey/index.html", "BMBS Tracker — NHL",
                 {"/data/hockey/tickets.json", "/data/hockey/tickets-previous.json"}),
    "/basketball/": ("basketball/index.html", "BMBS Tracker — NBA",
                     {"/data/basketball/tickets.json", "/data/basketball/tickets-previous.json"}),
    "/wnba/": ("wnba/index.html", "BMBS Tracker — WNBA", {"/data/wnba/tickets.json", "/data/wnba/tickets-previous.json"}),
    "/cfb/": ("cfb/index.html", "BMBS Tracker — College Football",
              {"/data/cfb/tickets.json", "/data/cfb/tickets-previous.json"}),
    "/history/": ("history/index.html", None, {"/data/history.json"}),
    "/football/history/": ("football/history/index.html", None, {"/data/football/history.json"}),
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for url, (file, h1, want) in PAGES.items():
        page = browser.new_page()
        errors, asked, stray = [], set(), []
        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))

        def make_handler(url, file, asked, stray):
            # Playwright passes (route, request) to a handler with two or more
            # parameters, so the per-page values are closed over, not defaulted.
            def handler(route):
                path = route.request.url.split("?", 1)[0].split("#", 1)[0]
                if path == ORIGIN + url:
                    return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                         body=(REPO / file).read_text(encoding="utf-8"))
                if path.startswith(ORIGIN + "/data/"):
                    asked.add(path[len(ORIGIN):])
                    return route.fulfill(status=404, body="")
                if path.startswith(ORIGIN):
                    stray.append(path[len(ORIGIN):])
                return route.abort()
            return handler

        page.route("**/*", make_handler(url, file, asked, stray))
        page.goto(ORIGIN + url)
        page.wait_for_timeout(1500)
        check(f"{url} loads with no uncaught error", not errors, errors[:2])
        if h1:
            check(f"{url} is titled {h1!r}", page.inner_text("h1").strip() == h1, page.inner_text("h1"))
        check(f"{url} asks for exactly its own data files", asked == want, sorted(asked))
        check(f"{url} requests nothing else on the site", not stray, stray[:3])
        page.close()
    browser.close()


# ---------------- the FEED_FIELDS copies agree ----------------

def feed_fields(path):
    src = (REPO / path).read_text(encoding="utf-8")
    m = re.search(r"const FEED_FIELDS = \[(.*?)\];", src, re.S)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return set(re.findall(r'"([A-Za-z]+)"', body))


reference = feed_fields("mlb/index.html")
check("F1 the MLB page has a FEED_FIELDS list", bool(reference))
for path in ("index.html", "hockey/index.html", "basketball/index.html", "wnba/index.html", "cfb/index.html"):
    got = feed_fields(path)
    check(f"F2 {path}'s FEED_FIELDS matches the MLB page's", got == reference,
          {"missing": sorted(reference - (got or set())), "extra": sorted((got or set()) - reference)})

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all page smoke checks passed")
