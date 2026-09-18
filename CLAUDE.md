# bmbs.bet — Home Run Card

A live-tracking site for a friend group's home run parlay/prop pool. Static
site on GitHub Pages, custom domain `bmbs.bet` via Namecheap DNS.

## Architecture

- **`index.html`** — the entire live site. Single self-contained file:
  inline CSS, inline JS. No build step, no framework, no dependencies.
  Deploy = commit this file, GitHub Pages serves it directly.
- **`features.html`** — a static, plain-language "what this site can do"
  page for end users (the friend group), reusing `index.html`'s exact
  color tokens/fonts so it reads as the same product. Linked subtly from
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
- **`CNAME`** — contains `bmbs.bet`, required by GitHub Pages for the custom domain.

## How live tracking actually works (important, don't reinvent this)

`index.html` polls the **MLB Stats API directly from the visitor's own
browser** every ~20 seconds — `statsapi.mlb.com`, which is free, keyless,
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

Batting order tracking (current inning, "at the plate now" / "guaranteed to
bat this inning" tags, at-bats-remaining estimate) comes from the same live
feed's boxscore `battingOrder` field, combined with a negative-binomial
model using a league-average 68.5% out rate (NOT the specific hitter's real
stats — this is disclosed in the UI, don't remove that framing).

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
   the picks-upload → GitHub Actions pipeline. Code changes should only
   ever touch `index.html`, `scripts/*.py`, `.github/workflows/*.yml`,
   `discord-bot/`, `README.md`, `CNAME`.

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

No test framework — `tests/` holds two plain scripts that exit non-zero
on failure:

- `tests/test_parser.py` — both picks formats (`tests/fixtures/gemini_picks.txt`
  vs `test_picks.txt`) parse identically, the slate-date heuristic, and
  the archive-on-date-change guard (against a temp dir, never `data/`).
- `tests/test_page.py` — Playwright (Python) headless Chromium, serving
  `index.html` through one `page.route("**/*")` handler with in-memory
  fixtures (tickets files, MLB schedule, live feeds) and a pinned clock.
  Covers the past-midnight slate, the all-Final rollover, a new upload
  landing, the 6am backstop, and filter regressions.

```
pip install -r tests/requirements.txt
python -m playwright install chromium
python tests/test_parser.py && python tests/test_page.py
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
the next poll cycle (~20s).
