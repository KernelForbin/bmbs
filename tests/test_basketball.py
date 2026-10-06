"""
basketball/index.html -- the NBA tracker (HIDDEN: complete, not in any other
page's sport switch) -- and basketball legs on the All Sports front page
(index.html).

Everything is served from in-memory fixtures through one route handler: the
tickets files, ESPN's NBA scoreboard and summary, and (for the All Sports
page) the MLB schedule. Anything else is aborted, so a missed host fails
loudly instead of reaching the internet. The fixture's shapes -- boxscore
labels, `active` / `didNotPlay`, play participants and their order -- are
copied from a real ESPN summary (UTAH @ DEN, 2026-10-04).

    python tests/test_basketball.py

The WNBA runs on the same engine and is held to exactly the same checks:
tests/test_wnba.py runs THIS file with HOOPS_LEAGUE=wnba, which swaps the
page, its data and ESPN paths, its storage namespace and the legs' sport.
"""
import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
LEAGUE = os.environ.get("HOOPS_LEAGUE", "nba")
# league -> (page path, data dir, page title word, storage prefix, the OTHER basketball league)
CONF = {"nba": ("/basketball/", "basketball", "NBA", "bmbs.bb.", "wnba"),
        "wnba": ("/wnba/", "wnba", "WNBA", "bmbs.wb.", "nba")}[LEAGUE]
PAGE, DATA, WORD, KEYS, OTHER = CONF
PAGES = {PAGE: REPO / PAGE.strip("/") / "index.html", "/all/": REPO / "index.html"}
DAY = "2026-10-05"
NOW = "2026-10-06T01:50:00Z"          # 9:50 PM ET

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{ascii(detail)}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- ESPN NBA fixtures ----------------
TEAM = {"UTAH": "26", "DEN": "7"}
LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def player(aid, name, pts=0, reb=0, ast=0, threes=0, stl=0, blk=0, pf=0, mins=20, active=False, dnp=False):
    row = {"MIN": str(mins), "PTS": str(pts), "FG": "0-0", "3PT": f"{threes}-{threes + 2}", "FT": "0-0", "REB": str(reb),
           "AST": str(ast), "TO": "0", "STL": str(stl), "BLK": str(blk), "OREB": "0", "DREB": str(reb), "PF": str(pf), "+/-": "0"}
    return {"athlete": {"id": aid, "displayName": name}, "active": active, "starter": not dnp, "didNotPlay": dnp,
            "reason": "COACH'S DECISION" if dnp else "", "ejected": False, "stats": [] if dnp else [row[k] for k in LABELS]}


def event(state, score, period=3, clock="6:00", name=None):
    away, home = score
    return {"id": "8101", "date": "2026-10-06T01:00Z",
            "status": {"period": period, "displayClock": clock,
                       "type": {"state": state, "name": name or {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS",
                                                                "post": "STATUS_FINAL"}[state],
                                "shortDetail": {"pre": "9:00 PM", "in": f"{clock} - 3rd", "post": "Final"}[state]}},
            "competitions": [{"competitors": [
                {"id": TEAM["UTAH"], "homeAway": "away", "score": str(away), "team": {"abbreviation": "UTAH"}},
                {"id": TEAM["DEN"], "homeAway": "home", "score": str(home), "team": {"abbreviation": "DEN"}}]}]}


def play(team, parts, wall, value=0, kind="Jump Shot", text="", period=3):
    return {"type": {"text": kind}, "team": {"id": TEAM[team]}, "period": {"number": period},
            "clock": {"displayValue": "5:00"}, "wallclock": wall, "scoringPlay": value > 0, "scoreValue": value,
            "text": text or kind, "participants": [{"athlete": {"id": a}} for a in parts]}


def summary(state, score, den, utah, plays, period=3, clock="6:00", name=None):
    ev = event(state, score, period, clock, name)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    side = lambda abbr, rows: {"team": {"abbreviation": abbr}, "statistics": [{"names": LABELS, "labels": LABELS, "athletes": rows}]}
    return {"header": {"competitions": [comp]},
            "boxscore": {"players": [side("UTAH", utah), side("DEN", den)]},
            "plays": plays}


