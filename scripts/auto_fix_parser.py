#!/usr/bin/env python3
"""
Bounded, single-purpose auto-fix for a picks-parser failure. NOT an
autonomous agent: one Claude API call per attempt (no tool use, no open-
ended agency), and every fix it proposes is validated by the SAME test
suite a human would run before trusting it -- it commits nothing on its
own; see main()'s docstring for exactly what "validated" means.

Run from .github/workflows/auto-fix-parse-failure.yml after a parse
workflow fails. Reads SPORT / INCOMING / PARSER from the environment (set
by that workflow's "Determine which sport failed" step) and ANTHROPIC_API_KEY.

On success: leaves the patched parser file, the freshly-written real
tickets.json, and an archived copy of the failing upload under
tests/fixtures/ sitting in the working tree, UNCOMMITTED -- the workflow's
own next step (plain bash, same git-push-with-retry pattern every other
workflow here uses) commits and pushes them. This script never runs git
itself, so its own bugs can't invent a novel way to corrupt the commit
history.

Writes GITHUB_OUTPUT keys `resolved` (yes/no) and `summary` (one line,
used in the commit message and the Discord status edit).
"""
import json
import difflib
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODEL = "claude-sonnet-5"
MAX_ATTEMPTS = 2
API_URL = "https://api.anthropic.com/v1/messages"

# The full offline suite (test_feed_fields.py excluded on purpose -- it needs
# live network games and can't produce a stable pass/fail for this gate).
FULL_SUITE = [
    "test_parser", "test_page", "test_live_at_bats", "test_steals",
    "test_history_import", "test_history", "test_build_roster",
    "test_football_parser", "test_football", "test_record_results",
    "test_football_history", "test_discord_bot", "test_at_bat_math",
    "test_live_data_schema", "test_site_links", "test_features_page",
    "test_workflow_yaml", "test_redirect_stub", "test_notify_discord",
]

# Hard-won lessons from four real template-drift incidents fixed by hand on
# 2026-09-19/20 (see CLAUDE.md's "picks-generation prompt has changed
# template" entries) -- written directly into the prompt because the same
# mistake was made independently on baseball AND football the same day, so
# it is not a one-off, it is the FIRST thing a new template trips over.
SYSTEM_PROMPT = """\
You are fixing ONE Python parser script in a small, established codebase.
A friend group pastes hand-written or LLM-generated sports-picks text into
this parser, which has to turn it into a specific JSON schema. The picks-
generation prompt is itself an LLM output and its template shape drifts
without warning every so often. Your only job right now: make the CURRENT
failing upload parse correctly, without breaking any upload shape the
parser already handles.

You will be given the parser's complete current source and the raw text
that failed to parse (exit 1, "WARNING: parsed nothing" or similar).

Hard rules, learned from real incidents -- read all of them before writing
anything:

1. **The #1 trap: a new ticket/card header often contains a literal
   substring like "3-Leg Parlay" or "5-MAN", and this codebase has a
   `PARLAY_HEADER_RE`-style regex (matching things like `\\d+-Leg Parlay`)
   used to detect SECTION headers earlier in the same function that decides
   what a line is. If you add a new ticket-header shape containing that
   substring, it WILL be misread as a brand-new section before your new
   ticket-parsing code ever sees it, unless you add it to that function's
   exclusion list first. This happened on both sports' parsers independently
   the same day. Check for this collision explicitly and guard against it.
2. **Never trust a header's own claimed leg count.** A ticket is a single
   vs. a parlay card purely by how many leg lines it actually has, decided
   after parsing all of them -- never by a number printed in the header.
3. **Player names resolve through the existing roster-lookup function
   (exact match, then fuzzy, then keep-as-typed with a NOTE).** Never invent
   a name-matching shortcut. If the card gives an explicit team code per
   leg, keep that as a fallback ONLY when the roster lookup can't resolve
   one -- the roster's own team wins when it has one.
4. **A bet-like line nothing understands must be reported, never silently
   dropped** (if the file already has an "unread"/`BETLIKE_RE` mechanism,
   keep using it -- don't remove or weaken it).
5. **Preserve every existing template this file already parses.** Add new
   regexes and a new branch in the parsing loop; do not restructure or
   simplify code that isn't related to the new template, and do not touch
   any existing regex unless the new template's addition genuinely requires
   it (state exactly why, in your summary, if you do).
6. **Match the file's own existing style exactly**: same regex-construction
   idioms (e.g. a shared BULLET prefix, a shared ODDS group), same function
   names and signatures, same docstring/comment voice already in the file
   (dry, specific, cites real examples). Add a comment above your new
   regex(es) explaining the new template's shape with a short real snippet,
   the same way every other template in this file is documented.
7. Free-text fields (e.g. a time field that might read "TBD" or "Check
   Listings" instead of a real clock time) should be stored as typed, not
   rejected or crashed on.

Respond with EXACTLY this structure and nothing else -- no markdown fences,
no commentary outside the two sections:

<<<SUMMARY>>>
One or two sentences: what shape the new template is, and what you changed.
<<<FILE>>>
<the COMPLETE new file content, from its first line to its last -- the
 whole file, not a diff, not an excerpt>
<<<END>>>
"""


