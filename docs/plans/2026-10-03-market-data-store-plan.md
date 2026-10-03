# Local Market-Data Store Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cut Schwab API calls from about 84,000 to about 48,000 per trading day by having the proxy answer repeat requests from what it already fetched, and by fetching watchlist-only chains every third minute.

**Architecture:** A new in-memory store inside `schwab-proxy` sits between the existing `/chains`, `/quotes` and `/pricehistory` handlers and Schwab. Callers are unchanged except for two optional request fields (`maxAge`, `X-Caller`). The GEX collector then uses the store to carry watchlist-only chains forward for two minutes out of three. Design: [2026-10-03-market-data-store-design.md](2026-10-03-market-data-store-design.md).

**Tech Stack:** Python 3.11, FastAPI (existing proxy), stdlib `threading` / `zlib` / `json`, `shared/config_toml.toml_loader`, pytest.

---

## Before you start

**Read these first.** The root `CLAUDE.md` sections "Paths and ports", "STANDING RULE — configurable by default", "Observability — a swallowed exception must leave a trace" and "THE DEVELOPMENT RULE"; `schwab-proxy/CLAUDE.md`; the design doc above.

**Python.** This worktree has no venv of its own. Every command below uses:

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
```

Run commands from the worktree root with the Bash tool. Keep any `cd` inside a subshell `( ... )`.

**Baselines.** Before Task 1, run each suite you will touch and save the failing set (not the count). Compare against it after every task.

```bash
(cd schwab-proxy && "$PY" -m pytest tests -q -rf) > /tmp/base_proxy.txt 2>&1
(cd options-scanner && "$PY" -m pytest tests -q -rf) > /tmp/base_scanner.txt 2>&1
"$PY" -m pytest services/options_svc -q -rf > /tmp/base_options.txt 2>&1
"$PY" -m pytest services/market_svc -q -rf > /tmp/base_market.txt 2>&1
"$PY" -m pytest shared/tests -q -rf > /tmp/base_shared.txt 2>&1
(cd webgui && "$PY" -m pytest tests/test_config_schema.py tests/test_settings.py -q -rf) > /tmp/base_webgui.txt 2>&1
```

**Rules that bind every task.**

- Write the test first and watch it fail for the stated reason.
- Never weaken or delete an existing assertion to get green. If an existing test pins something this plan changes on purpose, the task says so; anything else is a real failure to investigate.
- `git add` only the files the task names. Another session may share this tree.
- End every commit message with the attribution trailer in force for the session.
- Nothing here talks to Schwab from a test. The root `conftest.py` blocks network and live SQLite; keep it that way.
- There is no dev environment. `/home/administrator/dev` on `vps2` **is prod**. Tasks 13, 14, 17 and 21 touch prod and each one stops for the operator's go-ahead.

**What ships dark.** `config/marketdata.toml` ships `mode = "shadow"`, `scan.wide_fetch = false` and `collection.tail_interval_min = 1`. In shadow the proxy still sends every request to Schwab. Nothing changes what a page shows until the operator flips a switch in Tasks 14, 17 and 21.

---

## Phase 0 — configuration, counters, the store, shadow mode

### Task 1: Configuration file and loader

**Files:**
- Create: `config/marketdata.toml`
- Modify: `repo_paths.py` (beside the other `*_TOML` constants, near line 112)
- Create: `shared/marketdata_config.py`
- Test: `shared/tests/test_marketdata_config.py`

**Step 1: Write the failing test**

```python
"""config/marketdata.toml - the proxy's local store and the collector cadence."""
import importlib

import pytest

from shared import marketdata_config as mc


@pytest.fixture(autouse=True)
def _fresh():
    mc.reset_cache()
    yield
    mc.reset_cache()


def test_shipped_file_matches_the_built_in_defaults():
    # The TOML only overrides; a key present in one and not the other is drift.
    assert mc.load() == mc.DEFAULTS


def test_ships_dark():
    assert mc.mode() == "shadow"
    assert mc.section("scan")["wide_fetch"] is False
    assert mc.section("collection")["tail_interval_min"] == 1


@pytest.mark.parametrize("bad", ["ON", "enabled", "", None, 1, True])
def test_an_unknown_mode_is_off_never_on(monkeypatch, bad):
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "mode": bad})
    assert mc.mode() == "off"


def test_a_store_switch_must_be_literally_true(monkeypatch):
    cfg = {**mc.DEFAULTS, "chains": {**mc.DEFAULTS["chains"], "enabled": "false"}}
    monkeypatch.setattr(mc, "load", lambda: cfg)
    assert mc.store_on("chains") is False
    assert mc.store_on("quotes") is True
    assert mc.store_on("nonsense") is False


def test_a_section_replaced_by_a_scalar_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(mc, "load", lambda: {**mc.DEFAULTS, "bars": 5})
    assert mc.section("bars") == mc.DEFAULTS["bars"]


def test_an_unusable_number_falls_back_to_its_default(monkeypatch):
    cfg = {**mc.DEFAULTS, "chains": {**mc.DEFAULTS["chains"],
                                     "max_age_sec": float("nan"),
                                     "closed_max_age_sec": -3}}
    monkeypatch.setattr(mc, "load", lambda: cfg)
    assert mc.section("chains")["max_age_sec"] == mc.DEFAULTS["chains"]["max_age_sec"]
    assert (mc.section("chains")["closed_max_age_sec"]
            == mc.DEFAULTS["chains"]["closed_max_age_sec"])


def test_module_reloads_cleanly():
    importlib.reload(mc)
    assert mc.mode() == "shadow"
```

**Step 2: Run it to verify it fails**

Run: `"$PY" -m pytest shared/tests/test_marketdata_config.py -q`
Expected: collection error, `cannot import name 'marketdata_config'`.

**Step 3: Write the implementation**

`repo_paths.py`, after `SWING_MODEL_TOML`:

```python
# The proxy's local market-data store and the collector cadence built on it.
MARKETDATA_TOML = REPO_ROOT / "config" / "marketdata.toml"
```

`config/marketdata.toml`:

```toml
# ─────────────────────────────────────────────────────────────────────────────
# config/marketdata.toml — THE PROXY'S LOCAL MARKET-DATA STORE
#
#   The proxy keeps what it fetches from Schwab and answers later requests from
#   that copy when the copy is fresh and covers the request. Read on every
#   request, so a change here needs NO restart.
#
#   Design: docs/plans/2026-10-03-market-data-store-design.md
# ─────────────────────────────────────────────────────────────────────────────

# "off"    — the proxy passes every request to Schwab, as it did before the store.
# "shadow" — every request still goes to Schwab; the store is filled and the proxy
#            counts what it WOULD have answered. Costs no extra calls.
# "on"     — repeat requests are answered locally.
# Anything else reads as "off".
mode = "shadow"

[chains]
enabled = true
max_age_sec = 45                  # while any session is open
closed_max_age_sec = 1800         # while every session is closed
max_entries = 400
shadow_compare_max_age_sec = 120  # shadow only: how old an entry may be and still be compared

[quotes]
enabled = true
max_age_sec = 5                   # the dashboard poll sends its own 1

[bars]
enabled = true
# How the bar for the session in progress is handled, daily series only.
#   "ttl"   — re-serve the last fetched series for session_ttl_sec.
#   "quote" — build today's bar from the live quote. Switch to this only after the
#             shadow counts show the two agree (shadow_bar_match).
today_bar = "ttl"
session_ttl_sec = 1740
today_quote_max_age_sec = 120
settle_min = 10                   # minutes after the close before the settled refetch

[scan]
# The autoscan fetches ONE today -> +45 day chain per symbol and cuts its three
# windows from it, instead of three fetches.
wide_fetch = false
wide_fetch_exclude = ["$SPX", "$NDX", "SPY", "QQQ"]   # too large for one fetch

[collection]
# Minutes between REAL chain fetches for symbols that are collected only because
# they are on the watchlist. 1 = every minute (the behaviour before this file).
# Works only while mode = "on" and chains.enabled; otherwise every fetch is real.
tail_interval_min = 1
fresh_max_age_sec = 20            # a chain older than this is treated as carried forward
```

`shared/marketdata_config.py`:

```python
"""The proxy's local market-data store settings, from ``config/marketdata.toml``.

Read by three things that cannot import each other: ``schwab-proxy`` (the
store), ``options-scanner`` (the collector's tiers and the scan's wide fetch)
and ``services/options_svc``. Every accessor reads at CALL time, so a saved
override applies without a restart.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
import math

from repo_paths import MARKETDATA_TOML
from shared.config_toml import toml_loader

MODES = ("off", "shadow", "on")

DEFAULTS = {
    "mode": "shadow",
    "chains": {
        "enabled": True,
        "max_age_sec": 45,
        "closed_max_age_sec": 1800,
        "max_entries": 400,
        "shadow_compare_max_age_sec": 120,
    },
    "quotes": {"enabled": True, "max_age_sec": 5},
    "bars": {
        "enabled": True,
        "today_bar": "ttl",
        "session_ttl_sec": 1740,
        "today_quote_max_age_sec": 120,
        "settle_min": 10,
    },
    "scan": {"wide_fetch": False, "wide_fetch_exclude": ["$SPX", "$NDX", "SPY", "QQQ"]},
    "collection": {"tail_interval_min": 1, "fresh_max_age_sec": 20},
}

load, reset_cache = toml_loader(MARKETDATA_TOML, DEFAULTS, label="marketdata.toml")


def mode() -> str:
    """``off`` / ``shadow`` / ``on``. Anything else is ``off``: a typo in this
    file must never switch local answers on."""
    m = load().get("mode")
    return m if isinstance(m, str) and m in MODES else "off"


def _usable(value, default):
    """``value`` if it is the same kind of thing as ``default`` and usable."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            return default
        return value
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def section(name: str) -> dict:
    """One table of the file with every value checked against its default's
    type. A table an override replaced with a scalar reads as the defaults."""
    base = DEFAULTS.get(name)
    if not isinstance(base, dict):
        return {}
    got = load().get(name)
    if not isinstance(got, dict):
        got = {}
    return {k: _usable(got.get(k, d), d) for k, d in base.items()}


def store_on(name: str) -> bool:
    """Whether one store (``chains`` / ``quotes`` / ``bars``) is switched on.
    Must be literally ``true``: a hand-typed ``"false"`` is a truthy string."""
    base = DEFAULTS.get(name)
    if not isinstance(base, dict) or "enabled" not in base:
        return False
    got = load().get(name)
    return isinstance(got, dict) and got.get("enabled") is True
```

**Step 4: Run it to verify it passes**

Run: `"$PY" -m pytest shared/tests/test_marketdata_config.py -q`
Expected: all pass. If `test_shipped_file_matches_the_built_in_defaults` fails, the TOML and `DEFAULTS` disagree; fix whichever is wrong, do not delete the test.

**Step 5: Commit**

```bash
git add config/marketdata.toml repo_paths.py shared/marketdata_config.py shared/tests/test_marketdata_config.py
git commit -m "feat(proxy): marketdata.toml and its loader"
```

---

### Task 2: Settings → Configuration catalogue entry

**Files:**
- Modify: `webgui/config_schema.py` (constants near line 37, `FILES` near line 1326)
- Test: `webgui/tests/test_config_schema.py` (existing; no edit expected)

**Step 1: Run the existing test to see the failure**

Run: `(cd webgui && "$PY" -m pytest tests/test_config_schema.py -q -rf)`
Expected: `test_every_config_file_is_either_catalogued_or_explained` FAILS with `uncatalogued config files: {'marketdata.toml'}`. That is the failing test for this task.

**Step 2: Add the catalogue entry**

Add `PROXY = "proxy"` beside the other unit names and `PROXY: "Schwab proxy"` to `RESTART_LABELS`.

Add before `FILES`:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Local market data — config/marketdata.toml
# ─────────────────────────────────────────────────────────────────────────────
_SEC = dict(kind="int", unit="seconds", min=0)

_MARKETDATA = ConfigFile(
    name="marketdata.toml", title="Local market data", icon="storage",
    summary="How the Schwab proxy reuses data it has already fetched, and how "
            "often watchlist symbols are fetched. Changes apply at once.",
    restart=(),
    caution="Longer time limits save Schwab calls and show older data. Turn the "
            "mode to off to return to fetching everything.",
    sections=(
        Section("Mode", "", (
            Field("mode", "Mode",
                  "Off fetches everything from Schwab. Shadow still fetches "
                  "everything and counts what could have been reused. On answers "
                  "repeat requests locally.",
                  kind="choice", choices=("off", "shadow", "on")),
        )),
        Section("Option chains", "", (
            Field("chains.enabled", "Reuse option chains", "", kind="bool"),
            Field("chains.max_age_sec", "Oldest chain to reuse while a session is open",
                  "The autoscan reads chains up to this old.", **_SEC, max=600),
            Field("chains.closed_max_age_sec", "Oldest chain to reuse while markets are closed",
                  "", **_SEC, max=86400),
            Field("chains.max_entries", "Most chains kept at once", "", kind="int",
                  min=50, max=5000),
            Field("chains.shadow_compare_max_age_sec",
                  "Oldest chain compared in shadow mode", "", **_SEC, max=600),
        )),
        Section("Quotes", "", (
            Field("quotes.enabled", "Reuse quotes", "", kind="bool"),
            Field("quotes.max_age_sec", "Oldest quote to reuse", "", **_SEC, max=60),
        )),
        Section("Daily price bars", "", (
            Field("bars.enabled", "Reuse daily price bars", "", kind="bool"),
            Field("bars.today_bar", "Today's bar",
                  "ttl re-serves the last fetched series. quote builds today's "
                  "bar from the live quote; use it only once the shadow counts "
                  "show the two agree.",
                  kind="choice", choices=("ttl", "quote")),
            Field("bars.session_ttl_sec", "Oldest series to reuse during the session",
                  "Used when today's bar is set to ttl.", **_SEC, max=7200),
            Field("bars.today_quote_max_age_sec", "Oldest quote used to build today's bar",
                  "", **_SEC, max=600),
            Field("bars.settle_min", "Minutes after the close before bars are refetched",
                  "", kind="int", unit="minutes", min=0, max=120),
        )),
        Section("Autoscan", "", (
            Field("scan.wide_fetch", "Fetch one wide chain per symbol",
                  "One fetch out to 45 days in place of three.", kind="bool"),
            Field("scan.wide_fetch_exclude", "Symbols that keep three separate fetches",
                  "Their 45-day chain is too large for one request.", kind="symbols"),
        )),
        Section("Collector", "", (
            Field("collection.tail_interval_min",
                  "Minutes between real fetches for watchlist symbols",
                  "1 fetches every symbol every minute. At 3, symbols that are "
                  "collected only because they are on the watchlist are fetched "
                  "every third minute and carried forward in between.",
                  kind="int", unit="minutes", min=1, max=10),
            Field("collection.fresh_max_age_sec",
                  "Oldest chain the collector treats as new", "", **_SEC, max=60),
        )),
    ),
)
```

Add `_MARKETDATA` to the `FILES` tuple.

**Step 3: Run the tests**

Run: `(cd webgui && "$PY" -m pytest tests/test_config_schema.py -q -rf)`
Expected: all pass. Likely first-run failures and their fixes:

- `test_labels_and_help_are_plain_words` names a word it does not accept: reword that label or help line; do not edit the test's word list.
- `test_every_shipped_value_round_trips_through_its_own_field` on a field: the `kind`, `min` or `max` is wrong for the shipped value; fix the `Field`.
- If the editor cannot take `restart=()`, grep `restart=()` in this file for the two existing examples and match them.

**Step 4: Commit**

```bash
git add webgui/config_schema.py
git commit -m "feat(settings): catalogue marketdata.toml"
```

---

### Task 3: Per-endpoint, per-caller counter

**Files:**
- Modify: `schwab-proxy/api_call_counter.py`
- Test: `schwab-proxy/tests/test_api_call_counter.py`

**Step 1: Write the failing tests** (append)

```python
def test_detail_counts_by_endpoint_caller_and_outcome():
    acc.record_detail("chains", "options_svc", "upstream", day="2026-10-05")
    acc.record_detail("chains", "options_svc", "upstream", day="2026-10-05")
    acc.record_detail("chains", "options_svc", "subset", day="2026-10-05")
    acc.record_detail("quotes", "market_svc", "hit", n=4, day="2026-10-05")
    s = acc.detail_summary(day="2026-10-05")
    assert s["by_outcome"] == {"upstream": 2, "subset": 1, "hit": 4}
    assert s["served_locally"] == 5                      # subset + hit
    assert {"endpoint": "chains", "caller": "options_svc",
            "outcome": "upstream", "n": 2} in s["rows"]


def test_shadow_outcomes_are_not_counted_as_served_locally():
    acc.record_detail("chains", "x", "shadow_subset_match", day="2026-10-05")
    acc.record_detail("chains", "x", "partial", day="2026-10-05")
    assert acc.detail_summary(day="2026-10-05")["served_locally"] == 0


def test_detail_is_per_day():
    acc.record_detail("chains", "x", "hit", day="2026-10-04")
    assert acc.detail_summary(day="2026-10-05") == {
        "served_locally": 0, "by_outcome": {}, "rows": []}


def test_detail_never_raises(monkeypatch):
    monkeypatch.setattr(acc, "_get_conn", lambda: (_ for _ in ()).throw(RuntimeError))
    acc.record_detail("chains", "x", "hit")              # must not raise
    assert acc.detail_summary()["served_locally"] == 0


def test_long_caller_names_are_cut():
    acc.record_detail("chains", "c" * 500, "hit", day="2026-10-05")
    assert len(acc.detail_summary(day="2026-10-05")["rows"][0]["caller"]) == 40
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_api_call_counter.py -q)`
Expected: FAIL, `module 'api_call_counter' has no attribute 'record_detail'`.

**Step 3: Implement**

In `api_call_counter.py`, add under `_SCHEMA`:

```python
_DETAIL_SCHEMA = ("CREATE TABLE IF NOT EXISTS api_calls_detail ("
                  "day TEXT NOT NULL, endpoint TEXT NOT NULL, caller TEXT NOT NULL, "
                  "outcome TEXT NOT NULL, n INTEGER NOT NULL, "
                  "PRIMARY KEY (day, endpoint, caller, outcome))")
# Outcomes that answered a request WITHOUT a call to Schwab.
LOCAL_OUTCOMES = ("hit", "subset", "coalesced", "composed")
```

