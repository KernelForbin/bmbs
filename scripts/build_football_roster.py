#!/usr/bin/env python3
"""
Builds data/football/roster.json from ESPN's public NFL API -- every team's
current roster. The football twin of build_roster.py, with one addition the
baseball file doesn't have: each player's ESPN athlete id.

That id is the point. The live page matches picks to ESPN's boxscore by id
first and only falls back to the name, so a pick survives any spelling
difference -- the baseball side learned this the hard way when "Fernando
Tatis" never matched "Fernando Tatis Jr." (ESPN writes "James Cook III",
"Marvin Harrison Jr.", and nobody types those suffixes into a picks card).

Name keys have generational suffixes stripped (see norm_key), so a card
that says "James Cook" resolves without leaning on fuzzy matching.

Two NFL players can share a name (there are two Josh Allens). When keys
collide, the offensive skill position wins -- an anytime-touchdown card
means the quarterback, not the linebacker.

Run:
    python scripts/build_football_roster.py [--out FILE]

Re-run after trades / signings if a newly added player can't be found.
"""
import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "football" / "roster.json"
API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

SKILL = {"QB", "RB", "WR", "TE", "FB"}
SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def norm_key(name):
    """Lowercase, accents/periods/apostrophes dropped, generational suffix
    dropped. parse_football_picks.py and football/index.html normalize the
    same way -- keep the three in step."""
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    name = name.replace(".", "").replace("'", "")
    name = " ".join(name.split()).strip().lower()
    return SUFFIX_RE.sub("", name)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-football-roster"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def build(fetcher=fetch):
    listing = fetcher(f"{API}/teams?limit=40")
    teams = [t["team"] for t in listing["sports"][0]["leagues"][0]["teams"]]
    if len(teams) != 32:
        print(f"WARNING: expected 32 NFL teams, got {len(teams)} -- check the roster for gaps.", file=sys.stderr)

    players = {}     # key -> {name, team, id, pos}
    for team in teams:
        abbr = team.get("abbreviation")
        if not abbr:
            print(f"NOTE: team {team.get('displayName')!r} has no abbreviation, skipped", file=sys.stderr)
            continue
        roster = fetcher(f"{API}/teams/{team['id']}/roster")
        for group in roster.get("athletes", []):
            for a in group.get("items", []):
                name = a.get("fullName") or a.get("displayName")
                if not name or not a.get("id"):
                    continue
                pos = (a.get("position") or {}).get("abbreviation", "")
                entry = {"name": name, "team": abbr, "id": str(a["id"]), "pos": pos}
                key = norm_key(name)
                old = players.get(key)
                if old and old["id"] != entry["id"]:
                    keep = entry if (pos in SKILL and old["pos"] not in SKILL) else old
                    print(f"NOTE: two players share the key {key!r}: {old['name']} ({old['team']} {old['pos']}) and "
                          f"{name} ({abbr} {pos}) -- keeping {keep['name']} ({keep['team']} {keep['pos']}).", file=sys.stderr)
                    players[key] = keep
                else:
                    players[key] = entry

    return {
        "team_by_name": {k: v["team"] for k, v in players.items()},
        "canonical_name_by_norm": {k: v["name"] for k, v in players.items()},
        "id_by_norm": {k: v["id"] for k, v in players.items()},
        "pos_by_norm": {k: v["pos"] for k, v in players.items()},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    payload = build()
    print(f"Built NFL roster: {len(payload['id_by_norm'])} players across {len(set(payload['team_by_name'].values()))} teams.")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
