# Home Run Checklist — auto-updating pipeline

Static page (`index.html`) that reads `data/tickets.json` (your slate) and
computes live hit/miss results in the visitor's own browser, straight from
the free MLB Stats API (`statsapi.mlb.com`, no key required). No backend,
no build step, no server-side job.

## 1. Push this to your GitHub repo

From inside this folder:

```bash
git init
git add .
git commit -m "Home run checklist pipeline"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 2. Turn on GitHub Pages

In the repo: **Settings → Pages**
- Source: "Deploy from a branch"
- Branch: `main`, folder `/ (root)`
- Save.

GitHub will build a URL like `https://<your-username>.github.io/<your-repo>/`.
Confirm that loads and shows the checklist before moving to DNS.

## 3. Point bmbs.bet at GitHub Pages (Namecheap)

In Namecheap → Domain List → **bmbs.bet** → Manage → Advanced DNS, add:

| Type | Host | Value |
|---|---|---|
| A Record | @ | 185.199.108.153 |
| A Record | @ | 185.199.109.153 |
| A Record | @ | 185.199.110.153 |
| A Record | @ | 185.199.111.153 |
| CNAME Record | www | `<your-username>.github.io.` |

(Those four A records are GitHub Pages' fixed IPs.)

Back in **Settings → Pages** on GitHub, under "Custom domain," enter
`bmbs.bet` and save — this is what writes the `CNAME` file's content into
Pages' config (the `CNAME` file already in this repo does the same thing,
so you likely just need to confirm it matches). Check "Enforce HTTPS" once
the certificate provisions (can take up to ~24 hrs, usually much faster).

DNS propagation is typically 15 minutes to a few hours.

## 4. Each day: paste new picks (no terminal, no git commands)

1. On GitHub.com, open your repo → `data/incoming_picks.txt`.
2. Click the pencil (✏️) icon to edit.
3. Select all, delete, paste in that day's full picks text (same format as
   your messages to Claude: Longshot Straight Bets, N-Leg Parlay Cards, etc).
4. Scroll down, click **Commit changes** (commit directly to `main`).

That's it. Committing this file automatically triggers
`.github/workflows/parse-picks.yml`, which:
- Runs `scripts/parse_picks.py` on what you pasted
- Normalizes every player name and team against `data/roster.json`
  (built from the MLB Stats API by `scripts/build_roster.py` — exact match
  first, then closest match)
- Regenerates `data/tickets.json` in the exact shape `index.html` expects,
  stamped with the MLB game date the slate is for
- Archives the previous slate to `data/tickets-previous.json` if that date
  changed, which is what fills the "Yesterday's Picks" tab
- Commits, live within about a minute

Check the Actions tab if something looks off after committing — the parser
prints a summary (`Parsed N singles, M parlay cards...`) and a `NOTE:` line
for any player name it couldn't match in the roster even with fuzzy
matching, so you can fix a typo and re-commit.

Expected: the very first push of this whole repo (with `incoming_picks.txt`
still holding its placeholder text) will show a **failed** run for this
workflow — that's correct, the placeholder deliberately parses to nothing.
It goes away the first time you paste real picks in and commit.

Alternatively, a friend can upload the day's picks as a `.txt` file in the
Discord intake channel and confirm with a reaction — see
`discord-bot/README.md`. Either route ends up in the same
`incoming_picks.txt` → workflow → `tickets.json` pipeline.

### Stolen base bets
A leg is a home run bet unless the card marks it as a steal -- write `SB` (or
"Stolen Base" / "Steal") on the leg's line, on the ticket's header, or on a
section header, e.g. `* (Kenny) Elly De La Cruz - CIN (-120) SB - 6:40 PM ET`.
One ticket can mix home run and steal legs. Steal legs show a STEAL tag, get
their own alert, and appear in the Live Bet Tracker while the player is on base.
Rules: a steal is a hit the moment it happens; there is no Pinch Hit Protection
for steals; and a player who never got into the game is void.

### If the picks format changes
`scripts/parse_picks.py`'s docstring documents the exact expected shape(s) —
the group's picks-generation prompt has changed template three times already
(most recently 2026-09-20), each time with no warning, and the parser now
accepts all four. `test_picks.txt` in this repo is a full real example of
the original one.
If a new day's format doesn't match any of them, the parser exits non-zero
with `WARNING: parsed nothing` and the Action fails loudly rather than
silently posting an empty/wrong slate — check the Actions tab, grab the raw
text from that failed run's upload, and paste it to Claude to add support
for the new shape (a fifth template is just another regex pair, same as
the last three).

