"""Web dashboard for the Telegram CRM (v2).

One FastAPI app, server-rendered HTML strings with a little inline CSS.
Every route requires ?key=DASHBOARD_KEY (POST forms may also send it as a
form field) or gets a 403. Started on port 8000 by run.py via main().
"""

import html
import json
from typing import Optional
from urllib.parse import quote

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import common
import db

app = FastAPI(title="CRM dashboard", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------------------
# auth

async def require_key(request: Request) -> str:
    """Read `key` from the query string, or from the form body on POST."""
    key = request.query_params.get("key")
    if key is None and request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "form" in ctype:
            try:
                form = await request.form()
                key = form.get("key")
            except Exception:
                key = None
    if not common.DASHBOARD_KEY or key != common.DASHBOARD_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    return key


# ---------------------------------------------------------------------------
# html helpers

CSS = """
body { font-family: sans-serif; font-size: 14px; margin: 16px; }
table { border-collapse: collapse; margin: 8px 0 16px 0; }
th, td { border: 1px solid #888; padding: 3px 8px; text-align: left; vertical-align: top; }
th { background: #eee; }
nav a { margin-right: 12px; }
form.inline { display: inline; }
.muted { color: #666; }
pre { margin: 0; white-space: pre-wrap; }
"""

PAGES = [("/", "Home"), ("/upload", "Upload"), ("/chats", "Chats"),
         ("/flags", "Flags"), ("/attempts", "Attempts")]


def e(value) -> str:
    """Escape anything for HTML; None becomes empty string."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def href(path: str, key: str) -> str:
    sep = "&" if "?" in path else "?"
    return e(f"{path}{sep}key={quote(key, safe='')}")


def hidden_key(key: str) -> str:
    return f'<input type="hidden" name="key" value="{e(key)}">'


def nav(key: str) -> str:
    return "<nav>" + "".join(
        f'<a href="{href(p, key)}">{e(label)}</a>' for p, label in PAGES
    ) + "</nav><hr>"


def page(title: str, body: str, key: str) -> HTMLResponse:
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body>"
        f"{nav(key)}<h2>{e(title)}</h2>{body}</body></html>"
    )
    return HTMLResponse(doc)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Rows contain already-escaped HTML cells."""
    if not rows:
        return "<p class='muted'>(none)</p>"
    out = ["<table><tr>"]
    out += [f"<th>{e(h)}</th>" for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def phone_link(phone, key: str) -> str:
    if not phone:
        return ""
    return f'<a href="{href("/lead/" + quote(str(phone), safe=""), key)}">{e(phone)}</a>'


def outcome_label(outcome) -> str:
    if not outcome:
        return ""
    return common.OUTCOMES.get(outcome, outcome)


def pct(num, den) -> str:
    try:
        num = int(num or 0)
        den = int(den or 0)
    except (TypeError, ValueError):
        return ""
    if den == 0:
        return "-"
    return f"{100.0 * num / den:.0f}%"


def avg_talk(seconds) -> str:
    if seconds is None:
        return "-"
    try:
        return common.fmt_talk(int(round(float(seconds))))
    except (TypeError, ValueError):
        return e(seconds)


def pick(row: dict, *names, default=""):
    """First present key among several candidate names (A's exact key names
    for stats rows are not pinned down by the contract)."""
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return default


def redirect(path: str, key: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}key={quote(key, safe='')}", status_code=303)


# ---------------------------------------------------------------------------
# GET /

@app.get("/", response_class=HTMLResponse)
async def home(key: str = Depends(require_key)):
    counts = db.stats()
    counts_html = table(
        ["new", "posted", "claimed", "closed", "blocked"],
        [[e(counts.get(k, 0)) for k in ("new", "posted", "claimed", "closed", "blocked")]],
    )

    src_rows = []
    for s in db.stats_by_source():
        attempts = pick(s, "attempts", default=0)
        answered = pick(s, "answered", default=0)
        src_rows.append([
            e(pick(s, "source", "name", "source_name")),
            e(pick(s, "leads", default=0)),
            e(attempts),
            e(answered),
            e(pct(answered, attempts)),
            e(pick(s, "no_response", default=0)),
            e(pick(s, "hung_up", default=0)),
            e(pick(s, "on_phone", default=0)),
            e(avg_talk(s.get("avg_talk_seconds"))),
        ])
    src_html = table(
        ["Source", "Leads", "Attempts", "Answered", "Answer rate",
         "No R", "Hung up", "On the phone", "Avg talk"],
        src_rows,
    )

    caller_rows = []
    for c in db.stats_by_caller():
        caller_rows.append([
            e(pick(c, "caller_name", "caller", "name") or pick(c, "caller_id")),
            e(pick(c, "attempts", default=0)),
            e(pick(c, "answered", default=0)),
            e(pct(pick(c, "answered", default=0), pick(c, "attempts", default=0))),
            e(avg_talk(c.get("avg_talk_seconds"))),
            e(pick(c, "notes_written", default=0)),
        ])
    caller_html = table(
        ["Caller", "Attempts", "Answered", "Answer rate", "Avg talk", "Notes written"],
        caller_rows,
    )

    chats = db.list_chats()
    if chats:
        options = "".join(
            f'<option value="{e(ch["chat_id"])}">{e(ch["chat_id"])} '
            f'{e(ch.get("title") or "")}</option>'
            for ch in chats
        )
        push_html = (
            f'<form method="post" action="{href("/push", key)}">{hidden_key(key)}'
            f'<select name="chat_id">{options}</select> '
            '<button type="submit">Push next lead</button></form>'
        )
    else:
        push_html = (
            f"<p class='muted'>No allowed chats. "
            f"<a href=\"{href('/chats', key)}\">Add one</a> first.</p>"
        )

    body = (
        "<h3>Queue</h3>" + counts_html +
        "<h3>Push</h3>" + push_html +
        "<h3>By source</h3>" + src_html +
        "<h3>By caller</h3>" + caller_html
    )
    return page("CRM dashboard", body, key)


# ---------------------------------------------------------------------------
# upload

def upload_form(key: str) -> str:
    return (
        f'<form method="post" action="{href("/upload", key)}" '
        f'enctype="multipart/form-data">{hidden_key(key)}'
        '<p>CSV file: <input type="file" name="csv" accept=".csv,text/csv" required></p>'
        '<p>Source name: <input type="text" name="source" required></p>'
        '<p>Phone column (optional): <input type="text" name="phone_column"></p>'
        '<p><button type="submit">Upload</button></p></form>'
    )


@app.get("/upload", response_class=HTMLResponse)
async def upload_get(key: str = Depends(require_key)):
    return page("Upload leads", upload_form(key), key)


@app.post("/upload", response_class=HTMLResponse)
async def upload_post(
    key: str = Depends(require_key),
    csv: UploadFile = File(...),
    source: str = Form(...),
    phone_column: Optional[str] = Form(""),
):
    raw = await csv.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    source = (source or "").strip()
    if not source:
        return page("Upload leads", "<p><b>Source name is required.</b></p>" + upload_form(key), key)
    rows = common.parse_csv(text)
    result = db.add_leads(rows, source, (phone_column or "").strip())
    body = (
        f"<p>File <b>{e(csv.filename)}</b> &rarr; source <b>{e(source)}</b>: "
        f"{e(len(rows))} rows parsed, <b>{e(result.get('added', 0))}</b> added, "
        f"<b>{e(result.get('skipped_blocked', 0))}</b> skipped (do not even try).</p>"
        f"<p><a href=\"{href('/', key)}\">Back to dashboard</a></p>"
        "<h3>Upload another</h3>" + upload_form(key)
    )
    return page("Upload leads", body, key)


# ---------------------------------------------------------------------------
# push

@app.post("/push")
async def push(key: str = Depends(require_key), chat_id: str = Form(...)):
    try:
        cid = int(chat_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="chat_id must be an integer")
    try:
        posted = await common.post_next_lead(cid)
    except Exception as exc:  # Telegram refused the send: bad token, bot not in chat
        return page(
            "Push failed",
            f"<p>Telegram did not accept the post: {e(str(exc))}</p>"
            "<p>Check that the CRM bot is in that group and the token is right.</p>",
            key,
        )
    if not posted:
        return page("Nothing to push", "<p>No new leads left in the queue.</p>", key)
    return redirect("/", key)


# ---------------------------------------------------------------------------
# chats

def chats_page(key: str, message: str = "") -> HTMLResponse:
    rows = []
    for ch in db.list_chats():
        remove_form = (
            f'<form class="inline" method="post" action="{href("/chats", key)}">'
            f'{hidden_key(key)}<input type="hidden" name="action" value="remove">'
            f'<input type="hidden" name="chat_id" value="{e(ch["chat_id"])}">'
            '<button type="submit">Remove</button></form>'
        )
        rows.append([e(ch["chat_id"]), e(ch.get("title") or ""), remove_form])
    add_form = (
        f'<form method="post" action="{href("/chats", key)}">{hidden_key(key)}'
        '<input type="hidden" name="action" value="add">'
        'Chat id: <input type="text" name="chat_id" required> '
        'Title: <input type="text" name="title"> '
        '<button type="submit">Add</button></form>'
    )
    body = (message + "<h3>Allowed chats</h3>" + table(["Chat id", "Title", ""], rows)
            + "<h3>Add a chat</h3>" + add_form)
    return page("Chats", body, key)


@app.get("/chats", response_class=HTMLResponse)
async def chats_get(key: str = Depends(require_key)):
    return chats_page(key)


@app.post("/chats", response_class=HTMLResponse)
async def chats_post(
    key: str = Depends(require_key),
    action: str = Form(...),
    chat_id: str = Form(...),
    title: Optional[str] = Form(""),
):
    try:
        cid = int(chat_id.strip())
    except ValueError:
        return chats_page(key, "<p><b>Chat id must be an integer.</b></p>")
    if action == "add":
        db.add_chat(cid, (title or "").strip())
        msg = f"<p>Added chat {e(cid)}.</p>"
    elif action == "remove":
        db.remove_chat(cid)
        msg = f"<p>Removed chat {e(cid)}.</p>"
    else:
        raise HTTPException(status_code=400, detail="unknown action")
    return chats_page(key, msg)


# ---------------------------------------------------------------------------
# flags

def flag_row(f: dict, key: str) -> list[str]:
    phone = f.get("phone")
    confirm = (
        f'<form class="inline" method="post" action="{href("/flags/confirm", key)}">'
        f'{hidden_key(key)}<input type="hidden" name="phone" value="{e(phone)}">'
        '<button type="submit">Confirm</button></form>'
    )
    unflag = (
        f'<form class="inline" method="post" action="{href("/flags/unflag", key)}">'
        f'{hidden_key(key)}<input type="hidden" name="phone" value="{e(phone)}">'
        '<button type="submit">Unflag</button></form>'
    )
    confirmed = bool(f.get("confirmed"))
    return [
        phone_link(phone, key),
        e(f.get("flagged_by_name") or f.get("flagged_by") or ""),
        e(f.get("flagged_at")),
        "confirmed" if confirmed else "pending",
        ("" if confirmed else confirm) + " " + unflag,
    ]


@app.get("/flags", response_class=HTMLResponse)
async def flags_get(key: str = Depends(require_key)):
    flags = db.list_flags()
    pending = [f for f in flags if not f.get("confirmed")]
    confirmed = [f for f in flags if f.get("confirmed")]
    headers = ["Phone", "Flagged by", "When", "Status", ""]
    body = (
        "<h3>Pending review</h3>" + table(headers, [flag_row(f, key) for f in pending])
        + "<h3>Confirmed</h3>" + table(headers, [flag_row(f, key) for f in confirmed])
    )
    return page("Do not even try", body, key)


@app.post("/flags/confirm")
async def flags_confirm(key: str = Depends(require_key), phone: str = Form(...)):
    db.confirm_flag(phone)
    return redirect("/flags", key)


@app.post("/flags/unflag")
async def flags_unflag(key: str = Depends(require_key), phone: str = Form(...)):
    db.unflag_phone(phone)
    return redirect("/flags", key)


# ---------------------------------------------------------------------------
# attempts

ATTEMPT_HEADERS = ["Date", "Caller", "Phone", "Source", "Outcome", "Talk", "Notes"]


def attempt_row(a: dict, key: str, with_phone: bool = True) -> list[str]:
    date = a.get("ended_at") or a.get("answered_at") or a.get("claimed_at")
    row = [e(date), e(a.get("caller_name") or a.get("caller_id"))]
    if with_phone:
        row.append(phone_link(a.get("phone"), key))
    row += [
        e(pick(a, "source", "source_name")),
        e(outcome_label(a.get("outcome")) or ("open" if not a.get("ended_at") else "")),
        e(common.fmt_talk(a.get("talk_seconds"))),
        e(a.get("notes")),
    ]
    return row


@app.get("/attempts", response_class=HTMLResponse)
async def attempts_get(key: str = Depends(require_key)):
    attempts = db.recent_attempts(limit=200)
    rows = [attempt_row(a, key) for a in attempts]
    return page("Last 200 attempts", table(ATTEMPT_HEADERS, rows), key)


# ---------------------------------------------------------------------------
# lead history

@app.get("/lead/{phone}", response_class=HTMLResponse)
async def lead_get(phone: str, key: str = Depends(require_key)):
    norm = db.normalize_phone(phone) or phone
    leads = db.leads_for_phone(norm)
    attempts = db.history_for_phone(norm)

    lead_rows = []
    for ld in leads:
        data = ld.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {"data": data}
        data_html = "<pre>" + e("\n".join(
            f"{k}: {v}" for k, v in (data or {}).items() if v not in (None, "")
        )) + "</pre>"
        lead_rows.append([
            e(ld.get("id")),
            e(pick(ld, "source", "source_name")),
            e(ld.get("status")),
            e(ld.get("created_at")),
            e(ld.get("chat_id")),
            data_html,
        ])

    attempt_rows = []
    for a in attempts:
        attempt_rows.append([
            e(a.get("id")),
            e(a.get("lead_id")),
            e(a.get("caller_name") or a.get("caller_id")),
            e(a.get("claimed_at")),
            e(a.get("answered_at")),
            e(a.get("ended_at")),
            e(outcome_label(a.get("outcome"))),
            e(common.fmt_talk(a.get("talk_seconds"))),
            e(a.get("notes")),
        ])

    body = (
        f"<p>Normalised phone: <b>{e(norm)}</b></p>"
        "<h3>Leads</h3>"
        + table(["Lead", "Source", "Status", "Created", "Chat", "Data"], lead_rows)
        + "<h3>Attempts</h3>"
        + table(["Attempt", "Lead", "Caller", "Claimed", "Answered", "Ended",
                 "Outcome", "Talk", "Notes"], attempt_rows)
    )
    return page(f"Phone {norm}", body, key)


# ---------------------------------------------------------------------------

def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
