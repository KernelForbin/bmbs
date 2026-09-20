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
    """`games` entries are (gamePk, abstractGameState) or, when a test cares
    about the detail MLB hangs off that, (gamePk, abstract, detailedState,
    codedGameState, [awayAbbr, homeAbbr]). The page reads `hydrate=team` for
    those abbreviations -- they're the only way to tie a pick to a game that
    has no boxscore yet."""
    out = []
    for g in games:
        pk, abstract = g[0], g[1]
        detailed = g[2] if len(g) > 2 else ("Final" if abstract == "Final" else
                                            "In Progress" if abstract == "Live" else "Scheduled")
        coded = g[3] if len(g) > 3 else ("F" if abstract == "Final" else
                                         "I" if abstract == "Live" else "S")
        teams = g[4] if len(g) > 4 else ["AWY", "HME"]
        out.append({"gamePk": pk,
                    "status": {"abstractGameState": abstract, "detailedState": detailed,
                               "codedGameState": coded, "reason": g[5] if len(g) > 5 else ""},
                    "teams": {"away": {"team": {"abbreviation": teams[0]}},
                              "home": {"team": {"abbreviation": teams[1]}}}})
    return {"dates": [{"date": date, "games": out}]}

def feed(abstract, roster, hrs=(), bench=(), coded=None):
    # battingOrder must stay collision-free: the app reads the FIRST digit as
    # the lineup slot, so a 10th player numbered "1000" would read as slot 1
    # and look like a substitute for the leadoff hitter -- which wrongly
    # triggers Pinch Hit Protection. Give each side its own 1-9 slots.
    assert len(roster) <= 18, "feed() supports 9 batters per side"
    sides = {"away": {}, "home": {}}
    for i, n in enumerate(roster):
        side = "away" if i < 9 else "home"
        sides[side][f"ID{i}"] = {"person": {"fullName": n}, "battingOrder": f"{(i % 9) + 1}00",
                                 "stats": {"batting": {"plateAppearances": 3}}}
    # Bench: in the boxscore (MLB lists a team's whole active roster) but with
    # no batting-order slot and zero plate appearances -- he never got in.
    for j, n in enumerate(bench):
        sides["away"][f"BENCH{j}"] = {"person": {"fullName": n}, "stats": {"batting": {"plateAppearances": 0}}}
    status = {"abstractGameState": abstract}
    if coded:
        status["codedGameState"] = coded
    return {"gameData": {"status": status},
            "liveData": {"plays": {"allPlays": [{"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
                                                 "matchup": {"batter": {"fullName": n}}} for n in hrs]},
                         "boxscore": {"teams": {"away": {"players": sides["away"]}, "home": {"players": sides["home"]}}},
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


def legs_of(page, ticket_name):
    """Visible leg player names for one ticket (waffle stripped)."""
    return page.evaluate(f"""() => {{
        const t = [...document.querySelectorAll('#content .ticket')]
            .find(x => x.querySelector('.ticket-name') && x.querySelector('.ticket-name').textContent === {ticket_name!r});
        return t ? [...t.querySelectorAll('.leg-player')].map(e => e.firstChild.textContent.trim()) : null;
    }}""")


def single_names(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#content .single-row .single-player')].map(e => e.firstChild.textContent.trim())")


def waffles(page):
    """Who currently carries the Iron waffle, split by legs vs singles."""
    return page.evaluate("""() => ({
        legs: [...document.querySelectorAll('#content .leg')]
            .filter(r => r.querySelector('.iron-mark'))
            .map(r => r.querySelector('.leg-player').firstChild.textContent.trim()),
        singles: [...document.querySelectorAll('#content .single-row')]
            .filter(r => r.querySelector('.iron-mark'))
            .map(r => r.querySelector('.single-player').firstChild.textContent.trim()),
    })""")


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

def leg_filters(page):
    return sorted(page.evaluate("() => [...LEG_FILTERS]"))


def bet_filters(page):
    return sorted(page.evaluate("() => [...BET_FILTERS]"))


def bettor_filters(page):
    return sorted(page.evaluate("() => [...BETTOR_FILTERS]"))


def summary_chips(page):
    """The active-filter chips in the consolidated summary bar, in order."""
    return page.evaluate("""() => [...document.querySelectorAll('#filter-summary-chips .fs-chip')]
        .map(c => c.textContent.replace(/\\s+/g, ' ').replace(/\\s*\\u00d7\\s*$/, '').trim())""")


def expand_cards(page):
    """Parlay Cards / Straight Bet Cards default to COLLAPSED (2026-09-20), and
    Playwright's inner_text() only sees visible text. Every section below reads
    the bet lists, so open both right after load. Section R is the one place
    that checks the collapsed default itself, before calling this."""
    page.evaluate("CARDS_OPEN.parlays = CARDS_OPEN.singles = true;"
                  " if (typeof TICKETS !== 'undefined' && TICKETS) renderContent();")

def visible(page, el_id):
    return page.evaluate(f"getComputedStyle(document.getElementById('{el_id}')).display") != "none"

def text(page, el_id):
    return page.evaluate(f"document.getElementById('{el_id}').textContent").strip()

def single_states(page):
    # firstChild, not textContent: the Iron waffle lives in a trailing span.
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .single-row')]
        .map(r => [r.querySelector('.single-player').firstChild.textContent.trim(), [...r.classList].find(c => c.startsWith('state-')).slice(6)]))""")

def leg_states(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .leg')]
        .map(r => [r.querySelector('.leg-player').firstChild.textContent.trim(), [...r.classList].find(c => c.startsWith('state-')).slice(6)]))""")

def ET(y, mo, d, h, mi):
    # September: ET is UTC-4
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc) + timedelta(hours=4)

