"""
Regression checks for scripts/parse_football_picks.py, build_football_roster.py
and the Discord bot's file-name routing. Fully offline: checked-in fixtures, a
fake ESPN, temp dirs -- never touches data/ and never the network.

    python tests/test_football_parser.py
"""
import json
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import build_football_roster as br  # noqa: E402
import parse_football_picks as fp  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# A small roster of our own, so these tests don't move when real NFL rosters do.
def entry(i, name, team, pos="WR"):
    return {"name": name, "team": team, "id": str(i), "pos": pos}


PLAYERS = [entry(1, "Saquon Barkley", "PHI", "RB"), entry(2, "Justin Jefferson", "MIN"), entry(3, "Marvin Harrison Jr.", "ARI"),
           entry(4, "Ja'Marr Chase", "CIN"), entry(5, "Derrick Henry", "BAL", "RB"), entry(6, "CeeDee Lamb", "DAL"),
           entry(7, "Josh Allen", "BUF", "QB"), entry(8, "Brian Thomas Jr.", "JAX"), entry(9, "Travis Kelce", "KC", "TE"),
           entry(10, "Christian McCaffrey", "SF", "RB"), entry(11, "Jonathan Taylor", "IND", "RB"), entry(12, "Jahmyr Gibbs", "DET", "RB"),
           entry(13, "Dalton Kincaid", "BUF", "TE"), entry(14, "James Cook III", "BUF", "RB"), entry(15, "Amon-Ra St. Brown", "DET")]
ROSTER = {"team_by_name": {br.norm_key(p["name"]): p["team"] for p in PLAYERS},
          "canonical_name_by_norm": {br.norm_key(p["name"]): p["name"] for p in PLAYERS},
          "id_by_norm": {br.norm_key(p["name"]): p["id"] for p in PLAYERS}}

# ---------- 1. the "Ticket N:" template ----------
windows, singles = fp.parse((REPO / "tests/fixtures/football_ticket_format.txt").read_text(encoding="utf-8"), ROSTER)
legs = [l for w in windows for c in w["tickets"] for l in c["legs"]]
check("ticket format: 3 singles, 3 cards, 8 legs", (len(singles), sum(len(w["tickets"]) for w in windows), len(legs)) == (3, 3, 8))
check("NEGATIVE odds parse, sign kept (a home run never needed this)",
      singles[0]["odds"] == "-135" and {l["player"]: l["odds"] for l in legs}["Christian McCaffrey"] == "-190", singles[0]["odds"])
check("positive odds keep their sign too", singles[1]["odds"] == "+120")
check("a card that omits the suffix still resolves: 'Marvin Harrison' -> 'Marvin Harrison Jr.'",
      singles[2]["player"] == "Marvin Harrison Jr." and singles[2]["athleteId"] == "3" and singles[2]["team"] == "ARI", str(singles[2]))
check("every leg carries its ESPN athlete id and team", all(l["athleteId"] and l["team"] for l in legs), str([l["player"] for l in legs if not l["athleteId"]]))
check("stake / payout / book", (windows[0]["tickets"][1]["stake"], windows[0]["tickets"][1]["payout"], windows[0]["tickets"][1]["book"]) == (3.75, 59.63, "Kenny"))
check("singles: bettor upper-cased, payout numeric and formatted", (singles[0]["who"], singles[0]["payout"], singles[0]["pp"]) == ("MEMO", 8.7, "PP $8.70"))

# ---------- 2. the "Card N:" template ----------
windows2, singles2 = fp.parse((REPO / "tests/fixtures/football_card_format.txt").read_text(encoding="utf-8"), ROSTER)
card = windows2[0]["tickets"][0]
check("card format: 2 singles + 1 two-leg card", len(singles2) == 2 and [len(c["legs"]) for w in windows2 for c in w["tickets"]] == [2])
check("negative odds in parentheses", singles2[0]["odds"] == "-120" and card["legs"][1]["odds"] == "-105")
check("'James Cook' -> ESPN's 'James Cook III'", card["legs"][0]["player"] == "James Cook III" and card["legs"][0]["athleteId"] == "14")
check("the '(Noid — Listed as ...)' aside never reaches the bettor", card["legs"][1]["who"] == "Noid", card["legs"][1]["who"])
check("same-game tag survives", "Both Players in DET @ BUF" in card["name"], card["name"])

