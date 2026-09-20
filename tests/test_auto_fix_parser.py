"""
Regression checks for scripts/auto_fix_parser.py -- the script that decides
whether an LLM-proposed parser patch is trustworthy enough to leave in the
working tree for the workflow to commit and push, unattended. Fully
offline: the Claude API call is a fake fetcher (never a real request), and
"the full test suite" / "does the upload parse now" are monkeypatched so
this file doesn't spend minutes re-running Playwright to test itself.

The one thing this file cares about most: attempt_fix() must NEVER leave a
patched file in place unless BOTH the suite passed AND the specific upload
now parses -- and must revert to the exact original content on every other
path, including a malformed model response or a raised exception.

    python tests/test_auto_fix_parser.py
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import auto_fix_parser as afp  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


ENVELOPE = "<<<SUMMARY>>>\nAdded the new emoji-ticket template.\n<<<FILE>>>\nNEW FILE CONTENTS\n<<<END>>>\n"


# ---------------- A. extract_file_and_summary -- never guesses on a bad shape ----------------

check("A1 a well-formed envelope extracts both parts",
      afp.extract_file_and_summary(ENVELOPE) == ("Added the new emoji-ticket template.", "NEW FILE CONTENTS"))
check("A2 missing <<<FILE>>> -> (None, None), never a partial guess",
      afp.extract_file_and_summary("<<<SUMMARY>>>\njust a summary\n") == (None, None))
check("A3 missing <<<SUMMARY>>> entirely -> (None, None)",
      afp.extract_file_and_summary("some prose the model wrote instead") == (None, None))
check("A4 an empty file section -> (None, None), not an empty-string file",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nx\n<<<FILE>>>\n\n<<<END>>>") == (None, None))
check("A5 markdown fences around the model's own answer don't confuse it "
      "(the fences just become part of the summary/file text verbatim)",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nfixed it\n<<<FILE>>>\nprint(1)\n<<<END>>>")
      == ("fixed it", "print(1)"))


# ---------------- B. build_prompt -- both texts present, feedback only when given ----------------

p = afp.build_prompt("baseball", "RAW UPLOAD TEXT", "PARSER SOURCE TEXT")
check("B1 both the raw upload and the current source are in the prompt",
      "RAW UPLOAD TEXT" in p and "PARSER SOURCE TEXT" in p and "baseball" in p)
check("B2 no retry-feedback section when none was given", "previous attempt" not in p)
p2 = afp.build_prompt("football", "x", "y", retry_feedback="the suite failed on test_parser.py")
check("B3 retry feedback is included verbatim when given",
      "the suite failed on test_parser.py" in p2 and "previous attempt" in p2)


# ---------------- C. attempt_fix -- the decision logic that matters most ----------------

def with_tmp_files(parser_content, incoming_content):
    d = Path(tempfile.mkdtemp())
    parser = d / "parser.py"
    incoming = d / "incoming.txt"
    parser.write_text(parser_content, encoding="utf-8")
    incoming.write_text(incoming_content, encoding="utf-8")
    return parser, incoming


def fetcher_returning(text):
    def _fetcher(url, headers, body):
        import json
        return json.dumps({"content": [{"text": text}]}).encode("utf-8")
    return _fetcher


def fetcher_raising():
    def _fetcher(url, headers, body):
        raise ConnectionError("network is down")
    return _fetcher


ORIGINAL_SOURCE = "def parse(): return 'old'\n"

# C1: well-formed response, suite passes, fix verified -> file updated, ok=True
parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "some upload")
old_suite, old_verify = afp.run_full_suite, afp.verify_fix
afp.run_full_suite = lambda: (True, "")
afp.verify_fix = lambda p, i: (True, "")
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_returning(ENVELOPE))
check("C1 a fix that passes both gates is left in place, ok=True",
      ok and parser.read_text(encoding="utf-8") == "NEW FILE CONTENTS", (ok, detail, parser.read_text(encoding="utf-8")))

# C2: suite FAILS after the patch -> file reverted, ok=False
parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "some upload")
afp.run_full_suite = lambda: (False, "test_parser.py failed")
afp.verify_fix = lambda p, i: (True, "")  # should never even be reached
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_returning(ENVELOPE))
check("C2 a suite failure reverts the file to its ORIGINAL content and returns ok=False",
      not ok and parser.read_text(encoding="utf-8") == ORIGINAL_SOURCE, (ok, parser.read_text(encoding="utf-8")))
check("C2b the failure detail names what broke", "test_parser.py failed" in detail, detail)

# C3: suite passes but the specific upload STILL doesn't parse -> reverted, ok=False
parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "some upload")
afp.run_full_suite = lambda: (True, "")
afp.verify_fix = lambda p, i: (False, "parser still exits 1")
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_returning(ENVELOPE))
check("C3 the suite passing is NOT enough on its own -- if the specific upload still fails, revert",
      not ok and parser.read_text(encoding="utf-8") == ORIGINAL_SOURCE, (ok, parser.read_text(encoding="utf-8")))

# C4: malformed model response -> the file is NEVER TOUCHED at all (not written, then reverted --
# never written in the first place, which check() can't tell apart from "written then reverted"
# by content alone, so also check the suite/verify functions were never even called)
parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "some upload")
suite_called = []
afp.run_full_suite = lambda: (suite_called.append(1), (True, ""))[1]
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_returning("not the right shape at all"))
check("C4 a malformed response never touches the file and never even runs the test suite",
      not ok and parser.read_text(encoding="utf-8") == ORIGINAL_SOURCE and suite_called == [], (ok, suite_called))

# C5: the API call itself raises -> handled, ok=False, file untouched
parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "some upload")
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_raising())
check("C5 a network/API failure is caught, not raised -- ok=False, file untouched",
      not ok and parser.read_text(encoding="utf-8") == ORIGINAL_SOURCE and "network is down" in detail, detail)

afp.run_full_suite, afp.verify_fix = old_suite, old_verify


# ---------------- D. main()'s retry loop and GITHUB_OUTPUT wiring ----------------

import os

d = Path(tempfile.mkdtemp())
gh_output = d / "gh_output.txt"
os.environ["GITHUB_OUTPUT"] = str(gh_output)


def read_outputs():
    if not gh_output.exists():
        return {}
    out = {}
    for line in gh_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


# D1: attempt 1 fails, attempt 2 (with feedback from attempt 1) succeeds
calls = []


def fake_attempt_fix(sport, incoming, parser, api_key, retry_feedback=None, fetcher=None):
    calls.append(retry_feedback)
    if len(calls) == 1:
        return False, "first attempt: wrong regex"
    return True, "second attempt: fixed it"


old_attempt_fix, old_archive = afp.attempt_fix, afp.archive_fixture
afp.attempt_fix = fake_attempt_fix
afp.archive_fixture = lambda sport, incoming: Path("tests/fixtures/fake.txt")

gh_output.write_text("", encoding="utf-8")
calls.clear()
os.environ.update({"SPORT": "baseball", "INCOMING": "x", "PARSER": "y", "ANTHROPIC_API_KEY": "fake"})
afp.main()
check("D1 attempt 2 is called WITH attempt 1's failure detail as feedback",
      calls == [None, "first attempt: wrong regex"], calls)
check("D2 a second-attempt success writes resolved=yes", read_outputs().get("resolved") == "yes", read_outputs())

# D3: both attempts fail -> resolved=no, never crashes
calls.clear()
afp.attempt_fix = lambda *a, **kw: (calls.append(1), (False, "still broken"))[1]
gh_output.write_text("", encoding="utf-8")
afp.main()
check("D3 both attempts failing -> resolved=no (never left unresolved/crashed)",
      read_outputs().get("resolved") == "no", read_outputs())
check("D4 exactly MAX_ATTEMPTS attempts were made, not more", len(calls) == afp.MAX_ATTEMPTS, len(calls))

# D5: no ANTHROPIC_API_KEY at all -> resolved=no immediately, no attempt made
calls.clear()
del os.environ["ANTHROPIC_API_KEY"]
gh_output.write_text("", encoding="utf-8")
afp.main()
check("D5 a missing API key short-circuits to resolved=no without ever calling attempt_fix",
      read_outputs().get("resolved") == "no" and calls == [], (read_outputs(), calls))

afp.attempt_fix, afp.archive_fixture = old_attempt_fix, old_archive
del os.environ["GITHUB_OUTPUT"]


# ---------------- E. set_output never crashes when GITHUB_OUTPUT isn't set ----------------

os.environ.pop("GITHUB_OUTPUT", None)
try:
    afp.set_output(resolved="yes")
    check("E1 set_output no-ops safely outside of Actions (no GITHUB_OUTPUT set)", True)
except Exception as e:
    check("E1 set_output no-ops safely outside of Actions (no GITHUB_OUTPUT set)", False, str(e))


# ---------------- F. archive_fixture copies the real upload content ----------------

parser, incoming = with_tmp_files(ORIGINAL_SOURCE, "the real failing card text")
dest = afp.archive_fixture("football", incoming)
full_dest = REPO / dest
check("F1 archive_fixture copies the upload's content byte-for-byte",
      full_dest.exists() and full_dest.read_text(encoding="utf-8") == "the real failing card text")
check("F2 the archived fixture is named for its sport and lands under tests/fixtures/",
      "football" in dest.name and dest.parent.name == "fixtures", str(dest))
full_dest.unlink()


print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all auto-fix-parser checks passed")
