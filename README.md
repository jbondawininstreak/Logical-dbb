# Telegram CRM v2

Two Telegram bots and a web dashboard, sharing one database hosted on Turso (a free, hosted SQLite). The **CRM bot** sits in your callers' group chat and posts one lead at a time: callers tap *I'm calling it* to take it, *Answered* when a human picks up, then how the call went. A call that runs over 5 minutes asks the caller to type a few words of notes, and any caller can tap *Do not even try* to hide a number for good. Every lead carries a *source* (the name you give a CSV when you upload it), and a number that has been called before shows its past attempts right on the lead card. The **dashboard** is where you run things: upload leads, push them to the group, manage allowed chats, review flagged numbers, and see outcomes per source and per caller. The **admin bot** from v1 still runs as a fallback for the same jobs over Telegram. Leads are posted as plain text, and the CRM bot must be added to the group before it can post anything.

## Setup

1. **Make two bots.** In Telegram, open **@BotFather** and send `/newbot`. Follow the prompts and copy the token it gives you. Do it a second time for the second bot. Name them something like `MyAdminBot` and `MyCRMBot`. Keep both tokens.
2. **Get your user ID.** Open **@userinfobot** in Telegram and send it anything. It replies with your ID, a number.
3. **Make a Turso database.** Go to turso.tech, sign up (free, no card), click *Create database*, name it `crm`. On the database page copy the **URL** (shown as `libsql://...`) and click *Generate token*; copy that too. Paste the URL as-is; the app swaps the `libsql://` scheme for `https://`, which is the transport Turso actually accepts.
4. **Fill in `.env`.** Copy `.env.example` to `.env`: `ADMIN_BOT_TOKEN`, `CRM_BOT_TOKEN`, `ADMIN_USER_ID`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `DASHBOARD_KEY` (any long random word you make up). `DEFAULT_COUNTRY` is optional: the two-letter country the upload form starts on and the one the admin bot uses, `GB` when it is not set.
5. **Run it.** `pip install -r requirements.txt` then `python run.py`. Open `http://localhost:8000/?key=<your DASHBOARD_KEY>` in a browser.
6. **Connect the group.** Add the CRM bot to the callers' group; it posts the chat ID. On the dashboard's *Chats* page, add that ID.
7. **Upload leads.** Dashboard *Upload*: pick the file, type a source name (for example `list-oct-18`), choose the country its local-looking numbers should be read as, and submit. You get a row-by-row check first and nothing is saved until you press *Load*. Then click *Push*.
8. **Work the leads.** Callers tap *I'm calling it*, then *Answered* when a human picks up, then the outcome. Calls over 5 minutes ask for notes. *Do not even try* hides a number; confirm or unflag it on the *Flags* page.

## Hosting on a server

The steps above run everything on your own computer, which has to stay on. To run it on a small server instead, so it works day and night and the dashboard has a proper web address:

1. **Get a server.** Any provider works (Hetzner, DigitalOcean, Vultr, and so on). Pick Ubuntu 24.04 and the smallest size. Write down its public IP address.
2. **Run the setup script.** Log in to the server as root and run one line, using your own domain name:
   ```
   curl -fsSL https://raw.githubusercontent.com/jbondawininstreak/Logical-dbb/main/deploy/setup.sh | bash -s -- crm.yourdomain.com
   ```
   It installs Python and Caddy, puts the code in `/opt/crm`, creates a service that starts on boot, and opens only ports 22, 80 and 443.
3. **Fill in the config.** `nano /opt/crm/.env` and paste in the same six values as step 4 above. Then `systemctl start crm`.
4. **Point the domain at it.** In cloaked.gg, open the DNS page for your domain and add an **A record**: name `crm` (or `@` for the bare domain), value the server's IP, with the proxy turned on. Give it a minute.
5. **Open the dashboard.** `https://crm.yourdomain.com/?key=<your DASHBOARD_KEY>`. Caddy fetches the HTTPS certificate on the first visit.

Useful afterwards:

- Logs: `journalctl -u crm -f`
- Update to the latest code: run the same setup line again, or `cd /opt/crm && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart crm`
- Restart: `systemctl restart crm`

