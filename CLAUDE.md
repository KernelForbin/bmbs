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
  `index.html`'s footer ("what this site can do"). **Kept in sync
  automatically as of 2026-09-20** (previously hand-maintained on request
  only — that restriction is lifted). Scope is strict, though: this page
  lists only **current, live, functional, end-user-noticeable** features —
  something a friend using the site would actually see or interact with.
  Never mention a bug fix, a backend/pipeline change, a refactor, or anything
  that isn't directly visible to a visitor (e.g. the results archive, the
  parser accepting a new card template, or a grading-logic correction are all
  out of scope unless they changed what's ON SCREEN). Update it as part of
  any change that adds/removes/visibly alters a feature; don't let it drift
  stale. Same page shape as the football section already there: color key,
  one short paragraph per feature, in the voice already established.
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
- **`data/roster.json`** — name/team lookup, used to normalize player names
  and teams during parsing. Built by `scripts/build_roster.py` straight
  from the MLB Stats API (every team's active roster). Originally a
  one-time upload of a roster CSV instead; that CSV had every generational
  suffix (Jr./Sr./II/III/IV) stripped from its name column, which silently
  broke live matching for anyone who has one -- confirmed 2026-09-18 when
  Fernando Tatis Jr., Bobby Witt Jr. and Vladimir Guerrero Jr. were all
  picked in the same live slate and none of the three could resolve a hit
  OR a miss all game (stuck at `not_started`, since `normalizeName()`
  doesn't strip suffixes -- MLB's own feed always includes them, so the
  fix is matching them, never stripping them). Rebuilt from the API instead
  of re-uploading a CSV; not a live-data file, ordinary to regenerate.
- **`scripts/parse_picks.py`** — parses the raw picks text format into
  `tickets.json`. Also resolves player names against `roster.json` (exact
  match, then fuzzy). Accepts the text with or without markdown markers
  (`## ` headers, `* ` bullets) — text copied out of a rendered Gemini
  response has them stripped, and that silently broke parsing once.
  Stamps `date` from the listed start times: a slate posted after its
  last first pitch is for tomorrow, otherwise it's for today (ET).
  **The group's picks-generation prompt has changed template at least three
  times without warning**, each time parsing to zero tickets (exit 1, nothing
  written, so the real slate silently never posts) until the new shape was
  added: the original `## Longshot` / `## N-Leg Parlay Cards` headers, then
  "`* Ticket N: TIME | Player (Team) +ODDS (Bettor)`" footed by "`(Bet by X)
  [Bet: $Y | PP: Z]`" (2026-09-18), then "`Ticket #N (Bettor - $X Bet) [PP:
  $Y]`" headers with "`* (Bettor) Player - TEAM (+ODDS) - TIME ET`" legs
  grouped under "`Part N: ...`" headers (2026-09-19), then an emoji-prefixed
  "`🎰 Ticket #N (M-Leg Parlay)`" / "`🔥 Bonus Ticket (M-Leg Parlay)`" header
  (some tickets in the same card omit the leg-count suffix), "`Bet: $X
  (Bettor) | PP: $Y`" on its own line, and "`• Player (TEAM) - TIME ET
  (+ODDS) (Bettor)`" legs (2026-09-20). All four parse from the same input,
  decided purely by which regex a line matches, and a ticket is a single vs.
  a parlay card by its actual leg count, never by which header/window it
  sits under. **The emoji-ticket header is a trap for `section_header()`:**
  "🎰 Ticket #1 (3-Leg Parlay)" contains the literal substring "3-Leg Parlay",
  which `PARLAY_HEADER_RE` matches, so every one of its headers was first
  misread as a brand-new section -- same class of collision football's own
  third template hit the same day (see its entry below), needing the same
  exclusion-guard fix. **If a new upload parses to zero again, that's a
  fifth template, not a regression** — check the Action's run log for
  `WARNING: parsed nothing`, get the raw text, and add support the same way
  (`tests/test_parser.py` has a fixture + assertions per template).
- **`history/index.html`** — the standalone History page at `/history/`
  (see "History page" below). Own inline CSS/JS; shares nothing with
  `index.html` except one footer link each way.
- **`data/history.json`** — the History page's only data source. PIPELINE
  DATA like the tickets files: written only by `scripts/import_history.py`,
  never hand-edited.
- **`data/results/<date>.json`** — one permanent record per finished slate,
  written only by `scripts/record_results.py` (see "Results archive" below).
  PIPELINE DATA. Football's twin is `data/football/results/<weekEnds>.json`.
- **`scripts/import_history.py`** + **`scripts/history_player_map.json`** +
  **`.github/workflows/import-history.yml`** — build `data/history.json` from
  the group's Google Sheet (older slates) plus `data/results/` (slates the
  tracker recorded itself), daily at 9am ET.
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
That pin is to the second, so a mid-at-bat game can still differ on one poll;
a single-game `recentABs` diff that doesn't reproduce is that, not a lost
field -- confirm by re-running before chasing it.

**Don't put a double-quoted phrase in a `FEED_FIELDS` comment.** The test
extracts the list by scraping quoted strings out of the array's source, so a
comment like `// "did he actually bat?"` was picked up as a field NAME and
spliced into the request URL (its space raised `InvalidURL`). The extractor
now strips `//` comments first, but the array is still read as text, not
parsed as JS.

**Anything that prints a time must pass `timeZone` explicitly.** The whole
site talks in ET, so a formatted time gets an "ET" label -- and
`toLocaleTimeString` with no `timeZone` silently formats the VISITOR's own
clock instead. `updateSyncLine()`'s "last updated ... ET" line did exactly
that until 2026-09-20: a viewer out west read their own wall clock under an ET
label, three hours off. Both sports had it (fixed in both). `nowET()` was
always correct -- it passes `timeZone: "America/New_York"` to
`Intl.DateTimeFormat` -- so copy that, and note the bug is invisible on an
Eastern machine, which is why it survived so long. `test_page.py`'s section Q
and `test_football.py`'s section K pin a browser to Pacific to catch it.

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
  bettor/leg filters' hide-non-matching-legs behaviour deliberately does not
  apply; `renderContent()` still uses them to decide whether a ticket
  appears, then overrides `visibleLegIdx` to all legs. **The expansion is
  decided PER TICKET** (`BET_FILTERS.has("irons") && evalRes.iron`), not by
  "the irons filter is on": since the chips multi-select, Open + Irons renders
  plain open cards too, and those must still hide their non-matching legs.
  What the expansion does NOT override is whether a card appears at all -- an
  Iron with no leg surviving the leg/bettor filters is hidden like any other.

A **dead** parlay is never an Iron even when one leg is numerically
unresolved -- `outcome === "live"` excludes it, same as void.

**The scoreboard is TWO rows: BETS** (Open / Irons / Hit / Missed) **and LEGS**
(Live / Hit / Missed / N/A), both sports. Read the history before changing
either, because it has gone back and forth once already:
- The original LEGS row was five chips (Hit / Missed / N/A / Live / Not
  Started) and, above both rows, BATTING NOW / BATTING SOON. All of it was
  removed on 2026-09-18 at the user's request -- Live At Bats had replaced
  the batting chips, and the leg chips read as redundant with BETS. **Don't
  bring BATTING NOW / BATTING SOON back**; that one still stands.
- The LEGS row itself came back on 2026-09-20, asked for explicitly, and this
  file's old "don't add them back" line went with it. It is now **four** chips,
  not five: `not_started` is bucketed into LIVE by `legFilterBucket()` so the
  row fits a 560px phone next to the 4-wide BETS row, and the always-on status
  line under each leg (below) says which of the two a pick actually is.
