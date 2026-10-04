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


def mlb_feed(abstract, roster, hrs=(), inning=None, state="Top", outs=1, batter=""):
    sides = {"away": {}, "home": {}}
    for i, n in enumerate(roster):
        side = "away" if i < 9 else "home"
        sides[side][f"ID{i}"] = {"person": {"fullName": n}, "battingOrder": f"{(i % 9) + 1}00",
                                 "stats": {"batting": {"plateAppearances": 3}}}
    plays = [{"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
              "matchup": {"batter": {"fullName": n}}} for n in hrs]
    if inning and batter:
        # The lineup slot "due up next" is worked out by walking play-by-play
        # BACKWARDS for the last batter on that side. With no plays at all
        # there is no last batter, nextUpSlot stays null, and every pick in
        # the game silently earns no tile -- which looks exactly like the
        # panel being broken.
        plays.append({"result": {"eventType": "field_out"},
                      "about": {"isTopInning": state in ("Top", "End")},
                      "matchup": {"batter": {"fullName": batter}}})
    return {"gameData": {"status": {"abstractGameState": abstract,
                                    "codedGameState": {"Final": "F", "Live": "I"}[abstract]}},
            "liveData": {"plays": {"allPlays": plays},
                "boxscore": {"teams": {"away": {"players": sides["away"]},
                                       "home": {"players": sides["home"]}}},
                # A live game needs a real linescore or there is no inning,
                # no half and nobody at the plate -- so battingContextFor()
                # returns null and the pick silently earns no tile.
                "linescore": ({"currentInning": inning, "inningState": state, "outs": outs,
                               "offense": {"batter": {"fullName": batter}}} if inning else {})}}


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


def espn_scoring(play_id, team, kind, text, period, clock, away_score, home_score):
    return {"id": play_id, "type": {"text": kind}, "text": text,
            "period": {"number": period}, "clock": {"value": 300.0, "displayValue": clock},
            "team": {"abbreviation": team}, "awayScore": away_score, "homeScore": home_score}


def espn_drive(drive_id, team, to_go, down_text, result=None, possession=None):
    """`team` owns the drive object. `possession` (default: the same team) is
    who the last play left the ball with -- the two differ for a real window
    after a punt or turnover, because a drive only appears once its first play
    posts. `result` set means the drive has ENDED, which ESPN keeps on
    drives.current right through the kickoff that follows."""
    holder = possession or team
    d = {"id": drive_id, "team": {"abbreviation": team}, "description": "6 plays, 55 yards",
         "isScore": False,
         "plays": [{"id": f"{drive_id}-p", "text": "last play",
                    "wallclock": f"{DAY}T19:30:00Z",
                    "end": {"team": {"id": TEAM_ID[holder]}, "yardsToEndzone": to_go,
                            "downDistanceText": down_text,
                            "shortDownDistanceText": down_text.split(" at ")[0]}}]}
    if result:
        d["displayResult"] = result
    return d


def espn_summary(gid, state, lines, score=(0, 0), plays=(), current=None):
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
    drives = {"previous": []}
    if current:
        drives["current"] = current
    return {"header": {"competitions": [comp]}, "boxscore": {"players": players},
            "scoringPlays": list(plays), "drives": drives}


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

# ---------- H. the live panel and the log carry both sports ----------
# A football pick has no batting order, no base and no inning, so without its
# own branch every path in renderLiveAtBats skips it and the panel silently
# shows a baseball-only view of a two-sport card.
FX["tickets"] = TICKETS
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5001, "Live", ["NYY", "BOS"]),
                                          (5002, "Live", ["NYM", "ATL"])])
FX["mlb_feeds"][5001] = mlb_feed("Live", ["Aaron Judge"] + [f"NY{i}" for i in range(8)],
                                 hrs=["Aaron Judge"])
# Soto bats second for the away side with one out in the top of the 5th, so
# he is genuinely due up -- the batting machinery has to have an inning and a
# current batter or it places nobody.
FX["mlb_feeds"][5002] = mlb_feed("Live", ["NM0", "Juan Soto"] + [f"NM{i}" for i in range(1, 8)],
                                 inning=5, state="Top", outs=1, batter="NM0")
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in NFL_GAMES}
# Barkley has NOT scored and his side has the ball inside the 20 -- a tile he
# can score from. Kelce has not scored either and KC's drive has just ENDED,
# which ESPN keeps on drives.current right through the following kickoff:
# the window that used to read green and say "ball on offense" while his team
# was actually kicking off.
#
# Both picks are deliberately still LIVE. A pick who has already scored is
# ineligible for a tile anyway, so scoring one here would make the
# drive-ended check pass without ever exercising the drive logic.
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0)),
                           ("99", "Dallas Goedert", "receiving", (3, 28, 1, 4))]}, (14, 10),
    plays=[espn_scoring("sp1", "PHI", "Receiving Touchdown",
                        "Dallas Goedert 7 Yd pass from Jalen Hurts", 3, "7:12", 14, 10)],
    current=espn_drive("d1", "PHI", 12, "1st & 10 at NYG 12"))
