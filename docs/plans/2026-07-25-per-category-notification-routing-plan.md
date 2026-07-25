# Per-category notification routing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every Discord webhook and Telegram chat target configurable **per
notification category**, so a channel can be moved by editing config — no code change.

**Architecture:** One `routes` block in `shared/notifications.json` keyed by category.
Two PURE resolvers in `shared/notify/channels.py` (`discord_target` /
`telegram_target`) with precedence **route → legacy key → global**, so existing
configs keep working untouched. All 9 call sites across `options_svc` and
`sentiment_svc` are rewired to ask the resolver.

**Tech Stack:** Python, pytest. No new deps.

**Design doc:** `docs/plans/2026-07-25-per-category-notification-routing-design.md`

---

## Reference: the 9 categories and their call sites (all verified)

| Category | Call site | Discord today | Telegram today |
|---|---|---|---|
| `signals` | `push_notify.notify_signals` (~L842-843) | `dc.get("webhook_url")` | `tg` global |
| `action_alert` | `push_notify.send_action_digest` (~L345-347) | `dc.get("webhook_url")` | `tg` global |
| `flow_uoa` / `flow_crossover` / `flow_gamma_flip` | `push_notify.send_flow_alert` (~L392-405) via `flow_webhook(dc, a)` (~L378-390) | per-type legacy keys | `tg` global |
| `eod_summary` | `push_notify.send_eod_summary` (~L505-506) | `dc.get("webhook_url")` | `tg` global |
| `gamma_briefing` | `push_notify.send_gamma_briefing` (~L613, 621-633) | `gamma_briefing.webhook_url` → global | `tg` global |
| `market_snapshot` | `push_notify.send_market_snapshot` (~L674-688) via `_ms_webhook(dc)` (~L640-644) | `discord.market_snapshot_webhook_url` → global | `tg` global |
| `market_state` | `sentiment_svc/state_alert.send_state_transition` (~L115-124) | `dc.get("webhook_url")` | `tg` global |

Telegram send sites in `push_notify.py` (all use `tg.get("bot_token"), tg.get("chat_id")`):
L345, L404, L505, L621, L632, L680, L687, L842.

**Legacy keys that MUST keep working** (step 2 of precedence):
`discord.flow_uoa_webhook_url`, `discord.flow_crossover_webhook_url`,
`discord.flow_gamma_flip_webhook_url`, `discord.market_snapshot_webhook_url`,
`gamma_briefing.webhook_url`.

---

### Task 1: The pure resolvers

**Files:**
- Modify: `shared/notify/channels.py`
- Test: `shared/notify/tests/test_channels.py` (find the existing test file; if the
  tests live elsewhere, e.g. `shared/notify/tests/`, add there and match its style)

**Step 1: Write the failing tests**

