# Options Flow redesign — implementation plan

Design: [2026-08-15-options-flow-redesign-design.md](2026-08-15-options-flow-redesign-design.md)

Bottom-up, one layer per step, TDD throughout. Status: **all steps done.**

## Tier 3 — per-strike premium collection ✅

1. **`options-scanner/flow_skew.premium_by_strike(chain)`** — PURE, the per-strike
   companion to `index_call_put_premium`. Same `Σ mark × totalVolume × 100`
   estimate, keyed off the **map key** (authoritative) not the contract's own
   `strike` field, accumulating across expirations. Returns cells shaped exactly
   `{call, put, net}` floats, which is what the columnar float32 packer gates on.
   A strike with volume but no usable price is **omitted**, not zero-filled.
   *Tests:* `tests/test_flow_skew.py` (+8) — including one that the per-strike
   totals equal `index_call_put_premium`'s scalars, since the ladder and the
   rail's session totals are read side by side.

2. **`gex_collector.poll_once`** writes a fifth view row, `view="prem"`, from that
   grid — reusing `insert_snapshot`/`_encode_grid` with **no schema change**. Its
   own `try/except`: a premium failure costs the ladder, never the four Greek
   rows the heatmap depends on. Spot comes from the Greek pass so the ladder is
   drawn against the same underlying the cursor reads.
   *Tests:* `tests/test_gex_collector_prem.py` (new, 4).

## Tier 2 — publish the ladder ✅

3. **`compute.prem_ladder(rows, n_side=5)`** — PURE. `load_date_with_grid` rows →
   `[{ts, spot, rows: [[strike, call$, put$], …]}]`, each entry cropped to ±5
   strikes around **that row's own spot** (the spec's 11 rows). Per-row centring
   is the point: a session-wide centre would slide every earlier ladder off the
   money on a trending day. `net` is not transmitted — one subtraction in the
   browser vs a third more payload.
   *Tests:* `services/options_svc/tests/test_prem_ladder.py` (new, 14).

4. **`gamma_snapshot`** attaches `prem_ladder`, read through the SAME
   `_history_rows_incremental` memo as the Greek views (it is generic in the view
   string, and this read has exactly the property the memo exists for). Additive:
   no contract change, since the gamma snapshot is cached raw.
   *Tests:* two cases in `test_compute.py` — the ladder is published, and a
   failure degrades to `[]` with the Greek views intact.

## Tier 1 — the panels ✅

5. **`config/theme.toml` `[flow]`** + `theme.flow_colors` / `FLOW_COLORS` /
   `FLOW_KEYFRAMES_CSS`, following the `[console]`/`[macro]` page-scoped
   precedent. 15 hexes; call/put are pinned to the plasma pair by comment,
   because the panels share a subtab strip with the heatmap.

6. **`webgui/pages/options/flow_panels.py`** (new) — pure builders:
   - primitives: `_scale` / `_path` / `_glow_line` / `_broken_paths` / `_text`
   - `divergence_series` / `divergence_geometry` / `ribbon_segments` /
     `divergence_session` / `align_ladder` / `divergence_svg` / `divergence_panel`
   - `field_series` / `field_geometry` / `declutter` / `field_svg` / `field_panel`
   - `_SCRUB_JS` + `scrub_js(uid, kind, payload)`
   *Tests:* `webgui/tests/test_flow_panels.py` (new, 68), including the DOMPurify
   allowlist guard over all four panel states.

7. **`gamma.py` wiring** — one persistent `ui.html` (`panel_el`) inside
   `chart_box`; `_show_panel` swaps `.content` and defers the scrub script one
   tick (`el.content` applies asynchronously, so an immediate run would bind to
   the previous fragment). `_hide_panel` restores the Highcharts path for every
   other view. The Net Prem branch keeps `net_prem_summary_text`,
   `net_prem_status_text` and the selection count.

8. **Removed** `flow_figure` and `net_prem_figure` (+ the `FLOW_*` palette) and
   the 12 tests that only exercised them. Four tests whose invariants outlive the
   chart were **rewritten** against the underlying readers rather than deleted
   (colour stability, ts sorting, mode-aware missing, selection guarding). The
   `chart_kind` registry gained an explicit `_PANEL_VIEWS` set so its
   completeness guard still fails on an unregistered NEW view.

## Verification

- Suites: webgui **1557**, options_svc **1090**, options-scanner (+12) — see the
  CHANGELOG entry for the baselines.
- Rendering + scrub verified in a browser against a standalone harness
  (`ui.html` → real DOMPurify), because the parts that can go wrong here are
  invisible server-side. Measured: both ribbon tones present, terminus declutter
  at exactly `min_gap`, leaderboard reorders on scrub, nothing overflows either
  viewBox, and the cursor dot lands on its line to **0.00px** (compared against
  real SVG path geometry via `getPointAtLength`, not against the payload).

## Not done (deliberate)

- **The ladder is forward-only.** It stays empty until `options_svc` restarts and
  collects; the panel says so instead of drawing a blank frame.
- **No Settings → Appearance entry** for `[flow]`, matching `[console]`/`[macro]`.
- **Per-symbol line colours stay in `gamma.NET_PREM_COLORS`** — 28 symbols, pinned
  for mutual distinctness; the spec's 7-colour palette cannot cover them.
