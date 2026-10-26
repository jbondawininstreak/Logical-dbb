"""Web dashboard for the Telegram CRM (v2).

One FastAPI app, server-rendered HTML strings with a little inline CSS.
Every route requires ?key=DASHBOARD_KEY (POST forms may also send it as a
form field) or gets a 403. Started on port 8000 by run.py via main().
"""

import html
import json
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote
from zoneinfo import available_timezones

import phonenumbers

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import common
import db
import gvoice

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
*, *::before, *::after { box-sizing: border-box; }

:root {
  --bg: #f2f2f7;
  --inset: #f2f2f7;
  --elev: #ffffff;
  --bar: rgba(242,242,247,0.82);
  --text: #1c1c1e;
  --muted: #6e6e73;
  --line: rgba(60,60,67,0.13);
  --tint: #007aff;
  --ok: #34c759;
  --warn: #ff9f0a;
  --bad: #ff3b30;
  --radius: 18px;
  --radius-sm: 12px;
  --shadow: 0 1px 2px rgba(0,0,0,0.05), 0 10px 28px rgba(0,0,0,0.055);
  color-scheme: light;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000000;
    --inset: #2c2c2e;
    --elev: #1c1c1e;
    --bar: rgba(0,0,0,0.7);
    --text: #f5f5f7;
    --muted: #98989d;
    --line: rgba(84,84,88,0.5);
    --tint: #0a84ff;
    --ok: #30d158;
    --warn: #ffd60a;
    --bad: #ff453a;
    --shadow: 0 1px 2px rgba(0,0,0,0.6);
    color-scheme: dark;
  }
}

body {
  margin: 0;
  padding: 0 0 56px;
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
        system-ui, Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}

/* the translucent title bar, the way a Mac window or an iOS screen heads itself */
.bar {
  position: sticky; top: 0; z-index: 5;
  background: var(--bar);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--line);
}
.bar .inner {
  display: flex; align-items: center; gap: 10px;
  height: 52px; max-width: 980px; margin: 0 auto; padding: 0 16px;
}
.bar h1 {
  flex: 1; margin: 0; text-align: center;
  font-size: 17px; font-weight: 600; letter-spacing: -0.01em;
}
.bar a.back, .bar .spacer { min-width: 72px; }
.bar a.back {
  color: var(--tint); text-decoration: none; font-weight: 450;
  display: inline-flex; align-items: center; gap: 2px;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 18px 16px 0; }

/* the home screen: rounded app icons with notification badges */
.grid {
  display: grid; gap: 20px 14px; margin: 6px 0 26px;
  grid-template-columns: repeat(auto-fill, minmax(92px, 1fr));
}
.tile { text-decoration: none; color: var(--text);
        display: flex; flex-direction: column; align-items: center; gap: 8px; }
.tile .icon {
  position: relative; width: 66px; height: 66px; border-radius: 21px;
  display: grid; place-items: center; color: #fff;
  background-image: linear-gradient(180deg, var(--c1), var(--c2));
  box-shadow: var(--shadow);
  transition: transform 0.12s ease;
}
.tile:hover .icon { transform: translateY(-2px); }
.tile:active .icon { transform: scale(0.94); }
.tile .label { font-size: 12.5px; color: var(--muted); text-align: center; }
.tile .badge {
  position: absolute; top: -5px; right: -5px;
  min-width: 21px; height: 21px; padding: 0 6px; border-radius: 11px;
  background: var(--bad); color: #fff;
  font-size: 11.5px; font-weight: 650; line-height: 21px; text-align: center;
  box-shadow: 0 0 0 2px var(--bg);
}

/* grouped content, the way Settings stacks its sections */
.card {
  background: var(--elev); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 14px 16px; margin: 0 0 16px;
}
.card > h3:first-child { margin-top: 0; }
.card h3 {
  margin: 0 0 11px; font-size: 12px; font-weight: 650;
  text-transform: uppercase; letter-spacing: 0.055em; color: var(--muted);
}
.stats { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(84px, 1fr)); }
.stat { background: var(--inset); border-radius: var(--radius-sm); padding: 10px 12px; }
.stat b { display: block; font-size: 23px; font-weight: 600; letter-spacing: -0.025em; }
.stat span { font-size: 12px; color: var(--muted); }

.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 0 -16px; padding: 0 16px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line);
         vertical-align: top; white-space: nowrap; }
