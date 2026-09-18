# Symbol Dossier Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** One screen per ticker that answers what eight pages answer today, and links to each of them.

**Architecture:** A Tier-1 page reading six existing cache views on one pipelined version-poll, plus a new per-symbol `dossier` command that fetches only what the cache cannot answer for off-watchlist tickers. One Tier-2 field closes the IV-vs-HV gap.

**Tech Stack:** NiceGUI/Quasar, `shared.bus` via `webgui/bus_client.py`, FastAPI command consumer in `services/options_svc`.

**Design:** [`2026-09-17-symbol-dossier-design.md`](2026-09-17-symbol-dossier-design.md)

**Land [the persistence plan](2026-09-17-signal-persistence-plan.md) FIRST** — Task 8 here consumes `pages.options.persistence`.

---

## Background an implementer needs

Tier 1 may import `nicegui`, `shared.bus` (via `webgui/bus_client.py` — never
`redis` directly), `shared.market_calendar`, `shared.symbols`,
`shared.calibration`, `shared.book_caps`, `repo_paths`, and `requests` for the
health fan-out. **Zero engine imports, zero `sqlite3`, zero Schwab calls, zero
`sys.path` glue.** Anything the page cannot compute under that rule must arrive
through a cache view.

The read API is `webgui/bus_client.py`: `read(view)`, `read_full(view)`,
`read_version(view)`, `read_versions([...])` (pipelined), `read_gated(view, memo)`,
and `request(domain, {"type": ..., "args": ...})` to enqueue.

`webgui/pages/desk.py` is the working precedent for everything structural here —
`VIEWS` (desk.py:1598) is the batched poll list and `_REGION_VIEWS` (desk.py:1607)
is how repaints are scoped to the panels whose views actually moved. Read both
before Task 7.

Tests:

```bash
cd webgui && ../.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m pytest services/options_svc -q
```

```bash
cd options-scanner && ../.venv/bin/python -m pytest tests -p no:randomly -q
```

---

## Task 1: extract `structure_positions` to a shared pure module

`webgui/pages/desk.py:111` `structure_positions(spot, flip, put_wall, call_wall)`
is pure geometry the dossier's Structure band needs. A second page reaching into
the Desk page for it is the wrong shape — the `scorecard.py` precedent
(2026-09-12) is to move it out.

**Files:**
- Create: `webgui/pages/structure.py`
- Modify: `webgui/pages/desk.py:111` (delete the function, import it back by name)
- Test: `webgui/tests/test_structure.py` (create)

**Step 1: Read the function and its existing tests**

```bash
sed -n '100,180p' webgui/pages/desk.py
grep -rn "structure_positions" webgui/
```

**Step 2: ⚠ Grep the destination for every name you are about to import**

This exact move found two silent shadowing bugs last time: `driver.py` re-assigned
`PNL_GREEN/PNL_RED/PNL_NEUTRAL` *after* the new import so the local values won, and
`portfolio.py` already had its own **unsigned** `_money` that shadowed the imported
one and printed realized P&L without a sign.

```bash
grep -n "^from\|^import\|^[A-Z_]* =\|^def " webgui/pages/desk.py | head -60
```

**Step 3: Move it verbatim**

Create `webgui/pages/structure.py` holding `structure_positions` and any private
helper it calls, with a module docstring saying it is shared by the Desk and the
Symbol Dossier. Copy the function **byte-for-byte**; this task changes no
behaviour.

In `desk.py`, replace the definition with:

```python
from .structure import structure_positions      # re-exported: desk.structure_positions
```

Import the **module** where a name could collide (`from . import structure`), per
the lesson above — but keep `desk.structure_positions` resolvable, because the
existing Desk tests reference it by that path.

**Step 4: Move the existing tests and add none**

Move any `structure_positions` test from the Desk's test module into
`webgui/tests/test_structure.py` unchanged. If there are none, add three:
spot between the walls, spot outside a wall, and every input `None`.

**Step 5: Run the full webgui suite**

Run: `cd webgui && ../.venv/bin/python -m pytest -q`
Expected: the same passing set as before the move. A pure move that changes a
count is a bug.

**Step 6: Commit**

```bash
git add webgui/pages/structure.py webgui/pages/desk.py webgui/tests/
git commit -m "refactor(webgui): share structure_positions out of the Desk page"
```

---

## Task 2: publish `hv_current` and `current_iv` on the scan funnel

