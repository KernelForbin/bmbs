"""
Offline checks for discord-bot/bot.py: the confirm/discard/timeout reaction
flow and the GitHub Contents API push, plus on_message's own gating (bot
messages, wrong channel, disallowed users, non-.txt / unroutable / empty
attachments). route_for() itself (file-name -> sport, case-insensitivity) is
already covered by tests/test_football_parser.py and isn't repeated here.

No real Discord connection and no real GitHub call is made. discord.py's
Client is constructed (it does no I/O at construction) but never logs in;
its `loop` is assigned the test's own running loop by hand, and confirmation
reactions are delivered with `client.dispatch("reaction_add", ...)` -- the
same call discord.py's own gateway code makes internally, just invoked
directly instead of over a websocket. requests.get/put are monkeypatched so
no network call reaches GitHub.

    python tests/test_discord_bot.py
"""
import asyncio
import base64
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# bot.py reads these at import time (os.environ["..."] -- KeyError if absent),
# and builds a real discord.Client() at module scope. Dummy values are enough:
# nothing here ever actually talks to Discord or GitHub.
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CHANNEL_ID", "555")
os.environ.setdefault("GITHUB_TOKEN", "test-gh-token")
os.environ.setdefault("GITHUB_REPO", "KernelForbin/bmbs")
os.environ.setdefault("GITHUB_BRANCH", "main")
os.environ.setdefault("CONFIRM_TIMEOUT_SECONDS", "60")

sys.path.insert(0, str(REPO / "discord-bot"))
import bot  # noqa: E402
import requests  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- fakes ----------------

class FakeMessageRef:
    def __init__(self, id):
        self.id = id


class FakeReaction:
    def __init__(self, message_id, emoji):
        self.message = FakeMessageRef(message_id)
        self.emoji = emoji


class FakeUser:
    def __init__(self, id, name="tester"):
        self.id = id
        self.mention = f"<@{id}>"
        self._name = name

    def __str__(self):
        return self._name


class FakePrompt:
    """What channel.send() hands back: the confirmation message itself."""
    _next_id = [9000]

    def __init__(self):
        self.id = FakePrompt._next_id[0]
        FakePrompt._next_id[0] += 1
        self.reactions_added = []

    async def add_reaction(self, emoji):
        self.reactions_added.append(emoji)


class FakeChannel:
    def __init__(self, id=555):
        self.id = id
        self.sent = []       # every message text sent to this channel
        self.prompts = []    # every FakePrompt returned, in order

    async def send(self, content):
        self.sent.append(content)
        prompt = FakePrompt()
        self.prompts.append(prompt)
        return prompt


class FakeAttachment:
    def __init__(self, filename, content=b""):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


class FakeMessage:
    def __init__(self, author, channel, attachments=(), is_bot=False):
        self.author = author
        self.author.bot = is_bot
        self.channel = channel
        self.attachments = list(attachments)
        self.replies = []

    async def reply(self, content):
        self.replies.append(content)


def run(coro):
    return asyncio.run(coro)


def with_loop(coro_fn):
    """Run an async test body with bot.client.loop set to THIS run's loop --
    wait_for()/dispatch() need a real loop, which an unconnected Client lacks."""
    async def wrapper():
        bot.client.loop = asyncio.get_running_loop()
        await coro_fn()
    return run(wrapper())


# ---------------- fake GitHub ----------------

