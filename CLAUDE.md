# bmbs.bet — Home Run Card

A live-tracking site for a friend group's home run parlay/prop pool. Static
site on GitHub Pages, custom domain `bmbs.bet` via Namecheap DNS.

## Architecture

- **`index.html`** — the entire live site. Single self-contained file:
  inline CSS, inline JS. No build step, no framework, no dependencies.
  Deploy = commit this file, GitHub Pages serves it directly.
- **`features/index.html`** — a static, plain-language "what this site can
  do" page for end users (the friend group), reusing `index.html`'s exact
  color tokens/fonts so it reads as the same product. Lives at the clean
  URL `/features/` (GitHub Pages resolves a directory request to its
  `index.html`). The old top-level `features.html` is now a redirect stub,
  kept so existing bookmarks/links still work — don't delete it, and don't
  put real content back in it.

  **Verified against the live site, not assumed:** a bare extensionless
  request (`/features`, no trailing slash) resolves to the sibling
  `features.html` FILE, not the `features/` directory — GitHub Pages tries
  the same-named `.html` file before it tries `<name>/index.html`. That
  means the redirect stub's target must be the trailing-slash form
  `/features/`; redirecting to bare `/features` reloads the stub itself
  forever. `index.html`'s own footer link goes straight to `/features/`
  to skip the redirect hop entirely. Linked subtly from
  `index.html`'s footer ("what this site can do"). **Maintained by hand
  only, on request** — the user explicitly does not want this kept in
  sync automatically with feature commits. Don't touch it as a side effect
  of an unrelated change; only edit it when asked to update it.
- **`data/tickets.json`** — the current day's parlay/single-bet picks, in a
  specific schema (see below), stamped with the MLB game `date` the slate
  is for. This is LIVE DATA, not code. Regenerated only by
  `scripts/parse_picks.py`, never hand-edited, never overwritten by a code
  deploy.
- **`data/tickets-previous.json`** — the prior slate, archived by
  `parse_picks.py` the moment a slate with a *different* `date` is parsed
  (same-day re-uploads leave it alone). Feeds the "Yesterday's Picks" tab.
  LIVE DATA, same rules as `tickets.json`.
- **Either tickets file may be absent, and that is a normal state**, not an
  error: a day with no picks submitted renders the tab's empty panel
  ("Waiting for today's picks" / "No picks submitted yesterday"). Only a
  404 counts as absent — any other fetch failure still raises the error
  banner, so a broken deploy can't masquerade as "nobody submitted picks".
  Both files were deleted on 2026-09-18 because everything in them to that
  point was bot-test data rather than real picks.
