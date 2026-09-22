"""Draw a published market report as a branded PNG, for the X post that links it.

The report itself is HTML on neuralstrike.co; what reaches a feed is its verdict
and a few highlights, as :func:`services.market_svc.report_summary.parse_report`
reads them. This card puts those on the same canvas as the hourly trade idea --
same lockup, same eyebrow, same 2x-native 16:9 grid -- by reusing
:mod:`trade_idea_card`'s primitives rather than restating them, so the two posts
cannot drift apart visually.

Every field is coerced with ``str(x or "")``: the payload is parsed out of a
rendered document, so any of it can be missing or odd. A report with no headline
has nothing to say and returns None rather than a blank card.

CONTRACT: **never raises.** A card that cannot be drawn returns None, and the
caller posts text alone.
"""
import datetime as _dt
import io
import logging

from services.options_svc import card_kit as K
from services.options_svc.trade_idea_card import (
    BRAND_ACCENT, HEIGHT, PAD, SCALE, WIDTH, _Canvas, _header, font,
)

log = logging.getLogger(__name__)

MAX_HIGHLIGHTS = 4
HEADLINE_SIZE, HEADLINE_LEAD, HEADLINE_LINES = 34, 44, 2
HIGHLIGHT_SIZE, HIGHLIGHT_STEP = 20, 58
ELLIPSIS = "…"


def highlights(report):
    """The first four highlights that are strings, in order."""
    raw = (report or {}).get("highlights") if isinstance(report, dict) else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [h for h in raw if isinstance(h, str) and h.strip()][:MAX_HIGHLIGHTS]


def _fit(c, s, size, weight, max_w):
    """``s`` cut to ``max_w`` layout px, with an ellipsis if anything was cut."""
    s = s.strip()
    if c.width(s, size, weight) <= max_w:
        return s
    while s and c.width(s.rstrip() + ELLIPSIS, size, weight) > max_w:
        s = s[:-1]
    return s.rstrip() + ELLIPSIS


def _headline_lines(c, text, max_w):
    lines = K.wrap(text, font(HEADLINE_SIZE, "extrabold"), max_w * SCALE, c.d)
    if len(lines) <= HEADLINE_LINES:
        return [_fit(c, ln, HEADLINE_SIZE, "extrabold", max_w) for ln in lines]
    head = lines[:HEADLINE_LINES - 1]
    rest = " ".join(lines[HEADLINE_LINES - 1:])
    last = _fit(c, rest, HEADLINE_SIZE, "extrabold", max_w)
    if not last.endswith(ELLIPSIS):
        last += ELLIPSIS
    return [_fit(c, ln, HEADLINE_SIZE, "extrabold", max_w) for ln in head] + [last]


def _render(report, now):
    from PIL import Image

    headline = str(report.get("headline") or "").strip()
    if not headline:
        return None
    label = str(report.get("slot_label") or "").strip() or "MARKET REPORT"
    as_of = str(report.get("as_of") or "").strip()

    im = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), K.BG)
    c = _Canvas(im)
    _header(c, now, label=label.upper())

    max_w = WIDTH - 2 * PAD
    y = 120
    for ln in _headline_lines(c, headline, max_w):
        c.text(PAD, y, ln, HEADLINE_SIZE, "extrabold", K.TITLE)
        y += HEADLINE_LEAD

    y += 34
    text_x = PAD + 30
    for h in highlights(report):
        c.rrect(PAD, y + 7, PAD + 12, y + 19, 3, fill=BRAND_ACCENT)
        c.text(text_x, y, _fit(c, h, HIGHLIGHT_SIZE, "regular", WIDTH - PAD - text_x),
               HIGHLIGHT_SIZE, "regular", K.TEXT)
        y += HIGHLIGHT_STEP

    c.line([(PAD, HEIGHT - 58), (WIDTH - PAD, HEIGHT - 58)], K.EDGE, 1)
    c.text(PAD, HEIGHT - 34, "neuralstrike.co/report.html", 13, "semibold", K.MUTED,
           spacing=0.6)
    if as_of:
        c.text(WIDTH - PAD - c.width(as_of, 13, "semibold"), HEIGHT - 34, as_of, 13,
               "semibold", K.MUTED)

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_report_png(report, *, now=None):
    """PNG bytes for one parsed report, or None. Never raises."""
    try:
        if not isinstance(report, dict):
            return None
        now = now or _dt.datetime.now()
        return _render(report, now)
    except Exception as exc:  # noqa: BLE001 -- best-effort, the text still posts
        log.warning("market report card render failed: %s", exc, exc_info=True)
        return None