# Kelce's side has just taken the ball back -- possession is KC's, but the
# drive object still open is DENVER'S, because a drive only appears once its
# first play posts. Measured on live games at 65s, 82s and 179s: several polls
# wide, a real state rather than a flicker.
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10),
    current=espn_drive("d2", "DEN", 70, "3rd & 8 at DEN 30", possession="KC"))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")   # the panel is collapsed by default
    page.wait_for_timeout(200)
    tiles = page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => els.map(e => ({name: e.querySelector('.ab-name').textContent.trim(), "
        "tag: e.querySelector('.ab-tag').textContent.trim()}))")
    by_name = {t["name"]: t["tag"] for t in tiles}
    check("H1 a football pick gets a tile in the Live Bet Tracker at all",
          "Saquon Barkley" in by_name, tiles)
    check("H2 ...and inside the 20 it reads RED ZONE",
          by_name.get("Saquon Barkley") == "RED ZONE", by_name)
    check("H3 a baseball pick still gets his own tile on the same wall",
          any(n in by_name for n in ("Aaron Judge", "Juan Soto")), by_name)
    # Possession is his but the drive object open is still the other team's.
    # Green here would claim he can score on a play his side has not snapped
    # yet, so it has to be the grey tile -- and it has to be a tile, because
    # his bet is very much still live.
    check("H4 a pick whose side has the ball but no drive open yet "
          "is TAKING THE FIELD SOON, not on offense",
          by_name.get("Travis Kelce") == "TAKING THE FIELD SOON", by_name)

    # Now his drive ENDS -- a punt. ESPN keeps naming KC on drives.current
    # right through the kickoff that follows, so possession plus an open
    # drive both still say yes. Only driveOver says otherwise, and without it
    # he sits there green reading "ball on offense" while his team kicks off.
    FX["espn_summaries"]["9002"] = espn_summary(
        "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10),
        current=espn_drive("d3", "KC", 70, "3rd & 8 at KC 30", result="Punt"))
    page.evaluate("SLATE_POLLS.clear()")   # the slate is cached per poll; force a re-read
    poll(page)
    after = page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => Object.fromEntries(els.map(e => [e.querySelector('.ab-name').textContent.trim(), "
        "e.querySelector('.ab-tag').textContent.trim()]))")
    check("H4b ...and once that drive is OVER he is not shown on offense at all",
          after.get("Travis Kelce") not in ("ON OFFENSE", "RED ZONE"), after)

    # ---- the scoring log ----
    # On EVERYTHING, because the touchdown here was scored by a tight end
    # nobody picked -- which is exactly what that filter is for.
    page.evaluate("toggleHrLog(); setHrFilter('all')")
    page.wait_for_timeout(200)
    rows = page.eval_on_selector_all(
        "#hrlog-list .hr-row .hr-batter", "els => els.map(e => e.textContent.trim())")
    check("H5 the log lists a touchdown and a home run together",
          "Dallas Goedert" in rows and "Aaron Judge" in rows, rows)
    check("H6 the count pill counts across both sports",
          page.inner_text("#hrlog-count") == "1 OURS · 2 TOTAL · 50%",
          page.inner_text("#hrlog-count"))
    check("H6b ...and marks only the pick's own score as ours",
          page.eval_on_selector_all(
              "#hrlog-list .hr-row.ours .hr-batter", "els => els.map(e => e.textContent.trim())")
          == ["Aaron Judge"],
          page.eval_on_selector_all("#hrlog-list .hr-row.ours .hr-batter",
                                    "els => els.map(e => e.textContent.trim())"))
    check("H7 the panel is named for both sports",
          "Scoring Log" in page.inner_text(".hrlog-title"), page.inner_text(".hrlog-title"))
    # The row's own onclick names toggleTdRow, which lives inside the module;
    # an inline handler is resolved against window when CLICKED, so the host
    # needs a shim or every touchdown row throws on tap.
    check("H8 a touchdown row can actually be expanded",
          page.evaluate("typeof toggleTdRow === 'function'"))
    page.evaluate("toggleTdRow('sp1')")
    page.wait_for_timeout(150)
    check("H9 ...and expanding it shows the touchdown's detail, not a home run's",
          page.query_selector("#hrlog-list .hr-row.open") is not None
          and "Statcast" not in (page.inner_text("#hrlog-list .hr-row.open") or ""),
          page.query_selector("#hrlog-list .hr-row.open") is not None)
    check("H10 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- I. alerts fire for whichever sport scored ----------
# The flood guard matters more than the alert here: the first poll of a slate
# records who has already scored WITHOUT announcing, so opening the page at
# half time doesn't replay the whole afternoon in a queue of overlays.
FX["tickets"] = TICKETS
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5001, "Live", ["NYY", "BOS"]),
                                          (5002, "Live", ["NYM", "ATL"])])
FX["mlb_feeds"][5001] = mlb_feed("Live", ["Aaron Judge"] + [f"NY{i}" for i in range(8)],
                                 inning=5, state="Top", outs=1, batter="Aaron Judge")
FX["mlb_feeds"][5002] = mlb_feed("Live", ["NM0", "Juan Soto"] + [f"NM{i}" for i in range(1, 8)],
                                 inning=5, state="Top", outs=1, batter="NM0")
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in NFL_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0))]}, (14, 10),
    current=espn_drive("d1", "PHI", 12, "1st & 10 at NYG 12"))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("""() => {
        window.FIRED = [];
        window.REAL_ALERT = playAlertSound;   // kept: I6b needs the real one back
        playAlertSound = (kind, cashed) => { window.FIRED.push({kind, cashed: !!cashed}); };
    }""")
    check("I1 nobody has scored yet, so nothing has fired",
          page.evaluate("window.FIRED.length") == 0)

    # Barkley scores. Card 2 (both NFL) is still live, so the alert is worth
    # firing, and the sound is the KICK -- not the home run's bomb.
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (13, 64, 1))]}, (21, 10),
        plays=[espn_scoring("sp9", "PHI", "Rushing Touchdown",
                            "Saquon Barkley 4 Yd Run", 3, "7:12", 21, 10)],
        current=espn_drive("d1", "PHI", 12, "1st & 10 at NYG 12"))
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    fired = page.evaluate("window.FIRED")
    check("I2 a touchdown by a pick fires an alert", len(fired) == 1, fired)
    check("I3 ...and it is the football sound, not the home run's",
          fired and fired[0]["kind"] == "kick", fired)

    # The same touchdown on the next poll must not fire again.
    poll(page)
    check("I4 the same touchdown does not announce itself twice",
          page.evaluate("window.FIRED.length") == 1, page.evaluate("window.FIRED"))

    # Judge homers. Different sport, different voice, same queue.
    FX["mlb_feeds"][5001] = mlb_feed(
        "Live", ["Aaron Judge"] + [f"NY{i}" for i in range(8)], hrs=["Aaron Judge"],
        inning=6, state="Top", outs=1, batter="Aaron Judge")
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    fired = page.evaluate("window.FIRED")
    check("I5 a home run on the same card fires its own alert", len(fired) == 2, fired)
    check("I6 ...flagged as a cash, because the mixed parlay just landed",
          fired[-1]["kind"] == "bomb" and fired[-1]["cashed"] is True, fired)
    # The spy above records the ARGUMENTS playAlertSound was called with, so
    # it cannot see which sound actually came out -- and a cash is supposed to
    # REPLACE the event sound rather than play after it. Stub the voices and
    # call the real thing.
    played = page.evaluate("""() => {
        const calls = [];
        ["bomb", "swipe", "kick", "cash"].forEach(k => { SOUNDS[k] = () => calls.push(k); });
        SOUND_ON = true;   // defaults off, and playAlertSound returns early when it is
        const grab = () => { const c = calls.slice(); calls.length = 0; return c; };
        const out = {};
        window.REAL_ALERT("kick", false); out.kick = grab();
        window.REAL_ALERT("kick", true);  out.kickCashed = grab();
        window.REAL_ALERT("bomb", true);  out.bombCashed = grab();
        return out;
    }""")
    check("I6b a touchdown that cashes plays the register INSTEAD of the kick, "
          "never both back to back",
          played == {"kick": ["kick"], "kickCashed": ["cash"], "bombCashed": ["cash"]}, played)
    check("I7 no JavaScript errors anywhere in the alert path", not errors, errors[:3])
    browser.close()

