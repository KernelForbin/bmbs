"""
cfb/index.html -- the college-football tracker (HIDDEN: complete, not in any
other page's sport switch) -- and college legs on all/index.html.

College football runs on the NFL engine (makeFootballEngine), so this checks
what is DIFFERENT about it, plus that it works end to end:
  * every request goes to ESPN's college-football host, never the NFL's;
  * a college game no pick is in is never fetched (a Saturday has 50+);
  * "played" is the per-game roster's `starter` flag, because college never
    sets didNotPlay -- a starter with no stat line is a miss, anyone else void;
  * on the All Sports page an NFL "MIA" and a college "MIA" never meet.
Fixture shapes are copied from a real college summary (VAN @ UGA, 2026-10-03).

    python tests/test_cfb.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
PAGES = {"/cfb/": REPO / "cfb" / "index.html", "/all/": REPO / "all" / "index.html"}
DAY = "2026-10-10"
NOW = "2026-10-10T21:00:00Z"          # 5:00 PM ET, a Saturday

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{ascii(detail)}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TEAM_ID = {"TEX": "251", "OU": "201", "OSU": "194", "PSU": "213", "MIA": "2390", "FSU": "52",
           "NFLMIA": "15", "BUF": "2"}
LABELS = {"rushing": ["CAR", "YDS", "AVG", "TD", "LONG"],
          "receiving": ["REC", "YDS", "AVG", "TD", "LONG"],
          "passing": ["C/ATT", "YDS", "AVG", "TD", "INT", "QBR"]}


def event(gid, away, home, state, score=(0, 0), period=3):
    def side(abbr, ha, pts):
        return {"id": TEAM_ID.get(abbr, abbr), "homeAway": ha, "score": str(pts),
                "team": {"abbreviation": "MIA" if abbr == "NFLMIA" else abbr}, "linescores": []}
    return {"id": gid, "date": f"{DAY}T19:30Z",
            "status": {"period": period, "displayClock": "8:00",
                       "type": {"state": state, "name": {"in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL",
                                                         "pre": "STATUS_SCHEDULED"}[state],
                                "shortDetail": {"pre": "3:30 PM", "in": "8:00 - 3rd", "post": "Final"}[state]}},
            "competitions": [{"competitors": [side(away, "away", score[0]), side(home, "home", score[1])]}]}


def drive(team, to_go, down_text):
    return {"id": "d1", "team": {"abbreviation": team}, "description": "5 plays, 40 yards", "isScore": False,
            "plays": [{"id": "d1-p", "text": "last play", "wallclock": f"{DAY}T20:50:00Z",
                       "end": {"team": {"id": TEAM_ID[team]}, "yardsToEndzone": to_go, "downDistanceText": down_text,
                               "shortDownDistanceText": down_text.split(" at ")[0]}}]}


def summary(ev, lines, plays=(), current=None):
    comp = json.loads(json.dumps(ev["competitions"][0]))
    comp["status"] = ev["status"]
    players = []
    for team, rows in lines.items():
        cats = {}
        for aid, name, cat, vals in rows:
            if cat == "rushing":
                stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0"]
            elif cat == "passing":
                stats = ["15/25", str(vals[0]), "8.0", str(vals[1]), "0", "0"]
            else:
                stats = [str(vals[0]), str(vals[1]), "0.0", str(vals[2]), "0"]
            cats.setdefault(cat, []).append({"athlete": {"id": aid, "displayName": name}, "stats": stats})
        players.append({"team": {"abbreviation": team},
                        "statistics": [{"name": c, "labels": LABELS[c], "athletes": a} for c, a in cats.items()]})
    drives = {"previous": []}
    if current:
        drives["current"] = current
    return {"header": {"competitions": [comp]}, "boxscore": {"players": players},
            "scoringPlays": list(plays), "drives": drives}


def leg(i, player, team, aid, market="td", line=None, sport="cfb", who="Kenny"):
    l = {"id": f"C{i}", "player": player, "team": team, "athleteId": aid, "who": who, "meta": team,
         "odds": "+150", "time": "3:30 PM ET", "sport": sport, "market": market}
    if line is not None:
        l["line"] = line
    return l


def card(n, legs):
    return {"name": f"Card {n}", "sub": f"{len(legs)}-Leg", "foot": "<b>$5.00</b> bet", "stake": 5.0,
            "book": "Kenny", "payout": 50.0, "legs": legs}


CARD = {"date": DAY, "endDate": DAY, "note": "", "sports": ["cfb"], "windows": [{"title": "Parlay Cards", "tickets": [
    card(1, [leg(1, "Arch Manning", "TEX", "11"), leg(2, "Jeremiah Smith", "OSU", "21")]),
    card(2, [leg(3, "Jeremiah Smith", "OSU", "21", "rec_yds", 89.5), leg(4, "Arch Manning", "TEX", "11", "pass_yds", 224.5)]),
    card(3, [leg(5, "Backup Back", "TEX", "12"), leg(6, "Starting Tight End", "TEX", "13")]),
]}], "singles": []}

TD_PLAY = {"id": "sp1", "type": {"text": "Passing Touchdown"}, "text": "Jeremiah Smith 40 Yd pass from Julian Sayin",
           "period": {"number": 2}, "clock": {"value": 300.0, "displayValue": "5:00"}, "team": {"abbreviation": "OSU"},
           "awayScore": 7, "homeScore": 0, "wallclock": f"{DAY}T20:10:00Z"}

FX = {}
SEEN = []
ROSTER_ASKS = []


def reset_live():
    FX["events"] = {"cfb": [event("701", "TEX", "OU", "in", (14, 10)), event("702", "OSU", "PSU", "in", (7, 3)),
                            event("703", "MIA", "FSU", "in", (21, 0))],
                    "nfl": []}
    FX["summaries"] = {
        "701": summary(FX["events"]["cfb"][0], {"TEX": [("11", "Arch Manning", "passing", (180, 1)),
                                                        ("11", "Arch Manning", "rushing", (6, 20, 0))]},
                       current=drive("TEX", 15, "2nd & 7 at OU 15")),
        "702": summary(FX["events"]["cfb"][1], {"OSU": [("21", "Jeremiah Smith", "receiving", (5, 60, 1))]}, plays=[TD_PLAY]),
    }
    FX["tickets"] = CARD
    FX["all_tickets"] = None


def handler(route, request):
    url = request.url
    SEEN.append(url)
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    for prefix, page in PAGES.items():
        if path in (prefix, prefix + "index.html"):
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=page.read_bytes())
    if path.endswith("tickets-previous.json"):
        return route.fulfill(status=404, body="")
    if path.endswith("/data/cfb/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["tickets"]))
    if path.endswith("/data/combined/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["all_tickets"]))
    m = re.search(r"/football/(college-football|nfl)/scoreboard\?dates=(\d{8})", url)
    if m:
        evs = FX["events"]["cfb" if m.group(1) == "college-football" else "nfl"] if m.group(2) == DAY.replace("-", "") else []
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    m = re.search(r"/football/(college-football|nfl)/summary\?event=(\d+)", url)
    if m:
        # A game with no fixture is one the page should never have asked for:
        # a 404 lets A5 say so, where raising here would just hang the page.
        if m.group(2) not in FX["summaries"]:
            return route.fulfill(status=404, body="")
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["summaries"][m.group(2)]))
    m = re.search(r"/leagues/(college-football|nfl)/events/\d+/competitions/\d+/competitors/(\d+)/roster", url)
    if m:
        ROSTER_ASKS.append(m.group(1))
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX.get("roster", {"entries": []})))
    if "statsapi.mlb.com" in url and "/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"dates": []}))
    if "fonts.g" in url:
        return route.fulfill(status=200, content_type="text/css", body="")
    return route.abort()


def poll(page):
    page.evaluate("SLATE_POLLS.clear(); pollAndRender()")
    page.wait_for_timeout(300)


def open_page(p, path):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 480, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)
    page.goto(f"http://bmbs.test{path}")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    poll(page)
    return browser, page, errors


def leg_states(page):
    return page.evaluate("""() => Object.fromEntries(EVALUATED.flatMap(e => e.tk.legs.map((l, i) => [l.id, e.states[i]])))""")


def tiles(page):
    page.evaluate("if (document.getElementById('liveab-section').classList.contains('collapsed')) toggleLiveAb()")
    page.wait_for_timeout(200)
    return page.eval_on_selector_all("#liveab-grid .ab-tile", """els => els.map(e => ({
        name: e.querySelector('.ab-name').textContent.trim(), tag: e.querySelector('.ab-tag').textContent.trim(),
        text: e.textContent.replace(/\\s+/g, ' ').replace(/[^\\x00-\\x7F]/g, '')}))""")


with sync_playwright() as p:
    # ================= A. the hidden page =================
    reset_live()
    browser, page, errors = open_page(p, "/cfb/")
    check("A1 the college page loads as the College Football tracker",
          page.inner_text("h1") == "BMBS Tracker — College Football", page.inner_text("h1"))
    hidden = {path: 'href="/cfb/"' in (REPO / path).read_text(encoding="utf-8")
              for path in ("index.html", "football/index.html", "all/index.html", "hockey/index.html",
                           "basketball/index.html", "wnba/index.html", "features/index.html")}
    check("A2 ...and it is HIDDEN: no other page links to it", not any(hidden.values()), hidden)
    keys = page.evaluate("""() => [...document.scripts].map(s => s.textContent).join('').match(/"bmbs\\.[a-z]+\\./g) || []""")
    check("A3 every storage key it writes is its own (bmbs.cf.*)", keys and all(k == '"bmbs.cf.' for k in keys), sorted(set(keys)))
    browser.close()

    # A4-D run on BOTH pages: /cfb/ is a generated copy of the All Sports
    # page's engine, so a rule must be pinned in each -- a mutation to one
    # copy is invisible to checks that only load the other.
    for PAGE_PATH, TAG in (("/cfb/", ""), ("/all/", "all:")):
        reset_live()
        FX["all_tickets"] = CARD
        SEEN.clear()
        ROSTER_ASKS.clear()
        browser, page, errors = open_page(p, PAGE_PATH)
        check(TAG + "A4 every football request goes to the COLLEGE host, never the NFL's",
              any("/college-football/" in u for u in SEEN) and not [u for u in SEEN if "/football/nfl/" in u],
              [u for u in SEEN if "/football/" in u][:3])
        check(TAG + "A5 a college game no pick is in is never fetched (MIA @ FSU)",
              not [u for u in SEEN if "summary?event=703" in u], [u for u in SEEN if "summary" in u])
        check(TAG + "A6 nor MLB, the NHL or basketball", not [u for u in SEEN if "statsapi" in u or "/hockey/" in u or "/basketball/" in u])

        # ================= B. grading, live =================
        ls = leg_states(page)
        check(TAG + "B1 a college touchdown is a hit the moment it's in the box score", ls.get("C2") == "hit", ls)
        check(TAG + "B2 no touchdown yet: live", ls.get("C1") == "live", ls)
        check(TAG + "B3 yardage props count up: 60 receiving yards of 90, 180 passing of 225 -- live",
              ls.get("C3") == "live" and ls.get("C4") == "live", ls)

        # ================= C. the tracker =================
        tl = {t["name"]: t for t in tiles(page)}
        am = tl.get("Arch Manning", {})
        check(TAG + "C1 a college pick whose side has the ball inside the 20 is in the RED ZONE", am.get("tag") == "RED ZONE", tl)
        check(TAG + "C2 ...with what he's chasing: 0 of 1 TD and 180 of 225 pass yds",
              "0 of 1 TD" in am.get("text", "") and "180 of 225 pass yds" in am.get("text", ""), am.get("text"))
        check(TAG + "C3 no JS errors", not errors, errors[:3])

        # ================= D. the final: who PLAYED, college's way =================
        # College never sets didNotPlay; the per-game roster's `starter` is the
        # only thing that says a man got in. A starter with no stat line is a
        # miss (he played and didn't score); anyone else with none is void.
        FX["events"]["cfb"][0] = event("701", "TEX", "OU", "post", (28, 24), period=4)
        FX["summaries"]["701"] = summary(FX["events"]["cfb"][0], {"TEX": [("11", "Arch Manning", "passing", (240, 2)),
                                                                          ("11", "Arch Manning", "rushing", (8, 30, 0))]})
        FX["roster"] = {"entries": [{"playerId": 12, "starter": False, "didNotPlay": False},
                                    {"playerId": 13, "starter": True, "didNotPlay": False},
                                    {"playerId": 11, "starter": True, "didNotPlay": False}]}
        poll(page)
        ls = leg_states(page)
        check(TAG + "D1 the final: Manning threw for 240 -- his passing over clears", ls.get("C4") == "hit", ls)
        check(TAG + "D2 ...and never scored himself: his anytime TD misses", ls.get("C1") == "miss", ls)
        check(TAG + "D3 a NON-starter with no stat line is VOID -- college can't say he played", ls.get("C5") == "na", ls)
        check(TAG + "D4 a STARTER with no stat line is a MISS -- he played", ls.get("C6") == "miss", ls)
        check(TAG + "D5 the per-game roster was asked of the college league", "college-football" in ROSTER_ASKS and "nfl" not in ROSTER_ASKS,
              ROSTER_ASKS)
        check(TAG + "D6 no JS errors", not errors, errors[:3])
        browser.close()


    # ================= E. college and NFL on one card =================
    # The same abbreviation in both leagues: the Miami Hurricanes and the
    # Miami Dolphins are both "MIA". Each leg must be graded by its own
    # league's game, and fired by its own league's touchdown.
    reset_live()
    FX["events"]["nfl"] = [event("801", "BUF", "NFLMIA", "in", (3, 0))]
    FX["summaries"]["801"] = summary(FX["events"]["nfl"][0], {"MIA": [("31", "Miami Back", "rushing", (5, 22, 0))]})
    FX["summaries"]["703"] = summary(FX["events"]["cfb"][2], {"MIA": [("41", "Hurricane Back", "rushing", (9, 70, 2))]})
    FX["all_tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nfl", "cfb"],
                         "windows": [{"title": "Parlay Cards", "tickets": [
                             card(10, [leg(1, "Hurricane Back", "MIA", "41"), leg(2, "Miami Back", "MIA", "31", sport="nfl")]),
                             card(11, [leg(3, "Cane Receiver", "MIA", "42"), leg(4, "Arch Manning", "TEX", "11")]),
                         ]}], "singles": []}
    SEEN.clear()
    browser, page, errors = open_page(p, "/all/")
    ls = leg_states(page)
    check("E1 two 'MIA's: the college back's touchdowns are his, the NFL back has none -- each league's own game",
          ls.get("C1") == "hit" and ls.get("C2") == "live", ls)
    check("E2 each league asked its own host", any("/college-football/summary?event=703" in u for u in SEEN)
          and any("/football/nfl/summary?event=801" in u for u in SEEN), [u for u in SEEN if "summary" in u])
    page.evaluate("""() => { window.TDS = []; const real = fireTouchdown;
        fireTouchdown = (name, cash) => { TDS.push(name); return real(name, cash); }; }""")
    FX["summaries"]["801"] = summary(FX["events"]["nfl"][0], {"MIA": [("31", "Miami Back", "rushing", (7, 40, 1))]})
    FX["summaries"]["703"] = summary(FX["events"]["cfb"][2], {"MIA": [("41", "Hurricane Back", "rushing", (9, 70, 2)),
                                                                      ("42", "Cane Receiver", "receiving", (3, 50, 1))]})
    poll(page)
    check("E3 touchdowns after the page is open alert in BOTH leagues -- the one already scored at load stays quiet",
          sorted(page.evaluate("TDS")) == ["Cane Receiver", "Miami Back"], page.evaluate("TDS"))
    check("E4 no JS errors", not errors, errors[:3])
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("all college-football checks passed")
