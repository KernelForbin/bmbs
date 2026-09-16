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

## 5. Each day: update data/tickets.json

`data/marks.json` is fully automatic. `data/tickets.json` is your slate and
is NOT automatic — edit it each day with that night's tickets (same
structure as the existing entries: `id`, `player`, `team` [MLB abbreviation,
important for matching], `meta`, `odds`, `time`). Commit and push; the page
picks it up on next load.

## How matching works

`scripts/fetch_home_runs.py`:
1. Gets today's MLB schedule and game IDs.
2. Pulls the live play-by-play feed for each game and collects every batter
   who has hit a home run so far today.
3. For each leg in `tickets.json`, marks it:
   - `hit` if that player's name is in today's home-run list
   - `miss` if that player's team's game is Final and they didn't homer
   - `pending` otherwise (game in progress or not yet started)
4. Writes the result to `data/marks.json`.

Name matching is by normalized full name (accents/punctuation stripped).
If a player's listed name doesn't exactly match their MLB roster name
(nicknames, suffixes), that leg may stay `pending` — check the workflow's
run log in the Actions tab if a result looks wrong, it prints hit/miss/pending
counts each run.

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
