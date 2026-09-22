# Telegram CRM Bot

Two Telegram bots that share one SQLite file (`crm.db`). The **admin bot** talks only to you: it decides which group chats are allowed, takes your CSV of leads, and pushes leads out. The **CRM bot** sits in your callers' group chat and posts one lead at a time. The first caller to tap *I'm calling it* owns that lead, then taps how the call went. Leads are posted as plain text, and the CRM bot must be added to the group before it can post anything.

## Setup

1. **Make two bots.** In Telegram, open **@BotFather** and send `/newbot`. Follow the prompts and copy the token it gives you. Do it a second time for the second bot. Name them something like `MyAdminBot` and `MyCRMBot`. Keep both tokens.
2. **Get your user ID.** Open **@userinfobot** in Telegram and send it anything. It replies with your ID, a number.
3. **Fill in the config.** Copy `.env.example` to `.env`. Paste in the admin bot token, the CRM bot token, and your user ID.
4. **Run it.** In a terminal in this folder: `pip install -r requirements.txt`, then `python run.py`. Leave it running.
5. **Connect your group.** Add the CRM bot to your callers' group chat. It posts the chat's ID. Open your admin bot and send `/allow <that id>`.
6. **Load leads.** Send your CSV file to the admin bot. Any columns work; the first row is the headers. Then send `/push`. The first lead appears in the group.
7. **Work the leads.** A caller taps *I'm calling it*. The next lead posts automatically. When done, the caller taps *No R*, *Hung up*, or *On the phone*.

## Admin commands

Send these to the admin bot in a private chat. Only your user ID is accepted; anyone else is told their own user ID and ignored.

| Command | What it does |
|---|---|
| `/start`, `/help` | Shows this list of commands. |
| `/allow <chat_id> [title]` | Lets the CRM bot work in that group chat. |
| `/revoke <chat_id>` | Stops the CRM bot working in that group chat. |
| `/chats` | Lists the allowed group chats. |
| Upload a `.csv` file | Loads the rows as new leads. First row is the headers. |
| `/push [chat_id]` | Posts the next new lead to the group. If only one chat is allowed, you can leave out the ID. |
| `/release <lead_id>` | Un-claims a lead so someone else can take it. |
| `/stats` | Shows how many leads are new, posted, claimed, and each outcome count. |

## Caller commands

Anyone in an allowed group chat can use these with the CRM bot.

- `/id` shows the chat's ID (works in any chat, even before it is allowed).
- `/start`, `/help` shows a short how-to.
- `/mine` lists the leads you have claimed and their outcomes.
- Tap *I'm calling it* on a lead to claim it. The first person to tap wins.
- Tap *No R*, *Hung up*, or *On the phone* on a lead you claimed to record how the call went. You can change it later by tapping another one.

## Assumptions

Where the brief left something open, we picked the simplest option. The choices are listed here.

- Timestamps (`claimed_at`, `outcome_at`) are stored as UTC text in `YYYY-MM-DD HH:MM:SS` form, matching SQLite's `CURRENT_TIMESTAMP` style.
- A lead post skips a CSV column when its value is empty or only spaces, and prints columns in the CSV header order.
- `/chats` lists chats in the order they were allowed. Re-allowing a chat moves it to the end.
- `add_leads` returns the number of rows inserted and stores non-Latin text as-is, so it stays readable in the database.
- CSV rows are tidied before saving: header names and values are trimmed, values past the last header are kept as `Column N`, and fully blank rows are skipped. A CSV with no lead rows gets a friendly message instead of `Loaded 0 leads`.
- CSV files are decoded as UTF-8 with any Excel byte-order mark removed, falling back to latin-1.
- `/push <chat_id>` for a chat that is not allowed replies `Chat <id> is not allowed. Send /allow <id> first.` Missing or non-numeric arguments on `/allow`, `/revoke`, `/push` and `/release` get a one-line usage hint.
- `/stats` lines are labelled `New`, `Posted`, `Claimed`, `No R`, `Hung up`, `On the phone`.
- `/release` skips the group-message edit when the lead was never posted (no chat or message ID).
- The added-to-group greeting is sent only when the CRM bot goes from not-a-member to member or administrator, so promoting it later does not repeat the greeting.
- If a claim fails and the lead has no recorded owner name (for example it was just released), the alert says `Already claimed by someone else`.
- Malformed button data (a non-numeric lead ID or a bad key) is answered silently and ignored.
- The environment used to build this had `python-telegram-bot` 22.8 installed, which is within the `>=21.0` requirement.