- The leg chips hide non-matching LEGS *inside* a card, exactly like the
  bettor filter, and note how many they hid; a card with no surviving leg
  drops out. Both leg-level filters go through `legPassesFilters(state, who)`.

**All three filters are MULTI-SELECT** (2026-09-20, both sports). They are
Sets, not single values: `BET_FILTERS`, `LEG_FILTERS`, `BETTOR_FILTERS`.
- **OR within a group, AND across groups.** Bets: Open + Hit shows both kinds
  of bet; Bets:Open + Legs:Missed + Bettor:Kenny means "Kenny's missed legs on
  bets that are still open". An empty set means "no filter from this group",
  never "nothing matches" -- check `.size` before testing membership.
- **Clicking a chip toggles only that chip.** Nothing else is cleared. Before
  this, picking a second chip silently replaced the first.
- `FILTER_GROUPS` is the single table the chips, the summary bar and
  `clearAllFilters()` all read, so they can't drift apart. Adding a fourth
  group means adding a row there, not four parallel code paths.
- **One consolidated summary bar** (`#filter-summary`) replaced the three
  per-group status strips. It lists every active filter as its own removable
  chip plus a Clear all button, and it is **`position: sticky`** -- which is
  why it lives OUTSIDE `<header>`: sticky is confined to its containing block,
  so inside the header it would stop following the moment the header scrolled
  away. That matters because the bettor filter is set from a panel far down
  the page.
- **Summary chips and Bettor Tracker rows carry `data-group`/`data-key` and are
  handled by ONE delegated listener each** (`initFilterHandlers()`), not an
  inline `onclick`. A bettor key is a name off the card, and a name with an
  apostrophe spliced into `onclick="f('x')"` breaks the handler -- the
  attribute decodes `&#39;` back to a quote before JS ever parses it. The
  containers are safe to delegate on because only their `innerHTML` is
  rebuilt each poll, never the containers themselves.

All three reset on a tab switch, and **every filter change must fan out through
`applyFilters()`**, which calls `renderLiveAtBats()` / `renderLiveDrives()` --
forgetting that left the panel stale once already.

**Page layout, reshuffled 2026-09-20** (both sports, user's call):
- `<h1>` now names the sport -- "BMBS Tracker &mdash; Baseball" / "&mdash; Football"
  -- and comes FIRST, with the LIVE FROM MLB / LIVE FROM ESPN eyebrow under it
  and the last-updated line directly under that. The sync line lost its
  "Live &mdash; " prefix (the eyebrow already says the slate is live).
- The colour key moved out of the header to the bottom of the page, above the
  footer, under a "COLOR KEY" label. `#legend-hit` / `#legend-miss` are still
  rewritten by `computeAll()` for steal slates -- the ids moved with it.
- **The "Auto-tracked against live MLB/NFL results." boilerplate is gone**, as
  redundant with the eyebrow. `#dynamic-note` itself MUST STAY: it is the only
  place an unread bet line surfaces (`BETLIKE_RE` -> `tickets.json`'s `note`),
  and dropping that would put the site back to silently posting 16 of 18
  tickets. It now renders "Heads up &mdash; &lt;note&gt;" when there is a
  warning and is empty otherwise, and `test_page.py`'s section O pins both.

**Every pick carries a status line, always** (`playerStatusLine()`, both
sports, 2026-09-20). `liveContextHtml()` used to return "" whenever it had
nothing rich to say -- before first pitch, for a player not in the lineup,
for a void or missed leg -- which on screen is indistinguishable from the page
having no idea. Now those branches fall through to one plain sentence
("Game hasn't started yet.", "Game on -- not in the lineup yet.", "On the
roster but never got in the game -- void / refunded.", "Game final -- no home
run."). A hit is the one state that returns "": the green check already said it.

**The bet lists are collapsible too** (`cardsSectionHtml()`, both sports):
`#content` renders as **Parlay Cards** and **Straight Bet Cards**, each with
the same shell as the panels above it, each carrying an unfiltered
`3 OPEN · 1 HIT · 2 MISSED` pill, and **both default to collapsed**. The open
state lives in `CARDS_OPEN` (persisted as `bmbs.cards.*` / `bmbs.fb.cards.*`)
rather than a DOM class, because `renderContent()` rebuilds that HTML on every
poll -- a class on the element would be wiped every 10 seconds. `#content`
itself is kept as the outer id so existing selectors still resolve.
**Test-helper consequence:** Playwright's `inner_text()` only returns VISIBLE
text, so every test that reads the bet lists has to open them first --
`expand_cards(page)` in both page test files does it, and only the section that
checks the collapsed default skips it (`open_page(..., expand=False)`).

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

**No alert for a home run that can't change anything** (2026-09-20, both
sports). `betsStillOpenFor()` checks, at the moment he goes deep, whether at
least one of his bets is still alive: any single he's on (a single can't be
dead while its own leg is the hit that just happened), or a parlay whose
`evaluateTicket()` outcome isn't `"dead"`. If every bet naming him is a parlay
already killed by some OTHER leg's earlier miss, `checkForBombs()` still marks
him seen (a dead parlay stays dead forever, so this is decided once and never
worth re-checking) but never calls `fireBomb()` -- no overlay, no push. This is
a firing decision only; the leg itself still resolves to `hit` on the page
exactly as before, and `betsCashedBy()`'s own dead-parlay handling (it simply
finds nothing to cash) is unchanged. Football's twin is `betsStillOpenFor()` in
`football/index.html`, same logic minus the market dimension.

**A bomb that cashes a bet is the same alert, upgraded** -- never a second
one. `betsCashedBy()` asks which of today's bets naming that player are now
fully hit (every single on him, plus any parlay his homer completed), and if
any are, the overlay turns gold, rains dollar signs / money bags down the
whole screen (behind the card, so nothing lands on the text), adds a line
like "2-LEG PARLAY CASHED $154.00" / "SINGLE CASHED" / "2 BETS CASHED" with
the combined payout, and holds longer (`BOMB_CASH_MS`). The push notification
gets a money bag in the title and the same line as its body. It evaluates with
the same `stateForPlayer()` / `evaluateTicket()` the tickets render from, so
void legs, Pinch Hit Protection and adjusted payouts all agree with the page.
Those helpers read the `RESULTS` global, which follows the TAB on screen, so
`betsCashedBy()` swaps in today's results for the duration (restored in a
`finally`) -- bombs are about today even while Yesterday is showing. Since
every open single is an Iron, any picked player with a single on him gets the
gold version; the plain green bomb is for homers that cash nothing yet.

The two toggles themselves are hidden (not disabled -- `applyActiveTab()`
sets `display:none` on `#notif-row`/`#notif-note`) while browsing Yesterday's
Slate, since `checkForBombs()` only ever looks at `SLATES.today` and showing
notification controls next to frozen, archived results is misleading. This
is visibility only: the saved settings and Today's actual notifications are
completely unaffected by which tab happens to be on screen.

## Bet markets: home runs and stolen bases

