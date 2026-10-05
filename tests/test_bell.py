"""
The notification bell, on all three tracker pages.

The engine is the same text on every page; what differs is a handful of
per-page hooks -- how a leg is graded, how a bet is named, and which results
have to be swapped in so the scan is about TODAY. Those hooks are where a
page-specific bug would live, so each page is exercised, and the behaviour
itself is pinned in depth on the MLB page.

The three pages share ONE origin, so they share localStorage. Independence is
therefore checked the only way that actually proves it: all three in one
browser context, where a shared key would show up as one bell in three
places.

Fully offline: every request is served from fixtures, anything unexpected is
aborted, and the clock is pinned.

    python tests/test_bell.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
DAY = "2026-10-04"
NOW = "2026-10-05T00:00:00Z"          # 8:00 PM ET

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- MLB fixtures ----------------

def mlb_schedule(games):
    return {"dates": [{"date": DAY, "games": [
        {"gamePk": pk, "status": {"abstractGameState": st, "detailedState": st,
                                  "codedGameState": {"Final": "F", "Live": "I"}[st], "reason": ""},
         "teams": {"away": {"team": {"abbreviation": t[0]}},
                   "home": {"team": {"abbreviation": t[1]}}}}
        for pk, st, t in games]}]}


def ms(iso):
    """An ISO time as the epoch milliseconds the page works in."""
    from datetime import datetime
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def hr_play(h):
    """A home run play. `h` is a name, or (name, ISO end time) for a play that
    carries the time it happened -- which is what the bell sorts and shows."""
    name, t = (h, None) if isinstance(h, str) else h
    play = {"result": {"eventType": "home_run"}, "about": {"isTopInning": True},
            "matchup": {"batter": {"fullName": name}}}
    if t:
        play["about"].update({"endTime": t, "isComplete": True})
    return play


def mlb_feed(abstract, roster, hrs=()):
    sides = {"away": {}, "home": {}}
    for i, n in enumerate(roster):
        side = "away" if i < 9 else "home"
        sides[side][f"ID{i}"] = {"person": {"fullName": n}, "battingOrder": f"{(i % 9) + 1}00",
                                 "stats": {"batting": {"plateAppearances": 3}}}
    return {"gameData": {"status": {"abstractGameState": abstract,
                                    "codedGameState": {"Final": "F", "Live": "I"}[abstract]}},
            "liveData": {"plays": {"allPlays": [hr_play(h) for h in hrs]},
                "boxscore": {"teams": {"away": {"players": sides["away"]},
                                       "home": {"players": sides["home"]}}},
                "linescore": {}}}


def leg(i, player, team, who="Memo", odds="+390", sport=None, market=None, aid=None):
    l = {"id": f"L{i}", "player": player, "team": team, "who": who,
         "meta": f"{team} &middot; {who}", "odds": odds, "time": "7:10 PM ET"}
    if sport:
        l["sport"] = sport
    if market:
        l["market"] = market
    if aid:
        l["athleteId"] = aid
    return l


def card(name, legs, stake=5.0, payout=100.0):
    return {"name": name, "sub": f"{len(legs)}-Leg", "foot": "<b>$5.00</b> bet by Memo",
            "stake": stake, "book": "Memo", "payout": payout, "legs": legs}


def single(i, who, player, team, payout=24.5, sport=None, market=None, aid=None):
    s = {"id": f"single-{i}", "who": who, "player": player, "team": team,
         "meta": f"{team} &middot; 7:10 PM ET", "odds": "+390", "stake": 5.0,
         "payout": payout, "pp": f"PP ${payout:.2f}"}
    if sport:
        s["sport"] = sport
    if market:
        s["market"] = market
    if aid:
        s["athleteId"] = aid
    return s


# Judge is on BOTH cards and a single. His home run is ONE thing that
# happened, and must be ONE leg entry listing all three bets -- not three.
MLB_TICKETS = {"date": DAY, "note": "", "windows": [{"title": "Parlay Cards", "tickets": [
    card("Card 1 &middot; Bronx Bombers", [leg(1, "Aaron Judge", "NYY"), leg(2, "Juan Soto", "NYM")],
         stake=6.0, payout=186.40),
    card("Card 2", [leg(3, "Aaron Judge", "NYY"), leg(4, "Shohei Ohtani", "LAD")]),
]}], "singles": [single(0, "KENNY", "Aaron Judge", "NYY")]}

# ---------------- ESPN fixtures (NFL + combined) ----------------
TEAM_ID = {"PHI": "21", "NYG": "19"}


def espn_event(state):
    return {"id": "9001", "date": f"{DAY}T17:00Z",
            "status": {"period": 4, "displayClock": "0:00",
                       "type": {"state": state, "shortDetail": "Final" if state == "post" else "Q4"}},
            "competitions": [{"competitors": [
                {"id": "21", "homeAway": "away", "score": "21", "team": {"abbreviation": "PHI"}},
                {"id": "19", "homeAway": "home", "score": "17", "team": {"abbreviation": "NYG"}}]}]}


def espn_summary(state, tds):
    ev = espn_event(state)
    comp = ev["competitions"][0]
    comp["status"] = ev["status"]
    return {"header": {"competitions": [comp]},
            "boxscore": {"players": [{"team": {"abbreviation": "PHI"}, "statistics": [
                {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD", "LONG"],
                 "athletes": [{"athlete": {"id": "1", "displayName": "Saquon Barkley"},
                               "stats": ["18", "92", "5.1", str(tds), "20"]}]}]}]},
            "scoringPlays": FX.get("scoring", []),
            "drives": {"previous": [{"plays": FX["drive_plays"]}] if FX.get("drive_plays") else []}}


NFL_TICKETS = {"sport": "football", "date": DAY, "endDate": DAY, "note": "",
               "windows": [{"title": "Parlay Cards", "tickets": [
                   card("TD Card", [leg(1, "Saquon Barkley", "PHI", aid="1")])]}],
               "singles": []}

# One parlay, both sports: Judge's home run AND Barkley's touchdown.
ALL_TICKETS = {"date": DAY, "endDate": DAY, "note": "", "sports": ["mlb", "nfl"],
               "windows": [{"title": "Parlay Cards", "tickets": [
                   card("Mixed Card", [leg(1, "Aaron Judge", "NYY", sport="mlb"),
                                       leg(2, "Saquon Barkley", "PHI", sport="nfl",
                                           market="td", aid="1")])]}],
               "singles": []}

FX = {"mlb": MLB_TICKETS, "nfl": NFL_TICKETS, "all": ALL_TICKETS,
      "sched": mlb_schedule([(5001, "Final", ["NYY", "BOS"]), (5002, "Final", ["NYM", "ATL"]),
                             (5003, "Live", ["LAD", "SF"])]),
      "feeds": {5001: mlb_feed("Final", ["Aaron Judge"] + [f"N{i}" for i in range(8)], ["Aaron Judge"]),
                5002: mlb_feed("Final", ["Juan Soto"] + [f"M{i}" for i in range(8)], ["Juan Soto"]),
                5003: mlb_feed("Live", ["Shohei Ohtani"] + [f"D{i}" for i in range(8)])},
      "espn_state": "post", "tds": 1}

# The MLB page moved to /mlb/ and All Sports to the root on 2026-10-05; the
# URLs below are kept as they were, because each page's relative data paths
# resolve to the same files from either one.
PAGES = {"/": REPO / "mlb" / "index.html", "/football/": REPO / "football" / "index.html",
         "/all/": REPO / "index.html"}


def handler(route, request):
    url = request.url
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0]
    if path in PAGES or path + "/" in PAGES:
        return route.fulfill(status=200, content_type="text/html; charset=utf-8",
                             body=PAGES[path if path in PAGES else path + "/"].read_bytes())
    for prefix, key in (("/data/combined/", "all"), ("/data/football/", "nfl"), ("/data/", "mlb")):
        if path.startswith(prefix):
            if path.endswith("tickets-previous.json"):
                return route.fulfill(status=404, body="")
            if path.endswith("tickets.json"):
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(FX[key]))
    if "statsapi.mlb.com" in url and "/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(FX["sched"]))
    m = re.search(r"/game/(\d+)/feed/live", url)
    if m:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FX["feeds"][int(m.group(1))]))
    if "scoreboard?dates=" in url:
        evs = [espn_event(FX["espn_state"])] if DAY.replace("-", "") in url else []
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": evs}))
    if "summary?event=" in url:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(espn_summary(FX["espn_state"], FX["tds"])))
    if "/roster" in url:
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"entries": []}))
    if "fonts.g" in url:
        return route.fulfill(status=200, content_type="text/css", body="")
    return route.abort()


def boot(ctx, path):
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.set_fixed_time(NOW)
    page.route("**/*", handler)
    page.goto(f"http://bmbs.test{path}")
    page.wait_for_function("typeof pollTimer !== 'undefined' && pollTimer !== null")
    page.evaluate("clearInterval(pollTimer)")
    page.evaluate("pollAndRender()")
    page.wait_for_timeout(300)
    return page, errors


def poll(page):
    page.evaluate("SLATE_POLLS.clear(); pollAndRender()")
    page.wait_for_timeout(300)


def badge(page):
    return page.inner_text("#bell-badge").strip()


def items(page):
    return page.evaluate("BELL.items.map(i => ({id: i.id, kind: i.kind, who: i.who, what: i.what, "
                         "name: i.name, bets: i.bets, on: i.on, legs: i.legs, stake: i.stake, "
                         "payout: i.payout, early: i.early}))")


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 420, "height": 900})

    # ================= A. MLB: what gets logged =================
    page, errors = boot(ctx, "/")
    its = items(page)
    legs_ = [i for i in its if i["kind"] == "leg"]
    bets_ = [i for i in its if i["kind"] == "bet"]

    check("A1 the bell is in the header", page.query_selector("header #bell-btn") is not None)
    check("A2 it backfills: opened late, everything already hit is waiting",
          badge(page) == "4", (badge(page), [i["id"] for i in its]))
    # Judge is on Card 1, Card 2 AND a single. That is ONE thing that happened.
    judge = [i for i in legs_ if i["who"] == "Aaron Judge"]
    check("A3 one home run is ONE leg entry however many bets carry him",
          len(judge) == 1, [i["who"] for i in legs_])
    check("A4 ...and it lists every bet he is on",
          judge and sorted(judge[0]["bets"]) == sorted(["Card 1 · Bronx Bombers", "Card 2", "Single"]),
          judge[0]["bets"] if judge else None)
    check("A5 the leg says what kind of bet it was", judge and judge[0]["what"] == "Home run",
          judge[0]["what"] if judge else None)
    # The bet entry is about the BET -- stake, payout, everyone on it -- not a
    # restatement of the leg that finished it.
    c1 = [b for b in bets_ if b["name"] == "Card 1 · Bronx Bombers"]
    check("A6 a parlay that cashed is its own entry, separate from its legs",
          len(c1) == 1, [b["name"] for b in bets_])
    check("A7 ...carrying the bet's own details: legs, stake and payout",
          c1 and c1[0]["legs"] == 2 and c1[0]["stake"] == 6.0 and c1[0]["payout"] == 186.40,
          c1[0] if c1 else None)
    check("A8 ...and EVERY leg on it, not just the one that finished it",
          c1 and sorted(c1[0]["on"]) == ["Aaron Judge", "Juan Soto"], c1[0]["on"] if c1 else None)
    check("A9 a single that hits is both a leg entry and a bet entry",
          any(b["name"] == "Single" for b in bets_), [b["name"] for b in bets_])
    check("A10 a parlay still open is NOT logged as cashed",
          not any(b["name"] == "Card 2" for b in bets_), [b["name"] for b in bets_])
    # Found on the first scan after load, so this browser only knows when it
    # NOTICED them -- printing that as a clock time would be a guess.
    check("A11 backfilled entries say 'Earlier' rather than inventing a time",
          all(i["early"] for i in its), [i["early"] for i in its])

    # ================= B. opening clears it =================
    page.click("#bell-btn")
    page.wait_for_timeout(150)
    check("B1 the panel opens", page.is_visible("#bell-panel"))
    check("B2 opening it clears the badge", badge(page) == "", badge(page))
    rows = page.eval_on_selector_all("#bell-list .bell-item", "els => els.length")
    check("B3 every entry is listed", rows == 4, rows)
    check("B4 what was new on THIS visit is still highlighted while you read it",
          page.eval_on_selector_all("#bell-list .bell-item.unread", "els => els.length") == 4)
    check("B5 the bet entry reads as a cash", "cashed" in page.inner_text("#bell-list"),
          page.inner_text("#bell-list")[:200])
    # What you SEE, not the stored flag. A11 above checks the flag, which stays
    # true even if the rendering ignores it and prints an invented clock time.
    times = page.eval_on_selector_all("#bell-list .bell-time", "els => els.map(e => e.textContent)")
    # These fixture plays carry no times, so nothing can be placed on the
    # play-by-play: a backfilled entry then says what is actually known --
    # it happened BY the time the page opened -- never a bare "Earlier"
    # (the user's rule, 2026-10-04), and never an invented exact time.
    check("B5b ...and a backfilled entry the feed can't place reads 'by <time> ET', not 'Earlier'",
          times and all(t.startswith("by ") and t.endswith(" ET") for t in times), times)
    # The FIRST entry used to sit flush against its own top edge. It carried
    # the class "leg" -- the ticket-row class -- and the page's
    # ".leg:first-of-type { padding-top: 0 }" reached it. Measured, because
    # nothing else would notice: the text was all there, just cramped.
    gaps = page.evaluate("""() => [...document.querySelectorAll('#bell-list .bell-item')].map(i =>
        Math.round(i.querySelector('.bell-title').getBoundingClientRect().top - i.getBoundingClientRect().top))""")
    check("B5c every entry has the same top padding, the first one included",
          gaps and min(gaps) >= 8 and max(gaps) - min(gaps) <= 2, gaps)
    stray = page.evaluate("""() => [...document.querySelectorAll('#bell-list .bell-item')]
        .flatMap(i => [...i.classList]).filter(c => !c.startsWith('bell-') && c !== 'unread')""")
    check("B5d the bell's markup uses only its own classes, so no page style can reach it",
          not stray, stray)
    page.click("h1")                               # a tap anywhere else
    page.wait_for_timeout(100)
    check("B6 tapping elsewhere closes it", not page.is_visible("#bell-panel"))

    # ================= C. it counts up live =================
    FX["feeds"][5003] = mlb_feed("Live", ["Shohei Ohtani"] + [f"D{i}" for i in range(8)], ["Shohei Ohtani"])
    poll(page)
    check("C1 a new hit counts the badge up again: Ohtani's leg + Card 2 cashing",
          badge(page) == "2", badge(page))
    new = [i for i in items(page) if not i["early"]]
    check("C2 something found while you watch is not marked 'Earlier'",
          len(new) == 2, [(i["id"], i["early"]) for i in items(page)])
    check("C3 ...it carries a real time, in ET",
          page.evaluate("bellTime(BELL.items.find(i => !i.early))") == "8:00 PM ET",
          page.evaluate("bellTime(BELL.items.find(i => !i.early))"))
    poll(page)
    check("C4 polling again does not log the same hit twice", badge(page) == "2", badge(page))
    check("C5 no JavaScript errors on the MLB page", not errors, errors[:3])
    page.close()

    # ================= D. it survives a refresh =================
    page, errors = boot(ctx, "/")
    check("D1 a refresh keeps the unread count", badge(page) == "2", badge(page))
    check("D2 ...and the whole list", len(items(page)) == 6, len(items(page)))
    page.close()

    # ================= E. independence, in ONE shared origin =================
    # Same context, same localStorage. A shared key would be one bell in three
    # places: reading MLB's would clear the others.
    nfl, nfl_err = boot(ctx, "/football/")
    check("E1 the NFL page logs its own hits", badge(nfl) == "2", (badge(nfl), items(nfl)))
    check("E2 ...a touchdown leg and the bet it cashed",
          sorted(i["kind"] for i in items(nfl)) == ["bet", "leg"], items(nfl))
    check("E3 ...named as a touchdown", any(i.get("what") == "Anytime TD" for i in items(nfl)),
          items(nfl))
    nfl.click("#bell-btn")
    nfl.wait_for_timeout(150)
    check("E4 reading NFL's bell clears NFL's badge", badge(nfl) == "")
    nfl.close()

    mlb, _ = boot(ctx, "/")
    check("E5 ...and leaves MLB's untouched", badge(mlb) == "2", badge(mlb))
    mlb.close()

    al, al_err = boot(ctx, "/all/")
    al_items = items(al)
    check("E6 the NFL+MLB page keeps a third, separate count", badge(al) == "3", (badge(al), al_items))
    check("E7 ...logging BOTH sports' legs from one mixed parlay",
          sorted(i["what"] for i in al_items if i["kind"] == "leg") == ["Anytime TD", "Home run"],
          [i["what"] for i in al_items])
    check("E8 ...and the mixed parlay cashing as its own entry",
          any(i["kind"] == "bet" and i["name"] == "Mixed Card" for i in al_items), al_items)
    keys = al.evaluate("Object.keys(localStorage).filter(k => k.endsWith('bell')).sort()")
    check("E9 three pages, three keys", keys == ["bmbs.all.bell", "bmbs.bell", "bmbs.fb.bell"], keys)
    # The combined page used to share the MLB page's keys outright -- sound,
    # panel state, collapsed cards -- because it was assembled from index.html
    # on the same origin. Every key it writes is now its own.
    shared = al.evaluate("""() => [...document.scripts].map(s => s.textContent).join('')
        .match(/"bmbs\\.(?!all\\.)[a-z.]+"/g) || []""")
    check("E10 the NFL+MLB page writes no key the MLB page also writes", not shared, shared)
    check("E11 no JavaScript errors on the NFL or combined pages",
          not nfl_err and not al_err, (nfl_err + al_err)[:3])
    al.close()

    # ================= F. a new slate starts a new log =================
    FX["mlb"] = dict(MLB_TICKETS, date="2026-10-05")
    FX["sched"] = {"dates": []}
    page, errors = boot(ctx, "/")
    check("F1 a slate with a new date starts the bell from nothing",
          page.evaluate("BELL.date") == "2026-10-05" and badge(page) == "",
          (page.evaluate("BELL.date"), badge(page)))
    check("F2 no JavaScript errors", not errors, errors[:3])
    page.close()

    # ================= G. the time is ET wherever you are =================
    # Run on an Eastern machine, C3 passes whether or not the time is
    # formatted with timeZone -- which is exactly how the sync line printed a
    # Pacific viewer's own clock under an ET label for weeks. So pin a browser
    # to Pacific: 8:00 PM ET is 5:00 PM there, and only a correctly-zoned
    # format still says 8:00.
    FX["mlb"] = MLB_TICKETS
    FX["sched"] = mlb_schedule([(5001, "Final", ["NYY", "BOS"]), (5002, "Final", ["NYM", "ATL"]),
                                (5003, "Live", ["LAD", "SF"])])
    FX["feeds"][5003] = mlb_feed("Live", ["Shohei Ohtani"] + [f"D{i}" for i in range(8)])
    west = browser.new_context(viewport={"width": 420, "height": 900},
                               timezone_id="America/Los_Angeles")
    page, errors = boot(west, "/")
    FX["feeds"][5003] = mlb_feed("Live", ["Shohei Ohtani"] + [f"D{i}" for i in range(8)], ["Shohei Ohtani"])
    poll(page)
    t = page.evaluate("bellTime(BELL.items.find(i => !i.early))")
    check("G1 a viewer on the West Coast still sees the time in ET", t == "8:00 PM ET", t)
    check("G2 no JavaScript errors", not errors, errors[:3])
    west.close()

    # ================= H. the morning after, on the combined page =================
    # Every game final, so the slate has rolled to Yesterday and Today is
    # empty -- which is when the football engine is holding NOTHING, because
    # the tab on screen has no slate. The bell has to swap the slate's own
    # football results in for its scan; in section E the slate was still
    # live, the right results were already loaded, and that swap could not be
    # seen to matter.
    FX["sched"] = mlb_schedule([(5001, "Final", ["NYY", "BOS"])])
    FX["espn_state"] = "post"
    fresh = browser.new_context(viewport={"width": 420, "height": 900})
    page, errors = boot(fresh, "/all/")
    morning = items(page)
    check("H1 the morning after, the combined bell still logs the football leg",
          any(i.get("what") == "Anytime TD" for i in morning), morning)
    check("H2 ...and the baseball one beside it",
          any(i.get("what") == "Home run" for i in morning), morning)
    check("H3 no JavaScript errors", not errors, errors[:3])
    fresh.close()

    # ================= I. every entry can replay its overlay =================
    # The user's ask (2026-10-04): a "Play overlay" button beside each
    # notification, repeatable. It plays with the overlay toggle OFF -- the tap
    # is the request -- and a second tap replays rather than queueing a pile.
    def overlay_text(pg):
        return pg.evaluate("document.getElementById('bomb-overlay').textContent.replace(/\\s+/g, ' ').trim()")

    def replay(pg, kind):
        pg.locator(f".bell-{kind} .bell-replay").first.click()
        pg.wait_for_timeout(100)

    FX["sched"] = mlb_schedule([(5001, "Final", ["NYY", "BOS"]), (5002, "Final", ["NYM", "ATL"])])
    FX["espn_state"] = "in"
    for path, leg_word in (("/", "BOMB!"), ("/football/", "TOUCHDOWN!"), ("/all/", None)):
        rc = browser.new_context(viewport={"width": 420, "height": 900})
        pg, errs = boot(rc, path)
        pg.evaluate("OVERLAY_ON = false")
        pg.click("#bell-btn")
        pg.wait_for_timeout(150)
        n_rows = pg.locator(".bell-item").count()
        # A real <button>, so it is reachable from the keyboard and read as one.
        check(f"I1 {path}: every entry has its own Play overlay button",
              n_rows > 0 and pg.locator(".bell-item button.bell-replay").count() == n_rows,
              (n_rows, pg.locator(".bell-item button.bell-replay").count()))
        btn = pg.locator(".bell-item button.bell-replay").first
        # Wordless on purpose (2026-10-04, "more subtle"): an icon, no label on
        # screen -- but still a named control for a screen reader and on hover.
        check(f"I1b {path}: the replay control is an icon, not words, and still says what it does",
              btn.inner_text().strip() == "" and btn.locator("svg").count() == 1
              and "Replay" in (btn.get_attribute("aria-label") or "") and btn.get_attribute("title"),
              (btn.inner_text(), btn.get_attribute("aria-label")))
        replay(pg, "leg")
        txt = overlay_text(pg)
        want = leg_word or ("TOUCHDOWN!" if "TOUCHDOWN!" in txt else "BOMB!")
        check(f"I2 {path}: a leg entry replays its own overlay, with the overlay toggle off",
              pg.evaluate("document.getElementById('bomb-overlay').classList.contains('active')") and want in txt, txt)
        check(f"I3 {path}: the panel stays open, so another can be played", pg.is_visible("#bell-panel"))
        replay(pg, "leg")
        check(f"I4 {path}: tapping again replays it -- one card on screen, nothing piled up behind",
              pg.locator("#bomb-overlay .bomb-card").count() == 1 and pg.evaluate("BOMB_QUEUE.length") == 0,
              (pg.locator("#bomb-overlay .bomb-card").count(), pg.evaluate("BOMB_QUEUE.length")))
        replay(pg, "bet")
        txt = overlay_text(pg)
        check(f"I5 {path}: a bet entry replays as a CASH, with what it paid",
              "CASHED!" in txt and "$" in txt and pg.locator("#bomb-overlay .bomb-card.cash").count() == 1, txt)
        check(f"I6 {path}: no JavaScript errors", not errs, errs[:3])
        rc.close()

    # A market with no alert of its own, and an entry whose stored words are
    # hostile: what comes back out of localStorage is data, never markup.
    rc = browser.new_context(viewport={"width": 420, "height": 900})
    pg, errs = boot(rc, "/")
    pg.evaluate("""() => {
        BELL.items.push({id: 'leg:x:prop', kind: 'leg', who: 'Up Now', what: 'HITS OVER 1.5', bets: [], at: Date.now(),
                         alert: {m: 'hits', sport: '', name: 'Up Now', word: '2+ HITS!'}});
        BELL.items.push({id: 'leg:x:evil', kind: 'leg', who: 'Evil', what: 'HITS', bets: [], at: Date.now() - 1,
                         alert: {m: 'hits', sport: '', name: '<img src=x id=pwn>', word: '<b id=pwn2>X</b>'}});
        bellRender(); }""")
    pg.click("#bell-btn")
    pg.wait_for_timeout(150)
    pg.evaluate("[...document.querySelectorAll('.bell-item')].find(r => r.dataset.id === 'leg:x:prop').querySelector('.bell-replay').click()")
    pg.wait_for_timeout(100)
    txt = overlay_text(pg)
    check("I7 a prop entry replays with its own words", "Up Now" in txt and "2+ HITS!" in txt, txt)
    pg.evaluate("[...document.querySelectorAll('.bell-item')].find(r => r.dataset.id === 'leg:x:evil').querySelector('.bell-replay').click()")
    pg.wait_for_timeout(100)
    check("I8 stored words are escaped on replay, never run as markup",
          pg.evaluate("!document.getElementById('pwn') && !document.getElementById('pwn2')")
          and "<b id=pwn2>" in overlay_text(pg), overlay_text(pg))
    # Entries logged before they carried their alert are upgraded on the next
    # scan, so the button works on what is already in the bell.
    pg.evaluate("BELL.items.forEach(i => { if (i.kind === 'leg' && !i.id.startsWith('leg:x')) delete i.alert; }); bellSave()")
    poll(pg)
    check("I9 an older entry with no alert is given one on the next scan",
          pg.evaluate("BELL.items.filter(i => i.kind === 'leg').every(i => i.alert && i.alert.m)"),
          pg.evaluate("BELL.items.map(i => [i.id, i.alert])"))
    check("I10 no JavaScript errors", not errs, errs[:3])
    rc.close()

    # ================= J. ordered and stamped by when it HAPPENED =================
    # The user's rule (2026-10-04): newest on top by the time the hit actually
    # happened, with that time shown in ET -- even for what was already in by
    # the time the page opened. Entries used to be stamped with the moment the
    # browser NOTICED them, so everything caught on opening shared one moment,
    # read "Earlier", and sorted arbitrarily. Judge homered at 7:10 PM ET and
    # Soto at 7:40, and the page is opened at 8:00 with both already in.
    FX["mlb"] = MLB_TICKETS
    FX["sched"] = mlb_schedule([(5001, "Final", ["NYY", "BOS"]), (5002, "Final", ["NYM", "ATL"]),
                                (5003, "Live", ["LAD", "SF"])])
    FX["feeds"][5001] = mlb_feed("Final", ["Aaron Judge"] + [f"N{i}" for i in range(8)],
                                 [("Aaron Judge", "2026-10-04T23:10:00Z")])
    FX["feeds"][5002] = mlb_feed("Final", ["Juan Soto"] + [f"M{i}" for i in range(8)],
                                 [("Juan Soto", "2026-10-04T23:40:00Z")])
    FX["feeds"][5003] = mlb_feed("Live", ["Shohei Ohtani"] + [f"D{i}" for i in range(8)])
    jc = browser.new_context(viewport={"width": 420, "height": 900})
    pg, errs = boot(jc, "/")
    pg.click("#bell-btn")
    pg.wait_for_timeout(150)
    rows = pg.eval_on_selector_all("#bell-list .bell-item", """els => els.map(e => ({
        title: e.querySelector('.bell-title').textContent.trim(),
        time: e.querySelector('.bell-time').textContent.trim()}))""")
    by_title = {r["title"]: r["time"] for r in rows}
    check("J1 a backfilled home run shows the time it was HIT, in ET -- not 'Earlier'",
          by_title.get("Aaron Judge") == "7:10 PM ET" and by_title.get("Juan Soto") == "7:40 PM ET", rows)
    order = [r["title"] for r in rows]
    check("J2 newest on top: Soto's 7:40 home run above Judge's 7:10",
          order.index("Juan Soto") < order.index("Aaron Judge"), order)
    card1 = next((r for r in rows if r["title"].startswith("Card 1")), {})
    check("J3 a bet cashed when its LAST leg landed -- Card 1 needed Soto, at 7:40",
          card1.get("time") == "7:40 PM ET", card1)
    check("J4 ...and sits above the leg that cashed it, which shares its moment",
          order.index(card1.get("title")) < order.index("Juan Soto") if card1 else False, order)
    check("J5 nothing with a known play time says 'Earlier' or 'by'",
          not [r for r in rows if r["time"] == "Earlier" or r["time"].startswith("by ")], rows)
    check("J6 no JavaScript errors", not errs, errs[:3])
    jc.close()

    # Football: a touchdown's time is its scoring play's own clock.
    FX["espn_state"] = "post"
    FX["scoring"] = [{"id": "s1", "type": {"text": "Rushing Touchdown"},
                      "text": "Saquon Barkley 12 Yd Rush (Jake Elliott Kick)",
                      "team": {"abbreviation": "PHI"}, "period": {"number": 2},
                      "clock": {"value": 300, "displayValue": "5:00"}, "awayScore": 7, "homeScore": 0}]
    FX["drive_plays"] = [
        {"id": "p1", "wallclock": "2026-10-04T23:05:00Z", "period": {"number": 1}, "awayScore": 0, "homeScore": 0,
         "statYardage": 15, "text": "(Shotgun) J.Hurts pass short right to A.Brown to NYG 40 for 15 yards (X.Defender)."},
        {"id": "s1", "wallclock": "2026-10-04T23:20:00Z", "period": {"number": 2}, "awayScore": 7, "homeScore": 0,
         "statYardage": 12, "text": "S.Barkley right end for 12 yards, TOUCHDOWN."}]
    for path in ("/football/", "/all/"):
        jc = browser.new_context(viewport={"width": 420, "height": 900})
        pg, errs = boot(jc, path)
        td = pg.evaluate("bellTime(BELL.items.find(i => i.kind === 'leg' && /Barkley/.test(i.who)) || {})")
        check(f"J7 {path}: a backfilled touchdown shows the time it was scored", td == "7:20 PM ET", td)
        check(f"J8 {path}: no JavaScript errors", not errs, errs[:3])
        if path == "/all/":
            got = pg.evaluate("""() => [
                NFL.hitTime({sport: 'nfl', market: 'q_score', team: 'PHI', quarter: 2}),
                NFL.hitTime({sport: 'nfl', market: 'q_score', team: 'NYG', quarter: 2}),
                NFL.hitTime({sport: 'nfl', market: 'rec_yds', player: 'A.J. Brown', team: 'PHI', line: 14.5}),
                NFL.hitTime({sport: 'nfl', market: 'rec_yds', player: 'A.J. Brown', team: 'PHI', line: 20.5})]""")
            check("J9 a team's first score in a quarter is timed off the play where its score went up",
                  got[0] and got[0]["t"] == ms("2026-10-04T23:20:00Z") and not got[0]["approx"], got[0])
            check("J10 ...and a team that never scored that quarter has no time at all", got[1] is None or got[1]["approx"], got[1])
            check("J11 a receiving-yards leg is timed off the catch that cleared its line",
                  got[2] and got[2]["t"] == ms("2026-10-04T23:05:00Z") and not got[2]["approx"], got[2])
            check("J12 ...and one the catches never reached is only a guess, never an exact time",
                  got[3] is None or got[3]["approx"], got[3])
        jc.close()
    FX.pop("scoring"); FX.pop("drive_plays")

    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("all bell checks passed")
