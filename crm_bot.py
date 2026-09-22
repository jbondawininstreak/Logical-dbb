"""Group-facing CRM bot: posts leads, lets callers claim them and set outcomes."""

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
)

import common
import db


async def _safe_edit(query, text: str, reply_markup) -> None:
    """Edit the callback's message; ignore BadRequest (e.g. text unchanged)."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest:
        pass


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
        "Tap I'm calling it to claim a lead, then tap the outcome when you're done. "
        "/mine shows your leads.",
    )


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not db.is_allowed(chat.id):
        return
    user = update.effective_user
    leads = db.leads_for_user(user.id)[:20]
    if not leads:
        await context.bot.send_message(chat.id, "You haven't claimed any leads yet")
        return
    lines = []
    for lead in leads:
        label = common.OUTCOMES.get(lead.get("outcome"), "no outcome yet")
        lines.append(f"#{lead['id']}  {label}")
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

    ok = db.claim_lead(lead_id, user.id, user.full_name)
    if not ok:
        lead = db.get_lead(lead_id)
        name = (lead or {}).get("claimed_by_name") or "someone else"
        await query.answer(f"Already claimed by {name}", show_alert=True)
        return

    await query.answer("It's yours")
    lead = db.get_lead(lead_id)
    await _safe_edit(query, common.format_lead(lead), common.outcome_keyboard(lead_id))

    posted = await common.post_next_lead(chat.id)
    if not posted:
        await context.bot.send_message(chat.id, "No more leads in the queue.")


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
    _, lead_id_str, key = parts
    if key not in common.OUTCOMES:
        await query.answer()
        return
    try:
        lead_id = int(lead_id_str)
    except ValueError:
        await query.answer()
        return

    ok = db.set_outcome(lead_id, user.id, key)
    if not ok:
        await query.answer(
            "Only the person who claimed this can set the outcome", show_alert=True
        )
        return

    await query.answer(f"Saved: {common.OUTCOMES[key]}")
    lead = db.get_lead(lead_id)
    await _safe_edit(query, common.format_lead(lead), common.outcome_keyboard(lead_id))


# --- app --------------------------------------------------------------------


def build_app() -> Application:
    app = Application.builder().token(common.CRM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CallbackQueryHandler(on_claim, pattern="^claim:"))
    app.add_handler(CallbackQueryHandler(on_outcome, pattern="^outcome:"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    return app


def main() -> None:
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
