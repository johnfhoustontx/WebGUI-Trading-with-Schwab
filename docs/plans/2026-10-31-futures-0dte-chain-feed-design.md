# /ES and /NQ 0-DTE chains on Dealer Positioning: design

**Status:** approved 2026-10-31. Build plan: `2026-10-31-futures-0dte-chain-feed-plan.md`.

## 1. Intent

Show dealer positioning for /ES and /NQ on the private Dealer Positioning page
(`/options/gamma`), computed **only from their own 0-DTE futures option chains**.

The stack has no futures option data today. `tools/nq_hud.py` and
`tools/nq_instruments.py` take a `$NDX`/QQQ or `$SPX`/SPY grid and convert levels
into futures points. The operator does not want that: "I do not want to assume /
calculate from $SPX and $NDX I want it directly from the option chain."

Schwab's API does not serve futures option chains (noted June 2026, not retested
for this work). ThinkorSwim does, and it runs on the operator's Windows PC.

## 2. Goals, non-goals, anti-goals

**Goals**
- /ES and /NQ in the Dealer Positioning symbol dropdown, labelled that way.
- A new snapshot on a configured interval, nearly around the clock: the operator
  asked for 23x5 collection because the instruments trade that way. 5 minutes as
  asked, with 2 minutes under consideration by the operator (see 5.10).
- GEX, Charm, DEX and Vanna from the existing calculation, with the heatmaps and
  the page's other charts for those views. The operator asked for this explicitly.
- A dated Excel copy of what was captured, on the PC.

**Non-goals (first version)**
- /CL. The operator deferred it. Nothing here should hard-code two symbols in a
  way that blocks it: contract size, expiry time and contract naming are per
  instrument.
- The public site. No public page, post, alert or report uses this data. Showing
  it publicly needs the operator's explicit decision.
- Scanner, paper books, flow alerts, trade ideas, the Opening Scalp, the Desk.
- Replacing the `$NDX`/`$SPX`-based NinjaTrader HUD levels.
- Greeks or quotes outside the 0-DTE expiry.

**Anti-goals: failure even if it technically works**
- Any /ES or /NQ number silently coming from `$SPX` or `$NDX`, including as a
  fallback when the PC is off.
- A stale snapshot that reads as current.
- Any change to the numbers of an existing symbol.
- Any Schwab request for a futures chain, or new load on the proxy's rate budget.
- Taking over the operator's screen or disturbing their own Excel or ThinkorSwim.
- Placing or preparing an order. This feed only reads.

## 3. What was measured (2026-10-31, market closed)

Read through ThinkorSwim's Excel link (RTD) in a private hidden Excel instance.

| Finding | Evidence |
|---|---|
| Futures option contracts resolve | `./E1AX26C7600:XCME`, `./Q1AX26C28500:XCME` return data |
| The symbol needs a two-digit year and the exchange | `./E1AX6C7600:XCME` and `/ESZ26` return `N/A` |
| Fields available | `LAST MARK BID ASK VOLUME OPEN_INT IMPL_VOL DELTA GAMMA THETA VEGA RHO` |
| Greeks are rounded to 2 decimals in the raw value | /ES and /NQ `GAMMA` is `0.0`, a Double; the chain UI also shows 0 |
| `IMPL_VOL` is a percent STRING | `"12.51%"` |
| Rounding is the only reason gamma is 0 | /CL 103 strike returns 0.11 (Monday), 0.06 (Friday) |
| Open interest is thin on the sampled strikes | /ES Monday 7600: 25 call, 461 put; /NQ Monday 28500: 0 call, 153 put |

A hand calculation from implied vol, price and time left gave about 0.109 and 0.058
for those /CL contracts, agreeing with ThinkorSwim at its 2 decimals. That is a
small check on two strikes, not a validation.

**Full ladder, same day.** Filled within about 3 seconds, the first check, with no
further change over 15 seconds. The market was closed, so no values were moving.

