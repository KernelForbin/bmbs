#!/usr/bin/env python3
"""
Parses raw pasted picks text (the "Longshot / N-Leg Parlay Cards" format)
into data/tickets.json, in the exact schema index.html expects:

    {
      "note": "...",
      "windows": [
        {
          "title": "...",
          "tickets": [
            {
              "name": "Card 1", "sub": "Early Lunch Slate", "sub2": "3-Leg",
              "foot": "<b>$3</b> bet by Memo &middot; Potential payout <b>$533.52</b>",
              "legs": [
                {"id": "p0-c0-l0", "player": "Jo Adell", "team": "LAD",
                 "meta": "Kenny", "odds": "+300", "time": "1:10 PM ET"}
              ]
            }
          ]
        }
      ],
      "singles": [
        {"id": "single-0", "who": "KENNY", "player": "Jake McCarthy",
         "team": "COL", "meta": "SD @ COL &middot; 3:10 PM ET &middot; $5.00 bet",
         "odds": "+870", "pp": "PP $58.95"}
      ]
    }

Player names are normalized against data/roster.json (built from your
uploaded MLB roster CSV): exact normalized match first, then a fuzzy
closest-match fallback. The canonical CSV spelling and team replace
whatever was in the pasted text, so downstream matching against the MLB
Stats API stays reliable.

Expected raw input shape — see test_picks.txt in this repo for a full
real example. The markdown markers ("## " on section headers, "* " on
item lines) are optional: text copied out of a rendered Gemini/ChatGPT
response has them stripped, and both forms parse identically.

    ## 🎯 Longshot (LS) Straight Bets (Single Legs)
    * Kenny: Jake McCarthy (+870) | (SD @ COL) 3:10 PM ET • $5.00 bet | PP: $58.95

    ------------------------------
    ## ⚡ 3-Leg Parlay Cards
    Card 1: Early Lunch Slate

    * Jo Adell (+300) | 1:10 PM ET (Kenny)
    * George Springer (+470) | 3:07 PM ET (Francher)
    * Alec Burleson (+430) | 1:15 PM ET (Bernie)
    Bet by Memo: $3.00 | PP: $533.52

Two other templates the group has posted, both fully supported (see
TICKET_START_RE / TICKET_HASH_START_RE below for the exact grammar):

    * Ticket 1: 7:40 PM ET | Chase Meidroth (White Sox) +1040 (Memo)
                (Bet by Memo) [Bet: $5 | PP: 54.00]

    Ticket #1 (Memo - $9 Bet) [PP: $151.99]
    * (Chantra) Riley Greene - DET (+314) - 2:10 PM ET
    * (Kenny) Munetaka Murakami - CWS (+438) - 2:10 PM ET

A ticket is a single or a parlay card purely by how many leg lines it
actually has -- never by which section/"Part N:" header it sits under, or
what a header's own leg-count claim says (seen for real: a "10 TWO-LEG
PARLAYS" header with only 9 tickets under it). A window that ends up with
no cards (every one of its tickets turned out to be a single) is dropped.

The output also carries "date": the MLB game date (YYYY-MM-DD, ET) the
slate is for -- see slate_date_for(). When that date differs from the
one already in data/tickets.json, the old file is first copied to
data/tickets-previous.json so the site can keep showing yesterday's
results.

Run:
    python scripts/parse_picks.py --file picks.txt
    # or piped:
    cat picks.txt | python scripts/parse_picks.py

This OVERWRITES data/tickets.json.
"""
import difflib
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "data" / "tickets.json"
PREVIOUS_PATH = ROOT / "data" / "tickets-previous.json"
ROSTER_PATH = ROOT / "data" / "roster.json"

try:
    ET = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    # Windows Python has no system tz database; Linux (incl. GitHub Actions) does.
    sys.exit("No timezone database found -- run: pip install tzdata")

SINGLES_HEADER_RE = re.compile(r"longshot|straight bet", re.IGNORECASE)
PARLAY_HEADER_RE = re.compile(
    r"\d+-Leg Parlay|(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)-LEG|\d+-MAN",
    re.IGNORECASE
)
CARD_HEADER_RE = re.compile(r"^Card\s+(\d+)\s*:\s*(.+)$")
BET_FOOT_RE = re.compile(
    r"Bet by\s+([A-Za-z]+)\s*:\s*\$([\d,.]+)\s*\|\s*PP:\s*\$([\d,.]+)", re.IGNORECASE
)
BULLET = r"^(?:[*\-•]\s*)?"
SINGLE_LINE_RE = re.compile(
    BULLET + r"([A-Za-z]+)\s*:\s*(.+?)\s*\(([+-]\d+)\)\s*\|\s*"
    r"(?:\(([^)]+)\)\s*)?([\d: ]*[AP]M ET)?\s*[•·]?\s*\$([\d,.]+)\s*bet\s*\|\s*PP:\s*\$([\d,.]+)",
    re.IGNORECASE
)
LEG_LINE_RE = re.compile(BULLET + r"(.+?)\s*\(([+-]\d+)\)\s*\|\s*(.+?)\s*\(([^)]+)\)\s*$")
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M\s*ET", re.IGNORECASE)
ODDS_RE = re.compile(r"\([+-]\d+\)")