def leg(i, name, team, market, line=None, side=None, aid="", who="Kenny", odds="+200"):
    l = {"id": f"B{i}", "player": name, "team": team, "who": who, "meta": team, "odds": odds, "time": "",
         "sport": LEAGUE, "market": market}
    if line is not None:
        l["line"] = line
    if side:
        l["side"] = side
    if aid:
        l["athleteId"] = aid
    return l


def card(n, legs, payout=100.0):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5.00</b> bet", "stake": 5.0,
            "book": "Kenny", "payout": payout, "legs": legs}


HOOPS = {"date": DAY, "endDate": DAY, "note": "", "sports": [LEAGUE], "windows": [{"title": "Parlay Cards", "tickets": [
    card(1, [leg(1, "Nikola Jokic", "DEN", "nba_points", 25.5, "over", aid="1"),
             leg(2, "Nikola Jokic", "DEN", "nba_td", aid="1")]),
    card(2, [leg(3, "Jamal Murray", "DEN", "nba_threes", 2.5, "over", aid="2"),
             leg(4, "Lauri Markkanen", "UTAH", "nba_pra", 30.5, "over", aid="4")]),
    card(3, [leg(5, "Denver Nuggets", "DEN", "nba_spread", -5.5),
             dict(leg(6, "Utah Jazz / Denver Nuggets", "UTAH", "nba_total", 220.5, "over"), opponent="DEN", teams=["UTAH", "DEN"])]),
    card(4, [leg(7, "Bench Guy", "DEN", "nba_points", 9.5, "over", aid="9"),
             leg(8, "Aaron Gordon", "DEN", "nba_rebounds", 5.5, "over", aid="3")]),
]}], "singles": [dict(leg(9, "Denver Nuggets", "DEN", "nba_ml"), id="single-0", stake=5.0, payout=8.00),
               dict(leg(10, "Denver Nuggets", "DEN", "nba_spread", -9.5), id="single-1", stake=5.0, payout=9.50)]}

# Jokic's 23 points, play by play: eleven twos and a free throw.
JOKIC_PLAYS = [play("DEN", ["1"], f"2026-10-06T01:{10 + i:02d}:00Z", 2, text="Nikola Jokic makes layup") for i in range(11)] + \
              [play("DEN", ["1"], "2026-10-06T01:22:00Z", 1, "Free Throw - 1 of 1", "Nikola Jokic makes free throw 1 of 1")]
LIVE_PLAYS = JOKIC_PLAYS + [
    play("DEN", ["2"], "2026-10-06T01:05:00Z", 3, text="Jamal Murray makes 24-foot three point jumper"),
    play("DEN", ["2"], "2026-10-06T01:08:00Z", 3, text="Jamal Murray makes 25-foot three point jumper"),
    play("DEN", ["2"], "2026-10-06T01:24:00Z", 3, text="Jamal Murray makes 26-foot three point jumper (Nikola Jokic assists)"),
    play("DEN", ["3"], "2026-10-06T01:25:00Z", 0, "Defensive Rebound", "Aaron Gordon defensive rebound"),
    play("UTAH", ["4", "1"], "2026-10-06T01:26:00Z", 0, "Lost Ball Turnover", "Lauri Markkanen lost ball turnover (Nikola Jokic steals)"),
]


def den_live(active=("1", "3"), jokic_pts=23):
    return [player("1", "Nikola Jokic", pts=jokic_pts, reb=9, ast=8, pf=4, mins=26, active="1" in active),
            player("2", "Jamal Murray", pts=15, threes=3, mins=24, active="2" in active),
            player("3", "Aaron Gordon", pts=8, reb=4, mins=22, active="3" in active),
            player("9", "Bench Guy", dnp=True)]


def utah_live(active=("5",)):
    return [player("4", "Lauri Markkanen", pts=18, reb=6, ast=3, mins=25, active="4" in active),
            player("5", "Keyonte George", pts=12, mins=24, active="5" in active)]


def live_summary(active_den=("1", "3"), active_utah=("5",)):
    return summary("in", (70, 80), den_live(active_den), utah_live(active_utah), LIVE_PLAYS)


FX = {"tickets": HOOPS, "summary": live_summary(), "event": event("in", (70, 80)), "mlb_sched": {"dates": []},
      "all_tickets": None}
SEEN = []