| Ladder (Monday expiry) | Contracts tried | Cells | Listed | Total open interest | Total volume |
|---|---|---|---|---|---|
| /ES, 7240-8000, every 5 points | 306 | 2,448 | 306 | 42,952 calls, 23,170 puts | 130,474 calls, 78,844 puts |
| /NQ, 27100-30000, every 10 points | 582 | 4,656 | 424 | 905 calls, 3,062 puts | 7,942 calls, 11,585 puts |

/NQ's listed strikes are not uniformly spaced at 10 points: 158 of the tried
contracts returned `N/A`.

**Contract names that returned data:**

| Expiry | /ES | /NQ |
|---|---|---|
| Monday (first) | `E1A` | `Q1A` |
| Tuesday, Wednesday, Thursday (first) | `E1B`, `E1C`, `E1D` | `Q1B`, `Q1C`, `Q1D` |
| Friday (first) | `EW1` | `QN1` |
| Third Friday, serial and quarterly month | `EW3` | `QN3` |
| Month-end | `EW` | `QNE` |
| Quarterly | `ES` | `NQ` |

`E5AX26` returned `N/A`. November 2026 has a fifth Monday, the 30th, which is also
month-end, and it trades under `EW`. A weekday-and-week table alone would pick the
wrong name.

**Each contract states its own terms.** `DESCRIPTION` over RTD returned, for example:

```
/ESZ26:XCME 1/50 2 NOV 26 (Monday) (Wk1) /E1AX26:XCME 7600 CALL
/ESZ26:XCME 1/50 30 NOV 26 (EOM) /EWX26:XCME 7600 CALL
/ESZ26:XCME 1/50 18 DEC 26 (AM Settled) /ESZ26:XCME 7600 CALL
/NQZ26:XCME 1/20 2 NOV 26 (Monday) (Wk1) /Q1AX26:XCME 28500 CALL
```

So the underlying future, the multiplier, the expiry date and morning settlement are
all read off the contract, and no hand-kept expiry table is needed. `EXPIRATION_DAY`
reads one day later than the description (`2026-11-03` for 2 NOV), so it is not used.
`QN1X26` and `QN2X26` returned `N/A` at strike 28500 while `QN3X26` and `QNEX26`
resolved; whether those two exist at other strikes is unmeasured. A listed /NQ
contract can read `N/A` for `OPEN_INT`.

**Named futures read directly:** `/ESZ26:XCME`, `/ESH27:XCME`, `/NQZ26:XCME` and
`/NQH27:XCME` all return prices.

**Excel startup is not reliable by default.** With no Excel open, creating
`Excel.Application` through COM failed twice with `0x80080005`, and Windows logged
DCOM event 10010 ("did not register ... within the required timeout"). Starting
`EXCEL.EXE /automation -Embedding` directly first let the connection succeed. Why
is unknown. Calling `Quit()` also did not always end the process: several hidden
copies were left running after failed runs and had to be stopped by process id.

**Not measured:** behaviour during a live session with moving prices, how often open
interest updates, and a start from a scheduled task. Claims about CME settlement
hours below are from general knowledge and must be confirmed.

## 4. Decisions

**Operator's decisions**
- Instruments: /ES and /NQ. /CL later.
- Destination: Dealer Positioning, not the Desk.
- Source: the instruments' own option chains, via ThinkorSwim. Never index-derived.
  Chosen after comparing suppliers (see 8), knowing it needs ThinkorSwim open and
  logged in.
- Coverage: 23x5, not only the regular session.
- Cadence: 5 minutes. The operator also asked about 2 minutes.
- The heatmaps and the page's other charts are in scope.
- Gamma: **calculated per contract** from that contract's own implied volatility,
  strike and time left, and the future's price, because ThinkorSwim's value rounds
  to 0.

**Claude's calls (the operator can overrule any of these)**
- RTD through Excel rather than scripted clicks on the chain's export.
- Open interest as the first weighting, matching every other symbol.
- Delta, implied volatility, open interest and volume come straight from
  ThinkorSwim. The engine already falls back to a calculated delta when it reads 0.
- Delivery by one `ssh vps2` per snapshot.
- Internal symbol names `FOP.ES` and `FOP.NQ`, labelled /ES and /NQ. See 5.3.
- Contract size by scaling the contract counts in the adapter. See 5.4.
- The Excel copy lives in `%USERPROFILE%\Documents\ToS 0DTE\<date>.xlsx`.
- First live proof is a shadow session on the server before the page shows the
  symbols. See 6.
