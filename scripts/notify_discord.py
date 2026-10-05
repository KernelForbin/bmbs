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

SITE_URL = {"baseball": "https://bmbs.bet/mlb/", "football": "https://bmbs.bet/football/",
            "combined": "https://bmbs.bet/", "hockey": "https://bmbs.bet/hockey/",
            "basketball": "https://bmbs.bet/basketball/",
            "wnba": "https://bmbs.bet/wnba/",
            "cfb": "https://bmbs.bet/cfb/"}


class NotifyFailed(Exception):
    """A Discord call didn't get through. Raised rather than exiting, so main()
    can decide -- and main() always decides the same way: warn, exit 0. See the
    note there; a status ping must never be able to fail the job around it."""


def webhook_url():
    url = os.environ.get("DISCORD_STATUS_WEBHOOK")
    if not url:
        raise NotifyFailed("DISCORD_STATUS_WEBHOOK is not set")
    return url


# Discord sits behind Cloudflare, which BLOCKS the default Python user agent
# outright: a request with no User-Agent comes back 403 with Cloudflare "error
# code: 1010" and never reaches Discord at all. That is exactly what happened
# on 2026-09-22 -- every Discord call this project had ever made in CI failed
# this way, unnoticed, because the tests all used a fake fetcher. Send a real
# one. (Proved by probing a deliberately bogus webhook id: without this header
# 403/1010, with it 404 "Unknown Webhook" -- i.e. Discord actually answering.)
USER_AGENT = "bmbs-status-bot (https://bmbs.bet, 1.0)"


