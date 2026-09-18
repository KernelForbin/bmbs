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
PARLAY_HEADER_RE = re.compile(r"\d+-Leg Parlay", re.IGNORECASE)
CARD_HEADER_RE = re.compile(r"^Card\s+(\d+)\s*:\s*(.+)$")
BET_FOOT_RE = re.compile(
    r"Bet by\s+([A-Za-z]+)\s*:\s*\$([\d,.]+)\s*\|\s*PP:\s*\$([\d,.]+)", re.IGNORECASE
)
BULLET = r"^(?:[*\-•]\s*)?"
SINGLE_LINE_RE = re.compile(
    BULLET + r"([A-Za-z]+)\s*:\s*(.+?)\s*\(\+(\d+)\)\s*\|\s*"
    r"(?:\(([^)]+)\)\s*)?([\d: ]*[AP]M ET)?\s*[•·]?\s*\$([\d,.]+)\s*bet\s*\|\s*PP:\s*\$([\d,.]+)",
    re.IGNORECASE
)
LEG_LINE_RE = re.compile(BULLET + r"(.+?)\s*\(\+(\d+)\)\s*\|\s*(.+?)\s*\(([^)]+)\)\s*$")
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M\s*ET", re.IGNORECASE)
ODDS_RE = re.compile(r"\(\+\d+\)")


def normalize_name(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    return " ".join(name.split()).strip().lower()


def clean_num(s):
    return float(s.replace(",", ""))


def load_roster():
    if not ROSTER_PATH.exists():
        return {}, {}
    data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    return data.get("team_by_name", {}), data.get("canonical_name_by_norm", {})


def section_header(line):
    """Header text if `line` is a section header (with or without '##'), else None."""
    text = line.lstrip("#").strip()
    # "Card 11: Mega Longshot Wager" would otherwise read as a singles header.
    if CARD_HEADER_RE.match(text) or ODDS_RE.search(text) or BET_FOOT_RE.search(text):
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

    def flush_card():
        nonlocal current_card
        if current_card and current_card["_legs"]:
            windows_append_card(current_card)
        current_card = None

    def windows_append_card(card):
        current_section["tickets"].append(card)

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("---"):
            continue

        header_text = section_header(line)
        if header_text is not None:
            flush_card()
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

        if mode == "parlay":
            card_match = CARD_HEADER_RE.match(line)
            if card_match:
                flush_card()
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
                leg = {"player": canon_name, "odds": f"+{odds}", "who": clean_who}
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
                    "odds": f"+{odds}",
                    "matchup": (matchup or "").strip(),
                    "time": (time_ or "").strip(),
                    "stake": clean_num(stake),
                    "pp": clean_num(pp),
                })
                single_idx += 1
                continue

    flush_card()

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
            tag_html = f' &middot; {card["tag"]}' if card.get("tag") else ""
            foot = (f'<b>${card["_stake"]:.2f}</b> bet by {card["_book"]} '
                    f'&middot; Potential payout <b>${card["_origPayout"]:,.2f}</b>')
            out_tickets.append({
                "name": f'{card["name"]} &middot; {card["sub"]}{tag_html}',
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
        out_singles.append({
            "id": f"single-{i}",
            "who": s["who"],
            "player": s["player"],
            "team": s["team"],
            "meta": " &middot; ".join(meta_parts),
            "odds": s["odds"],
            "stake": s["stake"],
            "payout": s["pp"],
            "pp": f'PP ${s["pp"]:,.2f}',
        })

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

    payload = {"date": slate_date, "note": "", "windows": windows, "singles": out_singles}
    TICKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    archive_previous_slate(slate_date)
    TICKETS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {TICKETS_PATH} (slate date: {slate_date} ET)")


if __name__ == "__main__":
    main()