- **`data/incoming_picks.txt`** — the paste target. The person pastes the
  day's raw picks text here (via GitHub's web editor, no terminal needed)
  and commits. That triggers `.github/workflows/parse-picks.yml`, which
  runs `scripts/parse_picks.py` against it and regenerates `tickets.json`.
  Also written by the Discord intake bot (`discord-bot/`, runs on the
  user's own always-on Windows machine): a friend uploads a `.txt` in a
  Discord channel, confirms with a reaction, and the bot commits it here
  via the GitHub Contents API.
- **`data/roster.json`** — name/team lookup built once from an uploaded MLB
  roster CSV, used to normalize player names and teams during parsing.
- **`scripts/parse_picks.py`** — parses the raw picks text format into
  `tickets.json`. Also resolves player names against `roster.json` (exact
  match, then fuzzy). Accepts the text with or without markdown markers
  (`## ` headers, `* ` bullets) — text copied out of a rendered Gemini
  response has them stripped, and that silently broke parsing once.
  Stamps `date` from the listed start times: a slate posted after its
  last first pitch is for tomorrow, otherwise it's for today (ET).
- **`history/index.html`** — the standalone History page at `/history/`
  (see "History page" below). Own inline CSS/JS; shares nothing with
  `index.html` except one footer link each way.
- **`data/history.json`** — the History page's only data source. PIPELINE
  DATA like the tickets files: written only by `scripts/import_history.py`,
  never hand-edited.
- **`scripts/import_history.py`** + **`scripts/history_player_map.json`** +
  **`.github/workflows/import-history.yml`** — import the group's Google
  Sheet into `data/history.json`, daily at 9am ET.
- **`CNAME`** — contains `bmbs.bet`, required by GitHub Pages for the custom domain.

## How live tracking actually works (important, don't reinvent this)

`index.html` polls the **MLB Stats API directly from the visitor's own
browser** every ~10 seconds — `statsapi.mlb.com`, which is free, keyless,
and has open CORS (confirmed working, not a guess). This is NOT a
server-side cron job. There is no backend. Every viewer's browser
independently computes hit/miss/live state from the same public data.

**Slate dates, not calendar dates.** Polling is keyed on `tickets.json`'s
`date`, never on the clock: MLB files a 10pm ET game under the date it
started, so a slate keeps tracking straight through midnight. A slate
rolls from the "Today's Picks" tab to "Yesterday's Picks" only when every
game on its date is Final per the schedule endpoint (postponed games are
encoded Final, so rain-outs count as done) — or, as a backstop for
suspended games only, at 6am ET the next morning. Today then shows
"Waiting for today's picks" until a slate with a new `date` lands; the
page re-reads both tickets files every poll so that happens without a
reload.

Precisely: **Today is the oldest slate that isn't over yet.** Picks are
often uploaded just after midnight, while the previous night's late game
is still being played — that upload archives the live slate into
`tickets-previous.json`, so without this rule the still-live slate would
be yanked onto the Yesterday tab mid-game. Instead the newer slate is held
as `SLATES.queued` (a small note on the Today tab says so) and takes over
the moment the live one goes final. Final games' feeds are cached and never re-fetched; Preview games'
feeds aren't fetched at all.

**Poll cadence is 10s, and that's the floor -- don't lower it.** Measured
2026-09-18 against live games: the feed carries `metaData.wait: 10`, responses
ship `Cache-Control: max-age=10`, and a game's feed only regenerated every
~18-20s (byte-identical payloads for 17+ seconds at a stretch). Polling faster
just re-downloads cached bytes; it cannot make MLB publish sooner. MLB's own
publish lag (~15-20s) dominates total latency, so the interval is the small
term.

**That 10s is only affordable because of `FEED_FIELDS`.** The full live feed is
~630KB raw / ~104KB gzipped *per game*, and a full slate pulls one per live
game per poll -- over 1MB a cycle. `getGameSnapshot()` sends a `fields=`
allow-list that cuts it to ~15KB gzipped, so 10s polling costs about a quarter of
what the old 20s polling did. `fields` matches field NAMES at any depth, not
paths, and a missing name silently yields `undefined` rather than erroring --
so every name read out of `data` must be listed, including intermediate ones.
`tests/test_feed_fields.py` fetches both the full and slim feed for every
currently-live game and requires `getGameSnapshot()` to compute identical
results; run it after touching that list. It pins both requests with
`timecode=` so a live game moving mid-check can't look like a lost field.

**Polls are non-overlapping** (`pollOnce()`): a full slate can take longer than
10s on a slow connection, and `setInterval` doesn't wait, so two polls could
finish out of order and write a stale slate over a fresher one. A tick landing
mid-poll is dropped. The guard releases in a `finally`, or one thrown error
would kill polling for the rest of the session.

Per-leg states (five total): `hit`, `miss`, `na` (didn't play), `live` (game
in progress, no HR yet), `not_started`. A player is resolved to `hit` the
instant a HR appears in the live play-by-play, regardless of whether their
game has finished.

**Pinch Hit Protection.** Most of the user's books credit the bet if the
player is pulled and whoever's since held their batting-order slot goes on
to homer -- so a pulled player is NOT resolved to `miss` immediately (that
was tried first and reverted; see `pinchHitProtection()`, `stateForPlayer()`
in `index.html`). Instead they stay `live`/`not_started`/`miss` exactly
like an unpulled player, tracking the entire chain of substitutes in that
slot (a double-switch can sub twice) via the live feed's `slotHolders`.
If any of them homers, the leg resolves to `hit` -- counted normally
everywhere (payout, Bettor Tracker, scoreboard) -- but rendered with a
visually distinct badge (green fill + diagonal yellow stripes, CSS class
`php-hit`) and an explanatory note, so it's clear the hit came via PHP and
not the named player's own bat. A player who already has his own hit
before being pulled (rare -- e.g. pinch-run for right after homering)
stays a plain, undecorated hit; PHP framing only applies when the credit
comes from a substitute.

## Irons

An **Iron** is an open bet one home run from cashing, marked with a 🧇
waffle next to the player who still has to go deep.

- **Parlays**: `outcome === "live" && (activeCount - hitCount) === 1`,
  computed in `evaluateTicket()` as `evalRes.iron`. The waffle goes on the
  one active leg that hasn't hit (`isIronLeg()`).
- **Singles**: every open single qualifies -- a single is by nature exactly
  one HR away (`singleIsIron()`). The one exception is state `na`: that
  player never played, so the bet is void/refunded rather than one swing
  away, and it gets no waffle and no count. Note this means a fresh slate
  before first pitch shows a waffle on *every* single; that's intended.

The IRONS chip (amber, between OPEN and HIT) counts parlays and singles in
one total. Irons are a *subset* of Open, not a separate bucket, so an Iron
is counted in both chips.

Two behaviours that are easy to break:
- **The waffle is driven by Iron state, never by the active filter**, so it
  shows under Open and under no filter at all -- not just under Irons.
- **The Irons filter renders the FULL parlay**, every leg including the ones
  already hit, because the point is seeing how close the card is. The
  bettor filter's hide-non-matching-legs behaviour deliberately does not
  apply; `renderContent()` still uses it to decide whether a ticket
  appears, then overrides `visibleLegIdx` to all legs.

A **dead** parlay is never an Iron even when one leg is numerically
unresolved -- `outcome === "live"` excludes it, same as void.

**The scoreboard is one row: BETS** (Open / Irons / Hit / Missed). There used
to be a LEGS row under it (Hit / Missed / N/A / Live / Not Started, with a
`LEG_FILTER`) and, above both, BATTING NOW / BATTING SOON. Both were removed
on 2026-09-18 at the user's request -- Live At Bats replaced the batting
chips, and the user judged the leg chips redundant with BETS. Don't add them
back. The per-leg state colors on the tickets are unchanged; only the
counters and their filter are gone. The remaining filters are
`CURRENT_FILTER` (bets) and `BETTOR_FILTER`.

**Test-helper trap:** `feed()` in `tests/test_page.py` numbers `battingOrder`
per side, because the app reads the FIRST digit as the lineup slot. A flat
`f"{i+1}00"` scheme breaks at the 10th player ("1000" -> slot 1), which makes
them look like a substitute for the leadoff hitter and wrongly triggers
Pinch Hit Protection. That silently corrupted leg states until it was caught.

## Bomb notifications

When a player named in the **live (today) slate** homers, the page announces
it as a "Bomb". Two independent toggles in the header, each persisted
separately in `localStorage` (`bmbs.notif.overlay` / `bmbs.notif.push`),
defaulting to overlay ON / push OFF:

- **Overlay** — a fixed, celebratory card ("<Player> BOMB! 💣"). Fires only
  while `document.visibilityState === "visible"`; a backgrounded tab banks
  nothing. Several at once queue and drain one at a time (`BOMB_MS`), they
  never stack on screen.
- **Push** — the plain Notifications API (no service worker, no server; only
  works while the page is open). `Notification.requestPermission()` is called
  *only* from the toggle's change event, since browsers ignore prompts that
  aren't tied to a user gesture — never on load. Denied flips the toggle back
  off with an inline explanation; an unsupported browser (iOS Safari from a
  website) disables just that toggle. The overlay never depends on this API.

**The flood guard is the important part.** `BOMB_STATE` is keyed on the slate
date, and its first successful poll records whoever has already gone deep
*without* announcing (`seeded`). Without that, opening the page mid-game
would fire a notification for every home run that already happened. Names are
deduped by normalized name, so a player in several legs is one notification.

Deliberately keyed on the player's own home run (`results.hitNames`), NOT on
leg state: a leg credited through Pinch Hit Protection wasn't a bomb by the
player the notification would name.

The two toggles themselves are hidden (not disabled -- `applyActiveTab()`
sets `display:none` on `#notif-row`/`#notif-note`) while browsing Yesterday's
Slate, since `checkForBombs()` only ever looks at `SLATES.today` and showing
notification controls next to frozen, archived results is misleading. This
is visibility only: the saved settings and Today's actual notifications are
completely unaffected by which tab happens to be on screen.

## Live At Bats

A collapsed-by-default panel (Today tab only; open/closed is remembered in
`localStorage` as `bmbs.liveab.open`) showing one tile per picked player who
is **at the plate** or **guaranteed to bat this half-inning** -- the same
`liveContextForPlayer()` logic behind the per-leg "AT THE PLATE NOW" /
"GUARANTEED TO BAT" tags, which are still on the tickets. It replaced the
BATTING NOW / BATTING SOON scoreboard chips and their filter (`ACTION_FILTER`
is gone). Panel order is Live At Bats, Home Run Log, Bettor Tracker.

Eligibility matches those tags: leg still `live` AND at least one bet it's on
can still cash, so a dead parlay's hitter gets no tile. One tile per player
however many tickets he's on (all distinct odds shown); the 🧇 appears if any
of them is an Iron.

**Every scoreboard filter scopes the tiles**, through the same two predicates
the ticket list uses (`ticketMatchesFilter`, `legPassesFilters`) so the panel
can't disagree with the tickets under it: IRONS shows only hitters one swing
from cashing something, a bettor filter only that person's picks, and they
stack. Filtering is per bet, before players are merged, so under IRONS a tile
lists only his Iron prices. HIT / MISSED leave
nothing to show (a tile is by definition a live leg on an open bet) and the
panel says the filter is why. Any new filter toggle must call
`renderLiveAtBats()` -- forgetting that left the panel stale until the next
poll once already.

Tile order is fixed: finished at-bats still holding their spot, then at bat,
then due up (the side batting now by distance from the plate -- ON DECK, IN
THE HOLE -- then the side due up next half). A finished at-bat's result holds
its tile ~9s (home run ~14s, the first ~3s as a bomb), then drops and the rest
slide up. Result tiles expire on their own timer, not the poll.

**How it knows an at-bat ended** (`trackLiveAtBats()`, once per poll):
`getGameSnapshot()` returns `currentAB` plus the last dozen finished plays as
`recentABs`; each finished play's key is announced once. Same flood guard as
bombs -- the first poll seeds without announcing -- plus `LAB.prevEligible`,
because a hitter who just homered is `hit` now and no longer "eligible" but
was a poll ago. At-bats that ended >3 min ago (tab was asleep) and at-bats
that finish while the Yesterday tab is showing are marked seen, not announced.

Two things learned from running against real live games, not mocks:
- **`about.isComplete` is the only trustworthy "finished" signal.** The feed
  writes mid-at-bat actions into `result.event` while the hitter is still up
  -- a real "Batter Timeout" on an 0-1 count got announced as an at-bat's
  outcome before this was fixed. Never infer completion from `result.event`.
- **Only whitelisted event types are announced** (`LAB_PA_RESULTS`). A "play"
  can also end on a runner event (inning-ending caught stealing: same hitter
  leads off next inning). Unknown types cost a missed tile, never a wrong one.

It is pitch-by-pitch *as of the last poll*, not a live stream: several pitches
can land at once, and a short at-bat can start and finish between polls (the
result tile still shows). Pinch Hit Protection substitutes don't get tiles.
No extra API calls -- it reads the same feeds already being fetched. Its
fields (`isComplete`, `count`, `balls`, `strikes`, `call`, `isOut`) are in
`FEED_FIELDS`, and `tests/test_feed_fields.py` compares `currentAB` /
`recentABs` between the full and slim feed along with everything else.

## Home Run Log

A collapsed-by-default panel on both tabs listing every home run from that
slate's date, newest first (sorted on `about.endTime`, which is a full ISO
timestamp on every HR, so ordering works across games). Filter toggle:
"Our Picks" (default) vs "All Home Runs". Rows for a hitter who matters to
the slate get a subtle green left-border + tint under *either* filter --
that's the point of the All mode. "Matters" = named in this tab's
tickets.json, OR a substitute whose HR is currently crediting one of our
legs under Pinch Hit Protection.