`iv_analysis.run_iv_analysis` already returns both; the `ScanResult` projection
drops the `iv_data` map, so neither reaches Redis. The funnel per-symbol account
is the one view already carrying `iv_rank` and `earnings_date` by symbol.

**Files:**
- Modify: `options-scanner/scanner_engine.py` — the funnel init (~line 1978) and the assignment at ~line 2035, plus the shape docstring at ~1745
- Test: `options-scanner/tests/test_scan_funnel.py` (append; confirm with `ls options-scanner/tests | grep funnel`)

**Step 1: Write the failing test**

```python
def test_the_funnel_carries_the_inputs_of_the_IV_vs_HV_ratio():
    # iv_rank alone cannot answer "is IV rich against realized vol" — that needs
    # BOTH legs, and the ScanResult projection drops the iv_data map that has
    # them. This is the only per-symbol route to Redis for them.
    results = run_full_scan(_FakeClient(), symbols=["MU"], collect_funnel=True)
    account = results["funnel"]["MU"]
    assert "hv_current" in account and "current_iv" in account
    assert account["hv_current"] == pytest.approx(
        results["iv_data"]["MU"]["hv_current"])
    assert account["current_iv"] == pytest.approx(
        results["iv_data"]["MU"]["current_iv"])


def test_the_funnel_keys_exist_even_when_the_IV_pass_never_ran():
    # A symbol Schwab did not quote stops before iv_analysis. The keys must be
    # present and None — a missing key and a null read differently to a page.
    results = run_full_scan(_NoQuoteClient(), symbols=["ZZZZ"],
                            collect_funnel=True)
    account = results["funnel"]["ZZZZ"]
    assert account["hv_current"] is None and account["current_iv"] is None
```

Reuse whatever fake client the existing funnel tests already build — do not invent
a new one. `grep -n "collect_funnel" options-scanner/tests/*.py`.

**Step 2: Run to verify they fail**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_scan_funnel.py -q -p no:randomly`
Expected: FAIL — `KeyError`/`assert 'hv_current' in ...`

**Step 3: Implement**

At the funnel init (~1978), beside `"iv_rank": None,` add:

```python
                "hv_current": None,
                "current_iv": None,
```

At the assignment site (~2035), beside `funnel[symbol]["iv_rank"] = iv_data.get("iv_rank")`:

```python
            # The two legs of the IV-vs-HV ratio. iv_rank is a VRP proxy (current
            # ATM IV inside the 52-week REALIZED-vol distribution), so it cannot
            # answer "is IV rich against realized" on its own. The ScanResult
            # projection drops iv_data, so the funnel is the only per-symbol route
            # to Redis for these.
            funnel[symbol]["hv_current"] = iv_data.get("hv_current")
            funnel[symbol]["current_iv"] = iv_data.get("current_iv")
```

Add both keys to the shape docstring at ~1745, noting that `current_iv` is a
**PERCENT**, not a decimal (see the `iv_analysis` module docstring and the
inversion `compute.swing_scan` does at compute.py:613).

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add options-scanner/scanner_engine.py options-scanner/tests/
git commit -m "feat(scanner): publish the IV-vs-HV inputs on the scan funnel"
```

---

## Task 3: `compute.build_dossier` — the on-demand fetch

**Files:**
- Create: `services/options_svc/dossier.py`
- Test: `services/options_svc/tests/test_dossier.py` (create)

**Step 1: Write the failing tests**

