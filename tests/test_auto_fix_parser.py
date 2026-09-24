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
import json
import pathlib
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


# ---------------- Z. call_claude's request body -- two live incidents, locked in ----------------
# Verified against the real API on 2026-09-20 before this was ever trusted:
# `temperature` (even 0) gets claude-sonnet-5 a hard HTTP 400, and leaving
# `thinking` at its default burned the ENTIRE token budget on an internal
# thinking block and returned empty (stop_reason: max_tokens, 0 text) --
# raising max_tokens alone did NOT fix it, only disabling thinking did.
# Nothing here hits the network; it just inspects the request call_claude
# builds via the injectable fetcher.
_captured = {}


def _capturing_fetcher(url, headers, body):
    _captured["body"] = json.loads(body)
    return json.dumps({"content": [{"text": "irrelevant for this check"}]}).encode("utf-8")


afp.call_claude("a prompt", "fake-key", fetcher=_capturing_fetcher)
check("Z1 no 'temperature' field at all -- claude-sonnet-5 rejects the request outright if it's present",
      "temperature" not in _captured["body"], _captured["body"])
check("Z2 'thinking' is explicitly disabled -- left at default, it silently ate the whole "
      "token budget and returned empty instead of the file",
      _captured["body"].get("thinking") == {"type": "disabled"}, _captured["body"].get("thinking"))
check("Z3 max_tokens is generous enough for a full parser file plus a summary "
      "(the real parser file alone needed ~9000 tokens once thinking stopped competing for budget)",
      _captured["body"].get("max_tokens", 0) >= 16000, _captured["body"].get("max_tokens"))


# ---------------- A. extract_file_and_summary -- never guesses on a bad shape ----------------

check("A1 a well-formed envelope extracts both parts",
      afp.extract_file_and_summary(ENVELOPE) == ("Added the new emoji-ticket template.", "NEW FILE CONTENTS"))
check("A2 missing <<<FILE>>> -> (None, None), never a partial guess",
      afp.extract_file_and_summary("<<<SUMMARY>>>\njust a summary\n") == (None, None))
check("A3 missing <<<SUMMARY>>> entirely -> (None, None)",
      afp.extract_file_and_summary("some prose the model wrote instead") == (None, None))
