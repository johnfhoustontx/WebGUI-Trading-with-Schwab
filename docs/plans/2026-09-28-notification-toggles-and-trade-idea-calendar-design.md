# Notification channel toggles + trade-idea Google Calendar popup — design

2026-09-28. Operator request: (1) a Google Calendar popup when the hourly trade
idea posts; (2) Settings switches to turn Discord / Telegram on or off for each
notification category.

## The signal

The "new signal at about the bottom of the hour" is the **hourly trade idea**
(`[slots.trade_idea]` in `config/sessions.toml`, 08:35–14:35 CT, five minutes
after the :30 autoscan), sent by `options_svc/push_notify.send_trade_idea`.

## 1. Per-category channel switches

**Config:** a new tracked `config/notify.toml`, one table per category of
`shared.notify.channels.ROUTE_CATEGORIES` (ten), every switch defaulting **on**,
so the first deploy changes nothing:

```toml
[channels.trade_idea]
discord  = true
telegram = true
calendar = false   # the ONLY category with a calendar switch
```

**Enforcement at the two chokepoints.** Every sender in both services already
resolves its destination through `channels.discord_target(cfg, category)` and
`channels.telegram_target(cfg, category)`, and every sender already treats an
empty target as a no-op. A switched-off category therefore returns `""` /
`("", "")` there, and no sender changes. This covers sentiment_svc's
`market_state` alert as well as the nine options_svc categories.

**Read through `shared.config_toml.toml_loader`** (in a new `shared/notify_config.py`),
which is layered (`config/local/notify.toml` wins) and mtime-cached, so a switch
flipped in Settings is honoured on the next send with no restart. It sits BELOW
the existing gates: the master `enabled` in `shared/notifications.json` and the
dev `allow_notifications` zeroing still apply first. An unknown category or a
malformed value reads as ON — a typo must not silently mute a feed.

**Settings:** Settings → General gains a "Push notifications" grid — one row per
category in plain English, Discord / Telegram switches, plus Calendar on the
trade-idea row. It writes only the operator override
(`config/local/notify.toml`) via `config_toml.write_overrides`, and only values
that differ from the shipped file. The keys are also catalogued in
`webgui/config_schema.py` (the standing configurable-by-default rule), so they
appear in Settings → Configuration too.

## 2. Google Calendar popup for the trade idea

**When:** at the end of `send_trade_idea`, after Discord and Telegram, if
`channels.trade_idea.calendar` is on. It rides the same attempt, so it fires
exactly when a trade idea was posted (the grade gate that skips an hour skips
the event too). It fires even if Discord / Telegram are switched off for the
category — the calendar switch is independent.

**Event:** summary `Trade idea: <first caption line>`, description = the full
caption, start = now + `lead_min` (1), length `duration_min` (5), and
`reminders.useDefault = true`.

**Why useDefault, and a dedicated calendar.** An event's `reminders` field is
"for the authenticated user" — here the service account, not the operator — so
a popup override written through the API would never reach the operator's
devices. Instead the operator creates a dedicated calendar ("NeuralStrike Trade
Ideas"), sets its default notification to **at time of event**, and shares it
with the service account (*Make changes to events*). Events inherit that
calendar's default, which is the operator's.

**Client:** `shared/notify/gcal.py`, stdlib + `requests` + `cryptography` (all
already locked — **no new dependency**). It signs an RS256 JWT for the
service-account flow, exchanges it at `https://oauth2.googleapis.com/token`
(token cached until ~1 min before expiry), and `POST`s to
`https://www.googleapis.com/calendar/v3/calendars/<id>/events`. Best-effort like
every sender: no key file or no calendar id → no-op; any failure → WARNING, never
raises; short timeouts.

**Secrets / settings:**
- `[calendar] calendar_id`, `lead_min`, `duration_min` in `config/notify.toml`
  (catalogued).
- The key file: `shared/google_calendar_sa.json` (`repo_paths.GCAL_SERVICE_ACCOUNT`),
  gitignored alongside the other secrets. Put on the server by the operator.
- Dev: `allow_notifications = false` makes the calendar a no-op, like every
  other channel.

**Operator setup (once, cannot be automated):** Google Cloud project → enable
the Calendar API → service account → JSON key → copy to the server; create the
calendar, set its default notification, share it with the service account's
email, paste the calendar id into Settings.

## Testing

- target functions: off → empty, on / missing file / unknown category → unchanged.
- a local-override flip is seen without reloading (discriminating test — patch
  the file, not the accessor's return value).
- gcal: HTTP stubbed — JWT claims, token caching, event body, missing key → no
  call, HTTP 4xx → logged not raised.
- `send_trade_idea` creates an event only when the switch is on.
- Settings grid writes the right override; config_schema catalogue test passes.
- Live: the grid in the local page harness; a real event only on prod once the
  key is installed.
