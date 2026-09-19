"""
Football's results archive, end to end and fully offline:

  1. scripts/record_football_results.py grading a finished NFL week against a
     fake ESPN, into a temp directory (never data/football/)
  2. the history.json it builds from those records
  3. football/history/index.html rendering that file in headless Chromium --
     including the empty state football starts in, and the isolation guarantee
     (it must never reach ESPN, the slate files, or anything of baseball's)

    python tests/test_football_history.py
"""
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import record_football_results as rf   # noqa: E402

PAGE_SRC = (REPO / "football" / "history" / "index.html").read_text(encoding="utf-8")
TRACKER_SRC = (REPO / "football" / "index.html").read_text(encoding="utf-8")
ORIGIN = "http://bmbs.test"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- a fake ESPN: Sunday DET@BUF, Monday JAX@KC ----------------

def event(eid, away, home, state):
    return {"id": eid, "date": "2026-09-20T17:00Z", "status": {"type": {"state": state}},
            "competitions": [{"competitors": [{"id": away[0], "homeAway": "away", "team": {"abbreviation": away[1]}, "score": "24"},
                                              {"id": home[0], "homeAway": "home", "team": {"abbreviation": home[1]}, "score": "20"}]}]}


def cat(name, labels, rows):
    return {"name": name, "labels": labels, "athletes": [{"athlete": {"id": i, "displayName": n}, "stats": st} for i, n, st in rows]}


def summary(state, away, home, box, scoring):
    return {"header": {"competitions": [{"status": {"type": {"state": state}}, "competitors": [
                {"id": away[0], "homeAway": "away", "team": {"abbreviation": away[1]}, "score": "24"},
                {"id": home[0], "homeAway": "home", "team": {"abbreviation": home[1]}, "score": "20"}]}]},
            "boxscore": {"players": [{"team": {"abbreviation": abbr}, "statistics": cats} for abbr, cats in box]},
            "scoringPlays": scoring}


def td(pid, text, abbr, kind="Rushing Touchdown"):
    return {"id": pid, "type": {"text": kind}, "text": text, "team": {"abbreviation": abbr},
            "period": {"number": 2}, "clock": {"displayValue": "4:10"}, "awayScore": 7, "homeScore": 0}


DET_BUF = summary("post", ("8", "DET"), ("2", "BUF"), [
    ("DET", [cat("passing", ["C/ATT", "YDS", "TD"], [("100", "Jared Goff", ["20/30", "250", "2"])]),
             cat("rushing", ["CAR", "YDS", "TD"], [("101", "Jahmyr Gibbs", ["18", "96", "2"]), ("100", "Jared Goff", ["2", "3", "0"])]),
             cat("receiving", ["REC", "YDS", "TD", "TGTS"], [("102", "Amon-Ra St. Brown", ["7", "88", "0", "9"])])]),
    ("BUF", [cat("rushing", ["CAR", "YDS", "TD"], [("200", "Josh Allen", ["6", "31", "0"]), ("201", "James Cook III", ["15", "70", "1"])])]),
], [td("1", "Jahmyr Gibbs 1 Yd Rush (Jake Bates Kick)", "DET"), td("2", "Jahmyr Gibbs 12 Yd Rush (Jake Bates Kick)", "DET"),
    td("3", "James Cook III 4 Yd Rush (Kick)", "BUF"),
    {"id": "4", "type": {"text": "Field Goal Good"}, "text": "Jake Bates 40 Yd Field Goal", "team": {"abbreviation": "DET"}}])

JAX_KC = summary("post", ("30", "JAX"), ("12", "KC"), [
    ("JAX", [cat("interceptions", ["INT", "YDS", "TD"], [("300", "Josh Allen", ["1", "45", "1"])])]),
    ("KC", [cat("receiving", ["REC", "YDS", "TD", "TGTS"], [("400", "Travis Kelce", ["5", "60", "0", "8"])])]),
], [td("9", "Josh Allen 45 Yd Interception Return (Kick)", "JAX", "Interception Return Touchdown")])


