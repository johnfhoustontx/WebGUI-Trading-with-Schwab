"""The shared performance-scorecard vocabulary — PURE render builders.

Two pages draw the same scorecard over the same ``driver_perf.build_scorecard``
shape: **Claude Trades** (the driver's isolated book) and **Paper Account** (the
manual book). These builders lived in ``pages/driver.py`` until 2026-09-12, when
the manual book got a scorecard of its own (gap assessment C5) — and a second
page reaching into the driver PAGE for its view vocabulary is the wrong shape.
Same reasoning as ``pages/fmt.py`` (the numeric vocabulary) and ``pages/copy.py``
(the shared sentences): text and formatting that more than one screen shows for
one condition belongs in one place, because two screens formatting it differently
is a defect rather than a style difference.

Pure throughout — every function takes the scorecard dict and returns strings,
tuples or render-ready row dicts, so the whole surface is testable without a
browser. ``driver.py`` imports these by name, which is why
``driver.scorecard_headline_chips`` still resolves.
"""

#: A P&L of exactly zero, or an unreadable one, is NEITHER green nor red. A fresh
#: account reads as flat rather than as a loss.
#: ⚠ These are the DRIVER page's existing values, moved verbatim - not the
#: Simulator's payoff green/red (#34d399 / #f87171), which is a different palette
#: for a different purpose. A shared module that quietly restated them would be
#: the very divergence it exists to prevent.
PNL_GREEN, PNL_RED, PNL_NEUTRAL = "#66bb6a", "#ef5350", "#bdbdbd"


def pnl_color(v):
    """Hex color for a numeric P&L: green > 0, red < 0, grey for 0 / None / junk."""
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v == 0:
        return PNL_NEUTRAL
    return PNL_GREEN if v > 0 else PNL_RED


def pnl_class(v):
    """Tailwind text arbitrary-value class for a numeric P&L (mirrors :func:`pnl_color`)."""
    return f"text-[{pnl_color(v)}]"


def money(v):
    """Signed dollar string for a P&L cell; exactly-zero is unsigned (``$0.00``),
    and ``None`` → ``$0.00`` so a fresh-account scorecard reads cleanly."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    if v == 0:
        return "$0.00"
    return f"{'+' if v > 0 else '-'}${abs(v):,.2f}"


def percent(frac):
    """A 0..1 fraction as a 1-dp percent (``0.6667 → '66.7%'``); None/junk → '0.0%'."""
    try:
        return f"{float(frac) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def scorecard_headline_chips(perf):
    """Headline (label, value) chips: trades, open/closed, win rate, realized,
    open unrealized, total P&L — the at-a-glance row of the scorecard."""
    p = perf or {}
    return [
        ("Trades", str(int(p.get("total_trades") or 0))),
        ("Open", str(int(p.get("open") or 0))),
        ("Closed", str(int(p.get("closed") or 0))),
        ("Win rate", percent(p.get("win_rate"))),
        ("Realized", money(p.get("realized_pnl"))),
        ("Open P&L", money(p.get("open_unrealized"))),
        ("Total P&L", money(p.get("total_pnl"))),
    ]


def scorecard_quality_chips(perf):
    """Quality (label, value) chips: avg win, avg loss, profit factor.

    ``profit_factor`` is ``None`` until there is at least one loss (gross-win /
    gross-loss is undefined with no losses) — rendered as the em-dash "—", never
    as 0.00, which would read as "no edge" rather than "no losses yet"."""
    p = perf or {}
    pf = p.get("profit_factor")
    pf_text = "—" if pf is None else f"{float(pf):.2f}"
    return [
        ("Avg win", money(p.get("avg_win"))),
        ("Avg loss", money(p.get("avg_loss"))),
        ("Profit factor", pf_text),
    ]


def _breakdown_rows(rows, key):
    """Format a P&L-by-{symbol|strategy|exit reason} list (signed pnl, % win rate)."""
    out = []
    for r in rows or []:
        r = r or {}
        out.append({
            key: r.get(key, "?"),
            "trades": r.get("trades", 0),
            "pnl": money(r.get("pnl")),
            "_pnl_color": pnl_color(r.get("pnl")),
            "_pnl_class": pnl_class(r.get("pnl")),
            "win_rate": percent(r.get("win_rate")),
        })
    return out


def scorecard_symbol_rows(perf):
    """Render-ready P&L-by-symbol table rows (from ``perf['by_symbol']``)."""
    return _breakdown_rows((perf or {}).get("by_symbol"), "symbol")


def scorecard_strategy_rows(perf):
    """Render-ready P&L-by-strategy table rows (from ``perf['by_strategy']``)."""
    return _breakdown_rows((perf or {}).get("by_strategy"), "strategy")


def scorecard_exit_reason_rows(perf):
    """Render-ready P&L-by-EXIT-REASON rows (from ``perf['by_exit_reason']``).

    ⚠ Added 2026-09-12 because this is the axis that would have shown an anomaly
    nobody could see: replaying the ladder (gap assessment C2) turned up that
    ``MANUAL_CLOSE`` accounts for **+$50,102** of the captured book's reported P&L
    against +$11,664 for every other exit reason combined, and that 130 of its 388
    rows book exactly the full credit. A scorecard split only by symbol and
    strategy cannot surface that; split by how a trade ENDED, it is the first row
    you read.
    """
    return _breakdown_rows((perf or {}).get("by_exit_reason"), "exit_reason")


def best_worst_text(perf):
    """``'Best MU +$120.00 · Worst MU -$60.00'`` — the extreme closed trades.

    Empty (nothing closed) → ``''`` so the card can hide the line. Defensive over
    a missing symbol / non-numeric realized_pnl."""
    p = perf or {}
    bits = []
    for label, pos in (("Best", p.get("best")), ("Worst", p.get("worst"))):
        if isinstance(pos, dict):
            sym = pos.get("symbol") or "?"
            bits.append(f"{label} {sym} {money(pos.get('realized_pnl'))}")
    return " · ".join(bits)
