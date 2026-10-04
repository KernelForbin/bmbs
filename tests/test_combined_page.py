"""
all/index.html -- the combined MLB + NFL tracker.

Everything the page fetches is served from in-memory fixtures through one
route handler: its own tickets files, the MLB schedule and live feed, and
ESPN's scoreboard / summary / roster. Nothing under data/ is read, and no
real request leaves the machine (`route.abort()` on anything unexpected, so
a missed host fails loudly instead of silently reaching the internet).

    pip install -r tests/requirements.txt
    python -m playwright install chromium
    python tests/test_combined_page.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "all" / "index.html"

DAY = "2026-10-04"
NEXT = "2026-10-05"
NOW = "2026-10-04T20:00:00Z"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- MLB fixtures ----------------

def mlb_schedule(date, games):
    out = []
    for pk, abstract, teams in games:
        coded = {"Final": "F", "Live": "I", "Preview": "S"}[abstract]
        out.append({"gamePk": pk,
                    "status": {"abstractGameState": abstract, "detailedState": abstract,
                               "codedGameState": coded, "reason": ""},
                    "teams": {"away": {"team": {"abbreviation": teams[0]}},
                              "home": {"team": {"abbreviation": teams[1]}}}})
    return {"dates": [{"date": date, "games": out}]}


def mlb_feed(abstract, roster, hrs=()):
    sides = {"away": {}, "home": {}}
    for i, n in enumerate(roster):
        side = "away" if i < 9 else "home"
        sides[side][f"ID{i}"] = {"person": {"fullName": n}, "battingOrder": f"{(i % 9) + 1}00",
                                 "stats": {"batting": {"plateAppearances": 3}}}
    return {"gameData": {"status": {"abstractGameState": abstract,
                                    "codedGameState": {"Final": "F", "Live": "I"}[abstract]}},
            "liveData": {"plays": {"allPlays": [
                {"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
                 "matchup": {"batter": {"fullName": n}}} for n in hrs]},
                "boxscore": {"teams": {"away": {"players": sides["away"]},
                                       "home": {"players": sides["home"]}}},
                "linescore": {}}}


# ---------------- ESPN fixtures ----------------
TEAM_ID = {"PHI": "21", "NYG": "19", "KC": "12", "DEN": "7"}
NFL_GAMES = {"9001": ("PHI", "NYG"), "9002": ("KC", "DEN")}
LABELS = {"rushing": ["CAR", "YDS", "AVG", "TD", "LONG"],
          "receiving": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"]}


def espn_event(gid, state, score=(0, 0)):
    away, home = NFL_GAMES[gid]
    return {"id": gid, "date": f"{DAY}T17:00Z",
            "status": {"period": 4, "displayClock": "2:00",
                       "type": {"state": state,
                                "shortDetail": {"pre": "1:00 PM", "in": "2:00 - Q4", "post": "Final"}[state]}},
            "competitions": [{"competitors": [
                {"id": TEAM_ID[away], "homeAway": "away", "score": str(score[0]),
                 "team": {"abbreviation": away}},
                {"id": TEAM_ID[home], "homeAway": "home", "score": str(score[1]),
                 "team": {"abbreviation": home}}]}]}


def espn_summary(gid, state, lines, score=(0, 0)):
    ev = espn_event(gid, state, score)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    players = []
    for team, rows in lines.items():
        cats = {}
        for aid, name, cat, vals in rows:
            stats = ([str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0"] if cat == "rushing"
                     else [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0", str(vals[3])])
            cats.setdefault(cat, []).append(
                {"athlete": {"id": aid, "displayName": name}, "stats": stats})
        players.append({"team": {"abbreviation": team},
                        "statistics": [{"name": c, "labels": LABELS[c], "athletes": a}
                                       for c, a in cats.items()]})
    return {"header": {"competitions": [comp]},
            "boxscore": {"players": players}, "scoringPlays": [], "drives": {"previous": []}}


# ---------------- the slate ----------------

def mlb_leg(i, player, team, who="Memo", odds="+390"):
    return {"id": f"L{i}", "player": player, "team": team, "who": who,
            "meta": f"{team} &middot; {who}", "odds": odds, "time": "7:10 PM ET", "sport": "mlb"}


def nfl_leg(i, player, team, aid, who="Kenny", odds="-135", market="td"):
    return {"id": f"L{i}", "player": player, "team": team, "athleteId": aid, "who": who,
            "meta": f"{team} &middot; {who}", "odds": odds, "time": "1:00 PM ET",
            "sport": "nfl", "market": market}


def card(n, legs, payout=100.0):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5.00</b> bet by Memo",
            "stake": 5.0, "book": "Memo", "payout": payout, "legs": legs}


TICKETS = {
    "date": DAY, "endDate": DAY, "note": "", "sports": ["mlb", "nfl"],
    "windows": [{"title": "Parlay Cards", "tickets": [
        card(1, [mlb_leg(1, "Aaron Judge", "NYY"), mlb_leg(2, "Juan Soto", "NYM")]),
        card(2, [nfl_leg(3, "Saquon Barkley", "PHI", "1"),
                 nfl_leg(4, "Travis Kelce", "KC", "2")]),
        # The headline: one parlay, both sports. Judge homers and Barkley
        # scores, so this must grade HIT -- which it can only do if each leg
        # was graded off its own league's data.
        card(3, [mlb_leg(5, "Aaron Judge", "NYY"), nfl_leg(6, "Saquon Barkley", "PHI", "1")]),
        # Same shape, but the football leg is a yardage prop nothing can
        # follow. A hit baseball leg must NOT be allowed to cash it.
        card(4, [mlb_leg(7, "Aaron Judge", "NYY"),
                 nfl_leg(8, "Travis Kelce", "KC", "2", market="rec_yds")]),
    ]}],
    # No "market" on this one, on purpose. A market-less leg means the
    # SPORT's default bet -- a home run for baseball, an anytime touchdown
    # for football. Every other NFL leg here names its market, so without
    # this single the dispatch that reads leg.sport is never run, and the
    # fallback it guards against (grading a touchdown off a batting line)
    # would go unnoticed.
    "singles": [{"id": "S1", "who": "KENNY", "player": "Saquon Barkley", "team": "PHI",
                 "athleteId": "1", "meta": "PHI &middot; 1:00 PM ET", "odds": "-135",
                 "stake": 5.0, "payout": 8.70, "pp": "PP $8.70", "sport": "nfl"}],
}

FX = {"tickets": TICKETS, "mlb_sched": {}, "mlb_feeds": {},
      "espn_events": {}, "espn_summaries": {}, "espn_rosters": {}}
SEEN = []


def handler(route, request):
    url = request.url
    SEEN.append(url)
    if url.endswith("/all/") or url.endswith("/all/index.html"):
        return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                             body=PAGE.read_bytes())
    if url.endswith("/data/combined/tickets-previous.json"):
        return route.fulfill(status=404, body="")
    if url.endswith("/data/combined/tickets.json"):
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["tickets"]))
    m = re.search(r"statsapi\.mlb\.com/api/v1/schedule.*?date=(\d{4}-\d{2}-\d{2})", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["mlb_sched"].get(m.group(1), {"dates": []})))
    m = re.search(r"statsapi\.mlb\.com/api/v1\.1/game/(\d+)/feed/live", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["mlb_feeds"][int(m.group(1))]))
    m = re.search(r"site\.web\.api\.espn\.com/.*/scoreboard\?dates=(\d{8})", url)
    if m:
        day = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
        evs = list(FX["espn_events"].values()) if day == DAY else []
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"events": evs}))
    m = re.search(r"site\.web\.api\.espn\.com/.*/summary\?event=(\d+)", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["espn_summaries"][m.group(1)]))
    m = re.search(r"sports\.core\.api\.espn\.com/.*/competitors/(\d+)/roster", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"entries": []}))
    return route.abort()


def poll(page):
    page.evaluate("pollAndRender()")
    page.wait_for_timeout(250)


def open_page(p, at=NOW):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text)
            if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.clock.set_fixed_time(at)
    page.route("**/*", handler)
    page.goto("http://bmbs.test/all/index.html")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    return browser, page, errors


def states(page):
    """Every ticket's outcome, by card name."""
    return page.evaluate("""() => Object.fromEntries(
        EVALUATED.map(e => [e.tk.name, e.evalRes.outcome]))""")


