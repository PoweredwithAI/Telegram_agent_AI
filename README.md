# Telegram AI Agent

Groq LLM · Rate limiting · Dropbox resume context · SQLite user tracking · Admin analytics

---

## Architecture

```
telegram-bot/
├── bot.py            ← Main bot (handlers, booking flow, admin commands)
├── profile.py        ← YOUR story — edit this. Projects, Ameya, Tap Health pitch.
├── groq_client.py    ← Groq LLM wrapper + per-user rate limiter
├── dropbox_loader.py ← Loads PDF/DOCX resumes from Dropbox
├── database.py       ← SQLite tracking: users, messages, bookings
├── requirements.txt
├── .env.example      ← Copy to .env and fill in
├── .gitignore        ← Keeps secrets out of Git (critical — read Security section)
└── README.md
```

---

## Step 1 — Get your API keys

### Telegram bot token
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → pick a name and username (e.g. `my_agent_bot`)
3. Copy the **token** BotFather gives you

### Your personal Telegram chat ID (for booking alerts)
1. Message **@userinfobot** on Telegram
2. It replies with your numeric **Id** — that's your `OWNER_CHAT_ID`

### Groq API key (free)
1. Go to https://console.groq.com/keys
2. Sign up / log in → Create API key
3. Copy the key (starts with `gsk_...`)
4. Groq free tier: 14,400 requests/day on llama-3.3-70b-versatile — more than enough

### Dropbox token (optional — for resume context)
1. Go to https://www.dropbox.com/developers/apps
2. Create App → "Scoped access" → "Full Dropbox" → name it "my-bot-reader"
3. Permissions tab → enable: `files.content.read`
4. Settings tab → "Generate access token" under OAuth 2 section
5. Copy the token

---

## Step 2 — Set up locally

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Open .env and fill in: TELEGRAM_TOKEN, GROQ_API_KEY, OWNER_CHAT_ID
# Optional: DROPBOX_ACCESS_TOKEN and DROPBOX_RESUME_PATHS

# 4. Update your profile
# Open profile.py — update contact links, sharpen the Tap Health pitch
# The Ameya section is pre-filled but update with your specific details

# 5. Run
python bot.py
```

---

## Step 3 — Connect your Dropbox resumes

In your `.env`:
```
DROPBOX_ACCESS_TOKEN=your_token_here
DROPBOX_RESUME_PATHS=/resume.pdf,/portfolio.docx
```

The paths must be exact Dropbox paths (e.g. `/Documents/my_CV_2026.pdf`).
The bot loads them at startup and refreshes every 6 hours.

**Supported formats:** `.pdf`, `.docx`, `.txt`

---

## Commands

| Command | Who | Action |
|---------|-----|--------|
| `/start` | Anyone | Reset and start conversation |
| `/reset` | Anyone | Clear conversation history |
| `/help` | Anyone | Show suggested questions |
| `/stats` | Owner only | See usage analytics (users, messages, bookings) |
| `/block <user_id>` | Owner only | Block an abusive user |

---

## Suggested test conversation

```
/start
→ "What have I been working on recently?"
→ "Tell me about <Project Name>"
→ "What did he decide NOT to build and why?"
→ "Why does he want to move into product?"
→ "What would he build at Tap Health in week 1?"
→ [paste your Calendly link]
→ "Thursday afternoon IST works"
→ [check your Telegram — you'll get a booking alert]
```

---

## Security — Read This Carefully

### The short version
Private GitHub repo + `.env` file (never committed) + `.gitignore` = sufficient for this use case.

### What protects your keys
| Layer | What it does |
|---|---|
| `.gitignore` | Blocks `.env` from being committed to Git |
| `.env` file | Keys live here — only on your machine or server |
| Private repo | Code is hidden from public; no keys in code anyway |
| Rate limiter | Blocks per-user abuse at the bot level |
| Groq dashboard | Set monthly spending limit at console.groq.com |
| `/block` command | Lets you manually cut off abusive users |

### What a private repo does NOT protect against

- If someone gains access to your GitHub account, they can see your code — but NOT your keys (because `.env` is never committed).
- If someone finds your deployed server and reads the filesystem — they could find `.env`. Mitigation: use environment variables injected by your hosting platform (Railway/Render do this natively).

### Best practices

1. **Never paste a key in the code.** Always `os.environ["KEY_NAME"]`.
2. **Set a Groq spending limit** at https://console.groq.com → Settings → Limits. Set to $5–$10/month.
3. **Rotate keys** every 30–60 days. Groq and Dropbox both allow this with zero downtime.
4. **On Railway/Render:** delete your `.env` file entirely and set the variables in their secrets dashboard. The `.env` pattern is for local dev only.
5. **Keep `bot_data.db` local.** It contains user IDs and message logs — don't commit it.

---

## Tracking who uses your bot

Every interaction is logged to `bot_data.db` automatically. Use `/stats` (owner-only) to see:
- Total unique users
- Active users today / this week
- Total messages sent to the bot
- Total booking requests
- Top 5 users by message count
- 5 most recent booking requests with names and time preferences

To inspect the raw database:
```bash
sqlite3 bot_data.db
.tables
SELECT first_name, username, msg_count, last_seen FROM users ORDER BY last_seen DESC;
SELECT * FROM bookings ORDER BY ts DESC;
.quit
```

---

## Keeping it running 24/7

### Option A — Railway (recommended, free tier available)

1. Push to private GitHub repo
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Set env vars in Railway dashboard (same keys as `.env`)
4. Set start command: `python bot.py`
5. Done — Railway keeps it alive automatically

### Option B — Render (free tier)

1. Push to private GitHub repo
2. https://render.com → New → Web Service → connect repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Set env vars in Render dashboard

### Option C — Local with screen (quick hack)

```bash
screen -S my-bot
source venv/bin/activate && python bot.py
# Ctrl+A, D to detach
# screen -r my-bot to reattach
```

---

## Rate limits at a glance

| Limit | Default | How to change |
|---|---|---|
| Messages per user per minute | 10 | `RATE_PER_MINUTE` in `.env` |
| Messages per user per day | 100 | `RATE_PER_DAY` in `.env` |
| Groq API calls (free tier) | 14,400/day | Groq console |
| Max tokens per response | 500 | `MAX_TOKENS` in `.env` |
| Conversation history | 20 messages | `MAX_HISTORY` in `.env` |

---

## Groq model options

| Model | Speed | Quality | Context | Best for |
|---|---|---|---|---|
| `llama-3.3-70b-versatile` | Medium | Excellent | 128k | Recommended default |
| `llama3-8b-8192` | Very fast | Good | 8k | High-volume testing |
| `mixtral-8x7b-32768` | Fast | Good | 32k | Long resume contexts |
