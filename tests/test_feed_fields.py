"""
Proves index.html's FEED_FIELDS allow-list doesn't lose anything.

This is the one test here that DOES hit the network: `fields` is an
undocumented filter on an already-unofficial API, and the failure mode is
silent -- a name missing from the list yields `undefined` rather than an
error, so tracking would quietly degrade instead of breaking loudly. The
mocked fixtures in test_page.py can't catch that, because they're built to
the shape the code already expects.

So: for every game live right now, fetch BOTH the full feed and the slim
one, run each through index.html's own getGameSnapshot(), and require the
computed snapshots to be identical.

Needs live MLB games to be meaningful. With none in progress it checks
Final games from the most recent slate instead, and says so. Re-run this
after editing FEED_FIELDS.

    python tests/test_feed_fields.py
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "index.html"
API = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
MAX_GAMES = 15


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "bmbs-field-check"})
    with urllib.request.urlopen(req, timeout=45) as res:
        return json.loads(res.read().decode("utf-8"))


def feed_fields():
    """The FEED_FIELDS list as index.html actually defines it -- read from the
    file so this test can never drift from the value being shipped."""
    src = INDEX.read_text(encoding="utf-8")
    m = re.search(r"const FEED_FIELDS = \[(.*?)\]\.join", src, re.S)
    if not m:
        sys.exit("FAIL: couldn't find FEED_FIELDS in index.html")
    # Strip // comments FIRST: the list is commented, and a comment containing
    # a quoted phrase would otherwise be scraped in as a field name and sent to
    # MLB as part of the URL (it happened -- a space in it raised InvalidURL).
    body = re.sub(r"//.*", "", m.group(1))
    return ",".join(re.findall(r'"([^"]+)"', body))


def pick_games():
    """(gamePks, label) -- games in progress if there are any, else the most
    recent slate's Final games."""
    now = datetime.now(timezone.utc)
    for back in range(0, 3):
        date = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        games = [g for d in get(SCHEDULE.format(date=date)).get("dates", []) for g in d.get("games", [])]
        live = [g["gamePk"] for g in games if g["status"]["abstractGameState"] == "Live"]
        if live:
            return live[:MAX_GAMES], f"{len(live)} live games on {date}"
        final = [g["gamePk"] for g in games if g["status"]["abstractGameState"] == "Final"]
        if final:
            return final[:MAX_GAMES], f"{len(final[:MAX_GAMES])} FINAL games on {date} (no live games right now)"
    sys.exit("FAIL: no games found in the last 3 days to check against")


# Sets and Maps don't survive page.evaluate() -- flatten to sorted plain JSON
# so the comparison is exact and order-independent.
NORMALIZE = """async (pk) => {
  const s = await getGameSnapshot(pk);
  return JSON.stringify({
    status: s.status, gamePk: s.gamePk,
    hrNames: [...s.hrNames].sort(),
    homeRuns: s.homeRuns,
    rosterNames: [...s.rosterNames].sort(),
    played: [...s.played].sort(),   // who actually batted: drives the bench -> N/A grading
    orderSlot: [...s.orderSlot.entries()].sort(),
    slotHolders: s.slotHolders,
    inning: s.inning, halfState: s.halfState, outs: s.outs,
    nextUpSlot: s.nextUpSlot, currentlyBattingSide: s.currentlyBattingSide,
    currentAB: s.currentAB, recentABs: s.recentABs,   // Live At Bats: count, every pitch, results
    sbNames: [...s.sbNames].sort(), steals: s.steals, bases: s.bases,   // steal bets: every attempt, and who is on which base
  });
}"""

fields = feed_fields()
pks, label = pick_games()
print(f"checking {label}\n")

failures = []
full_bytes = slim_bytes = 0

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    served = {"feed": None}

    def handler(route):
        url = route.request.url
        if url.endswith("/index.html"):
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=INDEX.read_bytes())
        if "/feed/live" in url:
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(served["feed"]))
        if "/schedule" in url:
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"dates": []}))
        if "tickets" in url:
            return route.fulfill(status=404, body="")
        return route.abort()

    page.route("**/*", handler)
    page.goto("http://bmbs.test/index.html")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")

    for pk in pks:
        full = get(API.format(pk=pk))
        # A live game moves between two requests -- an out recorded in the gap
        # shows up as a "difference" that has nothing to do with `fields`. Pin
        # the slim fetch to the exact instant the full response describes
        # (`timecode` and `fields` compose), so any diff is really a lost field.
        at = (full.get("metaData") or {}).get("timeStamp", "")
        # Only pin a game that's actually moving. A finished game can't change
        # between requests, and pinning one REWINDS it: its last timeStamp
        # predates the flip to Final, so the pinned copy comes back "Live" --
        # which looked exactly like fields= breaking status until it was
        # checked against the unpinned request the site really sends.
        moving = ((full.get("gameData") or {}).get("status") or {}).get("abstractGameState") == "Live"
        pin = f"?timecode={at}&" if at and moving else "?"
        slim = get(API.format(pk=pk) + pin + "fields=" + fields)
        full_bytes += len(json.dumps(full))
        slim_bytes += len(json.dumps(slim))

        served["feed"] = full
        from_full = page.evaluate(NORMALIZE, pk)
        served["feed"] = slim
        from_slim = page.evaluate(NORMALIZE, pk)

        if from_full == from_slim:
            s = json.loads(from_full)
            ab = s.get("currentAB") or {}
            print(f"PASS  {pk}  status={s['status']:7s} roster={len(s['rosterNames']):3d} "
                  f"HRs={len(s['homeRuns'])} inning={s['inning']} recentABs={len(s.get('recentABs') or [])} "
                  f"atBat={ab.get('batter', '-')} {ab.get('balls', '')}-{ab.get('strikes', '')} pitches={len(ab.get('pitches') or [])} "
                  f"steals={len(s.get('steals') or [])} onBase={sum(1 for v in (s.get('bases') or {}).values() if v)}")
        else:
            failures.append(pk)
            print(f"FAIL  {pk}  slim feed computed a DIFFERENT snapshot")
            a, b = json.loads(from_full), json.loads(from_slim)
            for key in a:
                if a[key] != b[key]:
                    print(f"        {key}:\n          full: {json.dumps(a[key])[:200]}"
                          f"\n          slim: {json.dumps(b[key])[:200]}")
    browser.close()

if slim_bytes:
    print(f"\nraw JSON over {len(pks)} games: full={full_bytes / 1024:.0f} KB  "
          f"slim={slim_bytes / 1024:.0f} KB  ({full_bytes / slim_bytes:.1f}x smaller)")

if failures:
    print(f"\n{len(failures)} game(s) differ -- a name is missing from FEED_FIELDS in index.html")
    sys.exit(1)
print("\nall feed-field checks passed")
