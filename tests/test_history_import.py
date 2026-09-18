"""
Regression checks for scripts/import_history.py. Fully offline: the sheet
is two inline CSV strings, MLB is a fake fetcher, and output goes to a temp
dir -- never to data/, never to the network.

    python tests/test_history_import.py
"""
import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import import_history as ih  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


HEADER = ("Date,L1 Bettor,L1 Pick,L1 Odds,L1 Status,L2 Bettor,L2 Pick,L2 Odds,L2 Status,"
          "L3 Bettor,L3 Pick,L3 Odds,L3 Status,Legs Hit,Parlay Status,Paid,Amount Won")

# Older tab: header on the first row, no odds, year-less dates, forward-filled.
ARCHIVE = HEADER + """
8/11,Miggs,Greene,,Miss,Kenny,Rice,,Miss,,,,,0,Miss,,
,Bernie,Suzuki,,Hit,Joe,Olson,,Hit,,,,,2,Hit,Yes,"$1,250.50"
8/12,Joe,Hec Rodriguez,,DNP,Noid,Slugger,,Hit,,,,,1,Hit,,
"""

# Running log: a summary block sits above the header row, like the real sheet.
LOG = """,,,,
,Home Run Parlay Tracker,,Bettor,Parlays Played
,,,Memo,119
""" + HEADER + """
8/25,Memo,Slugger,+450,Miss,Kenny,Rice,+300,Hit,Joe,  Pete   Alonso ,abc,Hit,2,Miss,,
2026-08-26 Set 2,Memo,Slugger,+500,Miss,,,,,,,,,0,Miss,,
,Kenny,Slugger,+480,,Joe,Rice,-110,Hit,,,,,1,,,
,,,,,,,,,,,,,,,,
"""

TODAY = date(2026, 9, 18)
ALIASES = {"Hec Rodriguez": "Hector Rodriguez"}

# --- 1. tab parsing ---
arc = ih.parse_tab(ARCHIVE, ALIASES, TODAY)
log = ih.parse_tab(LOG, ALIASES, TODAY)
check("archive: three parlays", len(arc) == 3, str(len(arc)))
check("year-less date gets this year", arc[0]["date"] == "2026-08-11")
check("blank date cell inherits the row above", arc[1]["date"] == "2026-08-11")
check("win amount parsed through $ and comma", arc[1].get("won") == 1250.5, str(arc[1].get("won")))
check("no win amount -> key absent", "won" not in arc[0])
check("alias folded into one name", arc[2]["legs"][0]["pick"] == "Hector Rodriguez")
check("DNP status", arc[2]["legs"][0]["status"] == "dnp")
check("missing odds are null", arc[0]["legs"][0]["odds"] is None)
check("log: header found below the summary block", len(log) == 3, str(len(log)))
check("odds parsed", [leg["odds"] for leg in log[0]["legs"]] == [450, 300, None], str(log[0]["legs"]))
check("pick whitespace collapsed", log[0]["legs"][2]["pick"] == "Pete Alonso")
check("ISO date + 'Set 2'", (log[1]["date"], log[1]["set"]) == ("2026-08-26", 2))
check("set number inherited with the date", log[2]["set"] == 2)
check("blank status is pending, never guessed", log[2]["legs"][0]["status"] == "pending")
check("negative odds", log[2]["legs"][1]["odds"] == -110)
check("a date that would be in the future is last year's",
      ih.parse_date_cell("12/25", TODAY)[0] == "2025-12-25")

try:
    ih.parse_tab("a,b\n1,2\n", {}, TODAY)
    check("layout change raises instead of writing garbage", False)
except ValueError:
    check("layout change raises instead of writing garbage", True)

# --- 2. build + got-away join against a fake MLB ---
calls = []
GAME_LOG = {"stats": [{"splits": [
    {"date": "2026-08-11", "stat": {"homeRuns": 1}},   # nobody had him  -> got away
    {"date": "2026-08-12", "stat": {"homeRuns": 2}},   # picked, Hit     -> agrees
    {"date": "2026-08-25", "stat": {"homeRuns": 0}},   # picked, Miss    -> agrees
    {"date": "2026-08-26", "stat": {"homeRuns": 1}},   # picked, Miss    -> sheet/MLB DISAGREE
    {"date": "2026-08-30", "stat": {"homeRuns": 1}},   # not a slate day -> ignored
]}]}


