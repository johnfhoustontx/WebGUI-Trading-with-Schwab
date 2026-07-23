# Market Regime — blended classifier — implementation plan (Phase 1)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task. Read the companion design first:
> [2026-07-23-market-regime-blended-classifier-design.md](2026-07-23-market-regime-blended-classifier-design.md)

**Goal:** Ship Phase 1 of the soft-membership structural regime classifier — pure
scoring modules, sentiment_svc 5-min compute + crisis fast path, SQLite recording,
`cache:sentiment:regime` publish, Sentiment-page regime panel, driver context line.
**Context-only** — no gate, no tilt, no sizing change.

**Architecture:** PURE evidence→membership math in `sentiment-dashboard/scoring/`
(TDD'd, no I/O), orchestration in `services/sentiment_svc` (own 5-min slot +
crisis re-check inside the existing 120 s refresh), additive `RegimeState` contract,
Tier-1 webgui reader. Dealer-gamma evidence via a defensive `cache:options:matrix`
read (never a cross-service import).

**Tech stack:** Python 3.11, pytest, fakeredis (via `shared/bus` under pytest),
NiceGUI + Highcharts (plain chart — NOT stockChart), SQLite.

**House rules that bind every task:** TDD (failing test first); tests run per-folder
(`cd sentiment-dashboard; ..\.venv\Scripts\python -m pytest tests` — services from the
repo root: `.venv\Scripts\python -m pytest services\sentiment_svc`); ruff clean before
each commit; webgui pages are Tailwind-first (no `.style()`); every compute is
defensive (degrade, never raise); commit after each green task.

---

## Task 1: `scoring/volatility.py` — ATR + Bollinger width (pure)

**Files:**
- Create: `sentiment-dashboard/scoring/volatility.py`
- Test: `sentiment-dashboard/tests/test_volatility.py`

**Step 1 — failing test** (`tests/test_volatility.py`):

```python
import numpy as np
from scoring import volatility as V


def test_atr_matches_wilder_hand_calc():
    h = [12, 12.5, 13, 12.8, 13.2]; l = [11, 11.5, 12, 12.2, 12.6]
    c = [11.5, 12.2, 12.5, 12.6, 13.0]
    # TR = [1.0, 1.0, 1.0, 0.6, 0.6]; ATR-3 (Wilder RMA, SMA seed) hand-computed:
    # seed = mean(1.0,1.0,1.0)=1.0; then (1.0*2+0.6)/3=0.8667; (0.8667*2+0.6)/3=0.7778
    atr = V.atr(h, l, c, n=3)
    assert abs(atr - 0.7778) < 1e-3


def test_atr_insufficient_bars_returns_none():
    assert V.atr([1, 2], [1, 2], [1, 2], n=14) is None


def test_bollinger_width_pct():
    closes = [100.0] * 19 + [100.0]          # zero variance → width 0
    assert V.bollinger_width_pct(closes, n=20) == 0.0
    closes2 = list(np.linspace(95, 105, 20))  # rising → positive width
    assert V.bollinger_width_pct(closes2, n=20) > 0


def test_percentile_of_last():
    hist = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10.0]
    assert V.percentile_of_last(hist + [10.5]) == 1.0      # new max
    assert abs(V.percentile_of_last(hist + [5.5]) - 0.5) < 0.1
    assert V.percentile_of_last([1.0]) is None             # too thin
```

**Step 2 — run, expect FAIL** (`ModuleNotFoundError: scoring.volatility`):
`cd sentiment-dashboard; ..\.venv\Scripts\python -m pytest tests\test_volatility.py -q`

**Step 3 — implement** `scoring/volatility.py` (pure, list/ndarray in → float|None out):

```python
"""ATR + Bollinger-width helpers for the market-regime classifier. PURE."""
import numpy as np


def atr(highs, lows, closes, n=14):
    """Wilder's ATR (RMA with SMA seed — same smoothing family as the house RSI/ADX).
    Needs n+? bars; returns None when too thin."""
    h, l, c = np.asarray(highs, float), np.asarray(lows, float), np.asarray(closes, float)
    if len(c) < n + 1 and len(c) < n:
        ...  # TR needs a prior close for bars 1..; accept len>=n, first TR = h-l
    if len(c) < n:
        return None
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(abs(h - prev_c), abs(l - prev_c)))
    a = tr[:n].mean()
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return float(a)


def bollinger_width_pct(closes, n=20, k=2.0):
    """(upper−lower)/middle as a fraction; None when < n bars or middle<=0."""
    c = np.asarray(closes, float)
    if len(c) < n:
        return None
    w = c[-n:]
    mid = w.mean(); sd = w.std(ddof=0)
    if mid <= 0:
        return None
    return float(2.0 * k * sd / mid)


def percentile_of_last(values, min_n=10):
    """Rank of the LAST value within the whole list, 0..1. None when < min_n."""
    v = [x for x in values if x is not None]
    if len(v) < min_n:
        return None
    last = v[-1]
    return float(sum(1 for x in v[:-1] if x <= last) / (len(v) - 1))
```

