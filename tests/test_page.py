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

def card(players, name="Card 1 &middot; Test"):
    return {"name": name, "sub": f"{len(players)}-Leg", "foot": "<b>$3</b> bet by Memo",
            "stake": 3.0, "book": "Memo", "payout": 100.0,
            "legs": [{"id": f"{name}-l{i}", "player": p, "team": "XXX", "who": "Kenny", "meta": "XXX &middot; Kenny",
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

def feed_lineup(abstract, lineup, hrs=()):
    """Like feed(), but `lineup` is [{"name": ..., "battingOrder": "301"}, ...] so
    tests can construct an explicit starter -> pinch hitter -> pinch hitter chain
    in one batting-order slot (last two digits of battingOrder = substitution order)."""
    players = {f"ID{i}": {"person": {"fullName": p["name"]}, "battingOrder": p["battingOrder"]} for i, p in enumerate(lineup)}
    return {"gameData": {"status": {"abstractGameState": abstract}},
            "liveData": {"plays": {"allPlays": [{"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
                                                 "matchup": {"batter": {"fullName": n}}} for n in hrs]},
                         "boxscore": {"teams": {"away": {"players": players}, "home": {"players": {}}}},
                         "linescore": {}}}

def hr_play(batter, pitcher, *, bat_side="R", pitch_hand="L", inning=4, top=True, end_time="2026-09-19T23:00:00.000Z",
            exit_velo=110.2, launch_angle=26.0, distance=423.0, trajectory="line_drive",
            pitch_type="Sinker", pitch_velo=97.6, zone=7, at_bat=10):
    """One home run play shaped like the real v1.1 feed (verified against live data)."""
    return {
        "atBatIndex": at_bat,
        "result": {"eventType": "home_run", "event": "Home Run"},
        "about": {"inning": inning, "isTopInning": top, "halfInning": "top" if top else "bottom", "endTime": end_time},
        "matchup": {"batter": {"fullName": batter}, "batSide": {"code": bat_side},
                    "pitcher": {"fullName": pitcher}, "pitchHand": {"code": pitch_hand}},
        "playEvents": [
            {"isPitch": True, "details": {"type": {"code": "FF", "description": "Four-Seam Fastball"}}},
            {"isPitch": True,
             "details": {"type": {"code": "SI", "description": pitch_type}},
             "pitchData": {"startSpeed": pitch_velo, "zone": zone},
             "hitData": {"launchSpeed": exit_velo, "launchAngle": launch_angle,
                         "totalDistance": distance, "trajectory": trajectory}},
        ],
    }

def feed_rich(abstract, roster, plays, *, venue="PNC Park", weather=None, away="PIT", home="MIL"):
    """Feed with venue/weather/team abbreviations and fully-detailed HR plays."""
    weather = weather if weather is not None else {"condition": "Partly Cloudy", "temp": "77", "wind": "5 mph, Out To LF"}
    players = {f"ID{i}": {"person": {"fullName": n}, "battingOrder": f"{i + 1}00"} for i, n in enumerate(roster)}
    return {"gameData": {"status": {"abstractGameState": abstract}, "venue": {"name": venue}, "weather": weather,
                         "teams": {"away": {"abbreviation": away}, "home": {"abbreviation": home}}},
            "liveData": {"plays": {"allPlays": plays},
                         "boxscore": {"teams": {"away": {"players": players}, "home": {"players": {}}}},
                         "linescore": {}}}

def ticket_names(page):
    return page.evaluate("() => [...document.querySelectorAll('#content .ticket-name')].map(e => e.textContent)")


def notif_stub(mode):
    """Replace window.Notification before any page script runs.

    mode: 'granted' (already allowed), 'default-grant' / 'default-deny' (prompts,
    then resolves that way), 'denied' (already blocked), 'unsupported' (no API).
    Records every constructed notification on window.__notifs.
    """
    return """
    window.__notifs = [];
    window.__permRequests = 0;
    (() => {
      const mode = %s;
      if (mode === 'unsupported') {
        Object.defineProperty(window, 'Notification', { value: undefined, configurable: true, writable: true });
        return;
      }
      function N(title, opts) { window.__notifs.push({ title: title, body: (opts || {}).body, tag: (opts || {}).tag }); }
      N.permission = mode === 'granted' ? 'granted' : (mode === 'denied' ? 'denied' : 'default');
      N.requestPermission = function () {
        window.__permRequests++;
        N.permission = (mode === 'default-grant') ? 'granted' : 'denied';
        return Promise.resolve(N.permission);
      };
      Object.defineProperty(window, 'Notification', { value: N, configurable: true, writable: true });
    })();
    """ % json.dumps(mode)


def prefs_stub(overlay=None, push=None):
    sets = []
    if overlay is not None:
        sets.append(f"localStorage.setItem('bmbs.notif.overlay', {'1' if overlay else '0'!r});")
    if push is not None:
        sets.append(f"localStorage.setItem('bmbs.notif.push', {'1' if push else '0'!r});")
    return "try { %s } catch (e) {}" % " ".join(sets)


def bomb_text(page):
    return page.evaluate("""() => {
        const host = document.getElementById('bomb-overlay');
        if (!host.classList.contains('active')) return null;
        const n = host.querySelector('.bomb-name'), w = host.querySelector('.bomb-word');
        return n && w ? (n.textContent + ' ' + w.textContent) : null;
    }""")


def notifs(page):
    return page.evaluate("window.__notifs || []")


def rendered(page, el_id):
    """True only if the element is actually laid out -- unlike visible(), this
    also accounts for a hidden ancestor (getClientRects is empty either way)."""
    return page.evaluate(f"document.getElementById('{el_id}').getClientRects().length > 0")

def hr_rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#hrlog-list .hr-row')].map(r => ({
        batter: r.querySelector('.hr-batter').textContent,
        sub: r.querySelector('.hr-sub').textContent.trim(),
        dist: r.querySelector('.hr-dist').textContent,
        ev: r.querySelector('.hr-ev').textContent,
        ours: r.classList.contains('ours'),
    }))""")

def hr_detail(page, batter):
    return page.evaluate(f"""() => {{
        const row = [...document.querySelectorAll('#hrlog-list .hr-row')]
            .find(r => r.querySelector('.hr-batter').textContent === {batter!r});
        if (!row) return null;
        const dl = row.querySelector('.hr-detail');
        if (!dl) return null;
        const out = {{}};
        const dts = [...dl.querySelectorAll('dt')], dds = [...dl.querySelectorAll('dd')];
        dts.forEach((dt, i) => out[dt.textContent] = dds[i].textContent.trim());
        out._zoneCells = dl.querySelectorAll('.zgrid .zcell.on').length;
        out._zoneOut = dl.querySelectorAll('.zgrid .zout').length;
        return out;
    }}""")

def badge_classes(page, player_selector_text):
    return page.evaluate(f"""() => {{
        const rows = [...document.querySelectorAll('#content .single-row, #content .leg')];
        const row = rows.find(r => r.textContent.includes({player_selector_text!r}));
        return row ? row.querySelector('.mark-badge').className : null;
    }}""")

def context_text(page, player_selector_text):
    return page.evaluate(f"""() => {{
        const rows = [...document.querySelectorAll('#content .single-row, #content .leg')];
        const row = rows.find(r => r.textContent.includes({player_selector_text!r}));
        return row ? (row.querySelector('.leg-live-context')?.textContent.trim() ?? '') : null;
    }}""")

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

def open_page(p, at, init_scripts=()):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    for script in init_scripts:
        page.add_init_script(script)
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

    # ========== H: Pinch Hit Protection ==========
    # Slot 3, away team: Original Guy (300) -> First Sub (301) -> Second Sub (302).
    # Game 699 is an unrelated always-live game on the same date, included purely
    # so the slate itself never rolls over to Yesterday mid-scenario (that's a
    # separate feature, tested above) -- this block is only about how one leg
    # resolves under Pinch Hit Protection.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Original Guy")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(601, "Live"), (699, "Live")])}
    lineup2 = [{"name": "Original Guy", "battingOrder": "300"}, {"name": "First Sub", "battingOrder": "301"}]
    FX["feeds"] = {601: feed_lineup("Live", lineup2, hrs=[]), 699: feed_lineup("Live", [{"name": "Nobody Tracked", "battingOrder": "100"}])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 20, 0))
    assert single_states(page) == {"Original Guy": "live"}, single_states(page)
    assert "First Sub" in context_text(page, "Original Guy") and "Pinch Hit Protection" in context_text(page, "Original Guy")
    assert "php-hit" not in badge_classes(page, "Original Guy")
    print("H  OK: pulled player stays live (not an immediate miss) while the replacement is tracked")

    # H2: the replacement goes deep -- credited as a hit, badge flips, payout/tracker follow.
    FX["feeds"][601] = feed_lineup("Live", lineup2, hrs=["First Sub"])
    poll(page)
    assert single_states(page) == {"Original Guy": "hit"}, single_states(page)
    ctx = context_text(page, "Original Guy")
    assert "First Sub" in ctx and "Pinch Hit Protection" in ctx and "credited" in ctx.lower(), ctx
    assert "php-hit" in badge_classes(page, "Original Guy")
    assert text(page, "total-payout") == "$30.00", text(page, "total-payout")
    assert text(page, "count-parlay-hit") == "1"
    kenny_hit = page.evaluate("""() => {
        const row = [...document.querySelectorAll('#bettor-list .bettor-row')].find(r => r.textContent.includes('Kenny'));
        return row ? row.querySelector('.bettor-stat.hit .num').textContent : null;
    }""")
    assert kenny_hit == "1", f"Bettor Tracker should credit Kenny with the PHP hit, got {kenny_hit!r}"
    print("H2 OK: replacement's HR is credited as a hit -- badge, payout, and bet counts all follow")

    # H3: replacement never homers and the game finishes -- now a real miss, with an explanatory note.
    FX["schedules"]["2026-09-19"] = schedule("2026-09-19", [(601, "Final"), (699, "Live")])
    FX["feeds"][601] = feed_lineup("Final", lineup2, hrs=[])
    poll(page)
    assert single_states(page) == {"Original Guy": "miss"}, single_states(page)
    ctx = context_text(page, "Original Guy")
    assert "First Sub" in ctx and "didn't apply" in ctx
    assert "php-hit" not in badge_classes(page, "Original Guy")
    print("H3 OK: no HR from the replacement by game end -> resolves to a real miss, with a note explaining why")

    assert not errors, errors
    browser.close()

    # H4: a SECOND substitution in the same slot -- credit must follow the whole chain,
    # not just the immediate pinch hitter.
    SEEN.clear()
    lineup3 = [{"name": "Original Guy", "battingOrder": "300"},
               {"name": "First Sub", "battingOrder": "301"},
               {"name": "Second Sub", "battingOrder": "302"}]
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(601, "Live")])}
    FX["feeds"] = {601: feed_lineup("Live", lineup3, hrs=["Second Sub"])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 20, 0))
    assert single_states(page) == {"Original Guy": "hit"}, single_states(page)
    ctx = context_text(page, "Original Guy")
    assert "Second Sub" in ctx, ctx
    print("H4 OK: credit follows a double-substitution chain to whoever actually homered")
    assert not errors, errors
    browser.close()

    # H5: the ORIGINAL player homers himself and is later pulled (e.g. pinch-run for) --
    # must stay a plain hit, no PHP messaging or striped badge for a hit he already earned.
    SEEN.clear()
    FX["feeds"] = {601: feed_lineup("Live", lineup2, hrs=["Original Guy"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 20, 0))
    assert single_states(page) == {"Original Guy": "hit"}, single_states(page)
    assert context_text(page, "Original Guy") == "", "no PHP note for a hit the player earned himself"
    assert "php-hit" not in badge_classes(page, "Original Guy")
    print("H5 OK: a player's own hit stays a plain hit even if he's pulled afterward")
    assert not errors, errors
    browser.close()

    # ========== I: Home Run Log ==========
    # Two games: one outdoor (our pick + a non-pick homer), one domed (non-pick).
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Our Slugger")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(801, "Live"), (802, "Live")])}
    FX["feeds"] = {
        801: feed_rich("Live", ["Our Slugger", "Random Guy"], [
            hr_play("Our Slugger", "Some Pitcher", bat_side="L", pitch_hand="R", inning=4, top=True,
                    end_time="2026-09-19T23:30:00.000Z", exit_velo=110.2, launch_angle=26.0, distance=423.0,
                    trajectory="line_drive", pitch_type="Sinker", pitch_velo=97.6, zone=7, at_bat=10),
            hr_play("Random Guy", "Other Pitcher", bat_side="R", pitch_hand="L", inning=2, top=False,
                    end_time="2026-09-19T22:00:00.000Z", exit_velo=99.9, launch_angle=31.0, distance=401.0,
                    trajectory="fly_ball", pitch_type="Curveball", pitch_velo=80.1, zone=13, at_bat=4),
        ], venue="PNC Park", away="PIT", home="MIL"),
        802: feed_rich("Live", ["Dome Guy"], [
            hr_play("Dome Guy", "Dome Pitcher", inning=7, top=True, end_time="2026-09-20T00:15:00.000Z",
                    distance=388.0, exit_velo=104.0, zone=5, at_bat=22),
        ], venue="Chase Field", weather={"condition": "Roof Closed", "temp": "74", "wind": "0 mph, None"},
           away="ARI", home="SD"),
    }

    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))

    # I1: collapsed by default, with the tap-to-expand help text showing.
    assert page.evaluate("document.getElementById('hrlog-section').classList.contains('collapsed')")
    assert not rendered(page, "hrlog-list"), "body should be hidden while collapsed"
    assert "Tap to expand" in text(page, "hrlog-sub"), text(page, "hrlog-sub")
    print("I1 OK: Home Run Log starts collapsed with tap-to-expand help text")

    page.click(".hrlog-head")
    assert rendered(page, "hrlog-list")
    assert "Tap to expand" not in text(page, "hrlog-sub")

    # I2: default filter is Our Picks -- only the picked hitter.
    rows = hr_rows(page)
    assert [r["batter"] for r in rows] == ["Our Slugger"], rows
    assert rows[0]["ours"] is True
    assert rows[0]["dist"] == "423 ft" and rows[0]["ev"] == "110.2 mph", rows[0]
    assert "PIT" in rows[0]["sub"] and "Top 4" in rows[0]["sub"] and "Some Pitcher" in rows[0]["sub"], rows[0]["sub"]
    print("I2 OK: 'Our Picks' shows only picked hitters, with team/inning/pitcher summary")

    # I3: All Home Runs -- every HR league-wide, newest first, picks still highlighted.
    page.click("#hrlog-btn-all")
    rows = hr_rows(page)
    assert [r["batter"] for r in rows] == ["Dome Guy", "Our Slugger", "Random Guy"], rows
    assert [r["ours"] for r in rows] == [False, True, False], rows
    print("I3 OK: 'All Home Runs' lists league-wide HRs newest-first; only picks carry the highlight")

    # I4: expanded detail lands in the right fields.
    page.evaluate("""() => [...document.querySelectorAll('#hrlog-list .hr-row')]
        .find(r => r.querySelector('.hr-batter').textContent === 'Our Slugger').click()""")
    d = hr_detail(page, "Our Slugger")
    assert d["Bats"] == "Left" and d["Throws"] == "Right", d
    assert d["Team"] == "PIT" and d["Pitcher"] == "Some Pitcher", d
    assert d["Pitch"] == "Sinker · 97.6 mph", d["Pitch"]
    assert d["Exit velo"] == "110.2 mph" and d["Launch angle"] == "26°" and d["Distance"] == "423 ft", d
    assert d["Trajectory"] == "Line drive", d["Trajectory"]
    assert d["Ballpark"] == "PNC Park", d
    assert d["Wind"] == "5 mph, Out To LF" and d["Temperature"] == "77°F", d
    assert d["Location"] == "Low" and d["_zoneCells"] == 1 and d["_zoneOut"] == 0, d
    print("I4 OK: expanded row shows every Statcast/matchup field in the right place")

    # I5: an out-of-zone pitch renders as a chase marker, not a filled grid cell.
    page.evaluate("""() => [...document.querySelectorAll('#hrlog-list .hr-row')]
        .find(r => r.querySelector('.hr-batter').textContent === 'Random Guy').click()""")
    d = hr_detail(page, "Random Guy")
    assert d["Location"] == "Low (chase)" and d["_zoneCells"] == 0 and d["_zoneOut"] == 1, d
    print("I5 OK: out-of-zone pitch renders as a chase marker outside the strike zone grid")

    # I6: a roofed park shows the roof, not a bogus "0 mph, None" calm reading.
    page.evaluate("""() => [...document.querySelectorAll('#hrlog-list .hr-row')]
        .find(r => r.querySelector('.hr-batter').textContent === 'Dome Guy').click()""")
    d = hr_detail(page, "Dome Guy")
    assert d["Wind"] == "Roof Closed", d["Wind"]
    assert d["Ballpark"] == "Chase Field" and d["Team"] == "ARI", d
    print("I6 OK: domed park reports the roof instead of a meaningless 0 mph wind")

    # I7: with no picks having homered, 'Our Picks' explains itself rather than going blank.
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Nobody Homered")])
    poll(page)
    page.click("#hrlog-btn-picks")
    assert hr_rows(page) == []
    assert "All Home Runs" in text(page, "hrlog-list"), text(page, "hrlog-list")
    print("I7 OK: empty 'Our Picks' state points at the All Home Runs filter")

    assert not errors, errors
    browser.close()

    # ========== J: Bomb notifications ==========
    # Slugger is picked twice (a parlay leg and a single) to prove one HR is
    # one notification. Already-Deep has homered before the page ever loads,
    # to prove the first poll seeds silently instead of flooding.
    def bomb_fixtures(hrs):
        FX["tickets"] = tickets("2026-09-19", [("Kenny", "Slugger"), ("Memo", "Already Deep")],
                                [card(["Slugger", "Other Guy"])])
        FX["previous"] = None
        FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(901, "Live")])}
        FX["feeds"] = {901: feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=hrs)}

    # J1: first poll is silent even though a pick has already homered.
    SEEN.clear()
    bomb_fixtures(["Already Deep"])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=True)])
    assert page.evaluate("document.getElementById('notif-overlay').checked") is True
    assert bomb_text(page) is None, "no overlay for a HR that happened before load"
    assert notifs(page) == [], "no push for a HR that happened before load"
    print("J1 OK: first poll seeds silently -- loading mid-game doesn't flood notifications")

    # J2: both toggles on -- a live->hit transition fires overlay AND push, once.
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Already Deep", "Slugger"])
    poll(page)
    assert bomb_text(page) == "Slugger BOMB!", bomb_text(page)
    assert [n["title"] for n in notifs(page)] == ["Slugger BOMB! \U0001F4A3"], notifs(page)
    print("J2 OK: both on -> overlay and OS notification, once, despite two picks naming him")

    # J3: a further poll with no change re-triggers nothing.
    poll(page)
    assert len(notifs(page)) == 1, notifs(page)
    assert page.evaluate("BOMB_QUEUE.length") == 0
    print("J3 OK: an unchanged poll doesn't re-notify")

    # J4: several players deep in one poll queue instead of clobbering.
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"],
                            hrs=["Already Deep", "Slugger", "Other Guy", "Not Ours"])
    poll(page)
    # Slugger's overlay is still up, so Other Guy waits his turn rather than
    # clobbering it -- and 'Not Ours' never notifies at all.
    assert bomb_text(page) == "Slugger BOMB!", bomb_text(page)
    assert page.evaluate("BOMB_QUEUE.length") == 1, page.evaluate("BOMB_QUEUE")
    assert len(notifs(page)) == 2, "only picks notify -- 'Not Ours' is league-wide noise"
    assert [n["title"] for n in notifs(page)][1] == "Other Guy BOMB! \U0001F4A3", notifs(page)
    page.wait_for_timeout(int(page.evaluate("BOMB_MS")) + 300)
    assert bomb_text(page) == "Other Guy BOMB!", "queue should advance once the first overlay times out"
    print("J4 OK: only picked players notify; simultaneous bombs queue and drain one at a time")
    assert not errors, errors
    browser.close()

    # J5: overlay only -- no OS notification.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=False)])
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) == "Slugger BOMB!" and notifs(page) == [], (bomb_text(page), notifs(page))
    print("J5 OK: overlay only -> overlay fires, no OS notification")
    assert not errors, errors
    browser.close()

    # J6: push only -- no overlay.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=False, push=True)])
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) is None and len(notifs(page)) == 1, (bomb_text(page), notifs(page))
    print("J6 OK: push only -> OS notification, no overlay")
    assert not errors, errors
    browser.close()

    # J7: both off -- silence.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=False, push=False)])
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) is None and notifs(page) == []
    assert single_states(page)["Slugger"] == "hit", "the page still tracks the hit itself"
    print("J7 OK: both off -> no notification of any kind, hit still tracked on the page")
    assert not errors, errors
    browser.close()

    # J8: a hidden tab gets the push but no overlay, and nothing is queued for later.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=True)])
    page.evaluate("Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })")
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) is None and len(notifs(page)) == 1
    assert page.evaluate("BOMB_QUEUE.length") == 0, "backgrounded tab must not bank a missed-event queue"
    print("J8 OK: hidden tab -> push fires, overlay doesn't, nothing queued for later")
    assert not errors, errors
    browser.close()

    # J9: turning Push on prompts once and sticks when granted.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("default-grant"), prefs_stub(overlay=True, push=False)])
    assert page.evaluate("window.__permRequests") == 0, "must not prompt on load"
    page.click("#notif-push")
    page.wait_for_function("document.getElementById('notif-push').checked === true")
    assert page.evaluate("window.__permRequests") == 1
    assert text(page, "notif-note") == ""
    assert page.evaluate("localStorage.getItem('bmbs.notif.push')") == "1"
    print("J9 OK: Push prompts only on the toggle gesture, and persists when granted")
    assert not errors, errors
    browser.close()

    # J10: denied -- toggle flips back off with an explanation, overlay untouched.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("default-deny"), prefs_stub(overlay=True, push=False)])
    page.click("#notif-push")
    page.wait_for_function("document.getElementById('notif-push').checked === false")
    assert "blocked" in text(page, "notif-note").lower(), text(page, "notif-note")
    assert page.evaluate("localStorage.getItem('bmbs.notif.push')") == "0"
    assert page.evaluate("document.getElementById('notif-overlay').checked") is True, "overlay toggle unaffected"
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) == "Slugger BOMB!" and notifs(page) == []
    print("J10 OK: denied -> Push flips back off with a note; overlay keeps working")
    assert not errors, errors
    browser.close()

    # J11: no Notification API at all -- Push disabled, overlay unaffected.
    SEEN.clear()
    bomb_fixtures([])
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("unsupported"), prefs_stub(overlay=True, push=True)])
    assert page.evaluate("document.getElementById('notif-push').disabled") is True
    assert page.evaluate("document.getElementById('notif-push').checked") is False
    assert page.evaluate("localStorage.getItem('bmbs.notif.push')") == "0", "a saved 'on' is cleared when unsupported"
    FX["feeds"][901] = feed("Live", ["Slugger", "Already Deep", "Other Guy", "Not Ours"], hrs=["Slugger"])
    poll(page)
    assert bomb_text(page) == "Slugger BOMB!", "overlay must not depend on the Notification API"
    print("J11 OK: unsupported browser -> Push disabled, overlay still fires")
    assert not errors, errors
    browser.close()

    # ========== K: Irons (open parlay, exactly one leg left) ==========
    # Game 1001 is live; 1002 is final (so C3 resolves to a miss). Anyone not
    # in either boxscore stays not_started, which is still an undecided leg.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Solo Guy")], [
        card(["A1", "A2", "A3"], name="IRON"),          # 2 hit + 1 live   -> Iron
        card(["B1", "B2", "B3"], name="TWO-LEFT"),      # 1 hit + 2 live   -> open, not Iron
        card(["C1", "C2", "C3"], name="DEAD"),          # 2 hit + 1 miss   -> dead, not Iron
        card(["D1", "D2"], name="CASHED"),              # all hit          -> hit, not Iron
        card(["E1", "E2", "E3 Unlisted"], name="IRON-PENDING"),  # 2 hit + 1 not_started -> Iron
    ])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(1001, "Live"), (1002, "Final")])}
    FX["feeds"] = {
        1001: feed("Live", ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "D1", "D2", "E1", "E2", "Solo Guy"],
                   hrs=["A1", "A2", "B1", "C1", "C2", "D1", "D2", "E1", "E2"]),
        1002: feed("Final", ["C3"], hrs=[]),
    }

    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))

    assert text(page, "count-parlay-iron") == "2", text(page, "count-parlay-iron")
    assert text(page, "count-parlay-open") == "4", "3 open parlays + the open single"
    assert text(page, "count-parlay-hit") == "1" and text(page, "count-parlay-miss") == "1"
    print("K1 OK: Irons counted (2) -- one leg left, and still counted within Open")

    page.click("#chip-iron")
    assert ticket_names(page) == ["IRON", "IRON-PENDING"], ticket_names(page)
    assert "IRONS" in text(page, "filter-status-text"), text(page, "filter-status-text")
    print("K2 OK: the Irons filter shows exactly the one-leg-away parlays")

    # The boundary cases the definition turns on.
    assert "TWO-LEFT" not in ticket_names(page), "two legs left is not an Iron"
    assert "DEAD" not in ticket_names(page), "a dead parlay is never an Iron, even with one leg unresolved"
    assert "CASHED" not in ticket_names(page), "an already-cashed parlay is not an Iron"
    print("K3 OK: two-left, dead, and cashed parlays are all excluded")

    # Singles can't be Irons -- the straight-bet tracker empties under this filter.
    assert page.evaluate("""() => {
        const rows = [...document.querySelectorAll('#content .single-row')];
        return rows.length;
    }""") == 0, "a single bet must never qualify as an Iron"
    print("K4 OK: single bets never appear under Irons")

    # Toggling off restores everything; chip active state tracks the filter.
    assert page.evaluate("document.getElementById('chip-iron').classList.contains('active-filter')") is True
    page.click("#chip-iron")
    assert page.evaluate("document.getElementById('chip-iron').classList.contains('active-filter')") is False
    assert sorted(ticket_names(page)) == ["CASHED", "DEAD", "IRON", "IRON-PENDING", "TWO-LEFT"], ticket_names(page)
    print("K5 OK: Irons chip toggles off like the other filters, restoring every ticket")

    # An Iron cashing stops being an Iron.
    FX["feeds"][1001] = feed("Live", ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "D1", "D2", "E1", "E2", "Solo Guy"],
                             hrs=["A1", "A2", "A3", "B1", "C1", "C2", "D1", "D2", "E1", "E2"])
    poll(page)
    assert text(page, "count-parlay-iron") == "1", "the cashed Iron drops out of the count"
    assert text(page, "count-parlay-hit") == "2"
    print("K6 OK: an Iron that cashes leaves the Iron count and becomes a hit")

    assert not errors, errors
    browser.close()

print("\nALL PAGE TESTS PASSED")
