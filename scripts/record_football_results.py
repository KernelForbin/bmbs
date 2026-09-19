#!/usr/bin/env python3
"""
Football's results archive: records a finished NFL week of touchdown picks into
data/football/results/<weekEnds>.json (one file per week, kept forever), then
rebuilds data/football/history.json -- the file football/history/index.html
reads -- from every week on file.

Football has no spreadsheet behind it: this IS the history, from the first week
recorded onward. A standalone sibling of record_results.py (baseball), sharing
no code with it, same as the rest of the football side.

Grading is a port of football/index.html (getGameSnapshot, linkPicks,
stateForPlayer, evaluateTicket), so the record says what the site said:
  * a touchdown = the boxscore TD column over rushing, receiving, defensive,
    interceptions, kickReturns, puntReturns. Passing never counts.
  * picks match by ESPN athlete id first; by name only if the touchdown was
    scored for the pick's own team (there are two Josh Allens).
  * game over, has a stat line, no TD -> miss. No stat line -> ask ESPN's
    per-game roster: didNotPlay -> "na" (void), dressed -> miss.

Per week it keeps: every parlay and single as posted (card, who placed it,
stake, listed payout, legs with player / team / bettor / odds / athlete id),
each leg's result, stat line and touchdowns, each bet's outcome and what it
returned, and every touchdown scored in the league that week (so "who scored
when nobody had him" can be built later without another data source).

When: once every game on every date of the slate is Final -- for a normal week
that is the morning after Monday night -- or two days past the slate's end
regardless (a record made that way is marked "complete": false and re-graded on
later runs). It looks at data/football/tickets.json and tickets-previous.json.
A week already recorded from the same picks is skipped without touching the
network. A second card in the same week replaces the first on the site, and so
it does here: the file is named for the week, and the newest card's record wins.

Run:
    python scripts/record_football_results.py            # record what's ready, rebuild history
    python scripts/record_football_results.py --force    # re-grade even if recorded
    python scripts/record_football_results.py --tickets FILE --out-dir DIR --history FILE   # tests
"""
import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone(timedelta(hours=-5))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "football"
RESULTS_DIR = DATA / "results"
HISTORY_PATH = DATA / "history.json"
SLATE_FILES = [DATA / "tickets-previous.json", DATA / "tickets.json"]   # oldest first
ESPN = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
TD_CATEGORIES = ("rushing", "receiving", "defensive", "interceptions", "kickReturns", "puntReturns")
SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")
BACKSTOP_DAYS = 2


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-football-recorder"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def normalize_name(name):
    """Must match normalizeName() in football/index.html: suffixes ARE stripped."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace(".", "").replace("'", "")
    return SUFFIX_RE.sub("", " ".join(s.split()).lower())


def who_name(raw):
    key = re.split("[" + chr(0x2014) + "-]", raw or "")[0].strip()   # em dash or hyphen
    return key[:1].upper() + key[1:].lower()


def odds_to_number(odds):
    if odds is None or odds == "":
        return None
    try:
        return int(str(odds).replace("+", "").replace(",", ""))
    except ValueError:
        return None


def american_to_decimal(odds):
    if odds is None:
        return None
    return 1 + odds / 100 if odds >= 0 else 1 + 100 / abs(odds)


def week_end(day_iso):
    """The Tuesday on or after `day_iso`: an NFL week runs Wednesday..Tuesday."""
    day = date.fromisoformat(day_iso)
    return (day + timedelta(days=(1 - day.weekday()) % 7)).isoformat()


def slate_dates(tickets):
    end = tickets.get("endDate") if (tickets.get("endDate") or "") >= tickets["date"] else tickets["date"]
    first = date.fromisoformat(tickets["date"])
    days = [(first + timedelta(days=i)).isoformat() for i in range(7)]
    return [d for d in days if d <= end]


# ---------------- one game ----------------

def stat_num(stats, labels, name):
    try:
        return int(stats[labels.index(name)])
    except (ValueError, IndexError):
        return 0


def game_snapshot(game, summary):
    """football/index.html's getGameSnapshot(), minus the live-drive parts."""
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    state = ((comp.get("status") or {}).get("type") or {}).get("state") or ("post" if game["final"] else "pre" if game["preview"] else "in")
    status = "final" if state == "post" else "preview" if state == "pre" else "live"
    teams = {}
    for c in comp.get("competitors") or []:
        teams[c.get("homeAway")] = {"id": str(c.get("id")), "abbr": (c.get("team") or {}).get("abbreviation") or "", "score": c.get("score") or "0"}
    for side in ("away", "home"):
        teams.setdefault(side, game["teams"].get(side) or {"id": "", "abbr": "", "score": "0"})

    lines = {}
    for side in (summary.get("boxscore") or {}).get("players") or []:
        abbr = (side.get("team") or {}).get("abbreviation") or ""
        for cat in side.get("statistics") or []:
            labels = cat.get("labels") or []
            for row in cat.get("athletes") or []:
                a = row.get("athlete") or {}
                if not a.get("id"):
                    continue
                aid = str(a["id"])
                line = lines.setdefault(aid, {"id": aid, "name": a.get("displayName") or "", "norm": normalize_name(a.get("displayName")),
                                              "team": abbr, "car": 0, "rushYds": 0, "rec": 0, "recYds": 0, "tgts": 0, "td": 0})
                stats = row.get("stats") or []
                if cat.get("name") == "rushing":
                    line["car"], line["rushYds"] = stat_num(stats, labels, "CAR"), stat_num(stats, labels, "YDS")
                if cat.get("name") == "receiving":
                    line["rec"], line["recYds"], line["tgts"] = (stat_num(stats, labels, n) for n in ("REC", "YDS", "TGTS"))
                if cat.get("name") in TD_CATEGORIES:
                    line["td"] += stat_num(stats, labels, "TD")

    touchdowns, td_name_teams = [], {}
    for sp in summary.get("scoringPlays") or []:
        kind = (sp.get("type") or {}).get("text") or ""
        if not re.search("touchdown", kind, re.IGNORECASE):
            continue
        text = sp.get("text") or ""
        m = re.match(r"^(.+?)\s+(-?\d+)\s+Yd\.?\s+(.+?)(?:\s*\(|$)", text, re.IGNORECASE)
        scorer = m.group(1).strip() if m else ""
        abbr = (sp.get("team") or {}).get("abbreviation") or ""
        passer = re.search(r"pass from\s+(.+?)\s*(?:\(|$)", text, re.IGNORECASE)
        if scorer:
            td_name_teams.setdefault(normalize_name(scorer), set()).add(abbr)
        touchdowns.append({
            "id": str(sp.get("id")), "gamePk": game["gamePk"], "date": game["date"],
            "scorer": scorer or text, "team": abbr, "kind": kind,
            "yards": int(m.group(2)) if m else None, "how": m.group(3).strip() if m else "",
            "passer": passer.group(1) if passer else "", "text": text,
            "period": (sp.get("period") or {}).get("number") or 0, "clock": (sp.get("clock") or {}).get("displayValue") or "",
            "score": f"{teams['away']['abbr']} {sp.get('awayScore')} - {teams['home']['abbr']} {sp.get('homeScore')}",
        })
    return {"status": status, "gamePk": game["gamePk"], "date": game["date"], "teams": teams,
            "lines": lines, "touchdowns": touchdowns, "tdNameTeams": td_name_teams,
            "matchup": f"{teams['away']['abbr']} @ {teams['home']['abbr']}",
            "final": f"{teams['away']['abbr']} {teams['away']['score']}, {teams['home']['abbr']} {teams['home']['score']}"}


