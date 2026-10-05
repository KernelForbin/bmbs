#!/usr/bin/env python3
"""
Builds data/basketball/roster.json from ESPN's public NBA API -- every team's
current roster. The basketball twin of build_hockey_roster.py: name -> team,
ESPN athlete id and position, plus each team's full name, so a card that
writes "Denver Nuggets" or "Nuggets" resolves to DEN.

The athlete id is the point, as on the football and hockey sides: the live
page matches picks to ESPN's boxscore by id first and only falls back to the
name. Name keys have generational suffixes stripped (ESPN writes "Jaren
Jackson Jr.", nobody types it).

A rebuild MERGES with the committed roster rather than replacing it: ESPN's
rosters drop players who are released or two-way, and a missing player is the
worst state there is -- his leg can never grade either way. Fresh data wins
for anyone in both; `--replace` starts over.

Run:
    python scripts/build_basketball_roster.py [--out FILE] [--replace]
"""
import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "basketball" / "roster.json"
API = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba"

SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")
PLAYER_MAPS = ("team_by_name", "canonical_name_by_norm", "id_by_norm", "pos_by_norm")


def norm_key(name):
    """Lowercase, accents/periods/apostrophes dropped, generational suffix
    dropped -- the same rule the page's NBA engine and the parsers use."""
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    name = " ".join(name.split()).strip().lower()
    return SUFFIX_RE.sub("", name)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-basketball-roster"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def roster_athletes(roster):
    """NBA rosters are a flat list; hockey's are grouped. Accept either."""
    out = []
    for a in roster.get("athletes", []):
        out.extend(a.get("items", []) if isinstance(a, dict) and "items" in a else [a])
    return out


def build(fetcher=fetch):
    listing = fetcher(f"{API}/teams?limit=40")
    teams = [t["team"] for t in listing["sports"][0]["leagues"][0]["teams"]]
    if len(teams) != 30:
        print(f"WARNING: expected 30 NBA teams, got {len(teams)} -- check the roster for gaps.", file=sys.stderr)

    players = {}     # key -> {name, team, id, pos}
    team_names = {}  # abbr -> "Denver Nuggets"
    for team in teams:
        abbr = team.get("abbreviation")
        if not abbr:
            print(f"NOTE: team {team.get('displayName')!r} has no abbreviation, skipped", file=sys.stderr)
            continue
        team_names[abbr] = team.get("displayName") or abbr
        for a in roster_athletes(fetcher(f"{API}/teams/{team['id']}/roster")):
            name = a.get("fullName") or a.get("displayName")
            if not name or not a.get("id"):
                continue
            pos = (a.get("position") or {}).get("abbreviation", "")
            entry = {"name": name, "team": abbr, "id": str(a["id"]), "pos": pos}
            key = norm_key(name)
            old = players.get(key)
            if old and old["id"] != entry["id"]:
                print(f"NOTE: two players share the key {key!r}: {old['name']} ({old['team']}) and "
                      f"{name} ({abbr}) -- keeping the first.", file=sys.stderr)
                continue
            players[key] = entry

    return {
        "team_by_name": {k: v["team"] for k, v in players.items()},
        "canonical_name_by_norm": {k: v["name"] for k, v in players.items()},
        "id_by_norm": {k: v["id"] for k, v in players.items()},
        "pos_by_norm": {k: v["pos"] for k, v in players.items()},
        "team_names": team_names,
    }


def merge_rosters(old, new):
    """Fresh data wins; nobody already known is dropped. Pure, for the tests."""
    if not old:
        return new
    out = {m: dict(old.get(m) or {}) for m in PLAYER_MAPS}
    for m in PLAYER_MAPS:
        out[m].update(new.get(m) or {})
    out["team_names"] = dict(old.get("team_names") or {})
    out["team_names"].update(new.get("team_names") or {})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--replace", action="store_true", help="start over instead of merging with the committed roster")
    args = ap.parse_args()

    payload = build()
    out = Path(args.out)
    if not args.replace and out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        kept = set(old.get("team_by_name") or {}) - set(payload["team_by_name"])
        payload = merge_rosters(old, payload)
        if kept:
            print(f"Merged with the existing roster: kept {len(kept)} name(s) ESPN no longer lists.")
    print(f"Built NBA roster: {len(payload['id_by_norm'])} players across {len(payload['team_names'])} teams.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
