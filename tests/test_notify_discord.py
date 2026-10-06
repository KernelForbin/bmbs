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
# "are now LIVE" rather than "are live": the BOT's own push confirmation
# already says the parser "should update within about a minute", which is a
# promise. This message is the fact, and its Discord timestamp is what tells
# the group when it actually happened.
check("A1 baseball: card + single + leg counts, singular single",
      msg == "✅ MLB picks for 2026-09-20 are now LIVE on https://bmbs.bet/mlb/ — "
             "2 parlay cards and 1 single (5 legs). Tracking has started.", msg)
p.unlink()

football_payload = {
    "date": "2026-09-21", "endDate": "2026-09-22", "note": "",
    "windows": [{"title": "x", "tickets": [{"legs": [1, 2]}]}], "singles": [],
}
p = write_tmp("football", football_payload)
msg = nd.summarize(p, "football")
check("A2 football: a date RANGE when endDate differs from date, no singles clause",
      msg == "✅ NFL picks for 2026-09-21 to 2026-09-22 are now LIVE on https://bmbs.bet/football/ — "
             "1 parlay card (2 legs). Tracking has started.", msg)
p.unlink()

same_day_payload = {"date": "2026-09-20", "endDate": "2026-09-20", "windows": [], "singles": [{}, {}]}
p = write_tmp("football_sameday", same_day_payload)
msg = nd.summarize(p, "football")
check("A3 football: endDate == date collapses to one date, plural singles, no cards clause",
      msg == "✅ NFL picks for 2026-09-20 are now LIVE on https://bmbs.bet/football/ — "
             "2 singles (0 legs). Tracking has started.", msg)
p.unlink()

empty_payload = {"date": "2026-09-20", "windows": [], "singles": []}
p = write_tmp("empty", empty_payload)
msg = nd.summarize(p, "baseball")
check("A4 a slate with nothing on it still produces a sane message",
      "no bets" in msg, msg)
p.unlink()


# ---------------- B. post_message() -- the HTTP shape ----------------

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
      calls[0][1]["content"] == "hello world" and calls[0][2] == "POST")
# allowed_mentions is set EXPLICITLY, not left to the webhook default: a
# webhook parses everything in the content, so a card containing "@everyone"
# could otherwise ping the whole server. Naming `users` means only a real
# user mention resolves.
check("B2b post_message pins allowed_mentions to users only",
      calls[0][1].get("allowed_mentions") == {"parse": ["users"]}, calls[0][1])

calls.clear()
nd.post_message("it's live", fetcher=fake_fetcher, mention="42")
check("B2c a mention is prefixed as <@id> so the uploader actually gets pinged",
      calls[0][1]["content"] == "<@42> it's live", calls[0][1])
check("B3 post_message returns the id from the response", mid == "999888777", mid)


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
    check("C1 a failed request raises rather than returning", False)
except nd.NotifyFailed as e:
    check("C1 a failed request raises NotifyFailed with the HTTP status", "404" in str(e), str(e))
    check("C2 the webhook URL itself never reaches the error message",
          "discord.test" not in str(e) and "fake-token" not in str(e), str(e))
finally:
    nd.urllib.request.urlopen = real_urlopen

del os.environ["DISCORD_STATUS_WEBHOOK"]
try:
    nd.webhook_url()
    check("C3 a missing DISCORD_STATUS_WEBHOOK raises rather than crashing with a KeyError", False)
except nd.NotifyFailed:
    check("C3 a missing DISCORD_STATUS_WEBHOOK raises rather than crashing with a KeyError", True)
os.environ["DISCORD_STATUS_WEBHOOK"] = "https://discord.test/api/webhooks/123/fake-token"

# C4-C6 are the ones that matter most. On 2026-09-22 a 403 from this script
# aborted the auto-fix workflow at step 4 of 8: the picks were never repaired
# and the group got no message at all -- the exact silent failure the notifier
# exists to prevent. A status ping must never be able to fail its caller.
nd.urllib.request.urlopen = raising_urlopen
buf = io.StringIO()
try:
    sys.argv = ["notify_discord.py", "post", "--text", "will not get through"]
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()) as out:
        rc = nd.main()
    check("C4 main() returns 0 when Discord is unreachable -- it must not fail the job", rc == 0, repr(rc))
    check("C5 ...and says so on stderr, so a dead webhook is still visible in the run log",
          "WARNING" in buf.getvalue() and "404" in buf.getvalue(), buf.getvalue().strip())
    check("C6 ...printing nothing to stdout, so `MSG_ID=$(...)` captures an empty id",
          out.getvalue().strip() == "", repr(out.getvalue()))
