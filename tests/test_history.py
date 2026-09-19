"""
Headless checks for the standalone History page (history/index.html).

Everything is served from one page.route handler with an in-memory fixture
whose answers are worked out by hand below -- no network, nothing under
data/ is read or written.

The first block is the isolation guarantee: the History page must never
reach the MLB Stats API or the live tickets files, however long it's open
and whatever is clicked.

    python tests/test_history.py
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
PAGE_SRC = (REPO / "history" / "index.html").read_text(encoding="utf-8")
INDEX_SRC = (REPO / "index.html").read_text(encoding="utf-8")
ORIGIN = "http://bmbs.test"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def leg(bettor, pick, odds, status):
    return {"bettor": bettor, "pick": pick, "odds": odds, "status": status}


XSS = "<img src=x onerror=window.__xss=1>"

# Hand-worked fixture. Resolved legs (hit/miss) only; DNP legs are void.
#   Ann: hit, miss, miss(+250), hit(+300)   -> 2/4 = 50.0%, flat +2.00u, drought 2, killed 1
#   Bob: hit, hit, hit(+300), 2x DNP        -> 3/3 = 100%,  flat +3.00u, drought 0, killed 0
#   Cy:  hit, miss(+400), miss(+350), miss(+500) -> 1/4 = 25.0%, flat -3.00u, drought 3, killed 1
#   Group: 6/11 = 54.5%; 2 of 6 decided parlays cashed ($140.00); 2 heartbreakers of 5 multi-leg.
HISTORY = {
    "firstSlate": "2026-08-11", "lastSlate": "2026-08-21", "oddsFrom": "2026-08-20",
    "parlays": [
        {"date": "2026-08-11", "set": 1, "won": 100.0,
         "legs": [leg("Ann", "Judge", None, "hit"), leg("Bob", "Soto", None, "hit")]},                       # cashed
        {"date": "2026-08-11", "set": 1,
         "legs": [leg("Ann", "Ohtani", None, "miss"), leg("Bob", "Rice", None, "hit"), leg("Cy", "Alonso", None, "hit")]},  # heartbreaker (Ann/Ohtani)
        {"date": "2026-08-20", "set": 1,
         "legs": [leg("Bob", "Judge", 300, "hit"), leg("Cy", "Ohtani", 400, "miss")]},                       # heartbreaker (Cy/Ohtani)
        {"date": "2026-08-20", "set": 1,
         "legs": [leg("Ann", "Soto", 250, "miss"), leg("Cy", "Rice", 350, "miss")]},                         # missed
        {"date": "2026-08-21", "set": 1, "won": 40.0,
         "legs": [leg("Ann", "Judge", 300, "hit"), leg("Bob", "Trout", 600, "dnp")]},                        # cashed (DNP leg voided)
        {"date": "2026-08-21", "set": 1, "legs": [leg("Cy", "Alonso", 500, "miss")]},                        # missed; 1 leg, so no heartbreaker
        {"date": "2026-08-21", "set": 2, "legs": [leg("Bob", XSS, 650, "dnp")]},                             # void
    ],
    "gotAway": {"minPicks": 2, "checkedPickDays": 5, "agreeingPickDays": 4, "players": [
        {"pick": "Judge", "name": "Aaron Judge", "picks": 3, "pickedDays": 3, "pickedDayHits": 3,
         "freeDays": 0, "freeDayHits": 0, "gotAway": []},
        {"pick": "Ohtani", "name": "Shohei Ohtani", "picks": 2, "pickedDays": 2, "pickedDayHits": 0,
         "freeDays": 1, "freeDayHits": 1, "gotAway": ["2026-08-21"]},
    ]},
}


def serve(p, history=HISTORY, status=200, width=1000):
    """Open the page; returns (page, requests) where requests is every URL the page asked for."""
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 900})
    requests = []
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def handler(route):
        url = route.request.url
        requests.append(url)
        if url == ORIGIN + "/history/":
            return route.fulfill(body=PAGE_SRC, content_type="text/html")
        if url == ORIGIN + "/data/history.json":
            return route.fulfill(status=status, body=json.dumps(history), content_type="application/json")
        if "fonts.googleapis.com" in url or "fonts.gstatic.com" in url:
            return route.fulfill(body="", content_type="text/css")
        return route.abort()   # anything else is recorded above and fails the isolation check

    page.route("**/*", handler)
    page.clock.install()
    page.goto(ORIGIN + "/history/")
    return browser, page, requests, errors


def rows(page, table):
    return page.evaluate(
        "id => [...document.querySelectorAll('#' + id + ' tbody tr')].map(tr => [...tr.cells].map(td => td.textContent.trim()))", table)


def tiles(page):
    return page.evaluate("[...document.querySelectorAll('#tiles .tile')].map(t => [...t.children].map(c => c.textContent))")


def chip(page, group, label):
    page.locator(f"#{group} .chip", has_text=label).first.click()


def log_badges(page):
    return page.evaluate("[...document.querySelectorAll('#log .parlay .badge')].map(b => b.textContent)")


with sync_playwright() as p:
    # ---------- A. isolation: the History page is decoupled from the live tracker ----------
    browser, page, requests, errors = serve(p)
    page.wait_for_selector("#board tbody tr")
    # use the page hard, then let five minutes pass -- 15 of the live page's 20s poll cycles
    chip(page, "bettor-chips", "Ann")
    chip(page, "minpick-chips", "All")
    page.locator("#board thead button", has_text="Legs").click()
    page.fill("#log-search", "Judge")
    chip(page, "outcome-chips", "Cashed")
    page.clock.fast_forward(5 * 60 * 1000)
    page.wait_for_timeout(200)

    asked = sorted(set(u for u in requests if "fonts.g" not in u))
    check("A1 the page requests only itself and data/history.json",
          asked == [ORIGIN + "/data/history.json", ORIGIN + "/history/"], str(asked))
    check("A2 never calls the MLB Stats API", not any("statsapi" in u or "mlb.com" in u for u in requests))
    check("A3 never fetches tickets.json / tickets-previous.json", not any("tickets" in u for u in requests))
    check("A4 history.json fetched exactly once in five minutes (no polling loop)",
          sum(u.endswith("/data/history.json") for u in requests) == 1)
    check("A5 none of the live page's polling machinery exists here", page.evaluate(
        "['pollTimer','pollAndRender','refreshEverything','pollSlate','getGameSnapshot','SLATES','TICKETS']"
        ".every(n => { try { return eval('typeof ' + n) === 'undefined'; } catch (e) { return false; } })"))
    check("A6 page source has no reference to the MLB API, tickets files, or index.html's script",
          not any(s in PAGE_SRC for s in ("statsapi", "tickets.json", "tickets-previous", "<script src")))
    check("A7 live page doesn't load history data (the coupling is one nav link, nothing else)",
          "history.json" not in INDEX_SRC and INDEX_SRC.count("/history/") == 1 and 'href="/history/"' in INDEX_SRC)
    check("A8 History page links back to the live tracker", page.get_attribute("#back-link", "href") == "/")
    check("A9 no script errors", not errors, str(errors))
    browser.close()

    # ---------- B. headline numbers ----------
    browser, page, requests, errors = serve(p)
    page.wait_for_selector("#board tbody tr")
    t = tiles(page)
    check("B1 slates tile", t[0][1] == "3" and t[0][2] == "Aug 11 – Aug 21", str(t[0]))
    check("B2 leg hit rate ignores DNP legs", t[1][1] == "54.5%" and t[1][2] == "6 of 11 legs", str(t[1]))
    check("B3 parlays cashed, void parlay not counted, recorded winnings summed",
          t[2][1] == "2 of 6" and t[2][2] == "$140.00 recorded", str(t[2]))
    check("B4 heartbreakers tile", t[3][1] == "2", str(t[3]))
    check("B5 range note", "7 parlays" in page.inner_text("#range-note") and "Aug 11 through Aug 21" in page.inner_text("#range-note"))
    callout = page.inner_text("#callout")
    check("B6 picked-vs-not callout from the MLB join (3/5 picked, 1/1 not)", "60.0%" in callout and "100.0%" in callout, callout)

    # ---------- C. leaderboard ----------
    board = {r[0]: r for r in rows(page, "board")}
    check("C1 default sort is hit % descending", [r[0] for r in rows(page, "board")] == ["Bob", "Ann", "Cy"])
    check("C2 Ann row", board["Ann"][1:6] == ["4", "2", "50.0%", "+2.00u", "+300"], str(board["Ann"]))
    check("C3 Bob: DNP legs excluded, best hit shown", board["Bob"][1:4] == ["3", "3", "100.0%"] and board["Bob"][6] == "Judge +300", str(board["Bob"]))
    check("C4 Cy: negative flat ROI, drought 3, killed 1", board["Cy"][4] == "−3.00u" and board["Cy"][7:9] == ["3", "1"], str(board["Cy"]))
    check("C5 Ann drought 2, killed 1", board["Ann"][7:9] == ["2", "1"], str(board["Ann"]))
    page.locator("#board thead button", has_text="Drought").click()
    check("C6 click a column to sort", [r[0] for r in rows(page, "board")] == ["Cy", "Ann", "Bob"])
    page.locator("#board thead button", has_text="Drought").click()
    check("C7 click again to reverse", [r[0] for r in rows(page, "board")] == ["Bob", "Ann", "Cy"])
    check("C8 aria-sort reflects it", page.get_attribute("#board th:nth-child(8)", "aria-sort") == "ascending")

    # ---------- D. charts ----------
    check("D1 one column per slate", page.locator("#slate-chart .hit-area").count() == 3)
    check("D2 a star on each slate where a parlay cashed (8/11 and 8/21)", page.locator("#slate-chart .star").count() == 2)
    slate = {r[0]: r for r in rows(page, "slate-table")}
    check("D3 table twin carries the same numbers",
          slate["Aug 11"][1:] == ["5", "4", "80.0%", "1 · $100.00"] and slate["Aug 20"][1:] == ["4", "1", "25.0%", "—"]
          and slate["Aug 21"][1:] == ["2", "1", "50.0%", "1 · $40.00"], str(slate))
    page.locator("#slate-chart").scroll_into_view_if_needed()   # scrolling dismisses the tooltip, so settle first
    page.locator("#slate-chart .hit-area").first.hover()
    page.locator("#slate-chart .hit-area").first.hover()
    tip = page.inner_text("#tip")
    check("D4 hover tooltip", page.is_visible("#tip") and "80.0%" in tip and "4 of 5" in tip and "cashed" in tip, tip)
    page.locator("#slate-chart .hit-area").nth(1).focus()
    check("D5 keyboard focus shows the same tooltip", "25.0%" in page.inner_text("#tip"))
    page.mouse.move(5, 5)
    bands = page.evaluate("[...document.querySelectorAll('#odds-chart .band')].map(b => [b.dataset.band, b.querySelector('.band-read').textContent])")
    check("D6 only bands with priced, resolved legs appear (the +600 DNP legs don't count)",
          [b[0] for b in bands] == ["under +300", "+300 to +399", "+400 to +499", "+500 to +599"], str(bands))
    check("D7 band hit rate vs break-even (+300,+350,+300 -> needs 24.1%)",
          "66.7% hit" in bands[1][1] and "needs 24.1%" in bands[1][1] and "2 of 3" in bands[1][1], bands[1][1])
    check("D8 small samples are labelled", "small sample" in bands[0][1])
    geo = page.evaluate("""(() => { const b = document.querySelectorAll('#odds-chart .band')[1];
        const f = b.querySelector('.band-fill').getBoundingClientRect(), n = b.querySelector('.band-need').getBoundingClientRect();
        return { fill: f.right, need: n.left }; })()""")
    check("D9 a winning band's bar runs past its break-even marker", geo["fill"] > geo["need"], str(geo))

    # ---------- E. players ----------
    check("E1 default 5+ picks hides everyone in this tiny fixture", rows(page, "players") == [["No players match."]])
    chip(page, "minpick-chips", "All")
    players = {r[0]: r for r in rows(page, "players")}
    check("E2 Judge: 3 picks, 3 hits, avg odds, biggest fan, last picked",
          players["Judge"][1:] == ["3", "3", "100.0%", "+300", "Ann (2)", "Aug 21"], str(players.get("Judge")))
    check("E3 DNP-only picks aren't listed", "Trout" not in players and len(players) == 5, str(list(players)))
    page.fill("#player-search", "oht")
    check("E4 search", [r[0] for r in rows(page, "players")] == ["Ohtani"])
    page.fill("#player-search", "")
    chip(page, "minpick-chips", "3+ picks")
    check("E5 min-picks filter", [r[0] for r in rows(page, "players")] == ["Judge"])
    chip(page, "minpick-chips", "All")

    # ---------- F. heartbreakers + got away ----------
    check("F1 heartbreaker summary", page.inner_text("#hearts-sub").startswith("2 of 5 multi-leg parlays (40.0%)"), page.inner_text("#hearts-sub"))
    k = rows(page, "killers")
    check("F2 parlay killer: Ohtani twice, by Ann and Cy", k == [["Ohtani", "2", "Ann, Cy", "Aug 20"]], str(k))
    a = rows(page, "away")
    check("F3 got-away sorted by days that got away", a[0][:3] == ["Ohtani", "1", "100.0% (1/1)"] and a[0][4] == "Aug 21", str(a))
    check("F4 players keep the group's own nickname, so the link finds them in the log", a[1][0] == "Judge")
    foot = page.inner_text("#away-foot")
    check("F5 sheet-vs-MLB disagreement is surfaced", "4 of 5 pick-days" in foot and "1 disagrees" in foot, foot)

    # ---------- G. parlay log ----------
    check("G1 newest first, with outcome badges",
          log_badges(page) == ["Cashed", "Missed", "Void", "1 away", "Missed", "Cashed", "1 away"], str(log_badges(page)))
    check("G2 recorded winnings shown on cashed parlays", page.evaluate("[...document.querySelectorAll('#log .won')].map(w => w.textContent)") == ["$40.00", "$100.00"])
    chip(page, "outcome-chips", "Heartbreakers")
    check("G3 heartbreakers chip", log_badges(page) == ["1 away", "1 away"])
    chip(page, "outcome-chips", "Missed")
    check("G4 missed chip includes heartbreakers (they lost too)", len(log_badges(page)) == 4)
    chip(page, "outcome-chips", "Cashed")
    check("G5 cashed chip", log_badges(page) == ["Cashed", "Cashed"])
    page.locator("#players .linkish", has_text="Rice").click()
    check("G6 tapping a player opens their parlays (and resets the outcome chip)",
          page.input_value("#log-search") == "Rice" and log_badges(page) == ["Missed", "1 away"], str(log_badges(page)))
    page.fill("#log-search", "")
    check("G7 sheet text is never parsed as HTML",
          page.evaluate("window.__xss === undefined && document.querySelectorAll('#log img').length === 0")
          and XSS in page.inner_text("#log"))

    # ---------- H. the bettor filter scopes the whole page ----------
    chip(page, "bettor-chips", "Cy")
    t = tiles(page)
    check("H1 tiles", t[1][1] == "25.0%" and t[1][2] == "1 of 4 legs" and t[3][1] == "−3.00u" and t[4][1] == "1", str(t))
    check("H2 cashed tile = parlays Cy was in on", t[2][1] == "0 of 4", str(t[2]))
    check("H3 callout hidden (it's a group-wide stat)", not page.is_visible("#callout"))
    check("H4 leaderboard keeps every bettor, highlights the chosen one",
          len(rows(page, "board")) == 3 and page.locator("#board tr.selected").inner_text().startswith("Cy"))
    check("H5 slate table", {r[0]: r[1:4] for r in rows(page, "slate-table")} ==
          {"Aug 11": ["1", "1", "100.0%"], "Aug 20": ["2", "0", "0.0%"], "Aug 21": ["1", "0", "0.0%"]})
    check("H6 players", sorted(r[0] for r in rows(page, "players")) == ["Alonso", "Ohtani", "Rice"])
    check("H7 killers: only the parlays Cy killed", rows(page, "killers") == [["Ohtani", "1", "Cy", "Aug 20"]])
    check("H8 got-away limited to players Cy has picked", [r[0] for r in rows(page, "away")] == ["Ohtani"])
    check("H9 log limited to Cy's parlays, his legs ringed",
          len(log_badges(page)) == 4 and page.locator("#log .leg.mine").count() == 4)
    bands = page.evaluate("[...document.querySelectorAll('#odds-chart .band')].map(b => b.dataset.band)")
    check("H10 odds chart", bands == ["+300 to +399", "+400 to +499", "+500 to +599"], str(bands))
    page.locator("#board tbody tr", has_text="Bob").click()
    check("H11 tapping a leaderboard row switches the filter",
          page.get_attribute("#bettor-chips .chip[aria-pressed='true']", "aria-pressed") == "true"
          and page.inner_text("#bettor-chips .chip[aria-pressed='true']") == "Bob")
    page.locator("#board tbody tr", has_text="Bob").click()
    check("H12 tapping it again clears the filter", page.inner_text("#bettor-chips .chip[aria-pressed='true']") == "Everyone"
          and len(log_badges(page)) == 7)
    check("H13 no script errors", not errors, str(errors))
    browser.close()

    # ---------- I. degraded data ----------
    browser, page, requests, errors = serve(p, status=404)
    page.wait_for_function("!document.getElementById('range-note').textContent.startsWith('Loading')")
    check("I1 missing history file -> a plain message, not a broken page",
          "Couldn't load" in page.inner_text("#range-note") and not page.is_visible("#app") and not errors, str(errors))
    browser.close()

    no_join = dict(HISTORY, gotAway=None)
    browser, page, requests, errors = serve(p, history=no_join)
    page.wait_for_selector("#board tbody tr")
    check("I2 import run without the MLB join -> section and callout hidden, rest works",
          not page.is_visible("#sec-away") and not page.is_visible("#callout") and len(rows(page, "board")) == 3 and not errors, str(errors))
    browser.close()

    pending = json.loads(json.dumps(HISTORY))
    pending["parlays"].append({"date": "2026-08-22", "set": 1,
                               "legs": [leg("Ann", "Judge", 300, "pending"), leg("Bob", "Soto", 300, "hit")]})
    browser, page, requests, errors = serve(p, history=pending)
    page.wait_for_selector("#board tbody tr")
    t = tiles(page)
    check("I3 an unmarked leg is Pending: not a miss, not a heartbreaker, not in anyone's rate",
          log_badges(page)[0] == "Pending" and t[3][1] == "2" and t[1][2] == "7 of 12 legs" and t[2][1] == "2 of 6", str(t))
    browser.close()

    # ---------- J. phone width ----------
    browser, page, requests, errors = serve(p, width=375)
    page.wait_for_selector("#board tbody tr")
    check("J1 no sideways page scroll on a phone",
          page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
          str(page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")))
    check("J2 chart fits its panel", page.evaluate(
        "document.querySelector('#slate-chart svg').getBoundingClientRect().right <= document.querySelector('#slate-chart').getBoundingClientRect().right + 0.5"))
    browser.close()

    # ---------- K. slates the tracker recorded itself (scripts/record_results.py) ----------
    # They carry what the sheet never had: stakes, who placed the bet, singles,
    # Pinch Hit Protection credits, home run distance.
    site = json.loads(json.dumps(HISTORY))
    site["recordedFrom"] = "2026-08-22"
    site["lastSlate"] = "2026-08-22"
    site["parlays"] += [
        {"date": "2026-08-22", "set": 1, "src": "site", "name": "Card 1", "book": "Ann", "stake": 5.0, "payout": 80.0,
         "returned": 80.0, "won": 80.0,
         "legs": [dict(leg("Ann", "Judge", 300, "hit"), name="Aaron Judge", dist=431),
                  dict(leg("Bob", "Soto", 300, "hit"), php="Pinch Guy")]},
        {"date": "2026-08-22", "set": 1, "src": "site", "name": "Card 2", "book": "Cy", "stake": 3.0, "payout": 60.0,
         "returned": 0.0, "legs": [leg("Cy", "Rice", 400, "miss"), leg("Ann", "Trout", 500, "hit")]},
        {"date": "2026-08-22", "set": 1, "src": "site", "kind": "single", "book": "Bob", "stake": 5.0, "payout": 40.0,
         "returned": 5.0, "legs": [leg("Bob", "Alonso", 700, "dnp")]},
    ]
    browser, page, requests, errors = serve(p, history=site)
    page.wait_for_selector("#board tbody tr")
    t = tiles(page)
    check("K1 Real money tile: $13 staked, $85 back (the void single refunds its stake) -> +$72",
          t[-1][0] == "Real money" and t[-1][1] == "+$72.00" and "$13.00 staked" in t[-1][2] and "$85.00 back" in t[-1][2]
          and "3 bets since Aug 22" in t[-1][2], str(t[-1]))
    check("K2 the older tiles keep their places", t[0][0] == "Slates" and t[3][0] == "Heartbreakers", str([x[0] for x in t]))
    first_day = page.evaluate("""() => { const out = []; let on = false;
        for (const n of document.querySelectorAll('#log > *')) {
          if (n.classList.contains('day')) { if (on) break; on = true; continue; }
          if (on) out.push(n.textContent); }
        return out; }""")
    check("K3 a cashed recorded bet shows what it paid and the stake", "$80.00 on $5.00" in first_day[0], first_day[0])
    check("K4 a hit shows the home run's distance; a PHP credit names the substitute",
          "431 ft" in first_day[0] and "PHP: Pinch Guy" in first_day[0], first_day[0])
    check("K5 a losing recorded bet shows its stake", first_day[1].endswith("$3.00 bet"), first_day[1])
    check("K6 a single is labelled, and a void one reads as refunded",
          "Single" in first_day[2] and first_day[2].endswith("$5.00 refunded"), first_day[2])
    check("K7 the full name rides along as a tooltip", page.evaluate(
        "[...document.querySelectorAll('#log .leg')].some(l => l.title === 'Aaron Judge')"))
    check("K8 footer says where the two halves of the history come from",
          "tracking sheet" in page.inner_text("#footer-line") and "Aug 22" in page.inner_text("#footer-line"))
    chip(page, "bettor-chips", "Cy")
    t = tiles(page)
    check("K9 with a bettor selected, Real money is the bets that person PLACED",
          "Cy placed" in t[-1][0] and t[-1][1] == "−$3.00", str(t[-1]))
    check("K10 no script errors, still only one data request", not errors and
          sorted(set(u for u in requests if "fonts.g" not in u)) == [ORIGIN + "/data/history.json", ORIGIN + "/history/"], str(errors))
    browser.close()

    # sheet-only history (no recorded slates yet) shows no money tile at all
    browser, page, requests, errors = serve(p)
    page.wait_for_selector("#board tbody tr")
    check("K11 no recorded bets -> no Real money tile", all(x[0] != "Real money" for x in tiles(page)))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all history-page checks passed")