def resolve(path):
    """Every other path-taking function below (run_full_suite, verify_fix,
    archive_fixture) resolves relative to REPO explicitly -- match that here
    too, rather than trusting the caller's cwd to already be the repo root."""
    path = Path(path)
    return path if path.is_absolute() else REPO / path


def read(path):
    return resolve(path).read_text(encoding="utf-8")


def write(path, text):
    resolve(path).write_text(text, encoding="utf-8")


def call_claude(prompt, api_key, fetcher=None):
    """One Messages API call. `fetcher(url, headers, body_bytes) -> response
    bytes` is injectable for tests; real use hits the network via urllib.

    No `temperature` field, and `thinking` explicitly disabled -- both
    confirmed live against the real API before this was ever trusted in the
    workflow, and both would have made every single invocation fail
    silently (resolved=no, forever) if shipped as first written:
      - `temperature` at any value, including 0, is rejected outright by
        claude-sonnet-5 (HTTP 400 "temperature is deprecated for this
        model"). A parameter that silently changed from "an option" to "a
        hard error" between model generations is exactly the kind of thing
        this script can't afford to get wrong unverified.
      - With `thinking` left at its default, the model spent its ENTIRE
        token budget on an internal thinking block and returned empty
        (`stop_reason: max_tokens`, 8192/8192 thinking tokens, zero text) --
        raising max_tokens alone didn't fix it (32000 tokens: 25914 went to
        thinking, the file got cut off mid-write, no <<<END>>>). Disabling
        thinking outright fixed it in one try: `stop_reason: end_turn`,
        0 thinking tokens, a complete well-formed response. This task
        doesn't need exploratory reasoning -- the hard rules above already
        spell out exactly what to check."""
    body = json.dumps({
        "model": MODEL, "max_tokens": 20000,
        "thinking": {"type": "disabled"},
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if fetcher is not None:
        raw = fetcher(API_URL, headers, body)
    else:
        req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Claude API request failed: HTTP {e.code}") from None
    data = json.loads(raw)
    return "".join(b.get("text", "") for b in data.get("content", []))


def extract_file_and_summary(response_text):
    """(summary, file_content) parsed out of the model's <<<...>>> envelope,
    or (None, None) if the response doesn't match the required shape --
    treated as a failed attempt, never guessed at."""
    try:
        _, rest = response_text.split("<<<SUMMARY>>>", 1)
        summary, rest = rest.split("<<<FILE>>>", 1)
        file_content, _ = rest.split("<<<END>>>", 1)
    except ValueError:
        return None, None
    summary = summary.strip()
    file_content = file_content.strip("\n")
    # Confirmed live, 2026-09-20: despite the system prompt saying "no
    # markdown fences", the model sometimes wraps the file section in one
    # anyway -- inconsistently, not every call. Writing "```python" as line 1
    # of a .py file is an instant SyntaxError, wasting an attempt on a purely
    # cosmetic slip verify_fix would otherwise have to reject. Strip one
    # leading/trailing fence if present rather than trusting the instruction
    # alone to have worked.
    file_content = re.sub(r"^```[A-Za-z]*\n", "", file_content)
    file_content = re.sub(r"\n```\s*$", "", file_content)
    if not summary or not file_content:
        return None, None
    return summary, file_content


def build_prompt(sport, incoming_text, parser_source, retry_feedback=None):
    parts = [
        f"Sport: {sport}",
        "Current parser source (fix THIS file):",
        "```python", parser_source, "```",
        "The raw text that failed to parse:",
        "```", incoming_text, "```",
    ]
    if retry_feedback:
        parts.append(
            "Your previous attempt did NOT resolve this. Here is exactly what "
            f"went wrong, so you can fix it this time:\n{retry_feedback}"
        )
    return "\n\n".join(parts)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, **kw)


def run_full_suite():
    """(passed, detail). Runs every file in FULL_SUITE; stops at the first
    failure (no point burning more time/CI minutes once one has failed)."""
    for name in FULL_SUITE:
        r = run([sys.executable, str(REPO / "tests" / f"{name}.py")])
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
            return False, f"{name}.py failed:\n{tail}"
    return True, ""


def verify_fix(parser_path, incoming_path):
    """Does the SPECIFIC failing upload parse now? Running this also writes
    the real tickets.json for the workflow's later commit step -- same
    invocation the ordinary workflow itself uses, nothing special-cased."""
    r = run([sys.executable, str(REPO / parser_path), "--file", str(REPO / incoming_path)])
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-15:])
        return False, f"parser still exits {r.returncode} against the real upload:\n{tail}"
    return True, ""


