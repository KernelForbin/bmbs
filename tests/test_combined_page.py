"""
index.html -- the All Sports front page (it lived at all/index.html until
2026-10-05; the tests still serve it at that URL, which resolves its data the same).

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
PAGE = REPO / "index.html"     # the front page since 2026-10-05 (was all/index.html)

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


def mlb_feed(abstract, roster, hrs=(), inning=None, state="Top", outs=1, batter="",
             teams=("AWY", "HME"), score=None):
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
    ls = ({"currentInning": inning, "inningState": state, "outs": outs,
           "offense": {"batter": {"fullName": batter}}} if inning else {})
    if score is not None:
        # teamScores is built from linescore.teams keyed by the ABBREVIATIONS
        # in gameData.teams -- a team bet has no player, so that pair is the
        # only way the page can find the game at all.
        ls["teams"] = {"away": {"runs": score[0]}, "home": {"runs": score[1]}}
    return {"gameData": {"status": {"abstractGameState": abstract,
                                    "codedGameState": {"Final": "F", "Live": "I"}[abstract]},
                         "teams": {"away": {"abbreviation": teams[0]},
                                   "home": {"abbreviation": teams[1]}}},
            "liveData": {"plays": {"allPlays": plays},
                "boxscore": {"teams": {"away": {"players": sides["away"]},
                                       "home": {"players": sides["home"]}}},
                # A live game needs a real linescore or there is no inning,
                # no half and nobody at the plate -- so battingContextFor()
                # returns null and the pick silently earns no tile.
                "linescore": ls}}


# ---------------- ESPN fixtures ----------------
TEAM_ID = {"PHI": "21", "NYG": "19", "KC": "12", "DEN": "7", "LAR": "14", "SEA": "26"}
NFL_GAMES = {"9001": ("PHI", "NYG"), "9002": ("KC", "DEN"), "9003": ("LAR", "SEA")}
# 9003 exists only for section O. Every earlier section builds its scoreboard
# from MAIN_GAMES, because an event with no summary fixture makes the route
# handler raise and the page never finishes its first poll.
MAIN_GAMES = ("9001", "9002")
LABELS = {"rushing": ["CAR", "YDS", "AVG", "TD", "LONG"],
          "receiving": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"],
          # Real labels, read off a finished game -- SACKS sits third.
          "defensive": ["TOT", "SOLO", "SACKS", "TFL", "PD", "QB HTS", "TD"],
          # Note SACKS here too, meaning sacks TAKEN -- the opposite of the
          # defensive column of the same name.
          "passing": ["C/ATT", "YDS", "AVG", "TD", "INT", "SACKS", "QBR", "RTG"]}


def espn_event(gid, state, score=(0, 0), quarters=None, period=None, break_=None):
    """`period` is the quarter on the clock -- by default the one the
    linescores have reached, as ESPN's own is. `break_` puts the game AT a
    quarter break: "end" (end of the 1st or 3rd) or "half"."""
    away, home = NFL_GAMES[gid]
    if period is None:
        period = max([len(q or []) for q in (quarters or [])] or [0]) or 4
    name = {"end": "STATUS_END_PERIOD", "half": "STATUS_HALFTIME"}.get(break_, "STATUS_IN_PROGRESS")
    return {"id": gid, "date": f"{DAY}T17:00Z",
            "status": {"period": period, "displayClock": "0:00" if break_ else "2:00",
                       "type": {"state": state, "name": name if state == "in" else "STATUS_" + state.upper(),
                                "shortDetail": {"pre": "1:00 PM", "in": "2:00 - Q4", "post": "Final"}[state]}},
            "competitions": [{"competitors": [
                {"id": TEAM_ID[away], "homeAway": "away", "score": str(score[0]),
                 "team": {"abbreviation": away},
                 # Per-quarter points, exactly as ESPN ships them.
                 "linescores": [{"displayValue": str(q)} for q in (quarters or [None, None])[0] or []]},
                {"id": TEAM_ID[home], "homeAway": "home", "score": str(score[1]),
                 "team": {"abbreviation": home},
                 "linescores": [{"displayValue": str(q)} for q in (quarters or [None, None])[1] or []]}]}]}


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


def espn_summary(gid, state, lines, score=(0, 0), plays=(), current=None, quarters=None, period=None, break_=None):
    ev = espn_event(gid, state, score, quarters, period, break_)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    players = []
    for team, rows in lines.items():
        cats = {}
        for aid, name, cat, vals in rows:
            if cat == "rushing":
                stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0"]
            elif cat == "defensive":            # (tackles, solo, sacks)
                stats = [str(vals[0]), str(vals[1]), str(vals[2]), "0", "0", "0", "0"]
            elif cat == "passing":              # (td thrown, sacks TAKEN)
                stats = ["20/30", "250", "8.3", str(vals[0]), "0", f"{vals[1]}-20", "0", "0"]
            else:
                stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0", str(vals[3])]
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
        # Same shape, but the football leg names a market nothing follows.
        # A hit baseball leg must NOT be allowed to cash it.
        card(4, [mlb_leg(7, "Aaron Judge", "NYY"),
                 # A market word no registry entry claims -- which is what
                 # this check is really about, and stays true however many
                 # real markets get added later. Using a named-but-ungraded
                 # one made it stale the moment that market was implemented.
                 nfl_leg(8, "Travis Kelce", "KC", "2", market="longest_reception")]),
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
FX["espn_events"] = {g: espn_event(g, "post", (21, 17)) for g in MAIN_GAMES}
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
    tags = dict(page.evaluate("""() => [...document.querySelectorAll('.leg-player, .single-player')]
        .map(e => [e.firstChild.textContent.trim(), (e.querySelector('.mkt-tag') || {}).textContent || ''])"""))
    check("A1b every leg row says what it is going for, in either sport",
          bool(tags) and all(tags.values()), tags)
    check("A1c ...a home run leg as HOME RUN, a touchdown leg as Anytime TD",
          tags.get("Aaron Judge") == "HOME RUN" and "Anytime TD" in tags.values(), tags)
    check("A1d a home run bet over 0.5 is just HOME RUN; 2+ keeps its line",
          page.evaluate("[marketTag('hr', {line: 0.5}), marketTag('hr', {line: 1.5}), marketTag('hr', {})]"
                        ".map(h => h.replace(/<[^>]+>/g, '').trim())") == ["HOME RUN", "HOME RUN OVER 1.5", "HOME RUN"],
          page.evaluate("[marketTag('hr', {line: 0.5}), marketTag('hr', {line: 1.5})]"))
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
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
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
FX["espn_events"] = {g: espn_event(g, "post", (24, 20)) for g in MAIN_GAMES}
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
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 0))]}, (14, 10))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    # The FRONT PAGE since 2026-10-05 (the user's call): All Sports is bmbs.bet
    # itself, MLB and NFL are hidden, and with one tab left the switch is too.
    switch = page.eval_on_selector(".sport-switch", "e => getComputedStyle(e).display")
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    check("F1 the sport switch is hidden -- every other tracker is reachable by URL only", switch == "none", switch)
    check("F2 ...and nothing on the page links a hidden tracker",
          not [h for h in hrefs if h.split("?")[0] in ("/mlb/", "/football/", "/hockey/", "/basketball/", "/wnba/", "/cfb/", "/all/")],
          hrefs)
    check("F3 the title is just the tracker's name", page.inner_text("h1") == "BMBS Tracker"
          and page.title() == "BMBS Tracker", (page.inner_text("h1"), page.title()))
    check("F4 the eyebrow says it updates live, naming no data source",
          page.inner_text("#eyebrow-text") == "TODAY'S SLATE · UPDATED LIVE", page.inner_text("#eyebrow-text"))
    # Hidden, not removed: every panel's code still runs, so unhiding one is
    # deleting a single class.
    shown = page.evaluate("""() => Object.fromEntries([['legs', '#chip-leg-live'], ['log', '#hrlog-section'],
        ['bettors', '#bettor-section']].map(([k, sel]) => [k, document.querySelector(sel).getClientRects().length > 0]))""")
    check("F5 the LEGS row, the Scoring Log and the Bettor Tracker are hidden", shown == {"legs": False, "log": False, "bettors": False}, shown)
    alive = page.evaluate("""() => [document.getElementById('count-leg-live').textContent,
        document.getElementById('bettor-list').innerHTML.length > 0, typeof renderHrLog === 'function']""")
    check("F6 ...and still running underneath: leg counts kept, Bettor Tracker rows built, the log's renderer there",
          alive[0] not in ("", None) and alive[1] is True and alive[2] is True, alive)
    page.evaluate("toggleLiveAb()")
    sub = page.inner_text("#liveab-sub")
    check("F7 the tracker's sub-line names no sport: no 'Gameday', no 'at the plate'",
          "Gameday" not in sub and "plate" not in sub and "updates every" in sub.lower(), sub)
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
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
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
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
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

    # ---- the always-on status line under each pick ----
    # Every pick carries one, always: "" on screen is indistinguishable from
    # the page having no idea. A football pick used to get BASEBALL's line --
    # "Game on -- not in the lineup yet." -- which was on screen under a man
    # whose team was first and goal on the twelve. Found by looking at the
    # rendered page, not by a test.
    legs = page.eval_on_selector_all(
        ".leg-live-context, .leg-status",
        "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
    joined = " || ".join(legs)
    check("L1 a football pick's status line is football's, not the lineup card's",
          "not in the lineup" not in joined, joined[:200])
    check("L2 ...and it says where the ball actually is",
          any("RED ZONE" in t and "NYG 12" in t for t in legs), legs[:6])
    check("L3 a baseball pick still gets the batting-order line",
          any("batting" in t and "AB left" in t for t in legs), legs[:6])
    # Kelce's drive ended at H4b, so his side is now kicking off -- on defense.
    # He gets no TILE for that (H4b), but his leg still has to say something,
    # and it must not be anything that reads like he can score on this play.
    check("L3b a pick whose side is on defense is told so plainly",
          any("on defense" in t for t in legs), legs[:6])

    # The colour key is the one thing on the page whose entire job is saying
    # what the colours mean, and it read "Home run" on a card half of which
    # was football.
    check("L4 the colour key names both sports on a mixed card",
          page.inner_text("#legend-hit").lower() == "home run / touchdown",
          page.inner_text("#legend-hit"))
    check("L5 ...and the miss swatch stops saying 'no HR'",
          "HR" not in page.inner_text("#legend-miss"), page.inner_text("#legend-miss"))
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
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
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
    check("K3 an all-football card's colour key says touchdown and nothing about home runs",
          page.inner_text("#legend-hit").lower() == "touchdown",
          page.inner_text("#legend-hit"))
    browser.close()

# ---------- N. a TEAM's own total ----------
# "Braves Over 3.5 Runs" is the Braves' runs, not the game's -- a different
# bet from the TOTAL market, which adds both sides. teamScores has carried
# the per-team number all along; nothing read it this way, so the leg sat
# untracked next to a game total that would have answered the wrong question.
def team_leg(i, team, line, side=None, market="team_total"):
    leg = {"id": f"T{i}", "player": team, "team": team, "who": "Memo",
           "meta": team, "odds": "+118", "time": "7:10 PM ET",
           "sport": "mlb", "market": market, "line": line}
    if side:
        leg["side"] = side
    return leg


FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(20, [team_leg(1, "ATL", 3.5)]),              # over, already clear
                     card(21, [team_leg(2, "NYM", 3.5)]),              # over, still short
                     card(22, [team_leg(3, "NYY", 3.5, "under")]),     # under, already busted
                     card(23, [team_leg(4, "BOS", 4.0)]),              # lands ON the number
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(6001, "Live", ["ATL", "NYM"]),
                                          (6002, "Final", ["NYY", "BOS"])])
# ATL 5 - NYM 1, still being played. NYY 6 - BOS 4, final.
FX["mlb_feeds"][6001] = mlb_feed("Live", ["A1"], inning=6, state="Top", outs=1,
                                 batter="A1", teams=("ATL", "NYM"), score=(5, 1))
FX["mlb_feeds"][6002] = mlb_feed("Final", ["N1"], teams=("NYY", "BOS"), score=(6, 4))
FX["espn_events"] = {}

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    ls = leg_states(page)
    check("N1 a team over that has already cleared settles mid-game, "
          "the way a stat prop does", ls.get("T1") == "hit", ls)
    check("N2 ...one still short of the number stays live", ls.get("T2") == "live", ls)
    check("N3 an under that has already busted is a miss", ls.get("T3") == "miss", ls)
    check("N4 landing exactly ON the number is a push", ls.get("T4") == "na", ls)
    check("N5 none of them fell through to untracked",
          "untracked" not in ls.values(), ls)
    check("N6 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- O. the three football markets that were untracked ----------
# All three were called untrackable and none of them was. ESPN publishes
# sacks in the DEFENSIVE boxscore category and per-quarter points as
# competitors[].linescores -- both checked against a real finished game
# before any of this was written.
def nfl_leg_m(i, player, team, market, line, aid="", players=None, teams=None):
    leg = {"id": f"O{i}", "player": player, "team": team, "athleteId": aid,
           "who": "Memo", "meta": team, "odds": "+308", "time": "1:00 PM ET",
           "sport": "nfl", "market": market}
    if line is not None:
        leg["line"] = line
    if players:
        leg["players"] = players
    if teams:
        leg["teams"] = teams
    return leg


FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(30, [nfl_leg_m(1, "Sack Guy", "PHI", "sacks", 0.5, "11")]),
                     card(31, [nfl_leg_m(2, "No Sack", "PHI", "sacks", 0.5, "12")]),
                     # One bet on three men's combined touchdowns: 2 + 1 + 1 = 4
                     card(32, [nfl_leg_m(3, "A, B, C", "PHI", "td_count", 3.5, "",
                                         players=["Three A", "Three B", "Three C"])]),
                     card(33, [nfl_leg_m(4, "PHI + NYG", "PHI", "quarters", None,
                                         teams=["PHI", "NYG"])]),
                     card(34, [nfl_leg_m(5, "KC + DEN", "KC", "quarters", None,
                                         teams=["KC", "DEN"])]),
                     # Still being played, and LAR have already been blanked
                     # in a quarter that finished. It can never come good, so
                     # it must settle now rather than waiting for the whistle.
                     card(35, [nfl_leg_m(6, "LAR + SEA", "LAR", "quarters", None,
                                         teams=["LAR", "SEA"])]),
                     # Touchdowns THROWN, which is not the anytime market --
                     # and the passer must NOT be credited with the sacks he
                     # took, which share the label "SACKS" one column over.
                     card(36, [nfl_leg_m(7, "Pass Guy", "PHI", "pass_tds", 2.5, "16")]),
                     card(37, [nfl_leg_m(8, "Pass Guy", "PHI", "sacks", 0.5, "16")]),
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {g: espn_event(g, "post", (24, 20)) for g in MAIN_GAMES}
FX["espn_events"]["9003"] = espn_event("9003", "in", (10, 14))
# LAR were blanked in the SECOND quarter and the third is still being played,
# so the bet is already dead with a quarter left to go.
FX["espn_summaries"]["9003"] = espn_summary(
    "9003", "in", {"LAR": [("20", "Ram Guy", "rushing", (5, 20, 0))]},
    (10, 14), quarters=[[7, 0, 3], [7, 7, 0]])
# PHI and NYG both score in all four. DENVER blanks the second quarter.
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "post", {"PHI": [("11", "Sack Guy", "defensive", (5, 4, 1)),
                             ("12", "No Sack", "defensive", (6, 5, 0)),
                             ("13", "Three A", "rushing", (12, 60, 2)),
                             ("14", "Three B", "rushing", (8, 40, 1)),
                             ("15", "Three C", "receiving", (4, 30, 1, 5)),
                             # 3 thrown, and 4 sacks TAKEN -- the passing
                             # line's own SACKS column, which must not reach
                             # his sack-prop leg.
                             ("16", "Pass Guy", "passing", (3, 4))]},
    (24, 20), quarters=[[7, 3, 7, 7], [3, 7, 3, 7]])
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "post", {"KC": [("2", "Travis Kelce", "receiving", (6, 71, 0, 8))]},
    (24, 20), quarters=[[7, 7, 3, 10], [3, 0, 3, 7]])

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("O1 a sack prop over 0.5 settles as a HIT when he got one",
          ls.get("O1") == "hit", ls)
    check("O2 ...and a miss when he didn't, once the game is final",
          ls.get("O2") == "miss", ls)
    check("O3 a combined touchdown leg ADDS all three players (2+1+1 beats 3.5)",
          ls.get("O3") == "hit", ls)
    check("O4 'each team all four quarters' hits when both teams did",
          ls.get("O4") == "hit", ls)
    check("O5 ...and misses when one of them was blanked in a quarter",
          ls.get("O5") == "miss", ls)
    check("O5b ...and it fails EARLY, on a quarter already played, rather than "
          "sitting live until the whistle on a bet that cannot come good",
          ls.get("O6") == "miss", ls)
    check("O5c passing touchdowns are graded off the passing line (3 beats 2.5)",
          ls.get("O7") == "hit", ls)
    check("O5d ...and the sacks he TOOK are not credited to his sack prop, "
          "though both columns are labelled SACKS",
          ls.get("O8") == "miss", ls)
    check("O6 none of them fell through to untracked",
          "untracked" not in ls.values(), ls)
    check("O7 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- P. yardage, and winning against the whole field ----------
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(40, [nfl_leg_m(1, "Big Catch", "PHI", "rec_yds", 14.5, "21")]),
                     card(41, [nfl_leg_m(2, "Small Catch", "PHI", "rec_yds", 99.5, "22")]),
                     card(42, [nfl_leg_m(3, "Big Run", "PHI", "rush_yds", 79.5, "23")]),
                     # The slate leader is in the OTHER game on purpose. Our
                     # 120-yard man leads his own, and loses the field to a
                     # 150 elsewhere -- so "compare the whole slate" and
                     # "compare his own game" give opposite answers here.
                     card(43, [nfl_leg_m(4, "Big Catch", "PHI", "most_rec_yds", None, "21")]),
                     card(44, [nfl_leg_m(5, "Mid Catch", "KC", "most_rec_yds", None, "24")]),
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {g: espn_event(g, "post", (24, 20)) for g in MAIN_GAMES}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "post", {"PHI": [("21", "Big Catch", "receiving", (6, 120, 0, 9)),
                             ("22", "Small Catch", "receiving", (2, 18, 0, 3)),
                             ("23", "Big Run", "rushing", (18, 95, 0))]}, (24, 20))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "post", {"KC": [("24", "Mid Catch", "receiving", (5, 150, 0, 7))]}, (24, 20))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("P1 receiving yards over the line is a hit", ls.get("O1") == "hit", ls)
    check("P2 ...and under it a miss", ls.get("O2") == "miss", ls)
    check("P3 rushing yards grade off the rushing line", ls.get("O3") == "hit", ls)
    check("P4 leading your OWN game is not enough -- 120 loses to a 150 elsewhere",
          ls.get("O4") == "miss", ls)
    check("P5 ...and the slate leader wins it from the other game",
          ls.get("O5") == "hit", ls)
    # Checked DIRECTLY, not just through the two leg states above: those stay
    # correct even if the comparison only ever looked at one game, because the
    # leader happens to be in the first one. The point of this market is that
    # it spans the whole slate.
    lead = page.evaluate("""() => {
        const r = NFL.leaders("recYds");
        return { best: r.best, holders: [...r.holders], allFinal: r.allFinal };
    }""")
    check("P5b the field spans EVERY game on the slate, not just the pick's own",
          lead["best"] == 150 and lead["holders"] == ["mid catch"], lead)
    check("P6 nothing fell through to untracked", "untracked" not in ls.values(), ls)
    # A yardage bet that missed says where it ended up -- never "no
    # touchdown", which is only the story of a touchdown bet.
    page.evaluate("toggleCardsSection('parlays')")
    page.wait_for_timeout(200)
    small = page.evaluate("""() => [...document.querySelectorAll('.leg')]
        .filter(e => e.querySelector('.leg-player').textContent.includes('Small Catch'))
        .map(e => (e.querySelector('.leg-live-context, .leg-status') || e).textContent.replace(/\\s+/g, ' ').trim())[0] || ''""")
    check("P6b a missed yardage bet's card says how many he got, not 'no touchdown'",
          "18 of 100 rec yds" in small and "touchdown" not in small.lower(), small)
    check("P7 no JavaScript errors", not errors, errors[:3])
    browser.close()

# It cannot settle while ANY game is unfinished -- a late kickoff can always
# overtake whoever led at four o'clock, which is the whole reason this market
# waits rather than answering early like a yardage line does.
FX["espn_events"]["9002"] = espn_event("9002", "in", (14, 10))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "in", {"KC": [("24", "Mid Catch", "receiving", (5, 80, 0, 7))]}, (14, 10))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    ls = leg_states(page)
    check("P8 the field is not settled while one game is still being played",
          ls.get("O4") == "live", ls)
    check("P9 ...while an ordinary yardage prop settles as soon as it clears",
          ls.get("O1") == "hit", ls)
    check("P10 no JavaScript errors", not errors, errors[:3])
    browser.close()

# A dead heat. Books settle it several ways -- some reduce the payout, some
# void -- so it is refunded rather than being called for one of them.
FX["espn_events"]["9002"] = espn_event("9002", "post", (24, 20))
FX["espn_summaries"]["9002"] = espn_summary(
    "9002", "post", {"KC": [("24", "Mid Catch", "receiving", (6, 120, 0, 9))]}, (24, 20))
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "post", {"PHI": [("21", "Big Catch", "receiving", (6, 120, 0, 9)),
                             ("22", "Small Catch", "receiving", (2, 18, 0, 3)),
                             ("23", "Big Run", "rushing", (18, 95, 0))]}, (24, 20))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("P11 a tie for the lead is refunded, not awarded to one of them",
          ls.get("O4") == "na" and ls.get("O5") == "na", ls)
    check("P12 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- Q. every followable football prop is on the tracker ----------
# Only anytime-TD legs had a tile, on reasoning that stopped being true once
# these props were graded off the live boxscore. A passing-TD leg never
# showed at all -- not at 0 thrown, not at 2 -- which is what the user saw.
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(50, [nfl_leg_m(1, "Pass Guy", "PHI", "pass_tds", 2.5, "31")]),
                     card(51, [nfl_leg_m(2, "Rush Guy", "NYG", "sacks", 0.5, "32")]),
                     # Same market, wrong side of the ball right now.
                     card(52, [nfl_leg_m(3, "Phi Rusher", "PHI", "sacks", 0.5, "33")]),
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {"9001": espn_event("9001", "in", (14, 10))}
# PHI have the ball at the NYG 12. Their QB has thrown 2 of the 3 he needs;
# NYG's pass rusher is on the field for exactly these snaps; PHI's own
# rusher is on the sideline while his offense is out there.
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("31", "Pass Guy", "passing", (2, 1)),
                           ("33", "Phi Rusher", "defensive", (3, 2, 0))],
                   "NYG": [("32", "Rush Guy", "defensive", (4, 3, 0))]},
    (14, 10), current=espn_drive("d1", "PHI", 12, "1st & 10 at NYG 12"))

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    tiles = page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => Object.fromEntries(els.map(e => [e.querySelector('.ab-name').textContent.trim(), "
        "{tag: e.querySelector('.ab-tag').textContent.trim(), text: e.textContent.replace(/\\s+/g, ' ')}]))")
    pg = tiles.get("Pass Guy", {})
    check("Q1 a passing-TD pick gets a tile while his offense has the ball",
          bool(pg), sorted(tiles))
    check("Q2 ...showing how close he is: 2 of 3 passing TDs",
          "2 of 3 passing TDs" in pg.get("text", ""), pg.get("text", "")[:160])
    rg = tiles.get("Rush Guy", {})
    check("Q3 a SACK pick gets a tile while his side is on DEFENSE -- the inverse "
          "of every other pick, because that is when a sack can happen",
          rg.get("tag") == "ON DEFENSE", tiles)
    check("Q4 ...showing the snap he can make the play on",
          "NYG 12" in rg.get("text", ""), rg.get("text", "")[:160])
    check("Q5 a sack pick whose OFFENSE has the ball gets no tile -- he is on the sideline",
          "Phi Rusher" not in tiles, sorted(tiles))
    # The bet card's own status line says where the bet stands too. It
    # showed only the score and his raw line -- "PASSING YDS OVER 249.5" with
    # no yardage count, seen live on Michael Penix Jr. (2026-10-05).
    page.evaluate("toggleCardsSection('parlays')")
    page.wait_for_timeout(200)
    lines = page.eval_on_selector_all(".leg", r"""els => Object.fromEntries(els.map(e => [
        e.querySelector('.leg-player').childNodes[0].textContent.trim(),
        (e.querySelector('.leg-live-context') || {}).textContent || '']))""")
    check("Q5b the card's line under a prop pick counts toward the bet, like his tile",
          "2 of 3 passing TDs" in lines.get("Pass Guy", "") and "0 of 1 sacks" in lines.get("Phi Rusher", ""), lines)
    # Halftime: nobody has the ball. Every pick on BOTH teams read "his side
    # is on defense" (seen live, 2026-10-05).
    FX["espn_events"] = {"9001": espn_event("9001", "in", (14, 10), break_="half", period=2)}
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("31", "Pass Guy", "passing", (2, 1)), ("33", "Phi Rusher", "defensive", (3, 2, 0))],
                       "NYG": [("32", "Rush Guy", "defensive", (4, 3, 0))]},
        (14, 10), break_="half", period=2)
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    lines = page.eval_on_selector_all(".leg", r"""els => Object.fromEntries(els.map(e => [
        e.querySelector('.leg-player').childNodes[0].textContent.trim(),
        (e.querySelector('.leg-live-context') || {}).textContent || '']))""")
    check("Q5c at halftime the card says the game is at the break -- not that either side is on defense",
          all("at the break" in lines.get(n, "") and "on defense" not in lines.get(n, "") for n in ("Pass Guy", "Rush Guy")), lines)
    check("Q6 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- R. "each team to score all four quarters" on the tracker ----------
# Two teams, four quarters, one bet -- so ONE tile for the pair, showing which
# quarters each side has scored in and who still owes the one being played.
# Shaped like the real card's leg: the parser's player split leaves TWO
# garbled "players" on it, so a tile filed per player rather than per pair
# would show up as two tiles named "Eagles" and "Giants Each team to...".
_q_leg = nfl_leg_m(1, "Eagles/Giants Each team to score", "PHI", "quarters", None,
                   teams=["PHI", "NYG"])
_q_leg["players"] = ["Eagles", "Giants Each team to score all four quarters"]
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [card(60, [_q_leg])]}],
                 "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {"9001": espn_event("9001", "in", (7, 10))}
# Second quarter under way: both scored in the first, NYG has scored in the
# second, PHI hasn't yet.
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("40", "Somebody", "rushing", (5, 20, 0))]}, (7, 10),
    quarters=[[7, 0], [3, 7]])


def quarter_tiles(page):
    return page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => els.map(e => ({name: e.querySelector('.ab-name').textContent.trim(), "
        "tag: e.querySelector('.ab-tag').textContent.trim(), "
        "text: e.textContent.replace(/\\s+/g, ' '), "
        "yes: e.querySelectorAll('.q-yes').length, owed: e.querySelectorAll('.q-owed').length}))")


with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    qt = quarter_tiles(page)
    check("R1 the quarters bet gets ONE tile for the pair, not one per team",
          len(qt) == 1 and qt[0]["name"] == "PHI + NYG", qt)
    t = qt[0] if qt else {}
    check("R2 ...tagged as what it is", t.get("tag") == "ALL 4 QUARTERS", t.get("tag"))
    odds_row = page.eval_on_selector("#liveab-grid .ab-tile .ab-odds", "e => e.textContent.trim()")
    # The waffle stays: a one-leg card still live is one away from cashing,
    # which is exactly what an Iron is. Only the price is under test here.
    price = odds_row.replace("\U0001F9C7", "").strip()
    check("R2b ...with its price once, and no home-run label it never had",
          price == "+308" and "HR" not in odds_row, ascii(odds_row))
    check("R3 ...marking the three quarter-scores already in (PHI Q1, NYG Q1, NYG Q2)",
          t.get("yes") == 3, t)
    check("R4 ...and the one still owed in the quarter being played",
          t.get("owed") == 1 and "Q2: PHI still needs to score" in t.get("text", ""),
          t.get("text", "")[:200])

    # PHI score in the second.
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("40", "Somebody", "rushing", (5, 20, 0))]}, (14, 10),
        quarters=[[7, 7], [3, 7]])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    t = (quarter_tiles(page) or [{}])[0]
    check("R5 once both have scored in the quarter, it says so and counts what's left",
          "Q2: both have scored" in t.get("text", "") and "2 to go" in t.get("text", ""),
          t.get("text", "")[:200])
    check("R6 ...and nothing is marked owed", t.get("owed") == 0, t)

    # Into the third, nobody on the board yet.
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("40", "Somebody", "rushing", (5, 20, 0))]}, (14, 10),
        quarters=[[7, 7, 0], [3, 7, 0]])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    t = (quarter_tiles(page) or [{}])[0]
    check("R7 a fresh quarter shows both teams owing it",
          "Q3: both still need to score" in t.get("text", "") and t.get("owed") == 2,
          t.get("text", "")[:200])
    check("R8 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- S. every tile says what it chases; every leg that hits alerts ----------
# The user's rules (2026-10-04): an anytime-TD pick reads "0 of 1 TD" the way
# a sack reads "0 of 1 sacks", and EVERY leg that hits -- not just home runs,
# steals and touchdowns -- changes its tile and pops the overlay.
#
# The quarters bet settles only at the FINAL whistle, and here it is the
# card's last game: the slate rolls to Yesterday on that same poll. Alerts
# read SLATES.today only, so a final-whistle leg on the last game -- or a
# walk-off home run -- never alerted at all until alertSlate().
_s_q = nfl_leg_m(3, "Eagles/Giants Each team to score", "PHI", "quarters", None, teams=["PHI", "NYG"])
_s_q["players"] = ["Eagles", "Giants Each team to score all four quarters"]
_s_td = nfl_leg(4, "Run Guy", "PHI", "40")
del _s_td["market"]          # no market named: the NFL default, anytime TD
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(80, [nfl_leg_m(1, "Pass Guy", "PHI", "pass_tds", 2.5, "31")]),
                     card(81, [_s_q]),
                     card(82, [_s_td]),
                     # Hasn't touched the ball: no stat line at all, which is a zero
                     card(83, [nfl_leg(5, "Quiet Guy", "PHI", "41", market="td")]),
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
FX["espn_events"] = {"9001": espn_event("9001", "in", (17, 17))}
FX["espn_summaries"]["9001"] = espn_summary(
    "9001", "in", {"PHI": [("31", "Pass Guy", "passing", (2, 1)), ("40", "Run Guy", "rushing", (5, 20, 0))]},
    (17, 17), current=espn_drive("d1", "PHI", 12, "1st & 10 at NYG 12"),
    quarters=[[7, 7, 3, 0], [3, 7, 7, 0]])

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    page.evaluate("""() => { window.FIRED = []; const real = fireLegHit;
        fireLegHit = (slate, leg, cash) => { FIRED.push(legAlertName(leg)); return real(slate, leg, cash); }; }""")
    tiles = page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => Object.fromEntries(els.map(e => [e.querySelector('.ab-name').textContent.trim(), "
        "e.textContent.replace(/\\s+/g, ' ')]))")
    check("S1 an anytime-TD pick says what it's chasing: 0 of 1 TD",
          "0 of 1 TD" in tiles.get("Run Guy", ""), tiles.get("Run Guy", sorted(tiles)))
    check("S1b ...including one with no stat line yet -- that is a zero, not a blank",
          "0 of 1 TD" in tiles.get("Quiet Guy", ""), tiles.get("Quiet Guy", sorted(tiles)))
    check("S2 ...and a prop still shows its own count beside it",
          "2 of 3 passing TDs" in tiles.get("Pass Guy", ""), tiles.get("Pass Guy", sorted(tiles)))

    # Final whistle: Pass Guy threw his third, both teams scored in the 4th.
    FX["espn_events"]["9001"] = espn_event("9001", "post", (24, 20))
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "post", {"PHI": [("31", "Pass Guy", "passing", (3, 1)), ("40", "Run Guy", "rushing", (5, 20, 0))]},
        (24, 20), quarters=[[7, 7, 3, 7], [3, 7, 7, 3]])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    fired = page.evaluate("FIRED")
    check("S3 the slate rolled over on this very poll -- the case being tested",
          page.evaluate("SLATES.today === null && !!SLATES.yesterday"),
          page.evaluate("[!!SLATES.today, !!SLATES.yesterday]"))
    check("S4 a passing-TD prop clearing alerts", "Pass Guy" in fired, fired)
    check("S5 all four quarters scored alerts too, settled by the final whistle of the LAST game",
          "PHI + NYG" in fired, fired)
    seen = page.evaluate("""[document.getElementById('bomb-overlay').textContent,
        ...BOMB_QUEUE.map(q => q.name + ' ' + (q.prop ? q.prop.word : ''))].join(' | ').replace(/\\s+/g, ' ')""")
    check("S6 ...each with its own words", "3+ PASSING TDS!" in seen and "ALL 4 QUARTERS!" in seen, seen)
    check("S7 both cashed their one-leg cards, so both say so",
          page.evaluate("BOMB_QUEUE.every(q => q.cash)") and "CASHED" in seen, seen)
    check("S8 the flash for a cleared NFL prop carries its count",
          page.evaluate("[...LAB.flashes.values()].some(f => f.cleared && f.cleared.detail === '3 of 3 passing TDs')"),
          page.evaluate("[...LAB.flashes.values()].map(f => f.cleared && f.cleared.detail)"))
    check("S9 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- T. each team to score in each quarter, written as EIGHT legs ----------
# Kenny's bet (2026-10-04), laid out the way the user asked: one leg per team
# per quarter. A leg hits the moment that team scores in that quarter and
# misses the moment the quarter ends without it -- so one scoreless quarter
# kills the bet, and with seven legs in it is an Iron. On the tracker, a leg
# shows only while ITS team has the ball in ITS quarter and hasn't scored.
def q_leg(i, team, q):
    return {"id": f"Q{i}", "player": {"PHI": "Eagles", "NYG": "Giants"}[team], "team": team,
            "who": "Kenny", "meta": f"{team} &middot; Kenny", "odds": None, "time": "",
            "market": "q_score", "sport": "nfl", "quarter": q}


_t_legs = [q_leg(2 * (q - 1) + j, t, q) for q in range(1, 5) for j, t in enumerate(("PHI", "NYG"))]
_t_all4 = nfl_leg_m(9, "Eagles/Giants Each team to score", "PHI", "quarters", None, teams=["PHI", "NYG"])
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     dict(card(90, _t_legs, payout=84.0), stake=9.88, book="Kenny"),
                     card(91, [_t_all4]),
                 ]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}


def t_game(state, quarters, drive_team=None, break_=None, period=None):
    FX["espn_events"] = {"9001": espn_event("9001", state, (sum(quarters[0]), sum(quarters[1])), quarters, period, break_)}
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", state, {"PHI": [("40", "Somebody", "rushing", (5, 20, 0))]},
        (sum(quarters[0]), sum(quarters[1])),
        current=espn_drive("dq", drive_team, 30, "2nd & 6 at NYG 30") if drive_team else None,
        quarters=quarters, period=period, break_=break_)


def t_tiles(page):
    return page.eval_on_selector_all(
        "#liveab-grid .ab-tile",
        "els => els.map(e => ({name: e.querySelector('.ab-name').textContent.trim(), "
        "tag: e.querySelector('.ab-tag').textContent.trim(), iron: !!e.querySelector('.iron-mark'), "
        # the waffle can't be printed on a Windows console, so it is spelled out
        "text: e.textContent.replace(/\\s+/g, ' ').replace(/\\u{1F9C7}/gu, '[iron]').replace(/[^\\x00-\\x7F]/g, '')}))")


def team_tiles(page):
    """Just the per-team tiles -- the each-team-all-quarters card has its own."""
    return [t for t in t_tiles(page) if t["name"] in ("PHI", "NYG")]


def t_rows(page):
    """Each Q-leg's row: [subject, tag, status line]."""
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.leg')].map(r => {
        const p = r.querySelector('.leg-player'); if (!p) return null;
        const tag = (p.querySelector('.mkt-tag') || {}).textContent || '';
        const ctx = (r.querySelector('.leg-live-context') || {}).textContent || '';
        return [p.firstChild.textContent.trim() + ' ' + tag, ctx.replace(/\\s+/g, ' ').trim()];
    }).filter(Boolean))""")


def t_poll(page):
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)


t_game("pre", [[], []])
with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    page.evaluate("""() => { window.FIRED = []; const real = fireLegHit;
        fireLegHit = (slate, leg, cash) => { FIRED.push(legHitWord(leg) + ' ' + legAlertName(leg)); return real(slate, leg, cash); }; }""")
    rows = t_rows(page)
    check("T1 the card lists all eight legs, each naming its team and its quarter",
          all(f"{t} Scores in Q{q}" in rows for q in range(1, 5) for t in ("PHI", "NYG")), sorted(rows))
    check("T2 the each-team-all-quarters leg names BOTH teams on its row, not just the first",
          "PHI + NYG All 4 Quarters" in rows, sorted(rows))
    check("T3 before kickoff every leg is waiting", set(leg_states(page).values()) == {"not_started"}, leg_states(page))

    # Q1 under way, scoreless, PHI driving.
    t_game("in", [[0], [0]], drive_team="PHI")
    t_poll(page)
    ls = leg_states(page)
    check("T4 in Q1 both Q1 legs are live and every later quarter is still waiting",
          ls["Q0"] == "live" and ls["Q1"] == "live" and all(ls[f"Q{i}"] == "not_started" for i in range(2, 8)), ls)
    tl = team_tiles(page)
    check("T5 the tracker shows the team WITH THE BALL, saying what it needs",
          [t["name"] for t in tl] == ["PHI"] and "needs a score in Q1" in tl[0]["text"]
          and tl[0]["tag"] in ("ON OFFENSE", "RED ZONE"), tl)
    rows = t_rows(page)
    check("T6 the card's own line says it too", "PHI still needs to score" in rows.get("PHI Scores in Q1", "")
          and "PHI has the ball" in rows.get("PHI Scores in Q1", ""), rows.get("PHI Scores in Q1"))
    check("T7 ...and a later quarter says it hasn't started", "Q3 hasn't started" in rows.get("NYG Scores in Q3", ""),
          rows.get("NYG Scores in Q3"))

    # PHI score in Q1; now NYG have the ball.
    t_game("in", [[7], [0]], drive_team="NYG")
    t_poll(page)
    ls = leg_states(page)
    check("T8 PHI's Q1 leg is a HIT the moment they score -- mid-quarter, not at the end of it",
          ls["Q0"] == "hit" and ls["Q1"] == "live", ls)
    tl = team_tiles(page)
    check("T9 PHI leave the wall (their Q1 leg is done) and NYG, now with the ball, take it",
          [t["name"] for t in tl if t["tag"] != "HIT" and t["tag"] != "CASHED"] == ["NYG"], tl)
    check("T10 the score alerted, in its own words", "SCORED IN Q1! PHI" in page.evaluate("FIRED"), page.evaluate("FIRED"))

    # Still Q1, PHI have the ball back -- PHI's Q1 is already in, so no tile.
    t_game("in", [[7], [0]], drive_team="PHI")
    t_poll(page)
    check("T11 a team that has already scored this quarter gets no tile while it has the ball",
          not [t for t in t_tiles(page) if t["name"] == "PHI" and t["tag"] not in ("HIT", "CASHED")], t_tiles(page))

    # End of the 1st, NYG never scored.
    t_game("in", [[7], [0]], break_="end", period=1)
    t_poll(page)
    ls = leg_states(page)
    check("T12 the quarter ending without a NYG score is a MISS at the break, not later",
          ls["Q1"] == "miss", ls)
    check("T13 ...which kills the whole bet", page.evaluate(
        "EVALUATED.find(e => e.tk.name === 'Card 90').evalRes.outcome") == "dead")
    t_game("in", [[7, 0], [0, 0]], drive_team="NYG")
    t_poll(page)
    check("T14 a dead bet leaves the tracker entirely, whoever has the ball",
          not [t for t in team_tiles(page) if t["tag"] not in ("HIT", "CASHED")], team_tiles(page))
    check("T14b a leg's HIT tile names its quarter, not a bare 'Scores in Q'",
          all("SCORED IN Q1!" in t["text"] and "Scores in Q " not in t["text"]
              for t in team_tiles(page) if t["tag"] == "HIT")
          and any(t["tag"] == "HIT" for t in team_tiles(page)), team_tiles(page))
    check("T15 the row says why", "Q1 ended without a NYG score" in t_rows(page).get("NYG Scores in Q1", ""),
          t_rows(page).get("NYG Scores in Q1"))
    check("T16 no JavaScript errors", not errors, errors[:3])
    browser.close()

# A fresh page for the long way round: every quarter scored by both, into the
# 4th, where PHI have scored and NYG still owe one -- seven legs in.
t_game("in", [[7, 3, 7, 3], [3, 7, 7, 0]], drive_team="NYG")
with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("T17 seven legs in, NYG's Q4 the only one left", sum(v == "hit" for k, v in ls.items() if k.startswith("Q")) == 7
          and ls["Q7"] == "live", ls)
    ev = page.evaluate("EVALUATED.find(e => e.tk.name === 'Card 90').evalRes")
    check("T18 that is an IRON: one team left to score in the 4th", ev["outcome"] == "live" and ev["iron"], ev)
    tl = team_tiles(page)
    check("T19 ...and NYG's tile carries the waffle", len(tl) == 1 and tl[0]["name"] == "NYG" and tl[0]["iron"], tl)

    # Halftime with a team scoreless in the 2nd is called at the break too.
    t_game("in", [[7, 0], [3, 7]], break_="half", period=2)
    t_poll(page)
    ls = leg_states(page)
    check("T20 halftime with PHI scoreless in the 2nd: their Q2 leg is a miss right then",
          ls["Q2"] == "miss" and ls["Q3"] == "hit", ls)
    check("T21 the each-team-all-quarters leg agrees, at the same moment",
          ls["O9"] == "miss", ls)
    # ...but the SAME score mid-2nd is still live: nothing is called early.
    t_game("in", [[7, 0], [3, 7]], drive_team="PHI")
    t_poll(page)
    ls = leg_states(page)
    check("T22 mid-quarter, a team that hasn't scored YET is live, not a miss",
          ls["Q2"] == "live" and ls["O9"] == "live", ls)
    check("T23 no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- T2. a football stat prop is WAITING before kickoff ----------
# It read "live" pre-game -- only "is every game final" was asked -- while an
# anytime-TD leg on the same team read "not started" (2026-10-05).
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(96, [nfl_leg_m(1, "Big Catch", "PHI", "rec_yds", 29.5, "21"),
                               nfl_leg(2, "Big Run", "PHI", "23")])]}], "singles": []}
FX["mlb_sched"][DAY] = {"dates": []}
t_game("pre", [[], []])
with sync_playwright() as p:
    browser, page, errors = open_page(p)
    ls = leg_states(page)
    check("T2a before kickoff a yardage prop is not started, the same as a TD leg on his team",
          ls.get("O1") == "not_started" and ls.get("L2") == "not_started", ls)
    FX["espn_summaries"]["9001"] = espn_summary(
        "9001", "in", {"PHI": [("21", "Big Catch", "receiving", (2, 18, 0, 3))]}, (7, 0),
        quarters=[[7], [0]])
    FX["espn_events"] = {"9001": espn_event("9001", "in", (7, 0), [[7], [0]])}
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    check("T2b ...and live once the game is on", leg_states(page).get("O1") == "live", leg_states(page))
    check("T2c no JavaScript errors", not errors, errors[:3])
    browser.close()

# ---------- U. the side that hasn't batted yet ----------
# Top of the 1st: the home side has no plate appearances, so its next hitter
# couldn't be worked out and every home pick had no batting context -- the
# exact thing mlb_feed()'s own comment works AROUND by always adding a play.
# Seen live on Mookie Betts (2026-10-04). A side yet to bat leads off from 1.
FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb"],
                 "windows": [{"title": "Parlay Cards", "tickets": [
                     card(95, [mlb_leg(1, "H1", "HME"), mlb_leg(2, "H5", "HME")])]}], "singles": []}
FX["mlb_sched"][DAY] = mlb_schedule(DAY, [(6101, "Live", ["AWY", "HME"])])
FX["mlb_feeds"][6101] = mlb_feed("Live", [f"A{i}" for i in range(1, 10)] + [f"H{i}" for i in range(1, 10)],
                                 inning=1, state="Top", outs=0, batter="A1")
FX["espn_events"] = {}
with sync_playwright() as p:
    browser, page, errors = open_page(p)
    ctx = page.evaluate("liveContextForPlayer('H1')")
    check("U1 the home leadoff man has a batting context before his side has batted",
          bool(ctx) and ctx["slot"] == 1 and ctx["battersAway"] == 0, ctx)
    check("U2 ...and so does a hitter further down", (page.evaluate("liveContextForPlayer('H5')") or {}).get("slot") == 5)
    # Hockey (2026-10-05): shown, graded by nobody -- the user's call. It must
    # neither kill nor cash the parlay it sits in, and it must not be graded
    # as a Rays run line just because it says "Tampa Bay".
    nhl = page.evaluate("stateForLeg({sport: 'nhl', market: 'puck line +1.5', player: 'Tampa Bay Lightning', team: ''})")
    check("U4 an NHL leg is untracked, never graded as something else", nhl == "untracked", nhl)
    # A football moneyline arrives as `ml` -- baseball's key, whose grader
    # reads MLB scores by abbreviation. "Lions Moneyline" was graded off the
    # Tigers. Same team code, same live game: baseball's leg is graded, the
    # football one is left alone.
    both = page.evaluate("""[stateForLeg({market: 'ml', team: 'HME', player: null}),
                             stateForLeg({sport: 'nfl', market: 'ml', team: 'HME', player: null}),
                             liveTileKind({sport: 'nfl', market: 'ml', team: 'HME'}),
                             legSubjectName({sport: 'nfl', market: 'ml', team: 'HME', player: null})]""")
    check("U5 another sport's market is untracked, never graded off a baseball club with the same code",
          both[0] != "untracked" and both[1] == "untracked", both)
    check("U6 ...gets no tile, and still names its team", both[2] is None and both[3] == "HME", both)
    check("U3 no JavaScript errors", not errors, errors[:3])
    browser.close()


# ---------- V. a touchdown, and a drive ending, hold the pick's tile ----------
# The NFL page's Live Drives turns a pick's tile into TOUCHDOWN when he scores
# and into DRIVE OVER ("Punt", "TD — not him") when his offense gives the ball
# up. The front page never had either: a touchdown pick's tile just vanished
# the moment he scored (ported 2026-10-05). The drive fixture is built by hand
# because espn_drive() stamps every play 30 minutes before NOW, and a drive
# that ended that long ago is history, not news (the stale guard, V5).

def ended_drive(drive_id, team, result, at="2026-10-04T19:59:50Z", is_score=False):
    return {"id": drive_id, "team": {"abbreviation": team}, "displayResult": result,
            "description": "4 plays, 12 yards", "isScore": is_score,
            "plays": [{"id": f"{drive_id}-p", "text": result, "wallclock": at,
                       "end": {"team": {"id": TEAM_ID[team]}, "yardsToEndzone": 60, "downDistanceText": ""}}]}


def v_summaries(barkley_td=False, previous=None, kc_previous=None):
    phi = espn_summary("9001", "in", {"PHI": [("1", "Saquon Barkley", "rushing", (12, 60, 1 if barkley_td else 0))]},
                       (21 if barkley_td else 14, 10),
                       plays=[espn_scoring("vtd", "PHI", "Rushing Touchdown", "Saquon Barkley 3 Yd Run", 3, "1:10", 21, 10)]
                       if barkley_td else [],
                       current=espn_drive("v1", "PHI", 12, "1st & 10 at NYG 12") if not barkley_td else
                       espn_drive("v9", "NYG", 75, "1st & 10 at NYG 25"))
    phi["drives"]["previous"] = previous or []
    kc = espn_summary("9002", "in", {"KC": [("2", "Travis Kelce", "receiving", (4, 40, 0, 5))]}, (14, 10),
                      current=espn_drive("v2", "KC", 40, "2nd & 5 at DEN 40") if not kc_previous else
                      espn_drive("v8", "DEN", 80, "1st & 10 at DEN 20"))
    kc["drives"]["previous"] = kc_previous or []
    return {"9001": phi, "9002": kc}


def v_tiles(page):
    return page.eval_on_selector_all("#liveab-grid .ab-tile", """els => Object.fromEntries(els.map(e => [
        e.querySelector('.ab-name').textContent.trim(),
        { tag: e.querySelector('.ab-tag').textContent.trim(),
          result: (e.querySelector('.ab-result') || {}).textContent || '',
          ball: !!e.querySelector('.ab-bomb'), href: e.getAttribute('href') || '' }]))""")


FX["mlb_sched"] = {DAY: {"dates": []}}
FX["mlb_feeds"] = {}
FX["espn_events"] = {g: espn_event(g, "in", (14, 10)) for g in MAIN_GAMES}
FX["espn_summaries"] = v_summaries()
FX["tickets"] = TICKETS

with sync_playwright() as p:
    browser, page, errors = open_page(p)
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    before = v_tiles(page)
    check("V0 both picks are on the wall with their offense on the field",
          before.get("Saquon Barkley", {}).get("tag") in ("RED ZONE", "ON OFFENSE")
          and before.get("Travis Kelce", {}).get("tag") in ("RED ZONE", "ON OFFENSE"), before)

    # Barkley runs it in; KC's drive ends in a punt.
    FX["espn_summaries"] = v_summaries(barkley_td=True,
                                       previous=[ended_drive("v1", "PHI", "Touchdown", is_score=True)],
                                       kc_previous=[ended_drive("v2", "KC", "Punt")])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    t = v_tiles(page)
    bark = t.get("Saquon Barkley", {})
    check("V1 the pick who scored keeps his tile, now reading TOUCHDOWN", bark.get("tag") == "TOUCHDOWN", t)
    check("V2 ...opening on a football, and linking his game", bark.get("ball") and "espn.com" in bark.get("href", ""), bark)
    check("V3 his own drive's end doesn't replace it -- the touchdown outranks the drive summary",
          bark.get("result") == "Touchdown", bark)
    kel = t.get("Travis Kelce", {})
    check("V4 a pick whose offense just punted holds a DRIVE OVER tile with the result",
          kel.get("tag") == "DRIVE OVER" and kel.get("result") == "Punt", t)

    # Twenty seconds on: both results have expired, and KC's NEXT drive ends
    # in a touchdown somebody else scored.
    page.clock.set_fixed_time("2026-10-04T20:00:20Z")
    FX["espn_summaries"] = v_summaries(barkley_td=True,
                                       previous=[ended_drive("v1", "PHI", "Touchdown", is_score=True)],
                                       kc_previous=[ended_drive("v2", "KC", "Punt"),
                                                    ended_drive("v7", "KC", "Touchdown", at="2026-10-04T20:00:10Z", is_score=True),
                                                    # DENVER's drive, ending last: nothing to do with Kelce
                                                    ended_drive("v6", "DEN", "Interception", at="2026-10-04T20:00:12Z")])
    page.evaluate("SLATE_POLLS.clear()")
    poll(page)
    t = v_tiles(page)
    check("V5 a teammate's touchdown is said plainly: TD — not him -- and the other side's drive ending is not his",
          t.get("Travis Kelce", {}).get("result") == "TD — not him", t)
    # Penix on a passing-yards bet read "TD — not him" live, about a touchdown
    # he had just thrown: "not him" is only said to a touchdown bet.
    plain = page.evaluate("""() => NFL.resultTile({ kind: 'drive', tdBet: false, at: 0,
        pick: { player: 'QB One', odds: [], whos: [], iron: false },
        drive: { result: 'Touchdown', isScore: true, description: '10 plays', gamePk: '9001' } }, 0)""")
    check("V5b ...but a pick with no touchdown bet just reads Touchdown",
          ">Touchdown<" in plain and "not him" not in plain, plain)
    check("V6 ...and the expired touchdown tile is gone", t.get("Saquon Barkley", {}).get("tag") != "TOUCHDOWN", t)
    check("V7 no JavaScript errors", not errors, errors[:3])
    browser.close()

    # ---------- W. one name, two sports ----------
    # Seen live 2026-10-05: a Rays run line ("TB SPREAD +1.5") and a Lightning
    # puck line on one card were merged into ONE hockey tile reading "PUCK LINE
    # +114 SPREAD +240", because tiles were grouped by name alone. Each sport
    # gets its own tile, and where a name means two sports the card and the
    # tile say which.
    browser, page, errors = open_page(p)
    picks = page.evaluate("""() => {
        const rays = { player: null, team: 'TB', market: 'spread', line: 1.5, sport: 'mlb', odds: '+240', who: 'Kenny' };
        const bolts = { player: null, team: 'TB', market: 'nhl_pl', line: -1.5, sport: 'nhl', odds: '+114', who: 'Kenny' };
        const judge = { player: 'Aaron Judge', team: 'NYY', sport: 'mlb', odds: '+390', who: 'Memo' };
        const savedT = TICKETS, savedE = EVALUATED, savedS = EVALUATED_SINGLES;
        TICKETS = { date: TICKETS.date, windows: [{ title: 'x', tickets: [{ name: 'W', legs: [rays, bolts, judge] }] }], singles: [] };
        EVALUATED = [{ tk: TICKETS.windows[0].tickets[0], states: ['live', 'live', 'live'],
                       evalRes: { outcome: 'live', iron: false, hitCount: 0, activeCount: 3 } }];
        EVALUATED_SINGLES = [];
        try {
            return [...labEligiblePicks().values()].map(p => ({ norm: p.norm, player: p.player, kinds: [...p.kinds],
                                                              odds: p.odds, badge: p.badge }));
        } finally { TICKETS = savedT; EVALUATED = savedE; EVALUATED_SINGLES = savedS; }
    }""")
    tb = [x for x in picks if x["player"] == "TB"]
    check("W1 a Rays run line and a Lightning puck line are TWO tiles, not one merged one",
          len(tb) == 2 and {tuple(x["odds"]) for x in tb} == {("+240",), ("+114",)}, picks)
    check("W2 ...each labelled with its sport, because the name alone can't say which",
          sorted(re.sub("<[^>]+>", "", x["badge"]).strip() for x in tb) == ["MLB", "NHL"], tb)
    check("W3 ...and a name only one sport uses carries no label",
          [x["badge"] for x in picks if x["player"] == "Aaron Judge"] == [""], picks)
    check("W4 the bet card's legs say which sport too",
          page.evaluate("""() => { const saved = TICKETS;
              TICKETS = { windows: [{ tickets: [{ legs: [{ team: 'TB', market: 'spread', sport: 'mlb' },
                                                          { team: 'TB', market: 'nhl_pl', sport: 'nhl' }] }] }], singles: [] };
              try { return [sportTagFor('TB', 'mlb'), sportTagFor('TB', 'nhl'), sportTagFor('NYY', 'mlb')]
                             .map(h => h.replace(/<[^>]+>/g, '').trim()); }
              finally { TICKETS = saved; } }""") == ["MLB", "NHL", ""])
    browser.close()

    # ...and on the card as actually rendered, not just the helper.
    def tb_leg(i, market, line, sport, odds):
        return {"id": f"W{i}", "player": None, "team": "TB", "market": market, "line": line, "sport": sport,
                "who": "Kenny", "meta": "TB &middot; Kenny", "odds": odds, "time": "7:10 PM ET"}
    FX["tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb", "nhl"],
                     "windows": [{"title": "Parlay Cards", "tickets": [
                         card(6, [tb_leg(1, "spread", 1.5, "mlb", "+240"), tb_leg(2, "nhl_pl", -1.5, "nhl", "+114")])]}],
                     "singles": []}
    browser, page, errors = open_page(p)
    page.evaluate("toggleCardsSection('parlays')")
    page.wait_for_timeout(200)
    rows = page.eval_on_selector_all(".leg-player", r"els => els.map(e => e.textContent.replace(/\s+/g, ' ').trim())")
    check("W5 the card itself reads TB MLB and TB NHL", any("TB MLB" in r for r in rows) and any("TB NHL" in r for r in rows), rows)
    browser.close()
    FX["tickets"] = TICKETS

    # Opening the page AFTER all that: nothing is replayed.
    browser, page, errors = open_page(p, at="2026-10-04T20:00:25Z")
    page.evaluate("toggleLiveAb()")
    page.wait_for_timeout(200)
    t = v_tiles(page)
    check("V8 opening the page mid-game replays no touchdown and no drive result",
          not any(v["tag"] in ("TOUCHDOWN", "DRIVE OVER") for v in t.values()), t)
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    raise SystemExit(1)
print("all combined-page checks passed")
