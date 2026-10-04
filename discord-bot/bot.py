"""
Discord intake bot for bmbs.bet picks.

A friend uploads the day's raw picks text (e.g. out of his own Gemini
session) as a .txt file attachment in a designated Discord channel. This
bot:

1. Reads the attached .txt file.
2. Posts a preview asking for confirmation via reactions.
3. On thumbs-up, pushes that content to the right sport's incoming file on
   `main` via the GitHub Contents API, and that sport's parse workflow
   takes it from there.

Which sport is decided by the FILE NAME, and nothing else:

    baseball*.txt  ->  data/incoming_picks.txt           (home run cards)
    football*.txt  ->  data/football/incoming_picks.txt  (touchdown cards)

Anything else is refused with a note saying how to rename it. The card text
is deliberately not inspected: both sports' cards share a template, and a
guess that's right most of the time would eventually file a touchdown card
under baseball and wipe that day's home run slate.

Using a file attachment (rather than pasted message text) sidesteps
Discord's 2000-character message cap, which a real day's picks routinely
exceed.

This bot never touches tickets.json or the parsing logic itself -- it only
ever writes an incoming_picks.txt, exactly like a human pasting into
GitHub's web editor would.
"""

import asyncio
import base64
import os

import discord
import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
ALLOWED_USER_IDS = {
    int(uid) for uid in os.environ.get("DISCORD_ALLOWED_USER_IDS", "").split(",") if uid.strip()
}

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "KernelForbin/bmbs")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

# file-name prefix -> where that sport's card goes
ROUTES = {
    "baseball": {"path": "data/incoming_picks.txt", "sport": "MLB (home runs)", "site": "https://bmbs.bet/"},
    "football": {"path": "data/football/incoming_picks.txt", "sport": "NFL (touchdowns)", "site": "https://bmbs.bet/football/"},
    "sports": {"path": "data/combined/incoming_picks.txt", "sport": "MLB + NFL", "site": "https://bmbs.bet/all/"},
}


def route_for(filename: str):
    """The ROUTES entry for this file name, or None if it names no sport."""
    lowered = filename.strip().lower()
    return next((route for prefix, route in ROUTES.items() if lowered.startswith(prefix)), None)


CONFIRM_TIMEOUT_SECONDS = float(os.environ.get("CONFIRM_TIMEOUT_SECONDS", "60"))
PREVIEW_CHARS = 400

GITHUB_API_BASE = "https://api.github.com"