def handler(route, request):
    url = request.url
    SEEN.append(url)
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    for prefix, page in PAGES.items():
        if path in (prefix, prefix + "index.html"):
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page.read_bytes())
    if path.endswith("tickets-previous.json"):
        return route.fulfill(status=404, body="")
    if path.endswith(f"/data/{DATA}/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["tickets"]))
    if path.endswith("/data/combined/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["all_tickets"]))
    if f"/basketball/{LEAGUE}/scoreboard" in url:
        evs = [FX["event"]] if DAY.replace("-", "") in url else []
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    if f"/basketball/{LEAGUE}/summary" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["summary"]))
    # The OTHER basketball league, only when section G gives it a game.
    if FX.get("other_event") and f"/basketball/{OTHER}/scoreboard" in url:
        evs = [FX["other_event"]] if DAY.replace("-", "") in url else []
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    if FX.get("other_summary") and f"/basketball/{OTHER}/summary" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["other_summary"]))
    if "statsapi.mlb.com" in url and "/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["mlb_sched"]))
    if "fonts.g" in url:
        return route.fulfill(status=200, content_type="text/css", body="")
    return route.abort()


def poll(page):
    page.evaluate("SLATE_POLLS.clear(); pollAndRender()")
    page.wait_for_timeout(300)


def open_page(p, path, at=NOW):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(at)
    page.route("**/*", handler)
    page.goto(f"http://bmbs.test{path}")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    return browser, page, errors


def leg_states(page):
    return page.evaluate("""() => Object.assign(
        Object.fromEntries(EVALUATED.flatMap(e => e.tk.legs.map((l, i) => [l.id, e.states[i]]))),
        Object.fromEntries(EVALUATED_SINGLES.map(e => [e.s.id, e.state])))""")


def tiles(page):
    return page.eval_on_selector_all("#liveab-grid .ab-tile", """els => els.map(e => ({
        name: e.querySelector('.ab-name').textContent.trim(), tag: e.querySelector('.ab-tag').textContent.trim(),
        covering: e.classList.contains('covering'),
        text: e.textContent.replace(/\\s+/g, ' ').replace(/[^\\x00-\\x7F]/g, '')}))""")


