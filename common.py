"""Config, outcome labels, message formatting, keyboards, and post_next_lead.

Shared by admin_bot.py and crm_bot.py.
"""

import os
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import db

load_dotenv()
ADMIN_BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
CRM_BOT_TOKEN   = os.environ["CRM_BOT_TOKEN"]
ADMIN_USER_ID   = int(os.environ["ADMIN_USER_ID"])

OUTCOMES = {                   # callback key -> button label
    "no_response": "No R",
    "hung_up":     "Hung up",
    "on_phone":    "On the phone",
}


def format_lead(lead: dict) -> str:
    lines = [f"Lead #{lead['id']}"]
    for header, value in (lead.get("data") or {}).items():
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        lines.append(f"{header}: {value}")
    if lead.get("claimed_by_name"):
        lines.append("")
        lines.append(f"Claimed by {lead['claimed_by_name']}")
    if lead.get("outcome"):
        label = OUTCOMES.get(lead["outcome"], lead["outcome"])
        lines.append(f"Outcome: {label}")
    return "\n".join(lines)


def claim_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("I'm calling it", callback_data=f"claim:{lead_id}")]]
    )


def outcome_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(label, callback_data=f"outcome:{lead_id}:{key}")
        for key, label in OUTCOMES.items()
    ]
    return InlineKeyboardMarkup([row])


async def post_next_lead(chat_id: int) -> bool:
    lead = db.next_new_lead()
    if lead is None:
        return False
    sent = await Bot(CRM_BOT_TOKEN).send_message(
        chat_id, format_lead(lead), reply_markup=claim_keyboard(lead["id"])
    )
    db.mark_posted(lead["id"], chat_id, sent.message_id)
    return True
