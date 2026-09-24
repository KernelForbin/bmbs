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


# --- 0. clean_num tolerates how money is actually written on a card ---
# It stripped only commas until 2026-09-22, so a template whose regex captured
# "$310.28" rather than "310.28" raised ValueError deep inside parse(). That
# cost the automatic fixer an entire attempt on a patch that was otherwise
# working. Every caller passes a price, so accept the symbol here rather than
# making each future template's regex remember to exclude it.
for raw, want in [("310.28", 310.28), ("$310.28", 310.28), ("1,039.18", 1039.18),
                  ("$1,039.18", 1039.18), (" $7.33 ", 7.33)]:
    got = pp.clean_num(raw)
    assert got == want, f"clean_num({raw!r}) -> {got!r}, wanted {want!r}"
print("OK: clean_num accepts $ and , and surrounding space")

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

# --- 11. FIFTH template: "🕒 <Name> Window (...)" section headers, bare
# "Parlay N (Bettor)" / "Ticket N (Bettor)" ticket headers, legs written
# "* Player (+ODDS) (Who) – TIME ET" (EN-DASH before the time, where the third
# and fourth templates use a hyphen), closed by "* Wager: $X | Payout: $Y".
#
# This is the real 2026-09-22 Discord upload, and it is the first template this
# repo did not hand-write support for: the automatic fixer read it, patched the
# parser, passed this whole suite and re-parsed the card on its own. The fixture
# is the raw upload the workflow archived, renamed. Two traps it had to clear,
# both of which have bitten a human here before:
#   * "Parlay 1 (Memo)" contains the word "Parlay", so PARLAY_HEADER_RE claims
#     it and section_header() would call every ticket header a new SECTION --
#     the same collision the emoji template hit. Four windows, not thirteen, is
#     the regression check.
#   * the new leg regexes are reachable only while a "Parlay N (...)" ticket is
#     open, so a lazy (.+?) in them cannot eat an older template's leg lines.
#     The four fixtures above re-parsing unchanged is what proves that.
parlay_text = (REPO / "tests" / "fixtures" / "discord_parlay_window_format.txt").read_text(encoding="utf-8")
p_windows, p_singles, _ = pp.parse(parlay_text, team_by_name, canon)
assert len(p_windows) == 5, [w["title"] for w in p_windows]
assert [w["title"] for w in p_windows] == [
    "\U0001f552 Early Window (6:35 PM \u2013 6:40 PM ET)",
    "\U0001f552 Prime Window (7:15 PM ET)",
    "\U0001f552 Night Window (8:05 PM \u2013 9:40 PM ET)",
    "\U0001f552 Late Night Window (10:10 PM ET)",
    "\U0001f39f\ufe0f Bonus Bets Tracker"], [w["title"] for w in p_windows]
# That fifth section is the fix for a real defect: "Bonus Bets Tracker" holds
# no literal "Window", so WINDOW_HEADER_RE missed it and section_header()
# rejects anything without odds or an N-Leg/Singles phrase -- both bonus
# tickets were filed under Late Night Window and the page showed them under a
# 10:10 PM first pitch they have nothing to do with. TRACKER_HEADER_RE opens
# it, and is deliberately narrow (word characters and spaces only) so it can
# never match a leg line and swallow the rest of the card.
assert p_singles == [], "every ticket on this card has 2+ legs; none is a single"
assert [len(c["legs"]) for w in p_windows for c in w["tickets"]] == [2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3], \
    [len(c["legs"]) for w in p_windows for c in w["tickets"]]

# The header's "(Bettor)" is the ticket OWNER (-> book), and each leg carries
# its own separate "(Who)" -- they differ, and conflating them would misreport
# the Bettor Tracker for the whole card.
p1 = p_windows[0]["tickets"][0]
assert (p1["stake"], p1["payout"], p1["book"]) == (7.33, 310.28, "Memo"), p1
assert [(l["player"], l["team"], l["odds"], l["who"], l["time"]) for l in p1["legs"]] == [
    ("Bo Bichette", "NYM", "+730", "Noid", "6:35 PM ET"),
    ("James Wood", "WSH", "+420", "Francher", "6:40 PM ET")], p1["legs"]
assert p1["book"] != p1["legs"][0]["who"], "ticket owner and leg bettor are different fields"