### Discord status messages
When a real upload parses successfully, the workflow posts one plain
"picks are live" message to the group's Discord channel via an incoming
webhook (Channel Settings → Integrations → Webhooks in Discord). Set that
webhook's URL as the `DISCORD_STATUS_WEBHOOK` repo secret (Settings →
Secrets and variables → Actions) — both sports share the one secret. **A
parse failure does not currently post anything to Discord** — that half
(auto-investigate-and-fix on failure) was designed but never wired up; see
CLAUDE.md's "Discord status automation" section for why and what's needed
to finish it. Until then, a failed upload still only shows up on the
Actions tab, exactly as before.

### Updating the roster
`data/roster.json` doesn't update itself — it's a snapshot. Refresh it any
time with:

```
python scripts/build_roster.py
```

This pulls every team's current active roster straight from the MLB Stats
API (no CSV, no upload) and rebuilds the file. Worth re-running after
trades or September call-ups if a newly-added player gets picked and the
site can't find them.

## How matching works

For each game on the slate's date, `index.html` pulls the live feed, which
carries both the play-by-play (who has homered) and the full boxscore
roster for both teams. Legs are then matched by **player name**, not by the
`team` field.

The `team` field in `tickets.json` is kept only for display/reference; the
matching logic does not depend on it being correct. This was changed after
an early version relied on the `team` field to know when to check a player's
game, and a few of those hand-entered team codes turned out to not match the
players' actual real-world rosters, which silently prevented some "Final"
games from ever marking their leg as a miss.