```python
"""On-demand per-symbol dossier fetch."""
import pytest

from services.options_svc import dossier


class _Chainless:
    """A symbol Schwab will not quote."""


def test_a_symbol_with_no_quote_yields_an_error_not_an_empty_success(
        monkeypatch):
    # An empty payload renders as "collected, nothing to report". A typo must
    # render as a typo.
    monkeypatch.setattr(dossier, "_quote", lambda s: None)
    out = dossier.build_dossier("ZZZZ")
    assert out["symbol"] == "ZZZZ"
    assert out["error"] == "no_quote"
    assert out["spot"] is None


def test_a_good_fetch_carries_spot_structure_and_vol(monkeypatch):
    monkeypatch.setattr(dossier, "_quote",
                        lambda s: {"last": 184.2, "change_pct": 1.8})
    monkeypatch.setattr(dossier, "_gex", lambda s: {"flip": 180.0,
                                                    "put_wall": 175.0,
                                                    "call_wall": 190.0,
                                                    "atm_iv": 34.1})
    monkeypatch.setattr(dossier, "_vol", lambda s: {"iv_rank": 62.0,
                                                    "current_iv": 34.1,
                                                    "hv_current": 27.8})
    out = dossier.build_dossier("MU")
    assert out["error"] is None
    assert out["spot"] == 184.2
    assert out["flip"] == 180.0
    assert out["iv_rank"] == 62.0
    assert out["hv_current"] == 27.8
    assert out["fetched_at"]


def test_a_failing_leg_degrades_that_leg_only(monkeypatch):
    # Off-hours a chain can be absent while the quote is fine. The dossier must
    # still render price and earnings rather than reporting total failure.
    monkeypatch.setattr(dossier, "_quote", lambda s: {"last": 184.2})
    monkeypatch.setattr(dossier, "_gex",
                        lambda s: (_ for _ in ()).throw(RuntimeError("no chain")))
    monkeypatch.setattr(dossier, "_vol", lambda s: {"iv_rank": 62.0})
    out = dossier.build_dossier("MU")
    assert out["error"] is None
    assert out["spot"] == 184.2
    assert out["flip"] is None
    assert out["iv_rank"] == 62.0


def test_earnings_coverage_is_three_valued(monkeypatch):
    # not_listed != none_scheduled. Conflating them is what makes the gate fail
    # open, and vendor coverage is genuinely patchy.
    monkeypatch.setattr(dossier, "_quote", lambda s: {"last": 1.0})
    monkeypatch.setattr(dossier, "_gex", lambda s: {})
    monkeypatch.setattr(dossier, "_vol", lambda s: {})
    monkeypatch.setattr(dossier, "_earnings",
                        lambda s: ("not_listed", None))
    out = dossier.build_dossier("MU")
    assert out["earnings_status"] == "not_listed"
    assert out["earnings_date"] is None


def test_every_key_is_present_on_every_path(monkeypatch):
    # A missing key and a null read differently to a page; the degraded twin must
    # have the same key set as the good one, as matrix.build_rows does.
    monkeypatch.setattr(dossier, "_quote", lambda s: None)
    bad = dossier.build_dossier("ZZZZ")
    monkeypatch.setattr(dossier, "_quote", lambda s: {"last": 1.0})
    monkeypatch.setattr(dossier, "_gex", lambda s: {})
    monkeypatch.setattr(dossier, "_vol", lambda s: {})
    good = dossier.build_dossier("MU")
    assert set(bad) == set(good)
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Create `services/options_svc/dossier.py`. Structure it as four small private legs
plus one total builder, so each leg can degrade alone:

- `_quote(symbol)` → `proxy` client `get_quotes([symbol])[symbol]`.
  ⚠ `schwab_client.get_quotes` returns a **FLATTENED** mapping —
  `{symbol: {"last", "change", "change_pct", "high", "low", "volume"}}` with **no
  nested `"quote"` key**. Reading `q["quote"][...]` yields `None` for every symbol
  with no exception. And `_extract_change_pct` falls through to a literal `0.0`,
  so a `0.00%` cell is not proof of a flat tape — only an omitted symbol is a real
  absence.
- `_gex(symbol)` → reuse `compute._light_gex_context(symbol)` and read it through
  `compute._gex_from_snapshot`, which **establishes wall sides from spot rather
  than list position** (compute.py:9318). Do not read the walls positionally.
- `_vol(symbol)` → `scanner_engine.fetch_price_history` + `iv_analysis`'s
  `calc_historical_vol_series` / `calc_iv_rank_percentile`, returning `iv_rank`,
  `current_iv`, `hv_current`.
- `_earnings(symbol)` → `shared.earnings.lookup` + `coverage`, returning the
  three-valued status and the date.

`build_dossier(symbol)` runs `_quote` first; a falsy quote short-circuits to the
degraded twin with `error="no_quote"`. Each remaining leg is wrapped
individually — `_degrade.degraded("options.dossier_<leg>")` on failure — and the
result carries `fetched_at` (ISO) and `error` (None on success).

⚠ Build the degraded twin from the **same key list** as the good one, the way
`matrix.build_rows` keeps its two return sites key-identical so a `KeyError` is
impossible.

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add services/options_svc/dossier.py services/options_svc/tests/test_dossier.py
git commit -m "feat(options_svc): on-demand per-symbol dossier fetch"
```

---

## Task 4: the `dossier` command, its key, and its guards

**Files:**
- Modify: `services/options_svc/handlers.py` — key builders beside `gamma_pub_key` (~line 372), `_REPLAY_GUARDED` (~line 160), the dispatch docstring (~2694) and the dispatch branches (~2933, beside `gamma_refresh`)
- Test: `services/options_svc/tests/test_dossier.py`

