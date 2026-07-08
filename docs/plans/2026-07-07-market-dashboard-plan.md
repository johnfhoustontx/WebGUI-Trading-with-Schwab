# Market Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/market` "Market Dashboard" page that shows a live, ~2 s-polling grid of ~48 macro tickers (from `symbol_categories.csv`), grouped into a framed panel per category, with each tile's background colored by semantic risk-on/risk-off condition.

**Architecture:** A new Tier-2 `market_svc` (port 8215) polls the proxy's raw `/quotes` endpoint on a market-hours-gated cadence, normalizes change across INDEX/EQUITY/FUTURE asset types, computes two spreads (`$ADVN-$DECN`, `HYG-LQD`), reads the app's own put/call from `cache:sentiment:composite`, derives a per-tile `color_state`, and publishes `cache:market:dashboard`. The Tier-1 webgui page version-polls that key and repaints tiles in place (Tailwind palette-mapped background classes, no `.style()`).

**Tech Stack:** Python 3.11, FastAPI (via `services/_scaffold.make_app`), Redis/Memurai (`shared.bus`), pydantic contracts (`shared/contracts`), NiceGUI (webgui), pytest.

**Reference design:** `docs/plans/2026-07-07-market-dashboard-design.md` (symbol map, polarities, equivalents, and the data-availability probe results are all there).

**Key facts already verified against the live proxy:**
- `/quotes?symbols=…` returns `{SYM: {assetMainType, quote:{lastPrice, netChange, netPercentChange, futurePercentChange, closePrice, …}}, …, "errors": {"invalidSymbols":[…]}}`.
- Change field differs by type: equities/indices → `netPercentChange`; futures → `futurePercentChange`; internals (`$ADVN`,`$DECN`,`$TICK`,`$ADD`,`$ADSPD`) → value-only (`netPercentChange`/`closePrice` are 0).
- CSV→Schwab symbol translations: `SPX`→`$SPX`, `NDX`→`$NDX`, `VIX`→`$VIX`, `SKEW`→`$SKEW`, `VIX1D`→`$VIX1D`, `VIX3M`→`$VIX3M`, `/ES[U26]`→`/ESU26`, `/NQ[U26]`→`/NQU26`, `$DXY`→`UUP` (equivalent).
- `$PCALL`/`$PCSP` have no Schwab data → replaced by one "Put/Call (cap-wt sectors)" tile fed from `cache:sentiment:composite` → `live.sector_pcr`.

**Test commands (run from the repo root unless noted):**
- Service: `.venv\Scripts\python -m pytest services\market_svc -q`
- Contracts: `.venv\Scripts\python -m pytest shared\contracts -q`
- Webgui: `cd webgui ; ..\.venv\Scripts\python -m pytest -q` (then `cd ..`)

---

## Task 1: Register the `market` service port

**Files:**
- Modify: `config/ports.toml` (the `[services]` table)