Keep it private. The key is in every dashboard link, so do not share links or screenshots of the address bar. cloaked.gg's firewall sits in front, and the dashboard itself only listens on the server's own loopback address, so the only way in is through Caddy on your domain. If cloaked.gg publishes the IP ranges its proxy uses, you can tighten things further by letting ports 80 and 443 through `ufw` only from those ranges, so nobody can reach the server by its bare IP.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests
```

They run against a throwaway SQLite file, never a hosted database: `tests/conftest.py`
sets `TURSO_DATABASE_URL` before anything imports `db`, and refuses to run if it is
not a `file:` URL. The sample files in every supported format are built during the
run rather than committed.

What they are there to hold still:

- Every format lands on the same list of row dicts, and parsing leaves phone values
  exactly as written.
- A number is only turned into E.164 against a chosen country, and a number that
  country cannot explain is rejected rather than guessed at.
- A lead key is digits only, so the same number matches its own earlier uploads,
  flags and call history whatever shape it arrived in.
- A preview writes nothing, and the button that turns it into leads cannot be
  pressed twice.
- A dashboard link never loses its key.

## Dashboard pages

Every page needs `?key=<your DASHBOARD_KEY>` on the end of the address, or it answers 403. Bookmark `http://localhost:8000/?key=...` once and use the links on the page.

| Page | What it shows or does |
|---|---|
| `/` | Queue counts (new, posted, claimed, closed, blocked), outcomes per source with an answer rate, outcomes per caller, and the *Push* button that posts the next lead to a chat. |
| `/upload` | Upload a file in any supported format with a source name, a country, a time zone and an optional phone column. Shows every row with its status before anything is written; *Load* then saves only the rows that passed. |
| `/sources` | Every source uploaded so far, its lead count, and the country and time zone it was read as. |
| `/chats` | The allowed group chats; add one by ID and title, or remove one. |
| `/flags` | Numbers callers marked *Do not even try*, pending first; *Confirm* keeps the block, *Unflag* puts the leads back in the queue. |
| `/attempts` | The last 200 call attempts, newest first: date, caller, phone, source, outcome, talk time, notes. |
| `/lead/<phone>` | Everything known about one phone number: every lead row and every attempt. |

## Caller buttons and commands

Anyone in an allowed group chat can use these with the CRM bot.

- `/id` shows the chat's ID (works in any chat, even before it is allowed).
- `/start`, `/help` shows a short how-to.
- `/mine` lists your last 20 attempts with their outcome and talk time.
- Tap *I'm calling it* on a lead to claim it. The first person to tap wins, and the next lead posts straight away. You can only hold one lead at a time.
- Tap *Answered* when a human picks up. The talk timer starts here.
- Tap *No R* if nobody answered (before or after *Answered*).
- Tap *Hung up* when they hung up on you. The timer stops.
- Tap *On the phone* when the call went well. The timer stops.
- Tap *Do not even try* on any lead to hide that number. Anyone can tap it; the card says `BLOCKED by <name> - pending review` and the admin confirms or unflags it on the dashboard.
- Only the caller who claimed a lead can tap its buttons; anyone else is told whose it is.
- **Notes rule:** if a call ran over 5 minutes, the bot replies to the card asking what went wrong. Reply to that message with your notes. Until you do, you cannot claim another lead.

## Admin bot (fallback)

The dashboard is the normal way to run things. The admin bot still answers these in a private chat, for when you only have your phone. Only your user ID is accepted; anyone else is told their own user ID and ignored.

| Command | What it does |
|---|---|
| `/start`, `/help` | Shows the list of commands. |
| `/allow <chat_id> [title]` | Lets the CRM bot work in that group chat. |
| `/revoke <chat_id>` | Stops the CRM bot working in that group chat. |
| `/chats` | Lists the allowed group chats. |
| Upload a file | Loads the rows as new leads, running the same checks as the dashboard. The file name without its extension becomes the source name. CSV, TSV, text, Excel and JSON are accepted. There is no preview here, so numbers are read as `DEFAULT_COUNTRY` and numbers from other countries are kept rather than held back. |
| `/push [chat_id]` | Posts the next new lead to the group. If only one chat is allowed, you can leave out the ID. |
| `/release <lead_id>` | Un-claims a lead so someone else can take it. |
| `/stats` | Shows how many leads are new, posted, claimed, closed, blocked, and each outcome count. |

## Assumptions

Where the brief left something open, we picked the simplest option. The choices are listed here.

