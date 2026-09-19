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
herb = [l for w in g_windows for c in w["tickets"] for l in c["legs"] if "Hern" in l["player"]][0]
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

# --- 4. second raw-text template: "Ticket N: TIME | Player (Team) +ODDS (Bettor)"
# -- a real Discord upload on 2026-09-18 that the format above parsed as
# zero tickets (exit 1, nothing written). Real fixture, not paraphrased.
ticket_text = (REPO / "tests" / "fixtures" / "discord_ticket_format.txt").read_text(encoding="utf-8")
t_windows, t_singles, t_raw = pp.parse(ticket_text, team_by_name, canon)

assert len(t_singles) == 10, len(t_singles)
counts = [len(c["legs"]) for w in t_windows for c in w["tickets"]]
assert sorted(counts) == sorted([3] * 4 + [2] * 9 + [5] * 2), counts
# leg count is read from the ticket body, never trusted from the header --
# the real file's "10 TWO-LEG PARLAYS" header sits over only 9 tickets
two_leg_window = next(w for w in t_windows if "TWO-LEG" in w["title"])
assert len(two_leg_window["tickets"]) == 9, "must count actual tickets, not the header's claimed 10"
# a lone document title line ("HOME RUN PARLAY CARD...") that happens to
# look header-shaped must not survive as an empty section
assert all(w["tickets"] for w in t_windows)
# a single is exactly the ticket with one leg -- not which section it's under
first_single = t_singles[0]
assert first_single["who"] == "MEMO" and first_single["player"] == "Chase Meidroth" and first_single["odds"] == "+1040"
assert first_single["stake"] == 5.0 and first_single["payout"] == 54.0 and first_single["pp"] == "PP $54.00"
five_man = next(c for w in t_windows for c in w["tickets"] if len(c["legs"]) == 5)
assert five_man["stake"] == 5.0 and five_man["payout"] == 20515.20 and five_man["book"] == "Kenny"
# a card with no subtitle text doesn't leave a dangling "&middot;" in its name
assert not five_man["name"].endswith("&middot;") and "  " not in five_man["name"], five_man["name"]
print("OK: second template (\"Ticket N:\" cards) parses 10 singles + 15 cards (40 legs) across 3 sections")

# --- 5. third raw-text template: "Ticket #N (Bettor - $X Bet) [PP: $Y]" header,
# legs as "* (Bettor) Player - TEAM (+ODDS) - TIME ET", grouped under "Part N: ..."
# headers -- a real Discord upload on 2026-09-19 that the first two formats
# parsed as zero tickets (exit 1, nothing written -- the real slate never posted).
hash_text = (REPO / "tests" / "fixtures" / "discord_ticket_hash_format.txt").read_text(encoding="utf-8")
h_windows, h_singles, h_raw = pp.parse(hash_text, team_by_name, canon)

# "Part 2" isn't here: its one ticket has a single leg, so it became a single
# (see below) and left the window empty -- same rule that drops an empty
# section in template 2.
assert [w["title"] for w in h_windows] == [
    "Part 1: Afternoon Early Birds (2:10 PM & 4:05 PM ET Mixed)",
    "Part 5: Bonus Bets (Unsorted)",
], [w["title"] for w in h_windows]
# a lone leg under its own "Ticket #N (...)" header is a single, same rule as template 2
assert len(h_singles) == 1, h_singles
counts = [len(c["legs"]) for w in h_windows for c in w["tickets"]]
assert sorted(counts) == [2, 3, 5], counts
single = h_singles[0]
assert (single["who"], single["player"], single["team"], single["odds"]) == ("FRANCHER", "Pete Crow-Armstrong", "CHC", "+330"), single
assert single["stake"] == 5.0 and single["payout"] == 330.0
# stake/payout/book live in the ticket's own header line, not a separate footer
five_leg = next(c for w in h_windows for c in w["tickets"] if len(c["legs"]) == 5)
assert (five_leg["stake"], five_leg["payout"], five_leg["book"]) == (5.0, 29471.0, "Kenny"), five_leg
# a name typed without its suffix still resolves against the roster
witt_leg = next(l for w in h_windows for c in w["tickets"] for l in c["legs"] if l["who"] == "Kenny" and l["team"] == "KC")
assert witt_leg["player"] == "Bobby Witt Jr.", witt_leg
assert len(h_windows) == 2, len(h_windows)
print("OK: third template (\"Ticket #N (...)\" cards under \"Part N:\" headers) "
      "parses 1 single + 3 cards (10 legs) across 2 sections")

print("\nALL PARSER TESTS PASSED")
