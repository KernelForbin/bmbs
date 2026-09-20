"""
Regression checks for scripts/parse_picks.py. Reads only checked-in
fixtures and writes only to a temp dir -- never touches data/.

    pip install -r tests/requirements.txt
    python tests/test_parser.py
"""
import contextlib
import io
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

# --- 6. stolen base bets. No real steal card exists yet (2026-09-19), so the
# parser accepts the marker wherever a card might plausibly put it: on the leg,
# on the ticket header, or on a section header. Home run legs carry NO market
# field, so a home-run-only card's tickets.json is unchanged by any of this.
sb_text = """Part 1: Evening Window
Ticket #1 (Memo - $5 Bet) [PP: $61.00]
* (Kenny) Elly De La Cruz - CIN (-120) SB - 6:40 PM ET
* (Joe) Kyle Schwarber - PHI (+240) - 4:10 PM ET

Ticket #2 (Memo - $5 Bet) [PP: $40.00] - Stolen Bases
* (Bernie) Jose Ramirez - CLE (+150) - 8:10 PM ET
* (Noid) Bobby Witt - KC (+130) - 6:40 PM ET

Part 2: Stolen Base Singles
Ticket #3 (Kenny - $5 Bet) [PP: $12.50]
* (Kenny) Chandler Simpson - TB (+150) - 4:10 PM ET

Part 3: Bonus Bets
Ticket #4 (Kenny - $5 Bet) [PP: $30.00]
* (Kenny) Pete Alonso - BAL (+500) - 4:05 PM ET
"""
s_windows, s_singles, _ = pp.parse(sb_text, team_by_name, canon)
mixed, all_sb = s_windows[0]["tickets"]
# one ticket, two markets; minus money keeps its sign
assert [(l["player"], l["odds"], l.get("market")) for l in mixed["legs"]] == [
    ("Elly De La Cruz", "-120", "sb"), ("Kyle Schwarber", "+240", None)], mixed["legs"]
# a marker on the ticket header covers every leg under it
assert [l.get("market") for l in all_sb["legs"]] == ["sb", "sb"], all_sb["legs"]
# a section header's marker lasts until the next header, and no longer
assert [(x["player"], x.get("market")) for x in s_singles] == [("Chandler Simpson", "sb"), ("Pete Alonso", None)], s_singles
# every earlier fixture is a home run card: not one leg or single may have grown a market
for wins, sgl in ((g_windows, g_singles), (m_windows, m_singles), (t_windows, t_singles), (h_windows, h_singles)):
    assert not any("market" in l for w in wins for c in w["tickets"] for l in c["legs"]) and not any("market" in x for x in sgl)
plain = "* (Kenny) Pete Alonso - BAL (+500) - 4:05 PM ET"
assert pp.take_market(plain) == (plain, False)
print("OK: steal markers on a leg / ticket / section; mixed parlays; minus-money odds; home run cards unchanged")

# --- 7. the first REAL card with a steal on it (2026-09-19). Tickets 1-16 are the
# third template with "-" bullets and upper-case "PART N:" headers; "PART 6: LATE
# ADDITIONS" adds a combined-odds bracket to the ticket header and spells the
# market out on every leg, with a matchup instead of a team and no per-leg bettor.
# Before this was handled, tickets 17-18 were dropped WITHOUT A WORD.
prop_text = (REPO / "tests" / "fixtures" / "discord_prop_legs_with_steals.txt").read_text(encoding="utf-8")
p_windows, p_singles, _ = pp.parse(prop_text, team_by_name, canon)
assert sum(len(w["tickets"]) for w in p_windows) == 18 and sum(len(c["legs"]) for w in p_windows for c in w["tickets"]) == 44
assert pp.parse.unread == [], pp.parse.unread
late = p_windows[-1]
assert late["title"] == "PART 6: LATE ADDITIONS" and [c["name"] for c in late["tickets"]] == ["Card 17", "Card 18"]
t17 = late["tickets"][0]
assert (t17["stake"], t17["payout"], t17["book"]) == (5.0, 151.25, "Bailey"), t17          # the [+2925] bracket is skipped over
assert [(l["player"], l["team"], l["odds"], l.get("market"), l["who"], l["time"]) for l in t17["legs"]] == [
    ("Josh Naylor", "SEA", "+450", "sb", "Bailey", "8:10 PM ET"),      # "Stolen Bases O0.5" -> a steal leg; team comes from the roster
    ("Ben Rice", "NYY", "+450", None, "Bailey", "8:10 PM ET")], t17["legs"]   # "Home Runs O0.5" -> an ordinary home run leg