with sync_playwright() as p:
    # ================= A. the hidden page itself =================
    browser, page, errors = open_page(p, PAGE)
    check(f"A1 the {WORD} page loads as the {WORD} tracker", page.inner_text("h1") == f"BMBS Tracker — {WORD}", page.inner_text("h1"))
    sw = page.eval_on_selector_all(".sport-switch .sport", "els => els.map(e => [e.textContent.trim(), e.classList.contains('active')])")
    check(f"A2 its own switch marks {WORD} active", any(n.endswith(WORD) and on for n, on in sw), sw)
    hidden = {path: f'href="{PAGE}"' in (REPO / path).read_text(encoding="utf-8")
              for path in [p.relative_to(REPO).as_posix() for p in REPO.rglob("*.html")
                           if not any(s.startswith(".") or s == "tests" for s in p.relative_to(REPO).parts)
                           and p.relative_to(REPO).as_posix() != PAGE.strip("/") + "/index.html"]}
    check("A3 ...and it is HIDDEN: no other page links to it", len(hidden) >= 11 and not any(hidden.values()), hidden)
    keys = page.evaluate("""() => [...document.scripts].map(s => s.textContent).join('').match(/"bmbs\\.[a-z]+\\./g) || []""")
    check(f"A4 every storage key it writes is its own ({KEYS}*), never another page's",
          keys and all(k == f'"{KEYS}' for k in keys), sorted(set(keys)))
    check("A5 a basketball-only card never asks MLB for anything -- the baseball engine stands down",
          not [u for u in SEEN if "statsapi.mlb.com" in u], [u for u in SEEN if "statsapi" in u][:2])
    check("A6 ...nor the NHL, the NFL, or the other basketball league",
          not [u for u in SEEN if "/hockey/nhl" in u or "/football/nfl" in u or f"/basketball/{OTHER}/" in u],
          [u for u in SEEN if "/hockey/" in u or "/football/" in u or f"/{OTHER}/" in u][:2])

    # ================= B. grading, live =================
    ls = leg_states(page)
    check("B1 points short of the line are live (23 of 26)", ls.get("B1") == "live", ls)
    check("B2 a triple-double not yet reached is live", ls.get("B2") == "live", ls)
    check("B3 threes clear the moment they pass the line: 3 > 2.5", ls.get("B3") == "hit", ls)
    check("B4 a PRA combo sums the three columns: 18+6+3 = 27, live under 30.5", ls.get("B4") == "live", ls)
    check("B5 a moneyline and a spread wait for the final, however far ahead", ls.get("single-0") == "live" and ls.get("B5") == "live", ls)
    check("B6 a total's over isn't there yet at 150 points", ls.get("B6") == "live", ls)
    check("B7 a man who hasn't played yet is still live mid-game, not void", ls.get("B7") == "live", ls)
    bare = page.evaluate(f"stateForLeg({{sport: '{LEAGUE}', player: 'Nikola Jokic', team: 'DEN', athleteId: '1'}})")
    check("B8 a basketball leg naming NO bet type is untracked -- never guessed as points", bare == "untracked", bare)
    line = page.evaluate("nbaStatusLine(EVALUATED[0].tk.legs[0], 'live')")
    check("B9 the leg's status line: ON THE COURT, foul trouble named, his line so far",
          "ON THE COURT" in line and "4 FOULS" in line and "23 PTS" in line, line)

    # ================= C. the tracker =================
    page.evaluate("if (document.getElementById('liveab-section').classList.contains('collapsed')) toggleLiveAb()")
    page.wait_for_timeout(200)
    tl = {t["name"]: t for t in tiles(page)}
    jok = tl.get("Nikola Jokic", {})
    check("C1 a player ESPN marks active is ON THE COURT", jok.get("tag") == "ON THE COURT", {k: v["tag"] for k, v in tl.items()})
    check("C2 ...with what he's chasing: 23 of 26 points, and 1 of 3 categories for the triple-double",
          "23 of 26 points" in jok.get("text", "") and "1 of 3 categories in double digits" in jok.get("text", ""), jok.get("text"))
    check("C3 ...and his foul trouble", "4 FOULS" in jok.get("text", ""), jok.get("text"))
    mark = tl.get("Lauri Markkanen", {})
    check("C4 a player not marked active is ON THE BENCH, with his combo count: 27 of 31",
          mark.get("tag") == "ON THE BENCH" and "27 of 31 pts+reb+ast" in mark.get("text", ""), mark)
    # A WNBA team tile is filed apart ("WNBA NY"), or a Liberty bet and a
    # Knicks bet -- both "NY" -- would share one tile on the All Sports wall.
    den = tl.get(("WNBA " if LEAGUE == "wnba" else "") + "DEN", {})
    check("C5 a team's bets share ONE tile listing each -- the Nuggets' moneyline AND spread",
          den.get("tag") == "TEAM BETS" and "MONEYLINE" in den.get("text", "") and "SPREAD -5.5: covering by 4.5" in den.get("text", ""), den)
    check("C5b ...and a spread that is covering right now wears the dotted green outline", den.get("covering") is True, den)
    # The bet card counts toward the bet too, as the tile does.
    page.evaluate("toggleCardsSection('parlays'); toggleCardsSection('singles')")
    page.wait_for_timeout(200)
    rows = page.evaluate(r"""() => Object.fromEntries([...document.querySelectorAll('.leg, .single-row')].map(e => [
        (e.querySelector('.leg-player, .single-player').childNodes[0].textContent || '').trim(), e.textContent.replace(/\s+/g, ' ')]))""")
    page.evaluate("toggleCardsSection('parlays'); toggleCardsSection('singles')")
    check("C5c a basketball prop's card line counts toward the bet: 27 of 31 pts+reb+ast",
          "27 of 31 pts+reb+ast" in rows.get("Lauri Markkanen", ""), rows.get("Lauri Markkanen"))
    check("C6 ...and a game total gets its own, with the points so far", "150 pts, needs 221" in
          next((t["text"] for t in tl.values() if t["tag"] == "TOTAL POINTS"), ""), [t["tag"] for t in tl.values()])
    raw = [(n, t["text"]) for n, t in tl.items() if re.search(r"&(?:[a-z]+|#\d+);", t["text"])]
    check("C6b no tile shows a raw HTML entity as text", not raw, raw)
    check("C7 a hit leg's man with nothing else live gets no tile (Murray's threes are in)", "Jamal Murray" not in tl, list(tl))
    # `active` is the one field not yet seen in a LIVE game. When nobody is
    # marked active, the page says only what it knows: LIVE.
    FX["summary"] = live_summary(active_den=(), active_utah=())
    poll(page)
    tl2 = {t["name"]: t for t in tiles(page)}
    check("C8 nobody marked active: a player's tile is plain LIVE, never ON THE COURT or ON THE BENCH",
          tl2.get("Nikola Jokic", {}).get("tag") == "LIVE" and tl2.get("Lauri Markkanen", {}).get("tag") == "LIVE",
          {k: v.get("tag") for k, v in tl2.items()})
    FX["summary"] = summary("in", (70, 80), den_live(), utah_live(), LIVE_PLAYS, name="STATUS_HALFTIME")
    poll(page)
    tl3 = {t["name"]: t for t in tiles(page)}
    check("C9 at halftime nobody is ON THE COURT", tl3.get("Nikola Jokic", {}).get("tag") == "HALFTIME",
          {k: v.get("tag") for k, v in tl3.items()})
    half = page.evaluate("nbaStatusLine(EVALUATED[0].tk.legs[0], 'live')")
    check("C9b ...not on the tile, and not in the leg's status line either", "ON THE COURT" not in half and "Halftime" in half, half)
    FX["summary"] = live_summary()
    poll(page)
    check("C10 no JS errors", not errors, errors[:3])

    # ================= D. a hit: state, alert, bell =================
    page.evaluate("""() => { window.FIRED = []; const real = fireLegHit;
        fireLegHit = (slate, leg, cash) => { FIRED.push(legAlertName(leg) + ' ' + legHitWord(leg)); return real(slate, leg, cash); }; }""")
    three = play("DEN", ["1"], "2026-10-06T01:48:00Z", 3, text="Nikola Jokic makes 25-foot three point jumper")
    FX["summary"] = summary("in", (70, 83), den_live(jokic_pts=26), utah_live(), LIVE_PLAYS + [three])
    FX["event"] = event("in", (70, 83))
    poll(page)
    ls = leg_states(page)
    check("D1 the three takes Jokic to 26: his points over is a hit", ls.get("B1") == "hit", ls)
    fired = page.evaluate("FIRED")
    check("D2 it alerts in the bet's words: 26+ POINTS!", "Nikola Jokic 26+ POINTS!" in fired, fired)
    t = page.evaluate("bellTime(BELL.items.find(i => i.kind === 'leg' && i.who === 'Nikola Jokic' && /Points/.test(i.what)) || {})")
    check("D3 the bell times it off the shot that got him there: 9:48 PM ET", t == "9:48 PM ET", t)
    t = page.evaluate("bellTime(BELL.items.find(i => i.kind === 'leg' && i.who === 'Jamal Murray') || {})")
    check("D4 ...and Murray's threes off his third: 9:24 PM ET", t == "9:24 PM ET", t)
    check("D5 no JS errors", not errors, errors[:3])

    # ================= E. the final =================
    den_final = [player("1", "Nikola Jokic", pts=30, reb=12, ast=10, mins=36),
                 player("2", "Jamal Murray", pts=20, threes=4, mins=34),
                 player("3", "Aaron Gordon", pts=12, reb=5, mins=30),
                 player("9", "Bench Guy", dnp=True)]
    utah_final = [player("4", "Lauri Markkanen", pts=20, reb=7, ast=4, mins=34), player("5", "Keyonte George", pts=20, mins=30)]
    FX["summary"] = summary("post", (104, 112), den_final, utah_final, LIVE_PLAYS + [three], period=4, clock="0:00")
    FX["event"] = event("post", (104, 112), 4, "0:00")
    poll(page)
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("E1 the moneyline settles a win at the final", ls.get("single-0") == "hit", ls)
    check("E2 the spread covers: 112 - 5.5 beats 104", ls.get("B5") == "hit", ls)
    check("E3 the total misses: 216 isn't over 220.5", ls.get("B6") == "miss", ls)
    check("E3b ...and a bigger spread misses: winning by 8 doesn't cover -9.5", ls.get("single-1") == "miss", ls)
    check("E4 the triple-double: 30 / 12 / 10", ls.get("B2") == "hit", ls)
    check("E5 PRA 20+7+4 = 31 clears 30.5", ls.get("B4") == "hit", ls)
    check("E6 rebounds 5 short of 5.5 is a miss", ls.get("B8") == "miss", ls)
    check("E7 a player who never got in (DNP) is VOID, not a miss", ls.get("B7") == "na", ls)
    check("E8 no JS errors", not errors, errors[:3])
    browser.close()

    # ================= F. basketball on the All Sports tab =================
    FX["summary"], FX["event"] = live_summary(), event("in", (70, 80))
    FX["all_tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": [LEAGUE],
                         "windows": [{"title": "Parlay Cards", "tickets": [
                             card(10, [leg(1, "Nikola Jokic", "DEN", "nba_points", 25.5, "over", aid="1"),
                                       leg(3, "Jamal Murray", "DEN", "nba_threes", 2.5, "over", aid="2")])]}],
                         "singles": []}
    SEEN.clear()
    browser, page, errors = open_page(p, "/all/")
    ls = leg_states(page)
    check("F1 the All Sports tracker grades basketball legs the same way", ls.get("B1") == "live" and ls.get("B3") == "hit", ls)
    check(f"F2 ...and its switch has no {WORD} tab (hidden)", "NBA" not in page.inner_text(".sport-switch"),
          page.inner_text(".sport-switch"))
    check("F3 a basketball-only card never asks MLB for anything", not [u for u in SEEN if "statsapi" in u])
    check("F4 no JS errors", not errors, errors[:3])
    browser.close()

    # ================= G. both leagues on one card =================
    # The same team code ("DEN") and the same player in BOTH leagues: this
    # league's game live, the other's final. Each leg must be graded by its
    # own league's engine, and the team tiles must not merge.
    FX["summary"], FX["event"] = live_summary(), event("in", (70, 80))
    FX["other_event"] = event("post", (60, 90), 4, "0:00")
    FX["other_summary"] = summary("post", (60, 90), [player("1", "Nikola Jokic", pts=40, reb=10, ast=9, mins=36)],
                                  utah_live(()), [], period=4, clock="0:00")
    other_leg = lambda i, *a, **k: dict(leg(i, *a, **k), sport=OTHER)
    FX["all_tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": sorted([LEAGUE, OTHER]),
                         "windows": [{"title": "Parlay Cards", "tickets": [
                             card(20, [leg(1, "Nikola Jokic", "DEN", "nba_points", 25.5, "over", aid="1"),
                                       other_leg(2, "Nikola Jokic", "DEN", "nba_points", 25.5, "over", aid="1")]),
                             card(21, [leg(5, "Denver", "DEN", "nba_spread", -5.5),
                                       other_leg(6, "Denver", "DEN", "nba_spread", -5.5)])]}],
                         "singles": []}
    SEEN.clear()
    browser, page, errors = open_page(p, "/all/")
    ls = leg_states(page)
    check(f"G1 one player, two leagues: the {WORD} leg is live at 23, the {OTHER.upper()} one HIT off its own final (40)",
          ls.get("B1") == "live" and ls.get("B2") == "hit", ls)
    check("G2 one team code, two leagues: this league's spread waits for its game, the other's settled off its final",
          ls.get("B5") == "live" and ls.get("B6") == "hit", ls)
    check("G2b no JS errors", not errors, errors[:3])
    browser.close()
    # Now both games live (a fresh page: a final game is cached for good):
    # two "DEN" teams on one wall, two tiles.
    FX["other_event"] = event("in", (60, 90))
    FX["other_summary"] = summary("in", (60, 90), [player("1", "Nikola Jokic", pts=20, active=True)], utah_live(()), [])
    browser, page, errors = open_page(p, "/all/")
    page.evaluate("if (document.getElementById('liveab-section').classList.contains('collapsed')) toggleLiveAb()")
    page.wait_for_timeout(200)
    names = [t["name"] for t in tiles(page)]
    # "DEN" names a team in both leagues on this card, so the NBA's tile also
    # carries the sport label every shared name gets (2026-10-05).
    check("G3 a Denver bet in each league gets its OWN tile -- 'DEN' (labelled NBA) and 'WNBA DEN', never one merged tile",
          names.count("DEN NBA") == 1 and names.count("WNBA DEN") == 1, names)
    check("G4 no JS errors", not errors, errors[:3])
    browser.close()
    FX["other_event"] = FX["other_summary"] = None

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print(f"all basketball checks passed ({WORD})")
