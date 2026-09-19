#!/usr/bin/env python3
"""
Builds data/history.json, the static file the History page
(history/index.html) reads, from TWO sources:

  1. the group's hand-kept "Bombs" Google Sheet -- every slate up to the day
     the tracker started keeping its own record (2026-09-18), and
  2. data/results/<date>.json -- slates the tracker recorded itself
     (scripts/record_results.py): the same legs, plus what the sheet never
     had -- singles, real stakes and payouts, who placed each bet, full player
     names, MLB ids, Pinch Hit Protection credits, home run distances.

On a date both sources cover, THE TRACKER'S RECORD WINS and the sheet's rows
for that date are dropped, so it doesn't matter whether anyone keeps filling
in the sheet. If the sheet can't be read at all (unshared, deleted, layout
changed), the sheet-sourced parlays already in data/history.json are reused
with a warning -- history.json is itself the local copy of the sheet -- so a
dead sheet can never stop new slates from being added.

The sheet is only ever read here, at import time -- the page itself never
talks to Google. Two tabs hold the log, in the same column layout:

    Archive      older slates
    HR Parlays   the running log (most of its rows are collapsed/hidden in
                 the Sheets UI; the CSV export below still includes them --
                 the gviz CSV endpoint does NOT, which is an easy trap)

Every other tab is a derived rollup and is deliberately ignored: they are
recomputed from the log instead, because the sheet's own Player Stats tab
substring-matches names ("Cruz" also counts "De La Cruz") and is wrong.
"Solo Tracker" is not valid data and is never read.

Parlay outcomes are derived from their legs by the page, not taken from the
sheet's hand-typed "Parlay Status" column, which is blank or a stale
"Pending" on many rows (it never contradicts its legs where filled in).

"The ones that got away" -- homers by often-picked players on slates when
nobody picked them -- can't come from the sheet, so MLB game logs are joined
in for the players listed in scripts/history_player_map.json. That file maps
the group's shorthand ("PCA", "Cal", "Julio") to MLB player ids and is
hand-reviewed; nothing is ever guessed at import time. `--draft-map` proposes
entries, scoring each candidate by whether his real home run dates agree with
the sheet's Hit/Miss marks.

Run:
    python scripts/import_history.py                 # sheet + MLB -> data/history.json
    python scripts/import_history.py --no-mlb        # skip the game-log join
    python scripts/import_history.py --from-dir DIR --out FILE   # offline (tests)
    python scripts/import_history.py --draft-map     # propose player-map entries
"""
import argparse
import csv
import difflib
import io
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "history.json"
RESULTS_DIR = ROOT / "data" / "results"
MAP_PATH = ROOT / "scripts" / "history_player_map.json"

SHEET_ID = "1u9vCGxGeU6HTeNOASOFzxOFb90lIgURejm34Twgjq2A"
TABS = [("archive", 1001), ("log", 0)]  # oldest first
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
MLB_API = "https://statsapi.mlb.com/api/v1"

MAX_LEGS = 6
STATUSES = {"hit": "hit", "miss": "miss", "dnp": "dnp"}
GOT_AWAY_MIN_PICKS = 5  # "even though we pick them often"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-history-import"})
    with urllib.request.urlopen(req, timeout=45) as res:
        return res.read().decode("utf-8")


