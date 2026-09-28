# Notification toggles + trade-idea Calendar popup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Per-category Discord/Telegram on/off switches in Settings, and a Google
Calendar event (popup) whenever the hourly trade idea posts.

**Architecture:** A new `config/notify.toml` (layered, mtime-cached via
`shared.config_toml.toml_loader`) read by `shared/notify/switches.py`; the two
existing routing chokepoints `channels.discord_target` / `telegram_target` return
an empty target for a switched-off category. `shared/notify/gcal.py` is a
dependency-free service-account Calendar client (`cryptography` + `requests`),
called at the end of `push_notify.send_trade_idea`. Settings → General gets a
grid that writes `config/local/notify.toml` through `config_store`.

**Tech Stack:** Python 3.11, NiceGUI, `cryptography`, `requests`, pytest.
Design: [the design doc](2026-09-28-notification-toggles-and-trade-idea-calendar-design.md).

**Test commands** (from the worktree root; the venv is the MAIN checkout's —
a worktree has none): `PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`.

---

### Task 1: `config/notify.toml` + the switch reader

**Files:**
- Create: `config/notify.toml`
- Modify: `repo_paths.py` (add `NOTIFY_TOML` beside `FLOW_ALERTS_TOML`)
- Create: `shared/notify/switches.py`
- Test: `shared/notify/tests/test_switches.py`

**Step 1: the config file** — every category from
`channels.ROUTE_CATEGORIES`, discord/telegram `true`; `trade_idea` also
`calendar = false`; a `[calendar]` table with `calendar_id = ""`, `lead_min = 1`,
`duration_min = 5`. Commented like the other config files.

**Step 2: failing tests**

```python
from shared.notify import switches as sw

def test_shipped_file_has_every_category_on():
    from shared.notify.channels import ROUTE_CATEGORIES
    for cat in ROUTE_CATEGORIES:
        assert sw.enabled(cat, "discord") and sw.enabled(cat, "telegram")
    assert sw.enabled("trade_idea", "calendar") is False

def test_off_in_file_reads_off(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text("[channels.signals]\ndiscord = false\n")
    monkeypatch.setattr(sw, "_load", sw._make_loader(f)[0])
    assert sw.enabled("signals", "discord") is False
    assert sw.enabled("signals", "telegram") is True     # sibling kept

def test_unknown_or_malformed_reads_the_default(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text('[channels.signals]\ndiscord = "no"\n')
    monkeypatch.setattr(sw, "_load", sw._make_loader(f)[0])
    assert sw.enabled("signals", "discord") is True      # non-bool → default
    assert sw.enabled("nope", "telegram") is True        # unknown category → on
    assert sw.enabled("nope", "calendar") is False       # calendar defaults off

def test_a_local_override_is_seen_without_a_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    f = tmp_path / "notify.toml"
    f.write_text("[channels.signals]\ndiscord = true\n")
    monkeypatch.setattr(sw, "_load", sw._make_loader(f)[0])
    assert sw.enabled("signals", "discord") is True
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "notify.toml").write_text("[channels.signals]\ndiscord = false\n")
    assert sw.enabled("signals", "discord") is False

def test_calendar_settings_defaults():
    c = sw.calendar_settings()
    assert c == {"calendar_id": "", "lead_min": 1, "duration_min": 5}
```

**Step 3: run** `"$PY" -m pytest shared/notify/tests/test_switches.py -q` → FAIL (no module).

**Step 4: implement** `shared/notify/switches.py`:

```python
"""Per-category channel switches (config/notify.toml) - Settings -> General.

Read at SEND time through the layered, mtime-cached loader, so a switch flipped
in the app applies on the next send with no restart. A missing or malformed
value reads as the DEFAULT (on for discord/telegram, off for calendar): a typo
must never silently mute a feed."""
from repo_paths import NOTIFY_TOML
from shared.config_toml import toml_loader

_DEFAULT_ON = ("discord", "telegram")
_CAL_DEFAULTS = {"calendar_id": "", "lead_min": 1, "duration_min": 5}

def _make_loader(path):
    return toml_loader(path, {"channels": {}, "calendar": dict(_CAL_DEFAULTS)},
                       label="notify.toml")

_load, reset = _make_loader(NOTIFY_TOML)

def enabled(category, channel) -> bool:
    default = channel in _DEFAULT_ON
    try:
        row = _load().get("channels", {}).get(category)
        v = row.get(channel) if isinstance(row, dict) else None
    except Exception:  # noqa: BLE001 - a switch read must never break a send
        return default
    return v if isinstance(v, bool) else default

def calendar_settings() -> dict:
    out = dict(_CAL_DEFAULTS)
    try:
        block = _load().get("calendar")
        if isinstance(block, dict):
            if isinstance(block.get("calendar_id"), str):
                out["calendar_id"] = block["calendar_id"].strip()
            for k in ("lead_min", "duration_min"):
                v = block.get(k)
                if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                    out[k] = v
    except Exception:  # noqa: BLE001
        pass
    return out
```

