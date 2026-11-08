"""Draw a blog entry's social card: 1200 x 630 layout px, a 2400 x 1260 file.

The same Nocturne ground the public site wears, laid out in three columns like
the trade-idea card: the MIDDLE column (``card_draw.GRID_STRIP``, the part
Instagram's profile grid keeps) holds everything that must read on its own - the
lockup, ``BLOG · <date>``, the title and the summary - and the two WINGS hold
the brand mark, large and faint, the address and the tags.

Geometry and colours are the operator-approved mockup's. The mark is the same
drawing as ``deploy/site/assets/mark.svg`` (``card_draw.mark``).

``blog_svc`` is its own process and imports nothing from ``options_svc``; the
drawing kit it shares with that card is ``shared/card_draw.py``.

CONTRACT: ``render_blog_card`` **never raises**. A card that cannot be drawn is
None, and the entry is published without one.
"""
import datetime as _dt
import hashlib
import json
import logging
from zoneinfo import ZoneInfo

from shared import card_draw as D
from shared import social_cards
from shared.card_draw import Canvas

log = logging.getLogger("blog_svc.card")

# Bump when the drawing changes: every entry's card is then drawn again.
RENDER_VERSION = 1

# Nocturne (deploy/site/assets/site.css tokens, restated: Pillow cannot read CSS).
BG = (0x16, 0x18, 0x26)
TEXT = (0xe9, 0xe9, 0xed)
ACCENT = (0x91, 0x84, 0xd9)
MUTED = (0x9a, 0x9b, 0xb0)
CHIP_EDGE = (0x3a, 0x3c, 0x55)
CHIP_TEXT = (0xc9, 0xc9, 0xd6)

MID = (384.0, 816.0)           # the text column, inside D.GRID_STRIP
MID_W = MID[1] - MID[0]
CX = (MID[0] + MID[1]) / 2
LINE_H = 1.12                  # title line height, in em
SUM_LINE_H = 1.4
ELLIPSIS = "…"
_CT = ZoneInfo("America/Chicago")
_MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def _tags(entry):
    raw = entry.get("tags")
    return [t for t in (_text(x) for x in (raw if isinstance(raw, (list, tuple)) else ())) if t]


