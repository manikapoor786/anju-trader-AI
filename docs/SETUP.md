# Setup — First-time

Read this once. Run the commands. The system runs itself after that.

---

## What you need

- A GitHub account (you have one — `manikapoor786`)
- A Telegram account (you have one)
- A Google AI Studio account (free) — for Gemini API key
- A laptop OR your phone with Termux (you only need this once to push the code)

You do **not** need:
- A paid Claude API key (only useful in Phase 3+)
- A Kite Connect subscription (only useful in Phase 4)

---

## Step 1 — Create the GitHub repo

On your phone or laptop:

1. Go to https://github.com/new
2. Repository name: `anju-trader-AI`
3. Visibility: **Public** during Phase 0–3 (per ADR-009 — gives unlimited free Actions minutes)
4. Do NOT initialise with README/license/.gitignore (we already have them)
5. Click "Create repository"

GitHub will show you the URL: `https://github.com/manikapoor786/anju-trader-AI`

---

## Step 2 — Create a new Telegram bot

Open Telegram and chat with `@BotFather`:

```
/newbot
Bot name: anju-AI
Username: anju_ai_<somethingunique>_bot
```

Save the **bot token** that BotFather sends you (looks like `7234567890:AAH...`).

Now create a new chat (Telegram → New Group → name it "anju-AI signals") OR send `/start` to the new bot directly.

To get the **chat ID**:
1. Send any message to the bot or to the group
2. Open in browser: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Find the `"chat":{"id": ...}` value — that's your chat ID

(If the bot is in a group, the chat ID is negative, like `-987654321`. That's correct.)

---

## Step 3 — Get a free Gemini API key

1. Go to https://aistudio.google.com/apikey
2. Sign in with a Google account
3. Click "Create API key"
4. Copy it

Free tier: 1500 requests/day, 15 RPM — plenty for our use.

---

## Step 4 — Push this code to your new repo

From the directory `~/anju-trader-AI/` on your laptop:

```bash
cd ~/anju-trader-AI
git remote add origin https://github.com/manikapoor786/anju-trader-AI.git
git branch -M main
git push -u origin main
```

(If you set up via the phone, the same can be done with Termux + ssh keys, but laptop is easier for this one-time push.)

---

## Step 5 — Set GitHub Actions secrets

In your browser:
1. Open `https://github.com/manikapoor786/anju-trader-AI/settings/secrets/actions`
2. Click "New repository secret" for each of these:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN_AI` | The bot token from Step 2 |
| `TELEGRAM_CHAT_ID_AI` | The chat ID from Step 2 |
| `GEMINI_API_KEY` | The Gemini key from Step 3 |

Leave these unset until later phases:
- `ANTHROPIC_API_KEY` (Phase 3)
- `KITE_API_KEY` / `KITE_API_SECRET` / `KITE_ACCESS_TOKEN` (Phase 4)

---

## Step 6 — Run the first workflow from your phone

1. Open the GitHub mobile app
2. Navigate to `anju-trader-AI` → Actions tab
3. Tap "Manual Scan (anju-AI)"
4. Tap "Run workflow" → leave defaults → Run

Within 3–5 minutes you should get a Telegram message in your new anju-AI chat:
```
🌅 anju-AI · First scan complete · PAPER MODE
   Universe: nifty500
   Signals generated: <N>
   ...
```

If you get that message: **the plumbing works.** You can now control the entire system from your phone.

If you don't: open the Actions tab, click into the failed run, read the error. Most common issues:
- Missing secret → add it
- Typo in secret name → match exactly to the table above

---

## What happens next

Once setup is verified (the manual scan produced a Telegram message):

- Phase 0 implementation tasks (0.13 onwards in [ROADMAP.md](ROADMAP.md)) can start
- Each implementation milestone is a PR you review on your phone
- The system runs on the schedule defined in workflow YAMLs — you don't have to do anything daily

---

## Cost summary

| Item | Phase 0–3 | Phase 4+ |
|---|---|---|
| GitHub Actions | Free (public repo) | Free or Private (~$0–$4/mo) |
| Gemini API | Free tier | Free tier |
| Claude API | Unused | ~₹500–800/mo |
| Telegram | Free | Free |
| Kite Connect | Unused | ₹500/mo (only if live tick streaming needed) |
| **Total** | **₹0** | **~₹500–2000/mo** |
