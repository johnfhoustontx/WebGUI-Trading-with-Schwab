# Signal age and persistence (2026-09-17)

How long a candidate has been in today's scan, and whether its score is rising or
fading — a steady setup against one that blinked into a single scan.

## The premise was wrong

The feature was proposed on the belief that *"the day's scan union already holds
this"*. It does not. `compute.merge_day_signals` (compute.py:138) adds exactly two
lifecycle fields to a row: `live` and `stale_since`. There is no first-seen, no
seen-count, no scan sequence and no score history.

Worse, the union **keeps the latest row on every reappearance** —
`fresh = _day_entry(cur_by_id[sid])` deep-copies the current scan's dict and
discards the carried one, including `fresh["stale_since"] = None`. So today a
signal that ran 09:00→10:00, vanished, and returned at 13:00 is byte-identical to
one that has been present all day. The evidence the feature needs is not merely
absent; it is actively erased once per scan.

Two consequences shape everything below:

1. **Anything attached to a carried row is destroyed on its next live scan.**
2. **`_cap_day_list` (compute.py:110) evicts oldest-stale-first** at 2000/list —
   deleting precisely the long-lived-then-dropped rows this feature exists to show.

## The identity problem

The signal `id` is content-derived and encodes the strikes:
`f"{symbol}_{side}_{exp_str}_{k}_{long_k}"` (`scanner_engine.py:1208`), and
directional joins every leg's strike (`strategy_scanner.py:458`). Strike selection
is delta-band driven, so as spot moves the chosen strike steps one increment and a
**brand-new id is minted**. The 2026-07-16 design measured ~30% of rows churning
per scan in its central case.

Age tracked on `id` would therefore report a rock-steady setup as a stream of
one-scan newcomers — the exact inverse of the question being asked.

**Decision: persistence is keyed on `setup_key = symbol|type|expiration`,** strikes
excluded. A setup is "a put credit spread on MU for Oct 17", however the strikes
drift under it. Directional rows key on the front expiry from `legs`.

⚠ **`setup_key` is a persistence LOOKUP, never a row key.** Row identity stays
`id`. `scanner.py:254` `_sig_key` documents a bug from the opposite mistake — a
coarse key collapsing genuinely distinct signals — and that must not be re-made
here. Two adjacent strikes on one expiry are two rows sharing one age.

⚠ A row with any missing component gets `setup_key = None`, renders `—`, and is
never folded into a fabricated group.

## The store: a setups map, not per-row fields

The day envelope gains a sibling of the three row lists:

```
{date, signals_0dte[], signals_swing[], signals_directional[],
 setups: {"MU|PCS|2026-10-17": {first_seen, seen, scores[], gaps, last_live}}}
```

Rows carry only a short `setup_key` string. This is not a stylistic choice — it is
the only shape that survives the two constraints above. The map is merged
separately from the row lists, so a fresh row replacing a carried one cannot touch
it, and it outlives `_cap_day_list` eviction.

It is also cheaper than the per-row alternative — a score series on every row,
measured by the exploration at **~+68%** on a key the page fetches whole on every
version change.

### Measured on prod, 2026-09-17 (replacing the original estimate)

The first draft of this section estimated "a few hundred setups against ~5,200
rows, roughly +200 KB on a 4.5 MB key (~4%)". Measured against the live
`cache:options:scan_day`:

| | estimated | measured |
|---|---|---|
| payload | 4.5 MB | **0.88 MB** |
| rows | ~5,238 | **487** — 478 of them `signals_directional` |
| setups | "a few hundred" | **221** |
| collapse | implied ~10x | **2.20 rows per setup** |
| map + row stamps | +200 KB (~4%) | **+102 KB (+11.0%)** at `_SETUP_SCORES_MAX=40` |
| the same at 12 | — | +65 KB (+7.0%) |

Row stamps are 15.6 KB of that; the rest is the map. No row was keyless.

**Three corrections fall out of it.** The collapse is real but **modest** — 2.2x,
not the order of magnitude implied; 77 of the 221 setups had exactly one row.
The percentage is **~2.7x worse** than claimed, and it is structural rather than
a light-day artefact: rows and setups scale together, so a heavy day moves both.
And the absolute cost is **far smaller** than claimed, because the baseline
payload was overstated 5x.

⚠ **`_SETUP_SCORES_MAX` stays at 40, and the 5%-of-payload threshold this plan
pre-registered is withdrawn as mis-specified.** That threshold was calibrated
against a 4.5 MB key, where 5% is 225 KB; against the real 0.88 MB key the same
absolute budget is 25%. Expressing a read-cost budget as a *fraction of a
payload that itself varies 5x* measures the wrong thing. The number that matters
is absolute: ~100 KB on a key read once per 15-minute scan, over localhost.

The estimate is also a **worst case that cannot occur**: it assumes every setup
carries a full 40-score series, while a 30-scan day bounds any entry at 30 and
most sit far below. 40 is dead headroom that only manual re-scans can approach.
Cutting to 12 would truncate the sparkline to three hours to save ~37 KB.