unknown_w, unknown_s = fp.parse("🎯 Longshot Straight Bets\nKenny: Totally Madeup Person (+500) | 1:00 PM ET • $5.00 bet | PP: $30.00\n", ROSTER)
check("an unknown name is kept as typed with no id -- never guessed onto someone else",
      unknown_s[0]["player"] == "Totally Madeup Person" and unknown_s[0]["athleteId"] == "" and unknown_s[0]["team"] == "")

# ---------- 3. slate dating comes from the NFL schedule ----------
SCHEDULE = {   # a Thursday game already played, Sunday, Monday, next Thursday
    "20260917": [("DET", "BUF", "post")],
    "20260920": [("PHI", "TEN", "pre"), ("CIN", "HOU", "pre"), ("IND", "KC", "pre")],
    "20260921": [("LAR", "NYG", "pre")],
    "20260924": [("BUF", "MIA", "pre")],
}


def fake_espn(url):
    ymd = url.split("dates=")[1]
    return {"events": [{"status": {"type": {"state": state}}, "competitions": [{"competitors": [
        {"team": {"abbreviation": a}}, {"team": {"abbreviation": h}}]}]} for a, h, state in SCHEDULE.get(ymd, [])]}


def dated(teams, now, times=()):
    return fp.slate_dates_for(teams, list(times), now, fake_espn)


FRI = datetime(2026, 9, 18, 23, 0, tzinfo=fp.ET)
check("a Sunday card posted FRIDAY NIGHT is dated Sunday (baseball's clock rule would have said Friday)",
      dated(["PHI", "CIN", "KC"], FRI, ["1:00 PM ET", "8:20 PM ET"]) == ("2026-09-20", "2026-09-20"))
check("Sunday + Monday card spans both days", dated(["PHI", "KC", "LAR"], FRI) == ("2026-09-20", "2026-09-21"))
check("posted Sunday night during the late game, for Monday: dated Monday",
      dated(["LAR", "NYG"], datetime(2026, 9, 20, 21, 0, tzinfo=fp.ET)) == ("2026-09-21", "2026-09-21"))
check("a team that already played (BUF, next game Thursday) can't stretch the slate a week",
      dated(["PHI", "KC", "BUF"], FRI) == ("2026-09-20", "2026-09-20"))
check("a FINAL game is never 'next': Thursday night, after DET@BUF ended, DET picks don't date the slate to Thursday",
      dated(["DET", "PHI"], datetime(2026, 9, 17, 23, 59, tzinfo=fp.ET)) == ("2026-09-20", "2026-09-20"))


def offline(url):
    raise OSError("no network")


check("ESPN unreachable -> falls back to the card's times instead of failing the upload",
      fp.slate_dates_for(["PHI"], ["1:00 PM ET"], datetime(2026, 9, 20, 9, 0, tzinfo=fp.ET), offline) == ("2026-09-20", "2026-09-20"))
check("...and a card whose kickoffs have all passed is tomorrow's",
      fp.slate_dates_for(["PHI"], ["1:00 PM ET"], datetime(2026, 9, 20, 23, 0, tzinfo=fp.ET), offline)[0] == "2026-09-21")

# ---------- 4. archive guard, against a temp dir (never data/) ----------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    fp.TICKETS_PATH, fp.PREVIOUS_PATH = td / "tickets.json", td / "tickets-previous.json"
    fp.TICKETS_PATH.write_text(json.dumps({"date": "2026-09-20"}), encoding="utf-8")
    fp.archive_previous_slate("2026-09-20")
    check("same-slate re-upload doesn't clobber the archive", not fp.PREVIOUS_PATH.exists())
    fp.archive_previous_slate("2026-09-27")
    check("a new slate archives the old one", json.loads(fp.PREVIOUS_PATH.read_text(encoding="utf-8"))["date"] == "2026-09-20")