```python
def test_discord_target_prefers_route_over_legacy_and_global():
    cfg = {"routes": {"flow_uoa": {"discord": "ROUTE"}},
           "discord": {"flow_uoa_webhook_url": "LEGACY", "webhook_url": "GLOBAL"}}
    assert channels.discord_target(cfg, "flow_uoa") == "ROUTE"

def test_discord_target_falls_back_to_legacy_key():
    cfg = {"discord": {"flow_uoa_webhook_url": "LEGACY", "webhook_url": "GLOBAL"}}
    assert channels.discord_target(cfg, "flow_uoa") == "LEGACY"

def test_discord_target_falls_back_to_global():
    cfg = {"discord": {"webhook_url": "GLOBAL"}}
    assert channels.discord_target(cfg, "signals") == "GLOBAL"
    assert channels.discord_target(cfg, "eod_summary") == "GLOBAL"

def test_discord_target_treats_blank_as_unset():
    cfg = {"routes": {"signals": {"discord": ""}}, "discord": {"webhook_url": "GLOBAL"}}
    assert channels.discord_target(cfg, "signals") == "GLOBAL"

def test_discord_target_gamma_briefing_legacy_lives_in_its_own_block():
    cfg = {"gamma_briefing": {"webhook_url": "GB"}, "discord": {"webhook_url": "GLOBAL"}}
    assert channels.discord_target(cfg, "gamma_briefing") == "GB"

def test_discord_target_unknown_category_uses_global():
    assert channels.discord_target({"discord": {"webhook_url": "G"}}, "nope") == "G"

def test_discord_target_missing_everything_is_empty_string():
    assert channels.discord_target({}, "signals") == ""

def test_telegram_target_prefers_route_chat_id():
    cfg = {"routes": {"eod_summary": {"telegram_chat_id": 42}},
           "telegram": {"bot_token": "BOT", "chat_id": 7}}
    assert channels.telegram_target(cfg, "eod_summary") == ("BOT", 42)

def test_telegram_target_falls_back_to_global_chat():
    cfg = {"telegram": {"bot_token": "BOT", "chat_id": 7}}
    assert channels.telegram_target(cfg, "signals") == ("BOT", 7)

def test_telegram_target_treats_zero_and_blank_as_unset():
    cfg = {"routes": {"signals": {"telegram_chat_id": 0}},
           "telegram": {"bot_token": "BOT", "chat_id": 7}}
    assert channels.telegram_target(cfg, "signals") == ("BOT", 7)
    cfg["routes"]["signals"]["telegram_chat_id"] = ""
    assert channels.telegram_target(cfg, "signals") == ("BOT", 7)

def test_telegram_target_bot_token_is_always_global():
    cfg = {"routes": {"signals": {"telegram_chat_id": 42}}, "telegram": {"bot_token": "BOT", "chat_id": 7}}
    assert channels.telegram_target(cfg, "signals")[0] == "BOT"
```

**Step 2: Run** → FAIL (functions not defined).
`.venv\Scripts\python -m pytest shared/notify -q`

**Step 3: Implement** in `shared/notify/channels.py`:

```python
# ── per-category routing ─────────────────────────────────────────────────────
# Every notification category can target its OWN Discord webhook + Telegram chat
# via the `routes` config block, so moving a feed to another channel is a config
# edit, not a code change. Resolution is route -> legacy key -> global, and the
# LEGACY step is what keeps existing installs working untouched.
ROUTE_CATEGORIES = (
    "signals", "flow_uoa", "flow_crossover", "flow_gamma_flip", "action_alert",
    "eod_summary", "gamma_briefing", "market_snapshot", "market_state",
)

# Category -> the pre-`routes` config key it used to read (back-compat only).
# `gamma_briefing` is the odd one out: its legacy key lives in its OWN block.
_LEGACY_DISCORD_KEYS = {
    "flow_uoa": ("discord", "flow_uoa_webhook_url"),
    "flow_crossover": ("discord", "flow_crossover_webhook_url"),
    "flow_gamma_flip": ("discord", "flow_gamma_flip_webhook_url"),
    "market_snapshot": ("discord", "market_snapshot_webhook_url"),
    "gamma_briefing": ("gamma_briefing", "webhook_url"),
}


def _first_set(*vals):
    """First value that is not None / "" / 0 (all three mean "not configured")."""
    for v in vals:
        if v not in (None, "", 0):
            return v
    return None


def _route(cfg, category) -> dict:
    routes = (cfg or {}).get("routes") or {}
    r = routes.get(category)
    return r if isinstance(r, dict) else {}


def discord_target(cfg, category) -> str:
    """Discord webhook for `category`: route -> legacy key -> global. "" if none."""
    cfg = cfg or {}
    legacy = None
    block, key = _LEGACY_DISCORD_KEYS.get(category, (None, None))
    if block:
        legacy = ((cfg.get(block) or {}) if isinstance(cfg.get(block), dict) else {}).get(key)
    return _first_set(
        _route(cfg, category).get("discord"),
        legacy,
        (cfg.get("discord") or {}).get("webhook_url"),
    ) or ""


def telegram_target(cfg, category) -> tuple:
    """(bot_token, chat_id) for `category`. The bot token is always the global one;
    only the chat can be overridden per category."""
    cfg = cfg or {}
    tg = cfg.get("telegram") or {}
    chat = _first_set(_route(cfg, category).get("telegram_chat_id"), tg.get("chat_id"))
    return tg.get("bot_token", ""), (chat if chat is not None else "")
```

