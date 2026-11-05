# Send a trade idea to ThinkorSwim as a live order: design

**Date:** 2026-11-05 · **Status:** design, not built · **Branch:** `claude/live-trade-thinkorswim-47ff6c`

## What the operator asked for

> When a trade idea is generated I need you to give me the opportunity to send a
> live trade to ThinkorSwim. This will mimic what is generated as a script, which
> the app already does. I should be able to select the number of contracts and
> which account I need to send the trade to.

Decisions taken on 2026-11-05:

- **The exits are worked out from the opening order.** The profit target and the
  stop are set from the opening order's **limit price**, using the same
  `config/tos_order.toml` percentages the paste script uses (`[debit]` +100 % /
  −50 %, `[credit]` −50 % / +100 %). The paste script's exits are percentages of
  the FILL, which ThinkorSwim works out after the fill. The API takes fixed prices
  (see "The one difference from the paste script").
- **The number of contracts is typed in by hand.** The box starts empty and
  nothing can be sent until it holds a whole number from 1 to `max_contracts`. The
  box has no default, so a size is never chosen for the operator.
- **The account is picked from a list** of the linked Schwab accounts, limited to
  an allow-list the operator keeps.
- **Paper-only is reversed on purpose, behind a switch that ships off.**

Not yet answered by the operator, so these are this design's defaults. Each one is
in config, so changing it means editing a setting, not the code:

- A fresh authenticator (TOTP) code is required for every Send (`require_totp = true`).
- Tight limits as shipped (`max_contracts = 5`, `max_risk_usd = 1000`), in line
  with the operator's standing preference for the tightest risk controls.

## "Sending to ThinkorSwim" means placing a Schwab order

ThinkorSwim is a front end on the Schwab brokerage account. Nothing outside it can
type into it, and the paste-from-clipboard feature needs a person at the keyboard.
So an order "sent to ThinkorSwim" is an order **placed through the Schwab Trader
API** (`POST /trader/v1/accounts/{accountHash}/orders`). It appears in
ThinkorSwim's order book at once, where the operator can watch, change or cancel it
like any other order.

## What this reverses, and why it is safe to

On 2026-10-03 the audit (SE-02) deleted the proxy's `POST /orders/{account_hash}`.
It forwarded a real order, nothing called it, and on prod its secret check passed
with no secret configured. Since then:

- `trader_request` raises on any method but GET (`schwab-proxy/schwab_proxy.py`).
- `schwab-proxy/tests/test_account_surface.py` fails on a new write.
- CLAUDE.md states "this app is paper-only" and "it has no order route".

The audit's finding was not "an order route exists" but **an order route that
anyone could reach, that nothing used, guarded by a check that didn't check**. This
design brings back a route that:

1. needs `PROXY_SHARED_SECRET` and refuses outright without one (as the account
   reads do now);
2. is off unless `config/live_orders.toml enabled = true`, read on **every**
   request;
3. places only an order the service built, for an account on the allow-list,
   within the size and risk limits, in regular market hours, as a LIMIT order;
4. is reachable from one stream, `cmd:live_order`, which the public process cannot
   write.

`test_account_surface.py` changes from "no writes" to "exactly these two writes,
each behind `require_account_secret` and the switch". The CLAUDE.md sentences are
**edited in place** in the same commit (rule 2 of that file).

The paper books are untouched: a live order is never booked into the Paper Ledger
or the Paper Account, and nothing in those books ever sends a live order.

## The operator's path

1. **The prompt, inside the app only.** When `run_trade_idea` posts an idea
   (`services/options_svc/handlers.py`), it already publishes
   `cache:options:trade_idea`. The private app's shell (`_layout` in
   `webgui/main.py`) watches that view's `:ver` counter (`view_watch.watch_view`)
   on whatever page is open. When the version moves to a new idea, it shows a
   banner under the header: *"New trade idea: PLTR Bull Call Spread, 21 Nov 26"*,
   with **Open order ticket** and **Dismiss**. The banner links to
   `/send-order?idea=<id>&posted=<stamp>`; it never sends anything itself. A
   dismissed idea stays dismissed in that browser (tab storage), and a banner whose
   idea is older than `[prompt] banner_max_age_min` is not shown.
   **No message leaves the app**: nothing goes to Telegram, Discord, X, ntfy or the
   site. Telegram's chat has a wide audience, and a ticket link there would show
   who acts on which idea, even though the page itself is behind the login. The
   banner exists in `main.py` only, so the public process (`live_main.py`) never
   draws it.