Since 2026-09-19 a leg can be a STOLEN BASE bet: `"market": "sb"` on the leg or
single. **No market field means home run**, and that default is the whole
safety story: `stateForLeg()` hands a market-less leg straight to the untouched
`stateForPlayer()`, the parser writes no market on home run legs (a home-run-only
card's `tickets.json` is byte-for-byte what it was), and `test_page.py` /
`test_live_at_bats.py` pass unchanged. One ticket can mix markets, and one
player can be on both (his steal leg and his home run leg grade separately).
The user asked for HR + steals only; a third market would follow the same path.

**Grading a steal leg** (`stateForSteal()`; mirrored in `record_results.py`'s
`grade_steal()` -- change one, change the other). Three rules the user decided:
- A hit is a `stolen_base_*` RUNNER event. Steals live in each play's
  `runners[]`, inside somebody else's plate appearance -- usually one that isn't
  complete yet -- never in the play's own `result`. Verified on a live game:
  a `pickoff_caught_stealing_3b` was in the feed with `isComplete: false`. One
  steal can be several runner entries (one per base-to-base segment), so events
  are keyed on at-bat + `playIndex` + runner. `caught_stealing_*` and
  `pickoff_caught_stealing_*` are attempts, not hits.
- **No Pinch Hit Protection.** A pulled player can't re-enter, so with no steal
  he's a miss immediately and his replacement's steal credits nobody.
- **"Played" = appeared in the game** (holds a batting-order spot), NOT "came to
  the plate": a pinch runner can steal without batting. On a finished game's
  roster without getting in -> `na`. (Deliberately different from home run legs.)

**Alerts are per market.** A bomb fires only for a player we have a HOME RUN
bet on, a steal alert ("STOLE 2ND!", blue; gold + money rain when it cashes
something) only for a steal pick, and `betsCashedBy()` takes the market so a
steal can't re-announce a parlay his homer finished an hour ago. The Home Run
Log's "ours" is home-run picks only. Same seeding flood guard, same queue.

**Live Bet Tracker** (the panel formerly "Live At Bats"; ids and the
`bmbs.liveab.open` key kept). Home run tiles are unchanged. A steal pick gets
an **ON 1ST / 2ND / 3RD** tile (blue) while he's on base -- base diamond, outs,
who's batting, and whether the next base is open or "blocked -- runner on 2nd".
Order: results, then runners with an open base, hitters at the plate, blocked
runners, then due-up. A steal pick who reaches goes straight to his on-base tile
(the "Single" result would only sit in front of it). Results: "Stole 2nd!" holds
like a home run, "Caught stealing" / "STRANDED" / "OFF THE BASES" briefly. Bases
come from `linescore.offense.first/second/third`; watched against live games
alongside `currentPlay.matchup.postOn*` and the two never disagreed.
`FEED_FIELDS` gained `runners, runner, playIndex, first, second, third` (~1-2 KB
gzipped per game); `test_feed_fields.py` compares steals and bases too.

**Odds can be minus money now** (a steal often is). Every baseball odds regex
takes `[+-]`, and the Bettor Tracker / tiles format with `fmtOdds()`.

**How a card says "steal".** The first real one (2026-09-19, kept as
`tests/fixtures/discord_prop_legs_with_steals.txt`) spells the market out on
every leg, prop-style, under a ticket header with an extra combined-odds bracket:
`Ticket #17 (Bailey - $5 Bet) [+2925] [PP: $151.25]` then
`- Josh Naylor - Stolen Bases O0.5 (+450) - SEA @ COL - 8:10 PM ET` /
`- Ben Rice - Home Runs O0.5 (+450) - NYY @ ARI - 8:10 PM ET`. No per-leg bettor
(the leg belongs to whoever placed the ticket), a matchup instead of a team (the
roster supplies the team). `TICKET_PROP_LEG_RE` reads it; an over other than 0.5
gets a NOTE, since the tracker only knows "at least one". The older, tolerant
`take_market()` (a bare `SB` / `Stolen Base` / `Steal` on a leg line, ticket
header or section header) is kept for cards that mark steals that way instead.

**A bet line nothing understands is never dropped silently.** Before the prop
format was handled, that same card parsed "successfully" as 16 of its 18 tickets
and said nothing. Now any unmatched line that looks like a bet (`BETLIKE_RE`: a
`Ticket #N` header, or a bulleted line with odds) is logged as a WARNING and
summarized in `tickets.json`'s `note`, which the page shows at the top -- the
slate still posts, but the group can see something is missing.

**History mixes the two markets** in hit rates and odds bands -- steals are
priced nothing like homers. Legs are tagged (`market: "sb"`) so they can be split
later; the log marks them "SB". Steal legs are excluded from the MLB home-run
cross-check and from "the ones that got away".

## Live At Bats

(Now titled **Live Bet Tracker** on the page -- see "Bet markets" above for the steal tiles. Everything below still describes the home run tiles.)

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

Three things learned from running against real live games, not mocks:
- **`about.isComplete` is the only trustworthy "finished" signal.** The feed
  writes mid-at-bat actions into `result.event` while the hitter is still up
  -- a real "Batter Timeout" on an 0-1 count got announced as an at-bat's
  outcome before this was fixed. Never infer completion from `result.event`.
- **Only whitelisted event types are announced** (`LAB_PA_RESULTS`). A "play"
  can also end on a runner event (inning-ending caught stealing: same hitter
  leads off next inning). Unknown types cost a missed tile, never a wrong one.
- **`inningState` has FOUR values and "End" is not "Bottom".** `Top` and
  `Bottom` are halves in progress; `Middle` (top over, home up next) and `End`
  (bottom over, AWAY up next, new inning) are the breaks between them.
  `currentlyBattingSide` read `halfState === "Top" ? "away" : "home"`, which
  put the home team at the plate all through `End` -- the wrong team entirely.
  Logged over an hour of live games on 2026-09-19 before fixing: 10 `End`
  windows, `offense` populated in 10 of 10 (so the branch always evaluates),
  median 71s -- about 7 consecutive polls, not a flicker. `Middle` is just as
  real (10 windows, median 91s), so neither state is dead.
  **`outs` reads 3 during both breaks** (all 20 windows), belonging to the half
  that just ended -- so the side about to lead off must not be charged it, the
  same allowance `remainingOutsForSide()` already makes. First applied to `End`
  only (fixing `Middle` too broke `test_steals.py`'s H3, which was itself
  asserting the bug -- a HOME steal pick vanishing after his STRANDED tile
  expired, rather than correctly showing ON DECK). Fixed properly once H3 was
  updated to expect the correct tile instead.
  **A second, related ghost-tag bug surfaced while fixing `Middle`:**
  `isCurrentlyBatting` (the flag behind "AT THE PLATE NOW") checked only
  `side === currentlyBattingSide && d === 0`, with no `betweenHalves` check of
  its own -- so the LITERAL next batter (not just someone a few slots out) was
  still tagged "at the plate" during a break, when nobody actually is. Same
  root cause, same fix shape: force it false during `Middle`/`End`, letting
  `guaranteedThisHalfInning` carry him instead. Covered end to end by
  `test_live_at_bats.py` J1-J7 (`End`) and K1-K5 (`Middle`), each checking the
  literal next batter (the ghost-tag case) separately from someone a few slots
  further out (the stale-outs case) so the two bugs can't hide behind each
  other.

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

**The collapsed header itself carries a live `N OURS &middot; M TOTAL &middot; P%`
pill** (`#hrlog-count`, styled like Live At Bats' count pill) so the tally is
visible without expanding the panel. `P` is plain `ours / total` rounded --
what share of the day's home runs came off a bat we picked, NOT weighted by
how many legs named him. It reflects both filters' totals regardless of which
one (`HR_FILTER`) is currently selected, is recomputed every `renderHrLog()`
call (poll, tab switch, new upload), and is empty -- hidden by
`.hrlog-count:empty` -- when the slate has zero home runs so far. Football's
Touchdown Log gained the identical pill (`#tdlog-count`) on 2026-09-20, and
both Bettor Trackers gained an `N BETTORS` one in their headers; all four use
the shared `.head-count` style.

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

## Football (anytime-touchdown) tracker -- a separate site in the same repo

`football/index.html` at `/football/` is the NFL twin of the baseball page:
Today's / Yesterday's picks, payout estimate, Irons, Bettor Tracker, touchdown
alerts (with the cash version), a **Touchdown Log**, and **Live Drives** (the
analogue of Live At Bats). Built 2026-09-19 under the instruction "do not
modify anything we have done to the baseball configuration" except three
things: the sport switch at the top of `index.html` (markup + CSS only, no
script), and the Discord bot's routing. Keep it that way:

- It was assembled ONCE from `index.html` as of commit `2268dc1` (the
  sport-agnostic slate/tab/filter/alert code is byte-identical) and is now an
  independent file. There is no build step and no shared script -- a fix that
  applies to both sports is made twice, on purpose. `tests/test_football.py`
  block A enforces the isolation both ways.
- Own data: `data/football/{tickets,tickets-previous}.json` (LIVE DATA, same
  rules as baseball's), `data/football/incoming_picks.txt` (paste target),
  `data/football/roster.json`. Own parser `scripts/parse_football_picks.py`,
  own workflow `parse-football-picks.yml`, own roster builder
  `scripts/build_football_roster.py`. None of them import baseball's.
  **The football card template has changed too, independently of
  baseball's** -- a card posted 2026-09-20 arrived as `Ticket N (M-Leg
  Parlay)` headers, `Bettor: X | Bet: $Y | Potential Payout: $Z` on their
  own line, and `- (Bettor) Player (ODDS) Time` legs (no team, no "ET",
  time can be free text like "Check Listings"), which the parser's first
  two templates didn't cover: exit 1, nothing written, that day's real
  slate never posted. Fixed the same day. One sharp trap in the fix: the
  header's own `"5-Leg Parlay"` text is a literal substring
  `PARLAY_HEADER_RE` matches, so every ticket header was first misread as
  a brand-new section (`section_header()` needed an explicit exclusion,
  the same class of guard `CARD_HEADER_RE` already has there). This is a
  DIFFERENT third shape from baseball's own third template, not the same
  one arriving late -- the two parsers' accepted card shapes are
  independent and have now diverged for real. If a future upload parses to
  zero again, check which sport and don't assume it's the other sport's
  already-known shape (`tests/test_football_parser.py` has a fixture +
  assertions per template, same pattern as `test_parser.py`).
- **The Discord bot routes by FILE NAME only**: `baseball*.txt` ->
  `data/incoming_picks.txt`, `football*.txt` -> `data/football/incoming_picks.txt`,
  anything else is refused with a rename hint. It never inspects the text --
  the cards share a template, and a guess would eventually overwrite the wrong
  sport's slate. The bot runs from this clone on the user's Windows machine
  (Task Scheduler), so a change to `discord-bot/bot.py` does nothing until that
  process is restarted.

**Data source: ESPN, from the visitor's browser, no backend -- but mind the host.**
`site.api.espn.com` answers `curl` with `Access-Control-Allow-Origin: *` and
then OMITS the header for a real browser on another origin. The same API on
**`site.web.api.espn.com`** (and `sports.core.api.espn.com`) is allowed. This
was only caught by fetching from bmbs.bet in real Chromium after curl said it
was fine -- test CORS in a browser, never with curl. **Python must use
`site.web.api.espn.com` too:** on 2026-09-19 `site.api.espn.com` answered
`urllib` with 403 (Akamai) while curl still got 200, which had silently pushed
`parse_football_picks.py` onto its no-schedule fallback. All three football
scripts were switched. Endpoints, all keyless:
- `scoreboard?dates=YYYYMMDD` -- one date per call (a date RANGE returns 400).
  Games are filed under their ET date, so a Sunday 8:20 PM game (00:20Z Monday)
  is still Sunday's -- same property the MLB schedule has.
- `summary?event=ID` -- ~60KB gzipped, cached only ~3s (`max-age=3`), can't be
  trimmed. Boxscore has athlete ids and a TD column per category.
- core `.../competitors/{teamId}/roster` -- per-game `didNotPlay` flags.
Polls every 15s. Final games are fetched once; pre-game ones not at all; live
games none of the picks are in are refreshed once a minute (they only feed the
"All Touchdowns" log).

**What counts as a hit.** Anytime TD = the boxscore TD column summed over
rushing, receiving, defensive, interceptions, kickReturns, puntReturns.
**Passing is excluded** -- throwing one doesn't cash the passer. Scoring-play
text is parsed only to describe the touchdown and as a by-name backstop.

**Picks match by ESPN athlete id first, name second.** The parser stores
`athleteId` on every leg. Names normalize with generational suffixes STRIPPED
on both sides (ESPN writes "James Cook III", "Marvin Harrison Jr."; cards
don't) -- the lesson baseball learned live with "Fernando Tatis". A by-name hit
only counts if the touchdown was scored for the pick's own team: there are two
Josh Allens, and the linebacker's pick-six must not cash the quarterback.

**miss vs void.** Game Final and the pick has a stat line -> miss. No stat line
-> ask the roster endpoint: `didNotPlay` (or absent) -> `na` (void), played ->
miss (a blocking tight end who never touched the ball really did lose). A pick
the card couldn't resolve to an id and who has no stat line -> `na`.

**A football slate is an NFL WEEK.** The tabs read "This Week's Picks" / "Last
Week's Picks" (the internal names are still `today` / `yesterday`, as in the
code football was assembled from), and a card stays on This Week until the
week's LAST game -- Monday night -- is final, *even a Sunday-only card*. That's
the user's rule (2026-09-19); don't shorten it to "the picked teams' last game".
The parser dates the slate from the NFL SCHEDULE, not the clock (cards go up
days early): `date` = the earliest upcoming game among the picked teams,
`endDate` = the last day of that NFL week with any game on it, `weekEnds` = the
week's Tuesday, which names the week. An NFL week runs Wednesday..Tuesday -- a
flat "+4 days" from Sunday reaches next Thursday and once stretched a slate
across two weeks. A second card in the SAME week (Thursday's, then Sunday's, or
a correction) REPLACES the first and leaves Last Week alone; only a card for a
new week archives the old one -- so a Thursday card's picks disappear when a
Sunday card is uploaded unless the Sunday card repeats them. The page polls
every date in the span (settled dates -- all Final, or empty and in the past --
are asked once and cached in `SETTLED_SCHEDULES`) and rolls over when every
game on every date is Final, or 6am ET after `endDate`. If ESPN is unreachable
the parser falls back to the card's kickoff times and the following Monday.

**Odds can be negative** (a star back is often -120; a home run never is).
Every football odds regex takes `[+-]`, and the Bettor Tracker formats with
`fmtOdds()` so an average never renders as "+-135".

**Live Drives.** A football pick is "live" for three hours, so the panel sorts
by whether his OFFENSE is on the field. **Three tile states as of 2026-09-20,
and green means "he can score on this play" -- nothing looser:**
- **RED ZONE** (red), **ON OFFENSE** (green) -- his side has possession AND
  ESPN has their drive open.
- **TAKING THE FIELD SOON** (grey, dashed) -- possession is theirs but their
  drive isn't open yet: the gap between the other team giving the ball up and
  this one snapping it.
- **Nothing at all** when his team is on defense. ON DEFENSE / HALFTIME /
  BETWEEN DRIVES tiles were removed at the user's request -- baseball has
  never shown a tile for a player whose side isn't batting, and a tile he
  can't score from is noise. The LIVE count in the header still counts him.

Possession comes from the LAST PLAY's end state (or the scoreboard's
`situation.possession`), which is NOT the same thing as `drives.current.team`:
a drive object only appears once its first play posts, so after a punt,
turnover or score the possession flag flips while `drives.current` still names
the team that just gave it up. `drive.driveTeam` carries that second value and
the mismatch is what defines TAKING THE FIELD SOON. **Measured against live
games on 2026-09-20: three clean windows at 65s, 82s and 179s** -- several
polls wide, a real state rather than a flicker. (Windows that span halftime
read longer and aren't clean measurements: during halftime `situation.possession`
is absent entirely, so nobody gets a tile at all.)

**ESPN publishes no personnel data, so "is HE on the field" is not knowable**
and the page never claims it. A play's `participants` array names only the
players involved in that play -- rusher, tackler, passer, receiver -- never the
eleven lined up, so a tight end who hasn't been targeted is indistinguishable
from one standing on the sideline. Checked directly against a live game
(MIN @ CHI, 2026-09-20) before the states above were designed around it; don't
re-derive this from the field name and assume otherwise.

When a drive ends its result holds the tile ~9s ("Punt", "Field Goal", and
"TD -- not him" when a teammate scored); the pick's own touchdown turns his
tile into a football, then the play, ~14s. Same seeding / stale / off-tab
guards as Live At Bats. Alerts and preferences use their own localStorage keys
(`bmbs.fb.*`). The per-leg tag on the tickets uses the same three states, so
the wording is "ON OFFENSE" everywhere -- "HAS THE BALL" is gone from both.

Football's History page is `/football/history/` -- see "Results archive".
`features/index.html` is kept in sync automatically now -- see its entry at
the top of this file for scope (live/functional/user-visible only).

## Results archive -- the site's memory (both sports)

The live pages grade every pick in the visitor's browser and keep nothing; a
slate that rolls off Yesterday / Last Week was simply gone. Since 2026-09-19 a
daily job writes finished slates down, and the History pages read from that.

- **`scripts/record_results.py`** -> **`data/results/<date>.json`**, one file per
  baseball slate, kept forever. The permanent record: every bet as posted
  (card, who placed it, stake, listed payout, legs with player / team / bettor /
  odds / time), each leg's result + MLB id + Pinch Hit Protection credit + the
  Statcast detail of his home runs, each bet's outcome and what it actually
  returned, and every home run in the league that day.
- **`scripts/import_history.py`** then rebuilds `data/history.json` from TWO
  sources: the group's sheet (every slate before the tracker kept its own
  record) and `data/results/`. **On a date both cover, the tracker's record
  wins** and the sheet's rows are dropped, so it doesn't matter whether anyone
  keeps filling in the sheet. If the sheet can't be read at all, the sheet
  parlays already in `history.json` are reused with a warning -- a dead sheet
  must never block new slates.
- **`scripts/record_football_results.py`** ->
  **`data/football/results/<weekEnds>.json`** (one per NFL week) and rebuilds
  **`data/football/history.json`**, which **`football/history/index.html`**
  (`/football/history/`) reads. Football has no sheet: this IS its history. The
  page was adapted ONCE from `history/index.html` and is independent since
  (same rule as `football/index.html`); an absent or empty history file is a
  normal state ("No finished weeks yet"), not an error.
- All three run from **`.github/workflows/import-history.yml`** (file name kept
  so the Actions history stays in one place), once a day at 9am ET, ONE job and
  ONE commit. Each step continues on error so one sport failing can't block the
  other's commit; the job is failed at the very end instead.

**Why 9am and not "when Today rolls to Yesterday":** that rollover happens
inside each visitor's browser -- there is no server to notice it. Catching it
would take a cron polling all evening, which is exactly what gotcha 3 is about.
A slate sits in `tickets.json` / `tickets-previous.json` for at least a day
after it ends, so one run a day sees every slate, and a missed day is caught by
the next. The user asked the question and this was the answer; don't move it to
an evening schedule.

A slate is recorded when every game on its date(s) is Final, or two days later
regardless (suspended game; that record is `"complete": false` and is re-graded
on later runs). A record carries a hash of the picks it was graded from: same
picks -> skipped without touching the network; picks corrected afterwards ->
graded again. Re-running is always safe.

**The graders are ports of the pages' own grading** (`stateForPlayer`, Pinch Hit
Protection, `evaluateTicket`, football's id-then-name+team matching), and were
checked against the live site on the first real slate: all 50 legs of
2026-09-18 and all 36 league home runs matched. A change to a page's grading
rules has to be made in its recorder too.

**Benched players are void, and the two agree again as of 2026-09-20.** MLB's
boxscore lists the whole active roster, so "is he in the boxscore?" says
nothing about whether he got in. The recorder always required a PLATE
APPEARANCE for a home run leg -- benched, or only a pinch runner / late
defensive sub -> `na` -- while `index.html` graded that same player a MISS.
Found on the first recorded slate: Andres Gimenez and Bryce Eldridge
(2026-09-18) were misses on the page, and the group's own sheet had Gimenez as
DNP. It was reported rather than fixed at the time, because `index.html`'s
grading isn't touched without asking; the user was asked on 2026-09-20 and
chose to fix it. The page is now a direct port of the recorder's rule:
`getGameSnapshot()` builds a `played` set from `stats.batting.plateAppearances`
(falling back to `battingOrder` / `allPositions` / any batting object), and
`pollSlate()` only writes a name into `rosterStatus` when his game is still on
OR he's in `played`. Two things to keep straight if you touch it:
- **`inBox` is a separate map** holding everyone on a roster regardless, and
  `stateForSteal()` reads THAT, not `rosterStatus` -- a steal bet asks "was he
  on the field", never "did he bat" (a pinch runner can steal without batting).
  Same split the recorder has always had.
- The page settles a benched player as soon as **his own** game is final
  (`inBox.get(norm) === "final"`), not when the whole slate is. The recorder
  only ever runs on finished slates so it never needed that; without it a
  benched player would read "not started" for hours while other games ran.
This changes leg states, parlay outcomes and adjusted payouts on the live page,
so it is a real grading change, not cosmetic. A change to a page's grading
rules still has to be made in its recorder too, and vice versa.

Things learned from the first merge, all handled in `import_history.py`:
- **The sheet mis-dates slates.** It logged the 2026-09-18 slate under 9/17
  (MLB confirms those homers were on the 18th), so the merged history counted it
  twice. `drop_misdated_copies()` drops a sheet slate one day either side of a
  recorded one when 80%+ of its (bettor, odds) legs match. The tracker's date
  comes from the MLB schedule, so its copy is the one kept.
- **The sheet's hand-typed marks have errors** the tracker doesn't: on that same
  slate it had Miguel Vargas (homered, 403 ft) as a miss and Dillon Dingler
  (3 plate appearances) as DNP.
- **Names.** The sheet uses shorthand ("Judge", "PCA"), the tracker full names.
  A recorded leg takes the sheet's nickname when `history_player_map.json` ties
  it to the same MLB id, so one player isn't two rows; the full name rides along
  as `name`. Anyone unmapped keeps his full name and may therefore appear twice
  in the Players table until someone adds a map entry (`--draft-map`). Recorded
  players join "the ones that got away" without a map entry -- their id came
  from the boxscore they were graded from, not a guess.
- The sheet already logged singles as 1-leg rows (since 9/14), so recorded
  singles are 1-leg bets too, flagged `"kind": "single"`.

`history.json` additions on recorded bets: `src: "site"`, `kind`, `name`, `book`
(who placed it), `stake`, `payout`, `returned`, `won`; on legs: `name`, `team`,
`php`, `dist`. Top level: `recordedFrom`. The History page uses them for a
**Real money** tile (actual staked vs returned -- possible for the first time,
since the sheet never had stakes) and stake / distance / PHP detail in the log.

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
  and commits only `data/history.json`, `data/results/` and football's twins
  (see "Results archive"). The importer refuses to write a
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
   and `data/incoming_picks.txt` (plus their `data/football/` twins) are live user data, managed only through
   the picks-upload → GitHub Actions pipeline. `data/history.json` is pipeline data too (only
   `scripts/import_history.py` writes it), and so are `data/results/`,
   `data/football/results/` and `data/football/history.json` (only the two
   `record_*_results.py` scripts write them). Code changes should only
   ever touch `index.html`, `football/index.html`, `history/`, `features/`, `scripts/`,
   `.github/workflows/*.yml`, `discord-bot/`, `tests/`, `README.md`, `CNAME`.
   `tests/test_live_data_schema.py` is a deliberate, narrow exception: it
   **reads** whatever is currently committed under `data/` to check it against
   the schemas above (nothing else does -- every other test uses a fixture or
   a fake fetcher). It never writes. Don't read that as license to touch
   `data/` from any other test or script; this file is the one exception, on
   purpose, and stays read-only.

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
  Also covers two things nothing else touched: `main()`'s unread-bet-line
  safety net end to end (a bet-like line nothing understands must surface as
  a stderr `WARNING` AND a `tickets.json` `note`, and the slate must still
  post rather than exit 1 — the actual fix for the real incident where a
  card "successfully" parsed 16 of 18 tickets and said nothing), and
  `resolve_player()`'s three branches directly (exact match, a fuzzy typo via
  `difflib` at the 0.82 cutoff, and the give-up fallback that must keep the
  name as typed with a BLANK team rather than guess).
- `tests/test_page.py` — Playwright (Python) headless Chromium, serving
  `index.html` through one `page.route("**/*")` handler with in-memory
  fixtures (tickets files, MLB schedule, live feeds) and a pinned clock.
  Covers the past-midnight slate, the all-Final rollover, a new upload
  landing, the 6am backstop, and filter regressions -- including the bettor
  filter end to end (section P): clicking a Bettor Tracker row, singles and
  whole parlays dropping out, only the matching leg surviving inside a
  MIXED-bettor parlay (with the "N other leg(s) hidden" note), tapping the
  row again to clear, and a tab switch resetting it. That path was
  unverified until 2026-09-20 -- the rows had only ever been read, never
  clicked, so `legPassesFilters()` was never exercised with a filter set.
  Section Q opens a browser pinned to `America/Los_Angeles` and asserts the
  live sync line still reads ET (see the sync-line note below); it is the
  only test that sets a non-default timezone. Also covers the
  `#dynamic-note` banner (section O): every fixture sets a `note` on
  `tickets.json` but nothing asserted it actually reaches the screen until
  now -- a real parser warning renders verbatim, an empty note falls back to
  "against live MLB results.", and the waiting-for-picks state leaves it
  blank rather than stale. **Section R** covers everything added on
  2026-09-20: both bet lists defaulting to collapsed with their own
  OPEN/HIT/MISSED pill and surviving a re-render, the bench-player-is-N/A
  grading fix (its fixture's `feed(..., bench=[...])` puts a man in the
  boxscore with zero plate appearances), the always-on status line in each of
  its states, the LEGS chip counts, and the leg filter hiding legs inside a
  mixed card / dropping a card with none / clearing on a tab switch. Every one
  of those was mutation-tested in a scratch copy before being trusted --
  including the one that mattered: reverting the grading gate makes R4 fail.
  One mutation taught something on its own -- flipping `CARDS_OPEN`'s
  initialiser changes nothing, because `loadCardsOpen()` overwrites it at boot.
  The real default is that function's `=== "1"`.
  **Section S** covers the multi-select filters: two chips in one group both
  staying on, groups AND-ing together, the summary bar appearing and naming
  every active filter, one chip removing only its own value, Clear all, and
  the per-ticket Irons expansion. **Section T** pins the header order, the
  sport in the `<h1>`, and the colour key sitting above the footer. Mutation
  testing paid for itself twice here: the first draft of S6 removed a filter
  from a group holding ONE value, so "delete this key" and "clear this whole
  group" were indistinguishable and a real bug walked past it; and S3 checked
  the summary's chips without checking the bar ever became visible. Both were
  found by deliberately breaking the code, not by reading it.

- `tests/test_live_at_bats.py` — the Live At Bats panel: one mocked game walked
  forward poll by poll (live count, strikeout, home run/bomb, stale at-bat,
  tab switch), with the pinned clock moved by hand instead of sleeping.
- `tests/test_steals.py` — stolen base bets end to end on the page: grading
  (appeared / void / pulled), mixed HR+steal tickets, the steal alert plain and
  cashed, on-base / blocked / stole / caught / stranded tiles, no bomb for a
  steal-only pick's homer, minus-money odds.
- `tests/test_history.py` — Playwright against `history/index.html` with a
  hand-worked `history.json` fixture: the isolation guarantee (block A),
  every stat, sorting/filters, chart tooltips, degraded data, phone width.
- `tests/test_history_import.py` — the importer, fully offline: inline CSV
  "tabs", a fake MLB fetcher, temp-dir output, the shrink guard.
- `tests/test_feed_fields.py` — the one test that DOES hit the network, since
  mocked fixtures can't prove a `fields=` allow-list is complete. Full vs slim
  feed for every live game, through the page's own `getGameSnapshot()`.
- `tests/test_football.py` — the football page: one mocked Sunday+Monday slate
  walked poll by poll (red zone, touchdown + cash alert, "TD -- not him", punt,
  the two Josh Allens, id-vs-name matching, inactive -> void, multi-day
  rollover, background-game cadence) plus the two-way isolation block. The
  bettor filter is covered at H8-H14, the twin of `test_page.py`'s section P
  and added at the same time for the same reason; it filters on Joe, who owns
  both a single and one leg of a mixed-bettor card. Section K is the twin of
  that file's section Q: its own browser on Pacific, checking the sync line
  still reads ET. **Section L** is the twin of that file's section R (chips,
  pills, status lines, collapsible bet lists); it rebuilds `TICKETS` from
  scratch first, because section J deliberately trims that global down and
  anything appended after it would otherwise assert against a two-bet slate.
  The Live Drives state changes live in C2/C6 (no ON DEFENSE tile survives)
  and D6 (possession flipped but the drive isn't open -> TAKING THE FIELD
  SOON, grey, not green) rather than in L. **Section M** is baseball's section
  S (multi-select + the summary bar) and **section N** is its section T (the
  header reshuffle, the sport in the title, the colour key above the footer).
- `tests/test_football_parser.py` — football parser (both templates, negative
  odds, suffixes), schedule-based slate dating against a fake ESPN, the NFL
  roster builder, and the Discord bot's file-name routing. Fully offline.
  Also checks `resolve_player()`'s fuzzy-match branch directly (a real typo,
  not a suffix that normalizes away) — every fixture card either matches
  exactly or hits the give-up fallback (already covered), so the actual
  `difflib` path was otherwise never exercised on its own.