# nothing in the 16 ordinary tickets grew a market
assert not any("market" in l for w in p_windows[:-1] for c in w["tickets"] for l in c["legs"])
# a bet line nobody understands is reported, never silently dropped
pp.parse("Part 1: X" + chr(10) + "Ticket #1 (Memo - nine dollars) [PP: $10]" + chr(10) + "- (Joe) Pete Alonso - BAL (+360) - 4:05 PM ET", team_by_name, canon)
assert len(pp.parse.unread) == 2, pp.parse.unread
for fixture_text in (gemini_text, md_text, ticket_text, hash_text, sb_text):
    pp.parse(fixture_text, team_by_name, canon)
    assert pp.parse.unread == [], pp.parse.unread
print("OK: real steal card parses all 18 tickets (prop-style legs, [+odds] header); unreadable bet lines are reported")

# --- 8. main()'s unread-line safety net, end to end. Section 7 only checks that
# parse() POPULATES pp.parse.unread -- nothing exercised the layer above it in
# main() that turns that into a stderr WARNING and a tickets.json `note`, which
# is the actual fix for the real incident (a card "successfully" parsed 16 of
# 18 tickets and said nothing). Against a temp dir, never data/.
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    pp.TICKETS_PATH = td / "tickets.json"
    pp.PREVIOUS_PATH = td / "tickets-previous.json"
    incoming = td / "incoming.txt"
    old_argv = sys.argv

    mixed_text = (
        "Part 1: Evening Window\n"
        "Ticket #1 (Memo - $5 Bet) [PP: $61.00]\n"
        "* (Kenny) Aaron Judge - NYY (+240) - 6:40 PM ET\n"
        "* (Joe) Kyle Schwarber - PHI (+240) - 4:10 PM ET\n"
        "\n"
        "Ticket #2 (Memo - nine dollars) [PP: $10]\n"
        "* (Joe) Pete Alonso - BAL (+360) - 4:05 PM ET\n"
    )
    incoming.write_text(mixed_text, encoding="utf-8")
    sys.argv = ["parse_picks.py", "--file", str(incoming)]
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            pp.main()
    finally:
        sys.argv = old_argv
    out = json.loads(pp.TICKETS_PATH.read_text(encoding="utf-8"))
    assert "1 line" in out["note"] and "NOT being tracked" in out["note"] and "Ticket #2" in out["note"], out["note"]
    assert "WARNING: couldn't read: Ticket #2" in stderr.getvalue(), stderr.getvalue()
    print("OK: an unreadable bet-like line surfaces as a stderr WARNING and a tickets.json note (slate still posts)")

    # a fully-understood upload must leave the note empty, not a stale one
    incoming.write_text(gemini_text, encoding="utf-8")
    sys.argv = ["parse_picks.py", "--file", str(incoming)]
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            pp.main()
    finally:
        sys.argv = old_argv
    out2 = json.loads(pp.TICKETS_PATH.read_text(encoding="utf-8"))
    assert out2["note"] == "", out2["note"]
    print("OK: a fully-understood upload leaves the note empty")

# --- 9. resolve_player()'s three branches, directly. Every fixture above only
# exercises the exact-match path (real cards typed real names correctly); the
# fuzzy-match path (difflib, cutoff 0.82) and the give-up fallback were never
# actually asserted on their own -- a change to the cutoff or the matching
# logic could start mismatching players, or blanking a team that should have
# resolved, with nothing to catch it.
assert pp.resolve_player("Aaron Judge", team_by_name, canon) == ("Aaron Judge", "NYY")
assert pp.resolve_player("Aaron Judg", team_by_name, canon) == ("Aaron Judge", "NYY"), \
    "a one-letter typo should still fuzzy-match the real player"