**Step 1: Write the failing tests**

```python
def test_the_dossier_key_is_PER_SYMBOL():
    # cache:options:gamma is ONE symbol-agnostic slot that refresh_gamma_current
    # reads the symbol back out of; a dossier on a shared slot would hijack
    # whatever the Gamma page was showing, and two tabs would fight.
    assert (handlers.dossier_key("mu ")
            == "cache:options:dossier:MU"
            != handlers.dossier_key("NVDA"))


def test_the_dossier_command_writes_the_symbols_own_key(fake_bus):
    handlers.handle_command(fake_bus,
                            {"type": "dossier", "args": {"symbol": "MU"}})
    assert fake_bus.cache_get("cache:options:dossier:MU") is not None
    assert fake_bus.cache_get("cache:options:gamma") is None


def test_the_dossier_command_is_replay_guarded():
    # Consumer groups are created at id 0, so a fresh group replays the backlog.
    # This is a paid Schwab fetch; it joins gamma_analyze and rescue_apply.
    assert "dossier" in handlers._REPLAY_GUARDED


def test_a_stale_dossier_command_does_not_fetch(monkeypatch, fake_bus):
    calls = []
    monkeypatch.setattr(handlers.dossier, "build_dossier",
                        lambda s: calls.append(s) or {})
    handlers.handle_command(
        fake_bus, {"type": "dossier", "args": {"symbol": "MU"},
                   "_ts": 0})          # match the shape _is_stale_open reads
    assert calls == []


def test_the_dossier_key_carries_a_TTL(monkeypatch, fake_bus):
    seen = {}
    real = fake_bus.cache_set
    monkeypatch.setattr(fake_bus, "cache_set",
                        lambda k, p, **kw: seen.update(kw) or real(k, p, **kw))
    handlers.handle_command(fake_bus,
                            {"type": "dossier", "args": {"symbol": "MU"}})
    assert seen.get("ttl") == handlers.DOSSIER_TTL_SEC
```

⚠ Use the suite's existing fake-bus fixture and its existing command-envelope
shape. Read a nearby test first: `grep -n "handle_command" services/options_svc/tests/test_handlers.py | head`.
The `_ts` spelling above is a placeholder — match what `_is_stale_open`
(handlers.py:116) actually reads.

**Step 2: Run to verify they fail**

**Step 3: Implement**

Beside `gamma_pub_key`:

```python
# One symbol's on-demand dossier. PER-SYMBOL, following gamma_pub_key rather than
# the single shared cache:options:gamma slot — that one is symbol-agnostic and
# refresh_gamma_current reads the symbol back out of it, so a dossier written
# there would move the symbol under whoever has the Gamma page open.
def dossier_key(symbol) -> str:
    return f"cache:options:dossier:{str(symbol).strip().upper()}"


def dossier_event(symbol) -> str:
    return f"events:options:dossier:{str(symbol).strip().upper()}"


# A dossier costs three Schwab calls. Fifteen minutes is one autoscan slot, so a
# repeat lookup inside it reads the key and spends nothing.
DOSSIER_TTL_SEC = 900
```

Add `"dossier"` to `_REPLAY_GUARDED`.

Dispatch branch, beside `gamma_refresh`:

```python
    if kind == "dossier":
        symbol = str((args or {}).get("symbol") or "").strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol):
            log.warning("dossier: refusing malformed symbol %r", symbol)
            return
        payload = dossier.build_dossier(symbol)
        bus.cache_set(dossier_key(symbol), payload,
                      event=dossier_event(symbol), ttl=DOSSIER_TTL_SEC)
        return
```

with `_SYMBOL_RE = re.compile(r"[A-Z$.]{1,8}")` at module level.

⚠ **The symbol is interpolated into a Redis key name.** `main.py:2331` records
that the Gamma page deliberately refuses a `symbol` query parameter for exactly
this reason — *"interpolated into a Redis key name with no allow-list behind it"*.
The allow-list is what makes the dossier's parameter safe where Gamma's was not.
It is enforced **here as well as** on the page, because the page is not the only
thing that can enqueue on `cmd:options`.

Add the command to the dispatch docstring at ~2694 — two tests in
`test_handlers.py` fail on drift in **either** direction, so the docstring is part
of the contract, not a comment.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc -q`

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_dossier.py
git commit -m "feat(options_svc): dossier command on a per-symbol TTL'd key"
```

