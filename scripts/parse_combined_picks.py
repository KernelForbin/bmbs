#!/usr/bin/env python3
"""Parse a combined MLB + NFL card (``sports_*.txt``) into data/combined/tickets.json.

WHY THIS IS A THIN LAYER AND NOT A THIRD PARSER
-----------------------------------------------
``parse_picks.py`` already understands ten card templates, every ticket/bettor/
stake/payout/odds shape the group's generator has ever emitted, and the
unread-bet-line safety net. Re-implementing any of that here would guarantee
the two drift apart, and the drift would be silent -- a card parsing to zero.

So the card is parsed by ``parse_picks.parse()`` exactly as a baseball card is,
and this module only answers the one question that parser cannot: WHICH SPORT
is each leg, and therefore which roster resolves the player and which market
the leg is.

The join between the two halves is the leg's player STRING. That works because
``parse_picks`` either resolves a name against the roster it was given (and
returns the canonical spelling) or, failing that, keeps the text exactly as
typed. Both are deterministic, so a pre-pass over the raw lines can index its
findings under both spellings and the post-pass can look each leg up.

Keying on the raw string -- not on the cleaned player name -- is what lets one
card carry "Jose Ramirez Anytime TD" (NE) and "Jose Ramirez" (CLE) in the SAME
parlay and grade them as different people in different sports. Five names are
on both rosters; Jose Ramirez is one of them, and he is a star in both.
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse_picks as mlb_parser            # noqa: E402
import parse_football_picks as nfl_parser   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MLB_ROSTER = ROOT / "data" / "roster.json"
NFL_ROSTER = ROOT / "data" / "football" / "roster.json"
OUT_PATH = ROOT / "data" / "combined" / "tickets.json"
PREV_PATH = ROOT / "data" / "combined" / "tickets-previous.json"

# ---------------------------------------------------------------------------
# NFL team words. The football roster stores team ABBREVIATIONS only -- unlike
# baseball's, it has no abbr_by_team_word -- but a card writes "Philadelphia
# Eagles" or "Eagles", never "PHI". Thirty-two rows of stable data is cheaper
# and far more predictable than another network call.
# ---------------------------------------------------------------------------
NFL_TEAM_WORDS = {
    "cardinals": "ARI", "arizona": "ARI",
    "falcons": "ATL", "atlanta": "ATL",
    "ravens": "BAL", "baltimore": "BAL",
    "bills": "BUF", "buffalo": "BUF",
    "panthers": "CAR", "carolina": "CAR",
    "bears": "CHI", "chicago": "CHI",
    "bengals": "CIN", "cincinnati": "CIN",
    "browns": "CLE",
    "cowboys": "DAL", "dallas": "DAL",
    "broncos": "DEN", "denver": "DEN",
    "lions": "DET",
    "packers": "GB", "green bay": "GB",
    "texans": "HOU",
    "colts": "IND", "indianapolis": "IND",
    "jaguars": "JAX", "jacksonville": "JAX",
    "chiefs": "KC",
    "chargers": "LAC",
    "rams": "LAR",
    "raiders": "LV", "las vegas": "LV",
    "dolphins": "MIA",
    "vikings": "MIN", "minnesota": "MIN",
    "patriots": "NE", "new england": "NE",
    "saints": "NO", "new orleans": "NO",
    "giants": "NYG",
    "jets": "NYJ",
    "eagles": "PHI", "philadelphia": "PHI",
    "steelers": "PIT", "pittsburgh": "PIT",
    "seahawks": "SEA",
    "49ers": "SF", "niners": "SF",
    "buccaneers": "TB", "bucs": "TB", "tampa bay": "TB",
    "titans": "TEN", "tennessee": "TEN",
    "commanders": "WSH",
}

# Words that name a sport outright, for a section header or a leg tag.
MLB_WORDS = re.compile(r"\b(?:mlb|baseball|home\s*runs?)\b", re.IGNORECASE)
NFL_WORDS = re.compile(r"\b(?:nfl|football)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# NFL markets. Checked BEFORE baseball's table, which is safe because no
# baseball card says "touchdown" -- and necessary because baseball's table is
# read in order and would otherwise claim some of these first.
#
# Only "td" is GRADEABLE. Every other row exists so a yardage prop is reported
# as the market it actually is and grades to `untracked` on the page -- shown,
# named, counted in neither column, unable to kill a parlay -- rather than
# silently defaulting to "did he score a touchdown", which is a different bet
# and would be confidently wrong.
# ---------------------------------------------------------------------------
# "3+" / "15+" -- a quantity the card puts in FRONT of the market, which
# parse_picks reads as the line (3+ means over 2.5) and strips from the name.
QTY_RE = re.compile(r"\b\d+(?:\.\d+)?\s*\+")

# A WHEN hanging off the end of a market -- "Most Receiving Yards Sunday",
# "Anytime TD Tonight". It belongs to neither the name nor the market, and
# left on the name it stops the player resolving at all.
WHEN_TAIL_RE = re.compile(
    r"\s*\b(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday"
    r"|tonight|today|boosted|bonus)\b\s*$", re.IGNORECASE)


# "(Jets)" / "(Bills)" -- a team in brackets at the END of a name.
PAREN_TEAM_RE = re.compile(r"\s*\(([^()]{2,30})\)\s*$")


def strip_when(text):
    """Trailing day-of-week / boost words, repeatedly: "... Sunday Boosted"."""
    prev = None
    while prev != text:
        prev = text
        text = WHEN_TAIL_RE.sub("", text).strip()
    return text

NFL_MARKET_ALIASES = [
    # BEFORE "td": "3+ Passing Touchdowns" contains the word, and a passing
    # touchdown does not cash an anytime-TD bet on the man who threw it --
    # the same exclusion football/index.html makes when it sums its TD
    # columns and deliberately leaves passing out.
    # BEFORE td: "each team to score all four quarters" contains neither
    # word, but it must not fall through to the generic yardage/unknown rows
    # either. Graded off ESPN's per-quarter linescores.
    # ONE team scoring in ONE named quarter -- "Lions: Score in 1st Quarter".
    # An "each team scores every quarter" bet laid out leg by leg is eight of
    # these. BEFORE "quarters" so a quarter that is NAMED is never read as all
    # of them.
    ("q_score",        r"\bscores?\s+(?:a\s+point\s+)?in\s+(?:the\s+)?(?:1st|2nd|3rd|4th|first|second|third|fourth)\s+(?:quarter|qtr)\b"
                       r"|\bscores?\s+in\s+q[1-4]\b"),
    ("quarters",       r"all\s+four\s+quarters|every\s+quarter|all\s*4\s*quarters"),
    ("pass_tds",       r"passing\s+(?:td|touchdown)s?"),
    # "combined" rides along with the market word on purpose. Left behind it
    # stays glued to the LAST name in the list -- "Jahmry Gibbs Combined" --
    # which then resolves to nobody and silently drops that player from a
    # three-man bet.
    ("td",             r"anytime\s*(?:td|touchdown)|to\s+score\s+a?\s*(?:td|touchdown)"
                       r"|combined\s*(?:td|touchdown)s?|\btouchdowns?\b|\btds?\b"),
    ("sacks",          r"\bsacks?\b"),
    # BEFORE rec_yds, which its text contains. "Most receiving yards" is won
    # against the whole field rather than against a number, so it is a
    # different bet settled a different way.
    ("most_rec_yds",   r"most\s+receiving\s+yards?"),
    ("rec_yds",        r"receiving\s+yards?|\brec\s+yds?\b"),
    ("rush_yds",       r"rushing\s+yards?|\brush\s+yds?\b"),
    ("pass_yds",       r"passing\s+yards?|\bpass\s+yds?\b"),
    ("receptions",     r"receptions?\b"),
    # LAST of the yardage rows, so "Receiving Yards" / "Rushing Yards" claim
    # theirs first. A bare "80+ Yards" doesn't say which kind, and the card
    # is the only thing that would know -- but every yardage prop is
    # untracked anyway, so naming it "yards" is both honest and strictly
    # better than letting it fall through as an unknown BASEBALL market.
    ("yards",          r"\byards?\b"),
]
NFL_MARKET_RE = [(k, re.compile(p, re.IGNORECASE)) for k, p in NFL_MARKET_ALIASES]

# Markets that only exist in one sport, used to settle the sport question
# before anything else is consulted.
MLB_ONLY_MARKETS = {"hr", "sb", "hrr", "hits", "rbi", "runs", "tb",
                    "doubles", "k", "er", "win", "f5"}
NFL_ONLY_MARKETS = {m for m, _ in NFL_MARKET_ALIASES} | {"td_count"}

# The first signed 2-4 digit number on a line is the price. Two digits minimum
# so a "-1.5" run line or an "Over 5.5" total can never be read as odds.
ODDS_RE = re.compile(r"[+-]\d{2,4}(?!\.\d)(?!\d)")
# A leg line carrying no price at all: a bullet, the subject, a colon, then
# what the bet is -- "- Tage Thompson: Anytime Goal". The bullet is REQUIRED
# so an ordinary sentence containing a colon can't be read as a leg, and the
# subject is kept short so a prose line that happens to start with a dash
# doesn't qualify either.
BULLET_LEG_RE = re.compile(
    r"^\s*[-*•●▪]\s*([^:]{2,40}?)\s*:\s*(.+?)\s*$")

# Leading bullet / checkbox / numbering / "Bettor:" prefix on a leg line.
LEG_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•●▪>]+\s*|\[\s*[x ]?\s*\]\s*|\d+[.)]\s*)*"
    r"(?:([A-Za-z][\w' .-]{0,20}):\s*)?")


# Which quarter a "score in the Nth quarter" leg names.
QUARTER_RE = re.compile(r"\b(1st|2nd|3rd|4th|first|second|third|fourth|q[1-4])\b", re.IGNORECASE)
QUARTER_NUM = {"1st": 1, "first": 1, "q1": 1, "2nd": 2, "second": 2, "q2": 2,
               "3rd": 3, "third": 3, "q3": 3, "4th": 4, "fourth": 4, "q4": 4}


def detect_nfl_market(text):
    """-> (market key, text with the market phrase removed) or (None, text)."""
    for key, rx in NFL_MARKET_RE:
        if rx.search(text):
            return key, re.sub(r"\s{2,}", " ", rx.sub(" ", text, count=1)).strip(" -|:–—")
    return None, text


def mlb_team_in(text, words):
    """The MLB abbreviation this text names, or None.

    parse_picks.team_abbr_for() cannot do this job: it normalizes the WHOLE
    string into one key, so it answers for a text that IS a team name and
    returns None for a line that merely contains one.
    """
    low = f" {text.lower()} "
    for word in sorted(words, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", low):
            return words[word]
    return None


def nfl_team_in(text):
    """The NFL abbreviation this text names, or None. Longest word first so
    'new england' beats nothing and 'tampa bay' isn't missed by 'tampa'."""
    low = f" {text.lower()} "
    for word in sorted(NFL_TEAM_WORDS, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", low):
            return NFL_TEAM_WORDS[word]
    # A bare abbreviation, as a matchup like "(PHI @ NYG)" writes it.
    for abbr in set(NFL_TEAM_WORDS.values()):
        if re.search(rf"(?<![A-Za-z]){re.escape(abbr)}(?![A-Za-z])", text):
            return abbr
    return None


def load_rosters():
    mlb = json.loads(MLB_ROSTER.read_text(encoding="utf-8"))
    nfl = json.loads(NFL_ROSTER.read_text(encoding="utf-8"))
    return mlb, nfl


def _in_roster(norm, roster):
    return norm in roster["team_by_name"]


def decide_sport(cleaned, market, rest_of_line, head_of_line, mlb, nfl, section_hint):
    """Which sport is this leg? Returns ('mlb'|'nfl'|None, why).

    Per-leg evidence outranks the section header ON PURPOSE: a card that files
    a mixed parlay under an "MLB" heading would otherwise mis-sport half of it,
    and a heading is the one signal that is about the GROUP of bets rather than
    this bet.
    """
    if market in NFL_ONLY_MARKETS:
        return "nfl", f"market {market!r} is NFL-only"
    if market in MLB_ONLY_MARKETS:
        return "mlb", f"market {market!r} is MLB-only"

    norm_mlb = mlb_parser.normalize_name(cleaned)
    norm_nfl = nfl_parser.norm_key(cleaned)
    in_mlb = _in_roster(norm_mlb, mlb)
    in_nfl = _in_roster(norm_nfl, nfl)
    if in_mlb and not in_nfl:
        return "mlb", "only on the MLB roster"
    if in_nfl and not in_mlb:
        return "nfl", "only on the NFL roster"

    # On both rosters (five names are, Jose Ramirez among them), or on
    # neither. The team is the tiebreak -- fifteen abbreviations are shared
    # between the leagues, so only a team word unique to one league counts.
    words = mlb.get("abbr_by_team_word") or {}
    nfl_team = nfl_team_in(rest_of_line)
    mlb_team = mlb_team_in(rest_of_line, words)
    if not nfl_team and not mlb_team:
        # Only now look at the text BEFORE the price, for the templates that
        # write the team there ("Saquon Barkley (Eagles) -110"). It has to be
        # last and it has to be all-or-nothing: that text is the player's own
        # name, and plenty of names ARE team words -- Buffalo, Jackson,
        # Carolina, Phoenix. Consulting it as a per-sport fallback let a leg
        # whose line clearly named one team pick up a second one off the name
        # and go ambiguous between them.
        nfl_team = nfl_team_in(head_of_line)
        mlb_team = mlb_team_in(head_of_line, words)
    if nfl_team and not mlb_team:
        return "nfl", f"team {nfl_team} is NFL-only"
    if mlb_team and not nfl_team:
        return "mlb", f"team {mlb_team} is MLB-only"

    if section_hint:
        return section_hint, "the section heading"
    return None, "nothing on the line says which sport"


def scan_card(text, mlb, nfl):
    """Pre-pass: read the RAW card and work out each leg's sport and market.

    -> ({normalized raw player text: record}, [ambiguity warnings])
    """
    found, warnings = {}, []
    section_hint = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        odds = ODDS_RE.search(line)
        tail = None
        if not odds:
            # A price written with no sign, at the end of the line: "Mookie
            # Betts 2+ TB 145". parse_picks' own splitter already knows the
            # rules for telling that from a market's line, so borrow it
            # rather than inventing a second set. Without this the pre-pass
            # never saw those legs at all and they fell through unresolved.
            body, tail_odds = mlb_parser.split_trailing_odds(line)
            if tail_odds:
                tail = body
        bullet = BULLET_LEG_RE.match(line) if not odds and not tail else None
        if not odds and not bullet and not tail:
            # Prose. A heading naming exactly ONE sport sets the default for
            # what follows; one naming BOTH (a "MIXED" header) deliberately
            # clears it, so every leg under it is judged alone.
            says_mlb, says_nfl = bool(MLB_WORDS.search(line)), bool(NFL_WORDS.search(line))
            if says_mlb and says_nfl:
                section_hint = None
            elif says_mlb:
                section_hint = "mlb"
            elif says_nfl:
                section_hint = "nfl"
            continue

        if odds:
            head, rest = line[:odds.start()], line[odds.end():]
        elif tail is not None:
            head, rest = tail, ""
        else:
            # A PRICELESS leg line: "- Pat Freiermuth: 30+ Receiving Yards".
            # The twelfth template writes every leg this way, and it is the
            # shape the group is actually sending -- mixing MLB, NFL and NHL
            # props in one ticket. Requiring a price here meant the pre-pass
            # saw no legs at all on such a card and every one of them silently
            # defaulted to baseball.
            #
            # Over-collecting is safe: an entry is only ever USED when a leg
            # parse_picks actually produced matches it by name.
            head, rest = bullet.group(1), bullet.group(2) or ""
        candidate = LEG_PREFIX_RE.sub("", head).strip(" (|-:–—·")
        if not candidate:
            continue

        # WHERE the market is written depends on the shape. With a price, it
        # is glued to the player name ("Max Fried Strikeouts Over 5.5") and
        # has to be stripped back off. Without one, the line already split it
        # out for us and the subject is just the subject. Reading the whole
        # line in both cases would let a stray "Over 8.5" from one leg's tail
        # land on another's.
        market_src = candidate if (odds or tail is not None) else rest
        market, cleaned = detect_nfl_market(market_src)
        if market is None:
            mkey, mline, mside, mcleaned = mlb_parser.detect_market(market_src)
            market, cleaned = mkey, mcleaned
            line_val, side_val = mline, mside
        else:
            # An NFL prop can still carry its own over/under ("Receiving Yards
            # Over 62.5"), and baseball's detector is the thing that reads one.
            _k, line_val, side_val, cleaned = mlb_parser.detect_market(cleaned)
        if not odds and tail is None:
            cleaned = candidate      # the subject was never the market

        sport, why = decide_sport(cleaned, market, rest, head, mlb, nfl, section_hint)
        if sport is None:
            # An UNSIGNED trailing number is weak evidence that this was ever
            # a leg -- a heading ending "OCTOBER 4, 2026" ends in one too. So
            # a line reached only that way and then sporting to nothing is
            # dropped quietly rather than reported as an ambiguous bet. A
            # SIGNED price is unambiguous, and still gets the warning.
            if tail is None:
                warnings.append(f"{candidate!r} could be MLB or NFL ({why})")
            continue

        # "15+" is the market's LINE, never part of the player's name. Left on
        # it, nothing resolves against either roster. It is also the only
        # place the line is stated on this card shape -- "2+ Touchdowns" has
        # no over/under phrase for detect_market to read -- so take the number
        # before discarding it: N+ means at least N, i.e. an over on N-0.5.
        qty = QTY_RE.search(cleaned) or QTY_RE.search(candidate)
        if qty and line_val is None:
            line_val = float(qty.group(0).rstrip("+ ").strip()) - 0.5
            side_val = side_val or "over"
        cleaned = strip_when(QTY_RE.sub(" ", cleaned).strip(" -:|·"))
        # "David Bailey (Jets)" -- a TEAM in brackets after the name. Left on,
        # it is part of the string being looked up, so a player who is plainly
        # on the roster (Bailey is, NYJ, exact) matched nothing; the leg only
        # got a team at all by reading the word "Jets", with no player and no
        # athlete id behind it. Taken off, and kept as a hint the match has to
        # agree with.
        paren_team = None
        pm = PAREN_TEAM_RE.search(cleaned)
        if pm:
            paren_team = (nfl_team_in(pm.group(1))
                          or mlb_team_in(pm.group(1), mlb.get("abbr_by_team_word") or {}))
            if paren_team:
                cleaned = cleaned[:pm.start()].strip()
        rec = {"sport": sport, "market": market, "line": line_val,
               "side": side_val, "name": cleaned, "why": why}
        # A TEAM subject, not a player: "Braves Over 3.5 Runs" is the Braves'
        # own runs. Nothing on the player rosters will ever match it, so the
        # leg came back with a blank team and the player-stat `runs` market --
        # pointed at a batter called Braves, who does not exist. It IS
        # gradeable: teamScores has carried each side's runs all along.
        if sport == "mlb" and not _in_roster(mlb_parser.normalize_name(cleaned), mlb):
            abbr = mlb_team_in(cleaned, mlb.get("abbr_by_team_word") or {})
            if abbr:
                rec["team"], rec["is_team"] = abbr, True
                # Only runs: teamScores knows the score and nothing else, so
                # any other per-team stat stays whatever it was and grades
                # untracked rather than being answered from a number that
                # isn't the one asked about.
                orig_market = market
                if market == "runs":
                    rec["market"] = market = "team_total"
                key = mlb_parser.normalize_name(cleaned)
                # Indexed under the market parse_picks will hand back as WELL
                # as the converted one. Its leg still says "runs" -- the
                # conversion happens here -- so keying only on the new name
                # means the lookup never finds what this pass just worked out.
                found[(key, orig_market or "hr")] = rec
                found.setdefault((key, market or "hr"), rec)
                found.setdefault(mlb_parser.normalize_name(candidate), rec)
                continue

        if sport == "mlb" and market:
            # parse_picks resolves the player BEFORE it knows a market phrase
            # was glued to the name, so "Max Fried Strikeouts Over 5.5" misses
            # the roster entirely and comes back as typed with no team. Having
            # stripped the phrase here, resolve what's left.
            canon, team = mlb_parser.resolve_player(
                cleaned, mlb["team_by_name"], mlb["canonical_name_by_norm"])
            rec["name"], rec["team"] = canon, team
        if sport == "nfl" and market == "q_score":
            # A TEAM, not a player, and WHICH quarter. Several legs share the
            # one team name ("Lions" four times), so the record is indexed on
            # the leg's FULL text -- what parse_picks keeps as its player
            # string for a market it doesn't know -- which is unique per
            # quarter. Keyed on the name alone, all four Lions legs would get
            # the same quarter.
            qm = QUARTER_RE.search(market_src)
            abbr = nfl_team_in(cleaned)
            rec.update({"team": abbr or "", "is_team": True,
                        "quarter": QUARTER_NUM.get(qm.group(1).lower()) if qm else None})
            if not abbr or not rec["quarter"]:
                warnings.append(f"{line.strip()!r} needs one team and one quarter (1st-4th) to be graded")
            whole = f"{head} {rest}".strip() if bullet else candidate
            found[mlb_parser.normalize_name(whole)] = rec
            continue
        if sport == "nfl" and market == "quarters":
            # Two TEAMS, not a player. Both have to be graded -- "each team"
            # is the whole bet -- so taking whichever one resolved first
            # would answer half the question and call it an answer.
            abbrs = []
            for part in re.split(r"\s*(?:/|\+|,|\band\b|&)\s*", cleaned):
                ab = nfl_team_in(part)
                if ab and ab not in abbrs:
                    abbrs.append(ab)
            rec["teams"] = abbrs
            rec["team"] = abbrs[0] if abbrs else ""
            rec["is_team"] = True
            if len(abbrs) < 2:
                warnings.append(f"{cleaned!r} names {len(abbrs)} team(s), not two -- "
                                f"an each-team bet can't be graded from one")
            found[(mlb_parser.normalize_name(cleaned), market)] = rec
            found.setdefault(mlb_parser.normalize_name(candidate), rec)
            found.setdefault(mlb_parser.normalize_name(cleaned), rec)
            # parse_picks reads "Lions/Panthers ..." as a two-team line and
            # keeps only the FIRST name, so index each part on its own or the
            # leg it produces matches nothing this pass worked out.
            for part in re.split(r"\s*(?:/|\+|,|\band\b|&)\s*", cleaned):
                if part.strip():
                    found.setdefault(mlb_parser.normalize_name(part), rec)
            continue

        if sport == "nfl":
            # A leg can name SEVERAL players -- "Amon-Ra St. Brown, Chubba
            # Hubbard, Jahmry Gibbs Combined" is one bet on their total, the
            # same shape a combined baseball prop has. Split before resolving,
            # or the whole string is looked up as one impossible name.
            parts = [p for p in re.split(r"\s*(?:,|\+|&|\band\b)\s*", cleaned) if p.strip()]
            if len(parts) > 1:
                names, teams, ids = [], [], []
                for part in parts:
                    pn = nfl_parser.norm_key(part)
                    if pn not in nfl["team_by_name"]:
                        near = difflib.get_close_matches(pn, nfl["team_by_name"].keys(),
                                                         n=2, cutoff=0.82)
                        if len(near) == 1:
                            pn = near[0]
                    if pn in nfl["team_by_name"]:
                        names.append(nfl["canonical_name_by_norm"].get(pn, part))
                        teams.append(nfl["team_by_name"][pn])
                        ids.append(nfl.get("id_by_norm", {}).get(pn, ""))
                # ALL of them, or none. A combined bet on three players
                # graded off the two that happened to resolve is not a
                # partial answer, it is a WRONG one -- the line was set for
                # three. Falling through leaves it untracked, which is the
                # honest outcome.
                if len(names) != len(parts):
                    warnings.append(f"{cleaned!r} names {len(parts)} players but only "
                                    f"{len(names)} resolve -- left untracked rather than "
                                    f"graded off part of the bet")
                elif len(names) > 1:
                    rec["players"], rec["name"] = names, ", ".join(names)
                    rec["team"] = teams[0]
                    rec["athleteIds"] = ids
                    rec["is_player"] = True
                    found[(mlb_parser.normalize_name(cleaned), market or "hr")] = rec
                    found.setdefault(mlb_parser.normalize_name(candidate), rec)
                    found.setdefault(mlb_parser.normalize_name(cleaned), rec)
                    # ...and with only the QUANTITY removed. parse_picks
                    # strips "4+" as the line but keeps "Combined TDs" in the
                    # name, which is neither of the spellings above.
                    nq = QTY_RE.sub(" ", candidate)
                    if nq != candidate:
                        found.setdefault(mlb_parser.normalize_name(nq), rec)
                    continue

            norm = nfl_parser.norm_key(cleaned)
            # A typo in the name gets the same treatment baseball's already
            # had: difflib at resolve_player()'s 0.82 cutoff, and only when a
            # SINGLE roster name is that close -- two equally near means no
            # answer, not a pick. Without it "Barelon Allen" stayed unresolved
            # with a blank team, which on the page is a leg that can never
            # grade a hit OR a miss.
            if norm not in nfl["team_by_name"]:
                # When the card named his team, only that team's players are
                # candidates. "Dione Walker" is 0.92 from Deone Walker (BUF)
                # and 0.815 from Devontez Walker (BAL) -- a hair under the
                # cutoff today, and one roster rebuild from going ambiguous.
                # The "(Bills)" the card wrote settles it either way.
                pool = ([k for k, v in nfl["team_by_name"].items() if v == paren_team]
                        if paren_team else nfl["team_by_name"].keys())
                near = difflib.get_close_matches(norm, pool, n=2, cutoff=0.82)
                if len(near) == 1:
                    print(f"NOTE: read {cleaned!r} as "
                          f"{nfl['canonical_name_by_norm'].get(near[0], near[0])!r}.", file=sys.stderr)
                    norm = near[0]
            rec["name"] = nfl["canonical_name_by_norm"].get(norm, cleaned)
            rec["team"] = (nfl["team_by_name"].get(norm)
                           or nfl_team_in(rest) or nfl_team_in(head) or "")
            rec["athleteId"] = nfl.get("id_by_norm", {}).get(norm, "")
            rec["is_player"] = norm in nfl["team_by_name"]
            if norm not in nfl["team_by_name"]:
                warnings.append(f"{cleaned!r} is not on the NFL roster -- "
                                f"check the spelling, it can't be graded as typed")

        # Indexed under BOTH spellings, because which one comes back out of
        # parse_picks depends on whether ITS roster happened to resolve the
        # name -- and for an NFL player it usually will not.
        found[mlb_parser.normalize_name(candidate)] = rec
        found.setdefault(mlb_parser.normalize_name(rec["name"]), rec)
        # ...and under the name as the CARD misspelt it, before this pass
        # resolved it. parse_picks strips the market itself but has no fuzzy
        # match on this path, so its leg comes back as the raw typo --
        # "Fernado Tatis". Indexing only the corrected spelling meant the
        # correction never reached the leg that needed it.
        #
        # Keyed on the MARKET as well, because that name alone is not unique:
        # the same card had "Fernado Tatis 3+ H+R+RBI" and "Fernado Tatis 1+
        # Home Run", which collapse to one key and hand both legs whichever
        # record landed first. That is the Jose Ramirez failure one sport
        # over -- a leg graded against a bet nobody placed.
        found[(mlb_parser.normalize_name(cleaned), market or "hr")] = rec
        # And WITHOUT the "N+" quantity. parse_picks reads that as the market's
        # LINE and drops it from the name, so "Keon Coleman 15+ Receiving
        # Yards" comes back as "Keon Coleman Receiving Yards" -- a key this
        # pass never had. Every football prop on the first real card was
        # sported correctly here and then lost on that mismatch, surfacing as
        # UNKNOWN on the page.
        for variant in (QTY_RE.sub(" ", candidate), strip_when(candidate),
                        strip_when(QTY_RE.sub(" ", candidate)),
                        # The WHOLE line, price and all: a one-leg ticket
                        # becomes a SINGLE, and that path keeps the raw text
                        # as the player's name rather than stripping anything.
                        line.strip()):
            if variant != candidate:
                found.setdefault(mlb_parser.normalize_name(variant), rec)
        if not odds and tail is None:
            # On a priceless line the colon is the only thing separating the
            # subject from the bet, and parse_picks keeps the two JOINED when
            # it can't resolve the name -- "Tage Thompson Anytime Goal". Index
            # that spelling too or the post-pass finds nothing and every leg on
            # the card quietly stays baseball.
            found.setdefault(mlb_parser.normalize_name(f"{candidate} {market_src}"), rec)

    return found, warnings


def apply_sports(windows, singles, found):
    """Post-pass: stamp sport/market/team onto every leg parse_picks produced."""
    touched = 0

    def fix(leg):
        nonlocal touched
        norm_player = mlb_parser.normalize_name(leg.get("player") or "")
        rec = found.get(norm_player)
        if rec is None:
            # The name alone didn't match, so try it alongside the market
            # parse_picks read off the same line. That pair is what separates
            # two legs on the same player -- his H+R+RBI prop from his home
            # run -- and it is the only key a misspelt name can be found by,
            # since parse_picks keeps the typo as typed.
            rec = found.get((norm_player, leg.get("market") or "hr"))
        if rec is None:
            # Nothing in the pre-pass claimed it: parse_picks resolved it
            # against the MLB roster and that stands.
            leg["sport"] = "mlb"
            return
        touched += 1
        leg["sport"] = rec["sport"]
        # "2+ Touchdowns" is NOT an anytime-TD bet. The td market grades
        # binary -- did he reach the end zone at all -- so a leg needing two
        # would cash on one, silently, in the group's favour. There is no
        # counted-TD grader, so it is reported as its own market and shown
        # untracked. Naming it td and hoping is the one thing that must not
        # happen.
        # The line can come from either pass -- parse_picks reads "N+" as a
        # line of its own -- so check both before deciding this is anytime.
        eff_line = rec.get("line") if rec.get("line") is not None else leg.get("line")
        if rec["market"] == "td" and (eff_line or 0) > 0.5:
            rec = dict(rec, market="td_count")
        if rec["market"] and rec["market"] != "hr":
            leg["market"] = rec["market"]
        elif rec["sport"] == "nfl" and not rec["market"] and rec.get("is_player"):
            # An NFL leg naming a PLAYER with no market is an anytime
            # touchdown, the same way a market-less baseball leg is a home run.
            #
            # Only a player. "Browns: +2.5" names a TEAM and is a point
            # spread; defaulting it to "did the Browns score a touchdown"
            # answers a different question and answers it confidently. A team
            # leg nothing recognises keeps its unknown market and grades
            # untracked, which is the honest answer.
            leg["market"] = "td"
        if rec.get("line") is not None:
            leg["line"] = rec["line"]
        if rec.get("side") and rec["side"] != "over":
            leg["side"] = rec["side"]
        # Rewrite the name only when this pass actually knows better: an NFL
        # leg (which the MLB roster could never have resolved), or a leg whose
        # market phrase was glued to the player and left it unresolved. A
        # plain home run leg is left exactly as parse_picks wrote it, meta and
        # all -- that string is richer than anything rebuilt here.
        rewrite = rec["sport"] == "nfl" or (rec["market"] and not leg.get("team"))
        if not rewrite:
            return
        if rec.get("players"):
            leg["players"] = rec["players"]
        if rec.get("teams"):
            leg["teams"] = rec["teams"]
        if rec.get("quarter"):
            leg["quarter"] = rec["quarter"]
        if rec.get("athleteIds"):
            leg["athleteIds"] = rec["athleteIds"]
        leg["player"] = rec["name"]
        if rec.get("team"):
            leg["team"] = rec["team"]
        if rec.get("athleteId"):
            leg["athleteId"] = rec["athleteId"]
        # meta is a PREBUILT display string that still spells out whatever the
        # MLB roster guessed; rebuild it from what the leg actually is now.
        bits = [b for b in [rec.get("team"), leg.get("who")] if b]
        leg["meta"] = " &middot; ".join(bits)

    for win in windows:
        for tk in win["tickets"]:
            for leg in tk["legs"]:
                fix(leg)
    for s in singles:
        fix(s)
    return touched


def nfl_span(teams, time_strs, now, fetcher=None):
    """(first, last) day a PICKED NFL team plays, or (None, None).

    Deliberately NOT the NFL tab's rule. That one holds a slate until the
    WEEK's last game -- Monday night, even for a Sunday-only card -- because a
    football slate IS an NFL week. A combined card is not a week: it is one
    night's bets that happen to span two leagues, and the user's rule for it is
    "every game ON THE CARD". So this asks only when the PICKED teams play and
    ignores the rest of the week's schedule.
    """
    teams = {t for t in teams if t}
    if not teams:
        return None, None
    try:
        games = nfl_parser.upcoming_games(now, fetcher or nfl_parser.fetch_json)
    except Exception as e:          # network, JSON, anything: a card must still parse
        print(f"NOTE: couldn't read the NFL schedule ({e}); dating the NFL legs "
              f"from the listed times instead.", file=sys.stderr)
        games = []

    next_game = {}
    for day, abbrs, final in games:          # already chronological
        if final:
            continue
        for team in abbrs & teams:
            next_game.setdefault(team, day)
    if not next_game:
        day = nfl_parser.time_heuristic(time_strs, now)
        return day, day
    days = sorted(next_game.values())
    return days[0], days[-1]


def leg_times(legs):
    return [l.get("time") or "" for l in legs if l.get("time")]


def slate_span(legs, now, fetcher=None):
    """(date, end_date) for a card holding either sport, or both."""
    mlb_legs = [l for l in legs if l.get("sport") != "nfl"]
    nfl_legs = [l for l in legs if l.get("sport") == "nfl"]

    days = []
    if mlb_legs:
        days.append(mlb_parser.slate_date_for(leg_times(mlb_legs), now))
    if nfl_legs:
        first, last = nfl_span({l.get("team") for l in nfl_legs},
                               leg_times(nfl_legs), now, fetcher)
        days += [d for d in (first, last) if d]
    if not days:
        days = [now.date().isoformat()]
    return min(days), max(days)


def archive_previous_slate(new_date, out_path, prev_path):
    """Move the committed slate aside, but only when the new one is a
    DIFFERENT day -- a same-day re-upload is a correction, not a new slate,
    and archiving it would throw away the real previous card."""
    if not out_path.exists():
        return False
    try:
        old = json.loads(out_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if not old.get("date") or old["date"] == new_date:
        return False
    prev_path.parent.mkdir(parents=True, exist_ok=True)
    prev_path.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
    print(f"archived the {old['date']} slate to {prev_path}", file=sys.stderr)
    return True


def build(text, mlb, nfl, now, fetcher=None):
    """The whole pipeline, as a pure-ish function so the tests can drive it."""
    found, warnings = scan_card(text, mlb, nfl)
    windows, singles, _raw = mlb_parser.parse(
        text, mlb["team_by_name"], mlb["canonical_name_by_norm"])
    if not windows and not singles:
        raise SystemExit("WARNING: parsed nothing -- this is a NEW TEMPLATE, "
                         "not a regression. Get the raw card and add a fixture.")

    apply_sports(windows, singles, found)
    legs = [l for w in windows for t in w["tickets"] for l in t["legs"]] + list(singles)
    by_sport = {"mlb": 0, "nfl": 0}
    for leg in legs:
        by_sport[leg.get("sport", "mlb")] += 1

    date, end_date = slate_span(legs, now, fetcher)

    # A bet line nothing understood is never dropped silently -- same contract
    # as the other two parsers. Ambiguous-sport legs ride the same channel.
    unread = list(getattr(mlb_parser.parse, "unread", []) or [])
    bits = []
    if unread:
        bits.append(f"{len(unread)} bet line(s) could not be read: " + "; ".join(unread[:3]))
    bits += warnings[:3]
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    out = {
        "date": date,
        "endDate": end_date,
        "note": " · ".join(bits),
        "sports": [k for k in ("mlb", "nfl") if by_sport[k]],
        "windows": windows,
        "singles": singles,
    }
    print(f"{sum(by_sport.values())} legs ({by_sport['mlb']} MLB, {by_sport['nfl']} NFL) "
          f"in {sum(len(w['tickets']) for w in windows)} ticket(s) and "
          f"{len(singles)} single(s); slate {date}"
          + (f"..{end_date}" if end_date != date else ""), file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Parse a combined MLB+NFL picks card.")
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--prev", default=str(PREV_PATH))
    ap.add_argument("--no-network", action="store_true",
                    help="don't ask ESPN for the NFL schedule (offline tests)")
    args = ap.parse_args()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))

    mlb, nfl = load_rosters()
    text = Path(args.file).read_text(encoding="utf-8")
    fetcher = (lambda _url: (_ for _ in ()).throw(RuntimeError("--no-network"))) \
        if args.no_network else None
    out = build(text, mlb, nfl, now, fetcher)

    out_path, prev_path = Path(args.out), Path(args.prev)
    archive_previous_slate(out["date"], out_path, prev_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
