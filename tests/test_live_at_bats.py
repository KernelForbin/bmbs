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

INDEX = Path(__file__).resolve().parent.parent / "mlb" / "index.html"
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
            {"dates": [{"date": DATE, "games": [{"gamePk": 1301, "status": {"abstractGameState": FX.get("state", "Live")}},
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
        kind: ['now', 'soon', 'result', 'onbase', 'mound', 'game'].find(k => t.classList.contains(k)),
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

    # ---------- D6-D9. the real MLB API quirk: linescore lags play-by-play ----------
    # Confirmed against live games on 2026-09-18: liveData.linescore.offense.batter
    # keeps naming whoever's plate appearance just ENDED for the whole gap until the
    # next one starts -- not a one-poll blip, stale for 45+ seconds in some cases.
    # Every other scenario in this file has game()'s `batter` already advanced to
    # the truth the instant an at-bat ends, which is unrealistic and was hiding
    # this bug. This one deliberately leaves it stale, as the real feed does.
    FX["feed"] = game(BASE + [play(200, "Up Now", False, [pitch("C", "Called Strike")], 0, 1)], "Bottom", 0, "Up Now")
    poll(page)
    check("D6 Up Now genuinely at the plate", tiles(page)[0]["player"] == "Up Now" and tiles(page)[0]["count"] == "0-1")

    # He grounds out. The play is complete, but no new play exists yet for the next
    # hitter, and offense.batter is passed as "Up Now" -- stale, exactly as MLB's
    # own feed reports it during this gap.
    FX["feed"] = game(BASE + [play(200, "Up Now", False, [pitch("C", "Called Strike"), pitch("X", "In play, out(s)")], 0, 1, "Groundout", "field_out", True)],
                      "Bottom", 1, "Up Now")
    poll(page)
    check("D7 result tile shows the groundout", tiles(page)[0]["player"] == "Up Now" and tiles(page)[0]["result"] == "Groundout")

    # Advance past the result hold, with the feed still stuck in that same gap
    # (unchanged -- MLB hasn't posted the next plate appearance yet either).
    advance(page, 9)
    t = tiles(page)
    check("D8 THE BUG: Up Now must not reappear as a live at-bat once his result drops",
          not any(x["player"] == "Up Now" and x["kind"] == "now" for x in t), str(t))
    check("D9 the actual next hitter (On Deck, the next lineup slot) takes his place instead",
          t[0]["player"] == "On Deck" and t[0]["kind"] == "now", str(t[0] if t else None))

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

    # ---------- I. the scoreboard filters scope the tiles, same rules as the tickets ----------
    names = lambda: [x["player"] for x in tiles(page)]
    everyone = ["Up Now", "On Deck", "In Hole", "Next Half"]
    page.click("#chip-iron")
    t = tiles(page)
    check("I1 IRONS: only hitters one swing from cashing something", names() == ["Up Now", "Next Half"], str(names()))
    check("I2 ...and a tile lists only the prices of his Iron bets (the single, not his 2-leg card)",
          t[0]["odds"] == "+300" and all(x["iron"] for x in t), str([(x["player"], x["odds"], x["iron"]) for x in t]))
    check("I3 the header says it's filtered, and counts what's left",
          "Irons" in page.inner_text("#liveab-sub") and page.inner_text("#liveab-count") == "1 UP \u00b7 1 DUE", page.inner_text("#liveab-sub"))
    check("I4 the ticket list below agrees with the tiles",
          page.evaluate("[...document.querySelectorAll('#content .iron-mark')].length") >= 2)
    page.evaluate("toggleBettorFilter('bernie')")
    check("I5 filters stack: Irons + Bernie", names() == ["Next Half"], str(names()))
    page.evaluate("toggleBettorFilter(null)")
    page.click("#chip-iron")
    check("I6 clearing the filter brings everyone back", names() == everyone, str(names()))
    page.click("#chip-open")
    check("I7 OPEN: every tile is on an open bet already, so nothing drops", names() == everyone, str(names()))
    page.click("#chip-open")
    page.click("#chip-hit")
    check("I8 HIT bets: nobody on a cashed bet is still batting for it -> empty, and it says why",
          names() == [] and "Bets: Hit" in page.inner_text("#liveab-grid") and "Clear the filter" in page.inner_text("#liveab-grid"), page.inner_text("#liveab-grid"))
    page.click("#chip-hit")
    check("I9 all filters off again", names() == everyone and "Filtered" not in page.inner_text("#liveab-sub"), page.inner_text("#liveab-sub"))

    # ---------- J. the break between innings: "End" means the AWAY team is up ----------
    # inningState has four values and only "Top" puts the away side up MID-half,
    # but "End" (the bottom half just finished) means away leads off the next
    # inning. currentlyBattingSide used to treat anything that wasn't "Top" as
    # home, so for the whole break it flagged a HOME hitter as being at the plate
    # while the away team was the side actually due up.
    #
    # Measured against live MLB games on 2026-09-19 before writing this: "End" is
    # real and reachable, `offense` is populated in 10 of 10 windows sampled (so
    # the branch is always taken, never short-circuited to null), and it lasts a
    # median 71s -- about 7 consecutive polls, not a flicker between them.
    #
    # Last away batter is slot 8, so away's next up is slot 9 and "Next Half"
    # (slot 1) is 1 away. Last home batter is "Up Now" (slot 2), so home's next
    # up is slot 3 -- "On Deck", who is exactly who used to be mislabelled.
    FX["feed"] = game(BASE + [play(42, "Away 8", True, event="Flyout", etype="field_out", out=True, end=OLD),
                              play(43, "Up Now", False, event="Groundout", etype="field_out", out=True, end=OLD)],
                      "End", 3, "Up Now")
    poll(page)

    def tag_for(player):
        """The leg's action tag, emoji stripped -- a failing check has to be
        printable on a cp1252 console, which a raw 🔥/⏳ in the detail is not."""
        raw = page.evaluate("""(p) => {
            const leg = [...document.querySelectorAll('#content .leg, #content .single-row')]
                .find(r => r.querySelector('.leg-player, .single-player').firstChild.textContent.trim() === p);
            const t = leg && leg.querySelector('.leg-action-tag');
            return t ? t.textContent.trim() : null;
        }""", player)
        return None if raw is None else raw.encode("ascii", "ignore").decode("ascii").strip()

    check("J1 a HOME hitter due up is NOT 'at the plate' during the break after the bottom half",
          tag_for("On Deck") != "AT THE PLATE NOW", tag_for("On Deck"))
    check("J2 nobody on the home side is flagged at the plate at all",
          page.evaluate("""[...document.querySelectorAll('#content .leg-action-tag.now')]
              .map(t => t.closest('.leg, .single-row'))
              .map(row => row.querySelector('.leg-player, .single-player').firstChild.textContent.trim())""") == [],
          str(page.evaluate("""[...document.querySelectorAll('#content .leg-action-tag.now')]
              .map(t => t.closest('.leg, .single-row'))
              .map(row => row.querySelector('.leg-player, .single-player').firstChild.textContent.trim())""")))
    # The other half of the fix: `outs` still reads 3 from the half that just
    # ended, so charging it against the side about to bat wiped out their
    # guaranteed-at-bat tags. Next Half is 1 away with a full 3 outs coming.
    check("J3 the AWAY side about to lead off is judged on a fresh 3 outs, not the stale 3",
          tag_for("Next Half") == "GUARANTEED TO BAT THIS INNING", tag_for("Next Half"))
    check("J4 no ghost 'Stepping in' tile for the wrong team",
          "On Deck" not in page.inner_text("#liveab-grid") or "AT BAT" not in page.inner_text("#liveab-grid"),
          page.inner_text("#liveab-grid"))
    check("J5 no script errors", not errors, str(errors))

    # A second, separate ghost-tag bug found while testing this one:
    # isCurrentlyBatting fires on d===0 with no betweenHalves check, so the
    # LITERAL next batter (not just someone a few slots out, like Next Half
    # above) is still wrongly "AT THE PLATE NOW" during the break --
    # guaranteedThisHalfInning should carry him instead. Reshapes the fixture
    # so away's last batter is slot 9 instead of slot 8 (home's stays "Up
    # Now", unchanged from above): that makes Next Half (slot 1) the literal
    # next-up batter (0 away, not J3's 1 away) instead of J3's already-tested
    # case. Run last, after J4/J5, since it changes what J4 assumed.
    FX["feed"] = game(BASE + [play(45, "Away 9", True, event="Flyout", etype="field_out", out=True, end=OLD),
                              play(43, "Up Now", False, event="Groundout", etype="field_out", out=True, end=OLD)],
                      "End", 3, "Away 9")
    poll(page)
    check("J6 nobody is 'at the plate' during a break, not even the literal next batter",
          tag_for("Next Half") == "GUARANTEED TO BAT THIS INNING", tag_for("Next Half"))
    check("J7 no script errors", not errors, str(errors))
    browser.close()

    # ---------- K. the OTHER break: "Middle" means the HOME team is up ----------
    # currentlyBattingSide's mapping was already correct for "Middle" (top just
    # ended, home up next) -- only two things needed the same fix "End" got:
    # the stale 3 outs, and the ghost "AT THE PLATE NOW" tag for the literal
    # next batter. Deliberately NOT done alongside "End" the first time (it
    # broke test_steals.py's H3, which was itself asserting the bug -- see
    # that file's H3 comment) so it's done properly here instead of skipped.
    #
    # Last away batter is slot 8 (Away 8), so away's next up is slot 9 -- not
    # checked here, this section is about the HOME side coming up. Home's last
    # recorded batter is "Lead Off" (slot 1, from BASE), so home's next up is
    # slot 2 -- "Up Now" (0 away) -- and slot 3 "On Deck" is 1 away.
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)
    FX["feed"] = game(BASE + [play(44, "Away 8", True, event="Flyout", etype="field_out", out=True, end=OLD)],
                      "Middle", 3, "Away 8")
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    check("K1 the literal next HOME batter is NOT 'at the plate' during the break after the top half",
          tag_for("Up Now") == "GUARANTEED TO BAT THIS INNING", tag_for("Up Now"))
    check("K2 nobody is flagged at the plate at all during the break",
          page.evaluate("""[...document.querySelectorAll('#content .leg-action-tag.now')]
              .map(t => t.closest('.leg, .single-row'))
              .map(row => row.querySelector('.leg-player, .single-player').firstChild.textContent.trim())""") == [],
          str(page.evaluate("""[...document.querySelectorAll('#content .leg-action-tag.now')]
              .map(t => t.closest('.leg, .single-row'))
              .map(row => row.querySelector('.leg-player, .single-player').firstChild.textContent.trim())""")))
    check("K3 the HOME side about to bat is judged on a fresh 3 outs, not the stale 3",
          tag_for("On Deck") == "GUARANTEED TO BAT THIS INNING", tag_for("On Deck"))
    check("K4 no ghost 'Stepping in' tile for the wrong team",
          "Up Now" not in page.inner_text("#liveab-grid") or "AT BAT" not in page.inner_text("#liveab-grid"),
          page.inner_text("#liveab-grid"))
    check("K5 no script errors", not errors, str(errors))

    check("H3 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    check("H4 no script errors", not errors, str(errors))
    browser.close()

    # A previous section closed its browser, so section Z opens its own.
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)

    # ================= Z: the other markets earn tiles too =================
    # The Live Bet Tracker was home runs and steals only. Everything the page
    # can actually FOLLOW live now gets a tile: a batting prop uses the same
    # batting-order machinery and shows progress toward its line, a pitcher
    # prop gets the count that climbs while his side is in the field, and a
    # game line gets the score plus what it still needs. An inning-specific
    # total gets nothing ON PURPOSE -- it can't be followed from a final
    # score, so a tile could only ever show a number that means nothing.
    def prop_leg(player, market, line, who="Kenny", odds=None, team="", players=None):
        lg = {"id": f"p-{player}-{market}", "player": player, "team": team, "who": who,
              "meta": who, "odds": odds, "time": "7:05 PM ET", "market": market}
        if line is not None:
            lg["line"] = line
        if players:
            lg["players"] = players
        return lg

    FX["feed"] = game(BASE + [play(50, "Up Now", False, COUNT_1_2, balls=1, strikes=2)],
                      "Bottom", 1, "Up Now")
    _box = FX["feed"]["liveData"]["boxscore"]["teams"]
    _home = _box["home"]["players"]
    _upnow_id = next(k for k, v in _home.items() if v["person"]["fullName"] == "Up Now")
    _home[_upnow_id]["stats"] = {"batting": {"plateAppearances": 3, "hits": 1, "runs": 0,
                                             "rbi": 0, "totalBases": 2, "doubles": 1, "homeRuns": 0}}
    _home["IDP"] = {"person": {"fullName": "Ace Arm"},
                    "stats": {"pitching": {"strikeOuts": 4}}}
    FX["feed"]["liveData"]["linescore"]["teams"] = {"away": {"runs": 2}, "home": {"runs": 5}}

    TICKETS["windows"][0]["tickets"] = [
        card(1, [prop_leg("Up Now", "hits", 1.5), prop_leg("On Deck", "tb", 2.5)]),
        card(2, [prop_leg("Ace Arm", "k", 5.5), prop_leg("", "spread", -1.5, team="NYY")]),
        card(3, [prop_leg("Inning Bet", "partial game", 1.5), prop_leg("In Hole", "hits", 0.5)]),
    ]
    TICKETS["singles"] = []
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    tl = {t["player"]: t for t in tiles(page)}

    up = tl.get("Up Now")
    check("Z1 a batting prop gets the ordinary AT BAT tile", bool(up) and up["kind"] == "now", sorted(tl))
    check("Z2 ...and shows progress toward its line", bool(up) and "1 of 2 hits" in up["text"], up and up["text"])
    # Named by the progress line under his name, not a label in the header --
    # that one repeated it and squeezed the status ("LEAD...") out of view.
    check("Z3 the market is named, not left looking like a home run bet",
          bool(up) and "1 of 2 hits" in up["text"] and "HR" not in up["odds"], up and up["text"])

    arm = tl.get("Ace Arm")
    check("Z4 a pitcher prop gets its own ON THE MOUND tile -- the batting "
          "order would never have surfaced him at all", bool(arm) and arm["kind"] == "mound", sorted(tl))
    check("Z5 ...showing the strikeout count and what it needs",
          bool(arm) and "needs 6" in arm["text"], arm and arm["text"])
    # The line was built with "&middot;" and THEN escaped, so the tile read
    # "6 K &middot; needs 9" live (Gavin Williams, 2026-10-05). Z5 passed
    # right through it: "needs 6" is in the broken text too.
    raw = [(t["player"], t["text"]) for t in tl.values() if re.search(r"&(?:[a-z]+|#\d+);", t["text"])]
    check("Z5b no tile shows a raw HTML entity as text", not raw, raw)

    gl = tl.get("NYY")
    check("Z6 a game line gets a tile even though it has no player at all",
          bool(gl) and gl["kind"] == "game", sorted(tl))
    check("Z7 ...with the live score and what it still needs",
          bool(gl) and "5" in (gl["count"] or "") and "cover" in gl["text"], gl and gl["text"])
    check("Z7b a run line wears the dotted 'covering' outline exactly when it would cash right now",
          bool(gl) and page.evaluate("document.querySelector('#liveab-grid .ab-tile.game').classList.contains('covering')")
          == ("covering by" in gl["text"]), gl and gl["text"])
    check("Z8 ...and says plainly that nothing settles until the final",
          bool(gl) and "Settles at the final" in gl["text"], gl and gl["text"])
    # Both read a `gamedayLink` off the MLB snapshot, which never carries one,
    # so these were the only tiles on the wall you couldn't tap through.
    links = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#liveab-grid .ab-tile.game, #liveab-grid .ab-tile.mound')]
        .map(t => [t.dataset.player, t.getAttribute('href') || '']))""")
    check("Z8b the game-line and pitcher tiles open Gameday like every other tile",
          len(links) >= 2 and all("mlb.com/gameday/" in h for h in links.values()), links)
    # A first-5-innings total was drawn as a full-game line: the whole score,
    # no target, "Settles at the final" -- it settles after the 5th.
    f5 = page.evaluate("""() => labGameTile({ player: 'NYY', whos: [], entries: [{ market: 'f5', line: 4.5, side: 'over' }] },
        { runs: 6, oppRuns: 1, byInning: [1, 0, 2, 0, 0, 3] }, 'Top 7', '')""")
    check("Z8c a first-5-innings total counts only those innings, and says when it settles",
          "3 runs thru 5, needs 5" in f5 and "Settles after the 5th inning." in f5, f5)

    # Checked on the CONTRACT, not just the absence of a tile: an untrackable
    # market can fail to produce one for several reasons, and only this says
    # the market is genuinely excluded. (Asserting absence alone passed even
    # with the exclusion removed -- a mutation proved it.)
    check("Z9 an inning-specific total is excluded from tiles outright: it "
          "can't be followed from a final score, so a number would mean nothing",
          page.evaluate("liveTileKind({market: 'partial game'})") is None
          and page.evaluate("liveTileKind({market: 'strikeouts'})") is None,
          page.evaluate("[liveTileKind({market:'partial game'}), liveTileKind({market:'hits'})]"))
    check("Z9b ...and the known markets each map to the right KIND of tile",
          page.evaluate("['hr','sb','hits','k','spread'].map(m => liveTileKind({market: m}))")
          == ["bat", "base", "bat", "mound", "game"],
          page.evaluate("['hr','sb','hits','k','spread'].map(m => liveTileKind({market: m}))"))
    check("Z9c and no tile appeared for it", "Inning Bet" not in tl, sorted(tl))

    pill = page.evaluate("document.getElementById('liveab-count').textContent")
    check("Z10 the header pill counts pitching and game lines alongside the rest",
          "PITCHING" in pill and "GAME LINE" in pill, pill)

    # A COMBINED leg names two players, and BOTH earn a tile: either one
    # batting moves the same bet.
    TICKETS["windows"][0]["tickets"] = [
        card(1, [prop_leg("Up Now", "hr", 0.5, players=["Up Now", "On Deck"]),
                 prop_leg("In Hole", "hits", 0.5)]),
    ]
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    names = {t["player"] for t in tiles(page)}
    check("Z11 both players on a combined leg get their own tile",
          {"Up Now", "On Deck"} <= names, sorted(names))

    # The odds row. Every prop tile read "HR+390 HITS+390" -- a home-run label
    # on a bet that wasn't one, and the price twice -- because add() files
    # every non-steal price into the list this row labelled HR. Checked on
    # the function directly: a rendered tile only says it once the right
    # fixture happens to put a prop on the wall.
    txt = lambda js: page.evaluate(
        "h => { const d = document.createElement('div'); d.innerHTML = h; return d.textContent; }",
        page.evaluate(js))
    check("Z13 a prop tile's odds row does not call it a home run bet -- and with one kind of bet "
          "it is just the price, since the progress line names the market",
          txt("labOddsHtml({odds:['+390'],oddsSb:[],entries:[{market:'hits',odds:'+390'}]}, 'AT BAT')")
          == "+390",
          txt("labOddsHtml({odds:['+390'],oddsSb:[],entries:[{market:'hits',odds:'+390'}]}, 'AT BAT')"))
    check("Z14 ...a plain home run still shows just its price",
          txt("labOddsHtml({odds:['+390'],oddsSb:[],entries:[{market:'hr',odds:'+390'}]}, 'AT BAT')")
          == "+390")
    check("Z15 ...and a man carrying BOTH still gets the HR label, where it's needed",
          txt("labOddsHtml({odds:['+390','+150'],oddsSb:[],entries:[{market:'hr',odds:'+390'},"
              "{market:'hits',odds:'+150'}]}, 'AT BAT')") == "HR+390 HITS+150")

    check("Z12 no JS errors across the new tiles", not errors, errors)

    # ================= AA: every tile says what it's chasing, every leg alerts =================
    # The user's two rules (2026-10-04): a home run pick reads "0 of 1 HR" the
    # way a sack reads "0 of 1 sacks", so no tile leaves you guessing what it
    # is waiting for; and EVERY leg that hits -- not just home runs and steals
    # -- changes its tile and pops the overlay.
    def set_bat(feed, name, **stats):
        for side in ("home", "away"):
            for pl in feed["liveData"]["boxscore"]["teams"][side]["players"].values():
                if pl["person"]["fullName"] == name:
                    pl["stats"] = {"batting": dict({"plateAppearances": 3}, **stats)}

    def aa_feed(extra_plays=(), on_deck_hits=1):
        f = game(BASE + [play(50, "Up Now", False, COUNT_1_2, balls=1, strikes=2)] + list(extra_plays),
                 "Bottom", 1, "Up Now")
        set_bat(f, "On Deck", hits=on_deck_hits)
        set_bat(f, "In Hole", hits=1, doubles=1)
        set_bat(f, "Home 5", hits=0)
        set_bat(f, "Home 6", hits=0)
        f["liveData"]["boxscore"]["teams"]["home"]["players"]["IDP"] = {
            "person": {"fullName": "Ace Arm"}, "stats": {"pitching": {"strikeOuts": 4, "earnedRuns": 1, "outs": 16}}}
        return f

    FX["feed"] = aa_feed()
    TICKETS["windows"][0]["tickets"] = [
        card(1, [leg("Up Now", "Kevin", "+265"), prop_leg("On Deck", "hits", 1.5)]),
        card(2, [prop_leg("Ace Arm", "er", 1.5), prop_leg("Lead Off", "rbi", 0.5)]),
        # dead: Missed Guy's game is final with no home run
        card(3, [leg("Missed Guy", "Noid", "+290"), prop_leg("Home 5", "hits", 0.5)]),
    ]
    TICKETS["singles"] = [
        dict(prop_leg("In Hole", "hits", 0.5), id="single-0", stake=5.0, payout=12.5),   # already hit at load
        dict(prop_leg("Home 6", "hits", 0.5), id="single-1", stake=5.0, payout=15.0),
    ]
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    page.evaluate("""() => { window.FIRED = []; const real = fireLegHit;
        fireLegHit = (slate, leg, cash) => { FIRED.push(legAlertName(leg)); return real(slate, leg, cash); }; }""")
    tl = {t["player"]: t for t in tiles(page)}
    up = tl.get("Up Now")
    check("AA1 a plain home run pick says what it's chasing: 0 of 1 HR",
          bool(up) and "0 of 1 HR" in up["text"], up and up["text"])
    arm = tl.get("Ace Arm")
    check("AA2 an earned-runs bet counts EARNED RUNS off the pitching line, in its own unit -- "
          "it read the batting line (always 0) and called it K",
          bool(arm) and "1 ER" in arm["text"] and "needs 2" in arm["text"] and " K " not in arm["text"],
          arm and arm["text"])
    check("AA3 a steal bet with no line reads 0 of 1 SB",
          page.evaluate("labProgress({market:'sb', names:['Nobody'], line:null}).text") == "0 of 1 SB",
          page.evaluate("labProgress({market:'sb', names:['Nobody'], line:null})"))
    under = page.evaluate("[labProgress({market:'hits', names:['In Hole'], line:1.5, side:'under'}),"
                          " labProgress({market:'hits', names:['In Hole'], line:0.5, side:'under'})]")
    check("AA4 an UNDER says the number to stay below, never 'x of y', and flags a bust",
          under[0]["text"] == "1 hits (under 1.5)" and not under[0]["bust"] and under[1]["bust"], under)

    # On Deck singles: his hits prop goes 1 -> 2 and clears, on the same play
    # whose plain "Single" result would otherwise hold his tile. Home 5's
    # prop clears too, but his only bet is a dead parlay. Home 6 clears a
    # SINGLE, which cashes it.
    FX["feed"] = aa_feed([play(51, "On Deck", False, [pitch("X", "In play, no out")], event="Single", etype="single")],
                         on_deck_hits=2)
    set_bat(FX["feed"], "Home 5", hits=1)
    set_bat(FX["feed"], "Home 6", hits=1)
    poll(page)
    fired = page.evaluate("FIRED")
    check("AA5 a hits prop clearing fires an alert", "On Deck" in fired, fired)
    check("AA6 ...but not for a leg that already hit before the page opened (the flood guard)",
          "In Hole" not in fired, fired)
    check("AA7 ...nor for one whose only bet is already dead", "Home 5" not in fired, fired)
    overlay = page.evaluate("document.getElementById('bomb-overlay').textContent.replace(/\\s+/g, ' ')")
    check("AA8 the overlay names him and what he did", "On Deck" in overlay and "2+ HITS!" in overlay, overlay)
    tl = tiles(page)
    od = [t for t in tl if t["player"] == "On Deck"]
    check("AA9 his tile turns into ONE green HIT tile -- the plain 'Single' result is replaced, not doubled",
          len(od) == 1 and od[0]["tag"] == "HIT" and od[0]["result"] == "2+ HITS!",
          [(t["tag"], t["result"]) for t in od])
    check("AA10 ...showing the count it reached", bool(od) and "2 of 2 hits" in od[0]["text"], od and od[0]["text"])
    h6 = [t for t in tl if t["player"] == "Home 6"]
    check("AA11 a leg that CASHES a bet gets the gold CASHED tile with what it paid",
          len(h6) == 1 and h6[0]["tag"] == "CASHED" and "SINGLE CASHED" in h6[0]["text"]
          and "$15.00" in h6[0]["text"], [t["text"] for t in h6])
    check("AA12 a cleared leg on a dead parlay changes no tile either",
          not [t for t in tl if t["player"] == "Home 5"], [t["player"] for t in tl])

    # When it HAPPENED, for the bell: the play on which the count reached the
    # line, not when the page noticed. On Deck's single (play 51) ended at NOW.
    timed = page.evaluate("""() => [
        legHitTime({player: 'On Deck', market: 'hits', line: 0.5}),
        legHitTime({player: 'On Deck', market: 'tb', line: 1.5})]""")
    check("AA17 a hits leg is timed off the play that reached its line",
          bool(timed[0]) and timed[0]["t"] == int(NOW.timestamp() * 1000) and not timed[0]["approx"], timed[0])
    check("AA18 ...and a line the plays never reached gets no exact time (a single is 1 total base, not 2)",
          timed[1] is None or timed[1]["approx"], timed[1])

    # Two markets first seen on the 2026-10-05 card: extra-base hits (doubles +
    # triples + homers) off the batting line, a pitcher's OUTS off the pitching
    # line. "Over 14.5 Outs" with 16 recorded is a hit.
    graded = page.evaluate("""() => [
        stateForLeg({player: 'In Hole', market: 'xbh', line: 0.5}),
        stateForLeg({player: 'On Deck', market: 'xbh', line: 0.5}),
        stateForLeg({player: 'Ace Arm', market: 'outs', line: 14.5}),
        stateForLeg({player: 'Ace Arm', market: 'outs', line: 16.5})]""")
    check("AA19 extra-base hits count a double; a man with only singles isn't there yet",
          graded[0] == "hit" and graded[1] == "live", graded)
    check("AA20 a pitcher's outs come off his PITCHING line: 16 clears 14.5, not 16.5",
          graded[2] == "hit" and graded[3] == "live", graded)

    advance(page, 15)
    left = [t["player"] for t in tiles(page) if t["tag"] in ("HIT", "CASHED")]
    check("AA13 the HIT tiles hold about as long as a home run's, then go", not left, left)
    # A walk-off. The home run and the final out arrive on the SAME poll, so
    # by the time alerts are checked the slate has already rolled to
    # Yesterday. Alerts read SLATES.today only, so the last swing of the
    # night -- or any leg settled by the final out -- never alerted.
    page.evaluate("""() => { window.BOMBED = []; const real = fireBomb;
        fireBomb = (name, cash) => { BOMBED.push(name); return real(name, cash); }; }""")
    FX["state"] = "Final"
    FX["feed"] = aa_feed([play(52, "Up Now", False, [pitch("X", "In play, run(s)")], event="Home Run", etype="home_run",
                               hit={"launchSpeed": 108.0, "launchAngle": 27.0, "totalDistance": 420.0, "trajectory": "fly_ball"})],
                         on_deck_hits=2)
    FX["feed"]["gameData"]["status"]["abstractGameState"] = "Final"
    poll(page)
    check("AA15 the slate rolled over on the walk-off poll -- the case being tested",
          page.evaluate("SLATES.today === null && !!SLATES.yesterday"),
          page.evaluate("[!!SLATES.today, !!SLATES.yesterday]"))
    check("AA16 the walk-off home run still alerts", page.evaluate("BOMBED") == ["Up Now"], page.evaluate("BOMBED"))
    FX["state"] = "Live"
    check("AA14 no JS errors across the hit alerts", not errors, errors)
    browser.close()


# ================= AB: the side that hasn't batted yet =================
# Top of the 1st: the HOME team has no plate appearances at all, so "the
# last hitter's slot + 1" has nothing to work from. It was left blank, and
# every home pick sat under "In the game -- waiting on the live feed" with no
# batting context until the home side finally batted -- reported live on
# Mookie Betts (2026-10-04), whose lineup was posted and whose game was on.
# A side that hasn't batted leads off from slot 1.
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    FX["clock"] = NOW
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)
    FX["state"] = "Live"
    FX["feed"] = game([play(1, "Next Half", True, COUNT_1_2[:1], strikes=1)], "Top", 0, "Next Half", inning=1)
    TICKETS["windows"][0]["tickets"] = [card(1, [leg("Lead Off", "Kenny", "+400"), leg("Home 5", "Memo", "+500")]),
                                        # a second KIND of bet on him, so his header carries labels too
                                        card(2, [dict(leg("Lead Off", "Kenny", "+145"), market="tb", line=1.5)])]
    TICKETS["singles"] = []
    page.goto("http://bmbs.test/index.html")
    boot(page)
    poll(page)
    ctx = page.evaluate("liveContextForPlayer('Lead Off')")
    check("AB1 before the home side has batted, its leadoff man still has a batting context",
          bool(ctx) and ctx["slot"] == 1 and ctx["battersAway"] == 0, ctx)
    check("AB2 ...so his line says where he bats, not 'waiting on the live feed'",
          "waiting on the live feed" not in page.evaluate("document.getElementById('content').textContent"),
          page.evaluate("[...document.querySelectorAll('.leg-live-context')].map(e => e.textContent)"))
    tl = {t["player"]: t for t in tiles(page)}
    check("AB3 ...and he gets his LEADS OFF NEXT tile", tl.get("Lead Off", {}).get("tag") == "LEADS OFF NEXT",
          {k: v["tag"] for k, v in tl.items()})
    check("AB4 a hitter further down is placed too, just not guaranteed this half",
          (page.evaluate("liveContextForPlayer('Home 5')") or {}).get("slot") == 5,
          page.evaluate("liveContextForPlayer('Home 5')"))
    # "LEAD..." with no way to read the rest: the status was ellipsised to make
    # room for the prices. It is never cut now -- the prices wrap instead.
    clip = page.evaluate("""() => [...document.querySelectorAll('#liveab-grid .ab-tile')]
        .filter(t => t.dataset.player === 'Lead Off').map(t => { const g = t.querySelector('.ab-tag');
          return {text: g.textContent, clipped: g.scrollWidth > g.clientWidth + 1,
                  ellipsis: getComputedStyle(g).textOverflow === 'ellipsis'}; })""")
    check("AB6 the tile's status is shown in full, even with prices for two kinds of bet beside it",
          clip and clip[0]["text"] == "LEADS OFF NEXT" and not clip[0]["clipped"] and not clip[0]["ellipsis"], clip)
    check("AB7 ...and the line under his name says where he bats",
          "batting 1st" in tl.get("Lead Off", {}).get("text", ""), tl.get("Lead Off", {}).get("text"))
    check("AB5 no JS errors", not errors, errors)
    browser.close()


print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all live-at-bats checks passed")