- `tests/test_record_results.py` — the baseball recorder against a fake MLB
  (own HR, PHP credit, benched -> void, void-leg re-pricing, refunds, the
  not-yet-final / backstop / already-recorded / corrected-picks cases) and the
  merge into `history.json` (mis-dated sheet copy, nickname mapping, dead sheet).
- `tests/test_football_history.py` — the football recorder against a fake ESPN,
  the history file it builds, and `football/history/index.html` in Chromium
  (empty state, isolation block, phone width). Fully offline.
- `tests/test_build_roster.py` — `scripts/build_roster.py`, fully offline: a
  fake fetcher standing in for the MLB API, checking suffixes and accents
  survive and a missing-abbreviation team or a name collision doesn't crash.
- `tests/test_discord_bot.py` — `discord-bot/bot.py`'s confirm/discard/timeout
  reaction flow and its GitHub Contents API push, plus `on_message`'s own
  gating (other bots, wrong channel, an allow-list, non-`.txt` / unroutable /
  empty attachments). Fully offline: a real (unconnected) `discord.Client` is
  built so `client.dispatch("reaction_add", ...)` can deliver fake reactions
  the same way discord.py's own gateway code would, and `requests.get`/`put`
  are monkeypatched so nothing reaches GitHub. `route_for()` itself (file name
  -> sport) is covered in `test_football_parser.py`, not repeated here.