# ---------------- one slate ----------------

def picks_of(tickets):
    out = []
    for w in tickets.get("windows") or []:
        for t in w.get("tickets") or []:
            out += [l for l in t.get("legs") or [] if l.get("player")]
    out += [s for s in tickets.get("singles") or [] if s.get("player")]
    return [{"name": p["player"], "norm": normalize_name(p["player"]),
             "id": str(p["athleteId"]) if p.get("athleteId") else "", "team": p.get("team") or ""} for p in out]


def poll_slate(tickets, fetcher=fetch_json):
    games = []
    for day in slate_dates(tickets):
        data = fetcher(f"{ESPN}/scoreboard?dates={day.replace('-', '')}")
        for ev in data.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            state = ((ev.get("status") or {}).get("type") or {}).get("state")
            teams = {c.get("homeAway"): {"id": str(c.get("id")), "abbr": (c.get("team") or {}).get("abbreviation") or "", "score": c.get("score") or "0"}
                     for c in comp.get("competitors") or []}
            games.append({"gamePk": str(ev.get("id")), "date": day, "final": state == "post", "preview": state == "pre", "teams": teams})

    results = {"games": len(games), "allScheduledFinal": all(g["final"] for g in games), "allGamesFinal": True,
               "gameInfo": {}, "gameByTeam": {}, "gameByAthlete": {}, "hitIds": set(), "lineIds": set(),
               "tdNameTeams": {}, "touchdowns": [], "participation": {}}
    for g in games:
        if g["preview"]:
            results["allGamesFinal"] = False
            continue
        snap = game_snapshot(g, fetcher(f"{ESPN}/summary?event={g['gamePk']}"))
        results["gameInfo"][snap["gamePk"]] = snap
        for side in ("away", "home"):
            if snap["teams"][side].get("abbr"):
                results["gameByTeam"][snap["teams"][side]["abbr"]] = snap["gamePk"]
        for line in snap["lines"].values():
            results["lineIds"].add(line["id"])
            results["gameByAthlete"][line["id"]] = snap["gamePk"]
            if line["td"] > 0:
                results["hitIds"].add(line["id"])
        for norm, teams in snap["tdNameTeams"].items():
            results["tdNameTeams"].setdefault(norm, set()).update(teams)
        results["touchdowns"] += snap["touchdowns"]
        if snap["status"] != "final":
            results["allGamesFinal"] = False

    # Final game, no stat line, and we know who he is: ask the roster whether he dressed.
    rosters = {}
    for p in picks_of(tickets):
        if not p["id"] or p["id"] in results["lineIds"]:
            continue
        snap = results["gameInfo"].get(results["gameByTeam"].get(p["team"]))
        if not snap or snap["status"] != "final":
            continue
        team = next((t for t in snap["teams"].values() if t.get("abbr") == p["team"]), None)
        if not team:
            continue
        key = (snap["gamePk"], team["id"])
        if key not in rosters:
            data = fetcher(f"{ESPN_CORE}/events/{key[0]}/competitions/{key[0]}/competitors/{key[1]}/roster")
            rosters[key] = {str(e["playerId"]): e.get("didNotPlay") is False for e in data.get("entries") or [] if e.get("playerId")}
        results["participation"][p["id"]] = rosters[key].get(p["id"]) is True
    return results


