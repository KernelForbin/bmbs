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


def merge_rosters(existing, fresh):
    """Fold a freshly built roster into the one already on disk.

    A rebuild must never LOSE a player. The API serves ACTIVE rosters, which
    exclude anyone on the IL, so a straight overwrite silently drops people:
    measured 2026-09-26, a rebuild 8 days on lost 73 names and gained 74, and
    one of the 73 was Aaron Judge. A missing player is the worst possible
    state, because normalizeName() then matches nothing and his leg can never
    resolve a hit OR a miss -- the Tatis Jr. failure from a new direction.

    A stale TEAM on someone who has since moved is a much smaller problem: it
    only affects which game a leg is shown waiting on before first pitch, and
    grading itself goes by name in the boxscore. So fresh data wins for anyone
    in both, and nobody is ever dropped.

    Returns (merged_payload, kept_count, moved_names).
    """
    merged = dict(fresh)
    kept = 0
    for key in ("team_by_name", "canonical_name_by_norm"):
        before = dict(existing.get(key) or {})
        only_in_existing = set(before) - set(fresh.get(key) or {})
        before.update(fresh.get(key) or {})      # fresh data wins
        merged[key] = before
        kept = max(kept, len(only_in_existing))
    old_teams = existing.get("team_by_name") or {}
    moved = [n for n, t in old_teams.items()
             if n in merged["team_by_name"] and merged["team_by_name"][n] != t]
    return merged, kept, moved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--replace", action="store_true",
                    help="overwrite instead of merging -- discards anyone the active "
                         "rosters no longer list. Only for building from scratch.")
    args = ap.parse_args()

    payload = build()
    print(f"Built roster: {len(payload['canonical_name_by_norm'])} players across "
          f"{len(set(payload['team_by_name'].values()))} teams.")
    suffixed = [n for n in payload["canonical_name_by_norm"].values() if n.split()[-1] in ("Jr.", "Sr.", "II", "III", "IV")]
    print(f"  {len(suffixed)} carry a generational suffix (Jr./Sr./II/III/IV) -- "
          f"the exact class of name this rebuild fixes.")

    out = Path(args.out)

    if out.exists() and not args.replace:
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"NOTE: couldn't read the existing roster ({e}) -- writing a fresh one.")
            existing = None
        if existing:
            payload, kept, moved = merge_rosters(existing, payload)
            print(f"  Merged with the existing roster: kept {kept} name(s) the active "
                  f"rosters no longer list (IL, optioned), updated {len(moved)} team(s).")
            if moved:
                print(f"    moved: {', '.join(sorted(moved)[:6])}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out} ({len(payload['team_by_name'])} players)")


if __name__ == "__main__":
    main()