- Timestamps (`claimed_at`, `answered_at`, `ended_at`, `flagged_at`) are stored as UTC text in `YYYY-MM-DD HH:MM:SS` form, matching SQLite's `CURRENT_TIMESTAMP` style. The `On the line since HH:MM` line on a lead card is shown in the server's local time.
- A lead post skips a CSV column when its value is empty or only spaces, and prints columns in the CSV header order.
- `/chats` lists chats in the order they were allowed. Re-allowing a chat moves it to the end.
- CSV rows are tidied before saving: header names and values are trimmed, values past the last header are kept as `Column N`, and fully blank rows are skipped. A CSV with no lead rows gets a friendly message instead of `Loaded 0 leads`.
- CSV files are decoded as UTF-8 with any Excel byte-order mark removed, falling back to latin-1.
- If the first CSV row holds an email, a phone number or a date, the file is treated as having no header row: that row is kept as a lead and the columns are named Email, Phone, Region, Date or `Column N` from their contents.
- Phone numbers are turned into E.164 by Google's libphonenumber (the `phonenumbers` package), not by rules of our own. A number written the local way is read with the country chosen on the upload form; one that already starts with `+` or `00` carries its own country code and that is used instead. Excel's `4.48E+11` form is unwrapped first. A number libphonenumber does not accept as real and dialable is held back, not loaded.
- The country a number turns out to belong to is compared by calling code, not by country letters, because `+44` covers GB, GG, JE and IM and a real UK mobile often reports itself as Guernsey. Without that, *only this country* would hold back valid UK numbers.
- Lead keys are still digits only (`+447911123456` is keyed as `447911123456`), so a number matches its own earlier uploads, flags and call history whatever shape and country format it arrived in.
- Nothing is written to the database until the upload preview is accepted. The parsed file waits in the dashboard's own memory for half an hour, so a restart or a long pause means uploading it again; no leads are half-loaded either way.
- An upload's file format is picked from its extension, falling back to the first bytes of the file (a `PK` header is a workbook, a leading `[` or `{` is JSON, anything else is read as delimited text). Delimited files have their separator sniffed, so comma, tab, semicolon and pipe all work, and a file with one value per line is read as a single column.
- A spreadsheet's first sheet is the one that is read. Numbers and dates arrive as real values rather than text, so `448000000000.0` loses its `.0` and a date cell becomes `YYYY-MM-DD`.
- JSON may be an array of objects (each object's own keys become the columns), one object per line, or an array of arrays (read as a grid like a CSV).
- `/push <chat_id>` on the admin bot for a chat that is not allowed replies `Chat <id> is not allowed. Send /allow <id> first.` Missing or non-numeric arguments on `/allow`, `/revoke`, `/push` and `/release` get a one-line usage hint.
- Admin bot `/stats` lines are labelled `New`, `Posted`, `Claimed`, `Closed`, `Blocked`, `No R`, `Hung up`, `On the phone`.
- `/release` skips the group-message edit when the lead was never posted (no chat or message ID).
- The added-to-group greeting is sent only when the CRM bot goes from not-a-member to member or administrator, so promoting it later does not repeat the greeting.
- Malformed button data (a non-numeric ID or a bad key) is answered silently and ignored.
- The environment used to build this had `python-telegram-bot` 22.8 installed, which is within the `>=21.0` requirement.
- `python-multipart` is in `requirements.txt` because FastAPI needs it to read HTML forms and file uploads.
- The admin bot's CSV upload uses the file name without `.csv` as the source name (`admin-upload` if the file has no name); the dashboard asks for a source name on the form.
- `db.release_lead` and `db.stats` are kept from v1 so the fallback admin bot's `/release` and `/stats` still work.
- `Do not even try` on a lead that is already claimed ends that attempt with no outcome; it is not counted as a call.
- Flags can only be confirmed or unflagged on the dashboard. A pending flag already hides the number from the queue; confirming it only records that an admin agreed.

- Dashboard *Push* shows a plain error page when Telegram refuses the post (wrong token, bot not in the group) and a "Nothing to push" page when the queue is empty, instead of a bare server error.
- `add_leads` raises a clear error naming the available columns when no phone column can be found; a blank source name is stored as `unknown`; rows whose phone has no digits are skipped silently; a phone seen in an earlier upload is stored again as a new lead, which is what makes `Seen before:` work.
- `end_attempt` refuses an outcome key outside No R, Hung up and On the phone.
- Four indexes are created alongside the tables (lead status, lead phone, attempt phone, attempt caller) so the hosted database stays quick as leads pile up.
- The answered keyboard shows the buttons in the brief's order: Hung up, No R, On the phone.
- The notes prompt mentions the caller by `@username` when they have one, otherwise by their full name. Any non-command text the caller then sends in the group is taken as the notes, not only a Telegram reply to the prompt.
- A tap on a card that is already closed answers `Already closed`; a claim on a blocked number answers `This number is blocked`.
- Dashboard answer rate and average talk show `-` when there are no attempts; a non-numeric chat ID on Push or Chats returns a 400 or an inline error.
- The hosted-database client is re-created after `run.py` forks its three processes and closed on exit, because the libsql client's background thread otherwise stops a process from exiting.
- The dashboard listens on `127.0.0.1:8000` unless `DASHBOARD_HOST` or `DASHBOARD_PORT` says otherwise, so on a server it is reachable only through Caddy.
- Caddy is the web server in front of the dashboard because it obtains and renews the HTTPS certificate on its own with no extra steps.
- The systemd service runs the two bots and the dashboard together as one unit, because `run.py` already starts all three; a crash restarts all of them after five seconds.