---

## Task 5: `symbol_coverage` and the band fact-builders (Tier 1, pure)

**Files:**
- Create: `webgui/pages/symbol_facts.py`
- Test: `webgui/tests/test_symbol_facts.py` (create)

**Step 1: Write the failing tests**

```python
"""Pure fact-builders for the Symbol Dossier."""
from pages import symbol_facts as sf


def _matrix(*symbols):
    return {"rows": [{"symbol": s, "spot": 184.2, "flip": 180.0,
                      "call_wall": 190.0, "put_wall": 175.0,
                      "net_gex": 2.4e9, "atm_iv": 34.1, "iv_state": "spiking",
                      "dealer_regime": "charm_grind", "gex_regime": "above"}
                     for s in symbols]}


def _funnel(*symbols):
    return {"symbols": {s: {"iv_rank": 62.0, "current_iv": 34.1,
                            "hv_current": 27.8,
                            "earnings_date": "2026-10-23"} for s in symbols}}


def test_a_scanned_symbol_is_complete_from_cache():
    assert sf.symbol_coverage("MU", _matrix("MU"), _funnel("MU")) == "scanned"


def test_a_collected_symbol_has_structure_but_no_funnel_account():
    # $VIX and the SPDR sectors are collected for GEX but are not scan symbols.
    assert sf.symbol_coverage("$VIX", _matrix("$VIX"), _funnel("MU")) == "collected"


def test_an_unknown_symbol_has_nothing():
    assert sf.symbol_coverage("XYZQ", _matrix("MU"), _funnel("MU")) == "unknown"


def test_coverage_is_case_and_whitespace_insensitive():
    assert sf.symbol_coverage(" mu ", _matrix("MU"), _funnel("MU")) == "scanned"


def test_cold_caches_read_as_unknown_not_as_an_error():
    assert sf.symbol_coverage("MU", None, None) == "unknown"


def test_iv_vs_hv_uses_the_scorers_own_bands():
    # strategy_scoring.py:370 — >=1.2 rich, <=0.9 cheap. The dossier must not
    # invent its own thresholds and disagree with the thing that ranks trades.
    assert sf.iv_vs_hv(34.1, 27.8)["band"] == "rich"
    assert sf.iv_vs_hv(25.0, 30.0)["band"] == "cheap"
    assert sf.iv_vs_hv(30.0, 29.0)["band"] == "normal"


def test_iv_vs_hv_declines_rather_than_dividing_by_a_missing_leg():
    for iv, hv in ((None, 27.8), (34.1, None), (34.1, 0.0), (float("nan"), 27.8)):
        assert sf.iv_vs_hv(iv, hv)["ratio"] is None
        assert sf.iv_vs_hv(iv, hv)["band"] == "na"


def test_expected_move_is_derived_from_atm_iv_and_spot():
    # Free, no command, and no race with the single shared
    # cache:options:expected_move slot.
    em = sf.expected_move(184.2, 34.1)
    assert em["day"] == pytest.approx(184.2 * 0.341 * (1 / 365) ** 0.5, rel=1e-6)
    assert em["week"] > em["day"]


def test_expected_move_declines_on_a_missing_leg():
    assert sf.expected_move(None, 34.1)["day"] is None
    assert sf.expected_move(184.2, None)["day"] is None


def test_position_rows_are_filtered_on_symbol_across_all_four_books():
    books = {
        "paper_account": {"positions": [{"symbol": "MU"}, {"symbol": "NVDA"}]},
        "paper_trades": {"trades": [{"symbol": "MU"}]},
        "driver_paper_account": {"positions": [{"symbol": "MU"}]},
        "captured": {"signals": [{"symbol": "NVDA"}]},
    }
    rows = sf.position_rows("MU", books)
    assert len(rows) == 3
    assert {r["book"] for r in rows} == {"account", "ledger", "driver"}


def test_an_empty_book_is_not_an_error():
    assert sf.position_rows("MU", {}) == []
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Create `webgui/pages/symbol_facts.py` holding `symbol_coverage`, `iv_vs_hv`,
`expected_move`, `position_rows`, `signal_rows_for`, `alert_rows_for`, and
`context_facts`. Rules that the tests above encode and the implementation must
honour:

- Every number goes through `webgui/pages/fmt.py` — `num` where the value feeds a
  comparison, a colour or a direction; `float_or` only where a fallback is
  genuinely right. They differ on purpose and a test pins it.
- **Never return `0` for a missing reading.** Every builder returns `None` and the
  page renders a dash. The matrix rows already carry this contract:
  `call_wall`/`net_gex`/`atm_iv` degrade to `None`/`"na"`, never `0`, and the
  off-hours case turns on the distinction.
- `iv_vs_hv` takes the bands from `strategy_scoring.py:370` as **module constants
  with a comment naming the source**, not as bare literals.
- `expected_move` treats `current_iv` as a **percent** (divide by 100) — that is
  the `iv_analysis` convention and the trap `compute.swing_scan` documents at
  compute.py:613.

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add webgui/pages/symbol_facts.py webgui/tests/test_symbol_facts.py
git commit -m "feat(webgui): pure fact-builders for the Symbol Dossier"
```