- ThinkorSwim on the operator's PC, not a Windows VPS. The operator's choice was
  ThinkorSwim; the machine is Claude's call, since it needs no new server. The capture
  does not depend on which Windows machine it runs on, so it can move later.
- Between sessions the by-strike charts show the last usable capture, labelled with
  its time. See 5.9.
- Ship at 5 minutes, and move to 2 only after the first session measures it.
- A phone alert when capture stops. See 5.9.

## 5. Design

### 5.1 Shape

```
PC: ThinkorSwim -> hidden Excel (RTD) -> capture script
       | every 5 min: one ssh vps2, snapshot on stdin        also: dated .xlsx
       v
vps2: tools/ ingest CLI -> XADD cmd:futures_chain
       v
options_svc consumer -> adapter (validate, calculate gamma, build a chain)
       -> derive_minute into gex_history.db
       -> cache:options:futures_chain:<FOP.xx> -> gamma_snapshot(chain=...)
       -> the existing gamma cache view + event -> Dealer Positioning
```

### 5.2 On the PC

A scheduled Windows task starts a capture script before the session. PowerShell
needs nothing installed and is what the probe used; the builder may choose
otherwise. It must:

- Own a private hidden Excel instance and never touch the operator's own Excel
  windows. Start it explicitly (`EXCEL.EXE /automation -Embedding`, see 3), track
  that process id, and stop that id on exit, because `Quit()` alone left copies
  behind. Survive a failed start with a logged retry.
- Build each day's contract names itself from the names in 3, followed by month
  code, two-digit year, `C`/`P`, strike and `:XCME`.
- Collect **every afternoon-settled series expiring that date**. Open interest and
  volume add per strike across series; gamma is calculated per contract before
  adding, since all share one expiry time. A morning-settled series, which the
  contract's description marks `(AM Settled)`, has already expired during the session
  and is excluded by rule. This is Claude's call.
- Refuse to guess at an unknown name. Each series must state this expiry date in its
  own description, and the server checks that again. If no series resolves, it sends
  no chain and reports why.
- Enumerate strikes around the live future to a configured width, because RTD offers
  no chain listing. An `N/A` contract means not listed, which is normal: /NQ's spacing
  is uneven. Delta and implied volatility can read `N/A` on a listed contract.
- If capture proves too slow or disturbs ThinkorSwim during a live session, narrow the
  strike width first, and show the captured range on the page, because walls and the
  flip only see captured strikes. Never slow the 5-minute cadence without asking the
  operator. Claude's call.
- On the configured interval, on the clock, through the futures session (see 5.11),
  read values and send one snapshot with the capture timestamp and the underlying
  price. CME's holiday schedule differs from the NYSE's, so whether to capture on a
  given day follows whether the contracts return live data, not `market_calendar`
  alone.
- Rebuild the contract list when the expiry changes, without restarting Excel if it
  can be avoided, because subscriptions move to a new expiry mid-evening.
- Save a dated `.xlsx` copy of the values, one file per expiry date.
- Refuse to send a snapshot when ThinkorSwim is logged out or nearly all cells read
  `N/A`, and log it. Keep retrying, so capture resumes on its own after the operator
  logs back in. The script never enters a password.

ThinkorSwim's help says to keep only one instance open, and to run Excel as
administrator if it was installed for all users. Its install here is per-user. The
script must not start a second instance.

The contract names are confirmed only for the sampled dates. Whether all of this holds
on a Sunday-night restart, a holiday week, an overnight logout or a Windows update is
unknown and belongs in the plan's tests and first sessions.

Underlying price: read the future the contract's own description names, not continuous
`/ES:XCME` and not `shared/futures.py`'s front month, which rolls by the operator's chart
convention eight days before expiry. It is checked against the chain each capture:
at-the-money call minus put, plus strike, gives the price the options themselves imply.
A disagreement beyond a configured limit stops that instrument's snapshot and reports it,
rather than shifting every strike's gamma without any visible error. No readable pair
(overnight quotes can be missing) is not a disagreement: the snapshot goes through and
records that the check could not run.

