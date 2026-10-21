# Trade ideas in the Paper Ledger, and a weekly signal review — design

Date: 2026-10-20. Status: design, awaiting the user's approval. Nothing is built.

## Intent

The hourly trade idea (seven posts a trading day, 08:35 … 14:35 CT, `[slots.trade_idea]`)
is public on X, Discord, Telegram and `neuralstrike.co/ideas.html`. Its results there are
**modelled**: Black-Scholes at an IV solved from the entry, walked over the stock's
1-minute bars (`services/options_svc/site_ideas.py`). Nothing records what the posted
trade did against real option quotes, and nothing keeps how far it went against the
position before it closed.

The user wants every posted idea **booked in the Paper Ledger** and followed to its
target or stop, so that each one has a real **entry, exit, time held and maximum
adverse excursion (MAE)**; the day's results in the **end-of-day report**; and, once a
week, an **informational report** in which Claude rates the closed trades by what the
signal knew when it posted, so the user can decide how to improve the signals.

## Decisions the user made (in their words where it matters)

1. **Every posted idea goes into the Paper Ledger**, tagged **"Trade idea"**. "Do not
   refuse any trade tagged 'Trade idea'… and will also be exempt from the risk limits."
2. Each trade records **entry, exit, time held, MAE**, and the report carries
   **cumulative gains/losses for the day / week / month**.