---

## Task 6: the score sparkline

**Files:**
- Modify: `webgui/pages/options/persistence.py` (from the persistence plan)
- Test: `webgui/tests/test_persistence.py`

**Step 1: Write the failing tests**

```python
def test_the_sparkline_is_a_FIXED_pixel_box_with_no_viewBox():
    # vector-effect is stripped by DOMPurify, and <polyline points> cannot take
    # percentages — so a scaled viewBox would render strokes thick horizontally
    # and hairline vertically, with the server-side string staying correct.
    svg = persistence.score_sparkline([60.0, 62.0, 61.0, 66.0])
    assert 'viewBox' not in svg
    assert 'vector-effect' not in svg
    assert 'width="64"' in svg and 'height="16"' in svg


def test_the_sparkline_declines_under_two_points():
    assert persistence.score_sparkline([60.0]) == ""
    assert persistence.score_sparkline([]) == ""


def test_a_flat_series_does_not_divide_by_zero():
    svg = persistence.score_sparkline([60.0, 60.0, 60.0])
    assert "polyline" in svg and "nan" not in svg.lower()


def test_sparkline_emits_nothing_dompurify_would_strip():
    # Same guard rings.py and rrg_view.tail_svg carry: extract the allow-list
    # from the shipped nicegui/static/dompurify.mjs and assert every tag and
    # attribute survives it.
    allowed_tags, allowed_attrs = _dompurify_allowlist()
    for tag, attrs in _parse(persistence.score_sparkline([1.0, 2.0, 3.0])):
        assert tag in allowed_tags
        assert set(attrs) <= allowed_attrs
```

Copy `_dompurify_allowlist` and `_parse` from
`webgui/tests/test_rings.py::test_ring_svg_emits_nothing_dompurify_would_strip`
rather than writing new ones — that helper already handles the DENY lists.

**Step 2: Run to verify they fail**

**Step 3: Implement**

```python
SPARK_W, SPARK_H = 64, 16


def score_sparkline(scores, width=SPARK_W, height=SPARK_H):
    """A fixed-size inline SVG of a setup's score series. ``''`` under 2 points.

    ⚠ FIXED PIXELS, NO viewBox, deliberately. The idiomatic fluid form is
    ``viewBox`` + ``preserveAspectRatio="none"`` + ``vector-effect:
    non-scaling-stroke`` — and DOMPurify strips ``vector-effect``, so the strokes
    render thick horizontally and hairline vertically while the server-side
    string stays perfectly correct and every test passes. ``<polyline points>``
    cannot take percentages either, so the percentage escape used by
    ``rrg_view.tail_svg`` is unavailable. A fixed box needs neither.
    """
    values = [v for v in (scores or []) if _fmt.num(v) is not None]
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0                      # a flat series is a flat line
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i * step:.1f},{height - (v - lo) / span * (height - 2) - 1:.1f}"
        for i, v in enumerate(values))
    return (f'<svg width="{width}" height="{height}">'
            f'<polyline points="{points}" fill="none" stroke="currentColor" '
            f'stroke-width="1.2" stroke-linejoin="round"/></svg>')
```

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add webgui/pages/options/persistence.py webgui/tests/test_persistence.py
git commit -m "feat(webgui): fixed-box score sparkline for the dossier"
```

---

## Task 7: the page, the route, and the rail entry

**Files:**
- Create: `webgui/pages/symbol.py`
- Modify: `webgui/main.py` — `FLAT_NAV` (~646), `NAV_SECTIONS` (~759), a new `@_page` registration
- Modify: `webgui/tests/test_shell.py`, `webgui/tests/test_no_inline_style.py`
- Test: `webgui/tests/test_symbol_page.py` (create)

**Step 1: Write the failing tests**

```python
def test_the_route_is_registered_as_a_shell_page():
    assert "/symbol" in test_shell.EXPECTED_ROUTES      # match the real name


