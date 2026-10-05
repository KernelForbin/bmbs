"""
hockey/index.html -- the NHL tracker (HIDDEN: complete, not in any other page's
sport switch) -- and hockey legs on all/index.html, the All Sports tracker.

Everything is served from in-memory fixtures through one route handler: the
tickets files, ESPN's NHL scoreboard and summary, and (for the All Sports
page) the MLB schedule and feed. Anything else is aborted, so a missed host
fails loudly instead of reaching the internet.

    python tests/test_hockey.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
PAGES = {"/hockey/": REPO / "hockey" / "index.html", "/all/": REPO / "all" / "index.html"}
DAY = "2026-10-05"
NOW = "2026-10-05T23:50:00Z"          # 7:50 PM ET

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{ascii(detail)}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- ESPN NHL fixtures ----------------
TEAM = {"PHI": "15", "TB": "20"}
SKATER_LABELS = ["BS", "HT", "TK", "+/-", "TOI", "PPTOI", "SHTOI", "ESTOI", "SHFT", "G", "YTDG", "A", "S", "SM", "SOG",
                 "FW", "FL", "FO%", "GV", "PN", "PIM"]
GOALIE_LABELS = ["GA", "SA", "SOS", "SOSA", "SV", "SV%", "ESSV", "PPSV", "SHSV", "TOI", "YTDG", "PIM"]


def skater(aid, name, g=0, a=0, sog=0, toi="14:00"):
    row = {k: "0" for k in SKATER_LABELS}
    row.update({"G": str(g), "A": str(a), "SOG": str(sog), "TOI": toi})
    return {"athlete": {"id": aid, "displayName": name}, "stats": [row[k] for k in SKATER_LABELS]}


def goalie(aid, name, sv, ga):
    row = {k: "0" for k in GOALIE_LABELS}
    row.update({"SV": str(sv), "GA": str(ga), "SA": str(sv + ga), "TOI": "40:00"})
    return {"athlete": {"id": aid, "displayName": name}, "stats": [row[k] for k in GOALIE_LABELS]}


def event(state, score, period=2, clock="10:00", name=None):
    away, home = score
    return {"id": "9101", "date": f"{DAY}T23:00Z",
            "status": {"period": period, "displayClock": clock,
                       "type": {"state": state, "name": name or {"pre": "STATUS_SCHEDULED", "in": "STATUS_IN_PROGRESS",
                                                                "post": "STATUS_FINAL"}[state],
                                "shortDetail": {"pre": "7:00 PM", "in": f"{clock} - 2nd", "post": "Final"}[state]}},
            "competitions": [{"competitors": [
                {"id": TEAM["PHI"], "homeAway": "away", "score": str(away), "team": {"abbreviation": "PHI"}},
                {"id": TEAM["TB"], "homeAway": "home", "score": str(home), "team": {"abbreviation": "TB"}}]}]}


def play(kind, team, parts, wall, period=2, strength="even-strength", away=0, home=0):
    return {"type": {"text": kind}, "team": {"id": TEAM[team]}, "period": {"number": period},
            "clock": {"displayValue": "5:00"}, "wallclock": wall, "awayScore": away, "homeScore": home,
            "scoringPlay": kind == "Goal", "strength": {"abbreviation": strength, "text": strength.replace("-", " ").title()},
            "text": f"{kind} play", "id": f"p{abs(hash(wall + kind)) % 99999}",
            "participants": [{"athlete": {"id": aid, "displayName": n}, "type": role} for aid, n, role in parts]}


def summary(state, score, tb_lines, phi_lines, tb_goalie, plays, on_ice=None, period=2, clock="10:00", name=None):
    ev = event(state, score, period, clock, name)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    side = lambda abbr, lines, g: {"team": {"abbreviation": abbr}, "statistics": [
        {"name": "forwards", "labels": SKATER_LABELS, "athletes": lines},
        {"name": "goalies", "labels": GOALIE_LABELS, "athletes": [g] if g else []}]}
    out = {"header": {"competitions": [comp]},
           "boxscore": {"players": [side("PHI", phi_lines, None), side("TB", tb_lines, tb_goalie)]},
           "plays": plays}
    if on_ice is not None:
        out["onIce"] = [{"entries": [{"athleteid": a, "whereabouts": {"name": "ROSTER_WHEREABOUTS_IN_PLAY"}} for a in on_ice]}]
    return out


def leg(i, player, team, market, line=None, side=None, aid="", who="Kenny", odds="+200"):
    l = {"id": f"H{i}", "player": player, "team": team, "who": who, "meta": team, "odds": odds, "time": "",
         "sport": "nhl", "market": market}
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


HOCKEY = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nhl"], "windows": [{"title": "Parlay Cards", "tickets": [
    card(1, [leg(1, "Nikita Kucherov", "TB", "nhl_goal", aid="1"),
             leg(2, "Brayden Point", "TB", "nhl_points", 1.5, "over", aid="2")]),
    card(2, [leg(3, "Andrei Vasilevskiy", "TB", "nhl_saves", 20.5, "over", aid="3"),
             leg(4, "Travis Konecny", "PHI", "nhl_sog", 2.5, "over", aid="4")]),
    card(3, [leg(5, "Tampa Bay Lightning", "TB", "nhl_pl", -1.5),
             dict(leg(6, "Tampa Bay Lightning / Philadelphia Flyers", "TB", "nhl_total", 5.5, "over"),
                  opponent="PHI", teams=["TB", "PHI"])]),
    card(4, [leg(7, "Scratched Guy", "TB", "nhl_goal", aid="9"), leg(8, "Nikita Kucherov", "TB", "nhl_sog", 0.5, "over", aid="1")]),
]}], "singles": [dict(leg(9, "Tampa Bay Lightning", "TB", "nhl_ml"), id="single-0", stake=5.0, payout=8.33)]}

LIVE_PLAYS = [play("Goal", "TB", [("2", "Brayden Point", "scorer")], "2026-10-05T23:20:00Z", 1, away=0, home=1),
              play("Goal", "PHI", [("4", "Travis Konecny", "scorer")], "2026-10-05T23:30:00Z", 1, away=1, home=1),
              play("Shot", "TB", [("1", "Nikita Kucherov", "shooter"), ("5", "Sam Ersson", "saver")], "2026-10-05T23:35:00Z"),
              play("Hooking", "PHI", [("4", "Travis Konecny", "penaltyOn")], "2026-10-05T23:44:00Z", strength="short-handed")]


def live_summary():
    return summary("in", (1, 1),
                   [skater("1", "Nikita Kucherov", sog=1), skater("2", "Brayden Point", g=1, sog=2)],
                   [skater("4", "Travis Konecny", g=1, sog=3)], goalie("3", "Andrei Vasilevskiy", 15, 1),
                   LIVE_PLAYS, on_ice=["1", "3"])


FX = {"tickets": HOCKEY, "summary": live_summary(), "event": event("in", (1, 1)), "mlb_sched": {"dates": []}, "mlb_feed": None,
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
    if path.endswith("/data/hockey/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["tickets"]))
    if path.endswith("/data/combined/tickets.json"):
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["all_tickets"]))
    if "/hockey/nhl/scoreboard" in url:
        evs = [FX["event"]] if DAY.replace("-", "") in url else []
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    if "/hockey/nhl/summary" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["summary"]))
    if "statsapi.mlb.com" in url and "/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["mlb_sched"]))
    if "statsapi.mlb.com" in url and "/feed/live" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["mlb_feed"]))
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
        text: e.textContent.replace(/\\s+/g, ' ').replace(/[^\\x00-\\x7F]/g, '')}))""")