Name matching is by normalized full name (accents/punctuation stripped). If
a player's listed name doesn't exactly match their MLB roster name
(nicknames, suffixes, a name that's simply wrong), that leg never resolves —
`scripts/parse_picks.py` guards against this by normalizing every name
against `data/roster.json` at parse time and printing a `NOTE:` line for
anything it couldn't match, so check the Actions tab run log if a result
looks wrong.

## Live home run tracking (near-instant, client-side)

`index.html` polls the MLB Stats API **directly from each visitor's
browser** every 10 seconds while the tab is open (pausing when the tab
isn't visible, to be a good citizen of a free public API). This replaced an
earlier design that relied on a GitHub Actions cron job committing results
every 10 minutes; that approach was removed from the repo on 2026-09-18.
The client-side version is faster (~10s vs 10+ min) and needs no
server-side moving parts at all.

Polling is keyed on the slate's own date rather than the wall clock, so a
10pm game that runs past midnight keeps tracking. The slate only moves from
the "Today's Picks" tab to "Yesterday's Picks" once every game on its date
is final; Today then waits for the next upload.

Per leg, five states:
- **Hit (green check):** the player's name has appeared in a home-run play
  in today's play-by-play, in any game — shown the instant it happens,
  regardless of whether that game has finished.
- **Miss (red X):** the player appeared in a game's boxscore roster, that
  specific game is Final, and they're not in the home-run list.
- **N/A (yellow):** the player has not appeared in *any* MLB boxscore
  today, and every game scheduled today is Final — meaning they didn't
  play anywhere. Shown as "Did not play"; the ticket treats it as void
  for that leg rather than a loss.
- **Live:** his game is in progress and he hasn't gone deep yet.
- **Not started:** nothing has resolved for him yet — no game of his has
  begun, or the first poll hasn't landed.

10 seconds is as fast as this can usefully go: the API caches responses
for 10s (`Cache-Control: max-age=10`), tells clients to wait 10s
(`metaData.wait`), and in practice only republishes a game's feed every
~18-20s. To keep that affordable, each feed is requested with a `fields=`
allow-list that trims it from ~104KB gzipped to ~15KB.

This relies on `statsapi.mlb.com` allowing unauthenticated, CORS-open
browser requests (confirmed, no key or proxy needed). It's an unofficial,
undocumented API and could change without notice — if results stop
updating, check the browser console on the live page first (F12 → Console)
for fetch errors before assuming the parsing logic is wrong.

## Football: touchdown parlays (`/football/`)

The football icon at the top of the site switches to a second tracker for
anytime-touchdown bets -- This Week's / Last Week's picks, payout estimate,
Irons, Bettor Tracker and alerts, with football's own panels: **Live Drives**
(who has the ball, down & distance, red zone) and a **Touchdown Log**. It is a
separate page (`football/index.html`) with its own data under `data/football/`
and its own parser; nothing about the baseball tracker depends on it.

Uploading works the same way, through the same Discord channel -- the **file
name** decides the sport:

| File name starts with | Goes to | Tracker |
|---|---|---|
| `baseball` | `data/incoming_picks.txt` | bmbs.bet |
| `football` | `data/football/incoming_picks.txt` | bmbs.bet/football |

Anything else is refused with a note asking for a rename. (Or paste a card
into `data/football/incoming_picks.txt` on GitHub, as with baseball.)

Football cards have their own set of accepted templates -- currently three, and
they don't have to match baseball's. Differences the parser handles: odds can
be negative (`-120`), players are stored with their ESPN athlete id so a
missing "Jr." can't break matching, and the slate is dated from the NFL
schedule -- so a Sunday card can go up on Friday. If a football upload parses
to zero, that's a new football template, not necessarily the same shape
baseball has already seen -- `scripts/parse_football_picks.py`'s docstring has
the exact shapes it currently handles.

Football runs by the **week**: the tabs are "This Week's Picks" and "Last
Week's Picks", and a card stays on This Week until the week's last game
(Monday night) is final -- even if nobody on it plays Monday. Uploading a
second card in the same week (say Thursday's, then Sunday's) replaces the
first, so put everything you still want tracked on the newer card.

Live data comes from ESPN's public NFL API, straight from each visitor's
browser, every 15 seconds. If the NFL roster changes enough that a new player
can't be found: `python scripts/build_football_roster.py`.

## The results archive (how history gets written)

The live pages grade picks in your browser and remember nothing, so once a day
(9am ET, `.github/workflows/import-history.yml`) the repo writes finished
slates down:

1. `scripts/record_results.py` grades any finished baseball slate into
   `data/results/<date>.json` -- one file per slate, kept forever: every bet
   with its stake and payout, every leg's result, home run distances, Pinch Hit
   Protection credits, and every home run hit in the league that day.
2. `scripts/import_history.py` rebuilds `data/history.json` from the group's
   sheet (older slates) **plus** those recorded slates. Where both have a date,
   the tracker's record wins -- so the sheet no longer has to be kept up.
3. `scripts/record_football_results.py` does the same per NFL week into
   `data/football/results/` and builds `data/football/history.json`, which the
   football History page (`/football/history/`) reads.

It runs in the morning rather than the moment a slate ends because there is no
server watching the games -- the site is static -- and a slate stays on the
Yesterday tab long enough that one run a day never misses one. To record sooner,
run the workflow by hand from the Actions tab; re-running is always safe.

One thing the archive gets right that the live page currently doesn't: a player
who sat on the bench all game is recorded as "didn't play" (void), where the
live page shows a miss.

## History page (`/history/`)

`history/index.html` is a separate, standalone page: the group's whole
record — bettor leaderboard, slate-by-slate hit rates, hit rate by odds
band vs break-even, player table, Heartbreakers, "the ones that got away",
and every parlay. It shares no code with `index.html`, never polls MLB and
never reads the tickets files. Its one data source is the static file
`data/history.json`.

That file is built by `scripts/import_history.py` from the group's Google
Sheet (the `Archive` and `HR Parlays` tabs), plus real MLB game logs for the
"got away" section. `.github/workflows/import-history.yml` runs it once a
day at 9am ET and commits the file only if it changed. To refresh it
sooner, run that workflow by hand from the Actions tab, or locally:

```
python scripts/import_history.py
```

The importer fails without writing if the sheet layout changes, MLB is
unreachable, or the result would have *fewer* parlays than the file already
committed (`--allow-shrink` overrides that last one on purpose).

When a new player gets picked five or more times, they need an entry in
`scripts/history_player_map.json` (group nickname → MLB player id) to show
up under "the ones that got away". `python scripts/import_history.py
--draft-map` proposes entries, scored against real home run dates — review
them before pasting in. Everything else on the page works without it.

## Notes / limitations

- This uses the free MLB Stats API, not Sportradar. It's the same underlying
  Statcast data, just without a paid contract.
- Live tracking is now computed independently in each visitor's own browser
  (see above) rather than from a shared committed file, so there's no
  single "source of truth" file to check if something looks off — open the
  browser console on the live page itself.
- The Stats API endpoints used here (schedule, and
  `/v1.1/game/{gamePk}/feed/live`) are public but undocumented/unofficial.
  They're stable and widely used by the open-source baseball community, but
  MLB could change them without notice.

