# Public Gamma page, any symbol - roadmap

**Status:** planned 2026-09-21, nothing built. Owner's ask: replace the pinned
public Gamma screens with one page that "looks and functions just like the app"
for every symbol in the database.

## What it replaces

Nine `live_screens.SCREENS` entries become ONE `/gamma` screen: `gamma`
($SPX GEX), `gamma-spy`, `gamma-qqq`, `charm`, `dex`, `vanna`, `term`,
`premium-divergence-spy`, `premium-divergence-qqq`. **Net Prem stays its own
screen** (`/net-premium`, unchanged, symbol-independent).

The page keeps the app's symbol dropdown and view tabs: GEX · Charm · DEX ·
Vanna · Flow. No Net Prem tab and **no Term tab** (owner's decisions).

## Decisions (owner, 2026-09-21)

| # | Decision |
|---|---|
| D1 | Symbols are picked from a dropdown. Nothing is typed. |
| D2 | Cap and lease left to engineering: default 8 hot symbols and a 15-minute lease, set by the Phase 0 measurement. Both go in config. |
| D3 | Net Prem stays a separate screen. |
| D4 | **No deep links.** The live origin keeps taking no query parameters; the symbol and view live in page state only. |
| D5 | The old routes REDIRECT to `/gamma`, opening on that route's symbol and view. `/term` opens on $SPX GEX. |
| D6 | **No Term view at all** on the public page, so a hot symbol makes no extra Schwab call. The hot path builds without `_term_chain` (a `with_term=False` on `gamma_snapshot`). |

**D1 detail, settled 2026-09-21:** the dropdown offers the app's own list,
`symbols.toml` `[collection]` PLUS the `Top 20.xlsx` watchlist (92 on prod once
$VIX is dropped). The owner accepted that this publishes the watchlist. The
public dropdown still reads a PUBLISHED copy of the list, not the private key.

## The one real obstacle

The public process computes nothing and can enqueue nothing on `cmd:options`.
Only $SPX, SPY and QQQ have published keys (`cache:options:gamma_pub:<SYM>` and
`gamma_pub_hist_<SYM>_<view>`; `handlers.PUBLISHED_GAMMA_HISTORY_VIEWS`).
Publishing every symbol every minute is about 92 x (0.4 MB snapshot +
4 x 1.1 MB history), **roughly 440 MB of Redis writes a minute**, so that is
ruled out.

**Chosen: on demand, following the Strategy Finder's pattern.** A visitor's
pick puts one validated symbol on a new stream, `cmd:gamma_public`. options_svc
adds the symbol to a HOT SET, which the minute tick publishes like the three
permanent names until its lease runs out. Schwab cost is close to zero: the
collector already fetches every symbol's chain each minute
(`compute.collect_gex_snapshots`) and only has to KEEP the chain for hot
symbols (the capture set, `handlers.py:1803`). With Term dropped (D6) a
hot symbol costs NO extra Schwab call.

## Phases

### Phase 0 - measure (ships nothing)
`tools/measure_gamma_public.py`, scheduled on the box for 2026-09-22 09:20 CT
(a one-off `systemd-run --user` timer; output in `~/phase0/`). It measures, for
seven unpublished symbols built without Term (D6):
- the wall time of `gamma_snapshot` plus the four history payloads;
- the bytes written;
- the live branch's own duration today, over 10 minutes (it already overruns
  its minute 4-9 times a day).

Output: the largest hot-set cap that still lets `refresh_gamma_current` finish
well inside its minute.

### Phase 1 - service (BUILT 2026-09-21, not yet shipped)
Built as below, with one refinement: the tick takes its hot set ONCE, before
the collect (`gamma_public.begin_tick`), and a hot-only symbol is built only
from a chain that collect kept (`handlers.refresh_gamma_hot`). A symbol granted
mid-tick waits one minute rather than costing a Schwab fetch. No daily budget:
nothing here spends Schwab calls, and the cap bounds the CPU and Redis cost. A
lease on $SPX, SPY or QQQ takes no slot but adds their missing history views
(SPY and QQQ publish GEX only), as TTL'd keys. The status view names only
dropdown symbols. Shipping needs the ACL selector (dev-prod-environments §4g).