# ---- second raw-text template: "Ticket N: TIME | Player (Team) +ODDS (Bettor)",
# one leg per line, closed by a "(Bet by X) [Bet: $Y | PP: Z]" footer line. Seen
# from the group for the first time 2026-09-18 (a Discord upload the old
# patterns above silently failed to parse at all -- exit code 1, nothing
# written). Whether a ticket is a single or a parlay card is decided purely by
# how many leg lines it actually has, never by which section header it sits
# under -- the header's own leg-count claim doesn't always match the tickets
# under it (seen for real: a "10 TWO-LEG PARLAYS" header with only 9 under it).
TICKET_START_RE = re.compile(r"^\*?\s*Ticket\s+\d+\s*:\s*(.+)$", re.IGNORECASE)
TICKET_LEG_RE = re.compile(
    r"^([\d: ]*[AP]M\s*ET)\s*\|\s*(.+?)\s*\(([^)]+)\)\s*([+-]\d+)\s*\(([^)]+)\)\s*$", re.IGNORECASE
)
TICKET_FOOT_RE = re.compile(
    r"^\(Bet by\s+([A-Za-z]+)\)\s*\[Bet:\s*\$([\d,.]+)\s*\|\s*PP:\s*\$?([\d,.]+)\]\s*$", re.IGNORECASE
)

# ---- third raw-text template: "Ticket #N (Bettor - $X Bet) [PP: $Y]" header
# (stake and payout live in the header itself, no separate footer line),
# legs as "* (Bettor) Player - TEAM (+ODDS) - TIME ET", grouped under
# "Part N: <window description>" headers. First seen 2026-09-19 (a real
# Discord upload the first two templates parsed as zero tickets). Reuses the
# second template's ticket/window machinery below -- same internal shape,
# just populated by different regexes -- so a single leg still becomes a
# single and a window is still keyed by whatever header text came before it.
PART_HEADER_RE = re.compile(r"^Part\s+\d+\s*:\s*.+$", re.IGNORECASE)
TICKET_HASH_START_RE = re.compile(
    r"^Ticket\s*#(\d+)\s*\(([A-Za-z]+)\s*-\s*\$([\d,.]+)\s*Bet\)\s*(?:\[[^\]]*\]\s*)*?\[PP:\s*\$?([\d,.]+)\]\s*$", re.IGNORECASE
)
# ...and its prop-style leg, first seen on the first real card with a steal on it
# (2026-09-19, "PART 6: LATE ADDITIONS"):
#     Ticket #17 (Bailey - $5 Bet) [+2925] [PP: $151.25]
#     - Josh Naylor - Stolen Bases O0.5 (+450) - SEA @ COL - 8:10 PM ET
#     - Ben Rice - Home Runs O0.5 (+450) - NYY @ ARI - 8:10 PM ET
# The market is spelled out on every leg, the game is a matchup rather than the
# player's team, and there is no per-leg bettor -- the leg belongs to whoever
# placed the ticket. A leading "(Bettor)" is still honoured if a card adds one.
TICKET_PROP_LEG_RE = re.compile(
    BULLET + r"(?:\(([^)]+)\)\s*)?(.+?)\s+-\s+(stolen\s+bases?|steals?|SB|home\s+runs?|HRs?)"
    r"(?:\s+O(?:ver)?\s*(\d+(?:\.\d+)?))?\s*\(([+-]\d+)\)\s*-\s*(.+?)\s*-\s*([\d: ]*[AP]M\s*ET)\s*$", re.IGNORECASE
)
# A line that is obviously part of a bet. If one of these reaches the end of the
# loop unmatched, a bet is about to go untracked -- say so, loudly (see main()).
BETLIKE_RE = re.compile(r"^\*?\s*Ticket\s*#?\s*\d+|^[*\-•]\s.*\([+-]\d{3,}\)", re.IGNORECASE)
TICKET_HASH_LEG_RE = re.compile(
    BULLET + r"\(([^)]+)\)\s*(.+?)\s+-\s+([A-Z]{2,4})\s*\(([+-]\d+)\)\s*-\s*([\d: ]*[AP]M\s*ET)\s*$", re.IGNORECASE
)