In `connect()`, after `conn.execute(_SCHEMA)`: `conn.execute(_DETAIL_SCHEMA)`.

Append:

```python
def record_detail(endpoint: str, caller: str, outcome: str, n: int = 1,
                  day: str | None = None) -> None:
    """Add ``n`` to one (day, endpoint, caller, outcome) row. Never raises.

    ``outcome`` is ``upstream`` for a call sent to Schwab, one of
    ``LOCAL_OUTCOMES`` for a request answered locally, ``partial`` for a quote
    request that fetched only its stale symbols, or a ``shadow_*`` name."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            c = _get_conn()
            c.execute(
                "INSERT INTO api_calls_detail(day, endpoint, caller, outcome, n) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(day, endpoint, caller, outcome) "
                "DO UPDATE SET n = n + excluded.n",
                (d, str(endpoint)[:40], str(caller)[:40], str(outcome)[:40], int(n)))
            c.commit()
    except Exception:  # noqa: BLE001 — counting must never break a request.
        pass


def detail_summary(day: str | None = None) -> dict:
    """One day's breakdown: ``{"served_locally", "by_outcome", "rows"}``.
    Never raises — an empty summary on any failure."""
    try:
        d = day or _dt.date.today().isoformat()
        with _lock:
            rows = _get_conn().execute(
                "SELECT endpoint, caller, outcome, n FROM api_calls_detail "
                "WHERE day = ? ORDER BY n DESC", (d,)).fetchall()
        by_outcome: dict = {}
        for _e, _c, outcome, n in rows:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + int(n)
        return {
            "served_locally": sum(by_outcome.get(o, 0) for o in LOCAL_OUTCOMES),
            "by_outcome": by_outcome,
            "rows": [{"endpoint": e, "caller": c, "outcome": o, "n": int(n)}
                     for e, c, o, n in rows],
        }
    except Exception:  # noqa: BLE001
        return {"served_locally": 0, "by_outcome": {}, "rows": []}
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_api_call_counter.py -q)`
Expected: all pass, including the older tests.

**Step 5: Commit**

```bash
git add schwab-proxy/api_call_counter.py schwab-proxy/tests/test_api_call_counter.py
git commit -m "feat(proxy): count calls by endpoint, caller and outcome"
```

---

### Task 4: Chain store — keep, exact hit, cut to a window

**Files:**
- Create: `schwab-proxy/market_store.py`
- Test: `schwab-proxy/tests/test_market_store_chains.py`

**Step 1: Write the failing tests**

```python
"""The chain store: keep a fetched chain, answer the same request again, and
answer a narrower date window by cutting a stored one."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402

EXPS = ("2026-10-05:0", "2026-10-07:2", "2026-10-09:4", "2026-10-12:7")
STRIKES = (95.0, 100.0, 105.0)


def chain(exps=EXPS, strikes=STRIKES, spot=100.0):
    def side(put_call):
        return {e: {str(k): [{"putCall": put_call, "strikePrice": k, "gamma": 0.05,
                              "openInterest": 100, "totalVolume": 10}]
                    for k in strikes} for e in exps}
    return {"symbol": "SPY", "status": "SUCCESS", "underlyingPrice": spot,
            "numberOfContracts": 2 * len(exps) * len(strikes),
            "callExpDateMap": side("CALL"), "putExpDateMap": side("PUT")}


WIDE = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-12")
NARROW = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-09")


def _store(**kw):
    s = ms.ChainStore(**kw)
    s.put(WIDE, chain(), now=1000.0, state="REGULAR")
    return s


def test_the_same_request_is_answered_from_the_store():
    got = _store().lookup(WIDE, max_age=45, now=1010.0, state="REGULAR")
    assert got.kind == "hit" and got.age == 10.0
    assert json.loads(got.body) == chain()


def test_an_entry_past_its_age_limit_is_not_served():
    assert _store().lookup(WIDE, max_age=45, now=1046.0, state="REGULAR") is None


def test_an_age_limit_of_zero_never_hits():
    assert _store().lookup(WIDE, max_age=0, now=1000.5, state="REGULAR") is None


def test_an_entry_never_crosses_a_session_change():
    # Stored while closed, asked for after the open: refetch, however young.
    s = ms.ChainStore()
    s.put(WIDE, chain(), now=1000.0, state="CLOSED")
    assert s.lookup(WIDE, max_age=1800, now=1001.0, state="REGULAR") is None


def test_a_narrower_window_is_cut_from_the_wide_chain():
    got = _store().lookup(NARROW, max_age=45, now=1010.0, state="REGULAR")
    assert got.kind == "subset"
    body = json.loads(got.body)
    assert sorted(body["callExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert sorted(body["putExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert body["numberOfContracts"] == 2 * 3 * len(STRIKES)
    assert body["underlyingPrice"] == 100.0
    assert body["callExpDateMap"]["2026-10-07:2"] == chain()["callExpDateMap"]["2026-10-07:2"]


def test_a_wider_window_is_never_answered_from_a_narrower_chain():
    s = ms.ChainStore()
    s.put(NARROW, chain(exps=EXPS[:3]), now=1000.0, state="REGULAR")
    assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_strike_filtered_request_is_never_cut_from_an_all_strikes_chain():
    # The sector put/call ratio sums volume over the strikes it receives.
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-09")
    assert _store().lookup(filtered, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_strike_filtered_chain_never_answers_an_all_strikes_request():
    s = ms.ChainStore()
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-12")
    s.put(filtered, chain(), now=1000.0, state="REGULAR")
    assert s.lookup(NARROW, max_age=45, now=1001.0, state="REGULAR") is None


def test_another_symbols_chain_is_never_served():
    other = ms.ChainKey("QQQ", from_date="2026-10-05", to_date="2026-10-09")
    assert _store().lookup(other, max_age=45, now=1001.0, state="REGULAR") is None


def test_a_request_without_dates_is_exact_match_only():
    undated = ms.ChainKey("SPY")
    assert _store().lookup(undated, max_age=45, now=1001.0, state="REGULAR") is None


def test_the_newest_covering_chain_wins():
    s = _store()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-10-20")
    s.put(wider, chain(spot=101.0), now=1020.0, state="REGULAR")
    got = s.lookup(NARROW, max_age=45, now=1030.0, state="REGULAR")
    assert json.loads(got.body)["underlyingPrice"] == 101.0 and got.age == 10.0


def test_a_malformed_payload_is_not_stored():
    s = ms.ChainStore()
    for bad in (None, [], "x", {"callExpDateMap": []}, {"callExpDateMap": {"k": 5},
                                                         "putExpDateMap": {}}):
        s.put(WIDE, bad, now=1000.0, state="REGULAR")
    assert s.lookup(WIDE, max_age=45, now=1001.0, state="REGULAR") is None


def test_the_oldest_entries_are_dropped_past_the_limit():
    s = ms.ChainStore(max_entries=2)
    keys = [ms.ChainKey(sym, from_date="2026-10-05", to_date="2026-10-12")
            for sym in ("A", "B", "C")]
    for i, k in enumerate(keys):
        s.put(k, chain(), now=1000.0 + i, state="REGULAR")
    assert s.lookup(keys[0], max_age=45, now=1003.0, state="REGULAR") is None
    assert s.lookup(keys[2], max_age=45, now=1003.0, state="REGULAR") is not None


def test_the_limit_can_be_changed_on_a_put():
    s = ms.ChainStore(max_entries=10)
    keys = [ms.ChainKey(sym, from_date="2026-10-05", to_date="2026-10-12")
            for sym in ("A", "B", "C")]
    for i, k in enumerate(keys):
        s.put(k, chain(), now=1000.0 + i, state="REGULAR", max_entries=2)
    assert s.lookup(keys[0], max_age=45, now=1003.0, state="REGULAR") is None
    s.put(keys[0], chain(), now=1004.0, state="REGULAR", max_entries="many")
    assert s.lookup(keys[2], max_age=45, now=1004.0, state="REGULAR") is not None


def test_key_from_request_parameters_round_trips():
    params = {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
              "fromDate": "2026-10-05", "toDate": "2026-10-12"}
    key = ms.ChainKey.from_params(params)
    assert key == WIDE and key.params() == params
    filtered = ms.ChainKey.from_params({**params, "range": "NTM", "strikeCount": "50"})
    assert filtered.strike_count == 50 and not filtered.plain
    assert filtered.params()["strikeCount"] == 50


def test_chain_shape_is_the_set_of_contracts_not_their_values():
    assert ms.chain_shape(chain(spot=1.0)) == ms.chain_shape(chain(spot=2.0))
    assert ms.chain_shape(chain()) != ms.chain_shape(chain(exps=EXPS[:3]))
    assert ms.chain_shape(None) == frozenset()
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_chains.py -q)`
Expected: collection error, `No module named 'market_store'`.

**Step 3: Implement** — create `schwab-proxy/market_store.py`:

```python
"""What the proxy has already fetched from Schwab, kept in memory.

Three stores (chains, quotes, daily bars) and the ``Gateway`` that decides, per
request, whether a stored copy is fresh and covers the request or whether to
call Schwab. No FastAPI and no repo imports: the handlers in ``schwab_proxy.py``
are thin adapters over ``Gateway``, and everything here is unit-testable.

Nothing here is written to disk or to Redis. A proxy restart starts empty.

Design: docs/plans/2026-10-03-market-data-store-design.md
"""
from __future__ import annotations

import json
import math
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass


class UpstreamError(Exception):
    """Schwab answered with something other than 200. Carries what the handler
    needs to raise the same HTTP error it raised before the store existed."""

    def __init__(self, status_code, detail):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Served:
    """One answer. ``body`` is ready-to-send JSON bytes; ``data`` is an object
    for the framework to serialize. Exactly one of them is set.

    ``kind``: ``hit`` / ``subset`` / ``coalesced`` / ``composed`` were answered
    locally; ``miss`` / ``partial`` / ``pass`` called Schwab."""
    kind: str
    age: float
    body: bytes | None = None
    data: object = None


def _real_number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


#############################################
# CHAINS
#############################################

@dataclass(frozen=True)
class ChainKey:
    """The full identity of a ``/chains`` request."""
    symbol: str
    contract_type: str = "ALL"
    strike_range: str = "ALL"
    strike_count: int | None = None
    from_date: str | None = None
    to_date: str | None = None

    @classmethod
    def from_params(cls, params) -> "ChainKey":
        count = params.get("strikeCount")
        return cls(
            symbol=str(params.get("symbol")),
            contract_type=str(params.get("contractType") or "ALL"),
            strike_range=str(params.get("range") or "ALL"),
            strike_count=int(count) if count is not None else None,
            from_date=str(params["fromDate"])[:10] if params.get("fromDate") else None,
            to_date=str(params["toDate"])[:10] if params.get("toDate") else None,
        )

    def params(self) -> dict:
        out = {"symbol": self.symbol, "contractType": self.contract_type,
               "range": self.strike_range}
        if self.from_date:
            out["fromDate"] = self.from_date
        if self.to_date:
            out["toDate"] = self.to_date
        if self.strike_count is not None:
            out["strikeCount"] = self.strike_count
        return out

    @property
    def plain(self) -> bool:
        """Every strike, both sides, a stated date window. Only a plain chain
        may be cut, and only a plain request may be answered by cutting: a
        strike-filtered consumer sums over exactly the strikes it asked for."""
        return (self.contract_type == "ALL" and self.strike_range == "ALL"
                and self.strike_count is None
                and bool(self.from_date) and bool(self.to_date))

    def covers(self, other: "ChainKey") -> bool:
        # ISO dates compare correctly as strings.
        return (self.plain and other.plain and self.symbol == other.symbol
                and self.from_date <= other.from_date
                and self.to_date >= other.to_date)


@dataclass(frozen=True)
class _ChainEntry:
    key: ChainKey
    fetched_at: float
    state: str        # the market session it was fetched in
    header: dict      # every top-level field except the two expiration maps
    calls: dict       # {expiration key: (zlib JSON of its strike map, contracts)}
    puts: dict


_SIDES = ("callExpDateMap", "putExpDateMap")


def _pack_side(exp_map) -> dict:
    out = {}
    for exp_key, strikes in exp_map.items():
        if not isinstance(strikes, dict):
            raise ValueError("unexpected chain shape")
        count = sum(len(v) for v in strikes.values() if isinstance(v, list))
        blob = zlib.compress(
            json.dumps(strikes, separators=(",", ":")).encode(), 1)
        out[str(exp_key)] = (blob, count)
    return out


def _render(entry: _ChainEntry, lo=None, hi=None) -> bytes:
    """The entry as response JSON, cut to expirations in ``[lo, hi]`` when
    given. Built by joining stored fragments; nothing is re-parsed."""
    def side(frags):
        parts, n = [], 0
        for exp_key, (blob, count) in frags.items():
            day = exp_key.split(":")[0]
            if lo is not None and not (lo <= day <= hi):
                continue
            parts.append(json.dumps(exp_key).encode() + b":" + zlib.decompress(blob))
            n += count
        return b"{" + b",".join(parts) + b"}", n

    calls, n_calls = side(entry.calls)
    puts, n_puts = side(entry.puts)
    header = dict(entry.header)
    if "numberOfContracts" in header:
        header["numberOfContracts"] = n_calls + n_puts
    head = json.dumps(header, separators=(",", ":")).encode()
    joiner = b"," if header else b""
    return (head[:-1] + joiner + b'"callExpDateMap":' + calls
            + b',"putExpDateMap":' + puts + b"}")


def chain_shape(payload) -> frozenset:
    """Which contracts a chain holds: ``(side, expiration date, strike)``.
    Shadow mode compares these, never values — values move every second."""
    out = set()
    if not isinstance(payload, dict):
        return frozenset()
    for side in _SIDES:
        exp_map = payload.get(side)
        if not isinstance(exp_map, dict):
            continue
        for exp_key, strikes in exp_map.items():
            day = str(exp_key).split(":")[0]
            for strike in (strikes or {}):
                out.add((side, day, str(strike)))
    return frozenset(out)


class ChainStore:
    def __init__(self, max_entries: int = 400):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[ChainKey, _ChainEntry]" = OrderedDict()
        self._max = max(1, int(max_entries))

    def put(self, key: ChainKey, payload, *, now: float, state: str,
            max_entries: int | None = None) -> bool:
        """Keep one fetched chain. Returns False (and keeps nothing) for a
        payload that is not chain-shaped. ``max_entries`` is the bound to
        enforce on THIS put (the gateway passes the configured value, so a
        saved setting applies without a restart)."""
        if not isinstance(payload, dict):
            return False
        calls, puts = payload.get("callExpDateMap"), payload.get("putExpDateMap")
        if not isinstance(calls, dict) or not isinstance(puts, dict):
            return False
        try:
            entry = _ChainEntry(
                key=key, fetched_at=now, state=state,
                header={k: v for k, v in payload.items() if k not in _SIDES},
                calls=_pack_side(calls), puts=_pack_side(puts))
        except (ValueError, TypeError):
            return False
        if max_entries is not None:
            try:
                self._max = max(1, int(max_entries))
            except (TypeError, ValueError):
                pass                      # keep the bound already in force
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return True

    @staticmethod
    def _fresh(entry, max_age, now, state) -> bool:
        age = now - entry.fetched_at
        return entry.state == state and 0 <= age <= max_age and max_age > 0

    def lookup(self, key: ChainKey, *, max_age: float, now: float,
               state: str) -> Served | None:
        with self._lock:
            exact = self._entries.get(key)
            others = ([e for k, e in self._entries.items()
                       if k.symbol == key.symbol and k != key]
                      if key.plain else [])
        if exact is not None and self._fresh(exact, max_age, now, state):
            return Served("hit", now - exact.fetched_at, body=_render(exact))
        best = None
        for e in others:
            if e.key.covers(key) and self._fresh(e, max_age, now, state):
                if best is None or e.fetched_at > best.fetched_at:
                    best = e
        if best is None:
            return None
        return Served("subset", now - best.fetched_at,
                      body=_render(best, key.from_date, key.to_date))
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_chains.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_store_chains.py
git commit -m "feat(proxy): chain store with exact and cut-to-window answers"
```

---

### Task 5: Chain store — refetch the wide window on a near miss

**Files:**
- Modify: `schwab-proxy/market_store.py` (`ChainStore`)
- Test: `schwab-proxy/tests/test_market_store_chains.py`

**Why:** the scan and the collector run in the same minute in either order. If the scan asks for today → +4 while the stored today → +7 chain is a few seconds too old, fetching today → +7 means the collector's request seconds later is a hit. Without this the saving depends on which branch wins the race.

**Step 1: Write the failing tests** (append)

```python
import datetime as dt

TODAY = dt.date(2026, 10, 5)


def test_a_near_miss_refetches_the_wider_window_already_held():
    s = _store()                               # holds today -> +7, at any age
    assert s.wide_key(NARROW, today=TODAY) == WIDE


def test_no_wider_refetch_for_a_symbol_nobody_fetched_wide():
    assert ms.ChainStore().wide_key(NARROW, today=TODAY) is None


def test_no_wider_refetch_when_the_request_is_the_held_window_itself():
    assert _store().wide_key(WIDE, today=TODAY) is None


def test_no_wider_refetch_for_a_window_the_held_one_does_not_cover():
    far = ms.ChainKey("SPY", from_date="2026-10-10", to_date="2026-10-20")
    assert _store().wide_key(far, today=TODAY) is None


def test_no_wider_refetch_for_a_strike_filtered_request():
    filtered = ms.ChainKey("SPY", strike_range="NTM", strike_count=50,
                           from_date="2026-10-05", to_date="2026-10-09")
    assert _store().wide_key(filtered, today=TODAY) is None


def test_the_narrowest_covering_window_is_the_one_refetched():
    s = _store()
    wider = ms.ChainKey("SPY", from_date="2026-10-05", to_date="2026-11-19")
    s.put(wider, chain(), now=1500.0, state="REGULAR")
    assert s.wide_key(NARROW, today=TODAY) == WIDE


def test_a_window_that_started_on_an_earlier_day_is_not_refetched():
    s = ms.ChainStore()
    yesterday = ms.ChainKey("SPY", from_date="2026-10-04", to_date="2026-10-11")
    s.put(yesterday, chain(), now=1000.0, state="REGULAR")
    assert s.wide_key(NARROW, today=TODAY) is None


def test_cut_answers_from_the_wide_entry_whatever_its_age():
    body = json.loads(_store().cut(WIDE, NARROW))
    assert sorted(body["callExpDateMap"]) == ["2026-10-05:0", "2026-10-07:2", "2026-10-09:4"]
    assert ms.ChainStore().cut(WIDE, NARROW) is None
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_chains.py -q)`
Expected: FAIL, `'ChainStore' object has no attribute 'wide_key'`.

