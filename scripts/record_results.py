#!/usr/bin/env python3
"""
Records a finished baseball slate -- the picks AND how they turned out -- into
data/results/<date>.json, one file per slate, kept forever.

Why this exists: the live page grades every pick in the visitor's own browser
and keeps nothing. Once a slate rolls off the Yesterday tab it is gone. This
script grades the same slate once more, server-side, and writes it down. It is
a port of index.html's grading (stateForPlayer, Pinch Hit Protection,
evaluateTicket), on purpose: the archive should say what the site said. If the
page's rules change, change them here too -- tests/test_record_results.py pins
the behaviours that matter.

ONE DELIBERATE DIFFERENCE: a player who sat on the bench all game. MLB's
boxscore lists the whole active roster, so the page (which asks only "is he in
the boxscore?") shows a benched player as a miss. He didn't play; books void
that bet. Found on the first slate ever recorded (Andres Gimenez, 2026-09-18:
the page said miss, the group's own sheet correctly said DNP). The permanent
record gets it right: on a Final game's roster but never came to the plate
(benched, or only a pinch runner / late defensive sub) -> "na".

STOLEN BASE LEGS (`"market": "sb"` on the leg; no market = home run) are graded
the way index.html's stateForSteal() grades them: a stolen_base_* runner event
is a hit; NO Pinch Hit Protection; and "played" means APPEARED IN THE GAME
(holds a batting-order spot), not "came to the plate" -- a pinch runner can
steal without batting. On a finished game's roster without getting in -> "na".

Those files are the permanent record. scripts/import_history.py folds them
into data/history.json (what the History page reads) next to the older slates
imported from the group's sheet.

What gets recorded, per slate:
  * every parlay and single as posted: card name, who placed it, stake, listed
    payout, and each leg's player / team / bettor / odds / game time
  * each leg's result (hit / miss / na), the player's MLB id, whether a hit
    came through Pinch Hit Protection (and whose home run it was), and the
    Statcast detail of every home run he hit
  * each bet's outcome and what it actually returned (void legs re-priced the
    same way the page does it; a fully void bet returns its stake)
  * every home run hit in the league that day (the Home Run Log), so "who went
    deep when nobody had them" never needs a second data source

When: a slate is recorded once every game on its date is Final -- or two days
later regardless, as a backstop for a suspended game (that record is marked
"complete": false and is re-graded on later runs). It looks at the two slates
the site holds, data/tickets.json and data/tickets-previous.json; a slate stays
in one of those for at least a day after it ends, so a once-a-day run sees every
slate. A slate already recorded from the same picks is skipped without touching
the network; if the picks were corrected afterwards it is graded again.

Run:
    python scripts/record_results.py                  # whatever is ready
    python scripts/record_results.py --force          # re-grade even if recorded
    python scripts/record_results.py --tickets FILE --out-dir DIR   # tests / one-offs
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
except Exception:  # no tz database (bare Windows Python): close enough for a two-day backstop
    ET = timezone(timedelta(hours=-5))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESULTS_DIR = DATA / "results"
SLATE_FILES = [DATA / "tickets-previous.json", DATA / "tickets.json"]   # oldest first
MLB_API = "https://statsapi.mlb.com/api"
BACKSTOP_DAYS = 2


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-results-recorder"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def normalize_name(name):
    """Must match normalizeName() in index.html: suffixes are NOT stripped."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[.']", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def who_name(raw):
    """whoKey() + whoDisplayName() from index.html: 'KENNY' -> 'Kenny'."""
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


# ---------------- one game ----------------