intents = discord.Intents.default()
# Attachments (and content) on other users' guild messages are gated behind
# this privileged intent -- without it, message.attachments comes back
# empty even though the file was uploaded. Must also be enabled in the
# Discord Developer Portal (Bot -> Privileged Gateway Intents).
intents.message_content = True
client = discord.Client(intents=intents)


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def push_incoming_picks(text: str, author_name: str, file_path: str, sport: str,
                        author_id: str = "") -> str:
    """Commit `text` as the new `file_path` on GITHUB_BRANCH.

    Returns the commit URL, or None when the file ALREADY has exactly this
    content and there is nothing to push.

    That case is not hypothetical and it used to fail silently: re-posting an
    identical card on 2026-09-30 produced an EMPTY commit, and because
    parse-picks.yml triggers on a change to data/incoming_picks.txt, the
    workflow never fired -- no parse, no Discord message -- while the bot had
    already announced "the parser should update within about a minute". The
    caller reports this honestly instead.

    Raises requests.HTTPError on a genuine failure.
    """
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{file_path}"

    get_resp = requests.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15)
    payload = {
        # The Discord USER ID rides along in the commit message so the
        # workflows can @ the person who uploaded when they report back. A
        # username can't be mentioned -- Discord needs the id -- and this is
        # the only channel between the bot and a GitHub Action. The format is
        # parsed by auto-fix-parse-failure.yml; keep the brackets.
        "message": (f"Picks upload ({sport}) from {author_name}"
                    + (f" [discord:{author_id}]" if author_id else "")
                    + " via Discord bot"),
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if get_resp.status_code != 404:   # 404 = first upload for this sport: create rather than replace
        get_resp.raise_for_status()
        existing = get_resp.json()
        payload["sha"] = existing["sha"]
        # Byte-for-byte identical -> GitHub makes an empty commit, which
        # changes no path and so triggers nothing. Say so rather than push it.
        try:
            current = base64.b64decode(existing.get("content") or "").decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            current = None
        if current is not None and current == text:
            return None

    put_resp = requests.put(url, headers=_github_headers(), json=payload, timeout=15)
    put_resp.raise_for_status()
    return put_resp.json()["commit"]["html_url"]


async def _confirm_and_push(
    channel: discord.abc.Messageable, author: discord.abc.User, text: str, filename: str, route: dict
) -> None:
    text = text.strip() + "\n"
    line_count = text.count("\n")
    preview = text[:PREVIEW_CHARS]
    truncated_note = "..." if len(text) > PREVIEW_CHARS else ""

    prompt = await channel.send(
        f"Got `{filename}` from {author.mention} ({len(text)} chars / {line_count} lines) "
        f"-- a **{route['sport']}** card.\n"
        f"React with ✅ to push to `{route['path']}` (triggers the picks parser), "
        f"or ❌ to discard.\n```\n{preview}{truncated_note}\n```"
    )
    await prompt.add_reaction("✅")
    await prompt.add_reaction("❌")

    def check(reaction: discord.Reaction, user: discord.abc.User) -> bool:
        return (
            reaction.message.id == prompt.id
            and user.id == author.id
            and str(reaction.emoji) in ("✅", "❌")
        )

    try:
        reaction, _ = await client.wait_for("reaction_add", timeout=CONFIRM_TIMEOUT_SECONDS, check=check)
    except asyncio.TimeoutError:
        await channel.send(f"⏳ Timed out waiting for confirmation from {author.mention} -- nothing pushed.")
        return

    if str(reaction.emoji) == "❌":
        await channel.send("Discarded.")
        return

    try:
        commit_url = push_incoming_picks(text, str(author), route["path"], route["sport"],
                                         author_id=str(author.id))
        if commit_url is None:
            await channel.send(
                f"\u2139\ufe0f {author.mention} that's byte-for-byte what's already in "
                f"`{route['path']}`, so there's nothing to push and the parser won't re-run.\n"
                f"If the card changed, re-export it; if you want to force a re-parse of the "
                f"same text, say so and it can be kicked off manually."
            )
            return
    except requests.HTTPError as exc:
        await channel.send(f"❌ Push failed: `{exc}`")
        return

    await channel.send(
        f"✅ Pushed to `{route['path']}`: {commit_url}\n"
        f"The parser should update {route['site']} within about a minute."
    )


@client.event
async def on_ready() -> None:
    print(f"Logged in as {client.user} -- watching channel {DISCORD_CHANNEL_ID}")


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if message.channel.id != DISCORD_CHANNEL_ID:
        return
    if ALLOWED_USER_IDS and message.author.id not in ALLOWED_USER_IDS:
        return
    if not message.attachments:
        return

    txt_attachment = next(
        (a for a in message.attachments if a.filename.lower().endswith(".txt")), None
    )
    if txt_attachment is None:
        await message.reply(
            f"Only `.txt` file uploads are accepted here -- got `{message.attachments[0].filename}`."
        )
        return

    route = route_for(txt_attachment.filename)
    if route is None:
        await message.reply(
            f"I can't tell which sport `{txt_attachment.filename}` is for, so nothing was pushed.\n"
            f"Start the file name with **`baseball`** for a home run card, **`football`** for a touchdown "
            f"card, or **`sports`** for a combined card holding both "
            f"(e.g. `baseball_2026-09-20.txt`, `football_week2.txt`, `sports_2026-10-04.txt`) "
            f"and upload it again."
        )
        return

    raw = await txt_attachment.read()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        await message.reply(f"`{txt_attachment.filename}` is empty -- nothing to push.")
        return

    await _confirm_and_push(message.channel, message.author, text, txt_attachment.filename, route)


if __name__ == "__main__":
    client.run(DISCORD_BOT_TOKEN)
