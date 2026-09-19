"""
Headless checks for the football (anytime-touchdown) tracker, football/index.html.

Same approach as the baseball page tests: one route handler, in-memory ESPN
fixtures, a pinned clock moved by hand, nothing under data/ read or written,
no real network. One Sunday-plus-Monday slate is walked forward poll by poll.

Block A is the isolation guarantee in both directions: the football page never
touches MLB or the baseball tickets files, and the baseball page never touches
ESPN or the football ones.

    python tests/test_football.py
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "football" / "index.html"
BASEBALL_SRC = (REPO / "index.html").read_text(encoding="utf-8")
FOOTBALL_SRC = PAGE.read_text(encoding="utf-8")
NOW = datetime(2026, 9, 20, 17, 30, tzinfo=timezone.utc)   # Sunday 1:30 PM ET
SUN, MON = "2026-09-20", "2026-09-21"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- ESPN fixtures ----------------
TEAM_ID = {"PHI": "21", "TEN": "10", "JAX": "30", "DEN": "7", "MIN": "16", "CHI": "3", "LAR": "14", "NYG": "19"}
GAMES = {"2001": ("PHI", "TEN", SUN), "2002": ("JAX", "DEN", SUN), "2003": ("MIN", "CHI", SUN), "2004": ("LAR", "NYG", MON)}


def event(gid, state, period=0, clock="", score=(0, 0)):
    away, home, _ = GAMES[gid]
    return {"id": gid, "date": "2026-09-20T17:00Z", "status": {"period": period, "displayClock": clock,
            "type": {"state": state, "shortDetail": {"pre": "1:00 PM", "in": f"{clock} - Q{period}", "post": "Final"}[state]}},
            "competitions": [{"competitors": [
                {"id": TEAM_ID[away], "homeAway": "away", "score": str(score[0]), "team": {"abbreviation": away}},
                {"id": TEAM_ID[home], "homeAway": "home", "score": str(score[1]), "team": {"abbreviation": home}}]}]}


def statline(athlete_id, name, **cats):
    """cats: rushing=(car, yds, td)  receiving=(rec, yds, td, tgts)  interceptions=(int, yds, td)"""
    return (athlete_id, name, cats)


LABELS = {"rushing": ["CAR", "YDS", "AVG", "TD", "LONG"], "receiving": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"],
          "interceptions": ["INT", "YDS", "TD"], "passing": ["C/ATT", "YDS", "AVG", "TD", "INT"]}


def boxscore(lines_by_team):
    out = []
    for team, lines in lines_by_team.items():
        cats = {}
        for athlete_id, name, given in lines:
            for cat, vals in given.items():
                if cat == "rushing":
                    stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0"]
                elif cat == "receiving":
                    stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0", str(vals[3])]
                elif cat == "interceptions":
                    stats = [str(v) for v in vals]
                else:   # passing: (td,) -- a thrown TD must never cash the passer
                    stats = ["20/30", "250", "8.3", str(vals[0]), "0"]
                cats.setdefault(cat, []).append({"athlete": {"id": athlete_id, "displayName": name}, "stats": stats})
        out.append({"team": {"abbreviation": team},
                    "statistics": [{"name": c, "labels": LABELS[c], "athletes": a} for c, a in cats.items()]})
    return out


def scoring(play_id, team, kind, text, period, clock, away_score, home_score):
    return {"id": play_id, "type": {"text": kind}, "text": text, "period": {"number": period},
            "clock": {"value": 300.0, "displayValue": clock}, "team": {"abbreviation": team},
            "awayScore": away_score, "homeScore": home_score}


def drive(drive_id, team, plays_end=None, result=None, is_score=False, description="5 plays, 40 yards, 2:31", ended=None, scoring_text=""):
    plays = []
    if scoring_text:
        plays.append({"id": f"{drive_id}-s", "scoringPlay": True, "text": scoring_text, "wallclock": iso(ended or NOW)})
    plays.append({"id": f"{drive_id}-p", "text": "last play of the drive", "wallclock": iso(ended or NOW),
                  "end": plays_end or {}})
    d = {"id": drive_id, "team": {"abbreviation": team}, "description": description, "isScore": is_score, "plays": plays}
    if result:
        d["displayResult"] = result
    return d


def ball(team, to_go, text):
    return {"team": {"id": TEAM_ID[team]}, "yardsToEndzone": to_go, "downDistanceText": text, "shortDownDistanceText": text.split(" at ")[0]}


def summary(gid, state, period, clock, score, lines, plays=(), current=None, previous=()):
    ev = event(gid, state, period, clock, score)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    drives = {"previous": list(previous)}
    if current:
        drives["current"] = current
    return {"header": {"competitions": [comp]}, "boxscore": {"players": boxscore(lines)}, "scoringPlays": list(plays), "drives": drives}


def leg(player, team, athlete_id, who, odds):
    return {"id": f"leg-{player}", "player": player, "team": team, "athleteId": athlete_id, "who": who,
            "meta": f"{team} &middot; {who}", "odds": odds, "time": "1:00 PM ET"}


def card(n, legs, payout=50.0):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5.00</b> bet by Memo", "stake": 5.0, "book": "Memo", "payout": payout, "legs": legs}


def single(i, who, player, team, athlete_id, odds, payout):
    return {"id": f"single-{i}", "who": who, "player": player, "team": team, "athleteId": athlete_id,
            "meta": "1:00 PM ET &middot; $5.00 bet", "odds": odds, "stake": 5.0, "payout": payout, "pp": f"PP ${payout:.2f}"}


TICKETS = {"sport": "football", "date": SUN, "endDate": MON, "note": "", "windows": [{"title": "2-Leg Parlay Cards", "tickets": [
    card(1, [leg("A.J. Brown", "PHI", "2", "Joe", "+120"), leg("Tony Pollard", "TEN", "", "Miggs", "+150")]),        # Pollard: no id -> by name
    card(2, [leg("James Cook", "TEN", "4", "Kevin", "+100"), leg("Josh Allen", "BUF", "5", "Bernie", "+140")]),      # BUF isn't playing
    card(3, [leg("Puka Nacua", "LAR", "8", "Bailey", "-110"), leg("Kyren Williams", "LAR", "9", "Didge", "-150")]),  # Monday night
]}], "singles": [
    single(0, "KENNY", "Saquon Barkley", "PHI", "1", "-135", 8.70),
    single(1, "JOE", "Inactive Guy", "PHI", "6", "+900", 50.0),
    single(2, "NOID", "Blocking Tight End", "PHI", "7", "+1200", 65.0),
]}

FX = {"events": {}, "summaries": {}, "rosters": {}, "clock": NOW}
SEEN = []


def handler(route, request):
    url = request.url
    SEEN.append(url)
    if url.endswith("/football/"):
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE.read_bytes())
    if url.endswith("/data/football/tickets-previous.json"):
        return route.fulfill(status=404, body="")
    if url.endswith("/data/football/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(TICKETS))
    m = re.search(r"site\.web\.api\.espn\.com/.*/scoreboard\?dates=(\d{8})", url)
    if m:
        day = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
        evs = [FX["events"][g] for g, (_, _, d) in GAMES.items() if d == day and g in FX["events"]]
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    m = re.search(r"site\.web\.api\.espn\.com/.*/summary\?event=(\d+)", url)
    if m:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["summaries"][m.group(1)]))
    m = re.search(r"sports\.core\.api\.espn\.com/.*/events/(\d+)/.*/competitors/(\d+)/roster", url)
    if m:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["rosters"].get(f"{m.group(1)}-{m.group(2)}", {"entries": []})))
    return route.abort()


def poll(page):
    page.evaluate("pollAndRender()")


def advance(page, seconds):
    FX["clock"] += timedelta(seconds=seconds)
    page.clock.set_fixed_time(FX["clock"])
    page.evaluate("renderLiveDrives()")


def states(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#content .leg, #content .single-row')].map(r =>
        [r.querySelector('.leg-player, .single-player').firstChild.textContent.trim(), [...r.classList].find(c => c.startsWith('state-')).slice(6)]))""")