def leg_states(page):
    return page.evaluate("""() => Object.fromEntries(
        EVALUATED.flatMap(e => e.tk.legs.map((l, i) => [l.id, e.states[i]])))""")


# =====================================================================
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5001, "Final", ["NYY", "BOS"]),
                                          (5002, "Final", ["NYM", "ATL"])])
FX["mlb_feeds"][5001] = mlb_feed("Final", ["Aaron Judge"] + [f"NY{i}" for i in range(8)],
                                 hrs=["Aaron Judge"])
FX["mlb_feeds"][5002] = mlb_feed("Final", ["Juan Soto"] + [f"NM{i}" for i in range(8)])
FX["espn_events"] = {g: espn_event(g, "post", (21, 17)) for g in NFL_GAMES}
# Barkley scored; Kelce caught passes but no touchdown.
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "post", {"PHI": [("1", "Saquon Barkley", "rushing", (18, 92, 1))]}, (21, 17))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "post", {"KC": [("2", "Travis Kelce", "receiving", (6, 71, 0, 8))]}, (21, 17))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    # Every game on this card is final, so the slate has correctly rolled to
    # the finished tab -- that IS the combined rollover rule working. Grade
    # it there, where the results are definitive rather than in-progress.
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(150)

    # ---------- A. the two engines coexist ----------
    # The combined page holds BOTH leagues' code in one document. They define
    # the same names for different jobs -- normalizeName above all, which
    # strips generational suffixes for ESPN and must not for MLB -- so the
    # football half lives in a closure. If that broke, it breaks loudly here.
    check("A1 the page loads with no JavaScript errors", not errors, errors[:3])
    check("A2 the NFL engine is reachable and sealed",
          page.evaluate("typeof NFL === 'object' && typeof NFL.state === 'function'"))
    check("A3 the MLB page's own normalizeName still keeps suffixes "
          "(ESPN strips them; MLB's feed always sends them)",
          page.evaluate("normalizeName('Bobby Witt Jr.')") == "bobby witt jr")
    check("A4 ...while the NFL engine's strips them, in the same document",
          page.evaluate("NFL.norm('Marvin Harrison Jr.')") == "marvin harrison")
    check("A5 it reads its OWN slate, never the MLB or NFL tab's",
          any("/data/combined/tickets.json" in u for u in SEEN)
          and not any(re.search(r"/data/(football/)?tickets", u) for u in SEEN),
          [u for u in SEEN if "tickets" in u])
    check("A6 it polls both leagues",
          any("statsapi.mlb.com" in u for u in SEEN) and any("espn.com" in u for u in SEEN))
    check("A7 every ESPN call uses a browser-permitted host "
          "(site.api.espn.com answers curl with CORS headers and a browser without)",
          all("site.web.api.espn.com" in u or "sports.core.api.espn.com" in u
              for u in SEEN if "espn" in u))

    # ---------- B. each leg grades off its own league ----------
    ls = leg_states(page)
    check("B1 a home run leg grades off the MLB feed", ls["L1"] == "hit", ls)
    check("B2 a baseball leg that didn't homer is a miss", ls["L2"] == "miss", ls)
    check("B3 an anytime-TD leg grades off the ESPN boxscore", ls["L3"] == "hit", ls)
    check("B4 a receiver with no touchdown is a miss", ls["L4"] == "miss", ls)
    check("B5 an NFL market nothing can follow is untracked, never a guess",
          ls["L8"] == "untracked", ls)
    check("B6 an NFL leg naming NO market is an anytime touchdown, "
          "not a home run bet graded off a batting line",
          page.evaluate("EVALUATED_SINGLES.map(e => e.s.id + ':' + e.state).join(',')") == "S1:hit",
          page.evaluate("EVALUATED_SINGLES.map(e => e.s.id + ':' + e.state).join(',')"))

    # ---------- C. one parlay, two sports ----------
    st = states(page)
    check("C1 an all-MLB parlay still grades exactly as it always did",
          st["Card 1"] == "dead", st)
    check("C2 an all-NFL parlay grades", st["Card 2"] == "dead", st)
    check("C3 A MIXED PARLAY CASHES when both sports' legs hit",
          st["Card 3"] == "hit", st)
    check("C4 a mixed parlay carrying an untracked leg is partial -- "
          "a hit baseball leg must not cash a football bet nobody followed",
          st["Card 4"] == "partial", st)

    browser.close()