def _request(url, payload, method):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # Never let the webhook URL itself reach stderr/logs.
        raise NotifyFailed(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise NotifyFailed(f"{type(e).__name__}") from None


def post_message(text, fetcher=_request, mention=None):
    """Post a fresh message, return its id.

    `mention` is a Discord USER ID. It's prefixed as <@id> so the person who
    uploaded the card actually gets a notification, which is the point: an
    edit to an older message changes no timestamp and pings nobody, so there
    was no way to tell WHEN a card went live.

    allowed_mentions is set explicitly rather than relied on. A webhook's
    default is to parse everything in the content, so a card that happened to
    contain "@everyone" could otherwise ping the whole server; naming `users`
    means only a real user mention resolves.
    """
    payload = {"content": text, "allowed_mentions": {"parse": ["users"]}}
    if mention:
        payload["content"] = f"<@{mention}> {text}"
    return fetcher(webhook_url() + "?wait=true", payload, "POST")["id"]


def edit_message(message_id, text, fetcher=_request):
    fetcher(f"{webhook_url()}/messages/{message_id}", {"content": text}, "PATCH")


def slate_key(data, sport):
    """What makes two tickets files the SAME slate. A football card is an NFL
    week (a second card in the same week replaces the first); the other two
    are a day."""
    if not data:
        return None
    if sport == "football":
        return data.get("weekEnds") or data.get("date")
    return data.get("date")


def _leg_sig(leg):
    return tuple(str(leg.get(k) if leg.get(k) is not None else "")
                 for k in ("player", "team", "market", "line", "side", "odds", "quarter", "who"))


def _cards(data):
    out = {}
    for w in data.get("windows", []):
        for t in w.get("tickets", []):
            out[t.get("name") or ""] = t
    return out


def what_changed(before, after):
    """Plain-English list of what a correction changed: cards added or
    dropped, legs and stakes corrected. Empty when nothing on the bets
    themselves moved (a team or date fixed behind the scenes)."""
    from collections import Counter
    old, new = _cards(before), _cards(after)
    added = [n for n in new if n not in old]
    dropped = [n for n in old if n not in new]
    legs_fixed = 0
    money_fixed = 0
    for name in new.keys() & old.keys():
        a = Counter(map(_leg_sig, old[name].get("legs", [])))
        b = Counter(map(_leg_sig, new[name].get("legs", [])))
        legs_fixed += max(sum((b - a).values()), sum((a - b).values()))
        if (old[name].get("stake"), old[name].get("payout")) != (new[name].get("stake"), new[name].get("payout")):
            money_fixed += 1
    sg_old = Counter(map(_leg_sig, before.get("singles", [])))
    sg_new = Counter(map(_leg_sig, after.get("singles", [])))
    singles_added = sum((sg_new - sg_old).values())
    singles_dropped = sum((sg_old - sg_new).values())

    def plural(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"
    bits = []
    if added:
        bits.append(f"{', '.join(added)} added" if len(added) <= 3 else f"{plural(len(added), 'card')} added")
    if dropped:
        bits.append(f"{', '.join(dropped)} removed" if len(dropped) <= 3 else f"{plural(len(dropped), 'card')} removed")
    if legs_fixed:
        bits.append(f"{plural(legs_fixed, 'leg')} corrected")
    if money_fixed:
        bits.append(f"stake/payout corrected on {plural(money_fixed, 'card')}")
    if singles_added and singles_dropped:
        bits.append(f"{plural(max(singles_added, singles_dropped), 'single')} corrected")
    elif singles_added:
        bits.append(f"{plural(singles_added, 'single')} added")
    elif singles_dropped:
        bits.append(f"{plural(singles_dropped, 'single')} removed")
    return bits


def summarize(tickets_path, sport, before_path=None, fixed=False):
    """The success message. With `before_path` -- the live tickets file as it
    stood before this parse -- a card that REPLACES the slate already on the
    site is announced as an update, saying what changed, rather than as a new
    slate going live (the user's call, 2026-10-04: "are now LIVE ... Tracking
    has started" on a correction read as if nothing had been live before).
    A missing or unreadable `before` file, or a different slate, is a new one."""
    data = json.loads(open(tickets_path, encoding="utf-8").read())
    before = None
    if before_path:
        try:
            before = json.loads(open(before_path, encoding="utf-8").read())
        except (OSError, ValueError):
            before = None
    if before is not None and slate_key(before, sport) == slate_key(data, sport):
        return _update_message(data, before, sport, fixed)
    return ("\U0001F6E0️ Fixed automatically — " if fixed else "") + _live_message(data, sport)


def _counts(data):
    windows = data.get("windows", [])
    cards = sum(len(w["tickets"]) for w in windows)
    legs = sum(len(c["legs"]) for w in windows for c in w["tickets"])
    singles = len(data.get("singles", []))
    bits = []
    if cards:
        bits.append(f"{cards} parlay card{'s' if cards != 1 else ''}")
    if singles:
        bits.append(f"{singles} single{'s' if singles != 1 else ''}")
    # Parlay legs only, as the message has always counted them.
    return (" and ".join(bits) if bits else "no bets"), legs


def _span(data, sport):
    if sport in ("football", "combined") and data.get("endDate") and data["endDate"] != data["date"]:
        return f'{data["date"]} to {data["endDate"]}'
    return data["date"]


LABEL = {"football": "NFL picks", "combined": "All Sports picks", "hockey": "NHL picks", "basketball": "NBA picks", "wnba": "WNBA picks", "cfb": "College football picks"}


def _update_message(data, before, sport, fixed):
    detail, legs = _counts(data)
    changed = what_changed(before, data)
    why = ("; ".join(changed) + ".") if changed else "details on the existing bets were corrected."
    lead = "\U0001F6E0\uFE0F Fixed automatically \u2014 the" if fixed else "\U0001F504 The"
    return (f"{lead} {LABEL.get(sport, 'MLB picks')} card for {_span(data, sport)} has been UPDATED on "
            f"{SITE_URL[sport]} with corrected information: {why} "
            f"It now holds {detail} ({legs} legs). Tracking carries on with the updated card.")


def _live_message(data, sport):
    detail, legs = _counts(data)
    # "are live NOW" on purpose. The bot's own push confirmation already says
    # the parser "should update within about a minute", which is a promise
    # rather than a fact -- this message is the fact, and its Discord
    # timestamp is what tells the group when it actually happened.
    return (f"✅ {LABEL.get(sport, 'MLB picks')} for {_span(data, sport)} are now LIVE on {SITE_URL[sport]} "
            f"— {detail} ({legs} legs). Tracking has started.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("success")
    s.add_argument("--tickets", required=True)
    s.add_argument("--sport", required=True, choices=["baseball", "football", "combined", "hockey", "basketball", "wnba", "cfb"])
    s.add_argument("--mention", default="", help="Discord user id to @, if known.")
    s.add_argument("--before", default="",
                   help="The live tickets file as it was BEFORE this parse. When it holds the "
                        "same slate, the message announces an update rather than a new slate.")
    s.add_argument("--fixed", action="store_true",
                   help="The card went live through the automatic fixer; say so.")

    p = sub.add_parser("post")
    p.add_argument("--text", required=True)
    p.add_argument("--mention", default="",
                   help="Discord user id to @ -- the person who uploaded the card. "
                        "Absent or blank simply posts without one.")

    e = sub.add_parser("edit")
    e.add_argument("--id", required=True)
    e.add_argument("--text", required=True)

    args = ap.parse_args()
    # THIS SCRIPT MUST NOT BE ABLE TO FAIL ITS CALLER. It posts status; it does
    # no work anybody depends on. On 2026-09-22 a 403 from this script's first
    # call killed the auto-fix job at step 4 of 8, so the picks were never
    # repaired and -- the real damage -- the group got no message at all, which
    # is the silent failure the whole notifier exists to prevent. A missing
    # ping is a nuisance; a blocked repair is an outage. Warn and exit 0.
    # The workflow steps also carry continue-on-error as a second layer.
    try:
        if args.cmd == "success":
            post_message(summarize(args.tickets, args.sport, args.before or None, args.fixed),
                         mention=(args.mention or "").strip() or None)
        elif args.cmd == "post":
            print(post_message(args.text, mention=(args.mention or "").strip() or None))
        elif args.cmd == "edit":
            edit_message(args.id, args.text)
    except NotifyFailed as e:
        # stderr, so `MSG_ID=$(... post ...)` captures an empty id rather than
        # this text -- the workflow already falls back to posting fresh when
        # the id is empty.
        print(f"WARNING: Discord notification failed: {e}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
