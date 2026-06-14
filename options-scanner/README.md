# OptionsScanner

An intraday options-trading platform: scans Schwab option chains for high-probability credit spreads, tracks signal performance, visualizes dealer gamma/charm/delta exposure with a forward-projection heatmap, manages paper trades, and generates end-of-day rollup reports.

Single-user, self-hosted, Windows-first. Runs as both a Tk desktop app and a FastAPI + React web interface.

---

## What it does

**Scan for trades.** Every 15 minutes during RTH (Mon–Fri 08:00–15:15 CT), the scanner polls Schwab for 0-DTE and swing credit spreads across the benchmark indices ($SPX, SPY, QQQ) plus the equities listed in Column A of `data/Top 20.xlsx` (edit the sheet to change the watchlist; picked up on the next scan). Each candidate gets a composite 0-100 score weighing R:R, PoP, theta, IV Rank, IV/HV, vega, expected-move buffer, liquidity, and trend. A-grade signals surface at the top of the dashboard tables.

**Read dealer positioning.** The gamma tool (launched from the dashboard) renders intraday gamma / charm / delta exposure per strike, side-by-side with a strike × time heatmap and a forward-projection band that shows where the gamma well will tighten into the close. For 0-DTE, a dedicated panel displays the dollar hedge pressure dealers must transact between now and settlement.

**Paper-trade and track.** Every signal you mark lands in the paper-trades tab with live mark-to-market. An Analyze popup deep-reads the trade's Greeks and data quality; an Ask-AI button copies a structured prompt for pasting into Claude.

**Roll up at end of day.** The EOD report closes 0-DTE signals at settlement, reprices open swings, runs a rule-based HOLD/TAKE/CUT recommender, and writes `data/reports/YYYY-MM-DD-eod-report.md` with today / 7-day / all-time stats by grade and strategy.

---

## Screenshots

