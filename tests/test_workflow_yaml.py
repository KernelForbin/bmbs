"""
Sanity checks on the three GitHub Actions workflows, without adding a YAML
dependency this project doesn't otherwise need -- all three are simple,
hand-written files with no anchors or nesting deep enough to need a real
parser, so this reads them with targeted regexes instead.

The interesting checks aren't "is this valid YAML" (a broken workflow fails
loudly and immediately in the Actions tab, which the user has always caught).
They're the ones that would fail QUIETLY -- a script path that's gone stale
after a rename, a trigger path that no longer matches what the script writes,
or, the one that matters most: **a workflow committing a file outside its
documented scope**. CLAUDE.md's gotcha 5 says only parse-picks.yml (via
parse_picks.py) may ever write data/tickets.json, and the results-archive job
(import-history.yml) must never touch the live tickets files at all -- this
encodes that rule as a test instead of leaving it as prose someone could
violate by accident while editing the commit step.

    python tests/test_workflow_yaml.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPTS = REPO / "scripts"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def commit_step_text(text, step_name="Commit and push if changed"):
    """The named commit step's run block, isolated so paths from OTHER steps
    (e.g. a --file argument) can't be mistaken for a git add. Every workflow
    here names its commit step this way except auto-fix-parse-failure.yml
    ("Commit and push the automatic fix" -- it can't reuse the exact name
    since its gate is different, see that file's own checks below)."""
    m = re.search(rf'name:\s*"?{re.escape(step_name)}"?\s*\n.*?run:\s*\|(.*?)(?=\n\s*- name:|\Z)', text, re.S)
    return m.group(1) if m else ""


def committed_paths(run_block):
    """Every path this step's run block could `git add`: literal `git add X`
    / `[ -f X ] &&` lines, plus the `for path in A B C; do ... done` loop form
    import-history.yml uses. Quotes stripped, $-prefixed shell variables
    (loop-bound, not a real path) dropped."""
    paths = set(re.findall(r'git add "?([^"\s]+)"?', run_block))
    paths |= set(re.findall(r'\[\s*-f\s+"?([^"\s]+)"?\s*\]', run_block))
    # only a `for path in ...` loop lists paths -- `for attempt in 1 2 3...`
    # is the push-retry counter and must not be mistaken for one
    for loop_list in re.findall(r'for\s+path\s+in\s+([^;]+);\s*do', run_block):
        paths |= set(loop_list.split())
    return {p for p in paths if not p.startswith("$")}


def scripts_invoked(text):
    """[(script_path, --file arg or None), ...] for every `run: python scripts/X.py [--file Y]` line."""
    return re.findall(r'run:\s*python\s+(scripts/\S+\.py)(?:\s+--file\s+(\S+))?', text)


def trigger_paths(text):
    m = re.search(r'on:.*?paths:\s*\n((?:\s*-\s*"[^"]+"\s*\n?)+)', text, re.S)
    if not m:
        return []
    return re.findall(r'-\s*"([^"]+)"', m.group(1))


files = {
    "parse-picks.yml": WORKFLOWS / "parse-picks.yml",
    "parse-football-picks.yml": WORKFLOWS / "parse-football-picks.yml",
    "import-history.yml": WORKFLOWS / "import-history.yml",
}
texts = {}
for name, path in files.items():
    check(f"{name}: exists", path.exists())
    if path.exists():
        texts[name] = path.read_text(encoding="utf-8")

if len(texts) < 3:
    print(f"{3 - len(texts)} workflow file(s) missing -- stopping here")
    sys.exit(1)


# ---------------- A. every workflow that pushes has the shape a push needs ----------------

for name, text in texts.items():
    check(f"{name}: grants contents:write (every one of these commits and pushes)",
          re.search(r'permissions:\s*\n\s*contents:\s*write', text) is not None)
    check(f"{name}: has a workflow_dispatch escape hatch for a manual re-run",
          "workflow_dispatch:" in text)
    m = re.search(r'concurrency:\s*\n\s*group:\s*(\S+)\s*\n\s*cancel-in-progress:\s*(true|false)', text)
    check(f"{name}: declares a concurrency group (two runs of the same workflow can't race each other's push)",
          m is not None, text[:0])
    run_block = commit_step_text(text)
    check(f"{name}: the commit step retries the push (git pull --rebase, not just fail on the first conflict)",
          "git pull --rebase" in run_block and "for attempt in" in run_block, "no retry loop found")
    check(f"{name}: a failed push after retries exits non-zero rather than silently swallowing it",
          re.search(r'exit 1', run_block.split("Failed to push")[-1][:80]) is not None if "Failed to push" in run_block else False)


# ---------------- B. every script a workflow runs actually exists ----------------

script_problems = []
for name, text in texts.items():
    for script, file_arg in scripts_invoked(text):
        script_path = REPO / script
        if not script_path.exists():
            script_problems.append(f"{name}: {script} does not exist")
        if file_arg and not file_arg.startswith("data/"):
            script_problems.append(f"{name}: {script} invoked with a suspicious --file {file_arg!r}")
check("B1 every 'python scripts/X.py' line in any workflow names a real script", not script_problems, "; ".join(script_problems))

parse_scripts = {name: scripts_invoked(text) for name, text in texts.items()}
check("B2 parse-picks.yml parses exactly one script, against exactly the file it's triggered by, "
      "then notifies Discord (no --file -- it takes --tickets/--sport instead)",
      parse_scripts["parse-picks.yml"] == [("scripts/parse_picks.py", "data/incoming_picks.txt"),
                                            ("scripts/notify_discord.py", "")],
      parse_scripts["parse-picks.yml"])
check("B3 parse-football-picks.yml parses exactly its football script, against exactly its football "
      "trigger, then notifies Discord the same way",
      parse_scripts["parse-football-picks.yml"] == [("scripts/parse_football_picks.py", "data/football/incoming_picks.txt"),
                                                      ("scripts/notify_discord.py", "")],
      parse_scripts["parse-football-picks.yml"])
check("B2b the notify step is properly gated on a real commit (never fires on a no-op rerun)",
      re.search(r"if:\s*steps\.commit\.outputs\.committed == 'yes'\s*\n\s*env:.*?notify_discord\.py success "
                 r"--tickets data/tickets\.json --sport baseball", texts["parse-picks.yml"], re.S) is not None)
check("B3b same gate on the football side, against the football tickets file",
      re.search(r"if:\s*steps\.commit\.outputs\.committed == 'yes'\s*\n\s*env:.*?notify_discord\.py success "
                 r"--tickets data/football/tickets\.json --sport football", texts["parse-football-picks.yml"], re.S) is not None)
check("B4 import-history.yml runs all three of its scripts (baseball record, baseball history, football record), no --file arg needed",
      [s for s, _ in parse_scripts["import-history.yml"]] ==
      ["scripts/record_results.py", "scripts/import_history.py", "scripts/record_football_results.py"],
      parse_scripts["import-history.yml"])


# ---------------- C. the trigger path matches what the script is told to parse ----------------

check("C1 parse-picks.yml triggers on exactly the file it parses",
      trigger_paths(texts["parse-picks.yml"]) == ["data/incoming_picks.txt"], trigger_paths(texts["parse-picks.yml"]))
check("C2 parse-football-picks.yml triggers on exactly the file it parses",
      trigger_paths(texts["parse-football-picks.yml"]) == ["data/football/incoming_picks.txt"],
      trigger_paths(texts["parse-football-picks.yml"]))
check("C3 import-history.yml is schedule/manual only -- it must NOT trigger on a picks upload (that's a race, not its job)",
      trigger_paths(texts["import-history.yml"]) == [] and "cron:" in texts["import-history.yml"])
cron = re.search(r'cron:\s*"([^"]+)"', texts["import-history.yml"])
check("C4 the cron expression has the 5 fields cron actually needs",
      cron is not None and len(cron.group(1).split()) == 5, cron.group(1) if cron else None)


# ---------------- D. THE important one: nobody commits outside their lane ----------------
# CLAUDE.md gotcha 5: only parse-picks.yml may ever write data/tickets.json;
# the daily archive job must never touch either sport's live tickets files.

committed = {name: committed_paths(commit_step_text(text)) for name, text in texts.items()}

check("D1 parse-picks.yml only ever commits baseball's OWN tickets files, never football's",
      committed["parse-picks.yml"] and all(p.startswith("data/tickets") for p in committed["parse-picks.yml"]),
      committed["parse-picks.yml"])
check("D2 parse-football-picks.yml only ever commits football's OWN tickets files, never baseball's",
      committed["parse-football-picks.yml"] and all(p.startswith("data/football/tickets") for p in committed["parse-football-picks.yml"]),
      committed["parse-football-picks.yml"])
forbidden = {"data/tickets.json", "data/tickets-previous.json", "data/football/tickets.json", "data/football/tickets-previous.json"}
check("D3 import-history.yml NEVER commits a live tickets file -- results/history only",
      not (committed["import-history.yml"] & forbidden), committed["import-history.yml"] & forbidden)
check("D4 import-history.yml's commits stay within its documented set (history.json, results/, football's twins)",
      committed["import-history.yml"] <= {"data/history.json", "data/results", "data/football/results", "data/football/history.json"},
      committed["import-history.yml"])

# ---------------- E. auto-fix-parse-failure.yml: the highest-stakes file ----------------
# Kept OUT of the generic files/texts loop above on purpose -- its commit
# step has a different name (its gate is different: only on a RESOLVED fix,
# never unconditionally), it has no push-triggered `paths:` (it's
# workflow_run/workflow_dispatch, not a picks upload), and its file targets
# are indirected through env vars rather than literal paths, so the shared
# committed_paths()/trigger_paths() checks above don't apply to it the same
# way. Checked here on its own terms instead.

AUTOFIX = WORKFLOWS / "auto-fix-parse-failure.yml"
check("E1: auto-fix-parse-failure.yml exists", AUTOFIX.exists())
if not AUTOFIX.exists():
    print("auto-fix-parse-failure.yml missing -- stopping here")
    sys.exit(1)
af_text = AUTOFIX.read_text(encoding="utf-8")

check("E2 grants contents:write", re.search(r'permissions:\s*\n\s*contents:\s*write', af_text) is not None)
check("E3 has a workflow_dispatch escape hatch for a manual re-run",
      "workflow_dispatch:" in af_text)
check("E4 declares a concurrency group (can't race the parse workflow it follows)",
      re.search(r'^concurrency:\s*\n\s*group:', af_text, re.M) is not None)
check("E5 fires off BOTH parse workflows completing, and only reacts to a FAILURE "
      "(a successful parse already notifies Discord on its own, see section B)",
      re.search(r'workflows:\s*\[\s*"Parse New Picks"\s*,\s*"Parse New Football Picks"\s*\]', af_text) is not None
      and "conclusion == 'failure'" in af_text)
# The workflow_run trigger names above are STRING literals -- if either real
# workflow's own `name:` ever changes, this trigger silently stops firing
# with no error anywhere. Cross-check them against the actual files.
check("E6 the two workflow names it listens for match the real workflows' OWN `name:` fields "
      "(a rename of either would silently break this trigger)",
      re.match(r'^name:\s*Parse New Picks\s*$', texts["parse-picks.yml"].splitlines()[0]) is not None
      and re.match(r'^name:\s*Parse New Football Picks\s*$', texts["parse-football-picks.yml"].splitlines()[0]) is not None)

af_run_block = commit_step_text(af_text, "Commit and push the automatic fix")
check("E7 the commit step only runs when a fix was actually resolved -- never unconditionally",
      re.search(r"Commit and push the automatic fix\s*\n\s*id:\s*commit\s*\n\s*if:\s*steps\.fix\.outputs\.resolved == 'yes'",
                af_text) is not None)
check("E8 that commit step retries the push the same way every other workflow here does",
      "git pull --rebase" in af_run_block and "for attempt in" in af_run_block, "no retry loop found")
check("E9 a failed push after retries exits non-zero rather than silently swallowing it",
      re.search(r'exit 1', af_run_block.split("Failed to push")[-1][:80]) is not None
      if "Failed to push" in af_run_block else False)

# The sport-routing step is where this workflow's OWN "gotcha 5" lives: get
# the branch backwards and an auto-fix could commit football's tickets file
# under a baseball fix, or vice versa. Verified directly against the two
# branches' own output values, not just trusted by reading the if/else.
sport_step = re.search(r'name:\s*Determine which sport failed.*?(?=\n\s*- name:|\Z)', af_text, re.S)
check("E10 the sport-routing step exists at all", sport_step is not None)
if sport_step:
    block = sport_step.group(0)
    football_branch = re.search(r"football.*?\n((?:\s+echo.*\n)+)", block)
    baseball_branch = re.search(r"else\s*\n((?:\s+echo.*\n)+)", block)
    check("E11 the FOOTBALL branch only ever points at football's own files",
          football_branch is not None and all(
              "football" in line or "sport=football" in line
              for line in football_branch.group(1).splitlines() if "echo" in line),
          football_branch.group(1) if football_branch else None)
    check("E12 the BASEBALL (else) branch never points at a football/ path",
          baseball_branch is not None and not any(
              "football" in line for line in baseball_branch.group(1).splitlines()),
          baseball_branch.group(1) if baseball_branch else None)

check("E13 the final Discord message checks BOTH the fix being resolved AND the commit "
      "actually succeeding -- a fix that couldn't be pushed must never be reported as live",
      re.search(r'RESOLVED"\s*=\s*"yes"\s*\]\s*&&\s*\[\s*"\$COMMIT_OUTCOME"\s*=\s*"success"', af_text) is not None)
afp_source = (SCRIPTS / "auto_fix_parser.py").read_text(encoding="utf-8")
check("E14 auto_fix_parser.py itself never invokes git -- only the workflow's own bash step "
      "does (keeps the highest-risk decision logic separate from the actual push mechanism)",
      not re.search(r'["\']git["\']', afp_source), "a literal 'git' argument was found in the script")

# ---------------- F. a status ping can never veto the work ----------------
# 2026-09-22: the auto-fix workflow's FIRST Discord call returned 403 (Cloudflare
# blocking the default Python user agent) and, having no continue-on-error, took
# the whole job down with it at step 4 of 8. The picks were never repaired and
# the group got no message at all -- the exact silent failure the notifier was
# built to prevent. Every Discord step in every workflow is now non-blocking,
# and these checks exist so that can't quietly come undone.

def steps_calling_notify(text):
    """-> [(step name, step body)] for every step that runs notify_discord.py."""
    out = []
    for block in re.split(r"\n      - name: ", text)[1:]:
        name = block.splitlines()[0].strip()
        if "notify_discord.py" in block:
            out.append((name, block))
    return out


notify_steps = []
for wf_name in ("auto-fix-parse-failure.yml", "parse-picks.yml", "parse-football-picks.yml"):
    wf_text = (WORKFLOWS / wf_name).read_text(encoding="utf-8")
    for step_name, body in steps_calling_notify(wf_text):
        notify_steps.append((wf_name, step_name, body))

check("F1 every workflow that notifies Discord has at least one such step",
      len(notify_steps) >= 4, f"found {len(notify_steps)}")

missing = [f"{wf}: {name}" for wf, name, body in notify_steps if "continue-on-error: true" not in body]
check("F2 EVERY Discord step is continue-on-error -- a failed status ping must never "
      "fail the run that was doing the actual work", not missing, "; ".join(missing))

# The script's own half of the same guarantee.
nd_source = (SCRIPTS / "notify_discord.py").read_text(encoding="utf-8")
check("F3 notify_discord.py sends a User-Agent -- without one Cloudflare 403s every call "
      "before it ever reaches Discord",
      "User-Agent" in nd_source and "USER_AGENT" in nd_source)
check("F4 notify_discord.py no longer sys.exit()s on a failed request -- it raises "
      "NotifyFailed, and main() turns that into a warning and exit 0",
      "NotifyFailed" in nd_source and "sys.exit(f\"Discord webhook request failed" not in nd_source)

# And the checkout must follow the branch, or this workflow can never be
# exercised end to end before it is merged -- which is how the 403 shipped.
check("F5 the auto-fix workflow checks out the branch it was dispatched from, not a "
      "hardcoded main, so it can be proven end to end before merging",
      "github.event.workflow_run.head_branch" in af_text and "\n          ref: main" not in af_text)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all workflow checks passed")