th { font-size: 11.5px; font-weight: 650; color: var(--muted);
     text-transform: uppercase; letter-spacing: 0.04em; }
tr:last-child td { border-bottom: 0; }
td pre, td.free { white-space: pre-wrap; }
a { color: var(--tint); }

button, .btn {
  font: inherit; font-weight: 590; border: 0; border-radius: 980px;
  padding: 9px 17px; background: var(--tint); color: #fff; cursor: pointer;
  text-decoration: none; display: inline-block;
}
button:active, .btn:active { filter: brightness(0.92); }
button.plain { background: var(--inset); color: var(--text); }
button.danger { background: var(--bad); }
button.small { padding: 5px 12px; font-size: 13px; }

label.field { display: block; margin: 0 0 13px; }
label.field > span { display: block; font-size: 12.5px; color: var(--muted); margin: 0 0 5px; }
input[type=text], input[type=file], select {
  font: inherit; width: 100%; max-width: 430px; padding: 9px 11px;
  border: 1px solid var(--line); border-radius: var(--radius-sm);
  background: var(--elev); color: var(--text);
}
label.check { display: flex; gap: 9px; align-items: flex-start; margin: 0 0 13px; max-width: 430px; }
label.check input { margin: 3px 0 0; }
label.check span { font-size: 13.5px; color: var(--muted); }
label.check b { color: var(--text); font-weight: 550; display: block; font-size: 15px; }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.row > * { flex: 1 1 200px; }
.row > button { flex: 0 0 auto; }

.pill { display: inline-block; padding: 2px 9px; border-radius: 980px;
        font-size: 12px; font-weight: 600; white-space: nowrap; }
