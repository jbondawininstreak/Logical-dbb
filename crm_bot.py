"""Group-facing CRM bot: posts leads, lets callers claim them, time the call,
set outcomes, leave notes on long calls and flag numbers as do-not-even-try."""

import re

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import common
import db

NO_MORE_LEADS = "No more leads in the queue."
NOTES_THRESHOLD = 300  # seconds of talk time that force a notes reply


# --- helpers ----------------------------------------------------------------


async def _safe_edit(query, text: str, reply_markup=None) -> None:
    """Edit the callback's message; ignore BadRequest (e.g. text unchanged)."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest:
        pass


def _card_text(query) -> str:
    message = query.message
    return (getattr(message, "text", None) or "") if message is not None else ""


def _owner_name(query, attempt_id: int | None = None) -> str:
    """Best-effort name of whoever holds the attempt shown on this card.

    Uses db.get_attempt(attempt_id) when db offers it (not in the section 3
    contract), otherwise reads the 'Claimed by <name>' line format_lead put on
    the card. Falls back to 'someone else'.
    """
    if attempt_id is not None:
        get_attempt = getattr(db, "get_attempt", None)
        if get_attempt is not None:
            try:
                attempt = get_attempt(attempt_id)
            except Exception:
                attempt = None
            if attempt and attempt.get("caller_name"):
                return attempt["caller_name"]
    match = re.search(r"^Claimed by (.+)$", _card_text(query), re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "someone else"


async def _belongs_alert(query, attempt_id: int | None = None) -> None:
    await query.answer(f"This one belongs to {_owner_name(query, attempt_id)}", show_alert=True)


def _mention(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


async def _post_next(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    posted = await common.post_next_lead(chat_id)
    if not posted:
        await context.bot.send_message(chat_id, NO_MORE_LEADS)


def _lead_and_history(lead_id: int):
    lead = db.get_lead(lead_id)
    history = db.history_for_phone(lead["phone"]) if lead else []
    return lead, history


# --- commands ---------------------------------------------------------------


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Works in any chat, allowed or not."""
    chat = update.effective_chat
    await context.bot.send_message(chat.id, f"This chat's ID is {chat.id}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not db.is_allowed(chat.id):
        return
    await context.bot.send_message(
        chat.id,
        "Tap I'm calling it to claim a lead, Answered when a human picks up, "
        "then the outcome (Hung up, No R, On the phone). Calls over 5 minutes "
        "ask you to reply with notes. Do not even try hides a number. "
        "/mine shows your last 20 calls.",
    )


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not db.is_allowed(chat.id):
        return
    user = update.effective_user
    # db.attempts_for_user(user_id, limit) is not in the section 3 contract;
    # it must exist for /mine (SELECT * FROM attempts WHERE caller_id=? ORDER BY id DESC LIMIT ?).
    attempts = db.attempts_for_user(user.id, 20)
    if not attempts:
        await context.bot.send_message(chat.id, "You haven't made any calls yet")
        return
    lines = []
    for a in attempts:
        label = common.OUTCOMES.get(a.get("outcome"), "no outcome")
        lines.append(f"#{a['lead_id']}  {label}  {common.fmt_talk(a.get('talk_seconds'))}")
    await context.bot.send_message(chat.id, "\n".join(lines))


# --- greeting when added to a group ----------------------------------------


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Works in any chat, allowed or not."""
    change = update.my_chat_member
    if change is None:
        return
    old_status = change.old_chat_member.status
    new_status = change.new_chat_member.status
    if new_status not in ("member", "administrator"):
        return
    if old_status in ("member", "administrator"):
        return  # e.g. promoted member -> administrator; already greeted
    chat = change.chat
    await context.bot.send_message(
        chat.id,
        f"Hi! This chat's ID is {chat.id}. "
        f"Admin: send  /allow {chat.id}  to the admin bot to turn me on here.",
    )


# --- callbacks --------------------------------------------------------------


async def on_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if chat is None or not db.is_allowed(chat.id):
        return
    user = update.effective_user
    try:
        lead_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return

    pending = db.attempt_needing_notes(user.id)
    if pending:
        await query.answer(f"Write your notes on #{pending['lead_id']} first", show_alert=True)
        return
    open_attempt = db.open_attempt_for_user(user.id)
    if open_attempt:
        await query.answer(f"Finish #{open_attempt['lead_id']} first", show_alert=True)
        return

    attempt_id = db.claim_lead(lead_id, user.id, user.full_name)
    if attempt_id is None:
        lead = db.get_lead(lead_id)
        status = (lead or {}).get("status")
        if status == "blocked":
            await query.answer("This number is blocked", show_alert=True)
        elif status == "claimed":
            await _belongs_alert(query)
        else:
            await query.answer("Already claimed", show_alert=True)
        return

    await query.answer("It's yours")
    attempt = db.open_attempt_for_user(user.id) or {
        "id": attempt_id,
        "lead_id": lead_id,
        "caller_id": user.id,
        "caller_name": user.full_name,
    }
    lead, history = _lead_and_history(lead_id)
    if lead:
        await _safe_edit(
            query,
            common.format_lead(lead, attempt, history),
            common.claimed_keyboard(attempt_id, lead_id),
        )
    await _post_next(context, chat.id)


async def on_answered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if chat is None or not db.is_allowed(chat.id):
        return
    user = update.effective_user
    try:
        attempt_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return

    mine = db.open_attempt_for_user(user.id)
    if not mine or mine["id"] != attempt_id:
        if "Outcome:" in _card_text(query):
            await query.answer("Already closed", show_alert=True)
        else:
            await _belongs_alert(query, attempt_id)
        return

    if not db.mark_answered(attempt_id, user.id):
        await _belongs_alert(query, attempt_id)
        return

    await query.answer("Timer started")
    attempt = db.open_attempt_for_user(user.id) or mine
    lead, history = _lead_and_history(attempt["lead_id"])
    if lead:
        await _safe_edit(
            query,
            common.format_lead(lead, attempt, history),
            common.answered_keyboard(attempt_id),
        )


async def on_outcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if chat is None or not db.is_allowed(chat.id):
        return
    user = update.effective_user
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, attempt_id_str, key = parts
    if key not in common.OUTCOMES:
        await query.answer()
        return
    try:
        attempt_id = int(attempt_id_str)
    except ValueError:
        await query.answer()
        return

    mine = db.open_attempt_for_user(user.id)
    if not mine or mine["id"] != attempt_id:
        if "Outcome:" in _card_text(query):
            await query.answer("Already closed", show_alert=True)
        else:
            await _belongs_alert(query, attempt_id)
        return

    attempt = db.end_attempt(attempt_id, user.id, key)
    if attempt is None:
        await _belongs_alert(query, attempt_id)
        return

    label = common.OUTCOMES[key]
    await query.answer(f"Saved: {label}")
    lead, history = _lead_and_history(attempt["lead_id"])
    if lead:
        await _safe_edit(query, common.format_lead(lead, attempt, history), None)

    talk = attempt.get("talk_seconds")
    if talk and talk > NOTES_THRESHOLD and not attempt.get("notes") and query.message is not None:
        try:
            await query.message.reply_text(
                f"{_mention(user)} that ran {common.fmt_talk(talk)}. "
                "Reply to this message with what went wrong."
            )
        except BadRequest:
            pass


async def on_flag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if chat is None or not db.is_allowed(chat.id):
        return
    user = update.effective_user
    try:
        lead_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return

    lead = db.get_lead(lead_id)
    if lead is None:
        await query.answer("Lead not found", show_alert=True)
        return

    db.flag_phone(lead["phone"], user.id, user.full_name)
    await query.answer("Flagged for review")
    await _safe_edit(query, f"BLOCKED by {user.full_name} - pending review", None)
    await _post_next(context, chat.id)


# --- notes ------------------------------------------------------------------


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or not db.is_allowed(chat.id):
        return
    user = update.effective_user
    if user is None:
        return
    pending = db.attempt_needing_notes(user.id)
    if not pending:
        return
    db.add_notes(pending["id"], user.id, message.text)
    try:
        await message.reply_text("Noted.")
    except BadRequest:
        pass


# --- app --------------------------------------------------------------------


def build_app() -> Application:
    app = Application.builder().token(common.CRM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CallbackQueryHandler(on_claim, pattern="^claim:"))
    app.add_handler(CallbackQueryHandler(on_answered, pattern="^answered:"))
    app.add_handler(CallbackQueryHandler(on_outcome, pattern="^outcome:"))
    app.add_handler(CallbackQueryHandler(on_flag, pattern="^flag:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    return app


def main() -> None:
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