def norm(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[.'`]", "", name).replace("-", " ")
    return " ".join(name.split()).lower()


# ---------------- the sheet ----------------

def parse_date_cell(cell, today):
    """'8/25' | '2026-09-02 Set 2' -> (iso_date, set_number) or None."""
    cell = cell.strip()
    set_no = 1
    m = re.search(r"\bset\s*(\d+)", cell, re.IGNORECASE)
    if m:
        set_no = int(m.group(1))
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", cell)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), set_no
    m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", cell)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if m.group(3):
        year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
    else:
        # The sheet omits the year. A slate can't be in the future, so a date
        # that would be is last year's (matters for an import run in spring).
        year = today.year
        if date(year, month, day) > today + timedelta(days=2):
            year -= 1
    return date(year, month, day).isoformat(), set_no


def parse_money(cell):
    cell = cell.replace("$", "").replace(",", "").strip()
    try:
        return round(float(cell), 2)
    except ValueError:
        return None


def parse_odds(cell):
    m = re.match(r"^\s*([+-]?\d+)\s*$", cell)
    return int(m.group(1)) if m else None


def parse_tab(text, aliases, today):
    rows = list(csv.reader(io.StringIO(text)))
    header_at = next((i for i, r in enumerate(rows) if "L1 Bettor" in r), None)
    if header_at is None:
        raise ValueError("no 'L1 Bettor' header row found -- has the sheet layout changed?")
    col = {name.strip(): i for i, name in enumerate(rows[header_at]) if name.strip()}
    for needed in ("Date", "L1 Bettor", "L1 Pick", "L1 Status"):
        if needed not in col:
            raise ValueError(f"missing column {needed!r}")

    def cell(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    parlays, current = [], None
    for row in rows[header_at + 1:]:
        parsed = parse_date_cell(cell(row, "Date"), today) if cell(row, "Date") else None
        if parsed:
            current = parsed
        legs = []
        for n in range(1, MAX_LEGS + 1):
            bettor, pick = cell(row, f"L{n} Bettor"), cell(row, f"L{n} Pick")
            if not bettor and not pick:
                continue
            pick = " ".join(pick.split())
            legs.append({
                "bettor": bettor,
                "pick": aliases.get(pick, pick),
                "odds": parse_odds(cell(row, f"L{n} Odds")),
                "status": STATUSES.get(cell(row, f"L{n} Status").lower(), "pending"),
            })
        if not legs:
            continue
        if current is None:
            raise ValueError(f"legs before any date: {row[:6]}")
        parlay = {"date": current[0], "set": current[1], "legs": legs}
        won = parse_money(cell(row, "Amount Won"))
        if won:
            parlay["won"] = won
        parlays.append(parlay)
    return parlays


def load_map(path=MAP_PATH):
    if not path.exists():
        return {"aliases": {}, "players": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"aliases": data.get("aliases", {}), "players": data.get("players", {})}


# ---------------- MLB game logs ----------------

def game_log(player_id, seasons, fetcher=fetch):
    """{iso_date: home runs that date} for every game the player appeared in."""
    days = {}
    for season in seasons:
        data = json.loads(fetcher(
            f"{MLB_API}/people/{player_id}/stats?stats=gameLog&group=hitting&season={season}&gameType=R"))
        for block in data.get("stats", []):
            for split in block.get("splits", []):
                d = split.get("date")
                if d:
                    days[d] = days.get(d, 0) + int((split.get("stat") or {}).get("homeRuns") or 0)
    return days


def pick_days(parlays, pick):
    """{iso_date: 'hit' | 'miss'} for slates where `pick` was taken and resolved."""
    days = {}
    for p in parlays:
        for leg in p["legs"]:
            if leg.get("php") or leg.get("market") == "sb":
                continue   # a substitute's homer, or a steal bet: MLB's HOME RUN log has nothing to say about either
            if leg["pick"] == pick and leg["status"] in ("hit", "miss") and days.get(p["date"]) != "hit":
                days[p["date"]] = leg["status"]
    return days


def agreement(sheet_days, log):
    """How well a player's real HR dates match the sheet's marks for a nickname."""
    agree = contra = absent = 0
    for d, status in sheet_days.items():
        if d not in log:
            absent += 1
        elif (log[d] > 0) == (status == "hit"):
            agree += 1
        else:
            contra += 1
    return agree, contra, absent


def got_away(parlays, players, fetcher=fetch):
    slates = sorted({p["date"] for p in parlays})
    seasons = sorted({d[:4] for d in slates})
    # "Got away" is about home runs: a day he was only picked to STEAL is a day nobody had his homer
    parlays = [dict(p, legs=[leg for leg in p["legs"] if leg.get("market") != "sb"]) for p in parlays]
    counts = {}
    for p in parlays:
        for leg in p["legs"]:
            counts[leg["pick"]] = counts.get(leg["pick"], 0) + 1

    rows, agree_total, contra_total = [], 0, 0
    for pick, info in sorted(players.items()):
        if counts.get(pick, 0) < GOT_AWAY_MIN_PICKS:
            continue
        log = game_log(info["id"], seasons, fetcher)
        picked = {p["date"] for p in parlays for leg in p["legs"] if leg["pick"] == pick}
        a, c, _ = agreement(pick_days(parlays, pick), log)
        agree_total, contra_total = agree_total + a, contra_total + c
        if c:
            print(f"NOTE: {pick} ({info['name']}): sheet and MLB disagree on {c} pick-day(s)", file=sys.stderr)
        played = [d for d in slates if d in log]
        free = [d for d in played if d not in picked]
        on = [d for d in played if d in picked]
        rows.append({
            "pick": pick,
            "name": info["name"],
            "picks": counts[pick],
            "pickedDays": len(on),
            "pickedDayHits": sum(1 for d in on if log[d] > 0),
            "freeDays": len(free),
            "freeDayHits": sum(1 for d in free if log[d] > 0),
            "gotAway": [d for d in free if log[d] > 0],
        })
    return {"minPicks": GOT_AWAY_MIN_PICKS, "checkedPickDays": agree_total + contra_total,
            "agreeingPickDays": agree_total, "players": rows}


# ---------------- slates the tracker recorded itself ----------------

SITE_STATUS = {"hit": "hit", "miss": "miss", "na": "dnp"}   # anything else (suspended game) is pending


def load_recorded(results_dir=RESULTS_DIR):
    """Every data/results/<date>.json, oldest first."""
    slates = []
    for path in sorted(Path(results_dir).glob("*.json")) if Path(results_dir).is_dir() else []:
        slate = json.loads(path.read_text(encoding="utf-8"))
        if slate.get("date") and slate.get("sport", "baseball") == "baseball":
            slates.append(slate)
    return sorted(slates, key=lambda s: s["date"])


def recorded_parlays(slates, players):
    """Recorded slates in history.json's shape. Returns (parlays, extra_players).

    The sheet knows players by the group's shorthand ("Judge", "PCA"); the
    tracker knows them by full name and MLB id. So one player doesn't become two
    rows, a recorded leg takes the sheet's nickname whenever the hand-reviewed
    player map ties that nickname to the same MLB id (or, for a player who
    never appeared in a boxscore, the same full name). Anyone else keeps his
    full name -- and since his id came from the boxscore he was graded from, not
    from a guess, he joins the got-away check without a map entry.
    """
    by_id = {info["id"]: pick for pick, info in players.items()}
    by_name = {norm(info["name"]): pick for pick, info in players.items()}
    extra = {}

    def leg_of(src):
        pick = by_id.get(src.get("mlbId")) or by_name.get(norm(src["player"])) or src["player"]
        if src.get("mlbId") and pick not in players:
            extra[pick] = {"id": src["mlbId"], "name": src["player"]}
        leg = {"bettor": src.get("who") or "", "pick": pick, "odds": src.get("odds"),
               "status": SITE_STATUS.get(src.get("state"), "pending")}
        if src["player"] != pick:
            leg["name"] = src["player"]
        if src.get("team"):
            leg["team"] = src["team"]
        if src.get("market") == "sb":
            leg["market"] = "sb"
        if src.get("php"):
            leg["php"] = src["php"]
        dists = [hr["distance"] for hr in src.get("homeRuns") or [] if hr.get("distance")]
        if dists:
            leg["dist"] = round(max(dists))
        return leg

    def bet_of(slate, src, legs, single=False):
        bet = {"date": slate["date"], "set": 1, "src": "site", "legs": legs}
        if single:
            bet["kind"] = "single"
        for key in ("name", "book"):
            if src.get(key):
                bet[key] = src[key]
        if single and legs[0]["bettor"]:
            bet["book"] = legs[0]["bettor"]
        for key in ("stake", "payout", "returned"):
            if src.get(key) is not None:
                bet[key] = src[key]
        if src.get("outcome") == "hit" and src.get("returned"):
            bet["won"] = src["returned"]
        return bet

    parlays = []
    for slate in slates:
        for p in slate.get("parlays") or []:
            parlays.append(bet_of(slate, p, [leg_of(l) for l in p["legs"]]))
        for s in slate.get("singles") or []:
            parlays.append(bet_of(slate, s, [leg_of(s)], single=True))
    return parlays, extra


def drop_misdated_copies(sheet, site):
    """Sheet slates that are really a recorded slate filed under the day before/after.

    Happened on the very first recorded slate: the tracker dated it 2026-09-18
    (MLB confirms those home runs were hit on the 18th) while the sheet logged
    the same 50 legs under 9/17 -- so the merged history counted it twice. Same
    bettors at the same odds, one day apart, is the same slate; the tracker's
    dating comes from the MLB schedule, so its copy is the one kept.
    """
    def prints(parlays):
        out = {}
        for p in parlays:
            for leg in p["legs"]:
                key = (leg["bettor"], leg["odds"])
                out[key] = out.get(key, 0) + 1
        return out

    site_dates = {p["date"] for p in site}
    drop = set()
    for d in sorted(site_dates):
        mine = prints([p for p in site if p["date"] == d])
        for delta in (-1, 1):
            other = (date.fromisoformat(d) + timedelta(days=delta)).isoformat()
            theirs = prints([p for p in sheet if p["date"] == other])
            if other in site_dates or not theirs:
                continue
            shared = sum(min(n, theirs.get(k, 0)) for k, n in mine.items())
            if shared >= 0.8 * max(sum(mine.values()), sum(theirs.values())):
                drop.add(other)
                print(f"NOTE: the sheet's {other} slate is the same bets the tracker recorded as {d} "
                      f"({shared} matching legs) -- keeping the tracker's copy only.", file=sys.stderr)
    return [p for p in sheet if p["date"] not in drop]


# ---------------- drafting the player map ----------------

def initials(full):
    return "".join(tok[0] for tok in re.split(r"[\s-]+", full) if tok).lower()


def strip_suffix(n):
    return " ".join(t for t in n.split() if t not in ("jr", "sr", "ii", "iii"))


def candidates_for(pick, people):
    key = strip_suffix(norm(pick))
    out = []
    for person in people:
        if (person.get("primaryPosition") or {}).get("type") == "Pitcher":
            continue
        full = norm(person.get("fullName", ""))
        last = strip_suffix(norm(person.get("lastName", "")))
        firsts = {norm(person.get("useName", "")), norm(person.get("firstName", ""))}
        if (key == strip_suffix(full) or key == last or key in firsts
                or (len(key) <= 4 and key == initials(person.get("fullName", "")))
                or (" " in key and difflib.SequenceMatcher(None, key, strip_suffix(full)).ratio() >= 0.86)):
            out.append(person)
    return out


def draft_map(parlays, existing):
    slates = sorted({p["date"] for p in parlays})
    seasons = sorted({d[:4] for d in slates})
    people = []
    for season in seasons:
        people += json.loads(fetch(f"{MLB_API}/sports/1/players?season={season}")).get("people", [])
    people = list({p["id"]: p for p in people}.values())

    counts = {}
    for p in parlays:
        for leg in p["legs"]:
            counts[leg["pick"]] = counts.get(leg["pick"], 0) + 1
    wanted = sorted((k for k, v in counts.items() if v >= GOT_AWAY_MIN_PICKS and k not in existing),
                    key=lambda k: -counts[k])
    print(f"{len(wanted)} unmapped picks with >= {GOT_AWAY_MIN_PICKS} picks\n")
    proposed = {}
    for pick in wanted:
        sheet_days = pick_days(parlays, pick)
        scored = []
        for person in candidates_for(pick, people):
            a, c, absent = agreement(sheet_days, game_log(person["id"], seasons))
            scored.append((c, -a, absent, person))
        scored.sort(key=lambda s: s[:3])
        line = "; ".join(f"{s[3]['fullName']} [agree {-s[1]}, contra {s[0]}, absent {s[2]}]" for s in scored[:4])
        clean = [s for s in scored if s[0] == 0 and -s[1] >= 2]
        unique = len(clean) == 1 or (len(clean) > 1 and clean[0][:3] < clean[1][:3])
        verdict = "OK " if clean and unique else "?? "
        print(f"{verdict}{pick!r} x{counts[pick]} ({len(sheet_days)} resolved days): {line or 'no candidates'}")
        if clean and unique:
            proposed[pick] = {"id": clean[0][3]["id"], "name": clean[0][3]["fullName"]}
    return proposed


# ---------------- output ----------------

def dump(payload):
    """Pretty at the top level, one parlay per line -- keeps daily diffs readable."""
    compact = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    parts = []
    for key, value in payload.items():
        if key in ("parlays",) or (key == "gotAway" and value):
            continue
        parts.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}")
    parts.append('  "parlays": [\n' + ",\n".join("    " + compact(p) for p in payload["parlays"]) + "\n  ]")
    if payload.get("gotAway"):
        ga = dict(payload["gotAway"])
        rows = ga.pop("players")
        head = ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in ga.items())
        parts.append('  "gotAway": {' + head + ', "players": [\n'
                     + ",\n".join("    " + compact(r) for r in rows) + "\n  ]}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def parse_sheet(texts, player_map, today):
    parlays = []
    for text in texts:
        parlays += parse_tab(text, player_map["aliases"], today)
    return parlays


def build(texts, player_map, today, with_mlb=True, fetcher=fetch, recorded=None, sheet_parlays=None):
    """texts: the sheet's CSV tabs (or pass already-parsed `sheet_parlays`).
    recorded: slates from data/results/ -- they replace the sheet on their dates."""
    parlays = list(sheet_parlays) if sheet_parlays is not None else parse_sheet(texts, player_map, today)
    players = dict(player_map["players"])
    recorded = recorded or []
    if recorded:
        site, extra = recorded_parlays(recorded, players)
        covered = {s["date"] for s in recorded}
        dropped = [p for p in parlays if p["date"] in covered]
        if dropped:
            print(f"NOTE: {len(dropped)} sheet parlay(s) on {len({p['date'] for p in dropped})} date(s) the tracker "
                  f"recorded itself were dropped in favour of the tracker's record.", file=sys.stderr)
        parlays = [p for p in parlays if p["date"] not in covered]
        parlays = drop_misdated_copies(parlays, site) + site
        players.update(extra)
    # Stable sort: by slate, keeping each tab's own row order within a day.
    parlays.sort(key=lambda p: (p["date"], p["set"]))
    if not parlays:
        raise ValueError("parsed no parlays -- refusing to write an empty history")
    slates = sorted({p["date"] for p in parlays})
    with_odds = sorted(p["date"] for p in parlays if any(leg["odds"] is not None for leg in p["legs"]))
    payload = {
        "firstSlate": slates[0],
        "lastSlate": slates[-1],
        "oddsFrom": with_odds[0] if with_odds else None,
        # first slate the tracker recorded itself: real stakes, payouts and singles exist from here on
        "recordedFrom": recorded[0]["date"] if recorded else None,
        "parlays": parlays,
        "gotAway": got_away(parlays, players, fetcher) if with_mlb and players else None,
    }
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", help="read archive.csv / log.csv from here instead of downloading")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--results-dir", default=str(RESULTS_DIR), help="recorded slates to merge in (data/results)")
    ap.add_argument("--no-mlb", action="store_true", help="skip the MLB game-log join")
    ap.add_argument("--draft-map", action="store_true", help="propose history_player_map.json entries and exit")
    ap.add_argument("--allow-shrink", action="store_true", help="write even if the result has fewer parlays than the existing file")
    args = ap.parse_args()

    player_map = load_map()
    today = date.today()
    out = Path(args.out)

    # The sheet is the legacy source. If it can't be read, carry on from the copy
    # of it already in history.json rather than blocking newly recorded slates.
    texts, sheet_parlays = None, None
    try:
        if args.from_dir:
            texts = [(Path(args.from_dir) / f"{name}.csv").read_text(encoding="utf-8") for name, _ in TABS]
        else:
            texts = [fetch(EXPORT_URL.format(sid=SHEET_ID, gid=gid)) for _, gid in TABS]
        sheet_parlays = parse_sheet(texts, player_map, today)
    except (OSError, ValueError) as err:
        if args.draft_map or not out.exists():
            raise
        sheet_parlays = [p for p in json.loads(out.read_text(encoding="utf-8")).get("parlays", []) if p.get("src") != "site"]
        print(f"WARNING: couldn't read the sheet ({err}). Reusing the {len(sheet_parlays)} sheet parlays already in "
              f"{out.name}; recorded slates are still merged.", file=sys.stderr)

    if args.draft_map:
        parlays = []
        for text in texts:
            parlays += parse_tab(text, player_map["aliases"], today)
        proposed = draft_map(parlays, player_map["players"])
        print("\nProposed entries (review before adding to scripts/history_player_map.json):")
        print(json.dumps(proposed, indent=2, ensure_ascii=False))
        return

    recorded = load_recorded(args.results_dir)
    payload = build(texts, player_map, today, with_mlb=not args.no_mlb, recorded=recorded, sheet_parlays=sheet_parlays)
    if recorded:
        print(f"Merged {len(recorded)} slate(s) recorded by the tracker ({recorded[0]['date']} .. {recorded[-1]['date']}).")
    legs = sum(len(p["legs"]) for p in payload["parlays"])
    print(f"Parsed {len(payload['parlays'])} parlays ({legs} legs), "
          f"{payload['firstSlate']} .. {payload['lastSlate']}.")
    if payload["gotAway"]:
        ga = payload["gotAway"]
        print(f"Got-away join: {len(ga['players'])} players; sheet and MLB agree on "
              f"{ga['agreeingPickDays']} of {ga['checkedPickDays']} pick-days.")
    # History only ever grows. Fewer parlays than last time means rows were
    # deleted or the sheet layout moved -- a person should look, not a cron job.
    if out.exists() and not args.allow_shrink:
        try:
            before = len(json.loads(out.read_text(encoding="utf-8")).get("parlays", []))
        except ValueError:
            before = 0
        if len(payload["parlays"]) < before:
            sys.exit(f"REFUSING to write: {len(payload['parlays'])} parlays parsed but {out.name} already has "
                     f"{before}. Check the sheet, or re-run with --allow-shrink if this is intended.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(payload), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
