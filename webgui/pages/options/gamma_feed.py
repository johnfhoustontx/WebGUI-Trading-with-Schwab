"""What Dealer Positioning does differently for /ES and /NQ.

These two have no stock session and no chain from Schwab: each is a capture from
ThinkorSwim on the operator's PC, one every interval, across a session that opens the
evening before its expiry. Three things follow, and all three live here:

* **The label.** The page's own clock and scan times describe the stock market, so the
  page says what these numbers are instead: when they were captured, and whether that
  is too long ago. It works that out against the clock. The flag in the payload was
  right when it was written, and the page may be opened on a payload written hours ago
  (the overnight tick does not run, and nothing else rewrites it).
* **An outage is an outage.** ``heatmap_matrix`` makes one column per row on a category
  axis, so two captures five hours apart would sit side by side and blend into a smooth
  change. Each longer gap is drawn as blank columns at the fill step.
* **What is offered.** Flow and Term need data the feed does not have, and the analysis
  buttons report on the stock indexes or fetch from Schwab.

No label or status text is built from ``dte``: the adapter stamps 0 on every capture,
including an evening's whose expiry is tomorrow, so it says nothing to a reader.

Every existing symbol passes through untouched: ``FeedPage`` hands back the same
objects. ``futures_chain.is_feed_symbol`` re-validates its tables on each call, so it
is asked once when the symbol changes and never per row or per tick. Outside
``gamma.render`` because that function has a size ceiling. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.8, 5.9 and 5.11.
"""
from datetime import date, datetime, timezone

from nicegui import ui

from pages import fmt as _fmt
from pages import ui_kit as kit
from shared import futures_chain, futures_chain_config

_HIDDEN_VIEWS = frozenset({"Flow", "Term"})
_FALLBACK_VIEW = "GEX"
_SYMBOL_INDEPENDENT_VIEW = "Net Prem"


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# ── what is offered ──────────────────────────────────────────────────────────

def select_options(symbols):
    """``symbols`` as they stand when none is a feed symbol, so an existing list is
    exactly what it was; else ``{value: label}``, which keeps the internal name as
    the value and shows /ES and /NQ."""
    feed = set(futures_chain.feed_symbols())
    if not feed.intersection(symbols):
        return symbols
    return {s: futures_chain.display_symbol(s) if s in feed else s for s in symbols}


def hidden_views(symbol) -> frozenset:
    """The subtabs a symbol does not offer."""
    return _HIDDEN_VIEWS if futures_chain.is_feed_symbol(symbol) else frozenset()


def shows_analysis(symbol) -> bool:
    """Whether Explain, Analyze and Briefings go with this symbol. Explain would
    fetch a chain from Schwab, and the other two read the stock indexes whatever the
    dropdown says."""
    return not futures_chain.is_feed_symbol(symbol)


# ── the label ────────────────────────────────────────────────────────────────