(Resolve the `atr` bar-count edge in code: require `len >= n`, first TR uses `h-l`.)

**Step 4 — run, expect PASS.** Adjust the hand-calc tolerance only if your seed
convention differs — document whichever convention ships in the docstring.

**Step 5 — ruff + commit:**
`..\.venv\Scripts\python -m ruff check scoring\volatility.py tests\test_volatility.py`
`git add -A && git commit -m "feat(regime): pure ATR + Bollinger-width helpers"`

---

## Task 2: `scoring/market_regime.py` — ramps + per-regime evidence → raw intensities

**Files:**
- Create: `sentiment-dashboard/scoring/market_regime.py`
- Test: `sentiment-dashboard/tests/test_market_regime.py`

The evidence contract (one flat dict; every key OPTIONAL — None drops that input):

```python
EVIDENCE_KEYS = {
    "adx", "adx_rising",            # float, bool — ADX-14 on 5-min bars
    "ema_slope_atr",                # EMA20 slope per bar, in ATR units (signed)
    "bb_width_pctile",              # 0..1 percentile vs trailing sessions
    "bb_width_expansion",           # width now / width 30-min ago (ratio)
    "band_hug_frac",                # fraction of last 12 closes in outer BB quartile
    "vwap_hold_frac",               # fraction of session on one side of VWAP (0.5..1)
    "or_break_state",               # "held" | "failed" | "none"
    "or_failed_count",              # int — break-then-recross count today
    "wick_two_sided",               # 0..1 from rejection_defense both directions
    "whipsaw_count",                # EMA20 cross count today
    "profile_balance",              # 0..1 from profile_shape (1 = balanced single-HVN)
    "rel_vol",                      # relative volume
    "atr_pctile",                   # 0..1
    "vix1d_spike_pct",              # day-over-day %
    "term_inversion",               # 0..1 depth (VIX1D>VIX, VIX>VIX3M scaled)
    "gap_open_pct", "gap_filled",   # float, bool
    "above_flip", "below_flip_deep",  # bool, 0..1 (matrix read; None when stale)
}
```

**Step 1 — failing tests** (representative; write one per regime + edges):

```python
from scoring import market_regime as MR


def test_ramp_edges():
    assert MR.ramp(17, 18, 30) == 0.0
    assert MR.ramp(30, 18, 30) == 1.0
    assert abs(MR.ramp(24, 18, 30) - 0.5) < 1e-9


def _quiet_range_day():
    return {"adx": 14, "adx_rising": False, "ema_slope_atr": 0.02,
            "bb_width_pctile": 0.45, "bb_width_expansion": 1.0,
            "band_hug_frac": 0.1, "vwap_hold_frac": 0.55, "or_break_state": "none",
            "or_failed_count": 0, "wick_two_sided": 0.1, "whipsaw_count": 2,
            "profile_balance": 0.9, "rel_vol": 0.9, "atr_pctile": 0.3,
            "vix1d_spike_pct": -2.0, "term_inversion": 0.0,
            "gap_open_pct": 0.1, "gap_filled": True,
            "above_flip": True, "below_flip_deep": 0.0}


def test_quiet_range_day_scores_mean_reversion_dominant():
    s = MR.score_regimes(_quiet_range_day())
    assert max(s.raw, key=s.raw.get) == "mean_reversion"
    assert s.raw["crisis"] < 0.1 and s.raw["trending"] < 0.35


def test_trend_day_scores_trending_dominant():
    ev = _quiet_range_day() | {"adx": 32, "adx_rising": True, "ema_slope_atr": 0.4,
                               "band_hug_frac": 0.8, "vwap_hold_frac": 0.95,
                               "or_break_state": "held", "profile_balance": 0.2}
    s = MR.score_regimes(ev)
    assert max(s.raw, key=s.raw.get) == "trending"


def test_single_crisis_tell_is_sufficient():          # crisis = max(), not avg
    ev = _quiet_range_day() | {"vix1d_spike_pct": 40.0}
    assert MR.score_regimes(ev).raw["crisis"] >= 0.9


def test_missing_inputs_drop_out_not_default():
    ev = {k: None for k in _quiet_range_day()}
    s = MR.score_regimes(ev)
    assert all(v == 0.0 for v in s.raw.values())
    assert s.unclear is True


def test_memberships_normalize_and_confidence_is_max_raw():
    s = MR.score_regimes(_quiet_range_day())
    assert abs(sum(s.memberships.values()) - 1.0) < 1e-9
    assert s.confidence == max(s.raw.values())
```

