# Gamma briefing → HTML push to Telegram + Discord (design)

**Date:** 2026-07-23
**Status:** approved, ready to plan
**Scope:** ship each scheduled Gamma Analyze briefing to the phone as a self-contained
HTML file attachment.

## Problem

The 4×/day Gamma Analyze briefing (premarket / open / midday / close) renders as a rich
infographic at `/options/analyze?slot=…`, but that route is localhost-only. Away from the
desk the briefing is unreachable — the one time its "into the close" read is most useful.

## Key constraint

**Neither Telegram nor Discord renders arbitrary HTML in a message.** Telegram's
`parse_mode:"HTML"` accepts a ~10-tag subset (b/i/u/s/a/code/pre/blockquote — no `div`, no
CSS); Discord accepts embeds only. The briefing's layout — banner, bias meter, per-index
cards, SVG price ladder, metric tiles — cannot survive either message body.

Both APIs **do** accept multipart file uploads, and `compute._analyze_doc` already emits a
**fully self-contained** document: inline `<style>`, system font stack, inline SVG, no
external scripts/links/CDN, ~30–60 KB. It is already a shippable HTML unit. Only the
delivery envelope is missing.

## Approach

Push the doc as a **file attachment**. One tap on the phone opens it in the browser at full
fidelity, offline, permanently. Zero new dependencies; the HTML generation path is untouched.

Rejected alternatives:

- **Headless PNG render** (Playwright/wkhtmltoimage) — renders inline in the feed with no
  tap, but adds a headless-browser dependency to an always-on service and ~1–3 s per
  briefing. Not worth it for a once-per-slot artifact.
- **Link only** — cheapest, but `/options/analyze` is localhost-bound; it would need the
  webgui exposed over LAN or a tunnel.
- **Summary text only** — loses the ladder/tiles that are the point of the infographic.

## Components

### 1. Two primitives — `shared/notify/channels.py`

Beside the existing `send_telegram` / `send_discord`, same contract throughout: no-op on
missing creds, `try/except → log.warning`, never raise, bounded timeout.

| Function | Call |
|---|---|
| `send_telegram_document(token, chat_id, filename, content, caption="")` | `POST /bot{token}/sendDocument`, `files={"document": (filename, content, "text/html")}`, `data={chat_id, caption, parse_mode:"HTML"}` |
| `send_discord_file(webhook_url, filename, content, caption="")` | `POST {webhook_url}`, `files={"files[0]": (filename, content, "text/html")}`, `data={"payload_json": json.dumps({"content": caption})}` |

A size guard logs and skips above the Discord 8 MB / Telegram 50 MB ceilings rather than
letting a 413 pass silently. Not reachable in practice (~1000× headroom), but a silent
failure here would be invisible.

### 2. Formatter + sender — `services/options_svc/push_notify.py`

Mirrors the established `send_eod_summary` shape.

- `briefing_caption(res, slot)` — PURE. One scannable line from `res["analysis"]`:
  slot label · regime · bias · headline. Telegram caps captions at **1024 chars**, so it is
  budget-defended (truncate the headline, never the leading identifiers) exactly as
  `twitter_signal_text` defends its 280.
- `briefing_filename(res, now)` — `gamma-briefing-YYYY-MM-DD-{slot}.html`.
- `send_gamma_briefing(res, *, slot, config=None) -> bool` — applies the gates, encodes
  `res["html"]` to UTF-8 bytes once, fans out to both channels. **No SMS** — a file cannot
  ride SMS and a bare text line there is noise.

### 3. One hook — `handlers.run_scheduled_gamma_analyze`

Appended after the existing `cache_set` / `_persist_briefing` / index-publish calls
(publish-first, notify-second — the house pattern from `rescan` and `refresh_captured`),
wrapped so a push failure can never break generation, caching, or history:

```python
try:
    push_notify.send_gamma_briefing(res, slot=slot)
except Exception:
    log.exception("gamma briefing push degraded")
```

Ad-hoc Analyze-button runs deliberately do **not** push — at the desk with the tab already
open, a phone push is noise.

## Gates

Three independent gates, matching the house convention:

1. **Master** `enabled` in `shared/notifications.json` — kills all notifications.
2. **Feature block** `gamma_briefing.enabled` — kills just this feed. The `slots` list
   subsets which of the four push, so thinning the cadence later needs no code change.
3. **Content gate** — push only when `res["analysis"]` is present, the same test
   `_persist_briefing` uses. A degraded run (no chains / no API key / API error / no tool
   reply) still produces readable HTML but carries no `analysis`; pushing "no chains
   available" four times a day is precisely the spam to avoid.

No market-hours gate — `scheduler.analyze_slot_due` already fires only on trading days.

## Config

Additive block in `shared/notifications.json` (gitignored) and
`shared/notifications.example.json` (committed, **empty placeholder** — the real webhook
URL is a credential and never enters git):

```json
"gamma_briefing": {
  "enabled": true,
  "slots": ["premarket", "open", "midday", "close"],
  "webhook_url": ""
}
```

Briefings post to their **own dedicated Discord webhook** (`gamma_briefing.webhook_url`),
following the existing `discord.flow_*_webhook_url` precedent, so the briefing feed stays
separate from the signal feed. Falls back to `discord.webhook_url` when unset. Telegram
reuses the existing bot token / chat id. A missing block defaults to enabled-all, matching
how `enabled` defaults `True` elsewhere.

## Failure modes

| Condition | Behavior |
|---|---|
| Missing creds | primitive no-ops (existing behavior) |
| Network error / 5xx | caught in the primitive, `log.warning`, returns |
| Discord 429 rate limit | best-effort, logged, not retried |
| Oversize document | size guard logs and skips |
| Degraded briefing (no `analysis`) | content gate skips, nothing sent |
| `send_gamma_briefing` raises | handler try/except — generation, cache, and history all still complete |

## Testing

TDD per layer.

- `shared/notify/tests` — the two primitives: correct URL and multipart shape (mocked
  `requests.post`), no-op on missing creds, exceptions swallowed.
- `services/options_svc/tests/test_push_notify.py` — `briefing_caption` (all fields present,
  fields missing, over-length truncation preserving the leading identifiers),
  `briefing_filename`, and the `send_gamma_briefing` gating matrix (master off / block off /
  slot excluded / no `analysis` → no send; happy path → both channels called with the
  correct bytes and filename).
- `services/options_svc/tests/test_handlers.py` — the load-bearing tests: the hook fires
  after `cache_set` on a successful run; it does **not** fire on a degraded run; and a
  raising `send_gamma_briefing` leaves the handler's caching and persistence intact.

Then live-verify: trigger one slot manually, confirm the file lands in both channels and
opens correctly on the phone.

**Restart `options_svc`** to pick it up.