# ---- fourth raw-text template: an emoji-prefixed "Ticket #N (M-Leg Parlay)"
# or "Bonus Ticket (M-Leg Parlay)" header (some tickets in the same card omit
# the leg-count suffix entirely), stake/payout/book on their OWN line right
# after it, and "• Player (TEAM) - TIME ET (+ODDS) (Bettor)" legs. First seen
# 2026-09-20 (a real Discord upload the first three templates parsed as zero
# tickets -- exit 1, nothing written, that day's real slate never posted):
#     🎰 Ticket #1 (3-Leg Parlay)
#     Bet: $4.00 (Memo) | PP: $283.50
#     • Michael Busch (CHC) - 1:40 PM ET (+360) (Noid)
#     🔥 Bonus Ticket (5-Leg Parlay)
#     Bet: $5.00 (Memo) | PP: $33,141.11
# The M-Leg count in the header is never trusted, same rule as every other
# template -- actual leg count decides single vs. parlay. "Bonus Ticket" has
# no number at all; it's given the placeholder name "Bonus" for the card name.
EMOJI_TICKET_START_RE = re.compile(
    r"^[^\w]*\s*(?:Ticket\s*#\s*(\d+)|(Bonus\s+Ticket))\s*(?:\(\s*\d+[- ]Leg\s*Parlay\s*\))?\s*$", re.IGNORECASE)
EMOJI_BOOK_RE = re.compile(r"^Bet:\s*\$([\d,.]+)\s*\(([A-Za-z]+)\)\s*\|\s*PP:\s*\$([\d,.]+)\s*$", re.IGNORECASE)
EMOJI_LEG_RE = re.compile(
    BULLET + r"(.+?)\s*\(([A-Z]{2,4})\)\s*-\s*([\d: ]*[AP]M\s*ET)\s*\(([+-]\d+)\)\s*\(([^)]+)\)\s*$", re.IGNORECASE)

# ---- fifth raw-text template: "Parlay N (Bettor)" / "Ticket N (Bettor)"
# header naming the whole ticket's owner (NOT a per-leg bettor), legs of the
# shape "Player (+ODDS) (Who) – TIME ET" (an en-dash before the time, not the
# hyphen the third/fourth templates use), and a closing
# "Wager: $X | Payout: $Y" line instead of a "Bet by"/"[PP: ...]" footer.
# First seen 2026-09-21 under "🕒 <Name> Window (...)" section headers, e.g.:
#     🕒 Early Window (6:35 PM – 6:40 PM ET)
#     Parlay 1 (Memo)
#     * Bo Bichette (+730) (Noid) – 6:35 PM ET
#     * James Wood (+420) (Francher) – 6:40 PM ET
#     * Wager: $7.33 | Payout: $310.28
# The header's own "(Bettor)" is the ticket owner, used as the leg's "who"
# fallback ONLY when a leg doesn't carry its own trailing "(Who)".
#
# The card's LAST section is a "Bonus Bets Tracker" whose tickets are the same
# "Ticket N (Bettor)" shape but whose legs carry no start time:
#     Bonus Bets Tracker
#     Ticket 10 (Memo)
#     * Bryce Harper (+540) (Francher)
#     * Wager: $3.00 | Payout: $1,039.18
# so BOTH the "(Who)" and the "- TIME ET" tail are optional. They were required
# at first, which meant a timeless leg matched nothing, and the six legs that
# produced were stored with the bettor still glued to the player name
# ("Francher-Harper"): resolved to nobody, blank team, unable to grade either
# way -- the Tatis Jr. failure mode. Making the tail optional fixes that and
# subsumes the separate bonus-leg pattern that used to sit here.
WINDOW_HEADER_RE = re.compile(r"^\W*\s*.+?\s+Window\s*\(.+\)\s*$", re.IGNORECASE)
# A "Bonus Bets Tracker" heading opens a section too. Deliberately narrow --
# word characters and spaces only -- so it can never match a LEG line (which
# always carries parenthesised odds) and swallow the rest of the card. Without
# it those tickets were filed under the preceding time window, and the page
# showed them under a first pitch they had nothing to do with.
TRACKER_HEADER_RE = re.compile(r"^\W*\s*[A-Za-z][\w ]*\bTracker\b[\w ]*$", re.IGNORECASE)
PARLAY_START_RE = re.compile(r"^(?:Parlay|Ticket)\s+(\d+)\s*\(([A-Za-z]+)\)\s*$", re.IGNORECASE)
# Player leg: "Bo Bichette (+730) (Noid) - 6:35 PM ET", or with either trailing
# part absent. Reachable only while a "Parlay N (...)" ticket is open, which is
# what keeps its lazy (.+?) from reaching a line an older template owns.
PARLAY_LEG_RE = re.compile(
    BULLET + r"(.+?)\s*\(([+-]\d+)\)\s*(?:\(([^)]+)\)\s*)?"
    r"(?:[\u2013\u2014-]\s*([\d: ]*[AP]M\s*ET)\s*)?$", re.IGNORECASE
)
PARLAY_FOOT_RE = re.compile(
    BULLET + r"Wager:\s*\$([\d,.]+)\s*\|\s*Payout:\s*\$?([\d,.]+)\s*$", re.IGNORECASE
)


