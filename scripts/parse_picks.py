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
import os
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
# ---- sixth raw-text template: the "DAILY HOME RUN PARLAY TRACKER" card
# (first seen 2026-09-23). A bare bettor NAME on its own line opens that
# person's section, and every ticket under it belongs to them:
#     Francher
#     Ticket #1 - 2-Leg Parlay $6.00
#     [ ] Matt Olson +340 (Braves) 7:15 PM
#     [ ] Kyle Schwarber +270 (Phillies) 6:40 PM
#     Potential Payout: $97.68
# Differences from every earlier template, all real on the first card:
#   * the STAKE is in the ticket header, not a separate Wager line
#   * legs are checkboxes, and the team is a full NICKNAME ("Braves"), not the
#     2-4 letter code -- so it's ignored entirely and the roster supplies the
#     team, exactly as the fifth template already does
#   * times carry no "ET"; it's added so the page reads the same everywhere
#   * "Steal" is written inline ("Elly De La Cruz Steal +350") -- take_market()
#     already lifts that out, so nothing extra is needed here
#   * a leg can have NO ODDS at all, and one was marked "- DNP"
# The bettor line is deliberately NOT a trigger on its own: a bare capitalised
# word is far too common to hang parsing off. It is only REMEMBERED, and used
# when a Ticket header actually follows -- so a stray word does nothing.
BARE_NAME_RE = re.compile(r"^[A-Z][A-Za-z.'\-]{1,19}$")
TRACKER_TICKET_RE = re.compile(
    r"^Ticket\s*#?\s*(\d+)\s*[\u2014\u2013-]\s*"
    r"(?:\d+-Leg\s+Parlay|Straight\s+Bet)\s*\$([\d,.]+)\s*$", re.IGNORECASE)
# "[ ] Player +ODDS (Team) TIME". Odds are optional so a priced and an unpriced
# leg both MATCH -- the difference is handled below, where an unpriced one is
# reported rather than silently dropped. A trailing "- DNP" lands in the tail.
CHECKBOX_LEG_RE = re.compile(
    r"^\[\s*[xX]?\s*\]\s*(.+?)\s*(?:([+-]\d+)\s*)?\(([^)]+)\)\s*(.*)$")
# "Potential Payout: $97.68", or "N/A" when the card can't state one yet --
# null, never a guess and never a dropped bet (rule 1c).
TRACKER_PAYOUT_RE = re.compile(
    r"^Potential\s+Payout:\s*(?:\$([\d,.]+)|N/?A)\s*$", re.IGNORECASE)
PARLAY_FOOT_RE = re.compile(
    BULLET + r"Wager:\s*\$([\d,.]+)\s*\|\s*Payout:\s*\$?([\d,.]+)\s*$", re.IGNORECASE
)

# ---- seventh raw-text template: the "SOLAR KEYS DAY TRACKER" card (first
# seen 2026-09-24). Its ticket header states the stake and payout INLINE, in
# plain-English parentheses, rather than the "[Bet: $X | PP: Y]" or
# "(Bettor - $X Bet) [PP: $Y]" shapes above:
#     🎟️ Ticket 1 (6.00 bet pays 198.00)
#     * Carson Benge (+560) - NYM (Kevin) 🕒 2:35 PM ET
#     * Miguel Vargas (+400) - CWS (Memo) 🕒 2:10 PM ET
# There is no separate "Bet by"/"Wager" footer line at all -- the numbers on
# the header line ARE the stake and payout, full stop. Each leg carries its
# own bettor (per-leg, like the third template) and its own team code, and
# ends with a clock emoji before the time rather than a bare "- TIME ET" tail
# -- distinct enough from TICKET_HASH_LEG_RE (which requires a leading
# "(Bettor)" *before* the player, not after the team) that the two never
# collide. Reuses the existing "current_ticket"/finalize_ticket machinery,
# same as templates two through four.
EMOJI_STAKE_TICKET_START_RE = re.compile(
    r"^[^\w]*\s*Ticket\s+(\d+)\s*\(\s*\$?([\d,.]+)\s*bet\s+pays\s*\$?([\d,.]+)\s*\)\s*$", re.IGNORECASE)
EMOJI_STAKE_LEG_RE = re.compile(
    BULLET + r"(.+?)\s*\(([+-]\d+)\)\s*-\s*([A-Z]{2,4})\s*\(([^)]+)\)\s*[^\w\s]*\s*([\d: ]*[AP]M\s*ET)\s*$",
    re.IGNORECASE
)


# ---- eighth raw-text template (first seen 2026-09-26): a BARE "Parlay N"
# header, legs separated from a FULL team name by an em-dash, and a footer
# carrying stake, payout and the ticket OWNER all at once:
#     Parlay 1
#     * Juan Soto (+480) - New York Mets (Joe) 12:35 PM
#     $6.00 Bet | Potential Payout: $117.48 (Memo)
# Near-miss with the fifth template ("Parlay N (Bettor)"), but the header names
# nobody, the team is spelled out in full instead of coded, and the footer is a
# different shape entirely -- the owner is at the END, after the payout.
# The full team name is IGNORED and the roster supplies the team, exactly as
# the fifth, sixth and seventh templates already do: a nickname-to-code map
# would be a second source of truth that could drift from the roster.
PLAIN_PARLAY_START_RE = re.compile(r"^Parlay\s+(\d+)\s*$", re.IGNORECASE)
# The separator is restricted to an EM/EN dash, never a plain hyphen. The
# seventh template's legs ("* Carson Benge (+560) - NYM (Kevin) ...") would
# otherwise match this pattern too -- same player/odds/team/bettor order, just
# a hyphen -- and whichever regex ran first would quietly own both shapes.
PLAIN_PARLAY_LEG_RE = re.compile(
    BULLET + r"(.+?)\s*\(([+-]\d+)\)\s*[\u2014\u2013]\s*(.+?)\s*\(([^)]+)\)\s*(.*)$")
# "$6.00 Bet | Potential Payout: $117.48 (Memo)" -- the trailing "(Owner)" is
# optional so a card that omits it still parses, with the ticket left ownerless
# (which prints as a plain "$6.00 bet", never "bet by None").
PLAIN_PARLAY_FOOT_RE = re.compile(
    r"^\$?([\d,.]+)\s*Bet\s*\|\s*Potential\s+Payout:\s*\$?([\d,.]+)"
    r"(?:\s*\(([^)]+)\))?\s*$", re.IGNORECASE)


# ---- ninth template (2026-09-29): the single-game prop card ------------
# The first card to use markets other than home runs and steals, and unlike
# every template before it the legs carry NO ODDS at all -- only the ticket is
# priced:
#     Phillies vs. Braves  2:00 PM
#     Ticket #1
#     - Phillies +1.5
#     - Schwarber/Olson 1+ Total Homers
#     - Turner SB
#     $5 pays $109.70
# Three things here are new and each was a user decision:
#   * a leg may have a NULL price. The ticket's own stake and payout still
#     work, so the card is fully usable; inventing per-leg odds would be
#     inventing money.
#   * names are SURNAMES, which this repo refuses to resolve league-wide --
#     "Turner" is several players. The header names both teams, so a surname
#     is resolved against only those two rosters, and one that still matches
#     two players there stays unresolved rather than picking.
#   * a leg can name TWO players ("Schwarber/Olson 1+ Total Homers"), graded
#     as their combined total.
GAME_HEADER_RE = re.compile(
    r"^\W*\s*([A-Za-z][A-Za-z .']{2,25}?)\s+(?:vs\.?|@|at)\s+([A-Za-z][A-Za-z .']{2,25}?)"
    r"\s*\W*\s*(\d{1,2}:\d{2}\s*[AP]M(?:\s*ET)?)?\s*$", re.IGNORECASE)