**Step 3: Implement** — add these methods to `ChainStore`:

```python
    def wide_key(self, key: ChainKey, *, today) -> ChainKey | None:
        """The wider window to fetch INSTEAD of ``key``, or None to fetch
        ``key`` as asked.

        It is the narrowest plain window already held for this symbol, at ANY
        age, that starts today and covers ``key`` - in practice the collector's
        today -> +7. Derived from what is held rather than configured, so it
        cannot drift from the window the collector actually asks for. A window
        that started on an earlier day is never reused: refetching it would ask
        Schwab for expirations in the past."""
        if not key.plain:
            return None
        start = today.isoformat()
        with self._lock:
            held = [e for k, e in self._entries.items()
                    if k.symbol == key.symbol and k != key
                    and k.from_date == start and k.covers(key)]
        if not held:
            return None
        return min(held, key=lambda e: (e.key.to_date, -e.fetched_at)).key

    def cut(self, wide: ChainKey, key: ChainKey) -> bytes | None:
        """``key``'s window out of the entry stored under ``wide``, regardless
        of age — for the moment right after ``wide`` was fetched."""
        with self._lock:
            entry = self._entries.get(wide)
        if entry is None:
            return None
        return _render(entry, key.from_date, key.to_date)
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_chains.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_store_chains.py
git commit -m "feat(proxy): refetch the wide chain window on a near miss"
```

---

### Task 6: Quote store

**Files:**
- Modify: `schwab-proxy/market_store.py`
- Test: `schwab-proxy/tests/test_market_store_quotes.py`

**Step 1: Write the failing tests**

```python
"""The quote store: per-symbol quotes with their own fetch times."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402


def q(last):
    return {"assetMainType": "EQUITY", "quote": {"lastPrice": last}}


def test_fresh_symbols_are_served_and_the_rest_reported_missing():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0), "QQQ": q(400.0)}, now=100.0)
    fresh, missing, oldest = s.split(["SPY", "IWM", "QQQ"], max_age=5, now=103.0)
    assert fresh == {"SPY": q(500.0), "QQQ": q(400.0)}
    assert missing == ["IWM"] and oldest == 3.0


def test_a_stale_symbol_is_missing():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    fresh, missing, _ = s.split(["SPY"], max_age=5, now=105.1)
    assert fresh == {} and missing == ["SPY"]


def test_an_age_limit_of_zero_never_hits():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    assert s.split(["SPY"], max_age=0, now=100.0)[1] == ["SPY"]


def test_schwabs_errors_block_is_not_a_symbol():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0), "errors": {"invalidSymbols": ["$DXY"]}}, now=100.0)
    assert s.split(["errors"], max_age=5, now=100.0)[1] == ["errors"]


def test_a_malformed_payload_stores_nothing():
    s = ms.QuoteStore()
    for bad in (None, [], "x", {"SPY": 5}):
        s.put_many(bad, now=100.0)
    assert s.get("SPY", max_age=5, now=100.0) is None


def test_get_returns_one_fresh_quote_or_none():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    assert s.get("SPY", max_age=120, now=200.0) == q(500.0)
    assert s.get("SPY", max_age=120, now=221.0) is None
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_quotes.py -q)`
Expected: FAIL, `module 'market_store' has no attribute 'QuoteStore'`.

**Step 3: Implement** — append to `market_store.py`:

```python
#############################################
# QUOTES
#############################################

class QuoteStore:
    """Schwab's raw per-symbol quote blocks, each with its own fetch time.
    Bounded by the app's symbol universe (about 900), so it needs no eviction."""

    def __init__(self):
        self._lock = threading.Lock()
        self._quotes: dict = {}

    def put_many(self, payload, *, now: float) -> None:
        if not isinstance(payload, dict):
            return
        with self._lock:
            for symbol, block in payload.items():
                # "errors" is Schwab's invalid-symbols bucket, not a quote.
                if symbol != "errors" and isinstance(block, dict):
                    self._quotes[symbol] = (block, now)

    def split(self, symbols, *, max_age: float, now: float):
        """``(fresh {symbol: block}, missing [symbol], oldest fresh age)``."""
        fresh, missing, oldest = {}, [], 0.0
        with self._lock:
            for symbol in symbols:
                got = self._quotes.get(symbol)
                age = None if got is None else now - got[1]
                if age is not None and max_age > 0 and 0 <= age <= max_age:
                    fresh[symbol] = got[0]
                    oldest = max(oldest, age)
                else:
                    missing.append(symbol)
        return fresh, missing, oldest

    def get(self, symbol: str, *, max_age: float, now: float):
        return self.split([symbol], max_age=max_age, now=now)[0].get(symbol)
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_quotes.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_store_quotes.py
git commit -m "feat(proxy): quote store"
```

---

### Task 7: Bar store — bar periods and today's bar

**Files:**
- Modify: `schwab-proxy/market_store.py`
- Test: `schwab-proxy/tests/test_market_store_bars.py`

**Background.** Daily candles from Schwab are stamped at midnight Central. A day has three bar periods: `pre` (trading day, before 08:30 CT), `live` (from the open until `settle_min` after the 15:00 CT close) and `settled`. An entry is valid only inside the period it was fetched in.

**Step 1: Write the failing tests**

```python
"""Daily bars: one fetch per bar period, and today's bar from the live quote."""
import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402

CT = ZoneInfo("America/Chicago")
MON = dt.date(2026, 10, 5)      # a trading day
SAT = dt.date(2026, 10, 3)
FRI = dt.date(2026, 10, 2)


class Cal:
    """The four calendar calls ``bar_epoch`` makes."""
    @staticmethod
    def is_trading_day(d):
        return d.weekday() < 5

    @staticmethod
    def regular_session_has_opened(now):
        return Cal.is_trading_day(now.date()) and now.time() >= dt.time(8, 30)

    @staticmethod
    def regular_close_on(d):
        return dt.datetime.combine(d, dt.time(15, 0), tzinfo=CT)

    @staticmethod
    def prev_trading_day(d):
        d -= dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d


def at(d, h, m):
    return dt.datetime.combine(d, dt.time(h, m), tzinfo=CT)


def test_the_three_periods_of_a_trading_day():
    assert ms.bar_epoch(at(MON, 7, 0), 10, Cal) == ("2026-10-05", "pre")
    assert ms.bar_epoch(at(MON, 8, 30), 10, Cal) == ("2026-10-05", "live")
    assert ms.bar_epoch(at(MON, 15, 9), 10, Cal) == ("2026-10-05", "live")
    assert ms.bar_epoch(at(MON, 15, 10), 10, Cal) == ("2026-10-05", "settled")
    assert ms.bar_epoch(at(MON, 23, 0), 10, Cal) == ("2026-10-05", "settled")


def test_a_weekend_stays_in_fridays_settled_period():
    assert ms.bar_epoch(at(SAT, 12, 0), 10, Cal) == ("2026-10-02", "settled")
    assert ms.bar_epoch(at(FRI, 20, 0), 10, Cal) == ("2026-10-02", "settled")


def stamp(d):
    return int(dt.datetime.combine(d, dt.time(0), tzinfo=CT).timestamp() * 1000)


def series(*days, close=100.0):
    return {"symbol": "SPY", "empty": False,
            "candles": [{"open": 99.0, "high": 101.0, "low": 98.0, "close": close,
                         "volume": 1000, "datetime": stamp(d)} for d in days]}


KEY = ms.bar_key({"symbol": "SPY", "periodType": "year", "period": 1,
                  "frequencyType": "daily", "frequency": 1})
LIVE = ("2026-10-05", "live")


def test_bar_key_separates_ranges():
    other = ms.bar_key({"symbol": "SPY", "periodType": "month", "period": "3",
                        "frequencyType": "daily", "frequency": "1"})
    assert KEY == ("SPY", "year", 1, "daily", 1) and other != KEY


def test_an_entry_is_served_inside_its_period_only():
    s = ms.BarStore()
    s.put(KEY, series(FRI, MON), now=500.0, epoch=LIVE)
    body, fetched_at = s.get(KEY, epoch=LIVE)
    assert fetched_at == 500.0 and b'"candles"' in body
    assert s.get(KEY, epoch=("2026-10-05", "settled")) is None


def test_an_empty_series_is_not_kept():
    s = ms.BarStore()
    s.put(KEY, {"candles": [], "empty": True}, now=500.0, epoch=LIVE)
    s.put(KEY, None, now=500.0, epoch=LIVE)
    assert s.get(KEY, epoch=LIVE) is None


QUOTE = {"quote": {"openPrice": 100.0, "highPrice": 103.0, "lowPrice": 99.5,
                   "lastPrice": 102.0, "totalVolume": 5000}}


def test_todays_bar_replaces_the_one_schwab_sent():
    out = ms.compose_today(series(FRI, MON), QUOTE, MON)
    assert len(out["candles"]) == 2
    assert out["candles"][-1] == {"open": 100.0, "high": 103.0, "low": 99.5,
                                  "close": 102.0, "volume": 5000,
                                  "datetime": stamp(MON)}
    assert out["candles"][0]["close"] == 100.0          # history untouched


def test_todays_bar_is_appended_when_schwab_sent_none():
    out = ms.compose_today(series(FRI), QUOTE, MON)
    assert [c["datetime"] for c in out["candles"]] == [stamp(FRI), stamp(MON)]


def test_the_input_series_is_not_mutated():
    src = series(FRI, MON)
    ms.compose_today(src, QUOTE, MON)
    assert src["candles"][-1]["close"] == 100.0


def test_an_unusable_quote_composes_nothing():
    for bad in (None, {}, {"quote": {}},
                {"quote": {**QUOTE["quote"], "lastPrice": 0}},
                {"quote": {**QUOTE["quote"], "highPrice": float("nan")}},
                {"quote": {**QUOTE["quote"], "openPrice": True}}):
        assert ms.compose_today(series(FRI, MON), bad, MON) is None


def test_a_missing_volume_is_zero_not_a_refusal():
    q = {"quote": {k: v for k, v in QUOTE["quote"].items() if k != "totalVolume"}}
    assert ms.compose_today(series(FRI), q, MON)["candles"][-1]["volume"] == 0


def test_shadow_verdicts():
    near = series(FRI, MON, close=102.1)
    near["candles"][-1].update(high=103.0, low=99.5)
    assert ms.compare_today_bar(near, QUOTE, MON) == "match"
    assert ms.compare_today_bar(series(FRI, MON, close=90.0), QUOTE, MON) == "mismatch"
    assert ms.compare_today_bar(series(FRI), QUOTE, MON) == "no_today"
    assert ms.compare_today_bar(series(FRI, MON), {}, MON) == "no_quote"
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_bars.py -q)`
Expected: FAIL, `module 'market_store' has no attribute 'bar_epoch'`.

**Step 3: Implement** — add `from datetime import datetime, time as _time, timedelta`, add `from zoneinfo import ZoneInfo` and `CT = ZoneInfo("America/Chicago")` near the top, then append:

```python
#############################################
# DAILY BARS
#############################################

def bar_key(params) -> tuple:
    """``(symbol, periodType, period, frequencyType, frequency)`` — the exact
    range asked for. A shorter range is never cut from a longer one."""
    return (str(params.get("symbol")), str(params.get("periodType")),
            int(params.get("period")), str(params.get("frequencyType")),
            int(params.get("frequency")))


def bar_epoch(now_ct, settle_min: float, cal) -> tuple:
    """``(session date, "pre" | "live" | "settled")``.

    ``cal`` is ``shared.market_calendar`` (or a stand-in with the same four
    calls). An entry is served only inside the period it was fetched in, so
    each boundary refetches once — which is also what picks up a split
    adjustment to the history."""
    d = now_ct.date()
    if cal.is_trading_day(d):
        settled_at = cal.regular_close_on(d) + timedelta(minutes=float(settle_min))
        if now_ct >= settled_at:
            return (d.isoformat(), "settled")
        if cal.regular_session_has_opened(now_ct):
            return (d.isoformat(), "live")
        return (d.isoformat(), "pre")
    return (cal.prev_trading_day(d).isoformat(), "settled")


def _candle_date(candle):
    try:
        return datetime.fromtimestamp(candle["datetime"] / 1000, CT).date()
    except Exception:  # noqa: BLE001 — a malformed candle has no date.
        return None


def compose_today(payload, quote, today):
    """``payload`` with the bar for ``today`` built from ``quote``, or None when
    the quote cannot supply one. Returns a new dict; ``payload`` is untouched."""
    block = quote.get("quote") if isinstance(quote, dict) else None
    if not isinstance(block, dict) or not isinstance(payload, dict):
        return None
    o, h, lo, last = (block.get(k) for k in
                      ("openPrice", "highPrice", "lowPrice", "lastPrice"))
    if not all(_real_number(v) and v > 0 for v in (o, h, lo, last)):
        return None
    volume = block.get("totalVolume")
    bar = {"open": o, "high": h, "low": lo, "close": last,
           "volume": volume if _real_number(volume) else 0,
           # Schwab stamps a daily candle at midnight Central.
           "datetime": int(datetime.combine(today, _time(0), tzinfo=CT)
                           .timestamp() * 1000)}
    candles = list(payload.get("candles") or [])
    if candles and _candle_date(candles[-1]) == today:
        candles[-1] = bar
    else:
        candles.append(bar)
    return {**payload, "candles": candles}


_BAR_TOLERANCE = 0.005   # the quote may be up to two minutes older than the bar


def compare_today_bar(upstream, quote, today) -> str:
    """Shadow verdict on whether the quote-built bar agrees with Schwab's:
    ``match`` / ``mismatch`` / ``no_today`` (Schwab sent no bar for today) /
    ``no_quote``."""
    composed = compose_today(upstream, quote, today)
    if composed is None:
        return "no_quote"
    candles = (upstream or {}).get("candles") or []
    if not candles or _candle_date(candles[-1]) != today:
        return "no_today"
    theirs, ours = candles[-1], composed["candles"][-1]
    for field in ("close", "high", "low"):
        a, b = theirs.get(field), ours[field]
        if not _real_number(a) or abs(a - b) > _BAR_TOLERANCE * abs(b):
            return "mismatch"
    return "match"


class BarStore:
    """Daily price series, one per (symbol, range), valid for one bar period."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict = {}

    def put(self, key, payload, *, now: float, epoch) -> None:
        if not isinstance(payload, dict) or not payload.get("candles"):
            return          # an empty series is refetched, never re-served
        blob = zlib.compress(
            json.dumps(payload, separators=(",", ":")).encode(), 1)
        with self._lock:
            self._entries[key] = (blob, now, epoch)

    def get(self, key, *, epoch):
        """``(JSON bytes, fetched_at)`` for this period, or None."""
        with self._lock:
            got = self._entries.get(key)
        if got is None or got[2] != epoch:
            return None
        return zlib.decompress(got[0]), got[1]
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_store_bars.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_store_bars.py
git commit -m "feat(proxy): daily bar store with bar periods and a quote-built bar"
```

---

### Task 8: The gateway — chains

**Files:**
- Modify: `schwab-proxy/market_store.py`
- Test: `schwab-proxy/tests/test_market_gateway.py`

**Step 1: Write the failing tests**