finally:
    nd.urllib.request.urlopen = real_urlopen

# The header that was missing for two days. Capture the Request object rather
# than trusting the constant: what matters is what actually goes on the wire.
captured = {}


def capturing_urlopen(req, timeout=15):
    captured["headers"] = dict(req.header_items())
    raise FakeHTTPError(404)   # we only care about the request, not a reply


nd.urllib.request.urlopen = capturing_urlopen
try:
    nd._request("https://discord.test/api/webhooks/123/fake-token", {"content": "x"}, "POST")
except nd.NotifyFailed:
    pass
finally:
    nd.urllib.request.urlopen = real_urlopen

hdrs = {k.lower(): v for k, v in captured.get("headers", {}).items()}
check("C7 the request carries a User-Agent -- without one Cloudflare 403s every call",
      bool(hdrs.get("user-agent")), str(hdrs))
check("C8 ...and it isn't Python's default, which is the one Cloudflare blocks",
      "urllib" not in hdrs.get("user-agent", "").lower(), str(hdrs))


# ---------------- D. the CLI wires each subcommand to the right function ----------------

cli_calls = []
nd.post_message = lambda text, fetcher=nd._request, mention=None: (
    cli_calls.append(("post", text, mention)), "111")[1]

sys.argv = ["notify_discord.py", "post", "--text", "hi there"]
nd.main()
check("D1 'post' subcommand calls post_message with the given text",
      cli_calls[-1] == ("post", "hi there", None), cli_calls)

# The @ mention is the point of the new flow: an EDIT to an older message
# changes no timestamp and pings nobody, so there was no way to tell when a
# card actually went live.
sys.argv = ["notify_discord.py", "post", "--text", "fixed", "--mention", "12345"]
nd.main()
check("D1b 'post --mention' passes the user id through",
      cli_calls[-1] == ("post", "fixed", "12345"), cli_calls)
sys.argv = ["notify_discord.py", "post", "--text", "fixed", "--mention", "  "]
nd.main()
check("D1c a blank mention posts without one, rather than an empty <@>",
      cli_calls[-1] == ("post", "fixed", None), cli_calls)

check("D2 there is no 'edit' subcommand -- every status is a new, pinging post",
      not hasattr(nd, "edit_message"))

success_payload = {"date": "2026-09-20", "windows": [], "singles": [{}]}
p = write_tmp("cli_success", success_payload)
sys.argv = ["notify_discord.py", "success", "--tickets", str(p), "--sport", "baseball"]
nd.main()
check("D3 'success' subcommand posts the summarize()'d message",
      cli_calls[-1][0] == "post" and "MLB picks for 2026-09-20" in cli_calls[-1][1], cli_calls[-1])

# The ORDINARY success has to reach the uploader too -- a mutation showed
# nothing was checking that this subcommand passed the mention on at all, so
# it could have been dropped on the floor and every test still passed.
sys.argv = ["notify_discord.py", "success", "--tickets", str(p), "--sport", "baseball",
            "--mention", "99887766"]
nd.main()
check("D3b 'success --mention' reaches post_message, so the uploader is pinged",
      cli_calls[-1][2] == "99887766", cli_calls[-1])
sys.argv = ["notify_discord.py", "success", "--tickets", str(p), "--sport", "baseball"]
nd.main()
check("D3c ...and with no --mention it posts without one",
      cli_calls[-1][2] is None, cli_calls[-1])
p.unlink()


# ---------------- E. a correction says so ----------------
# The user's call (2026-10-04): when a card is changed after its picks are
# already live -- a bet added, a leg fixed -- the ping must say the card was
# UPDATED with corrected information, not repeat "are now LIVE ... Tracking
# has started", which read as though nothing had been live before.
def leg(player, team="DET", market="q_score", quarter=1, who="Kenny"):
    return {"player": player, "team": team, "market": market, "quarter": quarter, "who": who, "odds": None}


def card(name, legs, stake=8.0, payout=80.0):
    return {"name": name, "legs": legs, "stake": stake, "payout": payout}