PLAIN_TICKET_RE = re.compile(r"^Ticket\s*#\s*(\d+)\s*$", re.IGNORECASE)
# "$5 pays $109.70"
# "pays?" because a real card wrote "$8 pay 255.36" with no "s" -- one line
# out of twelve on the same card, so it is a typo rather than a shape. Missing
# it left that ticket with no stake at all.
PAYS_FOOT_RE = re.compile(
    r"^\W*\s*\$?([\d,.]+)\s*pays?\s*\$?([\d,.]+)\s*$", re.IGNORECASE)
# A bullet leg with no odds and free-form market text.
BULLET_PROP_RE = re.compile(r"^[*\-\u2022\u00b7]\s*(.+?)\s*$")
# ---- eleventh template variant (first seen 2026-10-01): same bare "Ticket N"
# shape as the tenth template, but the legs carry NO bullet character at all
# -- just the free-form market text on its own line -- and the footer omits
# the "$" entirely ("8.50 pays 105.79" rather than "$8.50 pays $105.79"):
#     Ticket 1
#     Mex Fried Over 1.5 ER
#     Alec Bohm RBI 1+
#     8.50 pays 105.79
# PAYS_FOOT_RE already tolerates a missing "$" (its "$?" groups), so only the
# leg line needed a fallback. Tried AFTER BULLET_PROP_RE so a bulleted card's
# lines are still claimed by that pattern first; this one excludes anything
# that looks like a ticket/game/footer line so it can never eat one of those.
BARE_PROP_LINE_RE = re.compile(r"^(?!\s*$)(.+?)\s*$")
# A price at the END of a leg line, with or without a sign: "... TD +130",
# "... 2+ TB 145". Deliberately strict about what counts, because this runs on
# a line that also carries the market and its LINE:
#   * a signed number is a price, full stop;
#   * an UNSIGNED one only counts at 100 or more, so the "2" of "2+ TB" and
#     the "3.5" of "Over 3.5 Runs" can never be mistaken for one;
#   * never a decimal -- odds are whole numbers, payouts are not, and this
#     must not eat the "81.87" of a footer that reached here by accident.
TRAILING_ODDS_RE = re.compile(r"^(.*?)\s+([+-]\d{2,4}|\d{3,4})\s*$")


def split_trailing_odds(text):
    """-> (line without its trailing price, that price as '+130' / None)."""
    m = TRAILING_ODDS_RE.match(text or "")
    if not m:
        return text, None
    body, num = m.group(1), m.group(2)
    if not body.strip():
        return text, None          # the whole line was a number; not a leg
    return body, num if num[0] in "+-" else f"+{num}"
# "1+ Total Homers" / "4+ Total Bases" / "2+ Hits" / "5+ Strikeouts".
# "N+" means at least N, which is an over on N-0.5.
N_PLUS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*\+", re.IGNORECASE)
# The market words this card uses, checked before the generic table because
# "Total Homers" must not be read as a game TOTAL.
CARD_MARKET_WORDS = [
    # hrr BEFORE hits: "3+ Hits+Runs+RBIs" otherwise matches the bare `hits`
    # alias and grades as a hits prop. er/win BEFORE runs, for the same
    # reason "Home Runs" has to beat "Runs".
    ("hrr",    r"h\s*\+\s*r\s*\+\s*rbi|hits?\s*\+\s*runs?\s*\+\s*rbis?"),
    ("er",     r"\bER\b|earned\s+runs?"),
    ("win",    r"to\s+get\s+the\s+win|\bpitcher\s+win\b|\bfor\s+the\s+win\b"),
    ("hr",     r"total\s+homers?|home\s+runs?\b|\bhr\b"),
    ("tb",     r"total\s+bases\b"),
    ("k",      r"strikeouts?\b|\bks?\b"),
    ("doubles", r"\bdoubles?\b"),
    ("hits",   r"\bhits?\b"),
    ("rbi",    r"\brbis?\b"),
    ("sb",     r"\bsb\b|stolen\s+bases?|steals?\b"),
]
CARD_MARKET_RE = [(k, re.compile(pat, re.IGNORECASE)) for k, pat in CARD_MARKET_WORDS]

# ---- tenth raw-text template (first seen 2026-09-30): a BARE "Ticket N"
# header with no stake/payout on it at all, bullet legs that are pure
# free-form market text with NO odds and no game header naming the two teams
# first -- so there is no team restriction to resolve surnames against, and
# a Wager/Payout footer that closes the ticket instead of a "$X pays $Y" line:
#     Ticket 1
#     • Yordan Alvarez 1+ RBI
#     • First 5 innings Red Sox vs Cubs under 3.5
#     • Cubs ML
#     Wager: $7.00 | Payout: $65.18
# Distinguished from the ninth template's PLAIN_TICKET_RE (which requires a
# preceding GAME_HEADER_RE to even be looked at) by being reachable with NO
# game header seen -- current_prop is only opened when prop_teams is already
# non-empty, so this template needed its own trigger. Reuses read_prop_leg()
# with an empty team set (full-league surname resolution is skipped there the
# same way it already is for an ambiguous surname -- see resolve_in_teams),
# and reuses PARLAY_FOOT_RE for the footer since "Wager: $X | Payout: $Y" is
# exactly that pattern already used by the eighth-and-earlier templates.
BARE_TICKET_RE = re.compile(r"^Ticket\s*#?\s*(\d+)\s*$", re.IGNORECASE)

# ---- twelfth raw-text template (first seen 2026-10-03): a bare "Ticket N"
# header (same trigger as the tenth template, BARE_TICKET_RE) but the legs
# use a plain hyphen bullet with a colon separator, mix in non-baseball props
# (NHL anytime goals, NFL anytime TDs/receiving yards/points) alongside real
# MLB props, and the footer is "Stake: 8.50 | Pays: 108.48" -- or, on a card
# that only gives a combined number, "Stake/Pays: 210":
#     Ticket 1
#     - Tage Thompson: Anytime Goal
#     - Phillies/Braves: Over 8.5 Runs
#     - Pat Freiermuth: 30+ Receiving Yards
#     Stake: 8.50 | Pays: 108.48
# Reuses read_prop_leg() with no team restriction, same as the tenth template
# (this card never names two MLB teams either, and when it DOES name two
# teams -- "Phillies/Braves: Over 8.5 Runs" -- that's a generic team-total
# leg read_prop_leg() already handles). A non-baseball prop (hockey goal,
# football TD/yards/points) has no market alias here and simply comes through
# with an "unknown" market, same as any other leg nothing in MARKET_ALIASES
# recognises -- shown on the page, not graded, not dropped.
HYPHEN_TICKET_LEG_RE = re.compile(r"^-\s*(.+?)\s*:\s*(.+?)\s*$")
# An optional "(Owner)" at the end names who placed the ticket -- this card
# shape names nobody otherwise, so a bet added for one person had nowhere to
# say so: "Stake: 9.88 | Pays: 84 (Kenny)". Absent, every leg's bettor stays
# blank exactly as before.
TICKET_STAKE_PAYS_RE = re.compile(
    r"^Stake:\s*\$?([\d,.]+)\s*\|\s*Pays:\s*\$?([\d,.]+)"
    r"(?:\s*\(\s*([A-Za-z][A-Za-z .'-]{0,30}?)\s*\))?\s*$", re.IGNORECASE)