def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def game_snapshot(game_pk, feed):
    """index.html's getGameSnapshot(), minus the live-at-bat parts."""
    game_data = feed.get("gameData") or {}
    abstract = ((game_data.get("status") or {}).get("abstractGameState")) or ""
    status = "final" if abstract == "Final" else "preview" if abstract == "Preview" else "live"
    venue = (game_data.get("venue") or {}).get("name") or ""
    weather = game_data.get("weather") or {}
    teams = game_data.get("teams") or {}

    def abbr(side):
        t = teams.get(side) or {}
        return t.get("abbreviation") or t.get("teamName") or ""

    hr_names, home_runs = set(), []
    plays = ((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or []
    for play in plays:
        result = play.get("result") or {}
        if result.get("eventType") != "home_run" and result.get("event") != "Home Run":
            continue
        matchup = play.get("matchup") or {}
        batter = (matchup.get("batter") or {}).get("fullName")
        if not batter:
            continue
        hr_names.add(normalize_name(batter))
        about = play.get("about") or {}
        events = play.get("playEvents") or []
        scoring = next((e for e in reversed(events) if e and e.get("hitData")), None) \
            or next((e for e in reversed(events) if e and e.get("isPitch")), None) or {}
        hit, pitch, details = scoring.get("hitData") or {}, scoring.get("pitchData") or {}, scoring.get("details") or {}
        home_runs.append({
            "id": f"{game_pk}-{play.get('atBatIndex')}",
            "time": about.get("endTime") or about.get("startTime") or "",
            "batter": batter,
            "batterId": (matchup.get("batter") or {}).get("id"),
            "batSide": (matchup.get("batSide") or {}).get("code") or "",
            "team": abbr("away" if about.get("isTopInning") else "home"),
            "pitcher": (matchup.get("pitcher") or {}).get("fullName") or "",
            "pitchHand": (matchup.get("pitchHand") or {}).get("code") or "",
            "inning": about.get("inning"),
            "half": about.get("halfInning") or "",
            "venue": venue,
            "wind": weather.get("wind") or "",
            "temp": weather.get("temp") or "",
            "condition": weather.get("condition") or "",
            "exitVelo": num(hit.get("launchSpeed")),
            "launchAngle": num(hit.get("launchAngle")),
            "distance": num(hit.get("totalDistance")),
            "trajectory": hit.get("trajectory") or "",
            "pitchType": (details.get("type") or {}).get("description") or "",
            "pitchVelo": num(pitch.get("startSpeed")),
            "zone": num(pitch.get("zone")),
            "gamePk": game_pk,
        })

    # Stolen bases: runner events inside somebody else's plate appearance. One
    # steal can appear as several runner entries (one per segment), hence the key.
    sb_names, steals, seen_steals = set(), [], set()
    for play in plays:
        about = play.get("about") or {}
        for r in play.get("runners") or []:
            det = r.get("details") or {}
            etype = det.get("eventType") or ""
            name = (det.get("runner") or {}).get("fullName")
            stole, caught = etype.startswith("stolen_base"), "caught_stealing" in etype
            if not name or not (stole or caught):
                continue
            key = (play.get("atBatIndex"), det.get("playIndex"), normalize_name(name))
            if key in seen_steals:
                continue
            seen_steals.add(key)
            if stole:
                sb_names.add(normalize_name(name))
            steals.append({"runner": name, "runnerId": (det.get("runner") or {}).get("id"), "caught": caught,
                           "base": "home" if "home" in etype else "3rd" if "3b" in etype else "2nd",
                           "inning": about.get("inning"), "half": about.get("halfInning") or "",
                           "pitcher": ((play.get("matchup") or {}).get("pitcher") or {}).get("fullName") or "",
                           "team": abbr("away" if about.get("isTopInning") else "home"), "gamePk": game_pk})

    roster = {}         # normalized name -> MLB person id (everyone in the boxscore)
    played = set()      # ...of whom, those who actually got into the game
    bat_stats = {}      # normalized name -> batting line, for stat props
    pitch_stats = {}    # normalized name -> pitching line
    order_slot = {}     # normalized name -> {slot, side, orderFull}
    slot_holders = {"away": {}, "home": {}}
    box_teams = ((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}
    for side in ("away", "home"):
        for p in ((box_teams.get(side) or {}).get("players") or {}).values():
            person = p.get("person") or {}
            full = person.get("fullName")
            if not full:
                continue
            norm = normalize_name(full)
            roster[norm] = person.get("id")
            stats = p.get("stats") or {}
            batting = stats.get("batting") or {}
            if batting:
                bat_stats[norm] = {k: batting.get(k) or 0 for k in
                                   ("hits", "runs", "rbi", "totalBases", "doubles", "triples", "homeRuns")}
            pitching = stats.get("pitching") or {}
            if "strikeOuts" in pitching:
                pitch_stats[norm] = {"strikeOuts": pitching.get("strikeOuts") or 0}
            if "plateAppearances" in batting:
                # a home run bet needs a plate appearance: a pinch runner or late
                # defensive sub who never batted is void at the books, same as the bench
                if batting["plateAppearances"] > 0:
                    played.add(norm)
            elif p.get("battingOrder") or p.get("allPositions") or batting:
                played.add(norm)
            if p.get("battingOrder"):
                order = str(p["battingOrder"])
                try:
                    slot, order_full = int(order[0]), int(order)
                except ValueError:
                    continue
                if 1 <= slot <= 9:
                    order_slot[norm] = {"slot": slot, "side": side, "orderFull": order_full}
                    slot_holders[side].setdefault(slot, []).append({"name": full, "orderFull": order_full})

    # The score, for game lines. Keyed by team ABBREVIATION, the same key
    # leg.team uses. A game line is ONLY ever settled on a FINAL game, so the
    # status rides along and the graders refuse to call anything before then.
    line_teams = ((feed.get("liveData") or {}).get("linescore") or {}).get("teams") or {}
    team_scores = {}
    for side in ("away", "home"):
        other = "home" if side == "away" else "away"
        mine, theirs = line_teams.get(side) or {}, line_teams.get(other) or {}
        if abbr(side):
            team_scores[abbr(side)] = {
                "runs": mine.get("runs"), "oppRuns": theirs.get("runs"),
                "opponent": abbr(other) or "", "status": status,
            }

    return {"status": status, "gamePk": game_pk, "hrNames": hr_names, "homeRuns": home_runs, "roster": roster, "played": played,
            "sbNames": sb_names, "steals": steals, "batStats": bat_stats, "pitchStats": pitch_stats,
            "teamScores": team_scores,
            "orderSlot": order_slot, "slotHolders": slot_holders,
            "matchup": f"{abbr('away')} @ {abbr('home')}" if abbr("away") and abbr("home") else ""}


# ---------------- one slate ----------------

def poll_slate(day, fetcher=fetch_json):
    """index.html's pollSlate(): every game on the date, folded into one results object."""
    sched = fetcher(f"{MLB_API}/v1/schedule?sportId=1&date={day}")
    games = []
    for d in sched.get("dates") or []:
        for g in d.get("games") or []:
            abstract = ((g.get("status") or {}).get("abstractGameState")) or ""
            games.append({"gamePk": g["gamePk"], "final": abstract == "Final", "preview": abstract == "Preview"})

    results = {"hitNames": set(), "sbNames": set(), "steals": [], "inBox": {}, "appeared": set(), "rosterStatus": {}, "rosterSide": {}, "rosterIds": {}, "substitutedOut": set(),
               "batStats": {}, "pitchStats": {}, "teamScores": {},
               "homeRuns": [], "gameInfo": {}, "games": len(games),
               "allScheduledFinal": all(g["final"] for g in games), "allGamesFinal": True}
    for g in games:
        if g["preview"]:
            results["allGamesFinal"] = False
            continue
        snap = game_snapshot(g["gamePk"], fetcher(f"{MLB_API}/v1.1/game/{g['gamePk']}/feed/live"))
        results["hitNames"] |= snap["hrNames"]
        results["homeRuns"] += snap["homeRuns"]
        results["sbNames"] |= snap["sbNames"]
        results["steals"] += snap["steals"]
        if snap["status"] != "preview":
            for norm, pid in snap["roster"].items():
                results["rosterIds"][norm] = pid
                results["inBox"][norm] = snap["status"]     # steal legs ask "was he there", not "did he bat"
                # benched all game in a game that's over = didn't play (see the docstring)
                if snap["status"] != "final" or norm in snap["played"]:
                    results["rosterStatus"][norm] = snap["status"]
            for norm, info in snap["orderSlot"].items():
                results["appeared"].add(norm)
                results["rosterSide"][norm] = {"side": info["side"], "gamePk": snap["gamePk"]}
                holders = snap["slotHolders"][info["side"]].get(info["slot"], [])
                if holders and max(h["orderFull"] for h in holders) > info["orderFull"]:
                    results["substitutedOut"].add(norm)
        results["batStats"].update(snap.get("batStats") or {})
        results["pitchStats"].update(snap.get("pitchStats") or {})
        for a, row in (snap.get("teamScores") or {}).items():
            # A DOUBLEHEADER puts the same team on two games in one day, so a
            # settled score must not be clobbered by one that hasn't started.
            # Which game a leg means is genuinely ambiguous from the card;
            # this only stops the score going backwards. Mirrors index.html.
            prev = results["teamScores"].get(a)
            if prev and prev.get("runs") is not None and row.get("runs") is None:
                continue
            results["teamScores"][a] = row
        results["gameInfo"][snap["gamePk"]] = snap
        if snap["status"] != "final":
            results["allGamesFinal"] = False
    results["homeRuns"].sort(key=lambda hr: str(hr["time"]))   # in the order they were hit
    return results


def pinch_hit_protection(results, player):
    """The substitute (or substitute's substitute) in this player's lineup slot who homered, if any."""
    norm = normalize_name(player)
    if norm not in results["substitutedOut"]:
        return None
    loc = results["rosterSide"].get(norm)
    game = results["gameInfo"].get(loc["gamePk"]) if loc else None
    info = game["orderSlot"].get(norm) if game else None
    if not info:
        return None
    holders = game["slotHolders"][loc["side"]].get(info["slot"], [])
    successors = sorted((h for h in holders if h["orderFull"] > info["orderFull"]), key=lambda h: h["orderFull"])
    return next((h["name"] for h in successors if normalize_name(h["name"]) in results["hitNames"]), None)


def grade_player(results, player):
    """-> (state, php_by). state: hit | miss | na | live | not_started, exactly as the page shows it."""
    norm = normalize_name(player)
    if norm in results["hitNames"]:
        return "hit", None
    php_by = pinch_hit_protection(results, player)
    if php_by:
        return "hit", php_by
    if norm in results["rosterStatus"]:
        return ("miss" if results["rosterStatus"][norm] == "final" else "live"), None
    return ("na" if results["allGamesFinal"] else "not_started"), None


def grade_steal(results, player):
    """index.html's stateForSteal()."""
    norm = normalize_name(player)
    if norm in results["sbNames"]:
        return "hit"
    if norm in results["substitutedOut"]:
        return "miss"                       # pulled, can't re-enter, and nobody inherits a steal bet
    if norm in results["inBox"]:
        if results["inBox"][norm] != "final":
            return "live"
        return "miss" if norm in results["appeared"] else "na"
    return "na" if results["allGamesFinal"] else "not_started"


def evaluate_ticket(stake, payout, legs):
    """index.html's evaluateTicket(). legs: [(state, odds_number)] -> (outcome, returned)."""
    states = [s for s, _ in legs]
    na, miss, hit = states.count("na"), states.count("miss"), states.count("hit")
    active = len(states) - na
    if active == 0:
        return "void", stake                  # nobody played: the book refunds it
    if miss:
        return "dead", 0.0                    # certain, even alongside an untracked leg
    # A leg in a market this script can't grade. Same rule the page uses: a
    # miss anywhere still kills the bet (handled above), but nothing else may
    # be claimed -- so this can never be recorded as a win.
    if states.count("untracked"):
        return "partial", None
    if hit != active:
        return "live", None                   # unresolved (suspended game at the backstop)
    adjusted = payout
    if na:
        product = 1.0
        for state, odds in legs:
            dec = american_to_decimal(odds) if state != "na" else None
            if dec is not None:
                product *= dec
        adjusted = (stake or 0) * product
    return "hit", adjusted


def hr_detail(hr):
    keep = ("inning", "half", "pitcher", "pitchHand", "distance", "exitVelo", "launchAngle", "trajectory",
            "pitchType", "pitchVelo", "zone", "venue")
    return {k: hr[k] for k in keep if hr.get(k) not in (None, "")}


# ---------------- markets beyond home runs and steals ----------------
# Ports of index.html's MARKETS registry. The rule from CLAUDE.md applies in
# both directions: a change to either page's grading has to be made here too,
# and vice versa. These were added 2026-09-30, after a first pass deliberately
# wrote "untracked" rather than risk a half-ported grader baking a wrong
# result into the permanent record.
MARKET_STATS = {
    "hrr": ("hits", "runs", "rbi"), "hits": ("hits",), "rbi": ("rbi",),
    "runs": ("runs",), "tb": ("totalBases",), "doubles": ("doubles",),
    "hr": ("homeRuns",), "k": ("strikeOuts",),
}
TEAM_MARKETS = ("ml", "spread", "total")


def leg_players(src):
    """Every player named on a leg. A leg can name two ("Schwarber/Olson"),
    and the bet is on their COMBINED total."""
    players = src.get("players")
    if isinstance(players, list) and players:
        return players
    return [src["player"]] if src.get("player") else []


def leg_line(src, default=0.5):
    line = src.get("line")
    try:
        return float(line) if line is not None else default
    except (TypeError, ValueError):
        return default


def grade_stat_prop(results, src, stat_keys, pitching=False):
    """index.html's stateForStatProp(). Summed across every name on the leg."""
    names = [normalize_name(n) for n in leg_players(src)]
    if not names:
        return "untracked"
    line, under = leg_line(src), src.get("side") == "under"
    table = results["pitchStats"] if pitching else results["batStats"]
    total = sum(sum(table.get(n, {}).get(k) or 0 for k in stat_keys) for n in names)

    # An OVER settles the moment it clears, mid-game; an UNDER can only settle
    # at the end unless it has already busted.
    if total > line:
        return "miss" if under else "hit"
    statuses = [results["inBox"].get(n) for n in names]
    if not all(st == "final" for st in statuses if st) or not any(statuses):
        return "live" if any(statuses) else "not_started"
    # Final. Somebody on the leg has to have actually played, or it's void.
    played = any(n in results["rosterStatus"] for n in names) or \
        (pitching and any(n in results["pitchStats"] for n in names))
    if not played:
        return "na"
    return "hit" if under else "miss"


def grade_game_line(results, src):
    """index.html's stateForMoneyline / stateForSpread / stateForTotal.

    ONLY ever settled on a FINAL game: leading in the seventh is not a result,
    and calling one early would be the worst kind of wrong in a permanent
    record. A spread or total landing exactly on the number is a PUSH.
    """
    market = src.get("market")
    row = results["teamScores"].get((src.get("team") or "").upper())
    if not row or row.get("runs") is None or row.get("oppRuns") is None:
        return "not_started"
    if row.get("status") != "final":
        return "live"
    mine, theirs = row["runs"], row["oppRuns"]
    if market == "ml":
        return "hit" if mine > theirs else "miss"
    if market == "spread":
        try:
            line = float(src.get("line"))
        except (TypeError, ValueError):
            return "untracked"
        margin = (mine + line) - theirs
        if margin == 0:
            return "na"
        return "hit" if margin > 0 else "miss"
    combined, line = mine + theirs, leg_line(src)
    if combined == line:
        return "na"
    return "hit" if (src.get("side") == "under") == (combined < line) else "miss"


def grade_market(results, src):
    """-> (state, php_by) for any market, mirroring stateForLeg().

    An UNRECOGNISED market is "untracked", never graded as something else.
    That is the whole safety story, here as on the page: a market nobody has
    taught this script must not end up in the permanent record as a loss.
    """
    market = src.get("market")
    if market in (None, "hr") and not src.get("players") and src.get("line") is None:
        return grade_player(results, src.get("player") or "")   # the ordinary home run path
    if market == "sb":
        return grade_steal(results, src.get("player") or ""), None
    if market in TEAM_MARKETS:
        return grade_game_line(results, src), None
    keys = MARKET_STATS.get(market if market else "hr")
    if keys:
        return grade_stat_prop(results, src, keys, pitching=(market == "k")), None
    return "untracked", None


def grade_leg(results, src):
    player = src.get("player") or ""
    market = src.get("market")
    state, php_by = grade_market(results, src)
    norm = normalize_name(player)
    leg = {"player": player, "team": src.get("team") or "", "who": who_name(src.get("who")),
           "odds": odds_to_number(src.get("odds")), "state": state, "mlbId": results["rosterIds"].get(norm)}
    # Everything the bet was ABOUT rides into the record, so a leg can be read
    # back years later without the card it came from.
    for k in ("market", "line", "side", "players"):
        if src.get(k) is not None:
            leg[k] = src[k]
    if src.get("time"):
        leg["time"] = src["time"]
    loc = results["rosterSide"].get(norm)
    if loc and results["gameInfo"].get(loc["gamePk"], {}).get("matchup"):
        leg["game"] = results["gameInfo"][loc["gamePk"]]["matchup"]
    credited = normalize_name(php_by) if php_by else norm
    if php_by:
        leg["php"] = php_by
    # What the leg actually counted, so a graded number is auditable rather
    # than something the reader has to take on trust.
    keys = MARKET_STATS.get(market) if market else None
    if keys:
        table = results["pitchStats"] if market == "k" else results["batStats"]
        leg["counted"] = sum(sum(table.get(normalize_name(n), {}).get(k) or 0 for k in keys)
                             for n in leg_players(src))
    elif market in TEAM_MARKETS:
        row = results["teamScores"].get((src.get("team") or "").upper())
        if row and row.get("runs") is not None:
            leg["score"] = f"{row['runs']}-{row['oppRuns']}"
    if market == "sb":
        leg["market"] = "sb"
        mine = [st for st in results["steals"] if normalize_name(st["runner"]) == norm]
        if mine:
            leg["steals"] = [{k: st[k] for k in ("base", "caught", "inning", "half", "pitcher") if st.get(k) not in (None, "")} for st in mine]
    elif state == "hit" and market in (None, "hr"):
        # Statcast detail belongs to a HOME RUN leg. Without the market check
        # this fired for ANY hit leg, so a cashed spread carried a list of
        # somebody else's home runs into the permanent record.
        leg["homeRuns"] = [hr_detail(hr) for hr in results["homeRuns"] if normalize_name(hr["batter"]) == credited]
    return leg


def round2(v):
    return None if v is None else round(float(v) + 1e-9, 2)


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
    hr_legs = [l for l in all_legs if l.get("market") != "sb"]
    picked = {normalize_name(l["player"]) for l in hr_legs} | {normalize_name(l["php"]) for l in hr_legs if l.get("php")}
    sb_picked = {normalize_name(l["player"]) for l in all_legs if l.get("market") == "sb"}
    stolen = [st for st in results["steals"] if not st["caught"]]
    # "partial" counts as incomplete: those legs are not graded, so the slate
    # is not finished being recorded and a later run should look again.
    complete = all(b["outcome"] not in ("live", "partial") for b in bets)
    return {
        "sport": "baseball",
        "date": tickets["date"],
        "complete": complete,
        "ticketsHash": tickets_hash(tickets),
        "summary": {
            "games": results["games"],
            "parlays": len(parlays), "parlaysCashed": sum(1 for p in parlays if p["outcome"] == "hit"),
            "singles": len(singles), "singlesCashed": sum(1 for s in singles if s["outcome"] == "hit"),
            "legs": len(all_legs), "legsHit": sum(1 for l in all_legs if l["state"] == "hit"),
            "legsVoid": sum(1 for l in all_legs if l["state"] == "na"),
            "staked": round2(sum(b["stake"] or 0 for b in bets)),
            "returned": round2(sum(b["returned"] or 0 for b in bets)),
            "ourHomeRuns": sum(1 for hr in results["homeRuns"] if normalize_name(hr["batter"]) in picked),
            "leagueHomeRuns": len(results["homeRuns"]),
            "ourSteals": sum(1 for st in stolen if normalize_name(st["runner"]) in sb_picked),
            "leagueSteals": len(stolen),
        },
        "note": tickets.get("note") or "",
        "parlays": parlays,
        "singles": singles,
        "homeRuns": results["homeRuns"],
        "steals": results["steals"],
    }


def tickets_hash(tickets):
    return hashlib.sha1(json.dumps(tickets, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------------- deciding what to record ----------------

def today_et():
    return datetime.now(ET).date()


def past_backstop(day, today):
    return today >= date.fromisoformat(day) + timedelta(days=BACKSTOP_DAYS)


def record(tickets, out_dir, today, fetcher=fetch_json, force=False):
    """Grade and write one slate if it's ready. Returns a one-line status for the log."""
    day = tickets.get("date")
    if not day or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return "skipped: slate has no date"
    out = Path(out_dir) / f"{day}.json"
    if out.exists() and not force:
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        if old.get("complete") and old.get("ticketsHash") == tickets_hash(tickets):
            return f"{day}: already recorded"
    if day > today.isoformat():
        return f"{day}: hasn't been played yet"

    results = poll_slate(day, fetcher)
    if not results["allScheduledFinal"] and not past_backstop(day, today):
        return f"{day}: games still in progress -- not recorded yet"
    payload = grade_slate(tickets, results)
    text = json.dumps(payload, indent=1, ensure_ascii=False) + "\n"
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return f"{day}: unchanged"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    s = payload["summary"]
    return (f"{day}: RECORDED {s['legsHit']}/{s['legs']} legs hit, {s['parlaysCashed']}/{s['parlays']} parlays and "
            f"{s['singlesCashed']}/{s['singles']} singles cashed, ${s['staked']:.2f} staked -> ${s['returned']:.2f} returned"
            + ("" if payload["complete"] else "  [INCOMPLETE: a game never went final; will re-grade]"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickets", action="append", help="slate file(s) to consider instead of data/tickets*.json")
    ap.add_argument("--out-dir", default=str(RESULTS_DIR))
    ap.add_argument("--force", action="store_true", help="re-grade a slate that's already recorded")
    args = ap.parse_args()

    failed = False
    seen = set()
    for path in [Path(p) for p in args.tickets] if args.tickets else SLATE_FILES:
        if not path.exists():
            continue   # a day with no picks is a normal state
        try:
            tickets = json.loads(path.read_text(encoding="utf-8"))
            if tickets.get("date") in seen:
                continue
            seen.add(tickets.get("date"))
            print(record(tickets, args.out_dir, today_et(), force=args.force))
        except Exception as err:   # one slate failing must not stop the other
            failed = True
            print(f"ERROR recording {path.name}: {err}", file=sys.stderr)
    if not seen:
        print("No slates on hand -- nothing to record.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