```python
"""The gateway: per request, a stored answer or a call to Schwab."""
import datetime as dt
import json
import pathlib
import sys
import threading
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402
from test_market_store_bars import Cal, series  # noqa: E402
from test_market_store_chains import chain  # noqa: E402

CT = ZoneInfo("America/Chicago")
DEFAULTS = {
    "chains": {"enabled": True, "max_age_sec": 45, "closed_max_age_sec": 1800,
               "max_entries": 400, "shadow_compare_max_age_sec": 120},
    "quotes": {"enabled": True, "max_age_sec": 5},
    "bars": {"enabled": True, "today_bar": "ttl", "session_ttl_sec": 1740,
             "today_quote_max_age_sec": 120, "settle_min": 10},
}


class Session:
    def __init__(self, name):
        self.name = name


class FakeCal(Cal):
    state = "REGULAR"

    @classmethod
    def session_at(cls, now):
        return Session(cls.state)


class Cfg:
    def __init__(self, mode="on", **over):
        self._mode, self._s = mode, json.loads(json.dumps(DEFAULTS))
        for dotted, v in over.items():
            sec, key = dotted.split("__")
            self._s[sec][key] = v

    def mode(self):
        return self._mode

    def store_on(self, name):
        return self._s[name]["enabled"] is True

    def section(self, name):
        return self._s[name]


class Harness:
    def __init__(self, cfg=None, responses=None):
        self.calls, self.records = [], []
        self.clock = 1000.0
        self.now_ct = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)
        self.responses = responses or (lambda endpoint, params: chain())
        FakeCal.state = "REGULAR"
        self.gw = ms.Gateway(
            fetch=self._fetch, config=cfg or Cfg(), calendar=FakeCal,
            record=lambda *a: self.records.append(a),
            clock=lambda: self.clock, now_ct=lambda: self.now_ct)

    def _fetch(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        out = self.responses(endpoint, params)
        if isinstance(out, Exception):
            raise out
        return out

    def outcomes(self):
        return [r[2] for r in self.records]


def P(frm="2026-10-05", to="2026-10-12", **kw):
    return {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
            "fromDate": frm, "toDate": to, **kw}


def body(served):
    return json.loads(served.body) if served.body is not None else served.data


# ---- mode: off -------------------------------------------------------------

def test_off_passes_every_request_through_and_stores_nothing():
    h = Harness(Cfg(mode="off"))
    h.gw.chains(P(), "a")
    h.gw.chains(P(), "a")
    assert len(h.calls) == 2 and h.outcomes() == ["upstream", "upstream"]
    assert h.gw.chain_store.lookup(ms.ChainKey.from_params(P()), max_age=99,
                                   now=h.clock, state="REGULAR") is None


def test_a_switched_off_store_is_off_even_when_the_mode_is_on():
    h = Harness(Cfg(chains__enabled=False))
    h.gw.chains(P(), "a")
    h.gw.chains(P(), "a")
    assert len(h.calls) == 2


# ---- mode: on --------------------------------------------------------------

def test_a_repeat_request_makes_no_call():
    h = Harness()
    first = h.gw.chains(P(), "a")
    h.clock += 10
    second = h.gw.chains(P(), "b")
    assert len(h.calls) == 1
    assert (first.kind, second.kind, second.age) == ("miss", "hit", 10.0)
    assert body(second) == chain()
    assert h.records == [("chains", "a", "upstream"), ("chains", "b", "hit")]


def test_a_narrower_window_makes_no_call():
    h = Harness()
    h.gw.chains(P(), "collector")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert len(h.calls) == 1 and got.kind == "subset"
    assert "2026-10-12:7" not in body(got)["callExpDateMap"]


def test_a_stale_entry_is_refetched():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 46
    assert h.gw.chains(P(), "a").kind == "miss" and len(h.calls) == 2


def test_the_callers_own_age_limit_wins():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 30
    assert h.gw.chains(P(), "collector", max_age=20).kind == "miss"
    h.clock += 100
    assert h.gw.chains(P(), "collector", max_age=210).kind == "hit"
    assert h.gw.chains(P(), "x", max_age=0).kind == "miss"


def test_a_nonsense_age_limit_falls_back_to_the_configured_one():
    h = Harness()
    h.gw.chains(P(), "a")
    h.clock += 10
    for bad in (float("nan"), -5, "soon"):
        assert h.gw.chains(P(), "a", max_age=bad).kind == "hit"


def test_closed_markets_use_the_longer_limit():
    h = Harness()
    FakeCal.state = "CLOSED"
    h.gw.chains(P(), "a")
    h.clock += 600
    assert h.gw.chains(P(), "a").kind == "hit"


def test_a_near_miss_fetches_the_wide_window_and_the_collector_then_hits():
    h = Harness()
    h.gw.chains(P(), "collector")                    # today -> +7 is now known
    h.clock += 50                                    # too old for the scan
    scan = h.gw.chains(P(to="2026-10-09"), "scan")
    assert h.calls[-1][1]["toDate"] == "2026-10-12"  # fetched the WIDE window
    assert "2026-10-12:7" not in body(scan)["callExpDateMap"]
    h.clock += 5
    assert h.gw.chains(P(), "collector", max_age=20).kind == "hit"
    assert len(h.calls) == 2


def test_a_strike_filtered_request_is_never_served_from_an_all_strikes_chain():
    h = Harness()
    h.gw.chains(P(), "collector")
    filtered = P(to="2026-10-09", range="NTM", strikeCount=50)
    assert h.gw.chains(filtered, "sentiment").kind == "miss"
    assert h.calls[-1][1]["strikeCount"] == 50
    assert h.gw.chains(filtered, "sentiment").kind == "hit"      # exact repeat


def test_an_upstream_error_is_raised_and_never_answered_from_a_stale_entry():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(502, "bad gateway")
                if state["fail"] else chain())
    h.gw.chains(P(), "a")
    h.clock += 100
    state["fail"] = True
    with pytest.raises(ms.UpstreamError) as err:
        h.gw.chains(P(), "a")
    assert err.value.status_code == 502


def test_a_store_bug_falls_through_to_a_plain_fetch_and_is_counted(monkeypatch):
    h = Harness()
    monkeypatch.setattr(h.gw.chain_store, "lookup",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = h.gw.chains(P(), "a")
    assert got.kind == "pass" and body(got) == chain()
    assert h.gw.degrades == {"chains": 1}


def test_two_concurrent_identical_misses_make_one_call():
    gate, started = threading.Event(), threading.Event()

    def slow(endpoint, params):
        started.set()
        gate.wait(5)
        return chain()

    h = Harness(responses=slow)
    kinds = []
    threads = [threading.Thread(target=lambda: kinds.append(h.gw.chains(P(), "a").kind))
               for _ in range(2)]
    threads[0].start()
    started.wait(5)
    threads[1].start()
    gate.set()
    for t in threads:
        t.join(5)
    assert len(h.calls) == 1 and sorted(kinds) == ["coalesced", "miss"]


def test_the_configured_entry_limit_is_enforced():
    h = Harness(Cfg(chains__max_entries=1))
    h.gw.chains(P(), "a")
    h.gw.chains({**P(), "symbol": "QQQ"}, "a")
    assert h.gw.chains(P(), "a").kind == "miss"          # SPY was dropped


# ---- mode: shadow ----------------------------------------------------------

def test_shadow_always_calls_schwab_and_reports_what_it_would_have_done():
    h = Harness(Cfg(mode="shadow"))
    h.gw.chains(P(), "collector")
    got = h.gw.chains(P(to="2026-10-09"), "scan")
    assert len(h.calls) == 2 and got.kind == "pass"
    assert h.outcomes() == ["upstream", "upstream", "shadow_subset_mismatch"]


def test_shadow_reports_a_match_when_the_cut_equals_what_schwab_sent():
    from test_market_store_chains import EXPS
    h = Harness(Cfg(mode="shadow"),
                responses=lambda e, p: chain(exps=EXPS if p["toDate"] == "2026-10-12"
                                             else EXPS[:3]))
    h.gw.chains(P(), "collector")
    h.gw.chains(P(to="2026-10-09"), "scan")
    assert h.outcomes()[-1] == "shadow_subset_match"
```

Note on `test_shadow_always_calls_schwab...`: the fake returns the full four-expiration chain for the narrow request too, so the cut (three expirations) differs from what "Schwab" sent and the verdict is `mismatch`. The next test makes the fake honour the window and gets `match`.

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_gateway.py -q)`
Expected: FAIL, `module 'market_store' has no attribute 'Gateway'`.

**Step 3: Implement** — add `import logging` and `import time` at the top of `market_store.py`, then append:

```python
#############################################
# GATEWAY
#############################################

def effective_max_age(requested, cfg, *, closed: bool) -> float:
    """The caller's ``maxAge`` when it is a usable number, else the configured
    limit for the current market state."""
    if requested is not None:
        try:
            value = float(requested)
        except (TypeError, ValueError):
            value = float("nan")
        if math.isfinite(value) and value >= 0:
            return value
    return float(cfg["closed_max_age_sec"] if closed else cfg["max_age_sec"])