def tiles(page):
    return page.evaluate("""() => [...document.querySelectorAll('#drives-grid .ab-tile')].map(t => ({
        player: t.dataset.player, tag: t.querySelector('.ab-tag').textContent,
        kind: t.classList.contains('result') ? 'result' : t.classList.contains('rz') ? 'rz' : t.classList.contains('now') ? 'ball' : 'wait',
        ball: !!t.querySelector('.ab-bomb'), iron: !!t.querySelector('.iron-mark'),
        result: t.querySelector('.ab-result') ? t.querySelector('.ab-result').textContent : null,
        text: t.textContent.replace(/\\s+/g, ' ').trim() }))""")


def overlay(page):
    return page.evaluate("""() => { const h = document.getElementById('bomb-overlay'); if (!h.classList.contains('active')) return null;
        const c = h.querySelector('.bomb-cash');
        return { text: h.querySelector('.bomb-name').textContent + ' ' + h.querySelector('.bomb-word').textContent,
                 cash: c ? c.textContent.replace(/\\s+/g, ' ').trim() : null }; }""")


def ctx(page, player):
    return page.evaluate("""n => { const r = [...document.querySelectorAll('#content .leg, #content .single-row')].find(x => x.textContent.includes(n));
        return r ? [...r.querySelectorAll('.leg-action-tag, .leg-live-context')].map(e => e.textContent.trim()).join(' || ') : null; }""", player)


