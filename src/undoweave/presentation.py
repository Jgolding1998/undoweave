"""A standalone, read-only HTML view of journal summary metadata.

This module does not import the engine, read files, or render source or undo
previews. Callers supply ``Engine.history()`` and optionally ``Engine.status()``.
Paths, actor labels and change labels are retained, so review before sharing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html import escape
import math
import unicodedata


_CSS = """
:root { color-scheme: light; --paper:#f7f4ef; --panel:#fffdfa; --ink:#282722;
  --muted:#66625a; --line:#ddd7cd; --accent:#a94418; --tint:#f9e8dc;
  font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  color:var(--ink); background:var(--paper); font-synthesis:none; }
* { box-sizing:border-box; } body { margin:0; font-size:15px; line-height:1.55; }
a { color:inherit; } a:focus-visible,summary:focus-visible { outline:3px solid var(--accent); outline-offset:5px; }
.skip { position:absolute; top:8px; left:12px; padding:10px; background:var(--panel); transform:translateY(-160%); z-index:5; }
.skip:focus { transform:none; } .shell { display:grid; grid-template-columns:228px minmax(0,1fr); min-height:100vh; }
.sidebar { padding:34px 22px; border-right:1px solid var(--line); display:flex; flex-direction:column; gap:30px; }
.brand { font-size:22px; font-weight:750; letter-spacing:-1px; display:flex; align-items:center; gap:10px; }
.brand-mark { width:30px; height:30px; color:var(--accent); flex:none; }
.eyebrow { color:var(--accent); text-transform:uppercase; font-size:11px; font-weight:750; letter-spacing:.12em; }
.nav { display:grid; gap:7px; } .nav a { padding:11px 13px; border-radius:9px; text-decoration:none; }
.nav .current { background:var(--tint); font-weight:650; } .nav a:hover { background:#eee8de; }
.side-note { font-size:12px; color:var(--muted); margin-top:auto; border-top:1px solid var(--line); padding-top:20px; }
main { width:100%; max-width:1120px; padding:44px 48px 60px; min-width:0; }
.topline { display:flex; justify-content:space-between; gap:20px; align-items:center; margin-bottom:35px; }
.pill { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:4px 11px; font-size:12px; font-weight:650; background:var(--panel); }
h1 { font-size:clamp(30px,4vw,43px); line-height:1.15; letter-spacing:-1.4px; margin:10px 0 14px; overflow-wrap:anywhere; }
h2 { font-size:19px; line-height:1.3; letter-spacing:-.4px; margin:0; }
h3 { font-size:16px; line-height:1.4; margin:8px 0 9px; overflow-wrap:anywhere; }
p { margin:0 0 12px; } .intro { color:var(--muted); max-width:650px; }
.stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:28px 0 30px; }
.stat { padding:19px 20px; background:var(--panel); border:1px solid var(--line); border-radius:12px; }
.stat dt { font-size:12px; color:var(--muted); } .stat dd { font-size:29px; font-weight:700; margin:5px 0 0; line-height:1.2; }
.notice { border-left:3px solid var(--accent); padding:14px 17px; background:var(--tint); border-radius:0 9px 9px 0; margin-bottom:28px; }
.notice p:last-child { margin-bottom:0; } .section-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; margin:0 0 20px; }
.section-heading p { font-size:12px; color:var(--muted); margin:0; }
.timeline { list-style:none; padding:0; margin:0; } .timeline > li { position:relative; padding:0 0 18px 29px; border-left:1px solid var(--line); margin-left:5px; }
.timeline > li:before { content:""; width:11px; height:11px; background:var(--accent); border:3px solid var(--paper); border-radius:50%; position:absolute; left:-6px; top:21px; }
.timeline > li:last-child { border-left-color:transparent; padding-bottom:0; }
.change { border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:18px 21px; min-width:0; }
.change-head { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }
.actor { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
.badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:6px; background:#eeeae3; color:#514c44; }
.badge.pending { background:#fff0cb; color:#79510b; } .badge.applied { background:#e7eee6; color:#38583d; }
.path { display:block; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; overflow-wrap:anywhere; }
.date { display:block; font-size:11px; color:var(--muted); margin-top:11px; }
details { margin-top:14px; border-top:1px solid var(--line); padding-top:10px; }
summary { cursor:pointer; font-size:12px; color:var(--muted); width:fit-content; }
.metadata { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:6px 14px; font-size:12px; margin:12px 0 0; }
.metadata dt { color:var(--muted); } .metadata dd { margin:0; overflow-wrap:anywhere; }
.empty { border:1px dashed var(--line); border-radius:12px; padding:35px 25px; text-align:center; background:var(--panel); }
.empty p { color:var(--muted); margin:8px 0 0; } .about { border-top:1px solid var(--line); padding-top:24px; margin-top:32px; color:var(--muted); font-size:12px; }
.about h2 { color:var(--ink); font-size:15px; margin-bottom:10px; } .about p:last-child { margin-bottom:0; }
@media(max-width:850px) { .shell { grid-template-columns:180px minmax(0,1fr); } .sidebar { padding:30px 14px; } main { padding:32px 26px 45px; } .brand { font-size:19px; } }
@media(max-width:620px) { .shell { display:block; } .sidebar { border-right:0; border-bottom:1px solid var(--line); padding:18px 20px; gap:16px; } .nav { display:flex; flex-wrap:wrap; gap:5px; } .nav a { padding:7px 11px; font-size:13px; } .side-note { display:none; } main { padding:25px 19px 38px; } .topline { margin-bottom:22px; } .stats { gap:7px; } .stat { padding:13px 10px; } .stat dt { font-size:11px; } .stat dd { font-size:25px; } .change { padding:16px; } .timeline > li { padding-left:20px; } .section-heading { align-items:flex-start; } .metadata { grid-template-columns:1fr; gap:2px; } .metadata dd { margin-bottom:8px; } }
@media print { .shell { display:block; } .sidebar,.skip { display:none; } main { padding:0; max-width:none; } .change { break-inside:avoid; } .about { break-inside:avoid; } }
"""


def _text(value: object, fallback: str = "", limit: int = 1024) -> str:
    """Only render scalar text, with visible control/bidi escapes and a bound."""
    if not isinstance(value, str) or not value:
        return fallback
    value = value[:limit] + ("…" if len(value) > limit else "")
    return "".join(
        ("\\u" + format(ord(char), "04x") if ord(char) <= 0xffff
         else "\\U" + format(ord(char), "08x"))
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        else char for char in value
    )


def _html(value: object, fallback: str = "", limit: int = 1024) -> str:
    return escape(_text(value, fallback, limit), quote=True)


def _time(value: object) -> str:
    if type(value) not in (int, float):
        return '<span class="date">Time unavailable</span>'
    try:
        if not math.isfinite(value):
            raise ValueError
        instant = datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return '<span class="date">Time unavailable</span>'
    machine = instant.isoformat(timespec="seconds").replace("+00:00", "Z")
    readable = instant.strftime("%d %b %Y · %H:%M:%S UTC")
    return f'<time class="date" datetime="{machine}">{readable}</time>'


def _count(status: Mapping, key: str) -> int | None:
    value = status.get(key)
    return value if type(value) is int and value >= 0 else None


def _entry(row: Mapping) -> str:
    raw_status = _text(row.get("status"), "unknown", 80)
    if raw_status == "applied" and row.get("undone_by"):
        label, style = "Undo recorded", ""
    else:
        label = {"applied": "Applied", "pending": "Pending", "aborted": "Not applied"}.get(raw_status, "Status unknown")
        style = raw_status if raw_status in {"applied", "pending"} else ""
    relation = ""
    for key, heading in (("undoes", "Undo of"), ("undone_by", "Undone by")):
        if isinstance(row.get(key), str) and row[key]:
            relation += f"<dt>{heading}</dt><dd>{_html(row[key], limit=128)}</dd>"
    return f"""<li><article class="change">
<div class="change-head"><span class="actor">{_html(row.get('actor'), 'Unlabeled actor', 80)}</span><span class="badge {style}">{label}</span></div>
<h3>{_html(row.get('label'), 'Recorded change', 240)}</h3>
<code class="path">{_html(row.get('path'), 'Path unavailable')}</code>
{_time(row.get('created'))}
<details><summary>Change metadata</summary><dl class="metadata">
<dt>Change ID</dt><dd>{_html(row.get('id'), 'Unavailable', 128)}</dd>
<dt>Journal status</dt><dd>{escape(raw_status, quote=True)}</dd>{relation}
</dl></details></article></li>"""


def render_timeline(
    history: Sequence[Mapping],
    status: Mapping | None = None,
    *,
    title: str = "Change timeline",
) -> str:
    """Return a complete HTML snapshot, preserving supplied history order.

    Accepts up to 200 ``Engine.history()`` summary rows, newest first. Only named metadata
    fields are rendered; source text, diffs, hashes, tokens, workspace paths and
    arbitrary extra keys are excluded. This is not an undo preview or a promise
    that a recorded change can currently be undone. No file access occurs.
    """
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise ValueError("Timeline history must be a sequence of summary rows.")
    if len(history) > 200 or any(not isinstance(row, Mapping) for row in history):
        raise ValueError("Timeline accepts at most 200 summary rows.")
    if status is not None and not isinstance(status, Mapping):
        raise ValueError("Timeline status must be a summary mapping.")
    status = status if status is not None else {}
    title_html = _html(title, "Change timeline", 120)
    recorded = _count(status, "recorded_count")
    pending = _count(status, "pending_count")
    recorded_text = str(recorded) if recorded is not None else "—"
    pending_text = str(pending) if pending is not None else "—"
    if recorded is None:
        scope = "Journal totals unavailable. This view contains only the supplied history."
    else:
        scope = "Journal totals are a separate status snapshot; shown history may be limited."
    notice = ""
    if pending:
        notice = '<aside class="notice" aria-label="Pending operations"><p><strong>Pending operations need review.</strong></p><p>The journal reported pending work when this snapshot was taken. Use UndoWeave status to review recovery information before making another change.</p></aside>'
    entries = "\n".join(_entry(row) for row in history)
    timeline = f'<ol class="timeline">{entries}</ol>' if history else '<div class="empty"><h3>No changes in this snapshot</h3><p>Only edits made through UndoWeave appear in its journal. This view does not scan your files.</p></div>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>{title_html} · UndoWeave</title><style>{_CSS}</style></head>
<body><a class="skip" href="#timeline">Skip to change history</a><div class="shell">
<aside class="sidebar" aria-label="Report navigation"><div class="brand"><svg class="brand-mark" aria-hidden="true" viewBox="0 0 32 32" fill="none"><path d="M12 8H7v5M7 8l7 7a7 7 0 1 0 0 10M20 24h5v-5M25 24l-7-7a7 7 0 1 0 0-10" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>UndoWeave</div>
<nav class="nav" aria-label="Sections"><a class="current" href="#timeline" aria-current="location">Change history</a><a href="#summary">Snapshot summary</a><a href="#about">About this report</a></nav>
<p class="side-note">A clear record of changes made through UndoWeave.<br><br>Actor labels are self-reported.</p></aside>
<main><div class="topline"><span class="eyebrow">Journal snapshot</span><span class="pill">Read only</span></div>
<header><h1>{title_html}</h1><p class="intro">See who labeled each recorded change, what it touched, and when it happened. Review the history without changing your files.</p></header>
<section id="summary" aria-label="Snapshot summary"><dl class="stats"><div class="stat"><dt>Applied in journal</dt><dd>{recorded_text}</dd></div><div class="stat"><dt>Shown in this snapshot</dt><dd>{len(history)}</dd></div><div class="stat"><dt>Pending operations</dt><dd>{pending_text}</dd></div></dl></section>
{notice}<section id="timeline" aria-labelledby="history-heading" tabindex="-1"><div class="section-heading"><h2 id="history-heading">Change history</h2><p>Newest first</p></div>{timeline}</section>
<footer class="about" id="about"><h2>About this snapshot</h2><p>{scope}</p><p>Only changes made through UndoWeave are attributed. Actor labels are self-reported. A journal status does not establish whether undo will succeed now; the normal undo flow still requires a fresh preview.</p><p>No source content, diffs, hashes or preview tokens are included. File paths and labels can still contain private information. Review this report before sharing it.</p></footer>
</main></div></body></html>"""