# ---------- 5. roster builder ----------
ESPN_ROSTERS = {
    f"{br.API}/teams?limit=40": {"sports": [{"leagues": [{"teams": [{"team": {"id": "2", "abbreviation": "BUF"}}, {"team": {"id": "30", "abbreviation": "JAX"}}]}]}]},
    f"{br.API}/teams/2/roster": {"athletes": [{"items": [{"id": "7", "fullName": "Josh Allen", "position": {"abbreviation": "QB"}},
                                                          {"id": "14", "fullName": "James Cook III", "position": {"abbreviation": "RB"}}]}]},
    f"{br.API}/teams/30/roster": {"athletes": [{"items": [{"id": "99", "fullName": "Josh Allen", "position": {"abbreviation": "LB"}}]}]},
}
built = br.build(fetcher=lambda url: ESPN_ROSTERS[url])
check("two Josh Allens: the skill-position one wins the name (whichever team is read first)",
      built["id_by_norm"]["josh allen"] == "7" and built["team_by_name"]["josh allen"] == "BUF", str(built["id_by_norm"]))
check("suffix dropped from the KEY, kept in the NAME", built["canonical_name_by_norm"].get("james cook") == "James Cook III")

# ---------- 6. the Discord bot routes by file name, and nothing else ----------
for name in ("discord", "requests", "dotenv"):   # the bot's imports aren't test dependencies
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["discord"].Intents = types.SimpleNamespace(default=lambda: types.SimpleNamespace(message_content=False))
sys.modules["discord"].Client = lambda **kw: types.SimpleNamespace(event=lambda f: f, run=lambda t: None)
sys.modules["discord"].abc = types.SimpleNamespace(Messageable=object, User=object)
sys.modules["discord"].Message = sys.modules["discord"].Reaction = object
sys.modules["dotenv"].load_dotenv = lambda: None
import os  # noqa: E402
os.environ.update({"DISCORD_BOT_TOKEN": "x", "DISCORD_CHANNEL_ID": "1", "GITHUB_TOKEN": "x"})
sys.path.insert(0, str(REPO / "discord-bot"))
import bot  # noqa: E402

route = lambda f: (bot.route_for(f) or {}).get("path")
check("baseball*.txt -> the baseball incoming file", route("baseball_2026-09-20.txt") == "data/incoming_picks.txt")
check("football*.txt -> the football incoming file", route("football_week2.txt") == "data/football/incoming_picks.txt")
check("case doesn't matter", route("Football Week 2.TXT") == "data/football/incoming_picks.txt" and route("BASEBALL.txt") == "data/incoming_picks.txt")
check("anything else is refused -- including yesterday's naming, and a file that merely mentions a sport",
      route("home_run_parlay_card_2026-09-18.txt") is None and route("picks.txt") is None and route("my_football_card.txt") is None)
check("the two sports can never be routed to the same file", len({r["path"] for r in bot.ROUTES.values()}) == len(bot.ROUTES))

# ---------- 7. CLI writes only where it's told (temp copy of the repo layout) ----------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    (td / "scripts").mkdir(); (td / "data" / "football").mkdir(parents=True)
    (td / "scripts" / "parse_football_picks.py").write_text((REPO / "scripts/parse_football_picks.py").read_text(encoding="utf-8"), encoding="utf-8")
    (td / "data" / "football" / "roster.json").write_text(json.dumps(ROSTER), encoding="utf-8")
    real = REPO / "data" / "football" / "tickets.json"
    before = real.read_bytes() if real.exists() else None
    r = subprocess.run([sys.executable, str(td / "scripts" / "parse_football_picks.py"), "--file", str(REPO / "tests/fixtures/football_card_format.txt")],
                       capture_output=True, text=True, encoding="utf-8")
    out = td / "data" / "football" / "tickets.json"
    check("CLI parses a card end to end", r.returncode == 0 and out.exists(), r.stderr[-300:])
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
        check("output is marked football and dated", payload.get("sport") == "football" and len(payload.get("date", "")) == 10, str(payload.get("date")))
    check("the real data/football/tickets.json was never touched", (real.read_bytes() if real.exists() else None) == before)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all football-parser checks passed")