TICKET_STAKE_PAYS_COMBINED_RE = re.compile(
    r"^Stake/Pays:\s*\$?([\d,.]+)\s*$", re.IGNORECASE)


_NICKNAMES = None


def nickname_map():
    """normalized group shorthand -> full player name, from the hand-reviewed
    scripts/history_player_map.json. Read once; an absent or unreadable file
    is a normal state that simply means no shorthand is known."""
    global _NICKNAMES
    if _NICKNAMES is None:
        _NICKNAMES = {}
        path = Path(__file__).resolve().parent / "history_player_map.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for short, info in (data.get("players") or {}).items():
                full = (info or {}).get("name")
                if full:
                    _NICKNAMES[normalize_name(short)] = full
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return _NICKNAMES


SUFFIX_WORDS = ("jr", "sr", "ii", "iii", "iv", "v")
_SUFFIXLESS = {}


def _strip_suffix(norm):
    parts = [w for w in norm.split() if w not in SUFFIX_WORDS]
    return " ".join(parts) or norm


def _suffixless_names(team_by_name):
    """normalized full name WITHOUT a generational suffix -> [(full, abbr)].

    Cached on the roster's identity: it's rebuilt only when a different roster
    is handed in, which in practice means once.
    """
    key = id(team_by_name)
    if _SUFFIXLESS.get("_key") != key:
        built = {}
        for full, abbr in team_by_name.items():
            built.setdefault(_strip_suffix(full), []).append((full, abbr))
        _SUFFIXLESS.clear()
        _SUFFIXLESS["_key"] = key
        _SUFFIXLESS["map"] = built
    return _SUFFIXLESS["map"]


def team_abbr_for(text, words):
    """A team word -> abbreviation, tolerating a typo.

    Cards misspell team names ("Philles"), and an exact-match-only lookup
    silently turns a perfectly ordinary team total into an ungradeable leg.
    Fuzzy at the same 0.82 cutoff resolve_player() uses, and only when it is
    UNAMBIGUOUS -- two teams equally close means no answer, not a guess.
    """
    key = normalize_name(text)
    if not key:
        return None
    if key in words:
        return words[key]
    close = difflib.get_close_matches(key, list(words), n=2, cutoff=0.82)
    if len(close) == 1 or (len(close) == 2 and words[close[0]] == words[close[1]]):
        print(f"NOTE: read team {text!r} as {close[0]!r} ({words[close[0]]}).", file=sys.stderr)
        return words[close[0]]
    return None


def resolve_in_teams(name, teams, roster=None):
    """A surname (or full name) resolved against only the teams the card named.

    Returns (canonical name, abbr) or (name as typed, "") when it can't be
    pinned down -- never a guess. Resolving a bare surname league-wide is
    exactly what this repo refuses to do for history nicknames, and for the
    same reason: "Turner" is several players.
    """
    roster = roster if roster is not None else ROSTER_EXTRAS
    by_surname = roster.get("by_surname") or {}
    canon = roster.get("canonical_name_by_norm") or {}
    team_by_name = roster.get("team_by_name") or {}
    norm = normalize_name(name)
    if norm in team_by_name:                       # a full name needs no help
        return canon.get(norm, name), team_by_name[norm]
    # by_surname is keyed on the last word that ISN'T a suffix, so the lookup
    # has to drop one too: "Lombard Jr" was queried as "lombard jr" and found
    # nothing, even though George Lombard Jr. is right there in the index.
    key = norm
    parts = [w for w in norm.split() if w not in ("jr", "sr", "ii", "iii", "iv", "v")]
    if parts:
        key = parts[-1]
    # The group's own HAND-REVIEWED shorthand, from history_player_map.json --
    # the table the history importer already trusts, where every entry was
    # checked against real MLB game logs. "PCA" is Pete Crow-Armstrong and
    # "Vargas" is Miguel Vargas because a human decided so, not because
    # anything here inferred it. That is the difference between this and the
    # auto-resolution CLAUDE.md forbids: reviewed beats ambiguous, and an
    # initialism has no surname to match on at all.
    #
    # Entries must stay hand-reviewed (`import_history.py --draft-map`). If a
    # nickname is wrong here it is wrong in the permanent history too.
    nick = nickname_map().get(norm)
    if nick:
        full_norm = normalize_name(nick)
        return canon.get(full_norm, nick), team_by_name.get(full_norm, "")

    # A FULL name minus its generational suffix. The roster stores "michael
    # harris ii" and "luis garcia jr", so a card writing the plain name misses
    # the exact lookup and then falls to a surname that is ambiguous six ways.
    # First AND last name matching is not a guess -- it only becomes one if two
    # players share both, which is checked.
    base = _suffixless_names(team_by_name)
    hit = base.get(_strip_suffix(norm))
    if hit and len(hit) == 1 and (not teams or hit[0][1] in teams):
        full, abbr = hit[0]
        return canon.get(full, name), abbr

    candidates = [c for c in by_surname.get(key, []) if not teams or c[1] in teams]
    if len(candidates) == 1:
        full, abbr = candidates[0]
        return canon.get(full, name), abbr
    if not candidates:
        # A typo in the name itself ("Luis Garica"). Same 0.82 cutoff
        # resolve_player() uses, and only when a single roster name is that
        # close -- two equally-near names means no answer, not a pick.
        # Matched against SUFFIX-STRIPPED names: "luis garica" scores below
        # the cutoff against "luis garcia jr" purely because of the " jr",
        # which has nothing to do with the typo being corrected.
        pool = {k: v for k, v in base.items()
                if not teams or any(ab in teams for _, ab in v)}
        near = difflib.get_close_matches(_strip_suffix(norm), list(pool), n=2, cutoff=0.82)
        if len(near) == 1 and len(pool[near[0]]) == 1:
            full, abbr = pool[near[0]][0]
            print(f"NOTE: read {name!r} as {canon.get(full, full)!r}.", file=sys.stderr)
            return canon.get(full, name), abbr
    if len(candidates) > 1:
        where = f"on {'/'.join(sorted(teams))}" if teams else "league-wide"
        print(f"NOTE: '{name}' matches {len(candidates)} players {where} -- "
              f"left unresolved rather than guessed.", file=sys.stderr)
    return name, ""


