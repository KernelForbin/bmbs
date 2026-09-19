"""
Headless checks for STOLEN BASE bets in index.html: grading, the STEAL tag on
tickets, the steal alert (plain and cashed), and the Live Bet Tracker's
on-base / steal-result tiles.

Same approach as test_live_at_bats.py: one route handler, in-memory fixtures, a
pinned clock, nothing under data/ read or written, no real network. One live
game is walked forward a poll at a time; a second, finished game covers the
"already over" cases and the load-time flood guard.

The home-run side of the page is covered by test_page.py / test_live_at_bats.py,
which pass unchanged -- that is the proof a leg with no `market` still behaves
exactly as it did. This file is about legs that say `"market": "sb"`.

    python tests/test_steals.py
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


AWAY = [f"Away {n}" for n in range(1, 10)]
HOME = ["Speedy Steal", "Both Ways", "Pulled Runner", "Steal Only Homers", "Slugger Hr"] + [f"Home {n}" for n in range(6, 10)]


def play(idx, batter, top=False, event=None, etype=None, out=False, end=None, runners=(), hit=None):
    events = [{"isPitch": True, "details": {"code": "B", "call": {"description": "Ball"}, "description": "Ball", "type": {"description": "Sinker"}},
               "pitchData": {"startSpeed": 94.0}, "endTime": iso(end or NOW)}]
    if hit:
        events[-1]["hitData"] = hit
    p = {"atBatIndex": idx, "result": {"type": "atBat"},
         "about": {"isTopInning": top, "inning": 6, "halfInning": "top" if top else "bottom", "isComplete": bool(event)},
         "count": {"balls": 1, "strikes": 0},
         "matchup": {"batter": {"fullName": batter}, "batSide": {"code": "R"}, "pitcher": {"fullName": "Brayan Bello"}, "pitchHand": {"code": "R"}},
         "playEvents": events, "runners": list(runners)}
    if event:
        p["result"].update({"event": event, "eventType": etype, "isOut": out})
        p["about"]["endTime"] = iso(end or NOW)
    return p


def runner(name, etype):
    # one steal is reported as one runner entry per base-to-base segment; the page must count it once
    return {"details": {"eventType": etype, "runner": {"fullName": name}, "playIndex": 0}, "movement": {}}


def game(plays, state="Bottom", outs=0, batter="Home 6", bases=None, subs=None):
    def side(names, extra=None):
        out = {f"ID{n}": {"person": {"fullName": n}, "battingOrder": f"{i + 1}00"} for i, n in enumerate(names)}
        out.update(extra or {})
        return out
    offense = {"batter": {"fullName": batter}}
    for base, name in (bases or {}).items():
        offense[base] = {"fullName": name}
    return {"gameData": {"status": {"abstractGameState": "Live"}, "venue": {"name": "Yankee Stadium"}, "weather": {},
                         "teams": {"away": {"abbreviation": "BOS"}, "home": {"abbreviation": "NYY"}}},
            "liveData": {"plays": {"allPlays": plays},
                         "boxscore": {"teams": {"away": {"players": side(AWAY)}, "home": {"players": side(HOME, subs)}}},
                         "linescore": {"currentInning": 6, "inningState": state, "outs": outs, "offense": offense}}}


OLD = NOW - timedelta(hours=2)
# A finished game: someone who stole (must be a hit, and must NOT be announced on
# load), someone who played and didn't, and someone who never got off the bench.
FINAL = {"gameData": {"status": {"abstractGameState": "Final"}},
         "liveData": {"plays": {"allPlays": [play(3, "Whoever", True, event="Walk", etype="walk", end=OLD,
                                                  runners=[runner("Final Stole", "stolen_base_2b"), runner("Final Stole", "stolen_base_2b")])]},
                      "boxscore": {"teams": {"away": {"players": {
                          "ID1": {"person": {"fullName": "Final Stole"}, "battingOrder": "100"},
                          "ID2": {"person": {"fullName": "Final NoSteal"}, "battingOrder": "200"},
                          "ID3": {"person": {"fullName": "Bench Thief"}}}}, "home": {"players": {}}}},
                      "linescore": {}}}


def leg(player, who, odds, market=None):
    out = {"id": f"leg-{player}-{market}", "player": player, "team": "", "who": who, "meta": who, "odds": odds, "time": "7:05 PM ET"}
    if market:
        out["market"] = market
    return out


def card(n, legs, payout=100.0):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5</b> bet by Memo", "stake": 5.0, "book": "Memo", "payout": payout, "legs": legs}


def single(i, who, player, odds, market=None, payout=20.0):
    out = {"id": f"single-{i}", "who": who, "player": player, "team": "", "meta": "$5.00 bet", "odds": odds, "stake": 5.0, "payout": payout, "pp": f"PP ${payout:.2f}"}
    if market:
        out["market"] = market
    return out


TICKETS = {"date": DATE, "note": "", "windows": [{"title": "Mixed Parlays", "tickets": [
    card(1, [leg("Slugger Hr", "Joe", "+400"), leg("Speedy Steal", "Miggs", "-120", "sb")], payout=154.0),   # HR + STEAL on one ticket
    card(2, [leg("Both Ways", "Bernie", "+150", "sb"), leg("Home 9", "Memo", "+500")]),                     # steal leg; other leg stays open
    card(3, [leg("Both Ways", "Bailey", "+450"), leg("Home 8", "Noid", "+600")]),                           # same player, HOME RUN leg
    card(4, [leg("Pulled Runner", "Kevin", "+200", "sb"), leg("Steal Only Homers", "Didge", "+175", "sb")]),
    card(5, [leg("Final Stole", "Joe", "+140", "sb"), leg("Final NoSteal", "Memo", "+160", "sb")]),
]}], "singles": [single(0, "KENNY", "Speedy Steal", "-120", "sb", payout=9.17),
                 single(1, "NOID", "Bench Thief", "+300", "sb"),
                 # his parlay (Card 4) dies when Pulled Runner is pulled; this single keeps him trackable
                 single(2, "DIDGE", "Steal Only Homers", "+175", "sb")]}

FX = {"feed": None, "clock": NOW}
HOMERED = play(5, "Slugger Hr", False, event="Home Run", etype="home_run", end=OLD,
               hit={"launchSpeed": 104.0, "launchAngle": 30.0, "totalDistance": 401.0, "trajectory": "fly_ball"})


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
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["feed"] if m.group(1) == "1301" else FINAL))
    return route.abort()


def poll(page):
    page.evaluate("pollAndRender()")


def advance(page, seconds):
    FX["clock"] += timedelta(seconds=seconds)
    page.clock.set_fixed_time(FX["clock"])
    page.evaluate("renderLiveAtBats()")


def tiles(page):
    return page.evaluate("""() => [...document.querySelectorAll('#liveab-grid .ab-tile')].map(t => ({
        player: t.dataset.player, cls: t.className, tag: t.querySelector('.ab-tag').textContent,
        odds: t.querySelector('.ab-odds').textContent.trim(),
        result: t.querySelector('.ab-result') ? t.querySelector('.ab-result').textContent : null,
        burst: !!t.querySelector('.ab-bomb'), text: t.textContent.replace(/\\s+/g, ' ').trim(),
    }))""")


def leg_rows(page):
    return page.evaluate("""() => [...document.querySelectorAll('#content .leg, #content .single-row')].map(r => ({
        player: (r.querySelector('.leg-player, .single-player').childNodes[0].textContent || '').trim(),
        steal: !!r.querySelector('.mkt-tag.sb'), state: r.className.match(/state-(\\w+)/)[1],
        php: !!r.querySelector('.php-hit'), text: r.textContent.replace(/\\s+/g, ' ').trim(),
    }))""")


def row(rows, player, steal):
    return next(r for r in rows if r["player"] == player and r["steal"] == steal)


def overlay(page):
    return page.evaluate("""() => { const c = document.querySelector('#bomb-overlay .bomb-card'); return c ? {
        cls: c.className, name: c.querySelector('.bomb-name').textContent, word: c.querySelector('.bomb-word').textContent,
        cash: c.querySelector('.bomb-cash') ? c.querySelector('.bomb-cash').textContent.replace(/\\s+/g, ' ').trim() : null } : null; }""")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.add_init_script("""window.__notes = [];
        window.Notification = function (t, o) { window.__notes.push([t, o && o.body]); };
        window.Notification.permission = 'granted';
        window.Notification.requestPermission = () => Promise.resolve('granted');
        try { localStorage.setItem('bmbs.notif.push', '1'); localStorage.setItem('bmbs.liveab.open', '1'); } catch (e) {}""")
    page.route("**/*", handler)

    # ---------- A. load: grading, tags, and the flood guard ----------
    FX["feed"] = game([HOMERED, play(40, "Speedy Steal")], batter="Speedy Steal")
    page.goto("http://bmbs.test/index.html")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    rows = leg_rows(page)
    check("A1 steal legs carry a STEAL tag; home run legs look exactly as they always have",
          row(rows, "Speedy Steal", True) and not any(r["steal"] for r in rows if r["player"] in ("Slugger Hr", "Home 9", "Home 8")),
          str([(r["player"], r["steal"]) for r in rows]))
    check("A2 same player, two markets: his steal leg and his home run leg are graded separately",
          row(rows, "Both Ways", True)["state"] == "live" and row(rows, "Both Ways", False)["state"] == "live")
    check("A3 finished game: stole -> hit, played without one -> miss",
          row(rows, "Final Stole", True)["state"] == "hit" and row(rows, "Final NoSteal", True)["state"] == "miss")
    check("A4 on the roster of a finished game but never got in -> void, not a miss",
          row(rows, "Bench Thief", True)["state"] == "na", row(rows, "Bench Thief", True)["text"])
    check("A5 flood guard: a steal from before the page loaded is a hit but is NOT announced",
          overlay(page) is None and page.evaluate("window.__notes.length") == 0)
    check("A6 one steal reported as two runner segments counts once",
          page.evaluate("RESULTS.gameInfo.get(1302).steals.length") == 1)
    check("A7 the panel is the Live Bet Tracker now", "Live Bet Tracker" in page.inner_text(".liveab-title"))
    t = tiles(page)
    check("A8 a steal pick at the plate still gets his at-bat tile, priced as a steal (minus money shown as such)",
          t[0]["player"] == "Speedy Steal" and t[0]["tag"] == "AT BAT" and t[0]["odds"].startswith("SB-120"), str(t))

    # ---------- B. he reaches base ----------
    FX["feed"] = game([HOMERED, play(40, "Speedy Steal", event="Single", etype="single"), play(41, "Both Ways")],
                      batter="Both Ways", bases={"first": "Speedy Steal"})
    poll(page)
    t = tiles(page)
    speedy = next(x for x in t if x["player"] == "Speedy Steal")
    check("B1 on base -> straight to an ON 1ST tile (no 'Single' result sitting in front of it)",
          "onbag" in speedy["cls"] and speedy["tag"] == "ON 1ST" and "result" not in speedy["cls"], str(speedy))
    check("B2 it says the base ahead is open, who's batting and who's pitching",
          "2nd is open" in speedy["text"] and "Ways batting" in speedy["text"] and "vs Bello" in speedy["text"], speedy["text"])
    check("B3 the runner with an open base sorts ahead of the hitter at the plate",
          [x["player"] for x in t][:2] == ["Speedy Steal", "Both Ways"], str([x["player"] for x in t]))
    check("B4 Both Ways is on two markets: one tile, both prices labelled", "HR" in t[1]["odds"] and "SB" in t[1]["odds"], t[1]["odds"])
    check("B5 count pill counts him", "1 ON BASE" in page.inner_text("#liveab-count"), page.inner_text("#liveab-count"))
    check("B6 the ticket leg says so too", "ON 1ST NOW" in row(leg_rows(page), "Speedy Steal", True)["text"])

    # ---------- C. blocked ----------
    FX["feed"] = game([HOMERED, play(40, "Speedy Steal", event="Single", etype="single"), play(41, "Both Ways", event="Walk", etype="walk"),
                       play(42, "Pulled Runner")], batter="Pulled Runner", bases={"first": "Both Ways", "second": "Speedy Steal"})
    poll(page)
    t = tiles(page)
    ways = next(x for x in t if x["player"] == "Both Ways")
    check("C1 runner directly ahead -> his tile says blocked, and drops behind the hitter",
          "blocked" in ways["cls"] and "runner on 2nd" in ways["text"] and [x["player"] for x in t][:3] == ["Speedy Steal", "Pulled Runner", "Both Ways"],
          str([(x["player"], x["cls"]) for x in t]))
    check("C2 Speedy moved up to second: 3rd is open", "ON 2ND" == t[0]["tag"] and "3rd is open" in t[0]["text"], t[0]["text"])

    # ---------- D. the steal (mid-at-bat, in a play that isn't complete) ----------
    FX["clock"] += timedelta(seconds=10)
    page.clock.set_fixed_time(FX["clock"])
    FX["feed"] = game([HOMERED, play(40, "Speedy Steal", event="Single", etype="single"), play(41, "Both Ways", event="Walk", etype="walk"),
                       play(42, "Pulled Runner", end=FX["clock"], runners=[runner("Speedy Steal", "stolen_base_3b")])],
                      batter="Pulled Runner", bases={"first": "Both Ways", "third": "Speedy Steal"})
    poll(page)
    rows = leg_rows(page)
    check("D1 the leg is a hit the moment the steal shows up, while that at-bat is still going",
          row(rows, "Speedy Steal", True)["state"] == "hit" and "Stole 3rd" in row(rows, "Speedy Steal", True)["text"])
    o = overlay(page)
    check("D2 steal alert, gold: his single AND the HR+steal parlay both cashed on it",
          o and "steal" in o["cls"] and "cash" in o["cls"] and o["name"] == "Speedy Steal" and o["word"] == "STOLE 3RD!"
          and "2 BETS CASHED" in o["cash"] and "$163.17" in o["cash"], str(o))
    notes = page.evaluate("window.__notes")
    check("D3 push notification says the same", len(notes) == 1 and "stole 3rd" in notes[0][0] and "2 BETS CASHED" in notes[0][1], str(notes))
    t = tiles(page)
    check("D4 his tile becomes the result, up front, with the runner burst", t[0]["player"] == "Speedy Steal" and "swiped" in t[0]["cls"]
          and t[0]["result"] == "Stole 3rd!" and t[0]["burst"], str(t[0]))
    check("D5 no Pinch-Hit-Protection styling on a steal hit", not row(rows, "Speedy Steal", True)["php"])
    poll(page)
    check("D6 an unchanged poll doesn't re-announce", len(page.evaluate("window.__notes")) == 1)
    advance(page, 4)
    check("D7 burst gives way to the result", not tiles(page)[0]["burst"] and tiles(page)[0]["result"] == "Stole 3rd!")
    advance(page, 11)
    check("D8 ...which drops after ~14s", all(x["player"] != "Speedy Steal" for x in tiles(page)), str([x["player"] for x in tiles(page)]))
    page.evaluate("dismissBomb()")

    # ---------- E. caught stealing, then a plain (uncashed) steal ----------
    FX["clock"] += timedelta(seconds=10)
    page.clock.set_fixed_time(FX["clock"])
    base_plays = [HOMERED, play(40, "Speedy Steal", event="Single", etype="single"), play(41, "Both Ways", event="Walk", etype="walk")]
    FX["feed"] = game(base_plays + [play(42, "Pulled Runner", end=FX["clock"], runners=[runner("Speedy Steal", "stolen_base_3b"), runner("Both Ways", "caught_stealing_2b")])],
                      outs=1, batter="Pulled Runner", bases={"third": "Speedy Steal"})
    poll(page)
    t = tiles(page)
    check("E1 caught stealing: a CAUGHT tile, leg still open, nothing announced",
          t[0]["player"] == "Both Ways" and t[0]["tag"] == "CAUGHT" and t[0]["result"] == "Caught stealing 2nd"
          and row(leg_rows(page), "Both Ways", True)["state"] == "live" and overlay(page) is None, str(t[0]))
    advance(page, 10)
    FX["clock"] += timedelta(seconds=10)
    page.clock.set_fixed_time(FX["clock"])
    FX["feed"] = game(base_plays + [play(42, "Pulled Runner", event="Strikeout", etype="strikeout", out=True, end=FX["clock"],
                                         runners=[runner("Speedy Steal", "stolen_base_3b"), runner("Both Ways", "caught_stealing_2b")]),
                                    play(50, "Both Ways", event="Single", etype="single", end=FX["clock"]),
                                    play(51, "Pulled Runner", end=FX["clock"], runners=[runner("Both Ways", "stolen_base_2b")])],
                      outs=2, batter="Pulled Runner", bases={"second": "Both Ways"})
    poll(page)
    o = overlay(page)
    rows = leg_rows(page)
    check("E2 a steal that cashes nothing yet is the plain blue alert",
          o and "steal" in o["cls"] and "cash" not in o["cls"] and o["word"] == "STOLE 2ND!" and o["cash"] is None, str(o))
    check("E3 only his STEAL leg hit -- his home run leg on another ticket is untouched",
          row(rows, "Both Ways", True)["state"] == "hit" and row(rows, "Both Ways", False)["state"] == "live")
    check("E4 the leg remembers he was caught earlier? no -- it's a hit now, it just says where he stole",
          "Stole 2nd" in row(rows, "Both Ways", True)["text"])
    page.evaluate("dismissBomb()")
    advance(page, 15)

    # ---------- F. a steal-only pick homers: not our bomb ----------
    FX["clock"] += timedelta(seconds=10)
    page.clock.set_fixed_time(FX["clock"])
    before = len(page.evaluate("window.__notes"))
    plays_f = FX["feed"]["liveData"]["plays"]["allPlays"] + [
        play(52, "Steal Only Homers", event="Home Run", etype="home_run", end=FX["clock"],
             hit={"launchSpeed": 101.0, "launchAngle": 28.0, "totalDistance": 388.0, "trajectory": "fly_ball"})]
    FX["feed"] = game(plays_f, outs=2, batter="Slugger Hr", bases={"second": "Both Ways"})
    poll(page)
    check("F1 a home run by someone we only need a STEAL from: no bomb, no notification, leg still open",
          overlay(page) is None and len(page.evaluate("window.__notes")) == before
          and row(leg_rows(page), "Steal Only Homers", True)["state"] == "live")
    page.evaluate("document.getElementById('hrlog-section').classList.remove('collapsed'); setHrFilter('picks')")
    ours = page.evaluate("[...document.querySelectorAll('#hrlog-list .hr-batter')].map(e => e.textContent)")
    check("F2 ...and it isn't one of 'our' home runs in the Home Run Log (Slugger Hr's is)", ours == ["Slugger Hr"], str(ours))

    # ---------- G. pulled from the game: no Pinch Hit Protection for steals ----------
    sub = {"IDSub": {"person": {"fullName": "Fresh Legs"}, "battingOrder": "301"}}
    FX["feed"] = game(plays_f, outs=2, batter="Slugger Hr", bases={"second": "Both Ways"}, subs=sub)
    poll(page)
    r = row(leg_rows(page), "Pulled Runner", True)
    check("G1 replaced in the lineup with no steal -> a miss right away, and the leg says why",
          r["state"] == "miss" and "can't re-enter" in r["text"], r["text"])
    FX["feed"] = game(plays_f + [play(53, "Whoever", runners=[runner("Fresh Legs", "stolen_base_2b")])], outs=2, batter="Slugger Hr", subs=sub)
    poll(page)
    check("G2 his replacement stealing does NOT credit him", row(leg_rows(page), "Pulled Runner", True)["state"] == "miss")

    # ---------- H. stranded ----------
    FX["feed"] = game(plays_f, outs=2, batter="Slugger Hr", bases={"first": "Steal Only Homers"}, subs=sub)
    poll(page)
    advance(page, 20)
    check("H1 Steal Only Homers is on first", any(x["player"] == "Steal Only Homers" and x["tag"] == "ON 1ST" for x in tiles(page)), str(tiles(page)))
    FX["feed"] = game(plays_f, state="Middle", outs=3, batter="Away 1", subs=sub)
    poll(page)
    t = tiles(page)
    check("H2 inning ends with him still out there -> a brief STRANDED tile instead of just vanishing",
          any(x["player"] == "Steal Only Homers" and x["tag"] == "STRANDED" for x in t), str(t))
    advance(page, 10)
    check("H3 ...then gone", all(x["player"] != "Steal Only Homers" for x in tiles(page)))

    # ---------- I. filters, tabs, hygiene ----------
    page.evaluate("toggleFilter('hit')")
    names = {r["player"] for r in leg_rows(page)}
    check("I1 HIT filter includes the cashed mixed HR+STEAL parlay and the cashed steal single", {"Slugger Hr", "Speedy Steal"} <= names, str(names))
    page.evaluate("toggleFilter(null)")
    check("I2 Bettor Tracker shows a minus-money steal as -120, never '+-120'",
          "+-" not in page.inner_text("#bettor-list") and "-120" in page.inner_text("#bettor-list"), page.inner_text("#bettor-list")[:200])
    check("I3 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    check("I4 no script errors", not errors, str(errors))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all steal checks passed")