- `tests/test_at_bat_math.py` — direct checks on `negBinomPmf()` /
  `atBatEstimate()` / `remainingOutsForSide()`, the negative-binomial model
  behind "~2.1 AB left". Every other test only confirms SOME number renders
  inside a mocked slate; this one cross-checks the actual values against an
  independently-written Python reference (`math.comb`, not a port of the JS's
  iterative loop) across a grid of realistic (batting distance, outs left,
  league out rate) combinations, plus hand-verified edge cases (`r<=0`, the
  `d=0` guaranteed-PA shortcut, the geometric-distribution identity at `r=1`,
  monotonicity as the out rate changes) and every branch of
  `remainingOutsForSide` (Top/Middle/Bottom/End, both sides, last inning).
- `tests/test_live_data_schema.py` — **the one test that reads the real,
  currently-committed files under `data/`** (read-only; never writes) instead
  of a fixture or a fake fetcher, so it's the only thing that would catch a
  bad manual edit, a parser bug that exits 0 but writes a malformed field, or
  drift between what this file documents and what's actually live. Checks
  `tickets.json` / `tickets-previous.json` (both sports; absence is a normal,
  skipped state), `roster.json` (both sports; must always exist, and every
  per-player map must share the same key set), `history.json` (both sports),
  and whatever's on hand under `data/results/` / `data/football/results/`.
  Verified against real corruption (a missing leg field, an unsigned odds
  string, a bogus leg status, a roster map with a key removed) in a scratch
  copy before trusting it -- it caught all four.