# An accented name and a generational suffix both survive the round trip --
# MLB's own feed writes them, so stripping either here would make the leg
# ungradeable (the Tatis Jr. incident).
accented = p_windows[1]["tickets"][1]["legs"][1]
assert accented["player"] == "Ronald Acu\u00f1a Jr." and accented["team"] == "ATL", accented
tatis = p_windows[3]["tickets"][0]["legs"][1]
assert tatis["player"] == "Fernando Tatis Jr." and tatis["team"] == "SD", tatis

# As UPLOADED, the bonus legs read "* Francher-Harper (+540)" -- a
# BETTOR-PLAYER pair with no time and no separate "(Who)". Nothing can rescue
# that: the whole string stays the player, resolves to nobody, and the leg
# carries a blank team, so it can never grade either way. Kept as a fixture
# because this is exactly what arrived, and the give-up path must stay
# predictable -- a blank team, never a guessed one.
b10, b11 = p_windows[4]["tickets"]
assert (b10["stake"], b10["payout"], b10["book"]) == (3.0, 1039.18, "Memo"), b10
assert (b11["stake"], b11["payout"], b11["book"]) == (3.0, 609.84, "Kenny"), b11
assert [l["player"] for l in b10["legs"]] == ["Francher-Harper", "Noid-Karros", "Memo-Merrill"]
assert all(l["team"] == "" for l in b10["legs"] + b11["legs"]), \
    "unresolved names keep a BLANK team rather than a guessed one"
assert all(l["time"] == "" for l in b10["legs"]), "bonus legs carry no start time"
# and with no "(Who)" of their own they fall back to the ticket owner
assert [l["who"] for l in b10["legs"]] == ["Memo", "Memo", "Memo"]

# A thousands separator in the payout ("$1,039.18") reaches clean_num intact.
assert b10["payout"] == 1039.18

# --- the CORRECTED bonus shape: "* Player (+ODDS) (Who)", a real player and a
# real per-leg bettor, still with no time. This is what the group re-sent once
# the pairs above turned out to be "<bettor>-<player>" written as one string.
# It is the reason PARLAY_LEG_RE's "(Who)" and "- TIME ET" tails are BOTH
# optional: with the time mandatory these legs matched nothing at all.
corrected = """\U0001f39f\ufe0f Bonus Bets Tracker

Ticket 10 (Memo)
* Bryce Harper (+540) (Francher)
* Kyle Karros (+820) (Noid)
* Jackson Merrill (+490) (Memo)
* Wager: $3.00 | Payout: $1,039.18

Ticket 11 (Kenny)
* Julio Rodriguez (+425) (Bailey)
* Randy Arozarena (+470) (Memo)
* Miguel Vargas (+433) (Kenny)
* Wager: $3.00 | Payout: $609.84
"""
c_windows, c_singles, _ = pp.parse(corrected, team_by_name, canon)
assert c_singles == [] and len(c_windows) == 1, [w["title"] for w in c_windows]
c10, c11 = c_windows[0]["tickets"]
assert [(l["player"], l["team"], l["who"], l["odds"], l["time"]) for l in c10["legs"]] == [
    ("Bryce Harper", "PHI", "Francher", "+540", ""),
    ("Kyle Karros", "COL", "Noid", "+820", ""),
    ("Jackson Merrill", "SD", "Memo", "+490", "")], c10["legs"]
# every leg resolves to a real team -- the whole point of the correction, and
# the difference between a leg that grades and one stuck forever at not_started
assert all(l["team"] for l in c10["legs"] + c11["legs"]), "no leg may be left teamless"
# the per-leg bettor is kept, NOT overwritten by the ticket owner
assert [l["who"] for l in c10["legs"]] == ["Francher", "Noid", "Memo"] and c10["book"] == "Memo"
assert [l["who"] for l in c11["legs"]] == ["Bailey", "Memo", "Kenny"] and c11["book"] == "Kenny"
# the roster supplies the canonical spelling, accent and all
assert c11["legs"][0]["player"] == "Julio Rodr\u00edguez", c11["legs"][0]
assert pp.parse.unread == [], pp.parse.unread

# Every earlier fixture must still parse EXACTLY as it did before this template
# existed -- the whole point of rule 1b. (Section 7 already re-parses them for
# the market default; this repeats it after the fifth template is in play.)
for fixture_text in (gemini_text, md_text, ticket_text, hash_text, sb_text, emoji_text):
    pp.parse(fixture_text, team_by_name, canon)