class KeyedLocks:
    """One lock per request identity, so identical concurrent misses make one
    upstream call. Keys carry dates, so the map grows by a few hundred a day;
    the proxy restarts on every promote, long before that matters."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict = {}

    def get(self, key) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())


class Gateway:
    """Decides, per request, between a stored answer and a call to Schwab.

    ``fetch(endpoint, params)`` returns Schwab's JSON or raises
    ``UpstreamError``. ``config`` is ``shared.marketdata_config`` (``mode()``,
    ``store_on(name)``, ``section(name)``). ``calendar`` is
    ``shared.market_calendar``. ``record(endpoint, caller, outcome)`` is the
    detail counter.

    Two rules hold everywhere: an upstream error is raised, never papered over
    with an old entry; and a bug in store code falls through to a plain fetch
    and is counted in ``degrades``."""

    def __init__(self, *, fetch, config, calendar, record,
                 clock=time.time, now_ct=None, log=None):
        self._fetch, self._cfg, self._cal = fetch, config, calendar
        self._record, self._clock = record, clock
        self._now_ct = now_ct or (lambda: datetime.now(CT))
        self._log = log or logging.getLogger("market_store")
        self._locks = KeyedLocks()
        self.chain_store = ChainStore()
        self.quote_store = QuoteStore()
        self.bar_store = BarStore()
        self.degrades: dict = {}

    # ---- shared ----------------------------------------------------------
    def _mode(self, store: str) -> str:
        try:
            return self._cfg.mode() if self._cfg.store_on(store) else "off"
        except Exception:  # noqa: BLE001 — unreadable config means no store.
            return "off"

    def _passthrough(self, label, endpoint, params, caller) -> Served:
        data = self._fetch(endpoint, params)
        self._record(label, caller, "upstream")
        return Served("pass", 0.0, data=data)

    def _degraded(self, area: str) -> None:
        self.degrades[area] = self.degrades.get(area, 0) + 1
        self._log.warning("market store degraded in %s; fetching directly",
                          area, exc_info=True)

    def _guarded(self, label, endpoint, params, caller, work) -> Served:
        try:
            return work()
        except UpstreamError:
            raise
        except Exception:  # noqa: BLE001 — a store bug must not take data down.
            self._degraded(label)
            return self._passthrough(label, endpoint, params, caller)

    # ---- chains ----------------------------------------------------------
    def chains(self, params, caller, max_age=None) -> Served:
        mode = self._mode("chains")
        if mode == "off":
            return self._passthrough("chains", "/chains", params, caller)
        return self._guarded("chains", "/chains", params, caller,
                             lambda: self._chains(params, caller, max_age, mode))

    def _chains(self, params, caller, max_age, mode) -> Served:
        cfg = self._cfg.section("chains")
        now_ct = self._now_ct()
        state = self._cal.session_at(now_ct).name
        key = ChainKey.from_params(params)
        store = self.chain_store

        if mode == "shadow":
            would = store.lookup(key, max_age=float(cfg["shadow_compare_max_age_sec"]),
                                 now=self._clock(), state=state)
            data = self._fetch("/chains", params)
            self._record("chains", caller, "upstream")
            if would is not None:
                same = chain_shape(json.loads(would.body)) == chain_shape(data)
                verdict = "match" if same else "mismatch"
                self._record("chains", caller, f"shadow_{would.kind}_{verdict}")
                if not same:
                    self._log.warning("shadow: stored %s answer for %s differs "
                                      "from Schwab's", would.kind, key)
            store.put(key, data, now=self._clock(), state=state,
                      max_entries=cfg["max_entries"])
            return Served("pass", 0.0, data=data)

        limit = effective_max_age(max_age, cfg, closed=(state == "CLOSED"))
        hit = store.lookup(key, max_age=limit, now=self._clock(), state=state)
        if hit is not None:
            self._record("chains", caller, hit.kind)
            return hit
        wide = store.wide_key(key, today=now_ct.date())
        fetch_key = wide or key
        with self._locks.get(("chains", fetch_key)):
            again = store.lookup(key, max_age=limit, now=self._clock(), state=state)
            if again is not None:
                self._record("chains", caller, "coalesced")
                return Served("coalesced", again.age, body=again.body)
            data = self._fetch("/chains", fetch_key.params())
            store.put(fetch_key, data, now=self._clock(), state=state,
                      max_entries=cfg["max_entries"])
            self._record("chains", caller, "upstream")
        if wide is None:
            return Served("miss", 0.0, data=data)
        cut = store.cut(wide, key)
        if cut is not None:
            return Served("miss", 0.0, body=cut)
        # The wide chain came back in a shape the store would not keep: answer
        # the request exactly as it was asked.
        return self._passthrough("chains", "/chains", params, caller)
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_gateway.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_gateway.py
git commit -m "feat(proxy): gateway for chains with shadow mode and coalescing"
```

---

### Task 9: The gateway — quotes and daily bars

**Files:**
- Modify: `schwab-proxy/market_store.py` (`Gateway`)
- Test: `schwab-proxy/tests/test_market_gateway.py`

**Step 1: Write the failing tests** (append)

```python
# ---- quotes ----------------------------------------------------------------

def quotes_for(endpoint, params):
    return {s: {"quote": {"lastPrice": 100.0, "openPrice": 99.0, "highPrice": 101.0,
                          "lowPrice": 98.0, "totalVolume": 10}}
            for s in params["symbols"].split(",")}


def test_fresh_quotes_make_no_call():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY,QQQ,IWM", "market_svc")
    h.clock += 3
    got = h.gw.quotes("QQQ,SPY", "sentiment_svc")
    assert len(h.calls) == 1 and got.kind == "hit"
    assert list(got.data) == ["QQQ", "SPY"]              # the caller's order
    assert h.outcomes() == ["upstream", "hit"]


def test_only_the_stale_and_missing_symbols_are_fetched():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY,QQQ", "a")
    h.clock += 3
    got = h.gw.quotes("SPY,DIA,QQQ", "b")
    assert h.calls[-1][1] == {"symbols": "DIA", "fields": "quote"}
    assert got.kind == "partial" and set(got.data) == {"SPY", "DIA", "QQQ"}
    assert h.outcomes()[-1] == "partial"


def test_the_dashboard_polls_own_limit_stops_it_rereading_itself():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY", "market_svc", max_age=1)
    h.clock += 3
    assert h.gw.quotes("SPY", "market_svc", max_age=1).kind == "miss"
    assert len(h.calls) == 2


def test_duplicate_and_blank_symbols_are_cleaned():
    h = Harness(responses=quotes_for)
    h.gw.quotes("SPY, SPY,,QQQ ", "a")
    assert h.calls[-1][1]["symbols"] == "SPY,QQQ"


def test_quotes_in_shadow_always_call_and_count_would_be_hits():
    h = Harness(Cfg(mode="shadow"), responses=quotes_for)
    h.gw.quotes("SPY", "a")
    h.gw.quotes("SPY", "b")
    assert len(h.calls) == 2
    assert h.outcomes() == ["upstream", "upstream", "shadow_hit"]


def test_an_upstream_quote_error_is_raised_even_when_some_symbols_were_fresh():
    state = {"fail": False}
    h = Harness(responses=lambda e, p: ms.UpstreamError(429, "slow down")
                if state["fail"] else quotes_for(e, p))
    h.gw.quotes("SPY", "a")
    state["fail"] = True
    with pytest.raises(ms.UpstreamError):
        h.gw.quotes("SPY,DIA", "a")


# ---- daily bars ------------------------------------------------------------

BAR = {"symbol": "SPY", "periodType": "year", "period": 1,
       "frequencyType": "daily", "frequency": 1, "needExtendedHoursData": "false"}
MON = dt.date(2026, 10, 5)
FRI = dt.date(2026, 10, 2)


def bars_for(endpoint, params):
    return quotes_for(endpoint, params) if endpoint == "/quotes" else series(FRI, MON)


def test_a_repeat_daily_request_in_the_same_period_makes_no_call():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 600
    assert h.gw.pricehistory(BAR, "sentiment").kind == "hit"
    assert len(h.calls) == 1


def test_during_the_session_ttl_mode_refetches_past_the_limit():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 1741
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"


def test_after_the_bar_settles_one_refetch_then_hits_all_evening():
    h = Harness(responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.now_ct = dt.datetime(2026, 10, 5, 15, 11, tzinfo=CT)
    assert h.gw.pricehistory(BAR, "x").kind == "miss"
    h.clock += 20000
    assert h.gw.pricehistory(BAR, "x").kind == "hit"
    assert len(h.calls) == 2


def test_quote_mode_builds_todays_bar_and_makes_no_call():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.gw.quotes("SPY", "market_svc")
    h.clock += 5000                                   # far past any ttl
    h.gw.quotes("SPY", "market_svc", max_age=0)       # the poll keeps it fresh
    got = h.gw.pricehistory(BAR, "scan")
    assert got.kind == "composed" and got.data["candles"][-1]["close"] == 100.0
    assert [c[0] for c in h.calls].count("/pricehistory") == 1


def test_quote_mode_without_a_fresh_quote_fetches():
    h = Harness(Cfg(bars__today_bar="quote"), responses=bars_for)
    h.gw.pricehistory(BAR, "scan")
    h.clock += 5000
    assert h.gw.pricehistory(BAR, "scan").kind == "miss"


def test_intraday_and_extended_hours_requests_pass_straight_through():
    h = Harness(responses=bars_for)
    minute = {**BAR, "periodType": "day", "period": 10, "frequencyType": "minute",
              "frequency": 5}
    ext = {**BAR, "needExtendedHoursData": "true"}
    for params in (minute, minute, ext, ext):
        assert h.gw.pricehistory(params, "x").kind == "pass"
    assert len(h.calls) == 4


def test_bars_in_shadow_count_repeats_and_judge_the_quote_built_bar():
    h = Harness(Cfg(mode="shadow"), responses=bars_for)
    h.gw.quotes("SPY", "market_svc")
    h.gw.pricehistory(BAR, "scan")
    h.gw.pricehistory(BAR, "scan")
    assert [c[0] for c in h.calls].count("/pricehistory") == 2
    out = [r[2] for r in h.records if r[0] == "pricehistory"]
    assert out == ["upstream", "shadow_bar_match",
                   "upstream", "shadow_hit", "shadow_bar_match"]
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_gateway.py -q)`
Expected: the new tests FAIL, `'Gateway' object has no attribute 'quotes'`.

**Step 3: Implement** — add to `Gateway`:

```python
    # ---- quotes ----------------------------------------------------------
    def quotes(self, symbols_csv, caller, max_age=None) -> Served:
        params = {"symbols": symbols_csv, "fields": "quote"}
        mode = self._mode("quotes")
        if mode == "off":
            return self._passthrough("quotes", "/quotes", params, caller)
        return self._guarded("quotes", "/quotes", params, caller,
                             lambda: self._quotes(symbols_csv, caller, max_age, mode))

    def _quotes(self, symbols_csv, caller, max_age, mode) -> Served:
        cfg = self._cfg.section("quotes")
        symbols = list(dict.fromkeys(
            s for s in (part.strip() for part in str(symbols_csv).split(",")) if s))
        limit = effective_max_age(
            max_age, {"max_age_sec": cfg["max_age_sec"],
                      "closed_max_age_sec": cfg["max_age_sec"]}, closed=False)
        store = self.quote_store
        fresh, missing, oldest = store.split(symbols, max_age=limit, now=self._clock())

        def call(wanted):
            return self._fetch("/quotes", {"symbols": ",".join(wanted),
                                           "fields": "quote"})

        if mode == "shadow":
            data = call(symbols)
            self._record("quotes", caller, "upstream")
            if not missing:
                self._record("quotes", caller, "shadow_hit")
            store.put_many(data, now=self._clock())
            return Served("pass", 0.0, data=data)

        if not missing:
            self._record("quotes", caller, "hit")
            return Served("hit", oldest, data={s: fresh[s] for s in symbols})
        data = call(missing)
        store.put_many(data, now=self._clock())
        kind = "partial" if fresh else "miss"
        self._record("quotes", caller, "partial" if fresh else "upstream")
        if not isinstance(data, dict):
            return Served(kind, 0.0, data=data)
        return Served(kind, 0.0, data={**fresh, **data})

    # ---- daily bars ------------------------------------------------------
    def pricehistory(self, params, caller) -> Served:
        daily = (str(params.get("frequencyType")) == "daily"
                 and str(params.get("needExtendedHoursData", "false")).lower() == "false")
        mode = self._mode("bars") if daily else "off"
        if mode == "off":
            return self._passthrough("pricehistory", "/pricehistory", params, caller)
        return self._guarded("pricehistory", "/pricehistory", params, caller,
                             lambda: self._bars(params, caller, mode))

    def _bars(self, params, caller, mode) -> Served:
        cfg = self._cfg.section("bars")
        now_ct = self._now_ct()
        epoch = bar_epoch(now_ct, float(cfg["settle_min"]), self._cal)
        live = epoch[1] == "live"
        key = bar_key(params)
        symbol = key[0]
        store = self.bar_store
        quote_age = float(cfg["today_quote_max_age_sec"])

        if mode == "shadow":
            had = store.get(key, epoch=epoch)
            data = self._fetch("/pricehistory", params)
            self._record("pricehistory", caller, "upstream")
            if had is not None:
                self._record("pricehistory", caller, "shadow_hit")
            if live:
                quote = self.quote_store.get(symbol, max_age=quote_age,
                                             now=self._clock())
                verdict = compare_today_bar(data, quote, now_ct.date())
                if verdict != "no_quote":
                    self._record("pricehistory", caller, f"shadow_bar_{verdict}")
            store.put(key, data, now=self._clock(), epoch=epoch)
            return Served("pass", 0.0, data=data)

        seen = store.get(key, epoch=epoch)
        if seen is not None:
            body, fetched_at = seen
            age = self._clock() - fetched_at
            if not live:
                self._record("pricehistory", caller, "hit")
                return Served("hit", age, body=body)
            if cfg["today_bar"] == "quote":
                quote = self.quote_store.get(symbol, max_age=quote_age,
                                             now=self._clock())
                composed = (compose_today(json.loads(body), quote, now_ct.date())
                            if quote is not None else None)
                if composed is not None:
                    self._record("pricehistory", caller, "composed")
                    return Served("composed", 0.0, data=composed)
            elif age <= float(cfg["session_ttl_sec"]):
                self._record("pricehistory", caller, "hit")
                return Served("hit", age, body=body)
        with self._locks.get(("bars", key)):
            again = store.get(key, epoch=epoch)
            if again is not None and (seen is None or again[1] > seen[1]):
                self._record("pricehistory", caller, "coalesced")
                return Served("coalesced", self._clock() - again[1], body=again[0])
            data = self._fetch("/pricehistory", params)
            store.put(key, data, now=self._clock(), epoch=epoch)
            self._record("pricehistory", caller, "upstream")
        return Served("miss", 0.0, data=data)
```

**Step 4: Run to verify they pass**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_gateway.py tests/test_market_store_chains.py tests/test_market_store_quotes.py tests/test_market_store_bars.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add schwab-proxy/market_store.py schwab-proxy/tests/test_market_gateway.py
git commit -m "feat(proxy): gateway for quotes and daily bars"
```

---

### Task 10: Wire the proxy's handlers to the gateway

**Files:**
- Modify: `schwab-proxy/schwab_proxy.py` (imports near line 52; handlers at lines 625–683; `/stats/api_calls` near line 492)
- Test: `schwab-proxy/tests/test_market_handlers.py`

**Step 1: Write the failing tests**

```python
"""The three market-data handlers are thin adapters over the gateway."""
import json
import pathlib
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402
import schwab_proxy  # noqa: E402


class Req:
    def __init__(self, caller=None):
        self.headers = {"x-caller": caller} if caller else {}


class FakeGateway:
    def __init__(self, served=None, error=None):
        self.served, self.error, self.seen = served, error, []
        self.degrades = {"chains": 2}

    def _answer(self, *args):
        self.seen.append(args)
        if self.error:
            raise self.error
        return self.served

    def chains(self, params, caller, max_age=None):
        return self._answer("chains", params, caller, max_age)

    def quotes(self, symbols, caller, max_age=None):
        return self._answer("quotes", symbols, caller, max_age)

    def pricehistory(self, params, caller):
        return self._answer("pricehistory", params, caller)


@pytest.fixture
def gw(monkeypatch):
    def install(**kw):
        g = FakeGateway(**kw)
        monkeypatch.setattr(schwab_proxy, "_GATEWAY", g)
        return g
    return install


def test_chains_passes_the_request_through_unchanged(gw):
    g = gw(served=ms.Served("pass", 0.0, data={"ok": 1}))
    out = schwab_proxy.get_option_chain(
        Req("options_svc"), symbol="SPY", contractType="ALL", range="ALL",
        fromDate="2026-10-05", toDate="2026-10-12", strikeCount=None, maxAge=20.0)
    kind, params, caller, max_age = g.seen[0]
    assert params == {"symbol": "SPY", "contractType": "ALL", "range": "ALL",
                      "fromDate": "2026-10-05", "toDate": "2026-10-12"}
    assert (caller, max_age) == ("options_svc", 20.0)
    assert json.loads(out.body) == {"ok": 1}
    assert out.headers["x-store"] == "pass" and out.headers["x-store-age"] == "0.0"


def test_a_stored_body_is_sent_as_is_with_its_age(gw):
    gw(served=ms.Served("subset", 12.34, body=b'{"a":1}'))
    out = schwab_proxy.get_option_chain(
        Req(), symbol="SPY", contractType="ALL", range="ALL",
        fromDate=None, toDate=None, strikeCount=None, maxAge=None)
    assert out.body == b'{"a":1}' and out.media_type == "application/json"
    assert out.headers["x-store"] == "subset" and out.headers["x-store-age"] == "12.3"


def test_a_missing_caller_header_is_unknown(gw):
    g = gw(served=ms.Served("pass", 0.0, data={}))
    schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    assert g.seen[0][2] == "unknown"


def test_single_quote_uses_the_same_store_as_the_batch(gw):
    g = gw(served=ms.Served("hit", 1.0, data={"SPY": {}}))
    schwab_proxy.get_quote(Req("x"), symbol="SPY", maxAge=None)
    assert g.seen[0][:2] == ("quotes", "SPY")


def test_pricehistory_sends_the_same_parameters_as_before(gw):
    g = gw(served=ms.Served("pass", 0.0, data={}))
    schwab_proxy.get_price_history(
        Req("x"), symbol="SPY", periodType="year", period=1,
        frequencyType="daily", frequency=1, needExtendedHoursData=False)
    assert g.seen[0][1] == {"symbol": "SPY", "periodType": "year", "period": 1,
                            "frequencyType": "daily", "frequency": 1,
                            "needExtendedHoursData": "false"}


def test_an_upstream_error_becomes_the_same_http_error_as_before(gw):
    gw(error=ms.UpstreamError(429, "slow down"))
    with pytest.raises(HTTPException) as err:
        schwab_proxy.get_quotes(Req(), symbols="SPY", maxAge=None)
    assert err.value.status_code == 429 and err.value.detail == "slow down"


def test_the_real_fetch_raises_upstream_error_on_a_non_200(monkeypatch):
    monkeypatch.setattr(schwab_proxy.token_mgr, "api_request",
                        lambda endpoint, params=None: {
                            "status_code": 400, "data": None, "error": "bad"})
    with pytest.raises(ms.UpstreamError) as err:
        schwab_proxy._upstream("/chains", {"symbol": "X"})
    assert (err.value.status_code, err.value.detail) == (400, "bad")


def test_stats_carry_the_local_breakdown_and_degrades(gw):
    gw(served=None)
    out = schwab_proxy.api_call_stats()
    assert {"today", "last_7_days", "last_30_days", "since"} <= set(out)
    assert set(out["store"]) == {"served_locally", "by_outcome", "rows"}
    assert out["store_degrades"] == {"chains": 2}
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_market_handlers.py -q)`
Expected: FAIL, `module 'schwab_proxy' has no attribute '_GATEWAY'`.

If `schwab_proxy.token_mgr` does not exist as a module attribute under pytest, read how `tests/test_retry.py` builds its stand-in and adapt the one test that needs it; do not skip it.

**Step 3: Implement**

Imports: change the FastAPI import to add `Request`, add `Response` to the responses import, and add below `import stream_bridge`:

```python
import market_store
from shared import market_calendar as _market_calendar
from shared import marketdata_config as _marketdata_config
```

Above `# MARKETDATA PROXY ENDPOINTS`, after `token_mgr` exists:

```python
#############################################
# LOCAL MARKET-DATA STORE
#############################################
# Design: docs/plans/2026-10-03-market-data-store-design.md. The handlers below
# are adapters; every decision lives in market_store.Gateway.

def _upstream(endpoint: str, params: Optional[Dict] = None):
    """One marketdata call to Schwab, or ``UpstreamError`` on a non-200."""
    result = token_mgr.api_request(endpoint, params=params)
    if result["status_code"] != 200:
        raise market_store.UpstreamError(result["status_code"], result["error"])
    return result["data"]


_GATEWAY = market_store.Gateway(
    fetch=_upstream, config=_marketdata_config, calendar=_market_calendar,
    record=api_call_counter.record_detail, log=logger)


def _caller(request) -> str:
    """Who is asking, from the ``X-Caller`` header the clients set."""
    return (request.headers.get("x-caller") or "unknown")[:40]


def _send(served) -> Response:
    """A gateway answer as an HTTP response, labelled with where it came from
    and how old it is."""
    body = served.body if served.body is not None else json.dumps(
        served.data, separators=(",", ":")).encode()
    return Response(content=body, media_type="application/json",
                    headers={"X-Store": served.kind,
                             "X-Store-Age": f"{served.age:.1f}"})


def _served(work) -> Response:
    try:
        return _send(work())
    except market_store.UpstreamError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
```

Replace the four handlers:

```python
@app.get("/quote")
def get_quote(request: Request, symbol: str, maxAge: Optional[float] = None):
    return _served(lambda: _GATEWAY.quotes(symbol, _caller(request), maxAge))


@app.get("/quotes")
def get_quotes(request: Request,
               symbols: str = Query(..., description="Comma-separated symbols"),
               maxAge: Optional[float] = None):
    return _served(lambda: _GATEWAY.quotes(symbols, _caller(request), maxAge))


@app.get("/chains")
def get_option_chain(
    request: Request,
    symbol: str,
    contractType: str = "ALL",
    range: str = Query("ALL", alias="range"),
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    strikeCount: Optional[int] = None,
    maxAge: Optional[float] = None,
):
    params: Dict[str, Any] = {"symbol": symbol, "contractType": contractType, "range": range}
    if fromDate:    params["fromDate"] = fromDate
    if toDate:      params["toDate"] = toDate
    if strikeCount is not None: params["strikeCount"] = strikeCount
    return _served(lambda: _GATEWAY.chains(params, _caller(request), maxAge))
```

In `get_price_history`, add `request: Request` as the first parameter, keep the `params` dict and its comment exactly as they are, and replace the last four lines with:

```python
    return _served(lambda: _GATEWAY.pricehistory(params, _caller(request)))
```

`/stats/api_calls`:

```python
    return {**api_call_counter.stats(),
            "store": api_call_counter.detail_summary(),
            "store_degrades": dict(_GATEWAY.degrades)}
```

The proxy's own `_track` (near line 1064) calls `token_mgr.api_request("/chains", ...)` directly. Leave it: it is a single-expiration fetch on paper-trade open, a handful a day.

**Step 4: Run the proxy suite**

Run: `(cd schwab-proxy && "$PY" -m pytest tests -q -rf)`
Expected: the new file passes and the failing set equals the Task-0 baseline. The tracked config ships `mode = "shadow"`, so under pytest every handler still calls its fake upstream.

**Step 5: Commit**

```bash
git add schwab-proxy/schwab_proxy.py schwab-proxy/tests/test_market_handlers.py
git commit -m "feat(proxy): route chains, quotes and price history through the store"
```

---

### Task 11: Clients — say who is calling, pass an age limit, read the age back

**Files:**
- Modify: `schwab-proxy/proxy_client.py` (`FakeResponse` line 72, `_apply_secret` line 59, both `__init__`, `_get`, `get_option_chain`)
- Modify: `services/market_svc/compute.py:27-43` (`fetch_raw_quotes`)
- Test: `schwab-proxy/tests/test_proxy_client_store.py`, `services/market_svc/tests/` (existing)

**Step 1: Write the failing tests** — `schwab-proxy/tests/test_proxy_client_store.py`:

```python
"""The client tells the proxy who it is, may ask for an age limit, and reports
how old the answer was."""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import proxy_client  # noqa: E402


class _Resp:
    status_code = 200

    def __init__(self, headers=None):
        self.headers = headers or {}

    def json(self):
        return {"ok": True}


class _Session:
    def __init__(self, headers=None):
        self.headers, self.sent, self._reply = {}, [], headers

    def get(self, url, params=None, timeout=None):
        self.sent.append((url, params))
        return _Resp(self._reply)


def _client(reply_headers=None):
    c = proxy_client.SchwabPyProxyClient("http://proxy")
    c.session = _Session(reply_headers)
    return c


def test_caller_name_is_the_service_folder_for_an_app_entrypoint(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/services/options_svc/app.py"])
    assert proxy_client._caller_name() == "options_svc"


def test_caller_name_is_the_script_name_for_a_tool(monkeypatch):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/tools/flow_delta_instrumentation.py"])
    assert proxy_client._caller_name() == "flow_delta_instrumentation"


def test_caller_name_can_be_set_by_environment(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "label_journal")
    assert proxy_client._caller_name() == "label_journal"


def test_both_clients_send_the_caller_header(monkeypatch):
    monkeypatch.setenv("TRADING_CALLER", "probe")
    assert proxy_client.SchwabPyProxyClient("http://p").session.headers["X-Caller"] == "probe"
    assert proxy_client.SchwabProxyClient("http://p").session.headers["X-Caller"] == "probe"


def test_no_age_limit_is_sent_unless_asked_for():
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL",
                       from_date=dt.date(2026, 10, 5), to_date=dt.date(2026, 10, 12))
    assert "maxAge" not in c.session.sent[0][1]


def test_an_age_limit_is_sent_when_asked_for():
    c = _client()
    c.get_option_chain("SPY", contract_type="ALL", max_age=210)
    assert c.session.sent[0][1]["maxAge"] == 210


def test_the_answers_age_and_kind_are_readable():
    r = _client({"X-Store": "subset", "X-Store-Age": "12.5"}).get_option_chain("SPY")
    assert (r.store_kind, r.store_age) == ("subset", 12.5)


def test_an_answer_without_store_headers_reports_no_age():
    r = _client({}).get_option_chain("SPY")
    assert r.store_kind is None and r.store_age is None


def test_a_garbled_age_header_reports_no_age():
    assert _client({"X-Store-Age": "soon"}).get_option_chain("SPY").store_age is None
```

**Step 2: Run to verify they fail**

Run: `(cd schwab-proxy && "$PY" -m pytest tests/test_proxy_client_store.py -q)`
Expected: FAIL, `module 'proxy_client' has no attribute '_caller_name'`.

**Step 3: Implement** in `proxy_client.py`.

After `_apply_secret`:

```python
def _caller_name() -> str:
    """Who this process is, for the proxy's per-caller counts: the env override,
    else the service folder for ``services/<name>/app.py``, else the script
    name. Never raises."""
    env = os.environ.get("TRADING_CALLER")
    if env and env.strip():
        return env.strip()[:40]
    try:
        entry = pathlib.Path(sys.argv[0])
        name = entry.parent.name if entry.name == "app.py" else entry.stem
        return (name or "unknown")[:40]
    except Exception:  # noqa: BLE001 — identity is a label, never a failure.
        return "unknown"


def _apply_identity(session: "requests.Session") -> None:
    session.headers["X-Caller"] = _caller_name()


def _store_age(headers):
    """Seconds since the answer left Schwab, from ``X-Store-Age``; None when the
    proxy did not say."""
    try:
        return float(headers.get("X-Store-Age"))
    except (TypeError, ValueError, AttributeError):
        return None
```

`FakeResponse`: add two fields after `_text`:

```python
    # Set by the proxy's local store: "hit" / "subset" / "miss" / ... and how
    # many seconds ago the data left Schwab. None from an older proxy.
    store_kind: Optional[str] = None
    store_age: Optional[float] = None
```

Both `__init__` methods: add `_apply_identity(self.session)` after `_apply_secret(self.session)`.

`SchwabPyProxyClient._get`, the 200 branch:

```python
                headers = getattr(resp, "headers", None) or {}
                return FakeResponse(status_code=200, _data=resp.json(),
                                    store_kind=headers.get("X-Store"),
                                    store_age=_store_age(headers))
```

`get_option_chain`: add `max_age=None,` before `**kwargs` and, before the `return`:

```python
        if max_age is not None:
            params["maxAge"] = max_age
```

`services/market_svc/compute.py`, `fetch_raw_quotes`:

```python
        # maxAge=1: this poll runs every three seconds, so with the proxy's
        # default limit it would re-read its own previous answer. It still
        # reuses a quote some other caller fetched within the last second.
        resp = _SESSION.get(f"{PROXY_URL}/quotes",
                            params={"symbols": ",".join(syms), "maxAge": 1},
                            headers={"X-Caller": "market_svc"}, timeout=timeout)
```

**Step 4: Run the suites**

```bash
(cd schwab-proxy && "$PY" -m pytest tests -q -rf)
"$PY" -m pytest services/market_svc -q -rf
```

Expected: the new file passes. In `services/market_svc`, a test that pins the exact `params` or call arguments of `fetch_raw_quotes` will now fail; that is the one intended change. Update it to expect `maxAge: 1` and the header. Any other new failure is real.

**Step 5: Commit**

```bash
git add schwab-proxy/proxy_client.py schwab-proxy/tests/test_proxy_client_store.py services/market_svc/compute.py services/market_svc/tests
git commit -m "feat(proxy-client): caller name, optional age limit, answer age"
```

---

### Task 12: Settings shows calls answered locally

**Files:**
- Modify: `webgui/pages/settings.py:79-93` (`api_stats_rows`)
- Test: `webgui/tests/test_settings.py`

**Step 1: Write the failing tests** (append)

```python
def test_api_stats_rows_show_requests_answered_locally():
    from pages import settings
    rows = settings.api_stats_rows({"today": 70000, "last_7_days": 1, "last_30_days": 1,
                                    "store": {"served_locally": 14250}})
    assert ("Answered locally today", "14,250") in rows


def test_api_stats_rows_omit_the_local_row_for_an_older_proxy():
    from pages import settings
    rows = settings.api_stats_rows({"today": 1, "last_7_days": 1, "last_30_days": 1})
    assert [label for label, _ in rows] == ["Today", "Last 7 days", "Last 30 days"]


def test_api_stats_rows_survive_a_malformed_store_block():
    from pages import settings
    for bad in (5, {"served_locally": "many"}, {"served_locally": True}, {}):
        rows = settings.api_stats_rows({"today": 1, "last_7_days": 1,
                                        "last_30_days": 1, "store": bad})
        assert len(rows) == 3
```

Match the import style the two existing `api_stats_rows` tests at the top of that file use.

**Step 2: Run to verify they fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_settings.py -q -k api_stats)`
Expected: the first new test FAILS.

**Step 3: Implement** — in `api_stats_rows`, replace the final `return`:

```python
    rows = [("Today", _fmt("today")), ("Last 7 days", _fmt("last_7_days")),
            ("Last 30 days", _fmt("last_30_days"))]
    # Requests the proxy answered from data it already held, so they are NOT in
    # the counts above. Absent from an older proxy: no row rather than a zero
    # nobody measured.
    store = stats.get("store")
    local = store.get("served_locally") if isinstance(store, dict) else None
    if isinstance(local, int) and not isinstance(local, bool):
        rows.append(("Answered locally today", f"{local:,}"))
    return rows
```

**Step 4: Run to verify they pass**

Run: `(cd webgui && "$PY" -m pytest tests/test_settings.py -q -rf)`
Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/settings.py webgui/tests/test_settings.py
git commit -m "feat(settings): show requests the proxy answered locally"
```

---

### Task 13: Ship in shadow and read one session (operator checkpoint)

**This task touches prod. Stop and get the operator's go-ahead before the push.**

**Step 1: Full local verification.** Re-run every baseline command from "Before you start" and diff the failing sets. Also:

```bash
"$PY" -m pyright
```

Expected: no new failures, pyright clean.

**Step 2: Land it.** With the operator's go-ahead: fast-forward `main` to this branch, push `main`, then promote inside the 15:25–16:15 CT window (a promote stops the whole stack):

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

Nothing goes after that command. No new dependency was added, so `requirements.lock` is untouched.

**Step 3: Confirm shadow is live and harmless.**

```bash
ssh vps2 'curl -s http://127.0.0.1:8100/stats/api_calls'
```

Expected: a `store` block and `"store_degrades": {}`. If `store_degrades` is non-empty, read `journalctl --user -u trading-prod-proxy | grep "market store degraded"` and fix before going further.

**Step 4: After one full session, read the counts.**

```bash
ssh vps2 'curl -s http://127.0.0.1:8100/stats/api_calls' > /tmp/shadow.json
```

Record in the design doc, under a new "Shadow results" heading:

| Question | Read from | Pass |
|---|---|---|
| Does a cut chain hold the contracts Schwab returns for the narrower window? | `shadow_subset_match` vs `shadow_subset_mismatch` on `chains` | mismatches are zero, or each one is explained from the proxy journal |
| Does an exact repeat match? | `shadow_hit_match` vs `shadow_hit_mismatch` | mismatches only where a new expiration listed between fetches |
| Does the quote-built bar agree with Schwab's? | `shadow_bar_match` / `shadow_bar_mismatch` / `shadow_bar_no_today` | decides `bars.today_bar` |
| How many calls would have been saved? | `shadow_*` totals per caller | replaces the design doc's estimates |

If `shadow_bar_no_today` dominates, Schwab's daily series does not carry the bar in progress; then `today_bar = "ttl"` with a long `session_ttl_sec` is already exact, and say so in the doc.

**Step 5: Commit the results**

```bash
git add docs/plans/2026-10-03-market-data-store-design.md
git commit -m "docs: shadow-mode results for the market-data store"
```

---

## Phase 1 — switch the stores on

### Task 14: Turn on bars, then chains, then quotes (operator checkpoint)

**No code. Each switch is the operator's, made in Settings → Configuration → Local market data, which writes `config/local/marketdata.toml`. The proxy reads it on the next request; no restart.**

Order, one per session so each effect is readable on its own:

1. `chains.enabled = false`, `quotes.enabled = false`, `mode = on` → only bars are live. If Task 13 showed the quote-built bar matches, also set `bars.today_bar = quote`.
2. `chains.enabled = true`.
3. `quotes.enabled = true`.

After each, check:

```bash
ssh vps2 'curl -s http://127.0.0.1:8100/stats/api_calls'
ssh vps2 'journalctl --user -u trading-prod-options_svc --since today | grep -c "still running"'
```

Expected: `today` falls by roughly the phase-1 estimate (about 14,000 with all three on), `store_degrades` stays empty, and the skipped-slot count is no higher than the day before. Open Dealer Positioning, the Market Scanner and Sentiment and confirm each still updates.

**Rollback at any point:** set `mode = off`.

Once all three have run a clean session, change the tracked `config/marketdata.toml` and `DEFAULTS` to `mode = "on"` (and `today_bar` if it was flipped), update `test_ships_dark` to the new shipped values, run `shared/tests`, the proxy suite and `services/options_svc`, and commit:

```bash
git add config/marketdata.toml shared/marketdata_config.py shared/tests/test_marketdata_config.py
git commit -m "feat(proxy): the market-data store is on by default"
```

A proxy test that relied on shadow pass-through may now hit the store. Fix it by giving it a fresh `_GATEWAY` (as `test_market_handlers.py` does), not by turning the store off globally.

---

## Phase 2 — one wide chain per scan symbol

### Task 15: Cut a chain to a window (pure)

**Files:**
- Modify: `options-scanner/scanner_engine.py` (beside `fetch_option_chain`, line 155)
- Test: `options-scanner/tests/test_scan_wide_fetch.py`

**Step 1: Write the failing tests**

```python
"""The autoscan can fetch one wide chain and cut its three windows locally."""
import datetime as dt

import scanner_engine as se

D = dt.date(2026, 10, 5)


def _chain():
    days = [0, 2, 4, 7, 14, 21, 30, 44]
    exp = {f"{(D + dt.timedelta(days=n)).isoformat()}:{n}": {"100.0": [{"x": n}]}
           for n in days}
    return {"symbol": "AAPL", "underlyingPrice": 100.0,
            "callExpDateMap": dict(exp), "putExpDateMap": dict(exp)}


def _dtes(chain, side="callExpDateMap"):
    return sorted(int(k.split(":")[1]) for k in chain[side])


def test_a_window_keeps_only_its_expirations_on_both_sides():
    out = se.slice_chain(_chain(), D, D + dt.timedelta(days=4))
    assert _dtes(out) == [0, 2, 4] and _dtes(out, "putExpDateMap") == [0, 2, 4]


def test_the_three_scan_windows_partition_as_three_fetches_would():
    wide = _chain()
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=5),
                                D + dt.timedelta(days=15))) == [7, 14]
    assert _dtes(se.slice_chain(wide, D + dt.timedelta(days=20),
                                D + dt.timedelta(days=45))) == [21, 30, 44]


def test_everything_else_on_the_chain_is_kept_and_the_source_untouched():
    wide = _chain()
    out = se.slice_chain(wide, D, D)
    assert out["underlyingPrice"] == 100.0 and out["symbol"] == "AAPL"
    assert len(wide["callExpDateMap"]) == 8


def test_a_window_with_no_expirations_has_empty_maps_not_missing_ones():
    out = se.slice_chain(_chain(), D + dt.timedelta(days=50), D + dt.timedelta(days=60))
    assert out["callExpDateMap"] == {} and out["putExpDateMap"] == {}


def test_no_chain_stays_no_chain():
    assert se.slice_chain(None, D, D) is None
```

**Step 2: Run to verify they fail**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_scan_wide_fetch.py -q)`
Expected: FAIL, `module 'scanner_engine' has no attribute 'slice_chain'`.

**Step 3: Implement** — after `fetch_option_chain`:

```python
def slice_chain(chain, from_date, to_date):
    """The part of ``chain`` whose expirations fall in ``[from_date, to_date]``:
    what a fetch of that window would have returned, out of a wider fetch.
    Returns a new dict; ``chain`` is untouched. ``None`` stays ``None``."""
    if not chain:
        return chain
    lo, hi = from_date.isoformat(), to_date.isoformat()
    out = dict(chain)
    for side in ("callExpDateMap", "putExpDateMap"):
        exp_map = chain.get(side) or {}
        out[side] = {k: v for k, v in exp_map.items()
                     if lo <= str(k).split(":")[0] <= hi}
    return out
```

**Step 4: Run to verify they pass**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_scan_wide_fetch.py -q)`
Expected: all pass.

**Step 5: Commit**

```bash
git add options-scanner/scanner_engine.py options-scanner/tests/test_scan_wide_fetch.py
git commit -m "feat(scanner): cut a chain to a date window"
```

---

### Task 16: The scan fetches one wide chain per symbol

**Files:**
- Modify: `options-scanner/scanner_engine.py:1950-1970` (`_fetch_symbol_data` inside `run_full_scan`)
- Test: `options-scanner/tests/test_scan_wide_fetch.py`

**Step 1: Check one assumption before writing code**

```bash
grep -n "numberOfContracts" options-scanner/scanner_engine.py options-scanner/iv_analysis.py options-scanner/scoring.py
```

Expected: no matches. `slice_chain` does not recompute that header field; if anything in the scan path reads it, stop and recompute it in `slice_chain` first, with a test.

**Step 2: Write the failing tests** (append)

```python
from shared import marketdata_config as mdc


class _Client:
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self):
        self.windows = []

    def get_option_chain(self, symbol, contract_type=None, from_date=None,
                         to_date=None, **kw):
        self.windows.append(((from_date - D).days, (to_date - D).days))

        class R:
            status_code = 200

            @staticmethod
            def json():
                return _chain()
        return R()


def _cfg(monkeypatch, wide, exclude=("SPY",)):
    monkeypatch.setattr(mdc, "section", lambda name: {
        "wide_fetch": wide, "wide_fetch_exclude": list(exclude)})


def test_wide_fetch_off_keeps_three_fetches(monkeypatch):
    _cfg(monkeypatch, wide=False)
    c = _Client()
    out = se.scan_chains(c, "AAPL", D)
    assert sorted(c.windows) == [(0, 4), (5, 15), (20, 45)]
    assert set(out) == {"iv", "swing", "zero"}


def test_wide_fetch_on_makes_one_fetch_and_cuts_three_windows(monkeypatch):
    _cfg(monkeypatch, wide=True)
    c = _Client()
    out = se.scan_chains(c, "AAPL", D)
    assert c.windows == [(0, 45)]
    assert _dtes(out["zero"]) == [0, 2, 4]
    assert _dtes(out["swing"]) == [7, 14]
    assert _dtes(out["iv"]) == [21, 30, 44]


def test_an_excluded_symbol_keeps_three_fetches(monkeypatch):
    _cfg(monkeypatch, wide=True, exclude=("SPY", "$SPX"))
    c = _Client()
    se.scan_chains(c, "SPY", D)
    assert len(c.windows) == 3


def test_a_failed_wide_fetch_yields_three_missing_chains(monkeypatch):
    _cfg(monkeypatch, wide=True)
    monkeypatch.setattr(se, "fetch_option_chain", lambda *a, **k: None)
    assert se.scan_chains(_Client(), "AAPL", D) == {"iv": None, "swing": None, "zero": None}


def test_unreadable_config_keeps_three_fetches(monkeypatch):
    monkeypatch.setattr(mdc, "section",
                        lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    c = _Client()
    se.scan_chains(c, "AAPL", D)
    assert len(c.windows) == 3
```

**Step 3: Run to verify they fail**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_scan_wide_fetch.py -q)`
Expected: FAIL, `module 'scanner_engine' has no attribute 'scan_chains'`.

**Step 4: Implement**

Read `scanner_engine.py:1920-1935` first: the six window dates are computed there from `today` (`iv_from`/`iv_to` = +20/+45, `zerodte_from`/`zerodte_to` = +0/+4, `swing_from`/`swing_to`). Move that arithmetic into a module-level helper so the fetch and the cut share one definition, and keep the local names in `run_full_scan` assigned from it so nothing else in the function changes:

```python
def scan_windows(today):
    """The scan's three chain windows as ``{name: (from_date, to_date)}``."""
    return {
        "zero": (today, today + timedelta(days=4)),
        "swing": (today + timedelta(days=5), today + timedelta(days=15)),
        "iv": (today + timedelta(days=20), today + timedelta(days=45)),
    }