Rows are tap-to-expand rather than a wide table: 16 columns of Statcast
detail cannot render on a 560px phone-first page, so the collapsed row
carries hitter/team/inning/pitcher/distance/exit-velo and the expanded
panel carries the rest.

**Field availability was verified against real data, not assumed** --
45 home runs across 24 completed games, all 18 fields 100% populated
(`matchup.batter/batSide/pitcher/pitchHand`, `playEvent.hitData`
launchSpeed/launchAngle/totalDistance/trajectory, `playEvent.pitchData`
startSpeed/zone, `details.type.description`, `gameData.venue/weather`).
Two caveats baked into the code:
- **Roofed parks report `"0 mph, None"` wind** with condition `"Roof
  Closed"`/`"Dome"` (7 of 24 games sampled). `weatherWind()` shows the roof
  instead, so it doesn't read as a genuine calm-air measurement.
- **Live-game Statcast latency is unverified** -- every sampled game was
  Final (no games in progress at the time). Fields are still individually
  guarded and render an em-dash if absent.

Pitch location renders as a 3x3 strike-zone grid (zones 1-9 fill a cell;
11-14 place a chase dot outside the corresponding corner, verified against
pitch coordinates) plus a height word. It deliberately never says
"inside"/"outside": that depends on batter handedness and the API's
coordinate sign convention, which was NOT verified -- don't add it without
checking, since getting it backwards would be silently wrong.