# ---------- D. the slate is over only when BOTH sports are ----------
# The rule for a combined card is "every game ON THE CARD", so a finished
# baseball slate must not move the card to Yesterday while its football game
# is still being played. Getting this wrong yanks a live card off the tab
# mid-game -- the exact failure a clock-based rollover caused once before.
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in NFL_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0))]}, (14, 10))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    check("D1 MLB final + NFL still playing -> the slate is NOT done",
          page.evaluate("SLATES.today !== null && SLATES.today.done === false"),
          page.evaluate("SLATES.today && SLATES.today.done"))
    check("D2 ...and it is still on the Today tab",
          page.evaluate("ACTIVE_TAB === 'today' && TICKETS !== null"))
    check("D3 no JavaScript errors in the live case either", not errors, errors[:3])
    browser.close()

# ---------- E. a card with no baseball on it at all ----------
# "No MLB game today" is a normal state for a combined card, not an empty
# slate. Treated as empty it would report done:true and park a live football
# card straight onto Yesterday.
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(9, [nfl_leg(1, "Saquon Barkley", "PHI", "1"),
                              nfl_leg(2, "Travis Kelce", "KC", "2")])]}],
                 "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    check("E1 an all-NFL card with no MLB game is not an empty slate",
          page.evaluate("SLATES.today !== null && SLATES.today.done === false"),
          page.evaluate("JSON.stringify({t: !!SLATES.today, d: SLATES.today && SLATES.today.done})"))
    check("E2 ...and its legs still grade", page.evaluate(
        "EVALUATED[0].states.join(',')") in ("live,live", "live,miss", "miss,live", "miss,miss"),
        page.evaluate("EVALUATED[0].states.join(',')"))
    check("E3 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ...and once that same all-NFL card finishes, it must not announce itself as
# a day with no baseball on it. MLB's "no games scheduled" shortcut sets
# results.noGames, and the sync line prints that verbatim as the headline for
# a finished slate -- true, but the wrong thing to say about a card that was
# football all along.
FX["espn_events"] = {g: espn_event(g, "post", (24, 20)) for g in NFL_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "post", {"PHI": [("1", "Saquon Barkley", "rushing", (18, 92, 1))]}, (24, 20))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "post", {"KC": [("2", "Travis Kelce", "receiving", (6, 71, 0, 8))]}, (24, 20))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(150)
    sync = page.inner_text("#sync-line")
    check("E4 a finished all-NFL card reports final results, "
          "not 'no MLB games were scheduled'",
          "Final results" in sync and "No MLB games" not in sync, sync)
    check("E5 ...and its touchdown still graded", page.evaluate(
        "EVALUATED[0].states[0]") == "hit", page.evaluate("EVALUATED[0].states.join(',')"))
    browser.close()