**Step 2 — run, expect FAIL.**

**Step 3 — implement.** Structure:

```python
REGIMES = ("mean_reversion", "trending", "breakout", "choppy", "crisis")
UNCLEAR_FLOOR = 0.25

@dataclass
class RegimeScores:
    raw: dict; memberships: dict; confidence: float; unclear: bool; evidence: list

def ramp(x, lo, hi): ...
def _wavg(pairs):
    """[(value_or_None, weight)] → confidence-weighted mean over PRESENT values;
    0.0 when nothing present (the missing-input rule)."""

def _mean_reversion(ev): ...   # avg of the design's ramps; above_flip bonus weight 0.15
def _trending(ev): ...
def _breakout(ev): ...         # multiplicative (squeeze × expansion × vol × OR)
def _choppy(ev): ...
def _crisis(ev): ...           # max() over its ramps

def score_regimes(ev) -> RegimeScores: ...
```

Use the exact ramps/thresholds from the design's tunables table as module constants.
`evidence` on the result = human strings for the popup ("ADX 32 rising", "VIX1D +40%").

**Step 4 — run, PASS. Step 5 — ruff + commit** `feat(regime): per-regime evidence ramps → raw intensities + memberships`.

---

## Task 3: `market_regime.py` — smoothing, transition, commit, crisis attack

**Files:** same module + test file.

**Step 1 — failing tests:**

```python
def test_alpha_from_half_life_wall_clock():
    # after exactly one half-life the old value's weight is 0.5
    a = MR.alpha(dt_sec=900, half_life_min=15)
    assert abs((1 - a) - 0.5) < 1e-9


def test_smooth_converges_and_lags():
    fast = slow = {r: 0.2 for r in MR.REGIMES}
    target = dict(fast, trending=0.8, mean_reversion=0.05)  # renormalized inside
    for _ in range(12):                                     # 12 × 5 min = 1 h
        fast, slow = MR.smooth(fast, slow, target, dt_sec=300)
    assert fast["trending"] > slow["trending"] > 0.3


def test_transition_reports_from_to_progress():
    fast = {"mean_reversion": 0.3, "trending": 0.5, "breakout": 0.05,
            "choppy": 0.1, "crisis": 0.05}
    slow = {"mean_reversion": 0.5, "trending": 0.3, "breakout": 0.05,
            "choppy": 0.1, "crisis": 0.05}
    t = MR.detect_transition(fast, slow)
    assert (t["from"], t["to"]) == ("mean_reversion", "trending")
    assert 0 < t["progress"] <= 1
    assert MR.detect_transition(fast, fast) is None          # no divergence → stable


def test_commit_label_needs_margin_for_n_reads():
    st = MR.CommitState(committed="mean_reversion", streak=0)
    fast = {"mean_reversion": 0.31, "trending": 0.44, "breakout": 0.05,
            "choppy": 0.15, "crisis": 0.05}                  # margin 0.13 > 0.10
    st = MR.commit_label(fast, st)
    assert st.committed == "mean_reversion" and st.streak == 1   # read 1: hold
    st = MR.commit_label(fast, st)
    assert st.committed == "trending"                            # read 2: flip


def test_crisis_attack_bypasses_smoothing():
    fast = {r: 0.2 for r in MR.REGIMES}
    out = MR.apply_crisis_attack(fast, raw_crisis=0.85)
    assert out["crisis"] >= 0.85                       # immediate
    assert MR.apply_crisis_attack(fast, raw_crisis=0.5) == fast  # below CRISIS_ATTACK
```

**Step 2 — FAIL. Step 3 — implement** (`alpha = 1 − 0.5**(dt/half_life)`;
`smooth` renormalizes the sample then EMAs both vectors; `detect_transition` floors
divergence at `TRANSITION_FLOOR = 0.05`, `progress = clamp(div/0.25, 0, 1)`;
`CommitState` dataclass, `COMMIT_MARGIN = 0.10`, `COMMIT_READS = 2`; crisis attack sets
`crisis = max(fast, raw)` then renormalizes and also force-commits the label).

