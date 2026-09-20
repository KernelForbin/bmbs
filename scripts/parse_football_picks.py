#!/usr/bin/env python3
"""
Parses a raw football (anytime-touchdown) picks card into
data/football/tickets.json, in the schema football/index.html expects --
the baseball tickets.json schema plus, per leg/single, an `athleteId`, and
per slate an optional `endDate`.

Deliberately a standalone sibling of parse_picks.py rather than an import
of it: the two sports' cards will drift (they already differ -- see odds
below), and a football fix must never be able to break baseball parsing.

Three of the group's card templates are accepted -- its own set, not
necessarily the same shapes baseball's parser handles at any given time:

    ## 🎯 Longshot Straight Bets
    * Kenny: Jahmyr Gibbs (-120) | (DET @ BUF) 8:15 PM ET • $5.00 bet | PP: $9.17

    ## ⚡ 3-Leg Parlay Cards
    Card 1: Early Window
    * Saquon Barkley (+105) | 1:00 PM ET (Kenny)
    Bet by Memo: $3.00 | PP: $53.52

and

    ⚾/🏈 1-LINE PARLAYS (STRAIGHT BETS)
    * Ticket 1: 1:00 PM ET | Saquon Barkley (Eagles) -110 (Memo)
                (Bet by Memo) [Bet: $5 | PP: 9.55]

and a third, first seen 2026-09-20 (a real upload that parsed to zero tickets
under the first two -- exit 1, nothing written, that day's slate never
posted):

    ### Touchdown Parlays Summary

    Ticket 1 (5-Leg Parlay)
    Bettor: Memo | Bet: $8.50 | Potential Payout: $95.66
    - (Kenny) Derrick Henry (-260) 1:00 PM
    - (Bernie) Christian McCaffrey (-250) 4:25 PM

No team is given per leg in this one (resolved from the roster instead), the
"M-Leg Parlay" count in the header is never trusted (same rule as the other
two: actual leg count decides single vs. parlay), and the time can be free
text ("Check Listings") instead of a real clock time. If a new upload parses
to zero again, that's a fourth template -- check which sport failed, since
baseball and football's accepted shapes are independent and have already
diverged once.

What's different from baseball, on purpose:
  * ODDS CAN BE NEGATIVE. A home run is always plus money; a star back to
    score is often -120. Every odds pattern here takes [+-].
  * athleteId. Names resolve against data/football/roster.json (built by
    build_football_roster.py), and the ESPN athlete id is stored on the leg
    so the live page matches by id, not by spelling.
  * A SLATE IS AN NFL WEEK, dated from the schedule, not from the clock.
    The site shows "This Week's Picks" / "Last Week's Picks", and a card stays
    on This Week until the week's LAST game (Monday night) is final -- even a
    Sunday-only card. So: `date` is the earliest upcoming game among the
    picked teams, `endDate` is the last day of that NFL week with any game on
    it, and `weekEnds` (the week's Tuesday) names the week. A second card in
    the SAME week (Thursday's, then Sunday's) replaces the first without
    touching Last Week; only a card for a new week archives the old one.
    If ESPN can't be reached it falls back to the card's own kickoff times
    and the following Monday.

Run:
    python scripts/parse_football_picks.py --file data/football/incoming_picks.txt

This OVERWRITES data/football/tickets.json (archiving the previous slate to
tickets-previous.json when the date changed).
"""
import difflib
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "football"
TICKETS_PATH = DATA / "tickets.json"
PREVIOUS_PATH = DATA / "tickets-previous.json"
ROSTER_PATH = DATA / "roster.json"
SCOREBOARD = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={ymd}"
LOOKAHEAD_DAYS = 7   # how far ahead a card can be posted

try:
    ET = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    sys.exit("No timezone database found -- run: pip install tzdata")

ODDS = r"([+-]\d+)"
SINGLES_HEADER_RE = re.compile(r"longshot|straight bet", re.IGNORECASE)
PARLAY_HEADER_RE = re.compile(
    r"\d+-Leg Parlay|(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)-LEG|\d+-MAN", re.IGNORECASE)
CARD_HEADER_RE = re.compile(r"^Card\s+(\d+)\s*:\s*(.+)$")
BET_FOOT_RE = re.compile(r"Bet by\s+([A-Za-z]+)\s*:\s*\$([\d,.]+)\s*\|\s*PP:\s*\$([\d,.]+)", re.IGNORECASE)
BULLET = r"^(?:[*\-•]\s*)?"
SINGLE_LINE_RE = re.compile(
    BULLET + r"([A-Za-z]+)\s*:\s*(.+?)\s*\(" + ODDS + r"\)\s*\|\s*"
    r"(?:\(([^)]+)\)\s*)?((?:[A-Za-z]{3,9}\s+)?[\d: ]*[AP]M ET)?\s*[•·]?\s*\$([\d,.]+)\s*bet\s*\|\s*PP:\s*\$([\d,.]+)",
    re.IGNORECASE)