Also add `"routes": {}` to `_DEFAULTS` (an empty dict — `_deep_merge` will fold the
file's block in; do NOT pre-populate all 9 categories in the defaults, or a blank
placeholder could never be distinguished from absent).

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): per-category route resolvers`.

---

### Task 2: Rewire the simple options_svc call sites

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

Covers `signals`, `action_alert`, `eod_summary` (the three that are hardcoded to the
global today).

**Step 1: Write failing tests** — one per category, asserting a category-specific
route overrides the global, for BOTH channels:

```python
def test_notify_signals_uses_signals_route(monkeypatch):
    got = {}
    monkeypatch.setattr(pn, "send_discord", lambda wh, e: got.setdefault("d", wh))
    monkeypatch.setattr(pn, "send_telegram", lambda tok, cid, t: got.setdefault("t", cid))
    # ... build cfg with routes.signals.discord="SIG_D"/telegram_chat_id=99,
    #     discord.webhook_url="GLOBAL", telegram.chat_id=1, and one new signal
    # assert got["d"] == "SIG_D" and got["t"] == 99

def test_send_action_digest_uses_action_alert_route(monkeypatch): ...
def test_send_eod_summary_uses_eod_summary_route(monkeypatch): ...
```
Mirror the existing tests in the file for how each function is invoked (e.g.
`send_action_digest` takes the items+slot; `send_eod_summary` takes the summary dict;
`notify_signals` needs a bus + seen-key — copy an existing test's setup).

**Step 2: Run** → FAIL (still sends to the global).

**Step 3: Implement.** Import the resolvers at the top of `push_notify.py` alongside
the existing `shared.notify.channels` imports:
```python
from shared.notify.channels import discord_target, telegram_target
```
Then at each site replace the manual lookups:
```python
# before: send_telegram(tg.get("bot_token"), tg.get("chat_id"), ...)
#         send_discord(dc.get("webhook_url"), ...)
tok, chat = telegram_target(cfg, "action_alert")
send_telegram(tok, chat, ...)
send_discord(discord_target(cfg, "action_alert"), ...)
```
Categories: `notify_signals` → `"signals"` (regardless of `kind`); `send_action_digest`
→ `"action_alert"`; `send_eod_summary` → `"eod_summary"`.

**Step 4: Run** the whole `test_push_notify.py` → new tests PASS, all existing PASS
(back-compat proof). **Step 5: Commit.**

---

### Task 3: Rewire flow alerts, gamma briefing, market snapshot

**Files:** same two as Task 2.

These three already have per-category overrides via `flow_webhook()` / `_ms_webhook()`
/ the `gamma_briefing.webhook_url` read — the job is to route them through the shared
resolver (so `routes` wins, legacy still works) **and** give them per-category Telegram.

**Step 1: Failing tests**
```python
def test_flow_alert_uses_per_type_route(monkeypatch):
    # routes.flow_uoa.discord="UOA_D"/telegram_chat_id=55 beats the legacy key + global
    # and a crossover alert with no route falls back to its LEGACY key
