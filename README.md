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
  (built from your uploaded MLB roster CSV — closest match wins)
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

### If the picks format changes
`scripts/parse_picks.py`'s docstring documents the exact expected shape.
`test_picks.txt` in this repo is a full real example you can diff against.
The markdown markers (`## ` on section headers, `* ` on item lines) are
optional — text copied out of a rendered Gemini response has them
stripped, and both forms parse identically.
If a new day's format doesn't match (a new section type, a reworded footer
line), the parser will likely under-count or print a WARNING — paste the
new format to Claude and ask for the parser to be updated to match.

### Updating the roster
`data/roster.json` is built once from your uploaded CSV and checked into
the repo — it doesn't update itself. If MLB rosters change (trades,
call-ups) enough to matter, give Claude an updated CSV and ask it to
regenerate `data/roster.json`.

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

Per leg, three states:
- **Hit (green check):** the player's name has appeared in a home-run play
  in today's play-by-play, in any game — shown the instant it happens,
  regardless of whether that game has finished.
- **Miss (red X):** the player appeared in a game's boxscore roster, that
  specific game is Final, and they're not in the home-run list.
- **N/A (yellow):** the player has not appeared in *any* MLB boxscore
  today, and every game scheduled today is Final — meaning they didn't
  play anywhere. Shown as "Did not play"; the ticket treats it as void
  for that leg rather than a loss.
- Anything not yet meeting one of the above stays in the neutral
  "in progress" state.

10 seconds is as fast as this can usefully go: the API caches responses
for 10s (`Cache-Control: max-age=10`), tells clients to wait 10s
(`metaData.wait`), and in practice only republishes a game's feed every
~18-20s. To keep that affordable, each feed is requested with a `fields=`
allow-list that trims it from ~104KB gzipped to ~10KB.

This relies on `statsapi.mlb.com` allowing unauthenticated, CORS-open
browser requests (confirmed, no key or proxy needed). It's an unofficial,
undocumented API and could change without notice — if results stop
updating, check the browser console on the live page first (F12 → Console)
for fetch errors before assuming the parsing logic is wrong.

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