BEFORE = {"date": "2026-10-04", "endDate": "2026-10-04",
          "windows": [{"title": "x", "tickets": [card("Ticket 1", [leg("A"), leg("B")]),
                                                  card("Ticket 2", [leg("C"), leg("D")])]}],
          "singles": [leg("S1")]}


def after(**changes):
    d = json.loads(json.dumps(BEFORE))
    d.update(changes)
    return d


b = write_tmp("before", BEFORE)

added = after()
added["windows"][0]["tickets"].append(card("Ticket 12", [leg("Lions"), leg("Panthers", team="CAR")]))
a = write_tmp("after", added)
msg = nd.summarize(a, "combined", b)
check("E1 the same slate again is an UPDATE, not a new slate going live",
      "UPDATED" in msg and "now LIVE" not in msg and "Tracking has started" not in msg, msg)
check("E2 ...that says it was corrected, and what changed",
      "corrected information" in msg and "Ticket 12 added" in msg, msg)
check("E3 ...and what the card holds now", "3 parlay cards and 1 single (6 legs)" in msg, msg)

fixed_leg = after()
fixed_leg["windows"][0]["tickets"][1]["legs"][0] = leg("C", market="rec_yds")
a = write_tmp("after", fixed_leg)
msg = nd.summarize(a, "combined", b)
check("E4 a leg changed on a card already there is a corrected leg", "1 leg corrected" in msg, msg)

money = after()
money["windows"][0]["tickets"][0]["payout"] = 92.5
a = write_tmp("after", money)
msg = nd.summarize(a, "combined", b)
check("E5 a corrected payout is named too", "stake/payout corrected on 1 card" in msg, msg)

quiet = after(endDate="2026-10-04")
quiet["windows"][0]["tickets"][0]["legs"][0]["meta"] = "DET"   # display only
a = write_tmp("after", quiet)
msg = nd.summarize(a, "combined", b)
check("E6 a fix to nothing on the bets themselves still says it was an update, plainly",
      "UPDATED" in msg and "details on the existing bets were corrected" in msg, msg)

new_day = after(date="2026-10-05", endDate="2026-10-05")
a = write_tmp("after", new_day)
msg = nd.summarize(a, "combined", b)
check("E7 a DIFFERENT slate is still announced as newly live", "are now LIVE" in msg and "UPDATED" not in msg, msg)

msg = nd.summarize(a, "combined", REPO_TMP / "_scratch_does_not_exist.json")
check("E8 no earlier file (a first card) is a new slate, not an error", "are now LIVE" in msg, msg)

wk_before = write_tmp("wk_before", {"date": "2026-10-01", "endDate": "2026-10-06", "weekEnds": "2026-10-06",
                                    "windows": [], "singles": [leg("X")]})
wk_after = write_tmp("wk_after", {"date": "2026-10-04", "endDate": "2026-10-06", "weekEnds": "2026-10-06",
                                  "windows": [], "singles": [leg("X"), leg("Y")]})
msg = nd.summarize(wk_after, "football", wk_before)
check("E9 football: a second card in the same NFL WEEK is an update, though its first day differs",
      "UPDATED" in msg and "1 single added" in msg, msg)

a = write_tmp("after", added)
msg = nd.summarize(a, "combined", b, fixed=True)
check("E10 the auto-fixer's success says it fixed it AND that it updated a live card",
      msg.startswith("\U0001F6E0️ Fixed automatically") and "UPDATED" in msg, msg)
msg = nd.summarize(a, "combined", None, fixed=True)
check("E11 ...and a fixed NEW slate says fixed and now live",
      msg.startswith("\U0001F6E0️ Fixed automatically") and "are now LIVE" in msg, msg)

sys.argv = ["notify_discord.py", "success", "--tickets", str(a), "--sport", "combined", "--before", str(b)]
nd.main()
check("E12 the CLI's --before reaches the message", "UPDATED" in cli_calls[-1][1], cli_calls[-1])
sys.argv = ["notify_discord.py", "success", "--tickets", str(a), "--sport", "combined", "--before", str(b), "--fixed"]
nd.main()
check("E13 ...and so does --fixed", cli_calls[-1][1].startswith("\U0001F6E0"), cli_calls[-1])
for f in (a, b, wk_before, wk_after):
    f.unlink()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all notify-discord checks passed")
