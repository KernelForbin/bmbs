#!/usr/bin/env python3
"""
Pulls today's MLB home runs from the free, key-less MLB Stats API
(statsapi.mlb.com) and writes data/marks.json: a leg_id -> state map
("hit" / "miss" / "pending") that the checklist page reads on load.

Run standalone:
    python scripts/fetch_home_runs.py

Designed to be run on a schedule (see .github/workflows/update-checklist.yml).
"""
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import urllib.request

ROOT = Path(__file__).resolve().parent.parent
TICKETS_PATH = ROOT / "data" / "tickets.json"
MARKS_PATH = ROOT / "data" / "marks.json"

ET = ZoneInfo("America/New_York")
API_BASE = "https://statsapi.mlb.com/api"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hr-checklist-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_name(name):
    """Strip accents/punctuation so 'Fernando Tatis Jr.' matches 'Fernando Tatis Jr'."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    return " ".join(name.split()).strip().lower()


def todays_game_pks(date_str):
    url = f"{API_BASE}/v1/schedule?sportId=1&date={date_str}"
    data = get_json(url)
    game_pks = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            game_pks.append({
                "gamePk": game["gamePk"],
                "status": game.get("status", {}).get("abstractGameState", ""),  # Preview/Live/Final
                "away": game["teams"]["away"]["team"].get("abbreviation", ""),
                "home": game["teams"]["home"]["team"].get("abbreviation", ""),
            })
    return game_pks


def game_snapshot(game_pk):
    """
    Returns a dict for one game, read from the live/final Gumbo feed:
      { "is_final": bool,
        "hr_names": set of normalized batter names who homered,
        "roster_names": set of normalized names of every player who
                         appeared in this game's boxscore (both teams) }

    Using the boxscore roster (not a hand-typed team code) is what lets us
    figure out which specific game a player was actually in tonight,
    regardless of what team they were labeled with in tickets.json.
    """
    url = f"{API_BASE}/v1.1/game/{game_pk}/feed/live"
    data = get_json(url)

    is_final = data.get("gameData", {}).get("status", {}).get("abstractGameState", "") == "Final"

    hr_names = set()
    plays = data.get("liveData", {}).get("plays", {}).get("allPlays", [])
    for play in plays:
        result = play.get("result", {})
        if result.get("eventType") == "home_run" or result.get("event") == "Home Run":
            batter = play.get("matchup", {}).get("batter", {}).get("fullName")
            if batter:
                hr_names.add(normalize_name(batter))

    roster_names = set()
    boxscore_teams = data.get("liveData", {}).get("boxscore", {}).get("teams", {})
    for side in ("away", "home"):
        players = boxscore_teams.get(side, {}).get("players", {})
        for _, pdata in players.items():
            full_name = pdata.get("person", {}).get("fullName")
            if full_name:
                roster_names.add(normalize_name(full_name))

    return {"is_final": is_final, "hr_names": hr_names, "roster_names": roster_names}


def main():
    if not TICKETS_PATH.exists():
        print(f"ERROR: {TICKETS_PATH} not found", file=sys.stderr)
        sys.exit(1)

    tickets = json.loads(TICKETS_PATH.read_text())

    # Flatten every leg (ticket legs + singles) into one list of {id, player, team}
    all_legs = []
    for window in tickets.get("windows", []):
        for ticket in window.get("tickets", []):
            for leg in ticket.get("legs", []):
                all_legs.append({"id": leg["id"], "player": leg["player"], "team": leg.get("team", "")})
    for single in tickets.get("singles", []):
        all_legs.append({"id": single["id"], "player": single["player"], "team": single.get("team", "")})

    today_et = datetime.now(ET).strftime("%Y-%m-%d")
    games = todays_game_pks(today_et)

    if not games:
        print(f"No MLB games found for {today_et}. Writing empty/pending marks.")
        marks = {leg["id"]: "pending" for leg in all_legs}
        write_marks(marks, today_et, note="no games found")
        return

    all_hr_names = set()          # every player who's homered today, across all games
    final_roster_names = set()    # players whose game is Final and did NOT homer today
    seen_roster_names = set()     # players who appeared in any boxscore today (any status)

    for g in games:
        try:
            snap = game_snapshot(g["gamePk"])
        except Exception as e:
            print(f"WARN: failed to fetch game {g['gamePk']}: {e}", file=sys.stderr)
            continue
        all_hr_names |= snap["hr_names"]
        seen_roster_names |= snap["roster_names"]
        if snap["is_final"]:
            final_roster_names |= (snap["roster_names"] - snap["hr_names"])

    marks = {}
    for leg in all_legs:
        norm = normalize_name(leg["player"])
        if norm in all_hr_names:
            marks[leg["id"]] = "hit"
        elif norm in final_roster_names:
            # this exact player appeared in a boxscore whose game is Final,
            # and did not homer
            marks[leg["id"]] = "miss"
        else:
            marks[leg["id"]] = "pending"
            if norm not in seen_roster_names:
                # didn't appear in any boxscore at all today — likely a name
                # mismatch (nickname/suffix) worth checking manually
                print(f"NOTE: '{leg['player']}' (id {leg['id']}) not found in any "
                      f"boxscore today. Check spelling against MLB roster name.")

    write_marks(marks, today_et)


def write_marks(marks, date_str, note=None):
    payload = {
        "date": date_str,
        "updated_at": datetime.now(ET).isoformat(),
        "marks": marks,
    }
    if note:
        payload["note"] = note
    MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKS_PATH.write_text(json.dumps(payload, indent=2))
    hits = sum(1 for v in marks.values() if v == "hit")
    misses = sum(1 for v in marks.values() if v == "miss")
    pending = sum(1 for v in marks.values() if v == "pending")
    print(f"Wrote {MARKS_PATH}: {hits} hit, {misses} miss, {pending} pending")


if __name__ == "__main__":
    main()