def test_the_dossier_sits_in_the_caption_less_leading_block():
    caption, entries = main.NAV_SECTIONS[0]
    assert caption is None
    routes = [e[1] for e in entries]
    assert routes == ["/desk", "/symbol"]


def test_the_dossier_is_NOT_a_published_live_screen():
    # It enqueues commands, which bus_client.set_read_only(True) refuses. A
    # published dossier would render permanently empty for every visitor.
    from webgui import live_screens
    assert all(s.route != "/symbol" for s in live_screens.SCREENS)


@pytest.mark.parametrize("raw,expected", [
    ("mu", "MU"), (" nvda ", "NVDA"), ("$SPX", "$SPX"), ("BRK.B", "BRK.B"),
    ("", None), (None, None), ("../../etc", None), ("MU NVDA", None),
    ("TOOLONGSYMBOL", None), ("cache:options:gamma", None),
])
def test_the_symbol_parameter_is_allow_listed(raw, expected):
    # ⚠ It is interpolated into a Redis key name. main.py:2331 records that the
    # Gamma page REFUSED a symbol pin for exactly this reason.
    assert symbol.clean_symbol(raw) == expected
```

**Step 2: Run to verify they fail**

**Step 3: Implement the page**

`webgui/pages/symbol.py` holds `clean_symbol`, `render(symbol=None)`, and the
wiring. It is widgets and wiring only — every computation lives in
`symbol_facts.py` and `persistence.py`.

```python
_SYMBOL_RE = re.compile(r"[A-Z$][A-Z$.]{0,7}")


def clean_symbol(raw):
    """Normalise a user-supplied ticker, or ``None``.

    ⚠ This value is interpolated into a Redis key name by the service's
    ``dossier_key``. ``main.py:2331`` records the Gamma page deliberately
    refusing a ``symbol`` query parameter because it had "no allow-list behind
    it"; this IS that allow-list. It is enforced service-side as well — a page
    is not the only thing that can enqueue on ``cmd:options``.
    """
    cleaned = str(raw or "").strip().upper()
    return cleaned if _SYMBOL_RE.fullmatch(cleaned) else None
```

Views, batched exactly as `desk.VIEWS` is:

```python
VIEWS = ("options:matrix", "options:scan_funnel", "options:scan_day",
         "options:flow_alerts", "options:paper_account", "options:paper_trades",
         "options:driver_paper_account", "options:captured",
         "sentiment:regime", "sentiment:bullbear")
```

- Poll with one `bus_client.read_versions(list(VIEWS))` on a 2-second timer, and
  scope repaints per band with a `_REGION_VIEWS` map, copying `desk.py:1607`.
- Read `options:scan_day` through `bus_client.read_gated` — it is a multi-megabyte
  key, and an ungated 2-second read is the exact pattern that measured 10.2 GB/tab/day
  before `read_gated` existed.
- Gate the day union on `scanner.day_is_today` before trusting `live`. The merge
  is best-effort and leaves a **stale** envelope on failure, so an ungated read
  renders yesterday's signals as live.
- Enqueue a fetch **only** when `symbol_coverage` says the cache cannot answer,
  and **never from the poll** — navigation and the Refresh button only.
- Wrap every timer callback and event handler in `ui_guard.guard`.
- Show `overlay.build_loading_overlay()` during a fetch, with the shared
  `LOAD_TIMEOUT_SEC` backstop.
- Styling is Tailwind tokens only — `PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED` from
  `pages/options/theme.py` plus `build_console_*`. **No `.style()`, no `:style=`,
  no fixed pixels outside Tailwind arbitrary values.** Data-driven colours map from
  a finite state to a static class.
- Shared empty-state lines go in `webgui/pages/copy.py`, not inline —
  `test_shared_copy.py` reads page source and fails on a reintroduced literal.

**Step 4: Register the route**

In `main.py`, add to `FLAT_NAV`:

```python
    ("/symbol", "Symbol", "manage_search"),
```

⚠ `manage_search` is verified free against the existing icon set; the drawer
distinctness test will catch it if that changes.

In `NAV_SECTIONS`, extend the leading block:

```python
    (None, [_sec_page("/desk"), _sec_page("/symbol")]),