def fake_espn(monday_state="post", log=None):
    def fetcher(url):
        if log is not None:
            log.append(url)
        if "scoreboard?dates=20260920" in url:
            return {"events": [event("501", ("8", "DET"), ("2", "BUF"), "post")]}
        if "scoreboard?dates=20260921" in url:
            return {"events": [event("502", ("30", "JAX"), ("12", "KC"), monday_state)]}
        if "scoreboard" in url:
            return {"events": []}
        if "summary?event=501" in url:
            return DET_BUF
        if "summary?event=502" in url:
            return dict(JAX_KC, header={"competitions": [dict(JAX_KC["header"]["competitions"][0], status={"type": {"state": monday_state}})]})
        if "/competitors/12/roster" in url:   # KC: one man inactive, one dressed and never touched the ball
            return {"entries": [{"playerId": "401", "didNotPlay": True}, {"playerId": "402", "didNotPlay": False}]}
        raise AssertionError("unexpected url " + url)
    return fetcher


def L(player, aid, team, who, odds):
    return {"id": "x", "player": player, "athleteId": aid, "team": team, "who": who, "odds": odds, "time": "1:00 PM ET"}


TICKETS = {
    "sport": "football", "date": "2026-09-20", "endDate": "2026-09-21", "weekEnds": "2026-09-22", "note": "",
    "windows": [{"title": "Parlays", "tickets": [
        {"name": "Card 1", "sub": "2-Leg", "stake": 5.0, "book": "Memo", "payout": 22.0,
         "legs": [L("Jahmyr Gibbs", "101", "DET", "Kenny", "-120"), L("James Cook", "201", "BUF", "Memo", "+140")]},   # cashed
        {"name": "Card 2", "sub": "2-Leg", "stake": 5.0, "book": "Joe", "payout": 60.0,
         "legs": [L("Jahmyr Gibbs", "101", "DET", "Joe", "-120"), L("Jared Goff", "100", "DET", "Noid", "+600")]},      # threw two, ran none: miss
        {"name": "Card 3", "sub": "3-Leg", "stake": 2.0, "book": "Noid", "payout": 90.0,
         "legs": [L("Jahmyr Gibbs", "101", "DET", "Noid", "-120"), L("Inactive Guy", "401", "KC", "Joe", "+300"),
                  L("Blocking TE", "402", "KC", "Memo", "+900")]},                                                    # hit + void + dressed-no-stats miss
    ]}],
    "singles": [
        {"id": "single-0", "who": "KENNY", "player": "Josh Allen", "athleteId": "200", "team": "BUF", "odds": "+110", "stake": 5.0, "payout": 10.5},
        {"id": "single-1", "who": "JOE", "player": "Josh Allen", "athleteId": None, "team": "JAX", "odds": "+2500", "stake": 1.0, "payout": 26.0},
    ],
}

