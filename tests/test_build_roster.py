"""
Regression checks for scripts/build_roster.py. Fully offline: a fake
fetcher stands in for the MLB Stats API, and output goes to a variable,
never to data/.

    python tests/test_build_roster.py
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import build_roster as br  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TEAMS = {
    "https://statsapi.mlb.com/api/v1/teams?sportId=1&activeStatus=Y": {
        "teams": [{"id": 135, "name": "San Diego Padres", "abbreviation": "SD"},
                  {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
                  {"id": 999, "name": "No Abbreviation Team"}]  # missing abbreviation -- must be skipped, not crash
    },
    "https://statsapi.mlb.com/api/v1/teams/135/roster?rosterType=active": {
        "roster": [
            {"person": {"id": 665487, "fullName": "Fernando Tatis Jr."}},   # the exact real case this script fixes
            {"person": {"id": 1, "fullName": "Manny Machado"}},
        ]
    },
    "https://statsapi.mlb.com/api/v1/teams/147/roster?rosterType=active": {
        "roster": [
            {"person": {"id": 2, "fullName": "Aaron Judge"}},
            {"person": {"id": 3, "fullName": "José Ramírez"}},   # accented -- must survive un-mangled
        ]
    },
    "https://statsapi.mlb.com/api/v1/teams/999/roster?rosterType=active": {"roster": [{"person": {"id": 4, "fullName": "Ghost Player"}}]},
}


def fake_fetch(url):
    if url not in TEAMS:
        raise AssertionError(f"unexpected URL: {url}")
    return TEAMS[url]


result = br.build(fetcher=fake_fetch)
canon = result["canonical_name_by_norm"]
team_by = result["team_by_name"]

check("suffix preserved -- the real bug this script fixes", canon.get("fernando tatis jr") == "Fernando Tatis Jr.")
check("no un-suffixed key created for a suffixed player", "fernando tatis" not in canon)
check("team code recorded", team_by.get("fernando tatis jr") == "SD")
check("plain names round-trip", canon.get("manny machado") == "Manny Machado" and canon.get("aaron judge") == "Aaron Judge")
check("accents preserved in the canonical name (only normalize() strips them, for matching)",
      canon.get("jose ramirez") == "José Ramírez")
check("a team with no abbreviation is skipped, not crashed on or silently mis-keyed",
      "ghost player" not in canon, canon.get("ghost player"))
check("exactly the players from valid teams made it in", set(canon) == {"fernando tatis jr", "manny machado", "aaron judge", "jose ramirez"})

# A duplicate normalized name across two different real people: last one wins,
# and it must not raise -- this happens for real (two "José Fermín"/"José Fermin"
# spelling variants collided when this was first run against live data).
dup_teams = {
    "https://statsapi.mlb.com/api/v1/teams?sportId=1&activeStatus=Y": {"teams": [{"id": 1, "name": "A", "abbreviation": "A"}]},
    "https://statsapi.mlb.com/api/v1/teams/1/roster?rosterType=active": {"roster": [
        {"person": {"id": 10, "fullName": "José Fermín"}},
        {"person": {"id": 11, "fullName": "José Fermin"}},
    ]},
}
dup_result = br.build(fetcher=lambda url: dup_teams[url])
check("a name collision doesn't crash -- last one wins", dup_result["canonical_name_by_norm"].get("jose fermin") == "José Fermin")

# ---- a rebuild MERGES; it must never lose a player ----------------------
# The MLB API serves ACTIVE rosters, which exclude anyone on the IL, so a
# straight overwrite drops players. Measured for real on 2026-09-26: a rebuild
# 8 days after the previous one lost 73 names and gained 74, and one of the 73
# was Aaron Judge. A missing player is the worst state there is -- nothing
# matches his name, so his leg can never resolve a hit OR a miss.
#
# merge_rosters() is a pure function precisely so this stays OFFLINE: driving
# main() would hit the real API, and then whether Judge is present depends on
# today's IL, which is the very thing under test.
existing = {
    "team_by_name": {"retired slugger": "BOS", "manny machado": "STALE"},
    "canonical_name_by_norm": {"retired slugger": "Retired Slugger",
                               "manny machado": "Manny Machado"},
}
fresh = {
    "team_by_name": {"manny machado": "SD", "aaron judge": "NYY"},
    "canonical_name_by_norm": {"manny machado": "Manny Machado",
                               "aaron judge": "Aaron Judge"},
}
merged, kept, moved = br.merge_rosters(existing, fresh)

check("a player the active rosters no longer list is KEPT, not dropped",
      merged["team_by_name"].get("retired slugger") == "BOS", merged["team_by_name"])
check("fresh data WINS for anyone in both, so a trade is picked up",
      merged["team_by_name"].get("manny machado") == "SD", merged["team_by_name"])
check("players only the fresh build knows about are added",
      merged["team_by_name"].get("aaron judge") == "NYY", merged["team_by_name"])
check("the kept count and moved list are reported for the log",
      kept == 1 and moved == ["manny machado"], (kept, moved))
check("canonical names merge the same way",
      merged["canonical_name_by_norm"].get("retired slugger") == "Retired Slugger")
# An empty/absent existing roster must behave like a plain build.
plain, kept0, moved0 = br.merge_rosters({}, fresh)
check("merging into nothing is just the fresh roster",
      plain["team_by_name"] == fresh["team_by_name"] and kept0 == 0 and moved0 == [])

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all roster-build checks passed")