**Step 4 — PASS. Step 5 — ruff + commit** `feat(regime): wall-clock EMA smoothing + transition + commit + crisis attack`.

---

## Task 4: bar-derived evidence assemblers (pure, same module)

`score_regimes` takes the flat dict; something must build it from bars. Keep that pure
too, so the service compute stays thin.

**Files:** same module + tests.

**Step 1 — failing tests** for `evidence_from_bars(bars_5m, daily, vix, matrix_row,
prior_widths)`:

```python
def test_evidence_from_bars_happy_path():
    bars = _make_trend_bars()          # helper: 60 synthetic rising 5-min OHLCV bars
    ev = MR.evidence_from_bars(bars, daily=_make_daily(), vix=_vix_quiet(),
                               matrix_row={"gex_regime": "above"}, prior_widths=[...])
    assert ev["adx"] > 25 and ev["band_hug_frac"] > 0.5 and ev["above_flip"] is True


def test_evidence_defensive_on_thin_bars():
    ev = MR.evidence_from_bars(_make_trend_bars()[:5], daily=None, vix=None,
                               matrix_row=None, prior_widths=[])
    assert ev["adx"] is None and ev["above_flip"] is None   # absent, not defaulted
```

**Step 2 — FAIL. Step 3 — implement**, REUSING the existing pure modules — do NOT
re-derive: ADX via `technical.calculate_adx` (import the standalone
`shared/analysis_lib/technical.py` the way `trade_svc` does), VWAP-hold + OR state via
`scoring.session_structure`, wicks via `scoring.rejection_defense`, balance via
`scoring.profile_shape`, ATR/BB via Task 1's `volatility`. Band-hug, whipsaw count,
failed-OR count are small local helpers here. `matrix_row` is the already-extracted
per-symbol dict (the service passes it) — this function never touches Redis.

**Step 4 — PASS. Step 5 — ruff + commit** `feat(regime): evidence assembly from 5-min bars (reuses session/rejection/profile modules)`.

---

## Task 5: contract — `RegimeState`

**Files:**
- Modify: `shared/contracts/sentiment.py` (additive)
- Test: `shared/contracts/tests/test_sentiment.py`

**Step 1 — failing test:**

```python
def test_regime_state_roundtrip():
    d = {"ts": "2026-07-23T10:05:00-05:00", "as_of": "2026-07-23T10:05:00-05:00",
         "memberships": {"mean_reversion": 0.5, "trending": 0.3, "breakout": 0.05,
                         "choppy": 0.1, "crisis": 0.05},
         "raw": {...same keys...}, "confidence": 0.6, "unclear": False,
         "label": "Mean Reversion", "committed_label": "Mean Reversion",
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.4},
         "evidence": ["ADX 24", "VWAP held 78%"]}
    assert RegimeState(**d)


def test_regime_state_transition_optional():
    ...  # transition=None valid; unknown regime key in memberships → ValidationError
```

**Step 2 — FAIL. Step 3 — implement** following the sibling contract idiom (pydantic
`_Base`, exact-5-key check on `memberships`/`raw`). **Step 4 — PASS.**
Run from repo root: `.venv\Scripts\python -m pytest shared\contracts -q`.
**Step 5 — commit** `feat(contracts): additive RegimeState`.

---

## Task 6: recording — `regime_intraday` table

**Files:**
- Modify: `services/sentiment_svc/intraday_history_db.py`
- Test: `services/sentiment_svc/tests/test_intraday_history_db.py`

**Step 1 — failing tests:** `insert_regime_point(conn, ts, memberships, confidence,
label)` + `load_regime_recent(conn, n_days=1)` + `prune_regime(conn, n_days=30)`
(30 sessions, NOT the sentiment 5 — this is tuning data). Assert the pytest `:memory:`
default still applies (the existing autouse fixture covers the file — extend its
assertion to the new table).

**Step 2 — FAIL. Step 3 — implement:** one table
`regime_intraday(ts INTEGER PRIMARY KEY, mr REAL, tr REAL, bo REAL, ch REAL, cr REAL,
confidence REAL, label TEXT)` created in the same `_SCHEMA` script (CREATE IF NOT
EXISTS → idempotent migration). **Step 4 — PASS. Step 5 — commit**
`feat(regime): regime_intraday recording table (30-session window)`.

---

## Task 7: service compute — `compute_market_regime`