No odds column. There's no Betr/odds integration in this project.

This costs no extra API calls: the app already fetches every game's full
feed for the slate date and previously discarded everything but the
batter's name.

Batting order tracking (current inning, "at the plate now" / "guaranteed to
bat this inning" tags, at-bats-remaining estimate) comes from the same live
feed's boxscore `battingOrder` field, combined with a negative-binomial
model using a league-average 68.5% out rate (NOT the specific hitter's real
stats — this is disclosed in the UI, don't remove that framing).

## History page (separate from live tracking — keep it that way)

`history/index.html` exists under a HARD CONSTRAINT from the user: it must
never touch or risk the Today/Yesterday functionality. It is a separate
file with its own script and its own data file. It does not poll MLB, does
not read `tickets.json` / `tickets-previous.json`, and `index.html` does not
read `history.json`. The only coupling is a footer link in `index.html` to
`/history/` and a back link the other way. `tests/test_history.py` block A
enforces all of this (request log, five minutes of fake clock, source
greps) — if that block fails, the change is wrong, not the test. Don't
refactor shared helpers out of `index.html` "for reuse"; duplication is the
point.

It lives at `history/index.html`, NOT `history.html`: GitHub Pages serves a
sibling `name.html` for a bare `/name` ahead of `name/index.html`, which
caused a redirect loop on `/features` once.

Data facts worth knowing before touching the importer:

- Source is the group's Google Sheet, tabs `Archive` (gid 1001, older
  slates) + `HR Parlays` (gid 0, running log), same column layout. The
  "Solo Tracker" tab is invalid per the user — never import it.
- **Use `/export?format=csv&gid=N`, never `/gviz/tq?tqx=out:csv`.** Most of
  the log's rows are collapsed/hidden in the Sheets UI; gviz silently drops
  hidden rows (it lost ~2,000 of them and made the log look like it had a
  three-week gap). `export` includes them.
- Compute every stat from the raw legs. The sheet's own "Player Stats" tab
  matches names by substring ("Cruz" also counts "Oneil Cruz"; same for
  Bell, Walker, Abreu, Valdez), so its per-player numbers are wrong.
- Real money is NOT derivable: "Amount Wagered" was never filled in. The
  page shows recorded win amounts on cashed parlays and a clearly-labelled
  hypothetical flat-stake ROI — don't present either as actual P&L.
- Odds were only logged from 2026-08-20 (`oddsFrom`); odds-based stats cover
  picks since then and the page says so.
- A blank leg status is `pending`, never guessed. DNP legs are void:
  excluded from hit rates, and a parlay whose other legs all hit still cashed.
- "The ones that got away" joins real MLB game logs at import time via
  `history_player_map.json` (group nickname -> MLB id, hand-reviewed; every
  entry was validated by checking the player's real HR dates against the
  sheet's Hit/Miss marks). Nicknames are ambiguous ("Lowe", "Muncy",
  "Garcia Jr") — never auto-resolve them at import time; use `--draft-map`
  and review. When sheet and MLB disagree the importer reports it and the
  page footnotes the count; it does not silently "fix" the sheet.
- The daily Action is a deliberate, user-chosen exception to the "no
  automated commit workflows" caution in gotcha 3. It runs at a quiet hour
  and commits `data/history.json` only. The importer refuses to write a
  history with fewer parlays than the committed one (`--allow-shrink`).
- `dump()` writes no timestamp, so an unchanged sheet produces no commit.

## tickets.json schema

```json
{
  "date": "2026-09-18",
  "note": "free text, shown at top of page",
  "windows": [
    {
      "title": "e.g. '⚡ 3-Leg Parlay Cards'",
      "tickets": [
        {
          "name": "Card 1 · Some Title",
          "sub": "3-Leg",
          "foot": "prebuilt HTML string: '<b>$3</b> bet by X · Potential payout <b>$500.00</b>'",
          "stake": 3.0,
          "book": "Memo",
          "payout": 500.00,
          "legs": [
            {"id": "p0-c0-l0", "player": "Full Name", "team": "MIA",
             "who": "Kenny", "meta": "MIA &middot; Kenny",
             "odds": "+390", "time": "9:40 PM ET"}
          ]
        }
      ]
    }
  ],
  "singles": [
    {"id": "single-0", "who": "KENNY", "player": "Full Name", "team": "COL",
     "meta": "SD @ COL &middot; 3:10 PM ET &middot; $5.00 bet",
     "odds": "+870", "stake": 5.0, "payout": 58.95, "pp": "PP $58.95"}
  ]
}
```

`leg.who` / `single.who` is the bettor's name — used for the Bettor Tracker
and bettor-filter feature. Must be JUST the name, no trailing annotations
(there was a real bug where source text like "(Noid — Listed as Herb
Hernandez)" got glued onto the `who` field; fixed by stripping anything
after a dash during parsing — don't reintroduce this).

## Known gotchas / mistakes already made once — don't repeat them

1. **NEVER include anything under `data/` in a code handoff/deploy unless
   explicitly asked.** Early on, test/sample data (`test_picks.txt` output)
   got shipped in delivery zips and silently overwrote the user's real
   picks multiple times because "copy the zip contents over the repo" also
   copied stale `tickets.json`. `data/tickets.json`, `data/tickets-previous.json`,
   and `data/incoming_picks.txt` are live user data, managed only through
   the picks-upload → GitHub Actions pipeline. `data/history.json` is pipeline data too (only
   `scripts/import_history.py` writes it). Code changes should only
   ever touch `index.html`, `history/`, `features/`, `scripts/`,
   `.github/workflows/*.yml`, `discord-bot/`, `tests/`, `README.md`, `CNAME`.

2. **Date handling is genuinely tricky here — games run past midnight.**
   The today/yesterday tabs, `date` field, and `tickets-previous.json`
   archive were first built with a **clock-based** rollover (3am ET
   cutover, 10am clear) and reverted at the user's request, because a
   slate must not move until its last game is actually over. They were
   re-added on 2026-09-18, this time rolled over strictly by **game
   state** (see "Slate dates, not calendar dates" above). Do not
   reintroduce any wall-clock rollover rule; the only clock in the logic
   is the deliberate 6am-next-day backstop for suspended games.

3. **Git push race conditions are common — keep it that way by having no
   cron.** A legacy cron (`update-checklist.yml` + `scripts/fetch_home_runs.py`
   + `data/marks.json`) committed results every 10 minutes all evening. It
   had been dead for a long time — `index.html` computes state in the
   browser and never read `marks.json` — but was still firing, racing both
   the parse-picks workflow and the Discord bot, whose read-sha-then-write
   against the GitHub Contents API fails outright if a commit lands in
   between. All three files were deleted on 2026-09-18 (recoverable via
   `git log --diff-filter=D`). Don't reintroduce an automated commit
   workflow without a real reason. For local pushes rejected by a
   concurrent commit: `git pull origin main` (or `--rebase`), then push.

4. **The user primarily works in PowerShell on Windows**, sometimes Git
   Bash, sometimes a GitHub Codespace (browser-based, already authenticated,
   no local files). When giving shell commands, ask or infer which
   environment is active — `findstr` vs `grep`, `copy` vs `cp`, etc. differ.
   Confusion between "am I in the actual repo folder with a configured
   remote, or a random unzipped folder" has caused real problems before —
   `git remote -v` is the fast way to confirm.

5. **Only two people/things should ever write to `tickets.json`:** the
   `parse-picks.yml` workflow (via `parse_picks.py`), or occasionally a
   direct pre-marked hit/miss update at the user's explicit request. Never
   regenerate it from test/sample data as a side effect of testing a code
   change — test in a scratch file instead and restore the real
   `tickets.json` content (or better, don't touch the file on disk at all;
   test with mocked fixtures, e.g. a headless browser with routed fetch
   responses).

## Testing approach that's worked well

No test framework — `tests/` holds plain scripts that exit non-zero
on failure:

- `tests/test_parser.py` — both picks formats (`tests/fixtures/gemini_picks.txt`
  vs `test_picks.txt`) parse identically, the slate-date heuristic, and
  the archive-on-date-change guard (against a temp dir, never `data/`).
- `tests/test_page.py` — Playwright (Python) headless Chromium, serving
  `index.html` through one `page.route("**/*")` handler with in-memory
  fixtures (tickets files, MLB schedule, live feeds) and a pinned clock.
  Covers the past-midnight slate, the all-Final rollover, a new upload
  landing, the 6am backstop, and filter regressions.

- `tests/test_live_at_bats.py` — the Live At Bats panel: one mocked game walked
  forward poll by poll (live count, strikeout, home run/bomb, stale at-bat,
  tab switch), with the pinned clock moved by hand instead of sleeping.
- `tests/test_history.py` — Playwright against `history/index.html` with a
  hand-worked `history.json` fixture: the isolation guarantee (block A),
  every stat, sorting/filters, chart tooltips, degraded data, phone width.
- `tests/test_history_import.py` — the importer, fully offline: inline CSV
  "tabs", a fake MLB fetcher, temp-dir output, the shrink guard.
- `tests/test_feed_fields.py` — the one test that DOES hit the network, since
  mocked fixtures can't prove a `fields=` allow-list is complete. Full vs slim
  feed for every live game, through the page's own `getGameSnapshot()`.

```
pip install -r tests/requirements.txt
python -m playwright install chromium
python tests/test_parser.py && python tests/test_page.py && python tests/test_live_at_bats.py
python tests/test_history_import.py && python tests/test_history.py
python tests/test_feed_fields.py   # needs network; run after editing FEED_FIELDS
```

Windows note: `venv` fails on very long paths and Windows Python has no tz
database (`tzdata` is in the requirements for that reason). Keep using
this pattern for any nontrivial change rather than shipping unverified.

## Deploy process

No build step. Edit `index.html` (or `scripts/*.py`) directly in the repo,
commit, push. GitHub Pages picks it up automatically within a minute or so.
The daily picks-update flow (separate from code deploys) is: paste text
into `data/incoming_picks.txt` via GitHub's web editor, or upload a `.txt`
in the Discord intake channel (see `discord-bot/README.md`) → commit →
GitHub Actions runs `parse_picks.py` → `tickets.json` updates (and the
prior slate is archived if the date changed) → live site reflects it on
the next poll cycle (~10s).
