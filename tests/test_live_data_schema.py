"""
Schema checks against whatever is ACTUALLY committed under data/ right now --
the one thing no other test does. Every other suite either builds its own
fixture or fakes the fetcher; none of them ever open the real files the live
pages fetch on every visit. A bad manual edit, a parser regression that still
exits 0 but writes a malformed field, or drift between what CLAUDE.md
documents and what's actually on disk would pass every other test in this
repo undetected.

READ-ONLY, on purpose: this is the one test file that touches data/ at all.
It never writes, and per the "absent tickets file is a normal state" rule
(CLAUDE.md gotcha 1 / the tickets.json schema section), a file that's simply
not there right now is not a failure -- it's skipped and reported as such.

    python tests/test_live_data_schema.py
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def skip(name, reason):
    print(f"SKIP  {name}  [{reason}]")


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def is_str(v):
    return isinstance(v, str)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_num_or_null(v):
    """Payouts only. An unknown ("TBD") payout is stored as null -- see the
    note at its call site. Everything else that must be a number uses is_num."""
    return v is None or is_num(v)


def has_keys(obj, required, label):
    missing = [k for k in required if k not in obj]
    return not missing, f"{label} missing {missing}" if missing else ""


ODDS_STR_RE = re.compile(r"^[+-]\d+$")           # tickets files: odds as displayed, e.g. "+300" / "-120"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ================= tickets.json / tickets-previous.json (both sports) =================

TICKET_LEG_KEYS = ("id", "player", "team", "who", "meta", "odds", "time")
TICKET_KEYS = ("name", "sub", "foot", "stake", "book", "payout", "legs")
SINGLE_KEYS = ("id", "who", "player", "team", "meta", "odds", "stake", "payout", "pp")


def check_tickets_file(label, path, football=False):
    data = load(path)
    if data is None:
        skip(label, "file not present -- a normal state (no picks currently loaded for this slate/tab)")
        return

    ok, why = has_keys(data, ("date", "note", "windows", "singles"), label)
    check(f"{label}: has date/note/windows/singles", ok, why)
    if not ok:
        return
    check(f"{label}: date is YYYY-MM-DD", DATE_RE.match(data["date"]) is not None, data["date"])
    check(f"{label}: note is a string (blank is fine)", is_str(data["note"]))
    check(f"{label}: windows and singles are lists", isinstance(data["windows"], list) and isinstance(data["singles"], list))
    if football and "endDate" in data:
        check(f"{label}: endDate is YYYY-MM-DD when present", DATE_RE.match(data["endDate"]) is not None, data.get("endDate"))
    if football and "weekEnds" in data:
        check(f"{label}: weekEnds is YYYY-MM-DD when present", DATE_RE.match(data["weekEnds"]) is not None, data.get("weekEnds"))

    leg_problems, ticket_problems = [], []
    for w in data["windows"]:
        if "title" not in w or "tickets" not in w or not isinstance(w["tickets"], list):
            ticket_problems.append(f"window missing title/tickets: {list(w.keys())}")
            continue
        for t in w["tickets"]:
            ok, why = has_keys(t, TICKET_KEYS, "ticket")
            if not ok:
                ticket_problems.append(why)
                continue
            # payout may be null: a card can list its potential payout as "TBD",
            # and the parser stores that rather than inventing a number or
            # dropping the bet. stake is always known -- you always know what
            # you put down. fmtMoney() renders a null payout as "TBD".
            if not is_num(t["stake"]) or not is_num_or_null(t["payout"]):
                ticket_problems.append(f"{t.get('name')}: stake not numeric, or payout not numeric-or-null")
            if not isinstance(t["legs"], list) or not t["legs"]:
                ticket_problems.append(f"{t.get('name')}: legs missing or empty")
                continue
            for leg in t["legs"]:
                ok, why = has_keys(leg, TICKET_LEG_KEYS, "leg")
                if not ok:
                    leg_problems.append(why)
                    continue
                if not ODDS_STR_RE.match(leg["odds"]):
                    leg_problems.append(f"{leg.get('player')}: odds {leg['odds']!r} not [+-]NNN")
                if leg.get("market") not in (None, "sb"):
                    leg_problems.append(f"{leg.get('player')}: unknown market {leg.get('market')!r}")
                if football and "athleteId" in leg and leg["athleteId"] is not None and not is_str(leg["athleteId"]):
                    leg_problems.append(f"{leg.get('player')}: athleteId present but not a string")
    check(f"{label}: every ticket has the full parlay-card shape", not ticket_problems, "; ".join(ticket_problems[:3]))
    check(f"{label}: every leg has the full shape, valid odds string, and a known market", not leg_problems, "; ".join(leg_problems[:3]))

    single_problems = []
    for s in data["singles"]:
        ok, why = has_keys(s, SINGLE_KEYS, "single")
        if not ok:
            single_problems.append(why)
            continue
        if not ODDS_STR_RE.match(s["odds"]):
            single_problems.append(f"{s.get('player')}: odds {s['odds']!r} not [+-]NNN")
        if not is_num(s["stake"]) or not is_num_or_null(s["payout"]):
            single_problems.append(f"{s.get('player')}: stake not numeric, or payout not numeric-or-null")
        if s.get("market") not in (None, "sb"):
            single_problems.append(f"{s.get('player')}: unknown market {s.get('market')!r}")
    check(f"{label}: every single has the full shape and valid odds", not single_problems, "; ".join(single_problems[:3]))

    all_names = [leg["player"] for w in data["windows"] for t in w["tickets"] for leg in t["legs"]] + [s["player"] for s in data["singles"]]
    check(f"{label}: no leg/single has a blank player name", all(n.strip() for n in all_names))


check_tickets_file("data/tickets.json", DATA / "tickets.json")
check_tickets_file("data/tickets-previous.json", DATA / "tickets-previous.json")
check_tickets_file("data/football/tickets.json", DATA / "football" / "tickets.json", football=True)
check_tickets_file("data/football/tickets-previous.json", DATA / "football" / "tickets-previous.json", football=True)


# ================= roster.json (both sports) =================

def check_roster_file(label, path, required_maps, min_size):
    data = load(path)
    check(f"{label}: exists (never a normal absence -- it's rebuilt, not live-uploaded data)", data is not None)
    if data is None:
        return
    ok, why = has_keys(data, required_maps, label)
    check(f"{label}: has {required_maps}", ok, why)
    if not ok:
        return
    for m in required_maps:
        check(f"{label}.{m}: is a non-trivially-sized dict", isinstance(data[m], dict) and len(data[m]) >= min_size, len(data.get(m, {})))
    # every map should share the same key set (normalized name), so a lookup
    # in one always has a matching entry in the others
    key_sets = [set(data[m].keys()) for m in required_maps]
    check(f"{label}: every map is keyed by the same set of normalized names", all(ks == key_sets[0] for ks in key_sets),
          [len(ks) for ks in key_sets])
    sample_norm, sample_canon = next(iter(data["canonical_name_by_norm"].items()))
    check(f"{label}: a normalized key actually normalizes its own canonical name (lowercase, no punctuation)",
          sample_norm == re.sub(r"[.'’]", "", sample_canon).lower(), (sample_norm, sample_canon))


check_roster_file("data/roster.json", DATA / "roster.json", ("team_by_name", "canonical_name_by_norm"), min_size=400)
check_roster_file("data/football/roster.json", DATA / "football" / "roster.json",
                   ("team_by_name", "canonical_name_by_norm", "id_by_norm", "pos_by_norm"), min_size=1500)


# ================= history.json (both sports) =================

HISTORY_LEG_KEYS = ("bettor", "pick", "odds", "status")
LEG_STATUSES = {"hit", "miss", "dnp", "pending"}


def check_history_file(label, path, sport):
    data = load(path)
    check(f"{label}: exists (rebuilt daily; should never simply be missing)", data is not None)
    if data is None:
        return
    required = ("firstSlate", "lastSlate", "parlays") if sport == "football" else ("firstSlate", "lastSlate", "oddsFrom", "parlays")
    ok, why = has_keys(data, required, label)
    check(f"{label}: has the expected top-level keys", ok, why)
    if sport == "football":
        check(f"{label}: sport is marked 'football'", data.get("sport") == "football")
        check(f"{label}: has a 'weeks' list", isinstance(data.get("weeks"), list))
    check(f"{label}: parlays is a list", isinstance(data.get("parlays"), list))
    if not data.get("parlays"):
        skip(f"{label} parlay contents", "no parlays recorded yet for this sport")
        return

    if data["firstSlate"] is not None:
        check(f"{label}: firstSlate/lastSlate are YYYY-MM-DD", DATE_RE.match(data["firstSlate"]) and DATE_RE.match(data["lastSlate"]))

    leg_problems, parlay_problems = [], []
    for parlay in data["parlays"]:
        if "date" not in parlay or "legs" not in parlay or not isinstance(parlay["legs"], list) or not parlay["legs"]:
            parlay_problems.append(f"parlay missing date/legs: {list(parlay.keys())}")
            continue
        if not DATE_RE.match(parlay["date"]):
            parlay_problems.append(f"bad date {parlay['date']!r}")
        for leg in parlay["legs"]:
            ok, why = has_keys(leg, HISTORY_LEG_KEYS, "leg")
            if not ok:
                leg_problems.append(why)
                continue
            if leg["status"] not in LEG_STATUSES:
                leg_problems.append(f"{leg['pick']}: unknown status {leg['status']!r}")
            if leg["odds"] is not None and not isinstance(leg["odds"], int):
                leg_problems.append(f"{leg['pick']}: odds {leg['odds']!r} is neither an int nor null")
            if leg.get("market") not in (None, "sb"):
                leg_problems.append(f"{leg['pick']}: unknown market {leg.get('market')!r}")
    check(f"{label}: every parlay has a valid date and at least one leg", not parlay_problems, "; ".join(parlay_problems[:3]))
    check(f"{label}: every leg has bettor/pick/odds/status, odds is int-or-null, status is a known value",
          not leg_problems, "; ".join(leg_problems[:3]))

    site_parlays = [p for p in data["parlays"] if p.get("src") == "site"]
    if site_parlays:
        recorded_problems = []
        for p in site_parlays:
            if "stake" in p and not is_num(p["stake"]):
                recorded_problems.append(f"{p.get('name', '(single)')}: stake not numeric")
            if "returned" in p and not is_num(p["returned"]):
                recorded_problems.append(f"{p.get('name', '(single)')}: returned not numeric")
        check(f"{label}: tracker-recorded parlays (src=site) carry numeric stake/returned where present",
              not recorded_problems, "; ".join(recorded_problems[:3]))
    else:
        skip(f"{label} recorded-slate fields", "no tracker-recorded (src=site) parlays yet -- all from the sheet")

    ga = data.get("gotAway")
    if ga:
        check(f"{label}.gotAway: has the expected shape",
              all(k in ga for k in ("minPicks", "checkedPickDays", "agreeingPickDays", "players")) and isinstance(ga["players"], list))
    elif sport != "football":
        skip(f"{label}.gotAway", "absent (no MLB join this run) or explicitly null -- both are valid states")


check_history_file("data/history.json", DATA / "history.json", sport="baseball")
check_history_file("data/football/history.json", DATA / "football" / "history.json", sport="football")


# ================= data/results/*.json and data/football/results/*.json =================

RESULT_TOP_KEYS = ("sport", "date", "complete", "ticketsHash", "summary", "note", "parlays", "singles")
RESULT_SUMMARY_KEYS = ("games", "parlays", "parlaysCashed", "singles", "singlesCashed", "legs", "legsHit", "legsVoid", "staked", "returned")


def check_recorded_slate(path, sport):
    data = load(path)
    problems = []
    required = RESULT_TOP_KEYS + (("weekEnds", "endDate", "games", "touchdowns") if sport == "football" else ("homeRuns",))
    ok, why = has_keys(data, required, path.name)
    if not ok:
        problems.append(why)
        return problems
    if data["sport"] != sport:
        problems.append(f"sport field says {data['sport']!r}, expected {sport!r}")
    if not isinstance(data["complete"], bool):
        problems.append("complete is not a bool")
    sk, swhy = has_keys(data["summary"], RESULT_SUMMARY_KEYS, "summary")
    if not sk:
        problems.append(swhy)
    for key in ("staked", "returned"):
        if key in data["summary"] and not is_num(data["summary"][key]):
            problems.append(f"summary.{key} not numeric")
    if not isinstance(data["parlays"], list) or not isinstance(data["singles"], list):
        problems.append("parlays/singles not lists")
    return problems


baseball_results = sorted((DATA / "results").glob("*.json")) if (DATA / "results").exists() else []
if baseball_results:
    all_problems = []
    for f in baseball_results:
        p = check_recorded_slate(f, "baseball")
        if p:
            all_problems.append(f"{f.name}: {p}")
    check(f"data/results/: every recorded slate ({len(baseball_results)} file(s)) has the recorder's full shape",
          not all_problems, "; ".join(all_problems[:3]))
else:
    skip("data/results/", "no slates recorded yet")

football_results = sorted((DATA / "football" / "results").glob("*.json")) if (DATA / "football" / "results").exists() else []
if football_results:
    all_problems = []
    for f in football_results:
        p = check_recorded_slate(f, "football")
        if p:
            all_problems.append(f"{f.name}: {p}")
    check(f"data/football/results/: every recorded week ({len(football_results)} file(s)) has the recorder's full shape",
          not all_problems, "; ".join(all_problems[:3]))
else:
    skip("data/football/results/", "no weeks recorded yet (no NFL week has finished since the archive started)")


print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all live-data schema checks passed")
