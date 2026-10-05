"""
scripts/parse_combined_picks.py -- the MLB + NFL card (sports_*.txt).

Fully offline: reads only checked-in fixtures and the two committed rosters,
writes only to a temp dir, and the NFL schedule lookup is given a fetcher that
raises so nothing reaches ESPN.

    python tests/test_combined_parser.py
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import parse_combined_picks as cp   # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def no_network(_url):
    raise RuntimeError("the tests never reach ESPN")


NOW = datetime(2026, 10, 4, 11, 0, tzinfo=ZoneInfo("America/New_York"))
CARD = (REPO / "tests" / "fixtures" / "sports_combined_format.txt").read_text(encoding="utf-8")
MLB, NFL = cp.load_rosters()
OUT = cp.build(CARD, MLB, NFL, NOW, no_network)

LEGS = [l for w in OUT["windows"] for t in w["tickets"] for l in t["legs"]]
ALL = LEGS + OUT["singles"]
BY = {}
for _l in ALL:
    BY.setdefault((_l["player"], _l.get("market")), _l)


# ================= A. the card parses, and every leg knows its sport ========
check("A1 five tickets and three singles",
      sum(len(w["tickets"]) for w in OUT["windows"]) == 5 and len(OUT["singles"]) == 3,
      f'{sum(len(w["tickets"]) for w in OUT["windows"])}/{len(OUT["singles"])}')
check("A2 every leg carries a sport", all(l.get("sport") in ("mlb", "nfl") for l in ALL),
      [l.get("sport") for l in ALL])
check("A3 the split is 7 MLB / 7 NFL",
      sum(1 for l in ALL if l["sport"] == "mlb") == 7
      and sum(1 for l in ALL if l["sport"] == "nfl") == 7,
      [(l["player"], l["sport"]) for l in ALL])
check("A4 both sports are declared on the slate", OUT["sports"] == ["mlb", "nfl"], OUT["sports"])
check("A5 nothing was dropped or flagged ambiguous", OUT["note"] == "", OUT["note"])


# ================= B. a parlay may hold both sports =========================
# The headline feature. Ticket 4 is one MLB leg + one NFL leg; ticket 5 is
# three legs across both. evaluate_ticket on the page grades each leg by its
# own sport, so this file only has to prove the data says so.
mixed = OUT["windows"][0]["tickets"][3]
three = OUT["windows"][0]["tickets"][4]
check("B1 a two-leg parlay spans MLB and NFL",
      sorted(l["sport"] for l in mixed["legs"]) == ["mlb", "nfl"],
      [(l["player"], l["sport"]) for l in mixed["legs"]])
check("B2 ...and keeps its stake and payout", mixed["stake"] == 6.0 and mixed["payout"] == 208.80,
      (mixed.get("stake"), mixed.get("payout")))
check("B3 a three-leg parlay spans both too",
      sorted(l["sport"] for l in three["legs"]) == ["mlb", "nfl", "nfl"],
      [(l["player"], l["sport"]) for l in three["legs"]])


# ================= C. the collision ========================================
# Five names sit on BOTH rosters and Jose Ramirez is one of them -- a star at
# each. The fixture puts both of him in the SAME parlay on purpose. Getting
# this wrong doesn't throw: it grades a touchdown bet off a boxscore batting
# line, silently, forever. The sport is decided from the leg's own market word
# first and its team second, never from the name.
nfl_jose = [l for l in three["legs"] if l["sport"] == "nfl" and "Ramirez" in l["player"]]
mlb_jose = [l for l in three["legs"] if l["sport"] == "mlb"]
check("C1 the NFL Jose Ramirez is New England, graded as a touchdown",
      len(nfl_jose) == 1 and nfl_jose[0]["team"] == "NE" and nfl_jose[0]["market"] == "td",
      nfl_jose)
check("C2 the MLB Jose Ramirez is Cleveland, graded as a home run",
      len(mlb_jose) == 1 and mlb_jose[0]["team"] == "CLE" and "market" not in mlb_jose[0],
      mlb_jose)
check("C3 ...and they are two different people, not one leg twice",
      len({l["id"] for l in three["legs"]}) == 3, [l["id"] for l in three["legs"]])


# ================= D. NFL legs are gradeable by the football page ===========
# The page matches by ESPN athlete id FIRST and name+team second, so a leg
# without an id is a leg that can only ever be graded by the weaker path.
nfl_legs = [l for l in ALL if l["sport"] == "nfl"]
check("D1 every NFL leg carries an ESPN athlete id",
      all(l.get("athleteId") for l in nfl_legs),
      [(l["player"], l.get("athleteId")) for l in nfl_legs])
check("D2 every NFL leg carries a team", all(l.get("team") for l in nfl_legs),
      [(l["player"], l.get("team")) for l in nfl_legs])
check("D3 an NFL leg naming no market is an anytime touchdown",
      BY[("Saquon Barkley", "td")]["sport"] == "nfl")
check("D4 ...and the team came off the roster, not the opponent in the matchup",
      BY[("Saquon Barkley", "td")]["team"] == "PHI",
      BY[("Saquon Barkley", "td")]["team"])


# ================= E. markets glued to the player name ======================
# parse_picks resolves the player BEFORE it knows a market phrase was stuck to
# the name, so "Max Fried Strikeouts Over 5.5" misses the roster entirely and
# comes back as typed with a BLANK team -- the Tatis Jr. failure mode, a leg
# that can never resolve a hit or a miss.
fried = BY.get(("Max Fried", "k"))
check("E1 an MLB prop's market phrase is stripped off the player name", fried is not None,
      sorted(k[0] for k in BY))
check("E2 ...and the player then resolves to a real team",
      fried and fried["team"] == "NYY", fried)
check("E3 ...keeping the line and the side", fried and fried["line"] == 5.5
      and fried.get("side") in (None, "over"), fried)
check("E4 the same works for an NFL prop",
      BY.get(("Travis Kelce", "rec_yds")) is not None, sorted(k for k in BY))

# An NFL market the page cannot follow must be reported AS ITSELF. Falling
# back to "td" would grade a receiving-yards bet as a touchdown bet -- a
# different question, answered confidently and wrongly. Untracked is the safe
# answer and the whole reason "accept anything" works.
check("E5 an ungradeable NFL prop keeps its own market, it does not become a TD",
      BY[("Travis Kelce", "rec_yds")]["market"] == "rec_yds")
check("E6 ...and the same player's TD leg is still a TD",
      BY[("Travis Kelce", "td")]["market"] == "td")


# ================= F. how the sport is decided, directly ====================
# Checked through decide_sport rather than only through the fixture, because
# the fixture can pass while the ORDER of the evidence is wrong -- and the
# order is the whole design.
hint_cases = [
    ("a market word outranks everything", "Jose Ramirez", "td", "", "", "mlb", "nfl"),
    ("an MLB market word does too", "Jose Ramirez", "k", "", "", "nfl", "mlb"),
    ("a name on one roster only", "Aaron Judge", None, "", "", None, "mlb"),
    ("a name on the other roster only", "Saquon Barkley", None, "", "", None, "nfl"),
    ("a shared name falls to the team", "Jose Ramirez", None, "Cleveland Guardians", "", None, "mlb"),
    ("...and the other way", "Jose Ramirez", None, "New England Patriots", "", None, "nfl"),
    ("the heading is the last resort", "Jose Ramirez", None, "", "", "nfl", "nfl"),
]
for label, name, mkt, rest, head, hint, want in hint_cases:
    got, _why = cp.decide_sport(name, mkt, rest, head, MLB, NFL, hint)
    check(f"F{hint_cases.index((label, name, mkt, rest, head, hint, want)) + 1} {label}",
          got == want, f"{name!r}/{mkt!r} rest={rest!r} hint={hint!r} -> {got!r}, wanted {want!r}")

check("F8 a name on neither roster with nothing else to go on is ambiguous, not a guess",
      cp.decide_sport("Nobody Atall", None, "", "", MLB, NFL, None)[0] is None)

# The team is read from the text AFTER the price, never from the player's own
# name. Plenty of surnames and first names ARE team words -- Buffalo, Jackson,
# Carolina, Phoenix -- and scanning the name finds a team that was never on the
# line. Here the line names a baseball team and the PLAYER happens to contain
# an NFL city; reading both makes the two cancel out and the leg goes
# ambiguous instead of resolving to the team that is actually written.
check("F9 the team is read from the line, not from the player's own name",
      cp.decide_sport("Buffalo Smith", None, "Cleveland Guardians", "Buffalo Smith",
                      MLB, NFL, None)[0] == "mlb",
      cp.decide_sport("Buffalo Smith", None, "Cleveland Guardians", "Buffalo Smith",
                      MLB, NFL, None))


# ================= G. a heading naming BOTH sports sets no default ==========
# A "MIXED (MLB + NFL)" heading must CLEAR the hint rather than leave the
# previous section's. Left set, every leg under it that had no other evidence
# would inherit whichever sport was last named -- which is exactly the kind of
# wrong that looks right until a bet grades off the other league.
found, warns = cp.scan_card(
    "NFL\nParlay 1\n* Saquon Barkley (-135) — Philadelphia Eagles (Kenny) 1:00 PM\n"
    "MIXED (MLB + NFL)\nParlay 2\n* Nobody Atall (+200) (Kenny) 1:00 PM\n",
    MLB, NFL)
check("G1 a leg under a MIXED heading with no other evidence is flagged, not guessed",
      any("could be MLB or NFL" in w for w in warns), warns)
check("G2 ...while the single-sport heading above it still worked",
      found.get("saquon barkley", {}).get("sport") == "nfl", found.get("saquon barkley"))


# ================= H. team words =========================================
check("H1 an NFL city resolves", cp.nfl_team_in("New England Patriots") == "NE")
check("H2 a bare NFL abbreviation resolves", cp.nfl_team_in("(PHI @ NYG)") in ("PHI", "NYG"))
check("H3 an NFL nickname does not match inside a longer word",
      cp.nfl_team_in("Bearsden Rovers") is None, cp.nfl_team_in("Bearsden Rovers"))
check("H4 an MLB city resolves",
      cp.mlb_team_in("Cleveland Guardians", MLB["abbr_by_team_word"]) == "CLE")
check("H5 a line naming no team gives nothing",
      cp.mlb_team_in("7:10 PM", MLB["abbr_by_team_word"]) is None)


# ================= I. the slate span ========================================
# The NFL tab holds a slate until the WEEK's last game -- Monday night even for
# a Sunday-only card -- because a football slate IS an NFL week. A combined
# card is not a week, and the rule the user chose for it is "every game ON THE
# CARD", so the span must come from the picked teams only.
check("I1 a one-day card spans one day", OUT["date"] == OUT["endDate"] == "2026-10-04",
      (OUT["date"], OUT["endDate"]))
check("I2 an unreachable schedule still dates the slate rather than failing",
      cp.nfl_span({"PHI"}, ["1:00 PM ET"], NOW, no_network) == ("2026-10-04", "2026-10-04"),
      cp.nfl_span({"PHI"}, ["1:00 PM ET"], NOW, no_network))
check("I3 no NFL legs means no NFL lookup at all",
      cp.nfl_span(set(), [], NOW, no_network) == (None, None))


# A re-parse AFTER the early games ended. The real one (2026-10-04, 6:39 PM ET,
# for the Sunday-night bet) skipped every finished game and found each of
# those teams' NEXT one, a week out: the slate ran Oct 4 to Oct 11.
def sched(rows):
    """{day: [(teams, state)]} -> a fake ESPN scoreboard fetcher."""
    def fetch(url):
        ymd = url.split("dates=")[1][:8]
        day = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        return {"events": [{"competitions": [{"competitors": [{"team": {"abbreviation": t}} for t in teams]}],
                            "status": {"type": {"state": st}}} for teams, st in rows.get(day, [])]}
    return fetch


SUNDAY = {"2026-10-04": [(("BUF", "NE"), "post"), (("DET", "CAR"), "pre")],
          "2026-10-11": [(("BUF", "MIA"), "pre"), (("DET", "GB"), "pre")]}
check("I4 a team whose game TODAY is already over still belongs to today's card, "
      "while another picked team plays tonight",
      cp.nfl_span({"BUF", "DET"}, [], NOW, sched(SUNDAY)) == ("2026-10-04", "2026-10-04"),
      cp.nfl_span({"BUF", "DET"}, [], NOW, sched(SUNDAY)))
DONE = {"2026-10-04": [(("BUF", "NE"), "post"), (("DET", "CAR"), "post")],
        "2026-10-11": [(("BUF", "MIA"), "pre"), (("DET", "GB"), "pre")]}
check("I5 ...but once every picked game today is over, a card is for the next ones",
      cp.nfl_span({"BUF", "DET"}, [], NOW, sched(DONE)) == ("2026-10-11", "2026-10-11"),
      cp.nfl_span({"BUF", "DET"}, [], NOW, sched(DONE)))


# ================= J. archiving ============================================
with tempfile.TemporaryDirectory() as td:
    out_p, prev_p = Path(td) / "tickets.json", Path(td) / "tickets-previous.json"
    out_p.write_text(json.dumps({"date": "2026-10-03", "windows": []}), encoding="utf-8")
    check("J1 a slate for a NEW day archives the old one",
          cp.archive_previous_slate("2026-10-04", out_p, prev_p) is True)
    check("J2 ...and the archive holds the old slate",
          json.loads(prev_p.read_text(encoding="utf-8"))["date"] == "2026-10-03")

    # A same-day re-upload is a CORRECTION, not a new slate. Archiving it
    # would overwrite the real previous card with a copy of today's.
    prev_p.write_text(json.dumps({"date": "KEEP ME"}), encoding="utf-8")
    out_p.write_text(json.dumps({"date": "2026-10-04", "windows": []}), encoding="utf-8")
    check("J3 a same-day re-upload does NOT archive",
          cp.archive_previous_slate("2026-10-04", out_p, prev_p) is False)
    check("J4 ...so the real previous slate survives it",
          json.loads(prev_p.read_text(encoding="utf-8"))["date"] == "KEEP ME")

    check("J5 nothing to archive is fine",
          cp.archive_previous_slate("2026-10-04", Path(td) / "nope.json", prev_p) is False)


# ================= K. the schema the page will read =========================
check("K1 ids are unique across the whole slate",
      len({l["id"] for l in LEGS}) == len(LEGS))
check("K2 every leg has the fields the page needs",
      all(all(k in l for k in ("id", "player", "team", "who", "odds", "sport")) for l in LEGS),
      [l for l in LEGS if not all(k in l for k in ("id", "player", "team", "who", "odds", "sport"))])
check("K3 odds are signed", all(str(l["odds"])[0] in "+-" for l in ALL),
      [l["odds"] for l in ALL])
check("K4 no leg carries a placeholder team or player",
      all(l["player"] and l["player"].lower() not in ("none", "tbd") for l in ALL))


# ================= L. legs with no price on them ===========================
# The twelfth template -- the shape the group is ACTUALLY sending, landed by
# the auto-fixer on 2026-10-03 -- writes every leg as "- Subject: Bet" with no
# odds anywhere on the line, and mixes MLB, NFL and NHL props in one ticket.
# The pre-pass originally keyed on the price, so on such a card it saw no legs
# at all and every one of them quietly stayed baseball.
PRICELESS = (
    "Ticket 1\n"
    "- DK Metcalf: Anytime TD\n"
    "- Browns: +2.5\n"
    "- Trea Turner: 2+ Total Bases\n"
    "Stake: 8.50 | Pays: 108.48\n"
)
out2 = cp.build(PRICELESS, MLB, NFL, NOW, no_network)
pl = {l["player"]: l for w in out2["windows"] for t in w["tickets"] for l in t["legs"]}

check("L1 a priceless card still parses", len(pl) == 3, sorted(pl))
check("L2 a football player with no price is found and sported",
      pl.get("DK Metcalf", {}).get("sport") == "nfl", pl.get("DK Metcalf"))
check("L3 ...with his anytime-TD market", pl.get("DK Metcalf", {}).get("market") == "td",
      pl.get("DK Metcalf"))
check("L4 a baseball leg on the same ticket stays baseball",
      pl.get("Trea Turner", {}).get("sport") == "mlb", pl.get("Trea Turner"))

# The one that matters. "Browns: +2.5" names a TEAM and is a point spread.
# The market-less default is "anytime touchdown", which for a team would be a
# different bet answered confidently and wrongly -- so the default applies
# only to a name that is actually on the player roster.
browns = pl.get("Browns", {})
check("L5 a team spread is NOT turned into an anytime-touchdown bet",
      browns.get("market") == "spread", browns)
check("L6 ...and it is still recognised as football", browns.get("sport") == "nfl", browns)


# ================= M. the first real card's props ==========================
# Eight legs on it surfaced as UNKNOWN. The sport detection had been RIGHT on
# every one; the answer was then thrown away on a key mismatch, because
# parse_picks reads the "15+" as the market's LINE and drops it from the name
# while this pass was still indexing it WITH the quantity attached.
REAL = (
    "Ticket 1\n"
    "Keon Coleman 15+ Receiving Yards +106\n"
    "Josh Allen 3+ Passing Touchdowns +235\n"
    "Derrick Henry 2+ Touchdowns +240\n"
    "Dione Walker (Bills) Sack +308\n"
    "Jeremiyah Love 80+ Yards +154\n"
    "Barelon Allen Anytime TD +130\n"
    "Mookie Betts 2+ TB 145\n"
    "Stake: 8.00 | Pays: 108.48\n"
)
out3 = cp.build(REAL, MLB, NFL, NOW, no_network)
by = {}
for _l in [l for w in out3["windows"] for t in w["tickets"] for l in t["legs"]] + out3["singles"]:
    by[_l["player"]] = _l

check("M1 a quantity in front of the market is not part of the name",
      "Keon Coleman" in by, sorted(by))
check("M2 ...and the prop keeps the market it actually names",
      by.get("Keon Coleman", {}).get("market") == "rec_yds", by.get("Keon Coleman"))
check("M3 ...on the right sport", by.get("Keon Coleman", {}).get("sport") == "nfl")

# "3+ Passing Touchdowns" must not read as an anytime TD: a thrown touchdown
# does not cash the passer, which is why football's own TD sum excludes
# passing entirely.
check("M4 passing touchdowns are their own market, not an anytime TD",
      by.get("Josh Allen", {}).get("market") == "pass_tds", by.get("Josh Allen"))

# THE one that would have paid out wrongly. "2+ Touchdowns" matches the td
# alias, and td grades BINARY -- did he reach the end zone at all -- so it
# would have cashed on one touchdown when the bet needed two.
henry = by.get("Derrick Henry", {})
check("M5 a 2+ touchdown leg is NOT graded as anytime", henry.get("market") == "td_count", henry)
check("M6 ...and carries the line it was actually set at", henry.get("line") == 1.5, henry)
check("M7 a one-touchdown leg IS still anytime, with no line",
      by.get("Braelon Allen", {}).get("market") == "td"
      and by.get("Braelon Allen", {}).get("line") is None, by.get("Braelon Allen"))

# These two used to assert that the name KEPT its "(Bills)", on the stated
# belief that "he isn't on the roster, so the parenthetical is the only thing
# that says which side he plays for". That belief was wrong: Deone Walker is
# on the roster, BUF. The card misspelt him "Dione" AND bracketed his team,
# and the bracket alone was enough to stop the match. The test was pinning the
# bug. See M30-M33 for the bracket handling itself.
check("M8 a sack prop is named rather than left unknown, under his real name",
      by.get("Deone Walker", {}).get("market") == "sacks", sorted(by))
check("M8b ...resolved to the roster, team and athlete id together",
      by.get("Deone Walker", {}).get("team") == "BUF"
      and by.get("Deone Walker", {}).get("athleteId"), by.get("Deone Walker"))
# The card doesn't say which kind -- his POSITION does (2026-10-05). It used to
# stay a bare, ungradeable "yards", and the user had to say "Jeremiyah Love is
# rushing yards" by hand; a back's yards are rushing.
check("M9 a bare 'Yards' is a yardage prop on the right sport, read by position: "
      "a running back's are rushing",
      by.get("Jeremiyah Love", {}).get("market") == "rush_yds"
      and by.get("Jeremiyah Love", {}).get("sport") == "nfl", by.get("Jeremiyah Love"))

# A typo in a football name gets the same 0.82 fuzzy treatment baseball has
# always had. Unresolved, it is a leg that can never grade a hit OR a miss.
check("M10 a misspelt football name resolves", "Braelon Allen" in by, sorted(by))
check("M11 ...and brings his team with him", by.get("Braelon Allen", {}).get("team"),
      by.get("Braelon Allen"))

# An UNSIGNED price at the end of the line ("...2+ TB 145") is still a price.
# Missed, the pre-pass never saw that leg at all.
# A misspelt BASEBALL name on this card shape never reached resolve_player()
# -- parse_picks' bare-prop path has no fuzzy match, so the leg came back as
# the raw typo with a blank team and could never grade either way. This pass
# does resolve it; the correction just had to be filed under the spelling the
# card used, not only the corrected one.
TYPOS = (
    "Ticket 1\n"
    "Fernado Tatis 3+ H+R+RBI +160\n"
    "Fernado Tatis 1+ Home Run +411\n"
    "Brice Turan 2+ Doubles +490\n"
    "Stake: 8.00 | Pays: 108.48\n"
)
out4 = cp.build(TYPOS, MLB, NFL, NOW, no_network)
tl = [l for w in out4["windows"] for t in w["tickets"] for l in t["legs"]] + out4["singles"]

check("M14 a misspelt baseball name is corrected",
      any(l["player"] == "Brice Turang" and l["team"] == "MIL" for l in tl),
      [(l["player"], l.get("team")) for l in tl])
check("M15 ...including one whose match needs its generational suffix set aside",
      any(l["player"] == "Fernando Tatis Jr." and l["team"] == "SD" for l in tl),
      [(l["player"], l.get("team")) for l in tl])

# THE trap. Both Tatis legs carry the same misspelt name, so a correction
# filed under the name ALONE hands whichever record landed first to both --
# and his home run leg starts grading as an H+R+RBI prop. Same failure as
# Jose Ramirez one sport over: a leg graded against a bet nobody placed.
tatis = [l for l in tl if "Tatis" in l["player"]]
check("M16 two legs on the SAME misspelt player keep their own markets",
      len(tatis) == 2 and {l.get("market") for l in tatis} == {"hrr", None},
      [(l["player"], l.get("market"), l.get("line")) for l in tatis])
check("M17 ...and their own lines", sorted(l.get("line") for l in tatis) == [0.5, 2.5],
      [l.get("line") for l in tatis])

# A TEAM subject, not a player. "Braves Over 3.5 Runs" is the Braves' own
# runs -- gradeable off the score, which teamScores has always carried. It
# came back with a blank team and the player-stat `runs` market, pointed at a
# batter called Braves who does not exist.
TEAMBET = (
    "Ticket 1\n"
    "Braves Over 3.5 Runs +118\n"
    "Aaron Judge 1+ Home Run +390\n"
    "Stake: 8.00 | Pays: 42.00\n"
)
out5 = cp.build(TEAMBET, MLB, NFL, NOW, no_network)
tb_legs = {l["player"]: l for w in out5["windows"] for t in w["tickets"] for l in t["legs"]}
braves = tb_legs.get("Braves", {})
check("M18 a team name resolves to its abbreviation", braves.get("team") == "ATL", braves)
check("M19 ...and becomes a TEAM total, not the player stat of the same name",
      braves.get("market") == "team_total", braves)
check("M20 ...keeping the line it was set at", braves.get("line") == 3.5, braves)
check("M21 a real player on the same ticket is unaffected",
      tb_legs.get("Aaron Judge", {}).get("team") == "NYY", tb_legs.get("Aaron Judge"))

# A combined bet names several players and the line is set for ALL of them.
# Grading it off however many happened to resolve is not a partial answer,
# it is a wrong one -- a 3.5 line judged on two men's touchdowns.
# TWO legs, because parse_picks reads nothing at all from a ONE-leg ticket of
# this shape -- and an empty list makes "no leg has players" vacuously true.
# That is exactly how the first version of this check passed while proving
# nothing, which M22a now makes impossible.
PARTIAL = (
    "Ticket 1\n"
    "Amon-Ra St. Brown, Chubba Hubbard, Zzqq Noperson 4+ Combined TDs +210\n"
    "Brice Turan 2+ Doubles +490\n"
    "Stake: 8.00 | Pays: 146.32\n"
)
out6 = cp.build(PARTIAL, MLB, NFL, NOW, no_network)
pl = [l for w in out6["windows"] for t in w["tickets"] for l in t["legs"]]
check("M22a the fixture actually parsed, so the check below means something",
      len(pl) == 2, [l["player"] for l in pl])
check("M22 a combined leg with an unresolvable name is NOT graded off the rest",
      all(not l.get("players") for l in pl), [(l["player"], l.get("players")) for l in pl])
check("M23 ...and says so in the note rather than going quiet",
      "only" in (out6["note"] or "") and "resolve" in (out6["note"] or ""), out6["note"])

# All three resolving IS the tracked case, typos and all.
WHOLE = (
    "Ticket 1\n"
    "Amon-Ra St. Brown, Chubba Hubbard, Jahmry Gibbs 4+ Combined TDs +210\n"
    "Brice Turan 2+ Doubles +490\n"
    "Stake: 8.00 | Pays: 146.32\n"
)
out7 = cp.build(WHOLE, MLB, NFL, NOW, no_network)
wl = [l for w in out7["windows"] for t in w["tickets"] for l in t["legs"]][0]
check("M24 all three resolving gives one leg naming all three",
      len(wl.get("players") or []) == 3, wl.get("players"))
check("M25 ...with the misspelt two corrected",
      wl.get("players") == ["Amon-Ra St. Brown", "Chuba Hubbard", "Jahmyr Gibbs"],
      wl.get("players"))
check("M26 ...counted, not anytime, at the line the card set",
      wl.get("market") == "td_count" and wl.get("line") == 3.5, wl)

# "Most Receiving Yards" is a different bet from a receiving-yards LINE --
# won against the whole field rather than against a number -- and its text
# contains the other market's, so the order of the alias table decides it.
# The trailing "Sunday" and "Boosted" belong to neither the name nor the
# market, and left on the name nothing resolves.
MOST = (
    "Ticket 1\n"
    "Jaxson Smith-Njigba Most Receiving Yards Sunday +1200 Boosted\n"
    "Keon Coleman 15+ Receiving Yards +106\n"
    "Stake: 8.00 | Pays: 146.32\n"
)
out8 = cp.build(MOST, MLB, NFL, NOW, no_network)
ml = {l["player"]: l for w in out8["windows"] for t in w["tickets"] for l in t["legs"]}
check("M27 'most receiving yards' is its own market, not a yardage line",
      ml.get("Jaxon Smith-Njigba", {}).get("market") == "most_rec_yds", sorted(ml))
check("M28 ...with the day and the boost stripped off the name, so he resolves",
      ml.get("Jaxon Smith-Njigba", {}).get("team") == "SEA",
      ml.get("Jaxon Smith-Njigba"))
check("M29 an ordinary receiving-yards leg on the same ticket is unaffected",
      ml.get("Keon Coleman", {}).get("market") == "rec_yds"
      and ml.get("Keon Coleman", {}).get("line") == 14.5, ml.get("Keon Coleman"))

# "David Bailey (Jets)" -- the team in brackets after the name. Bailey is on
# the roster, NYJ, exact; with "(Jets)" left on the string he matched nothing,
# and the card was flagged as misspelt when it wasn't. "Dione" is a real typo
# for Deone Walker, and the "(Bills)" is what keeps the fuzzy match honest.
PAREN = (
    "Ticket 1\n"
    "Dione Walker (Bills) Sack +308\n"
    "David Bailey (Jets) Sack +249\n"
    "Stake: 4.00 | Pays: 40.00\n"
)
out9 = cp.build(PAREN, MLB, NFL, NOW, no_network)
pb = {l["player"]: l for w in out9["windows"] for t in w["tickets"] for l in t["legs"]}
check("M30 a bracketed team comes off the name, so a rostered player resolves",
      "David Bailey" in pb and pb["David Bailey"].get("athleteId"), sorted(pb))
check("M31 ...and with no false 'not on the roster' warning about him",
      "Bailey" not in (out9["note"] or ""), out9["note"])
check("M32 a typo is corrected within the team the card named",
      pb.get("Deone Walker", {}).get("team") == "BUF"
      and pb.get("Deone Walker", {}).get("athleteId"), sorted(pb))
# The guard itself, directly: a misspelt name whose ONLY close match plays for
# a different team than the one written is not that player.
GUARD = (
    "Ticket 1\n"
    "Dione Walker (Ravens) Sack +308\n"
    "David Bailey (Jets) Sack +249\n"
    "Stake: 4.00 | Pays: 40.00\n"
)
out10 = cp.build(GUARD, MLB, NFL, NOW, no_network)
gb = [l for w in out10["windows"] for t in w["tickets"] for l in t["legs"]]
check("M33 a fuzzy match is refused when he plays for a different team than the "
      "card says -- Deone Walker is BUF, not BAL",
      not any(l["player"] == "Deone Walker" for l in gb), [(l["player"], l.get("team")) for l in gb])

check("M12 an unsigned trailing price is still read as the price",
      by.get("Mookie Betts", {}).get("odds") == "+145", by.get("Mookie Betts"))
check("M13 ...and the leg still resolves its team",
      by.get("Mookie Betts", {}).get("team") == "LAD", by.get("Mookie Betts"))


# ================= N. each team to score in each quarter, as eight legs ======
# Kenny's bet (2026-10-04): one leg per team per quarter, priced only as a
# whole, with the owner named on the footer. The same team appears four times,
# so each leg's quarter has to come from ITS line -- keyed on the name alone,
# all four Lions legs would get the same quarter.
QUARTERS8 = "Ticket 12\n" + "".join(
    f"- {team}: Score in {q} Quarter\n"
    for q in ("1st", "2nd", "3rd", "4th") for team in ("Lions", "Panthers")
) + "Stake: 9.88 | Pays: 84 (Kenny)\n"
out11 = cp.build(QUARTERS8, MLB, NFL, NOW, no_network)
tk11 = [t for w in out11["windows"] for t in w["tickets"]]
q8 = tk11[0]["legs"] if tk11 else []
check("N1 eight legs, one ticket", len(tk11) == 1 and len(q8) == 8, [len(t["legs"]) for t in tk11])
check("N2 every leg is the per-quarter market, in football",
      all(l.get("market") == "q_score" and l.get("sport") == "nfl" for l in q8),
      [(l.get("market"), l.get("sport")) for l in q8])
check("N3 each leg has ITS quarter and ITS team, in card order",
      [(l.get("team"), l.get("quarter")) for l in q8]
      == [(t, q) for q in (1, 2, 3, 4) for t in ("DET", "CAR")],
      [(l.get("team"), l.get("quarter")) for l in q8])
check("N4 the owner named on the footer is the bettor on every leg and the ticket's book",
      all(l.get("who") == "Kenny" for l in q8) and tk11[0].get("book") == "Kenny",
      [l.get("who") for l in q8] + [tk11[0].get("book") if tk11 else None])
check("N4b ...but the legs don't repeat it -- the footer already says who placed the bet",
      all(l.get("meta") == l.get("team") for l in q8), [l.get("meta") for l in q8])
check("N5 stake and payout come off the footer", tk11 and tk11[0]["stake"] == 9.88 and tk11[0]["payout"] == 84.0,
      tk11 and (tk11[0]["stake"], tk11[0]["payout"]))
check("N6 nothing reported as unreadable, and no team mistaken for a missing player",
      out11["note"] == "", out11["note"])
check("N7 a footer WITHOUT an owner still leaves the bettor blank",
      all(l.get("who") == "" for l in [l for w in out5["windows"] for t in w["tickets"] for l in t["legs"]]),
      [l.get("who") for w in out5["windows"] for t in w["tickets"] for l in t["legs"]])
check("N8 the all-four-quarters wording is still its own bet, not one quarter",
      cp.detect_nfl_market("Each team to score all four quarters")[0] == "quarters"
      and cp.detect_nfl_market("Score in 2nd Quarter")[0] == "q_score"
      and cp.detect_nfl_market("scores in Q3")[0] == "q_score")



# ================= O. the 2026-10-05 card: bare counts, no "+" =================
# Every leg written as a bare count with an unsigned price -- "Gavin Williams 9
# Strikeouts 259" -- so the 9 was lost and a nine-strikeout bet would have
# graded on the first one. Also: "3 H R Rbi" without its plus signs, a receiving
# yards UNDER that joined to nothing and fell back to baseball, a pitcher's
# OUTS, EXTRA BASE HITS, an unsigned run line, a bare "Yards", the same man on
# two different bets, and an NHL leg read as the Buccaneers. The real upload.
BARE = (REPO / "tests" / "fixtures" / "sports_bare_count_format.txt").read_text(encoding="utf-8")
out12 = cp.build(BARE, MLB, NFL, NOW, no_network)
bl = [l for w in out12["windows"] for t in w["tickets"] for l in t["legs"]]
def find(player, market=None):
    return next((l for l in bl if l["player"] == player and (market is None or l.get("market") == market)), {})
check("O1 the whole card parses: 10 tickets, 24 legs, nothing unread",
      sum(len(w["tickets"]) for w in out12["windows"]) == 10 and len(bl) == 24 and out12["note"] == "",
      (len(bl), out12["note"]))
check("O2 a bare count is AT LEAST that many: '9 Strikeouts' is over 8.5",
      find("Gavin Williams").get("market") == "k" and find("Gavin Williams").get("line") == 8.5, find("Gavin Williams"))
check("O3 '3 H R Rbi', no plus signs, is H+R+RBI over 2.5",
      find("Kyle Teel").get("market") == "hrr" and find("Kyle Teel").get("line") == 1.5
      and [l.get("line") for l in bl if l.get("market") == "hrr" and l["player"] != "Kyle Teel"] == [2.5, 2.5],
      [(l["player"], l.get("market"), l.get("line")) for l in bl if l.get("market") == "hrr"])
check("O4 an NFL receiving-yards UNDER is football, with its line and side",
      find("Kyle Pitts Sr.").get("sport") == "nfl" and find("Kyle Pitts Sr.").get("market") == "rec_yds"
      and find("Kyle Pitts Sr.").get("line") == 29.5 and find("Kyle Pitts Sr.").get("side") == "under", find("Kyle Pitts Sr."))
check("O5 a pitcher's outs: 'Over 14.5 Outs'",
      find("Freddy Peralta").get("market") == "outs" and find("Freddy Peralta").get("line") == 14.5, find("Freddy Peralta"))
check("O6 extra-base hits are their own market, not plain hits, and the player resolves",
      find("Chase DeLauter").get("market") == "xbh" and find("Chase DeLauter").get("team"), find("Chase DeLauter"))
check("O7 an unsigned team half-point is the plus side of the run line, on the Rays (not the Bucs)",
      find("Tampa Bay Rays").get("market") == "spread" and find("Tampa Bay Rays").get("line") == 1.5
      and find("Tampa Bay Rays").get("team") == "TB" and find("Tampa Bay Rays").get("sport") == "mlb", find("Tampa Bay Rays"))
check("O8 a bare 'Yards' is read by position: a QB's are passing, a receiver's receiving",
      find("Michael Penix Jr.").get("market") == "pass_yds" and find("Michael Penix Jr.").get("line") == 249.5
      and find("Devaughn Vele", "rec_yds").get("line") == 59.5,
      (find("Michael Penix Jr."), find("Devaughn Vele", "rec_yds")))
check("O9 one man on two different bets keeps each its own line -- Bellinger's HR, and his 3+ H+R+RBI",
      find("Cody Bellinger", "hrr").get("line") == 2.5 and find("Cody Bellinger").get("market") in (None, "hr"),
      [(l.get("market"), l.get("line")) for l in bl if l["player"] == "Cody Bellinger"])
lt = find("Tampa Bay Lightning")
# Shown-but-untracked was the stopgap until hockey was built (later the same
# day): it is now a real puck line on the LIGHTNING, never the Bucs or Rays.
check("O10 the NHL leg is hockey: the Lightning's puck line, +1.5",
      lt.get("sport") == "nhl" and lt.get("market") == "nhl_pl" and lt.get("team") == "TB"
      and lt.get("line") == 1.5, lt)
check("O11 ...so the slate is today only -- the Bucs' Thursday game is not on this card",
      out12["date"] == out12["endDate"], (out12["date"], out12["endDate"]))
_untouched = "Ticket 4\n8 pays 96.91\n5 Pays 228.46\n"
check("O12 the rewrite never touches a footer or a header",
      cp.normalize_card(_untouched) == _untouched, cp.normalize_card(_untouched))
_untouched = "A B 2+ Hits 150\nC D Over 14.5 Outs 189\n"
check("O13 ...or a count that already says '+', or a decimal line",
      cp.normalize_card(_untouched) == _untouched, cp.normalize_card(_untouched))
check("O14 a team word only ONE league uses breaks a shared-city tie",
      cp.decide_sport("Tampa Bay Rays", "spread", "", "Tampa Bay Rays +1.5", MLB, NFL, None)[0] == "mlb"
      and cp.decide_sport("Tampa Bay Buccaneers", "spread", "", "Tampa Bay Buccaneers +3.5", MLB, NFL, None)[0] == "nfl")


# ================= P. hockey (2026-10-05) =====================================
# A hockey_*.txt card through parse_hockey_picks (every leg forced to NHL), and
# hockey legs on an All Sports card, where nothing forces anything. Written to
# the generator's current shape, not observed -- no real hockey card yet.
import parse_hockey_picks as hp            # noqa: E402
import build_hockey_roster as bhr          # noqa: E402
import subprocess                          # noqa: E402
HOCKEY = (REPO / "tests" / "fixtures" / "hockey_format.txt").read_text(encoding="utf-8")
HNOW = datetime(2026, 10, 6, 11, 0, tzinfo=ZoneInfo("America/New_York"))
hk = hp.build(HOCKEY, HNOW, no_network)
hl = [l for w in hk["windows"] for t in w["tickets"] for l in t["legs"]] + hk["singles"]
def hfind(player):
    return next((l for l in hl if l["player"] == player), {})
check("P1 the hockey card parses: 2 parlays, 2 singles, every leg NHL, nothing flagged -- not even its title",
      sum(len(w["tickets"]) for w in hk["windows"]) == 2 and len(hk["singles"]) == 2
      and all(l.get("sport") == "nhl" for l in hl) and hk["note"] == "" and hk["sports"] == ["nhl"],
      (len(hl), hk["note"], hk["sports"]))
bp = hfind("Brayden Point")
check("P2 'Brayden Point Anytime Goal' is a GOAL bet -- his surname is not a points prop",
      bp.get("market") == "nhl_goal" and bp.get("team") == "TB" and bp.get("athleteId"), bp)
check("P3 shots on goal and points carry their lines; neither is read as a goal",
      hfind("Auston Matthews").get("market") == "nhl_sog" and hfind("Auston Matthews").get("line") == 3.5
      and hfind("Connor McDavid").get("market") == "nhl_points" and hfind("Connor McDavid").get("line") == 1.5,
      (hfind("Auston Matthews"), hfind("Connor McDavid")))
check("P4 a team bet: 'Lightning Moneyline' is the Lightning's moneyline",
      hfind("Tampa Bay Lightning").get("market") == "nhl_ml" and hfind("Tampa Bay Lightning").get("team") == "TB",
      hfind("Tampa Bay Lightning"))
tot = next((l for l in hl if l.get("market") == "nhl_total"), {})
check("P5 two teams joined by '/' with an over is the GAME's total goals, both teams kept",
      tot.get("line") == 6.5 and tot.get("side") == "over" and tot.get("teams") == ["BOS", "TOR"], tot)
check("P6 a signed half-goal on a team is the puck line, sign kept",
      hfind("Carolina Hurricanes").get("market") == "nhl_pl" and hfind("Carolina Hurricanes").get("line") == -1.5,
      hfind("Carolina Hurricanes"))
check("P7 singles: a goalie's saves and a skater's assists",
      hfind("Andrei Vasilevskiy").get("market") == "nhl_saves" and hfind("Andrei Vasilevskiy").get("line") == 27.5
      and hfind("David Pastrnak").get("market") == "nhl_assists", (hfind("Andrei Vasilevskiy"), hfind("David Pastrnak")))
_bad = hp.build("Parlay 1\n* Zed Nobodyson Anytime Goal (+300) — Tampa Bay Lightning (Kenny) 7:00 PM\n"
                "* Brayden Point Anytime Goal (+210) — Tampa Bay Lightning (Kenny) 7:00 PM\n"
                "$5.00 Bet | Potential Payout: $50.00 (Kenny)\n", HNOW, no_network)
check("P8 ...but a PRICED leg naming nobody on the roster is still reported, never dropped",
      "Zed Nobodyson" in _bad["note"], _bad["note"])
check("P9 the market words: 'Shots on Goal' is shots, a kicker's 'Field Goals' is not hockey at all",
      cp.detect_nhl_market("Shots on Goal Over 3.5")[0] == "nhl_sog"
      and cp.detect_nhl_market("Field Goals Made Over 1.5")[0] is None
      and cp.detect_nhl_market("Anytime Goal Scorer")[0] == "nhl_goal")
_mixed = cp.build("Parlay 1\n* Florida Panthers Moneyline (-140) — Florida Panthers (Memo) 7:00 PM\n"
                  "* Carolina Panthers Moneyline (+150) — Carolina Panthers (Memo) 1:00 PM\n"
                  "* Aaron Judge (+390) — New York Yankees (Memo) 7:10 PM\n"
                  "$5.00 Bet | Potential Payout: $60.00 (Memo)\n", MLB, NFL, NOW, no_network)
_ml = {l["player"]: l for w in _mixed["windows"] for t in w["tickets"] for l in t["legs"]}
check("P10 on an All Sports card 'Panthers' needs its city: Florida's are hockey, Carolina's football, Judge baseball",
      (_ml.get("Florida Panthers") or {}).get("sport") == "nhl"
      and (_ml.get("Carolina Panthers") or {}).get("sport") == "nfl"
      and (_ml.get("Aaron Judge") or {}).get("sport") == "mlb", {k: v.get("sport") for k, v in _ml.items()})
check("P11 ...and the slate declares all three", _mixed["sports"] == ["mlb", "nfl", "nhl"], _mixed["sports"])
_old = {"team_by_name": {"a": "TB", "b": "BOS"}, "canonical_name_by_norm": {"a": "A", "b": "B"},
        "id_by_norm": {"a": "1", "b": "2"}, "pos_by_norm": {"a": "C", "b": "D"}, "team_names": {"TB": "Tampa Bay Lightning"}}
_new = {"team_by_name": {"a": "TOR"}, "canonical_name_by_norm": {"a": "A"},
        "id_by_norm": {"a": "1"}, "pos_by_norm": {"a": "C"}, "team_names": {"TOR": "Toronto Maple Leafs"}}
_m = bhr.merge_rosters(_old, _new)
check("P12 a roster rebuild merges: a player ESPN stopped listing (IR) is kept, a moved one takes his new team",
      _m["team_by_name"] == {"a": "TOR", "b": "BOS"} and _m["id_by_norm"]["b"] == "2"
      and set(_m["team_names"]) == {"TB", "TOR"}, _m)
with tempfile.TemporaryDirectory() as td:
    src, out_p, prev_p = Path(td, "in.txt"), Path(td, "t.json"), Path(td, "p.json")
    src.write_text(HOCKEY, encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "parse_hockey_picks.py"), "--file", str(src),
                        "--out", str(out_p), "--prev", str(prev_p), "--no-network"],
                       capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    written = json.loads(out_p.read_text(encoding="utf-8")) if out_p.exists() else {}
    check("P13 the CLI writes exactly where it's told, as hockey", r.returncode == 0 and written.get("sports") == ["nhl"],
          (r.returncode, r.stderr[-300:]))
# Will Smith catches for the Dodgers AND centres for the Sharks. Nothing on the
# line says hockey -- only the file it came in on -- and a hockey_*.txt card
# is hockey, while the same line on an All Sports card is the catcher.
_ws = "🎯 Longshot (LS) Straight Bets (Single Legs)\nKenny: Will Smith (+400) | (SJ @ ANA) 7:00 PM ET • $5.00 bet | PP: $25.00\n"
_h = (hp.build(_ws, HNOW, no_network)["singles"] or [{}])[0]
_a = (cp.build(_ws, MLB, NFL, HNOW, no_network)["singles"] or [{}])[0]
check("P14 a hockey card's name shared with another league is the HOCKEY player -- the Sharks' Will Smith, his id, a goal bet",
      _h.get("sport") == "nhl" and _h.get("team") == "SJ" and _h.get("athleteId") and _h.get("market") == "nhl_goal"
      and _a.get("sport") == "mlb" and _a.get("team") == "LAD", (_h, _a))

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all combined-parser checks passed")