⚠ Re-measure `seen` directly once this is deployed — a snapshot can bound the
series length but cannot observe it.

**Stamping is Tier-2 only.** `merge_day_signals` writes `setup_key` onto each row,
so Tier 1 never derives it and no cross-tier mirror is created.

## first_seen must be omitted, never fabricated

`docs/plans/2026-07-16-scanner-directional-day-persistence-design.md:253` records a
`first_seen` field being **deleted** — correctly. That implementation was unconsumed
and it **lied on cold start**: a service restart at noon stamped every 09:00 signal
`first_seen = 12:00`.

`prev` is read from Redis, so an ordinary service restart actually preserves the
map. The dangerous case is a flushed or wrong-dated envelope mid-session. So:

- A genuine newcomer in a merge whose `prev` was usable gets a real `first_seen`.
- A merge with no usable `prev` **after the scan window has already been open**
  marks the whole map `age_unknown` and omits `first_seen`. The page renders `—`.
- Nothing is ever stamped `now` to fill a gap in knowledge.

## Gaps are counted, not erased

Today's `fresh["stale_since"] = None` wipes the record that a setup ever went away.
The map keeps `gaps` — the number of times the setup went absent and returned — so
"steady all day" and "blinked three times" render differently. That distinction is
half of what the feature was asked for.

## The trend rule

One pure function, Tier-1 side:

```
score_trend(scores, window=4, deadband=2.0) -> "new" | "rising" | "steady" | "fading"
```

- Fewer than `window` readings (one hour, at the 15-minute autoscan cadence) →
  `new`. **No direction is claimed**, the same discipline `commit_direction`
  applies to the regime label.
- `delta = scores[-1] - scores[-window]`; `|delta| <= deadband` → `steady`.

The deadband exists because the composite is recomputed from scratch every scan, so
a ±1-point wobble is noise rather than a fade.

⚠ The composite is **two different scorers** — `scoring.calc_composite_score` for
credit spreads and `strategy_scoring`'s quality/fit blend for directional. A trend
is only ever computed within one setup, so the two never meet; nothing here may
compare a score across families.

## Cadence, and what it bounds

`scheduler.autoscan_due` fires at most once per 15-minute slot within
`[windows.scan]` 08:00–15:15 CT — **30 scans on a full day**, plus manual runs. So
age resolution is 15 minutes and `scores` is bounded at roughly 30 entries.

## Where it renders

**Market Scanner** gains two columns beside `Dropped at`, so the lifecycle reads as
a cluster:

| column | live row | dropped row | unknown |
|---|---|---|---|
| **Seen since** | `09:15 · 14x` | `09:15 · 14x` (frozen) | `—` |
| **Score trend** | `▲ +4.2` / `▬` / `▼ −6.1` / `new` | last known | `—` |

**The Symbol Dossier's signal list** carries the same two marks plus a fixed
**64×16px** inline SVG sparkline — no `viewBox`, absolute coordinates. That is
deliberate: `vector-effect` is stripped by DOMPurify and `<polyline points>` cannot
take percentages, so a fixed box is the one form that dodges both traps. It gets the
allow-list test `rings.py` and `rrg_view.tail_svg` carry. Affordable there (a few
rows), not on the scanner (~5,200).

A setup that outlived its rows renders the line the coarse key exists to produce:

> *Live since 09:15 across 4 strikes · 1 gap*

**No sort, no filter, no checklist line.** The feature is a readout; turning it into
a screening axis or an entry gate is a separate decision with its own measurement.

## Two ordering rules that will bite if unstated

- **Prune the map AFTER the row cap, never before, and never a key a surviving row
  still references.** `_cap_day_list` evicts oldest-stale-first, so the naive order
  deletes the setup entry of a row still on screen.
- **The setups block gets its own `try`.** `merge_day_signals` is on the live scan
  path and its caller's `except` leaves the previous envelope untouched — so a bug
  in the persistence code would not degrade persistence, it would freeze the whole
  day union and the Scanner page with it. An exception drops the map for that scan,
  speaks through `_degrade.degraded("options.merge_setups")`, and the row lists
  merge exactly as they do today.

## Testing

Properties, not characterization — `test_adx_uses_wilder_smoothing` pinned a buggy
value for years by recording what the code did.

- A setup present in every scan has `seen == scan_count`.
- Strike drift does not reset `first_seen`.
- A gap is counted, not erased.
- After the cap, nothing in the map is unreferenced by a surviving row.
- A cold start mid-session yields `age_unknown`, never a re-baselined `first_seen`.
- `score_trend` claims no direction under `window` readings.

⚠ Rows are built through the real scanners and driven through the real
`merge_day_signals` across a simulated scan sequence. A hand-written fixture is
exactly how `test_signal_band_facts_print_a_dash_for_a_cold_cache_never_neutral`
passed throughout the bug it was written to catch.

## Verification

Not additive — this edits a function on the live scan path. Local page harness
first, then land, then watch two consecutive real scans on prod before the columns
are trusted. Promote 15:25–16:15 CT: `promote.sh` stops the whole target, so the
public stream drops and GEX slots are lost.