# ---- bet markets: home runs (the default) and stolen bases ----
# A leg is a home run bet unless the card says otherwise. This matcher predates
# the first real steal card (2026-09-19, which turned out to spell the market
# out prop-style -- see TICKET_PROP_LEG_RE above) and is deliberately tolerant
# about WHERE the card says it rather than a guess at one exact shape, so it
# still covers a card that marks steals any of these other ways:
#   * on the leg's own line      "* (Kenny) Elly De La Cruz - CIN (+150) SB - 6:40 PM ET"
#   * on the ticket's header     "Ticket #4 (Memo - $5 Bet) [PP: $40.00] - Stolen Bases"
#   * on a section header        "Part 3: Stolen Base Parlays"  (until the next header)
# written as SB / Stolen Base(s) / Steal(s) / To Steal (a Base), bare or in
# ( ) or [ ]. The marker is lifted out of the line before the usual patterns
# run, so it can sit anywhere without breaking them. Home run legs get NO
# market field at all -- tickets.json for a home-run-only card is byte-for-byte
# what it was before steals existed.
SB_MARK_RE = re.compile(
    r"[\(\[]?\s*\b(?:SB|stolen\s+bases?|steals?|to\s+steal(?:\s+a\s+base)?)\b\s*[\)\]]?", re.IGNORECASE)


def take_market(line):
    """-> (line with any steal marker removed, whether there was one)."""
    m = SB_MARK_RE.search(line)
    if not m:
        return line, False
    clean = line[:m.start()] + " " + line[m.end():]
    clean = re.sub(r"\s*([-|])\s*(?:[-|]\s*)+", r" \1 ", clean)     # "- -" where the marker sat between separators
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    clean = re.sub(r"\s*[-|:]\s*$", "", clean)                      # ...or a separator left dangling at the end
    return clean, True


def normalize_name(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    return " ".join(name.split()).strip().lower()


def clean_num(s):
    """Money string -> float. Tolerates the currency symbol and separators,
    because every caller is handing this a price off a betting card.

    It used to strip only commas, so a template whose regex captured "$310.28"
    rather than "310.28" blew up with a ValueError deep inside parse(). That
    cost the automatic fixer a whole attempt on 2026-09-22 -- it had the new
    template otherwise working and the full suite passing. A card that writes
    its payout with a dollar sign is completely ordinary, so accept it here
    instead of making every future template's regex remember to exclude it."""
    return float(s.replace(",", "").replace("$", "").strip())


def load_roster():
    if not ROSTER_PATH.exists():
        return {}, {}
    data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    return data.get("team_by_name", {}), data.get("canonical_name_by_norm", {})


def section_header(line):
    """Header text if `line` is a section header (with or without '##'), else None."""
    text = line.lstrip("#").strip()
    # "Card 11: Mega Longshot Wager" would otherwise read as a singles header.
    # "🎰 Ticket #1 (3-Leg Parlay)" would too -- it contains the literal
    # substring "3-Leg Parlay", which PARLAY_HEADER_RE below matches, so every
    # emoji-ticket header was misread as a brand new section before
    # EMOJI_TICKET_START_RE ever got a look at it. Same story for
    # "Parlay 1 (Memo)" against PARLAY_START_RE below -- it contains the word
    # "Parlay" but is a ticket header, not a section header.
    if (CARD_HEADER_RE.match(text) or ODDS_RE.search(text) or BET_FOOT_RE.search(text)
            or EMOJI_TICKET_START_RE.match(text) or PARLAY_START_RE.match(text)):
        return None
    if SINGLES_HEADER_RE.search(text) or PARLAY_HEADER_RE.search(text):
        return text
    return text if line.startswith("##") else None


def slate_date_for(time_strs, now):
    """MLB game date (YYYY-MM-DD) a slate with these ET start times is for."""
    starts = []
    for t in time_strs:
        m = re.match(r"(\d{1,2}):(\d{2})\s*([AP])M", t.strip(), re.IGNORECASE)
        if not m:
            continue
        hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "P" else 0)
        starts.append(hour * 60 + int(m.group(2)))
    # Nobody posts a slate after its last first pitch, so if every listed
    # start time is already behind us today, these picks are for tomorrow
    # (e.g. posted 11pm for the next day). Posted after midnight but before
    # first pitch, they're today's.
    if starts and now.hour * 60 + now.minute > max(starts):
        return (now.date() + timedelta(days=1)).isoformat()
    return now.date().isoformat()