def game_for_pick(results, pick):
    pk = (pick["id"] and results["gameByAthlete"].get(pick["id"])) or (pick["team"] and results["gameByTeam"].get(pick["team"]))
    return results["gameInfo"].get(pk) if pk else None


def line_for_pick(results, pick):
    game = game_for_pick(results, pick)
    if not game:
        return None
    if pick["id"] and pick["id"] in game["lines"]:
        return game["lines"][pick["id"]]
    return next((l for l in game["lines"].values() if l["norm"] == pick["norm"] and (not pick["team"] or l["team"] == pick["team"])), None)


def grade_player(results, pick):
    """hit | miss | na | live | not_started, exactly as football/index.html's stateForPlayer()."""
    by_id = bool(pick["id"]) and pick["id"] in results["hitIds"]
    scored_for = results["tdNameTeams"].get(pick["norm"])
    by_name = bool(scored_for) and (not pick["team"] or pick["team"] in scored_for)
    if by_id or by_name:
        return "hit"
    game = game_for_pick(results, pick)
    if not game:
        return "na" if results["allGamesFinal"] else "not_started"
    if game["status"] == "preview":
        return "not_started"
    if game["status"] == "live":
        return "live"
    if line_for_pick(results, pick):
        return "miss"
    if pick["id"] and pick["id"] in results["participation"]:
        return "miss" if results["participation"][pick["id"]] else "na"
    return "miss" if pick["id"] else "na"


def evaluate_ticket(stake, payout, legs):
    states = [s for s, _ in legs]
    na, miss, hit = states.count("na"), states.count("miss"), states.count("hit")
    active = len(states) - na
    if active == 0:
        return "void", stake
    if miss:
        return "dead", 0.0
    if hit != active:
        return "live", None
    adjusted = payout
    if na:
        product = 1.0
        for state, odds in legs:
            dec = american_to_decimal(odds) if state != "na" else None
            if dec is not None:
                product *= dec
        adjusted = (stake or 0) * product
    return "hit", adjusted


def round2(v):
    return None if v is None else round(float(v) + 1e-9, 2)


