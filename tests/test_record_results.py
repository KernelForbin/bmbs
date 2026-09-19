"""
Offline checks for scripts/record_results.py (grading a finished baseball slate
into data/results/<date>.json) and for the way scripts/import_history.py folds
those records into data/history.json.

A fake fetcher stands in for the MLB Stats API; everything is written to a temp
directory. Nothing under data/ is read or written.

    python tests/test_record_results.py
"""
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import record_results as rr          # noqa: E402
import import_history as ih          # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- a fake MLB ----------------

def player(pid, name, order=None, pa=None):
    p = {"person": {"id": pid, "fullName": name}, "stats": {"batting": {} if pa is None else {"plateAppearances": pa}}}
    if order:
        p["battingOrder"] = str(order)
    return p


def hr(batter_id, batter, idx, dist=410.0, top=True):
    return {"result": {"eventType": "home_run", "event": "Home Run"},
            "about": {"atBatIndex": idx, "isTopInning": top, "halfInning": "top" if top else "bottom", "inning": 3,
                      "endTime": f"2026-09-18T23:{idx:02d}:00.000Z", "isComplete": True},
            "matchup": {"batter": {"id": batter_id, "fullName": batter}, "batSide": {"code": "R"},
                        "pitcher": {"fullName": "Some Pitcher"}, "pitchHand": {"code": "L"}},
            "playEvents": [{"isPitch": True, "details": {"type": {"description": "Sinker"}},
                            "pitchData": {"startSpeed": 95.1, "zone": 5},
                            "hitData": {"launchSpeed": 108.2, "launchAngle": 27.0, "totalDistance": dist, "trajectory": "fly_ball"}}]}


def feed(state, away, home, plays):
    return {"gameData": {"status": {"abstractGameState": state}, "venue": {"name": "Test Park"},
                         "weather": {"condition": "Clear", "temp": "70", "wind": "5 mph, Out To CF"},
                         "teams": {"away": {"abbreviation": "AAA"}, "home": {"abbreviation": "HHH"}}},
            "liveData": {"plays": {"allPlays": plays},
                         "boxscore": {"teams": {"away": {"players": {f"ID{p['person']['id']}": p for p in away}},
                                                "home": {"players": {f"ID{p['person']['id']}": p for p in home}}}}}}


GAME1 = feed("Final",
             away=[player(1, "Own Homer", 100, 4), player(2, "Played NoHr", 200, 4),
                   player(3, "Pulled Starter", 300, 2), player(4, "Pinch Guy", 301, 2),
                   player(5, "Bench Warmer"),                      # on the roster, never got in
                   player(6, "Pinch Runner", 401, 0)],             # got in, never batted
             home=[player(7, "José Ramírez Jr.", 100, 5), player(8, "Other Guy", 200, 4)],
             plays=[hr(1, "Own Homer", 5, 431.0), hr(4, "Pinch Guy", 9), hr(7, "José Ramírez Jr.", 12, top=False), hr(8, "Other Guy", 14, top=False)])
GAME2_LIVE = feed("Live", away=[player(20, "Still Playing", 100, 2)], home=[player(21, "Home Dude", 100, 2)], plays=[])
GAME2_FINAL = feed("Final", away=[player(20, "Still Playing", 100, 4)], home=[player(21, "Home Dude", 100, 4)], plays=[])


def fake_mlb(game2, log=None):
    def fetcher(url):
        if log is not None:
            log.append(url)
        if "/schedule" in url:
            state2 = game2["gameData"]["status"]["abstractGameState"]
            return {"dates": [{"games": [{"gamePk": 1, "status": {"abstractGameState": "Final"}},
                                         {"gamePk": 2, "status": {"abstractGameState": state2}}]}]}
        if "/game/1/" in url:
            return GAME1
        if "/game/2/" in url:
            return game2
        raise AssertionError("unexpected url " + url)
    return fetcher


def L(player_name, who, odds):
    return {"id": "x", "player": player_name, "team": "AAA", "who": who, "odds": odds, "time": "7:05 PM ET"}


