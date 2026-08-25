# EV in the Trade detail panel — implementation plan (2026-08-25)

TDD throughout: each step names the test written **first**, then the code that
makes it pass. Design: [`-design.md`](2026-08-25-ev-in-trade-detail-design.md).

---

## Phase 1 — hoist the pure statistics to `shared/`

The math already exists and is tested (`tools/tests/test_signal_calibration.py`,
39 tests). Two consumers are about to need it, and a second copy would be the
`clamp`-times-nine trap.

1. **Move** the pure half of `tools/signal_calibration.py` → `shared/calibration.py`:
   `_finite` · `r_multiple` · `breakeven_win_rate` · `priced_win_rate` ·
   `score_bin` · `_t_stat` · `bucket_stats` · `calibrate` · `split_calibrate`.
   `tools/signal_calibration.py` keeps `_SELECT`/`load_rows`/`format_*`/`main`
   and imports the rest.
2. **Move** the corresponding tests → `shared/tests/test_calibration.py`; the
   CLI's own tests (rendering, `--split` wiring) stay in `tools/tests/`.
3. Guard: `tools/tests/test_signal_calibration.py` must still pass **unchanged**
   in behaviour — the move is a refactor, and the existing tests are what prove it.

*Done when:* `shared\tests` and `tools\tests` both green, CLI output on prod
byte-identical to the pre-move run.

## Phase 2 — Tier 2 publisher

4. **Test first** (`services/options_svc/tests/test_calibration.py`):
   - a bucket whose `t_day` is inside ±2 is marked `speaks: False`
   - the payload carries no per-trade rows (size guard — the repo has two
     documented unbounded-payload incidents)
   - a missing/unreadable `signals.db` publishes an EMPTY payload, never raises
   - the bucket key is `family|score_bin` and is stable
5. `services/options_svc/calibration.py` — `build_calibration(rows, min_n, t_gate)`
   over `shared.calibration`, keyed `{"0-DTE|60-65": {...}}`.
6. `handlers.refresh_calibration(bus)` → `cache_set("cache:options:calibration",
   …, event=…, skip_unchanged=True)`.
7. `scheduler.calibration_due(now, last_session)` mirroring
   `sentiment_svc.scheduler.momentum_due`; `config/sessions.toml [slots.calibration]`
   at **16:30 CT** (after the momentum slot, well past `collection.stop`).
8. Wire into the service loop beside the existing due-checks.

*Done when:* `cache:options:calibration` is present in prod Redis with sane
buckets, and `options_svc` tests green.

## Phase 3 — Tier 1 display

9. **Test first** (`webgui/tests/test_ev.py`) — `webgui/pages/options/ev.py`, PURE:
   - `breakeven_facts(signal)` → `None` for a signal with no `credit`
     (directional), a labelled percentage for a credit spread
   - the margin's sign drives a semantic class from the **finite** set
     (Tailwind-first: never a runtime `text-[…]`)
   - `calibrated_facts(signal, payload)` → `None` when the bucket is absent,
     `None` when `speaks` is False, a sentence carrying `n` and `days` otherwise
   - an iron condor never yields a delta-derived `p`
   - a `max_profit`-shaped signal yields `None`, not `+2137R`
10. `detail.py`: add the breakeven row to the **ECONOMICS** block under
    `Probability`; add the calibrated sentence beneath it; **remove** the
    `expected_pnl_10` row from the collapsed expander.
11. The panel reads `cache:options:calibration` once per page build via
    `bus_client`, version-gated with `view_watch.watch_view` — it changes nightly,
    so a per-tick read would be pure waste.

*Done when:* `webgui` suite green, `test_no_inline_style.py` still passes, and the
panel is verified in the **dev** browser (`:9500`) against a real signal.

## Phase 4 — docs

12. `docs/webgui-routes.md` — the detail panel's new rows.
13. `webgui/page_help.py` — the hover guide (it is the fifth manual and rots
    first).
14. `docs/CHANGELOG.md` entry. `CLAUDE.md` only if an invariant moved — a new
    cache view and a new `[slots]` entry qualify, so add both to their tables.

## Risks

- **Phase 1 churn.** The file was pushed to `main` an hour before this plan. The
  move is protected by its own 39 tests, and the CLI output is diffable against
  a pre-move capture.
- **`skip_unchanged` on a nightly key.** It must read the stored payload to
  decide, which is the documented floor cost; the payload is ~10 small buckets,
  so this is negligible.
- **The `tDay` gate will silence most buckets early on.** That is the intended
  behaviour, not a bug — but it means the feature will look inert until the
  sample thickens. Say so in the CHANGELOG.
