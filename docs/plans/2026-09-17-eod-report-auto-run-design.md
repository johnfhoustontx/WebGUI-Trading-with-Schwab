# Auto-run the EOD report at 15:15 CT

**2026-09-17.** Operator request: *"I need you to auto run the eod report at 3:15 each
trading day."*

## What it is

`/eod`'s **Generate** button, run for you, once per trading day at **15:15 CT**. It
writes the same two files from the same builders:
`webgui/data/eod/<date>/summary.html` and `detail.html`.

Nothing about the report is duplicated. The report **is** `webgui/pages/eod.py`;
`tools/generate_eod_report.py` is a headless caller of it.

## Where the schedule lives, and why it is not a service slot

The obvious home is a `[slots.…]` gate in `options_svc.scheduler`, beside
`eod_summary_due` (the 15:10 push) and the rest. It does not work, for two reasons
that are both structural rather than stylistic:

* **The builders are Tier 1.** `pages/eod.py` reads Redis and renders HTML; Tier 2 may
  not import it. An `options_svc` copy of the report would be a second EOD report free
  to drift from the one the page renders — and the drift would be invisible, because
  each would look correct on its own.
* **The webgui has no app-wide scheduler.** Its only timers are per-client
  `ui.timer`s. "Generate at 15:15" implemented there means "generate at 15:15 **if a
  browser tab happens to be open**" — which behaves like a schedule right up until the
  day nobody is looking, which is every day this feature exists for.

So it is a **systemd timer owning a oneshot**, the pattern the gallery capture
(`[slots.gallery_capture]`) and the flow-delta instrumentation (`[slots.flow_delta]`)
already use for exactly this shape of work: a once-a-day job that is not a service.

⚠ That makes **three** slots read by systemd rather than by a service scheduler. The
note in `shared/market_calendar.py` claiming `gallery_capture` was "the ONE" was
already wrong when `flow_delta` joined it; it is corrected in place rather than
appended to.

## Why 15:15 costs nothing

The operator asked for 3:15, and it happens to sit well. The post-close order:

```
15:00  regular close; [slots.action_alert] close sweep
15:10  the EOD phone push (options_svc eod_summary_due) — the driver's manage
       cycle has settled the day's expiries by here
15:15  THIS, and [slots.analyze] close (a paid Claude call), and the last
       [windows.scan] autoscan slot
15:20  GEX collection stops
15:25  live_capture (headless Chrome over fourteen screens)
```

Sharing the minute with the autoscan's last slot and the close briefing is free
because this run makes **no Schwab call and no Claude call**: it reads the already
published `options:*` caches and writes two local files, in well under a second.
That is also why the unit carries no `CPUQuota` and no `TimeoutStartSec` — unlike the
two capture units either side of it, there is nothing here to contain.

15:15 is CT, like every other time in `config/sessions.toml`; the host runs
`America/Chicago` and the unit pins `TZ` as well.

## The two decisions that are easy to get backwards

### An all-empty snapshot writes NOTHING

Every builder in `pages/eod.py` is deliberately defensive — a missing cache renders a
"No data" note rather than raising. That is right for a page and dangerous for an
unattended writer: a run against a stopped stack, or one whose bus could not
authenticate, produces a **complete-looking report** of nothing. The archive is keyed
by DATE and overwrites in place, so committing that would replace the day's real
report (a hand-run one, or an earlier firing) with an empty page.

`generate_eod_report.py` therefore checks `eod.has_data(snap)` and, on an all-empty
snapshot, writes nothing and exits **1** — surfacing in `systemctl --user --failed`
instead of as a blank report nobody questions. `--allow-empty` is the deliberate
override.

This is the same shape as the flow-delta failure the unit comments describe, where a
degraded run still exited 0 and the only symptom was a directory that stopped gaining
dates. The `EnvironmentFile` (which carries `MEMURAI_PASSWORD`) is load-bearing here
for the same reason, and the refusal message names it.

⚠ The **button** keeps its existing unconditional behaviour. A person clicking
Generate is watching, and can see what they got; the gate is for the run nobody sees.

`has_data` and `read_snapshot` walk ONE list (`_CACHE_VIEWS`), so the gate cannot come
to check a different set of views than the snapshot fills — which would make it either
vacuous or a permanent refusal. `generate(snap)` takes the snapshot the gate inspected
rather than re-reading: at 15:15 the caches are still moving, and the bytes checked
must be the bytes archived.

### No `Persistent=true`

The timer must **not** catch up. This looks like the flow-delta case (a missed day
cannot be recovered) and behaves like the opposite:

* The report is named for the day it is generated **ON**, from **live** caches, not
  from history. A catch-up run at whatever hour the box came back would not produce
  the missed day's report — it would produce the **next** day's date off pre-open
  caches, and then be overwritten by that day's real firing.
* A missed day is simply a day with no archived file. While the caches still hold the
  day, **Generate** recovers it in one click.

`Mon..Fri` on the timer excludes weekends only. Holidays are the script's job
(`shared.market_calendar.is_trading_day`, exiting 0), because only the market calendar
can see Thanksgiving — the division of labour the stream and live-capture timers use.

## Pieces

| File | Change |
|---|---|
| `config/sessions.toml` | new `[slots.eod_report] at = "15:15"` |
| `shared/market_calendar.py` | its default; corrected the stale "ONE slot read by systemd" note |
| `webgui/pages/eod.py` | `_CACHE_VIEWS`, `has_data(snap)`, `generate(snap=None)` |
| `tools/generate_eod_report.py` | the headless entrypoint + both gates |
| `deploy/systemd/generate_units.py` | `_eod_report_units()`, wired into `render_all()` |

Tests: `tools/tests/test_generate_eod_report.py` (the gates),
`tests/test_systemd_units.py` (the units — slot-derived schedule, no catch-up, oneshot,
not a fleet member, armed), `webgui/tests/test_eod.py` (`has_data`, `generate(snap)`).

## Deploying it

`--install` writes **and arms** the timer, so the eleven-day flow-delta failure cannot
repeat here:

```bash
.venv/bin/python -m deploy.systemd.generate_units --install
```

Prod only — `activate()` skips arming in dev by design.