.pill.ok { background: rgba(52,199,89,0.16); color: var(--ok); }
.pill.bad { background: rgba(255,59,48,0.16); color: var(--bad); }
.pill.warn { background: rgba(255,159,10,0.18); color: var(--warn); }
.pill.off { background: var(--inset); color: var(--muted); }
.muted { color: var(--muted); }
form.inline { display: inline; }
.note { font-size: 13px; color: var(--muted); margin: 8px 0 16px; }
"""

# Home-screen tiles. Each glyph is an inline stroke drawing so the page stays a
# single file with nothing to fetch.
_GLYPHS = {
    "upload": "<path d='M14 20V6M8 12l6-6 6 6'/><path d='M5 20v3h18v-3'/>",
    "attempts": "<path d='M6 5h4l2 5-3 2a11 11 0 006 6l2-3 5 2v4a2 2 0 01-2 2A17 17 0 014 7a2 2 0 012-2z'/>",
    "flags": "<path d='M7 22V4h10l-2 4 2 4H7'/>",
    "chats": "<path d='M4 7h16v10H11l-5 4v-4H4z'/>",
    "sources": "<ellipse cx='14' cy='7' rx='9' ry='3.5'/><path d='M5 7v6c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5V7'/>"
               "<path d='M5 13v6c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5v-6'/>",
    "texts": "<path d='M4 6h20v12H12l-5 4v-4H4z'/><path d='M9 10h10M9 14h6'/>",
}

TILES = [
    ("/upload", "Upload", "upload", "#34c759", "#1f9c42"),
    ("/attempts", "Attempts", "attempts", "#0a84ff", "#0060df"),
    ("/flags", "Flags", "flags", "#ff3b30", "#c9271e"),
    ("/chats", "Chats", "chats", "#5e5ce6", "#3f3dc4"),
    ("/sources", "Sources", "sources", "#ff9f0a", "#d97d00"),
    ("/texts", "Texts", "texts", "#30b0c7", "#1a8aa0"),
]

PAGES = [(p, label) for p, label, _g, _a, _b in TILES]


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


def glyph(name: str) -> str:
    """One tile icon, drawn inline so the page fetches nothing."""
    return (
        "<svg viewBox='0 0 28 28' width='29' height='29' fill='none' "
        "stroke='currentColor' stroke-width='2' stroke-linecap='round' "
        f"stroke-linejoin='round'>{_GLYPHS.get(name, '')}</svg>"
    )


def tile(path: str, label: str, name: str, c1: str, c2: str, key: str, badge=0) -> str:
    dot = f"<span class='badge'>{e(badge)}</span>" if badge else ""
    return (
        f'<a class="tile" href="{href(path, key)}">'
        f"<span class='icon' style=\"--c1:{e(c1)};--c2:{e(c2)}\">{glyph(name)}{dot}</span>"
        f'<span class="label">{e(label)}</span></a>'
    )


def card(inner: str, title: str = "") -> str:
    head = f"<h3>{e(title)}</h3>" if title else ""
    return f'<section class="card">{head}{inner}</section>'


def stats_row(pairs: list[tuple[str, object]]) -> str:
    """Big-number tiles, the way a Mac widget reads out a few counts."""
    cells = "".join(
        f'<div class="stat"><b>{e(value)}</b><span>{e(label)}</span></div>'
        for label, value in pairs
    )
    return f'<div class="stats">{cells}</div>'


def pill(text: str, kind: str = "off") -> str:
    return f'<span class="pill {e(kind)}">{e(text)}</span>'


def page(title: str, body: str, key: str, home: bool = False) -> HTMLResponse:
    """The whole document. `home` drops the back link, since home is the top."""
    left = (
        '<span class="spacer"></span>' if home
        else f'<a class="back" href="{href("/", key)}">&lsaquo; Home</a>'
    )
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, "
        "viewport-fit=cover'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body>"
        f'<div class="bar"><div class="inner">{left}<h1>{e(title)}</h1>'
        '<span class="spacer"></span></div></div>'
        f'<div class="wrap">{body}</div></body></html>'
    )
    return HTMLResponse(doc)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Rows contain already-escaped HTML cells. Scrolls sideways on a phone."""
    if not rows:
        return "<p class='muted'>(none)</p>"
    out = ['<div class="scroll"><table><tr>']
    out += [f"<th>{e(h)}</th>" for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    out.append("</table></div>")
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
    chats = db.list_chats()
    # A red badge is "something is waiting for you": flags pending review, and
    # numbers that texted in and have not been written back to. Chat and source
    # counts are facts, not alerts, and belong in the cards below rather than
    # on a tile.
    badges = {
        "/flags": len([f for f in db.list_flags() if not f.get("confirmed")]),
        "/texts": db.phones_awaiting_reply(),
    }
    tiles = "".join(
        tile(path, label, name, c1, c2, key, badges.get(path, 0))
        for path, label, name, c1, c2 in TILES
    )

    queue = stats_row([
        (label, counts.get(k, 0))
        for label, k in (("New", "new"), ("Posted", "posted"), ("Claimed", "claimed"),
                         ("Closed", "closed"), ("Blocked", "blocked"))
    ])

    if chats:
        options = "".join(
            f'<option value="{e(ch["chat_id"])}">{e(ch["chat_id"])} '
            f'{e(ch.get("title") or "")}</option>'
            for ch in chats
        )
        push = (
            f'<form method="post" action="{href("/push", key)}">{hidden_key(key)}'
            f'<div class="row"><label class="field"><span>Group chat</span>'
            f'<select name="chat_id">{options}</select></label>'
            '<button type="submit">Push next lead</button></div></form>'
        )
    else:
        push = (
            "<p class='muted'>No allowed chats yet. "
            f"<a href=\"{href('/chats', key)}\">Add one</a> before pushing.</p>"
        )

    src_rows = []
    for s_ in db.stats_by_source():
        attempts = pick(s_, "attempts", default=0)
        answered = pick(s_, "answered", default=0)
        src_rows.append([
            e(pick(s_, "source", "name", "source_name")),
            e(pick(s_, "leads", default=0)),
            e(attempts),
            e(answered),
            e(pct(answered, attempts)),
            e(pick(s_, "no_response", default=0)),
            e(pick(s_, "hung_up", default=0)),
            e(pick(s_, "on_phone", default=0)),
            e(avg_talk(s_.get("avg_talk_seconds"))),
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

    body = (
        f'<div class="grid">{tiles}</div>'
        + card(queue, "Queue")
        + card(push, "Push")
        + card(src_html, "By source")
        + card(caller_html, "By caller")
    )
    return page("CRM", body, key, home=True)


# ---------------------------------------------------------------------------
# sources

@app.get("/sources", response_class=HTMLResponse)
async def sources_get(key: str = Depends(require_key)):
    rows = []
    for s_ in db.list_sources():
        country = (s_.get("country") or "").upper()
        rows.append([
            e(s_.get("name")),
            e(s_.get("leads", 0)),
            pill(country, "ok") if country else pill("not set", "off"),
            e(s_.get("timezone") or ""),
            e(s_.get("created_at")),
        ])
    body = card(
        table(["Source", "Leads", "Read as", "Time zone", "Created"], rows)
        + "<p class='note'>The country a source was read as is what its local-looking "
          "numbers were turned into E.164 with. Sources uploaded before this was "
          "recorded show no country.</p>",
        "Uploaded sources",
    )
    return page("Sources", body, key)


# ---------------------------------------------------------------------------
# upload

# A parsed upload waiting for its Commit button. Held in this process on
# purpose: a preview is worth nothing after a restart, and the dashboard runs as
# one process, so a table in Turso would be more writes for no gain.
_PENDING: dict[str, dict] = {}
_PENDING_TTL = 1800
_PENDING_MAX = 12

_COMMON_COUNTRIES = ("AU", "GB", "US", "CA", "NZ", "IE", "ZA", "AE", "SG", "IN",
                     "DE", "FR", "ES", "NL")

STATUS_PILLS = {
    "valid": "ok",
    "invalid": "bad",
    "wrong_country": "warn",
    "duplicate": "warn",
    "blocked": "bad",
    "no_phone": "off",
}

STATUS_LABELS = {
    "valid": "Ready",
    "invalid": "Not a real number",
    "wrong_country": "Another country",
    "duplicate": "Duplicate",
    "blocked": "Do not even try",
    "no_phone": "No number",
}


def _sweep_pending() -> None:
    cutoff = time.time() - _PENDING_TTL
    for token in [t for t, v in _PENDING.items() if v["at"] < cutoff]:
        _PENDING.pop(token, None)
    while len(_PENDING) > _PENDING_MAX:
        _PENDING.pop(min(_PENDING, key=lambda t: _PENDING[t]["at"]), None)


def _stash(payload: dict) -> str:
    _sweep_pending()
    token = secrets.token_urlsafe(16)
    _PENDING[token] = dict(payload, at=time.time())
    return token


def _unstash(token: str) -> Optional[dict]:
    _sweep_pending()
    return _PENDING.get(token or "")


def country_options(selected: str) -> str:
    """Every country phonenumbers knows, the ones in regular use pinned on top."""
    selected = (selected or common.DEFAULT_COUNTRY).upper()
    rest = sorted(r for r in phonenumbers.SUPPORTED_REGIONS if r not in _COMMON_COUNTRIES)
    out = []
    for group, codes in (("Common", _COMMON_COUNTRIES), ("All", rest)):
        out.append(f'<optgroup label="{e(group)}">')
        for code in codes:
            try:
                dial = phonenumbers.country_code_for_region(code)
            except Exception:
                dial = 0
            mark = " selected" if code == selected else ""
            label = f"{code}  +{dial}" if dial else code
            out.append(f'<option value="{e(code)}"{mark}>{e(label)}</option>')
        out.append("</optgroup>")
    return "".join(out)


def timezone_options(selected: str) -> str:
    """Automatic first, then every zone Python knows."""
    mark = "" if selected else " selected"
    out = [f'<option value=""{mark}>Automatic - from the country above</option>']
    for zone in sorted(available_timezones()):
        hit = " selected" if zone == selected else ""
        out.append(f'<option value="{e(zone)}"{hit}>{e(zone)}</option>')
    return "".join(out)


def upload_form(key: str, country: str = "", timezone: str = "",
                strict: bool = False, source: str = "", phone_column: str = "") -> str:
    accept = ",".join(common.UPLOAD_EXTENSIONS)
    return (
        f'<form method="post" action="{href("/upload", key)}" '
        f'enctype="multipart/form-data">{hidden_key(key)}'
        f'<label class="field"><span>File</span>'
        f'<input type="file" name="upload" accept="{e(accept)}" required></label>'
        f"<p class='note'>CSV, TSV, plain text, Excel (.xlsx, .xlsm), JSON or JSONL. "
        f"Whatever columns the file has are kept as they are; only the phone column "
        f"has to be readable.</p>"
        f'<label class="field"><span>Source name</span>'
        f'<input type="text" name="source" value="{e(source)}" '
        f'placeholder="list-oct-18" required></label>'
        f'<label class="field"><span>Phone column (optional)</span>'
        f'<input type="text" name="phone_column" value="{e(phone_column)}" '
        f'placeholder="left empty, any column named phone is used"></label>'
        f'<label class="field"><span>Read local numbers as</span>'
        f'<select name="country">{country_options(country)}</select></label>'
        f"<p class='note'>Numbers written the local way are turned into E.164 for "
        f"this country. A number that already starts with + or 00 carries its own "
        f"country code and keeps it.</p>"
        f'<label class="field"><span>Time zone for this list</span>'
        f'<select name="timezone">{timezone_options(timezone)}</select></label>'
        f'<label class="check"><input type="checkbox" name="strict" value="1"'
        f'{" checked" if strict else ""}>'
        f"<span><b>Only this country</b>Numbers that are real but belong somewhere "
        f"else are held back instead of loaded.</span></label>"
        '<p><button type="submit">Check the file</button></p></form>'
    )


@app.get("/upload", response_class=HTMLResponse)
async def upload_get(key: str = Depends(require_key)):
    return page("Upload", card(upload_form(key), "New upload"), key)


@app.post("/upload", response_class=HTMLResponse)
async def upload_post(
    request: Request,
    key: str = Depends(require_key),
    upload: UploadFile = File(...),
    source: str = Form(...),
    phone_column: Optional[str] = Form(""),
    country: Optional[str] = Form(""),
    timezone: Optional[str] = Form(""),
    strict: Optional[str] = Form(None),
):
    """Parse and check the file, then show what would be loaded. Writes nothing."""
    raw = await upload.read()
    source = (source or "").strip()
    phone_column = (phone_column or "").strip()
    country = (country or common.DEFAULT_COUNTRY).strip().upper()
    strict_on = bool(strict)
    zone = (timezone or "").strip()
    if not zone:
        zones = common.country_timezones(country)
        zone = zones[0] if zones else ""

    def back(message: str) -> HTMLResponse:
        return page(
            "Upload",
            card(f"<p><b>{e(message)}</b></p>", "That file was not loaded")
            + card(upload_form(key, country, timezone or "", strict_on, source,
                               phone_column), "Try again"),
            key,
        )

    if not source:
        return back("A source name is required.")
    try:
        rows = common.parse_upload(upload.filename or "", raw)
    except Exception as exc:
        return back(f"That file could not be read: {exc}")
    if not rows:
        return back("That file has no rows in it.")
    try:
        results = common.validate_rows(
            rows, country=country, strict=strict_on,
            phone_column=phone_column, blocked=db.blocked_phones(),
        )
    except ValueError as exc:  # no phone column, naming the ones there are
        return back(str(exc))

    token = _stash({
        "results": results, "source": source, "country": country,
        "timezone": zone, "strict": strict_on, "filename": upload.filename or "",
    })
    return page("Check the upload", preview_body(results, token, key, source,
                                                 country, zone, strict_on,
                                                 upload.filename or ""), key)


def preview_body(results: list[dict], token: str, key: str, source: str,
                 country: str, zone: str, strict_on: bool, filename: str) -> str:
    """What the file holds and what would happen, before anything is written."""
    counts = common.count_statuses(results)
    ready = counts.get("valid", 0)

    summary = stats_row(
        [(STATUS_LABELS.get(st, st), counts.get(st, 0))
         for st in common.ROW_STATUSES if counts.get(st, 0)]
        or [("Rows", len(results))]
    )
    about = (
        f"<p class='note'><b>{e(filename)}</b> &rarr; source <b>{e(source)}</b>, "
        f"read as <b>{e(country)}</b>"
        + (f", time zone <b>{e(zone)}</b>" if zone else "")
        + (", only this country" if strict_on else "")
        + f". {e(len(results))} rows in the file.</p>"
    )

    shown = results[:200]
    extra_columns: list[str] = []
    for r in shown:
        for k in r["row"].keys():
            if k not in extra_columns:
                extra_columns.append(k)
    extra_columns = extra_columns[:6]

    rows_html = []
    for r in shown:
        status = r["status"]
        cells = [
            pill(STATUS_LABELS.get(status, status), STATUS_PILLS.get(status, "off")),
            e(r["phone"] or r["raw"]),
            e(f"{r['region']} +{r['dial_code']}" if r.get("dial_code") else r["region"]),
            e((r["timezones"] or ("",))[0]),
            f"<span class='muted'>{e(r['reason'])}</span>",
        ]
        cells += [f"<span class='muted'>{e(r['row'].get(c, ''))}</span>"
                  for c in extra_columns]
        rows_html.append(cells)
    headers = ["Status", "Number", "Country", "Time zone", "Why"] + extra_columns
    listing = table(headers, rows_html)
    if len(results) > len(shown):
        listing += (f"<p class='note'>Showing the first {e(len(shown))} of "
                    f"{e(len(results))} rows.</p>")

    if ready:
        commit = (
            f'<form method="post" action="{href("/upload/commit", key)}">'
            f'{hidden_key(key)}<input type="hidden" name="token" value="{e(token)}">'
            f'<button type="submit">Load {e(ready)} '
            f'lead{"" if ready == 1 else "s"}</button> '
            f'<a class="btn plain" style="background:var(--inset);color:var(--text)" '
            f'href="{href("/upload", key)}">Start over</a></form>'
        )
    else:
        commit = (
            "<p><b>Nothing here can be loaded.</b> Every row was held back for a "
            "reason above.</p>"
            f'<p><a class="btn" href="{href("/upload", key)}">Try another file</a></p>'
        )

    return (card(summary + about, "What is in the file")
            + card(commit, "Load it")
            + card(listing, "Row by row"))


@app.post("/upload/commit", response_class=HTMLResponse)
async def upload_commit(key: str = Depends(require_key), token: str = Form(...)):
    """Write the rows the preview showed as ready, and nothing else."""
    pending = _unstash(token)
    if pending is None:
        return page(
            "Upload",
            card("<p><b>That check has expired.</b> Nothing was loaded. "
                 "Upload the file again to see it fresh.</p>", "Expired")
            + card(upload_form(key), "New upload"),
            key,
        )
    result = db.add_validated_leads(
        pending["results"], pending["source"],
        country=pending["country"], timezone=pending["timezone"],
    )
    _PENDING.pop(token, None)
    skipped = result.get("skipped") or {}
    held = "".join(
        f"<li>{e(count)} {e(STATUS_LABELS.get(st, st).lower())}</li>"
        for st, count in skipped.items() if count
    )
    body = card(
        f"<p><b>{e(result.get('added', 0))}</b> leads loaded into source "
        f"<b>{e(pending['source'])}</b>, read as {e(pending['country'])}.</p>"
        + (f"<p class='note'>Held back:</p><ul class='note'>{held}</ul>" if held else "")
        + f'<p><a class="btn" href="{href("/", key)}">Home</a> '
          f'<a class="btn" style="background:var(--inset);color:var(--text)" '
          f'href="{href("/upload", key)}">Upload another</a></p>',
        "Loaded",
    )
    return page("Upload", body, key)


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
            card(f"<p>Telegram did not accept the post: {e(str(exc))}</p>"
                 "<p class='note'>Check that the CRM bot is in that group and the "
                 "token is right.</p>", "Not posted"),
            key,
        )
    if not posted:
        return page("Nothing to push",
                    card("<p>No new leads left in the queue.</p>"), key)
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
            '<button class="plain small" type="submit">Remove</button></form>'
        )
        rows.append([e(ch["chat_id"]), e(ch.get("title") or ""), remove_form])
    add_form = (
        f'<form method="post" action="{href("/chats", key)}">{hidden_key(key)}'
        '<input type="hidden" name="action" value="add">'
        '<div class="row">'
        '<label class="field"><span>Chat id</span>'
        '<input type="text" name="chat_id" required></label>'
        '<label class="field"><span>Title</span>'
        '<input type="text" name="title"></label>'
        '<button type="submit">Add</button></div></form>'
    )
    body = (message + card(table(["Chat id", "Title", ""], rows), "Allowed chats")
            + card(add_form, "Add a chat"))
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
        '<button class="small" type="submit">Confirm</button></form>'
    )
    unflag = (
        f'<form class="inline" method="post" action="{href("/flags/unflag", key)}">'
        f'{hidden_key(key)}<input type="hidden" name="phone" value="{e(phone)}">'
        '<button class="plain small" type="submit">Unflag</button></form>'
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
        card(table(headers, [flag_row(f, key) for f in pending]), "Pending review")
        + card(table(headers, [flag_row(f, key) for f in confirmed]), "Confirmed")
    )
    return page("Flags", body, key)


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
    return page("Attempts", card(table(ATTEMPT_HEADERS, rows), "Last 200 attempts"), key)