2. **From the Paper Ledger.** An idea's row (`is_idea`) gets a
   **Send to ThinkorSwim** button that opens the same page, for an idea that was
   missed or dismissed.
3. **The ticket page** (`/send-order`, behind the login; never in `live_screens`):
   - the idea as posted: structure, legs, expiry, the price it was posted at;
   - the live mid of those legs, fetched fresh (`maxAge=0`), and how far it has
     moved from the posted price, with a warning past `max_drift_pct`;
   - **Limit price**: starts at the live mid, can be edited, rounded to the tick;
   - **Contracts**: an empty box the operator types into;
   - **Account**: a dropdown of allow-listed accounts, showing nickname and last 4
     digits, never the full number;
   - worked out from the above: max risk, max profit, the target and stop prices,
     and an estimate of commissions (`config/commissions.toml`);
   - the order text in ThinkorSwim's paste format (`tos_order.order`), so the
     operator can compare it with the script they have already checked.
4. **Preview.** The service builds the Schwab order and sends it to Schwab's
   `previewOrder`, which checks it **without placing it**. The page shows Schwab's
   verdict, warnings and the effect on buying power. Changing the price, size or
   account clears the preview.
5. **Send.** It stays disabled until a preview has passed within
   `ticket_ttl_sec`. It asks for a TOTP code (`auth.verify_totp`), then a final
   confirmation that names the size, the account's last 4 digits and the limit
   (`kit.confirm`).
6. **After.** The page shows Schwab's order ID and the order's status (Working,
   Filled, Rejected, Canceled), read from Schwab every few seconds while the page
   is open.

## Architecture (inside the three tiers)

```
webgui /send-order (Tier 1)            options_svc (Tier 2)                 schwab-proxy
  build ticket view  ──cmd:live_order──▶ live_order_handlers                  POST /orders/preview
  Preview / Send       {preview|place}    · builds the payload (shared/)  ──▶ POST /orders/place
  reads cache:live_order:ticket:<id> ◀──  · live_orders.db (journal)          GET  /orders/{hash}/{id}
                                          · status poll                       GET  /accounts/nicknames
```

### Shared, pure: `shared/live_order.py`

The order builder, pure (`math`, `datetime`, `shared.symbols` and nothing else),
imported by both tiers, as `shared/book_caps.py` is. Tier 1 uses it to show the
same numbers the service will send; Tier 2 uses it to build the payload. It joins
the Tier-1 allow-list in CLAUDE.md with a pinned import-set test.

- `legs_from_idea(idea)`: the idea's legs, signed, as `trade_idea.normalize` holds
  them.
- `side(legs)`: debit or credit, read off the **legs**, never off the price's sign
  (the rule `tos_order` already follows).
- `exit_prices(limit, side, cfg)`: target and stop from the limit price and the
  `tos_order.toml` percentages, rounded to the tick.
- `tick(price, symbol, cfg)`: $0.05 under $3 and $0.10 at or above, with a penny
  set in config (`[ticks]`). Preview catches anything this misses.
- `occ_symbol(root, expiry, right, strike)`: Schwab's option symbol
  (`SPXW  261106C05800000`, root padded to 6). The SPX / SPXW choice follows the
  expiry, as `tos_order`'s `(Weeklys)` tag does.