TICKETS = {
    "date": "2026-09-18", "note": "",
    "windows": [{"title": "2-Leg", "tickets": [
        {"name": "Card 1", "sub": "2-Leg", "stake": 5.0, "book": "Ann", "payout": 100.0,
         "legs": [L("Own Homer", "Ann", "+300"), L("Jose Ramirez Jr.", "Bob", "+400")]},          # both hit -> cashed
        {"name": "Card 2", "sub": "2-Leg", "stake": 5.0, "book": "Bob", "payout": 90.0,
         "legs": [L("Pulled Starter", "Bob", "+350"), L("Played NoHr", "Cy", "+300")]},           # PHP hit + miss -> dead
        {"name": "Card 3", "sub": "3-Leg", "stake": 2.0, "book": "Cy", "payout": 400.0,
         "legs": [L("Own Homer", "Ann", "+300"), L("Bench Warmer", "Cy", "+500"), L("Not On Any Roster", "Bob", "+600")]},  # 1 hit + 2 void
        {"name": "Card 4", "sub": "2-Leg", "stake": 4.0, "book": "Ann", "payout": 50.0,
         "legs": [L("Bench Warmer", "Ann", "+500"), L("Pinch Runner", "Cy", "+450")]},            # all void
    ]}],
    "singles": [
        {"id": "single-0", "who": "KENNY", "player": "Still Playing", "team": "AAA", "odds": "+700", "stake": 5.0, "payout": 40.0},
    ],
}