print("OK: fifth template (\"Parlay N (Bettor)\" under \"Window\" headers) parses 11 tickets (24 legs)")
# --- 12. SIXTH template: the "DAILY HOME RUN PARLAY TRACKER" card. A bare
# bettor NAME opens that person's section; tickets read
# "Ticket #N - 2-Leg Parlay $6.00" with the STAKE in the header; legs are
# checkboxes carrying a full team NICKNAME instead of a code; times have no
# "ET"; "Steal" is written inline; and the footer is "Potential Payout: $X"
# or "N/A". Real upload, 2026-09-23.
tracker_text = (REPO / "tests" / "fixtures" / "discord_tracker_checkbox_format.txt").read_text(encoding="utf-8")
k_windows, k_singles, _ = pp.parse(tracker_text, team_by_name, canon)

# One window per bettor, in card order -- that's what the bare-name line buys.
assert [w["title"] for w in k_windows] == [
    "Francher", "Kenny", "Kevin", "Noid", "Bernie", "Bailey", "Memo"], [w["title"] for w in k_windows]
assert sum(len(c["legs"]) for w in k_windows for c in w["tickets"]) == 24

# The header's own "2-Leg Parlay" is a literal PARLAY_HEADER_RE match, so
# section_header() claimed every ticket header as a new SECTION until
# TRACKER_TICKET_RE joined its exclusion guard -- the THIRD template to hit
# that exact trap. Seven windows (not twenty) is the regression check.
assert len(k_windows) == 7

# Stake comes off the ticket header; the team comes off the ROSTER, never the
# card's nickname ("Braves" -> ATL); the bettor comes off the section heading.
f1 = k_windows[0]["tickets"][0]
assert (f1["stake"], f1["payout"], f1["book"]) == (6.0, 97.68, "Francher"), f1
assert [(l["player"], l["team"], l["who"], l["odds"], l["time"]) for l in f1["legs"]] == [
    ("Matt Olson", "ATL", "Francher", "+340", "7:15 PM ET"),
    ("Kyle Schwarber", "PHI", "Francher", "+270", "6:40 PM ET")], f1["legs"]

# "Steal" written inline is lifted by take_market(): the leg is an SB bet and
# the player name does NOT keep the word.
bailey = k_windows[5]["tickets"]
steal_leg = bailey[0]["legs"][0]
assert steal_leg["player"] == "Elly De La Cruz" and steal_leg.get("market") == "sb", steal_leg
assert bailey[1]["legs"][0]["player"] == "Pete Crow-Armstrong"
assert bailey[1]["legs"][0].get("market") == "sb", bailey[1]["legs"][0]
# ...and a home run leg on the same card stays unmarked
assert bailey[0]["legs"][1].get("market", "hr") == "hr", bailey[0]["legs"][1]

# A leg with NO ODDS is reported, never guessed and never silently dropped: a
# price can't be invented, so that bet isn't tracked and the page says so.
assert pp.parse.unread == ["[ ] Pete Crow-Armstrong (Cubs) 7:40 PM"], pp.parse.unread
# Its ticket therefore has one leg left, which makes it a SINGLE by leg count
# -- never by which header it sat under.
murakami = [s for s in k_singles if s["player"] == "Munetaka Murakami"][0]
assert (murakami["who"], murakami["odds"], murakami["stake"]) == ("FRANCHER", "+350", 6.0), murakami

# A leg marked "- DNP" is an explicit scratch: dropped WITHOUT a warning,
# because the card itself said so -- unlike the unpriced leg above.
assert not any("DNP" in u for u in pp.parse.unread), pp.parse.unread
soto = [s for s in k_singles if s["player"] == "Juan Soto"][0]
assert (soto["who"], soto["odds"], soto["stake"]) == ("NOID", "+420", 5.0), soto
# "Potential Payout: N/A" is null -- not 0, not a guess, not a dropped bet
assert soto["payout"] is None, soto
assert len(k_singles) == 2, k_singles

# Every earlier fixture must STILL parse unchanged: the new patterns are
# reachable only from a tracker ticket, so they cannot reach another template.
for fixture_text in (gemini_text, md_text, ticket_text, hash_text, sb_text, emoji_text, parlay_text):
    pp.parse(fixture_text, team_by_name, canon)
print("OK: sixth template (DAILY HOME RUN PARLAY TRACKER checkbox card) parses 12 tickets (24 legs) across 7 bettors")


print("\nALL PARSER TESTS PASSED")