```

Before writing it, confirm the swing window's end against the existing `swing_to` line; use whatever the code has today, not the number above.

After `slice_chain`:

```python
def _wide_scan(symbol) -> bool:
    """Whether the scan fetches ONE wide chain for ``symbol`` (config/marketdata.toml
    [scan]). Any trouble reading the setting means three fetches, as before."""
    try:
        from shared import marketdata_config
        cfg = marketdata_config.section("scan")
        return cfg.get("wide_fetch") is True and symbol not in cfg.get("wide_fetch_exclude", [])
    except Exception:  # noqa: BLE001 — the old behaviour is the safe fallback.
        return False


def scan_chains(client, symbol, today) -> dict:
    """The scan's three chains for one symbol: ``{"iv", "swing", "zero"}``.

    One fetch out to the last window's end, cut locally, when the wide fetch is
    on for this symbol; otherwise three fetches. A failed wide fetch yields
    three ``None``s, exactly what three failed fetches would."""
    windows = scan_windows(today)
    if _wide_scan(symbol):
        wide = fetch_option_chain(client, symbol, from_date=windows["zero"][0],
                                  to_date=windows["iv"][1])
        return {name: slice_chain(wide, lo, hi) for name, (lo, hi) in windows.items()}
    return {name: fetch_option_chain(client, symbol, from_date=lo, to_date=hi)
            for name, (lo, hi) in windows.items()}
```

In `_fetch_symbol_data`, replace the three `fetch_option_chain` calls:

```python
        chains = scan_chains(client, symbol, today)
        chain_iv, chain_swing, chain_0 = chains["iv"], chains["swing"], chains["zero"]
```

Keep the `run_iv_analysis` block between them exactly as it is, and keep the comments that explain the windows. `today` must be the same date object the old window variables were built from; check it is in scope inside the nested function.

**Step 5: Run the scanner and service suites**

```bash
(cd options-scanner && "$PY" -m pytest tests -q -rf)
"$PY" -m pytest services/options_svc -q -rf
```

Expected: the failing sets equal the baselines. The tracked config ships `wide_fetch = false`, so every existing scan test takes the three-fetch path.

**Step 6: Commit**

```bash
git add options-scanner/scanner_engine.py options-scanner/tests/test_scan_wide_fetch.py
git commit -m "feat(scanner): optional single wide chain fetch per scan symbol"
```

---

### Task 17: Switch the wide scan fetch on (operator checkpoint)

**Touches prod. Stop for the operator's go-ahead.**

1. Land and promote as in Task 13, Step 2.
2. In Settings → Configuration → Local market data, set **Fetch one wide chain per symbol** on. `options_svc` reads it on the next scan.
3. After two scan slots, compare the Market Scanner against the slot before the change: the same symbols should produce signals in all three tabs. A tab that goes empty for every symbol means a window is cut wrongly; set the switch off and investigate.
4. Read the scan burst:

```bash
ssh vps2 'journalctl --user -u trading-prod-proxy --since "-20 min" -o cat | grep -c "GET /chains"'
```

Expected: a scan slot shows roughly a third of the scan's former chain requests.

5. Once a clean session has run, set the tracked default to `wide_fetch = true` in `config/marketdata.toml` and `DEFAULTS`, update `test_ships_dark`, run the three suites, commit:

```bash
git add config/marketdata.toml shared/marketdata_config.py shared/tests/test_marketdata_config.py
git commit -m "feat(scanner): the wide scan fetch is on by default"
```

Existing scan tests that count chain fetches will now see one per symbol. Where a test is about the three windows themselves, pin `wide_fetch` off in that test with `monkeypatch` on `marketdata_config.section`; do not loosen its assertions.

---

## Phase 3 — collector cadence

### Task 18: Carry a chain forward to the live price (pure)

**Files:**
- Create: `options-scanner/chain_carry.py`
- Test: `options-scanner/tests/test_chain_carry.py`

**Background.** The GEX engine reads Schwab's own `gamma` and `delta` per contract and computes charm and vanna itself from spot and volatility (`gamma_tool.py:814-843`). It uses only the nearest expiration. So a carried chain needs three things changed: `underlyingPrice`, and the `gamma` and `delta` of the nearest expiration's contracts. Schwab's value stays the base and only the Black-Scholes change between the two prices is applied, so a symbol does not step when a real fetch arrives.

**Step 1: Write the failing tests**

```python
"""Carry a chain forward: same contracts, live price, greeks moved by the
Black-Scholes change."""
import copy
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import chain_carry as cc
from options_calculator import RISK_FREE_RATE, bs_delta, bs_gamma

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)
NEAR, FAR = "2026-10-09:4", "2026-10-16:11"
IV = 30.0


def _c(put_call, strike, gamma, delta, iv=IV):
    return {"putCall": put_call, "strikePrice": strike, "gamma": gamma,
            "delta": delta, "volatility": iv, "openInterest": 500,
            "totalVolume": 40, "mark": 1.25}


def _chain(spot=100.0):
    def side(pc, sign):
        return {exp: {"95.0": [_c(pc, 95.0, 0.030, sign * 0.70)],
                      "100.0": [_c(pc, 100.0, 0.060, sign * 0.50)],
                      "105.0": [_c(pc, 105.0, 0.030, sign * 0.30)]}
                for exp in (NEAR, FAR)}
    return {"symbol": "AAPL", "underlyingPrice": spot,
            "callExpDateMap": side("CALL", 1), "putExpDateMap": side("PUT", -1)}


def test_the_price_becomes_the_live_price():
    assert cc.carry_chain(_chain(), 101.5, age_sec=120, now=NOW)["underlyingPrice"] == 101.5


def test_the_source_chain_is_not_mutated():
    src = _chain()
    before = copy.deepcopy(src)
    cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert src == before