- `tests/test_site_links.py` — walks every internal `href` across all six
  HTML pages and checks it resolves to a real file. Every other test only
  asserts its own page's own footer link works; this is the only one that
  checks the WHOLE link graph. The resolver mirrors GitHub Pages' actual
  behaviour (a bare extensionless path tries a same-named sibling FILE.html
  *before* `<name>/index.html` -- the exact trap `features.html`'s redirect
  stub exists for), and that resolver is itself checked against the
  documented gotcha before being trusted to check anything else. It only
  checks that the stub file EXISTS where the resolver expects it -- the
  stub's own actual redirect behaviour is `test_redirect_stub.py`, below.
- `tests/test_redirect_stub.py` — Playwright against `features.html` itself:
  the `<meta refresh>` fallback targets exactly `/features/` (a bare
  `/features` would just reload the stub forever), and -- the part nothing
  else checked -- the script-driven `location.replace()` that actually fires
  in a real browser preserves the visitor's query string AND hash (so a
  bookmarked `/features?sport=football` lands back on the football tab
  instead of resetting to baseball). Verified against a scratch copy with
  the search/hash preservation removed -- it caught it.
- `tests/test_features_page.py` — `features/index.html`'s toggle, URL sync,
  deep-linking, and isolation (it must never call MLB/ESPN or read the
  tickets/history files). Content is deliberately NOT checked, since that's
  supposed to keep changing -- only that the mechanics under it can't quietly
  break while the copy keeps getting edited. Added once the page became
  auto-maintained rather than static; skipped before that for the opposite
  reason.