### 5.3 Symbol names

`shared/symbols.SYMBOL_RE` refuses `/`, deliberately: its comment says a futures
root could never have a dossier, and the value becomes part of a Redis key. It also
guards the public pages. **Do not loosen it.**

Internally these are `FOP.ES` and `FOP.NQ`, which pass the existing rule and cannot
collide with a real ticker. `ES` alone would: it is Eversource. The page labels them
/ES and /NQ through one mapping in `shared/`. Whichever module holds it joins the
Tier-1 allow-list in `CLAUDE.md` if the page imports it.

### 5.4 Contract size

`gamma_tool.py` and `forward_grid.py` assume 100 per contract in dozens of lines.
/ES is 50 and /NQ is 20.

The adapter multiplies each contract's open interest and volume by `multiplier / 100`
(0.5 and 0.2) before the engine sees them. Every downstream dollar figure is then
right and **`gamma_tool.py` is not edited**, which guarantees existing symbols do not
move. The per-instrument multiplier is config.

The cost: a contract COUNT read downstream is scaled. The builder must find anything
on the page that prints a raw count for the selected symbol and hide or correct it
for these two. If that list turns out long, say so and reconsider a real engine
parameter.

### 5.5 Gamma

Per contract, from that contract's own values: `φ(d1) / (F · σ · √T)` with zero
carry, which is what Black-76 gives on a future apart from a discount too small to
matter at this horizon. Time left must go through the tier's one helper; the page's
engine closes at 15:00 CT (`CLOSE_HOUR_CT`), and E-mini PM expiries are understood to
stop at 15:00 CT, to be confirmed. A contract with no usable implied volatility has
**no** gamma: absent, never zero.

The engine's gamma comes from the chain and has no fallback, so the adapter fills
the chain's gamma field and the engine stays unchanged. Near the close, at-the-money
gamma spikes; this is the same behaviour as other 0-DTE symbols, not a defect here.

### 5.6 Delivery and ingest

- `ssh vps2 '<venv python> tools/<ingest>.py'` with the snapshot on stdin. Use the
  public alias: the tailnet is refused on this PC while ProtonVPN is connected. One
  connection per 5 minutes is well below the burst that rate-limited this host
  (about a dozen in a few minutes). The scheduled task must be able to use the SSH
  key unattended; this has not been tried.
- A stream of its own, `cmd:futures_chain`, with its own low `[stream_keep]` entry
  set BEFORE anything writes it, because each command carries a document. Not
  `cmd:options`, whose table of commands guards paper books.
- The consumer drops a snapshot older than a configured age so a backlog never
  replays as current. Never create the consumer group in a way that replays the
  stream's history (known hazard, see memory `command-streams-replay-on-fresh-group`).
- Validate on write: a small contract model; a payload that fails is not published
  and counts as a degrade.
- Snapshots are per-instrument independent. /NQ failing does not block /ES.

### 5.7 Server side

- **Never reach Schwab.** `_gamma_fetch_chain` and any refresh path must answer these
  symbols from the stored latest chain. They must stay out of
  `gex_collector.collection_symbols()`, so the collector, watchlist fan-outs, flow
  detectors and traded-premium hook never see them.
- `with_term=False`. There is one expiry, so Term is hidden for these two, along
  with any tab that needs data this feed lacks.
- Storage: `gex_history.db` through `derive_minute`, with the minute being the
  capture time. `prem` comes out correct, because scaled volume times mark times 100
  is the true dollar figure. Two things checked in the code do not work as for any
  symbol. The purge counts distinct calendar dates, so a Sunday-evening capture would
  cost every existing symbol a session; these rows must not count. The archive closes
  a date at the stock session's stop, so rows from that evening would be missed and a
  Sunday would never close; **the Gamma Archive leaves these symbols out in the first
  version**, and they keep the store's 5 sessions only.
