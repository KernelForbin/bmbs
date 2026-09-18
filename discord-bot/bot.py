"""
Discord intake bot for bmbs.bet picks.

A friend uploads the day's raw picks text (e.g. out of his own Gemini
session) as a .txt file attachment in a designated Discord channel. This
bot:

1. Reads the attached .txt file.
2. Posts a preview asking for confirmation via reactions.
3. On thumbs-up, pushes that content to data/incoming_picks.txt on `main`
   via the GitHub Contents API. The repo's existing parse-picks.yml
   workflow (unchanged) takes it from there.

Using a file attachment (rather than pasted message text) sidesteps
Discord's 2000-character message cap, which a real day's picks routinely
exceed.

This bot never touches tickets.json or the parsing logic itself -- it only
ever writes incoming_picks.txt, exactly like a human pasting into GitHub's
web editor would.
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
GITHUB_FILE_PATH = "data/incoming_picks.txt"

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


def push_incoming_picks(text: str, author_name: str) -> str:
    """Commit `text` as the new data/incoming_picks.txt on GITHUB_BRANCH.

    Returns the commit URL on success. Raises requests.HTTPError on failure.
    """
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"

    get_resp = requests.get(url, headers=_github_headers(), params={"ref": GITHUB_BRANCH}, timeout=15)
    get_resp.raise_for_status()
    current_sha = get_resp.json()["sha"]

    payload = {
        "message": f"Picks upload from {author_name} via Discord bot",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "sha": current_sha,
        "branch": GITHUB_BRANCH,
    }
    put_resp = requests.put(url, headers=_github_headers(), json=payload, timeout=15)
    put_resp.raise_for_status()
    return put_resp.json()["commit"]["html_url"]


async def _confirm_and_push(
    channel: discord.abc.Messageable, author: discord.abc.User, text: str, filename: str
) -> None:
    text = text.strip() + "\n"
    line_count = text.count("\n")
    preview = text[:PREVIEW_CHARS]
    truncated_note = "..." if len(text) > PREVIEW_CHARS else ""

    prompt = await channel.send(
        f"Got `{filename}` from {author.mention} ({len(text)} chars / {line_count} lines).\n"
        f"React with ✅ to push to `{GITHUB_FILE_PATH}` (triggers the picks parser), "
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
        commit_url = push_incoming_picks(text, str(author))
    except requests.HTTPError as exc:
        await channel.send(f"❌ Push failed: `{exc}`")
        return

    await channel.send(
        f"✅ Pushed to `{GITHUB_FILE_PATH}`: {commit_url}\n"
        f"The parse-picks workflow should update the live site within about a minute."
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

    raw = await txt_attachment.read()
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        await message.reply(f"`{txt_attachment.filename}` is empty -- nothing to push.")
        return

    await _confirm_and_push(message.channel, message.author, text, txt_attachment.filename)


if __name__ == "__main__":
    client.run(DISCORD_BOT_TOKEN)