- `tests/test_workflow_yaml.py` — the three `.github/workflows/*.yml` files,
  read with targeted regexes rather than a YAML parser (no dependency this
  project doesn't otherwise need; none of the three is complex enough to
  require one). Checks every `python scripts/X.py` line names a real script,
  every trigger path matches what that script is actually told to parse, and
  -- the one that matters most -- **that no workflow commits a file outside
  its documented lane**: `parse-picks.yml` may only ever touch baseball's own
  tickets files, `parse-football-picks.yml` only football's, and
  `import-history.yml` (the daily archive job) must never touch either
  sport's live tickets file at all (CLAUDE.md gotcha 5, now enforced instead
  of just written down). Verified against a scratch copy with a fake
  `git add data/tickets.json` slipped into the archive job's commit step --
  caught it. Also checks both parsers' commit steps notify Discord ONLY when
  `committed == 'yes'` -- see "Discord status automation" above.
- `tests/test_notify_discord.py` — `scripts/notify_discord.py`, fully
  offline: a fake fetcher standing in for the HTTP call, so nothing ever
  reaches Discord. Checks the message text for both sports (singular vs.
  plural, a date range vs. a single date), the exact request shape `post`/
  `edit` send (POST with `?wait=true` so Discord returns an id to edit
  later; PATCH to `/messages/<id>`), the CLI wiring for all three
  subcommands, and -- the one that matters most -- that the webhook URL
  itself never reaches a log line or error message even when the request
  fails or the secret is missing.
- `tests/test_auto_fix_parser.py` — `scripts/auto_fix_parser.py`, the script
  behind the auto-fix-on-failure workflow (see "Discord status automation"
  above). Fully offline: a fake fetcher stands in for the Claude API call,
  and "did the full suite pass" / "does the upload parse now" are
  monkeypatched rather than actually re-run. The thing this file cares
  about most: every failure path (a malformed model response, the suite
  failing after the patch, the suite passing but the specific upload still
  not parsing, the API call itself raising) leaves the parser file
  byte-for-byte back at its ORIGINAL content, and only the one path where
  every gate passes leaves the patch in place -- checked directly by
  reading the file back after each scenario, not just trusting the return
  value. Also covers the retry-with-feedback loop (attempt 2 receives
  attempt 1's exact failure detail) and that a missing `ANTHROPIC_API_KEY`
  short-circuits cleanly instead of crashing the job.

```
pip install -r tests/requirements.txt
python -m playwright install chromium
python tests/test_parser.py && python tests/test_page.py && python tests/test_live_at_bats.py && python tests/test_steals.py
python tests/test_history_import.py && python tests/test_history.py && python tests/test_build_roster.py
python tests/test_football_parser.py && python tests/test_football.py
python tests/test_record_results.py && python tests/test_football_history.py
python tests/test_discord_bot.py && python tests/test_at_bat_math.py && python tests/test_live_data_schema.py
python tests/test_site_links.py && python tests/test_features_page.py && python tests/test_workflow_yaml.py && python tests/test_redirect_stub.py
python tests/test_notify_discord.py && python tests/test_auto_fix_parser.py
python tests/test_feed_fields.py   # needs network; run after editing FEED_FIELDS
```

Windows note: `venv` fails on very long paths and Windows Python has no tz
database (`tzdata` is in the requirements for that reason). Keep using
this pattern for any nontrivial change rather than shipping unverified.

## Discord status automation

**`scripts/notify_discord.py`** posts (or edits) a status message in the
group's Discord intake channel via an incoming webhook (`DISCORD_STATUS_WEBHOOK`,
a GitHub Actions secret -- both sports share one webhook, one channel).
Three subcommands: `success` (summarizes a freshly-written `tickets.json`
and posts it), `post` (posts fresh text, prints the message id), `edit`
(PATCHes a message this webhook posted earlier, by id). It never logs,
prints, or lets an HTTP error surface the webhook URL itself -- `tests/test_notify_discord.py`
checks that specifically, fully offline (a fake fetcher, never a real request).

