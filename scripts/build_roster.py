#!/usr/bin/env python3
"""
Builds data/roster.json straight from the MLB Stats API -- one active
roster per team, 30 teams. Replaces the original one-time process (a
manually uploaded roster CSV): that CSV's name column had every
generational suffix (Jr., Sr., II, III, IV) stripped, which silently broke
matching for anyone who has one. Confirmed 2026-09-18: ALL 8 spot-checked
suffixed players in the old roster.json (Guerrero, Witt, Acuna, Garcia,
Tatis, Gurriel, Harris, Chisholm) were missing theirs -- not a one-off, the
CSV lacked suffixes across the board. Three of those (Tatis, Witt,
Guerrero) were picked in a real slate while this was discovered, live.

The Stats API's `person.fullName` is authoritative and already correct
(e.g. "Fernando Tatis Jr."), so this only has to reproduce it faithfully --
no suffix-guessing.

Run:
    python scripts/build_roster.py [--out FILE]

Rosters change (call-ups, trades) -- re-run this occasionally, the same
limitation the old CSV had. Uses `rosterType=active`: the players who can
actually appear in a game today, same scope the CSV was meant to cover.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_picks import normalize_name  # noqa: E402 -- single source of truth for normalization

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "roster.json"
API = "https://statsapi.mlb.com/api/v1"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-roster-build"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def build(fetcher=fetch):
    teams = fetcher(f"{API}/teams?sportId=1&activeStatus=Y")["teams"]
    if len(teams) != 30:
        print(f"WARNING: expected 30 active MLB teams, got {len(teams)} -- "
              f"proceeding anyway, but check the roster for gaps.", file=sys.stderr)

    team_by_name = {}
    canonical_name_by_norm = {}
    collisions = []
    for team in teams:
        abbr = team.get("abbreviation")
        if not abbr:
            print(f"NOTE: team {team.get('name')!r} has no abbreviation, skipped", file=sys.stderr)
            continue
        roster = fetcher(f"{API}/teams/{team['id']}/roster?rosterType=active").get("roster", [])
        for entry in roster:
            full_name = (entry.get("person") or {}).get("fullName")
            if not full_name:
                continue
            norm = normalize_name(full_name)
            if norm in canonical_name_by_norm and canonical_name_by_norm[norm] != full_name:
                # Two different active players normalize the same way (rare,
                # but worth knowing about rather than silently overwriting).
                collisions.append((norm, canonical_name_by_norm[norm], full_name))
            team_by_name[norm] = abbr
            canonical_name_by_norm[norm] = full_name

    for norm, first, second in collisions:
        print(f"NOTE: two active players both normalize to {norm!r}: "
              f"{first!r} and {second!r} (last one wins) -- check by hand if either is picked.", file=sys.stderr)

    return {"team_by_name": team_by_name, "canonical_name_by_norm": canonical_name_by_norm}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    payload = build()
    print(f"Built roster: {len(payload['canonical_name_by_norm'])} players across "
          f"{len(set(payload['team_by_name'].values()))} teams.")
    suffixed = [n for n in payload["canonical_name_by_norm"].values() if n.split()[-1] in ("Jr.", "Sr.", "II", "III", "IV")]
    print(f"  {len(suffixed)} carry a generational suffix (Jr./Sr./II/III/IV) -- "
          f"the exact class of name this rebuild fixes.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
