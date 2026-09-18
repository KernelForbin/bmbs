"""
Regression checks for scripts/parse_picks.py. Reads only checked-in
fixtures and writes only to a temp dir -- never touches data/.

    pip install -r tests/requirements.txt
    python tests/test_parser.py
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import parse_picks as pp  # noqa: E402

team_by_name, canon = pp.load_roster()


def shape(windows, singles):
    return {
        "sections": [(w["title"].split(" ", 1)[-1], [len(c["legs"]) for c in w["tickets"]]) for w in windows],
        "singles": len(singles),
    }


# --- 1. Gemini export (no ## / no *) vs raw markdown (test_picks.txt), same slate ---
gemini_text = (REPO / "tests" / "fixtures" / "gemini_picks.txt").read_text(encoding="utf-8")
md_text = (REPO / "test_picks.txt").read_text(encoding="utf-8")

g_windows, g_singles, g_raw = pp.parse(gemini_text, team_by_name, canon)
m_windows, m_singles, m_raw = pp.parse(md_text, team_by_name, canon)

print("gemini :", shape(g_windows, g_singles))
print("markdwn:", shape(m_windows, m_singles))
assert shape(g_windows, g_singles) == shape(m_windows, m_singles), "formats parse differently"
assert len(g_windows) == 3 and sum(len(w["tickets"]) for w in g_windows) == 12 and len(g_singles) == 8

# who-cleaning still strips the "(Noid — Listed as Herb Hernandez)" aside
herb = [l for w in g_windows for c in w["tickets"] for l in c["legs"] if "Hernandez" in l["player"]][0]
assert herb["who"] == "Noid", herb
# bonus 4-leg cards use team codes instead of times
bonus = g_windows[2]["tickets"][0]["legs"][0]
assert bonus["time"] == "" and bonus["team"], bonus
# "Card 11: Mega Longshot Wager" must not be mistaken for a singles header
assert len(g_windows[2]["tickets"]) == 2, [c["name"] for c in g_windows[2]["tickets"]]
# same-game card tag survives
assert "Both Players" in g_windows[1]["tickets"][0]["name"], g_windows[1]["tickets"][0]["name"]
# trailing chat line didn't create a section or leg
assert all("Let me know" not in c["name"] for w in g_windows for c in w["tickets"])
print("OK: both formats parse identically (3 sections / 12 cards / 8 singles)")

# --- 2. slate date heuristic (latest listed start in the fixture is 9:40 PM ET) ---
times = [l["time"] for w in g_windows for c in w["tickets"] for l in c["legs"] if l["time"]] + [s["time"] for s in g_raw if s["time"]]
ET = pp.ET
cases = [
    (datetime(2026, 9, 18, 14, 0, tzinfo=ET), "2026-09-18", "afternoon of game day"),
    (datetime(2026, 9, 17, 23, 0, tzinfo=ET), "2026-09-18", "11pm the night before"),
    (datetime(2026, 9, 18, 0, 30, tzinfo=ET), "2026-09-18", "12:30am on game day"),
    (datetime(2026, 9, 18, 21, 39, tzinfo=ET), "2026-09-18", "one minute before last first pitch"),
    (datetime(2026, 9, 18, 21, 41, tzinfo=ET), "2026-09-19", "one minute after last first pitch -> tomorrow"),
]
for now, expect, label in cases:
    got = pp.slate_date_for(times, now)
    assert got == expect, f"{label}: got {got}, expected {expect}"
    print(f"OK: {label:45s} -> {got}")
assert pp.slate_date_for([], datetime(2026, 9, 18, 23, 59, tzinfo=ET)) == "2026-09-18"
print("OK: no times at all -> today")

# --- 3. archive guard, against a temp dir (never data/) ---
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    pp.TICKETS_PATH = td / "tickets.json"
    pp.PREVIOUS_PATH = td / "tickets-previous.json"

    pp.archive_previous_slate("2026-09-18")
    assert not pp.PREVIOUS_PATH.exists(), "archived with no existing tickets.json"

    pp.TICKETS_PATH.write_text(json.dumps({"date": "2026-09-18", "note": "same"}), encoding="utf-8")
    pp.archive_previous_slate("2026-09-18")
    assert not pp.PREVIOUS_PATH.exists(), "same-day re-upload must not archive"

    pp.TICKETS_PATH.write_text(json.dumps({"note": "legacy, no date"}), encoding="utf-8")
    pp.archive_previous_slate("2026-09-18")
    assert not pp.PREVIOUS_PATH.exists(), "dateless legacy file must not archive"

    pp.TICKETS_PATH.write_text(json.dumps({"date": "2026-09-17", "note": "yesterday"}), encoding="utf-8")
    pp.archive_previous_slate("2026-09-18")
    assert json.loads(pp.PREVIOUS_PATH.read_text(encoding="utf-8"))["date"] == "2026-09-17"
    print("OK: archive fires only on a date change")

print("\nALL PARSER TESTS PASSED")
