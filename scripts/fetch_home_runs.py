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


def home_runs_and_status_for_game(game_pk):
    """
    Returns (set of normalized batter names who homered, is_final: bool)
    for one game, read from the live/final Gumbo feed.
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

    return hr_names, is_final


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

    all_hr_names = set()
    final_teams = set()   # teams whose game is Final
    started_teams = set() # teams whose game is Live or Final (i.e. not Preview)

    for g in games:
        try:
            hr_names, is_final = home_runs_and_status_for_game(g["gamePk"])
        except Exception as e:
            print(f"WARN: failed to fetch game {g['gamePk']}: {e}", file=sys.stderr)
            continue
        all_hr_names |= hr_names
        if g["status"] != "Preview":
            started_teams.add(g["away"])
            started_teams.add(g["home"])
        if is_final:
            final_teams.add(g["away"])
            final_teams.add(g["home"])

    marks = {}
    for leg in all_legs:
        norm = normalize_name(leg["player"])
        team = leg["team"]
        if norm in all_hr_names:
            marks[leg["id"]] = "hit"
        elif team in final_teams:
            # that player's game is over and they never homered
            marks[leg["id"]] = "miss"
        else:
            marks[leg["id"]] = "pending"

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