`repo_paths.py` already does `SERVICE_PORTS = dict(_ports["services"])` + `SERVICE_URLS = {…}`, so adding the row auto-flows everywhere (incl. the `/status` page's component sweep).

**Step 1: Add the port**

In `config/ports.toml`, under `[services]`, add `market` after `driver`:

```toml
[services]                # Tier-2 domain services (repo_paths → SERVICE_PORTS/SERVICE_URLS)
sentiment = 8210
options   = 8211
portfolio = 8212
trade     = 8213
driver    = 8214
market    = 8215
```

**Step 2: Verify it resolves**

Run: `.venv\Scripts\python -c "from repo_paths import SERVICE_PORTS, SERVICE_URLS; print(SERVICE_PORTS['market'], SERVICE_URLS['market'])"`
Expected: `8215 http://127.0.0.1:8215`

**Step 3: Commit**

```bash
git add config/ports.toml
git commit -m "feat(market): register market_svc port 8215"
```

---

## Task 2: The symbol map (pure data + grouping)

**Files:**
- Create: `services/market_svc/__init__.py` (empty)
- Create: `services/market_svc/symbols.py`
- Create: `services/market_svc/tests/__init__.py` (empty)
- Create: `services/market_svc/tests/conftest.py`
- Create: `services/market_svc/tests/test_symbols.py`

**Step 1: Write `conftest.py`** (puts the repo root on `sys.path` like the other service test suites)

```python
# services/market_svc/tests/conftest.py
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

**Step 2: Write the failing test** `services/market_svc/tests/test_symbols.py`

```python
from services.market_svc import symbols as S


def test_every_csv_symbol_is_mapped():
    # 48 CSV rows, but $PCALL+$PCSP collapse to ONE external put/call tile,
    # so the dashboard has 47 tiles.
    assert len(S.SYMBOL_MAP) == 47


def test_categories_cover_the_expected_set_in_frame_order():
    assert S.CATEGORY_ORDER == [
        "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
        "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Custom Basket / Spread",
        "Sector SPDR", "Thematic / Industry ETF", "Factor / Momentum ETF",
        "Fixed Income / Credit ETF", "Crypto / Alternatives",
    ]
    # every mapped tile's category is in the order list
    assert {t["category"] for t in S.SYMBOL_MAP} <= set(S.CATEGORY_ORDER)


def test_translations_and_polarities():
    by_disp = {t["display"]: t for t in S.SYMBOL_MAP}
    assert by_disp["VIX"]["quote_symbol"] == "$VIX"
    assert by_disp["VIX"]["polarity"] == "inverted"
    assert by_disp["SPX"]["quote_symbol"] == "$SPX"
    assert by_disp["/ES[U26]"]["quote_symbol"] == "/ESU26"
    assert by_disp["$DXY"]["quote_symbol"] == "UUP"      # equivalent
    assert by_disp["$DXY"]["polarity"] == "inverted"
    assert by_disp["TLT"]["polarity"] == "inverted"
    assert by_disp["XLP"]["polarity"] == "normal"        # defensive sector stays literal


def test_kinds():
    kinds = {t["display"]: t["kind"] for t in S.SYMBOL_MAP}
    assert kinds["$ADVN-$DECN"] == "spread"
    assert kinds["HYG-LQD"] == "spread"
    # the collapsed put/call tile is external (fed from sentiment)
    ext = [t for t in S.SYMBOL_MAP if t["kind"] == "external"]
    assert len(ext) == 1 and ext[0]["category"] == "Options Sentiment"


def test_quote_symbols_are_the_real_ones_only():
    qs = S.quote_symbols()
    # includes spread legs, excludes computed/external
    assert "$ADVN" in qs and "$DECN" in qs and "HYG" in qs and "LQD" in qs
    assert "$ADVN-$DECN" not in qs and "HYG-LQD" not in qs
    assert "$PCALL" not in qs
```

**Step 3: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_symbols.py -q`
Expected: FAIL (ModuleNotFoundError: services.market_svc.symbols)

**Step 4: Write `services/market_svc/symbols.py`**

```python
"""Market-dashboard symbol map (pure data — no I/O).

Baked from ``symbol_categories.csv``. Single source of truth for CSV→Schwab
symbol translation, per-symbol risk polarity, and how each tile is sourced
(a plain quote, a computed spread, or an external app value). Everything the
poll + coloring logic needs is a lookup here, so those stay pure + testable.

Each entry:
  csv_symbol   – the symbol as written in the CSV (display fallback)
  display      – tile label
  description  – short muted subtitle
  category     – one of CATEGORY_ORDER
  polarity     – "normal" (value up → risk-on/green) | "inverted" (up → risk-off/red)
  kind         – "quote" (fetch quote_symbol) | "spread" (compute) | "external"
  quote_symbol – Schwab symbol to fetch (kind=="quote"); None otherwise
  value_only   – True for internals Schwab returns with no % change (color by sign)
  spread       – (leg_a, leg_b, mode) for kind=="spread"; mode ∈ {"diff_last","diff_pct"}
  source       – for kind=="external" (e.g. "sentiment_pcr")
"""

# Frame layout order (design §5): macro gauges → tape → rotation.
CATEGORY_ORDER = [
    "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
    "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Custom Basket / Spread",
    "Sector SPDR", "Thematic / Industry ETF", "Factor / Momentum ETF",
    "Fixed Income / Credit ETF", "Crypto / Alternatives",
]


def _q(csv, quote, desc, cat, polarity="normal", value_only=False):
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "quote", "quote_symbol": quote,
            "value_only": value_only, "spread": None, "source": None}


def _spread(csv, leg_a, leg_b, mode, desc, cat, polarity="normal"):
    return {"csv_symbol": csv, "display": csv, "description": desc, "category": cat,
            "polarity": polarity, "kind": "spread", "quote_symbol": None,
            "value_only": False, "spread": (leg_a, leg_b, mode), "source": None}


def _external(csv, source, desc, cat, polarity, display):
    return {"csv_symbol": csv, "display": display, "description": desc, "category": cat,
            "polarity": polarity, "kind": "external", "quote_symbol": None,
            "value_only": False, "spread": None, "source": source}


_INT = "Market Internals / Breadth"
_SEC = "Sector SPDR"
_THM = "Thematic / Industry ETF"
_BRD = "Broad-Market ETF"

SYMBOL_MAP = [
    # Volatility (inverted — fear up = risk-off)
    _q("VIX", "$VIX", "CBOE Volatility Index (30-day)", "Volatility", "inverted"),
    _q("VIX1D", "$VIX1D", "1-day volatility index", "Volatility", "inverted"),
    _q("VIX3M", "$VIX3M", "3-month volatility index", "Volatility", "inverted"),
    _q("SKEW", "$SKEW", "CBOE SKEW / tail-risk index", "Volatility", "inverted"),
    # Options Sentiment (external — app's cap-weighted sector P/C; inverted)
    _external("Put/Call", "sentiment_pcr", "Cap-weighted sector put/call ratio",
              "Options Sentiment", "inverted", "Put/Call (cap-wt sectors)"),
    # Market Internals / Breadth (value-only internals + a computed net spread)
    _q("$ADVN", "$ADVN", "NYSE advancing issues", _INT, "normal", value_only=True),
    _q("$DECN", "$DECN", "NYSE declining issues", _INT, "inverted", value_only=True),
    _spread("$ADVN-$DECN", "$ADVN", "$DECN", "diff_last",
            "Net advancers (breadth spread)", _INT, "normal"),
    _q("$ADD", "$ADD", "NYSE advance/decline line (net)", _INT, "normal", value_only=True),
    _q("$ADSPD", "$ADSPD", "Advance/decline spread", _INT, "normal", value_only=True),
    _q("$TICK", "$TICK", "NYSE TICK", _INT, "normal", value_only=True),
    # Currency (equivalent: UUP; inverted — dollar strength = risk-off)
    _q("$DXY", "UUP", "US Dollar Index (via UUP proxy)", "Currency", "inverted"),
    # Cash Index
    _q("SPX", "$SPX", "S&P 500 Index", "Cash Index"),
    _q("NDX", "$NDX", "Nasdaq 100 Index", "Cash Index"),
    # Equity Index Futures
    _q("/ES[U26]", "/ESU26", "E-mini S&P 500 future, Sep 2026", "Equity Index Futures"),
    _q("/NQ[U26]", "/NQU26", "E-mini Nasdaq 100 future, Sep 2026", "Equity Index Futures"),
    # Custom Basket / Spread (HY vs IG relative day-performance; risk-on when HY leads)
    _spread("HYG-LQD", "HYG", "LQD", "diff_pct",
            "Credit spread (high-yield minus IG)", "Custom Basket / Spread", "normal"),
    # Broad-Market ETF
    _q("SPY", "SPY", "SPDR S&P 500 ETF", _BRD),
    _q("DIA", "DIA", "SPDR Dow Jones Industrial Average ETF", _BRD),
    _q("QQQ", "QQQ", "Invesco QQQ (Nasdaq 100) ETF", _BRD),
    _q("IWM", "IWM", "iShares Russell 2000 ETF", _BRD),
    _q("RSP", "RSP", "Invesco S&P 500 Equal Weight ETF", _BRD),
    _q("QQEW", "QQEW", "First Trust Nasdaq-100 Equal Weight ETF", _BRD),
    # Factor / Momentum ETF
    _q("MTUM", "MTUM", "iShares MSCI USA Momentum Factor ETF", "Factor / Momentum ETF"),
    _q("SPMO", "SPMO", "Invesco S&P 500 Momentum ETF", "Factor / Momentum ETF"),
    # Thematic / Industry ETF
    _q("SMH", "SMH", "VanEck Semiconductor ETF", _THM),
    _q("XSD", "XSD", "SPDR S&P Semiconductor ETF", _THM),
    _q("IGV", "IGV", "iShares Expanded Tech-Software Sector ETF", _THM),
    _q("QTUM", "QTUM", "Defiance Quantum ETF", _THM),
    _q("XBI", "XBI", "SPDR S&P Biotech ETF", _THM),
    _q("XRT", "XRT", "SPDR S&P Retail ETF", _THM),
    _q("XME", "XME", "SPDR S&P Metals & Mining ETF", _THM),
    # Sector SPDR (all literal up=green — defensive sectors NOT inverted, per design)
    _q("XLC", "XLC", "Communication Services Select Sector SPDR", _SEC),
    _q("XLE", "XLE", "Energy Select Sector SPDR", _SEC),
    _q("XLF", "XLF", "Financials Select Sector SPDR", _SEC),
    _q("XLI", "XLI", "Industrials Select Sector SPDR", _SEC),
    _q("XLK", "XLK", "Technology Select Sector SPDR", _SEC),
    _q("XLP", "XLP", "Consumer Staples Select Sector SPDR", _SEC),
    _q("XLRE", "XLRE", "Real Estate Select Sector SPDR", _SEC),
    _q("XLU", "XLU", "Utilities Select Sector SPDR", _SEC),
    _q("XLV", "XLV", "Health Care Select Sector SPDR", _SEC),
    _q("XLY", "XLY", "Consumer Discretionary Select Sector SPDR", _SEC),
    # Fixed Income / Credit ETF (TLT inverted — flight-to-safety)
    _q("TLT", "TLT", "iShares 20+ Year Treasury Bond ETF", "Fixed Income / Credit ETF", "inverted"),
    _q("HYG", "HYG", "iShares iBoxx High Yield Corp Bond ETF", "Fixed Income / Credit ETF"),
    _q("LQD", "LQD", "iShares iBoxx Investment Grade Corp Bond ETF", "Fixed Income / Credit ETF"),
    # Crypto / Alternatives
    _q("GDLC", "GDLC", "Grayscale CoinDesk Crypto 5 ETF", "Crypto / Alternatives"),
    _q("VCX", "VCX", "Fundrise Innovation Fund (private venture)", "Crypto / Alternatives"),
]


def quote_symbols():
    """Deduped list of real Schwab symbols to fetch (kind=='quote' + spread legs)."""
    out = []
    for t in SYMBOL_MAP:
        if t["kind"] == "quote" and t["quote_symbol"]:
            out.append(t["quote_symbol"])
        elif t["kind"] == "spread":
            out.extend([t["spread"][0], t["spread"][1]])
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq
```

**Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_symbols.py -q`
Expected: PASS (5 passed)

**Step 6: Commit**

```bash
git add services/market_svc/__init__.py services/market_svc/symbols.py services/market_svc/tests/
git commit -m "feat(market): symbol map + category order (pure)"
```

---

## Task 3: The classify module (normalize + spread + color — pure)

**Files:**
- Create: `services/market_svc/classify.py`
- Create: `services/market_svc/tests/test_classify.py`

**Step 1: Write the failing test** `services/market_svc/tests/test_classify.py`

```python
from services.market_svc import classify as C


def test_normalize_equity_uses_net_percent_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 100.0, "netChange": 1.0, "netPercentChange": 1.0,
                   "closePrice": 99.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 100.0 and chg == 1.0 and round(pct, 3) == 1.0


def test_normalize_future_uses_future_percent_change():
    q = {"assetMainType": "FUTURE",
         "quote": {"lastPrice": 7560.75, "netChange": 9.5,
                   "futurePercentChange": 0.1258, "closePrice": 7551.25}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 7560.75 and chg == 9.5 and round(pct, 3) == 0.126


def test_normalize_internal_value_only_no_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 38.0, "netChange": 0.0, "netPercentChange": 0.0,
                   "closePrice": 0.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 38.0 and chg == 0.0 and pct == 0.0


def test_spread_diff_last():
    assert C.spread_value("diff_last", (1160.0, 0.0, 0.0), (1625.0, 0.0, 0.0)) == \
        (-465.0, -465.0, 0.0)  # (last, change, pct) — value = a.last - b.last


def test_spread_diff_pct():
    # HYG +0.5% vs LQD +0.2% → HY outperforms by +0.3
    last, chg, pct = C.spread_value("diff_pct", (81.0, 0.0, 0.5), (110.0, 0.0, 0.2))
    assert round(pct, 3) == 0.3 and round(last, 3) == 0.3


def test_color_state_normal_up_is_risk_on():
    assert C.color_state(2.0, polarity="normal") == "risk_on_strong"
    assert C.color_state(0.4, polarity="normal") == "risk_on_mild"


def test_color_state_inverted_up_is_risk_off():
    # VIX +5% → inverted → risk-off
    assert C.color_state(5.0, polarity="inverted") == "risk_off_strong"


def test_color_state_flat_and_no_data():
    assert C.color_state(0.02, polarity="normal") == "flat"
    assert C.color_state(None, polarity="normal") == "no_data"


def test_color_state_value_only_uses_sign_one_intensity():
    # a value-only internal (e.g. $TICK = +300) → mild risk-on, not "strong"
    assert C.color_state(300.0, polarity="normal", value_only=True) == "risk_on_mild"
    assert C.color_state(-300.0, polarity="normal", value_only=True) == "risk_off_mild"
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_classify.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `services/market_svc/classify.py`**

```python
"""Pure normalization + coloring for the market dashboard (no I/O)."""

_FLAT_PCT = 0.1     # |%change| below this → flat/grey
_STRONG_PCT = 1.0   # |%change| at/above this → strong intensity


def _num(v):
    try:
        f = float(v)
        return f if f == f else 0.0  # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def normalize_quote(raw):
    """(last, change, change_pct) from one raw Schwab per-symbol dict.

    Picks the % field by asset type: FUTURE → futurePercentChange, else
    netPercentChange (falling back to close-derived). Internals come back with
    close/change 0 → change_pct 0 (value-only; colored by sign upstream).
    """
    q = raw.get("quote", raw) if isinstance(raw, dict) else {}
    last = _num(q.get("lastPrice"))
    change = _num(q.get("netChange"))
    asset = (raw.get("assetMainType") or "").upper()
    if asset == "FUTURE":
        pct = _num(q.get("futurePercentChange"))
    else:
        pct = _num(q.get("netPercentChange")) or _num(q.get("netPercentChangeInDouble"))
    if pct == 0.0:
        close = _num(q.get("closePrice"))
        if close:
            pct = (last - close) / close * 100.0
    return last, change, pct


def spread_value(mode, leg_a, leg_b):
    """Compute a spread tile's (last, change, change_pct) from two legs.

    Each leg is a (last, change, pct) tuple. ``diff_last`` = a.last − b.last
    (used as both value and the color-driving 'change'); ``diff_pct`` =
    a.pct − b.pct (relative day performance).
    """
    al, _ac, ap = leg_a
    bl, _bc, bp = leg_b
    if mode == "diff_pct":
        d = ap - bp
        return d, d, d
    d = al - bl
    return d, d, 0.0


def color_state(effective_change, *, polarity="normal", value_only=False):
    """Map a signed move to a color bucket, applying polarity.

    ``effective_change`` = the % change (or value sign for value_only). Returns
    one of: risk_on_strong / risk_on_mild / flat / risk_off_mild /
    risk_off_strong / no_data. ``polarity=="inverted"`` flips green↔red (VIX up =
    risk-off). ``value_only`` collapses to a single (mild) intensity by sign.
    """
    if effective_change is None:
        return "no_data"
    signed = effective_change * (-1.0 if polarity == "inverted" else 1.0)
    if value_only:
        if signed > 0:
            return "risk_on_mild"
        if signed < 0:
            return "risk_off_mild"
        return "flat"
    mag = abs(signed)
    if mag < _FLAT_PCT:
        return "flat"
    intensity = "strong" if mag >= _STRONG_PCT else "mild"
    side = "risk_on" if signed > 0 else "risk_off"
    return f"{side}_{intensity}"
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_classify.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/market_svc/classify.py services/market_svc/tests/test_classify.py
git commit -m "feat(market): pure normalize/spread/color-state classifier"
```

---

## Task 4: The `MarketDashboard` contract

**Files:**
- Create: `shared/contracts/market.py`
- Create: `shared/contracts/tests/test_market.py`

**Step 1: Write the failing test** `shared/contracts/tests/test_market.py`

```python
from shared.contracts.market import MarketDashboard


def test_round_trip_and_defaults():
    md = MarketDashboard(
        categories=[{"category": "Volatility",
                     "tiles": [{"display": "VIX", "last": 16.1, "change_pct": 3.6,
                                "color_state": "risk_off_strong"}]}],
        proxy_up=True, timestamp="2026-07-07T12:00:00Z")
    d = md.model_dump()
    assert d["categories"][0]["category"] == "Volatility"
    assert d["proxy_up"] is True
    # defaults
    assert MarketDashboard().categories == []
    assert MarketDashboard().proxy_up is False
    # envelope-validation round trip
    assert MarketDashboard.from_json(md.to_json()).proxy_up is True
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest shared\contracts\tests\test_market.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `shared/contracts/market.py`**

```python
from .envelope import _Base


class MarketDashboard(_Base):
    """Market-dashboard payload (cache:market:dashboard).

    The single view market_svc publishes each poll tick. ``categories`` is an
    ORDERED list (frame layout order) of ``{"category": str, "tiles": [tile, …]}``
    where each tile is a display-ready dict:
    ``{display, description, category, last, change, change_pct, value_only,
       color_state, polarity, stale}``. Like the other domain contracts this
    validates the envelope container shape, not each sparse tile.
    """

    categories: list[dict] = []
    proxy_up: bool = False
    errors: list = []
    timestamp: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest shared\contracts\tests\test_market.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add shared/contracts/market.py shared/contracts/tests/test_market.py
git commit -m "feat(market): MarketDashboard contract"
```

---

## Task 5: `compute.py` — fetch raw quotes, build the dashboard

**Files:**
- Create: `services/market_svc/compute.py`
- Create: `services/market_svc/tests/test_compute.py`

`compute` hits the **raw** `/quotes` endpoint (not `SchwabProxyClient.get_quotes`, which discards `assetMainType`/`futurePercentChange` and mangles the `errors` bucket). It reads the sentiment put/call from the bus. `build_dashboard` is pure over an already-fetched raw dict + pcr so it's fully testable; `fetch_raw_quotes`/`read_sector_pcr` are the thin I/O seams.

**Step 1: Write the failing test** `services/market_svc/tests/test_compute.py`

```python
from services.market_svc import compute


def _raw():
    return {
        "$VIX": {"assetMainType": "INDEX",
                 "quote": {"lastPrice": 16.13, "netChange": 0.56, "netPercentChange": 3.6}},
        "$SPX": {"assetMainType": "INDEX",
                 "quote": {"lastPrice": 7503.85, "netChange": -33.6, "netPercentChange": -0.44}},
        "/ESU26": {"assetMainType": "FUTURE",
                   "quote": {"lastPrice": 7560.75, "netChange": 9.5,
                             "futurePercentChange": 0.126, "closePrice": 7551.25}},
        "$ADVN": {"assetMainType": "EQUITY",
                  "quote": {"lastPrice": 1160.0, "netPercentChange": 0.0, "closePrice": 0.0}},
        "$DECN": {"assetMainType": "EQUITY",
                  "quote": {"lastPrice": 1625.0, "netPercentChange": 0.0, "closePrice": 0.0}},
        "HYG": {"assetMainType": "EQUITY", "quote": {"lastPrice": 81.0, "netPercentChange": 0.5}},
        "LQD": {"assetMainType": "EQUITY", "quote": {"lastPrice": 110.0, "netPercentChange": 0.2}},
        "UUP": {"assetMainType": "EQUITY", "quote": {"lastPrice": 28.4, "netPercentChange": 0.26}},
        "errors": {"invalidSymbols": ["NOPE"]},
    }


def test_build_dashboard_shapes_categories_in_order():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    cats = [c["category"] for c in d["categories"]]
    assert cats[0] == "Volatility"          # frame order preserved
    assert d["proxy_up"] is True
    assert "errors" not in {c["category"] for c in d["categories"]}


def test_vix_tile_is_risk_off_and_spx_risk_off_down():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["VIX"]["color_state"] == "risk_off_strong"   # +3.6% inverted
    assert tiles["SPX"]["color_state"] == "risk_off_mild"     # -0.44% normal
    assert tiles["/ES[U26]"]["color_state"] == "risk_on_mild" # +0.126% future


def test_spread_tiles_computed():
    d = compute.build_dashboard(_raw(), sector_pcr=0.99, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["$ADVN-$DECN"]["last"] == -465.0            # 1160 - 1625
    assert tiles["$ADVN-$DECN"]["color_state"] == "risk_off_mild"
    assert round(tiles["HYG-LQD"]["change_pct"], 3) == 0.3   # 0.5 - 0.2
    assert tiles["HYG-LQD"]["color_state"] == "risk_on_mild"


def test_putcall_tile_from_sentiment_pcr_inverted():
    d = compute.build_dashboard(_raw(), sector_pcr=1.10, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    pc = tiles["Put/Call (cap-wt sectors)"]
    assert round(pc["last"], 2) == 1.10
    assert pc["color_state"] == "risk_off_mild"   # pcr>1 = more puts = risk-off


def test_missing_symbol_is_no_data_not_a_crash():
    raw = _raw()
    del raw["$SPX"]
    d = compute.build_dashboard(raw, sector_pcr=None, proxy_up=True)
    tiles = {t["display"]: t for c in d["categories"] for t in c["tiles"]}
    assert tiles["SPX"]["color_state"] == "no_data"
    assert tiles["Put/Call (cap-wt sectors)"]["color_state"] == "no_data"  # pcr None
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_compute.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `services/market_svc/compute.py`**

```python
"""Market dashboard compute — fetch raw quotes, build the display payload.

I/O seams (``fetch_raw_quotes`` / ``read_sector_pcr``) are thin + defensive;
``build_dashboard`` is PURE over an already-fetched raw dict + pcr so it carries
the coverage. All defensive — a fetch/parse failure degrades to no-data tiles.
"""
import logging

import requests

from repo_paths import PROXY_URL
from services.market_svc import classify, symbols

log = logging.getLogger("market_svc.compute")

CACHE_SENTIMENT = "cache:sentiment:composite"

# Baseline for the cap-weighted put/call tile: pcr>1 = more puts = risk-off.
_PCR_BASELINE = 1.0


def fetch_raw_quotes(syms, *, timeout=8.0):
    """GET the proxy's raw /quotes for ``syms``; returns the raw Schwab dict.

    Uses the raw endpoint (not SchwabProxyClient.get_quotes) so assetMainType +
    futurePercentChange survive. Never raises — returns {} on any failure.
    """
    if not syms:
        return {}
    try:
        resp = requests.get(f"{PROXY_URL}/quotes",
                            params={"symbols": ",".join(syms)}, timeout=timeout)
        if resp.status_code != 200:
            return {}
        return resp.json() or {}
    except Exception:  # noqa: BLE001
        log.warning("market /quotes fetch failed", exc_info=True)
        return {}


def read_sector_pcr(bus):
    """Cap-weighted sector put/call ratio from cache:sentiment:composite, or None."""
    try:
        env = bus.cache_get(CACHE_SENTIMENT)
        if not env:
            return None
        live = (env.payload or {}).get("live") or {}
        pcr = live.get("sector_pcr")
        return float(pcr) if pcr not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def _leg(raw, sym):
    q = raw.get(sym)
    if not q:
        return None
    return classify.normalize_quote(q)


def _tile_base(entry):
    return {"display": entry["display"], "description": entry["description"],
            "category": entry["category"], "polarity": entry["polarity"],
            "value_only": entry["value_only"]}


def build_dashboard(raw, *, sector_pcr, proxy_up):
    """Assemble the ordered categories→tiles payload (pure)."""
    tiles_by_cat = {c: [] for c in symbols.CATEGORY_ORDER}
    for e in symbols.SYMBOL_MAP:
        t = _tile_base(e)
        if e["kind"] == "quote":
            n = _leg(raw, e["quote_symbol"])
            if n is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                last, chg, pct = n
                drive = last if e["value_only"] else pct
                t.update(last=last, change=None if e["value_only"] else chg,
                         change_pct=None if e["value_only"] else pct,
                         color_state=classify.color_state(
                             drive, polarity=e["polarity"], value_only=e["value_only"]))
        elif e["kind"] == "spread":
            a, b, mode = e["spread"]
            la, lb = _leg(raw, a), _leg(raw, b)
            if la is None or lb is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                last, chg, pct = classify.spread_value(mode, la, lb)
                t.update(last=last, change=chg, change_pct=pct,
                         color_state=classify.color_state(chg, polarity=e["polarity"]))
        elif e["kind"] == "external":  # sentiment put/call
            if sector_pcr is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                dev = sector_pcr - _PCR_BASELINE
                t.update(last=sector_pcr, change=None, change_pct=None,
                         color_state=classify.color_state(
                             dev, polarity=e["polarity"], value_only=True))
        tiles_by_cat[e["category"]].append(t)

    categories = [{"category": c, "tiles": tiles_by_cat[c]}
                  for c in symbols.CATEGORY_ORDER if tiles_by_cat[c]]
    return {"categories": categories, "proxy_up": proxy_up, "errors": []}


def collect(bus):
    """Fetch + build the full dashboard payload (the scheduler's per-tick call)."""
    from services import _proxy
    raw = fetch_raw_quotes(symbols.quote_symbols())
    pcr = read_sector_pcr(bus)
    proxy_up = bool(raw) or bool(_proxy.health().get("up"))
    return build_dashboard(raw, sector_pcr=pcr, proxy_up=proxy_up)
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_compute.py -q`
Expected: PASS (6 passed)

**Step 5: Commit**

```bash
git add services/market_svc/compute.py services/market_svc/tests/test_compute.py
git commit -m "feat(market): compute — raw quote fetch + build_dashboard (pure)"
```

---

## Task 6: `handlers.py` — publish `cache:market:dashboard`

**Files:**
- Create: `services/market_svc/handlers.py`
- Create: `services/market_svc/tests/test_handlers.py`

**Step 1: Write the failing test** `services/market_svc/tests/test_handlers.py`

```python
from shared.bus import Bus
from services.market_svc import handlers


def test_publish_validates_and_caches():
    bus = Bus()  # fakeredis under pytest
    payload = {"categories": [{"category": "Volatility",
                               "tiles": [{"display": "VIX", "color_state": "flat"}]}],
               "proxy_up": True, "errors": []}
    version = handlers.publish(bus, payload)
    assert version >= 1
    env = bus.cache_get(handlers.CACHE)
    assert env.payload["categories"][0]["category"] == "Volatility"
    assert env.payload["proxy_up"] is True
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_handlers.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `services/market_svc/handlers.py`**

```python
"""Market service handlers — validate + publish the dashboard view."""
import logging

from shared.contracts.market import MarketDashboard

log = logging.getLogger("market_svc.handlers")

CACHE = "cache:market:dashboard"
EVENT = "events:market:dashboard"


def publish(bus, payload) -> int:
    """Validate against MarketDashboard and cache+publish. Returns the version."""
    md = MarketDashboard(**payload)
    return bus.cache_set(CACHE, md.model_dump(), event=EVENT, skip_unchanged=True)
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_handlers.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/market_svc/handlers.py services/market_svc/tests/test_handlers.py
git commit -m "feat(market): handlers — publish cache:market:dashboard"
```

---

## Task 7: `scheduler.py` — RTH-gated ~2 s poll loop

**Files:**
- Create: `services/market_svc/scheduler.py`
- Create: `services/market_svc/tests/test_scheduler.py`

Mirrors the other schedulers' market-hours gate. The pure `poll_interval(now)` is tested; the `loop` coroutine (its own `while True`) is wired into the scaffold and covered by the app smoke test.

**Step 1: Write the failing test** `services/market_svc/tests/test_scheduler.py`

```python
import datetime as dt
from zoneinfo import ZoneInfo

from services.market_svc import scheduler as sch

_CT = ZoneInfo("America/Chicago")


def test_fast_cadence_during_rth():
    now = dt.datetime(2026, 7, 7, 10, 0, tzinfo=_CT)  # Tue 10:00 CT
    assert sch.poll_interval(now) == sch.RTH_INTERVAL_SEC


def test_slow_cadence_off_hours():
    now = dt.datetime(2026, 7, 7, 22, 0, tzinfo=_CT)  # Tue 22:00 CT
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_weekend():
    now = dt.datetime(2026, 7, 11, 10, 0, tzinfo=_CT)  # Sat
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC


def test_slow_cadence_on_holiday():
    now = dt.datetime(2026, 7, 3, 10, 0, tzinfo=_CT)  # holiday in _HOLIDAYS
    assert sch.poll_interval(now) == sch.OFFHOURS_INTERVAL_SEC
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_scheduler.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `services/market_svc/scheduler.py`**

```python
"""Market dashboard scheduler — poll the proxy, publish, repeat.

~2 s cadence during regular trading hours; throttled to ~15 s off-hours/
weekends/holidays (indices/internals are stale then; futures still move but a
glance-cadence suffices). The market-hours gate mirrors the other services.
"""
import asyncio
import datetime as _dt
import logging
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from services.market_svc import compute, handlers

_log = logging.getLogger("market_svc.scheduler")

RTH_INTERVAL_SEC = 2
OFFHOURS_INTERVAL_SEC = 15

_CT = ZoneInfo("America/Chicago")
_RTH_START = (8, 30)
_RTH_END = (15, 0)
# Keep in sync with the other service schedulers + webgui/alerts.py. Update yearly.
_HOLIDAYS = {
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
}


def _is_rth(now):
    if now.weekday() >= 5 or now.date() in _HOLIDAYS:
        return False
    return _time(*_RTH_START) <= now.time() <= _time(*_RTH_END)


def poll_interval(now=None):
    """Seconds until the next poll — fast during RTH, slow off-hours (pure)."""
    now = now or _dt.datetime.now(_CT)
    return RTH_INTERVAL_SEC if _is_rth(now) else OFFHOURS_INTERVAL_SEC


async def loop(bus) -> None:
    """Poll → publish → sleep(poll_interval), forever. Never raises out."""
    loop_ = asyncio.get_event_loop()
    while True:
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        await asyncio.sleep(poll_interval())
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_scheduler.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/market_svc/scheduler.py services/market_svc/tests/test_scheduler.py
git commit -m "feat(market): scheduler — RTH-gated poll cadence"
```

---

## Task 8: `app.py` — assemble the runnable service

**Files:**
- Create: `services/market_svc/app.py`
- Create: `services/market_svc/tests/test_app.py`

**Step 1: Write the failing test** `services/market_svc/tests/test_app.py`

```python
from fastapi.testclient import TestClient


def test_app_health():
    from services.market_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "market"
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_app.py -q`
Expected: FAIL (ModuleNotFoundError)

**Step 3: Write `services/market_svc/app.py`**

```python
"""Runnable market dashboard service (port 8215).

Read-only: a scheduler polls the proxy for ~48 macro symbols and publishes
cache:market:dashboard. No command handler (the page only reads). Importable
without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.market_svc import scheduler  # noqa: E402

app = make_app("market", scheduler=scheduler.loop)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["market"])
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_app.py -q`
Expected: PASS (the scheduler loop starts, does one poll against no proxy → degrades, harmless in-test)

Then run the whole service suite:
Run: `.venv\Scripts\python -m pytest services\market_svc -q`
Expected: PASS (all)

**Step 5: Commit**

```bash
git add services/market_svc/app.py services/market_svc/tests/test_app.py
git commit -m "feat(market): runnable app.py (:8215)"
```

---

## Task 9: The webgui page (`pages/market.py`)

**Files:**
- Create: `webgui/pages/market.py`
- Create: `webgui/tests/test_market.py`

Tier-1, engine-free (imports only `nicegui` + `bus_client`). Pure builders (`bg_class`, `frame_columns`, `tile_text`) are tested; `render()` is thin (build once, version-poll, repaint in place). Color is a **finite→fixed-Tailwind-class map** (Tailwind-first standard).

**Step 1: Write the failing test** `webgui/tests/test_market.py`

```python
from pages import market


def test_bg_class_maps_every_state():
    for state in ("risk_on_strong", "risk_on_mild", "flat",
                  "risk_off_mild", "risk_off_strong", "no_data"):
        cls = market.bg_class(state)
        assert isinstance(cls, str) and cls  # non-empty fixed class
    # green vs red are distinct
    assert market.bg_class("risk_on_strong") != market.bg_class("risk_off_strong")
    # unknown → neutral fallback
    assert market.bg_class("bogus") == market.bg_class("no_data")


def test_tile_text_formats_last_and_change():
    t = {"display": "VIX", "last": 16.13, "change": None, "change_pct": 3.6,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "16.13"
    assert "3.6" in txt["change"] and "%" in txt["change"]


def test_tile_text_value_only_hides_change():
    t = {"display": "$TICK", "last": 300.0, "change": None, "change_pct": None,
         "value_only": True}
    txt = market.tile_text(t)
    assert txt["last"] == "300"
    assert txt["change"] == ""      # no change line for value-only


def test_tile_text_no_data():
    t = {"display": "SPX", "last": None, "change_pct": None, "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "—"
```

**Step 2: Run test to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests\test_market.py -q ; cd ..`
Expected: FAIL (ModuleNotFoundError: pages.market)

**Step 3: Write `webgui/pages/market.py`**

```python
"""Market Dashboard page (/market) — Tier-1, engine-free.

Reads cache:market:dashboard (published by market_svc), renders one framed
panel per category with tiles colored by risk-on/off condition. Repaints in
place on the ~2 s version bump. Tailwind-first: data-driven colors map from the
finite color_state set to fixed background classes (no .style()).
"""
import bus_client
from pages.ui_guard import guard
from nicegui import ui

VIEW = "market:dashboard"

# color_state → fixed Tailwind background + text classes (finite map, Tailwind-first).
_BG = {
    "risk_on_strong": "bg-emerald-600/80 text-white",
    "risk_on_mild": "bg-emerald-500/25 text-emerald-100",
    "flat": "bg-slate-600/30 text-slate-200",
    "risk_off_mild": "bg-rose-500/25 text-rose-100",
    "risk_off_strong": "bg-rose-600/80 text-white",
    "no_data": "bg-slate-700/40 text-slate-400",
}


def bg_class(state):
    """Fixed Tailwind bg/text classes for a color_state (neutral fallback)."""
    return _BG.get(state, _BG["no_data"])


def _fmt(v, nd=2):
    try:
        f = float(v)
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def tile_text(t):
    """Display strings for a tile: {last, change}."""
    if t.get("last") is None:
        return {"last": "—", "change": ""}
    if t.get("value_only"):
        return {"last": _fmt(t["last"], 0), "change": ""}
    last = _fmt(t["last"])
    pct = t.get("change_pct")
    chg = t.get("change")
    parts = []
    if chg is not None:
        parts.append(f"{'+' if chg >= 0 else ''}{_fmt(chg)}")
    if pct is not None:
        parts.append(f"{'+' if pct >= 0 else ''}{_fmt(pct)}%")
    return {"last": last, "change": "  ".join(parts)}


def render():
    ui.label("Market Dashboard").classes("text-h6 text-slate-100")
    board = ui.row().classes("w-full flex-wrap gap-4 items-start")
    state = {"version": None}

    def _paint(payload):
        board.clear()
        with board:
            for cat in payload.get("categories", []):
                with ui.column().classes(
                        "rounded-lg border border-slate-700 bg-slate-900/40 p-3 gap-2"):
                    ui.label(cat["category"]).classes(
                        "text-xs uppercase tracking-wide text-slate-400")
                    with ui.row().classes("flex-wrap gap-2"):
                        for t in cat["tiles"]:
                            txt = tile_text(t)
                            with ui.column().classes(
                                    f"rounded-md p-2 w-[120px] gap-0 {bg_class(t['color_state'])}"):
                                ui.label(t["display"]).classes("text-sm font-semibold truncate")
                                ui.label(txt["last"]).classes("text-base font-bold")
                                if txt["change"]:
                                    ui.label(txt["change"]).classes("text-xs")

    @guard
    def _poll():
        v = bus_client.read_version(VIEW)
        if v is None:
            return
        if v != state["version"]:
            payload = bus_client.read(VIEW)
            if payload:
                state["version"] = v
                _paint(payload)

    payload = bus_client.read(VIEW)
    if payload:
        state["version"] = bus_client.read_version(VIEW)
        _paint(payload)
    else:
        with board:
            ui.label("Waiting for the market service…").classes("text-slate-400")
    ui.timer(2.0, _poll)
```

**Step 4: Run test to verify it passes**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests\test_market.py -q ; cd ..`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/market.py webgui/tests/test_market.py
git commit -m "feat(market): webgui Market Dashboard page"
```

---

## Task 10: Wire the nav item + route

**Files:**
- Modify: `webgui/main.py` (add to `MORE_CHILDREN`; add the `@ui.page` route)
- Modify: `webgui/tests/test_shell.py` (expected route set)
- Modify: `webgui/tests/test_no_inline_style.py` (guard the new page)

**Step 1: Add to nav** — in `webgui/main.py`, `MORE_CHILDREN`, add before EOD (macro/market sits with the other views):

```python
MORE_CHILDREN = [
    ("/market", "Market Dashboard", "dashboard"),
    ("/eod", "EOD Report", "summarize"),
    ("/status", "System Status", "monitor_heart"),
    ("/settings", "Settings", "settings"),
    ("/terminate", "Terminate", "power_settings_new"),
]
```

**Step 2: Add the route** — near the other `@ui.page` blocks in `webgui/main.py` (e.g. after the `/status` page):

```python
@ui.page("/market")
def market_page() -> None:
    with _layout("/market", "Market Dashboard"):
        from pages import market
        market.render()
```

**Step 3: Update the shell test** — in `webgui/tests/test_shell.py`, add `"/market"` to the expected registered-routes set (find the set literal listing `/status`, `/eod`, … and add `/market`).

**Step 4: Update the inline-style guard** — in `webgui/tests/test_no_inline_style.py`, add `market.py` to the list of guarded page files (mirror how `status.py` is listed).

**Step 5: Run the webgui suite**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest -q ; cd ..`
Expected: PASS (all — incl. test_shell + test_no_inline_style + test_market)

**Step 6: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py webgui/tests/test_no_inline_style.py
git commit -m "feat(market): wire /market nav item + route"
```

---

## Task 11: Add `market_svc` to the launch scripts

**Files:**
- Modify: `start_all.bat`
- Modify: `start_all_wt.bat`

**Step 1:** In `start_all.bat`, add a line to launch `market_svc` after `driver_svc` (copy the driver line, swap the path/title). It must wait for the proxy like the others (uses the same `wait_and_run.bat 8100` pattern already present in the file).

**Step 2:** In `start_all_wt.bat`, add an 8th tab for `services\market_svc\app.py` mirroring the driver tab (`wait_and_run.bat 8100 services\market_svc\app.py`).

**Step 3: Verify the service starts standalone** (proxy must be up on :8100):

Run: `.venv\Scripts\python services\market_svc\app.py` (in a separate console), then:
Run: `curl -s http://127.0.0.1:8215/health`
Expected: `{"domain":"market","up":true,...}`

**Step 4: Commit**

```bash
git add start_all.bat start_all_wt.bat
git commit -m "feat(market): launch market_svc in start_all scripts"
```

---

## Task 12: Live end-to-end verification + docs

**Files:**
- Modify: `CLAUDE.md` (root — add a Market Dashboard entry + the `/market` route row)
- Modify: `config/ports.toml` reference in the "Paths and ports" section of `CLAUDE.md` (add `market = 8215`)

**Step 1: Redis-driven end-to-end** (proxy + market_svc + Memurai up). Confirm the service published a real snapshot:

Run:
```
.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); from shared.bus import Bus; b=Bus(); e=b.cache_get('cache:market:dashboard'); import json; p=e.payload; print('cats', [c['category'] for c in p['categories']]); print('vix', [t for c in p['categories'] for t in c['tiles'] if t['display']=='VIX'])"
```
Expected: the ordered category list + a VIX tile with a real `last` + a `color_state` (e.g. `risk_off_*`). Confirm `$ADVN-$DECN` and `HYG-LQD` tiles have numeric `last`, and the `Put/Call (cap-wt sectors)` tile has a `last` near the sentiment `sector_pcr` (or `no_data` if sentiment_svc isn't running).

**Step 2: Browser verification** — start the `webgui` preview server (`.claude/launch.json` → `webgui`, :8500), navigate to `/market`, and screenshot. Confirm: framed panels in the approved order (Volatility/Options-Sentiment/Internals/Currency on top), tiles colored (green/red/grey), values populated, no console errors. If the screenshot tool times out on the dense grid, verify via `preview_snapshot` / `preview_inspect` on a couple of tiles' background classes instead.

**Step 3: Update `CLAUDE.md`** — add a route-table row for `/market` and a short "Market Dashboard — DONE" section summarizing: new `market_svc` (:8215), `cache:market:dashboard`, ~2 s RTH poll, semantic risk-on/off coloring, the CSV→Schwab symbol map + the `$DXY→UUP` / put/call-from-sentiment equivalents, and the design/plan doc links. Add `market = 8215` to the ports block.

**Step 4: Final full-suite sanity**

Run: `.venv\Scripts\python -m pytest services\market_svc -q` → PASS
Run: `.venv\Scripts\python -m pytest shared\contracts -q` → PASS
Run: `cd webgui ; ..\.venv\Scripts\python -m pytest -q ; cd ..` → PASS

**Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(market): Market Dashboard shipped — CLAUDE.md + route table"
```

---

## Notes for the implementer

- **Run service tests per-folder** (`services\market_svc`), never `pytest services` over all of them (the documented cross-app module-name collision).
- `shared.bus.Bus()` is **fakeredis under pytest** automatically — no Redis needed for the unit tests.
- The webgui is **Tailwind-first**: never use `.style()`. The `bg_class` map is the pattern for data-driven color (finite set → fixed class).
- All service code is **defensive** (never raises out of a poll cycle / handler) — match the existing `except Exception: log` discipline; a missing symbol or a down sentiment service degrades to grey `no_data` tiles, never a crash.
- `market_svc` needs **no command handler** — it's read-only (the page never enqueues), so `make_app("market", scheduler=scheduler.loop)` (no `command_handler=`).
- The `/status` page enumerates services from `SERVICE_URLS`, so `market` appears on the health board automatically once Task 1 lands (no status-page code change).