Screenshots are a TODO pending a live market-hours capture. For now see the SpotGamma reference image in [docs/plans/2026-04-18-heatmap-design.md](docs/plans/2026-04-18-heatmap-design.md) for the intended gamma-heatmap layout, and the DEX-view description in [USER_GUIDE § 3](docs/USER_GUIDE.md#3--the-gamma-tool) for the bars + ghost-overlay + pressure-panel UI.

---

## Quick start

```bash
git clone https://github.com/johnfhoustontx/OptionsScanner.git
cd OptionsScanner
pip install -r requirements.txt
# Add your Schwab credentials to .env (see docs/INSTALL.md § 3)
python dashboard.py
```

The dashboard opens. First run triggers a Schwab OAuth flow in your browser; subsequent launches are silent.

Gamma-history collection runs automatically: the GEX collector **auto-starts inside the gamma tool** (open the gamma window from the dashboard) and polls every 5 min during market hours — no Windows Task Scheduler setup needed. `python gex_collector.py` remains a manual fallback for running it without the gamma tool open. See [INSTALL.md § 7](docs/INSTALL.md#7--the-gex-collector-no-scheduling-needed).

For the web UI instead of the Tk dashboard:

```bash
uvicorn server.main:app --reload --port 8000   # backend
cd frontend && npm install && npm run dev      # frontend at localhost:5173
```

Full install walkthrough in [INSTALL.md](docs/INSTALL.md).

---

## Features at a glance

### Signal scanner
- 15-minute polling Mon–Fri 08:00–15:15 CT
- 0-DTE and swing credit spreads (PCS, CCS, IC)
- 9-factor composite scoring (0–100) with A/B/C/D grading
- IV Rank / IV Percentile / Expected Move updated each cycle
- De-duped persistence to `signals.db` via `signal_recorder`
- Toast + audio alerts on new A-grade signals

### Gamma / Charm / Delta dashboard
- Side-by-side layout: per-strike bars (left) + strike × time heatmap (right) with shared strike axis
- Three views: GEX (gamma exposure), Charm (dDelta/dTime), DEX (delta exposure)
- Forward-projection band past "now" to 15:00 CT, re-running Black-Scholes on the retained chain at decreasing T — visually shows gamma wall tightening, charm accelerating, delta clamping
- ΔDEX ghost overlay shows today's positioning shift relative to the open
- 0-DTE charm-projected hedge-pressure panel: "Dealers must buy $370M to stay neutral by 15:00 CT (buy)"
- Auto-labeled key strikes on heatmap right edge: Call Wall, Put Wall, Gamma Flip, Key Γ Strike, Last Close
- Hamburger menu with Display / Grouping / GEX Formula / overlay toggles / Show Heatmap / Chart Setup popup
- Chart Setup popup for live per-element style customization (21 styleable chart elements × color/size/thickness/linestyle)

### Paper trading
- SQLite-backed lifecycle (`data/trades.db`)
- Live mark-to-market against Schwab mid-prices
- Analyze popup: live Greeks, unrealized P&L, data-quality flags
- Ask-AI prompt generator for exit-timing consultation
- Auto-expiry at settlement via intrinsic-value repricer

### End-of-day report
- 0-DTE signals closed at settlement with hypothetical P&L
- Open swings repriced to current mid, with HOLD/TAKE_PROFIT/CUT recommendation
- Rolling stats: today / 7-day / all-time, by grade and strategy
- Markdown output at `data/reports/YYYY-MM-DD-eod-report.md`
- Auto-triggered at 15:00 CT by the FastAPI server; CLI re-runnable for any past date

### Two interfaces
- **Tk desktop** (`dashboard.py`) with gamma tool launched as a `Toplevel` child window
- **Web** — FastAPI backend (`server/`) + React/TypeScript frontend (`frontend/`) with REST + WebSocket API

Shared core libraries drive both.

---

## Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — clean-machine install: Python env, Schwab OAuth, SchwabProxy (optional), data dirs, the in-tool GEX collector (no scheduling) + optional Task Scheduler for the EOD report, web-UI build, troubleshooting
- **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — trader walkthrough: the main dashboard, signal tables and scoring, the gamma tool (GEX / Charm / DEX views + heatmap + hedge pressure), paper trading, EOD report, AI-prompt workflow, tips, known issues
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — code structure: five subsystems, per-module role table, interface layers (Tk / Web / CLI), data flow, extension points, per-feature design-doc index
- **[docs/SIGNAL_TRACKING.md](docs/SIGNAL_TRACKING.md)** — signal DB + EOD report details (predates the ARCHITECTURE doc; still the best reference for signals.db internals)
- **[docs/plans/](docs/plans/)** — per-feature design + implementation plan documents (29 docs covering every substantial feature from April 2026 onward)

---

## Project history

Every substantial feature has a design doc and an implementation plan in [`docs/plans/`](docs/plans/) alongside its commits. For the most recent changes see `git log` on `master`; for architectural intent see [ARCHITECTURE.md § Design docs per feature](docs/ARCHITECTURE.md#6--design-docs-per-feature).

---

## Runtime

| Component | Run how | Schedule |
|---|---|---|
| Tk dashboard | `python dashboard.py` | Manual, during market hours |
| Web backend | `uvicorn server.main:app --port 8000` | Manual or systemd/Task Scheduler |
| Web frontend (dev) | `cd frontend && npm run dev` | Manual |
| Scanner (standalone CLI) | `python scanner.py` | Auto-loops 15 min Mon–Fri 08:00–15:15 CT |
| Scanner (inside server) | Auto, via `server/scanner_task.py` | Same cadence — **pick one orchestrator, not both** (conflicts on signals.db + Schwab token refresh) |
| GEX collector | Auto-starts inside the gamma tool (`python dashboard.py` → gamma window); `python gex_collector.py` is a manual fallback | Automatic during market hours — no Task Scheduler |
| EOD report (auto) | Via `server/scanner_task.py` at 15:00 CT | — |
| EOD report (manual) | `python eod_report.py [--date YYYY-MM-DD]` | Ad hoc for backfill |

---

## Technology

- **Python 3.11+** — app core, CLI tools, FastAPI backend
- **Tkinter + matplotlib** — desktop dashboard (`dashboard.py`) and gamma tool (`gamma_tool.py`)
- **FastAPI + uvicorn + WebSockets** — web backend (`server/`)
- **React + TypeScript + Vite** — web frontend (`frontend/`)
- **SQLite** — signal history (`data/signals.db`), gamma snapshots (`data/gex_history.db`); both use idempotent `init_schema` migrations
- **SQLite** — paper trades (`data/trades.db`)
- **Markdown** — EOD reports (`data/reports/*.md`)
- **schwab-py** — Schwab API client, with optional proxy through `D:/AI_Based_Analysis/SchwabProxy` for multi-tool token sharing

Testing: pytest, 253 tests total — expected baseline is **246 passed, 7 failed**. The 7 failures are pre-existing scanner-engine liquidity-gate fixtures unrelated to current features; see [USER_GUIDE § Known issues](docs/USER_GUIDE.md#71-seven-pre-existing-scanner-engine-test-failures).

---

## Contributing

This is a single-user project, but if you fork:

- Every substantive feature gets a design doc in `docs/plans/YYYY-MM-DD-<topic>-design.md` before code
- Every design doc gets a matching implementation plan (`-plan.md`) with task-level breakdown
- TDD is expected for engine-math and DB-layer work; UI changes verified manually
- Small commits with conventional prefixes (`feat:`, `fix:`, `refactor:`, `chore:`, `test:`, `docs:`)
- Run `pytest tests/ -v --tb=no -q` before pushing; baseline is `246 passed, 7 failed`

---

## License

Private. Not for public redistribution as of this writing.

---

## Acknowledgments

- [schwab-py](https://github.com/alexgolec/schwab-py) — Schwab API Python client
- [SpotGamma](https://spotgamma.com) — visual reference for the gamma heatmap layout (this project doesn't use or depend on their data/code; it was purely inspirational)
- [Claude Code](https://claude.com/claude-code) — pair-programmed most of the 2026-04 feature work (see commit trailers and `docs/plans/` for session provenance)
