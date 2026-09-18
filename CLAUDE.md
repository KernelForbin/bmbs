# bmbs.bet — Home Run Card

A live-tracking site for a friend group's home run parlay/prop pool. Static
site on GitHub Pages, custom domain `bmbs.bet` via Namecheap DNS.

## Architecture

- **`index.html`** — the entire live site. Single self-contained file:
  inline CSS, inline JS. No build step, no framework, no dependencies.
  Deploy = commit this file, GitHub Pages serves it directly.
- **`data/tickets.json`** — the current day's parlay/single-bet picks, in a
  specific schema (see below). This is LIVE DATA, not code. Regenerated
  only by `scripts/parse_picks.py`, never hand-edited, never overwritten by
  a code deploy.
- **`data/incoming_picks.txt`** — the paste target. The person pastes the
  day's raw picks text here (via GitHub's web editor, no terminal needed)
  and commits. That triggers `.github/workflows/parse-picks.yml`, which
  runs `scripts/parse_picks.py` against it and regenerates `tickets.json`.
- **`data/roster.json`** — name/team lookup built once from an uploaded MLB
  roster CSV, used to normalize player names and teams during parsing.
- **`scripts/parse_picks.py`** — parses the raw picks text format into
  `tickets.json`. Also resolves player names against `roster.json` (exact
  match, then fuzzy).
- **`scripts/fetch_home_runs.py`** + **`.github/workflows/update-checklist.yml`**
  — LEGACY, no longer used. An earlier cron-based approach to live tracking
  that got replaced by client-side polling (see below). Left in the repo,
  safe to ignore or delete.
- **`CNAME`** — contains `bmbs.bet`, required by GitHub Pages for the custom domain.

## How live tracking actually works (important, don't reinvent this)

`index.html` polls the **MLB Stats API directly from the visitor's own
browser** every ~20 seconds — `statsapi.mlb.com`, which is free, keyless,
and has open CORS (confirmed working, not a guess). This is NOT a
server-side cron job. There is no backend. Every viewer's browser
independently computes hit/miss/live state from the same public data.

Per-leg states (five total): `hit`, `miss`, `na` (didn't play), `live` (game
in progress, no HR yet), `not_started`. A player is resolved to `hit` the
instant a HR appears in the live play-by-play, regardless of whether their
game has finished. A pinch-hit-for player resolves straight to `miss`
immediately (MLB has no re-entry rule, so this is provably correct, not a
guess) rather than waiting for the game to end.

Batting order tracking (current inning, "at the plate now" / "guaranteed to
bat this inning" tags, at-bats-remaining estimate) comes from the same live
feed's boxscore `battingOrder` field, combined with a negative-binomial
model using a league-average 68.5% out rate (NOT the specific hitter's real
stats — this is disclosed in the UI, don't remove that framing).

## tickets.json schema

```json
{
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
   copied stale `tickets.json`. `data/tickets.json`, `data/tickets-previous.json`
   (if it exists), and `data/incoming_picks.txt` are live user data, managed
   only through the picks-paste → GitHub Actions pipeline. Code changes
   should only ever touch `index.html`, `scripts/*.py`, `.github/workflows/*.yml`,
   `README.md`, `CNAME`.

2. **Date handling is genuinely tricky here — games run past midnight.**
   A "today/yesterday slate" tabs feature (with a `tickets-previous.json`
   archive file, a `date` field on tickets.json, and 3am ET cutover logic)
   was built and then **explicitly reverted** at the user's request back to
   a single-view, no-date-awareness version. Do not re-add tabs, a `date`
   field, or slate-archiving logic unless the user asks for it again — the
   current live version deliberately does not have it.

3. **Git push race conditions are common.** No cron bot is currently
   running (see legacy note above), but if any automated commit workflow
   is reintroduced, expect `git push` rejections when the user's local
   push lands near the same time as a bot commit. Standard fix:
   `git pull origin main` (or `--rebase`), resolve, `git push` again.

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

No test framework — verification has been done with Playwright (Python)
launching a headless browser, routing `fetch()` calls to mocked JSON
fixtures (schedule, live game feed, tickets.json), and asserting on
rendered DOM state. This works well for this project because the whole app
is client-side JS with no server to spin up. Recommend continuing this
pattern for any nontrivial change rather than shipping unverified.

## Deploy process

No build step. Edit `index.html` (or `scripts/*.py`) directly in the repo,
commit, push. GitHub Pages picks it up automatically within a minute or so.
The daily picks-update flow (separate from code deploys) is: paste text
into `data/incoming_picks.txt` via GitHub's web editor → commit → GitHub
Actions runs `parse_picks.py` → `tickets.json` updates → live site reflects
it on next poll cycle (~20s).