LEG_LINE_RE = re.compile(BULLET + r"(.+?)\s*\(" + ODDS + r"\)\s*\|\s*(.+?)\s*\(([^)]+)\)\s*$")
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M\s*ET", re.IGNORECASE)
ODDS_RE = re.compile(r"\([+-]\d+\)")
TICKET_START_RE = re.compile(r"^\*?\s*Ticket\s+(\d+)\s*:\s*(.+)$", re.IGNORECASE)
# a leading day name ("Sun 1:00 PM ET") is tolerated and dropped
TICKET_LEG_RE = re.compile(
    r"^(?:[A-Za-z]{3,9}\.?,?\s+)?([\d: ]*[AP]M\s*ET)\s*\|\s*(.+?)\s*\(([^)]+)\)\s*" + ODDS + r"\s*\(([^)]+)\)\s*$", re.IGNORECASE)
TICKET_FOOT_RE = re.compile(
    r"^\(Bet by\s+([A-Za-z]+)\)\s*\[Bet:\s*\$([\d,.]+)\s*\|\s*PP:\s*\$?([\d,.]+)\]\s*$", re.IGNORECASE)

# ---- third template, first seen 2026-09-20 (a Discord upload that parsed to
# zero tickets, exit 1, nothing written -- the real slate never posted): a bare
# "Ticket N (M-Leg Parlay)" header (no colon, unlike TICKET_START_RE), stake/
# payout on their OWN line right after it instead of a header or footer, and
# "- (Bettor) Player (ODDS) Time" legs -- no team, no "ET" suffix, and the time
# can be free text ("Check Listings") instead of a real time:
#     Ticket 1 (5-Leg Parlay)
#     Bettor: Memo | Bet: $8.50 | Potential Payout: $95.66
#     - (Kenny) Derrick Henry (-260) 1:00 PM
# The M-Leg count in the header is never trusted, same rule as every other
# template -- actual leg count decides single vs. parlay.
SUMMARY_TICKET_START_RE = re.compile(r"^Ticket\s+(\d+)\s*\(\s*\d+[- ]Leg\s*Parlay\s*\)\s*$", re.IGNORECASE)
SUMMARY_BOOK_RE = re.compile(
    r"^Bettor:\s*([A-Za-z]+)\s*\|\s*Bet:\s*\$([\d,.]+)\s*\|\s*(?:Potential\s+)?Payout:\s*\$([\d,.]+)\s*$", re.IGNORECASE)
SUMMARY_LEG_RE = re.compile(BULLET + r"\(([^)]+)\)\s*(.+?)\s*\(" + ODDS + r"\)\s*(.+)$", re.IGNORECASE)
SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def norm_key(name):
    """Same normalization as build_football_roster.py and football/index.html."""
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    name = " ".join(name.split()).strip().lower()
    return SUFFIX_RE.sub("", name)


def clean_num(s):
    return float(s.replace(",", ""))


def load_roster(path=ROSTER_PATH):
    if not path.exists():
        return {"team_by_name": {}, "canonical_name_by_norm": {}, "id_by_norm": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: data.get(k, {}) for k in ("team_by_name", "canonical_name_by_norm", "id_by_norm")}


def resolve_player(raw_name, roster):
    """(canonical_name, team, athlete_id). Exact key first, then a fuzzy
    match; failing both, the name as typed with no team/id -- never a guess
    at some other player."""
    key = norm_key(raw_name)
    teams = roster["team_by_name"]
    if key not in teams:
        close = difflib.get_close_matches(key, teams.keys(), n=1, cutoff=0.82)
        key = close[0] if close else None
    if key is None:
        print(f"NOTE: '{raw_name}' not found in the NFL roster (even fuzzy). "
              f"Keeping as typed, no team/id -- check spelling.", file=sys.stderr)
        return raw_name.strip(), "", ""
    return roster["canonical_name_by_norm"][key], teams[key], roster["id_by_norm"].get(key, "")


