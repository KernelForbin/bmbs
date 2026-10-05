#!/usr/bin/env python3
"""
Builds data/cfb/roster.json from ESPN's public college-football API: every FBS
team's current roster. The college twin of build_football_roster.py -- name ->
team, ESPN athlete id and position, plus each school's full name -- for the
hidden college tracker at /cfb/ and college legs on the All Sports page.

FBS only (ESPN group 80, ~148 schools): the site API's team list holds every
division (760+), and a roster per school is one request each.

College names collide far more than any pro league's (16,000 players): two
Jordan Smiths is ordinary. The plain maps keep ONE holder per name -- a skill
player (QB/RB/WR/TE) over anyone else, since a touchdown bet means him -- and
`others_by_norm` lists every OTHER holder, so the parser can pick by the team
the line names instead of guessing.

ESPN's school rosters are INCOMPLETE: on VAN @ UGA (2026-10-03) 12 of the 63
players in the box score -- Georgia's starting quarterback among them -- were
on no roster. So the build also reads the last two weeks of FBS box scores
(`--days`) and adds anyone who actually played and isn't listed. A box score
names exactly the people a card bets on. Box scores carry no position; those
players are filed with pos "" (never preferred over a listed skill player).

A rebuild MERGES with the committed roster, like every other roster builder
here (a missing player can never grade either way); `--replace` starts over.

Run:
    python scripts/build_cfb_roster.py [--out FILE] [--replace]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_basketball_roster import fetch, norm_key, roster_athletes   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "cfb" / "roster.json"
API = "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football"
FBS = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/{season}/types/2/groups/80/teams?limit=300"
PLAYER_MAPS = ("team_by_name", "canonical_name_by_norm", "id_by_norm", "pos_by_norm")
SKILL = {"QB", "RB", "WR", "TE", "FB"}


def fbs_ids(fetcher, season):
    data = fetcher(FBS.format(season=season))
    return {ref["$ref"].split("/teams/")[1].split("?")[0] for ref in data.get("items", [])}


def box_score_players(fetcher, days, today=None):
    """[(name, team abbr, id)] for everyone in a finished FBS game's box score
    over the last `days` days. The scoreboard's default IS the FBS slate."""
    from datetime import date, timedelta
    today = today or date.today()
    out, seen = [], set()
    for back in range(1, days + 1):
        ymd = (today - timedelta(days=back)).strftime("%Y%m%d")
        try:
            events = fetcher(f"{API}/scoreboard?dates={ymd}").get("events", [])
        except Exception as e:      # a gap in the top-up, not a failed build
            print(f"NOTE: scoreboard {ymd} unreadable ({e}).", file=sys.stderr)
            continue
        for ev in events:
            if ((ev.get("status") or {}).get("type") or {}).get("state") != "post" or ev["id"] in seen:
                continue
            seen.add(ev["id"])
            try:
                summary = fetcher(f"{API}/summary?event={ev['id']}")
            except Exception as e:
                print(f"NOTE: box score {ev['id']} unreadable ({e}).", file=sys.stderr)
                continue
            for side in (summary.get("boxscore") or {}).get("players", []):
                abbr = (side.get("team") or {}).get("abbreviation", "")
                for cat in side.get("statistics", []):
                    for row in cat.get("athletes", []):
                        a = row.get("athlete") or {}
                        if a.get("id") and (a.get("displayName") or "").strip() and abbr:
                            out.append((a["displayName"].strip(), abbr, str(a["id"])))
    return out


def build(fetcher=fetch, season=2026, days=14):
    ids = fbs_ids(fetcher, season)
    listing = fetcher(f"{API}/teams?limit=1000")
    teams = [t["team"] for t in listing["sports"][0]["leagues"][0]["teams"] if str(t["team"].get("id")) in ids]
    if len(teams) < 120:
        print(f"WARNING: only {len(teams)} FBS schools found -- check the roster for gaps.", file=sys.stderr)

    players = {}     # key -> {name, team, id, pos}
    others = {}      # key -> [[name, team, id, pos], ...] for every other holder of that name
    team_names = {}  # abbr -> "Georgia Bulldogs"
    team_aliases = {}  # abbr -> ["Georgia", "Georgia", "Bulldogs"]: location, short name, nickname
    for team in teams:
        abbr = team.get("abbreviation")
        if not abbr:
            continue
        team_names[abbr] = team.get("displayName") or abbr
        team_aliases[abbr] = [x for x in (team.get("location"), team.get("shortDisplayName"), team.get("name")) if x]
        try:
            roster = fetcher(f"{API}/teams/{team['id']}/roster")
        except Exception as e:    # one school failing must not sink the other 147
            print(f"NOTE: {team_names[abbr]}'s roster couldn't be read ({e}) -- skipped.", file=sys.stderr)
            continue
        for a in roster_athletes(roster):
            name = a.get("fullName") or a.get("displayName")
            if not name or not a.get("id"):
                continue
            entry = {"name": name, "team": abbr, "id": str(a["id"]), "pos": (a.get("position") or {}).get("abbreviation", "")}
            key = norm_key(name)
            old = players.get(key)
            if old and old["id"] != entry["id"]:
                keep, other = (entry, old) if (entry["pos"] in SKILL and old["pos"] not in SKILL) else (old, entry)
                players[key] = keep
                others.setdefault(key, []).append([other["name"], other["team"], other["id"], other["pos"]])
            else:
                players[key] = entry

    # The top-up: anyone who has PLAYED lately and isn't on a roster.
    known_ids = {v["id"] for v in players.values()} | {o[2] for lst in others.values() for o in lst}
    added = 0
    for name, abbr, pid in box_score_players(fetcher, days) if days else []:
        if pid in known_ids or abbr not in team_names:
            continue
        known_ids.add(pid)
        key = norm_key(name)
        if key in players:
            others.setdefault(key, []).append([name, abbr, pid, ""])
        else:
            players[key] = {"name": name, "team": abbr, "id": pid, "pos": ""}
        added += 1
    print(f"Box scores added {added} player(s) the school rosters didn't list.")

    return {
        "team_by_name": {k: v["team"] for k, v in players.items()},
        "canonical_name_by_norm": {k: v["name"] for k, v in players.items()},
        "id_by_norm": {k: v["id"] for k, v in players.items()},
        "pos_by_norm": {k: v["pos"] for k, v in players.items()},
        "others_by_norm": others,
        "team_names": team_names,
        "team_aliases": team_aliases,
    }


def merge_rosters(old, new):
    """Fresh data wins; nobody already known is dropped. Pure, for the tests."""
    if not old:
        return new
    out = {m: dict(old.get(m) or {}) for m in PLAYER_MAPS}
    for m in PLAYER_MAPS:
        out[m].update(new.get(m) or {})
    out["others_by_norm"] = dict(old.get("others_by_norm") or {})
    out["others_by_norm"].update(new.get("others_by_norm") or {})
    for k in ("team_names", "team_aliases"):
        out[k] = dict(old.get(k) or {})
        out[k].update(new.get(k) or {})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--days", type=int, default=14, help="days of FBS box scores to top the rosters up from (0 = none)")
    ap.add_argument("--replace", action="store_true", help="start over instead of merging with the committed roster")
    args = ap.parse_args()

    payload = build(season=args.season, days=args.days)
    out = Path(args.out)
    if not args.replace and out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        payload = merge_rosters(old, payload)
    print(f"Built college roster: {len(payload['id_by_norm'])} players across {len(payload['team_names'])} schools "
          f"({len(payload['others_by_norm'])} names held by more than one player).")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=0, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