class FakeResponse:
    def __init__(self, status_code, json_data=None, exc=None):
        self.status_code = status_code
        self._json = json_data or {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._json


GH = {"get": [], "put": []}   # every call made, as (url, kwargs)


def reset_gh(get_resp, put_resp):
    GH["get"].clear()
    GH["put"].clear()

    def fake_get(url, **kwargs):
        GH["get"].append((url, kwargs))
        return get_resp

    def fake_put(url, **kwargs):
        GH["put"].append((url, kwargs))
        return put_resp

    bot.requests.get = fake_get
    bot.requests.put = fake_put


# ================= A. push_incoming_picks() : the GitHub Contents API =================

reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://github.com/x/y/commit/abc"}}))
url = bot.push_incoming_picks("Card 1\n", "kenny#0001", "data/incoming_picks.txt", "baseball (home runs)")
check("A1 first upload (GET 404): no sha in the PUT payload, GET is never fatal", GH["put"][0][1]["json"].get("sha") is None)
check("A2 returns the new commit's URL", url == "https://github.com/x/y/commit/abc", url)
put_url, put_kwargs = GH["put"][0]
check("A3 PUT hits the exact Contents API path for this file", put_url == f"{bot.GITHUB_API_BASE}/repos/KernelForbin/bmbs/contents/data/incoming_picks.txt", put_url)
payload = put_kwargs["json"]
check("A4 content is base64 of the exact text (trailing newline included by the caller, not this function)",
      base64.b64decode(payload["content"]).decode("utf-8") == "Card 1\n", payload["content"])
check("A5 commit message names the sport and the uploader", payload["message"] == "Picks upload (baseball (home runs)) from kenny#0001 via Discord bot", payload["message"])
check("A6 pushes to the configured branch", payload["branch"] == "main" and GH["get"][0][1]["params"] == {"ref": "main"})
headers = put_kwargs["headers"]
check("A7 GitHub auth header carries the token as a Bearer token", headers["Authorization"] == "Bearer test-gh-token")

reset_gh(FakeResponse(200, {"sha": "deadbeef"}), FakeResponse(200, {"commit": {"html_url": "https://github.com/x/y/commit/def"}}))
bot.push_incoming_picks("Card 2\n", "memo", "data/football/incoming_picks.txt", "football (touchdowns)")
check("A8 existing file (GET 200): the file's sha is carried into the PUT so it updates rather than fails",
      GH["put"][0][1]["json"]["sha"] == "deadbeef")

reset_gh(FakeResponse(500, exc=requests.HTTPError("500 Server Error")), FakeResponse(200))
try:
    bot.push_incoming_picks("x", "kenny", "data/incoming_picks.txt", "baseball (home runs)")
    check("A9 a GET failure other than 404 raises, and never reaches PUT", False)
except requests.HTTPError:
    check("A9 a GET failure other than 404 raises, and never reaches PUT", len(GH["put"]) == 0)

reset_gh(FakeResponse(404), FakeResponse(422, exc=requests.HTTPError("422 Unprocessable")))
try:
    bot.push_incoming_picks("x", "kenny", "data/incoming_picks.txt", "baseball (home runs)")
    check("A10 a PUT failure (e.g. stale sha) raises HTTPError to the caller", False)
except requests.HTTPError:
    check("A10 a PUT failure (e.g. stale sha) raises HTTPError to the caller", True)


# ================= B. _confirm_and_push(): confirm / discard / timeout =================

BASEBALL_ROUTE = bot.ROUTES["baseball"]


async def confirm_flow_ok():
    reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://github.com/x/y/commit/ok1"}}))
    channel = FakeChannel()
    author = FakeUser(1, "memo")
    task = asyncio.create_task(bot._confirm_and_push(channel, author, "Card 1\n", "baseball_card.txt", BASEBALL_ROUTE))
    await asyncio.sleep(0.05)
    prompt = channel.prompts[0]
    check("B1 the prompt is posted with both reaction options, before anything is decided",
          prompt.reactions_added == ["✅", "❌"] and len(GH["put"]) == 0)

    # a reaction on some OTHER message must not resolve this prompt
    bot.client.dispatch("reaction_add", FakeReaction(prompt.id + 999, "✅"), author)
    # a reaction from someone else entirely must not resolve it either
    bot.client.dispatch("reaction_add", FakeReaction(prompt.id, "✅"), FakeUser(2, "someone-else"))
    await asyncio.sleep(0.02)
    check("B2 a reaction on the wrong message, or from the wrong person, changes nothing", len(GH["put"]) == 0)

    # an emoji outside the accepted pair (from the RIGHT person) is ignored too
    bot.client.dispatch("reaction_add", FakeReaction(prompt.id, "👍"), author)
    await asyncio.sleep(0.02)
    check("B3 an unrelated emoji from the right author is ignored", len(GH["put"]) == 0)

    bot.client.dispatch("reaction_add", FakeReaction(prompt.id, "✅"), author)
    await task
    check("B4 the real author's checkmark pushes to GitHub, once", len(GH["put"]) == 1)
    check("B5 confirmation names the destination file and the live site",
          "data/incoming_picks.txt" in channel.sent[-1] and "commit/ok1" in channel.sent[-1] and "bmbs.bet" in channel.sent[-1], channel.sent[-1])


with_loop(confirm_flow_ok)


async def discard_flow():
    reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
    channel = FakeChannel()
    author = FakeUser(3, "bailey")
    task = asyncio.create_task(bot._confirm_and_push(channel, author, "Card X\n", "football_card.txt", bot.ROUTES["football"]))
    await asyncio.sleep(0.05)
    prompt = channel.prompts[0]
    bot.client.dispatch("reaction_add", FakeReaction(prompt.id, "❌"), author)
    await task
    check("B6 a thumbs-down discards -- nothing is pushed, and the channel is told so",
          len(GH["put"]) == 0 and channel.sent[-1] == "Discarded.")


with_loop(discard_flow)


async def timeout_flow():
    bot.CONFIRM_TIMEOUT_SECONDS = 0.05
    reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
    channel = FakeChannel()
    author = FakeUser(4, "noid")
    await bot._confirm_and_push(channel, author, "Card Y\n", "baseball_card.txt", BASEBALL_ROUTE)
    check("B7 no reaction within the timeout -> nothing pushed, and the author is told it timed out",
          len(GH["put"]) == 0 and "Timed out" in channel.sent[-1] and author.mention in channel.sent[-1], channel.sent[-1])
    bot.CONFIRM_TIMEOUT_SECONDS = 60


with_loop(timeout_flow)


async def push_failure_flow():
    reset_gh(FakeResponse(404), FakeResponse(422, exc=requests.HTTPError("422 Unprocessable Entity")))
    channel = FakeChannel()
    author = FakeUser(5, "joe")
    task = asyncio.create_task(bot._confirm_and_push(channel, author, "Card Z\n", "baseball_card.txt", BASEBALL_ROUTE))
    await asyncio.sleep(0.05)
    prompt = channel.prompts[0]
    bot.client.dispatch("reaction_add", FakeReaction(prompt.id, "✅"), author)
    await task
    check("B8 a GitHub push failure is reported in the channel, not swallowed",
          "Push failed" in channel.sent[-1] and "422" in channel.sent[-1], channel.sent[-1])


with_loop(push_failure_flow)


async def preview_and_counts_flow():
    reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
    channel = FakeChannel()
    author = FakeUser(6, "kevin")
    long_text = "\n".join(f"line {i}" for i in range(200))
    task = asyncio.create_task(bot._confirm_and_push(channel, author, long_text, "baseball_big.txt", BASEBALL_ROUTE))
    await asyncio.sleep(0.05)
    prompt_text = channel.sent[0]
    check("B9 the prompt previews the card and truncates a long one, with the right size in the header",
          "..." in prompt_text and str(len("\n".join(f"line {i}" for i in range(200)) + "\n")) + " chars" in prompt_text, prompt_text[:200])
    bot.client.dispatch("reaction_add", FakeReaction(channel.prompts[0].id, "❌"), author)
    await task


with_loop(preview_and_counts_flow)


# ================= C. on_message(): the gates before a prompt is ever posted =================

def on_message_test(name, message, expect_prompted, expect_reply_contains=None, allowed_ids=None):
    """Runs on_message against a fake message and checks whether a confirmation
    prompt was posted and/or a reply was sent. If the message is valid enough to
    reach the confirm prompt, the timeout is shortened to near-zero first so the
    call resolves (by timing out) instead of hanging on a real 60s wait -- these
    cases only check that the PROMPT went out, not how it's resolved (see block B
    and C9-C11 for the reaction/timeout mechanics themselves)."""
    old_allowed, old_timeout = bot.ALLOWED_USER_IDS, bot.CONFIRM_TIMEOUT_SECONDS
    if allowed_ids is not None:
        bot.ALLOWED_USER_IDS = allowed_ids
    bot.CONFIRM_TIMEOUT_SECONDS = 0.05
    try:
        with_loop(lambda: bot.on_message(message))
    finally:
        bot.ALLOWED_USER_IDS, bot.CONFIRM_TIMEOUT_SECONDS = old_allowed, old_timeout
    prompted = len(message.channel.sent) > 0 if hasattr(message.channel, "sent") else False
    ok = prompted == expect_prompted
    if expect_reply_contains is not None:
        ok = ok and any(expect_reply_contains in r for r in message.replies)
    check(name, ok, f"replies={message.replies!r} sent={getattr(message.channel, 'sent', None)!r}")


channel = FakeChannel()
bot_author = FakeUser(10, "some-bot")
msg = FakeMessage(bot_author, channel, [FakeAttachment("baseball_x.txt", b"stuff")], is_bot=True)
on_message_test("C1 a message from another bot is ignored outright", msg, expect_prompted=False)

msg = FakeMessage(FakeUser(11), FakeChannel(id=999), [FakeAttachment("baseball_x.txt", b"stuff")])
on_message_test("C2 a message in the wrong channel is ignored", msg, expect_prompted=False)

msg = FakeMessage(FakeUser(12), FakeChannel(), [FakeAttachment("baseball_x.txt", b"stuff")])
on_message_test("C3 an allow-list is enforced when set", msg, expect_prompted=False, allowed_ids={999})

msg = FakeMessage(FakeUser(12), FakeChannel(), [FakeAttachment("baseball_x.txt", b"stuff")])
on_message_test("C4 ...but the same author passes once they're on it", msg, expect_prompted=True, allowed_ids={12})

msg = FakeMessage(FakeUser(13), FakeChannel(), [])
on_message_test("C5 no attachment at all is ignored (not even a reply)", msg, expect_prompted=False)

msg = FakeMessage(FakeUser(14), FakeChannel(), [FakeAttachment("readme.pdf", b"stuff")])
on_message_test("C6 a non-.txt attachment gets a reply naming the file, not a prompt", msg, expect_prompted=False, expect_reply_contains="readme.pdf")

msg = FakeMessage(FakeUser(15), FakeChannel(), [FakeAttachment("hockey_picks.txt", b"stuff")])
on_message_test("C7 a filename that names no sport asks for a rename, doesn't guess", msg, expect_prompted=False, expect_reply_contains="upload it again")

msg = FakeMessage(FakeUser(16), FakeChannel(), [FakeAttachment("baseball_empty.txt", b"   \n  ")])
on_message_test("C8 whitespace-only content is refused as empty, never pushed", msg, expect_prompted=False, expect_reply_contains="empty")

async def full_on_message_then_discard(msg):
    """Runs on_message as a background task (it blocks on the confirm prompt),
    gives it a moment to post, then discards -- so the test can inspect the
    prompt it sent without waiting out a real timeout."""
    task = asyncio.create_task(bot.on_message(msg))
    await asyncio.sleep(0.05)
    if msg.channel.prompts:
        bot.client.dispatch("reaction_add", FakeReaction(msg.channel.prompts[0].id, "❌"), msg.author)
    await task


reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
msg = FakeMessage(FakeUser(17), FakeChannel(), [FakeAttachment("baseball_real.txt", "Card 1\n".encode("utf-8"))])
with_loop(lambda: full_on_message_then_discard(msg))
check("C9 a valid baseball upload reaches the confirmation prompt", "baseball" in msg.channel.sent[0].lower(), msg.channel.sent)

reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
msg = FakeMessage(FakeUser(18), FakeChannel(), [FakeAttachment("football_real.txt", "Card 1\n".encode("utf-8"))])
with_loop(lambda: full_on_message_then_discard(msg))
check("C10 a valid football upload routes to football's file, named in the prompt", "data/football/incoming_picks.txt" in msg.channel.sent[0], msg.channel.sent)

reset_gh(FakeResponse(404), FakeResponse(201, {"commit": {"html_url": "https://x/never"}}))
msg = FakeMessage(FakeUser(19), FakeChannel(), [FakeAttachment("notes.pdf"), FakeAttachment("baseball_ok.txt", b"Card 1\n")])
with_loop(lambda: full_on_message_then_discard(msg))
check("C11 the first .txt attachment is used even if it isn't the first attachment overall", "baseball_ok.txt" in msg.channel.sent[0], msg.channel.sent)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all discord-bot checks passed")