def read_prop_leg(text, teams, roster=None):
    """One free-form bullet leg -> a leg dict, or None if it reads as nothing.

    Deliberately generous: anything it can't classify still becomes a leg with
    an unknown market, which the page shows and leaves ungraded. The only way
    to produce None is an empty line.
    """
    roster = roster if roster is not None else ROSTER_EXTRAS
    text = text.strip()
    if not text:
        return None
    leg = {"odds": None}
    market = line = side = None

    n_plus = N_PLUS_RE.search(text)
    if n_plus:
        line = float(n_plus.group(1)) - 0.5       # "2+ hits" is over 1.5
        side = "over"
    for key, rx in CARD_MARKET_RE:
        if rx.search(text):
            market = key
            break

    # An inning-specific total ("Over 1.5 runs in 6th inning") is a real bet
    # nobody here can grade: the page reads a FINAL score, not a per-inning
    # one. Named as its own market so it shows honestly rather than being
    # mistaken for the game total.
    # A total over part of a game -- "First 5 innings Red Sox vs Cubs under
    # 3.5". Gradeable off the linescore's per-inning runs; the reason it
    # wasn't for a while is that I assumed only the final score was available
    # and never checked.
    partial = re.search(r"\bfirst\s+(\d+)\s+innings?\b", text, re.IGNORECASE)
    inning_only = re.search(r"\b(\d)(?:st|nd|rd|th)\s+inning\b", text, re.IGNORECASE)
    if partial or inning_only:
        # The over/under scan below hasn't run yet at this point, so do it
        # here -- without it the line was silently dropped and every partial
        # total graded against the default 0.5.
        if line is None:
            ou_here = OVER_UNDER_RE.search(text)
            if ou_here:
                side = "under" if (ou_here.group(1) or ou_here.group(2) or "o").lower().startswith("u") else "over"
                line = float(ou_here.group(3))
        words = roster.get("abbr_by_team_word") or {}
        # Both team names, in the order the card WRITES them, matched
        # longest-first so "White Sox" wins over a bare "Sox".
        hits_ = []
        for word in sorted(words, key=len, reverse=True):
            m = re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE)
            if m and all(not (m.start() < e and s_ < m.end()) for s_, e in [(h[0], h[1]) for h in hits_]):
                hits_.append((m.start(), m.end(), words[word]))
        named = []
        for _, _, abbr in sorted(hits_):
            if abbr not in named:
                named.append(abbr)
        leg.update({"player": "" if named else text, "team": named[0] if named else "",
                    "market": "f5" if partial else "partial game"})
        if len(named) > 1:
            # Kept so the page can refuse a matchup that isn't real: the card
            # can name two teams that aren't playing each other.
            leg["opponent"] = named[1]
        if partial:
            leg["innings"] = int(partial.group(1))
        if line is not None:
            leg["line"] = line
        if side and side != "over":
            leg["side"] = side
        if not named:
            leg["player"] = text
        return leg

    generic, g_line, g_side, cleaned = detect_market(text)
    if market is None and generic:
        market, cleaned_text = generic, cleaned
        if line is None:
            line, side = g_line, g_side
    else:
        cleaned_text = text
        for key, rx in CARD_MARKET_RE:
            if key == market:
                cleaned_text = rx.sub(" ", cleaned_text, count=1)
                break
        # The card's own market table matched, so detect_market() never ran --
        # and with it went the over/under scan. Without this the line was
        # dropped AND "Over 1.5" stayed glued to the player's name, which then
        # resolved to nobody ("Mex Fried Over 1.5").
        if line is None:
            ou_here = OVER_UNDER_RE.search(cleaned_text)
            if ou_here:
                raw_side = (ou_here.group(1) or ou_here.group(2) or "o").lower()
                side = "under" if raw_side.startswith("u") else "over"
                line = float(ou_here.group(3))
                cleaned_text = OVER_UNDER_RE.sub(" ", cleaned_text, count=1)
    if n_plus:
        cleaned_text = N_PLUS_RE.sub(" ", cleaned_text, count=1)
    cleaned_text = re.sub(r"\s{2,}", " ", cleaned_text).strip(" -|:")

    # A team bet: whatever text is left names one of the teams.
    words = roster.get("abbr_by_team_word") or {}
    # "Yankees -1.5 RL" -- RL/run line is just how a spread is written. Lift it
    # out before the team lookup or the team never matches.
    rl = re.search(r"\b(?:RL|run\s*line)\b", cleaned_text, re.IGNORECASE)
    if rl:
        cleaned_text = re.sub(r"\s*\b(?:RL|run\s*line)\b\s*", " ", cleaned_text, count=1,
                              flags=re.IGNORECASE).strip()
        if market is None:
            market = "spread"
    if market == "spread" and line is None:
        bare = re.search(r"([+-]\d+(?:\.\d+)?)\s*$", cleaned_text)
        if bare:
            line = float(bare.group(1))
            cleaned_text = cleaned_text[:bare.start()].strip()
    # A team bet often carries a qualifier the market alias doesn't eat:
    # "White Sox over 5.5 Total Runs" leaves "White Sox Total" once "Runs" is
    # lifted, and the stray word stops the team ever matching. Only tried when
    # the text ISN'T already a team name, so a real club called e.g. "Team X"
    # could never be damaged by it.
    if normalize_name(cleaned_text) not in words:
        stripped = re.sub(r"\b(?:total|totals|team|game)\b", " ", cleaned_text, flags=re.IGNORECASE)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        if stripped and team_abbr_for(stripped, words):
            cleaned_text = stripped
    team_abbr = team_abbr_for(cleaned_text, words)
    # "White Sox over 5.5 Total Runs" is a TEAM total, not a player's runs
    # prop -- the leftover text names a team, so the counting alias that
    # matched ("runs") belongs to the game, not a batter.
    if team_abbr and market in ("runs", "hits", "tb", "hr", None) and line is not None:
        market = "total"
    if team_abbr and market in ("ml", "spread", "total"):
        leg.update({"player": "", "team": team_abbr, "market": market})
        if line is not None:
            leg["line"] = line
        if side and side != "over":
            leg["side"] = side
        return leg
    if market in ("ml", "spread", "total") and not team_abbr:
        # Named as a game bet but we can't tell WHICH team, so it can't be
        # graded. Shown with its own text rather than filed under a player.
        leg.update({"player": text, "team": "", "market": market})
        if line is not None:
            leg["line"] = line
        return leg

    # Two TEAMS joined by "/" or "+" is the GAME total -- "White Sox/Astros
    # Over 7.5" is one number for the whole game, not a bet on two teams. It
    # has to be checked before the combined-PLAYER split below, which would
    # otherwise read them as two people.
    sides = [x.strip() for x in re.split(r"\s*[/+]\s*", cleaned_text) if x.strip()]
    if len(sides) == 2 and line is not None:
        pair = [team_abbr_for(x, words) for x in sides]
        if all(pair):
            leg.update({"player": "", "team": pair[0], "opponent": pair[1], "market": "total",
                        "line": line})
            if side and side != "over":
                leg["side"] = side
            return leg

    # One or two players, "/"-separated.
    names = [n.strip() for n in re.split(r"\s*/\s*", cleaned_text) if n.strip()]
    resolved = [resolve_in_teams(n, teams, roster) for n in names] if names else []
    if not resolved:
        leg.update({"player": cleaned_text or text, "team": "", "market": market or "unknown"})
        return leg
    leg["player"] = resolved[0][0]
    leg["team"] = resolved[0][1]
    if len(resolved) > 1:
        # Graded as the combined total across both names, which is what the
        # bet means. The page sums them.
        leg["players"] = [r[0] for r in resolved]
    if market and market != "hr":
        leg["market"] = market
    elif market is None:
        leg["market"] = "unknown"
    if line is not None:
        leg["line"] = line
    if side and side != "over":
        leg["side"] = side
    return leg


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


