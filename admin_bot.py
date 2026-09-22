"""Admin bot: talks to exactly one person (ADMIN_USER_ID) and ignores everyone else."""

import csv
import io
import logging

from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import common
import db

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)

HELP_TEXT = "\n".join(
    [
        "I'm the admin bot. Here is what I understand:",
        "",
        "/allow <chat_id> [title] - let the CRM bot work in that group chat",
        "/revoke <chat_id> - stop the CRM bot working in that group chat",
        "/chats - show the allowed group chats",
        "Send me a .csv file - load its rows as new leads (first row is the headers)",
        "/push [chat_id] - post the next new lead to a group chat",
        "/release <lead_id> - un-claim a lead so someone else can take it",
        "/stats - show how many leads are new, posted, claimed and each outcome",
        "/help - show this message",
    ]
)


async def _authorized(update: Update) -> bool:
    """Reply with the user's ID and return False if this is not the admin."""
    user = update.effective_user
    user_id = user.id if user else None
    if user_id == common.ADMIN_USER_ID:
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            f"Not authorized. Your Telegram user ID is {user_id}."
        )
    return False


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    await update.effective_message.reply_text(HELP_TEXT)


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /allow <chat_id> [title]")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Usage: /allow <chat_id> [title]")
        return
    title = " ".join(args[1:])
    db.add_chat(chat_id, title)
    await update.effective_message.reply_text(f"Allowed {chat_id}")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /revoke <chat_id>")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Usage: /revoke <chat_id>")
        return
    if db.remove_chat(chat_id):
        await update.effective_message.reply_text(f"Revoked {chat_id}")
    else:
        await update.effective_message.reply_text("That chat wasn't allowed")


def _format_chats(chats: list) -> str:
    return "\n".join(f"{c['chat_id']}  {c.get('title') or ''}".rstrip() for c in chats)


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    chats = db.list_chats()
    if not chats:
        await update.effective_message.reply_text("No chats allowed yet")
        return
    await update.effective_message.reply_text(_format_chats(chats))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    message = update.effective_message
    document = message.document
    name = (document.file_name or "").lower()
    mime = (document.mime_type or "").lower()
    if not (name.endswith(".csv") or mime == "text/csv"):
        await message.reply_text(
            "Sorry, I can only load leads from a .csv file. Please send a CSV."
        )
        return

    file = await document.get_file()
    raw = bytes(await file.download_as_bytearray())
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        clean = {}
        for key, value in row.items():
            if key is None:
                continue
            if isinstance(value, list):
                value = ", ".join(v for v in value if v)
            clean[str(key).strip()] = (value or "").strip()
        if any(clean.values()):
            rows.append(clean)

    if not rows:
        await message.reply_text(
            "That CSV has no lead rows. The first row must be the headers, "
            "followed by one lead per row."
        )
        return

    n = db.add_leads(rows)
    await message.reply_text(f"Loaded {n} leads. Send /push to post the first one.")


async def cmd_push(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    message = update.effective_message
    args = context.args or []
    chats = db.list_chats()

    if args:
        try:
            chat_id = int(args[0])
        except ValueError:
            await message.reply_text("Usage: /push [chat_id]")
            return
        if not db.is_allowed(chat_id):
            await message.reply_text(
                f"Chat {chat_id} is not allowed. Send /allow {chat_id} first."
            )
            return
    elif len(chats) == 1:
        chat_id = chats[0]["chat_id"]
    elif not chats:
        await message.reply_text(
            "No chats allowed yet. Send /allow <chat_id> first, then /push <chat_id>."
        )
        return
    else:
        await message.reply_text(
            "Several chats are allowed. Send /push <chat_id> with one of these:\n"
            + _format_chats(chats)
        )
        return

    if await common.post_next_lead(chat_id):
        await message.reply_text("Posted")
    else:
        await message.reply_text("No new leads left")


async def cmd_release(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    message = update.effective_message
    args = context.args or []
    if not args:
        await message.reply_text("Usage: /release <lead_id>")
        return
    try:
        lead_id = int(args[0].lstrip("#"))
    except ValueError:
        await message.reply_text("Usage: /release <lead_id>")
        return

    if not db.release_lead(lead_id):
        await message.reply_text(f"Lead {lead_id} is not claimed")
        return

    lead = db.get_lead(lead_id)
    if lead and lead.get("chat_id") and lead.get("message_id"):
        try:
            await Bot(common.CRM_BOT_TOKEN).edit_message_text(
                chat_id=lead["chat_id"],
                message_id=lead["message_id"],
                text=common.format_lead(lead),
                reply_markup=common.claim_keyboard(lead_id),
            )
        except Exception as exc:  # editing is best effort
            log.warning("Could not edit message for lead %s: %s", lead_id, exc)

    await message.reply_text(f"Lead {lead_id} released")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    s = db.stats()
    lines = [
        f"New: {s.get('new', 0)}",
        f"Posted: {s.get('posted', 0)}",
        f"Claimed: {s.get('claimed', 0)}",
        f"No R: {s.get('no_response', 0)}",
        f"Hung up: {s.get('hung_up', 0)}",
        f"On the phone: {s.get('on_phone', 0)}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    await update.effective_message.reply_text("Send /help")


def build_app() -> Application:
    app = Application.builder().token(common.ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("chats", cmd_chats))
    app.add_handler(CommandHandler("push", cmd_push))
    app.add_handler(CommandHandler("release", cmd_release))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL, fallback))
    return app


def main() -> None:
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
