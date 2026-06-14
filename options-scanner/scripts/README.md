# GEX Collector — Install Guide (DEPRECATED)

> **DEPRECATED — no Task Scheduler setup needed.** The GEX collector now
> **auto-starts inside the gamma tool**: run `python dashboard.py`, open the
> gamma window, and the collector launches automatically on a background thread
> and polls every 5 min during market hours, writing snapshots to
> `gex_history.db`. There is **no Task Scheduler entry to create** anymore.
>
> `gex_collector.py` (and `start_gex_collector.bat`) remain available as a
> **manual fallback** for running the collector without the gamma tool open.
> Only one collector runs at a time, coordinated by an advisory file lock at
> `data/gex_collector.lock`; the standalone collector stands down if the in-tool
> collector already owns the lock.
>
> **If you previously created the `GEX Collector` scheduled task, disable or
> delete it** — leaving it running risks a second collector competing for the
> lock:
>
> ```
> schtasks /End /TN "GEX Collector"
> schtasks /Delete /TN "GEX Collector" /F
> ```
>
> The Task Scheduler install steps below are retained only for historical
> reference. **Do not follow them.**

---

## What the collector does

The `gex_collector.py` script polls Schwab every 5 minutes during market hours and writes GEX/Charm snapshots to `gex_history.db`. The dashboard reads from that DB to show intraday history overlays.

- Trigger: in-tool, auto-started by the gamma tool (or manual via `python gex_collector.py` / `start_gex_collector.bat`)
- Polls on 5-minute wall-clock boundaries during market hours (Mon–Fri, ~08:30–15:20 CT)
- Exits cleanly past 15:20 CT (or on fatal error with exit code 1)
- Single-instance: an advisory lock at `data/gex_collector.lock` prevents a second collector from double-collecting

## Verify

Run `python dashboard.py`, open the gamma window, then tail `logs/gex_collector.log` — you should see `Collector started` and a polling line. Or run `python gex_collector.py` standalone and tail the same log.

---

## Historical reference (Task Scheduler — DO NOT FOLLOW)

> The steps in this section are obsolete. They are kept only to document how the
> retired `GEX Collector` scheduled task used to be installed. The collector now
> auto-starts inside the gamma tool; no scheduled task is required.
