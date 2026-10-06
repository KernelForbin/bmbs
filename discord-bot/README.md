# Picks intake bot

Lets the group upload a day's picks card as a `.txt` file in a Discord
channel instead of using GitHub's web editor. The bot reads the attachment,
asks for a reaction to confirm, and commits its contents to that sport's
`incoming_picks.txt` on `main` -- the file that sport's parse workflow
watches. It never touches `tickets.json` or the parsing itself.

A `.txt` upload (rather than a pasted message) sidesteps Discord's
2000-character message cap, which a full day's card routinely exceeds.

**Where it runs:** not from the dev clone. Production is a separate clone on
an always-on Windows box, started by Task Scheduler through `run_bot.bat`
(`pythonw`, output appended to the gitignored `bot.log`). The quick way to
tell the two apart: the dev clone has no `.env`.

## Day-to-day use

1. Save the card as a `.txt` file and drag it into the intake channel -- no
   message text needed. **The file name says which sport it is**, and must
   start with one of:

   | Name starts with | For | Example | Shows on |
   |---|---|---|---|
   | `sports` | the All Sports card -- any mix of leagues, mixed parlays included | `sports_2026-10-04.txt` | bmbs.bet |
   | `baseball` | MLB-only cards | `baseball_2026-09-20.txt` | bmbs.bet/mlb/ |
   | `football` | NFL-only cards | `football_week2.txt` | bmbs.bet/football/ |
   | `hockey` | NHL-only cards | `hockey_2026-10-06.txt` | bmbs.bet/hockey/ |
   | `basketball` | NBA-only cards | `basketball_2026-10-21.txt` | bmbs.bet/basketball/ |
   | `wnba` | WNBA-only cards | `wnba_2026-06-01.txt` | bmbs.bet/wnba/ |
   | `cfb` | college-football-only cards | `cfb_week7.txt` | bmbs.bet/cfb/ |

   Anything else is refused with a note listing every prefix above, and
   nothing is pushed. The bot never guesses the sport from the text: the
   cards share a template, and a wrong guess would overwrite another sport's
   slate.
2. The bot posts a preview (which sport, character/line count, a snippet)
   with ✅ / ❌ reactions.
3. React ✅. The bot commits the file to that sport's
   `data/.../incoming_picks.txt` and replies with the commit link and the
   page that will update. The parse workflow takes it from there and posts
   "now LIVE" to the channel, @-mentioning you (the bot writes your Discord
   user id into the commit message as `[discord:<id>]` so the workflow can).
4. ❌, or letting it time out (60 seconds by default), discards the upload.

Re-uploading a card **identical** to the one already there pushes nothing --
the parse workflow would never fire on an unchanged file -- and the bot says
so instead of promising an update.

Only attachments in the configured channel (and, if set, from an allowed
user id) are considered. A non-`.txt` or empty attachment there gets a reply
saying why, rather than being silently ignored.

## After changing `bot.py`: pull and restart

The bot is a long-running process and keeps running the code it started
with, so a `git pull` on the bot's box does nothing until it is restarted:
end the `pythonw.exe` running `bot.py` (`taskkill /F /IM pythonw.exe`, or
just that PID), then run the scheduled task again -- `schtasks /Run /TN
"<task name>"`.

**To confirm the new code is live**, drop a `.txt` whose name starts with
none of the prefixes (say `zzz.txt`) into the intake channel. The refusal
lists every prefix the running code knows, so if it names all seven, the
restart took. A refused file pushes nothing.

## One-time setup

### 1. Create the Discord bot

1. https://discord.com/developers/applications -> **New Application**.
2. **Bot** tab -> **Reset Token**, copy it (this is `DISCORD_BOT_TOKEN`).
3. Same tab, under **Privileged Gateway Intents**, enable **Message Content
   Intent**.
4. **OAuth2 -> URL Generator**: scope `bot`, permissions `Send Messages`,
   `Read Message History`, `Add Reactions`. Open the generated URL and add
   the bot to the group's server.
5. In Discord, enable **Developer Mode** (User Settings -> Advanced), then
   right-click the intake channel -> **Copy Channel ID** for
   `DISCORD_CHANNEL_ID`. To restrict who can push, right-click each person
   -> **Copy User ID** for `DISCORD_ALLOWED_USER_IDS` (comma-separated;
   blank allows anyone who can post in the channel).

### 2. Create a scoped GitHub token

Fine-grained PAT (https://github.com/settings/personal-access-tokens/new),
**Repository access -> Only select repositories -> bmbs**, permission
**Contents: Read and write**. Nothing else. This is `GITHUB_TOKEN`.

### 3. Configure and install

```bash
cd discord-bot
cp .env.example .env
# fill in .env (GITHUB_REPO, GITHUB_BRANCH and CONFIRM_TIMEOUT_SECONDS are optional)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py               # once in the foreground, to confirm it logs in
```

### 4. Run it 24/7

Create a Task Scheduler task that runs `run_bot.bat` at startup ("run
whether user is logged in or not"). It is a single lightweight process with
no state beyond `.env`, so any always-on runner works if the box changes.
