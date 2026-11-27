"""New highs / new lows across US-listed stocks, for the Macro Board's top bar.

Publishes ``cache:market:new_highs``: how many stocks are at a new 52-week,
13-week and 4-week high and low, across every common stock with its main listing
on the exchanges in ``config/new_highs.toml`` (4,984 on 2026-11-27), and again
for those priced at or above ``min_price``.

The counts come from TradingView's screener, not Schwab: Schwab's API has no
scanner, and counting 5,000 stocks ourselves would cost the proxy a pull per
stock. The screener COUNTS on its own side: each request carries a field-to-
field filter (``high`` at or above ``price_52_week_high``) and a two-row range,
and only its ``totalCount`` is read. One refresh is thirteen small requests.
⚠ The address is unofficial (``config/new_highs.toml`` says why that is
accepted); a refresh that loses any one count publishes nothing, so the board
keeps the last full set and greys it by age, never a half-fresh one.

⚠ TradingView has no week fields: 13 wk is its 3-month window and 4 wk its
1-month window (``WINDOWS``). The windows live here, not in config, because
changing one means changing the labels and the help that explain it.

This module imports nothing from ``compute``: it calls neither the proxy nor
Schwab.
"""

CACHE = "cache:market:new_highs"
EVENT = "events:market:new_highs"

SOURCE = "TradingView"

# (key, label, the window's high field, the window's low field), in board order.
WINDOWS = (
    ("52w", "52 wk", "price_52_week_high", "price_52_week_low"),
    ("13w", "13 wk", "High.3M", "Low.3M"),
    ("4w", "4 wk", "High.1M", "Low.1M"),
)
SIDES = ("highs", "lows")
ALL, OVER = "all", "over5"
UNIVERSE = "universe"


def _universe(cfg) -> list[dict]:
    return [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "exchange", "operation": "in_range", "right": list(cfg["exchanges"])},
    ]


def _body(filters) -> dict:
    return {"markets": ["america"], "symbols": {"query": {"types": []}},
            "filter": filters, "columns": ["name"], "range": [0, 1]}


def _rows(cfg) -> tuple[str, ...]:
    return (ALL, OVER) if cfg["min_price"] > 0 else (ALL,)


def _side_filter(side, high_field, low_field) -> dict:
    if side == "highs":
        return {"left": "high", "operation": "egreater", "right": high_field}
    return {"left": "low", "operation": "eless", "right": low_field}


def requests_for(cfg) -> list[tuple[str, dict]]:
    """PURE: every request one refresh makes, as ``(key, body)``, in the order
    they are sent. The universe count first, then ``row.window.side``."""
    base = _universe(cfg)
    out = [(UNIVERSE, _body(list(base)))]
    for row in _rows(cfg):
        price = ([{"left": "close", "operation": "egreater", "right": cfg["min_price"]}]
                 if row == OVER else [])
        for key, _label, high_field, low_field in WINDOWS:
            for side in SIDES:
                out.append((f"{row}.{key}.{side}",
                            _body(base + price + [_side_filter(side, high_field, low_field)])))
    return out


def build(counts, cfg, session) -> dict | None:
    """PURE: the published view, or None unless EVERY count is present.

    ``counts`` maps each ``requests_for`` key to its ``totalCount`` (None for a
    request that failed). All or nothing: a window must never pair a fresh high
    with a stale low."""
    keys = [k for k, _ in requests_for(cfg)]
    if any(counts.get(k) is None for k in keys):
        return None
    rows = {row: {key: {side: counts[f"{row}.{key}.{side}"] for side in SIDES}
                  for key, *_ in WINDOWS}
            for row in _rows(cfg)}
    return {"enabled": True, "source": SOURCE, "universe": counts[UNIVERSE],
            "session": session, "min_price": cfg["min_price"],
            "exchanges": list(cfg["exchanges"]), "public": cfg["public_show"],
            "rows": rows}


def publish(bus, payload) -> int:
    """Validate against MarketNewHighs and cache+publish. Returns the version.

    ``skip_unchanged``: the view carries no timestamp, so a refresh that finds the
    same counts only refreshes ``:ts`` and bumps no version."""
    from shared.contracts.market import MarketNewHighs
    return bus.cache_set(CACHE, MarketNewHighs(**payload).model_dump(), event=EVENT,
                         skip_unchanged=True)
