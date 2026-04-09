"""
bot.py — My Telegram AI Agent 
Features: Groq LLM, per-user rate limiting, Dropbox resume context,
          SQLite user tracking, admin /stats, booking flow, owner DM alerts.
"""

import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)

from profile import PROFILE
from groq_client import GroqChat, RateLimiter
from database import (
    init_db, upsert_user, increment_msg_count,
    log_message, log_booking, is_blocked,
    block_user, get_stats_summary,
)

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID   = os.getenv("OWNER_CHAT_ID", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY     = int(os.getenv("MAX_HISTORY", "20"))
RATE_PER_MINUTE = int(os.getenv("RATE_PER_MINUTE", "10"))
RATE_PER_DAY    = int(os.getenv("RATE_PER_DAY", "100"))
MAX_TOKENS      = int(os.getenv("MAX_TOKENS", "500"))
DROPBOX_TOKEN   = os.getenv("DROPBOX_ACCESS_TOKEN", "")
DROPBOX_PATHS   = [p.strip() for p in os.getenv("DROPBOX_RESUME_PATHS", "").split(",") if p.strip()]

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Services ─────────────────────────────────────────────────────────────────

groq    = GroqChat(api_key=GROQ_API_KEY, model=GROQ_MODEL, max_tokens=MAX_TOKENS)
limiter = RateLimiter(per_minute=RATE_PER_MINUTE, per_day=RATE_PER_DAY)

# Dropbox loader 
resume_loader = None
if DROPBOX_TOKEN and DROPBOX_PATHS:
    try:
        from dropbox_loader import DropboxResumeLoader
        resume_loader = DropboxResumeLoader(token=DROPBOX_TOKEN, paths=DROPBOX_PATHS)
        resume_loader.load()
        logger.info("Dropbox resume loader active — %d file(s)", len(DROPBOX_PATHS))
    except Exception as exc:
        logger.warning("Dropbox loader failed to init: %s", exc)
        resume_loader = None

# In-memory stores
histories:      dict[int, list[dict]] = {}
booking_states: dict[int, dict]       = {}

# ─── Calendar link detection ──────────────────────────────────────────────────

_CAL_PATTERNS = [
    r"https?://calendly\.com/\S+",
    r"https?://cal\.com/\S+",
    r"https?://tidycal\.com/\S+",
    r"https?://savvycal\.com/\S+",
    r"https?://zcal\.co/\S+",
    r"https?://doodle\.com/\S+",
    r"https?://letsmeet\.io/\S+",
    r"https?://meetings\.hubspot\.com/\S+",
    r"https?://app\.hubspot\.com/meetings/\S+",
    r"https?://fantastical\.app/\S+",
    r"https?://reclaim\.ai/\S+",
    r"https?://calendar\.google\.com/calendar/\S+",
    r"https?://calendar\.app\.google/\S+",
    r"https?://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}\S*",
    r"https?://teams\.microsoft\.com/l/meetup-join/\S+",
    r"https?://teams\.microsoft\.com/l/meeting/\S+",
    r"https?://teams\.live\.com/meet/\S+",
    r"https?://outlook\.office365\.com/owa/calendar/\S+",
    r"https?://outlook\.office\.com/owa/calendar/\S+",
    r"https?://outlook\.live\.com/calendar/\S+",
]

def _find_calendar_link(text: str) -> str | None:
    for pat in _CAL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).rstrip(".,)")
    return None

