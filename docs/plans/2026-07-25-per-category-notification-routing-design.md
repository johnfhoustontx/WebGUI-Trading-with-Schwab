# Per-category notification routing (Discord + Telegram) — design

**Date:** 2026-07-25
**Branch:** `Using_Highcharts`
**Status:** approved, pending implementation

## Problem

Notification targets are half-hardwired and inconsistent, so moving a feed to a
different channel needs a **code change** for most categories.

| Category | Discord today | Telegram today |
|---|---|---|
| Option signals (scanner + captured) | `discord.webhook_url` **hardcoded** | shared chat |
| Flow UOA / crossover / gamma-flip | per-type override ✅ | shared chat |
| Action-alert digest | `discord.webhook_url` **hardcoded** | shared chat |
| EOD summary | `discord.webhook_url` **hardcoded** | shared chat |
| Gamma briefing | `gamma_briefing.webhook_url` (a DIFFERENT block) | shared chat |
| Market snapshot | `discord.market_snapshot_webhook_url` | shared chat |
| Market state (sentiment_svc) | `discord.webhook_url` **hardcoded** | shared chat |

Three problems: four categories cannot be moved without editing code; the override
keys exist in three different shapes; and **Telegram has no per-category routing at
all** — every message goes to one chat.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Config shape | **One `routes` block keyed by category** — both channels handled uniformly in one self-documenting place. |
| Telegram | **Per-category `chat_id`, shared bot token.** Falls back to the global `telegram.chat_id`. |
| Granularity | **Finest grain everywhere EXCEPT scanner/captured, which merge into one `signals` category** → 9 categories. |
| `market_state` | **In scope** (the `sentiment_svc` regime-flip alert). |

## Config shape

In the gitignored `shared/notifications.json` (+ placeholders in the committed
`shared/notifications.example.json`):

```json
"routes": {
  "signals":         { "discord": "", "telegram_chat_id": 0 },
  "flow_uoa":        { "discord": "", "telegram_chat_id": 0 },
  "flow_crossover":  { "discord": "", "telegram_chat_id": 0 },
  "flow_gamma_flip": { "discord": "", "telegram_chat_id": 0 },
  "action_alert":    { "discord": "", "telegram_chat_id": 0 },
  "eod_summary":     { "discord": "", "telegram_chat_id": 0 },
  "gamma_briefing":  { "discord": "", "telegram_chat_id": 0 },
  "market_snapshot": { "discord": "", "telegram_chat_id": 0 },
  "market_state":    { "discord": "", "telegram_chat_id": 0 }
}
```

**Blank / missing / `0` = inherit the global.** Only the categories you actually want
to split out need a value. Moving a channel is a one-line config edit + a service
restart — no code change, which is the whole point of this work.

## Shared resolver

Two PURE functions in `shared/notify/channels.py` so `options_svc` and
`sentiment_svc` resolve targets identically (state_alert is hardcoded today):

```python
def discord_target(cfg, category) -> str
def telegram_target(cfg, category) -> tuple[str, str | int]   # (bot_token, chat_id)
```

Resolution order — **first non-empty wins**:
1. `routes.<category>.discord` / `.telegram_chat_id`
2. the **legacy key** for that category — `discord.flow_uoa_webhook_url`,
   `discord.flow_crossover_webhook_url`, `discord.flow_gamma_flip_webhook_url`,
   `discord.market_snapshot_webhook_url`, `gamma_briefing.webhook_url`
3. the global `discord.webhook_url` / `telegram.chat_id`

Step 2 is the **back-compat guarantee**: every existing install keeps working with
its current config untouched. The `routes` block is purely additive; nothing breaks
on upgrade. Empty string / `0` / `None` all count as "unset" so a blank placeholder
in the example config never shadows the global.

The bot token stays global (per the brainstorm decision) — `telegram_target` returns
it alongside the chat so call sites have one thing to unpack.

## Call sites rewired (9 categories, 2 services)

`services/options_svc/push_notify.py`:
- `notify_signals` → `signals` (both `kind="scanner"` and `kind="captured"`)
- `send_action_digest` → `action_alert`
- `send_flow_alert` → `flow_uoa` / `flow_crossover` / `flow_gamma_flip` by alert type
- `send_eod_summary` → `eod_summary`
- `send_gamma_briefing` → `gamma_briefing`
- `send_market_snapshot` → `market_snapshot`

`services/sentiment_svc/state_alert.py`:
- `send_state_transition` → `market_state`

Every **Discord and Telegram** send goes through the resolver. The ad-hoc
`flow_webhook()` and `_ms_webhook()` helpers collapse into it (keep thin wrappers
only if existing tests depend on them).

## Testing

- PURE resolver unit tests: precedence (route > legacy > global), empty-string/`0`
  treated as unset, unknown category → global, each legacy key honored.
- Per-call-site tests asserting the correct category is requested and that a
  category-specific target actually overrides the global.
- **All existing push tests must stay green** — that is the back-compat proof.

## Out of scope (YAGNI)

- **Per-route env vars** (`NOTIFY_ROUTE_*`) — the config file is already gitignored;
  9 categories × 2 channels = 18 new env names for little gain. Existing env
  overrides (`DISCORD_WEBHOOK_URL`, `TELEGRAM_CHAT_ID`, …) are unchanged and keep
  acting on the globals.
- **SMS routing** — a single number by nature.
- **Per-category bot tokens** — explicitly rejected in brainstorming.
- A Settings-page UI for routing — config-file gated, like every other push setting.
