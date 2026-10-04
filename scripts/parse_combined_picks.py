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
NFL_MARKET_ALIASES = [
    ("td",             r"anytime\s*(?:td|touchdown)|to\s+score\s+a?\s*(?:td|touchdown)"
                       r"|\btouchdowns?\b|\btds?\b"),
    ("rec_yds",        r"receiving\s+yards?|\brec\s+yds?\b"),
    ("rush_yds",       r"rushing\s+yards?|\brush\s+yds?\b"),
    ("pass_yds",       r"passing\s+yards?|\bpass\s+yds?\b"),
    ("receptions",     r"receptions?\b"),
    ("pass_tds",       r"passing\s+(?:td|touchdown)s?"),
]
NFL_MARKET_RE = [(k, re.compile(p, re.IGNORECASE)) for k, p in NFL_MARKET_ALIASES]

# Markets that only exist in one sport, used to settle the sport question
# before anything else is consulted.
MLB_ONLY_MARKETS = {"hr", "sb", "hrr", "hits", "rbi", "runs", "tb",
                    "doubles", "k", "er", "win", "f5"}
NFL_ONLY_MARKETS = {m for m, _ in NFL_MARKET_ALIASES}

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
        bullet = BULLET_LEG_RE.match(line) if not odds else None
        if not odds and not bullet:
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
        market_src = candidate if odds else rest
        market, cleaned = detect_nfl_market(market_src)
        if market is None:
            mkey, mline, mside, mcleaned = mlb_parser.detect_market(market_src)
            market, cleaned = mkey, mcleaned
            line_val, side_val = mline, mside
        else:
            # An NFL prop can still carry its own over/under ("Receiving Yards
            # Over 62.5"), and baseball's detector is the thing that reads one.
            _k, line_val, side_val, cleaned = mlb_parser.detect_market(cleaned)
        if not odds:
            cleaned = candidate      # the subject was never the market

        sport, why = decide_sport(cleaned, market, rest, head, mlb, nfl, section_hint)
        if sport is None:
            warnings.append(f"{candidate!r} could be MLB or NFL ({why})")
            continue

        rec = {"sport": sport, "market": market, "line": line_val,
               "side": side_val, "name": cleaned, "why": why}
        if sport == "mlb" and market:
            # parse_picks resolves the player BEFORE it knows a market phrase
            # was glued to the name, so "Max Fried Strikeouts Over 5.5" misses
            # the roster entirely and comes back as typed with no team. Having
            # stripped the phrase here, resolve what's left.
            canon, team = mlb_parser.resolve_player(
                cleaned, mlb["team_by_name"], mlb["canonical_name_by_norm"])
            rec["name"], rec["team"] = canon, team
        if sport == "nfl":
            norm = nfl_parser.norm_key(cleaned)
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
        if not odds:
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
        rec = found.get(mlb_parser.normalize_name(leg.get("player") or ""))
        if rec is None:
            # Nothing in the pre-pass claimed it: parse_picks resolved it
            # against the MLB roster and that stands.
            leg["sport"] = "mlb"
            return
        touched += 1
        leg["sport"] = rec["sport"]
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