def grade_leg(results, src):
    pick = {"name": src.get("player") or "", "norm": normalize_name(src.get("player")),
            "id": str(src["athleteId"]) if src.get("athleteId") else "", "team": src.get("team") or ""}
    state = grade_player(results, pick)
    leg = {"player": pick["name"], "team": pick["team"], "who": who_name(src.get("who")),
           "odds": odds_to_number(src.get("odds")), "state": state, "athleteId": pick["id"] or None}
    if src.get("time"):
        leg["time"] = src["time"]
    game = game_for_pick(results, pick)
    if game:
        leg["game"], leg["gameDate"] = game["matchup"], game["date"]
    line = line_for_pick(results, pick)
    if line:
        leg["team"] = leg["team"] or line["team"]
        leg["line"] = {k: line[k] for k in ("car", "rushYds", "rec", "recYds", "tgts", "td")}
    if state == "hit":
        keep = ("kind", "yards", "how", "passer", "period", "clock", "text")
        leg["touchdowns"] = [{k: td[k] for k in keep if td.get(k) not in (None, "")} for td in results["touchdowns"]
                             if normalize_name(td["scorer"]) == pick["norm"] and (not pick["team"] or td["team"] == pick["team"])]
    return leg


def tickets_hash(tickets):
    return hashlib.sha1(json.dumps(tickets, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def grade_slate(tickets, results):
    parlays, singles = [], []
    for w in tickets.get("windows") or []:
        for tk in w.get("tickets") or []:
            legs = [grade_leg(results, l) for l in tk.get("legs") or []]
            if not legs:
                continue
            outcome, returned = evaluate_ticket(tk.get("stake"), tk.get("payout"), [(l["state"], l["odds"]) for l in legs])
            parlays.append({"name": tk.get("name") or "", "window": w.get("title") or "", "sub": tk.get("sub") or "",
                            "book": tk.get("book") or "", "stake": tk.get("stake"), "payout": tk.get("payout"),
                            "outcome": outcome, "returned": round2(returned), "legs": legs})
    for s in tickets.get("singles") or []:
        leg = grade_leg(results, s)
        outcome, returned = evaluate_ticket(s.get("stake"), s.get("payout"), [(leg["state"], leg["odds"])])
        singles.append(dict(leg, stake=s.get("stake"), payout=s.get("payout"), outcome=outcome, returned=round2(returned)))

    bets = parlays + singles
    all_legs = [l for p in parlays for l in p["legs"]] + singles
    def ours(td):
        return any(normalize_name(l["player"]) == normalize_name(td["scorer"]) and (not l["team"] or l["team"] == td["team"]) for l in all_legs)
    return {
        "sport": "football",
        "weekEnds": tickets.get("weekEnds") or week_end(tickets["date"]),
        "date": tickets["date"],
        "endDate": slate_dates(tickets)[-1],
        "complete": all(b["outcome"] != "live" for b in bets),
        "ticketsHash": tickets_hash(tickets),
        "summary": {
            "games": results["games"],
            "parlays": len(parlays), "parlaysCashed": sum(1 for p in parlays if p["outcome"] == "hit"),
            "singles": len(singles), "singlesCashed": sum(1 for s in singles if s["outcome"] == "hit"),
            "legs": len(all_legs), "legsHit": sum(1 for l in all_legs if l["state"] == "hit"),
            "legsVoid": sum(1 for l in all_legs if l["state"] == "na"),
            "staked": round2(sum(b["stake"] or 0 for b in bets)),
            "returned": round2(sum(b["returned"] or 0 for b in bets)),
            "ourTouchdowns": sum(1 for td in results["touchdowns"] if ours(td)),
            "leagueTouchdowns": len(results["touchdowns"]),
        },
        "note": tickets.get("note") or "",
        "parlays": parlays,
        "singles": singles,
        "games": [{"gamePk": g["gamePk"], "date": g["date"], "final": g["final"]} for g in results["gameInfo"].values()],
        "touchdowns": results["touchdowns"],
    }


# ---------------- recording ----------------

def today_et():
    return datetime.now(ET).date()


def record(tickets, out_dir, today, fetcher=fetch_json, force=False):
    day = tickets.get("date")
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return "skipped: slate has no date"
    week = tickets.get("weekEnds") or week_end(day)
    out = Path(out_dir) / f"{week}.json"
    if out.exists() and not force:
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        if old.get("complete") and old.get("ticketsHash") == tickets_hash(tickets):
            return f"week ending {week}: already recorded"
    if day > today.isoformat():
        return f"week ending {week}: hasn't kicked off yet"

    results = poll_slate(tickets, fetcher)
    end = slate_dates(tickets)[-1]
    if not results["allScheduledFinal"] and today < date.fromisoformat(end) + timedelta(days=BACKSTOP_DAYS):
        return f"week ending {week}: games still to be played -- not recorded yet"
    payload = grade_slate(tickets, results)
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return f"week ending {week}: unchanged"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    s = payload["summary"]
    return (f"week ending {week}: RECORDED {s['legsHit']}/{s['legs']} legs hit, {s['parlaysCashed']}/{s['parlays']} parlays and "
            f"{s['singlesCashed']}/{s['singles']} singles cashed, ${s['staked']:.2f} staked -> ${s['returned']:.2f} returned"
            + ("" if payload["complete"] else "  [INCOMPLETE: a game never went final; will re-grade]"))


# ---------------- history.json ----------------

STATUS = {"hit": "hit", "miss": "miss", "na": "dnp"}   # anything else is pending


def build_history(results_dir):
    """Every recorded week, in the shape football/history/index.html reads --
    deliberately the same shape as baseball's data/history.json (bets with legs
    of bettor / pick / odds / status), so the two History pages stay siblings."""
    weeks = []
    for path in sorted(Path(results_dir).glob("*.json")) if Path(results_dir).is_dir() else []:
        week = json.loads(path.read_text(encoding="utf-8"))
        if week.get("sport") == "football" and week.get("weekEnds"):
            weeks.append(week)
    weeks.sort(key=lambda w: w["weekEnds"])

    def leg_of(src):
        leg = {"bettor": src.get("who") or "", "pick": src["player"], "odds": src.get("odds"),
               "status": STATUS.get(src.get("state"), "pending")}
        if src.get("team"):
            leg["team"] = src["team"]
        tds = src.get("touchdowns") or []
        if tds:
            leg["tds"] = len(tds)
            yards = [td["yards"] for td in tds if isinstance(td.get("yards"), int)]
            if yards:
                leg["yards"] = max(yards)
        return leg

    def bet_of(week, src, legs, single=False):
        bet = {"date": week["date"], "week": week["weekEnds"], "set": 1, "src": "site", "legs": legs}
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

    bets = []
    for week in weeks:
        bets += [bet_of(week, p, [leg_of(l) for l in p["legs"]]) for p in week.get("parlays") or []]
        bets += [bet_of(week, s, [leg_of(s)], single=True) for s in week.get("singles") or []]
    return {
        "sport": "football",
        "firstSlate": weeks[0]["date"] if weeks else None,
        "lastSlate": weeks[-1]["date"] if weeks else None,
        "weeks": [{"week": w["weekEnds"], "date": w["date"], "endDate": w.get("endDate") or w["date"],
                   "ourTouchdowns": w["summary"].get("ourTouchdowns"), "leagueTouchdowns": w["summary"].get("leagueTouchdowns")} for w in weeks],
        "recordedFrom": weeks[0]["date"] if weeks else None,
        "parlays": bets,
    }


def dump_history(payload):
    compact = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    parts = [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in payload.items() if k not in ("parlays", "weeks")]
    parts.append('  "weeks": [' + ("\n" + ",\n".join("    " + compact(w) for w in payload["weeks"]) + "\n  " if payload["weeks"] else "") + "]")
    parts.append('  "parlays": [' + ("\n" + ",\n".join("    " + compact(p) for p in payload["parlays"]) + "\n  " if payload["parlays"] else "") + "]")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickets", action="append", help="slate file(s) to consider instead of data/football/tickets*.json")
    ap.add_argument("--out-dir", default=str(RESULTS_DIR))
    ap.add_argument("--history", default=str(HISTORY_PATH))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    failed = False
    slates = []
    for path in [Path(p) for p in args.tickets] if args.tickets else SLATE_FILES:
        if path.exists():
            try:
                slates.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except ValueError as err:
                failed = True
                print(f"ERROR reading {path.name}: {err}", file=sys.stderr)
    seen = set()
    for path, tickets in slates:
        key = tickets.get("weekEnds") or tickets.get("date")
        if key in seen:
            continue
        seen.add(key)
        try:
            print(record(tickets, args.out_dir, today_et(), force=args.force))
        except Exception as err:   # one week failing must not stop the other, or the history rebuild
            failed = True
            print(f"ERROR recording {path.name}: {err}", file=sys.stderr)
    if not slates:
        print("No football slates on hand -- nothing to record.")

    history = build_history(args.out_dir)
    text = dump_history(history)
    out = Path(args.history)
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out.name}: {len(history['weeks'])} week(s), {len(history['parlays'])} bets.")
    else:
        print(f"{out.name} unchanged ({len(history['weeks'])} week(s)).")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