# ---------------------------------------------------------------------------
# texts (Google Voice through the Matrix bridge)

TEXT_HEADERS = ["When", "Way", "Phone", "Lead", "Source", "Text"]


def way_pill(direction) -> str:
    """Which way a text went: in from the number, out from us, or a bridge notice."""
    direction = str(direction or "")
    if direction == "in":
        return pill("in", "ok")
    if direction == "out":
        return pill("out", "off")
    if direction == "bridge":
        return pill("bridge", "warn")
    return pill(direction, "off")


def text_row(t: dict, key: str, with_phone: bool = True) -> list[str]:
    row = [e(t.get("sent_at")), way_pill(t.get("direction"))]
    if with_phone:
        row.append(phone_link(t.get("phone"), key))
    row += [
        e(t.get("lead_id") or ""),
        e(t.get("source") or ""),
        e(t.get("body")),
    ]
    return row


def texts_status() -> str:
    """Whether texts can arrive at all, and what the intake last saw."""
    if not gvoice.configured():
        return (
            "<p class='note'>Google Voice is not set up. On the server, run "
            "deploy/gvoice/setup-gvoice.sh to add the Matrix homeserver and the "
            "bridge; it fills in MATRIX_HOMESERVER, MATRIX_USER and "
            "MATRIX_PASSWORD in .env.</p>"
        )
    lines = [
        f"<p>Homeserver: <b>{e(common.MATRIX_HOMESERVER)}</b></p>",
        f"<p>Last sync: <b>{e(db.setting_get(gvoice.KEY_LAST_SYNC) or 'never')}</b></p>",
    ]
    last_error = db.setting_get(gvoice.KEY_LAST_ERROR)
    if last_error:
        lines.append(f"<p>Last error: {e(last_error)}</p>")
    last_notice = db.setting_get(gvoice.KEY_LAST_NOTICE)
    if last_notice:
        lines.append(f"<p>The bridge last said: {e(last_notice)}</p>")
    return "".join(lines)