**Files:**
- Modify: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_compute_regime.py` (new file)

**Step 1 — failing tests** (fake schwab client returning canned candles; fake matrix
payload; no network):

```python
def test_compute_market_regime_returns_contract_shape():
    out = compute.compute_market_regime(FakeSchwab(), matrix=FAKE_MATRIX,
                                        vix=FAKE_VIX, prior=None, now=NOW)
    RegimeState(**out)                      # validates
    assert out["transition"] is None or {"from", "to", "progress"} <= set(out["transition"])


def test_compute_market_regime_never_raises():
    out = compute.compute_market_regime(BrokenSchwab(), matrix=None, vix=None,
                                        prior=None, now=NOW)
    assert out["unclear"] is True            # degrades, never raises


def test_prior_state_threads_smoothing():
    a = compute.compute_market_regime(FakeSchwab(), ..., prior=None, now=NOW)
    b = compute.compute_market_regime(FakeSchwab(trending=True), ..., prior=a,
                                      now=NOW + 300)
    assert b["memberships"]["trending"] > a["memberships"]["trending"]
```

**Step 2 — FAIL. Step 3 — implement:**

- `_fetch_spy_5m(schwab)` — SPY 5-min today + daily ~10 sessions via
  `get_intraday_history`/`get_daily_history`, **memoized with a 240 s TTL**
  (module-level `(ts, frames)` latch) so the 15-min trend recompute can share it.
- `compute_market_regime(schwab, matrix, vix, prior, now=None)`:
  extract SPY/$SPX row from the matrix payload (staleness-gated: matrix `ts` older
  than 5 min → `matrix_row=None`), `evidence_from_bars(...)`, `score_regimes`,
  `smooth`/`detect_transition`/`commit_label` threaded off `prior` (the caller-held
  state dict), crisis attack applied, return the contract-shaped dict. Whole body
  try/except → an `unclear=True` shell. Prior-widths history for the BB percentile
  comes from the recorded `regime_intraday`? No — keep it self-contained: compute the
  width series from the fetched daily+intraday frames (YAGNI).

**Step 4 — PASS** (`.venv\Scripts\python -m pytest services\sentiment_svc -q` — whole
folder stays green). **Step 5 — commit** `feat(regime): sentiment_svc compute_market_regime (TTL-shared SPY fetch, defensive)`.

---

## Task 8: scheduler slot + handlers + crisis fast path

**Files:**
- Modify: `services/sentiment_svc/scheduler.py`, `services/sentiment_svc/handlers.py`
- Test: `services/sentiment_svc/tests/test_scheduler.py`, `tests/test_handlers.py`

**Step 1 — failing tests:**

```python
def test_regime_due_every_5_min_rth_only():
    # 10:02 CT trading day, last slot 10:00 → not due; 10:05 → due; Saturday → never;
    # off-hours → never (last committed state persists).
def test_run_regime_publishes_and_records(bus):
    # fake compute → handlers.run_regime(bus) writes cache:sentiment:regime
    # (skip_unchanged) + a regime_intraday row + cache:sentiment:regime_history.
def test_crisis_check_republishes_on_attack(bus):
    # handlers.run_crisis_check(bus, vix=SPIKED_VIX) with a held non-crisis state
    # → immediate re-publish with crisis dominant; quiet vix → no write.
def test_refresh_survives_regime_failure(bus):
    # run_regime raising is swallowed (best-effort, like run_flow_alerts).
