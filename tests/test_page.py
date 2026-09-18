"""
Headless regression checks for the today/yesterday slate logic in index.html.

Everything the page fetches is served from in-memory fixtures via a single
route handler (tickets files, MLB schedule, MLB live feeds); the browser
clock is pinned with page.clock. Nothing under data/ is read or written,
and no real network request is made.

    pip install -r tests/requirements.txt
    python -m playwright install chromium
    python tests/test_page.py
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

INDEX = Path(__file__).resolve().parent.parent / "index.html"

# ---------------- fixtures (mutable so scenarios can flip them) ----------------

def single(i, who, player):
    return {"id": f"single-{i}", "who": who, "player": player, "team": "XXX",
            "meta": "XXX @ YYY &middot; 7:10 PM ET &middot; $5.00 bet", "odds": "+500",
            "stake": 5.0, "payout": 30.0, "pp": "PP $30.00"}

def card(players):
    return {"name": "Card 1 &middot; Test", "sub": f"{len(players)}-Leg", "foot": "<b>$3</b> bet by Memo",
            "stake": 3.0, "book": "Memo", "payout": 100.0,
            "legs": [{"id": f"p0-c0-l{i}", "player": p, "team": "XXX", "who": "Kenny", "meta": "XXX &middot; Kenny",
                      "odds": "+400", "time": "7:10 PM ET"} for i, p in enumerate(players)]}

def tickets(date, singles, cards=()):
    return {"date": date, "note": f"slate {date}",
            "windows": [{"title": "2-Leg Parlay Cards", "tickets": list(cards)}] if cards else [],
            "singles": [single(i, w, p) for i, (w, p) in enumerate(singles)]}

def schedule(date, games):
    return {"dates": [{"date": date, "games": [{"gamePk": pk, "status": {"abstractGameState": st}} for pk, st in games]}]}

def feed(abstract, roster, hrs=()):
    players = {f"ID{i}": {"person": {"fullName": n}, "battingOrder": f"{i + 1}00"} for i, n in enumerate(roster)}
    return {"gameData": {"status": {"abstractGameState": abstract}},
            "liveData": {"plays": {"allPlays": [{"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
                                                 "matchup": {"batter": {"fullName": n}}} for n in hrs]},
                         "boxscore": {"teams": {"away": {"players": players}, "home": {"players": {}}}},
                         "linescore": {}}}

FX = {"tickets": None, "previous": None, "schedules": {}, "feeds": {}}
SEEN = []

def handler(route, request):
    url = request.url
    SEEN.append(url)
    if url.endswith("/index.html"):
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=INDEX.read_bytes())
    if "/data/tickets-previous.json" in url:
        if FX["previous"]:
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["previous"]))
        return route.fulfill(status=404, body="")
    if "/data/tickets.json" in url:
        if FX["tickets"] is None:
            return route.fulfill(status=404, body="")
        if isinstance(FX["tickets"], int):   # sentinel: serve this HTTP status
            return route.fulfill(status=FX["tickets"], body="")
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["tickets"]))
    m = re.search(r"/api/v1/schedule\?.*date=(\d{4}-\d{2}-\d{2})", url)
    if m:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["schedules"].get(m.group(1), {"dates": []})))
    m = re.search(r"/api/v1\.1/game/(\d+)/feed/live", url)
    if m:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["feeds"][int(m.group(1))]))
    return route.abort()  # fonts etc.

def count(pattern):
    return sum(1 for u in SEEN if re.search(pattern, u))

# ---------------- page helpers ----------------

def poll(page):
    page.evaluate("pollAndRender()")

def visible(page, el_id):
    return page.evaluate(f"getComputedStyle(document.getElementById('{el_id}')).display") != "none"

def text(page, el_id):
    return page.evaluate(f"document.getElementById('{el_id}').textContent").strip()

def single_states(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .single-row')]
        .map(r => [r.querySelector('.single-player').textContent, [...r.classList].find(c => c.startsWith('state-')).slice(6)]))""")