with sync_playwright() as p:
    # ================= A. the hidden page itself =================
    browser, page, errors = open_page(p, "/hockey/")
    check("A1 the NHL page loads as the NHL tracker", page.inner_text("h1") == "BMBS Tracker — NHL", page.inner_text("h1"))
    sw = page.eval_on_selector_all(".sport-switch .sport", "els => els.map(e => [e.textContent.trim(), e.classList.contains('active')])")
    check("A2 its own switch marks NHL active", any(n.endswith("NHL") and on for n, on in sw), sw)
    hidden = {path: 'href="/hockey/"' in (REPO / path).read_text(encoding="utf-8")
              for path in ("index.html", "football/index.html", "all/index.html", "features/index.html")}
    check("A3 ...and it is HIDDEN: no other page links to it", not any(hidden.values()), hidden)
    keys = page.evaluate("""() => [...document.scripts].map(s => s.textContent).join('').match(/"bmbs\\.[a-z]+\\./g) || []""")
    check("A4 every storage key it writes is its own (bmbs.hk.*), never another page's",
          keys and all(k == '"bmbs.hk.' for k in keys), sorted(set(keys)))
    check("A5 a hockey-only card never asks MLB for anything -- the baseball engine stands down",
          not [u for u in SEEN if "statsapi.mlb.com" in u], [u for u in SEEN if "statsapi" in u][:2])

    # ================= B. grading, live =================
    ls = leg_states(page)
    check("B1 an anytime goal with no goal yet is live", ls.get("H1") == "live", ls)
    check("B2 points under the line are live (1 of 2)", ls.get("H2") == "live", ls)
    check("B3 goalie saves still short are live", ls.get("H3") == "live", ls)
    check("B4 shots on goal clear the moment they pass the line: 3 > 2.5", ls.get("H4") == "hit", ls)
    check("B5 a moneyline and a puck line wait for the final", ls.get("single-0") == "live" and ls.get("H5") == "live", ls)
    check("B6 a total's over isn't there yet at 2 goals", ls.get("H6") == "live", ls)
    # No market written means a GOAL for a hockey leg, the way it means a home
    # run for baseball's and a touchdown for football's. Point has one.
    bare = page.evaluate("[stateForLeg({sport: 'nhl', player: 'Brayden Point', team: 'TB', athleteId: '2'}),"
                         " stateForLeg({sport: 'nhl', player: 'Nikita Kucherov', team: 'TB', athleteId: '1'})]")
    check("B7 a hockey leg with no market is an anytime goal: Point's has hit, Kucherov's is live", bare == ["hit", "live"], bare)

    # ================= C. the tracker =================
    page.evaluate("if (document.getElementById('liveab-section').classList.contains('collapsed')) toggleLiveAb()")
    page.wait_for_timeout(200)
    tl = {t["name"]: t for t in tiles(page)}
    kuch = tl.get("Nikita Kucherov", {})
    check("C1 a skater ESPN lists in play is ON THE ICE", kuch.get("tag") == "ON THE ICE", tl.keys())
    check("C2 ...and a power play is named: PHI took the penalty, TB is a man up", "POWER PLAY" in kuch.get("text", ""), kuch.get("text"))
    check("C3 ...with what he's chasing: 0 of 1 goals", "0 of 1 goals" in kuch.get("text", ""), kuch.get("text"))
    check("C4 a skater not listed in play is ON THE BENCH -- never a guess", tl.get("Brayden Point", {}).get("tag") == "ON THE BENCH",
          tl.get("Brayden Point"))
    check("C5 ...still showing his count: 1 of 2 points", "1 of 2 points" in tl.get("Brayden Point", {}).get("text", ""))
    vas = tl.get("Andrei Vasilevskiy", {})
    check("C6 a goalie is IN NET, with his saves", vas.get("tag") == "IN NET" and "15 of 21 saves" in vas.get("text", ""), vas)
    tb = tl.get("TB", {})
    check("C7 a team's bets share ONE tile listing each -- the Lightning's moneyline AND puck line",
          tb.get("tag") == "TEAM BETS" and "MONEYLINE" in tb.get("text", "") and "PUCK LINE -1.5" in tb.get("text", ""), tb)
    check("C7b ...and a game total gets its own, with the goal count", "2 goals, needs 6" in
          next((t["text"] for t in tl.values() if t["tag"] == "TOTAL GOALS"), ""), [t["tag"] for t in tl.values()])
    raw = [(n, t["text"]) for n, t in tl.items() if re.search(r"&(?:[a-z]+|#\d+);", t["text"])]
    check("C7c no tile shows a raw HTML entity as text", not raw, raw)
    # ESPN's `onIce` is the one feed field not yet seen in a LIVE game. When
    # it's missing the page must say only what it knows -- "LIVE" -- and never
    # call a skater benched off the absence of a list.
    FX["summary"] = live_summary()
    del FX["summary"]["onIce"]
    poll(page)
    tl2 = {t["name"]: t for t in tiles(page)}
    check("C9 no onIce in the feed: a skater's tile is plain LIVE, never ON THE ICE or ON THE BENCH",
          tl2.get("Brayden Point", {}).get("tag") == "LIVE" and tl2.get("Nikita Kucherov", {}).get("tag") == "LIVE",
          {k: v.get("tag") for k, v in tl2.items()})
    FX["summary"] = live_summary()
    poll(page)
    check("C8 no JS errors", not errors, errors[:3])

    # ================= D. a goal: hit, alert, log, bell =================
    page.evaluate("""() => { window.FIRED = []; const real = fireLegHit;
        fireLegHit = (slate, leg, cash) => { FIRED.push(legAlertName(leg) + ' ' + legHitWord(leg)); return real(slate, leg, cash); }; }""")
    goal = play("Goal", "TB", [("1", "Nikita Kucherov", "scorer"), ("2", "Brayden Point", "assister")],
                 "2026-10-05T23:48:00Z", 2, strength="power-play", away=1, home=2)
    FX["summary"] = summary("in", (1, 2), [skater("1", "Nikita Kucherov", g=1, sog=2), skater("2", "Brayden Point", g=1, a=1, sog=2)],
                            [skater("4", "Travis Konecny", g=1, sog=3)], goalie("3", "Andrei Vasilevskiy", 15, 1),
                            LIVE_PLAYS + [goal], on_ice=["1", "3"])
    FX["event"] = event("in", (1, 2))
    poll(page)
    ls = leg_states(page)
    check("D1 the goal is a hit the moment it's in", ls.get("H1") == "hit", ls)
    check("D2 ...and his assist takes Point to 2 points -- a hit too", ls.get("H2") == "hit", ls)
    fired = page.evaluate("FIRED")
    check("D3 it alerts, in hockey's words: GOAL!", "Nikita Kucherov GOAL!" in fired, fired)
    log = page.evaluate("""() => [...document.querySelectorAll('#hrlog-list .hr-row')].map(r => [r.querySelector('.hr-batter').textContent,
        r.classList.contains('ours'), r.querySelector('.hr-ev').textContent])""")
    check("D4 the Scoring Log lists the goal as OURS, tagged a power-play goal",
          ["Nikita Kucherov", True, "PPG"] in log, log)
    t = page.evaluate("bellTime(BELL.items.find(i => i.kind === 'leg' && i.who === 'Nikita Kucherov' && /Anytime Goal/.test(i.what)) || {})")
    check("D5 the bell times it off the goal itself: 7:48 PM ET", t == "7:48 PM ET", t)
    check("D6 no JS errors", not errors, errors[:3])

    # ================= E. the final =================
    FX["summary"] = summary("post", (1, 4), [skater("1", "Nikita Kucherov", g=1, sog=4), skater("2", "Brayden Point", g=1, a=1, sog=2)],
                            [skater("4", "Travis Konecny", g=1, sog=3)], goalie("3", "Andrei Vasilevskiy", 25, 1),
                            LIVE_PLAYS + [goal], period=3, clock="0:00")
    FX["event"] = event("post", (1, 4), 3, "0:00")
    poll(page)
    # Every game on the card final: the slate has rolled to Yesterday.
    page.evaluate("activateTab('yesterday')")
    page.wait_for_timeout(200)
    ls = leg_states(page)
    check("E1 the moneyline settles a win at the final", ls.get("single-0") == "hit", ls)
    check("E2 the puck line covers: 4 - 1.5 beats 1", ls.get("H5") == "hit", ls)
    check("E3 the total misses: 5 goals isn't over 5.5", ls.get("H6") == "miss", ls)
    check("E4 the goalie's 25 saves clear 20.5", ls.get("H3") == "hit", ls)
    check("E5 a player who never dressed (no boxscore line) is VOID, not a miss", ls.get("H7") == "na", ls)
    check("E6 no JS errors", not errors, errors[:3])
    browser.close()

    # ================= F. hockey on the All Sports tab =================
    FX["summary"], FX["event"] = live_summary(), event("in", (1, 1))
    FX["all_tickets"] = {"date": DAY, "endDate": DAY, "note": "", "sports": ["nhl"],
                         "windows": [{"title": "Parlay Cards", "tickets": [
                             card(10, [leg(1, "Nikita Kucherov", "TB", "nhl_goal", aid="1"),
                                       leg(4, "Travis Konecny", "PHI", "nhl_sog", 2.5, "over", aid="4")])]}],
                         "singles": []}
    SEEN.clear()
    browser, page, errors = open_page(p, "/all/")
    check("F1 the tab is renamed ALL SPORTS", page.inner_text("h1") == "BMBS Tracker — All Sports" and
          "ALL SPORTS" in page.inner_text(".sport-switch"), page.inner_text("h1"))
    ls = leg_states(page)
    check("F2 the All Sports tracker grades hockey legs the same way", ls.get("H1") == "live" and ls.get("H4") == "hit", ls)
    check("F3 ...and its switch still has no NHL tab (hidden)", "NHL" not in page.inner_text(".sport-switch"),
          page.inner_text(".sport-switch"))
    check("F4 an engine-less sport never asks MLB for anything", not [u for u in SEEN if "statsapi" in u])
    check("F5 no JS errors", not errors, errors[:3])
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("all hockey checks passed")
