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
  change. A gap past ``futures_chain_config.outage_after()`` is drawn as blank columns,
  in a band labelled "No capture". A capture a minute late is not an outage.
* **A column is a capture, not a minute.** The Bar picker, Smooth and the projected
  afternoon all count columns on a page that assumes a minute each, so each is handed
  what it says in minutes, at ``futures_chain_config.column_step()``, the one width.
* **What is offered.** Flow prints scaled contract counts: the adapter scales open
  interest and volume by contract size so the engine's dollars come out right, and that
  is no number of contracts to show. Term needs more than one expiry, and a capture
  holds one. The analysis buttons report on the stock indexes or fetch from Schwab.

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
_BAR_TIP = ("Bar size for candles / OHLC. Highs and lows are sampled once a capture, so "
            "a bar no longer than the capture interval holds one reading.")


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


def _headline(state, captured, now, last_row) -> str:
    if captured is None:
        return "Stale: no capture time"
    when = futures_chain.central_time(captured)
    clock = f"{when:%H:%M} Central"
    if state == "last":
        day = f"{when:%A, %B} {when.day}"
        if last_row is not None and last_row - captured >= 60:
            # The kept capture is taken before the close's spike; the session's rows
            # run on past it, so one time would be wrong about one of the panels.
            ends = futures_chain.central_time(last_row)
            text = f"Last session. Strike bars as of {clock} on {day}; the heat map runs to {ends:%H:%M}"
        else:
            text = f"Last session, as of {clock} on {day}"
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

    ``why`` is what the server last heard. ``reason`` is never read: it is that plus
    an age worked out when the payload was written."""
    state = reading(snap, now)
    if state is None:
        return None
    block = _block(snap)
    # The same rule as the service's, at this page's clock: the payload may be from
    # last week. Between sessions nothing is expected, and an old cause is not why.
    between = state == "last" and not futures_chain.in_session(futures_chain.central_time(now))
    parts = [_headline(state, _fmt.num(block.get("captured_at")), now,
                       _fmt.num(block.get("last_row_at")))]
    low, high = _fmt.num(block.get("strike_low")), _fmt.num(block.get("strike_high"))
    if low is not None and high is not None:
        parts.append(f"Strikes captured {_fmt.price(low)} to {_fmt.price(high)}")
    cause = block.get("why")
    if isinstance(cause, str) and cause.strip() and not between:
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
    """``rows`` with every time jump past ``outage_after()`` turned into blank columns,
    ``(ts, None, ..., None)``, at the column width: as many as fit in it, the nearest
    whole number, at least one and at most ``max_outage_columns``. A shorter jump adds
    nothing: a read crossing a minute gives gaps of 240 and then 360 at a 300-second
    interval, and that capture was late, not missing. The rows themselves are kept as
    they came.

    The jump is measured between captures. At a step below the interval a capture's
    last repeat sits up to an interval past it, so a missed capture would measure short
    from there. A repeat holds the same cells as the row before and lies under one
    interval from its capture, so two identical captures a whole interval apart are
    still two captures."""
    capture = futures_chain_config.capture()
    step = futures_chain_config.column_step()
    after = futures_chain_config.outage_after()
    interval = capture["interval_sec"]
    repeated = step < interval
    taken = None
    out: list = []
    for row in rows or []:
        if out:
            last, ts = out[-1][0], row[0]
            if repeated and ts - taken < interval and row[1:] == out[-1][1:]:
                out.append(row)
                continue
            if ts - taken > after:
                count = max(1, int((ts - last + step / 2) // step) - 1)
                blank = (None,) * (len(row) - 1)
                out.extend((at, *blank) for at in
                           (last + step * (i + 1) for i in range(min(count, capture["max_outage_columns"])))
                           if at < ts)
        taken = row[0]
        out.append(row)
    return out


def _blank_stretches(rows) -> list:
    """``(first, last)`` column of each run of blank columns."""
    out: list = []
    for i, row in enumerate(rows):
        if _is_blank(row):
            if out and out[-1][1] == i - 1:
                out[-1] = (out[-1][0], i)
            else:
                out.append((i, i))
    return out


def outage_bands(rows, fill, font) -> list:
    """An x-axis plot band for each blank stretch, so an outage is named on screen:
    against quiet cells a blank's edge disappears into the wash. The text stands on
    end because a stretch can be one column wide."""
    return [{"from": first - 0.5, "to": last + 0.5, "color": fill,
             "label": {"text": "No capture", "rotation": 270, "textAlign": "center",
                       "verticalAlign": "middle", "style": {"color": font}}}
            for first, last in _blank_stretches(rows)]


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
    column a minute; the feed's columns are ``column_step`` apart."""
    return max(1, round(minutes * 60 / futures_chain_config.column_step()))


def smooth_on_columns(smooth, rows) -> dict:
    """Smooth's widths for the feed's columns. ``minutes`` is a width in columns on a
    page that assumes a minute each, so Medium would otherwise blur five times as far
    as it says. An average may not reach across a blank column either: a gap is only
    skipped, and the capture on the far side would still be counted."""
    return {**smooth, "minutes": smooth["minutes"] * 60 / futures_chain_config.column_step(),
            "breaks": frozenset(i for i, row in enumerate(rows) if _is_blank(row))}


def same_capture(old, new) -> bool:
    """Whether two payloads draw the same chart: a refresh with no new capture
    republishes the same numbers, and a full chart update freezes the browser."""
    a, b = _block(old), _block(new)
    return (a is not None and b is not None and old.get("symbol") == new.get("symbol")
            and all(a.get(key) == b.get(key)
                    for key in ("captured_at", "last_row_at", "frozen", "expiry")))