- `schwab_order(legs, contracts, limit, cfg)`: the JSON below.
- `risk(legs, contracts, limit)`: max loss at that size, read off the same payoff
  function `trade_idea.economics` uses (never the rows' own `max_loss` fields).

A test drives every one of the 15 structures in `test_tos_order.PASTED` through
both `tos_order.order` and `schwab_order`. The two must agree on leg order, side
and quantities.

### The order Schwab is sent

A "1st triggers OCO" bracket, the API's form of the paste script:

```json
{
  "orderStrategyType": "TRIGGER",
  "orderType": "NET_DEBIT", "price": "1.50", "duration": "DAY", "session": "NORMAL",
  "complexOrderStrategyType": "VERTICAL",
  "orderLegCollection": [
    {"instruction": "BUY_TO_OPEN",  "quantity": 3, "instrument": {"assetType": "OPTION", "symbol": "SPXW  261106C05800000"}},
    {"instruction": "SELL_TO_OPEN", "quantity": 3, "instrument": {"assetType": "OPTION", "symbol": "SPXW  261106C05810000"}}
  ],
  "childOrderStrategies": [{
    "orderStrategyType": "OCO",
    "childOrderStrategies": [
      {"orderStrategyType": "SINGLE", "orderType": "NET_CREDIT", "price": "3.00",
       "duration": "GOOD_TILL_CANCEL", "session": "NORMAL", "complexOrderStrategyType": "VERTICAL",
       "orderLegCollection": ["… the same legs, TO_CLOSE, sides reversed …"]},
      {"orderStrategyType": "SINGLE", "orderType": "STOP", "stopPrice": "0.75",
       "duration": "GOOD_TILL_CANCEL", "session": "NORMAL", "complexOrderStrategyType": "VERTICAL",
       "orderLegCollection": ["… the same legs, TO_CLOSE, sides reversed …"]}
    ]
  }]
}
```

The opening order is DAY and the exits take `exit_tif` (`GTC` → `GOOD_TILL_CANCEL`),
as the paste script does.

### The one difference from the paste script

The paste script writes `@TRG+100.00%`. ThinkorSwim prices that exit off the
**fill** once the order fills. The API takes fixed prices, so the exits here are
priced off the **limit** (the operator's decision above). A fill better than the
limit therefore leaves the exits a little wider than a pasted order's. The page
says so beside the exit prices.

⚠ **Not yet known: whether Schwab's API accepts a STOP on a multi-leg spread.**
ThinkorSwim's own text does (`STP TRG-50.00% MARK`), but that does not prove the
API takes it. Phase 0 measures it with `previewOrder`. If the API refuses it, the
bracket becomes opening order + profit target, and the page says in plain words
that **the stop is not on the order** and must be set in ThinkorSwim.

### The proxy

- `trader_request` allows POST to exactly two endpoint shapes,
  `/accounts/{hash}/previewOrder` and `/accounts/{hash}/orders`. Anything else still
  raises.
- `POST /orders/preview` and `POST /orders/place`, both behind
  `require_account_secret`, both refusing with 403 while `live_orders.toml enabled`
  is false. The proxy reads the switch itself, so turning it off stops orders even
  if a service is wrong.
- `place` returns the order ID from Schwab's `Location` header (201, empty body).
- New read-only routes: `GET /orders/{hash}/{order_id}` (status) and
  `GET /accounts/nicknames` (from `/userPreference`), for the dropdown.
- None of these is on the public domain; the proxy never is.

### The service: `services/options_svc/live_order_handlers.py`

A sibling module that imports nothing from `compute` (the size ceiling). It has a
stream of its own, **`cmd:live_order`**, with its own consumer thread, its own
`[stream_keep]` entry (50) and none of `cmd:options`' table. It is never on the
slow lane.

Two commands:

- **`preview`** `{idea_id, posted, account_hash, contracts, limit}`: re-reads the
  idea from the Ledger by `idea_id` + `idea_posted` (never from the page's echo),
  checks the limits, builds the payload, calls the proxy's preview, and stores a
  **ticket** in `live_orders.db`: an id, the exact payload, its digest, the
  account hash, `status = 'previewed'`, `expires_at`. The result is published to
  `cache:live_order:ticket:<id>`.
- **`place`** `{ticket_id}`: places the **stored** payload, never one the page
  sends (the rule `rescue_apply` follows). It moves the ticket
  `previewed → sending` in ONE `UPDATE … WHERE status = 'previewed' AND expires_at > now`;
  if no row changed, it refuses. So a second delivery of the same command places
  nothing.

**Against replay:** this matters more here than anywhere in the app, because a
fresh consumer group replays the stream's whole backlog on its first launch.
Three guards, each enough by itself:

1. the consumer drops any `cmd:live_order` command older than `max_command_age_sec` (30);
2. a ticket is single-use (the conditional `UPDATE`);
3. a ticket expires `ticket_ttl_sec` (120) after its preview.

**An answer that never comes is not a failure.** If the proxy's `place` call times
out or errors after the request went out, the order may still have been placed. The
ticket becomes `unknown`, **never retried**, and the page says "check ThinkorSwim
before sending again". The service then lists the account's orders for the last few
minutes (`GET /accounts/{hash}/orders`) and, if it finds a match on legs and price,
records its ID.

### The store: `services/options_svc/data/live_orders.db`

Columns: ticket id, idea id and posted stamp, account hash (never the number),
contracts, limit, the payload JSON, preview verdict, `status`
(`previewed | sending | sent | unknown | rejected | expired`), Schwab order ID, and
timestamps. It holds no secret. It goes in the nightly backup and is never added to
`DB_EXCLUDE`.

### Configuration: `config/live_orders.toml`

Following the configurable-by-default rule, every value has an entry in
`webgui/config_schema.py` (Settings → Configuration).

```toml
enabled = false              # the one switch; read on every request by the proxy and the service
accounts = []                # last 4 digits of the accounts orders may go to. Empty = none.
                             # Set it in config/local/live_orders.toml (gitignored), never here.
max_contracts = 5            # the Contracts box's ceiling
max_risk_usd = 1000.0        # max loss of one order, at its size
max_drift_pct = 10.0         # warn when the live mid has moved this far from the idea's price
require_totp = true          # a fresh authenticator code on every Send
ticket_ttl_sec = 120         # a preview is good for this long
max_command_age_sec = 30     # older cmd:live_order commands are dropped unread
status_poll_sec = 5          # the page's status refresh while an order is working

[prompt]
banner = true                # show the in-app "New trade idea" banner when an idea posts
banner_max_age_min = 30      # don't show the banner for an idea older than this

[ticks]
penny = ["SPY", "QQQ", "IWM"]   # symbols that trade in $0.01 under $3 and $0.05 above
```

The size and risk limits are checked in three places: the page (so Send can't
light), the service (the truth), and a second time by the service just before the
place.

### What the public site can never reach

- The read-only Redis user (`REDIS_LIVE_URL`) cannot `XADD` to `cmd:live_order`;
  `test_bus_client.py`'s pinned writer set does not include it.
- `/send-order` is a `@ui.page` in `main.py` only, never in `live_screens.py`.
- The ticket cache holds account hashes: it gets a key prefix
  (`cache:live_order:`) the public ACL user cannot read.
- `/ES` and `/NQ` (`futures_chain.is_feed_symbol`) are refused at the builder:
  Schwab cannot place futures options.

## What is left out, and why

- **Protective puts and collars**: they hold shares beside a long put, and there is
  no order text for them; `tos_order` already gives them none.
- **Covered calls**: these buy 100 shares a contract. They come in the last phase,
  as an opening order only, as the paste script does.
- **Calendars and diagonals** never post as ideas, so they never reach this page.
- **Market orders**: never. A limit only.
- **Booking a live fill into the paper books**: not done. The paper books stay a
  measurement of the app's picks, not of what the operator traded.

## Phases (the smallest usable slice first)

0. **Measure, placing nothing.** Add the proxy's preview route only, with the
   switch on for preview alone (`enabled = "preview"`). Preview a single option, a
   vertical and an iron condor, each with the bracket, on SPX, SPY and a stock.
   Record which order types Schwab accepts, the stop-on-a-spread answer above, and
   the tick rule. Write the results into this doc.
1. **Single options, verticals and iron condors**, which covers most ideas: the
   builder, the ticket page, preview, place, the journal and the status read. The
   in-app idea banner comes with it. Ships with `enabled = false`.
2. **The other structures**: butterflies, condors, straddles and strangles, CUSTOM.
3. **Cancel from the page** (a third write route, `DELETE`, by the same rules), a
   list of today's live orders, and the covered call.

Each phase updates the manuals and `webgui/page_help.py` in the same commit, adds
the `cmd:live_order` row to the API Reference, and edits CLAUDE.md's
paper-only and order-route sentences in place (phase 1).

## How it is tested and verified

- **Unit tests**: the builder against `test_tos_order.PASTED`; the exit prices for
  debit and credit; rounding to the tick; refusals (no account, zero or too many
  contracts, risk over the limit, outside hours, a feed symbol, the switch off);
  the single-use ticket under two deliveries; the age gate; the `unknown` path when
  the place call errors.
- **Proxy tests**: `test_account_surface.py` pins the exact write set; each write
  route refuses without the secret and with the switch off.
- **The page**: `tools/ui_harness.py` with a fake bus and a fake proxy (no network).
- **On prod**: preview only. **The operator places the first real order
  themselves**, at one contract, and checks it in ThinkorSwim. Claude never places
  one.

## Open questions

- Does Schwab's API take a STOP on a multi-leg order (phase 0)?
- Should the page offer the opening order alone, with no exits, as a choice?