# ─── System prompt builder ────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    profile_json = json.dumps(PROFILE, indent=2, ensure_ascii=False)

    resume_section = ""
    if resume_loader and resume_loader.is_loaded:
        resume_section = f"""
─── RESUME / PORTFOLIO DOCUMENTS (from Dropbox) ───
{resume_loader.get_combined_text()[:30000]}
────────────────────────────────────────────────────
Use the resumes to answer specific questions about my qualifications,
education, certifications, or detailed work history. Prefer the profile data above
for conversational replies; use the resume documents for specific factual lookups.
"""

    return f"""You are my AI agent — my interactive resume and scheduling assistant.

Your two jobs:
1. Give a concrete, honest account of what I have built and how I think.
2. Help the person book a meeting when they share a calendar link.

─── My structured profile ───
{profile_json}
────────────────────────────────────
{resume_section}

STRICT RESPONSE RULES:
- Be direct and concrete. No buzzwords. No "passionate about leveraging synergies."
- Keep responses under 8 lines unless the user explicitly asks for more detail.
- Project stories: Problem → What was built → Outcome/learning. Always this order.
- When asked about Tap Health: use tap_health_angle and week_one_idea from the profile.
- When asked "why product?": use the why_product field.
- For specific details not in the profile (exact dates, grades): check the resume documents
  section above if available, otherwise say "I don't have that on file — shall I flag it?"
- NEVER invent, guess, or extrapolate facts not explicitly stated in your context.
- If you lack enough information, respond with: "I don't have that detail —
  want me to help book a meeting instead?"
- Do NOT say "extensive experience in X" unless X is explicitly documented.
- Keep answers to 3–6 lines unless the user asks for more.
- Anchor every claim to a specific project, outcome, or time period from the data.
- Tone: confident, brief, collegial. Not a hype machine. Not corporate.
- Plain text. Telegram renders *bold* and _italic_ — use sparingly.

BOOKING RULES:
- If message contains [CALENDAR_LINK_DETECTED: <url>]: confirm warmly, explain you'll help
  lock in a time, ask for their time preference.
- If message contains [BOOKING_TIME_RECEIVED: <pref>]: confirm booking is in motion,
  tell them I have been notified and they'll get an invite shortly. 1-2 sentences max.

WHAT I CARE ABOUT (steer here if conversation goes generic):
- Healthcare AI that clinicians and patients can actually trust
- Production systems, not demos
- Knowing what NOT to build — and having a story for it
- The move from ML engineer → product, and why
"""

# Build the prompt once at startup (resumes already loaded)
SYSTEM_PROMPT = _build_system_prompt()

# ─── AI reply helper ──────────────────────────────────────────────────────────

async def _get_reply(chat_id: int, user_msg: str) -> str:
    hist = histories.setdefault(chat_id, [])
    hist.append({"role": "user", "content": user_msg})
    if len(hist) > MAX_HISTORY:
        histories[chat_id] = hist[-MAX_HISTORY:]

    # Refresh resume context if stale
    if resume_loader and resume_loader._is_stale():
        resume_loader.load()
        # Rebuild the prompt with fresh content
        global SYSTEM_PROMPT
        SYSTEM_PROMPT = _build_system_prompt()

    try:
        reply = await groq.chat(
            [{"role": "system", "content": SYSTEM_PROMPT}] + histories[chat_id]
        )
    except Exception as exc:
        logger.error("LLM error: %s", exc)
        reply = (
            "I'm having a brief connection issue. "
            "Feel free to message me directly — I won't mind."
        )

    histories[chat_id].append({"role": "assistant", "content": reply})
    return reply

# ─── Owner notification ───────────────────────────────────────────────────────

