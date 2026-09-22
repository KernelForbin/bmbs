#!/usr/bin/env python3
"""
The ONE notify-discord test that really reaches discord.com.

`test_notify_discord.py` is fully offline -- a fake fetcher, so nothing ever
posts to the group's channel. That is the right default, and it is also exactly
why this project shipped a Discord notifier that had NEVER once worked:

  On 2026-09-22 every Discord call the site had ever made in CI came back
  403 with Cloudflare "error code: 1010". Discord sits behind Cloudflare, which
  blocks requests carrying the default Python user agent. The request never
  reached Discord at all. A fake fetcher cannot see that, because the bug lives
  entirely in the HTTP layer the fake replaces. It went unnoticed for two days
  because the success path had no chance to run in between.

So: this hits the real host, using notify_discord.py's own headers, against a
deliberately BOGUS webhook id. Nothing is ever posted anywhere -- the whole
point is the STATUS CODE.

  403 + Cloudflare 1010  -> Cloudflare blocked us; no Discord call can work
  404 "Unknown Webhook"  -> we reached Discord, which rejected the fake id

Needs network. Run it after touching the request headers:

    python tests/test_notify_discord_live.py
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import notify_discord  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# A syntactically valid but nonexistent webhook. Discord answers 404 for it.
BOGUS = ("https://discord.com/api/webhooks/000000000000000000/"
         + "a" * 68)


def probe(headers):
    """-> (status, body) for a POST to the bogus webhook."""
    body = json.dumps({"content": "connectivity probe -- never delivered"}).encode()
    req = urllib.request.Request(BOGUS + "?wait=true", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode()[:200]
        except Exception:
            return e.code, ""


print("probing discord.com with a bogus webhook id (nothing is posted)\n")

# ---------------- A. the headers the script actually sends get through ----------------
sent = {"Content-Type": "application/json", "User-Agent": notify_discord.USER_AGENT}
status, body = probe(sent)
check("A1 notify_discord's own headers are not blocked by Cloudflare",
      status != 403, f"HTTP {status} {body}")
check("A2 ...and the request actually reaches Discord (404 Unknown Webhook)",
      status == 404 and "Unknown Webhook" in body, f"HTTP {status} {body}")

# ---------------- B. the exact bug, so this test can prove itself ----------------
# Without a User-Agent the same request is blocked. If this ever stops being
# true, A1/A2 above have stopped proving anything and this file needs rethinking.
status_nb, body_nb = probe({"Content-Type": "application/json"})
check("B1 the no-User-Agent request IS still blocked -- so A1 is a real check",
      status_nb == 403, f"HTTP {status_nb} {body_nb}")

# ---------------- C. the script sets a User-Agent at all ----------------
check("C1 USER_AGENT is a non-empty, non-default agent",
      bool(notify_discord.USER_AGENT) and "urllib" not in notify_discord.USER_AGENT.lower(),
      notify_discord.USER_AGENT)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all live notify-discord checks passed")