- `gamma_symbol_options` feeds both the private key and the public
  `public_gamma.SYMBOLS_KEY` from one list (`handlers.publish_gamma_symbols`). The
  two must be split so these names reach the private list only, with a test that
  they never reach the public one.

### 5.8 Five-minute rows on a one-minute page

`heatmap_matrix` makes one column per stored row, and the projected afternoon is one
column per minute, so 5-minute rows would squeeze the morning relative to the
projection. Hedge and level panels share that axis.

Plan: leave the store truthful with one row per capture, and when building the view
repeat each row across the minutes to the next capture, for these symbols only, up to
one interval. A longer outage stays a gap. Existing symbols go through unchanged
code. The builder checks the interpolated heatmap (`uniform_strike_grid`,
`raster_range`) with these strike grids: /NQ's strike spacing is unmeasured.

### 5.9 Staleness

Each snapshot carries its capture time. When the newest is older than a configured
limit in the session, the page labels the symbol stale with the age of the data, and
the numbers stay as they were, marked. It must never fall back to index data. `/health`
reports the feed.

**A phone alert fires when capture stops during the session**, through the existing
notification path, and clears when it resumes. It needs a check on the server, because
a PC that is off cannot report itself. The alert must be quiet in the daily gap and at
weekends, and rate-limited so an overnight logout does not repeat all night.

**Between sessions** (the daily gap, weekends, holidays): the heatmap keeps the last
session. The page's rule for existing symbols cannot carry over, because a stored chain
past its expiry has no nearest expiry, so `gamma_snapshot` returns `None` and the page
would go blank. The by-strike charts instead show the last capture taken at least a
configured time before expiry, frozen at that capture's own time and labelled with it,
so the spike in gamma close to expiry is not left on screen all weekend. That requires a
priced-at time passed to `gamma_snapshot`, with existing callers unchanged.

### 5.10 The interval is config

The capture interval is one config value, and nothing may assume 5. Estimated cost of
2 minutes against 5 over a 22-hour session, not measured:

| Where | 5 minutes | 2 minutes | Note |
|---|---|---|---|
| Excel and ThinkorSwim | same | same | Subscriptions tick live whatever the interval; only the read repeats |
| Schwab and the proxy | none | none | This feed never calls them |
| SSH connections per hour | 12 | 30 | Reuse one long-lived connection if 2 minutes is chosen |
| Captures per session | 264 | 660 | 2.5 times the work on the server |
| Stored rows per session, 2 symbols, 5 views | 2,640 | 6,600 | Existing symbols write 1,950 each |
| Excel copy rows per session, 730 contracts | about 193,000 | about 482,000 | Under Excel's 1,048,576-row sheet limit; a sheet per symbol is safer |

The first shadow session measures capture time, snapshot size and the server's work.

### 5.11 The futures session

The page and the stack think in stock-market days. These symbols do not.

- **One session is one expiry:** from the evening trading opens for it until it expires,
  understood to be about 17:00 CT the evening before to 15:00 CT, with a gap of about
  two hours and a longer one at weekends. Confirm these times against CME. Rows and
  views for these symbols key on the expiry date, not the calendar date of the capture,
  so a Sunday-evening capture belongs to Monday's session.
- **The time charts:** the page's filters (`_rth_only`, `_rth_bounds`,
  `_display_session_date`) keep 08:30 to 15:00. These symbols need the full session, and
  existing symbols must go through unchanged code. Offer no new switch that overrides an
  existing one.
- **Heatmap size:** 22 hours is more than three times the columns of a regular session.
  A fill to one column a minute would be about 1,320 columns. Measured at build on
  synthetic chains (264 captures, 365 strikes, on the real engine and store) against a
  regular `$SPX` session of 5.28 MB: a 60-second step gave 1,316 rows and 16.85 MB (3.19
  times), and a 120-second step 790 rows and 10.18 MB (1.93 times). In the browser, every
  repaint at about 790 columns blocked the page for 4.5 s, past NiceGUI's 2 s ping, so the
  feed ships at `fill_step_sec = 300`: one column per capture, each a real capture, with
  no row repeated. The operator can lower it in Settings. A real session's wider spot
  path may crop larger; the shadow session measures that. The picture must stay on its
  axis.