def _ago(seconds) -> str:
    """``21 minutes ago``, ``5 hours 33 minutes ago``, ``2 days 3 hours ago``."""
    minutes = max(0, int(seconds // 60))
    if minutes < 1:
        return "under a minute ago"

    def unit(count, word):
        return f"{count} {word}" + ("" if count == 1 else "s")

    days, hours, minutes = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        parts = [unit(days, "day")] + ([unit(hours, "hour")] if hours else [])
    elif hours:
        parts = [unit(hours, "hour")] + ([unit(minutes, "minute")] if minutes else [])
    else:
        parts = [unit(minutes, "minute")]
    return " ".join(parts) + " ago"


def _session_ended(block, now) -> bool:
    """Whether the expiry the capture belongs to has passed. A date that does not
    parse says nothing, and the age still does."""
    try:
        expiry = date.fromisoformat(block.get("expiry"))
    except (TypeError, ValueError):
        return False
    return now >= futures_chain.session_bounds(expiry)[1]


def _block(snap):
    block = snap.get("feed") if isinstance(snap, dict) else None
    return block if isinstance(block, dict) else None


def reading(snap, now):
    """``"last"``, ``"stale"`` or ``"current"`` for a payload at ``now``, or ``None``
    with no feed block. The payload's flags are one input: each can only make it
    worse, because they are as old as the payload."""
    block = _block(snap)
    if block is None:
        return None
    if block.get("frozen") or _session_ended(block, now):
        return "last"
    captured = _fmt.num(block.get("captured_at"))
    too_old = (captured is None
               or now - captured > futures_chain_config.capture()["stale_after_sec"])
    return "stale" if block.get("stale") or too_old else "current"


def _headline(state, captured, now) -> str:
    if captured is None:
        return "Stale: no capture time"
    when = futures_chain.central_time(captured)
    clock = f"{when:%H:%M} Central"
    if state == "last":
        text = f"Last session, as of {clock} on {when:%A, %B} {when.day}"
        if futures_chain.in_session(futures_chain.central_time(now)):
            text += ". No capture has arrived for this session"
        return text
    if state == "stale":
        return f"Stale: captured {_ago(now - captured)}"
    return f"Captured {clock}, {_ago(now - captured)}"


def feed_label(snap, now):
    """What a payload is, in words, at ``now`` (epoch seconds), or ``None`` for a
    payload with no feed block. It always states the captured strikes when it has
    them: the walls and the flip see only those.

    ``why`` is what the server last heard. ``reason`` is that plus an age worked out
    when the payload was written, so it is read only when there is no ``why``."""
    state = reading(snap, now)
    if state is None:
        return None
    block = _block(snap)
    parts = [_headline(state, _fmt.num(block.get("captured_at")), now)]
    low, high = _fmt.num(block.get("strike_low")), _fmt.num(block.get("strike_high"))
    if low is not None and high is not None:
        parts.append(f"Strikes captured {_fmt.price(low)} to {_fmt.price(high)}")
    cause = block["why"] if "why" in block else block.get("reason")
    if isinstance(cause, str) and cause.strip():
        parts.append(cause.strip().rstrip("."))
    return ". ".join(parts)


def no_capture_text(symbol) -> str:
    """The empty view: the symbol has no capture to show at all."""
    return (f"No capture of {futures_chain.display_symbol(symbol)} to show yet. Its "
            "chain comes from ThinkorSwim on the PC, not from Schwab.")


def no_greeks_text(symbol) -> str:
    """Every contract's gamma is absent: the feed calculates it from each contract's
    own implied volatility, so that is what was missing."""
    return (f"No gamma could be calculated for {futures_chain.display_symbol(symbol)}: "
            "ThinkorSwim sent no usable implied volatility in this capture, so there "
            "are no strike bars or walls to draw.")


def hedge_note(hedge):
    """The Delta view's note when the engine has no hedge figure, or ``None`` when
    it has one. An evening capture's nearest expiry is the session's own, so the
    page's sentence about the nearest expiry would be false here."""
    if (hedge or {}).get("hedge_pressure") is not None:
        return None
    return "hedge n/a for this capture"


# ── an outage is an outage ───────────────────────────────────────────────────

def _is_blank(row) -> bool:
    return len(row) > 1 and all(cell is None for cell in row[1:])


def with_outages(rows) -> list:
    """``rows`` with every time jump longer than one capture interval turned into
    blank columns, ``(ts, None, ..., None)``, at the fill step. A jump of an interval
    or less adds nothing. The rows themselves are kept as they came."""
    capture = futures_chain_config.capture()
    interval, step = capture["interval_sec"], capture["fill_step_sec"]
    out: list = []
    for row in rows or []:
        if out:
            at, ts = out[-1][0] + step, row[0]
            if ts - out[-1][0] > interval:
                blank = (None,) * (len(row) - 1)
                while at < ts:
                    out.append((at, *blank))
                    at += step
        out.append(row)
    return out


def break_spot_line(fig, rows):
    """``heatmap_figure`` leaves a column with no spot out of its price line, and a
    line series rules straight across what is left out. A null point at the start of
    each blank stretch breaks it there. The figure is changed and handed back."""
    starts = [i for i, row in enumerate(rows)
              if _is_blank(row) and (i == 0 or not _is_blank(rows[i - 1]))]
    for series in fig.get("series") or []:
        if starts and series.get("name") == "Spot" and series.get("data"):
            series["data"] = sorted([*series["data"], *([x, None] for x in starts)],
                                    key=lambda point: point[0])
    return fig


def levels_on_columns(levels, rows):
    """The level tracks on the columns drawn. They are positional, one value per row
    the service published, so each blank column needs a gap of its own or every later
    value would sit that many columns early."""
    if not isinstance(levels, dict) or not any(_is_blank(row) for row in rows):
        return levels
    out = {}
    for key, values in levels.items():
        if not isinstance(values, list):
            out[key] = values
            continue
        remaining = iter(values)
        out[key] = [None if _is_blank(row) else next(remaining, None) for row in rows]
    return out


def bar_columns(minutes) -> int:
    """How many columns a bar of ``minutes`` spans. The page's bar picker counts one
    column a minute; the feed's columns are the fill step apart."""
    step = futures_chain_config.capture()["fill_step_sec"]
    return max(1, round(minutes * 60 / step))


# ── on the page ──────────────────────────────────────────────────────────────

class FeedPage:
    """The feed's part of one render: the label, and the controls that go with it.

    ``enabled`` is the page's own ``may_enqueue``: the feed is on the private page
    only, so a public or pinned render builds nothing and each method hands back what
    it was given. ``sync`` is the first thing every repaint does and ``paint`` runs
    on the page's one-second timer, so the label keeps its age with nothing new
    arriving. The label follows the symbol DRAWN, which during a switch is not yet
    the one in the dropdown."""

    def __init__(self, head, view_tabs, tabs, *, buttons=(), enabled=True):
        self.enabled, self.on, self.symbol = enabled, False, None
        self._head, self._view_tabs, self._tabs = head, view_tabs, tabs
        self._buttons = [b for b in buttons if b is not None]
        self._status_word = None
        self._text = ""
        if not enabled:
            return
        self._line = ui.label("").classes("text-xs opacity-70")
        self._notice = kit.notice("", icon="schedule")
        self._notice_text = next(child for child in self._notice.default_slot.children
                                 if isinstance(child, ui.label))
        self._line.set_visibility(False)
        self._notice.set_visibility(False)

    def keep_status_word(self, label):
        """The collector's word beside the strip: it is about the stock collector."""
        self._status_word = label
        return label

    def _switch(self, on):
        self.on = on
        self._head.stamp.set_visibility(not on)     # a refresh moves it, a capture does not
        if self._status_word is not None:
            self._status_word.set_visibility(not on)
        for view in _HIDDEN_VIEWS:
            self._tabs[view].set_visibility(not on)
        self.sync_buttons(force=True)

    def sync_buttons(self, force=False):
        """Run after anything that sets the buttons by the page's own rule."""
        if self.enabled and (self.on or force):
            show = not self.on and self._view_tabs.value != _SYMBOL_INDEPENDENT_VIEW
            for button in self._buttons:
                button.set_visibility(show)

    def sync(self, snap, symbol):
        """Follow the symbol drawn: ``snap``'s, else the dropdown's."""
        if not self.enabled:
            return
        drawn = (snap or {}).get("symbol") or symbol
        if drawn != self.symbol:
            self.symbol = drawn
            on = futures_chain.is_feed_symbol(drawn)
            if on != self.on:
                self._switch(on)
        if self.on and self._view_tabs.value in _HIDDEN_VIEWS:
            self._view_tabs.value = _FALLBACK_VIEW
        self.paint(snap)

    def _active(self) -> bool:
        return self.on and self._view_tabs.value != _SYMBOL_INDEPENDENT_VIEW

    def paint(self, snap, now=None):
        """The label as of now. Pure over what the page holds: no bus read."""
        if not self.enabled:
            return
        if not self._active():
            self._text = ""
            self._line.set_visibility(False)
            self._notice.set_visibility(False)
            return
        now = _now() if now is None else now
        self._text = feed_label(snap, now) or no_capture_text(self.symbol)
        flagged = reading(snap, now) != "current"
        self._line.text = self._text
        self._notice_text.text = self._text
        self._line.set_visibility(not flagged)
        self._notice.set_visibility(flagged)

    def strip(self, summary):
        """The status strip for a feed symbol, in place of the NYSE session, the scan
        times and the refresh countdown; ``None`` otherwise."""
        if not self.enabled or not self._active():
            return None
        return "  ·  ".join(part for part in (self._text, summary) if part)

    def columns(self, rows):
        return with_outages(rows) if self.enabled and self.on else rows

    def figure(self, fig, rows):
        return break_spot_line(fig, rows) if self.enabled and self.on else fig

    def levels(self, levels, rows):
        return levels_on_columns(levels, rows) if self.enabled and self.on else levels

    def bar_columns(self, minutes):
        return bar_columns(minutes) if self.enabled and self.on else minutes

    def hedge_note(self, hedge):
        return hedge_note(hedge) if self.enabled and self.on else None
