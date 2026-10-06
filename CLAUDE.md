# bmbs.bet — BMBS Tracker

A live-tracking site for a friend group's parlay/prop pool. Static site on
GitHub Pages, custom domain `bmbs.bet` (Namecheap DNS, `CNAME` file). No
backend, no build step, no framework: every page is one self-contained HTML
file with inline CSS and JS, and every visitor's browser grades the bets itself
from public sports APIs.

## 1. READ FIRST: what lives where

Since 2026-10-05 the **All Sports tracker is the front page, and every
single-sport tracker is hidden** (the user's call).

| URL | File | What |
|---|---|---|
| `/` | `index.html` | **All Sports** front page, titled plain "BMBS Tracker". Data: `data/combined/`. |
| `/mlb/` | `mlb/index.html` | MLB tracker, HIDDEN. Data: `data/`. |
| `/football/` | `football/index.html` | NFL (anytime-TD) tracker, HIDDEN. Data: `data/football/`. |
| `/hockey/` | `hockey/index.html` | NHL tracker, HIDDEN. Data: `data/hockey/`. |
| `/basketball/` | `basketball/index.html` | NBA tracker, HIDDEN. Data: `data/basketball/`. |
| `/wnba/` | `wnba/index.html` | WNBA tracker, HIDDEN. Data: `data/wnba/`. |
| `/cfb/` | `cfb/index.html` | College-football tracker, HIDDEN. Data: `data/cfb/`. |
| `/history/` | `history/index.html` | MLB History page (back link goes to `/`). |
| `/football/history/` | `football/history/index.html` | NFL History page. |
| `/features/` | `features/index.html` | "What this site can do", for the friend group. |
| `/all/` | `all/index.html` | Redirect stub to `/` (query + hash kept). The All Sports page lived here until 2026-10-05. |
| `/features` | `features.html` | Redirect stub to `/features/` (query + hash kept). |

Every tracker page fetches its data as `../data/<dir>/...`, which resolves the
same from `/` and from `/<sport>/`. The page tests kept their old serving URLs
(e.g. the front page is served at `/all/` in tests) for that reason.

### Labels vs addresses

On screen the sports are **MLB, NFL, ALL SPORTS, NHL, NBA, WNBA, College
Football** (MLB/NFL renamed from Baseball/Football 2026-10-04; ALL SPORTS from
"NFL+MLB" 2026-10-05). Those are LABELS. Addresses were deliberately left
alone: the `/football/` and `data/football/` paths, `data/combined/`,
`?sport=baseball|football|combined`, the `baseball*` / `football*` / `sports*`
upload prefixes, `--sport` CLI values, and every element id and CSS class.
Don't rename an address to match a label.

### What "hidden" means

- **Hidden = linked from no visible page.** The front page's sport switch is
  `display:none` with only its own tab left. Each hidden page's switch shows
  itself and ALL SPORTS (`/`); hidden pages don't link each other. They are
  not secret: Discord's status message for a `hockey_*.txt` etc. upload links
  the hidden page directly.
- **Three front-page panels are hidden, not removed:** the LEGS chip row, the
  Scoring Log and the Bettor Tracker carry `class="hidden-panel"`
  (`display:none !important`). All the code behind them still runs; unhiding
  one = deleting that class. `test_combined_page.py` F5/F6 pin both halves.
- `/features/` opens on All Sports with its switch hidden; `?sport=baseball` /
  `?sport=football` still open the MLB and NFL sections (the MLB and NFL
  pages' footers use them). The All Sports section lists no hidden panel and
  links no hidden tracker.
- Pinned by: `test_combined_page.py` F1/F2 (front page switch hidden, links no
  tracker), `test_site_links.py` C1-C4, and each league test's "no other page
  links it" check (`test_hockey.py` A3, `test_basketball.py` A3, `test_cfb.py`
  A2, which scan every HTML page).
- **To unhide a tracker:** add its tab to the front page's switch and drop the
  switch's `display:none`; remove the corresponding hidden checks above; add a
  section to `features/index.html`.

### Front-page wording

`<h1>` is plain "BMBS Tracker" on the front page and "BMBS Tracker &mdash;
MLB / NFL / NHL / NBA / WNBA / College Football" on the hidden pages. Eyebrows:
front page "TODAY'S SLATE · UPDATED LIVE"; MLB "TODAY'S SLATE · LIVE FROM
MLB"; NFL "THIS WEEK'S SLATE · LIVE FROM ESPN"; the other hidden pages "TODAY'S
SLATE · LIVE FROM ESPN". The front page's Live Bet Tracker sub-line names no
sport ("Your picks in live games...", "N in action, M up next").

## 2. Repo layout

**Pages** (each independent; a fix that applies to several is made in each,
on purpose — there is no shared script and no build step):
`index.html`, `mlb/index.html`, `football/index.html`, `hockey/index.html`,
`basketball/index.html`, `wnba/index.html`, `cfb/index.html`,
`history/index.html`, `football/history/index.html`, `features/index.html`,
plus the two redirect stubs `all/index.html` and `features.html` (don't put
real content back in either).

**Data** (`data/`, per sport: `data/` = MLB, `data/football/`,
`data/combined/`, `data/hockey/`, `data/basketball/`, `data/wnba/`,
`data/cfb/`):
- **LIVE DATA** — `tickets.json` (current slate), `tickets-previous.json`
  (prior slate, archived when a slate for a different date/week is parsed),
  `incoming_picks.txt` (the paste/upload target). Written only by that sport's
  parse workflow. Never hand-edited, never written by tests, never in a code
  deploy. **Either tickets file may be absent — that's a normal state**
  ("Waiting for today's picks"); only a 404 counts as absent, any other fetch
  failure raises the error banner.
- **PIPELINE DATA** — `data/history.json`, `data/results/<date>.json`,
  `data/football/history.json`, `data/football/results/<weekEnds>.json`.
  Written only by `import_history.py` / `record_*_results.py`.
- **Rosters** — `roster.json` in `data/`, `data/football/`, `data/hockey/`,
  `data/basketball/`, `data/wnba/`, `data/cfb/` (the combined slate has none;
  it resolves against the others). Ordinary to regenerate (§11).

**Scripts** (`scripts/`): `parse_picks.py` (MLB), `parse_football_picks.py`,
`parse_combined_picks.py` (All Sports; a layer over `parse_picks`),
`parse_hockey_picks.py` / `parse_basketball_picks.py` / `parse_wnba_picks.py` /
`parse_cfb_picks.py` (thin wrappers over the combined parser),
`build_roster.py` / `build_football_roster.py` / `build_hockey_roster.py` /
`build_basketball_roster.py` (`--league nba|wnba`) / `build_cfb_roster.py`,
`record_results.py`, `record_football_results.py`, `import_history.py` +
`history_player_map.json`, `notify_discord.py`, `auto_fix_parser.py`.

**Workflows** (`.github/workflows/`): `parse-picks.yml`,
`parse-football-picks.yml`, `parse-combined-picks.yml`,
`parse-hockey-picks.yml`, `parse-basketball-picks.yml`,
`parse-wnba-picks.yml`, `parse-cfb-picks.yml` (each triggers on its own
`incoming_picks.txt` and commits only its own `tickets*`),
`auto-fix-parse-failure.yml`, `import-history.yml` (daily archive).

**`discord-bot/`**: `bot.py`, `run_bot.bat`, `requirements.txt`,
`.env.example`, `README.md` (§12). **`tests/`**: plain-script tests plus
`tests/fixtures/` (every card template, including `test_picks.txt`) and
`tests/requirements.txt` (§17).

## 3. Shared page engine

### Polling from the browser

Every tracker polls public APIs **directly from the visitor's browser** — the
MLB Stats API (`statsapi.mlb.com`, keyless, open CORS) and ESPN (§10). There is
no server-side cron and no backend. Every viewer independently computes
hit/miss/live state.

**MLB poll cadence is 10s and that's the floor — don't lower it.** Measured
2026-09-18: the feed carries `metaData.wait: 10`, responses ship
`Cache-Control: max-age=10`, and a game's feed only regenerates every ~18-20s.
Polling faster re-downloads cached bytes. ESPN pages poll every 15s.

**Polls are non-overlapping** (`pollOnce()`): `setInterval` doesn't wait, and
two polls finishing out of order write a stale slate over a fresh one. A tick
landing mid-poll is dropped; the guard releases in a `finally` or one thrown
error kills polling for the session.

### Slate dates, not calendar dates

Polling is keyed on `tickets.json`'s `date`, never the clock: MLB files a 10pm
ET game under the date it started, so a slate tracks straight through midnight.
A slate rolls from Today to Yesterday only when every game on its date is Final
per the schedule (postponed games are encoded Final), or — backstop for
suspended games only — at 6am ET the next morning. **Never reintroduce any
other wall-clock rollover**; a clock-based one was built and reverted at the
user's request.

**Today is the oldest slate that isn't over yet.** Picks often land just after
midnight while last night's late game is still live; that upload archives the
live slate into `tickets-previous.json`. The newer slate is held as
`SLATES.queued` (a note on the Today tab says so) and takes over when the live
one goes final. The page re-reads both tickets files every poll, so a new
upload appears without a reload. Final games' feeds are cached and never
re-fetched; pre-game feeds aren't fetched at all.

NFL slates are a week, not a day (§10); the All Sports page's rollover is "every
engine with legs on the card is done" (§9).

### FEED_FIELDS

The full MLB live feed is ~104KB gzipped per game; `getGameSnapshot()` sends a
`fields=` allow-list (`FEED_FIELDS`) that cuts it to ~15KB, which is what makes
10s polling affordable.
- `fields` matches field NAMES at any depth, not paths, and a missing name
  silently yields `undefined`. Every name read out of the feed must be listed,
  including intermediate ones.
- **There are six copies** (every page carrying the MLB engine: `index.html`,
  `mlb/index.html`, `hockey/`, `basketball/`, `wnba/`, `cfb/`).
  `test_smoke_pages.py` F2 requires them identical; `test_feed_fields.py`
  (network) proves the MLB page's copy complete by comparing
  `getGameSnapshot()` on the full vs slim feed for every live game, pinned
  with `timecode=`. A one-off single-game `recentABs` diff that doesn't
  reproduce is a mid-at-bat timing race, not a lost field — re-run first.
  Run it after touching the list.
- **Don't put a double-quoted phrase in a `FEED_FIELDS` comment.** The test
  scrapes quoted strings out of the array's source (it strips `//` comments
  first, but reads the array as text); a quoted comment was once spliced into
  the request URL.

### Times are ET, always with an explicit `timeZone`

`toLocaleTimeString` without `timeZone` formats the VISITOR's clock. The sync
line printed a Pacific viewer's wall clock under an "ET" label until
2026-09-20 — invisible on an Eastern machine. Copy `nowET()`, which passes
`timeZone: "America/New_York"`. Pinned from a Pacific browser in
`test_page.py` Q, `test_football.py` K and `test_bell.py` G1.

### Warmup and delays (MLB)