def count(pattern):
    return sum(1 for u in SEEN if re.search(pattern, u))


PRE = {g: event(g, "pre") for g in GAMES}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)

    # ---------- A. isolation, both ways ----------
    FX["events"] = dict(PRE)
    page.goto("http://bmbs.test/football/")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    check("A1 football page never calls MLB", not any("mlb.com" in u or "statsapi" in u for u in SEEN))
    check("A2 football page never reads the baseball tickets files",
          not any(re.search(r"/data/tickets(-previous)?\.json", u) for u in SEEN), str([u for u in SEEN if "tickets" in u]))
    check("A3 it reads its own files, under data/football/", any(u.endswith("/data/football/tickets.json") for u in SEEN))
    check("A4 football source has no MLB API, baseball source has no ESPN",
          "statsapi" not in FOOTBALL_SRC and "espn" not in BASEBALL_SRC.lower())
    check("A5 baseball page's only football coupling is the switch link",
          BASEBALL_SRC.count('href="/football/"') == 1 and "data/football" not in BASEBALL_SRC)
    check("A6 every ESPN call uses the browser-permitted hosts (site.api.espn.com is CORS-blocked for browsers)",
          all("site.web.api.espn.com" in u or "sports.core.api.espn.com" in u for u in SEEN if "espn" in u) and "//site.api.espn.com" not in FOOTBALL_SRC)
    check("A7 sport switch: Football active here, Baseball links home",
          page.get_attribute(".sport-switch .sport.active", "href") == "/football/" and page.get_attribute(".sport-switch .sport:not(.active)", "href") == "/")
    check("A8 the baseball page has the same switch, pointing here",
          'class="sport active" href="/"' in BASEBALL_SRC and 'class="sport" href="/football/"' in BASEBALL_SRC)

    labels = page.evaluate("[...document.querySelectorAll('.tab-btn')].map(b => b.firstChild.textContent.trim())")
    check("A9 football's tabs are weeks, not days", labels == ["This Week's Picks", "Last Week's Picks"], str(labels))
    body = page.inner_text("body")
    check("A10 no 'today' / 'yesterday' wording anywhere a visitor can see", not re.search(r"today|yesterday", body, re.I), re.findall(r".{20}(?:today|yesterday).{20}", body, re.I)[:3])
    check("A11 baseball keeps its own day-based labels", "Today's Picks<span" in BASEBALL_SRC and "Yesterday's Picks<span" in BASEBALL_SRC)

    # ---------- B. before kickoff ----------
    st = states(page)
    check("B1 everyone not started", set(st.values()) == {"not_started"}, str(st))
    check("B2 a Sunday-Monday card shows its span on the tab", page.inner_text("#tab-date-today") == "Sun, Sep 20 – Mon, Sep 21", page.inner_text("#tab-date-today"))
    check("B3 Live Drives: collapsed, and says nobody's playing",
          page.evaluate("document.getElementById('drives-section').classList.contains('collapsed')") and "none of your picks are playing" in page.inner_text("#drives-sub"))
    check("B4 pre-game summaries aren't downloaded", count(r"summary\?event=") == 0)
    page.click(".drives-head")

    # ---------- C. live: red zone, has the ball, on defense ----------
    phi_lines = [statline("1", "Saquon Barkley", rushing=(3, 14, 0)), statline("2", "A.J. Brown", receiving=(1, 9, 0, 2))]
    FX["events"].update({"2001": event("2001", "in", 1, "8:41", (0, 0)), "2003": event("2003", "in", 1, "9:00")})
    FX["summaries"]["2001"] = summary("2001", "in", 1, "8:41", (0, 0), {"PHI": phi_lines},
                                      current=drive("d1", "PHI", ball("PHI", 12, "2nd & 7 at TEN 12"), description="6 plays, 63 yards, 3:10"))
    FX["summaries"]["2003"] = summary("2003", "in", 1, "9:00", (0, 0), {})
    poll(page)
    st = states(page)
    check("C1 PHI/TEN picks live; BUF (not playing) and Monday's picks still not started",
          st["Saquon Barkley"] == "live" and st["Tony Pollard"] == "live" and st["Josh Allen"] == "not_started" and st["Puka Nacua"] == "not_started", str(st))
    t = tiles(page)
    kinds = [x["kind"] for x in t]
    check("C2 red-zone offense first, then the picks waiting on their defense",
          {x["player"] for x in t if x["kind"] == "rz"} == {"Saquon Barkley", "A.J. Brown", "Inactive Guy", "Blocking Tight End"}
          and {x["player"] for x in t if x["kind"] == "wait"} == {"Tony Pollard", "James Cook"}
          and kinds == sorted(kinds, key=["rz", "ball", "wait"].index), str([(x["player"], x["kind"], x["tag"]) for x in t]))
    barkley = next(x for x in t if x["player"] == "Saquon Barkley")
    check("C3 tile: down & distance, score/clock, his line so far, whose pick",
          all(s in barkley["text"] for s in ("RED ZONE", "2nd & 7 at TEN 12", "Q1 8:41", "3 car, 14 yds", "Kenny")), barkley["text"])
    check("C4 negative odds survive onto the tile", "-135" in barkley["text"])
    check("C5 the ticket row says it too", "IN THE RED ZONE" in ctx(page, "Saquon Barkley") and "1 rec, 9 yds (2 tgt)" in ctx(page, "A.J. Brown"), ctx(page, "A.J. Brown"))
    check("C6 defense tiles are tagged", {x["tag"] for x in t if x["kind"] == "wait"} == {"ON DEFENSE"}, str({x["tag"] for x in t}))
    check("C7 header counts", page.inner_text("#drives-count") == "4 RED ZONE · 6 LIVE", page.inner_text("#drives-count"))
    check("C8 nothing announced yet", overlay(page) is None)

    # ---------- D. touchdown ----------
    phi_lines = [statline("1", "Saquon Barkley", rushing=(4, 26, 1)), statline("2", "A.J. Brown", receiving=(1, 9, 0, 2)),
                 statline("50", "Jalen Hurts", passing=(0,))]
    td1 = scoring("p1", "PHI", "Rushing Touchdown", "Saquon Barkley 12 Yd Rush (Jake Elliott Kick)", 1, "7:58", 7, 0)
    FX["events"]["2001"] = event("2001", "in", 1, "7:58", (7, 0))
    FX["summaries"]["2001"] = summary("2001", "in", 1, "7:58", (7, 0), {"PHI": phi_lines}, [td1],
                                      current=drive("d2", "PHI", ball("TEN", 75, "1st & 10 at TEN 25"), description="0 plays, 0 yards, 0:00"),
                                      previous=[drive("d1", "PHI", result="Touchdown", is_score=True, ended=NOW, scoring_text=td1["text"])])
    poll(page)
    check("D1 the scorer's leg is a hit", states(page)["Saquon Barkley"] == "hit")
    o = overlay(page)
    check("D2 touchdown alert, in football's words", o and o["text"] == "Saquon Barkley TOUCHDOWN!", str(o))
    check("D3 ...and his single cashed, so it's the money version", o and o["cash"] and o["cash"].endswith("SINGLE CASHED$8.70"), str(o))
    t = tiles(page)
    barkley = next(x for x in t if x["player"] == "Saquon Barkley")
    check("D4 his tile becomes a football, at the front with the other results",
          barkley["ball"] and barkley["tag"] == "TOUCHDOWN" and t[0]["kind"] == "result", str(barkley))
    brown = next(x for x in t if x["player"] == "A.J. Brown")
    check("D5 his teammate's tile says the score wasn't his", brown["kind"] == "result" and brown["result"] == "TD — not him", str(brown))
    check("D6 possession follows the LAST PLAY, not the stale 'current drive' team: TEN has it now",
          {x["player"]: x["kind"] for x in t if x["player"] in ("Tony Pollard", "James Cook")} == {"Tony Pollard": "ball", "James Cook": "ball"}, str(t))
    check("D7 the ticket row describes the touchdown", "12-yd rush" in ctx(page, "Saquon Barkley") and "Q1 7:58" in ctx(page, "Saquon Barkley"), ctx(page, "Saquon Barkley"))
    page.click(".tdlog-head")
    rows = page.evaluate("[...document.querySelectorAll('#tdlog-list .hr-row')].map(r => [r.querySelector('.hr-batter').textContent, r.classList.contains('ours'), r.querySelector('.hr-dist').textContent, r.querySelector('.hr-ev').textContent])")
    check("D8 Touchdown Log", rows == [["Saquon Barkley", True, "12 yd", "RUSH"]], str(rows))
    advance(page, 4)
    barkley = next(x for x in tiles(page) if x["player"] == "Saquon Barkley")
    check("D9 football gives way to the result", not barkley["ball"] and barkley["result"] == "Touchdown" and "12-yd rush" in barkley["text"], barkley["text"])
    advance(page, 11)
    check("D10 then the tile drops: he's cashed, nothing left to watch", "Saquon Barkley" not in [x["player"] for x in tiles(page)])
    check("D11 throwing a touchdown never cashes the passer", page.evaluate("RESULTS.hitIds.has('50')") is False)

    # ---------- E. a punt; and the other Josh Allen ----------
    page.evaluate("dismissBomb()")
    jax_lines = [statline("99", "Josh Allen", interceptions=(1, 40, 1))]
    td2 = scoring("p2", "JAX", "Interception Return Touchdown", "Josh Allen 40 Yd Interception Return (Cam Little Kick)", 1, "3:10", 7, 0)
    FX["events"]["2002"] = event("2002", "in", 1, "3:10", (7, 0))
    FX["summaries"]["2002"] = summary("2002", "in", 1, "3:10", (7, 0), {"JAX": jax_lines}, [td2])
    FX["summaries"]["2001"] = summary("2001", "in", 1, "5:02", (7, 0), {"PHI": phi_lines, "TEN": [statline("3", "Tony Pollard", rushing=(2, 5, 0))]}, [td1],
                                      current=drive("d3", "PHI", ball("PHI", 70, "1st & 10 at PHI 30")),
                                      previous=[drive("d1", "PHI", result="Touchdown", is_score=True, ended=NOW, scoring_text=td1["text"]),
                                                drive("d2", "TEN", result="Punt", ended=FX["clock"], description="3 plays, 4 yards, 1:48")])
    poll(page)
    t = tiles(page)
    punts = {x["player"]: x["result"] for x in t if x["kind"] == "result"}
    check("E1 a drive that ends shows its result on that offense's picks", punts == {"Tony Pollard": "Punt", "James Cook": "Punt"}, str(punts))
    check("E2 the linebacker's pick-six does NOT cash the quarterback (same name, different team, different id)",
          states(page)["Josh Allen"] == "not_started" and overlay(page) is None, str(states(page)))
    page.click("#tdlog-btn-all")
    rows = page.evaluate("[...document.querySelectorAll('#tdlog-list .hr-row')].map(r => [r.querySelector('.hr-batter').textContent, r.classList.contains('ours')])")
    check("E3 ...it's in All Touchdowns, just not marked as ours", ["Josh Allen", False] in rows and ["Saquon Barkley", True] in rows, str(rows))
    page.click("#tdlog-btn-picks")

    # ---------- F. matching: by id across a suffix, and by name when the card had no id ----------
    advance(page, 10)
    ten_lines = [statline("3", "Tony Pollard", rushing=(9, 41, 1)), statline("4", "James Cook III", receiving=(3, 30, 1, 4))]
    td3 = scoring("p3", "TEN", "Rushing Touchdown", "Tony Pollard 3 Yd Rush (Kick)", 2, "9:00", 7, 7)
    td4 = scoring("p4", "TEN", "Passing Touchdown", "James Cook III 18 Yd pass from Cam Ward (Kick)", 2, "1:12", 7, 14)
    FX["summaries"]["2001"] = summary("2001", "in", 2, "1:12", (7, 14), {"PHI": phi_lines, "TEN": ten_lines}, [td1, td3, td4],
                                      current=drive("d6", "PHI", ball("PHI", 75, "1st & 10 at PHI 25")))
    poll(page)
    st = states(page)
    check("F1 'James Cook' on the card is ESPN's 'James Cook III' -- matched by athlete id", st["James Cook"] == "hit", str(st))
    check("F2 a pick the card couldn't resolve to an id still hits, by name + team", st["Tony Pollard"] == "hit", str(st))
    check("F3 two alerts queue", overlay(page) is not None and page.evaluate("BOMB_QUEUE.length") == 1)
    check("F4 Card 1 is now an Iron on A.J. Brown", next(x for x in tiles(page) if x["player"] == "A.J. Brown")["iron"])
    page.evaluate("dismissBomb()")

    # ---------- G. background games aren't hammered ----------
    before = count(r"summary\?event=2003")
    poll(page); poll(page)
    check("G1 a live game none of our picks are in is refreshed about once a minute, not every poll",
          count(r"summary\?event=2003") == before and count(r"summary\?event=2001") >= 5, f"{before} -> {count(r'summary.event=2003')}")
    advance(page, 61)
    poll(page)
    check("G2 ...but it IS refreshed once that minute is up", count(r"summary\?event=2003") == before + 1)

    # ---------- H. finals: miss, void, and the long way to 'done' ----------
    for g, sc in (("2001", (7, 14)), ("2002", (7, 0)), ("2003", (0, 0))):
        FX["events"][g] = event(g, "post", 4, "0:00", sc)
    FX["summaries"]["2001"] = summary("2001", "post", 4, "0:00", (7, 14), {"PHI": phi_lines, "TEN": ten_lines}, [td1, td3, td4])
    FX["summaries"]["2002"] = summary("2002", "post", 4, "0:00", (7, 0), {"JAX": jax_lines}, [td2])
    FX["summaries"]["2003"] = summary("2003", "post", 4, "0:00", (0, 0), {})
    FX["rosters"]["2001-21"] = {"entries": [{"playerId": 6, "didNotPlay": True}, {"playerId": 7, "didNotPlay": False}, {"playerId": 2, "didNotPlay": False}]}
    poll(page)
    st = states(page)
    check("H1 played, never scored -> miss", st["A.J. Brown"] == "miss", str(st))
    check("H2 inactive (roster says didNotPlay) -> void, not a loss", st["Inactive Guy"] == "na", str(st))
    check("H3 dressed and played but never touched the ball -> a real miss", st["Blocking Tight End"] == "miss", str(st))
    check("H4 the roster is only asked about the team that needs it", count(r"competitors/21/roster") == 1 and count(r"competitors/10/roster") == 0)
    check("H5 final stat line on the ticket", "Final line: 1 rec, 9 yds (2 tgt)" in ctx(page, "A.J. Brown"), ctx(page, "A.J. Brown"))
    check("H6 Sunday's over but Monday night isn't: the slate stays on Today",
          page.is_visible("#content") and not page.is_visible("#waiting-panel") and st["Puka Nacua"] == "not_started")
    page.evaluate("toggleBettorSection()")
    kenny = page.evaluate("[...document.querySelectorAll('#bettor-list .bettor-row')].find(r => r.textContent.includes('Kenny')).textContent.replace(/\\s+/g, ' ')")
    check("H7 Bettor Tracker signs negative odds properly (never '+-135')", "-135" in kenny and "+-" not in kenny, kenny)

    # ---------- I. Monday night ends it ----------
    FX["clock"] = datetime(2026, 9, 22, 3, 30, tzinfo=timezone.utc)   # 11:30 PM ET Monday
    page.clock.set_fixed_time(FX["clock"])
    FX["events"]["2004"] = event("2004", "post", 4, "0:00", (24, 10))
    FX["summaries"]["2004"] = summary("2004", "post", 4, "0:00", (24, 10),
                                      {"LAR": [statline("8", "Puka Nacua", receiving=(7, 101, 1, 9)), statline("9", "Kyren Williams", rushing=(18, 77, 0))]},
                                      [scoring("p9", "LAR", "Passing Touchdown", "Puka Nacua 22 Yd pass from Matthew Stafford (Kick)", 3, "4:00", 17, 3)])
    poll(page)
    check("I1 the week's last game is final -> This Week goes back to waiting",
          page.is_visible("#waiting-panel") and page.inner_text("#waiting-title") == "Waiting for this week's picks", page.inner_text("#waiting-title"))
    page.click("#tab-btn-yesterday")
    st = states(page)
    check("I2 the finished week is on the Last Week tab, fully graded",
          st["Puka Nacua"] == "hit" and st["Kyren Williams"] == "miss" and st["Josh Allen"] == "na", str(st))
    check("I3 no Live Drives on a finished slate", not page.evaluate("document.getElementById('drives-section').getClientRects().length > 0"))
    check("I4 sync line", page.inner_text("#sync-line").startswith("Final results for Sun, Sep 20"), page.inner_text("#sync-line"))
    check("I5 no script errors", not errors, str(errors))
    check("I6 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
    check("I7 Last Week's header", "LAST WEEK'S SLATE" in page.inner_text("#eyebrow-text"), page.inner_text("#eyebrow-text"))

    # ---------- J. a SUNDAY-ONLY card still holds This Week until Monday night ends ----------
    # The rule is the week's last game, not the card's: nobody here is playing Monday.
    TICKETS["windows"][0]["tickets"] = [TICKETS["windows"][0]["tickets"][0]]          # A.J. Brown + Tony Pollard, both Sunday
    TICKETS["singles"] = TICKETS["singles"][:1]                                        # Saquon Barkley
    FX["clock"] = datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc)                    # 12:30 AM ET Monday: Sunday is over
    page.clock.set_fixed_time(FX["clock"])
    FX["events"]["2004"] = event("2004", "pre")
    SEEN.clear()
    page.reload()
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    st = states(page)
    check("J1 every pick is already graded...", st == {"A.J. Brown": "miss", "Tony Pollard": "hit", "Saquon Barkley": "hit"}, str(st))
    check("J2 ...but the card stays on This Week's Picks: Monday night hasn't been played",
          page.is_visible("#content") and not page.is_visible("#waiting-panel") and "THIS WEEK'S SLATE" in page.inner_text("#eyebrow-text"))
    page.click("#tab-btn-yesterday")
    check("J3 and Last Week is still empty", page.inner_text("#waiting-title") == "No picks submitted last week", page.inner_text("#waiting-title"))
    page.click("#tab-btn-today")
    poll(page); poll(page)
    check("J4 Sunday's schedule is settled, so it's asked for once -- only Monday's is re-polled",
          count(r"scoreboard\?dates=20260920") == 1 and count(r"scoreboard\?dates=20260921") >= 3,
          f"{count(r'dates=20260920')} / {count(r'dates=20260921')}")
    FX["clock"] = datetime(2026, 9, 22, 3, 30, tzinfo=timezone.utc)                    # 11:30 PM ET Monday
    page.clock.set_fixed_time(FX["clock"])
    FX["events"]["2004"] = event("2004", "post", 4, "0:00", (24, 10))
    poll(page)
    check("J5 Monday night goes final -> the card moves to Last Week's Picks",
          page.is_visible("#waiting-panel") and page.inner_text("#waiting-title") == "Waiting for this week's picks")
    page.click("#tab-btn-yesterday")
    check("J6 ...where it's fully graded", states(page) == {"A.J. Brown": "miss", "Tony Pollard": "hit", "Saquon Barkley": "hit"}, str(states(page)))
    check("J7 no script errors", not errors, str(errors))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all football checks passed")