# ---------- F. chrome ----------
# Back to the full two-sport card with a game still going, so there IS a live
# slate on screen -- the eyebrow only names its sources while one is showing.
FX["tickets"] = TICKETS
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5001, "Final", ["NYY", "BOS"]),
                                          (5002, "Final", ["NYM", "ATL"])])
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in NFL_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0))]}, (14, 10))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    # The icon span is an emoji; reading it back through a Windows console
    # raises rather than failing the check, so take only the label node.
    sports = page.eval_on_selector_all(
        ".sport-switch .sport", "els => els.map(e => e.lastChild.textContent.trim())")
    check("F1 the sport switch offers all three trackers", sports == ["MLB", "NFL", "NFL+MLB"], sports)
    check("F2 this page is the one marked active",
          page.get_attribute(".sport-switch .sport.active", "href") == "/all/")
    check("F3 the title names the combined tracker",
          page.inner_text("h1") == "BMBS Tracker — NFL+MLB", page.inner_text("h1"))
    check("F4 the eyebrow says where the data comes from",
          "MLB + ESPN" in page.inner_text("#eyebrow-text"), page.inner_text("#eyebrow-text"))
    browser.close()

# ---------- G. a combined card with no football on it ----------
# The mirror of section E, and the reason the NFL engine is handed only the
# NFL legs: hand it all of them and NFL.has() answers true for a card that is
# pure baseball. The page then polls ESPN for a slate with no football on it
# AND -- far worse -- waits on a live NFL game before a finished baseball card
# may roll over, which parks a settled slate on the Today tab indefinitely.
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(7, [mlb_leg(1, "Aaron Judge", "NYY"),
                              mlb_leg(2, "Juan Soto", "NYM")])]}],
                 "singles": []}
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5001, "Final", ["NYY", "BOS"]),
                                          (5002, "Final", ["NYM", "ATL"])])
# ESPN still has live games today. Nothing on this card is in them.
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in NFL_GAMES}
SEEN.clear()

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    check("G1 an all-MLB card asks ESPN nothing at all",
          not any("espn" in u for u in SEEN), [u for u in SEEN if "espn" in u][:3])
    check("G2 ...and a finished baseball card is DONE, "
          "never held open by a football game it has no bet in",
          page.evaluate("SLATES.today === null && SLATES.yesterday !== null"),
          page.evaluate("JSON.stringify({today: !!SLATES.today, yd: !!SLATES.yesterday})"))
    check("G3 no JavaScript errors", not errors, errors[:3])
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    raise SystemExit(1)
print("all combined-page checks passed")