```

**Step 2 — FAIL. Step 3 — implement:**

- `scheduler.py`: `REGIME_INTERVAL_SEC = 300`; `regime_due(now, last_slot)` (RTH
  Mon–Fri 08:30–15:00 CT slot gate, mirrors `sectors_due`'s shape); call it from the
  existing 120 s loop — a 5-min slot checked on a 120 s tick fires within ≤2 min of
  the boundary, which is fine (document that; do NOT add a second loop).
- `handlers.py`: module `_REGIME = {"state": None}` + `_REGIME_LOCK`;
  `run_regime(bus)` → read `cache:options:matrix` + reuse the refresh's VIX quotes →
  `compute_market_regime(prior=_REGIME["state"])` → hold, record
  (`insert_regime_point` under `_INTRADAY_LOCK`), publish `cache:sentiment:regime`
  (validated, `skip_unchanged=True`) + today's points as
  `cache:sentiment:regime_history`. `run_crisis_check(bus, vix)` — cheap: re-ramp only
  the crisis inputs against the HELD evidence; on `raw_crisis ≥ CRISIS_ATTACK` and the
  held state not already crisis-dominant, apply the attack + re-publish. Wire both into
  `refresh()` best-effort (each in its own try/except → `log.exception`).

**Step 4 — PASS (whole sentiment_svc folder). Step 5 — commit**
`feat(regime): 5-min slot + publish/record + ≤2-min crisis fast path`.

---

## Task 9: driver context line (context-only)

**Files:**
- Modify: `services/driver_svc/compute.py` (`_market_read`), `services/driver_svc/handlers.py` (read the cache)
- Test: `services/driver_svc/tests/test_market_read.py` (or the existing market-read test file)

**Step 1 — failing test:** feeding a regime payload into `_market_read(...,
regime=REGIME)` adds a `regime` entry — `{"label", "top": [("trending", 0.5),
("mean_reversion", 0.3)], "transition": "mean_reversion→trending 60%"}`; absent/None →
no key (byte-identical packet — pin with an equality test). `guardrails.py` untouched
(pin with a no-new-import source test if one exists for the market-read feature; else
skip).

**Steps 2–4 — FAIL → implement (additive kwarg, handler reads
`cache:sentiment:regime` defensively) → PASS**
(`.venv\Scripts\python -m pytest services\driver_svc -q`).
**Step 5 — commit** `feat(driver): regime line in market_read (context only)`.

---

## Task 10: Sentiment page — regime mix panel

**Files:**
- Modify: `webgui/pages/sentiment.py`
- Test: `webgui/tests/test_sentiment.py`

**Step 1 — failing tests** for the PURE builders:

```python
def test_regime_headline_parts():
    label, conf_txt, cls = S.regime_headline_parts(REGIME)      # "Trending", "62%", class
    assert label == "Trending" and "62" in conf_txt
    assert S.regime_headline_parts({"unclear": True, ...})[0] == "Unclear"

def test_regime_transition_text():
    assert S.regime_transition_text(REGIME) == "Mean Reversion → Trending · 60%"
    assert S.regime_transition_text(REGIME_STABLE) == ""

def test_regime_mix_figure_stacked_area_plain_chart():
    fig = S.build_regime_mix_figure(POINTS)      # today's recorded vector
    assert fig["chart"]["type"] == "area"
    assert fig["plotOptions"]["area"]["stacking"] == "normal"
    assert len(fig["series"]) == 5
    assert "stockChart" not in str(fig)          # the documented freeze gotcha
    assert fig["xAxis"]["type"] == "category"    # synthetic index, gaps packed
```

**Step 2 — FAIL. Step 3 — implement:** builders + render wiring — a card in the
Signals column area (below the tiles): headline chip (label + confidence, colors from
a finite local class map — Tailwind-first, `remove/add` on repaint), transition line,
and the stacked-area chart (built ONCE, updated in place `el.options=…; el.update()`;
explicit `chart.height`; series colors from `THEME["charts"]` with fallbacks).
`_read_cache` additionally reads `cache:sentiment:regime` + `regime_history`; repaint
rides the existing version poll (add the regime view to the version tuple).

**Step 4 — PASS** (`cd webgui; ..\.venv\Scripts\python -m pytest -q` — full folder).
**Step 5 — commit** `feat(webgui): Sentiment regime-mix panel (stacked memberships + transition)`.

---

## Task 11: help text, docs, live verification, final sweep

**Files:** `webgui/page_help.py` (Sentiment entry), root `CLAUDE.md` (Last-updated
entry + Sentiment route row), `docs/CADENCES.md` (regime rows in tables 1 & 3),
`services/sentiment_svc` docstrings.

**Steps:**
1. Update the three docs (help text in plain language — the house
   "whole words, no jargon" rule).
2. **Restart `sentiment_svc` + the webgui**, then live-verify end-to-end during RTH:
   `cache:sentiment:regime` populates within 5 min (Redis read via
   `Bus().cache_get`), the Sentiment panel renders the stacked area with no console
   errors, a `regime_intraday` row lands, and the driver's next packet carries the
   regime line. Off-hours: verify the "Unclear/as-of" degradation renders instead.
3. Full per-folder suites: sentiment-dashboard, `services\sentiment_svc`,
   `services\driver_svc`, `shared\contracts`, webgui. All green + ruff clean.
4. Final commit `docs(regime): CLAUDE.md + CADENCES + help text; live-verified` —
   leave push to the user (house workflow).

**Explicitly OUT of scope (Phase 2+/design):** the validation harness, any scanner
tilt, any driver sizing/guardrail change, the Markov transition prior, ticker items.