- **The projected afternoon** (`forward_grid`, `gamma_heat.forward_series`) assumes the
  session it knows. Check that it projects from any hour of the night.
- **The engine's time left is wrong overnight.** `gamma_tool.py` computes
  `hours_left_trading = max(0, 15 - hour ...)` and adds `dte * 24`. At 18:00 CT the
  evening before expiry it gets 24 hours, where 21 remain, which moves charm, vanna and
  any calculated delta. The fix must leave every existing symbol byte-identical, such as
  an optional time-left argument that only this feed passes. This is a small and
  justified edit to `gamma_tool.py`, unlike the contract size.
- **Overnight quality:** quotes widen and ThinkorSwim's implied volatility may go missing
  or jump on far strikes. Absent stays absent. Compare overnight levels against the
  regular session before relying on them.
- **Purges and retention:** `gex_history.db` keeps 5 sessions and the archive thins closed
  sessions. Check both key correctly for a session spanning two calendar dates.

## 6. Rollout

1. A shadow session, overnight and through the next regular session: the feed runs and
   writes the store and cache, with the symbols not yet in the dropdown. Read the output
   against ThinkorSwim, including computed gamma against the 2-decimal value wherever
   that value is large enough to compare, late in the session. Measure capture time,
   snapshot size and server work, and watch ThinkorSwim and Excel for memory growth and
   lag, especially if the operator trades from this PC.
2. Compare open-interest and volume weightings over a few sessions, and report it to
   the operator. Do not change the weighting without them.
3. The operator decides whether to show them.

Promotion follows `tools/promote.sh` and the trading-day timing rule.

## 7. Risks

- **The PC is a single point of failure** for this feed, by design. For 23x5 it must stay
  on, awake and online overnight, including through Windows updates.
- **ThinkorSwim may log out** or be restarted, and its RTD could drop mid-session. Only
  the operator can log back in, so a logout overnight is a gap until they do. **The 23x5
  goal is therefore a target, not a guarantee.**
- **Nothing shows whether a long unattended ThinkorSwim session is reliable.** The first
  sessions measure it.
- **Open interest may make the levels unrepresentative.** Monday's whole /NQ ladder
  held 3,967 contracts of open interest against 19,527 of volume, and open interest
  cannot include 0-DTE positions opened and closed the same day. /NQ's walls may rest
  on very few contracts. This is why step 2 of the rollout compares the weightings.
- **Dealer sign is an assumption** carried over from the index formula (long calls,
  short puts). Futures option dealers may be positioned differently; the page's
  meaning for these symbols is less tested than for `$SPX`.
- **A full ladder is only proven with the market closed.** 7,104 cells filled in
  seconds on a Saturday. Live ticks across that many subscriptions could load Excel or
  ThinkorSwim differently, on a machine the operator may be trading from. The first
  session must watch this.

## 8. Alternatives considered (2026-10-31, from documentation, none tested)

| Source | Finding | Why not now |
|---|---|---|
| Databento | Confirmed CME options on futures; quotes and trades, no Greeks; $199 a month; personal use only | The operator chose ThinkorSwim. The first alternative if the PC proves unreliable, and most of this design carries over |
| ThinkorSwim on the Ubuntu server | RTD is Windows-only | Not possible on this box |
| ThinkorSwim on a Windows VPS | Same design on another machine; still needs a manual login | Possible later |
| TradeStation | Expiry and strike lookups accept "an equity or index" only | No futures chain found; no API key to test |
| Unusual Whales | CME futures contracts only; no options on futures listed | Ruled out |
| NinjaTrader | "Options are not supported for NinjaScript use" | Ruled out; also runs live trading |
| tastytrade, Interactive Brokers | Chains exist; live Greeks and costs unconfirmed | Not pursued |

## 9. Left to the builder

The capture language, config file layout and key names, stream cap value, strike
width, Windows task details, Excel copy layout, and the wording of labels (whole
words, per project convention). Add `config_schema.py` entries, the suite's CI row,
manual and `page_help.py` updates, and `CLAUDE.md` changes for a new stream, Tier-1
import or invariant.