def section_header(line):
    text = line.lstrip("#").strip()
    # "Ticket 1 (5-Leg Parlay)" contains the literal substring "5-Leg Parlay",
    # which PARLAY_HEADER_RE below would otherwise match -- misreading every
    # ticket header in the third template as a brand new section and starting
    # a fresh (empty) window instead of ever reaching SUMMARY_TICKET_START_RE.
    if CARD_HEADER_RE.match(text) or ODDS_RE.search(text) or BET_FOOT_RE.search(text) or SUMMARY_TICKET_START_RE.match(text):
        return None
    if SINGLES_HEADER_RE.search(text) or PARLAY_HEADER_RE.search(text):
        return text
    return text if line.startswith("##") else None


# ---------------- slate dating ----------------

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-football-parse"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def upcoming_games(now, fetcher=fetch_json):
    """[(iso_date, {team abbreviations}, is_final)] for today .. today+LOOKAHEAD."""
    out = []
    for offset in range(LOOKAHEAD_DAYS + 1):
        day = now.date() + timedelta(days=offset)
        data = fetcher(SCOREBOARD.format(ymd=day.strftime("%Y%m%d")))
        for event in data.get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            abbrs = {c.get("team", {}).get("abbreviation") for c in comp.get("competitors", [])}
            state = ((event.get("status") or {}).get("type") or {}).get("state")
            out.append((day.isoformat(), abbrs, state == "post"))
    return out


def time_heuristic(time_strs, now):
    """Baseball's rule, used only when the schedule can't be consulted: a card
    whose every kickoff has already passed today is for tomorrow."""
    starts = []
    for t in time_strs:
        m = re.search(r"(\d{1,2}):(\d{2})\s*([AP])M", t.strip(), re.IGNORECASE)
        if m:
            starts.append((int(m.group(1)) % 12 + (12 if m.group(3).upper() == "P" else 0)) * 60 + int(m.group(2)))
    if starts and now.hour * 60 + now.minute > max(starts):
        return (now.date() + timedelta(days=1)).isoformat()
    return now.date().isoformat()


def week_end(day_iso):
    """The Tuesday on or after `day_iso`: an NFL week runs Wednesday..Tuesday."""
    day = datetime.fromisoformat(day_iso).date()
    return (day + timedelta(days=(1 - day.weekday()) % 7)).isoformat()


def slate_dates_for(teams, time_strs, now, fetcher=fetch_json):
    """(date, end_date): first game among the picked teams .. last game of that NFL week."""
    teams = {t for t in teams if t}
    try:
        games = upcoming_games(now, fetcher) if teams else []
    except Exception as e:  # network, JSON, anything -- a card must still parse
        print(f"NOTE: couldn't read the NFL schedule ({e}); dating the slate from the listed times instead.", file=sys.stderr)
        games = []

    next_game = {}
    for day, abbrs, final in games:   # already chronological
        if final:
            continue
        for team in abbrs & teams:
            next_game.setdefault(team, day)
    if not next_game:
        # No schedule to go by: the week still ends Monday night as far as we
        # know (the day before week_end's Tuesday), never before the card's day.
        day = time_heuristic(time_strs, now)
        monday = (datetime.fromisoformat(week_end(day)).date() - timedelta(days=1)).isoformat()
        return day, max(day, monday)

    # One slate never crosses into the next NFL week. A week runs Wednesday
    # to Tuesday (Thu / Sun / Mon games, the odd Tuesday), so the limit is the
    # Tuesday on or after the first game -- NOT a flat number of days: Sunday
    # + 4 reaches next Thursday, which is how a card holding one player whose
    # team had already played got stretched across two weeks. Anything beyond
    # the limit (that case, or a mis-resolved name) is ignored for dating.
    days = sorted(next_game.values())
    first = days[0]
    limit = week_end(first)
    for team, day in sorted(next_game.items()):
        if day > limit:
            print(f"NOTE: {team}'s next game is {day}, outside this week ({first}..{limit}) -- ignored for dating.", file=sys.stderr)
    # The slate holds until the WEEK's last game, whoever is playing in it: a
    # Sunday-only card still belongs to This Week until Monday night ends.
    last_game_day = max(d for d, _abbrs, _final in games if first <= d <= limit)
    return first, last_game_day


def archive_previous_slate(new_week):
    """Archive the current card only when the new one is for a different NFL
    week. Within a week (Thursday's card, then Sunday's; or a correction) the
    new card simply replaces it and Last Week's Picks stays put."""
    if not TICKETS_PATH.exists():
        return
    try:
        old = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    old_date = old.get("date")
    if not old_date or (old.get("weekEnds") or week_end(old_date)) == new_week:
        return
    PREVIOUS_PATH.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Archived {old_date} slate -> {PREVIOUS_PATH}")


# ---------------- the card ----------------