check("A4 an empty file section -> (None, None), not an empty-string file",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nx\n<<<FILE>>>\n\n<<<END>>>") == (None, None))
check("A5 no fences present -> the file content is taken verbatim, untouched",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nfixed it\n<<<FILE>>>\nprint(1)\n<<<END>>>")
      == ("fixed it", "print(1)"))
# A6-A7: confirmed live 2026-09-20 -- despite the system prompt saying "no
# markdown fences", the model wrapped the file section in one anyway on a
# real call, and the fence markers got written as literal lines 1 and N of
# the .py file -- an instant SyntaxError that wasted a whole attempt on a
# purely cosmetic slip. A real ```` ``` ```` fence (with a language tag,
# since that's what actually happened) must be stripped, not left in.
check("A6 a language-tagged code fence around the file section is stripped",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nfixed it\n<<<FILE>>>\n```python\nprint(1)\n```\n<<<END>>>")
      == ("fixed it", "print(1)"))
check("A7 a bare fence (no language tag) is stripped too",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nfixed it\n<<<FILE>>>\n```\nprint(1)\n```\n<<<END>>>")
      == ("fixed it", "print(1)"))
check("A8 only a MATCHING leading+trailing fence is stripped -- a bare triple-backtick "
      "occurring naturally inside real file content (e.g. in a docstring) is left alone",
      afp.extract_file_and_summary("<<<SUMMARY>>>\nx\n<<<FILE>>>\ncode\n```\nmore code\n<<<END>>>")
      == ("x", "code\n```\nmore code"))


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

# C6-C8: a failed attempt must say what it TRIED, not just what broke. Without
# this the run log shows a test name and nothing else, so nobody can tell
# whether the model was close or wildly off without an API key and a local
# reproduction. Found the hard way on 2026-09-22.
afp.run_full_suite = lambda: (False, "test_parser.py failed")
afp.verify_fix = lambda p, i: (True, "")
ok, detail = afp.attempt_fix("baseball", incoming, parser, "fake-key", fetcher=fetcher_returning(ENVELOPE))
check("C6 a suite failure reports the model's own summary of what it did",
      "the model said:" in detail, detail[:200])
check("C7 ...and a unified diff of the patch it proposed",
      "what the model changed:" in detail and "parser (model's patch)" in detail, detail[:300])
check("C8 ...while still naming the test that failed",
      "test_parser.py failed" in detail, detail[:200])

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


# ---------------- G. a transport failure is NOT reported as a bad card ----------------
# On 2026-09-23 all four attempts died on "The read operation timed out": the
# model never answered, so the upload was never examined -- and the group was
# told "Couldn't parse this baseball upload", which points the blame at a card
# nothing had read. The cause was our own 120s timeout, outgrown because every
# template we add makes the file the model has to REGENERATE IN FULL longer.

os.environ["GITHUB_OUTPUT"] = str(gh_output)
os.environ["ANTHROPIC_API_KEY"] = "test-key"
parser_g, incoming_g = with_tmp_files(ORIGINAL_SOURCE, "some upload")
os.environ["SPORT"], os.environ["INCOMING"], os.environ["PARSER"] = "baseball", str(incoming_g), str(parser_g)

check("G1 the API timeout is generous enough to regenerate the whole parser -- "
      "it is now ~10.5k output tokens and grows with every template added",
      afp.API_TIMEOUT >= 300, afp.API_TIMEOUT)

# the upload must look genuinely broken, or the section-H guard short-circuits
# main() before any of this is reached
_real_already_parses = afp.already_parses
afp.already_parses = lambda *a, **kw: False

# every attempt a transport failure -> reason=transport
afp.attempt_fix = lambda *a, **kw: (False, "TRANSPORT: couldn't reach the Claude API: timed out")
gh_output.write_text("", encoding="utf-8")
afp.main()
out = read_outputs()
check("G2 all-transport failures report reason=transport, not a card problem",
      out.get("resolved") == "no" and out.get("reason") == "transport", out)
check("G3 the summary says the upload was never examined",
      "never examined" in out.get("summary", ""), out)

# a genuine model failure still reports as unresolved, even mixed with one timeout
seq = ["TRANSPORT: couldn't reach the Claude API: timed out",
       "the model's response didn't match the required shape",
       "TRANSPORT: couldn't reach the Claude API: timed out",
       "the suite failed"]
it = iter(seq)
afp.attempt_fix = lambda *a, **kw: (False, next(it))
gh_output.write_text("", encoding="utf-8")
afp.main()
out = read_outputs()
check("G4 one real model failure among the timeouts -> reason=unresolved (the card WAS judged)",
      out.get("reason") == "unresolved", out)

# G5-G7 exercise the REAL attempt_fix, so the stub above has to go first
afp.attempt_fix = old_attempt_fix
afp.already_parses = _real_already_parses

# a transport-level exception inside call_claude must be caught, flagged, and
# must never crash the workflow -- including non-timeout errors like auth
for exc, label in [(TimeoutError("timed out"), "G5 a socket timeout"),
                   (ConnectionError("network is down"), "G6 a connection error"),
                   (ValueError("bad json"), "G7 an unexpected error")]:
    def _boom(url, headers, body, _e=exc):
        raise _e
    ok, detail = afp.attempt_fix("baseball", str(incoming_g), str(parser_g), "k", fetcher=_boom)
    check(f"{label} is caught, flagged TRANSPORT, and never crashes",
          ok is False and detail.startswith("TRANSPORT:"), detail)

# and the parser file must be untouched after any of them
check("G8 a transport failure leaves the parser byte-for-byte original",
      parser_g.read_text(encoding="utf-8") == ORIGINAL_SOURCE)

afp.attempt_fix = old_attempt_fix
del os.environ["GITHUB_OUTPUT"]


# ---------------- H. a healthy upload must NOT be reported as fixed ----------------
# 2026-09-23: the workflow was dispatched on a branch cut BEFORE the failing
# upload landed, so it read the PREVIOUS day's card -- which parses fine. The
# model found nothing wrong, made a cosmetic edit, every gate passed (verify_fix
# only asks "does it parse now", already true), and Discord announced
# "Fixed automatically -- picks are live". It had fixed nothing, and the run
# also re-parsed the old card over tickets.json and 408 lines of
# tickets-previous.json, which merging would have pushed live over the real slate.

os.environ["GITHUB_OUTPUT"] = str(gh_output)
os.environ["ANTHROPIC_API_KEY"] = "test-key"
parser_h, incoming_h = with_tmp_files(ORIGINAL_SOURCE, "some upload")
os.environ["SPORT"], os.environ["INCOMING"], os.environ["PARSER"] = "baseball", str(incoming_h), str(parser_h)

calls.clear()
afp.attempt_fix = lambda *a, **kw: (calls.append(1), (True, "fixed!"))[1]
# H3 drives main() down the SUCCESS path, which calls archive_fixture() for
# real -- and that writes into tests/fixtures/ in the actual repo. Three junk
# files ("some upload") were committed before this was stubbed. A test must
# not leave anything behind; section F covers archive_fixture properly and
# deletes what it writes.
archived = []
old_archive_h = afp.archive_fixture
afp.archive_fixture = lambda sport, incoming: archived.append(sport) or pathlib.Path("(stubbed)")
old_already = afp.already_parses

afp.already_parses = lambda *a, **kw: True
gh_output.write_text("", encoding="utf-8")
afp.main()
out = read_outputs()
check("H1 an upload that already parses reports reason=notbroken, never resolved=yes",
      out.get("resolved") == "no" and out.get("reason") == "notbroken", out)
check("H2 ...and no fix is even attempted (no needless rewrite of a working parser)",
      calls == [], calls)

# the guard must not block a genuinely broken upload
afp.already_parses = lambda *a, **kw: False
calls.clear()
gh_output.write_text("", encoding="utf-8")
afp.main()
check("H3 a genuinely broken upload still gets fixed",
      read_outputs().get("resolved") == "yes" and len(calls) == 1, (read_outputs(), calls))

check("H4 the success path archived exactly one fixture (stubbed -- a test must "
      "never write into tests/fixtures/ for real)", archived == ["baseball"], archived)

afp.archive_fixture = old_archive_h
afp.already_parses = old_already
afp.attempt_fix = old_attempt_fix
del os.environ["GITHUB_OUTPUT"]


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