with tempfile.TemporaryDirectory() as tmp:
    out_dir = Path(tmp) / "results"

    # ---------- A. when a week gets recorded ----------
    msg = rf.record(TICKETS, out_dir, date(2026, 9, 21), fetcher=fake_espn("in"))
    check("A1 Sunday's done but Monday night isn't -> nothing recorded", "not recorded" in msg and not out_dir.exists(), msg)
    msg = rf.record(TICKETS, out_dir, date(2026, 9, 19), fetcher=fake_espn())
    check("A2 a week that hasn't kicked off is left alone", "hasn't kicked off" in msg, msg)
    msg = rf.record(TICKETS, out_dir, date(2026, 9, 22), fetcher=fake_espn())
    check("A3 every game Final -> recorded, in a file named for the NFL week", "RECORDED" in msg and (out_dir / "2026-09-22.json").exists(), msg)
    week = json.loads((out_dir / "2026-09-22.json").read_text(encoding="utf-8"))

    # ---------- B. grading ----------
    cards = {p["name"]: p for p in week["parlays"]}
    gibbs = cards["Card 1"]["legs"][0]
    check("B1 touchdown by athlete id, stat line and both scores kept",
          gibbs["state"] == "hit" and gibbs["line"]["td"] == 2 and gibbs["line"]["rushYds"] == 96 and [t["yards"] for t in gibbs["touchdowns"]] == [1, 12], str(gibbs))
    check("B2 the card says 'James Cook', ESPN says 'James Cook III' -> still a hit", cards["Card 1"]["legs"][1]["state"] == "hit")
    check("B3 cashed parlay returns its payout", (cards["Card 1"]["outcome"], cards["Card 1"]["returned"]) == ("hit", 22.0))
    check("B4 PASSING touchdowns never cash the passer", cards["Card 2"]["legs"][1]["state"] == "miss" and cards["Card 2"]["outcome"] == "dead")
    c3 = cards["Card 3"]["legs"]
    check("B5 inactive (didNotPlay) -> void; dressed but no stat line -> a real miss", (c3[1]["state"], c3[2]["state"]) == ("na", "miss"), str([l["state"] for l in c3]))
    qb, lb = week["singles"]
    check("B6 two Josh Allens: the linebacker's pick-six doesn't cash the quarterback...", qb["state"] == "miss" and qb["outcome"] == "dead", str(qb))
    check("B7 ...and does cash the linebacker, matched by name + team with no athlete id", lb["state"] == "hit" and lb["returned"] == 26.0 and lb["who"] == "Joe", str(lb))
    s = week["summary"]
    check("B8 summary adds up", (s["parlays"], s["parlaysCashed"], s["singles"], s["singlesCashed"], s["legs"], s["legsHit"], s["legsVoid"]) == (3, 1, 2, 1, 9, 5, 1)
          and (s["staked"], s["returned"]) == (18.0, 48.0), str(s))
    check("B9 every touchdown in the league that week is kept (field goals aren't), ours counted",
          s["leagueTouchdowns"] == 4 and len(week["touchdowns"]) == 4 and s["ourTouchdowns"] == 4, str(s))

    calls = []
    msg = rf.record(TICKETS, out_dir, date(2026, 9, 23), fetcher=fake_espn(log=calls))
    check("B10 already recorded from the same card -> skipped without touching the network", "already recorded" in msg and calls == [], msg)
    out2 = Path(tmp) / "backstop"
    msg = rf.record(TICKETS, out2, date(2026, 9, 23), fetcher=fake_espn("in"))
    check("B11 two days past the week's end with a game never Final -> recorded, marked incomplete",
          "INCOMPLETE" in msg and json.loads((out2 / "2026-09-22.json").read_text(encoding="utf-8"))["complete"] is False, msg)

    # ---------- C. history.json ----------
    history = rf.build_history(out_dir)
    text = rf.dump_history(history)
    check("C1 one week, five bets, singles flagged", len(history["weeks"]) == 1 and len(history["parlays"]) == 5
          and [p.get("kind") for p in history["parlays"]] == [None, None, None, "single", "single"])
    first = history["parlays"][0]
    check("C2 same leg shape as baseball's history (bettor / pick / odds / status) + touchdown detail",
          first["legs"][0] == {"bettor": "Kenny", "pick": "Jahmyr Gibbs", "odds": -120, "status": "hit", "team": "DET", "tds": 2, "yards": 12}, str(first["legs"][0]))
    check("C3 money carried over", (first["stake"], first["won"], first["book"]) == (5.0, 22.0, "Memo"))
    check("C4 dump round-trips and is deterministic", json.loads(text) == history and text == rf.dump_history(json.loads(text)))
    empty = rf.build_history(Path(tmp) / "nothing-here")
    check("C5 no weeks on file -> a valid, empty history", empty["parlays"] == [] and empty["weeks"] == [] and json.loads(rf.dump_history(empty)) == empty)

    # ---------- D. CLI + isolation ----------
    real = REPO / "data" / "football"
    before = sorted(str(p.relative_to(real)) for p in real.rglob("*"))
    tk = Path(tmp) / "tickets.json"
    tk.write_text(json.dumps(dict(TICKETS, date="2099-01-03", endDate="2099-01-04", weekEnds="2099-01-05")), encoding="utf-8")
    run = subprocess.run([sys.executable, str(REPO / "scripts" / "record_football_results.py"), "--tickets", str(tk),
                          "--out-dir", str(Path(tmp) / "cli"), "--history", str(Path(tmp) / "cli-history.json")], capture_output=True, text=True)
    check("D1 CLI: future week exits cleanly, still writes an (empty) history", run.returncode == 0 and (Path(tmp) / "cli-history.json").exists(), run.stdout + run.stderr)
    check("D2 the real data/football was never touched", sorted(str(p.relative_to(real)) for p in real.rglob("*")) == before)
    src = (REPO / "scripts" / "record_football_results.py").read_text(encoding="utf-8")
    check("D3 football's recorder shares nothing with baseball's: no imports of it, no MLB, no baseball data paths",
          "import record_results" not in src and "statsapi" not in src and '"tickets.json"' in src and 'DATA = ROOT / "data" / "football"' in src)

    # ---------- E. the page ----------
    def serve(p, history_json, status=200, width=1000):
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        requests, errors = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def handler(route):
            url = route.request.url
            requests.append(url)
            if url == ORIGIN + "/football/history/":
                return route.fulfill(body=PAGE_SRC, content_type="text/html")
            if url == ORIGIN + "/data/football/history.json":
                if history_json is None:
                    return route.fulfill(status=404, body="not found")
                return route.fulfill(status=status, body=history_json, content_type="application/json")
            if "fonts.g" in url:
                return route.fulfill(body="", content_type="text/css")
            return route.abort()

        page.route("**/*", handler)
        page.clock.install()
        page.goto(ORIGIN + "/football/history/")
        return browser, page, requests, errors

    with sync_playwright() as p:
        browser, page, requests, errors = serve(p, None)
        page.wait_for_function("!document.getElementById('range-note').textContent.startsWith('Loading')")
        check("E1 no history file yet -> a plain 'no finished weeks yet', not an error",
              "No finished weeks yet" in page.inner_text("#range-note") and not page.is_visible("#app") and not errors, page.inner_text("#range-note"))
        browser.close()

        browser, page, requests, errors = serve(p, rf.dump_history(empty))
        page.wait_for_function("!document.getElementById('range-note').textContent.startsWith('Loading')")
        check("E2 an empty history file reads the same way", "No finished weeks yet" in page.inner_text("#range-note") and not errors)
        browser.close()

        browser, page, requests, errors = serve(p, "{}", status=500)
        page.wait_for_function("!document.getElementById('range-note').textContent.startsWith('Loading')")
        check("E3 a genuine load failure still says so", "Couldn't load" in page.inner_text("#range-note"))
        browser.close()

        browser, page, requests, errors = serve(p, text)
        page.wait_for_selector("#board tbody tr")
        tiles = page.evaluate("[...document.querySelectorAll('#tiles .tile')].map(t => [...t.children].map(c => c.textContent))")
        check("E4 tiles: 1 week, 5 of 8 resolved legs, 2 of 5 bets cashed, real money +$30",
              tiles[0][:2] == ["Weeks", "1"] and tiles[1][2] == "5 of 8 legs" and tiles[2][1] == "2 of 5" and tiles[-1][1] == "+$30.00", str(tiles))
        board = page.evaluate("[...document.querySelectorAll('#board tbody tr')].map(tr => [...tr.cells].map(td => td.textContent.trim()))")
        kenny = next(r for r in board if r[0] == "Kenny")
        check("E5 leaderboard handles minus-money odds (Kenny: Gibbs -120 hit, Allen +110 miss -> -0.17u)", kenny[1:5] == ["2", "1", "50.0%", "−0.17u"], str(kenny))
        bands = page.evaluate("[...document.querySelectorAll('#odds-chart .band')].map(b => b.dataset.band)")
        check("E6 odds bands are football's, starting with the favorites", bands[0].startswith("favorites") and "+500 and up" in bands, str(bands))
        log_text = page.inner_text("#log")
        check("E7 log: touchdown detail on hits, singles labelled, stakes shown", "2 TD" in log_text and "12 yd" in log_text and "SINGLE" in log_text.upper() and "$22.00 on $5.00" in log_text, log_text[:400])
        check("E8 nothing of baseball's on the page", not page.query_selector("#sec-away") and "home run" not in page.inner_text("body").lower())
        page.locator("#bettor-chips .chip", has_text="Joe").first.click()
        page.clock.fast_forward(5 * 60 * 1000)
        page.wait_for_timeout(200)
        asked = sorted(set(u for u in requests if "fonts.g" not in u))
        check("E9 ISOLATION: the page asks for itself and football's history.json, once, and nothing else in five minutes",
              asked == [ORIGIN + "/data/football/history.json", ORIGIN + "/football/history/"]
              and sum(u.endswith("/history.json") for u in requests) == 1, str(asked))
        check("E10 source never mentions ESPN, the slate files, the MLB API or baseball's history file",
              not any(s in PAGE_SRC for s in ("espn.com", "tickets.json", "tickets-previous", "statsapi", '"../data/history.json"', "/data/history.json")))
        check("E11 no script errors", not errors, str(errors))
        browser.close()

        browser, page, requests, errors = serve(p, text, width=375)
        page.wait_for_selector("#board tbody tr")
        check("E12 no sideways scroll on a phone", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        browser.close()

    # ---------- F. the tracker links to it, and only links ----------
    check("F1 football tracker's footer links to /football/history/", 'href="/football/history/"' in TRACKER_SRC)
    check("F2 ...and that link is the only coupling: the tracker never reads the history or results files",
          "history.json" not in TRACKER_SRC and "/results/" not in TRACKER_SRC)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all football-history checks passed")
