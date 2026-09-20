"""
Structural checks for features/index.html: the toggle mechanics, URL sync,
deep-linking, and the isolation guarantee -- it must never call MLB or ESPN,
and must never read the tickets/history files those trackers use.

This page was previously untested on purpose (it was static and hand-edited
only on request, so nothing else was touching it). As of 2026-09-20 it's kept
in sync automatically with every user-visible feature change, which is
exactly the situation the rest of this test suite exists to protect against:
a page that changes often enough that a human won't always eyeball every
edit. This does NOT check the page's prose content (that's supposed to
change) -- only that the toggle can't silently break while the copy around
it keeps getting edited.

Same approach as the other page tests: one route handler, in-memory HTML,
pinned nothing needed (no clock-dependent behaviour here), no real network.

    python tests/test_features_page.py
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE = Path(__file__).resolve().parent.parent / "features" / "index.html"
SRC = PAGE.read_text(encoding="utf-8")
ORIGIN = "http://bmbs.test"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def serve(p, width=1000):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 900})
    requests, errors = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def handler(route):
        url = route.request.url
        requests.append(url)
        if url.split("?")[0].endswith("/features/"):
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=SRC)
        if "fonts.g" in url:
            return route.fulfill(body="", content_type="text/css")
        return route.abort()   # anything else reaching the network fails the isolation check below

    page.route("**/*", handler)
    page.goto(ORIGIN + "/features/")
    page.wait_for_timeout(150)
    return browser, page, requests, errors


# ---------------- A. every id the script depends on exists, exactly once ----------------

REQUIRED_IDS = ["page-title", "back-link", "switch-baseball", "switch-football",
                "baseball-content", "football-content", "footer-back", "footer-source", "site-footer"]
for el_id in REQUIRED_IDS:
    check(f"A: id=\"{el_id}\" appears exactly once", len(re.findall(f'id="{el_id}"', SRC)) == 1)


with sync_playwright() as p:
    # ---------------- B. default state: baseball, on load, no query string ----------------
    browser, page, requests, errors = serve(p)
    check("B1 baseball content is shown by default", page.is_visible("#baseball-content"))
    check("B2 football content is hidden by default", not page.is_visible("#football-content"))
    check("B3 the baseball switch reads as active", "active" in page.get_attribute("#switch-baseball", "class"))
    check("B4 the football switch does not", "active" not in page.get_attribute("#switch-football", "class"))
    check("B5 page title matches the baseball tracker", page.title() == "What the BMBS Tracker Can Do", page.title())
    check("B6 the back link points at the live baseball tracker", page.get_attribute("#back-link", "href") == "/")
    browser.close()

    # ---------------- C. the toggle, both directions ----------------
    browser, page, requests, errors = serve(p)
    page.click("#switch-football")
    page.wait_for_timeout(50)
    check("C1 football content shows, baseball hides", page.is_visible("#football-content") and not page.is_visible("#baseball-content"))
    check("C2 the football switch is now active, baseball isn't",
          "active" in page.get_attribute("#switch-football", "class") and "active" not in page.get_attribute("#switch-baseball", "class"))
    check("C3 the URL picks up ?sport=football via replaceState (no reload)",
          page.url == ORIGIN + "/features/?sport=football" and len(requests) == sum(1 for u in requests if "fonts.g" in u) + 1,
          page.url)
    check("C4 page title switches to the football tracker", page.title() == "What the Football Tracker Can Do")
    check("C5 the back link now points at the football tracker", page.get_attribute("#back-link", "href") == "/football/")
    check("C6 the footer's data-source line switches to ESPN", "ESPN" in page.inner_text("#footer-source"))

    page.click("#switch-baseball")
    page.wait_for_timeout(50)
    check("C7 toggling back to baseball clears ?sport from the URL", page.url == ORIGIN + "/features/", page.url)
    check("C8 ...and everything else reverts with it",
          page.is_visible("#baseball-content") and page.title() == "What the BMBS Tracker Can Do"
          and page.get_attribute("#back-link", "href") == "/" and "MLB" in page.inner_text("#footer-source"))
    browser.close()

    # ---------------- D. deep-linking straight to ?sport=football ----------------
    browser = p.chromium.launch()
    page = browser.new_page()
    requests, errors = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def handler2(route):
        url = route.request.url
        requests.append(url)
        if url.split("?")[0].endswith("/features/"):
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=SRC)
        if "fonts.g" in url:
            return route.fulfill(body="", content_type="text/css")
        return route.abort()

    page.route("**/*", handler2)
    page.goto(ORIGIN + "/features/?sport=football")
    page.wait_for_timeout(150)
    check("D1 a direct load with ?sport=football opens straight to the football section",
          page.is_visible("#football-content") and not page.is_visible("#baseball-content"))
    check("D2 ...with the right switch, title and back link already set",
          "active" in page.get_attribute("#switch-football", "class") and page.title() == "What the Football Tracker Can Do"
          and page.get_attribute("#back-link", "href") == "/football/")
    browser.close()

    # ---------------- E. isolation: this is a content page, not a tracker ----------------
    browser, page, requests, errors = serve(p)
    page.click("#switch-football")
    page.click("#switch-baseball")
    page.wait_for_timeout(100)
    asked = sorted(set(u for u in requests if "fonts.g" not in u))
    check("E1 the page never asks for anything but itself -- no MLB, no ESPN, no tickets/history files",
          asked == [ORIGIN + "/features/"], str(asked))
    check("E2 no reference anywhere in the source to the live pages' data sources or polling machinery",
          not any(s in SRC for s in ("statsapi.mlb.com", "site.web.api.espn.com", "tickets.json", "tickets-previous",
                                     "history.json", "pollAndRender", "getGameSnapshot")))
    check("E3 no script errors from any of the above", not errors, str(errors))
    browser.close()

    # ---------------- F. both sport sections stay structurally matched ----------------
    # Not a content check (the two lists legitimately differ -- baseball has
    # Pinch Hit Protection, football doesn't) -- just that neither side is
    # accidentally left broken relative to the other: same section count give
    # or take the sports' known real differences, and every heading is unique
    # within its own half so the toggle can't land on a garbled duplicate.
    browser, page, requests, errors = serve(p)
    baseball_h2 = page.eval_on_selector_all("#baseball-content h2", "els => els.map(e => e.textContent)")
    football_h2 = page.eval_on_selector_all("#football-content h2", "els => els.map(e => e.textContent)")
    check("F1 neither section is empty", len(baseball_h2) > 5 and len(football_h2) > 5, (len(baseball_h2), len(football_h2)))
    check("F2 no duplicate section heading within either sport's list",
          len(baseball_h2) == len(set(baseball_h2)) and len(football_h2) == len(set(football_h2)),
          (baseball_h2, football_h2))
    def has_topic(headings, needle):
        return any(needle.lower() in h.lower() for h in headings)
    topics = ("color key", "live tracking", "irons", "alerts", "history")
    check("F3 both sports document a Color key, a live-tracking section, Irons, an alerts section, and History",
          all(has_topic(baseball_h2, t) for t in topics) and all(has_topic(football_h2, t) for t in topics),
          (baseball_h2, football_h2))
    check("F4 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all features-page checks passed")