# ── on the page ──────────────────────────────────────────────────────────────

class FeedPage:
    """The feed's part of one render: the label, and the controls that go with it.

    ``enabled`` is the page's own ``may_enqueue``: the feed is on the private page
    only, so a public or pinned render builds nothing and each method hands back what
    it was given. ``sync`` is the first thing every repaint does and ``paint`` runs
    on the page's one-second timer, so the label keeps its age with nothing new
    arriving. The label follows the symbol DRAWN, which during a switch is not yet
    the one in the dropdown."""

    def __init__(self, head, view_tabs, tabs, *, buttons=(), enabled=True,
                 bar_picker=None, band=("", "")):
        self.enabled, self.on, self.symbol = enabled, False, None
        self._head, self._view_tabs, self._tabs = head, view_tabs, tabs
        self._buttons = [b for b in buttons if b is not None]
        self._status_word = None
        self._text = ""
        self._hidden = frozenset()
        self._reading = None
        self._band, self._bands_drawn = band, False
        self._bar_tip = self._bar_tip_text = None
        if not enabled:
            return
        if bar_picker is not None:
            # Found in NiceGUI's own element tree. If that shape ever changes the tip
            # keeps the stock market's words, and the page must still open.
            self._bar_tip = next((child for child in bar_picker.parent_slot.children
                                  if isinstance(child, ui.tooltip)
                                  and str(child.props.get("target", "")).endswith(bar_picker.html_id)),
                                 None)
            self._bar_tip_text = getattr(self._bar_tip, "text", None)
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
            self._tabs[view].set_visibility(view not in self._hidden)
        if self._bar_tip is not None:
            self._bar_tip.text = _BAR_TIP if on else self._bar_tip_text
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
            self._hidden = hidden_views(drawn)
            on = bool(self._hidden)
            if on != self.on:
                self._switch(on)
        if self._view_tabs.value in self._hidden:
            self._view_tabs.value = _FALLBACK_VIEW
        self.paint(snap)

    def _active(self) -> bool:
        return self.on and self._view_tabs.value != _SYMBOL_INDEPENDENT_VIEW

    def paint(self, snap, now=None) -> bool:
        """The label as of now. Pure over what the page holds: no bus read. True when
        the payload has just stopped or started reading as current, which changes
        what the chart may draw."""
        if not self.enabled:
            return False
        if not self._active():
            self._text = ""
            self._line.set_visibility(False)
            self._notice.set_visibility(False)
            return False
        now = _now() if now is None else now
        self._text = feed_label(snap, now) or no_capture_text(self.symbol)
        state = reading(snap, now)
        changed = self._reading is not None and state != self._reading
        self._reading = state
        flagged = state != "current"
        self._line.text = self._text
        self._notice_text.text = self._text
        self._line.set_visibility(not flagged)
        self._notice.set_visibility(flagged)
        return changed

    def strip(self, summary):
        """The status strip for a feed symbol, in place of the NYSE session, the scan
        times and the refresh countdown; ``None`` otherwise."""
        if not self.enabled or not self._active():
            return None
        return "  ·  ".join(part for part in (self._text, summary) if part)

    def columns(self, rows):
        return with_outages(rows) if self.enabled and self.on else rows

    def figure(self, fig, rows):
        if not self.enabled:
            return fig
        if self.on:
            break_spot_line(fig, rows)
            fig["xAxis"]["plotBands"] = outage_bands(rows, *self._band)
            self._bands_drawn = True
        elif self._bands_drawn:
            # An in-place update merges options: a key left out keeps the last ones.
            fig["xAxis"]["plotBands"] = []
            self._bands_drawn = False
        return fig

    def forward(self, band, snap):
        """The projected afternoon at this page's step, which the band carries so the
        heat map and the hedge panel cannot differ, or ``None`` for a payload not
        reading as current: it is priced from the capture's own time, so the hours
        since would be drawn as a forecast from a "now" long past."""
        if not (self.enabled and self.on) or not isinstance(band, dict):
            return band
        if reading(snap, _now()) != "current":
            return None
        return {**band, "step": futures_chain_config.column_step()}

    def heat_keywords(self, keywords, rows):
        """``HeatControls.read``'s keywords, with Smooth on this page's columns."""
        if self.enabled and self.on and keywords.get("smooth"):
            return {**keywords, "smooth": smooth_on_columns(keywords["smooth"], rows)}
        return keywords

    def keep_chart(self, state, snap, view, done) -> bool:
        """True when ``snap`` is the capture already on screen: it is adopted and the
        label repainted, and the chart is left alone. ``done`` ends the page's wait."""
        if not (self.enabled and self.on and same_capture(state.get("snap"), snap)
                and (state.get("hist") or {}).get(view)):
            return False
        state["snap"] = snap
        if self.paint(snap):
            return False
        done()
        return True

    def no_rows_text(self):
        """The heat map's line with no rows to draw. The feed has no collector."""
        if self.enabled and self.on:
            return f"No rows to draw for {futures_chain.display_symbol(self.symbol)} yet."
        return None

    def levels(self, levels, rows):
        return levels_on_columns(levels, rows) if self.enabled and self.on else levels

    def bar_columns(self, minutes):
        return bar_columns(minutes) if self.enabled and self.on else minutes

    def hedge_note(self, hedge):
        return hedge_note(hedge) if self.enabled and self.on else None