- `shared/public_gamma.py`: the stream name, command type, key helpers and
  config accessors. The config lives in `config/gamma_public.toml`: cap, lease
  minutes and daily budget.
- A consumer, `services/options_svc/gamma_public.py`, wired in through
  `make_app(extra_consumers=)`. It validates the symbol against the dropdown
  list, drops duplicates, checks the budget and renews the lease. Its refusals
  use the Finder's outcome vocabulary.
- The minute tick publishes `PUBLISHED_GAMMA_SYMBOLS | hot set`, and the
  collector keeps the chains of hot symbols. Hot-symbol keys get a TTL; the
  three permanent symbols keep none.
- A published list key for the dropdown: the same list as the app's
  (`compute.gamma_symbol_options`), written beside it, so the public page
  validates against what it shows.
- ACL: add `(%W~cmd:gamma_public +xadd)` to the `live` user, then run
  `CONFIG REWRITE`.

### Phase 2 - page (`webgui/pages/options/gamma.py`) (BUILT 2026-09-21)
Built as below. Additions: a per-visitor limit on symbol CHANGES
(`[visitor] picks_per_hour`, 30; renewals are free), a line under the
dropdown worded from the service's per-symbol outcome (`status["last"]`),
and the header stamp driven from the symbol on screen. Verified on a local
harness with real prod payloads: $SPX drew, picking NVDA went "Loading" ->
"Live" when the simulated tick published it, Charm and Flow drew, no
horizontal scroll at 375 px.

- `render(public=True)` builds the dropdown and the view tabs. A symbol change
  calls a gated `bus_client.request_public_gamma` and reads that symbol's
  published keys. While the page is open it keeps re-requesting, which renews
  the lease.
- Drop the Term tab in public mode, and build hot symbols with `with_term=False`.
- Split the gate into `shows_picker`, `may_request_public` and `may_enqueue`
  (private). Explain, Analyze, Briefings, Refresh and the history row stay
  absent. `test_live_commands` still covers every caller of `.request(`.
- Level movement, Spot style and Bar become per-tab state. They do nothing
  today, because `app_settings.set` is a no-op on the live origin.
- Move the Flow Alerts hand-off to tab storage. Today it is
  `handoff._pending["gamma"]`, a module-level value shared by every visitor.
- Show a "loading SYMBOL, first draw in about a minute" state for a cold symbol,
  and a refusal line for each outcome.

### Phase 3 - screens, site, tests (BUILT 2026-09-21)
Built as below, except the redirects carry NO state: each retired route is a
plain 308 to `/gamma`, opening on $SPX GEX. Carrying the old route's view
would be a deep link (D4). `PUBLISHED_GAMMA_HISTORY_VIEWS` is unchanged:
$SPX keeps all four views (the page's default symbol), SPY and QQQ keep GEX
and gain the rest through a lease.

- One `Screen("gamma", "/gamma", ..., kwargs={"public": True})`, plus redirects
  from the eight retired routes. A redirect route may seed the page state on the
  server side (it is not a query parameter), so `/charm` opens on $SPX Charm.
- `live.html` gets one Gamma tile ($SPX GEX capture); the Net Prem tile is
  unchanged.
- Rewrite these together:
  - the 24-screen count;
  - the three cross-tier mirror tests (published symbols and history views);
  - the grid tests;
  - the navigation tests (`PUBLIC_ROUTES["/options/gamma"]`);
  - CLAUDE.md, webgui-routes and the user guide.

### Phase 4 - prove on prod
Check four things:
- the tick time with a full hot set;
- Redis memory and write rate;
- one real visitor session on desktop and on phone;
- that no GEX slot is lost.
