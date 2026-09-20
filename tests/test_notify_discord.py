"""
Regression checks for scripts/notify_discord.py. Fully offline: the HTTP
layer is a fake fetcher, never a real request, and DISCORD_STATUS_WEBHOOK
is a fake value for the duration of the test only.

    python tests/test_notify_discord.py
"""
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import notify_discord as nd  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


import os
os.environ["DISCORD_STATUS_WEBHOOK"] = "https://discord.test/api/webhooks/123/fake-token"

REPO_TMP = REPO / "tests" / "fixtures"


def write_tmp(name, payload):
    p = REPO_TMP / f"_scratch_{name}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------- A. summarize() -- the message text, both sports ----------------

baseball_payload = {
    "date": "2026-09-20", "note": "",
    "windows": [{"title": "x", "tickets": [{"legs": [1, 2, 3]}, {"legs": [1, 2]}]}],
    "singles": [{}],
}
p = write_tmp("baseball", baseball_payload)
msg = nd.summarize(p, "baseball")
check("A1 baseball: card + single + leg counts, singular single",
      msg == "✅ Baseball picks for 2026-09-20 are live on https://bmbs.bet/ — "
             "2 parlay cards and 1 single (5 legs).", msg)
p.unlink()

football_payload = {
    "date": "2026-09-21", "endDate": "2026-09-22", "note": "",
    "windows": [{"title": "x", "tickets": [{"legs": [1, 2]}]}], "singles": [],
}
p = write_tmp("football", football_payload)
msg = nd.summarize(p, "football")
check("A2 football: a date RANGE when endDate differs from date, no singles clause",
      msg == "✅ Football picks for 2026-09-21 to 2026-09-22 are live on https://bmbs.bet/football/ — "
             "1 parlay card (2 legs).", msg)
p.unlink()

same_day_payload = {"date": "2026-09-20", "endDate": "2026-09-20", "windows": [], "singles": [{}, {}]}
p = write_tmp("football_sameday", same_day_payload)
msg = nd.summarize(p, "football")
check("A3 football: endDate == date collapses to one date, plural singles, no cards clause",
      msg == "✅ Football picks for 2026-09-20 are live on https://bmbs.bet/football/ — "
             "2 singles (0 legs).", msg)
p.unlink()

empty_payload = {"date": "2026-09-20", "windows": [], "singles": []}
p = write_tmp("empty", empty_payload)
msg = nd.summarize(p, "baseball")
check("A4 a slate with nothing on it still produces a sane message",
      "no bets" in msg, msg)
p.unlink()


# ---------------- B. post_message() / edit_message() -- the HTTP shape ----------------

calls = []


def fake_fetcher(url, payload, method):
    calls.append((url, payload, method))
    if method == "POST":
        return {"id": "999888777"}
    return {}


calls.clear()
mid = nd.post_message("hello world", fetcher=fake_fetcher)
check("B1 post_message hits '?wait=true' so Discord returns the message id", calls[0][0].endswith("?wait=true"))
check("B2 post_message sends the text as {'content': ...} via POST",
      calls[0][1] == {"content": "hello world"} and calls[0][2] == "POST")
check("B3 post_message returns the id from the response", mid == "999888777", mid)

calls.clear()
nd.edit_message("42", "updated text", fetcher=fake_fetcher)
check("B4 edit_message hits '/messages/<id>' via PATCH",
      calls[0][0].endswith("/messages/42") and calls[0][2] == "PATCH")
check("B5 edit_message sends the new text", calls[0][1] == {"content": "updated text"})


# ---------------- C. the webhook URL never leaks, even on failure ----------------

class FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code):
        super().__init__("https://discord.test/should-not-appear/fake-token", code, "boom", {}, BytesIO(b""))


def raising_urlopen(req, timeout=15):
    raise FakeHTTPError(404)


import io
import contextlib

real_urlopen = nd.urllib.request.urlopen
nd.urllib.request.urlopen = raising_urlopen
buf = io.StringIO()
try:
    with contextlib.redirect_stderr(buf):
        nd._request("https://discord.test/api/webhooks/123/fake-token", {"content": "x"}, "POST")
    check("C1 a failed request exits rather than returning", False)
except SystemExit as e:
    check("C1 a failed request exits with the HTTP status", "404" in str(e), str(e))
    check("C2 the webhook URL itself never reaches the error message",
          "discord.test" not in str(e) and "fake-token" not in str(e), str(e))
finally:
    nd.urllib.request.urlopen = real_urlopen

del os.environ["DISCORD_STATUS_WEBHOOK"]
try:
    nd.webhook_url()
    check("C3 a missing DISCORD_STATUS_WEBHOOK exits rather than crashing with a KeyError", False)
except SystemExit:
    check("C3 a missing DISCORD_STATUS_WEBHOOK exits rather than crashing with a KeyError", True)
os.environ["DISCORD_STATUS_WEBHOOK"] = "https://discord.test/api/webhooks/123/fake-token"


# ---------------- D. the CLI wires each subcommand to the right function ----------------

cli_calls = []
nd.post_message = lambda text, fetcher=nd._request: (cli_calls.append(("post", text)), "111")[1]
nd.edit_message = lambda mid, text, fetcher=nd._request: cli_calls.append(("edit", mid, text))

sys.argv = ["notify_discord.py", "post", "--text", "hi there"]
nd.main()
check("D1 'post' subcommand calls post_message with the given text", cli_calls[-1] == ("post", "hi there"), cli_calls)

sys.argv = ["notify_discord.py", "edit", "--id", "55", "--text", "updated"]
nd.main()
check("D2 'edit' subcommand calls edit_message with the given id and text",
      cli_calls[-1] == ("edit", "55", "updated"), cli_calls)

success_payload = {"date": "2026-09-20", "windows": [], "singles": [{}]}
p = write_tmp("cli_success", success_payload)
sys.argv = ["notify_discord.py", "success", "--tickets", str(p), "--sport", "baseball"]
nd.main()
check("D3 'success' subcommand posts the summarize()'d message",
      cli_calls[-1][0] == "post" and "Baseball picks for 2026-09-20" in cli_calls[-1][1], cli_calls[-1])
p.unlink()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all notify-discord checks passed")
