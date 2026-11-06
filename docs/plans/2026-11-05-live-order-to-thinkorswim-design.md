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
- **The number of contracts is typed in by hand, from a default sized to $1,000
  of risk.** The box opens holding the most contracts whose combined max loss is
  at most `default_risk_usd` ($1,000). When one contract alone risks more than
  that, it opens at **1**. The operator can type any other whole number
  (2026-11-05: "default number of contracts will be up to $1000 risk per order.
  If the risk is greater than 1000 it will default to 1 contract").
- **Two accounts, shown by name, never by number** (2026-11-05: "Trading will be
  done in the Accounts ending with 6763 and 1191 (default) instead of displaying
  account numbers use alias 6763 will be IRA and 1191 will be Trading account"):

  | Account | Shown as | |
  |---|---|---|
  | ending 1191 | **Trading account** | the default |
  | ending 6763 | **IRA** | |

  Both passed every Phase 0 preview. The other two linked accounts (ending 2878
  and 9591) are not approved for these options and are never offered. No page,
  dialog, log line or cached view shows an account's digits: the page names the
  account by its alias alone.
- **The authenticator code is asked once a day, before the first live trade.**
  The first Send of each Central trading day asks for the code. Once it is right,
  that day is unlocked and later Sends ask only for the final confirmation. The
  unlock ends at midnight Central.
- **Paper-only is reversed on purpose, behind a switch that ships off.**

- **A typed size may risk more than $1,000; the risk then shows in the warning
  colour** (2026-11-05: "The warning color is good for now"). A hard ceiling
  catches a mistyped size: `max_contracts = 50` and `max_risk_usd = 5000` as
  shipped. An order past either is refused, whatever was typed. Both are settings,
  and the operator has not tuned them yet.

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
2. is off unless `config/live_orders.toml` `mode` allows it, read on **every**
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
   - **Contracts**: a number box the operator can type into. It opens at
     `default_contracts`: the most contracts whose max loss is at most
     `default_risk_usd`, or 1 when a single contract risks more. A typed size
     whose risk passes `default_risk_usd` shows the risk in the warning colour
     but is allowed, up to the hard ceilings;
   - **Account**: a dropdown of the configured accounts by alias only
     ("Trading account", "IRA"), opening on the one marked default;
   - worked out from the above: max risk, max profit, the target and stop prices,
     and an estimate of commissions (`config/commissions.toml`);
   - the order text in ThinkorSwim's paste format (`tos_order.order`), so the
     operator can compare it with the script they have already checked.
4. **Preview.** The service builds the Schwab order and sends it to Schwab's
   `previewOrder`, which checks it **without placing it**. The page shows Schwab's
   verdict, warnings and the effect on buying power. Changing the price, size or
   account clears the preview.
5. **Send.** It stays disabled until a preview has passed within
   `ticket_ttl_sec`.
   - **The first Send of the day** asks for the authenticator code
     (`auth.verify_totp`). A right code records the unlock server-side in the
     app's credential store (`auth_store.py`) for that Central date, never in the
     browser, so a reload or another tab on the same sign-in stays unlocked. A
     wrong code counts toward the sign-in's failure lockout (`auth.py`'s
     limiter), so the code box cannot be guessed at.
   - **Every Send** ends with a final confirmation that names the size, the
     account's alias and the limit (`kit.confirm`).
6. **After.** The page shows Schwab's order ID and the order's status (Working,
   Filled, Rejected, Canceled), read from Schwab every few seconds while the page
   is open.

## Architecture (inside the three tiers)

```
webgui /send-order (Tier 1)            options_svc (Tier 2)                 schwab-proxy
  build ticket view  ──cmd:live_order──▶ live_order_handlers                  POST /orders/preview
  Preview / Send       {preview|place}    · builds the payload (shared/)  ──▶ POST /orders/place
  reads cache:live_order:ticket:<id> ◀──  · live_orders.db (journal)          GET  /orders/{hash}/{id}
                                          · status poll
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
- `default_contracts(risk_per_contract, budget, ceiling)`: `floor(budget /
  risk_per_contract)`, never below 1 and never above `ceiling`. So $230 a contract
  opens at 4 ($920), $1,000 at 1, $1,400 at 1. A risk that cannot be worked out
  (no finite max loss) opens at 1.

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

⚠ **Measured in Phase 0: Schwab's API refuses a STOP on a multi-leg spread**
(`400 "Stop price must be populated only for stop orders."`, in every form
tried), though ThinkorSwim's own text carries one (`STP TRG-50.00% MARK`). So a
spread's order is the opening order + the profit target, and the page says in
plain words that **the stop is not on the order** and must be set in
ThinkorSwim. A single option keeps the full bracket.

### The proxy

- `trader_request` allows POST to exactly two endpoint shapes,
  `/accounts/{hash}/previewOrder` and `/accounts/{hash}/orders`. Anything else still
  raises.
- `POST /orders/preview` and `POST /orders/place`, both behind
  `require_account_secret`, both refusing with 403 unless `live_orders.toml mode`
  allows them (`preview` for the preview, `live` for the place). The proxy reads the switch itself, so turning it off stops orders even
  if a service is wrong.
- `place` returns the order ID from Schwab's `Location` header (201, empty body).
- New read-only route: `GET /orders/{hash}/{order_id}` (status). The dropdown's
  names are the operator's aliases from config, so no Schwab nickname route is
  needed.
- The service maps an alias to an account HASH (from `/accounts`) inside Tier 2.
  The page, the command and the cache carry only the alias. A hash or an account
  number never reaches Tier 1, and Schwab's answers are scrubbed before they are
  cached or stored (Phase 0 found that `previewOrder` echoes the full number).
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
mode = "off"                 # the one switch: "off", "preview" (Phase 0), "live" (Phase 1);
                             # read on every request by the proxy and the service
# [[accounts]] tables: the accounts orders may go to. NONE in this tracked file;
# the operator's config/local/live_orders.toml (gitignored) holds them:
#   [[accounts]]
#   last4 = "1191"
#   alias = "Trading account"
#   default = true
#   [[accounts]]
#   last4 = "6763"
#   alias = "IRA"
# An account not listed is refused. Exactly one may be the default; with
# none marked, the first listed opens.
default_risk_usd = 1000.0    # the Contracts box opens at the most contracts risking at most this; 1 if one risks more
max_contracts = 50           # hard ceiling: an order of more contracts is refused
max_risk_usd = 5000.0        # hard ceiling: an order risking more is refused
max_drift_pct = 10.0         # warn when the live mid has moved this far from the idea's price
totp = "daily"               # the authenticator code: "daily" (before the first Send of each
                             # Central day) or "every_send"
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
   switch on for preview alone (`mode = "preview"`). Preview a single option, a
   vertical and an iron condor, each with the bracket, on SPX, SPY and a stock.
   Record which order types Schwab accepts, the stop-on-a-spread answer above, and
   the tick rule. Write the results into this doc (see "Phase 0" below).
1. **Single options, verticals and iron condors**, which covers most ideas: the
   builder, the ticket page, preview, place, the journal and the status read. The
   in-app idea banner comes with it. Ships with `mode = "off"`.
2. **The other structures**: butterflies, condors, straddles and strangles, CUSTOM.
3. **Cancel from the page** (a third write route, `DELETE`, by the same rules), a
   list of today's live orders, and the covered call.

Each phase updates the manuals and `webgui/page_help.py` in the same commit, adds
the `cmd:live_order` row to the API Reference, and edits CLAUDE.md's
paper-only and order-route sentences in place (phase 1).

## Phase 0: what was built, and how to run it

Built 2026-11-05. **Nothing in it can place an order**: no such route exists.

- `config/live_orders.toml`: `mode = "off"` as shipped; `"preview"` lets the
  proxy ask Schwab to check an order. Anything else reads as `off`
  (`shared/live_orders_config.py`). Also `[ticks]`, the first guess at the price
  steps. Settings → Configuration → Live orders.
- `shared/live_order.py`: the pure builder. Legs in the app's canonical shape
  plus each contract's Schwab symbol (from the chain, never built), out comes
  the order JSON in three shapes: `open`, `target` (opening triggers a profit
  target) and `bracket` (opening triggers an OCO of target and stop). Exits are
  priced off the limit with `config/tos_order.toml`'s percentages.
- The proxy: `trader_preview(account_hash, order)` POSTs to exactly
  `/accounts/{hash}/previewOrder`, with the endpoint fixed in code, one attempt
  and no retry loop. `POST /orders/preview` wraps it behind
  `require_account_secret` and the switch. `trader_request` is still GET-only.
  `/health` reports `live_orders` (the mode). `test_account_surface.py` pins
  that the trader URL is built only in `trader_request` and `trader_preview`,
  that `session.post` appears only in `trader_preview`, and that the one route
  naming an order is `/orders/preview`.
- `tools/live_order_preview.py`: `accounts` lists the linked accounts by their
  last 4 digits; `matrix --account 1234` builds the four structures (long call,
  debit call vertical, credit put vertical, iron condor) on SPY, $SPX and INTC's
  nearest expiry 1 to 10 days out, in all three shapes, at 1 contract, previews
  each, and prints Schwab's verdict. `--out` saves the orders and answers (last 4
  digits only, never the hash).

**To run it** (after a promote, during market hours, with the operator's go):

1. Set Settings → Configuration → Live orders to `preview` (it is read on the
   next request; no restart).
2. On the box:
   ```bash
   cd /home/administrator/dev && set -a && . ./.env && set +a && .venv/bin/python tools/live_order_preview.py accounts
   ```
   then `matrix --account <last 4> --out /tmp/live_order_preview.json`.
3. Set it back to `off`.

**What Phase 0 must answer:**

1. Does `previewOrder` answer at all? It is in Schwab's Trader API reference,
   but nothing in this app has ever called it. If it answers 404 or 501, the
   design loses its check-before-send step, and that has to be settled before
   Phase 1.
2. Does each structure's opening order pass, with the complex type this builder
   names (`VERTICAL`, `IRON_CONDOR`, none for one leg)?
3. Does `TRIGGER` → single target pass? Does `TRIGGER` → `OCO` (target and
   `STOP`) pass, for one leg and for a spread?
4. Are the price steps right for SPY, $SPX and a non-penny stock?

**Results (run 2026-11-05, 18:45-19:05 CT, after the close; nothing placed).**
Four structures (long call, debit call vertical, credit put vertical, iron
condor) on SPY, $SPX and INTC's nearest expiry (2026-11-06), each in the three
shapes, at 1 contract, on all four linked accounts: 144 previews.

| Question | Answer |
|---|---|
| 1. Does `previewOrder` answer? | **Yes.** Every preview came back with Schwab's full `orderValidationResult` (accepts, rejects, warns). The check-before-send step stands. |
| 2. Do the opening orders pass? | **Yes**, on accounts …6763 and …1191, for all four structures on all three symbols, with the complex types this builder names (`VERTICAL`, `IRON_CONDOR`, none for one leg). |
| 3a. Opening → profit target? | **Yes**, every structure, on …6763 and …1191. |
| 3b. Opening → OCO of target and STOP? | **A single option: yes. Any spread: no.** Every vertical and iron condor bracket answered `400 "Stop price must be populated only for stop orders."` on every account. Two other forms of a spread's stop were tried on …6763: without `complexOrderStrategyType`, and as `STOP_LIMIT`. Both got the same 400. A third, the stop written as a `NET_CREDIT` limit at the stop price, was "accepted", but that is a limit order to sell BELOW the market, which fills at once: it is no stop, and must never be sent. |
| 4. Price steps | Every price the builder sent passed (SPY in pennies, $SPX and INTC in nickels under $3). |
| Accounts | **…2878 and …9591 cannot trade these options**: every order was rejected with "The account is not approved for this level of options trading" (plus buying-power and "a naked option requires $5000 in equity" rejects, even on long-only orders). **…6763 and …1191** passed everything above. |

**One thing the run found in the tool itself.** Schwab's preview answer echoes
the FULL account number (`orderStrategy.accountNumber`). The tool saved those
answers to `--out` unscrubbed, against its own docstring. The four result files
were deleted from the box straight after the run, and the tool now scrubs every
account-naming key to its last 4 digits before it prints or saves anything
(`tools/live_order_preview.scrub`, with a test). **The same answer reaches the
service and the ticket page in Phase 1, so the service must scrub it before
it is cached or stored.**

**What this changes for Phase 1:**
- **Exits by structure.** A single option sends the full bracket (target and
  stop). **A spread sends the opening order and the profit target only**, and
  the ticket says in plain words, beside the exits, that the stop is **not on
  the order** and must be set in ThinkorSwim. This is the fallback this design
  named before the run.
- **The allow-list ships empty in the tracked file.** The operator chose
  …1191 ("Trading account", the default) and …6763 ("IRA") for prod's
  `config/local/live_orders.toml`. …2878 and …9591 stay off it.
- `previewOrder` stays the gate before Send.

## How it is tested and verified

- **Unit tests**: the builder against `test_tos_order.PASTED`; the exit prices for
  debit and credit; rounding to the tick; refusals (no account, zero or too many
  contracts, risk over the limit, outside hours, a feed symbol, the switch off);
  the default size ($230 -> 4, $1,000 -> 1, $1,400 -> 1, no finite risk -> 1, the
  ceiling); the daily unlock (asked on the first Send, not on the second that
  day, asked again after midnight Central, a wrong code counted as a failure);
  the single-use ticket under two deliveries; the age gate; the `unknown` path when
  the place call errors.
- **Proxy tests**: `test_account_surface.py` pins the exact write set; each write
  route refuses without the secret and with the switch off.
- **The page**: `tools/ui_harness.py` with a fake bus and a fake proxy (no network).
- **On prod**: preview only. **The operator places the first real order
  themselves**, at one contract, and checks it in ThinkorSwim. Claude never places
  one.

## Open questions

- ~~Does Schwab's API take a STOP on a multi-leg order?~~ No (Phase 0).
- Should the page offer the opening order alone, with no exits, as a choice?
- Are the hard ceilings right (50 contracts, $5,000 of risk)? The warning colour
  past $1,000 is the operator's choice "for now", so it may be revisited.