- **Warmup is not live.** MLB's own `/api/v1/gameStatus` lists Warmup as
  `abstractGameState: "Live"`, `codedGameState: "P"`. Trusting `abstract`
  tagged a leadoff hitter "AT THE PLATE NOW" before a pitch. **`codedGameState
  === "P"` is the gate**, applied in BOTH `getScheduleForDate()` (feed not
  fetched) and `getGameSnapshot()` (a fetched feed can't be misread); they can
  disagree for a poll around first pitch (`test_page.py` U5).
- **A delay is named.** The schedule request carries `hydrate=team` (~600
  bytes) for team abbreviations, because a pre-game leg can only find its game
  via `leg.team` (`RESULTS.teamState`). `scheduleStateText()` renders "Game
  delayed -- rain." etc., reading the reason from `detailedState` ("Delayed
  Start: Rain") or the separate `reason` field. Both families are covered:
  `Delayed Start*` (never began) and `Delayed*` (began then stopped).

## 4. Legs, bets, scoreboard, filters, layout

### Leg states

`hit`, `miss`, `na` (didn't play / push → void), `live`, `not_started`, and
`untracked` (a market the page can't grade, §5). A home-run leg is `hit` the
instant the HR appears in the play-by-play, before the game ends.

**Benched = void, not miss** (the user's call, 2026-09-20). MLB's boxscore
lists the whole active roster, so presence there says nothing. The MLB engine
builds a `played` set from `stats.batting.plateAppearances` (falling back to
`battingOrder` / `allPositions` / any batting object); `pollSlate()` writes a
name into `rosterStatus` only while his game is on OR he's in `played`, and a
benched player settles `na` as soon as **his own** game is final
(`inBox.get(norm) === "final"`). `inBox` is a separate map of everyone on a
roster; `stateForSteal()` reads THAT, because a steal bet asks "was he on the
field", not "did he bat". `record_results.py` has the same rule.

**Pinch Hit Protection** (MLB home-run legs). Most of the group's books credit
the bet if whoever later holds the pulled player's batting-order slot homers.
So a pulled player is NOT resolved `miss` immediately (that was tried and
reverted); `pinchHitProtection()` / `stateForPlayer()` follow the whole chain of
substitutes in that slot (`slotHolders`). A substitute's homer resolves the leg
`hit`, counted normally everywhere but drawn with the `php-hit` badge (green +
yellow diagonal stripes) and a note. A player who homered himself before being
pulled stays a plain hit. Steals get no PHP (§5).

### Bet outcomes and the scoreboard

**Two rows of four, aligned: BETS** (Open / Hit / Missed / N/A) **and LEGS**
(Live / Hit / Missed / N/A), same columns, same colours (white, green, red,
yellow). Keep them aligned — that's why Irons and Not-Fully-Tracked are slim
bars, not chips.
- `not_started` folds into LIVE in the leg row (`legFilterBucket()`), and
  `untracked` too; the status line under each leg says which it really is.
- **A void bet is N/A, not Open**: every leg was N/A, so it's refunded.
  `singleOutcome()` returns `"void"` for a DNP single. Its card reads
  "VOID — nobody on it played, refunded" (`test_page.py` V4).
  `isOpenOutcome()` still counts void as open for payouts, `betIsOpen` and
  Live Bet Tracker eligibility — "can this still be graded" is a different
  question from "which column".
- **Don't bring back BATTING NOW / BATTING SOON chips** (removed 2026-09-18;
  the Live Bet Tracker replaced them).
- On the front page the LEGS row is hidden (§1), still computed.
- **Payout:** a parlay with void legs is re-priced from the remaining legs'
  odds (`adjustedPayout` in `evaluateTicket()`); the running payout estimate,
  cash alerts and the Bettor Tracker all use it. A priceless leg shows an
  em-dash and odds-based numbers skip it (`oddsToNumber()` returns null) —
  never a fabricated price.
- **Bettor Tracker** (collapsed panel, `N BETTORS` pill): each bettor's picks
  and record; tapping a row toggles that bettor in `BETTOR_FILTERS`.

### Irons

An **Iron** is an open bet **one leg from cashing**, marked with 🧇 next to the
leg still needed.
- Parlays: `outcome === "live" && activeCount - hitCount === 1`
  (`evalRes.iron`); the waffle goes on that leg (`isIronLeg()`). A dead parlay
  is never an Iron.
- Singles: every open single is an Iron (`singleIsIron()`) except state `na`
  (void). A fresh slate therefore shows a waffle on every single; intended.
- The waffle is driven by Iron state, never by the active filter.
- **The Irons filter renders the FULL parlay**, decided per ticket
  (`BET_FILTERS.has("irons") && evalRes.iron`) — Open + Irons still hides
  non-matching legs on plain open cards. It does not override whether a card
  appears at all.
- Irons has its own slim amber bar under the BETS row (`.irons-bar`,
  `#chip-iron`), still a member of `FILTER_GROUPS.bet`. Amber is reserved for
  it.
- **The waffle entity is easy to get wrong:** `&#129479;` is U+1F9C7 🧇;
  `&#129415;` is a bat 🦇 (shipped for ten minutes). `test_page.py` V2 pins
  the codepoint against `ironMark()`'s.

### Filters (all trackers)

- **Multi-select Sets**: `BET_FILTERS`, `LEG_FILTERS`, `BETTOR_FILTERS`. OR
  within a group, AND across groups; an empty set means "no filter". Clicking a
  chip toggles only that chip.
- `FILTER_GROUPS` is the single table the chips, the summary bar and
  `clearAllFilters()` read. A new group = a new row there.
- Leg-level filters hide non-matching legs inside a card (with an "N other
  leg(s) hidden" note); a card with no surviving leg drops out. Both go
  through `legPassesFilters(state, who)`.
- **One sticky summary bar** (`#filter-summary`) lists every active filter as
  a removable chip plus Clear all. It lives OUTSIDE `<header>` because sticky
  is confined to its containing block.
- **Summary chips and Bettor Tracker rows use `data-group`/`data-key` with one
  delegated listener each** (`initFilterHandlers()`), never inline `onclick`:
  a bettor name with an apostrophe broke `onclick="f('x')"`.
- All filters reset on a tab switch, and **every filter change must fan out
  through `applyFilters()`**, which re-renders the Live Bet Tracker (forgetting
  that left the panel stale once).

### Page layout

- Header order: `<h1>`, eyebrow, sync line ("last updated ... ET"),
  `#dynamic-note` (`test_page.py` T1 pins the first three children). The bell
  is the header's LAST child, absolutely positioned.
- **`#dynamic-note` must stay.** It renders "Heads up — <note>" when the
  parser left a warning (unread bet lines, ungradeable legs) and is EMPTY
  otherwise (`test_page.py` O1-O3). It is the only place an unread bet line
  surfaces. The note is inserted with `innerHTML` (it legitimately carries
  entities) and quotes friends' card text — a known trust boundary, not a bug
  to fix.
- The colour key sits at the bottom under "COLOR KEY", below the horizontal
  rule (owned by `.colorkey`; the footer has no border). `#legend-hit` /
  `#legend-miss` are rewritten for steal slates.
- Footers: MLB links `/history/` and `/features/?sport=baseball`; NFL links
  `/football/history/` and `/features/?sport=football`; the front page links
  `/features/?sport=combined`; NHL/NBA/WNBA/CFB link nothing.

### Status lines, collapsible lists, Expand all

- **Every pick carries a status line, always** (`playerStatusLine()`): "Game
  hasn't started yet.", "Game on -- not in the lineup yet.", "On the roster but
  never got in the game -- void / refunded.", "Game final -- no home run." etc.
  A hit returns "" (the green check says it). An empty line is
  indistinguishable from the page not knowing.
- **Every leg row names its market** (`marketTag()`): HOME RUN (plus any line),
  ANYTIME TD on the NFL page, uppercased tags on the front page.
- **Bet lists are collapsible** (`cardsSectionHtml()`): Parlay Cards and
  Straight Bet Cards, each with an unfiltered `3 OPEN · 1 HIT · 2 MISSED`
  pill, **both default collapsed**. Open state lives in `CARDS_OPEN`
  (persisted per page), not a DOM class, because `renderContent()` rebuilds
  the HTML every poll. The real default is `loadCardsOpen()`'s `=== "1"`.
- **Expand all / Collapse all** (`#panel-controls`) drives `COLLAPSIBLES`;
  **each entry calls that panel's OWN toggle** so sub-text, localStorage and
  re-render side effects can't be skipped. A new collapsible panel = a new
  row there (`test_page.py` V6-V8).
- **Test-helper consequence:** Playwright's `inner_text()` returns only
  VISIBLE text, so tests reading bet lists must open them first
  (`expand_cards(page)`; `open_page(..., expand=False)` for the default check).

## 5. Bet markets: a registry

`MARKETS` (in every page carrying the MLB engine; the front page's also holds
the NFL, NHL and NBA/WNBA markets) declares each market's `label`, `subject`
(`player` / `team` / `game`), `grade`, and on non-MLB markets `sport`.

**THE DEFAULT IS THE SAFETY STORY.**
- **No `market` field means the sport's default:** `hr` for MLB, `td` for NFL
  and college, `nhl_goal` for NHL. NBA/WNBA have NO default ("Jokic +150" says
  nothing gradeable → `nba_unknown` → untracked). The default lives in
  `legMarket()` and **nowhere else** — special-casing it in `stateForLeg` once
  left `betsCashedBy`, `liveTileKind` and `relevantHrNames` still calling a
  touchdown a home run.
- The MLB parser writes no market on HR legs, so a home-run-only card's
  `tickets.json` is byte-for-byte what it always was.
- **An unrecognised market grades `untracked`**: shown by name, labelled not
  graded, counted in neither column, unable to kill a parlay. Never "improve"
  `stateForLeg()` by falling back to `stateForPlayer()` — that grades a spread
  as a home run bet (a mutation for exactly that is in the suite).
- **A bet carrying an untracked leg is `partial`** (user's rule): a MISS
  anywhere still kills it, but it can never go `hit`. Counted inside OPEN and
  surfaced by the slim **NOT FULLY TRACKED** bar (`#chip-partial`).
- **Foreign-market guard** (`marketForeign()`): a leg whose `sport` differs
  from its market's (no `sport` on a market = baseball; wnba counts as nba,
  cfb as nfl) is `untracked`. Before it, a football "Lions Moneyline" parsed
  as `ml` was graded off the Detroit TIGERS. `marketSubject()` reads the
  registry directly so an untracked Lions moneyline still names the Lions.
  (`test_combined_page.py` U5/U6.)
- **A team bet has no player.** `badge()` once called
  `isPhpCreditedHit(null)` on a won team bet and `normalizeName(null)` threw,
  taking the render down. Guarded in two places (`test_page.py` AB; the
  mutation has to remove both).

### MLB markets

- **Stat props** — `hrr` (H+R+RBI), `hits`, `rbi`, `runs`, `tb`, `doubles`,
  `xbh` (extra-base hits) — summed from the batting line, with `leg.line` and
  `leg.side` (no line = at least one). An OVER settles the moment it clears; an
  UNDER only at the end unless already busted. Rostered but never batted =
  void.
- **Pitcher props** — `k`, `er` (earned runs), `win`, `outs` (outs recorded),
  off the pitching line. A win has no line (`wins` is 0 until final, 1 for one
  pitcher). Only explicit phrasings ("to get the win") map to `win`; "Yankees
  to win" stays `ml`. `er` and `win` sit AHEAD of `runs` in the alias tables,
  same trap as "Home Runs" vs "Runs".
- **Game lines** — `ml`, `spread`, `total` — from `linescore.teams`, keyed by
  team abbreviation. **Only settled on a FINAL game** (`test_page.py` Y9 uses a
  live game with a score). Landing exactly on the number is a PUSH → `na`.
- **`f5`, a first-N-innings total, IS gradeable** — `linescore.innings[]`
  carries every inning; `stateForPartial()` sums the first N and settles early
  when already passed, waiting on `completeInnings` (the last array entry may
  be in progress). It was wrongly declared untrackable once: check the feed
  before declaring something untrackable.
- **Two teams joined by "/" or "+" is the GAME total**, checked before the
  combined-player split.
- **A card can name a matchup that isn't real** ("Red Sox vs Cubs" on a night
  BOS played NYY). The parser keeps `team` + `opponent`, `flag_matchups()`
  compares against the schedule and writes `mismatch`; the page grades
  `untracked` and says what really happened.
- **Doubleheaders:** `teamScores` is keyed by abbreviation, so a row with real
  runs beats one without (the score can't go backwards); which game a leg
  meant is genuinely ambiguous.
- **Odds can be minus money.** Every odds regex takes `[+-]`; tiles and the
  Bettor Tracker format with `fmtOdds()`.

### Stolen bases (`sb`)

Added 2026-09-19 (the user asked for HR + steals; later markets followed the
same registry path). `stateForSteal()` mirrors `record_results.py`'s
`grade_steal()` — change one, change the other.
- A hit is a `stolen_base_*` RUNNER event, found in `runners[]` inside somebody
  else's (often unfinished) plate appearance, never in the play's `result`.
  One steal can be several runner entries; events are keyed on at-bat +
  `playIndex` + runner. `caught_stealing_*` / `pickoff_caught_stealing_*` are
  attempts, not hits.
- **No Pinch Hit Protection**: a pulled player with no steal is a miss.
- **"Played" = appeared in the game** (holds a batting-order spot), not "came
  to the plate" — a pinch runner can steal without batting.
- Steal legs are tagged `market: "sb"` in history and excluded from the HR
  cross-check and "the ones that got away".

### Parity rules

- **`MARKET_STATS` exists in every page carrying the MLB engine and in
  `scripts/record_results.py`, and they must agree** — the first thing to check
  if page and history disagree on a prop. `record_results.py`'s
  `grade_market()` / `grade_stat_prop()` / `grade_game_line()` mirror the page
  graders (FINAL-only, push, combined legs summed). A recorded leg carries
  `counted` or `score` so the number is auditable. Statcast detail is gated to
  HR legs.
- **`parse_picks.py`'s `KNOWN` set** (the markets it treats as gradeable when
  writing the "ungradeable legs" note) must cover every market it emits that
  the page grades, or a gradeable leg gets a false warning.
- `LIVE_TILE_KIND` and `MARKET_STATS` mirror the registry's graders (§6).

## 6. Live Bet Tracker

Collapsed-by-default panel (Today tab only; ids and the `bmbs.liveab.open` key
date from its old name "Live At Bats"). One tile per picked player/team that is
doing something right now. **Eligibility:** leg still `live` AND at least one
bet it's on can still cash (a dead parlay's hitter gets no tile). One tile per
player however many tickets; 🧇 if any is an Iron.

**Every scoreboard filter scopes the tiles** through the same predicates the
ticket list uses (`ticketMatchesFilter`, `legPassesFilters`), filtered per bet
before players are merged. HIT/MISSED leave nothing and the panel says so.

### Tile kinds (`LIVE_TILE_KIND`, `liveTileKind()`)

| Kind | Markets | Tile |
|---|---|---|
| `bat` | hr, hits, hrr, rbi, runs, tb, doubles, xbh | batting-order machinery + progress line ("1 of 2 hits") |
| `base` | sb | ON 1ST/2ND/3RD (blue): diamond, outs, batter, next base open or "blocked" |
| `mound` | k, er, win, outs | pitcher tile (no batting order) |
| `game` | ml, spread, total, f5 | live score; says nothing settles until the final (f5: counts only the first N innings, "Settles after the 5th inning.") |
| `drive` | td, td_count, pass/rush/rec yds, receptions, pass_tds, most_rec_yds | NFL/CFB possession states (§10) |
| `defense` | sacks | ON DEFENSE, with the opponent's down & distance |
| `quarters` | quarters (each team scores all four) | one tile for the PAIR, grid per team |
| `team_drive` | q_score | only while THAT team has the ball |
| `ice` / `rink` | NHL player / team bets | §10 |
| `court` / `hoop` | NBA/WNBA player / team bets | §10 |

**A market absent from the table gets no tile on purpose.** `test_live_at_bats.py`
Z9 checks `liveTileKind()` directly — asserting tile absence alone passed with
the exclusion removed.

- **Every tile says what it's chasing**: "0 of 1 HR", "0 of 1 SB", "0 of 1 TD",
  "1 hits (under 1.5)" (red once busted). `labProgress()` reads the registry's
  `pitching` flag for the stat source.
- **Header rules:** the status is never truncated (prices wrap to a second
  line); a market label the progress line already says is dropped — labels
  stay only when one man carries two kinds of bet. The due-up line says where
  he bats ("Top 5 · batting 1st"). `labOddsHtml` lists HR prices from HR
  entries only (it once printed a hits price under "HR").
  (`test_live_at_bats.py` AB6/AB7, Z3/Z13-Z15; `test_steals.py` A8/A8b.)
- **Game-line and pitcher tiles link Gameday** via `gamedayUrl(gamePk)` like
  every other tile; pitcher stat lines carry `gamePk` because a DH-era pitcher
  has no batting-order slot (`test_live_at_bats.py` Z8b/Z8c).
- A leg naming two players puts both on the wall.
- **Grep a name before declaring it in these single-file pages.** A new helper
  named `stealsBy` silently REPLACED an existing function of that name (a later
  declaration wins) and the page died far away on `.find is not a function`.

### MLB batting machinery

Tile order: finished at-bats still holding their spot, at bat, then due up
(ON DECK, IN THE HOLE, then next half). For steals: results, runners with an
open base, hitters, blocked runners, due-up. Result tiles hold ~9s (home run
~14s, first ~3s as a bomb; "Stole 2nd!" holds like a homer) on their own timer.

`trackLiveAtBats()` (once per poll) reads `currentAB` + `recentABs`; each
finished play's key is announced once. Same seeding flood guard as alerts plus
`LAB.prevEligible` (a hitter who just homered is no longer "eligible"). At-bats
>3 min old or finished while Yesterday is showing are marked seen, not shown.
- **`about.isComplete` is the only trustworthy "finished" signal.** The feed
  writes mid-at-bat actions ("Batter Timeout") into `result.event`.
- **Only whitelisted events are announced** (`LAB_PA_RESULTS`); a play can end
  on a runner event.
- **`inningState` has FOUR values**: `Top`, `Bottom`, `Middle` (home up next),
  `End` (away up next). Treating anything-not-Top as home put the wrong team at
  the plate through every `End` window (measured: median ~71s, `Middle` ~91s).
  **`outs` reads 3 during both breaks** — don't charge it to the side about to
  lead off. **Nobody is "at the plate" during a break**: `isCurrentlyBatting`
  is forced false in `Middle`/`End`. (`test_live_at_bats.py` J1-J7, K1-K5.)
- **A side that hasn't batted leads off from slot 1** (only when it has NO
  plays). (`test_live_at_bats.py` AB, `test_combined_page.py` U.)
- Bases come from `linescore.offense.first/second/third`.
- It's pitch-by-pitch *as of the last poll*: several pitches can land at once
  and a short at-bat can finish between polls (its result tile still shows).
  PHP substitutes get no tiles. No extra API calls — it reads the feeds already
  fetched (`isComplete`, `count`, `balls`, `strikes`, `call`, `isOut` are in
  `FEED_FIELDS`).
- At-bats remaining uses a negative-binomial model with a league-average 68.5%
  out rate, NOT the hitter's own stats — disclosed in the UI; keep that
  framing (`test_at_bat_math.py`).
- **Test-helper trap:** `feed()` in `tests/test_page.py` numbers
  `battingOrder` per side. A flat `f"{i+1}00"` scheme makes the 10th player
  "1000" → slot 1, a fake substitute for the leadoff hitter, which wrongly
  triggers Pinch Hit Protection.

## 7. Alerts, sound and the bell

### Alerts (Overlay / Push / Sound)

Three independent toggles in the header, persisted per page; overlay defaults
ON, push and sound OFF. Toggles are hidden (visibility only) on the Yesterday
tab.
- **Overlay** — fixed celebratory card, only while the tab is visible; queued,
  never stacked.
- **Push** — plain Notifications API, page-open only. `requestPermission()` is
  called only from the toggle's change event (user gesture), never on load.
- **Sound** — Web Audio, **synthesised, not audio files** (no assets in these
  pages). `sndBomb` (HR), `sndSwipe` (steal), `sndKick` (TD; also NHL goals),
  `sndCash`. Fires whether or not the tab is visible. **Never create an
  `AudioContext` without a user gesture** — it's created in `setSoundNotif()`;
  switching on plays one bomb as confirmation. `SOUND_TRIM` is measured with an
  `OfflineAudioContext` peak, not eyeballed — re-measure if you change an
  envelope.
- **A play that CASHES a bet plays the register INSTEAD of the event sound**
  (user's correction; never two sounds back to back), mirroring the overlay
  turning gold rather than adding a second card. `test_page.py` W7 /
  `test_football.py` Q6 stub `SOUNDS` and call the real `playAlertSound`,
  because a spy only sees arguments.

**Guards (all channels):**
- **Flood guard:** `BOMB_STATE` is keyed on the slate date; the first poll
  seeds everything already hit without announcing. Names dedupe by normalized
  name.
- **No alert for a play that can't change anything:** `betsStillOpenFor()` —
  if every bet naming him is a parlay already dead, he's marked seen silently.
- **Keyed on the player's own event**, not leg state: a PHP-credited leg isn't
  a bomb by the named player. Alerts are per market: a bomb only for a HR pick,
  a steal alert only for a steal pick, and `betsCashedBy()` takes the market.
- **Cash upgrade:** `betsCashedBy()` finds bets now fully hit; the overlay
  turns gold, rains money behind the card, adds "2-LEG PARLAY CASHED $154.00",
  holds longer (`BOMB_CASH_MS`). It grades with the same `stateForPlayer()` /
  `evaluateTicket()` as the page, swapping today's `RESULTS` in (restored in a
  `finally`) because `RESULTS` follows the tab on screen. On the front page it
  swaps every engine's results in step (`NFL.swap()`, `NHL.swap()`, …).
- **Every leg alerts** (user's rule, 2026-10-04): `propHitEvents()` finds every
  `hit` leg outside `OWN_ALERT_MARKETS` (hr/sb, plus td on the front page),
  one event per distinct leg, through the same notified set (`prop:` prefix)
  and guards. `fireLegHit()` plays the sport's sound, pushes, queues an overlay
  in the leg's own words (`legHitWord()`: "2+ HITS!", "UNDER 4.5 K!", "WON!",
  "ALL 4 QUARTERS!") and flashes a green **HIT** / gold **CASHED** tile that
  replaces the plain at-bat result. The tile changes regardless of the overlay
  toggle. (`test_live_at_bats.py` AA, `test_combined_page.py` S.)
- **`alertSlate()`: alerts survive the last play.** When the last game goes
  final the slate rolls to Yesterday on the same poll, so walk-offs and
  final-whistle legs never alerted. Alerts also read the finished slate IF its
  date is the one `BOMB_STATE` was already watching (never one it wasn't —
  that keeps the morning after silent). (AA15/AA16, S3/S5, football I8/I9.)

### Notification bell (every tracker)

A bell top-right with a red badge counting what hit since you last looked;
opening it clears the badge. Each page has its own log key.
- **Two kinds of entry, and a leg that cashes a bet produces BOTH** (user's
  rule): the leg entry (who, what, odds, bettor, every bet he's on) and the bet
  entry (every leg, stake, payout, bettor). A single gets both too. **One event
  is one leg entry** however many bets carry him; keyed on sport + player +
  what was bet.
- **It BACKFILLS** on the first scan after load (user's choice) — the opposite
  of the overlay's flood guard.
- **Every entry is stamped with when it HAPPENED**, newest on top, ET time
  shown. `legHitTime(leg)` → `{t, approx}` from the play-by-play: MLB HR/steal
  times, a stat prop's crossing play (`snapshot.timeline`; `isScoringEvent` is
  in `FEED_FIELDS` for runs), a total's crossing play, final out for
  unders/wins/lines; NFL via `NFL.hitTime` off `snapshot.playLog` (yards etc.
  summed from play TEXT, `textKey()` builds ESPN's "F.Last"); a bet when its
  last leg landed. `approx` ("~") marks a guess; nothing placeable falls back
  to when the browser saw it, or "by 8:00 PM ET" — never an invented time.
- **Follows `SLATES.today || SLATES.yesterday`** (so the morning after isn't
  empty), never a queued slate; resets when the slate date changes.
  `bellWithToday()` swaps that slate's results in (all engines on the front
  page).
- **Replay control** on every entry (a wordless bell-in-arrow icon with
  `aria-label`): shows the alert again even with the overlay toggle off, to the
  FRONT of the queue, replacing a replay already showing
  (`BOMB_SHOWING_REPLAY`). Leg entries store `alert` as DATA, rebuilt and
  escaped on the way out, because the bell comes back out of localStorage
  (`test_bell.py` I8).
- **Every bell class is `bell-` prefixed — load-bearing.** A `leg` class picked
  up `.leg:first-of-type { padding-top: 0 }` (`test_bell.py` B5c/B5d).

### localStorage namespaces

All pages share one origin, so they share `localStorage`. Every page has its
own prefix and **a new key on any page needs its page's prefix**: `bmbs.`
(MLB), `bmbs.fb.` (NFL), `bmbs.all.` (front page), `bmbs.hk.` (NHL),
`bmbs.bb.` (NBA), `bmbs.wb.` (WNBA), `bmbs.cf.` (college). The front page once
shared MLB's keys (sound on one switched it on in the other); `test_bell.py`
E10 now fails on any non-`bmbs.all.` key there, and each league test pins its
own (e.g. `test_hockey.py` A4).

## 8. Scoring Log / Home Run Log / Touchdown Log

- MLB **Home Run Log**: every HR on the slate's date, newest first (sorted on
  `about.endTime`), "Our Picks" vs "All Home Runs". Rows that matter to the
  slate (named in its tickets, or a PHP substitute currently crediting a leg)
  get a green border under either filter. The header pill reads
  `N OURS · M TOTAL · P%` (`ours / total`, unweighted), empty when no HRs.
- Tap-to-expand rows: the collapsed row carries hitter/team/inning/pitcher/
  distance/exit velo, the expansion the rest of the Statcast detail. Field
  availability was verified on 45 HRs across 24 games. **Roofed parks report
  "0 mph, None" wind**; `weatherWind()` shows the roof instead. Live-game
  Statcast latency is unverified; every field is guarded (em-dash if absent).
- The strike-zone grid never says "inside"/"outside" — handedness and the
  API's sign convention were not verified.
- NFL **Touchdown Log** is the twin (`#tdlog-count`).
- Front page **Scoring Log** (hidden, §1): HRs, NFL and college touchdowns and
  NHL goals (PPG/SHG/EN/GOAL) interleaved by timestamp, sharing `.hr-row`
  markup. "Ours" for a touchdown uses the ENGINE's normalizer. A touchdown
  row's inline `onclick` names `toggleTdRow`, which lives inside the module —
  the host carries a `window` shim or every TD row throws on tap. No basketball
  rows (hundreds of baskets a game).
- All panel-header pills use the muted `.head-count` style except the Live Bet
  Tracker's green `.liveab-count` ("something is happening now").

## 9. The All Sports front page (`index.html`)

Tracks one card that can hold MLB, NFL, NHL, NBA, WNBA and college legs,
including a single parlay mixing sports. Assembled 2026-10-04 from the MLB page
(the superset: registry, untracked/partial, PHP, stat props, game lines) plus
sealed engines for the other leagues; independent since.

**Each non-MLB league is a SEALED engine, not a merge — the most important
thing to understand about this file.** The pages define the same NAMES for
different jobs: `normalizeName` most dangerously (football strips generational
suffixes because ESPN writes "Marvin Harrison Jr."; MLB must NOT, because MLB's
feed always sends them), plus `stateForPlayer`, `evaluateTicket`, `RESULTS`,
`TICKETS`, `getGameSnapshot`, `pollSlate`. A merge would need every collision
renamed by hand, and a miss is silent. So:
- `const NFL = makeFootballEngine("nfl", "nfl")`, `const CFB =
  makeFootballEngine("college-football", "cfb")`
- `const NHL = (function () { ... })()`
- `const NBA = makeHoopsEngine("nba")`, `const WNBA = makeHoopsEngine("wnba")`

Each has its own closure (RESULTS, picks, caches) and exports a small API
(`has/register/poll/use/swap/empty/state/context/teamContext/hitTime/rowHtml/
toggleRow/gameUrl/norm/...`). Engines bring no page shell (render, filters,
tabs, alert queue) — the host owns that. `hoops(leg)` / `gridiron(leg)` pick
the engine wherever the host reads one. `test_combined_page.py` A3/A4 pin both
normalizers coexisting; if they fail, the seal is broken.

Which pages carry which engine generation: `cfb/index.html` matches the front
page (both factories); `wnba/index.html` has `makeHoopsEngine` but an older NFL
IIFE; `hockey/index.html` and `basketball/index.html` were generated before the
factories and keep their single-league IIFEs. All are independent; none was
regenerated.

- **A mixed parlay needed no special case**: `evaluateTicket()` takes an array
  of leg states and never asks which sport produced them.
- **Rollover: every engine with legs on the card is done** (user's rule).
  Deliberately not the NFL week rule. An all-NFL card is not an empty slate;
  an all-MLB card never waits on football. `mlbLegsOn()` makes MLB's
  `pollSlate()` return done before fetching even the schedule when the card has
  no baseball leg. Each engine is handed only its own legs.
- **Team tiles are keyed with a league prefix** where abbreviations collide:
  `CFB MIA` vs the Dolphins, `WNBA NY` vs the Knicks (`test_cfb.py` E,
  `test_basketball.py` G3).
- **The front page never ported football's drive-result flash tiles** ("Punt",
  "TD — not him"); their dead code was removed 2026-10-05.
  `football/index.html` still has them.
- **No results archive and no History page** for All Sports yet (deferred by
  the user), so the footer does not link `/history/`.
- It was assembled by scratchpad scripts, not a build step; don't add one.

## 10. Per-league notes

### NFL (`football/index.html`, and the NFL engine)

Built 2026-09-19 as an independent twin of the MLB page.

**ESPN, from the browser, but mind the host.** `site.api.espn.com` answers
curl with `Access-Control-Allow-Origin: *` and then OMITS it for a real browser
on another origin. Use **`site.web.api.espn.com`** (and
`sports.core.api.espn.com`). Test CORS in a real browser, never with curl.
**Python must use `site.web.api.espn.com` too** — `site.api.espn.com` returned
Akamai 403 to `urllib`. Endpoints (keyless): `scoreboard?dates=YYYYMMDD` (one
date per call; a range is 400; games filed under their ET date), `summary?event=ID`
(~60KB gzipped, `max-age=3`, can't be trimmed), core
`.../competitors/{teamId}/roster` (per-game `didNotPlay`). Final games fetched
once, pre-game not at all, live games with no picks once a minute (for the log).

- **Anytime TD** = boxscore TD column summed over rushing, receiving,
  defensive, interceptions, kick/punt returns. **Passing excluded.**
- **Match by ESPN athlete id first, name second.** Names normalize with
  suffixes stripped. A by-name hit only counts for the pick's own team (two
  Josh Allens).
- **Miss vs void:** final + stat line → miss; no stat line → roster endpoint:
  `didNotPlay`/absent → `na`, played → miss. Unresolved id + no stat line → `na`.
- **A football slate is an NFL WEEK** (user's rule): tabs read "This Week's
  Picks" / "Last Week's Picks" (internals still `today`/`yesterday`). A card
  stays until the week's LAST game (Monday night) is final, even a Sunday-only
  card. The parser dates from the NFL schedule: `date` = earliest upcoming
  picked game, `endDate` = last day of that week with a game, `weekEnds` = the
  week's Tuesday (weeks run Wed..Tue). A second card in the same week REPLACES
  the first (Thursday picks vanish unless repeated); only a new week archives.
  The page polls every date in the span (`SETTLED_SCHEDULES` caches settled
  ones), backstop 6am ET after `endDate`. No ESPN → card times + following
  Monday.
- **Negative odds are normal**; `fmtOdds()` everywhere.
- **Live Drives states — green means "he can score on this play":**
  RED ZONE / ON OFFENSE (his side has possession AND their drive is open AND
  that drive isn't over); TAKING THE FIELD SOON (grey, dashed: possession is
  theirs but the drive isn't open yet; measured windows 65-179s); nothing at
  all on defense or right after his own drive ended (ON DEFENSE / HALFTIME /
  BETWEEN DRIVES tiles removed at the user's request).
  - **The finished-drive bug:** ESPN stamps `displayResult` on
    `drives.current` when a drive ends but keeps `situation.possession` on that
    team until the kickoff is returned — a field-goal team's pick sat green
    through the kickoff. All three conditions are needed (possession alone is
    stale after a score; an open drive alone is stale after a punt/turnover;
    `drive.driveOver` covers the kickoff window). The scoring team is dropped,
    not shown as TAKING THE FIELD SOON (`!myDriveOpen`).
  - Possession comes from the LAST PLAY's end state (or
    `situation.possession`), not `drives.current.team`, which lags after a
    change; `drive.driveTeam` carries the lagging value. Halftime has no
    `situation.possession` at all.
  - **ESPN publishes no personnel data**: `participants` names only players
    involved in a play. "Is HE on the field" is unknowable; never claim it.
  - Drive results hold the tile ~9s ("Punt", "Field Goal", "TD -- not him");
    his own TD ~14s.
- **Every football prop the front page can GRADE it can FOLLOW** (yards,
  receptions, passing TDs), progress first via `nflProgress()` (skipped for a
  multi-player leg). Sacks have the `defense` kind (shows only while his side
  defends — not the removed anytime-TD ON DEFENSE tile).
- **Quarter bets:** `quarters` (each team scores all four) is one tile per pair
  — the real leg carries a garbled `players` split, so never fall back to
  `legPlayers()`. `q_score` (a team scores in quarter N; "each team in each
  quarter" = eight legs) HITS the moment that team scores in that quarter and
  MISSES when the quarter ends without. **"The quarter ended" is read off the
  clock** — ESPN `period` + status name (`STATUS_END_PERIOD`,
  `STATUS_HALFTIME`) → `periodOver` — not a 0:00 clock (an untimed down can
  still score). `quarterOver()` is shared by both graders. A later quarter is
  `not_started`. Seven of eight in is an ordinary Iron. A two-team row names
  both teams (`legSubjectName`). (`test_combined_page.py` R, T.) The
  `espn_event` fixture derives `period` from the linescores; it used to
  hardcode 4.
- History: `/football/history/` (§14).

### NHL (`hockey/index.html` and the `NHL` engine), built 2026-10-05

- **ESPN `site.web.api.espn.com/.../hockey/nhl`** scoreboard + summary. The
  NHL's own `api-web.nhle.com` sends no CORS header — unusable.
- **Markets are always `nhl_*`**: `nhl_goal` (default), `nhl_points`,
  `nhl_assists`, `nhl_sog`, `nhl_saves`, `nhl_ml`, `nhl_pl` (puck line),
  `nhl_total`. Never `ml`/`total`: those are baseball graders keyed by
  abbreviation, and TB is the Rays AND the Lightning. Overs settle when they
  clear; unders and team bets at the final; OT and shootout count; push = `na`;
  rostered with no stat line at the final = void.
- **Tiles:** `ice` — count toward the line plus IN NET (goalie) / ON THE ICE /
  ON THE BENCH from the summary's `onIce` list ("In Play" entries), or plain
  LIVE when the list is absent. **`onIce` CONFIRMED live 2026-10-05**: 11-12
  "In Play" ids during play, absent around the opening faceoff. It's only
  trusted while the game is live, and "benched" is never inferred from a
  missing list (`test_hockey.py` C9). POWER PLAY / SHORT-HANDED come from the
  latest play's `strength` (e.g. `power-play`), relative to that play's team.
  `rink` — ONE tile per team listing every bet on it; a total gets its own.
- Goals have their own alert (🚨, kick sound) and log rows.

### NBA (`basketball/index.html` and the front page's `NBA` engine), built 2026-10-05

- ESPN `.../basketball/nba` scoreboard + summary. Boxscore labels `MIN PTS FG
  3PT FT REB AST TO STL BLK OREB DREB PF +/-` ("3-7": made first); rows carry
  `active`, `starter`, `didNotPlay`, `reason`, `stats: []` for a DNP. A play's
  participants: actor FIRST, assister/stealer/blocker SECOND; `scoreValue`
  1/2/3. `PLAY_CREDIT` reads that to time a hit.
- **Box-score `active` CONFIRMED live 2026-10-05** = exactly 5 per team,
  changing with substitutions. Trusted only while live AND naming 1..10
  players; otherwise `onCourt` is null and the page says LIVE, never ON THE
  BENCH. In a FINAL game `active` is false for everyone.
- **Markets `nba_*`**: points, rebounds, assists, threes, steals, blocks, the
  combos `nba_pra`/`nba_pr`/`nba_pa`/`nba_ra` (an engine stat key may be a "+"
  sum), `nba_dd`/`nba_td` (2/3 categories in double digits among PTS REB AST
  STL BLK), `nba_ml`/`nba_spread`/`nba_total`. Hockey's grading shape; **no
  default market**.
- Tiles: `court` (ON THE COURT / ON THE BENCH / LIVE / HALFTIME / BREAK, count,
  "N FOULS" from four) and `hoop` (one per team).

### WNBA (`wnba/index.html`, `makeHoopsEngine("wnba")`)

ESPN's WNBA summary is the NBA's column for column, so it runs on the same
engine factory with its own closure. A WNBA leg is `sport: "wnba"` with the
same `nba_*` markets; `marketForeign()` treats wnba as nba. The 2026 season
ended Oct 1, so nothing WNBA has been seen live; same rule as the NBA (unknown
means LIVE). `test_wnba.py` runs `test_basketball.py` with
`HOOPS_LEAGUE=wnba`, plus its section G (both leagues on one card).

### College football (`cfb/index.html`, `makeFootballEngine("college-football", "cfb")`)

ESPN's college summary is the NFL's field for field. A college leg is
`sport: "cfb"` with the NFL's markets. Touchdown alerts loop over both
football engines (college under `cfbtd:` keys).
- **"Played" = `starter`** — ESPN never sets `didNotPlay` on a college roster
  (128 listed, 0 flagged). A starter with no stat line is a miss, anyone else
  with none is VOID. **The user confirmed this rule 2026-10-05** (declined
  "no stat line = miss for everyone"); don't change it without asking.
  (`test_cfb.py` D3/D4.)
- **A Saturday is 50+ FBS games** (the default scoreboard is FBS): a game no
  pick is in is never fetched, not even for the log, and an unresolvable name
  doesn't open every game (`watchEverything` is NFL-only). FCS (group 81) isn't
  on that scoreboard; an FCS pick sits not-started.
- **ESPN's school rosters are incomplete** (12 of 63 box-score players missing
  on VAN @ UGA, including Georgia's starting QB). See §11 for the top-up.

## 11. Parsers and rosters

### `parse_picks.py` (MLB) — the card templates

The group's generator changes template without warning, and each change parsed
to zero tickets (exit 1, nothing written, the slate silently never posted)
until supported. All templates parse from the same input, decided by which
regex a line matches. **A ticket is a single or a parlay by its actual leg
count, never by its header.** Accepts text with or without markdown markers
(`## `, `* `) — a rendered Gemini copy has them stripped.

| # | First seen | Distinguishing shape | Fixture (`tests/fixtures/`) | `test_parser.py` § |
|---|---|---|---|---|
| 1 | original | `## Longshot` / `## N-Leg Parlay Cards` headers, `Card N: Title`, `Player (+ODDS) \| TEAM (Bettor)`, `Bet by X: $Y \| PP: $Z` | `gemini_picks.txt`, `test_picks.txt` | 1 |
| 2 | 2026-09-18 | `Ticket N: ...`, legs `TIME \| Player (Team) +ODDS (Bettor)`, footer `(Bet by X) [Bet: $Y \| PP: Z]` | `discord_ticket_format.txt` | 4 |
| 3 | 2026-09-19 | `Ticket #N (Bettor - $X Bet) [PP: $Y]` under `Part N:`; legs `* (Bettor) Player - TEAM (+ODDS) - TIME ET`; prop-style legs `- Player - Stolen Bases O0.5 (+450) - SEA @ COL - TIME` | `discord_ticket_hash_format.txt`, `discord_prop_legs_with_steals.txt` | 5, 7 |
| 4 | 2026-09-20 | `🎰 Ticket #N (M-Leg Parlay)` / `🔥 Bonus Ticket`, `Bet: $X (Book) \| PP: $Y` line, `• Player (TEAM) - TIME ET (+ODDS) (Bettor)` | `discord_emoji_ticket_format.txt` | 10 |
| 5 | 2026-09-21 | `🕒 <Name> Window (...)` + `Bonus Bets Tracker` sections, `Parlay N (Owner)` / `Ticket N (Owner)`, `* Player (+ODDS) (Who) – TIME ET` (en-dash; who and time optional), `* Wager: $X \| Payout: $Y` | `discord_parlay_window_format.txt` | 11 |
| 6 | 2026-09-23 | "DAILY HOME RUN PARLAY TRACKER": bare bettor-name line, `Ticket #N - 2-Leg Parlay $6.00`, `[ ] Player +ODDS (Nickname) TIME`, `Potential Payout: $X` / `N/A` | `discord_tracker_checkbox_format.txt` | 12 |
| 7 | 2026-09-24 | "SOLAR KEYS": `🎟️ Ticket N (6.00 bet pays 198.00)`, `* Player (+ODDS) - TEAM (Who) 🕒 TIME ET` (written by the auto-fixer) | `discord_solar_keys_tracker_format.txt` | 20 |
| 8 | 2026-09-26 | bare `Parlay N`, `* Player (+ODDS) — Full Team (Bettor) TIME` (em/en-dash only), `$6.00 Bet \| Potential Payout: $X (Owner)` | `discord_plain_parlay_emdash_format.txt` | 13 |
| 9 | 2026-09-29 | single-game props: `Phillies vs. Braves 2:00 PM` header, `Ticket #N`, priceless bullet props (surnames, `Schwarber/Olson`), `$5 pays $109.70` | `discord_single_game_props.txt` | 14 |
| 10 | 2026-09-29 | bare `Ticket N`, bullet free-text props, no game header, `Wager: $X \| Payout: $Y` | `discord_team_bets_no_header.txt` | 15 |
| 11 | 2026-09-30 | bare `Ticket N`, UNbulleted prop lines, `8.50 pays 105.79` (no `$`) | `discord_pitcher_markets.txt` | 17 |
| 12 | 2026-10-01 | `- Subject: Bet` legs, mixed sports, `Stake: X \| Pays: Y [(Owner)]` or `Stake/Pays: N` | `discord_priceless_mixed_format.txt` | 21 |
| 13 | 2026-10-04 | bare `Ticket N`, price at the END of each leg (`... Anytime TD +130`, `2+ TB 145`) | `sports_bare_ticket_trailing_odds.txt` | 19 |

**If an upload parses to zero again, that's a new template, not a
regression.** First let the auto-fixer try it (§13); either way check the
Action log for `WARNING: parsed nothing`, get the raw text, and add a fixture +
assertions — including for what the fixer writes, which is reviewed like any
patch, not trusted because it passed.

Lasting lessons (each pinned in `test_parser.py`; "section N" below means
that file's sections):
- **Header collisions with `PARLAY_HEADER_RE`:** "🎰 Ticket #1 (3-Leg Parlay)"
  contains "3-Leg Parlay", so every ticket header was misread as a new
  SECTION. `section_header()` carries explicit exclusion guards (football's
  parser hit the same trap independently).
- **A new pattern must not match a line an older template owns.** The
  em-dash in template 8 is load-bearing: template 7's legs are the same shape
  with a hyphen, so `PLAIN_PARLAY_LEG_RE` excludes `-` (pinned in section 13).
  `TRACKER_HEADER_RE` is word characters and spaces only so it can never
  swallow a leg line.
- **Template 5's `(Bettor)` header is the ticket OWNER**, a fallback for legs
  without their own `(Who)`. Requiring the optional tails once glued the bettor
  to the name ("Francher-Harper": unresolvable, ungradeable).
- **Template 6 user decisions, deliberately different branches:** a leg with
  NO ODDS is reported via the note and NOT tracked (a price can't be invented;
  the ticket may become a single by leg count); a leg marked `- DNP` is dropped
  WITHOUT a warning. The bare name line is only REMEMBERED, consumed only when
  a ticket header follows. Card nicknames are ignored — the roster supplies the
  team (no second source of truth).
- **Templates 9+: a leg may have a NULL price** (only the ticket is priced).
  Surnames resolve only within the two named teams (`resolve_in_teams`), and
  one that still matches two players stays unresolved. "N+" means over N-0.5.
  `CARD_MARKET_WORDS` order matters (hrr before hits, er/win before runs, xbh
  before doubles and hits).
- **Template 13's price splitter** (`split_trailing_odds`): a signed number is
  a price; an unsigned one only at 100+ and never a decimal, so "2+ TB" and
  "Over 3.5 Runs" keep their line.
- **One unreadable footer costs that ticket its stake, not the card**
  (section 18).
  `clean_num()` accepts `$` and `,` (a "$310.28" capture once crashed
  `parse()`).
- **`leg.who` must be JUST the name** — anything after a dash is stripped
  ("(Noid — Listed as Herb Hernandez)" was once glued on).
- **Nothing is dropped silently.** An unmatched line that looks like a bet
  (`BETLIKE_RE`: a `Ticket #N` header, or a bulleted line with odds) is a
  stderr `WARNING` and goes into `tickets.json`'s `note` — the slate still
  posts. (A card once "successfully" parsed 16 of 18 tickets.) Legs that parsed
  but can't be graded (unknown market, unresolved player, `mismatch`) are
  listed in the note too.
- **Name resolution** (`resolve_player()`): exact, then a full name minus its
  generational suffix ("Michael Harris" → Michael Harris II, first AND last
  matching), then fuzzy at 0.82 against SUFFIX-STRIPPED names (" jr" alone
  can drop a typo below the cutoff), then give up: name as typed, BLANK team
  (never guess). Misspelt teams fuzzy-match too, only when unambiguous.
- **Slate `date`** from the listed start times: posted after its last first
  pitch → tomorrow, else today (ET).
- **First pitch times filled from MLB** (`fetch_start_times()` /
  `fill_missing_times()`): only ever fills a BLANK; a stated time is never
  overridden. `startTimeTBD` games are skipped (placeholder `gameDate`); a
  doubleheader takes the earlier game; a single's prebuilt `meta` is spliced
  too. The parser's only network call; it fails soft (section 16).

### `parse_football_picks.py`

Independent of the MLB parser; its accepted shapes have diverged. Templates
(`test_football_parser.py` sections): `Ticket N:` (`football_ticket_format.txt`,
1), `Card N:` (`football_card_format.txt`, 2), and `Ticket N (M-Leg Parlay)` with
`Bettor: X | Bet: $Y | Potential Payout: $Z` and `- (Bettor) Player (ODDS) Time`
legs, free-text times like "Check Listings" (`football_summary_format.txt`,
2b; same `PARLAY_HEADER_RE` collision fix). If a future upload parses to zero,
check which sport. Stores `athleteId` on every leg; dates from the NFL schedule
(§10).

### `parse_combined_picks.py` (All Sports) and the league wrappers

**A thin layer over `parse_picks.parse()`, not a third parser** — re-reading
every template would guarantee drift. It answers only: which SPORT is each
leg, and so which roster resolves it.
- **The join is the leg's player STRING** as `parse_picks` returned it
  (canonical or as typed; `pp_key_for`), so one parlay can carry "Jose Ramirez
  Anytime TD" (NE) and "Jose Ramirez" (CLE) and grade two people. Name +
  MARKET is looked up before name alone (one man on two bets). Several names
  sit on two rosters; a wrong sport doesn't throw, it grades quietly wrong.
- **Evidence order** (`decide_sport`, checked directly): explicit market word,
  roster membership when the name is on one roster only, the team named on the
  line, and the section heading LAST (a heading naming both sports clears the
  default). Nothing resolvable → reported in the `note`, never guessed.
- **The team is read from the text AFTER the price, never the player's name**
  (Buffalo, Jackson, Carolina, Phoenix are names). Text before the price is
  consulted only when the rest names no team, all-or-nothing.
- **A market phrase glued to the name is stripped and re-resolved** ("Max
  Fried Strikeouts Over 5.5" otherwise comes back with a blank team).
- `NFL_TEAM_WORDS` is a static 32-row table (the football roster has
  abbreviations only).
- **`normalize_card()`** first turns a bare count into "N+" and an unsigned
  team half-point into "+1.5" (the 2026-10-05 bare-count card,
  `sports_bare_count_format.txt`, `test_combined_parser.py` O). A bare "Yards" is read by position
  (`pos_by_norm`: QB passing, RB rushing, WR/TE receiving). A shared-city tie
  ("Tampa Bay") is broken by a nickname only one league uses.
- **League detection order in `scan_card`:** WNBA first (`nba_record(...,
  league="wnba")`, evidence: a WNBA name or team only), then NBA (NBA name or
  team, or an NBA-ONLY word: `NBA_ONLY_MARKETS` — rebounds, threes, combos,
  double/triple-double, blocks; "steals" is MLB's too and excluded), then
  hockey (`nhl_record`), then college (`cfb_claim`), then MLB/NFL. NBA before
  hockey because "Points"/"Assists" are hockey words; WNBA before NBA so A'ja
  Wilson's rebounds don't land in the NBA.
- Hockey traps: **name first, market second** ("Brayden Point Anytime Goal" was
  read as a POINTS bet); shared nicknames need their city
  (`nhl_team_words` accepts only nicknames no other league uses; full names
  always); `NHL_MARKET_ALIASES` most-specific first ("Shots on Goal" isn't a
  goal; "Field Goal" isn't hockey).
- **"Kings" is both leagues'**: each league's team words exclude the other's
  nicknames on an All Sports card; `teams_for()` lets a bare nickname take its
  city from the team named after the price, only when the subject IS that team.
  Cross-league player names (Jose Alvarado, Spencer Jones, …) are settled by
  the team on the line (`his_club`); with no team they stay baseball's.
- College (`cfb_claim`, before the MLB/NFL decision): the player at the school
  the line names, a school named after the price with nobody from another
  league, or a name only the college roster knows. `cfb_team_words`: full names
  always; a short name/nickname only when exactly one school has it and (on an
  All Sports card) no other league's team contains it ("Arizona" → Cardinals).
  Collisions: the parser takes the holder at the named school
  (`others_by_norm`). College legs then take the NFL path with the college
  roster swapped in.
- **`only_sport`**: a `hockey_*.txt` / `basketball_*.txt` / `wnba_*.txt` /
  `cfb_*.txt` card forces every leg to that league (Will Smith is a Dodgers
  catcher AND a Sharks centre). On a single-sport card every own nickname
  counts. A dated card title ending in a bare number is skipped.
- **Slate span = the PICKED teams' games**, not the NFL week rule. A FINISHED
  game is skipped EXCEPT today's while a picked team still plays today
  (`test_combined_parser.py` I4/I5); college looks eight days ahead (a Saturday
  card goes up Monday).
- Twelfth-template legs: "- Lions: Score in 1st Quarter" → `q_score`, keyed on
  the leg's FULL text (the team name repeats four times); a footer owner
  ("Stake: 9.88 | Pays: 84 (Kenny)") becomes every leg's bettor and the
  ticket's book. A leg's sub-line omits the bettor when it IS the owner.
- **The wrappers** (`parse_hockey_picks.py`, `parse_basketball_picks.py`,
  `parse_wnba_picks.py`, `parse_cfb_picks.py`) set `only_sport`, `sports`, their
  own OUT/PREV paths (`test_combined_parser.py` P13b), archive on a new day.
  **No real hockey, basketball, WNBA or college card has arrived yet**: their
  fixtures are written to the generator's current shape, so treat the first
  real one as a template incident.
- The bare-count card's NHL leg is now a real `nhl_pl` on the Lightning
  (`test_combined_parser.py` O10); the "shown, not tracked" stopgap from before
  hockey was built is gone.

### Rosters

- **Every roster builder MERGES with the committed roster; none overwrites**
  (`merge_rosters()` in `build_roster.py`, `build_football_roster.py`,
  `build_hockey_roster.py`, `build_basketball_roster.py`,
  `build_cfb_roster.py`; `--replace` is the escape hatch). Active rosters
  exclude the IL: one MLB overwrite dropped 73 names including Aaron Judge, and
  a missing player is the worst state (his leg can never resolve). A stale TEAM
  is cheap. Fresh data wins for anyone in both. `merge_rosters()` is pure so
  it's tested offline (`test_build_roster.py`; football in
  `test_football_parser.py` section 5).
- **No roster is on a schedule. Rebuild whenever a name won't resolve.**
  (MLB went 8 days stale before a call-up couldn't resolve.)
- **MLB: never strip generational suffixes.** MLB's feed always includes them
  ("Fernando Tatis Jr."); a CSV roster without them left three picked stars
  stuck `not_started` all game. The roster is built from the MLB Stats API.
- `build_basketball_roster.py --league nba|wnba` (NBA/WNBA rosters are a flat
  list). `build_cfb_roster.py`: FBS only (group 80: 138 schools), tops rosters
  up from the last `--days` of box scores (~14k players; 209 names shared —
  plain maps keep a skill player, `others_by_norm` lists the rest);
  `--season` defaults from the date (ESPN files a college season under the
  year it starts, so before March it's last year). Rebuild weekly in season.

## 12. Discord intake bot (`discord-bot/`)

A friend uploads a `.txt` in the intake channel, confirms with a reaction, and
the bot commits it to that sport's `incoming_picks.txt` via the GitHub
Contents API, which triggers the parse workflow.

**Routing is by FILE NAME prefix only** (`ROUTES` in `bot.py`; never inspects
the text — cards share a template and a guess would overwrite the wrong slate):

| Prefix | Writes | Status message links |
|---|---|---|
| `baseball` | `data/incoming_picks.txt` | `https://bmbs.bet/mlb/` |
| `football` | `data/football/incoming_picks.txt` | `https://bmbs.bet/football/` |
| `sports` | `data/combined/incoming_picks.txt` | `https://bmbs.bet/` |
| `hockey` | `data/hockey/incoming_picks.txt` | `https://bmbs.bet/hockey/` |
| `basketball` | `data/basketball/incoming_picks.txt` | `https://bmbs.bet/basketball/` |
| `wnba` | `data/wnba/incoming_picks.txt` | `https://bmbs.bet/wnba/` |
| `cfb` | `data/cfb/incoming_picks.txt` | `https://bmbs.bet/cfb/` |

Anything else is refused with a rename hint that lists every prefix, built
from `ROUTES` (2026-10-05; it used to hard-code three) — `test_discord_bot.py`
C7b. `test_notify_discord.py` F requires the CLI's `--sport` choices ==
`SITE_URL` == the bot's route sites.
- **Re-posting an IDENTICAL card is a no-op and the bot says so.** The Contents
  API creates an empty commit for identical content, and the workflow triggers
  only on a change, so nothing would fire. `push_incoming_picks()` compares
  first and returns None (`test_discord_bot.py` E1-E5, B9-B11).
- **`[discord:<id>]` in the commit message** carries the uploader's id to
  GitHub Actions (Discord mentions need the id, not a username). Every parse
  workflow and the auto-fixer grep it out. Absent (web-editor upload) = no
  mention.
- **The bot does NOT run from this clone.** It runs from a separate clone on
  the user's always-on Windows "Plex box", under Task Scheduler via
  `run_bot.bat`. That box's clone has the `.env`; the dev clone doesn't — the
  quick tell. A Claude Code session here cannot reach it; a `bot.py` change
  does nothing until that box pulls AND restarts the process (commands in
  `discord-bot/README.md`). **Proving a restart took:** upload an unroutable
  `.txt`; the refusal lists every prefix the RUNNING code knows.

## 13. Discord status messages and the auto-fixer

### `scripts/notify_discord.py`

Posts to the intake channel via an incoming webhook (`DISCORD_STATUS_WEBHOOK`,
an Actions secret; one channel for all sports). Two subcommands: `success`
(summarize a freshly written tickets file and post it) and `post` (post text,
print the message id). Every status is a NEW post that @-mentions the
uploader — never an edit (an edit pings nobody and changes no timestamp).
- **Success path:** every parse workflow's commit step sets `committed`
  (`yes` only for a genuine new commit); a step gated on
  `committed == 'yes'` runs `success --mention <id> --before ...`. The message
  says the picks **are now LIVE** on that sport's page and shows a date range
  whenever `endDate` differs from `date` (any sport).
- **Correction vs new slate:** each workflow copies the live tickets file to
  `$RUNNER_TEMP/live-before.json` before parsing and passes `--before`. Same
  slate (`slate_key()`: the date, or NFL's `weekEnds`) → "UPDATED with
  corrected information" plus `what_changed()`; otherwise "now LIVE". The
  auto-fixer's success adds `--fixed`. (`test_notify_discord.py` E,
  `test_workflow_yaml.py` H.)
- **The four hidden-league workflows also post a "couldn't be processed"
  notice `if: failure()`** — they have no auto-fixer (`test_workflow_yaml.py`
  B3m: posts on failure, that step can't fail the job).
- `allowed_mentions` is pinned to `users` (a card containing "@everyone" would
  otherwise ping the server). The webhook URL never reaches a log or error
  message. Errors raise `NotifyFailed`, which `main()` turns into a stderr
  warning and exit 0.
- The ping fires on the commit, not the Pages deploy, which has sat queued 10+
  minutes — "now LIVE" can precede the site.

### `auto-fix-parse-failure.yml` + `scripts/auto_fix_parser.py`

Covers **baseball, football and combined** only (fires on those three parse
workflows' `workflow_run` failure; `workflow_dispatch` with a sport dropdown).
An unattended Claude Code AGENT was refused by the platform's safety classifier
("Create Unsafe Agents") — not a bug to route around. What's built is bounded:
ONE plain Claude API call per attempt, no tools, judged by a plain script.

1. Post "attempting an automatic fix now" to Discord.
2. `already_parses()` runs `RUN_PARSER` (the combined parser for a combined
   card) on the upload first; if it already parses, stop — a repair whose
   subject is healthy must not "succeed" (a branch cut before the upload once
   reported a fake fix and re-parsed an old card over live data).
3. Send the parser source + the raw upload to the API (`ANTHROPIC_API_KEY`;
   absent → `resolved=no` immediately). The model returns **search/replace
   edits** (`apply_edits()`: each SEARCH must match exactly once, all or
   nothing, no-ops refused), with a whole-file response accepted as fallback.
4. Run `FULL_SUITE` — every `tests/test_*.py` except the two network tests
   (`test_feed_fields`, `test_notify_discord_live`); it was a stale hand list
   of 19 that missed the combined parser built on `parse_picks.py`
   (`test_auto_fix_parser.py` S1). Then `verify_fix()` re-parses the real
   upload AND re-runs `test_live_data_schema.py` against the tickets file just
   written.
5. Any failure reverts the parser byte-for-byte and retries with the failure
   detail (including the model's summary and a diff of what it tried), up to
   `MAX_ATTEMPTS = 4`.
6. Success leaves the patched parser, the new tickets file and an archived
   upload (`tests/fixtures/auto_detected_<sport>_<timestamp>.txt`, for a human
   to turn into a regression test) uncommitted; a separate bash step commits
   and pushes. The script never runs `git`.
7. Post the outcome as a NEW message: fixed and live / found a fix but couldn't
   push it (the commit step's outcome is checked separately) / couldn't
   resolve.

**Security:** the upload and the model's response are untrusted. Neither is
spliced into a shell command via `${{ }}` (substituted before the shell
parses); values go through `env:` + quoted shell variables.

**Lessons, each found the hard way (2026-09-20 → 10-01):**
- **Discord needs a `User-Agent`**, or Cloudflare returns 403 `error code:
  1010` before Discord sees the request. No message had ever been delivered in
  CI until this was found. `test_notify_discord_live.py` probes the real host
  with a bogus webhook id (expects 404, and that a no-UA request is still
  blocked).
- **A status ping must never veto the repair:** every Discord step is
  `continue-on-error` and `NotifyFailed` exits 0 (`test_workflow_yaml.py` F).
- **`python-dotenv` must be in `tests/requirements.txt`** (`bot.py` imports it
  at module level); without it the suite gate was unsatisfiable in CI.
- **Checkout follows `workflow_run.head_branch || ref_name`**, not a hardcoded
  `main`, so it can be exercised on a branch
  (`gh workflow run "Auto-fix a Picks Parse Failure" --ref <branch> -f sport=baseball`).
- **The push retry rebases onto the checked-out branch**, not `main` (a
  hardcoded main once replayed a whole branch and threw away a good fix). The
  other workflows keep literal `main` on purpose; F6/F7 pin both sides, and F6
  strips comment lines before its negative grep.
- **Failure detail includes the attempted diff**, built before the revert.
- **System-prompt rule 1b:** a new pattern must not match a line an existing
  template owns (the model once avoided the header trap and fell into the same
  trap with a lazy leg regex).
- **`clean_num()` accepts `$`** in both the MLB and football parsers.
- **The schema re-run in `verify_fix()`** caught a patch whose output had
  `"payout": "TBD"` (a string where a number is required) after every other
  gate passed.
- **No `temperature`** (any value is HTTP 400 on `claude-sonnet-5`);
  **`thinking` explicitly disabled** (left default it consumed the whole
  budget and returned no text, even at 32000 `max_tokens`).
- **Strip a matching markdown fence** from a whole-file response
  (`extract_file_and_summary()`); the model sometimes adds one despite
  instructions.
- **Search/replace, not whole files and not unified diffs** (2026-10-01):
  whole-file responses were truncated at ~16k tokens on two real incidents;
  diffs carry line numbers a model gets wrong in ways that still apply. A real
  run used 1662 output tokens and resolved on attempt 1.

## 14. Results archive and History pages

The live pages keep nothing; a daily job writes finished slates down.
- **`record_results.py`** → `data/results/<date>.json` (MLB): every bet as
  posted, each leg's result + MLB id + PHP credit + Statcast detail of his
  HRs, each bet's outcome and return, every HR in the league that day.
- **`import_history.py`** rebuilds `data/history.json` from the group's Google
  Sheet (older slates) plus `data/results/`. **On a date both cover, the
  tracker's record wins.** A dead sheet reuses the sheet parlays already in
  `history.json` with a warning. Refuses to shrink (`--allow-shrink`).
- **`record_football_results.py`** → `data/football/results/<weekEnds>.json`
  and `data/football/history.json` (football has no sheet).
- All run from **`import-history.yml`** daily at 9am ET (`cron: 0 13 * * *`),
  one job, one commit, each step `continue-on-error` with the job failed at the
  end. **9am, not "when Today rolls over"** — the rollover happens in visitors'
  browsers; catching it would need an evening-long cron (gotcha 3). The user
  asked and chose this; don't move it to an evening schedule. It commits only
  history/results files, never a live tickets file.
- A slate is recorded when every game is Final, or two days later regardless
  (`"complete": false`, re-graded later). A record carries a hash of its picks:
  same picks → skipped offline; corrected picks → re-graded. Re-running is
  always safe.
- **The graders are ports of the pages' grading**; a grading change on a page
  must be made in its recorder too, and vice versa (checked on the first real
  slate: all 50 legs and 36 league HRs matched).
- No All Sports or hidden-league archive yet (deferred).

**Sheet import facts** (`import_history.py`):
- Tabs `Archive` (gid 1001) + `HR Parlays` (gid 0). Never import "Solo
  Tracker" (invalid per the user).
- **Use `/export?format=csv&gid=N`, never `/gviz/tq?tqx=out:csv`** — gviz drops
  hidden rows (~2,000 of them).
- Compute every stat from raw legs; the sheet's "Player Stats" tab matches by
  substring ("Cruz" counts "Oneil Cruz").
- The sheet mis-dates slates: `drop_misdated_copies()` drops a sheet slate ±1
  day from a recorded one when 80%+ of its (bettor, odds) legs match. Its
  hand-typed marks have errors the tracker doesn't; disagreements are reported
  and footnoted, never silently "fixed".
- "The ones that got away" joins real MLB game logs at import time via the
  player map; recorded players join it by the id they were graded with.
- Names: the sheet uses shorthand ("Judge", "PCA"); `history_player_map.json`
  (nickname → MLB id, hand-reviewed) ties them. **Never auto-resolve nicknames**
  ("Lowe", "Muncy" are ambiguous) — use `--draft-map` and review.
- Real money from the sheet is NOT derivable (stakes never filled in); the
  page labels its flat-stake ROI hypothetical. Recorded bets (`src: "site"`,
  `stake`, `payout`, `returned`, `won`, `book`, `kind`) power the **Real
  money** tile. Odds only from 2026-08-20 (`oddsFrom`). Blank status =
  `pending`; DNP legs are void.
- `dump()` writes no timestamp, so an unchanged sheet produces no commit.

**History pages are isolated (hard user constraint).** `history/index.html`
must never touch or risk the live tracking: own script, own data, never polls
MLB or reads tickets files, and `mlb/index.html` never reads `history.json`.
Coupling is footer links only (MLB → `/history/`; history → `/`;
`football/history/` ↔ `/football/` and "MLB history"). Don't refactor shared
helpers out "for reuse" — duplication is the point. `test_history.py` block A
enforces it; if it fails, the change is wrong. Both live in `<name>/index.html`
because GitHub Pages serves a sibling `name.html` for a bare `/name` first.

## 15. `tickets.json` schema

```json
{
  "date": "2026-09-18",
  "note": "free text, shown at top of page (parser warnings)",
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

`payout` may be null (card said "TBD"/"N/A"); `odds` may be null (priceless
legs). Optional fields:

| Where | Field | Meaning / written by |
|---|---|---|
| leg, single | `market` | registry key (§5); absent = sport default |
| leg, single | `line`, `side` | prop line; `side` omitted when "over" |
| leg, single | `time` | first pitch / kickoff ("TIME ET") |
| leg | `players[]` | a leg naming several players (graded combined) |
| leg | `opponent` | the other team the card named (MLB matchup check; two-team NHL/NBA lines) |
| leg | `mismatch` | `flag_matchups()` text when the MLB schedule contradicts the matchup |
| leg | `innings` | N for an `f5` total |
| leg | `sport` | `nfl` / `cfb` / `nhl` / `nba` / `wnba` (combined/league parsers; absent = MLB) |
| leg | `athleteId`, `athleteIds` | ESPN ids (football and combined parsers) |
| leg | `teams` | both teams of a two-team bet (game lines, `quarters`) |
| leg | `quarter` | the quarter of a `q_score` leg |
| top | `endDate` | last date of the span (football, combined, league wrappers) |
| top | `weekEnds`, `sport` | NFL only: the week's Tuesday; `"football"` |
| top | `sports[]` | which leagues a combined/league slate covers |

`tests/test_live_data_schema.py` validates the committed files against this.
The page writes `name`, `sub`, `title`, `foot` and `meta` into HTML: `foot` and
`meta` are prebuilt HTML by design; `who` is plain text and escaped.

## 16. Known gotchas — don't repeat them

1. **Never touch `data/` in a code change unless explicitly asked.** Sample
   data shipped in delivery zips once overwrote real picks several times.
   Every sport's `tickets.json`, `tickets-previous.json` and
   `incoming_picks.txt` (`data/` and its `football/`, `combined/`, `hockey/`,
   `basketball/`, `wnba/`, `cfb/` twins) are live data, managed only by the
   upload → Actions pipeline; history/results files are pipeline data. Code
   changes touch only: the tracker pages (`index.html`, `mlb/`, `football/`,
   `hockey/`, `basketball/`, `wnba/`, `cfb/`), `all/index.html` (a stub),
   `features.html` (a stub), `history/`, `football/history/`, `features/`,
   `scripts/`, `.github/workflows/`, `discord-bot/`, `tests/`, `README.md`,
   `CLAUDE.md`, `CNAME`, `.gitignore`. `test_live_data_schema.py` is the one
   deliberate exception: it READS `data/`, never writes.
2. **Dates are tricky — games run past midnight.** Rollover is by game state
   only (§3). The only clocks are the 6am-next-day backstop and the NFL
   6am-after-`endDate` backstop.
3. **No cron that commits.** A legacy 10-minute cron raced the parse workflows
   and the bot's read-sha-then-write; it was deleted 2026-09-18. The daily
   9am archive is the one user-chosen exception. For a rejected local push:
   `git pull --rebase origin main`, then push.
4. **The user works in PowerShell on Windows**, sometimes Git Bash or a
   Codespace. Infer the shell before giving commands; `git remote -v` confirms
   you're in the real repo.
5. **Only the parse workflows write tickets files** (or a direct edit at the
   user's explicit request). Never regenerate one from test data — test with
   mocked fixtures in a headless browser or a temp dir.

## 17. Testing

No framework: `tests/` holds plain scripts that exit non-zero on failure.
Page tests run Playwright (Python) headless Chromium, serving the page through
one `page.route("**/*")` handler with in-memory fixtures and a pinned clock,
aborting anything unexpected so a missed host fails loudly. Everything is
offline except `test_feed_fields.py` and `test_notify_discord_live.py`.

**Mutation-test new checks**: break the code in a scratch copy and confirm the
check fails. It has repeatedly exposed checks that couldn't fail (a filter
group holding one value, a fixture whose pick already scored, a tidy fixture
`player` hiding a garbled real shape, an absence assertion). **In
`test_record_results.py` every check must sit ABOVE the `if failures:` block**
— a section appended after it ran, printed, and could never fail. If a new
section passes its mutations suspiciously badly, check where it sits.

- `test_parser.py` — `parse_picks.py`: every template (§11 table), slate
  date, archive guard (temp dir), the unread-line safety net end to end,
  `resolve_player()`'s three branches, markets, start-time fill, footers.
- `test_page.py` — `mlb/index.html`: slate rollover/queue/backstop, filters
  (P bettor filter clicked end to end, S multi-select + summary bar), O note
  banner (empty when no warning), Q Pacific timezone, R collapsed lists /
  benched-is-void / status lines / leg chips, T header order, U warmup and
  delays, V chip rows / Irons / void as N/A (V4 "VOID" label) / Expand all,
  W sound, X unknown payouts, Y markets, AB team-bet crash.
- `test_live_at_bats.py` — the MLB Live Bet Tracker walked poll by poll:
  results, stale at-bats, J/K inning breaks, Z tile kinds (Z8b/Z8c Gameday
  links and f5), AA every-leg alerts and walk-offs, AB leadoff from slot 1.
- `test_steals.py` — steal grading, mixed HR+steal tickets, steal alerts and
  tiles, minus-money odds.
- `test_at_bat_math.py` — `negBinomPmf()` / `atBatEstimate()` /
  `remainingOutsForSide()` against an independent Python reference.
- `test_combined_page.py` — the front page: A3/A4 the engine seal, C mixed
  parlays, D/E/G rollover, F front-page layout (switch hidden, no tracker
  links, h1, eyebrow, hidden panels still running), H both sports' tiles, I/J/K
  alerts, Q/R/T football props and quarter bets, S every-leg alerts, U leadoff
  and foreign markets.
- `test_combined_parser.py` — `parse_combined_picks.py` offline: sport
  decision and its order, collisions, glued markets, span (I4/I5), archive,
  L/M priceless legs and the first real card, N quarters, O bare counts,
  P hockey (P13b wrapper paths), Q basketball, R WNBA, S college.
- `test_bell.py` — the bell on the MLB, NFL and All Sports pages in ONE browser
  context (shared origin): backfill, leg/bet split, dedupe, persistence, slate
  reset, morning after, G1 Pacific, I replay, J hit times, E10 namespaces,
  B5c/B5d class collision.
- `test_hockey.py` — `hockey/index.html` and hockey on the front page: A3
  hidden, A4 `bmbs.hk.*`, A5 no MLB requests, grading, tiles (C9 missing
  `onIce` → LIVE), goal alert/log/bell, the final.
- `test_basketball.py` — `basketball/index.html` and the front page: A3 hidden,
  `bmbs.bb.*`, no other leagues' requests, every market, tiles, alerts, final,
  G both leagues on one card.
- `test_wnba.py` — runs `test_basketball.py` with `HOOPS_LEAGUE=wnba`.
- `test_cfb.py` — `cfb/index.html` and the front page: A2 hidden, A3
  `bmbs.cf.*`, college host only, unpicked games never fetched, D3/D4 the
  starter rule, E college vs NFL "MIA".
- `test_football.py` — `football/index.html`: a Sunday+Monday slate poll by
  poll, A isolation/market tags, H8-H14 bettor filter, I alerts (I8/I9 final
  whistle), K Pacific timezone, L/M/N/P twins of the MLB page's R/S/T/V (L
  rebuilds `TICKETS` first because J trims it), O the finished-drive bug, Q
  sound.
- `test_football_parser.py` — football templates, NFL schedule dating, archive
  guard, section 5 roster builder (merge), section 6 the bot's `route_for()`,
  fuzzy resolution.
- `test_record_results.py` — the MLB recorder against a fake MLB, and the
  merge into `history.json`.
- `test_football_history.py` — the football recorder, its history file, and
  `football/history/index.html`.
- `test_history.py` — `history/index.html`: block A isolation, every stat,
  filters, degraded data, phone width.
- `test_history_import.py` — the importer offline (inline CSV tabs, fake MLB,
  shrink guard).
- `test_build_roster.py` — `build_roster.py` and `merge_rosters()` offline.
- `test_live_data_schema.py` — READS the committed `data/` files: every
  sport's tickets files (absence is a skip), every roster (team counts NHL 32,
  NBA 30, WNBA 15, CFB 138; per-player maps share a key set), both histories,
  results files.
- `test_smoke_pages.py` — every tracker and both history pages at their REAL
  URLs: exactly their own data requests, no page errors, the expected `<h1>`;
  F2 all `FEED_FIELDS` copies identical.
- `test_site_links.py` — every internal `href` on every HTML page (globbed)
  resolves, with a resolver that mirrors GitHub Pages' sibling-`.html` rule;
  C1-C4 the hidden trackers (front page links none; each hidden tracker links
  only itself, the front page, its history and features; features' All Sports
  section links none).
- `test_redirect_stub.py` — `features.html`'s meta refresh targets `/features/`
  and its script keeps query and hash.
- `test_features_page.py` — `features/index.html` mechanics (toggle, URL sync,
  deep links, isolation), not its copy.
- `test_workflow_yaml.py` — the workflows via targeted regexes: script paths,
  trigger paths, **commit lanes** (no workflow commits outside its own files;
  the archive job never touches a tickets file), Discord gating on
  `committed == 'yes'`, B3m failure notices, E auto-fix routing, F
  continue-on-error / rebase target, G mentions, H `--before`.
- `test_discord_bot.py` — reaction flow, Contents API push (identical-content
  no-op), `on_message` gating, C7b refusal lists every prefix. Builds a real
  unconnected `discord.Client` and monkeypatches `requests`.
- `test_notify_discord.py` — message text, request shapes, CLI, the webhook URL
  never leaking, E corrections, F all seven sports consistent with the bot and
  date spans.
- `test_auto_fix_parser.py` — every failure path leaves the parser
  byte-for-byte original; retry feedback; edits; S1 the suite is every offline
  test.
- `test_feed_fields.py` — **network**: full vs slim MLB feed for every live
  game.
- `test_notify_discord_live.py` — **network**: hits Discord with a bogus
  webhook id (posts nothing).

```
pip install -r tests/requirements.txt
python -m playwright install chromium
python tests/test_parser.py && python tests/test_page.py && python tests/test_live_at_bats.py && python tests/test_steals.py && python tests/test_at_bat_math.py
python tests/test_combined_parser.py && python tests/test_combined_page.py && python tests/test_bell.py
python tests/test_hockey.py && python tests/test_basketball.py && python tests/test_wnba.py && python tests/test_cfb.py
python tests/test_football_parser.py && python tests/test_football.py && python tests/test_football_history.py
python tests/test_record_results.py && python tests/test_history_import.py && python tests/test_history.py && python tests/test_build_roster.py
python tests/test_live_data_schema.py && python tests/test_smoke_pages.py && python tests/test_site_links.py && python tests/test_redirect_stub.py && python tests/test_features_page.py
python tests/test_workflow_yaml.py && python tests/test_discord_bot.py && python tests/test_notify_discord.py && python tests/test_auto_fix_parser.py
# network:
python tests/test_feed_fields.py           # run after editing FEED_FIELDS
python tests/test_notify_discord_live.py
```

Windows: `venv` fails on very long paths, and Windows Python has no tz database
(`tzdata` is in the requirements). Keep using this pattern for any nontrivial
change rather than shipping unverified.

## 18. Deploy and the daily flow

- **Code:** edit the pages/scripts directly, commit, push. GitHub Pages
  deploys in about a minute (it has sat queued 10+ minutes).
- **`features/index.html` is maintained automatically** as part of any change
  that adds, removes or visibly alters a feature. Scope is strict: only
  current, live, user-visible features — never a bug fix, refactor, pipeline
  change or anything a visitor can't see. Same voice and shape as what's
  there (colour key, one short paragraph per feature). The All Sports section
  includes a "Reading the page" card after "One card, every sport". Hidden trackers and hidden panels are
  not described.
- **Picks:** a friend uploads `<prefix>_*.txt` in Discord (§12) or pastes into
  that sport's `incoming_picks.txt` in GitHub's web editor → its parse workflow
  writes `tickets.json` (archiving the previous slate if the date/week
  changed) and posts to Discord → pages pick it up on their next poll. The
  front page is fed by `sports_*.txt` → `data/combined/`.
