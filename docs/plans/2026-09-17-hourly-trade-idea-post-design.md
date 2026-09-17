# Hourly trade idea post — design

**Date:** 2026-09-17 · **Status:** built

## Ask

For social media: every hour of the regular session, find one proposed options
trade and post a tasteful, branded PNG showing the symbol, the legs (direction,
strike, expiration), grade, risk, profit, probability of profit and a small chart.
Post it to the Discord Options Signals channel and to Telegram.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Where do trades come from? | `cache:options:scan`, the live Market Scanner | Every row is already graded; nothing new scores a trade, and there is no Schwab or Claude call. |
| When? | `[slots.trade_idea]`, 08:35 … 14:35 CT, grace 10 min | Five minutes after the :30 autoscan, so the post reads a finished scan. Seven posts a day. |
| Which trade? | Strong/Good; unposted symbol first, then grade, then a different structure from the last post, then score | A feed that alternates into weaker trades to look varied is the wrong trade, so grade outranks variety. A symbol posted twice in a day is the stronger complaint, so it outranks grade. |
| Refused | unbounded loss · open through a report · unknown PoP · multi-expiry or share leg · scan > 45 min old · same-day expiry | Each is a post that is wrong, unactionable, or undrawable as a single expiry payoff. |
| Discord channel | the global webhook (operator: it is Options Signals); `routes.trade_idea` overrides | New route category, resolving route → global like every other. |
| Footer | `neuralstrike.co`, no disclaimer | Operator choice; `trade_idea.footer` in `notifications.json`. |
| Nothing eligible | skip the hour, record why in `cache:options:trade_idea` | A skipped hour beats a marginal post, and the reason must be readable. |

## The units trap

Credit rows (PCS/CCS/IC) carry money **per share** and strikes in flat fields
(`call_short`/`call_long` only on an IC). Directional rows carry money **per
contract** and a `legs` list. The card therefore takes no dollar figure from a
row: `trade_idea.economics` computes max profit, max loss and breakevens exactly
from the legs and the net entry cash (commission included), and the chart draws
the same `payoff` function. A units slip would show up as a curve disagreeing
with its own numbers rather than as a plausible wrong figure.

## Measured before shipping

Replaying prod's 15:00 CT scan of 2026-09-16 through the selector: without a DTE
floor, six of seven picks were same-day long options at $15–$33. With
`min_dte = 1` the day was seven 2–9 DTE long options (MU, CVX, QQQ, WMT, RGTI,
DELL, PLTR). The directional tab grades higher than the credit tabs, so **expect a
mostly long-premium feed** — that is the scanner's ranking, not this selector's.

## The card

1200×675 layout drawn natively at 2x (2400×1350). Brand lockup redrawn from
`neuralstrike-mark.svg`'s geometry and the `[brand]` hexes (literals; Tier 2 does
not read the web GUI theme). Inter from `deploy/site/assets`, weights by variation
name, card_kit's system fonts as the fallback. Never raises; a render failure posts
the text caption instead.

## Where the cards are kept

Each posted card and caption is written to `options-scanner/data/trade_ideas/<day>/`
(`repo_paths.TRADE_IDEAS_DIR`) as `trade-idea-<day>-<HHMM>-<SYMBOL>.png` + `.txt`.
The workstation's "NeuralStrike social pull" task copies them into
`D:\NeuralStrike Reports\Trade Ideas\<day>\` every 15 minutes, looking back five days.
A failed save never blocks the post.

## Not done

- No X/Twitter post; the existing X channel is separate and still off.
- A long put's "max profit" is the stock-to-zero figure (true, and large).