def test_flow_alert_legacy_key_still_works(monkeypatch):   # back-compat
def test_gamma_briefing_uses_route_then_legacy(monkeypatch):
def test_market_snapshot_uses_route_then_legacy(monkeypatch):
```

**Step 2: Run** → FAIL.

**Step 3: Implement.**
- `flow_webhook(dc, a)`: keep the function (tests reference it) but reimplement as a
  thin category mapper over the resolver. NOTE its signature takes `dc` (the discord
  sub-dict) — change it to take the FULL `cfg` (`flow_webhook(cfg, a)`) and update its
  caller + any test, OR add a new `_flow_category(a) -> str` helper and call
  `discord_target(cfg, _flow_category(a))` directly at the call site and delete
  `flow_webhook`. **Prefer the latter (delete it)** — check for other callers first
  with `grep -rn "flow_webhook" .`; if only `send_flow_alert` + its tests use it,
  delete it and update the tests.
- Same for `_ms_webhook(dc)` → replace with `discord_target(cfg, "market_snapshot")`.
- `send_gamma_briefing`: replace
  `webhook = gb.get("webhook_url") or (cfg.get("discord", {}) or {}).get("webhook_url")`
  with `webhook = discord_target(cfg, "gamma_briefing")` (the resolver already reads
  the `gamma_briefing.webhook_url` legacy key).
- All Telegram sends in these three functions use
  `tok, chat = telegram_target(cfg, <category>)`.
- Flow category mapping: `{"uoa": "flow_uoa", "crossover": "flow_crossover",
  "gamma_flip": "flow_gamma_flip"}` by `a.get("type")`; an unknown type → `"signals"`?
  **No** — an unknown type must fall back to the GLOBAL webhook, which
  `discord_target(cfg, <unknown>)` already does. Pass the raw mapped value (or the
  unmapped type) straight through.

**Step 4: Run** whole file → all PASS. **Step 5: Commit.**

---

### Task 4: Rewire sentiment_svc market_state

**Files:**
- Modify: `services/sentiment_svc/state_alert.py`
- Test: the existing state_alert test file (find it under `services/sentiment_svc/tests/`)

**Step 1: Failing test**
```python
def test_state_transition_uses_market_state_route(monkeypatch):
    # routes.market_state.discord="MS_D"/telegram_chat_id=77 must beat
    # discord.webhook_url="GLOBAL"/telegram.chat_id=1
```

**Step 2: Run** → FAIL.

**Step 3: Implement.** In `send_state_transition` (~L115-124) import the resolvers
from `shared.notify.channels` (the module already imports `send_telegram`/
`send_discord` from there) and replace:
```python
tok, chat = telegram_target(cfg, "market_state")
_safe(send_telegram, tok, chat, transition_telegram(...))
_safe(send_discord, discord_target(cfg, "market_state"), transition_discord(...))
```
Leave the SMS send exactly as-is (single number, out of scope).

**Step 4: Run** the sentiment_svc suite → PASS. **Step 5: Commit.**

---

### Task 5: Config templates

**Files:**
- Modify: `shared/notifications.example.json` (committed template — placeholders ONLY)
- Modify: `shared/notifications.json` (gitignored real config — add the block, leave
  every existing value untouched)

Add the `routes` block from the design doc with **blank placeholders**
(`"discord": ""`, `"telegram_chat_id": 0`) for all 9 categories, so the operator can
see every routable category and fill in only what they need. Do NOT move any existing
real webhook value into it — the legacy keys must keep proving back-compat.

Only `shared/notifications.example.json` gets committed (`shared/notifications.json`
is gitignored — edit it but do not attempt to add it to git).

**Commit** the example template.

---

### Task 6: Full verification

**Step 1:** `.venv\Scripts\python -m pytest shared/notify -q` → green.
**Step 2:** `.venv\Scripts\python -m pytest services/options_svc/tests/test_push_notify.py -q` → green.
**Step 3:** `.venv\Scripts\python -m pytest services/sentiment_svc -q` → green.
**Step 4:** `.venv\Scripts\ruff check shared/notify/channels.py services/options_svc/push_notify.py services/sentiment_svc/state_alert.py` → clean.
**Step 5:** Commit any fixups.

---

### Task 7: Live smoke check (manual, controller runs this)

Prove routing end-to-end WITHOUT spamming channels:
1. In a throwaway Python snippet, load the real config
   (`from services.options_svc.push_notify import load_config`) and print
   `discord_target(cfg, c)` + `telegram_target(cfg, c)` for all 9 categories.
2. Confirm: with no `routes` values filled in, the flow/market-snapshot/gamma
   categories still resolve to their existing dedicated webhooks (legacy honored) and
   everything else resolves to the global — i.e. **behavior is identical to today**.
3. Then set ONE route value in the real config, re-run, confirm only that category moved.

## Update CLAUDE.md

Add a "Last updated" entry: per-category routing, the `routes` block, the
route→legacy→global precedence, the 9 categories, back-compat guarantee, and
**restart `options_svc` + `sentiment_svc`** to pick up config changes.
