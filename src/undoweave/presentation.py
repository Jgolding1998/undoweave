"""A standalone, read-only HTML view of journal summary metadata.

This module does not import the engine, read files, or render source or undo
previews. Callers supply ``Engine.history()`` and optionally ``Engine.status()``.
Paths, actor labels and change labels are retained, so review before sharing.

The view is a vertical timeline: a UTC time column, one continuous spine and
an event column, grouped by UTC day. Rows keep the supplied journal order;
timestamps are never used to re-sort, and a timestamp that disagrees with that
order is flagged rather than hidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html import escape
import math
import re
import unicodedata


_CSS = """
:root { color-scheme:light; --paper:#f7f4ef; --panel:#fffdfa; --ink:#282722; --muted:#625e56;
  --line:#ddd7cd; --spine:#cfc6b8; --accent:#a94418; --tint:#f9e8dc; --undo:#24655c; --undo-tint:#e3efec;
  --amber:#8a5a0b; --amber-tint:#fdf0d2; --grey:#7a756c; --time:96px; --rail:40px;
  font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  color:var(--ink); background:var(--paper); font-synthesis:none; }
* { box-sizing:border-box; } body { margin:0; font-size:15px; line-height:1.55; }
a { color:inherit; } a:focus-visible,summary:focus-visible,[tabindex]:focus-visible { outline:3px solid var(--accent); outline-offset:4px; border-radius:4px; }
code { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
.skip { position:absolute; top:8px; left:12px; padding:10px 14px; background:var(--panel); border:1px solid var(--accent); border-radius:8px; transform:translateY(-170%); z-index:5; }
.skip:focus { transform:none; }
.bar { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 32px; border-bottom:1px solid var(--line); background:var(--panel); }
.brand { font-size:20px; font-weight:750; letter-spacing:-.8px; display:flex; align-items:center; gap:9px; }
.brand-mark { width:28px; height:28px; color:var(--accent); flex:none; }
.bar-meta { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.pill { display:inline-block; border:1px solid var(--line); border-radius:999px; padding:4px 11px; font-size:12px; font-weight:650; background:var(--paper); white-space:nowrap; }
main { max-width:980px; margin:0 auto; padding:40px 32px 64px; }
.eyebrow { color:var(--accent); text-transform:uppercase; font-size:11px; font-weight:750; letter-spacing:.12em; margin:0; }
h1 { font-size:clamp(28px,4vw,40px); line-height:1.15; letter-spacing:-1.2px; margin:8px 0 12px; overflow-wrap:anywhere; }
h2 { font-size:19px; line-height:1.3; letter-spacing:-.4px; margin:0; }
p { margin:0 0 12px; } .intro { color:var(--muted); max-width:640px; }
.stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; margin:26px 0 18px; background:var(--line); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.stat { padding:15px 18px; background:var(--panel); }
.stat dt { font-size:12px; color:var(--muted); } .stat dd { font-size:26px; font-weight:700; margin:2px 0 0; line-height:1.2; font-variant-numeric:tabular-nums; }
.legend { display:flex; flex-wrap:wrap; gap:8px 18px; list-style:none; padding:0; margin:0 0 26px; font-size:12px; color:var(--muted); }
.legend li { display:flex; align-items:center; gap:7px; }
.notice { border-left:3px solid var(--accent); padding:13px 16px; background:var(--tint); border-radius:0 9px 9px 0; margin:0 0 18px; }
.notice.clock { border-left-color:var(--amber); background:var(--amber-tint); }
.notice p:last-child { margin-bottom:0; }
.section-heading { display:flex; align-items:baseline; justify-content:space-between; gap:16px; margin:8px 0 18px; }
.section-heading p { font-size:12px; color:var(--muted); margin:0; }
/* Timeline: one continuous spine through every day group. */
.timeline { position:relative; }
.timeline::before { content:""; position:absolute; top:6px; bottom:22px; left:calc(var(--time) + var(--rail) / 2 - 1px); width:2px; background:var(--spine); border-radius:2px; }
.day { position:relative; margin:0 0 6px; }
.day-head { position:relative; display:grid; grid-template-columns:var(--time) var(--rail) minmax(0,1fr); align-items:center; margin:0 0 12px; font-size:13px; font-weight:700; letter-spacing:.01em; }
.day-head::before { content:""; grid-column:2; justify-self:center; width:12px; height:12px; border-radius:3px; background:var(--ink); transform:rotate(45deg); box-shadow:0 0 0 4px var(--paper); }
.day-head span { grid-column:3; display:flex; flex-wrap:wrap; gap:4px 10px; align-items:baseline; }
.day-head small { color:var(--muted); font-weight:500; font-size:12px; }
.events { list-style:none; padding:0; margin:0; }
.event { display:grid; grid-template-columns:var(--time) var(--rail) minmax(0,1fr); padding-bottom:14px; scroll-margin-top:16px; }
.when { text-align:right; padding:15px 2px 0 0; font-variant-numeric:tabular-nums; }
.clock { display:block; font-size:15px; font-weight:700; letter-spacing:-.2px; }
.clock .sec { color:var(--muted); font-weight:500; }
.vh { position:absolute; width:1px; height:1px; margin:-1px; padding:0; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
.clock-none { display:block; font-size:12px; color:var(--muted); line-height:1.3; }
.node { grid-column:2; justify-self:center; margin-top:18px; width:16px; height:16px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px var(--paper); display:grid; place-items:center; color:#fff; font-size:11px; font-weight:800; line-height:1; }
.node.undo { width:22px; height:22px; margin-top:15px; background:var(--undo); font-size:14px; }
.node.reversed { background:var(--panel); border:3px solid var(--accent); }
.node.pending { background:var(--amber-tint); border:2px dashed var(--amber); }
.node.aborted,.node.unknown { background:var(--panel); border:2px solid var(--grey); color:var(--grey); }
.legend .node,.legend .node.undo { margin:0; box-shadow:none; }
.card { grid-column:3; min-width:0; border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:14px 18px 12px; }
.event.is-undo .card { border-left:4px solid var(--undo); }
.event.is-reversed .card { background:#fbf8f3; }
.event.is-pending .card { border-color:#e3c98f; }
.event.is-aborted .card,.event.is-unknown .card { border-style:dashed; background:transparent; }
.event:target .card { box-shadow:0 0 0 3px var(--tint),0 0 0 4px var(--accent); }
.card-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px 12px; flex-wrap:wrap; }
h4.title { font-size:16px; line-height:1.4; margin:0; overflow-wrap:anywhere; min-width:0; flex:1 1 220px; }
.badges { display:flex; gap:6px; flex-wrap:wrap; }
.badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:6px; background:#eeeae3; color:#4e493f; white-space:nowrap; }
.badge.kind-undo { background:var(--undo-tint); color:var(--undo); }
.badge.pending { background:var(--amber-tint); color:#6e4806; } .badge.applied { background:#e7eee6; color:#335338; }
.badge.reversed { background:var(--tint); color:#7e3412; }
.facts { display:flex; flex-wrap:wrap; gap:4px 14px; margin:6px 0 0; font-size:13px; color:var(--muted); }
.path { font-size:12.5px; color:var(--ink); overflow-wrap:anywhere; background:#f2ede5; padding:1px 6px; border-radius:5px; }
.actor { overflow-wrap:anywhere; }
.relation { display:flex; gap:8px; align-items:baseline; margin:10px 0 0; padding:8px 11px; border-radius:8px; font-size:13px; background:var(--undo-tint); color:#1f4f48; overflow-wrap:anywhere; }
.relation.back { background:var(--tint); color:#6e2f12; }
.relation a { font-weight:650; text-underline-offset:3px; }
.flag { margin:10px 0 0; padding:7px 11px; border-radius:8px; font-size:12.5px; background:var(--amber-tint); color:#6e4806; }
details { margin-top:10px; }
summary { cursor:pointer; font-size:12px; color:var(--muted); width:fit-content; padding:4px 0; }
.metadata { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:5px 14px; font-size:12px; margin:8px 0 2px; padding:10px 12px; background:var(--paper); border-radius:8px; }
.metadata dt { color:var(--muted); } .metadata dd { margin:0; overflow-wrap:anywhere; font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
.end { display:grid; grid-template-columns:var(--time) var(--rail) minmax(0,1fr); align-items:center; font-size:12px; color:var(--muted); }
.end::before { content:""; grid-column:2; justify-self:center; width:10px; height:2px; background:var(--spine); }
.end span { grid-column:3; }
.empty { border:1px dashed var(--line); border-radius:12px; padding:35px 25px; text-align:center; background:var(--panel); }
.empty h3 { margin:0; } .empty p { color:var(--muted); margin:8px 0 0; }
.about { border-top:1px solid var(--line); padding-top:22px; margin-top:34px; color:var(--muted); font-size:13px; }
.about h2 { color:var(--ink); font-size:15px; margin-bottom:10px; } .about p:last-child { margin-bottom:0; }
@media(max-width:620px) {
  :root { --time:52px; --rail:26px; }
  .bar { padding:12px 18px; } .bar-meta .pill:first-child { display:none; } .brand { font-size:18px; } main { padding:24px 16px 40px; }
  .stats { grid-template-columns:repeat(3,minmax(0,1fr)); } .stat { padding:11px 10px; } .stat dt { font-size:11px; line-height:1.3; } .stat dd { font-size:22px; }
  .clock { font-size:13px; } .clock .sec { position:absolute; width:1px; height:1px; margin:-1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
  .when { padding-top:13px; } .node { width:14px; height:14px; margin-top:17px; } .node.undo { width:20px; height:20px; margin-top:14px; font-size:12px; }
  .card { padding:12px 13px 10px; } h4.title { font-size:15px; flex-basis:100%; }
  .metadata { grid-template-columns:1fr; gap:1px; } .metadata dd { margin-bottom:7px; }
  .section-heading { flex-direction:column; gap:4px; }
}
@media print { .skip { display:none; } .bar { border:0; } main { padding:0; max-width:none; } .event,.notice,.about { break-inside:avoid; } }
"""

_HEX_ID = re.compile(r"[a-f0-9]{32}")
_STATUS = {"applied": ("Applied", "applied"), "pending": ("Pending", "pending"), "aborted": ("Not applied", "")}


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


def _instant(value: object) -> datetime | None:
    """A UTC instant for a finite numeric timestamp, else None (never guessed)."""
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            raise ValueError
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _anchor(value: object) -> str | None:
    """Only canonical change IDs become in-page anchors."""
    return value if isinstance(value, str) and _HEX_ID.fullmatch(value) else None


def _count(status: Mapping, key: str) -> int | None:
    value = status.get(key)
    return value if type(value) is int and value >= 0 else None


def _clock(instant: datetime | None) -> str:
    if instant is None:
        return '<span class="clock-none">Time unavailable</span>'
    machine = instant.isoformat(timespec="seconds").replace("+00:00", "Z")
    full = instant.strftime("%d %b %Y %H:%M:%S UTC")
    # Column shows HH:MM; seconds and the UTC zone stay in the accessible text,
    # the title tooltip and the exact machine-readable datetime attribute.
    return (f'<time class="clock" datetime="{machine}" title="{full}">{instant:%H:%M}'
            f'<span class="sec">:{instant:%S}</span><span class="vh"> UTC</span></time>')


def _short_time(instant: datetime | None) -> str:
    return f"{instant:%d %b %H:%M} UTC" if instant else "an unknown time"


def _relation(kind: str, target: object, index: Mapping[str, tuple[str, datetime | None]]) -> str:
    """Describe an undo link; only in-snapshot canonical IDs become links."""
    if not isinstance(target, str) or not target:
        return ""
    if kind == "undoes":
        lead, css, glyph = "Reverses", "relation", "↶"
    else:
        lead, css, glyph = "Reversed by", "relation back", "↷"
    anchor = _anchor(target)
    if anchor and anchor in index:
        label, instant = index[anchor]
        what = "the undo" if kind == "undone_by" else f"“{label}”"
        body = f'<a href="#change-{anchor}">{what} at {_short_time(instant)}</a>'
    else:
        body = f"change <code>{_html(target, limit=12)}</code> (outside this snapshot)"
    return f'<p class="{css}"><span aria-hidden="true">{glyph}</span><span>{lead} {body}</span></p>'


def _entry(row: Mapping, instant: datetime | None, out_of_order: bool,
           index: Mapping[str, tuple[str, datetime | None]], used: set[str]) -> str:
    raw_status = _text(row.get("status"), "unknown", 80)
    is_undo = isinstance(row.get("undoes"), str) and bool(row.get("undoes"))
    reversed_later = raw_status == "applied" and bool(row.get("undone_by"))
    if reversed_later:
        label, style = "Undo recorded", "reversed"
    else:
        label, style = _STATUS.get(raw_status, ("Status unknown", ""))
    if raw_status == "pending":
        kind, node = "pending", '<span class="node pending" aria-hidden="true"></span>'
    elif raw_status not in ("applied",):
        kind = "aborted" if raw_status == "aborted" else "unknown"
        node = f'<span class="node {kind}" aria-hidden="true">{"×" if kind == "aborted" else "?"}</span>'
    elif is_undo:
        kind, node = "undo", '<span class="node undo" aria-hidden="true">↶</span>'
    elif reversed_later:
        kind, node = "reversed", '<span class="node reversed" aria-hidden="true"></span>'
    else:
        kind, node = "edit", '<span class="node" aria-hidden="true"></span>'
    classes = f"event is-{kind}" + (" is-undo" if is_undo and kind != "undo" else "")
    anchor = _anchor(row.get("id"))
    # Element IDs stay unique even if a caller repeats a change ID.
    id_attr = f' id="change-{anchor}"' if anchor and anchor not in used else ""
    if anchor:
        used.add(anchor)
    kind_badge = '<span class="badge kind-undo">Undo</span>' if is_undo else ""
    relation = _relation("undoes", row.get("undoes"), index) + _relation("undone_by", row.get("undone_by"), index)
    flag = ('<p class="flag">Clock earlier than the entry recorded before it. '
            'Journal order is shown; timestamps may come from different clocks.</p>') if out_of_order else ""
    meta = ""
    for key, heading in (("undoes", "Undo of"), ("undone_by", "Undone by")):
        if isinstance(row.get(key), str) and row[key]:
            meta += f"<dt>{heading}</dt><dd>{_html(row[key], limit=128)}</dd>"
    title = _html(row.get("label"), "Recorded change", 240)
    return f"""<li class="{classes}"{id_attr}><div class="when">{_clock(instant)}</div>{node}
<article class="card" aria-label="{title}"><div class="card-head"><h4 class="title">{title}</h4><span class="badges">{kind_badge}<span class="badge {style}">{label}</span></span></div>
<p class="facts"><code class="path">{_html(row.get('path'), 'Path unavailable')}</code><span class="actor">by {_html(row.get('actor'), 'Unlabeled actor', 80)} <small>(self-reported)</small></span></p>
{relation}{flag}<details><summary>Change metadata</summary><dl class="metadata">
<dt>Change ID</dt><dd>{_html(row.get('id'), 'Unavailable', 128)}</dd>
<dt>Journal status</dt><dd>{escape(raw_status, quote=True)}</dd>{meta}
</dl></details></article></li>"""


def _timeline(history: Sequence[Mapping]) -> tuple[str, int]:
    """Day-grouped events in supplied order, plus the count of clock disagreements."""
    instants = [_instant(row.get("created")) for row in history]
    index = {}
    for row, instant in zip(history, instants):
        anchor = _anchor(row.get("id"))
        if anchor and anchor not in index:
            index[anchor] = (_html(row.get("label"), "Recorded change", 80), instant)
    groups: list[tuple[object, list[str]]] = []
    disagreements = 0
    used: set[str] = set()
    for position, (row, instant) in enumerate(zip(history, instants)):
        older = instants[position + 1] if position + 1 < len(instants) else None
        # Newest first: a newer journal entry should not carry an earlier time.
        out_of_order = instant is not None and older is not None and instant < older
        disagreements += out_of_order
        key = instant.date() if instant else None
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append(_entry(row, instant, out_of_order, index, used))
    days = []
    for key, entries in groups:
        count = f'{len(entries)} change{"s" if len(entries) != 1 else ""}'
        if key is None:
            head = f"<span>Time unavailable <small>{count} · no valid timestamp</small></span>"
        else:
            head = (f'<span><time datetime="{key.isoformat()}">{key:%A %d %B %Y}</time>'
                    f" <small>UTC day · {count}</small></span>")
        days.append(f'<section class="day"><h3 class="day-head">{head}</h3>'
                    f'<ol class="events">{"".join(entries)}</ol></section>')
    end = '<p class="end"><span>End of this snapshot. Older history may exist beyond its limit.</span></p>'
    return f'<div class="timeline">{"".join(days)}{end}</div>', disagreements


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
    if history:
        timeline, disagreements = _timeline(history)
        if disagreements:
            notice += (f'<aside class="notice clock" aria-label="Clock order"><p><strong>{disagreements} '
                       f'timestamp{"s" if disagreements != 1 else ""} disagree{"" if disagreements != 1 else "s"} with journal order.</strong></p>'
                       '<p>Entries stay in the order UndoWeave recorded them. Wall-clock times can differ between processes or after a clock change.</p></aside>')
    else:
        timeline = '<div class="empty"><h3>No changes in this snapshot</h3><p>Only edits made through UndoWeave appear in its journal. This view does not scan your files.</p></div>'
    legend = ('<ul class="legend" aria-label="Marker key">'
              '<li><span class="node" aria-hidden="true"></span>Edit</li>'
              '<li><span class="node undo" aria-hidden="true">↶</span>Undo (recorded as a new edit)</li>'
              '<li><span class="node reversed" aria-hidden="true"></span>Edit with a recorded undo</li>'
              '<li><span class="node pending" aria-hidden="true"></span>Pending</li>'
              '<li><span class="node aborted" aria-hidden="true">×</span>Not applied</li></ul>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>{title_html} · UndoWeave</title><style>{_CSS}</style></head>
<body><a class="skip" href="#timeline">Skip to change history</a>
<header class="bar"><div class="brand"><svg class="brand-mark" aria-hidden="true" viewBox="0 0 32 32" fill="none"><path d="M12 8H7v5M7 8l7 7a7 7 0 1 0 0 10M20 24h5v-5M25 24l-7-7a7 7 0 1 0 0-10" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>UndoWeave</div>
<div class="bar-meta"><span class="pill">Journal snapshot</span><span class="pill">Read only</span></div></header>
<main><p class="eyebrow">Change timeline</p>
<h1>{title_html}</h1><p class="intro">Recorded changes in this snapshot, newest first in journal order. Each row shows when UndoWeave recorded it, what it touched, who labeled it, and how undos connect to the edits they reverse. Older history may exist beyond the snapshot limit.</p>
<section id="summary" aria-label="Snapshot summary"><dl class="stats"><div class="stat"><dt>Applied in journal</dt><dd>{recorded_text}</dd></div><div class="stat"><dt>Shown in this snapshot</dt><dd>{len(history)}</dd></div><div class="stat"><dt>Pending operations</dt><dd>{pending_text}</dd></div></dl>{legend}</section>
{notice}<section id="timeline" aria-labelledby="history-heading" tabindex="-1"><div class="section-heading"><h2 id="history-heading">Change history</h2><p>Newest first · journal order · <strong>all times UTC</strong></p></div>{timeline}</section>
<footer class="about" id="about"><h2>About this snapshot</h2><p>{scope}</p><p>Only changes made through UndoWeave are attributed. Actor labels are self-reported. “Undo recorded” means the journal links an undo to that edit; a journal status does not establish whether undo will succeed now. The normal undo flow still requires a fresh preview and your approval.</p><p>No source content, diffs, hashes or preview tokens are included. File paths and labels can still contain private information. Review this report before sharing it.</p></footer>
</main></body></html>"""
