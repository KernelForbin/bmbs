# Picks intake bot

Lets a friend upload his day's raw picks text (e.g. out of his own Gemini
session) as a `.txt` file in a Discord channel instead of using GitHub's
web editor. The bot reads the attachment, asks for a reaction to confirm,
and pushes its contents to `data/incoming_picks.txt` on `main` -- exactly
the file the existing `parse-picks.yml` workflow already watches. Nothing
about that workflow, or about `tickets.json`, changes.

A `.txt` upload (rather than a pasted message) sidesteps Discord's
2000-character message cap, which a full day's picks routinely exceed.

## One-time setup

### 1. Create the Discord bot

1. https://discord.com/developers/applications -> **New Application**.
2. **Bot** tab -> **Reset Token**, copy it (this is `DISCORD_BOT_TOKEN`).
3. Same tab, under **Privileged Gateway Intents**, enable **Message Content
   Intent**. The bot can't read paste text without this.
4. **OAuth2 -> URL Generator**: scope `bot`, permissions `Send Messages`,
   `Read Message History`, `Add Reactions`. Open the generated URL and add
   the bot to your friend group's server.
5. In Discord, enable **Developer Mode** (User Settings -> Advanced), then
   right-click your intake channel -> **Copy Channel ID** for
   `DISCORD_CHANNEL_ID`. Right-click your friend's name -> **Copy User ID**
   if you want to lock `DISCORD_ALLOWED_USER_IDS` to just him.

### 2. Create a scoped GitHub token

Fine-grained PAT (https://github.com/settings/personal-access-tokens/new),
**Repository access -> Only select repositories -> bmbs**, permission
**Contents: Read and write**. Nothing else. This is `GITHUB_TOKEN`.

### 3. Configure and install

```bash
cd discord-bot
cp .env.example .env
# fill in .env with the values from steps 1-2
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py               # run it once in the foreground to confirm it logs in
```

### 4. Run it 24/7

Pick whatever your always-on machine supports. A systemd example is
included (`bmbs-picks-bot.service`) -- edit the paths, then:

```bash
sudo cp bmbs-picks-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bmbs-picks-bot
sudo journalctl -u bmbs-picks-bot -f   # tail logs
```

On Windows, run it under NSSM or Task Scheduler ("run whether user is
logged in or not"). On anything else, a `screen`/`tmux` session, `pm2`, or
a Docker container with `restart: unless-stopped` all work fine -- it's a
single lightweight process with no external state beyond `.env`.

## Day-to-day use

1. Your friend saves his picks text as a `.txt` file (e.g. copy Gemini's
   output into Notepad and save) and drags it into the intake channel as
   an attachment -- no message text needed. **The file name says which
   sport it is**, and must start with one of:

   | Name starts with | For | Example |
   |---|---|---|
   | `baseball` | home run cards | `baseball_2026-09-20.txt` |
   | `football` | touchdown cards | `football_week2.txt` |

   Anything else is refused with a note asking for a rename -- nothing is
   pushed. The bot never guesses the sport from the text: both cards use the
   same template, and a wrong guess would overwrite the other sport's slate.
2. The bot reads the file and posts a preview (which sport, character/line
   count + a snippet) with ✅ / ❌ reactions.
3. He reacts ✅. The bot pushes the file's contents to that sport's incoming
   file (`data/incoming_picks.txt` or `data/football/incoming_picks.txt`)
   and replies with the commit link. That sport's parse workflow runs
   automatically from there, same as a manual GitHub web-editor paste.
4. ❌ or letting it time out (60s default) discards the upload --
   nothing is written.

Only attachments in the configured channel (and, if set, from an allowed
user ID) are ever considered, so unrelated chatter or files elsewhere in
the server can't trigger a push. If someone attaches a non-`.txt` file
there, the bot points that out instead of silently ignoring it.

**After changing `bot.py`, restart the bot** -- it's a long-running process
and keeps running the code it started with. With the Task Scheduler setup:
end the `pythonw.exe` running `bot.py` (or reboot), then run the scheduled
task again.
