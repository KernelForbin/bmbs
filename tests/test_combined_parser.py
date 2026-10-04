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

# The name keeps its "(Bills)" annotation, which is how the team got
# resolved at all -- he isn't on the roster, so the parenthetical is the only
# thing that says which side he plays for. Asserted as it really is rather
# than as it would look tidiest.
check("M8 a sack prop is named rather than left unknown",
      by.get("Dione Walker (Bills)", {}).get("market") == "sacks", sorted(by))
check("M8b ...and the parenthetical team is what resolves him",
      by.get("Dione Walker (Bills)", {}).get("team") == "BUF",
      by.get("Dione Walker (Bills)"))
check("M9 a bare 'Yards' is a yardage prop on the right sport, not an unknown "
      "BASEBALL market -- the card doesn't say which kind and neither do we",
      by.get("Jeremiyah Love", {}).get("market") == "yards"
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

check("M12 an unsigned trailing price is still read as the price",
      by.get("Mookie Betts", {}).get("odds") == "+145", by.get("Mookie Betts"))
check("M13 ...and the leg still resolves its team",
      by.get("Mookie Betts", {}).get("team") == "LAD", by.get("Mookie Betts"))


print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all combined-parser checks passed")