3. **Re-priced every 5 minutes** ("every 5 minutes is fine").
4. **No push to Telegram or Discord — only the EOD report** (the app's `/eod` report).
5. The weekly report shows **closed trades and open trades separately**.
6. The weekly grading as played back (groups by grade, structure, 0-DTE/swing, score,
   hour posted, symbol; a plain rating per closed trade; suggestions that carry their
   sample size; all-time tables beside the week's): **"looks good"**.
7. **Claude writes the judgment**, through a **scheduled query on the server** — a
   systemd timer on vps2 running Claude unattended, the way the daily market reports
   run — not an in-app API call.
8. **Debit idea trades get a stop of their own** ("Give idea trades a stop of their
   own"), rather than riding to target or expiry.

## Goals

- Every idea that is POSTED (the `if sent:` branch of `handlers.run_trade_idea`) becomes
  one OPEN Ledger row tagged as a trade idea, within the same run.
- Idea rows close only at the idea's **target**, its **stop**, or **expiry**.
- Each closed idea row carries entry price/time, exit price/time/reason, time held, and
  MAE (dollars per contract, and as % of the trade's max loss).
- `/eod` gains a "Trade ideas" section and a third book in its performance tables.
- A weekly report, archived in the app beside `/eod`, written after the last trading day
  of each week.

## Non-goals

- No change to how ideas are picked, drawn or posted, or to `ideas.html` and its model.
- No Telegram, Discord, X or public-site output from any of this.
- Nothing changes signal settings automatically; the weekly report is read by the user.
- No backfill: tracking starts with the first idea posted after this goes live
  (`ideas.json` keeps six days and its prices are scan-time, not fills).
- No maximum favourable excursion (not asked; cheap to add later from the same marks).

## Anti-goals (a failure even if it "works")

- **An idea that is posted but not booked.** Booking must never be refused by a cap,
  a structure name, or a missing field; when something is missing it books with what
  it has and says so on the row.
- **A post lost to booking.** Booking runs after the post and never raises into it.
- **Idea trades leaking into the user's own book**: its caps, its equity, its
  "Manual paper" totals on `/eod`, or its generic exit rules.
- **Claude stating a number the code did not compute**, or a pattern presented as
  evidence when it rests on three trades.

## Constraints (from the repo)

- Tier rules: booking and marking live in `options_svc` / `options-scanner`; the webgui
  reads Redis only. The weekly tool is a `tools/` script run by a systemd timer, like
  `tools/generate_eod_report.py`.
- Every mutation of a paper book runs under `paper_lock.BOOK_LOCK`
  (`@paper_lock.serialized`). `run_trade_idea` runs on a pool thread outside the lock,
  so the booking function takes it itself (pattern at `handlers.py` ~3086).
- Configurable by default: every tunable below lives in `config/*.toml` through
  `toml_loader`, with an entry in `webgui/config_schema.py`.
- Settlement uses the one rule, `paper_engine.settlement_underlying` /
  `should_settle`.
- Proxy budget: 5 requests/s shared; scheduled chain bursts stay out of :02-:05,
  :17-:20, :32-:35, :47-:50 and the first minute after the hour and half hour.
- Promote rule: never restart `options_svc` (the GEX collector) 06:30-15:20 CT on a
  trading day.
- `trades.db` stores typed columns; `paper_trader._trade_to_row` drops unknown keys, so
  every new field needs an additive `ALTER` in `trades_db.init_db`.
- Docs that rot: `webgui/page_help.py` (Paper Ledger, EOD), the User Guide and Reference
  Guide, the CHANGELOG.

## Design

### 1. Booking (at the post)

A new `_book_trade_idea_ledger(idea, now)` in `handlers.py`, called inside
`run_trade_idea`'s `if sent:` branch after `_publish_trade_idea_site`, guarded so it
never raises (`_degrade.degraded("trade_idea_ledger")` on failure), its outcome stored
in `result["ledger"]`. It calls a new `@serialized` compute function
(`compute.book_trade_idea`, in a sibling module that imports nothing from `compute`
per the size ceiling — e.g. `services/options_svc/idea_ledger.py`) which:

- builds the row from the **idea dict** (`trade_idea.normalize` output: `legs`, `type`,
  `expiration`, `entry_cash`, `max_loss`, `max_profit`, `spot`, `grade`, `score`,
  `trade_type`, `id`), **not** from the scanner row's flat fields;
- **always stores `legs`** (credit ideas too) and, for PCS/CCS/IC, also fills the
  strike columns the Ledger page already reads;
- writes it with `paper_trader.add_trade` directly — **no `book_caps` evaluation, no
  `create_paper_trade` structure refusal**. The existing `create_paper_trade` and its
  refusal of straddles, strangles, the iron butterfly and backspreads stay exactly as
  they are for the Paper button; `test_straddle_analysis_only.py` keeps pinning that,
  and gains a case that an idea row of those structures IS stored (a deliberate,
  narrow exception for this tag only).
- quantity **1** contract (builder's call; the caps are not in play).

**Entry price.** Builder's call, marked as mine: the entry is the idea's own
`entry_cash` (the price printed on the card, commission in), because the review grades
the signal **as posted**. The row also records `entry_source = "card"`. (A fresh chain
fill at post time was considered; it would grade the posting latency as well as the
signal, and adds a dependency that can fail at the moment booking must not.)

**Calendars/diagonals** never post (`normalize` drops multi-expiry legs), so no
two-expiry idea row can exist; the booking refuses nothing but the marking code still
handles one per-leg (it uses `structure_marks`).

### 2. New Ledger columns (additive migration in `trades_db.init_db`)

| column | meaning |
|---|---|
| `source` | `'trade_idea'` for these rows; NULL for every existing and manual row |
| `idea_id` | the idea's scanner row id. **Not unique**: scanner ids are built from the trade itself, and `pick` only blocks a repeat within a day, so the same spread can post Monday and again Tuesday. Each POST is its own trade and its own row (the user: "do not refuse any trade"), even while an earlier row of the same id is open. The double-booking key is `(idea_id, idea_posted)`: a re-run of the same slot must not book twice. The review counts each row as a trade and reports repeats ("posted 2× this week") |
| `idea_posted` | post time (CT ISO) |
| `idea_grade`, `idea_score`, `idea_trade_type`, `idea_label` | what the signal knew |
| `idea_spot` | stock price at the post |
| `target_pnl`, `stop_pnl` | per contract. Target from `site_ideas.exit_levels(entry_facts(idea))` (NULL for a backspread). Stop from the same call, **except** that a debit idea for which it gives no stop (every debit structure today: `debit_stop_frac` ships commented out) gets the idea book's OWN stop, `[idea_trades] debit_stop_frac` (default 0.60) × the debit paid — the user's decision, 2026-10-20. Applies to idea rows only: the card, `ideas.html`, the user's own Ledger trades and the Account are unchanged, so an idea's Ledger result can differ from its `ideas.html` result |
| `mae_pnl`, `mae_at` | worst unrealized P&L per contract seen at a mark (≤ 0; 0 until a mark goes negative) and when |
| `last_mark_pnl`, `last_mark_at` | the latest mark, so the 5-minute cycle's result is visible without a chain fetch |
| `entry_source` | `'card'` |

Time held is derived (`exit_time − idea_posted`), not stored.

### 3. The 5-minute idea cycle

A new scheduler branch, `idea_marks_due`, following the `captured_manage_due` pattern:
trading days, regular session, every `[idea_trades] mark_interval_min` (5) minutes,
with minutes chosen to stay out of the proxy's burst windows (builder: read the proxy
access log before settling the offsets; a mark landing a minute late is fine). One pass,
under `BOOK_LOCK`, over OPEN `source='trade_idea'` rows:

1. Settle first: an expired row settles at intrinsic of its `legs` on
   `settlement_underlying` (`structure_marks.expiry_value` semantics), exit reason
   `EXPIRED`. The 15:05 `[slots.paper_settle]` pass covers expiry day.
2. Mark each with `structure_marks.reprice` (per-leg chain, numeric strike match;
   works for every posted structure). No mark → skip this cycle, never a zero.
3. Update `mae_pnl` / `mae_at` if the mark is worse; write `last_mark_*`.
4. Close if `pnl ≥ target_pnl` → `TARGET`, or `pnl ≤ −stop_pnl` → `STOP`, **at the
   mark's value** (not at the level: the paper book records what a 5-minute check would
   have got). Stop is checked first.

Idea rows are **excluded** from: `compute.ledger_book_state` (caps and equity),
`refresh_ledger_caps`, `manage_ledger_trades` (generic debit rules),
`expire_ledger_trades` (they settle in their own pass, so the hand-coded PCS/CCS/IC
credit branch never meets a straddle), and the per-row chain reprice in
`_reprice_open_pnl` (they show `last_mark_pnl` instead — otherwise every open idea
costs a second chain fetch per Ledger refresh).

Cost: one chain per distinct (symbol, expiry) per cycle; the proxy's store often
answers from the GEX collector's minute fetch (`chain_max_age_sec`).

### 4. The Paper Ledger page

Idea rows carry a "Trade idea" tag in the table and a header toggle (All / My trades /
Trade ideas) — a pure filter in `paper_rows`. Their Close button stays (the user may
close one by hand; the exit reason is then `MANUAL` and the review counts it apart).
The Ledger's own equity/caps line excludes them.

**Deletes (my call).** Closed idea rows are the only source of the weekly review's
all-time tables and `/eod`'s week and month totals, so "Delete closed trades"
(`paper_trader.delete_closed_trades`) **spares `source='trade_idea'` rows**, and its
confirm says so. A per-row Delete of an idea row is still allowed, behind a confirm that
names what it removes from the review.

### 5. The `/eod` report

- `cache:options:paper_trades` already carries the rows; the new columns ride along.
- `_books` gains a third book, **"Trade ideas"**, and `"Manual paper"` excludes
  `source='trade_idea'`, so the existing day / week / month performance table shows
  idea trades on their own row set.
- A new "Trade ideas" detail section: today's posted ideas (time posted, symbol,
  structure, grade, entry, exit or "open", exit reason, time held, MAE $ and % of max
  loss, P&L); trades closed today from earlier days; the open idea trades at their last
  mark; and closed-trade P&L totals for the day / week / month with the open trades'
  unrealized total on its own line (it never enters the period totals).
- The summary page gets one tile: today's idea P&L and how many closed.

### 6. The weekly review

**When.** A systemd timer `[slots.idea_review]` at 15:30 CT Mon–Fri; the tool exits
quietly unless today is the last trading day of its ISO week (`is_trading_day(d)` and
`next_trading_day(d)` in a later week). A Friday holiday therefore runs Thursday.

**What runs.** `tools/weekly_idea_review.py`, unit built by a new
`_idea_review_units()` in `deploy/systemd/generate_units.py` (pattern:
`_eod_report_units`; `OnFailure` alert comes for free):

1. **Code computes every number** from `trades.db` (opened read-only), for the week
   and all-time: per closed trade the rating; per group (grade, structure, 0-DTE vs
   swing by `idea_trade_type`, score band, post hour, symbol) n, targets, stops,
   expiries, manual closes, win rate, average and total P&L, average MAE (% of max
   loss), average time held. Open trades: last mark, MAE so far, days open.
2. **Ratings** (code, thresholds in config): `clean win` (TARGET with MAE better than
   `clean_mae_pct`, default −25% of max loss), `win after drawdown` (TARGET, deeper),
   `stopped`, `expired win`, `expired loss`, `closed by hand`.
3. **Claude writes the judgment.** `services/options_svc/claude_cli.CliMessagesClient`
   (subscription, `claude -p`, Sonnet, forced JSON schema), given ONLY the computed
   facts, each with an id and its n. It returns an assessment (short paragraphs) and a
   list of suggestions, each citing the fact ids it rests on. Code verifies every cited
   id exists and stamps each suggestion with its n; a suggestion citing nothing is
   dropped; any group under `min_n` (default 5) is labelled "too few trades to act on".
4. **Writes** `webgui/data/idea_review/<YYYY-Www>/report.html` (atomic), gitignored,
   in the nightly backup's derived list (it is generated state like `data/eod`).
   If Claude is unavailable the report is still written with the numbers and a line
   saying the judgment is missing, and the tool exits non-zero so the failure alert
   fires. **No paid-API fallback**: the user asked for the scheduled query "instead of
   the API call", so the tool uses `CliMessagesClient` directly, never `FallbackClient`.
   The run needs the claude binary and `~/.config/neuralstrike/claude.env` on vps2, both
   already there for the daily market reports (which moved to vps2 systemd timers on
   2026-09-14; the desktop tasks are disabled).

**Where it is read.** In the app beside `/eod`: a "Weekly review" tab/page that lists
weeks (newest first) and frames the archived document, mirroring `/eod`'s archive
browser. Private only.

### 7. Configuration

New `[idea_trades]` table (builder picks the file: `config/paper.toml` fits), each key
in `config_schema.py`:
`enabled` (kill switch: off = ideas still post, nothing is booked), `quantity` (1),
`debit_stop_frac` (0.60; the idea book's own stop on a debit idea),
`mark_interval_min` (5), `clean_mae_pct` (25), `min_n` (5), `review_model` ("sonnet"),
and `[slots.idea_review] at = "15:30"` in `config/sessions.toml`.

## Left to the builder

- Module split and names; the exact scheduler offsets within the burst-window rule.
- Whether the weekly page is a tab on `/eod` or its own route under the same group.
- The schema of Claude's structured output and the HTML layout (follow `pages/eod.py`'s
  document builders and CSS).
- Tests: the exemption from each of the five book paths; the idempotent booking; MAE
  monotonic; stop-before-target in one mark; settlement of every posted structure;
  `/eod` third book; the review tool's last-trading-day gate, its verification of
  Claude's citations, and its numbers-only fallback.

## Settled after the builder check

- **The user's call:** debit idea trades get a stop of their own (above, `target_pnl`,
  `stop_pnl`).
- **My calls, from the user's words:** each post is its own row even when the same
  trade re-posts on a later day; no paid-API fallback for the weekly judgment.
- **My call:** "Delete closed trades" spares idea rows (section 4).