typed_as, team = pp.resolve_player("Zzzqqqxxx Nobody", team_by_name, canon)
assert typed_as == "Zzzqqqxxx Nobody" and team == "", \
    "an unresolvable name must fall back to the name as typed with a BLANK team, never a guess"
print("OK: resolve_player exact match, fuzzy-typo match, and the give-up fallback (blank team, name as typed)")

# --- 10. fourth raw-text template: emoji-prefixed "Ticket #N (M-Leg Parlay)" /
# "Bonus Ticket (M-Leg Parlay)" headers (some tickets in the same card omit the
# leg-count suffix), "Bet: $X (Bettor) | PP: $Y" on their own line, and
# "* Player (TEAM) - TIME ET (+ODDS) (Bettor)" legs. A real Discord upload on
# 2026-09-20 that the first three templates parsed as zero tickets -- exit 1,
# nothing written, that day's real slate never posted. Real fixture, not
# paraphrased. The header's own "3-Leg Parlay" text is a trap: it's a literal
# match for PARLAY_HEADER_RE, so every emoji-ticket header was first misread
# as a brand-new section instead of a ticket -- one window (not eight) is the
# regression check for that.
emoji_text = (REPO / "tests" / "fixtures" / "discord_emoji_ticket_format.txt").read_text(encoding="utf-8")
e_windows, e_singles, _ = pp.parse(emoji_text, team_by_name, canon)
assert len(e_windows) == 1, [w["title"] for w in e_windows]
assert e_singles == [] and [len(c["legs"]) for c in e_windows[0]["tickets"]] == [3, 2, 2, 2, 2, 2, 2, 5], \
    [len(c["legs"]) for c in e_windows[0]["tickets"]]
t1 = e_windows[0]["tickets"][0]
assert (t1["stake"], t1["payout"], t1["book"]) == (4.0, 283.50, "Memo"), t1
assert [(l["player"], l["team"], l["odds"], l["who"], l["time"]) for l in t1["legs"]] == [
    ("Michael Busch", "CHC", "+360", "Noid", "1:40 PM ET"),
    ("Kyle Schwarber", "PHI", "+240", "Joe", "1:10 PM ET"),
    ("Junior Caminero", "TB", "+341", "Kevin", "1:40 PM ET")], t1["legs"]
# a header with no leg-count suffix at all ("⚾ Ticket #2") still starts a ticket
t2 = e_windows[0]["tickets"][1]
assert (t2["stake"], t2["payout"]) == (8.5, 320.96) and t2["legs"][0]["team"] == "AZ", \
    "team comes from the roster (ARI as typed -> AZ), same as every other template"
# "Bonus Ticket" has no number at all -- given the placeholder card name "Bonus"
bonus = e_windows[0]["tickets"][-1]
assert bonus["name"] == "Card Bonus" and len(bonus["legs"]) == 5 and bonus["stake"] == 5.0
# a typo close enough to fuzzy-match, and an initial-abbreviated name -- both
# already covered by resolve_player, exercised here through a REAL card
assert bonus["legs"][1]["player"] == "Riley Greene"
sixth = e_windows[0]["tickets"][5]
assert sixth["legs"][1]["player"] == "Lazaro Montes", "typed 'Lazuro Montes' must fuzzy-match the real spelling"
seventh = e_windows[0]["tickets"][6]
assert seventh["legs"][0]["player"] == "Pete Crow-Armstrong", "'P. Crow-Armstrong' must resolve off the roster"
# a name the real card typed that genuinely isn't on the roster (even fuzzy)
# must fall through unresolved -- but this template DOES give an explicit team
# code per leg, so that typed code ("TOR") is kept rather than left blank;
# only the player name itself is left exactly as typed, never guessed
okamoto = bonus["legs"][2]
assert okamoto["player"] == "K. Okamoto" and okamoto["team"] == "TOR", okamoto
assert pp.parse.unread == [], pp.parse.unread
print("OK: fourth template (emoji \"Ticket #N (M-Leg Parlay)\" / \"Bonus Ticket\") parses all 8 tickets (20 legs)")

print("\nALL PARSER TESTS PASSED")