@app.get("/texts", response_class=HTMLResponse)
async def texts_get(key: str = Depends(require_key)):
    rows = [text_row(t, key) for t in db.recent_texts(limit=200)]
    body = (
        card(texts_status(), "Google Voice")
        + card(table(TEXT_HEADERS, rows), "Last 200 texts")
    )
    return page("Texts", body, key)


@app.post("/texts/send")
async def texts_send(
    key: str = Depends(require_key),
    phone: str = Form(...),
    body: str = Form(...),
):
    norm = db.normalize_phone(phone) or phone
    lead_path = "/lead/" + quote(norm, safe="")
    try:
        gvoice.send_text(norm, body)
    except (gvoice.MatrixError, ValueError) as exc:
        return page(
            "Not sent",
            card(
                f"<p>{e(exc)}</p>"
                f"<p><a href=\"{href(lead_path, key)}\">Back to the lead</a></p>"
            ),
            key,
        )
    return redirect(lead_path, key)


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

    # The text thread reads oldest first, the way a conversation does, and the
    # reply box only appears once the bridge has a room for this number.
    text_rows = [
        [e(t.get("sent_at")), way_pill(t.get("direction")), e(t.get("body"))]
        for t in db.texts_for_phone(norm)
    ]
    if not gvoice.configured():
        reply = "<p class='note'>Google Voice is not set up.</p>"
    elif db.room_for_phone(norm):
        reply = (
            f'<form method="post" action="{href("/texts/send", key)}">{hidden_key(key)}'
            f'<input type="hidden" name="phone" value="{e(norm)}">'
            '<div class="row"><label class="field"><span>Reply by text</span>'
            '<input name="body" required maxlength="1600" placeholder="Type a text">'
            '</label><button type="submit">Send</button></div></form>'
        )
    else:
        reply = (
            "<p class='note'>No conversation with this number yet. One appears "
            "when they text the Google Voice number, or when you start a chat "
            "with them from Element.</p>"
        )

    body = (
        card(f"<p>Lead key: <b>{e(norm)}</b></p>"
             "<p class='note'>Leads are keyed on digits only, so every shape the "
             "same number arrived in matches here.</p>", "This number")
        + card(table(["Lead", "Source", "Status", "Created", "Chat", "Data"],
                     lead_rows), "Leads")
        + card(table(["Attempt", "Lead", "Caller", "Claimed", "Answered", "Ended",
                      "Outcome", "Talk", "Notes"], attempt_rows), "Attempts")
        + card(table(["When", "Way", "Text"], text_rows) + reply, "Texts")
    )
    return page(f"Phone {norm}", body, key)


# ---------------------------------------------------------------------------

def main() -> None:
    # Defaults to localhost only. On a server, Caddy (deploy/Caddyfile) proxies to it.
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
