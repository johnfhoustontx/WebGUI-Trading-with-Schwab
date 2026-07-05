# Signal push notifications — Telegram / Discord / Google Fi SMS — design

**Date:** 2026-07-05
**Branch:** `Using_Highcharts`
**Status:** approved (design)

## Goal

Push a notification to the user's phone the moment the options service surfaces:

1. **A new scanner signal** (`cache:options:scan` — `signals_0dte` + `signals_swing`).
2. **A new captured signal** (`cache:options:captured`).

Delivery over three channels: **Telegram**, **Discord**, and **SMS via Google Fi**.

The defining requirement (chosen during brainstorming): notifications fire
**server-side, from the always-on `options_svc` process**, so the phone is pinged
24/7 regardless of whether a browser tab is open. This rules out reusing the
existing webgui 2 s alert watcher (browser-gated + would double-send across tabs).

## What already exists

`options-scanner/notifier.py` already contains **working** Telegram (`_send_telegram`,
HTML formatting) and Discord (`_send_discord`, embeds) senders plus env/file config
loading and a `config_notifications.example.py` template. The formatting logic is
proven and will be **ported** (not re-invented). It is NOT imported directly because:

- It pulls in `winsound` / `winotify` / toast machinery that is useless in a headless
  service and triggers auto-`pip install` side effects on import.
- `notifier` is a **documented cross-app module-name collision** (both
  `options-scanner/notifier.py` and `sentiment-dashboard/notifier.py` exist). A
  service-owned module avoids the `sys.path` trap entirely.

## SMS via Google Fi

Google Fi has a proprietary email-to-text gateway that remains functional in 2026
(unlike the largely-deprecated Verizon/T-Mobile `@vtext`/`@tmomail` carrier gateways):

> Email `<10-digit-Fi-number>@msg.fi.google.com` → delivered to the phone as a text.

So SMS = send an email through SMTP to that address. The sender is the user's Gmail
(`fernandesj@gmail.com`) with a **Gmail app password** over `smtp.gmail.com:587` (STARTTLS).

Sources:
- <https://support.google.com/fi/answer/6356597?hl=en>
- <https://textbolt.com/blog/email-to-sms/>

## Architecture

New self-contained module **`services/options_svc/push_notify.py`** (Tier 2,
service-owned, headless). Structure mirrors the service's existing pure-core +
thin-I/O split so the logic is unit-testable.

### Channels (all gate on config presence — missing creds → silent no-op)

| Channel  | Transport                         | Payload shape                          |
|----------|-----------------------------------|----------------------------------------|
| Telegram | `requests` POST to Bot API        | HTML message, **one per new signal**   |
| Discord  | `requests` POST to webhook        | embed, **one per new signal**          |
| SMS (Fi) | `smtplib` → `<num>@msg.fi.google.com` | plain-text **batched summary**, one text per event (capped list) |

Batched SMS (rather than one text per signal) avoids text spam and Gmail send-rate
limits when a scan surfaces several signals at once.

### Triggers (hooked at the existing publish points in `handlers.py`)

- **New scanner signals** — in `rescan(bus)`, after `cache_set(CACHE_SCAN, …)`:
  diff the current signal keys against a persisted "already-notified" set, filter by
  the min-score gate, notify the new ones, then add them to the set.
- **New captured signals** — in `refresh_captured(bus)` (and the `captured_reprice`
  republish path): diff `signal_id`s against a persisted seen-set, notify new ones.

Both hooks call a single dispatcher on `push_notify` that fans out to the configured
channels. The dispatch runs best-effort and off the critical path — a slow or failing
send never blocks the scan/publish.

### "New" detection — single-source, restart-safe

- **Stable signal key** built from the same fields `signal_db` dedups on:
  `symbol / type / short_strike / long_strike / expiration` (IC also folds in the
  call legs). Pure function, unit-tested.
- **Persisted per-trigger sets in Redis**: `cache:options:notified_scan` and
  `cache:options:notified_captured`, each scoped to the **trading date** (a
  `{date, keys[]}` envelope). On a date change the set resets, so a signal that
  persists across days does not re-spam, but each genuinely-new signal fires once.
- Because `options_svc` is one always-on process writing to Redis, there is **no
  per-browser double-send** and the seen-state **survives a service restart** (no
  re-notify storm on restart).

### Seeding on first run

On the service's first publish after (re)start, if the persisted set is empty for
today, seed it with the current keys **without** notifying (mirrors the webgui
watcher's `alerted_init` seed), so a restart mid-session doesn't blast every
already-open signal.

## Config / secrets

Gitignored **`shared/notifications.json`** (+ committed `shared/notifications.example.json`),
loaded by `push_notify`. **Env vars override file values.** Shape:

```json
{
  "enabled": true,
  "market_hours_only": true,
  "min_score": 0,
  "telegram": { "bot_token": "", "chat_id": 0 },
  "discord":  { "webhook_url": "" },
  "sms":      { "fi_number": "", "smtp_user": "", "smtp_app_password": "" }
}
```

Env overrides: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`,
`FI_SMS_NUMBER`, `SMS_SMTP_USER`, `SMS_SMTP_APP_PASSWORD`, `NOTIFY_ENABLED`.

A channel with no usable creds silently no-ops. `enabled: false` disables all.
Path added to `repo_paths.py` (e.g. `NOTIFICATIONS_CONFIG`) — never hard-coded.

## Error handling

- Every send wrapped in try/except → a single `log.warning` (ported from the legacy
  notifier), never raises into the caller.
- Timeouts on all HTTP (`requests … timeout=8`) and SMTP.
- Fan-out is defensive per-channel: one channel failing does not affect the others.

## Testing (TDD, per-service convention)

Pure unit tests:
- Formatters (Telegram HTML / Discord embed / SMS summary text) against sample dicts.
- `signal_key` stable-key builder (incl. IC).
- New-key diff + date-scoped reset logic.
- Config load + env-override precedence.

Thin I/O senders tested with mocked `requests` / `smtplib` (assert the URL/payload,
no network). A hook-level test enqueues a scan/captured publish through the handler
and asserts the dispatcher is called with the correct new keys (and NOT called on the
seed run / for already-notified signals).

## Out of scope (YAGNI)

- Per-channel runtime toggles in the webgui Settings page (channels self-gate on
  config; can be a follow-up).
- `trade_executed` / `error` notifications — only the two signal triggers requested.