def open_page(p, at, init_scripts=(), timezone_id=None, expand=True):
    browser = p.chromium.launch()
    opts = {"viewport": {"width": 480, "height": 1000}}
    if timezone_id:
        opts["timezone_id"] = timezone_id   # only section Q needs a non-ET visitor
    page = browser.new_page(**opts)
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
    if expand:
        expand_cards(page)
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
    bets = tuple(text(page, f"count-parlay-{k}") for k in ("open", "hit", "miss"))
    assert bets == ("3", "1", "1"), bets   # open: the card + 2 singles; hit / missed: one single each
    # The LEGS row was removed on 2026-09-18 and put back on 2026-09-20 at the
    # user's request, four chips wide with not_started folded into LIVE.
    legs = tuple(text(page, f"count-leg-{k}") for k in ("live", "hit", "miss", "na"))
    # 4 singles + a 2-leg card: live = Live single + Absent single + Live leg;
    # hit = Hr single + Hr leg; miss = the Miss single. Nobody is void here.
    assert legs == ("3", "2", "1", "0"), legs
    assert "LEGS" in text(page, "slate-stats")
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

    page.click("#chip-hit")
    assert set(single_states(page).values()) == {"hit"}, single_states(page)
    page.click("#tab-btn-yesterday"); page.click("#tab-btn-today")
    assert set(single_states(page).values()) == {"hit", "live", "miss", "not_started"}, "filter should reset on tab switch"
    print("A4 OK: bets filter works and resets when switching tabs")

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

    # I2b: the header pill counts ours vs league-wide, independent of the
    # filter currently selected (Our Picks is active here, yet it reads 3 total).
    assert text(page, "hrlog-count") == "1 OURS · 3 TOTAL · 33%", text(page, "hrlog-count")
    print("I2b OK: header pill shows 'N OURS · M TOTAL · P%' regardless of the active filter")

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

    # I8: the pill recomputes with the new slate -- 0 of our (new) picks have
    # gone deep, but the league-wide total is untouched by the ticket swap.
    assert text(page, "hrlog-count") == "0 OURS · 3 TOTAL · 0%", text(page, "hrlog-count")
    print("I8 OK: header pill recomputes 'ours' on a new upload without changing the league total")

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
    # Slugger has a single riding on him, and a single cashes the moment he goes
    # deep -- so his is the money version of the alert (scenario N covers those).
    assert [n["title"] for n in notifs(page)] == ["Slugger BOMB! \U0001F4A3\U0001F4B0"], notifs(page)
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
    # Other Guy's homer finishes the Slugger / Other Guy card, so his is a cash alert too.
    assert [n["title"] for n in notifs(page)][1] == "Other Guy BOMB! \U0001F4A3\U0001F4B0", notifs(page)
    page.wait_for_timeout(int(page.evaluate("BOMB_CASH_MS")) + 300)   # Slugger's is the longer, money version
    assert bomb_text(page) == "Other Guy BOMB!", "queue should advance once the first overlay times out"
    print("J4 OK: only picked players notify; simultaneous bombs queue and drain one at a time")
    assert not errors, errors
    browser.close()

    # J4b: a player whose ONLY bet is a parlay that's already dead from another
    # leg's miss -> no alert at all, on either channel, even though his own
    # swing is still tracked as a hit on the page. Two games: Miss Mate's is
    # already Final with no HR (his leg missed, killing the card); Lone Dead
    # Guy's is Live, and he's the one who homers this poll.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [], [card(["Miss Mate", "Lone Dead Guy"])])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(910, "Final"), (911, "Live")])}
    FX["feeds"] = {910: feed("Final", ["Miss Mate"]), 911: feed("Live", ["Lone Dead Guy"], hrs=[])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=True)])
    assert page.evaluate("document.querySelector('.ticket.is-dead') !== null"), "the card should already show as dead"
    FX["feeds"][911] = feed("Live", ["Lone Dead Guy"], hrs=["Lone Dead Guy"])
    poll(page)
    assert bomb_text(page) is None, "a dead parlay's only hit must not fire the overlay"
    assert notifs(page) == [], "...or the push notification"
    assert leg_states(page)["Lone Dead Guy"] == "hit", "the leg itself still tracks the hit -- only the alert is suppressed"
    print("J4b OK: a home run on an already-dead parlay (and nothing else) fires no alert on either channel")

    # J4c: give him a second bet that's still alive (a single) -- now he DOES
    # matter, so the alert fires normally despite the dead card.
    FX["tickets"] = tickets("2026-09-19", [("Bernie", "Lone Dead Guy")], [card(["Miss Mate", "Lone Dead Guy"])])
    FX["feeds"][911] = feed("Live", ["Lone Dead Guy"], hrs=[])
    browser.close()
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=True)])
    FX["feeds"][911] = feed("Live", ["Lone Dead Guy"], hrs=["Lone Dead Guy"])
    poll(page)
    assert bomb_text(page) == "Lone Dead Guy BOMB!", "a live single on the same player still matters, dead card or not"
    assert len(notifs(page)) == 1
    print("J4c OK: ...but a still-open single on him fires the alert as normal")
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

    # ========== K: Irons (one home run from cashing) ==========
    # Parlays: IRON (2 hit + 1 live), TWO-LEFT (1 hit + 2 live), DEAD (2 hit +
    # 1 miss), CASHED (all hit). Singles: one open, one hit, one missed.
    # Game 1002 is Final so the "miss" states resolve.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19",
                            [("Kenny", "S Open"), ("Memo", "S Hit"), ("Noid", "S Miss")],
                            [card(["A1", "A2", "A3"], name="IRON"),
                             card(["B1", "B2", "B3"], name="TWO-LEFT"),
                             card(["C1", "C2", "C3"], name="DEAD"),
                             card(["D1", "D2"], name="CASHED")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(1001, "Live"), (1002, "Final")])}
    FX["feeds"] = {
        1001: feed("Live", ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "D1", "D2", "S Open", "S Hit"],
                   hrs=["A1", "A2", "B1", "C1", "C2", "D1", "D2", "S Hit"]),
        1002: feed("Final", ["C3", "S Miss"], hrs=[]),
    }

    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))

    # K1: the badge sums qualifying parlays AND qualifying open singles.
    assert text(page, "count-parlay-iron") == "2", text(page, "count-parlay-iron")   # IRON + S Open
    assert text(page, "count-parlay-open") == "3", "IRON + TWO-LEFT + S Open"
    assert text(page, "count-parlay-hit") == "2" and text(page, "count-parlay-miss") == "2"
    print("K1 OK: Irons badge sums Iron parlays and open singles into one total")

    # K2: waffle shows with NO filter active -- on the remaining leg only, and
    # on the open single. Not on already-hit legs, not on resolved singles.
    w = waffles(page)
    assert w["legs"] == ["A3"], w["legs"]
    assert w["singles"] == ["S Open"], w["singles"]
    print("K2 OK: waffle marks only the remaining Iron leg and the open single")

    # K3: it's there under plain Open too, without switching to Irons.
    page.click("#chip-open")
    w = waffles(page)
    assert w["legs"] == ["A3"] and w["singles"] == ["S Open"], w
    page.click("#chip-open")
    print("K3 OK: waffle shows while browsing under the plain Open filter")

    # K4: the Irons filter -- Iron parlays plus every open single.
    page.click("#chip-iron")
    assert ticket_names(page) == ["IRON"], ticket_names(page)
    assert single_names(page) == ["S Open"], single_names(page)
    # The three per-group status bars became one consolidated summary on
    # 2026-09-20, listing every active filter as its own removable chip.
    assert summary_chips(page) == ["Bets: Irons"], summary_chips(page)
    print("K4 OK: Irons filter shows Iron parlays and open singles together")

    # K5: the FULL parlay renders -- already-hit legs included, not hidden.
    assert legs_of(page, "IRON") == ["A1", "A2", "A3"], legs_of(page, "IRON")
    assert page.evaluate("() => document.querySelectorAll('#content .ticket').length") >= 1
    assert "hidden by the current filter" not in page.evaluate("document.getElementById('content').textContent")
    print("K5 OK: an Iron parlay renders every leg, including the ones already hit")

    # K6: the exclusions the definition turns on.
    assert "TWO-LEFT" not in ticket_names(page), "two legs left is not an Iron"
    assert "DEAD" not in ticket_names(page), "2 hit + 1 missed is dead, never an Iron"
    assert "CASHED" not in ticket_names(page), "a fully-hit parlay is just Hit"
    assert "S Hit" not in single_names(page) and "S Miss" not in single_names(page)
    print("K6 OK: dead, cashed, two-left parlays and resolved singles are all excluded")

    # K7: toggles off like the other chips.
    assert page.evaluate("document.getElementById('chip-iron').classList.contains('active-filter')") is True
    page.click("#chip-iron")
    assert page.evaluate("document.getElementById('chip-iron').classList.contains('active-filter')") is False
    assert sorted(ticket_names(page)) == ["CASHED", "DEAD", "IRON", "TWO-LEFT"], ticket_names(page)
    print("K7 OK: Irons chip toggles off like the other filters")

    # K8: when the Iron leg hits, the parlay cashes and leaves the Iron count.
    FX["feeds"][1001] = feed("Live", ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "C2", "D1", "D2", "S Open", "S Hit"],
                             hrs=["A1", "A2", "A3", "B1", "C1", "C2", "D1", "D2", "S Hit"])
    poll(page)
    assert text(page, "count-parlay-iron") == "1", "only the open single remains an Iron"
    assert waffles(page)["legs"] == [], "a cashed parlay has no remaining leg to mark"
    assert text(page, "count-parlay-hit") == "3"
    print("K8 OK: an Iron that cashes drops out of the Iron count and loses its waffle")
    assert not errors, errors
    browser.close()

    # K9: a single whose player never played is void, not one swing away.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Joe", "Never Played"), ("Kenny", "Did Play")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(1003, "Final")])}
    FX["feeds"] = {1003: feed("Final", ["Did Play"], hrs=["Did Play"])}
    browser, page, errors = open_page(p, ET(2026, 9, 20, 3, 0))
    # 'na' only exists once every game is final -- which also rolls the slate
    # onto the Yesterday tab, so that's where this one is asserted.
    page.click("#tab-btn-yesterday")
    assert single_states(page)["Never Played"] == "na", single_states(page)
    assert waffles(page)["singles"] == [], "an N/A single can't hit any more -- no waffle"
    assert text(page, "count-parlay-iron") == "0", text(page, "count-parlay-iron")
    print("K9 OK: a single whose player didn't play is void, not an Iron")
    assert not errors, errors
    browser.close()

    # ========== L: notification toggles hidden on Yesterday's Slate ==========
    # Bomb notifications only ever fire off Today's live data, so the
    # controls are meaningless next to archived results -- verify they're
    # hidden there and reappear back on Today, without losing their state.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Player Live")])
    FX["previous"] = tickets("2026-09-18", [("Memo", "Old Guy")])
    FX["schedules"] = {"2026-09-18": schedule("2026-09-18", [(1101, "Final")]),
                       "2026-09-19": schedule("2026-09-19", [(1102, "Live")])}
    FX["feeds"] = {1101: feed("Final", ["Old Guy"], hrs=[]), 1102: feed("Live", ["Player Live"], hrs=[])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [prefs_stub(overlay=True, push=False)])
    assert rendered(page, "notif-row"), "notif toggles should show on Today by default"
    page.click("#chip-open")  # incidental UI state, should survive the tab switch below
    page.click("#tab-btn-yesterday")
    assert not rendered(page, "notif-row"), "notif toggles must be hidden on Yesterday's Slate"
    assert not rendered(page, "notif-note"), "the note line hides along with the toggles"
    page.click("#tab-btn-today")
    assert rendered(page, "notif-row"), "toggles come back on returning to Today"
    assert page.evaluate("document.getElementById('notif-overlay').checked") is True, "the setting itself is untouched by hiding the UI"
    print("L  OK: notification toggles hide on Yesterday's Slate and reappear on Today")
    assert not errors, errors
    browser.close()

    # ========== M: overlapping polls are skipped ==========
    # POLL_MS is 10s and a full slate's feeds can take longer than that on a
    # slow connection. setInterval fires regardless, so without a guard two
    # polls run at once and can finish out of order, writing a stale slate
    # over a fresher one. A tick landing mid-poll must be dropped.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Player Live")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(1201, "Live")])}
    FX["feeds"] = {1201: feed("Live", ["Player Live"], hrs=[])}

    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))
    assert page.evaluate("typeof pollOnce === 'function'"), "pollOnce guard is missing"

    # Stand in for the slow part of a poll with a promise the test resolves by
    # hand, so "still in flight" is exact rather than a race against a timer.
    page.evaluate("""() => {
        window.__calls = 0;
        window.__release = null;
        window.__origRefresh = refreshEverything;
        refreshEverything = () => { window.__calls++; return new Promise(r => { window.__release = r; }); };
    }""")

    page.evaluate("() => { pollOnce(); }")                # starts, then blocks (not awaited)
    page.wait_for_function("window.__calls === 1")
    page.evaluate("() => { pollOnce(); pollOnce(); pollOnce(); }")   # ticks landing mid-poll
    page.wait_for_timeout(100)
    assert page.evaluate("window.__calls") == 1, \
        f"overlapping polls not suppressed: {page.evaluate('window.__calls')} concurrent refreshes"
    print("M  OK: a poll tick that lands while one is already running is skipped")

    # ...and once the in-flight poll finishes, polling resumes normally.
    page.evaluate("window.__release()")
    page.wait_for_timeout(100)
    page.evaluate("() => { pollOnce(); }")
    page.wait_for_function("window.__calls === 2")
    print("M2 OK: the guard releases once the in-flight poll finishes")

    # A poll that throws must release the guard, or polling dies for the rest
    # of the session. render() sits outside pollAndRender's own try/catch, so
    # throwing there is a real rejection -- only the `finally` recovers it.
    page.evaluate("""() => {
        window.__release();
        refreshEverything = window.__origRefresh;
        window.__origRender = render;
        render = () => { throw new Error("boom"); };
    }""")
    page.wait_for_timeout(150)
    page.evaluate("() => { pollOnce().catch(() => {}); }")
    page.wait_for_timeout(300)
    page.evaluate("render = window.__origRender")
    SEEN.clear()
    page.evaluate("pollOnce()")
    page.wait_for_timeout(500)
    assert count(r"/game/1201/feed") >= 1, "a poll that threw left the guard wedged shut"
    print("M3 OK: a poll that throws still releases the guard")
    errors.clear()  # the deliberate "boom" above
    assert not errors, errors
    browser.close()

    # ========== N: a bomb that cashes a bet says so ==========
    # Same alert, more on it: when the home run completes a bet, the overlay goes
    # gold, rains money instead of confetti, and names what cashed and for how much.
    def bomb_cash(page):
        return page.evaluate("""() => {
            const host = document.getElementById('bomb-overlay');
            if (!host.classList.contains('active')) return null;
            const c = host.querySelector('.bomb-cash');
            return { gold: !!host.querySelector('.bomb-card.cash'), money: host.querySelectorAll('.bomb-money').length,
                     confetti: host.querySelectorAll('.bomb-confetti').length,
                     cash: c ? c.textContent.replace(/\\s+/g, ' ').trim() : null };
        }""")

    def next_bomb(page):
        page.evaluate("() => { clearTimeout(bombTimer); bombTimer = null; showNextBomb(); }")

    SEEN.clear()
    roster = ["Iron Man", "Setup Guy", "Plain Guy", "Single Guy", "Both Guy", "Other Leg", "Both Mate", "Void Mate"]
    FX["tickets"] = tickets("2026-09-19",
                            [("Kenny", "Single Guy"), ("Joe", "Both Guy")],
                            [card(["Setup Guy", "Iron Man"], "Card 1 &middot; Iron"),          # Setup Guy already hit -> Iron Man is the Iron
                             card(["Plain Guy", "Other Leg"], "Card 2 &middot; Not close"),    # nobody's hit: a HR here cashes nothing
                             card(["Both Mate", "Both Guy"], "Card 3 &middot; Both")])         # Both Guy also has a single
    FX["previous"] = tickets("2026-09-18", [("Memo", "Old Guy")])
    FX["schedules"] = {"2026-09-18": schedule("2026-09-18", [(1401, "Final")]),
                       "2026-09-19": schedule("2026-09-19", [(1402, "Live")])}
    FX["feeds"] = {1401: feed("Final", ["Old Guy"]), 1402: feed("Live", roster, hrs=["Setup Guy", "Both Mate"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), [notif_stub("granted"), prefs_stub(overlay=True, push=True)])
    assert bomb_text(page) is None, "seeding poll must stay silent"

    FX["feeds"][1402] = feed("Live", roster, hrs=["Setup Guy", "Both Mate", "Plain Guy"])
    poll(page)
    c = bomb_cash(page)
    assert bomb_text(page) == "Plain Guy BOMB!" and not c["gold"] and c["cash"] is None and c["money"] == 0 and c["confetti"] > 0, c
    assert notifs(page)[-1]["body"] == "Home run \u2014 bmbs.bet", notifs(page)[-1]
    print("N1 OK: a home run that cashes nothing is the plain green bomb, exactly as before")
    # Asked about someone who HASN'T gone deep, the answer is "nothing" -- his
    # single isn't cashed just because it exists.
    assert page.evaluate("betsCashedBy(SLATES.today, 'Single Guy').length") == 0, "an open single isn't a cashed one"
    assert page.evaluate("betsCashedBy(SLATES.today, 'Iron Man').length") == 0, "an Iron that hasn't hit hasn't cashed"
    print("N1b OK: nothing is 'cashed' for a player who hasn't homered")

    FX["feeds"][1402] = feed("Live", roster, hrs=["Setup Guy", "Both Mate", "Plain Guy", "Iron Man"])
    poll(page)
    next_bomb(page)
    c = bomb_cash(page)
    assert bomb_text(page) == "Iron Man BOMB!", bomb_text(page)
    assert c["gold"] and c["money"] > 0 and c["confetti"] == 0, c
    assert c["cash"].endswith("2-LEG PARLAY CASHED$100.00"), c["cash"]
    n = notifs(page)[-1]
    assert n["title"] == "Iron Man BOMB! \U0001F4A3\U0001F4B0" and "2-LEG PARLAY CASHED" in n["body"] and "$100.00" in n["body"], n
    assert page.evaluate("BOMB_CASH_MS > BOMB_MS"), "a cash alert has more to read, so it stays up longer"
    print("N2 OK: an Iron going deep -> gold alert, money rain, '2-LEG PARLAY CASHED $100.00', and the push says it too")

    FX["feeds"][1402] = feed("Live", roster, hrs=["Setup Guy", "Both Mate", "Plain Guy", "Iron Man", "Single Guy"])
    poll(page)
    next_bomb(page)
    assert bomb_text(page) == "Single Guy BOMB!" and bomb_cash(page)["cash"].endswith("SINGLE CASHED$30.00"), bomb_cash(page)
    print("N3 OK: a single cashing is worded as a single")

    FX["feeds"][1402] = feed("Live", roster, hrs=["Setup Guy", "Both Mate", "Plain Guy", "Iron Man", "Single Guy", "Both Guy"])
    poll(page)
    next_bomb(page)
    assert bomb_text(page) == "Both Guy BOMB!" and bomb_cash(page)["cash"].endswith("2 BETS CASHED$130.00"), bomb_cash(page)
    print("N4 OK: one swing cashing a parlay and a single -> '2 BETS CASHED' with the combined payout")

    # The helpers the cash check leans on follow whichever tab is showing; a bomb
    # is about TODAY's slate even while Yesterday's is on screen.
    page.evaluate("() => { dismissBomb(); }")
    FX["tickets"]["windows"][0]["tickets"].append(card(["Setup Guy", "Late Iron"], "Card 4 &middot; Late"))
    FX["feeds"][1402] = feed("Live", roster + ["Late Iron"], hrs=["Setup Guy", "Both Mate", "Plain Guy", "Iron Man", "Single Guy", "Both Guy"])
    poll(page)
    page.click("#tab-btn-yesterday")
    FX["feeds"][1402] = feed("Live", roster + ["Late Iron"], hrs=["Setup Guy", "Both Mate", "Plain Guy", "Iron Man", "Single Guy", "Both Guy", "Late Iron"])
    poll(page)
    assert bomb_text(page) == "Late Iron BOMB!" and bomb_cash(page)["cash"].endswith("2-LEG PARLAY CASHED$100.00"), (bomb_text(page), bomb_cash(page))
    assert single_states(page) == {"Old Guy": "miss"}, "evaluating today's bets must not disturb the tab on screen"
    print("N5 OK: cash detection reads today's slate even from the Yesterday tab, and leaves that tab alone")
    assert not errors, errors
    browser.close()

    # ========== O: the parser's `note` field actually reaches the screen ==========
    # scripts/parse_picks.py writes an unread-bet-line warning into tickets.json's
    # `note` specifically so the group sees it at the top of the live page -- every
    # fixture above sets a note (tickets() defaults to f"slate {date}") but nothing
    # ever asserted the banner renders it. This closes that loop.
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Player Live")])
    FX["tickets"]["note"] = "⚠ 1 line couldn't be read — first one: “Ticket #2”"
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(1801, "Live")])}
    FX["feeds"] = {1801: feed("Live", ["Player Live"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))
    assert text(page, "dynamic-note") == "Heads up — ⚠ 1 line couldn't be read — first one: “Ticket #2”", text(page, "dynamic-note")
    print("O1 OK: a real parser warning in tickets.json's note renders verbatim in the banner")

    # The old "Auto-tracked against live MLB results." fallback was removed on
    # 2026-09-20 as redundant with the LIVE FROM MLB eyebrow. The banner must
    # now be EMPTY when there is no warning -- but it must still exist, because
    # it is the only place an unread bet line surfaces.
    FX["tickets"]["note"] = ""
    poll(page)
    assert text(page, "dynamic-note") == "", text(page, "dynamic-note")
    print("O2 OK: no warning -> the banner is empty, not filled with boilerplate")
    assert not errors, errors
    browser.close()

    # a slate with nothing to show (Waiting for today's picks) must not leak
    # a stale note into a banner that no longer has a slate to describe
    FX["tickets"] = None
    FX["previous"] = None
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))
    assert text(page, "dynamic-note") == "", text(page, "dynamic-note")
    print("O3 OK: the waiting-for-picks state leaves the note banner empty, not stale")
    assert not errors, errors
    browser.close()

    # ========== P: the bettor filter ==========
    # legPassesFilters() only does anything while a bettor is selected, and
    # nothing here ever set it -- the Bettor Tracker rows were only ever read,
    # never clicked. So the whole filtering path (including the per-leg hiding
    # inside a mixed-bettor parlay) went unverified.
    SEEN.clear()
    all_kenny = card(["Kenny Leg A", "Kenny Leg B"], "Card All Kenny")
    mixed = card(["Mixed Kenny Leg", "Mixed Memo Leg"], "Card Mixed")
    mixed["legs"][1]["who"] = "Memo"
    mixed["legs"][1]["meta"] = "XXX &middot; Memo"
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Kenny Single"), ("Memo", "Memo Single")],
                            cards=[all_kenny, mixed])
    FX["previous"] = tickets("2026-09-18", [("Kenny", "Old Guy")])
    FX["schedules"] = {"2026-09-18": schedule("2026-09-18", [(1901, "Final")]),
                       "2026-09-19": schedule("2026-09-19", [(1902, "Live")])}
    FX["feeds"] = {1901: feed("Final", ["Old Guy"]),
                   1902: feed("Live", ["Kenny Single", "Memo Single", "Kenny Leg A", "Kenny Leg B",
                                       "Mixed Kenny Leg", "Mixed Memo Leg"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0))

    def click_bettor(name):
        page.evaluate("""(n) => [...document.querySelectorAll('#bettor-list .bettor-row')]
            .find(r => r.textContent.includes(n)).click()""", name)
        page.wait_for_timeout(50)

    unfiltered_singles, unfiltered_legs = single_states(page), leg_states(page)
    assert set(unfiltered_singles) == {"Kenny Single", "Memo Single"}, unfiltered_singles
    assert len(unfiltered_legs) == 4, unfiltered_legs
    assert bettor_filters(page) == [], bettor_filters(page)
    print("P1 OK: no bettor filter -> every single and every leg renders")

    click_bettor("Memo")
    assert bettor_filters(page) == ["memo"], bettor_filters(page)
    assert page.evaluate("document.querySelectorAll('#bettor-list .bettor-row.active-filter').length") == 1
    print("P2 OK: clicking a Bettor Tracker row sets the filter and marks that row active")

    assert set(single_states(page)) == {"Memo Single"}, single_states(page)
    print("P3 OK: ...singles narrow to just that bettor's")

    names = ticket_names(page)
    assert "Card All Kenny" not in " ".join(names), names
    assert set(leg_states(page)) == {"Mixed Memo Leg"}, leg_states(page)
    print("P4 OK: ...a parlay with none of his legs drops out, and a mixed one shows only his")

    assert "1 other leg(s) in this parlay hidden by the current filter." in page.inner_text("#content"), \
        "the mixed card should say a leg is hidden rather than silently shrinking"
    print("P5 OK: ...and the mixed card says so rather than silently shrinking")

    click_bettor("Memo")
    assert bettor_filters(page) == []
    assert single_states(page) == unfiltered_singles and leg_states(page) == unfiltered_legs
    print("P6 OK: tapping the same row again clears the filter and restores every leg")

    click_bettor("Kenny")
    assert bettor_filters(page) == ["kenny"]
    page.click("#tab-btn-yesterday")
    page.wait_for_timeout(50)
    assert bettor_filters(page) == [], "switching tabs must not carry a bettor filter across"
    assert set(single_states(page)) == {"Old Guy"}, single_states(page)
    print("P7 OK: switching tabs clears the filter instead of carrying it to the other slate")
    assert not errors, errors
    browser.close()

    # ========== Q: the live sync line is ET for everyone, not the visitor's clock ==========
    # updateSyncLine() formats with toLocaleTimeString and then appends " ET",
    # so it MUST pass timeZone explicitly -- otherwise it prints the visitor's
    # own wall clock under an ET label. Every other test runs in the machine's
    # own timezone, where that bug is invisible; this one pins the browser to
    # Pacific so the two readings can't coincide.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Live Guy")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(2001, "Live")])}
    FX["feeds"] = {2001: feed("Live", ["Live Guy"])}
    # 9:00:00 PM ET, which is 6:00:00 PM for the Pacific visitor below
    browser, page, errors = open_page(p, ET(2026, 9, 19, 21, 0), timezone_id="America/Los_Angeles")

    line = text(page, "sync-line")
    assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "America/Los_Angeles"
    assert line == "Last updated 9:00:00 PM ET", line   # "Live — " prefix dropped 2026-09-20
    assert "6:00:00" not in line, f"that's the visitor's local clock wearing an ET label: {line}"
    print("Q1 OK: a Pacific visitor sees 9:00:00 PM ET, not their own 6:00:00 PM")
    assert not errors, errors
    browser.close()

    # ========== R: the LEGS chip row, the always-on status line, the bench
    # grading fix, and the collapsible bet sections (all added 2026-09-20) ==========
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19",
                            [("Kenny", "Hit Guy"), ("Memo", "Bench Guy"), ("Memo", "Later Guy")],
                            [card(["Hit Guy", "Miss Guy"], "Card 1 &middot; Mixed")])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(3001, "Final"), (3002, "Preview")])}
    # 3001 is over: Hit Guy homered, Miss Guy batted and didn't, Bench Guy never got in.
    FX["feeds"] = {3001: feed("Final", ["Hit Guy", "Miss Guy"], hrs=["Hit Guy"], bench=["Bench Guy"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 23, 0), expand=False)

    # --- R1/R2: both bet lists start collapsed, each with its own count pill ---
    collapsed = page.evaluate("""() => ["parlays", "singles"].map(k =>
        document.getElementById(k + "-section").classList.contains("collapsed"))""")
    assert collapsed == [True, True], collapsed
    assert page.inner_text("#content").count("Tap to expand") == 2, page.inner_text("#content")
    print("R1 OK: Parlay Cards and Straight Bet Cards both default to collapsed")

    pills = page.evaluate("""() => ["parlays", "singles"].map(k =>
        document.querySelector("#" + k + "-section .head-count").textContent)""")
    # the card is dead (Miss Guy); singles are one hit, one void, one still to come
    assert pills == ["1 MISSED", "2 OPEN · 1 HIT"], pills
    print("R2 OK: each header carries its own OPEN / HIT / MISSED pill")

    page.click("#parlays-section .cards-head")
    assert page.evaluate("!document.getElementById('parlays-section').classList.contains('collapsed')")
    assert page.evaluate("localStorage.getItem('bmbs.cards.parlays')") == "1", "opening it must be remembered"
    assert page.evaluate("document.getElementById('singles-section').classList.contains('collapsed')"), \
        "the two sections open independently"
    page.click("#singles-section .cards-head")
    poll(page)   # a poll re-renders #content; the open state has to survive it
    assert page.evaluate("""() => ["parlays", "singles"].every(k =>
        !document.getElementById(k + "-section").classList.contains("collapsed"))"""), "a poll closed a section"
    print("R3 OK: each opens independently, is remembered, and survives a re-render")

    # --- R4: the bench player is VOID, not a miss (ported from record_results.py) ---
    st = single_states(page)
    assert st == {"Hit Guy": "hit", "Bench Guy": "na", "Later Guy": "not_started"}, st
    print("R4 OK: a player who sat on the bench all game is N/A, not a miss")

    # --- R5: the always-on status line ---
    ctx = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .single-row')]
        .map(r => [r.querySelector('.single-player').firstChild.textContent.trim(),
                   (r.querySelector('.leg-status') || {}).textContent || ""]))""")
    assert "never got in the game" in ctx["Bench Guy"], ctx
    assert "hasn't started" in ctx["Later Guy"], ctx
    assert ctx["Hit Guy"] == "", f"a hit needs no status line -- the check already says it: {ctx}"
    miss_status = page.evaluate("""() => [...document.querySelectorAll('#content .leg')]
        .find(l => l.querySelector('.leg-player').firstChild.textContent.trim() === 'Miss Guy')
        .querySelector('.leg-status').textContent""")
    assert "no home run" in miss_status, miss_status
    print("R5 OK: every pick carries a plain-language status line, whatever state it's in")

    # --- R6: the LEGS chips ---
    legs = tuple(text(page, f"count-leg-{k}") for k in ("live", "hit", "miss", "na"))
    assert legs == ("1", "2", "1", "1"), legs   # live: Later Guy; hit: Hit Guy twice; miss: Miss Guy; na: Bench Guy
    assert text(page, "bettor-count") == "2 BETTORS", text(page, "bettor-count")
    print("R6 OK: leg chips count every leg and single; the Bettor Tracker header counts people")

    page.click("#chip-leg-hit")
    assert leg_filters(page) == ["hit"]
    assert single_names(page) == ["Hit Guy"], single_names(page)
    assert legs_of(page, "Card 1 · Mixed") == ["Hit Guy"], legs_of(page, "Card 1 · Mixed")
    assert "1 other leg(s) in this parlay hidden by the current filter." in page.inner_text("#content"), \
        "the card must say a leg is hidden rather than silently shrinking"
    print("R7 OK: a leg-state filter hides non-matching LEGS inside a card, and says so")

    page.click("#chip-leg-hit")        # clear HIT first: chips no longer replace each other
    page.click("#chip-leg-na")
    assert single_names(page) == ["Bench Guy"], single_names(page)
    assert ticket_names(page) == [], "no leg of that card is void, so the whole card drops out"
    page.click("#chip-leg-na")
    assert leg_filters(page) == []
    assert len(single_names(page)) == 3 and len(legs_of(page, "Card 1 · Mixed")) == 2
    print("R8 OK: a card with no matching leg drops out entirely; tapping again clears it")

    page.click("#chip-leg-miss")
    page.click("#tab-btn-yesterday")
    page.wait_for_timeout(50)
    assert leg_filters(page) == [], "switching tabs must not carry a leg filter across"
    print("R9 OK: switching tabs clears the leg filter")
    assert not errors, errors
    browser.close()

    # ========== S: filters multi-select, and the summary bar lists them all ==========
    # Every filter used to be single-value: clicking a second chip silently
    # replaced the first. All three groups are Sets now -- OR within a group,
    # AND across groups -- so this section is about combinations, which no
    # earlier section could express.
    SEEN.clear()
    hit_card = card(["S Hit A", "S Hit B"], "Card HIT")          # both legs hit -> bet hit
    dead_card = card(["S Hit A", "S Miss"], "Card DEAD")         # one leg missed -> bet dead
    dead_card["legs"][1]["who"] = "Memo"
    dead_card["legs"][1]["meta"] = "XXX &middot; Memo"
    open_card = card(["S Hit A", "S Live"], "Card OPEN")         # one hit, one live -> open + iron
    FX["tickets"] = tickets("2026-09-19", [("Memo", "S Live")], cards=[hit_card, dead_card, open_card])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(4001, "Final"), (4002, "Live")])}
    FX["feeds"] = {4001: feed("Final", ["S Hit A", "S Hit B", "S Miss"], hrs=["S Hit A", "S Hit B"]),
                   4002: feed("Live", ["S Live"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 23, 0))

    assert summary_chips(page) == [], summary_chips(page)
    assert not page.evaluate("document.getElementById('filter-summary').classList.contains('active')"), \
        "the summary bar must stay hidden until something is actually filtered"
    print("S1 OK: no filters -> the summary bar is hidden and empty")

    # --- BETS multi-select: Hit + Missed, which no single-value filter could show ---
    page.click("#chip-hit")
    page.click("#chip-dead")
    assert bet_filters(page) == ["dead", "hit"], bet_filters(page)
    assert sorted(ticket_names(page)) == ["Card DEAD", "Card HIT"], ticket_names(page)
    assert page.evaluate("document.querySelectorAll('#slate-stats .score-chip.active-filter').length") == 2
    print("S2 OK: Bets Hit + Missed shows both groups; clicking one did not clear the other")

    assert summary_chips(page) == ["Bets: Hit", "Bets: Missed"], summary_chips(page)
    assert page.evaluate("document.getElementById('filter-summary').classList.contains('active')"), \
        "the bar holds chips but never became visible"
    print("S3 OK: ...and both appear as their own chips in a now-visible summary bar")

    # --- LEGS multi-select stacks ON TOP of the bets filter (AND across groups) ---
    page.click("#chip-leg-hit")
    page.click("#chip-leg-miss")
    assert leg_filters(page) == ["hit", "miss"], leg_filters(page)
    assert sorted(ticket_names(page)) == ["Card DEAD", "Card HIT"], ticket_names(page)
    assert legs_of(page, "Card DEAD") == ["S Hit A", "S Miss"], legs_of(page, "Card DEAD")
    print("S4 OK: Legs Hit + Missed keeps both legs of the mixed card, and stacks with the bets filter")

    # --- a third group on top, and the summary lists all of them ---
    page.evaluate("toggleBettorFilter('memo')")
    assert summary_chips(page) == ["Bets: Hit", "Bets: Missed", "Legs: Hit", "Legs: Missed", "Bettor: Memo"], summary_chips(page)
    assert ticket_names(page) == ["Card DEAD"], ticket_names(page)
    assert legs_of(page, "Card DEAD") == ["S Miss"], legs_of(page, "Card DEAD")
    print("S5 OK: all three groups stack, and the summary names every one of them")

    # --- removing one chip leaves the rest alone, INCLUDING the rest of its own
    # group. Bets holds two values here on purpose: with only one selected,
    # "delete this key" and "clear this whole group" look identical, which is
    # exactly the bug a scratch mutation slipped past an earlier version of this. ---
    page.evaluate("""() => [...document.querySelectorAll('#filter-summary-chips .fs-chip')]
        .find(c => c.dataset.group === 'bet' && c.dataset.key === 'hit').click()""")
    assert bet_filters(page) == ["dead"], bet_filters(page)
    assert leg_filters(page) == ["hit", "miss"] and bettor_filters(page) == ["memo"], \
        "removing one chip must not disturb the other groups"
    page.evaluate("""() => [...document.querySelectorAll('#filter-summary-chips .fs-chip')]
        .find(c => c.dataset.group === 'bettor').click()""")
    assert bettor_filters(page) == [] and bet_filters(page) == ["dead"], bet_filters(page)
    print("S6 OK: a summary chip removes exactly its own value, not its whole group")

    # --- Clear all ---
    page.click("#filter-clear-all")
    assert (bet_filters(page), leg_filters(page), bettor_filters(page)) == ([], [], [])
    assert summary_chips(page) == [] and len(ticket_names(page)) == 3
    assert page.evaluate("document.querySelectorAll('#slate-stats .score-chip.active-filter').length") == 0
    print("S7 OK: Clear all empties every group and un-highlights every chip")

    # --- IRONS still expands a card's legs, but only for cards that ARE irons ---
    # Card OPEN is an Iron (one leg hit, one still live); Card DEAD is not.
    # With Legs:Hit on, both cards have exactly one hit leg, so the contrast is
    # purely the Iron expansion: the Iron shows its live leg too, the dead card
    # hides its missed one. That generalisation is new -- when the bets chip
    # could only hold one value, every rendered card was an Iron.
    page.click("#chip-iron")
    page.click("#chip-dead")
    page.click("#chip-leg-hit")
    assert sorted(ticket_names(page)) == ["Card DEAD", "Card OPEN"], ticket_names(page)
    assert legs_of(page, "Card OPEN") == ["S Hit A", "S Live"], \
        "an Iron renders every leg, so the leg filter must not hide any of them"
    assert legs_of(page, "Card DEAD") == ["S Hit A"], \
        "Card DEAD is not an Iron, so the leg filter still hides its missed leg"
    print("S8 OK: IRONS expands only the cards that really are Irons; others obey the leg filter")

    # The one thing the expansion does NOT override: whether the card appears
    # at all. A card with no surviving leg is hidden even when it's an Iron --
    # the same rule the bettor filter has always had (see CLAUDE.md, "Irons").
    page.click("#chip-leg-hit")
    page.click("#chip-leg-miss")
    assert ticket_names(page) == ["Card DEAD"], ticket_names(page)
    print("S9 OK: an Iron with no leg matching the leg filter is still hidden entirely")

    # --- T: the header / colour-key reshuffle (football's section N) ---
    order = page.evaluate("""() => [...document.querySelectorAll('header > *')]
        .map(e => e.id || e.className || e.tagName.toLowerCase()).slice(0, 3)""")
    assert order == ["h1", "eyebrow", "sync-line"], order
    assert page.inner_text("h1") == "BMBS Tracker — Baseball", page.inner_text("h1")
    assert page.inner_text("#sync-line").startswith("Last updated"), page.inner_text("#sync-line")
    assert page.evaluate("() => !document.querySelector('header .legend')"), "the colour key must leave the header"
    assert page.evaluate("""() => { const l = document.querySelector('.legend'), f = document.getElementById('footer-line');
        return !!l && !!f && (l.compareDocumentPosition(f) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0; }"""), \
        "the colour key must sit above the footer, not after it"
    print("T1 OK: title -> LIVE FROM MLB -> last-updated, and the colour key sits above the footer")
    assert not errors, errors
    browser.close()

    # ========== U: Warmup is not "live", and a delay says so ==========
    # MLB's own /v1/gameStatus table reports WARMUP as abstractGameState
    # "Live" with codedGameState "P". Taking `abstract` at face value made the
    # page download the feed, read the posted lineup, and tag the leadoff
    # hitter "AT THE PLATE NOW" before a pitch had been thrown.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Warm Guy"), ("Memo", "Rain Guy")])
    FX["tickets"]["singles"][0]["team"] = "WRM"
    FX["tickets"]["singles"][1]["team"] = "RNY"
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [
        (5001, "Live", "Warmup", "P", ["WRM", "OPP"]),                       # lineups posted, no pitch thrown
        (5002, "Preview", "Delayed Start: Rain", "P", ["RNY", "OPP2"]),      # never started
    ])}
    # If the page asks for either feed at all that's already a bug; serve one
    # that WOULD produce an at-the-plate tag, so the test fails loudly if it does.
    FX["feeds"] = {5001: feed("Live", ["Warm Guy"]), 5002: feed("Live", ["Rain Guy"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 18, 45))

    assert count(r"/game/5001/feed") == 0, "a warmup game's feed must not be downloaded"
    assert count(r"/game/5002/feed") == 0, "a delayed-start game's feed must not be downloaded"
    print("U1 OK: neither a warmup nor a delayed-start game is treated as live")

    st = single_states(page)
    assert st == {"Warm Guy": "not_started", "Rain Guy": "not_started"}, st
    assert page.evaluate("() => document.querySelectorAll('#content .leg-action-tag').length") == 0, \
        "nobody is at the plate before first pitch"
    print("U2 OK: ...so both picks stay NOT STARTED with no 'AT THE PLATE NOW' tag")

    ctx = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .single-row')]
        .map(r => [r.querySelector('.single-player').firstChild.textContent.trim(),
                   (r.querySelector('.leg-status') || {}).textContent || ""]))""")
    assert "Warming up" in ctx["Warm Guy"], ctx
    assert ctx["Rain Guy"] == "Game delayed — rain.", ctx
    print("U3 OK: warmup and a rain delay each say so, by team, instead of 'hasn't started yet'")

    # The plain "Delayed Start" spelling puts the cause in a separate field.
    FX["schedules"]["2026-09-19"] = schedule("2026-09-19", [
        (5001, "Live", "Warmup", "P", ["WRM", "OPP"]),
        (5002, "Preview", "Delayed Start", "P", ["RNY", "OPP2"], "Wet Grounds"),
    ])
    poll(page)
    ctx = page.evaluate("""() => [...document.querySelectorAll('#content .single-row')]
        .filter(r => r.textContent.includes('Rain Guy')).map(r => r.querySelector('.leg-status').textContent)[0]""")
    assert ctx == "Game delayed — wet grounds.", ctx
    print("U4 OK: the separate `reason` field is used when the state doesn't carry it")
    assert not errors, errors
    browser.close()

    # The schedule is what normally stops a warmup game's feed being fetched,
    # so getGameSnapshot()'s own coded-state gate would otherwise never run.
    # It is not redundant: the two endpoints can disagree for a poll or two
    # around first pitch, and if the SCHEDULE flips to In Progress first, the
    # feed is downloaded and its (still Warmup) status is all that's left to
    # stop the posted lineup being read as live.
    SEEN.clear()
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "Warm Guy")])
    FX["tickets"]["singles"][0]["team"] = "WRM"
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(5003, "Live", "In Progress", "I", ["WRM", "OPP"])])}
    FX["feeds"] = {5003: feed("Live", ["Warm Guy"], coded="P")}   # feed still says pre-game
    browser, page, errors = open_page(p, ET(2026, 9, 19, 18, 45))
    assert count(r"/game/5003/feed") >= 1, "this case only means anything if the feed IS fetched"
    assert single_states(page) == {"Warm Guy": "not_started"}, single_states(page)
    assert page.evaluate("() => document.querySelectorAll('#content .leg-action-tag').length") == 0, \
        "the feed still says pre-game, so nobody is at the plate"
    print("U5 OK: a fetched feed that still says pre-game is not treated as live either")
    assert not errors, errors
    browser.close()

    # ========== V: the chip rows line up, Irons moved out, Expand/Collapse all ==========
    SEEN.clear()
    void_card = card(["V NA1", "V NA2"], "Card VOID")     # every leg N/A -> the bet is void
    hit_card = card(["V Hit"], "Card HIT")
    dead_card = card(["V Miss"], "Card DEAD")
    open_card = card(["V Live"], "Card OPEN")
    FX["tickets"] = tickets("2026-09-19", [("Kenny", "V NA1"), ("Memo", "V Live")],
                            cards=[void_card, hit_card, dead_card, open_card])
    FX["previous"] = None
    FX["schedules"] = {"2026-09-19": schedule("2026-09-19", [(6001, "Final"), (6002, "Live")])}
    FX["feeds"] = {6001: feed("Final", ["V Hit", "V Miss"], hrs=["V Hit"], bench=["V NA1", "V NA2"]),
                   6002: feed("Live", ["V Live"])}
    browser, page, errors = open_page(p, ET(2026, 9, 19, 23, 0))

    # --- the two rows are the same four states in the same order, so the
    # colours line up column for column. Same class == same colour. ---
    rows = page.evaluate("""() => ["chip-open,chip-hit,chip-dead,chip-void", "chip-leg-live,chip-leg-hit,chip-leg-miss,chip-leg-na"]
        .map(ids => ids.split(",").map(id => {
            const el = document.getElementById(id);
            return [...el.classList].find(c => ["pending", "hit", "miss", "na"].includes(c));
        }))""")
    assert rows == [["pending", "hit", "miss", "na"], ["pending", "hit", "miss", "na"]], rows
    print("V1 OK: BETS and LEGS are the same four states in the same order, sharing colours")

    assert page.evaluate("() => document.querySelectorAll('#slate-stats .bets-row')[0].children.length") == 4
    assert page.evaluate("() => !document.getElementById('chip-iron').classList.contains('score-chip')"), \
        "Irons is a subset of Open, not a fifth state -- it must not sit in the mutually-exclusive row"
    assert page.evaluate("() => document.getElementById('chip-iron').classList.contains('irons-bar')")
    # The waffle is the Iron's established marker, and it must be the SAME
    # glyph ironMark() puts on a leg. Written as a numeric entity in the
    # markup, which is easy to get wrong: &#129415; is 🦇, a bat.
    waffle = page.evaluate("""() => [...document.querySelector('#chip-iron .irons-lbl')
        .textContent.trim()][0].codePointAt(0).toString(16)""")
    leg_waffle = page.evaluate("""() => { const m = document.querySelector('.iron-mark');
        return m ? [...m.textContent.trim()][0].codePointAt(0).toString(16) : null; }""")
    assert waffle == "1f9c7", f"the Irons bar should carry a waffle, got U+{waffle.upper()}"
    assert leg_waffle == waffle, (waffle, leg_waffle)
    print("V2 OK: Irons moved out of the chip grid into its own bar, with the leg waffle")

    # --- a void BET is counted as N/A, not folded into Open ---
    bets = tuple(text(page, f"count-parlay-{k}") for k in ("open", "hit", "miss", "void"))
    assert bets == ("2", "1", "1", "2"), bets   # open: OPEN card + live single; void: VOID card + the DNP single
    assert text(page, "count-parlay-iron") == "2", text(page, "count-parlay-iron")
    print("V3 OK: an all-N/A card and a single on a player who didn't play both count as N/A bets")

    page.click("#chip-void")
    assert ticket_names(page) == ["Card VOID"], ticket_names(page)
    assert single_names(page) == ["V NA1"], single_names(page)
    assert summary_chips(page) == ["Bets: N/A"], summary_chips(page)
    page.click("#chip-void")
    print("V4 OK: filtering Bets N/A shows exactly the refunded bets")

    page.click("#chip-open")
    assert sorted(ticket_names(page)) == ["Card OPEN"], ticket_names(page)
    assert "Card VOID" not in ticket_names(page), "a refunded bet is not an open bet any more"
    page.click("#chip-open")
    print("V5 OK: ...and OPEN no longer sweeps them up")

    # --- Expand all / Collapse all drives every pill-collapsed panel ---
    def panel_states():
        return page.evaluate("""() => ({
            liveab: !document.getElementById("liveab-section").classList.contains("collapsed"),
            hrlog: !document.getElementById("hrlog-section").classList.contains("collapsed"),
            bettor: !document.getElementById("bettor-section").classList.contains("collapsed"),
            parlays: CARDS_OPEN.parlays, singles: CARDS_OPEN.singles })""")

    def collapse_all():
        page.evaluate("""() => [...document.querySelectorAll('#panel-controls .panel-link')]
            .find(b => b.textContent.includes('Collapse')).click()""")

    # open_page() opened the two card sections so the assertions above could
    # read them, so this starts MIXED -- which is the more interesting case:
    # Collapse all has to close the open ones and leave the closed ones alone.
    assert panel_states() == {"liveab": False, "hrlog": False, "bettor": False,
                              "parlays": True, "singles": True}, panel_states()
    collapse_all()
    assert not any(panel_states().values()), panel_states()
    print("V6 OK: Collapse all closes the open panels and leaves the closed ones alone")

    page.click("#panel-controls .panel-link")          # Expand all
    assert all(panel_states().values()), panel_states()
    print("V7 OK: Expand all opens every collapsible panel at once")
    collapse_all()

    # It must drive them through their OWN toggles, so the side effects happen:
    # the Live Bet Tracker's open/closed state is persisted, and the sub-text
    # of each panel is the collapsed wording.
    page.click("#panel-controls .panel-link")
    assert page.evaluate("localStorage.getItem('bmbs.liveab.open')") == "1", \
        "Expand all must go through toggleLiveAb(), which is what persists the preference"
    assert page.evaluate("localStorage.getItem('bmbs.cards.parlays')") == "1"
    print("V8 OK: ...and it does it through each panel's own toggle, so side effects still run")
    assert not errors, errors
    browser.close()

print("\nALL PAGE TESTS PASSED")