**The success path.** `parse-picks.yml` and `parse-football-picks.yml`'s
commit step sets a `committed` output (`yes` only on a genuine new slate,
never a no-op rerun), and a step gated on `committed == 'yes'` calls
`notify_discord.py success` right after -- one plain "✅ live on bmbs.bet"
message, no AI involved, nothing to investigate. Deterministic and safe by
construction.

**The failure path: `.github/workflows/auto-fix-parse-failure.yml`.**
First attempt (2026-09-20) was to fire an unattended Claude Code AGENT off
a `workflow_run` failure event (`RemoteTrigger`, `action: "create"`) --
refused outright by the platform's own auto-mode safety classifier
("Reason: \[Create Unsafe Agents\]"), which is the platform declining to
let an agent spin up another agent with unattended production push access.
Not a bug to route around.

**What's actually built instead is narrower and bounded on purpose: ONE
plain Claude API call per attempt, not an agent** -- no tool use, no
open-ended agency, just a request/response that a plain script (`scripts/auto_fix_parser.py`)
decides whether to trust:

1. Fires on either parse workflow's `workflow_run` `completed` event with
   `conclusion == 'failure'` (also has a `workflow_dispatch` escape hatch,
   sport picked from a dropdown, for a manual re-run).
2. Posts one Discord message ("investigating") and keeps its id.
3. Sends the failing parser's complete source + the raw failing upload to
   the Claude API (`ANTHROPIC_API_KEY`, a GitHub Actions secret), with a
   system prompt that spells out the hard-won lessons from the four real
   template-drift incidents fixed by hand the same day -- most importantly
   the `PARLAY_HEADER_RE`-collision trap that bit BOTH sports' parsers
   independently (a new header containing a literal "N-Leg Parlay"
   substring gets misread as a brand-new section unless explicitly
   excluded). Asks for the complete new file back in a fixed
   `<<<SUMMARY>>>`/`<<<FILE>>>`/`<<<END>>>` envelope -- a malformed response
   is treated as a failed attempt, never guessed at.
4. Writes the proposed file, then runs the FULL 19-file offline test suite.
   **Only if every test still passes** does it re-run the parser against
   the actual failing upload to confirm the ORIGINAL problem is actually
   fixed (this run also writes the real `tickets.json` -- no special-cased
   second code path for that). Any failure at either gate reverts the
   parser file to its exact original content and retries once with the
   failure detail fed back to the model; two failed attempts and it gives
   up rather than guess a third time.
5. On success: leaves the patched parser, the real `tickets.json`, and an
   auto-archived copy of the raw upload (`tests/fixtures/auto_detected_<sport>_<timestamp>.txt`,
   for a human to later turn into a permanent regression test) sitting
   **uncommitted** in the working tree. `auto_fix_parser.py` never runs
   `git` itself -- a separate, plain bash step (the same git-push-with-retry
   pattern every other workflow here already uses) does the actual commit
   and push, so the highest-risk decision logic can't also invent a novel
   way to corrupt the commit history.
6. Edits the original Discord message to its final state: fixed and live,
   found-a-fix-but-couldn't-push-it (checked separately -- `resolved: yes`
   is NOT enough on its own to claim success; the commit step's own outcome
   is checked too), or couldn't-resolve-it-automatically.

**Security note, since two different untrusted inputs flow through this
pipeline:** the raw Discord upload (a friend's paste) and the model's own
response (which read that upload) are both treated as untrusted text.
Neither is ever spliced directly into a shell command via GitHub Actions'
`${{ }}` templating -- that substitution happens BEFORE the shell parses
the script, so a value containing shell metacharacters could otherwise
alter what the script does. Every such value goes through `env:` + a
quoted shell variable instead (safe, because by the time the shell expands
`"$VAR"` the command has already been parsed).

**`tests/test_auto_fix_parser.py`** is the important test file here: fully
offline (a fake fetcher stands in for the Claude API; "did the suite pass"
and "did the fix work" are monkeypatched), and specifically proves the
revert-on-any-failure guarantee -- a malformed response, a suite failure,
and "the suite passed but the specific upload still doesn't parse" each
leave the parser file byte-for-byte unchanged, and only the one path where
BOTH gates pass leaves the patch in place. `tests/test_workflow_yaml.py`
section E separately checks the workflow YAML itself: the two workflow
names it listens for are cross-checked against the real workflows' own
`name:` fields (a rename of either would otherwise silently break the
trigger), and the sport-routing step's football/baseball branches are
checked directly so a swapped branch can't commit the wrong sport's files.

**Needs `ANTHROPIC_API_KEY` (a GitHub Actions secret) to actually run.**
Without it, `auto_fix_parser.py` short-circuits to `resolved=no` on the
first line -- a parse failure still gets the "investigating" Discord
message, then immediately the "couldn't resolve automatically" edit,
rather than silently hanging or crashing the job.

**Verified end to end against the real API on 2026-09-20, once the key was
set** -- not just offline-mocked: a synthetic new template the real parser
genuinely can't handle (`>> WAGER A (2 legs)` / `Stake: $6 | To Win: $210.00
| Placed by: Kenny` / numbered legs), run through the real `attempt_fix()`
against scratch copies only, real Claude call, real 19-file suite, real
`verify_fix`. Two real bugs surfaced this way that offline mocking could
never have caught, both fixed and covered by `tests/test_auto_fix_parser.py`
section Z / A6-A8 before being trusted:
- `temperature` (any value) is a hard HTTP 400 for `claude-sonnet-5`. Removed.
- `thinking` left at its default silently consumed the ENTIRE token budget
  on internal reasoning and returned zero text (`stop_reason: max_tokens`);
  raising `max_tokens` from 8192 to 32000 did NOT fix it (25914 of it went
  to thinking, the file still got cut off mid-write). Explicitly disabling
  thinking (`"thinking": {"type": "disabled"}`) fixed it in one try.
- (Found in the same run, not a request-shape bug:) the model wrapped the
  file section in a markdown fence on one real call despite the system
  prompt explicitly saying not to -- inconsistent, not every call. A
  ```` ``` ```` -fenced .py file is an instant `SyntaxError`, which
  `verify_fix` correctly caught and reverted, but wasted the attempt.
  `extract_file_and_summary()` now strips a matching leading+trailing fence
  defensively rather than trusting the instruction alone to hold.

With both fixed, the full pipeline resolved the synthetic failure in ~150s:
real API call, real suite, real re-parse, correct ticket/leg counts.

## Deploy process

No build step. Edit `index.html` (or `scripts/*.py`) directly in the repo,
commit, push. GitHub Pages picks it up automatically within a minute or so.
The daily picks-update flow (separate from code deploys) is: paste text
into `data/incoming_picks.txt` via GitHub's web editor, or upload a `.txt`
in the Discord intake channel (see `discord-bot/README.md`) → commit →
GitHub Actions runs `parse_picks.py` → `tickets.json` updates (and the
prior slate is archived if the date changed) → live site reflects it on
the next poll cycle (~10s).