def test_gamma_moves_by_the_black_scholes_ratio():
    out = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    t1 = cc.years_to_expiry(NOW, "2026-10-09")
    t0 = t1 + 120 / (365 * 24 * 3600)
    want = 0.060 * (bs_gamma(102.0, 100.0, t1, RISK_FREE_RATE, 0.30, "call")
                    / bs_gamma(100.0, 100.0, t0, RISK_FREE_RATE, 0.30, "call"))
    got = out["callExpDateMap"][NEAR]["100.0"][0]["gamma"]
    assert got == pytest.approx(want, rel=1e-9)


def test_delta_moves_by_the_black_scholes_difference():
    out = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    t1 = cc.years_to_expiry(NOW, "2026-10-09")
    t0 = t1 + 120 / (365 * 24 * 3600)
    want = -0.50 + (bs_delta(102.0, 100.0, t1, RISK_FREE_RATE, 0.30, "put")
                    - bs_delta(100.0, 100.0, t0, RISK_FREE_RATE, 0.30, "put"))
    got = out["putExpDateMap"][NEAR]["100.0"][0]["delta"]
    assert got == pytest.approx(want, rel=1e-9)


def test_an_unmoved_price_and_no_age_changes_nothing():
    out = cc.carry_chain(_chain(), 100.0, age_sec=0, now=NOW)
    assert out["callExpDateMap"][NEAR] == _chain()["callExpDateMap"][NEAR]


def test_only_the_nearest_expiration_is_touched():
    out = cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][FAR] == _chain()["callExpDateMap"][FAR]
    assert out["callExpDateMap"][NEAR] != _chain()["callExpDateMap"][NEAR]


def test_volume_premium_inputs_and_open_interest_are_never_touched():
    out = cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["totalVolume"], c["openInterest"], c["mark"], c["volatility"]) == (40, 500, 1.25, IV)


def test_deltas_stay_inside_their_side():
    out = cc.carry_chain(_chain(), 140.0, age_sec=120, now=NOW)
    for strikes in out["callExpDateMap"][NEAR].values():
        assert 0.0 <= strikes[0]["delta"] <= 1.0
    for strikes in out["putExpDateMap"][NEAR].values():
        assert -1.0 <= strikes[0]["delta"] <= 0.0


def test_a_contract_without_usable_volatility_keeps_schwabs_greeks():
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["volatility"] = -999.0   # Schwab's sentinel
    src["putExpDateMap"][NEAR]["100.0"][0]["volatility"] = None
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["100.0"][0]["gamma"] == 0.060
    assert out["putExpDateMap"][NEAR]["100.0"][0]["delta"] == -0.50


def test_a_contract_with_no_gamma_stays_at_zero_not_nan():
    src = _chain()
    src["callExpDateMap"][NEAR]["105.0"][0]["gamma"] = 0
    src["callExpDateMap"][NEAR]["95.0"][0]["gamma"] = None
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["105.0"][0]["gamma"] == 0
    assert out["callExpDateMap"][NEAR]["95.0"][0]["gamma"] is None


@pytest.mark.parametrize("bad", [None, 0, -5.0, float("nan"), float("inf"), True, "101"])
def test_an_unusable_live_price_returns_the_chain_unchanged(bad):
    src = _chain()
    assert cc.carry_chain(src, bad, age_sec=120, now=NOW) is src


def test_a_chain_with_no_usable_price_of_its_own_is_returned_unchanged():
    src = _chain(spot=0)
    assert cc.carry_chain(src, 101.0, age_sec=120, now=NOW) is src


def test_the_engine_sees_the_move():
    import gamma_tool as gt
    engine = gt.GammaEngine()
    base, *_ = engine.calc_all_from_chain(_chain(), use_volume=False)
    moved, *_ = gt.GammaEngine().calc_all_from_chain(
        cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW), use_volume=False)
    assert moved["spot"] == 104.0
    assert moved["gex"][100.0]["call"] != base["gex"][100.0]["call"]
```

The last test runs the real engine, whose "today" is the wall clock. If the fixed 2026 expirations are in the past when this plan is executed, build `NEAR`/`FAR` and `NOW` from `datetime.now(CT)` instead (today + 4 and + 11 days). Derive them; do not pin dates that will expire.

**Step 2: Run to verify they fail**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_chain_carry.py -q)`
Expected: collection error, `No module named 'chain_carry'`.

**Step 3: Implement** — `options-scanner/chain_carry.py`:

```python
"""Carry a fetched option chain forward to the live price.

The GEX collector fetches watchlist-only symbols every third minute
(config/marketdata.toml [collection]). On the minutes between, it re-uses the
last fetched chain and calls ``carry_chain`` so the engine prices it at the
LIVE spot.

What changes: ``underlyingPrice``, and ``gamma`` and ``delta`` on the contracts
of the nearest expiration — the only expiration ``GammaEngine`` reads. Schwab's
own value stays the base and only the Black-Scholes CHANGE between the fetch
price and the live price is applied, so the series does not step when the next
real fetch arrives. Charm and vanna need nothing: the engine computes them from
spot and volatility itself.

What never changes: volume, open interest, marks, volatility. A carried chain
has no new trades, and nothing here pretends otherwise.

Pure: no I/O, and the input chain is never mutated.
"""
from __future__ import annotations

import math
from datetime import datetime, time as _time
from zoneinfo import ZoneInfo

from gamma_tool import GammaEngine
from options_calculator import RISK_FREE_RATE, bs_delta, bs_gamma, expiry_time_to_years

_SECONDS_PER_YEAR = 365 * 24 * 3600
_TINY_GAMMA = 1e-12
_SIDES = (("callExpDateMap", "call"), ("putExpDateMap", "put"))


def _real(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def years_to_expiry(now, expiry_iso: str) -> float:
    """Years from ``now`` to that expiration's settlement, through the one
    helper every pricer in this folder uses."""
    return expiry_time_to_years(now, datetime.strptime(expiry_iso, "%Y-%m-%d").date())


def _moved(contract, kind, spot0, spot1, t0, t1):
    """One contract with gamma and delta moved from ``spot0`` to ``spot1``."""
    iv = contract.get("volatility")
    strike = contract.get("strikePrice")
    if not _real(iv) or iv <= 0 or not _real(strike) or strike <= 0:
        return contract                      # nothing to model the change with
    sigma = iv / 100.0
    out = dict(contract)

    gamma = contract.get("gamma")
    if _real(gamma) and gamma != 0:
        g0 = bs_gamma(spot0, strike, t0, RISK_FREE_RATE, sigma, kind)
        g1 = bs_gamma(spot1, strike, t1, RISK_FREE_RATE, sigma, kind)
        if _real(g0) and _real(g1) and g0 > _TINY_GAMMA:
            out["gamma"] = gamma * (g1 / g0)

    delta = contract.get("delta")
    if _real(delta):
        d0 = bs_delta(spot0, strike, t0, RISK_FREE_RATE, sigma, kind)
        d1 = bs_delta(spot1, strike, t1, RISK_FREE_RATE, sigma, kind)
        if _real(d0) and _real(d1):
            lo, hi = (0.0, 1.0) if kind == "call" else (-1.0, 0.0)
            out["delta"] = min(hi, max(lo, delta + (d1 - d0)))
    return out


def carry_chain(chain, live_spot, *, age_sec: float, now):
    """``chain`` as it would read at ``live_spot``, ``age_sec`` after it was
    fetched. Returns a new dict. Returns ``chain`` itself, unchanged, when there
    is no usable live price or the chain has no usable price of its own —
    a stale chain is better than an invented one."""
    if not isinstance(chain, dict) or not _real(live_spot) or live_spot <= 0:
        return chain
    spot0 = chain.get("underlyingPrice")
    if not _real(spot0) or spot0 <= 0:
        return chain
    age = age_sec if _real(age_sec) and age_sec > 0 else 0.0
    today = now.strftime("%Y-%m-%d")

    out = dict(chain)
    out["underlyingPrice"] = float(live_spot)
    for map_key, kind in _SIDES:
        exp_map = chain.get(map_key)
        if not isinstance(exp_map, dict) or not exp_map:
            continue
        # The SAME rule the engine uses, so the expiration adjusted here is the
        # one it will read.
        exp_key, _dte = GammaEngine._find_nearest_exp_key(exp_map, today)
        if not exp_key or exp_key not in exp_map:
            continue
        t1 = max(years_to_expiry(now, exp_key.split(":")[0]), 1e-6)
        t0 = t1 + age / _SECONDS_PER_YEAR
        new_map = dict(exp_map)
        new_map[exp_key] = {
            strike: [_moved(c, kind, spot0, live_spot, t0, t1) if isinstance(c, dict)
                     else c for c in contracts]
            for strike, contracts in exp_map[exp_key].items()}
        out[map_key] = new_map
    return out
```

Remove the unused `_time` and `ZoneInfo` imports if ruff flags them.

**Step 4: Run to verify they pass**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_chain_carry.py -q)`
Expected: all pass. If `test_gamma_moves_by_the_black_scholes_ratio` is off by the time basis, read `options_calculator.expiry_time_to_years` (line 56) and make the test and the module use it the same way; do not compute a time to expiry inline (root `CLAUDE.md`, "one settlement instant").

**Step 5: Commit**

```bash
git add options-scanner/chain_carry.py options-scanner/tests/test_chain_carry.py
git commit -m "feat(collector): carry a chain forward to the live price"
```

---

### Task 19: The collector fetches watchlist-only symbols every third minute

**Files:**
- Modify: `options-scanner/gex_collector.py` (`poll_once`, lines 266–445; new helpers above it)
- Test: `options-scanner/tests/test_gex_collector_tiers.py`

**Step 1: Write the failing tests**

```python
"""Watchlist-only symbols get a real chain fetch every Nth minute and are
carried forward in between."""
import datetime as dt
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gex_collector as gc

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)
TIERS = {"tail": frozenset({"AAPL", "SOFI", "UBER"}), "interval_min": 3,
         "fresh_max_age_sec": 20}


def _chain(symbol):
    return {"symbol": symbol, "underlyingPrice": 100.0,
            "callExpDateMap": {}, "putExpDateMap": {}}


def _client(ages=None, quotes=None, quotes_raise=False):
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    client.asked = {}

    def get_chain(symbol, **kw):
        client.asked[symbol] = kw.get("max_age", "none")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _chain(symbol)
        resp.store_age = (ages or {}).get(symbol)
        return resp

    def get_quotes(symbols, **kw):
        if quotes_raise:
            raise RuntimeError("proxy down")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {s: {"quote": {"lastPrice": (quotes or {}).get(s, 0)}}
                                  for s in symbols}
        return resp

    client.get_option_chain.side_effect = get_chain
    client.get_quotes.side_effect = get_quotes
    return client


def _engine():
    engine = MagicMock()
    engine._last_dte = 0
    engine.calc_all_from_chain.return_value = (None, None, None, None)
    return engine


def _poll(client, symbols, tiers=TIERS, now=RTH, on_chain=None, engine=None):
    gc.poll_once(client, engine or _engine(), MagicMock(), symbols=symbols,
                 on_chain=on_chain, poll_term=False, now=now, tiers=tiers)


def test_due_is_one_minute_in_n_and_staggered_by_symbol():
    for sym in ("AAPL", "SOFI", "UBER", "HOOD", "INTC", "PLD"):
        due = [m for m in range(30) if gc.tail_due(sym, m, 3)]
        assert len(due) == 10 and all(b - a == 3 for a, b in zip(due, due[1:]))
    phases = {min(m for m in range(3) if gc.tail_due(s, m, 3))
              for s in ("AAPL", "SOFI", "UBER", "HOOD", "INTC", "PLD", "META", "T")}
    assert len(phases) > 1                       # not all on the same minute


def test_an_interval_of_one_is_always_due():
    assert all(gc.tail_due("AAPL", m, 1) for m in range(5))


def test_without_tiers_no_age_limit_is_sent():
    c = _client()
    _poll(c, ["SPY", "AAPL"], tiers=None)
    assert c.asked == {"SPY": "none", "AAPL": "none"}


def test_core_symbols_always_ask_for_a_fresh_chain():
    c = _client()
    _poll(c, ["SPY", "AAPL"])
    assert c.asked["SPY"] == 20


def test_a_tail_symbol_asks_fresh_when_due_and_stored_otherwise():
    minute = int(RTH.timestamp()) // 60
    seen = set()
    for offset in range(3):
        now = RTH + dt.timedelta(minutes=offset)
        c = _client()
        _poll(c, ["AAPL"], now=now)
        want = 20 if gc.tail_due("AAPL", minute + offset, 3) else 210
        assert c.asked["AAPL"] == want
        seen.add(want)
    assert seen == {20, 210}


def test_a_carried_chain_is_priced_at_the_live_quote():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], engine=engine)
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"SPY": 100.0, "AAPL": 103.0}
    assert c.get_quotes.call_args.args[0] == ["AAPL"]      # one batch, carried only


def test_no_quote_call_when_nothing_was_carried():
    c = _client(ages={"AAPL": 3.0})
    _poll(c, ["SPY", "AAPL"])
    c.get_quotes.assert_not_called()


def test_a_carried_chain_is_not_handed_to_the_detectors():
    seen = []
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == ["SPY"]


def test_a_carried_chain_is_still_written():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_count == 1


def test_a_failed_quote_call_keeps_the_stored_price_and_the_poll_alive():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes_raise=True)
    _poll(c, ["SPY", "AAPL"], engine=engine)
    spots = [call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list]
    assert spots == [100.0, 100.0]


def test_a_carried_symbol_with_no_live_price_keeps_the_stored_price():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 0})     # the no-print sentinel
    _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 100.0