# ---- markets beyond home runs and steals (2026-09-29) -------------------
# The page carries a market REGISTRY, and anything it doesn't recognise is
# shown but not graded rather than silently treated as a home run. The parser's
# job is therefore to report what the card SAID, not to decide what's
# gradeable: an unknown market word is passed through as-is, and the page
# handles it. That is the whole reason this can be tolerant.
#
# No real card has arrived yet using any of these, so the phrasings below are
# the common ones rather than anything observed. A leg nothing matches still
# goes through BETLIKE_RE and surfaces in the note -- never dropped in silence.
# ORDER MATTERS, most specific first. "Home Runs" contains the word "Runs",
# so a `runs` alias checked earlier claims it and a home run prop grades as a
# runs prop -- which is exactly what happened to the real steals fixture the
# first time this table was written. Same class of collision as the
# PARLAY_HEADER_RE trap: a later pattern must not match a line an earlier one
# owns. Add new markets in specificity order, never alphabetically.
MARKET_ALIASES = [
    ("hrr",     r"h\s*\+\s*r\s*\+\s*rbi|hits?\s*\+\s*runs?\s*\+\s*rbis?"),
    # BEFORE "runs": "earned runs" contains the word, and an alias table is
    # read in order. Same trap "Home Runs" hit.
    ("er",      r"\bER\b|earned\s+runs?"),
    # A PITCHER's win, which is not the same bet as a team moneyline. Only the
    # explicit phrasings, so "Yankees to win" stays a moneyline.
    ("win",     r"to\s+get\s+the\s+win|\bpitcher\s+win\b|\bfor\s+the\s+win\b"),
    ("hr",      r"home\s+runs?|total\s+homers?|\bhr\b|to\s+go\s+deep"),
    ("tb",      r"total\s+bases|tot\s*bases|\bTB\b"),
    ("sb",      r"\bsb\b|stolen\s+bases?|steals?"),
    ("doubles", r"\bdoubles?\b"),
    ("k",       r"strikeouts?\b"),
    ("hits",    r"\bhits?\b"),
    ("rbi",     r"\brbis?\b"),
    ("runs",    r"\bruns?\s+scored\b|\bruns?\b"),
    ("ml",      r"\bml\b|money\s*line|to\s+win(?:\s+the\s+game)?"),
    ("spread",  r"run\s*line|\bspread\b"),
    ("total",   r"\btotal\b|\bo/u\b|over\s*/\s*under"),
]
MARKET_ALIAS_RE = [(k, re.compile(pat, re.IGNORECASE)) for k, pat in MARKET_ALIASES]

