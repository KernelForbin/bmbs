"""
Headless checks for the Live At Bats panel in index.html.

Same approach as test_page.py: one route handler, in-memory fixtures, a
pinned clock, nothing under data/ read or written, no real network. One
game is walked forward a poll at a time -- live count, strikeout, home run,
a stale at-bat, a tab switch -- and the clock is moved by hand so "several
seconds later" is exact rather than a sleep.

    python tests/test_live_at_bats.py
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

INDEX = Path(__file__).resolve().parent.parent / "index.html"
DATE = "2026-09-19"
NOW = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)   # 9:00 PM ET on the 19th

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------- fixtures ----------------

AWAY = ["Next Half", "Dead Parlay Guy", "Hit Already"] + [f"Away {n}" for n in range(4, 10)]
HOME = ["Lead Off", "Up Now", "On Deck", "In Hole"] + [f"Home {n}" for n in range(5, 10)]


def pitch(code, call, ptype="Slider", mph=88.0):
    return {"isPitch": True, "details": {"code": code, "call": {"description": call}, "description": call, "type": {"description": ptype}},
            "pitchData": {"startSpeed": mph}}


def play(idx, batter, top, pitches=(), balls=0, strikes=0, event=None, etype=None, out=False, end=None, hit=None, complete=None):
    events = list(pitches)
    if hit:
        events = events[:-1] + [dict(events[-1], hitData=hit)]
    p = {"atBatIndex": idx, "result": {"type": "atBat"},
         "about": {"isTopInning": top, "inning": 6, "halfInning": "top" if top else "bottom",
                   "isComplete": bool(event) if complete is None else complete},
         "count": {"balls": balls, "strikes": strikes},
         "matchup": {"batter": {"fullName": batter}, "batSide": {"code": "R"}, "pitcher": {"fullName": "Brayan Bello"}, "pitchHand": {"code": "R"}},
         "playEvents": events}
    if event:
        p["result"].update({"event": event, "eventType": etype, "isOut": out})
        p["about"]["endTime"] = iso(end or NOW)
    return p


def game(plays, state, outs, batter, inning=6):
    def side(names):
        return {f"ID{n}": {"person": {"fullName": n}, "battingOrder": f"{i + 1}00"} for i, n in enumerate(names)}
    return {"gameData": {"status": {"abstractGameState": "Live"}, "venue": {"name": "Yankee Stadium"}, "weather": {},
                         "teams": {"away": {"abbreviation": "BOS"}, "home": {"abbreviation": "NYY"}}},
            "liveData": {"plays": {"allPlays": plays},
                         "boxscore": {"teams": {"away": {"players": side(AWAY)}, "home": {"players": side(HOME)}}},
                         "linescore": {"currentInning": inning, "inningState": state, "outs": outs, "offense": {"batter": {"fullName": batter}}}}}


FINAL_NO_HR = {"gameData": {"status": {"abstractGameState": "Final"}},
               "liveData": {"plays": {"allPlays": []},
                            "boxscore": {"teams": {"away": {"players": {"ID1": {"person": {"fullName": "Missed Guy"}, "battingOrder": "100"}}}, "home": {"players": {}}}},
                            "linescore": {}}}


def leg(player, who, odds):
    return {"id": f"leg-{player}", "player": player, "team": "", "who": who, "meta": who, "odds": odds, "time": "7:05 PM ET"}


def card(n, legs):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5</b> bet by Memo", "stake": 5.0, "book": "Memo", "payout": 100.0, "legs": legs}


TICKETS = {"date": DATE, "note": "", "windows": [{"title": "2-Leg Parlay Cards", "tickets": [
    card(1, [leg("On Deck", "Joe", "+410"), leg("In Hole", "Miggs", "+520")]),
    card(2, [leg("Next Half", "Bernie", "+240"), leg("Hit Already", "Memo", "+500")]),        # Next Half is the Iron leg
    card(3, [leg("Dead Parlay Guy", "Bailey", "+370"), leg("Missed Guy", "Noid", "+290")]),   # dead: Missed Guy's game is Final
    card(4, [leg("Up Now", "Kevin", "+265"), leg("Home 9", "Didge", "+900")]),
]}], "singles": [{"id": "single-0", "who": "KENNY", "player": "Up Now", "team": "", "meta": "$5.00 bet", "odds": "+300",
                  "stake": 5.0, "payout": 20.0, "pp": "PP $20.00"}]}

OLD = NOW - timedelta(hours=1)
BASE = [play(5, "Hit Already", True, [pitch("X", "In play, run(s)")], event="Home Run", etype="home_run", end=OLD,
             hit={"launchSpeed": 104.0, "launchAngle": 30.0, "totalDistance": 401.0, "trajectory": "fly_ball"}),
        play(40, "Away 9", True, event="Flyout", etype="field_out", out=True, end=OLD),
        play(41, "Lead Off", False, [pitch("X", "In play, no out")], event="Single", etype="single", end=OLD)]
COUNT_1_2 = [pitch("C", "Called Strike", "Sinker", 95.1), pitch("B", "Ball", "Slider", 86.4), pitch("F", "Foul", "Four-Seam Fastball", 97.2)]

FX = {"feed": None, "clock": NOW}


def handler(route, request):
    url = request.url
    if url.endswith("/index.html"):
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=INDEX.read_bytes())
    if "/data/tickets-previous.json" in url:
        return route.fulfill(status=404, body="")
    if "/data/tickets.json" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(TICKETS))
    if "/api/v1/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"dates": [{"date": DATE, "games": [{"gamePk": 1301, "status": {"abstractGameState": "Live"}},
                                                 {"gamePk": 1302, "status": {"abstractGameState": "Final"}}]}]}))
    m = re.search(r"/game/(\d+)/feed/live", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["feed"] if m.group(1) == "1301" else FINAL_NO_HR))
    return route.abort()


def boot(page):
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")


def poll(page):
    page.evaluate("pollAndRender()")


def advance(page, seconds):
    """Move the pinned clock and let the panel notice, as its own timer would."""
    FX["clock"] += timedelta(seconds=seconds)
    page.clock.set_fixed_time(FX["clock"])
    page.evaluate("renderLiveAtBats()")


def tiles(page):
    return page.evaluate("""() => [...document.querySelectorAll('#liveab-grid .ab-tile')].map(t => ({
        player: t.dataset.player,
        kind: ['now', 'soon', 'result'].find(k => t.classList.contains(k)),
        homer: t.classList.contains('homer'),
        tag: t.querySelector('.ab-tag').textContent,
        odds: t.querySelector('.ab-odds').textContent.replace(/[^+\\d/ to]/g, '').trim(),
        iron: !!t.querySelector('.iron-mark'),
        count: t.querySelector('.ab-count') ? t.querySelector('.ab-count').textContent : null,
        pitches: [...t.querySelectorAll('.ab-pitch')].map(x => x.textContent).join(''),
        result: t.querySelector('.ab-result') ? t.querySelector('.ab-result').textContent : null,
        bomb: !!t.querySelector('.ab-bomb'),
        text: t.textContent.replace(/\\s+/g, ' ').trim(),
    }))""")


def shown(page, el_id):
    return page.evaluate(f"document.getElementById('{el_id}').getClientRects().length > 0")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)

    # ---------- A. the old chips are gone, the panels are reordered ----------
    FX["feed"] = game(BASE + [play(42, "Up Now", False, COUNT_1_2, balls=1, strikes=2)], "Bottom", 0, "Up Now")
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    check("A1 BATTING NOW / BATTING SOON chips removed",
          page.evaluate("!document.getElementById('action-chip-now') && !document.getElementById('action-chip-soon') && !/BATTING (NOW|SOON)/.test(document.body.textContent)"))
    check("A2 no leftover filter plumbing", page.evaluate("typeof toggleActionFilter === 'undefined' && typeof ACTION_FILTER === 'undefined'"))
    order = page.evaluate("['liveab-section', 'hrlog-section', 'bettor-section', 'content'].map(id => document.getElementById(id).getBoundingClientRect().top)")
    check("A3 order: Live At Bats, Home Run Log, Bettor Tracker, then the tickets", order == sorted(order) and len(set(order)) == 4, str(order))
    check("A4 the per-leg AT THE PLATE tag on the ticket itself is untouched",
          page.evaluate("[...document.querySelectorAll('#content .leg-action-tag.now')].length") >= 1)

    # ---------- B. collapsed by default, with a live hint ----------
    check("B1 collapsed by default", page.evaluate("document.getElementById('liveab-section').classList.contains('collapsed')") and not shown(page, "liveab-grid"))
    sub = page.inner_text("#liveab-sub")
    check("B2 collapsed hint says to expand and what's waiting", "Tap to expand" in sub and "1 at the plate" in sub and "3 due up" in sub, sub)
    check("B3 count pill", page.inner_text("#liveab-count") == "1 UP · 3 DUE", page.inner_text("#liveab-count"))
    page.click(".liveab-head")
    check("B4 expands on tap", shown(page, "liveab-grid"))

    # ---------- C. the tiles ----------
    t = tiles(page)
    check("C1 at-bat first, then on deck, in the hole, then the side due up next half",
          [(x["player"], x["kind"], x["tag"]) for x in t] == [("Up Now", "now", "AT BAT"), ("On Deck", "soon", "ON DECK"),
                                                             ("In Hole", "soon", "IN THE HOLE"), ("Next Half", "soon", "LEADS OFF NEXT")], str([(x["player"], x["tag"]) for x in t]))
    up = t[0]
    check("C2 live count and each pitch so far", up["count"] == "1-2" and up["pitches"] == "CBF", str(up))
    check("C3 pitcher, last pitch (shortened) and whose pick it is", "vs Bello" in up["text"] and "Foul · 4-Seam 97" in up["text"] and "Kevin" in up["text"] and "Kenny" in up["text"], up["text"])
    check("C4 one tile per player, every distinct price shown", up["odds"] == "+265/+300", up["odds"])
    check("C5 waffle on Irons only: an open single, and the last leg of a parlay",
          {x["player"]: x["iron"] for x in t} == {"Up Now": True, "On Deck": False, "In Hole": False, "Next Half": True}, str({x["player"]: x["iron"] for x in t}))
    check("C6 a dead parlay's hitter gets no tile, even though he's due up", "Dead Parlay Guy" not in [x["player"] for x in t])
    check("C7 at-bat tile looks different from the due-up tiles",
          page.evaluate("getComputedStyle(document.querySelector('.ab-tile.now')).borderColor !== getComputedStyle(document.querySelector('.ab-tile.soon:not(.ondeck)')).borderColor"))
    check("C8 tiles link to that game's Gameday", page.get_attribute(".ab-tile.now", "href") == "https://www.mlb.com/gameday/1301")
    check("C9 seeding: at-bats that finished before the page loaded aren't announced", not any(x["kind"] == "result" for x in t))

    # The real feed writes mid-at-bat actions into result.event while the hitter
    # is still up -- caught against live games, where a "Batter Timeout" was
    # announced as an at-bat's outcome. Only about.isComplete ends an at-bat.
    FX["feed"] = game(BASE + [play(42, "Up Now", False, COUNT_1_2, balls=1, strikes=2, event="Batter Timeout", etype="batter_timeout", complete=False)],
                      "Bottom", 0, "Up Now")
    poll(page)
    t = tiles(page)
    check("C10 a mid-at-bat timeout isn't a result: he's still up, count intact",
          (t[0]["player"], t[0]["kind"], t[0]["count"]) == ("Up Now", "now", "1-2") and not any(x["kind"] == "result" for x in t), str(t[0]))
    # ...and a "play" that ends on a runner event isn't the hitter's outcome either.
    FX["feed"] = game(BASE + [play(90, "Up Now", False, COUNT_1_2, 1, 2, "Caught Stealing 2B", "caught_stealing_2b", True),
                              play(91, "Up Now", False, [], 0, 0)], "Bottom", 1, "Up Now")
    poll(page)
    t = tiles(page)
    check("C11 inning-ending caught stealing: no result tile for the man at the plate",
          not any(x["kind"] == "result" for x in t) and t[0]["player"] == "Up Now" and t[0]["kind"] == "now", str(t[0]))

    # ---------- D. the at-bat ends: result holds its place, then drops ----------
    FX["feed"] = game(BASE + [play(42, "Up Now", False, COUNT_1_2 + [pitch("S", "Swinging Strike", "Sweeper", 84.0)], 1, 3, "Strikeout", "strikeout", True),
                              play(43, "On Deck", False)], "Bottom", 1, "On Deck")
    poll(page)
    t = tiles(page)
    check("D1 result tile holds the front spot", t[0]["player"] == "Up Now" and t[0]["kind"] == "result" and t[0]["result"] == "Strikeout", str(t[0]))
    check("D2 result detail: pitches seen and the pitcher", "4 pitches" in t[0]["text"] and "vs Bello" in t[0]["text"], t[0]["text"])
    check("D3 the next hitter is already up behind it, stepping in at 0-0",
          (t[1]["player"], t[1]["kind"], t[1]["count"]) == ("On Deck", "now", "0-0") and t[2]["tag"] == "ON DECK" and t[2]["player"] == "In Hole", str(t[1:3]))
    advance(page, 8)
    check("D4 still showing after 8s", tiles(page)[0]["kind"] == "result")
    advance(page, 2)
    t = tiles(page)
    check("D5 dropped after ~9s; everyone moves up", [x["player"] for x in t] == ["On Deck", "In Hole", "Next Half"], str([x["player"] for x in t]))

    # ---------- E. a home run ----------
    FX["feed"] = game(BASE + [play(42, "Up Now", False, COUNT_1_2, 1, 3, "Strikeout", "strikeout", True, end=FX["clock"] - timedelta(seconds=30)),
                              play(43, "On Deck", False, [pitch("B", "Ball"), pitch("X", "In play, run(s)", "Four-Seam Fastball", 98.4)], 1, 0, "Home Run", "home_run", False, end=FX["clock"],
                                   hit={"launchSpeed": 113.4, "launchAngle": 27.0, "totalDistance": 438.0, "trajectory": "fly_ball"}),
                              play(44, "In Hole", False, [pitch("B", "Ball")], 1, 0)], "Bottom", 1, "In Hole")
    poll(page)
    t = tiles(page)
    check("E1 the tile turns into a bomb", t[0]["player"] == "On Deck" and t[0]["homer"] and t[0]["bomb"], str(t[0]))
    check("E2 his parlay-mate is now one swing from cashing: waffle appears on In Hole", t[1]["player"] == "In Hole" and t[1]["iron"], str(t[1]))
    advance(page, 4)
    t = tiles(page)
    check("E3 bomb gives way to the result", not t[0]["bomb"] and t[0]["result"] == "Home Run" and "438 ft" in t[0]["text"] and "off Bello" in t[0]["text"], t[0]["text"])
    advance(page, 11)
    check("E4 home run tile lingers longer than an out, then drops", "On Deck" not in [x["player"] for x in tiles(page)], str([x["player"] for x in tiles(page)]))
    check("E5 it's a hit on the ticket itself", page.evaluate(
        "[...document.querySelectorAll('#content .leg')].find(l => l.textContent.includes('On Deck')).classList.contains('state-hit')"))

    # ---------- F. old news isn't news ----------
    plays_f = BASE + [play(44, "In Hole", False, [pitch("X", "In play, out(s)")], 0, 0, "Groundout", "field_out", True, end=FX["clock"] - timedelta(minutes=10)),
                      play(45, "Home 5", False)]
    FX["feed"] = game(plays_f, "Bottom", 2, "Home 5")
    poll(page)
    check("F1 an at-bat that ended 10 minutes ago (tab was asleep) isn't announced", not any(x["kind"] == "result" for x in tiles(page)), str(tiles(page)))

    # ---------- G. tabs ----------
    page.click("#tab-btn-yesterday")
    check("G1 no Live At Bats on Yesterday's Slate", not shown(page, "liveab-section"))
    FX["feed"] = game(plays_f + [play(46, "Next Half", True, [pitch("S", "Swinging Strike")], 0, 3, "Strikeout", "strikeout", True, end=FX["clock"]),
                                 play(47, "Dead Parlay Guy", True)], "Top", 1, "Dead Parlay Guy", inning=7)
    poll(page)                       # finishes while we're looking at Yesterday
    page.click("#tab-btn-today")
    poll(page)
    check("G2 back on Today: panel returns, and what finished meanwhile isn't replayed",
          shown(page, "liveab-section") and not any(x["kind"] == "result" for x in tiles(page)), str(tiles(page)))
    check("G3 nobody due up -> a plain empty note, zero count pill",
          "None of your picks" in page.inner_text("#liveab-grid") and page.inner_text("#liveab-count") == "", page.inner_text("#liveab-grid"))

    # ---------- H. bettor filter + persistence ----------
    FX["feed"] = game(BASE + [play(42, "Up Now", False, COUNT_1_2, balls=1, strikes=2)], "Bottom", 0, "Up Now")
    page.reload()
    boot(page)
    poll(page)
    check("H1 expanded state is remembered across a reload", not page.evaluate("document.getElementById('liveab-section').classList.contains('collapsed')"))
    page.evaluate("toggleBettorFilter('joe')")
    check("H2 the bettor filter narrows the tiles too", [x["player"] for x in tiles(page)] == ["On Deck"], str([x["player"] for x in tiles(page)]))
    page.evaluate("toggleBettorFilter(null)")
    check("H3 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    check("H4 no script errors", not errors, str(errors))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all live-at-bats checks passed")
