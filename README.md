# BMBS Tracker — bmbs.bet

A live tracker for a friend group's betting card: parlays and straight bets
across MLB, NFL, college football, NHL, NBA and WNBA, graded in each
visitor's own browser straight from the free public MLB Stats API and ESPN.
No backend, no build step, no server-side job — static pages on GitHub
Pages, with the custom domain `bmbs.bet`.

Project internals, design decisions and the gotchas already learned the hard
way are in [`CLAUDE.md`](CLAUDE.md). This file covers what the site is and
how to run it.

## The pages

| URL | File | What |
|---|---|---|
| `/` | `index.html` | **All Sports** — the front page. One card holding any mix of sports, mixed parlays included. |
| `/mlb/` | `mlb/index.html` | MLB-only tracker (home runs, steals, props, game lines) |
| `/football/` | `football/index.html` | NFL-only tracker, by the week |
| `/hockey/`, `/basketball/`, `/wnba/`, `/cfb/` | as named | NHL, NBA, WNBA and college-football trackers |
| `/history/`, `/football/history/` | as named | the group's whole record, MLB and NFL |
| `/features/` | `features/index.html` | plain-language "what this site can do" |

Every single-sport tracker is **hidden**: complete and working at its URL,
but not linked from the front page. `all/index.html` and `features.html` are
redirect stubs kept so old links keep working.

## Each day: getting a card onto the site

Upload the card as a `.txt` file in the group's Discord intake channel and
confirm with a 👍 — see [`discord-bot/README.md`](discord-bot/README.md). The
**file name** picks the sport:

| File name starts with | Goes to | Shows on |
|---|---|---|
| `sports` | `data/combined/incoming_picks.txt` | bmbs.bet |
| `baseball` | `data/incoming_picks.txt` | bmbs.bet/mlb/ |
| `football` | `data/football/incoming_picks.txt` | bmbs.bet/football/ |
| `hockey` | `data/hockey/incoming_picks.txt` | bmbs.bet/hockey/ |
| `basketball` | `data/basketball/incoming_picks.txt` | bmbs.bet/basketball/ |
| `wnba` | `data/wnba/incoming_picks.txt` | bmbs.bet/wnba/ |
| `cfb` | `data/cfb/incoming_picks.txt` | bmbs.bet/cfb/ |

Or paste the card into that file with GitHub's web editor (✏️, then
**Commit changes** to `main`). Either way, the commit triggers that sport's
`.github/workflows/parse-*-picks.yml`, which parses the card into the
sport's `tickets.json`, archives the previous slate to `tickets-previous.json`
when the card is for a new day (or NFL week), commits, and posts "picks are
now LIVE" to Discord, @-mentioning whoever uploaded. A correction to a slate
that is already live is announced as an update, with what changed.

The site picks up a new slate on its next poll once GitHub Pages has
deployed — usually about a minute, occasionally longer.

**If a card doesn't parse** (the group's card generator changes its format
without warning — the MLB parser alone knows a dozen shapes):
- MLB, NFL and All Sports cards go to `.github/workflows/auto-fix-parse-failure.yml`,
  which asks Claude for a bounded parser fix, commits it only if the whole
  offline test suite still passes and the card now parses, and reports back
  in Discord either way. It needs the `ANTHROPIC_API_KEY` repo secret.
- NHL, NBA, WNBA and college cards have no auto-fixer; their workflow posts
  a "couldn't be processed" message instead.

A line on a card that looks like a bet but can't be read is never dropped
silently: the slate still posts, and a "Heads up" note at the top of the page
says what was skipped.

### Repo secrets

| Secret | Used for |
|---|---|
| `DISCORD_STATUS_WEBHOOK` | the status messages (Discord channel → Integrations → Webhooks); every sport shares it |
| `ANTHROPIC_API_KEY` | the auto-fixer |

## How live tracking works

Each tracker polls its data **from the visitor's browser** — the MLB Stats API
every 10 seconds (the API's own cache floor; polling faster gains nothing),
ESPN every 10–15 seconds — and pauses while the tab is hidden. Every viewer
computes the same results independently from the same public data. Both APIs
are free, keyless and allow browser requests; they are also unofficial, so if
results stop updating, check the browser console (F12) for fetch errors first.

Slates follow **game state, not the clock**: a card stays on Today's Picks
until every game on it is final (a 10 pm game that runs past midnight keeps
tracking), then moves to Yesterday's Picks. An NFL slate is a whole week, and
stays until Monday night's game ends.

Each leg is one of: **Hit**, **Missed**, **N/A** (didn't play — void, the bet
is repriced without it), **Live**, **Not started**, or **not tracked** (a bet
type the site can't grade — shown, never guessed at, and it can't cash its
parlay). A player who was on the roster but never batted is N/A, not a miss.
MLB home run legs also get **Pinch Hit Protection**: if he's pinch-hit for and
whoever holds his lineup spot homers, the leg is credited.

## Rosters

Player names on a card are matched against `data/roster.json` (MLB) and each
league's `data/<sport>/roster.json`. They don't update themselves; when a
picked player's name won't resolve, rebuild that league's roster:

```
python scripts/build_roster.py                          # MLB
python scripts/build_football_roster.py                 # NFL
python scripts/build_hockey_roster.py                   # NHL
python scripts/build_basketball_roster.py               # NBA
python scripts/build_basketball_roster.py --league wnba # WNBA
python scripts/build_cfb_roster.py                      # college (FBS); weekly in season
```

A rebuild **merges** with the committed roster and never drops anyone (a
player on the injured list is missing from the APIs' active rosters);
`--replace` starts over.

## History and the results archive

The live pages remember nothing, so once a day at 9 am ET
`.github/workflows/import-history.yml` writes finished slates down:

1. `scripts/record_results.py` grades each finished MLB slate into
   `data/results/<date>.json` — every bet, stake, payout, leg result, home run
   distance and Pinch Hit Protection credit, kept forever.
2. `scripts/import_history.py` rebuilds `data/history.json` from the group's
   Google Sheet (older slates) plus those records; where both cover a date,
   the tracker's record wins.
3. `scripts/record_football_results.py` does the same per NFL week into
   `data/football/results/` and `data/football/history.json`.

`/history/` and `/football/history/` read those files and nothing else.
Re-running the workflow by hand from the Actions tab is always safe. When a
player has been picked five or more times, give them an entry in
`scripts/history_player_map.json` (`python scripts/import_history.py --draft-map`
proposes entries to review) so they appear under "the ones that got away".

The other leagues and the All Sports card have no archive yet.

## Tests

Plain Python scripts under `tests/` (most drive the pages in headless
Chromium with fixture data; nothing touches `data/`). Setup and the full
list of commands are in [`CLAUDE.md`](CLAUDE.md) under "Testing".

## Hosting (one-time setup, already done)

GitHub Pages serves `main` from the repo root (Settings → Pages). The domain
is on Namecheap: four A records for `@` pointing at GitHub Pages
(185.199.108.153, .109.153, .110.153, .111.153) and a `www` CNAME to the
`github.io` host. The `CNAME` file in the repo holds `bmbs.bet`; HTTPS is
enforced in the Pages settings.
