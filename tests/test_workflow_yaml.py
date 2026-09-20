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


def commit_step_text(text):
    """The 'Commit and push if changed' step's run block, isolated so paths
    from OTHER steps (e.g. a --file argument) can't be mistaken for a git add."""
    m = re.search(r'name:\s*"?Commit and push if changed"?\s*\n.*?run:\s*\|(.*?)(?=\n\s*- name:|\Z)', text, re.S)
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

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all workflow checks passed")