def archive_previous_slate(new_date):
    if not TICKETS_PATH.exists():
        return
    try:
        old = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    old_date = old.get("date")
    # Same-day re-uploads (corrections) must not clobber yesterday's archive.
    if not old_date or old_date == new_date:
        return
    PREVIOUS_PATH.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Archived {old_date} slate -> {PREVIOUS_PATH}")


def resolve_player(raw_name, team_by_name, canonical_by_norm):
    """
    Returns (canonical_name, team). Exact normalized match first; if that
    misses, fuzzy-matches against the roster and uses the closest name
    (only if reasonably close, otherwise falls back to the name as typed
    with an empty team so it doesn't silently mismatch to someone else).
    """
    norm = normalize_name(raw_name)
    if norm in team_by_name:
        return canonical_by_norm[norm], team_by_name[norm]

    candidates = difflib.get_close_matches(norm, team_by_name.keys(), n=1, cutoff=0.82)
    if candidates:
        best = candidates[0]
        return canonical_by_norm[best], team_by_name[best]

    print(f"NOTE: '{raw_name}' not found in roster (even fuzzy). "
          f"Keeping as typed, team left blank — check spelling.", file=sys.stderr)
    return raw_name.strip(), ""


def parse(text, team_by_name, canonical_by_norm):
    lines = [l.rstrip() for l in text.splitlines()]
    windows = []
    singles = []
    mode = None
    current_section = None
    current_card = None
    single_idx = 0

    # ---- steal markers: the section's default, and the open ticket's ----
    section_sb = False
    ticket_sb = False
    unread = []          # bet-looking lines no pattern understood
    parse.unread = unread

    def market_for(leg_sb):
        return "sb" if (leg_sb or ticket_sb or section_sb) else "hr"

    # ---- state for the "Ticket N:" template (see TICKET_START_RE above) ----
    last_header_title = None
    ticket_windows_by_title = {}
    current_ticket = None  # {"_legs": [(time, player, team, odds, who), ...]}

    # ---- state for the "Parlay N (Bettor)" template (see PARLAY_START_RE) ----
    current_parlay = None  # {"_num": ..., "_who": bettor, "_legs": [...], "_stake": None, "_pp": None}

    def flush_card():
        nonlocal current_card
        if current_card and current_card["_legs"]:
            windows_append_card(current_card)
        current_card = None

    def flush_ticket():
        nonlocal current_ticket
        if current_ticket is not None:
            finalize_ticket(current_ticket, current_ticket["_num"])
            current_ticket = None

    def flush_parlay():
        nonlocal current_parlay
        if current_parlay is not None:
            finalize_parlay(current_parlay)
            current_parlay = None

    def windows_append_card(card):
        current_section["tickets"].append(card)

    def ticket_window(title):
        w = ticket_windows_by_title.get(title)
        if w is None:
            w = {"title": title or "Parlay Cards", "tickets": []}
            ticket_windows_by_title[title] = w
            windows.append(w)
        return w

    def finalize_ticket(ticket, num):
        legs_raw = ticket["_legs"]
        if not legs_raw or ticket["_stake"] is None:
            return  # malformed/truncated ticket (e.g. file cut off) -- drop, don't guess
        if len(legs_raw) == 1:
            time_, player_raw, team_raw, odds, who, mkt = legs_raw[0]
            canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
            singles.append({
                "who": who.strip().upper(),
                "player": canon_name,
                "team": team or team_raw.strip(),
                "odds": odds,
                "market": mkt,
                "matchup": "",
                "time": time_.strip(),
                "stake": ticket["_stake"],
                "pp": ticket["_pp"],
            })
        else:
            legs = []
            for time_, player_raw, team_raw, odds, who, mkt in legs_raw:
                canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
                legs.append({"player": canon_name, "odds": odds, "who": who.strip(), "market": mkt,
                             "team": team or team_raw.strip(), "time": time_.strip()})
            card = {"name": f"Card {num}", "sub": "", "tag": None,
                    "_stake": ticket["_stake"], "_book": ticket["_book"],
                    "_origPayout": ticket["_pp"], "_legs": legs}
            ticket_window(last_header_title)["tickets"].append(card)

    def finalize_parlay(ticket):
        legs_raw = ticket["_legs"]
        if not legs_raw:
            return  # no legs at all -- nothing to report
        # A "Wager"/"Payout" line is required for the third+ templates but
        # this one always prints one right after its legs; if it's missing
        # (truncated paste) the payout is unknown, not guessed -- see rule 1c.
        stake = ticket["_stake"]
        pp = ticket["_pp"] if ticket["_pp"] is not None else None
        if len(legs_raw) == 1:
            time_, player_raw, who, odds, mkt = legs_raw[0]
            canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
            singles.append({
                "who": (who or ticket["_who"]).strip().upper(),
                "player": canon_name,
                "team": team,
                "odds": odds,
                "market": mkt,
                "matchup": "",
                "time": time_.strip(),
                "stake": stake,
                "pp": pp,
            })
        else:
            legs = []
            for time_, player_raw, who, odds, mkt in legs_raw:
                canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
                legs.append({"player": canon_name, "odds": odds,
                             "who": (who or ticket["_who"]).strip(), "market": mkt,
                             "team": team, "time": time_.strip()})
            card = {"name": f'Card {ticket["_num"]}', "sub": "", "tag": None,
                    "_stake": stake, "_book": ticket["_who"],
                    "_origPayout": pp, "_legs": legs}
            ticket_window(last_header_title)["tickets"].append(card)

    for raw in lines:
        original = raw.strip()
        if not original or re.fullmatch(r"[-=]{3,}", original):
            continue
        # headers keep their wording (it's the window title); everything else is
        # matched with the steal marker lifted out
        line, sb_here = take_market(original)

        header_text = section_header(original)
        if header_text is not None:
            flush_card()
            flush_ticket()
            flush_parlay()
            section_sb, ticket_sb = sb_here, False
            last_header_title = header_text
            if SINGLES_HEADER_RE.search(header_text):
                mode = "singles"
                current_section = None
            elif PARLAY_HEADER_RE.search(header_text):
                mode = "parlay"
                current_section = {"title": header_text, "tickets": []}
                windows.append(current_section)
            else:
                mode = None
            continue

        if (PART_HEADER_RE.match(original) or WINDOW_HEADER_RE.match(original)
                or TRACKER_HEADER_RE.match(original)):
            flush_card()
            flush_ticket()
            flush_parlay()
            section_sb, ticket_sb = sb_here, False
            last_header_title = original
            mode = None
            current_section = None
            continue

        parlay_start = PARLAY_START_RE.match(line)
        if parlay_start:
            flush_parlay()
            ticket_sb = sb_here
            num, who = parlay_start.groups()
            current_parlay = {"_num": num, "_who": who, "_legs": [], "_stake": None, "_pp": None}
            continue

        if current_parlay is not None:
            foot = PARLAY_FOOT_RE.match(line)
            if foot:
                stake, pp = foot.groups()
                current_parlay["_stake"] = clean_num(stake)
                current_parlay["_pp"] = clean_num(pp)
                flush_parlay()
                continue
            leg = PARLAY_LEG_RE.match(line)
            if leg:
                player_raw, odds, who, time_ = leg.groups()
                # both trailing groups are optional -- a bonus-tracker leg has
                # neither, so they arrive as None rather than ""
                current_parlay["_legs"].append(
                    (time_ or "", player_raw, who or "", odds, market_for(sb_here)))
                continue

        ticket_start = TICKET_START_RE.match(line)
        if ticket_start:
            flush_ticket()
            ticket_sb = False   # this template's first line is also its first leg: the marker is the leg's
            current_ticket = {"_num": re.match(r"^\*?\s*Ticket\s+(\d+)", line, re.IGNORECASE).group(1),
                               "_legs": [], "_book": None, "_stake": None, "_pp": None}
            leg = TICKET_LEG_RE.match(ticket_start.group(1).strip())
            if leg:
                current_ticket["_legs"].append(leg.groups() + (market_for(sb_here),))
            continue

        hash_start = TICKET_HASH_START_RE.match(line)
        if hash_start:
            flush_ticket()
            ticket_sb = sb_here
            num, book, stake, pp = hash_start.groups()
            current_ticket = {"_num": num, "_legs": [], "_book": book,
                               "_stake": clean_num(stake), "_pp": clean_num(pp)}
            continue

        emoji_start = EMOJI_TICKET_START_RE.match(line)
        if emoji_start:
            flush_ticket()
            ticket_sb = sb_here
            # stake/payout/book sit on their OWN line for this template
            # (EMOJI_BOOK_RE below), never inline with the header.
            current_ticket = {"_num": emoji_start.group(1) or "Bonus",
                               "_legs": [], "_book": None, "_stake": None, "_pp": None}
            continue

        if current_ticket is not None:
            foot = TICKET_FOOT_RE.match(line)
            if foot:
                book, stake, pp = foot.groups()
                current_ticket["_book"] = book
                current_ticket["_stake"] = clean_num(stake)
                current_ticket["_pp"] = clean_num(pp)
                flush_ticket()
                continue
            book_line = EMOJI_BOOK_RE.match(line)
            if book_line:
                stake, book, pp = book_line.groups()
                current_ticket["_stake"] = clean_num(stake)
                current_ticket["_book"] = book
                current_ticket["_pp"] = clean_num(pp)
                continue
            emoji_leg = EMOJI_LEG_RE.match(line)
            if emoji_leg:
                player_raw, team_raw, time_, odds, who = emoji_leg.groups()
                current_ticket["_legs"].append((time_, player_raw, team_raw, odds, who, market_for(sb_here)))
                continue
            leg = TICKET_LEG_RE.match(line)
            if leg:
                current_ticket["_legs"].append(leg.groups() + (market_for(sb_here),))
                continue
            prop_leg = TICKET_PROP_LEG_RE.match(original)
            if prop_leg:
                who, player_raw, market_word, threshold, odds, matchup, time_ = prop_leg.groups()
                if threshold and float(threshold) != 0.5:
                    print(f"NOTE: '{original}' is an over-{threshold} bet; the tracker only knows 'at least one' -- "
                          f"it will be marked a hit on the FIRST one.", file=sys.stderr)
                mkt = "hr" if re.match(r"h", market_word, re.IGNORECASE) else "sb"
                # no team code on these lines: resolve_player() supplies it from the roster
                current_ticket["_legs"].append((time_, player_raw, "", odds, who or current_ticket["_book"] or "", mkt))
                continue
            hash_leg = TICKET_HASH_LEG_RE.match(line)
            if hash_leg:
                who, player_raw, team_raw, odds, time_ = hash_leg.groups()
                current_ticket["_legs"].append((time_, player_raw, team_raw, odds, who, market_for(sb_here)))
                continue

        if mode == "parlay":
            card_match = CARD_HEADER_RE.match(line)
            if card_match:
                flush_card()
                ticket_sb = sb_here
                card_num, rest = card_match.groups()
                tag = None
                sub = rest
                tag_match = re.search(r"\(([^)]+)\)\s*$", rest)
                if tag_match and ("both" in tag_match.group(1).lower() or "@" in tag_match.group(1)):
                    tag = tag_match.group(1)
                    sub = rest[:tag_match.start()].strip()
                current_card = {
                    "name": f"Card {card_num}", "sub": sub, "tag": tag,
                    "_stake": None, "_book": None, "_origPayout": None, "_legs": []
                }
                continue

            bet_match = BET_FOOT_RE.search(line)
            if bet_match and current_card:
                book, stake, payout = bet_match.groups()
                current_card["_book"] = book
                current_card["_stake"] = clean_num(stake)
                current_card["_origPayout"] = clean_num(payout)
                continue

            leg_match = LEG_LINE_RE.match(line)
            if leg_match and current_card:
                player_raw, odds, middle, who = leg_match.groups()
                canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
                # The "who" capture is meant to be just the bettor's name, but
                # the source text sometimes tacks on a naming aside in the same
                # parens, e.g. "(Noid — Listed as Herb Hernandez)". That aside
                # describes the PLAYER, not the bettor, and resolve_player()
                # already gives us the correct roster name above -- so strip
                # anything after a dash/em-dash from who before storing it.
                clean_who = re.split(r"[\u2014-]", who, maxsplit=1)[0].strip()
                leg = {"player": canon_name, "odds": odds, "who": clean_who, "market": market_for(sb_here)}
                t = TIME_RE.search(middle)
                if t:
                    leg["time"] = t.group(0)
                    leg["team"] = team
                else:
                    # middle held a team code instead of a time; prefer the
                    # roster's team if we resolved one, else keep what was typed
                    leg["team"] = team or middle.strip()
                    leg["time"] = ""
                current_card["_legs"].append(leg)
                continue

        elif mode == "singles":
            m = SINGLE_LINE_RE.match(line)
            if m:
                who, player_raw, odds, matchup, time_, stake, pp = m.groups()
                canon_name, team = resolve_player(player_raw, team_by_name, canonical_by_norm)
                singles.append({
                    "who": who.strip().upper(),
                    "player": canon_name,
                    "team": team,
                    "odds": odds,
                    "market": market_for(sb_here),
                    "matchup": (matchup or "").strip(),
                    "time": (time_ or "").strip(),
                    "stake": clean_num(stake),
                    "pp": clean_num(pp),
                })
                single_idx += 1
                continue

        if BETLIKE_RE.search(original):
            unread.append(original)

    flush_card()
    flush_ticket()
    flush_parlay()

    # Drop windows that ended up with nothing in them (e.g. a document title
    # line like "HOME RUN PARLAY CARD" that happens to look header-shaped).
    windows = [w for w in windows if w["tickets"]]

    # ---- build final schema matching index.html ----
    out_windows = []
    for si, section in enumerate(windows):
        out_tickets = []
        for ci, card in enumerate(section["tickets"]):
            legs = []
            for li, leg in enumerate(card["_legs"]):
                meta = f"{leg['team']} &middot; {leg['who']}" if leg["team"] else leg["who"]
                legs.append({
                    "id": f"p{si}-c{ci}-l{li}",
                    "player": leg["player"],
                    "team": leg["team"],
                    "who": leg["who"],
                    "meta": meta,
                    "odds": leg["odds"],
                    "time": leg["time"],
                })
                if leg.get("market") == "sb":
                    legs[-1]["market"] = "sb"
            tag_html = f' &middot; {card["tag"]}' if card.get("tag") else ""
            sub_html = f' &middot; {card["sub"]}' if card.get("sub") else ""
            if card["_origPayout"] is None:
                payout_str = "TBD"
            else:
                payout_str = f'${card["_origPayout"]:,.2f}'
            foot = (f'<b>${card["_stake"]:.2f}</b> bet by {card["_book"]} '
                    f'&middot; Potential payout <b>{payout_str}</b>')
            out_tickets.append({
                "name": f'{card["name"]}{sub_html}{tag_html}',
                "sub": f'{len(legs)}-Leg',
                "foot": foot,
                "stake": card["_stake"],
                "book": card["_book"],
                "payout": card["_origPayout"],
                "legs": legs,
            })
        out_windows.append({"title": section["title"], "tickets": out_tickets})

    out_singles = []
    for i, s in enumerate(singles):
        meta_parts = [p for p in [s["matchup"], s["time"], f'${s["stake"]:.2f} bet'] if p]
        pp_str = "PP TBD" if s["pp"] is None else f'PP ${s["pp"]:,.2f}'
        out_singles.append({
            "id": f"single-{i}",
            "who": s["who"],
            "player": s["player"],
            "team": s["team"],
            "meta": " &middot; ".join(meta_parts),
            "odds": s["odds"],
            "stake": s["stake"],
            "payout": s["pp"],
            "pp": pp_str,
        })
        if s.get("market") == "sb":
            out_singles[-1]["market"] = "sb"

    return out_windows, out_singles, singles