def fake_fetch(url):
    calls.append(url)
    return json.dumps(GAME_LOG)


pmap = {"aliases": ALIASES, "players": {"Slugger": {"id": 1, "name": "Sam Slugger"},
                                         "Rice": {"id": 2, "name": "Ben Rice"}}}
old_min = ih.GOT_AWAY_MIN_PICKS
ih.GOT_AWAY_MIN_PICKS = 4
payload = ih.build([ARCHIVE, LOG], pmap, TODAY, with_mlb=True, fetcher=fake_fetch)
ih.GOT_AWAY_MIN_PICKS = old_min

check("build: six parlays, chronological", [p["date"] for p in payload["parlays"]] ==
      ["2026-08-11", "2026-08-11", "2026-08-12", "2026-08-25", "2026-08-26", "2026-08-26"])
check("first/last slate", (payload["firstSlate"], payload["lastSlate"]) == ("2026-08-11", "2026-08-26"))
check("oddsFrom = first slate with any odds", payload["oddsFrom"] == "2026-08-25")
ga = payload["gotAway"]
check("only often-picked players are joined (Rice has 3 picks < 4)", [r["pick"] for r in ga["players"]] == ["Slugger"])
check("only MLB is called, once per season per player", len(calls) == 1 and "statsapi.mlb.com" in calls[0] and "/people/1/" in calls[0])
s = ga["players"][0]
check("got away = HR on a slate day nobody picked him", s["gotAway"] == ["2026-08-11"], str(s["gotAway"]))
check("free days / hits", (s["freeDays"], s["freeDayHits"]) == (1, 1))
check("picked days / hits come from MLB, not the sheet", (s["pickedDays"], s["pickedDayHits"]) == (3, 2))
check("sheet-vs-MLB disagreement is counted, not hidden",
      (ga["checkedPickDays"], ga["agreeingPickDays"]) == (3, 2), f"{ga['checkedPickDays']}/{ga['agreeingPickDays']}")
check("--no-mlb leaves gotAway null", ih.build([ARCHIVE, LOG], pmap, TODAY, with_mlb=False)["gotAway"] is None)

try:
    ih.build([HEADER + "\n"], pmap, TODAY, with_mlb=False)
    check("empty sheet refuses to build", False)
except ValueError:
    check("empty sheet refuses to build", True)

# --- 3. output: stable, round-trips, no timestamp (so no daily no-op commits) ---
text = ih.dump(payload)
check("dump round-trips", json.loads(text) == payload)
check("dump is deterministic", text == ih.dump(json.loads(text)))
check("one parlay per line (readable diffs)", text.count("\n") >= len(payload["parlays"]))

# --- 4. CLI: writes only where told, and never shrinks an existing history ---
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "archive.csv").write_text(ARCHIVE, encoding="utf-8")
    (tmp / "log.csv").write_text(LOG, encoding="utf-8")
    out = tmp / "out" / "history.json"
    real = REPO / "data" / "history.json"
    before = real.read_bytes() if real.exists() else None
    cmd = [sys.executable, str(REPO / "scripts" / "import_history.py"), "--from-dir", str(tmp), "--out", str(out), "--no-mlb"]

    r = subprocess.run(cmd, capture_output=True, text=True)
    check("CLI import succeeds offline", r.returncode == 0 and out.exists(), r.stderr[-300:])
    check("CLI wrote six parlays", len(json.loads(out.read_text(encoding="utf-8"))["parlays"]) == 6)

    (tmp / "log.csv").write_text(HEADER + "\n", encoding="utf-8")       # the log tab "lost" its rows
    kept = out.read_bytes()
    r = subprocess.run(cmd, capture_output=True, text=True)
    check("shrinking history is refused", r.returncode != 0 and "REFUSING" in r.stderr, r.stderr[-300:])
    check("...and the existing file is untouched", out.read_bytes() == kept)
    r = subprocess.run(cmd + ["--allow-shrink"], capture_output=True, text=True)
    check("--allow-shrink overrides", r.returncode == 0 and len(json.loads(out.read_text(encoding="utf-8"))["parlays"]) == 3)

    check("the real data/history.json was never touched", (real.read_bytes() if real.exists() else None) == before)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all history-import checks passed")