```

And the page:

```python
@_page("/symbol")
def symbol_page(symbol: str | None = None) -> None:
    # ⚠ A @ui.page signature IS its query-parameter surface. This one is
    # DELIBERATE — a dossier must be linkable — but it reaches a Redis key name
    # and a Schwab lookup, so pages.symbol.clean_symbol allow-lists it and the
    # service allow-lists it again.
    with _layout("/symbol", "Symbol"):
        from pages import symbol as symbol_page_mod
        symbol_page_mod.render(symbol)
```

**Step 5: Add the page to the standing guards**

- `webgui/tests/test_shell.py` — the expected route set.
- `webgui/tests/test_no_inline_style.py` — the page list.

**Step 6: Run the full webgui suite**

Run: `cd webgui && ../.venv/bin/python -m pytest -q`

**Step 7: Commit**

```bash
git add webgui/pages/symbol.py webgui/main.py webgui/tests/
git commit -m "feat(webgui): Symbol Dossier page at /symbol"
```

---

## Task 8: wire the signal band's persistence marks

**Files:**
- Modify: `webgui/pages/symbol.py`
- Test: `webgui/tests/test_symbol_page.py`

Render the symbol's `scan_day` rows through `scanner.signal_rows` +
`scanner.stamp_stale` + `scanner.stamp_persistence`, then add
`persistence.score_sparkline(entry["scores"])` per row and the
`persistence_facts(...)["detail"]` line above the list.

**Test the payoff case explicitly:**

```python
def test_the_band_reports_a_setup_that_outlived_its_rows():
    # The sentence the coarse key exists to produce.
    ...
    assert detail == "Live since 09:15 · 1 gap"
```

**Commit**

```bash
git add webgui/pages/symbol.py webgui/tests/test_symbol_page.py
git commit -m "feat(webgui): signal age and persistence on the dossier"
```

---

## Task 9: the Opportunity Board link in

**Files:**
- Modify: `webgui/pages/options/matrix.py`

Add a per-row action navigating to `/symbol?symbol=<row symbol>`. That is the
app's existing pick-a-symbol surface. The other seven symbol pages are a
follow-up, deliberately out of scope.

```bash
git add webgui/pages/options/matrix.py webgui/tests/
git commit -m "feat(webgui): open a dossier from the Opportunity Board"
```

---

## Task 10: verify it live

**Step 1: Run every affected suite and compare the failing SET, not the count**

```bash
cd webgui && ../.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m pytest services/options_svc -q
```

```bash
cd options-scanner && ../.venv/bin/python -m pytest tests -p no:randomly -q
```

**Step 2: Confirm no new dependency landed**

```bash
git diff main --stat -- requirements.txt requirements.lock
```

Expected: empty. If either moved, the pin must be added **by hand** to
`requirements.lock` as well — prod has its own venv and `promote.sh` reinstalls
only when the lock moved, so a package in `requirements.txt` alone ships missing.

**Step 3: Promote**

Push to `main` first. Between 15:25 and 16:15 CT:

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

**Step 4: Drive the command through Redis — the most reliable 3-tier check**

```bash
ssh vps2 '/home/administrator/dev/.venv/bin/python - <<PY
import sys; sys.path.insert(0, "/home/administrator/dev")
from shared.bus import Bus
b = Bus()
b.enqueue_command("cmd:options", {"type": "dossier", "args": {"symbol": "ORCL"}})
PY'
```

Wait a few seconds, then read `cache:options:dossier:ORCL` and confirm it carries a
spot, a flip and an `iv_rank`, and that `cache:options:gamma` **did not move** —
that is the shared-slot hazard this design exists to avoid.

**Step 5: Check it in a browser, on a phone width**

Open `https://app.neuralstrike.co/symbol?symbol=MU`, then `?symbol=$VIX` (the
collected-not-scanned case — structure present, Vol Rank fetched), then
`?symbol=ZZZZ` (the not-found state), then `?symbol=../../etc` (must render the
not-found state and enqueue nothing). Confirm the bands stack at phone width with
no horizontal scroll.

**Step 6: Documentation**

Add a Symbol Dossier entry to `webgui/page_help.py`, a route row to
`docs/webgui-routes.md`, and a section to the Reference Guide and the User Guide.
Add a CHANGELOG entry. ⚠ Touch `CLAUDE.md` **only** for the invariants: the new
route in the route table, and the `[collection]`-vs-scan-watchlist nesting if it
is not already stated. A shipped feature is not an entry there.

```bash
git add webgui/page_help.py docs/
git commit -m "docs: the Symbol Dossier"
```