def main():
    if "--file" in sys.argv:
        idx = sys.argv.index("--file")
        text = Path(sys.argv[idx + 1]).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    team_by_name, canonical_by_norm = load_roster()
    windows, out_singles, raw_singles = parse(text, team_by_name, canonical_by_norm)

    total_legs = sum(len(c["legs"]) for w in windows for c in w["tickets"])
    print(f"Parsed {len(out_singles)} singles, "
          f"{sum(len(w['tickets']) for w in windows)} parlay cards "
          f"({total_legs} legs) across {len(windows)} sections.")

    if total_legs == 0 and not out_singles:
        print("WARNING: parsed nothing. Check the input format.", file=sys.stderr)
        sys.exit(1)

    all_times = [leg["time"] for w in windows for c in w["tickets"] for leg in c["legs"] if leg.get("time")]
    all_times += [s["time"] for s in raw_singles if s.get("time")]
    slate_date = slate_date_for(all_times, datetime.now(ET))

    # A bet line nothing understood means a bet that is NOT being tracked. The
    # upload still posts (better most of a slate than none), but the page's note
    # line says so where the group will see it, instead of only an Actions log.
    unread = getattr(parse, "unread", [])
    note = ""
    if unread:
        for line in unread:
            print(f"WARNING: couldn't read: {line}", file=sys.stderr)
        note = (f"&#9888; {len(unread)} line{'s' if len(unread) != 1 else ''} on the card couldn't be read, so "
                f"{'those bets are' if len(unread) != 1 else 'that bet is'} NOT being tracked &mdash; first one: "
                f"&ldquo;{unread[0][:70]}&rdquo;")
    payload = {"date": slate_date, "note": note, "windows": windows, "singles": out_singles}
    TICKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    archive_previous_slate(slate_date)
    TICKETS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {TICKETS_PATH} (slate date: {slate_date} ET)")


if __name__ == "__main__":
    main()