with tempfile.TemporaryDirectory() as tmp:
    out_dir = Path(tmp) / "results"
    today = date(2026, 9, 19)

    # ---------- A. not before the slate is over ----------
    msg = rr.record(TICKETS, out_dir, today, fetcher=fake_mlb(GAME2_LIVE))
    check("A1 a game still in progress -> nothing is recorded", "not recorded" in msg and not out_dir.exists(), msg)
    msg = rr.record(dict(TICKETS, date="2026-09-25"), out_dir, today, fetcher=fake_mlb(GAME2_FINAL))
    check("A2 a slate that hasn't been played yet is left alone", "hasn't been played" in msg, msg)

    # ---------- B. grading ----------
    msg = rr.record(TICKETS, out_dir, today, fetcher=fake_mlb(GAME2_FINAL))
    rec = json.loads((out_dir / "2026-09-18.json").read_text(encoding="utf-8"))
    cards = {p["name"]: p for p in rec["parlays"]}
    legs = {l["player"]: l for p in rec["parlays"] for l in p["legs"]}
    check("B1 every game Final -> recorded, complete", "RECORDED" in msg and rec["complete"] is True, msg)
    check("B2 own home run -> hit, with MLB id and Statcast detail",
          legs["Own Homer"]["state"] == "hit" and legs["Own Homer"]["mlbId"] == 1
          and legs["Own Homer"]["homeRuns"][0]["distance"] == 431.0 and legs["Own Homer"]["homeRuns"][0]["pitcher"] == "Some Pitcher",
          str(legs["Own Homer"]))
    check("B3 accents and suffixes match the way the page matches them (card typed without the accent)",
          legs["Jose Ramirez Jr."]["state"] == "hit" and legs["Jose Ramirez Jr."]["mlbId"] == 7)
    check("B4 Pinch Hit Protection: the substitute's homer credits the pulled starter, and says whose it was",
          legs["Pulled Starter"]["state"] == "hit" and legs["Pulled Starter"]["php"] == "Pinch Guy"
          and len(legs["Pulled Starter"]["homeRuns"]) == 1, str(legs["Pulled Starter"]))
    check("B5 played, no home run -> miss", legs["Played NoHr"]["state"] == "miss")
    check("B6 THE DELIBERATE DIFFERENCE: on the roster but never got in -> void, not a miss",
          legs["Bench Warmer"]["state"] == "na" and legs["Bench Warmer"]["mlbId"] == 5, str(legs["Bench Warmer"]))
    check("B7 pinch ran, never came to the plate -> void", legs["Pinch Runner"]["state"] == "na")
    check("B8 not on any roster -> void", legs["Not On Any Roster"]["state"] == "na" and legs["Not On Any Roster"]["mlbId"] is None)
    check("B9 cashed parlay returns its listed payout", (cards["Card 1"]["outcome"], cards["Card 1"]["returned"]) == ("hit", 100.0))
    check("B10 one miss kills it", (cards["Card 2"]["outcome"], cards["Card 2"]["returned"]) == ("dead", 0.0))
    check("B11 void legs re-price the bet the way the page does: $2 x 4.00 = $8.00, not the listed $400",
          (cards["Card 3"]["outcome"], cards["Card 3"]["returned"]) == ("hit", 8.0), str(cards["Card 3"]["returned"]))
    check("B12 nobody played -> void, stake refunded", (cards["Card 4"]["outcome"], cards["Card 4"]["returned"]) == ("void", 4.0))
    single = rec["singles"][0]
    check("B13 single: bettor's name un-shouted, graded, stake kept",
          (single["who"], single["state"], single["outcome"], single["stake"], single["returned"]) == ("Kenny", "miss", "dead", 5.0, 0.0), str(single))
    s = rec["summary"]
    check("B14 summary adds up", (s["parlays"], s["parlaysCashed"], s["singles"], s["legs"], s["legsHit"], s["legsVoid"]) == (4, 2, 1, 10, 4, 4)
          and (s["staked"], s["returned"]) == (21.0, 112.0), str(s))
    check("B15 every home run in the league that day is kept, ours counted (the PHP substitute's is ours)",
          s["leagueHomeRuns"] == 4 and len(rec["homeRuns"]) == 4 and s["ourHomeRuns"] == 3, str(s))

    # ---------- C. idempotent, and cheap ----------
    calls = []
    msg = rr.record(TICKETS, out_dir, today, fetcher=fake_mlb(GAME2_FINAL, calls))
    check("C1 already recorded from the same picks -> skipped without touching the network", "already recorded" in msg and calls == [], msg)
    fixed = json.loads(json.dumps(TICKETS))
    fixed["singles"][0]["player"] = "Own Homer"
    msg = rr.record(fixed, out_dir, today, fetcher=fake_mlb(GAME2_FINAL))
    rec2 = json.loads((out_dir / "2026-09-18.json").read_text(encoding="utf-8"))
    check("C2 picks corrected afterwards -> graded again", "RECORDED" in msg and rec2["singles"][0]["state"] == "hit", msg)

    # ---------- D. the backstop ----------
    out2 = Path(tmp) / "backstop"
    msg = rr.record(TICKETS, out2, date(2026, 9, 20), fetcher=fake_mlb(GAME2_LIVE))
    rec3 = json.loads((out2 / "2026-09-18.json").read_text(encoding="utf-8"))
    check("D1 two days on with a game still not Final -> recorded anyway, marked incomplete",
          "INCOMPLETE" in msg and rec3["complete"] is False and rec3["singles"][0]["state"] == "live", msg)
    msg = rr.record(TICKETS, out2, date(2026, 9, 21), fetcher=fake_mlb(GAME2_FINAL))
    check("D2 ...and re-graded once it does finish", json.loads((out2 / "2026-09-18.json").read_text(encoding="utf-8"))["complete"] is True, msg)

    # ---------- E. folding recorded slates into history.json ----------
    SHEET = ("Date,L1 Bettor,L1 Pick,L1 Odds,L1 Status,L2 Bettor,L2 Pick,L2 Odds,L2 Status,Amount Won\n"
             "9/16,Ann,Homer,300,Hit,Bob,Soto,400,Miss,\n"
             # the same bets the tracker recorded as 9/18, logged under 9/17 by hand (this really happened)
             "9/17,Ann,Homer,300,Hit,Bob,Ramirez,400,Hit,$100\n"
             "9/17,Bob,Starter,350,Miss,Cy,NoHr,300,Miss,\n"
             "9/17,Ann,Homer,300,Hit,Cy,Warmer,500,DNP,\n"
             "9/17,Ann,Warmer,500,DNP,Cy,Runner,450,DNP,\n"
             "9/17,Kenny,Playing,700,Miss,,,,,\n")
    pmap = {"aliases": {}, "players": {"Homer": {"id": 1, "name": "Own Homer"}}}
    recorded = ih.load_recorded(out_dir)
    # put the original (uncorrected) record back for a clean comparison
    recorded = [json.loads(json.dumps(rec))]
    payload = ih.build([SHEET], pmap, date(2026, 9, 19), with_mlb=False, recorded=recorded)
    dates = sorted({p["date"] for p in payload["parlays"]})
    check("E1 the sheet's mis-dated copy of a recorded slate is dropped, not double counted", dates == ["2026-09-16", "2026-09-18"], str(dates))
    site = [p for p in payload["parlays"] if p.get("src") == "site"]
    check("E2 recorded slate: 4 parlays + 1 single, flagged as the tracker's", len(site) == 5 and site[-1].get("kind") == "single")
    c1 = site[0]
    check("E3 a player the map knows by MLB id keeps the group's nickname (one row, not two), full name rides along",
          c1["legs"][0]["pick"] == "Homer" and c1["legs"][0]["name"] == "Own Homer" and c1["legs"][0]["dist"] == 431, str(c1["legs"][0]))
    check("E4 anyone else keeps his full name", c1["legs"][1]["pick"] == "Jose Ramirez Jr.")
    check("E5 stake, payout, who placed it, and winnings carried over",
          (c1["stake"], c1["payout"], c1["book"], c1["won"]) == (5.0, 100.0, "Ann", 100.0), str(c1))
    check("E6 void -> dnp; PHP credit flagged", site[3]["legs"][0]["status"] == "dnp" and site[1]["legs"][0].get("php") == "Pinch Guy")
    check("E7 recordedFrom marks where the tracker's own record begins", payload["recordedFrom"] == "2026-09-18" and payload["lastSlate"] == "2026-09-18")
    same_day = ih.build([SHEET.replace("9/17", "9/18")], pmap, date(2026, 9, 19), with_mlb=False, recorded=recorded)
    check("E8 same date in both sources -> the tracker's record wins",
          all(p.get("src") == "site" for p in same_day["parlays"] if p["date"] == "2026-09-18") and len(same_day["parlays"]) == 6)
    check("E9 no recorded slates -> exactly the old sheet-only behaviour",
          ih.build([SHEET], pmap, date(2026, 9, 19), with_mlb=False)["recordedFrom"] is None)
    check("E10 a PHP-credited hit isn't held against MLB's game log (he really didn't homer)",
          "2026-09-18" not in ih.pick_days(site, "Pulled Starter"))
    text = ih.dump(payload)
    check("E11 dump round-trips with the new fields", json.loads(text) == payload)

    # ---------- F. a dead sheet can't block new slates ----------
    hist = Path(tmp) / "history.json"
    hist.write_text(ih.dump(ih.build([SHEET], pmap, date(2026, 9, 19), with_mlb=False)), encoding="utf-8")
    empty_dir = Path(tmp) / "no-sheet-here"
    empty_dir.mkdir()
    run = subprocess.run([sys.executable, str(REPO / "scripts" / "import_history.py"), "--from-dir", str(empty_dir), "--no-mlb",
                          "--results-dir", str(out_dir), "--out", str(hist)], capture_output=True, text=True)
    after = json.loads(hist.read_text(encoding="utf-8"))
    check("F1 sheet unreadable -> warns, reuses the sheet slates already on file, still merges the recorded one",
          run.returncode == 0 and "WARNING" in run.stderr and any(p.get("src") == "site" for p in after["parlays"])
          and any(p["date"] == "2026-09-16" for p in after["parlays"]), run.stderr[-300:])

    # ---------- G. the CLI never touches the real data/ ----------
    real = REPO / "data" / "results"
    before = sorted(p.name for p in real.glob("*.json")) if real.exists() else []
    tk = Path(tmp) / "tickets.json"
    tk.write_text(json.dumps(dict(TICKETS, date="2099-01-01")), encoding="utf-8")
    run = subprocess.run([sys.executable, str(REPO / "scripts" / "record_results.py"), "--tickets", str(tk), "--out-dir", str(Path(tmp) / "cli")],
                         capture_output=True, text=True)
    check("G1 CLI: a future slate exits cleanly without the network", run.returncode == 0 and "hasn't been played" in run.stdout, run.stdout + run.stderr)
    check("G2 the real data/results was never touched", (sorted(p.name for p in real.glob("*.json")) if real.exists() else []) == before)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all record-results checks passed")