def patch_diff(original, proposed, limit=160):
    """A unified diff of what the model actually changed.

    Without this the run log shows only WHICH test failed, never what was
    tried -- so a failed attempt is undebuggable after the fact and the next
    person has to reproduce it by hand with an API key. The model's output is
    untrusted text, but it only ever reaches a log line here."""
    diff = list(difflib.unified_diff(
        original.splitlines(), proposed.splitlines(),
        fromfile="parser (before)", tofile="parser (model's patch)", lineterm="", n=2))
    if not diff:
        return "(the model returned the file unchanged)"
    if len(diff) > limit:
        diff = diff[:limit] + [f"... {len(diff) - limit} more diff lines truncated ..."]
    return "\n".join(diff)

def attempt_fix(sport, incoming_path, parser_path, api_key, retry_feedback=None, fetcher=None):
    """(ok, detail). On ok=True, the patched file is left in place (and the
    real tickets.json has been written by verify_fix's own parser run). On
    ok=False, parser_path is guaranteed to be back to its ORIGINAL content --
    every failure branch below reverts explicitly before returning, rather
    than relying on a shared cleanup path, so "did this attempt succeed" and
    "is the file reverted" can never disagree with each other."""
    original = read(parser_path)
    incoming_text = read(incoming_path)
    prompt = build_prompt(sport, incoming_text, original, retry_feedback)

    try:
        response = call_claude(prompt, api_key, fetcher=fetcher)
    except Exception as e:  # network, auth, rate limit -- never crash the workflow over it
        return False, f"Claude API call failed: {e}"

    summary, new_source = extract_file_and_summary(response)
    if new_source is None:
        return False, "the model's response didn't match the required <<<SUMMARY>>>/<<<FILE>>> shape"

    write(parser_path, new_source)

    # Reported on failure so the run log shows what was attempted, not just
    # what broke. Built before the file is reverted.
    attempted = f"what the model changed:\n{patch_diff(original, new_source)}"
    if summary:
        attempted = f"the model said: {summary}\n\n{attempted}"

    passed, suite_detail = run_full_suite()
    if not passed:
        write(parser_path, original)
        return False, f"the full test suite failed after the patch:\n{suite_detail}\n\n{attempted}"

    fixed, fix_detail = verify_fix(parser_path, incoming_path)
    if not fixed:
        write(parser_path, original)
        return False, f"{fix_detail}\n\n{attempted}"

    return True, summary


def archive_fixture(sport, incoming_path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = REPO / "tests" / "fixtures" / f"auto_detected_{sport}_{stamp}.txt"
    dest.write_text(read(incoming_path), encoding="utf-8")
    return dest.relative_to(REPO)


def set_output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def main():
    sport = os.environ["SPORT"]
    incoming_path = os.environ["INCOMING"]
    parser_path = os.environ["PARSER"]
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        set_output(resolved="no", summary="ANTHROPIC_API_KEY is not configured")
        return

    feedback = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Attempt {attempt}/{MAX_ATTEMPTS}...")
        ok, detail = attempt_fix(sport, incoming_path, parser_path, api_key, retry_feedback=feedback)
        if ok:
            fixture = archive_fixture(sport, incoming_path)
            print(f"Resolved: {detail}")
            print(f"Archived the raw upload as {fixture}")
            set_output(resolved="yes", summary=detail.replace("\n", " ")[:200])
            return
        print(f"Attempt {attempt} did not resolve it:\n{detail}", file=sys.stderr)
        feedback = detail

    set_output(resolved="no", summary="couldn't resolve automatically after 2 attempts -- needs a human look")


if __name__ == "__main__":
    main()