**Step 5:** tests pass. **Step 6: commit** `feat(notify): per-category channel switches in config/notify.toml`.

---

### Task 2: honour the switches at the two chokepoints

**Files:** Modify `shared/notify/channels.py` (`discord_target`, `telegram_target`);
Test `shared/notify/tests/test_channels.py` (append).

**Step 1: failing tests**

```python
def test_a_switched_off_category_has_no_discord_target(monkeypatch):
    from shared.notify import switches
    monkeypatch.setattr(switches, "enabled",
                        lambda cat, ch_: not (cat == "signals" and ch_ == "discord"))
    cfg = {"discord": {"webhook_url": "https://g"}, "telegram": {"bot_token": "t", "chat_id": 1}}
    assert ch.discord_target(cfg, "signals") == ""
    assert ch.discord_target(cfg, "flow_uoa") == "https://g"
    assert ch.telegram_target(cfg, "signals") == ("t", 1)

def test_a_switched_off_category_has_no_telegram_target(monkeypatch):
    from shared.notify import switches
    monkeypatch.setattr(switches, "enabled", lambda cat, ch_: ch_ != "telegram")
    cfg = {"telegram": {"bot_token": "t", "chat_id": 1}}
    assert ch.telegram_target(cfg, "trade_idea") == ("", "")
```

**Step 2:** run → FAIL. **Step 3:** at the top of each function:

```python
    if not _switch_on(category, "discord"):
        return ""
```
(`("", "")` for telegram), with

```python
def _switch_on(category, channel) -> bool:
    """Settings -> General's per-category switch. Never raises: a broken switch
    read leaves the feed ON, the pre-switch behaviour."""
    try:
        from shared.notify import switches
        return switches.enabled(category, channel)
    except Exception:  # noqa: BLE001
        return True
```
Lazy import so the module reference is patchable and `channels` stays importable
if `repo_paths` gains nothing. **Step 4:** full `shared/notify/tests` green.
**Step 5: commit** `feat(notify): a switched-off category resolves to no target`.

---

### Task 3: catalogue `notify.toml` for Settings → Configuration

**Files:** Modify `webgui/config_schema.py` (new `_NOTIFY`, add to `FILES`).

```python
_NOTIFY = ConfigFile(
    name="notify.toml", title="Push notifications", icon="notifications",
    summary="Which channels each push category is sent to, and the trade-idea "
            "Google Calendar event.",
    restart=(),       # read at send time - no restart
    sections=(
        Section("Channels", "One entry per category. Also on Settings → General.", (
            Field("channels.*.discord", "Discord", "", kind="bool"),
            Field("channels.*.telegram", "Telegram", "", kind="bool"),
            Field("channels.*.calendar", "Google Calendar event",
                  "Trade idea only.", kind="bool", optional=True),
        )),
        Section("Google Calendar", "Needs the service-account key on the server.", (
            Field("calendar.calendar_id", "Calendar ID",
                  "From the calendar's Settings → Integrate calendar.",
                  kind="text", blank_ok=True),
            Field("calendar.lead_min", "Starts after", "", kind="int",
                  unit="min", min=0, max=60, step=1),
            Field("calendar.duration_min", "Length", "", kind="int",
                  unit="min", min=1, max=120, step=1),
        )),
    ),
)
```
Run `"$PY" -m pytest webgui/tests/test_config_schema.py -q` (from `webgui/`) → green.
Commit `feat(settings): catalogue notify.toml`.

---

### Task 4: the Google Calendar client

**Files:** Create `shared/notify/gcal.py`; Modify `repo_paths.py`
(`GCAL_SERVICE_ACCOUNT = SHARED / "google_calendar_sa.json"`), `.gitignore`
(add `shared/google_calendar_sa.json` + the anywhere-name line);
Test `shared/notify/tests/test_gcal.py`.