def digest(entry):
    """sha256 of everything the drawing reads, and the render version."""
    body = json.dumps([RENDER_VERSION, entry.get("title"), entry.get("summary"),
                       list(entry.get("tags") or []), entry.get("published_at")],
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def split_title(title):
    """``(plain, accent)``: the accent on the second half, split at the space
    nearest the middle by character count (the earlier on a tie). One word is
    all plain."""
    t = _text(title)
    spaces = [i for i, ch in enumerate(t) if ch == " "]
    if not spaces:
        return t, ""
    mid = len(t) / 2
    cut = min(spaces, key=lambda i: (abs(i - mid), i))
    return t[:cut], t[cut + 1:]


def _date_label(iso):
    try:
        when = _dt.datetime.fromisoformat(str(iso))
        if when.tzinfo is None:
            when = when.replace(tzinfo=_CT)
        local = when.astimezone(_CT)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"{_MON[local.month - 1]} {local.day}, {local.year}"


# ── wrapping ─────────────────────────────────────────────────────────────────

def _clip(c, s, size, weight, max_w):
    """``s`` cut with an ellipsis so it fits ``max_w`` (``s`` itself when it does)."""
    if c.width(s, size, weight) <= max_w:
        return s
    s = s.rstrip(" .,;:!?-")
    while s and c.width(s + ELLIPSIS, size, weight) > max_w:
        s = s[:-1]
    return s.rstrip(" .,;:!?-") + ELLIPSIS


def _wrap(c, words, size, weight, max_w):
    """Greedy lines of ``(word, tone)``; a word wider than a line is cut first."""
    lines, cur, cur_w = [], [], 0.0
    space = c.width(" ", size, weight)
    for word, tone in words:
        if c.width(word, size, weight) > max_w:
            word = _clip(c, word, size, weight, max_w)
        w = c.width(word, size, weight)
        if cur and cur_w + space + w > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur_w = w if not cur else cur_w + space + w
        cur.append((word, tone))
    if cur:
        lines.append(cur)
    return lines


def _fit_lines(c, lines, max_lines, size, weight, max_w):
    """At most ``max_lines`` lines; if cut, the last one ends in an ellipsis."""
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    text = " ".join(w for w, _ in last)
    tone = last[-1][1]
    kept[-1] = [(_clip(c, text + ELLIPSIS, size, weight, max_w), tone)]
    return kept


def _segments(line):
    """Consecutive words of one tone joined, so a line is at most a few draws."""
    out = []
    for word, tone in line:
        if out and out[-1][1] == tone:
            out[-1] = (out[-1][0] + " " + word, tone)
        else:
            out.append((word, tone))
    return out


def _draw_line(c, line, size, weight, baseline, tones):
    segs = _segments(line)
    space = c.width(" ", size, weight)
    total = sum(c.width(s, size, weight) for s, _ in segs) + space * (len(segs) - 1)
    x = CX - total / 2
    for s, tone in segs:
        x += c.text(x, baseline, s, size, weight, tones[tone], anchor="ls") + space


def _baseline(top, line_h, size):
    """Baseline of a line whose box starts at ``top`` and is ``line_h`` px tall."""
    return top + (line_h - size * 1.21) / 2 + size * 0.97


# ── the drawing ──────────────────────────────────────────────────────────────

def _lockup(c):
    word, size, track = "NEURALSTRIKE", 22, 4.4
    text_w = c.width(word, size, "medium", track)
    total = 50 + 18 + text_w
    x = CX - total / 2
    D.mark(c, x, 62 - 25, 50, rule=ACCENT, chevrons=TEXT)
    # Centre the capitals on the mark's centre line (y 62): cap height is ~0.73em.
    c.text(x + 50 + 18, 62 - size * 0.73 / 2 - size * 0.2, word, size, "medium", TEXT,
           spacing=track)


def _wings(c):
    faint = dict(rule=D.mix(ACCENT, BG, 0.07), chevrons=D.mix(TEXT, BG, 0.07))
    D.mark(c, -250, 35, 560, **faint)
    D.mark(c, 890, 35, 560, **faint)


def _eyebrow(c, entry):
    day = _date_label(entry.get("published_at"))
    label = f"BLOG · {day}" if day else "BLOG"
    sp = 17 * 0.22
    w = c.width(label, 17, "semibold", sp)
    c.text(CX - w / 2, 140, label, 17, "semibold", ACCENT, spacing=sp)


def _title(c, entry, cfg):
    plain, accent = split_title(entry.get("title"))
    words = [(w, 0) for w in plain.split()] + [(w, 1) for w in accent.split()]
    if not words:
        c.title_lines, c.title_size = [], 0
        return 184.0
    hi, lo = int(cfg["title_max_px"]), int(cfg["title_min_px"])
    max_lines = max(1, int(cfg["title_max_lines"]))
    size = hi
    while True:
        lines = _wrap(c, words, size, "medium", MID_W)
        if len(lines) <= max_lines or size - 2 < lo:
            break
        size -= 2
    lines = _fit_lines(c, lines, max_lines, size, "medium", MID_W)
    line_h = size * LINE_H
    top = 184.0
    for line in lines:
        _draw_line(c, line, size, "medium", _baseline(top, line_h, size), (TEXT, ACCENT))
        top += line_h
    c.title_lines = [" ".join(w for w, _ in ln) for ln in lines]
    c.title_size = size
    return top


def _summary(c, entry, cfg, top):
    text = _text(entry.get("summary"))
    if not text:
        return
    size = int(cfg["summary_px"])
    lines = _wrap(c, [(w, 0) for w in text.split()], size, "regular", MID_W)
    lines = _fit_lines(c, lines, max(1, int(cfg["summary_max_lines"])), size, "regular", MID_W)
    line_h = size * SUM_LINE_H
    y = top + 24
    for line in lines:
        _draw_line(c, line, size, "regular", _baseline(y, line_h, size), (MUTED,))
        y += line_h


def _foot(c, entry):
    c.text(60, 564, "neuralstrike.co/blog", 18, "regular", MUTED, anchor="ls")
    tags = _tags(entry)
    size, pad, h, gap, right, room = 17, 14, 30, 8, 1140.0, 1140.0 - 856.0
    widths, used = [], 0.0
    for t in tags:
        w = c.width(t, size) + 2 * pad
        if used + w + (gap if widths else 0) > room:
            break
        used += w + (gap if widths else 0)
        widths.append(w)
    x = right - used
    top = 560 - h / 2
    for t, w in zip(tags, widths):
        c.rrect(x, top, x + w, top + h, h / 2, outline=CHIP_EDGE, width=1)
        c.text(x + pad, top + h / 2 + size * 0.36, t, size, "regular", CHIP_TEXT, anchor="ls")
        x += w + gap


def layout(entry):
    """Draw the card and return the canvas, so a test reads ``drawn``,
    ``title_lines`` and ``title_size``."""
    cfg = social_cards.config()["blog"]
    entry = entry if isinstance(entry, dict) else {}
    c = Canvas(D.new_image(BG))
    c.title_lines, c.title_size = [], 0
    _wings(c)
    _lockup(c)
    _eyebrow(c, entry)
    bottom = _title(c, entry, cfg)
    _summary(c, entry, cfg, bottom)
    _foot(c, entry)
    return c


def render_blog_card(entry):
    """PNG bytes for one entry (``title``, ``summary``, ``tags``, ``published_at``),
    or None. Never raises."""
    try:
        return D.png_bytes(layout(entry).im)
    except Exception as exc:  # noqa: BLE001 -- an entry is published with no card rather than not at all
        log.warning("blog card render failed: %s", type(exc).__name__, exc_info=False)
        return None