# "Over 1.5" / "O1.5" / "U8.5" / "+1.5" / "-1.5"
OVER_UNDER_RE = re.compile(r"\b(?:(over|under)|([ou]))\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
SPREAD_NUM_RE = re.compile(r"(?<![\w.])([+-]\d+(?:\.\d+)?)(?![\d])")
# Anything that looks like it names a market we don't have an alias for --
# "Strikeouts O5.5", "Doubles Over 0.5". Captured so the page can show it,
# untracked, instead of the leg quietly grading as a home run.
UNKNOWN_MARKET_RE = re.compile(
    r"\b((?:[A-Za-z][A-Za-z+]*\s+){0,1}[A-Za-z][A-Za-z+]*)\s*"
    r"(?:\b(?:over|under)\b|\b[OU])\s*\d+(?:\.\d+)?\b", re.IGNORECASE)


def detect_market(text):
    """What market does this leg text name, and at what line?

    -> (market_key or None, line or None, side or None, text with the market
    phrase removed). None for the key means "say nothing", which leaves the
    leg exactly as it has always been: a home run bet with no market field.
    """
    if not text:
        return None, None, None, text
    key = None
    for k, rx in MARKET_ALIAS_RE:
        if rx.search(text):
            key = k
            break
    line = side = None
    ou = OVER_UNDER_RE.search(text)
    if ou:
        side = (ou.group(1) or ou.group(2) or "o").lower()
        side = "under" if side.startswith("u") else "over"
        line = float(ou.group(3))
    if key is None:
        unknown = UNKNOWN_MARKET_RE.search(text)
        if unknown:
            raw = re.sub(r"\s+", " ", unknown.group(1)).strip().lower()
            # Don't let a player's own name become a "market": only accept it
            # when an over/under number sat right behind it.
            if raw and ou:
                key = raw
    if key == "spread" and line is None:
        sp = SPREAD_NUM_RE.search(text)
        if sp:
            line = float(sp.group(1))
    elif key is None and side is None:
        # "Yankees -1.5" -- a team and a signed number, nothing else. Narrow on
        # purpose: the whole line has to look like exactly that, so a stray
        # "+390" price can never be read as a run line.
        bare = re.match(r"^\s*([A-Za-z][A-Za-z .'-]{1,24}?)\s*([+-]\d+(?:\.\d+)?)\s*$", text)
        if bare and "." in bare.group(2):
            key, line = "spread", float(bare.group(2))
            return key, line, None, bare.group(1).strip()
    if key is None and side is not None:
        stripped = OVER_UNDER_RE.sub(" ", text, count=1).strip(" -|:")
        if not stripped:
            key = "total"
    cleaned = text
    if key:
        for k, rx in MARKET_ALIAS_RE:
            if k == key:
                cleaned = rx.sub(" ", cleaned, count=1)
                break
    if ou:
        cleaned = OVER_UNDER_RE.sub(" ", cleaned, count=1)
    cleaned = re.sub(r"\s*[-|:]\s*$", "", re.sub(r"\s{2,}", " ", cleaned).strip())
    return key, line, side, cleaned


def market_fields(key, line, side):
    """The leg fields a detected market contributes. Home runs contribute
    NOTHING, so a home-run-only card's tickets.json is byte-for-byte what it
    has always been -- the same guarantee steals were added under."""
    out = {}
    if key and key != "hr":
        out["market"] = key
    if line is not None:
        out["line"] = line
    if side and side != "over":
        out["side"] = side
    return out


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


# The whole roster file, for the lookups that aren't keyed by player name
# (team words, surnames). load_roster() keeps returning its two maps so every
# existing caller -- and parse()'s signature -- is untouched.
ROSTER_EXTRAS = {}


# ---- first pitch times, from MLB rather than from the card --------------
# Cards increasingly don't state a time at all (the 2026-09-29 one states
# none), and a pick with no time reads on the page as if nobody knows when
# it's on. MLB does know, and the slate date is already established, so the
# schedule fills the blanks.
#
# Only ever FILLS a blank: a time the card stated is left exactly as written.
# Overriding what a person typed -- silently, from a different source -- is a
# worse failure than a stale time, and a card sometimes means something
# specific by the time it gives.
#
# Network, so it fails SOFT: unreachable MLB means times stay blank and the
# slate still posts, which is the same bargain every other fetch here makes.
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&hydrate=team&date="
# Filled in as a side effect of fetch_start_times(), which reads the same
# payload -- one request answers both questions.
_SCHEDULE_OPPONENTS = {}


def fetch_start_times(date, fetcher=None):
    """-> {TEAM ABBR: "7:05 PM ET"} for one slate date, or {} if unavailable.

    A doubleheader gives a team two games; the EARLIER one wins, because a
    card that doesn't say which game it means almost certainly means the one
    that starts first. Which game a leg actually refers to is not knowable
    from the card, and nothing here pretends otherwise.
    """
    try:
        if fetcher is not None:
            data = fetcher(MLB_SCHEDULE + date)
        else:
            import urllib.request
            with urllib.request.urlopen(MLB_SCHEDULE + date, timeout=20) as res:
                data = json.loads(res.read())
    except Exception as e:                      # noqa: BLE001 -- soft on purpose
        print(f"NOTE: couldn't read MLB's schedule for {date} ({e}) -- "
              f"times the card didn't state will stay blank.", file=sys.stderr)
        return {}

    out = {}
    for day in data.get("dates") or []:
        for game in day.get("games") or []:
            iso = game.get("gameDate")
            if not iso:
                continue
            # A game with no announced time still carries a placeholder
            # gameDate. Filling from that would print an invented first pitch,
            # which is worse than printing none.
            if (game.get("status") or {}).get("startTimeTBD"):
                continue
            try:
                when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
            except ValueError:
                continue
            label = when.strftime("%-I:%M %p ET") if os.name != "nt" else when.strftime("%#I:%M %p ET")
            for side in ("away", "home"):
                abbr = (((game.get("teams") or {}).get(side) or {}).get("team") or {}).get("abbreviation")
                if not abbr:
                    continue
                other = "home" if side == "away" else "away"
                opp = (((game.get("teams") or {}).get(other) or {}).get("team") or {}).get("abbreviation")
                prev = out.get(abbr)
                if prev is None or when < prev[1]:
                    out[abbr] = (label, when, opp or "")
    _SCHEDULE_OPPONENTS[date] = {a: v[2] for a, v in out.items() if v[2]}
    return {a: v[0] for a, v in out.items()}


def fetch_opponents(date, fetcher=None):
    """-> {TEAM ABBR: OPPONENT ABBR} for one slate date, or {} if unavailable.

    Shares fetch_start_times()'s work because it reads the same payload; kept
    separate so a caller that only wants times isn't handed a second map it
    has to think about.
    """
    return _SCHEDULE_OPPONENTS.get(date, {})


def flag_matchups(windows, opponents):
    """Mark any leg naming a matchup the schedule contradicts. -> how many.

    A card can name two teams that aren't playing each other -- the
    2026-09-29 card said "Red Sox vs Cubs" on a night BOS played NYY and CHC
    played SD. The page refuses to grade that (grading against whichever team
    resolved would answer a question nobody asked), so the reason is recorded
    here and surfaced in the note, rather than leaving a bare "not tracked".
    """
    if not opponents:
        return 0
    flagged = 0
    for win in windows:
        for card_ in win["tickets"]:
            for lg in card_["legs"]:
                want, team = lg.get("opponent"), lg.get("team")
                if want and team and opponents.get(team) and opponents[team] != want:
                    lg["mismatch"] = f"{team} played {opponents[team]}, not {want}"
                    flagged += 1
    return flagged


def fill_missing_times(windows, singles, date, fetcher=None):
    """Fill in any leg or single whose time the card never gave. -> how many."""
    blanks = [l for w in windows for c in w["tickets"] for l in c["legs"] if not l.get("time")]
    blanks += [s for s in singles if not s.get("time")]
    if not blanks:
        return 0
    times = fetch_start_times(date, fetcher)
    if not times:
        return 0
    filled = 0
    for item in blanks:
        when = times.get((item.get("team") or "").upper())
        if not when:
            continue
        item["time"] = when
        # A SINGLE displays its time through the prebuilt `meta` string, which
        # was assembled before this ran -- so setting `time` alone would fill
        # it everywhere except the one place it's read. Splice it in ahead of
        # the stake, matching how meta is built.
        if "meta" in item and when not in (item["meta"] or ""):
            parts = [x for x in (item["meta"] or "").split(" &middot; ") if x]
            stake_at = next((i for i, x in enumerate(parts) if x.startswith("$")), len(parts))
            parts.insert(stake_at, when)
            item["meta"] = " &middot; ".join(parts)
        filled += 1
    return filled


def load_roster():
    if not ROSTER_PATH.exists():
        return {}, {}
    data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    ROSTER_EXTRAS.clear()
    ROSTER_EXTRAS.update(data)
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
    # "Parlay" but is a ticket header, not a section header. And a THIRD time
    # for "Ticket #1 - 2-Leg Parlay $6.00" (TRACKER_TICKET_RE): the literal
    # "2-Leg Parlay" inside it is a PARLAY_HEADER_RE match. And a FOURTH time
    # for "🎟️ Ticket 1 (6.00 bet pays 198.00)" (EMOJI_STAKE_TICKET_START_RE):
    # it doesn't contain "Parlay" at all, but ODDS_RE below is happy to match
    # its own header form's odds group and would wrongly reject it as a
    # ticket header candidate if it ever gained a "(+NNN)" -- it doesn't
    # today, so no change needed there, but the exclusion is listed here
    # regardless. Every new ticket header that names its own leg count, or
    # otherwise coincidentally matches an existing section-header pattern,
    # lands here; assume the next one will too and add it to this guard
    # before anything else.
    if (CARD_HEADER_RE.match(text) or ODDS_RE.search(text) or BET_FOOT_RE.search(text)
            or EMOJI_TICKET_START_RE.match(text) or PARLAY_START_RE.match(text)
            or TRACKER_TICKET_RE.match(text) or EMOJI_STAKE_TICKET_START_RE.match(text)
            or BARE_TICKET_RE.match(text)):
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
    # ---- state for the "DAILY HOME RUN PARLAY TRACKER" template ----
    # The last bare capitalised word seen. Only consumed when a Ticket header
    # follows it, so it can never affect any other card shape.
    pending_bettor = None

    # ---- state for the single-game prop card (see GAME_HEADER_RE) ----
    current_prop = None      # {"_num": n, "_legs": [leg dicts], "_stake", "_pp"}
    prop_teams = set()       # the two teams the card's header named
    prop_time = ""           # the one start time it gave

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

    def flush_prop():
        nonlocal current_prop
        if current_prop is None:
            return
        legs = current_prop["_legs"]
        if legs:
            finalize_prop(current_prop)
        current_prop = None

    def finalize_prop(ticket):
        legs = []
        for i, leg in enumerate(ticket["_legs"]):
            out = dict(leg)
            out["id"] = f"prop-{ticket['_num']}-{i}"
            # This card names no bettor, unless its footer named an owner.
            out["who"] = ticket.get("_who") or ""
            out["time"] = prop_time
            out["meta"] = out.get("team") or ""
            legs.append(out)
        if len(legs) == 1:
            one = legs[0]
            singles.append({
                "who": ticket.get("_who") or "", "player": one.get("player", ""), "team": one.get("team", ""),
                "odds": one.get("odds"), "market": one.get("market"),
                "matchup": "", "time": prop_time,
                "stake": ticket["_stake"], "pp": ticket["_pp"],
                "_extra": {k: one[k] for k in ("line", "side", "players", "text") if k in one},
            })
            return
        card = {"name": f'Ticket {ticket["_num"]}', "sub": "", "tag": None,
                "_stake": ticket["_stake"], "_book": ticket.get("_who") or "", "_origPayout": ticket["_pp"],
                "_legs": legs, "_prebuilt": True}
        ticket_window(last_header_title)["tickets"].append(card)

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

        if BARE_NAME_RE.match(original):
            # Remembered, never acted on here -- see BARE_NAME_RE's note. No
            # `continue`: the line still falls through exactly as before.
            pending_bettor = original

        tracker_ticket = TRACKER_TICKET_RE.match(line)
        if tracker_ticket:
            flush_card()
            flush_ticket()
            flush_parlay()
            ticket_sb = sb_here
            num, stake_s = tracker_ticket.groups()
            if pending_bettor:
                last_header_title = pending_bettor  # one window per bettor
            current_parlay = {"_num": num, "_who": pending_bettor or "",
                              "_legs": [], "_stake": clean_num(stake_s), "_pp": None}
            continue

        if current_parlay is not None:
            tracker_payout = TRACKER_PAYOUT_RE.match(line)
            if tracker_payout:
                amt = tracker_payout.group(1)
                current_parlay["_pp"] = clean_num(amt) if amt else None
                flush_parlay()
                continue
            plain_foot = PLAIN_PARLAY_FOOT_RE.match(line)
            if plain_foot:
                stake, pp, owner = plain_foot.groups()
                current_parlay["_stake"] = clean_num(stake)
                current_parlay["_pp"] = clean_num(pp)
                if owner:
                    current_parlay["_who"] = owner.strip()
                flush_parlay()
                continue
            plain_leg = PLAIN_PARLAY_LEG_RE.match(line)
            if plain_leg:
                player_raw, odds, _full_team, who, tail = plain_leg.groups()
                time_ = tail.strip()
                if time_ and not re.search(r"\bET\b", time_, re.IGNORECASE):
                    time_ += " ET"   # every other template's times say ET
                current_parlay["_legs"].append(
                    (time_, player_raw, who, odds, market_for(sb_here)))
                continue
            checkbox = CHECKBOX_LEG_RE.match(line)
            if checkbox:
                player_raw, odds, _team_nickname, tail = checkbox.groups()
                if "DNP" in tail.upper():
                    # An explicit scratch, not an unreadable line: the card
                    # says so itself, so it's dropped without a warning and
                    # the ticket grades on its remaining legs.
                    continue
                if not odds:
                    # A leg with no price can't be graded or paid out, and
                    # guessing one would be inventing money. Reported so the
                    # page's note names it, and left untracked.
                    unread.append(original)
                    continue
                time_ = tail.strip()
                if time_ and not re.search(r"\bET\b", time_, re.IGNORECASE):
                    time_ += " ET"   # every other template's times say ET
                current_parlay["_legs"].append(
                    (time_, player_raw, "", odds, market_for(sb_here)))
                continue

        game_header = GAME_HEADER_RE.match(original)
        if game_header and not current_prop:
            a, b, when = game_header.groups()
            words = ROSTER_EXTRAS.get("abbr_by_team_word") or {}
            ta, tb_ = words.get(normalize_name(a)), words.get(normalize_name(b))
            if ta and tb_:
                # Only treated as a game header when BOTH names really are
                # teams -- otherwise "Player vs Player" chatter would reset
                # the card's context.
                flush_card(); flush_ticket(); flush_parlay(); flush_prop()
                prop_teams = {ta, tb_}
                prop_time = (when or "").strip()
                if prop_time and not re.search(r"\bET\b", prop_time, re.IGNORECASE):
                    prop_time += " ET"
                last_header_title = f"{a.strip()} vs. {b.strip()}"
                current_section = None
                continue

        plain_ticket = PLAIN_TICKET_RE.match(line)
        if plain_ticket and prop_teams:
            # Gated on a game header having been seen: a bare "Ticket #1" is
            # otherwise ambiguous with templates that use the same words.
            flush_card(); flush_ticket(); flush_parlay(); flush_prop()
            current_prop = {"_num": plain_ticket.group(1), "_legs": [], "_stake": None, "_pp": None}
            continue

        bare_ticket = BARE_TICKET_RE.match(line)
        if bare_ticket and not prop_teams:
            # No game header at all for this template -- a bare "Ticket N"
            # opens a prop ticket with no team restriction on surnames.
            flush_card(); flush_ticket(); flush_parlay(); flush_prop()
            current_prop = {"_num": bare_ticket.group(1), "_legs": [], "_stake": None, "_pp": None}
            continue

        if current_prop is not None:
            pays = PAYS_FOOT_RE.match(original)
            if pays:
                current_prop["_stake"] = clean_num(pays.group(1))
                current_prop["_pp"] = clean_num(pays.group(2))
                flush_prop()
                continue
            wager_foot = PARLAY_FOOT_RE.match(line)
            if wager_foot:
                stake, pp = wager_foot.groups()
                current_prop["_stake"] = clean_num(stake)
                current_prop["_pp"] = clean_num(pp)
                flush_prop()
                continue
            stake_pays = TICKET_STAKE_PAYS_RE.match(original)
            if stake_pays:
                stake, pp, owner = stake_pays.groups()
                current_prop["_stake"] = clean_num(stake)
                current_prop["_pp"] = clean_num(pp)
                current_prop["_who"] = (owner or "").strip()
                flush_prop()
                continue
            stake_pays_combined = TICKET_STAKE_PAYS_COMBINED_RE.match(original)
            if stake_pays_combined:
                combined = clean_num(stake_pays_combined.group(1))
                current_prop["_stake"] = combined
                current_prop["_pp"] = combined
                flush_prop()
                continue
            hyphen_leg = HYPHEN_TICKET_LEG_RE.match(original)
            if hyphen_leg:
                subject, desc = hyphen_leg.groups()
                leg = read_prop_leg(f"{subject} {desc}", prop_teams)
                if leg:
                    current_prop["_legs"].append(leg)
                continue
            bullet = BULLET_PROP_RE.match(original)
            if bullet:
                leg = read_prop_leg(bullet.group(1), prop_teams)
                if leg:
                    current_prop["_legs"].append(leg)
                continue
            if (not BARE_TICKET_RE.match(original) and not PLAIN_TICKET_RE.match(original)
                    and not GAME_HEADER_RE.match(original)):
                bare_line = BARE_PROP_LINE_RE.match(original)
                if bare_line:
                    # ---- thirteenth template (first seen 2026-10-04) ----
                    # Same bare "Ticket N" header as the tenth and twelfth,
                    # but the PRICE sits at the end of each leg line with no
                    # brackets and no bullet:
                    #     Barelon Allen Anytime TD +130
                    #     Mookie Betts 2+ TB 145
                    #     $8 Pays 81.87
                    # read_prop_leg() has never looked for odds (templates ten
                    # and twelve carry none), so every leg came back
                    # odds=None AND kept the digits glued to the name --
                    # "Jake Bauers +460" resolves to nobody, which is the
                    # Tatis Jr. failure mode: no team, never grades either way.
                    # Taking the price off FIRST fixes both at once.
                    body, trailing_odds = split_trailing_odds(bare_line.group(1))
                    leg = read_prop_leg(body, prop_teams)
                    if leg:
                        if trailing_odds and not leg.get("odds"):
                            leg["odds"] = trailing_odds
                        current_prop["_legs"].append(leg)
                    continue

        plain_start = PLAIN_PARLAY_START_RE.match(line)
        if plain_start:
            flush_card()
            flush_ticket()
            flush_parlay()
            ticket_sb = sb_here
            # No bettor on the header -- the footer names the owner, so _who
            # is filled in when that line is reached.
            current_parlay = {"_num": plain_start.group(1), "_who": "",
                              "_legs": [], "_stake": None, "_pp": None}
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

        emoji_stake_start = EMOJI_STAKE_TICKET_START_RE.match(line)
        if emoji_stake_start:
            flush_ticket()
            ticket_sb = sb_here
            num, stake, pp = emoji_stake_start.groups()
            current_ticket = {"_num": num, "_legs": [], "_book": None,
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
            stake_leg = EMOJI_STAKE_LEG_RE.match(line)
            if stake_leg:
                player_raw, odds, team_raw, who, time_ = stake_leg.groups()
                current_ticket["_legs"].append((time_, player_raw, team_raw, odds, who, market_for(sb_here)))
                continue
            leg = TICKET_LEG_RE.match(line)
            if leg:
                current_ticket["_legs"].append(leg.groups() + (market_for(sb_here),))
                continue
            prop_leg = TICKET_PROP_LEG_RE.match(original)
            if prop_leg:
                who, player_raw, market_word, threshold, odds, matchup, time_ = prop_leg.groups()
                mkt, _line, _side, _ = detect_market(market_word)
                mkt = mkt or "hr"
                if threshold and float(threshold) != 0.5 and mkt in ("hr", "sb"):
                    # HR and SB are "at least one" markets and carry no line,
                    # so an over-1.5 on them really can't be graded properly.
                    # Every other market DOES carry its line through, so this
                    # warning no longer applies to them.
                    print(f"NOTE: '{original}' is an over-{threshold} bet; the tracker only knows 'at least one' -- "
                          f"it will be marked a hit on the FIRST one.", file=sys.stderr)
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
    flush_prop()

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
                # Join only the parts that exist. A card that names no bettor
                # (the 2026-09-29 single-game card) otherwise rendered
                # "PHI &middot; " with a dangling separator on every leg.
                meta = " &middot; ".join(x for x in (leg["team"], leg["who"]) if x)
                legs.append({
                    "id": f"p{si}-c{ci}-l{li}",
                    "player": leg["player"],
                    "team": leg["team"],
                    "who": leg["who"],
                    "meta": meta,
                    "odds": leg["odds"],
                    "time": leg["time"],
                })
                # Carry EVERY market field, not just "sb". Until 2026-09-29
                # only steals survived this loop, so a prop's market, its line
                # and a combined leg's second player were all silently dropped
                # on the way out -- the leg then rendered as a plain home run
                # bet. A home run leg still contributes nothing, which is what
                # keeps a home-run-only card byte-for-byte unchanged.
                for k in ("market", "line", "side", "players", "text", "opponent",
                          "innings", "mismatch"):
                    if leg.get(k) is not None and not (k == "market" and leg[k] == "hr"):
                        legs[-1][k] = leg[k]
            tag_html = f' &middot; {card["tag"]}' if card.get("tag") else ""
            sub_html = f' &middot; {card["sub"]}' if card.get("sub") else ""
            if card["_origPayout"] is None:
                payout_str = "TBD"
            else:
                payout_str = f'${card["_origPayout"]:,.2f}'
            # Not every template names a ticket OWNER. The "SOLAR KEYS" card
            # has none -- each leg carries its own bettor instead -- and
            # interpolating that straight in printed "bet by None" on the live
            # page. The schema check can't see this: it validates types and
            # never reads the prebuilt foot string. So the phrase is dropped
            # entirely when there's nobody to name, rather than rendering a
            # placeholder, and book is "" rather than None.
            book = (card["_book"] or "").strip()
            by_html = f' bet by {book}' if book else ' bet'
            # A ticket whose footer no pattern read has NO stake, and
            # f"${None:.2f}" is a TypeError that kills the whole run -- every
            # other ticket on the card included. That is strictly worse than
            # the silent-drop this file works so hard to avoid: the slate
            # doesn't post at all. One card wrote "$8 pay 255.36" (no "s")
            # and took down all twelve of its tickets.
            #
            # So the stake is omitted from the printed foot when it's unknown,
            # the same way a missing OWNER is omitted rather than printed as
            # "bet by None". `stake` itself stays null for the page to read.
            stake_html = f'<b>${card["_stake"]:.2f}</b>{by_html}' if card["_stake"] is not None \
                else (f'Bet by {book}' if book else 'Bet')
            foot = (f'{stake_html} '
                    f'&middot; Potential payout <b>{payout_str}</b>')
            out_tickets.append({
                "name": f'{card["name"]}{sub_html}{tag_html}',
                "sub": f'{len(legs)}-Leg',
                "foot": foot,
                "stake": card["_stake"],
                "book": book,
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
        # Same as the parlay legs above: every market field rides through,
        # and a home run single still contributes nothing.
        if s.get("market") and s["market"] != "hr":
            out_singles[-1]["market"] = s["market"]
        for k, v in (s.get("_extra") or {}).items():
            if v is not None:
                out_singles[-1][k] = v

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
    # The slate date belongs to when the card was UPLOADED, not to whenever
    # the parser happens to run. Normally those are the same thing -- the
    # workflow fires on the commit -- but a re-run hours later would decide
    # differently, because "posted after the last first pitch" means tomorrow.
    # That window is knife-edge for a SINGLE-GAME card: the 2026-09-29 card
    # had one 2:00 PM game and was uploaded at 1:58 PM, so a re-parse at 2:14
    # dated it tomorrow. --now makes re-parsing an earlier upload reproducible.
    now = datetime.now(ET)
    if "--now" in sys.argv:
        raw = sys.argv[sys.argv.index("--now") + 1]
        now = datetime.fromisoformat(raw)
        if now.tzinfo is None:
            now = now.replace(tzinfo=ET)
        print(f"Dating the slate as of {now.isoformat()} (--now)", file=sys.stderr)
    slate_date = slate_date_for(all_times, now)

    # Fill in first pitches the card never gave. After the slate date is
    # settled, because that's the date whose schedule to ask for.
    filled = fill_missing_times(windows, out_singles, slate_date)
    # A card can name a matchup that isn't on tonight's schedule -- the
    # 2026-09-29 card said "Red Sox vs Cubs" on a night BOS played NYY and CHC
    # played SD. The page refuses to grade it (grading against whichever team
    # resolved would answer a question nobody asked), so say WHY rather than
    # leaving a bare "not tracked".
    flag_matchups(windows, fetch_opponents(slate_date))
    if filled:
        print(f"Filled in {filled} start time(s) from MLB's schedule for {slate_date}.", file=sys.stderr)

    # A bet line nothing understood means a bet that is NOT being tracked. The
    # upload still posts (better most of a slate than none), but the page's note
    # line says so where the group will see it, instead of only an Actions log.
    # A leg that PARSED but can't be graded is the quiet failure mode: the
    # slate posts, the page shows the leg marked "not tracked", and nobody is
    # told. Real card, 2026-09-29: six of seventeen legs were untracked or
    # unresolved and the note was empty. The page already says it per-leg;
    # this is what puts it where the group actually looks.
    KNOWN = {"hr", "sb", "hrr", "hits", "rbi", "runs", "tb", "doubles", "k",
             "ml", "spread", "total", "f5", "er", "win"}
    ungradeable = []
    for win in windows:
        for card_ in win["tickets"]:
            for lg in card_["legs"]:
                mk = lg.get("market")
                if lg.get("mismatch"):
                    ungradeable.append(f"{lg.get('team')} {mk or ''} — {lg['mismatch']}".strip())
                elif mk is not None and mk not in KNOWN:
                    ungradeable.append(f"{lg.get('player') or lg.get('team') or '?'} ({mk})")
                elif mk in (None, "hr", "sb", "hrr", "hits", "rbi", "runs", "tb", "doubles", "k") \
                        and not lg.get("team"):
                    ungradeable.append(f"{lg.get('player') or '?'} (couldn't match a player)")
    for sg in out_singles:
        mk = sg.get("market")
        if mk is not None and mk not in KNOWN:
            ungradeable.append(f"{sg.get('player') or '?'} ({mk})")
        elif not sg.get("team") and mk not in ("ml", "spread", "total"):
            ungradeable.append(f"{sg.get('player') or '?'} (couldn't match a player)")

    unread = getattr(parse, "unread", [])
    note = ""
    if ungradeable and not unread:
        for u in ungradeable:
            print(f"NOTE: not being tracked: {u}", file=sys.stderr)
        note = (f"&#9888; {len(ungradeable)} leg{'s' if len(ungradeable) != 1 else ''} "
                f"can't be tracked live &mdash; {'they show' if len(ungradeable) != 1 else 'it shows'} "
                f"on the card but won't be graded: &ldquo;{ungradeable[0]}&rdquo;"
                + (f" and {len(ungradeable) - 1} more" if len(ungradeable) > 1 else ""))
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