#!/usr/bin/env python3
"""
Posts (or edits) a status message in the group's Discord channel via an
incoming webhook, so an upload's status is always ONE message that gets
updated in place rather than a stream of step-by-step pings.

Three subcommands:
  success   called from parse-picks.yml / parse-football-picks.yml right
            after a real commit -- summarizes the freshly-written
            tickets.json and posts it. Fast, deterministic, no AI involved.
  post      posts a fresh message with the given text and prints its id
            (used by the automated fix-and-recover routine to open with an
            "investigating" message it can edit later).
  edit      edits a message this same webhook posted earlier, by id.

Needs DISCORD_STATUS_WEBHOOK in the environment (a GitHub Actions secret --
the same webhook posts for both sports, since they share one intake
channel). Never logs, prints, or echoes the webhook URL itself; a bad
request only ever surfaces the HTTP status, never the URL.

    python scripts/notify_discord.py success --tickets data/tickets.json --sport baseball
    python scripts/notify_discord.py post --text "..."      # prints the message id
    python scripts/notify_discord.py edit --id 123 --text "..."
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SITE_URL = {"baseball": "https://bmbs.bet/", "football": "https://bmbs.bet/football/"}


def webhook_url():
    url = os.environ.get("DISCORD_STATUS_WEBHOOK")
    if not url:
        sys.exit("DISCORD_STATUS_WEBHOOK is not set")
    return url


def _request(url, payload, method):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # Never let the webhook URL itself reach stderr/logs.
        sys.exit(f"Discord webhook request failed: HTTP {e.code}")


def post_message(text, fetcher=_request):
    """Post a fresh message, return its id (so it can be edited later)."""
    return fetcher(webhook_url() + "?wait=true", {"content": text}, "POST")["id"]


def edit_message(message_id, text, fetcher=_request):
    fetcher(f"{webhook_url()}/messages/{message_id}", {"content": text}, "PATCH")


def summarize(tickets_path, sport):
    data = json.loads(open(tickets_path, encoding="utf-8").read())
    windows = data.get("windows", [])
    cards = sum(len(w["tickets"]) for w in windows)
    legs = sum(len(c["legs"]) for w in windows for c in w["tickets"])
    singles = len(data.get("singles", []))
    if sport == "football" and data.get("endDate") and data["endDate"] != data["date"]:
        span = f'{data["date"]} to {data["endDate"]}'
    else:
        span = data["date"]
    label = "Football picks" if sport == "football" else "Baseball picks"
    bits = []
    if cards:
        bits.append(f"{cards} parlay card{'s' if cards != 1 else ''}")
    if singles:
        bits.append(f"{singles} single{'s' if singles != 1 else ''}")
    detail = " and ".join(bits) if bits else "no bets"
    return f"✅ {label} for {span} are live on {SITE_URL[sport]} — {detail} ({legs} legs)."


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("success")
    s.add_argument("--tickets", required=True)
    s.add_argument("--sport", required=True, choices=["baseball", "football"])

    p = sub.add_parser("post")
    p.add_argument("--text", required=True)

    e = sub.add_parser("edit")
    e.add_argument("--id", required=True)
    e.add_argument("--text", required=True)

    args = ap.parse_args()
    if args.cmd == "success":
        post_message(summarize(args.tickets, args.sport))
    elif args.cmd == "post":
        print(post_message(args.text))
    elif args.cmd == "edit":
        edit_message(args.id, args.text)


if __name__ == "__main__":
    main()