def test_an_answer_with_no_readable_age_is_treated_as_fresh():
    # A MagicMock attribute, an old proxy, a garbled header: none may read as
    # "carried", or the detectors would be skipped for a chain that is new.
    seen = []
    c = _client()                                            # store_age is None
    _poll(c, ["AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == ["AAPL"]
    c.get_quotes.assert_not_called()
```

**Step 2: Run to verify they fail**

Run: `(cd options-scanner && "$PY" -m pytest tests/test_gex_collector_tiers.py -q)`
Expected: FAIL, `module 'gex_collector' has no attribute 'tail_due'`.

**Step 3: Implement** in `gex_collector.py`.

Add `import zlib` and `import chain_carry` with the other imports. Above `poll_once`:

```python
# Slack added to the tail interval when asking the proxy for a STORED chain, so
# a chain fetched a little late in its own minute is still inside the limit.
CARRY_SLACK_SEC = 30


def tail_due(symbol, minute_index: int, interval: int) -> bool:
    """Whether a watchlist-only symbol gets a REAL fetch this minute.

    One minute in ``interval``, offset by a stable hash of the symbol so each
    minute fetches about ``1/interval`` of them. ``zlib.crc32`` rather than
    ``hash()``: the built-in is salted per process, which would move every
    symbol's minute on each restart."""
    if interval <= 1:
        return True
    return (minute_index + zlib.crc32(symbol.encode()) % interval) % interval == 0


def _chain_max_age(symbol, minute_index, tiers):
    """The age limit to send for ``symbol``'s chain, or None to send none
    (``tiers`` off: exactly the request made before tiers existed)."""
    if not tiers:
        return None
    fresh = tiers["fresh_max_age_sec"]
    interval = int(tiers["interval_min"])
    if symbol in tiers["tail"] and not tail_due(symbol, minute_index, interval):
        return interval * 60 + CARRY_SLACK_SEC
    return fresh


def _answer_age(resp):
    """Seconds since the chain left Schwab, or None when the proxy did not say.
    Strict on type: a test double's attribute, or anything that is not a real
    number, must read as "fresh", never as carried."""
    age = getattr(resp, "store_age", None)
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return None
    return float(age)


def _carry_forward(client, fetched, ages, tiers, now):
    """Re-price every carried chain at the live quote, in place in ``fetched``.
    Returns the set of carried symbols.

    ONE batched ``/quotes`` call for all of them. Any failure leaves the chains
    as stored: a price up to three minutes old beats a dead poll."""
    limit = tiers["fresh_max_age_sec"]
    carried = {s for s, chain in fetched
               if chain and ages.get(s) is not None and ages[s] > limit}
    if not carried:
        return carried
    try:
        resp = client.get_quotes(sorted(carried))
        payload = resp.json() if getattr(resp, "status_code", 500) == 200 else None
        spots = live_spots(payload)
    except Exception as e:  # noqa: BLE001
        log.warning("Carry-forward quotes unavailable (%s); using stored prices", e)
        return carried
    for i, (symbol, chain) in enumerate(fetched):
        if symbol in carried and symbol in spots:
            try:
                fetched[i] = (symbol, chain_carry.carry_chain(
                    chain, spots[symbol], age_sec=ages[symbol], now=now))
            except Exception:  # noqa: BLE001 — one symbol never breaks the poll
                log.debug("Carry-forward failed for %s", symbol, exc_info=True)
    return carried
```

In `poll_once`:

1. Signature: add `tiers=None` after `now=None`. Extend the docstring with:

```python
    ``tiers`` — ``{"tail": frozenset, "interval_min": int, "fresh_max_age_sec":
    int}`` or None. With tiers, a watchlist-only symbol gets a real fetch one
    minute in ``interval_min`` and the proxy's stored chain otherwise; a stored
    chain is carried forward to the live quote, is NOT passed to ``on_chain``
    (it has no new volume for the detectors), and IS written like any other.
    None keeps every request exactly as it was before tiers existed.
```

2. After `today = now.date()`: `minute_index = ts_boundary // 60` and `ages: dict = {}`.

3. In `_fetch`, build the call with the optional limit and record the age:

```python
                kwargs = {}
                limit = _chain_max_age(symbol, minute_index, tiers)
                if limit is not None:
                    kwargs["max_age"] = limit
                r = client.get_option_chain(
                    symbol,
                    contract_type=client.Options.ContractType.ALL,
                    from_date=today,
                    to_date=today + timedelta(days=7),
                    **kwargs,
                )
            chain = r.json() if getattr(r, "status_code", 500) == 200 else None
            ages[symbol] = _answer_age(r)
```

`ages` is written from pool threads, one key per symbol; a plain dict is safe for that.

4. After the `_reanchor_spots` block:

```python
    # Watchlist-only symbols between real fetches: the proxy handed back its
    # stored chain. Move it to the live price before anything prices off it.
    carried = _carry_forward(client, fetched, ages, tiers, now) if tiers else set()
    if carried:
        log.info("Carried %d chain(s) forward on live quotes", len(carried))
```

5. In the write loop, change the callback guard to `if on_chain is not None and symbol not in carried:`. Everything below it in the loop is unchanged: all five views are written for a carried chain.

**Step 4: Run the collector tests and the whole scanner suite**

```bash
(cd options-scanner && "$PY" -m pytest tests/test_gex_collector_tiers.py -q)
(cd options-scanner && "$PY" -m pytest tests -q -rf)
```

Expected: the new file passes; the failing set equals the baseline. Every existing collector test calls `poll_once` without `tiers`, so none of them sees a `max_age` argument.

**Step 5: Commit**

```bash
git add options-scanner/gex_collector.py options-scanner/tests/test_gex_collector_tiers.py
git commit -m "feat(collector): fetch watchlist-only chains every Nth minute"
```

---

### Task 20: The options service decides the tiers

**Files:**
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots`, lines 4882–5044; new helper above it)
- Test: `services/options_svc/tests/test_collection_tiers.py`

**Step 1: Write the failing tests**

```python
"""Which symbols get a real chain fetch every minute, and which every third."""
import pytest

from services.options_svc import compute
from shared import marketdata_config as mdc

UNIVERSE = ["$SPX", "SPY", "XLK", "NVDA", "AAPL", "SOFI", "UBER", "HOOD"]
BASE = ["$SPX", "SPY", "XLK", "NVDA", "AAPL"]


@pytest.fixture
def cfg(monkeypatch):
    def set_(mode="on", chains=True, interval=3):
        monkeypatch.setattr(mdc, "mode", lambda: mode)
        monkeypatch.setattr(mdc, "store_on", lambda name: chains)
        monkeypatch.setattr(mdc, "section", lambda name: {
            "tail_interval_min": interval, "fresh_max_age_sec": 20})
    return set_


def tiers(**kw):
    return compute.collection_tiers(UNIVERSE, base=BASE, **kw)


def test_watchlist_only_symbols_are_the_tail(cfg):
    cfg()
    t = tiers()
    assert t == {"tail": frozenset({"SOFI", "UBER", "HOOD"}),
                 "interval_min": 3, "fresh_max_age_sec": 20}


def test_the_viewed_symbol_and_the_public_hot_symbols_stay_on_one_minute(cfg):
    cfg()
    assert tiers(capture={"SOFI"})["tail"] == frozenset({"UBER", "HOOD"})


def test_hedging_flow_symbols_stay_on_one_minute(cfg):
    cfg()
    assert tiers(hiro={"HOOD"})["tail"] == frozenset({"SOFI", "UBER"})


@pytest.mark.parametrize("kw", [dict(mode="shadow"), dict(mode="off"),
                                dict(chains=False), dict(interval=1), dict(interval=0)])
def test_no_tiers_unless_the_store_is_on_and_the_interval_is_above_one(cfg, kw):
    cfg(**kw)
    assert tiers() is None


def test_no_tiers_when_nothing_is_watchlist_only(cfg):
    cfg()
    assert compute.collection_tiers(BASE, base=BASE) is None


def test_unreadable_config_means_no_tiers(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert tiers() is None
```

**Step 2: Run to verify they fail**

Run: `"$PY" -m pytest services/options_svc/tests/test_collection_tiers.py -q`
Expected: FAIL, `module 'services.options_svc.compute' has no attribute 'collection_tiers'`.

**Step 3: Implement** — above `collect_gex_snapshots`:

```python
def collection_tiers(universe, *, base, capture=None, hiro=None):
    """The collector's tiers for this poll, or None for "fetch everything every
    minute" (see ``gex_collector.poll_once``).

    The TAIL is every polled symbol that is collected only because it is on the
    watchlist. Kept on the one-minute tier: the symbols named in
    config/symbols.toml [collection] (``base``), the symbol open on the Dealer
    Positioning page and the public page's hot symbols (``capture``), and the
    hedging-flow symbols (``hiro``), whose rows are measured from fresh chains.

    None unless the proxy's chain store is ON: in shadow or off, every request
    reaches Schwab, so there is no stored chain to carry and nothing to gain.
    Any trouble reading the settings is also None — the old behaviour."""
    try:
        from shared import marketdata_config as mdc

        if mdc.mode() != "on" or not mdc.store_on("chains"):
            return None
        cfg = mdc.section("collection")
        interval = int(cfg["tail_interval_min"])
        if interval <= 1:
            return None
        core = set(base) | set(capture or ()) | set(hiro or ())
        tail = frozenset(s for s in universe if s not in core)
        if not tail:
            return None
        return {"tail": tail, "interval_min": interval,
                "fresh_max_age_sec": int(cfg["fresh_max_age_sec"])}
    except Exception:
        _degrade.degraded("options.collection_tiers")
        return None
```

In `collect_gex_snapshots`, replace the `gc.poll_once(...)` call:

```python
        _universe = symbols if symbols is not None else gc.collection_symbols()
        _tiers = collection_tiers(_universe, base=gc.SYMBOLS, capture=wanted,
                                  hiro=_hiro_syms)
        # Passed only when set: test doubles for poll_once predate the argument.
        _poll_kw = {"tiers": _tiers} if _tiers else {}
        gc.poll_once(_proxy.schwab_py_client, gt.GammaEngine(), conn,
                     symbols=symbols, on_chain=on_chain, **_poll_kw)
```

**Step 4: Run the options service suite**

Run: `"$PY" -m pytest services/options_svc -q -rf`
Expected: the new file passes; the failing set equals the baseline. The tracked config ships `mode = "shadow"` or, after Task 14, `tail_interval_min = 1`; either way `collection_tiers` is None under pytest.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_collection_tiers.py
git commit -m "feat(options): decide the collector's one-minute and three-minute tiers"
```

---

### Task 21: Measure the carry, then switch to three minutes (operator checkpoint)

**Files:**
- Create: `tools/measure_chain_carry.py`
- Test: `tools/tests/test_measure_chain_carry.py`

**Why a tool.** The carry-forward is a model. Before it replaces real fetches, compare the engine's output from a carried chain with its output from the chain actually fetched that minute.

**Step 1: Write the failing test** (the comparison is pure; the fetching is not tested)

```python
"""The carry-accuracy report's arithmetic."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "measure_chain_carry.py"
_spec = importlib.util.spec_from_file_location("measure_chain_carry", _PATH)
mcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcc)


def summary(net, flip, pos, neg):
    return {"net_total": net, "flip": flip, "top_pos_strike": pos, "top_neg_strike": neg}


def test_identical_summaries_have_no_error():
    row = mcc.compare(summary(1e9, 100.0, 105.0, 95.0), summary(1e9, 100.0, 105.0, 95.0))
    assert row == {"net_rel_err": 0.0, "flip_abs_err": 0.0, "walls_agree": True}


def test_errors_are_relative_to_the_fresh_reading():
    row = mcc.compare(summary(1.0e9, 100.0, 105.0, 95.0),
                      summary(1.1e9, 100.5, 110.0, 95.0))
    assert round(row["net_rel_err"], 6) == 0.1
    assert row["flip_abs_err"] == 0.5 and row["walls_agree"] is False


def test_a_missing_reading_is_reported_as_missing_not_as_zero_error():
    row = mcc.compare(summary(None, None, 105.0, 95.0), summary(1e9, 100.0, 105.0, 95.0))
    assert row["net_rel_err"] is None and row["flip_abs_err"] is None


def test_report_lines_state_the_worst_case_and_the_count():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True},
            {"net_rel_err": 0.04, "flip_abs_err": None, "walls_agree": False}]
    text = mcc.report(rows)
    assert "2 comparisons" in text and "4.0%" in text and "1 of 2" in text
```

**Step 2: Run to verify it fails**

Run: `"$PY" -m pytest tools/tests/test_measure_chain_carry.py -q`
Expected: FAIL, file not found.

**Step 3: Implement** — `tools/measure_chain_carry.py`:

```python
"""How close is a carried chain to a real one?

Fetches a few symbols' chains every minute for a short window. Each minute it
compares the GEX engine's output from the chain fetched THAT minute with its
output from a chain fetched one or two minutes earlier and carried forward to
the live price (options-scanner/chain_carry.py).

Run on the box, during the regular session:

    .venv/bin/python tools/measure_chain_carry.py --symbols SOFI,UBER,HOOD --minutes 12

Costs about (symbols x minutes) chain calls plus one quote call a minute, once.
Sends maxAge=0, so it measures against real fetches whatever the store's mode.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")


def compare(fresh: dict, carried: dict) -> dict:
    """One comparison row. A reading either side lacks is None, never 0."""
    def num(d, k):
        v = d.get(k)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    f_net, c_net = num(fresh, "net_total"), num(carried, "net_total")
    f_flip, c_flip = num(fresh, "flip"), num(carried, "flip")
    net = (abs(c_net - f_net) / abs(f_net)
           if f_net not in (None, 0) and c_net is not None else None)
    flip = abs(c_flip - f_flip) if f_flip is not None and c_flip is not None else None
    walls = (fresh.get("top_pos_strike") == carried.get("top_pos_strike")
             and fresh.get("top_neg_strike") == carried.get("top_neg_strike"))
    return {"net_rel_err": net, "flip_abs_err": flip, "walls_agree": walls}


def report(rows: list) -> str:
    nets = [r["net_rel_err"] for r in rows if r["net_rel_err"] is not None]
    flips = [r["flip_abs_err"] for r in rows if r["flip_abs_err"] is not None]
    off = sum(1 for r in rows if not r["walls_agree"])
    lines = [f"{len(rows)} comparisons"]
    if nets:
        nets.sort()
        lines.append(f"net gamma error: median {nets[len(nets) // 2]:.1%}, "
                     f"worst {nets[-1]:.1%}")
    if flips:
        flips.sort()
        lines.append(f"flip level error: median {flips[len(flips) // 2]:.2f}, "
                     f"worst {flips[-1]:.2f}")
    lines.append(f"walls differ in {off} of {len(rows)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", default="SOFI,UBER,HOOD")
    ap.add_argument("--minutes", type=int, default=12)
    args = ap.parse_args(argv)

    for p in (ROOT, ROOT / "options-scanner", ROOT / "schwab-proxy"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import chain_carry
    import gamma_tool as gt
    import gex_collector as gc
    from proxy_client import SchwabPyProxyClient

    client = SchwabPyProxyClient()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    held: dict = {}                      # symbol -> [(fetched_at, chain), ...]
    rows = []

    def summarize(chain):
        gex, *_ = gt.GammaEngine().calc_all_from_chain(chain, use_volume=False)
        return gt.GammaEngine.snapshot_summary(gex) if gex else {}

    for minute in range(args.minutes):
        now = dt.datetime.now(CT)
        today = now.date()
        quotes = client.get_quotes(symbols)
        spots = gc.live_spots(quotes.json() if quotes.status_code == 200 else None)
        for sym in symbols:
            r = client.get_option_chain(
                sym, contract_type="ALL", from_date=today,
                to_date=today + dt.timedelta(days=7), max_age=0)
            if r.status_code != 200 or not r.json():
                continue
            fresh = r.json()
            fresh_summary = summarize(fresh)
            for fetched_at, old in held.get(sym, []):
                age = time.time() - fetched_at
                if sym in spots and age <= 190:
                    carried = chain_carry.carry_chain(old, spots[sym],
                                                      age_sec=age, now=now)
                    row = compare(fresh_summary, summarize(carried))
                    rows.append(row)
                    print(f"{now:%H:%M} {sym:6s} age {age:5.0f}s  {row}")
            held[sym] = (held.get(sym, []) + [(time.time(), fresh)])[-2:]
        if minute < args.minutes - 1:
            time.sleep(max(0.0, 60 - dt.datetime.now(CT).second))
    print()
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Check `snapshot_summary`'s key names against `gamma_tool.py` before relying on `flip`, `top_pos_strike` and `top_neg_strike`; `tests/test_gex_collector_live_spot.py:76-80` shows the shape the collector writes. Use the real names in both `compare` and its test.

**Step 4: Run to verify it passes**

Run: `"$PY" -m pytest tools/tests/test_measure_chain_carry.py -q`
Expected: all pass.

**Step 5: Commit**

```bash
git add tools/measure_chain_carry.py tools/tests/test_measure_chain_carry.py
git commit -m "feat(tools): measure a carried chain against a real fetch"
```

**Step 6: Land, measure, decide (operator checkpoint — touches prod)**

1. Land and promote as in Task 13, Step 2. Nothing changes yet: `tail_interval_min` is 1.
2. During the regular session:

```bash
ssh vps2 'cd /home/administrator/dev && TRADING_CALLER=measure_chain_carry .venv/bin/python tools/measure_chain_carry.py --symbols SOFI,UBER,HOOD,INTC,PLD --minutes 12'
```

3. Put the report in the design doc under "Carry accuracy" and take it to the operator with a recommendation. A reasonable bar: median net-gamma error under about 3%, and walls agreeing in the large majority of comparisons. The operator decides; the numbers are quoted as measured, including any that look bad.
4. With the operator's go-ahead, set **Minutes between real fetches for watchlist symbols** to 3 in Settings → Configuration → Local market data.
5. Verify on the next few minutes:

```bash
ssh vps2 'journalctl --user -u trading-prod-options_svc --since "-10 min" | grep "Carried"'
ssh vps2 'journalctl --user -u trading-prod-proxy --since "-3 min" -o cat | grep -c "GET /chains"'
```

Expected: a `Carried N chain(s) forward` line each minute with N around 43, and roughly 49 chain requests reaching Schwab per minute outside scan slots (was about 92). On Dealer Positioning, open a watchlist-only symbol: it becomes a core symbol while open, so compare a watchlist symbol on the Opportunity Board instead; its row should update every minute.

6. After a clean session, set the tracked default to `tail_interval_min = 3` in `config/marketdata.toml` and `DEFAULTS`, update `test_ships_dark`, run `shared/tests`, `services/options_svc` and the scanner suite, and commit:

```bash
git add config/marketdata.toml shared/marketdata_config.py shared/tests/test_marketdata_config.py docs/plans/2026-10-03-market-data-store-design.md
git commit -m "feat(collector): watchlist-only chains every third minute by default"
```

`services/options_svc/tests/test_eth_activation.py:79` defines a stand-in `_poll(client, engine, conn, lock=None, symbols=None, on_chain=None)`. With tiers now possible under pytest it will receive `tiers=`; add `**kw` to that stand-in's signature. That is a signature following the real function, not a weakened assertion.

**Rollback:** set the interval back to 1.

---

## Phase 4 — documentation

### Task 22: Record what changed

**Files:**
- Modify: `CLAUDE.md` (root), `schwab-proxy/CLAUDE.md`, `options-scanner/CLAUDE.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/manuals/` Technical Reference and API Reference sources, then rebuild
- Modify: `webgui/page_help.py`
- Modify: `docs/plans/2026-10-03-market-data-store-design.md` (status → built)
- Modify: `config/symbols.toml` (the cost comment)

**Step 1: Root `CLAUDE.md` — durable facts only, edited in place.**

- Config table under "Four config files were extracted": add `config/marketdata.toml` (the store's limits, the scan's wide fetch, the collector interval; read by the proxy, `options-scanner` and `options_svc`; read per request, so no restart).
- "Performance characteristics": replace the sentence that the proxy has no cache. State the rules that will still be true next month:
  - the proxy answers repeat chain, quote and daily-bar requests from memory; a hit skips the rate limiter and the call counter;
  - a chain is cut to a narrower window only between all-strikes requests, never for a `strikeCount` or `range` request, and why (the put/call ratio sums the strikes it receives);
  - an entry never crosses a market-session change, and is never served past its limit because Schwab failed;
  - any new caller that needs a guaranteed fresh fetch sends `maxAge=0`; any new fast poller must send its own `maxAge` or it will re-read its own previous answer;
  - watchlist-only symbols get a real chain every third minute and are carried forward between; a carried chain is written but not passed to `on_chain`, so a new detector that reads volume must not expect one-minute data for those symbols.
- "Every service shares the proxy's 5 req/s": update the scan burst figure with the measured one.
- Do not add test counts, commit SHAs or "verified live" lines.

**Step 2: `schwab-proxy/CLAUDE.md`.** Add `market_store.py` to the key-files table, the `maxAge` parameter and `X-Store` / `X-Store-Age` / `X-Caller` headers to the endpoint notes, and the `store` block to `/stats/api_calls`.

**Step 3: `options-scanner/CLAUDE.md`.** In the Gamma analytics paragraph, replace "writes every 1 min … over `collection_symbols()`" with the two tiers, and name `chain_carry.py`. Update the `config/symbols.toml` header comment: one extra `[collection]` symbol still costs about 440 chain calls a day, but one extra watchlist-only symbol now costs about 150.

**Step 4: Manuals.** In the Technical Reference, correct every stated collection cadence and the daily-call figure with the measured numbers from Tasks 14, 17 and 21. In the API Reference, document `maxAge` and the three headers. In `webgui/page_help.py`, find the Flow Alerts and Opportunity Board guides (grep `minute`); where they promise one-minute detection for every symbol, say that watchlist-only symbols are checked every third minute. Rebuild:

```bash
"$PY" docs/manuals/build_docs.py
(cd webgui && "$PY" -m pytest tests -q -k "manual or page_help" -rf)
```

**Step 5: `docs/CHANGELOG.md`.** One dated entry: what shipped, the measured before/after call counts per phase, the shadow results, the carry-accuracy numbers.

**Step 6: Run everything once more and compare failing sets** (the baseline commands from "Before you start", plus `"$PY" -m pyright`).

**Step 7: Commit**

```bash
git add CLAUDE.md schwab-proxy/CLAUDE.md options-scanner/CLAUDE.md docs config/symbols.toml webgui/page_help.py
git commit -m "docs: the proxy's market-data store and the collector tiers"
```

---

## What "done" looks like

- `/stats/api_calls` on prod shows `today` near 48,000 on a full weekday, with a `store` block attributing the rest, and `store_degrades` empty.
- The options service journal shows no more skipped GEX slots than before the change.
- Every page that reads market data still updates; a watchlist-only symbol's row moves every minute.
- `mode = off` and `tail_interval_min = 1` each restore the previous behaviour with no deploy.