# The flood guard, on its own page: both scores are ALREADY on the board when
# the page opens. Nothing may fire -- the difference between opening the tab
# late and being shouted at by every play of the afternoon.
#
# Checked on the OVERLAY rather than with a spy, because the first poll
# happens inside open_page: a spy installed afterwards is already too late to
# see it, which is why the first version of this check passed with the
# seeding deleted.
with sync_playwright() as p:
    browser, page, errors = open_page(p)
    check("I8 scores already on the board when the page opens announce nothing",
          page.evaluate("document.getElementById('bomb-overlay').innerHTML") == ""
          and page.evaluate("BOMB_QUEUE.length") == 0,
          page.evaluate("document.getElementById('bomb-overlay').innerHTML")[:120])
    check("I8b ...but they ARE recorded, so they can't fire later either",
          page.evaluate("BOMB_STATE.seeded === true && BOMB_STATE.notified.size > 0"),
          page.evaluate("JSON.stringify([BOMB_STATE.seeded, [...BOMB_STATE.notified]])"))
    browser.close()

# ---------- J. a score that can't change anything stays quiet ----------
# The parlay is already dead from a leg that missed, and the scorer is on
# nothing else. He still resolves to `hit` on the card; what he does not get
# is an alert, because there is no longer anything his touchdown can do.
DEAD = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb", "nfl"],
        "windows": [{"title": "Parlay Cards", "tickets": [
            card(8, [mlb_leg(1, "Dead Bat", "SEA"),
                     nfl_leg(2, "Travis Kelce", "KC", "2")])]}],
        "singles": []}