def leg_states(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .leg')]
        .map(r => [r.querySelector('.leg-player').textContent, [...r.classList].find(c => c.startsWith('state-')).slice(6)]))""")

def ET(y, mo, d, h, mi):
    # September: ET is UTC-4
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) + timedelta(hours=4)

def open_page(p, at):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.clock.set_fixed_time(at)
    page.route("**/*", handler)
    page.goto("http://bmbs.test/index.html")
    # init() sets pollTimer only after its first poll resolves -- waiting on
    # that keeps our explicit polls from overlapping the boot one. Must be a
    # bare reference: a top-level `let` is not a property of `window`, so
    # `window.pollTimer` would read undefined and never actually wait.
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")  # drive polls by hand; no background races
    poll(page)
    return browser, page, errors


with sync_playwright() as p:
    # ================= A: 1am ET on 9/18, the 9/17 slate still has a live game =================
    FX["tickets"] = tickets("2026-09-17",
                            [("Kenny", "Player Hr"), ("Kevin", "Player Live"), ("Noid", "Player Miss"), ("Joe", "Player Absent")],
                            [card(["Player Hr", "Player Live"])])
    FX["previous"] = tickets("2026-09-16", [("Memo", "Old Guy")])
    FX["schedules"] = {"2026-09-16": schedule("2026-09-16", [(90, "Final")]),
                       "2026-09-17": schedule("2026-09-17", [(101, "Final"), (102, "Live")]),
                       "2026-09-18": schedule("2026-09-18", [(201, "Preview")])}
    FX["feeds"] = {90: feed("Final", ["Old Guy"]),
                   101: feed("Final", ["Player Hr", "Player Miss"], hrs=["Player Hr"]),
                   102: feed("Live", ["Player Live"])}

    browser, page, errors = open_page(p, ET(2026, 9, 18, 1, 0))

    assert not visible(page, "waiting-panel") and visible(page, "content"), "slate should be showing, not waiting"
    assert text(page, "tab-date-today") == "Thu, Sep 17", text(page, "tab-date-today")
    assert text(page, "tab-date-yesterday") == "Wed, Sep 16", text(page, "tab-date-yesterday")
    st = single_states(page)
    assert st == {"Player Hr": "hit", "Player Live": "live", "Player Miss": "miss", "Player Absent": "not_started"}, st
    assert leg_states(page) == {"Player Hr": "hit", "Player Live": "live"}, leg_states(page)
    assert text(page, "count-live") == "2" and text(page, "count-hit") == "2", (text(page, "count-live"), text(page, "count-hit"))
    assert count(r"schedule\?.*date=2026-09-18") == 0, "must not query the wall-clock date"
    assert "LIVE FROM MLB" in text(page, "eyebrow-text")
    assert not visible(page, "queued-note"), "nothing is queued behind the live slate here"
    print("A  OK: past midnight, 9/17 slate stays in Today with live tracking (no reset)")

    poll(page)
    assert count(r"/game/101/feed") == 1, f"Final game re-fetched: {count(r'/game/101/feed')}"
    assert count(r"/game/102/feed") >= 2, "live game must be re-fetched every poll"
    print("A2 OK: Final game's feed fetched once and cached; live game re-polled")

    page.click("#tab-btn-yesterday")
    assert single_states(page) == {"Old Guy": "miss"}, single_states(page)
    assert text(page, "sync-line") == "Final results for Wed, Sep 16", text(page, "sync-line")
    assert "YESTERDAY" in text(page, "eyebrow-text") and not visible(page, "live-dot")
    page.click("#tab-btn-today")
    print("A3 OK: Yesterday tab shows the archived 9/16 slate with final results")

    page.click("#leg-chip-hit")
    assert set(single_states(page).values()) == {"hit"}, single_states(page)
    page.click("#tab-btn-yesterday"); page.click("#tab-btn-today")
    assert set(single_states(page).values()) == {"hit", "live", "miss", "not_started"}, "filter should reset on tab switch"
    print("A4 OK: leg filter works and resets when switching tabs")

    # ================= B: the last 9/17 game goes Final -> rollover =================
    FX["schedules"]["2026-09-17"] = schedule("2026-09-17", [(101, "Final"), (102, "Final")])
    FX["feeds"][102] = feed("Final", ["Player Live"])
    poll(page)

    assert visible(page, "waiting-panel") and not visible(page, "content"), "Today should now be waiting"
    assert text(page, "waiting-title") == "Waiting for today's picks"
    assert text(page, "sync-line") == "Waiting for today's picks", text(page, "sync-line")
    assert text(page, "tab-date-today") == "" and text(page, "tab-date-yesterday") == "Thu, Sep 17"
    assert not visible(page, "live-dot")
    page.click("#tab-btn-yesterday")
    st = single_states(page)
    assert st == {"Player Hr": "hit", "Player Live": "miss", "Player Miss": "miss", "Player Absent": "na"}, st
    assert text(page, "sync-line") == "Final results for Thu, Sep 17", text(page, "sync-line")
    print("B  OK: last game Final -> slate moves to Yesterday, Today shows 'Waiting for today's picks'")

    sched17_before = count(r"schedule\?.*date=2026-09-17")
    poll(page)
    assert count(r"schedule\?.*date=2026-09-17") == sched17_before, "settled slate must not be re-polled"
    print("B2 OK: fully-final slate is settled and no longer polled")

    # ================= C: today's picks get posted =================
    FX["previous"] = FX["tickets"]
    FX["tickets"] = tickets("2026-09-18", [("Bailey", "New Guy")])
    poll(page)
    page.click("#tab-btn-today")
    assert visible(page, "content") and not visible(page, "waiting-panel")
    assert single_states(page) == {"New Guy": "not_started"}, single_states(page)
    assert text(page, "tab-date-today") == "Fri, Sep 18" and text(page, "tab-date-yesterday") == "Thu, Sep 17"
    assert count(r"/game/201/feed") == 0, "Preview games' feeds must not be downloaded"
    page.click("#tab-btn-yesterday")
    assert single_states(page)["Player Hr"] == "hit" and single_states(page)["Player Absent"] == "na"
    print("C  OK: new slate posted -> Today shows it, Yesterday shows the 9/17 archive with final results")
    assert not errors, errors
    browser.close()

    # ================= D: safety valve for a game stuck non-Final =================
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-18", [("Bailey", "New Guy")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-18": schedule("2026-09-18", [(201, "Live")])}
    FX["feeds"] = {201: feed("Live", ["New Guy"])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 5, 59))
    assert visible(page, "content") and single_states(page) == {"New Guy": "live"}, "5:59am next day: still Today"
    assert text(page, "tab-date-yesterday") == ""
    page.clock.set_fixed_time(ET(2026, 9, 19, 6, 1))
    poll(page)
    assert visible(page, "waiting-panel"), "6:01am next day: safety valve should have rolled the slate over"
    page.click("#tab-btn-yesterday")
    assert single_states(page) == {"New Guy": "live"}
    print("D  OK: 6am ET safety valve rolls a stuck slate over; strictly game-state before that")

    # ================= E: legacy tickets.json without a date still renders =================
    FX["tickets"] = {"note": "", "windows": [], "singles": [single(0, "Kenny", "Legacy Guy")]}
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(301, "Live")])}
    FX["feeds"] = {301: feed("Live", ["Legacy Guy"])}
    page.clock.set_fixed_time(ET(2026, 9, 19, 20, 0))
    poll(page)
    page.click("#tab-btn-today")
    assert single_states(page) == {"Legacy Guy": "live"}, single_states(page)
    print("E  OK: date-less legacy tickets.json falls back to the ET calendar date")

    assert not errors, errors
    browser.close()

    # ========== F: tomorrow's picks uploaded while tonight's games are still on ==========
    # The upload archives the live slate into tickets-previous.json, so the
    # newer slate is what's in tickets.json. Today must still show the live one.
    SEEN.clear()
    FX["previous"] = tickets("2026-09-18", [("Kenny", "Tonight Guy")])
    FX["tickets"] = tickets("2026-09-19", [("Bailey", "Tomorrow Guy")])
    FX["schedules"] = {"2026-09-18": schedule("2026-09-18", [(401, "Live")]),
                       "2026-09-19": schedule("2026-09-19", [(501, "Preview")])}
    FX["feeds"] = {401: feed("Live", ["Tonight Guy"])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 0, 45))
    assert text(page, "tab-date-today") == "Fri, Sep 18", text(page, "tab-date-today")
    assert single_states(page) == {"Tonight Guy": "live"}, single_states(page)
    assert text(page, "tab-date-yesterday") == "", "nothing has finished yet"
    assert visible(page, "queued-note") and "Sep 19" in text(page, "queued-note"), text(page, "queued-note")
    print("F  OK: early upload does NOT bump tonight's live slate off Today")

    FX["schedules"]["2026-09-18"] = schedule("2026-09-18", [(401, "Final")])
    FX["feeds"][401] = feed("Final", ["Tonight Guy"])
    poll(page)
    assert text(page, "tab-date-today") == "Sat, Sep 19", text(page, "tab-date-today")
    assert single_states(page) == {"Tomorrow Guy": "not_started"}, single_states(page)
    assert not visible(page, "queued-note"), "note should clear once the handoff happens"
    page.click("#tab-btn-yesterday")
    assert text(page, "tab-date-yesterday") == "Fri, Sep 18"
    assert single_states(page) == {"Tonight Guy": "miss"}, single_states(page)
    print("F2 OK: last game final -> queued slate takes over Today, live one moves to Yesterday")

    assert not errors, errors
    browser.close()

    # ========== G: no picks submitted at all -- neither file exists ==========
    SEEN.clear()
    FX["tickets"] = None
    FX["previous"] = None
    FX["schedules"] = {}
    FX["feeds"] = {}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 12, 0))
    assert visible(page, "waiting-panel") and not visible(page, "content")
    assert text(page, "waiting-title") == "Waiting for today's picks", text(page, "waiting-title")
    assert text(page, "tab-date-today") == "" and text(page, "tab-date-yesterday") == ""
    assert not visible(page, "err-box") or text(page, "err-box") == "", "a missing file is not an error"
    page.click("#tab-btn-yesterday")
    assert visible(page, "waiting-panel") and not visible(page, "content")
    assert text(page, "waiting-title") == "No picks submitted yesterday", text(page, "waiting-title")
    assert text(page, "waiting-text") == "Nothing was submitted for the previous slate.", text(page, "waiting-text")
    assert count(r"statsapi|/api/v1") == 0, "nothing to track, so MLB should not be polled"
    print("G  OK: no slate files at all -> both tabs show their empty states, no MLB polling")

    # a real failure (not a 404) must still surface as an error
    FX["tickets"] = 500
    poll(page)
    assert text(page, "err-box") != "", "a non-404 failure should show the error banner"
    print("G2 OK: a genuine load failure still surfaces an error instead of 'no picks'")

    assert not errors, errors
    browser.close()

print("\nALL PAGE TESTS PASSED")
