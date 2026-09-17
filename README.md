# Home Run Checklist — auto-updating pipeline

Static page (`index.html`) that reads `data/tickets.json` (your slate) and
`data/marks.json` (live hit/miss results), the latter regenerated on a
schedule by `scripts/fetch_home_runs.py` from the free MLB Stats API
(`statsapi.mlb.com`, no key required).

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
Confirm that loads and shows the checklist (with all "LIVE / pending" marks,
since `marks.json` is still the placeholder) before moving to DNS.

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

## 4. Turn on the scheduled updates

The workflow at `.github/workflows/update-checklist.yml` is already wired
to run automatically every 10 minutes during typical game hours (6pm–2am ET,
covering both EDT/EST via two cron entries) once this is pushed to GitHub —
no extra setup needed. It writes to `data/marks.json` and commits only when
something changed.

To trigger it manually (e.g. to test right now): repo → **Actions** tab →
"Update Home Run Checklist" → **Run workflow**.

## 5. Each day: paste new picks (no terminal, no git commands)

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
- Regenerates `data/tickets.json` in the exact shape `index.html` expects
- Resets `data/marks.json` to blank/pending for the new slate
- Commits both, live within about a minute

Check the Actions tab if something looks off after committing — the parser
prints a summary (`Parsed N singles, M parlay cards...`) and a `NOTE:` line
for any player name it couldn't match in the roster even with fuzzy
matching, so you can fix a typo and re-commit.

Expected: the very first push of this whole repo (with `incoming_picks.txt`
still holding its placeholder text) will show a **failed** run for this
workflow — that's correct, the placeholder deliberately parses to nothing.
It goes away the first time you paste real picks in and commit.

### If the picks format changes
`scripts/parse_picks.py`'s docstring documents the exact expected shape.
`test_picks.txt` in this repo is a full real example you can diff against.
If a new day's format doesn't match (a new section type, a reworded footer
line), the parser will likely under-count or print a WARNING — paste the
new format to Claude and ask for the parser to be updated to match.

### Updating the roster
`data/roster.json` is built once from your uploaded CSV and checked into
the repo — it doesn't update itself. If MLB rosters change (trades,
call-ups) enough to matter, give Claude an updated CSV and ask it to
regenerate `data/roster.json`.

## How matching works

`scripts/fetch_home_runs.py`:
1. Gets today's MLB schedule and game IDs.
2. Pulls the live feed for each game, which includes both the play-by-play
   (to see who's homered) and the full boxscore roster for both teams.
3. For each leg in `tickets.json`, marks it by **player name**, not by the
   `team` field:
   - `hit` if that player's name is in today's home-run list (any game)
   - `miss` if that exact player's name appears in a boxscore whose game is
     Final and they're not in the home-run list
   - `pending` otherwise (their game is still in progress, hasn't started,
     or they don't appear in any boxscore yet)
4. Writes the result to `data/marks.json`.

The `team` field in `tickets.json` is kept only for display/reference, the
matching logic no longer depends on it being correct. This was changed after
an early version relied on the `team` field to know when to check a player's
game, and a few of those hand-entered team codes turned out to not match the
players' actual real-world rosters, which silently prevented some "Final"
games from ever marking their leg as a miss.

Name matching is by normalized full name (accents/punctuation stripped). If
a player's listed name doesn't exactly match their MLB roster name
(nicknames, suffixes, a name that's simply wrong), that leg will stay
`pending` indefinitely and the workflow log will print a `NOTE:` line for
it — check the Actions tab run log if a result looks wrong; it also prints
hit/miss/pending totals every run.

## Notes / limitations

- This uses the free MLB Stats API, not Sportradar. It's the same underlying
  Statcast data, just without a paid contract.
- Marks.json is the single source of truth for everyone viewing the page —
  this is a shared board, not per-device like the earlier Claude artifact
  version.
- If GitHub Actions' schedule feels too infrequent or too frequent during
  games, adjust the `cron` lines in the workflow file (`*/10` = every 10 min).
- The Stats API endpoint used here (`/v1.1/game/{gamePk}/feed/live`) is
  public but undocumented/unofficial. It's stable and widely used by the
  open-source baseball community, but MLB could change it without notice.