FX["tickets"] = DEAD
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(5003, "Final", ["SEA", "TEX"])])
FX["mlb_feeds"][5003] = mlb_feed("Final", ["Dead Bat"] + [f"SE{i}" for i in range(8)])
FX["espn_events"] = {"9002": espn_event("9002", "in", (14, 10))}
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("""() => {
        window.FIRED = [];
        playAlertSound = (k, c) => { window.FIRED.push(k); };
    }""")   # wrapped: a bare assignment returns the arrow, which Playwright then CALLS
    check("J1 the parlay is already dead from the baseball leg",
          page.evaluate("EVALUATED[0].evalRes.outcome") == "dead",
          page.evaluate("EVALUATED[0].evalRes.outcome"))
    FX["espn_summaries"]["9002"] = espn_summary(
        "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (5, 52, 1, 6))]}, (21, 10),
        plays=[espn_scoring("sp2", "KC", "Receiving Touchdown",
                            "Travis Kelce 8 Yd pass", 3, "4:00", 21, 10)])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    check("J2 his touchdown still resolves the leg on the card",
          page.evaluate("EVALUATED[0].states[1]") == "hit",
          page.evaluate("EVALUATED[0].states.join(',')"))
    check("J3 ...but nothing is announced, because it can't change a thing",
          page.evaluate("window.FIRED.length") == 0, page.evaluate("window.FIRED"))
    check("J4 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- K. an all-football card can still alert ----------
# checkForBombs used to bail unless the BASEBALL half was ready. On a card
# with no baseball on it that never happens -- there are no MLB games to make
# it happen -- so every touchdown on an all-NFL card went unannounced.
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(9, [nfl_leg(1, "Saquon Barkley", "PHI", "1")])]}],
                 "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {"9001": espn_event("9001", "in", (14, 10))}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("""() => {
        window.FIRED = [];
        window.CASHED = null;
        playAlertSound = (k, c) => { window.FIRED.push(k); window.CASHED = !!c; };
    }""")   # wrapped: a bare assignment returns the arrow, which Playwright then CALLS
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (13, 64, 1))]}, (21, 10),
        plays=[espn_scoring("sp3", "PHI", "Rushing Touchdown",
                            "Saquon Barkley 4 Yd Run", 3, "7:12", 21, 10)])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    # "kick" is the KIND asked for; a cash is the second argument, and
    # playAlertSound swaps in the register itself (I6b pins that). Asserting
    # "cash" here would be asserting against the wrong layer.
    check("K1 a touchdown on an all-football card still alerts",
          page.evaluate("window.FIRED") == ["kick"], page.evaluate("window.FIRED"))
    check("K1b ...and it is flagged as cashing the bet it just completed",
          page.evaluate("window.CASHED") is True, page.evaluate("window.CASHED"))
    check("K2 no JavaScript errors", not errors, errors[:3])
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    raise SystemExit(1)
print("all combined-page checks passed")