**Tests** (RSA key generated in the test with `cryptography`; `requests.post`
monkeypatched on the module): JWT has three parts, RS256 header, claims
`iss`/`scope=https://www.googleapis.com/auth/calendar.events`/`aud`/`exp-iat==3600`,
and the signature verifies with the public key · token cached across two calls
· event POST goes to `.../calendars/<quoted id>/events` with `Bearer` auth and a
body of summary/description/start/end (end − start == duration) and
`reminders.useDefault is True` · no key file → no HTTP call, returns False · no
calendar id → no call · token 4xx or event 4xx → returns False and logs a
WARNING, never raises · `ENV_FLAGS["allow_notifications"] = False` → no call.

**Implementation** (public surface: `create_event(summary, description, *,
calendar_id, lead_min, duration_min, key_path=GCAL_SERVICE_ACCOUNT, now=None) -> bool`):
base64url JWT signed with `serialization.load_pem_private_key(...).sign(msg,
padding.PKCS1v15(), hashes.SHA256())`; token from `key["token_uri"]` with
`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`; module cache
`{"token", "exp", "email"}` reused until 60 s before expiry; event times
RFC 3339 in America/Chicago; timeouts 10 s; every failure `log.warning`, return
False. Commit `feat(notify): Google Calendar service-account client`.

---

### Task 5: create the event when the trade idea posts

**Files:** Modify `services/options_svc/push_notify.py` (`send_trade_idea`);
Test `services/options_svc/tests/test_trade_idea.py` (append).

**Tests:** switch on → `gcal.create_event` called once with summary = caption's
first line and description = caption, calendar settings passed through · switch
off (default) → not called · `create_event` raising → `send_trade_idea` still
returns True · text-fallback path (render failed) also creates the event · the
too-large early return does NOT.

**Implementation:** a helper `_calendar_trade_idea(caption)` guarded by
`switches.enabled("trade_idea", "calendar")`, try/except → `log.warning`, called
just before each `return True`. Commit `feat(trade-idea): Google Calendar popup`.

---

### Task 6: Settings → General "Push notifications" grid

**Files:** Modify `webgui/pages/settings.py`; Test `webgui/tests/test_settings.py`.

Pure helpers (tested):
- `CATEGORY_LABELS` — plain English for all ten categories (Trade signals,
  Unusual options activity, Premium crossover, Gamma flip, Position action
  alerts, End-of-day summary, Gamma briefing, Market snapshot, Market state
  change, Hourly trade idea). Test: keys == the shipped `[channels]` tables.
- `notify_grid_rows(shipped, overrides)` → `[(category, label, {channel: bool})]`
  in the shipped file's order; calendar only where the shipped row has it.
- `notify_toggle(shipped, overrides, category, channel, value)` → the new
  override table via `config_store.build_overrides` over the flattened effective
  values (preserves an operator's other overrides, e.g. `calendar_id`; setting a
  value back to shipped removes the key).

Render: a `_card()` after "Desktop notifications": `kit.section_title("Push
notifications")`, one-line muted explanation ("Applies on the next send; no
restart."), a header row + one row per category with `ui.switch` per channel;
`on_value_change` → `config_store.save("notify.toml", …, changes=[…])` wrapped
in `guard`, a `kit.toast("error", …)` on failure. Show a note under the grid
when the calendar switch is on but no calendar id / key is configured? — **no**
(YAGNI; the Configuration tab has the id). Commit `feat(settings): push
notification channel switches`.

Verify in the local page harness (fake bus, Windows) — screenshot the grid,
flip a switch, confirm `config/local/notify.toml` content, then delete it.

---

### Task 7: docs

- `docs/manuals/user-guide.md` Settings section + `webgui/page_help.py` Settings
  entry: the grid, and the calendar setup.
- Operator setup steps (Google Cloud project, Calendar API, service account,
  key to `shared/google_calendar_sa.json` on the server, dedicated calendar with
  "at time of event" default, share with the service-account email, paste the ID)
  into `docs/dev-prod-environments.md`'s runbook or a new
  `docs/manuals` section — wherever the notification secrets are documented.
- `docs/CHANGELOG.md` entry. `CLAUDE.md`: one line in the config section that
  `notify.toml`'s switches are enforced at `discord_target`/`telegram_target`.
- Rebuild manuals (`build_docs.py`). Commit `docs: notification switches + calendar`.

Then: full suites for `shared/notify`, `services/options_svc`,
`services/sentiment_svc`, `webgui`; compare the failing SET.
