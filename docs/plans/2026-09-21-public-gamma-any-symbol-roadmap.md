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
Vanna · Flow · Term. No Net Prem tab (owner's decision).

## Decisions (owner, 2026-09-21)

| # | Decision |
|---|---|
| D1 | Symbols are picked from a dropdown. Nothing is typed. |
| D2 | Cap and lease left to engineering: default 8 hot symbols and a 15-minute lease, set by the Phase 0 measurement. Both go in config. |
| D3 | Net Prem stays a separate screen. |
| D4 | **No deep links.** The live origin keeps taking no query parameters; the symbol and view live in page state only. |
| D5 | The old routes REDIRECT to `/gamma`, opening on that route's symbol and view. |

**Still open (D1 detail): which list the dropdown offers.** The app's list
(`cache:options:gamma_symbols`) is `symbols.toml` `[collection]` PLUS the
gitignored `Top 20.xlsx` watchlist, 92 symbols on prod once $VIX is dropped.
Publishing it publishes the owner's watchlist. The other option is the 27
`symbols.toml` names only. Either way the accessor reads a config switch.

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
symbols (the capture set, `handlers.py:1803`). The one extra cost is Term:
`_term_chain` may fetch up to three wider chains for a symbol that has no daily
expirations. So for hot symbols, Term refreshes every 5 minutes instead of
every minute.

## Phases

### Phase 0 - measure (ships nothing)
Measure on prod during regular trading hours, for about 5 unpublished symbols
(one of them without daily expirations):
- the wall time of `gamma_snapshot` plus the four history payloads;
- the bytes written;
- the extra Schwab calls Term makes.

Output: the largest hot-set cap that still lets `refresh_gamma_current` finish
well inside its minute.

### Phase 1 - service
- `shared/public_gamma.py`: the stream name, command type, key helpers and
  config accessors. The config lives in `config/gamma_public.toml`: cap, lease
  minutes, daily budget, symbol-list switch and Term interval.
- A consumer, `services/options_svc/gamma_public.py`, wired in through
  `make_app(extra_consumers=)`. It validates the symbol against the dropdown
  list, drops duplicates, checks the budget and renews the lease. Its refusals
  use the Finder's outcome vocabulary.
- The minute tick publishes `PUBLISHED_GAMMA_SYMBOLS | hot set`, and the
  collector keeps the chains of hot symbols. Hot-symbol keys get a TTL; the
  three permanent symbols keep none.
- A published list key for the dropdown. If D1 settles on the 27 names, the
  public page must not read the private list.
- ACL: add `(%W~cmd:gamma_public +xadd)` to the `live` user, then run
  `CONFIG REWRITE`.

### Phase 2 - page (`webgui/pages/options/gamma.py`)
- `render(public=True)` builds the dropdown and the view tabs. A symbol change
  calls a gated `bus_client.request_public_gamma` and reads that symbol's
  published keys. While the page is open it keeps re-requesting, which renews
  the lease.
- Split the gate into `shows_picker`, `may_request_public` and `may_enqueue`
  (private). Explain, Analyze, Briefings, Refresh and the history row stay
  absent. `test_live_commands` still covers every caller of `.request(`.
- Level movement, Spot style and Bar become per-tab state. They do nothing
  today, because `app_settings.set` is a no-op on the live origin.
- Move the Flow Alerts hand-off to tab storage. Today it is
  `handoff._pending["gamma"]`, a module-level value shared by every visitor.
- Show a "loading SYMBOL, first draw in about a minute" state for a cold symbol,
  and a refusal line for each outcome.

### Phase 3 - screens, site, tests
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