def parse(text, roster):
    lines = [l.rstrip() for l in text.splitlines()]
    windows, singles = [], []
    mode = None
    current_section = None
    current_card = None
    last_header_title = None
    ticket_windows = {}
    current_ticket = None

    def flush_card():
        nonlocal current_card
        if current_card and current_card["_legs"]:
            current_section["tickets"].append(current_card)
        current_card = None

    def ticket_window(title):
        w = ticket_windows.get(title)
        if w is None:
            w = {"title": title or "Parlay Cards", "tickets": []}
            ticket_windows[title] = w
            windows.append(w)
        return w

    def make_leg(player_raw, odds, who, time_, typed_team=""):
        name, team, athlete_id = resolve_player(player_raw, roster)
        # "(Noid — Listed as ...)" asides describe the player, not the bettor
        who = re.split(r"[—-]", who, maxsplit=1)[0].strip()
        return {"player": name, "team": team or typed_team.strip(), "athleteId": athlete_id,
                "odds": odds, "who": who, "time": (time_ or "").strip()}

    def finalize_ticket(ticket):
        legs_raw = ticket["_legs"]
        if not legs_raw or ticket["_stake"] is None:
            return   # truncated -- drop, don't guess
        if len(legs_raw) == 1:
            time_, player_raw, team_raw, odds, who = legs_raw[0]
            leg = make_leg(player_raw, odds, who, time_)
            singles.append({"who": leg["who"].upper(), "player": leg["player"], "team": leg["team"],
                            "athleteId": leg["athleteId"], "odds": odds, "matchup": "", "time": leg["time"],
                            "stake": ticket["_stake"], "pp": ticket["_pp"]})
            return
        legs = [make_leg(p, o, w, t) for t, p, _team, o, w in legs_raw]
        ticket_window(last_header_title)["tickets"].append({
            "name": f"Card {ticket['_num']}", "sub": "", "tag": None, "_stake": ticket["_stake"],
            "_book": ticket["_book"], "_origPayout": ticket["_pp"], "_legs": legs})

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("---"):
            continue

        header_text = section_header(line)
        if header_text is not None:
            flush_card()
            last_header_title = header_text
            if SINGLES_HEADER_RE.search(header_text):
                mode, current_section = "singles", None
            elif PARLAY_HEADER_RE.search(header_text):
                mode = "parlay"
                current_section = {"title": header_text, "tickets": []}
                windows.append(current_section)
            else:
                mode = None
            continue

        start = TICKET_START_RE.match(line)
        summary_start = None if start else SUMMARY_TICKET_START_RE.match(line)
        if start or summary_start:
            if current_ticket is not None:
                finalize_ticket(current_ticket)
            if start:
                current_ticket = {"_num": start.group(1), "_legs": [], "_book": None, "_stake": None, "_pp": None}
                leg = TICKET_LEG_RE.match(start.group(2).strip())
                if leg:
                    current_ticket["_legs"].append(leg.groups())
            else:
                # stake/payout/book sit on their OWN line for this template
                # (SUMMARY_BOOK_RE below), never inline with the header.
                current_ticket = {"_num": summary_start.group(1), "_legs": [], "_book": None, "_stake": None, "_pp": None}
            continue
        if current_ticket is not None:
            foot = TICKET_FOOT_RE.match(line)
            if foot:
                current_ticket["_book"] = foot.group(1)
                current_ticket["_stake"] = clean_num(foot.group(2))
                current_ticket["_pp"] = clean_num(foot.group(3))
                finalize_ticket(current_ticket)
                current_ticket = None
                continue
            book = SUMMARY_BOOK_RE.match(line)
            if book:
                current_ticket["_book"] = book.group(1)
                current_ticket["_stake"] = clean_num(book.group(2))
                current_ticket["_pp"] = clean_num(book.group(3))
                continue
            leg = TICKET_LEG_RE.match(line)
            if leg:
                current_ticket["_legs"].append(leg.groups())
                continue
            summary_leg = SUMMARY_LEG_RE.match(line)
            if summary_leg:
                who, player_raw, odds, time_ = summary_leg.groups()
                # same 5-tuple shape finalize_ticket already expects from
                # TICKET_LEG_RE: (time_, player_raw, team_raw, odds, who) --
                # this template never gives a team, so that slot is blank and
                # make_leg() falls through to the roster lookup for it.
                current_ticket["_legs"].append((time_.strip(), player_raw.strip(), "", odds, who.strip()))
                continue

        if mode == "parlay":
            card = CARD_HEADER_RE.match(line)
            if card:
                flush_card()
                num, rest = card.groups()
                tag, sub = None, rest
                tag_match = re.search(r"\(([^)]+)\)\s*$", rest)
                if tag_match and ("both" in tag_match.group(1).lower() or "@" in tag_match.group(1)):
                    tag, sub = tag_match.group(1), rest[:tag_match.start()].strip()
                current_card = {"name": f"Card {num}", "sub": sub, "tag": tag,
                                "_stake": None, "_book": None, "_origPayout": None, "_legs": []}
                continue
            bet = BET_FOOT_RE.search(line)
            if bet and current_card:
                current_card["_book"] = bet.group(1)
                current_card["_stake"] = clean_num(bet.group(2))
                current_card["_origPayout"] = clean_num(bet.group(3))
                continue
            leg = LEG_LINE_RE.match(line)
            if leg and current_card:
                player_raw, odds, middle, who = leg.groups()
                t = TIME_RE.search(middle)
                current_card["_legs"].append(make_leg(player_raw, odds, who, t.group(0) if t else "", "" if t else middle))
                continue
        elif mode == "singles":
            m = SINGLE_LINE_RE.match(line)
            if m:
                who, player_raw, odds, matchup, time_, stake, pp = m.groups()
                leg = make_leg(player_raw, odds, who, time_)
                singles.append({"who": who.strip().upper(), "player": leg["player"], "team": leg["team"],
                                "athleteId": leg["athleteId"], "odds": odds, "matchup": (matchup or "").strip(),
                                "time": leg["time"], "stake": clean_num(stake), "pp": clean_num(pp)})
                continue

    flush_card()
    if current_ticket is not None:
        finalize_ticket(current_ticket)
    windows = [w for w in windows if w["tickets"]]

    out_windows = []
    for si, section in enumerate(windows):
        out_tickets = []
        for ci, card in enumerate(section["tickets"]):
            legs = [{"id": f"p{si}-c{ci}-l{li}", "player": leg["player"], "team": leg["team"], "athleteId": leg["athleteId"],
                     "who": leg["who"], "meta": f"{leg['team']} &middot; {leg['who']}" if leg["team"] else leg["who"],
                     "odds": leg["odds"], "time": leg["time"]} for li, leg in enumerate(card["_legs"])]
            if card["_stake"] is None:
                print(f"NOTE: {card['name']} has no 'Bet by' line -- skipped.", file=sys.stderr)
                continue
            sub_html = f' &middot; {card["sub"]}' if card.get("sub") else ""
            tag_html = f' &middot; {card["tag"]}' if card.get("tag") else ""
            out_tickets.append({
                "name": f'{card["name"]}{sub_html}{tag_html}', "sub": f"{len(legs)}-Leg",
                "foot": f'<b>${card["_stake"]:.2f}</b> bet by {card["_book"]} &middot; Potential payout <b>${card["_origPayout"]:,.2f}</b>',
                "stake": card["_stake"], "book": card["_book"], "payout": card["_origPayout"], "legs": legs})
        if out_tickets:
            out_windows.append({"title": section["title"], "tickets": out_tickets})

    out_singles = []
    for i, s in enumerate(singles):
        meta = [p for p in [s["matchup"], s["time"], f'${s["stake"]:.2f} bet'] if p]
        out_singles.append({"id": f"single-{i}", "who": s["who"], "player": s["player"], "team": s["team"],
                            "athleteId": s["athleteId"], "meta": " &middot; ".join(meta), "odds": s["odds"],
                            "stake": s["stake"], "payout": s["pp"], "pp": f'PP ${s["pp"]:,.2f}'})
    return out_windows, out_singles


def main():
    if "--file" in sys.argv:
        text = Path(sys.argv[sys.argv.index("--file") + 1]).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    windows, singles = parse(text, load_roster())
    legs = [leg for w in windows for c in w["tickets"] for leg in c["legs"]]
    print(f"Parsed {len(singles)} singles, {sum(len(w['tickets']) for w in windows)} parlay cards "
          f"({len(legs)} legs) across {len(windows)} sections.")
    if not legs and not singles:
        print("WARNING: parsed nothing. Check the input format.", file=sys.stderr)
        sys.exit(1)

    picks = legs + singles
    date, end_date = slate_dates_for([p["team"] for p in picks], [p.get("time", "") for p in legs] + [s.get("meta", "") for s in singles], datetime.now(ET))
    payload = {"sport": "football", "date": date, "endDate": end_date, "weekEnds": week_end(date),
               "note": "", "windows": windows, "singles": singles}

    DATA.mkdir(parents=True, exist_ok=True)
    archive_previous_slate(payload["weekEnds"])
    TICKETS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    span = date if end_date == date else f"{date} .. {end_date}"
    print(f"Wrote {TICKETS_PATH} (slate: {span} ET)")


if __name__ == "__main__":
    main()