async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, user, booking: dict) -> None:
    if not OWNER_CHAT_ID:
        return
    try:
        uname = f"@{user.username}" if user.username else "(no username)"
        msg = (
            f"📅 *New meeting request*\n\n"
            f"From: {user.first_name} {uname}\n"
            f"Telegram ID: `{user.id}`\n"
            f"Calendar: {booking['calendar_link']}\n"
            f"Time preference: {booking.get('time_preference', 'not specified')}\n"
            f"Received: {datetime.now().strftime('%d %b %Y, %H:%M IST')}"
        )
        await context.bot.send_message(
            chat_id=int(OWNER_CHAT_ID), text=msg, parse_mode="Markdown"
        )
    except Exception as exc:
        logger.error("Owner notification failed: %s", exc)

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    histories.pop(chat_id, None)
    booking_states.pop(chat_id, None)
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    log_message(user.id, "in", "/start")

    await update.message.reply_text(
        "👋 I'm his/her AI agent.\n\n"
        "Ask me what he's built, how he thinks about product decisions, "
        "or share a calendar link and I'll help book a meeting.\n\n"
        "Try: *What has [name] been working on?*",
        parse_mode="Markdown",
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    histories.pop(chat_id, None)
    booking_states.pop(chat_id, None)
    await update.message.reply_text("Conversation cleared 🔄")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*What can I do?*\n\n"
        "• Tell you what [name] has built — projects, decisions, outcomes\n"
        "• Explain his product thinking and trade-offs\n"
        "• Help book a meeting — paste a calendar link\n\n"
        "*Commands:* /start /reset /help\n\n"
        "*Try asking:*\n"
        "— What has [name] been working on?\n"
        "— What did he decide NOT to build and why?\n"
        "— Why does he want to move into product?\n",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only command to see bot usage analytics."""
    if str(update.effective_user.id) != str(OWNER_CHAT_ID):
        await update.message.reply_text("Not authorised.")
        return

    s = get_stats_summary()
    top = "\n".join(
        f"  {r['first_name']} (@{r['username'] or '—'}): {r['msg_count']} msgs, {r['booking_count']} bookings"
        for r in s["top_users"]
    ) or "  No data yet"

    bookings = "\n".join(
        f"  {r['first_name']} (@{r['username'] or '—'}) — {r['time_preference'] or 'time TBD'} — {r['ts'][:10]}"
        for r in s["recent_book"]
    ) or "  No bookings yet"

    text = (
        f"📊 *Bot Analytics*\n\n"
        f"Total users: {s['total_users']}\n"
        f"Active today: {s['active_today']}\n"
        f"Active this week: {s['active_week']}\n"
        f"Total messages in: {s['total_msgs']}\n"
        f"Total bookings: {s['total_book']}\n\n"
        f"*Top users:*\n{top}\n\n"
        f"*Recent bookings:*\n{bookings}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: /block <user_id> — blocks a user from using the bot."""
    if str(update.effective_user.id) != str(OWNER_CHAT_ID):
        await update.message.reply_text("Not authorised.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /block <user_id>")
        return
    try:
        uid = int(context.args[0])
        block_user(uid)
        await update.message.reply_text(f"User {uid} blocked.")
    except ValueError:
        await update.message.reply_text("Invalid user ID.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user    = update.effective_user
    text    = update.message.text.strip()

    # Security: block list check
    if is_blocked(user.id):
        await update.message.reply_text("This bot is not available to you.")
        return

    # Rate limit check
    allowed, reason = limiter.check(user.id)
    if not allowed:
        await update.message.reply_text(f"⏳ Rate limit: {reason}.")
        return

    # Track user & message
    upsert_user(user.id, user.username, user.first_name, user.last_name)
    increment_msg_count(user.id)
    log_message(user.id, "in", text)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    state    = booking_states.get(chat_id, {})
    cal_link = _find_calendar_link(text)

    # ── Calendar link detected ──────────────────────────────────────────────
    if cal_link and state.get("step") != "confirmed":
        booking_states[chat_id] = {
            "calendar_link": cal_link,
            "step": "awaiting_time_preference",
            "ts": datetime.utcnow().isoformat(),
        }
        log_booking(user.id, cal_link)
        augmented = f"{text}\n\n[CALENDAR_LINK_DETECTED: {cal_link}]"
        reply = await _get_reply(chat_id, augmented)

    # ── Time preference received ────────────────────────────────────────────
    elif state.get("step") == "awaiting_time_preference":
        booking_states[chat_id]["step"]            = "confirmed"
        booking_states[chat_id]["time_preference"] = text
        await _notify_owner(context, user, booking_states[chat_id])
        log_booking(user.id, state["calendar_link"], time_preference=text)
        log_message(user.id, "in", text, is_booking=True)
        augmented = f"{text}\n\n[BOOKING_TIME_RECEIVED: {text}]"
        reply = await _get_reply(chat_id, augmented)

    # ── Normal conversation ─────────────────────────────────────────────────
    else:
        reply = await _get_reply(chat_id, text)

    log_message(user.id, "out", reply)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update caused error: %s", context.error, exc_info=context.error)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    init_db()
    logger.info("Starting my Telegram bot (Groq: %s)", GROQ_MODEL)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CommandHandler("block",  cmd_block))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot running. Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()