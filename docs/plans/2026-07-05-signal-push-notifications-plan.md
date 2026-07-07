# Signal push notifications (Telegram / Discord / Google Fi SMS) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fire Telegram + Discord + Google-Fi-SMS notifications from the always-on `options_svc` the moment a new scanner signal or new captured signal is published.

**Architecture:** A new service-owned module `services/options_svc/push_notify.py` (pure formatters/key-logic + thin I/O senders) is called from the existing publish points in `services/options_svc/handlers.py` (`rescan` for scanner signals, `refresh_captured` + the `captured_reprice` path for captured signals). "New" is detected by diffing stable signal keys against a **date-scoped Redis set** so it is single-source, restart-safe, and never double-sends. Config comes from a gitignored `shared/notifications.json` with env-var overrides.

**Tech Stack:** Python 3.11, `requests` (Telegram/Discord HTTP), `smtplib` (Fi email→SMS), Redis via `shared.bus`, pytest. Reference: [design doc](2026-07-05-signal-push-notifications-design.md).

**Reference — signal dict fields** (from `options-scanner/notifier.py`): `symbol`, `type` (`PCS`/`CCS`/`IC`), `short_strike`, `long_strike`, `call_short`, `call_long`, `expiration`, `width`, `credit`, `max_loss`, `rr_pct`, `pop_pct`, `short_delta`, `net_theta`, `short_iv`, `breakeven`, `trade_type`, `composite_score`. Captured signals additionally carry `signal_id`.

**Run tests from the repo root:** `.venv\Scripts\python -m pytest services\options_svc -q`

---

### Task 1: Path constant + example config template

**Files:**
- Modify: `repo_paths.py` (near `APPSETTINGS`, line ~21)
- Create: `shared/notifications.example.json`
- Modify: `.gitignore` (add `shared/notifications.json`)

**Step 1:** Add to `repo_paths.py` after the `TOKENS` line:

```python
NOTIFICATIONS_CONFIG = SHARED / "notifications.json"
```

**Step 2:** Create `shared/notifications.example.json`:

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

**Step 3:** Append to `.gitignore` (under the other `shared/` secret entries):

```
shared/notifications.json
```

**Step 4: Commit**

```bash
git add repo_paths.py shared/notifications.example.json .gitignore
git commit -m "feat(notify): add notifications config path + example template"
```

---

### Task 2: Config loader (file + env override)

**Files:**
- Create: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
import json
from services.options_svc import push_notify as pn


def test_load_config_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({
        "enabled": True, "min_score": 20,
        "telegram": {"bot_token": "T", "chat_id": 5},
        "discord": {"webhook_url": "https://d"},
        "sms": {"fi_number": "5551234567", "smtp_user": "u@gmail.com",
                "smtp_app_password": "pw"},
    }))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = pn.load_config()
    assert cfg["min_score"] == 20
    assert cfg["telegram"]["bot_token"] == "T"
    assert cfg["sms"]["fi_number"] == "5551234567"


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "FILE", "chat_id": 1}}))
    monkeypatch.setattr(pn, "_CONFIG_PATH", p)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "ENV")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://envhook")
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == "ENV"
    assert cfg["discord"]["webhook_url"] == "https://envhook"


def test_missing_file_returns_disabled_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(pn, "_CONFIG_PATH", tmp_path / "nope.json")
    for k in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "FI_SMS_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    cfg = pn.load_config()
    assert cfg["telegram"]["bot_token"] == ""
    assert cfg["enabled"] is True  # default-on; channels self-gate on creds
```

**Step 2:** Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_push_notify.py -q` — Expected: FAIL (module missing).

**Step 3: Implement** — create `services/options_svc/push_notify.py`:

```python
"""Server-side signal push notifications (Telegram / Discord / Google Fi SMS).

Called from options_svc handlers when a new scanner or captured signal is
published. Pure formatters + key/diff logic are unit-tested; senders are thin
I/O wrappers. Every send is best-effort (never raises into the caller).

Config: shared/notifications.json (gitignored) with env-var overrides. A channel
with no usable creds silently no-ops. Built service-owned (NOT importing the
legacy options-scanner/notifier.py) to avoid its winsound/winotify baggage and
the documented `notifier` cross-app module-name collision.
"""
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText

import requests

from repo_paths import NOTIFICATIONS_CONFIG

log = logging.getLogger(__name__)
_CONFIG_PATH = NOTIFICATIONS_CONFIG

_DEFAULTS = {
    "enabled": True,
    "market_hours_only": True,
    "min_score": 0,
    "telegram": {"bot_token": "", "chat_id": 0},
    "discord": {"webhook_url": ""},
    "sms": {"fi_number": "", "smtp_user": "", "smtp_app_password": ""},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """Merged config: DEFAULTS < file < env. Never raises (bad file → defaults)."""
    cfg = _deep_merge(_DEFAULTS, {})
    try:
        raw = json.loads(_CONFIG_PATH.read_text())
        if isinstance(raw, dict):
            cfg = _deep_merge(cfg, raw)
    except Exception:
        pass
    # Env overrides (win over file).
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram"]["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        try:
            cfg["telegram"]["chat_id"] = int(os.environ["TELEGRAM_CHAT_ID"])
        except ValueError:
            pass
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        cfg["discord"]["webhook_url"] = os.environ["DISCORD_WEBHOOK_URL"]
    if os.environ.get("FI_SMS_NUMBER"):
        cfg["sms"]["fi_number"] = os.environ["FI_SMS_NUMBER"]
    if os.environ.get("SMS_SMTP_USER"):
        cfg["sms"]["smtp_user"] = os.environ["SMS_SMTP_USER"]
    if os.environ.get("SMS_SMTP_APP_PASSWORD"):
        cfg["sms"]["smtp_app_password"] = os.environ["SMS_SMTP_APP_PASSWORD"]
    if os.environ.get("NOTIFY_ENABLED"):
        cfg["enabled"] = os.environ["NOTIFY_ENABLED"].lower() not in ("0", "false", "no")
    return cfg
```

**Step 4:** Run the test file — Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/push_notify.py services/options_svc/tests/test_push_notify.py
git commit -m "feat(notify): config loader with env override"
```

---

### Task 3: Stable signal-key builder

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
def test_signal_key_stable_and_distinct():
    a = {"symbol": "SPY", "type": "PCS", "short_strike": 500, "long_strike": 495,
         "expiration": "2026-07-10"}
    b = dict(a, short_strike=499)
    assert pn.signal_key(a) == pn.signal_key(a)
    assert pn.signal_key(a) != pn.signal_key(b)


def test_signal_key_ic_folds_call_legs():
    ic1 = {"symbol": "QQQ", "type": "IC", "short_strike": 400, "long_strike": 395,
           "call_short": 420, "call_long": 425, "expiration": "2026-07-10"}
    ic2 = dict(ic1, call_short=421)
    assert pn.signal_key(ic1) != pn.signal_key(ic2)


def test_captured_key_prefers_signal_id():
    assert pn.captured_key({"signal_id": "abc", "symbol": "SPY"}) == "abc"
```

**Step 2:** Run — Expected: FAIL (`signal_key` not defined).

**Step 3: Implement** — append:

```python
def signal_key(s: dict) -> str:
    """Stable identity for a scanner signal (symbol/type/strikes/expiration).

    Mirrors the fields signal_db dedups on. IC folds in the call legs so a
    different call wing is a distinct signal.
    """
    parts = [str(s.get("symbol", "")), str(s.get("type", "")),
             str(s.get("short_strike", "")), str(s.get("long_strike", "")),
             str(s.get("expiration", ""))]
    if str(s.get("type", "")).upper() == "IC":
        parts += [str(s.get("call_short", "")), str(s.get("call_long", ""))]
    return "|".join(parts)


def captured_key(s: dict) -> str:
    """Identity for a captured signal — its signal_id when present, else signal_key."""
    sid = s.get("signal_id")
    return str(sid) if sid not in (None, "") else signal_key(s)
```

**Step 4:** Run — Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): stable signal-key builders"
```

---

### Task 4: Formatters (Telegram HTML / Discord embed / SMS summary)

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
def _sig():
    return {"symbol": "SPY", "type": "PCS", "trade_type": "0DTE",
            "short_strike": 500, "long_strike": 495, "width": 5,
            "expiration": "2026-07-10", "credit": 1.20, "max_loss": 3.80,
            "rr_pct": 31.6, "pop_pct": 72, "short_delta": -0.18,
            "net_theta": 0.05, "short_iv": 14.2, "breakeven": 498.8,
            "composite_score": 80}


def test_telegram_text_has_symbol_and_credit():
    t = pn.telegram_signal_text(_sig())
    assert "SPY" in t and "PCS" in t and "1.20" in t


def test_discord_embed_has_fields():
    e = pn.discord_signal_embed(_sig())
    assert e["title"].startswith("SPY PCS")
    names = {f["name"] for f in e["fields"]}
    assert "Credit" in names and "R:R" in names


def test_sms_summary_batches_and_caps():
    sigs = [dict(_sig(), symbol=f"S{i}") for i in range(8)]
    txt = pn.sms_summary_text(sigs, kind="scanner", cap=5)
    assert txt.startswith("8 new scanner")
    assert txt.count("\n") <= 6  # header + <=5 lines
    assert "S0" in txt
```

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement** — append (ported from `options-scanner/notifier.py`):

```python
import html as _html
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Chicago")
_MULT = 100
_D_GREEN, _D_YELLOW, _D_GRAY = 0x2ECC71, 0xF1C40F, 0x95A5A6


def _strikes_str(s: dict) -> str:
    if str(s.get("type", "")).upper() == "IC":
        return (f"{s.get('short_strike')}/{s.get('long_strike')}p — "
                f"{s.get('call_short', '')}/{s.get('call_long', '')}c")
    return f"{s.get('short_strike')}/{s.get('long_strike')} ({s.get('width', '')}-wide)"


def telegram_signal_text(s: dict) -> str:
    e = lambda v: _html.escape(str(v))
    rr = s.get("rr_pct", 0) or 0
    emoji = "🟢" if rr >= 25 else ("🟡" if rr >= 15 else "⚪")
    return (
        f"{emoji} <b>{e(s.get('symbol'))} {e(s.get('type'))}</b> ({e(s.get('trade_type', ''))})\n"
        f"Exp <code>{e(s.get('expiration'))}</code> • {e(_strikes_str(s))}\n"
        f"Credit <b>${(s.get('credit') or 0):.2f}</b> "
        f"(${(s.get('credit') or 0) * _MULT:,.0f}/ct) • Max loss ${(s.get('max_loss') or 0):.2f}\n"
        f"R:R <b>{rr:.1f}%</b> • PoP {s.get('pop_pct', 0):.0f}% • Δ {s.get('short_delta', 0):.3f}"
    )


def discord_signal_embed(s: dict) -> dict:
    rr = s.get("rr_pct", 0) or 0
    color = _D_GREEN if rr >= 25 else (_D_YELLOW if rr >= 15 else _D_GRAY)
    return {
        "title": f"{s.get('symbol')} {s.get('type')} ({s.get('trade_type', '')})",
        "description": f"Exp {s.get('expiration')} • Strikes {_strikes_str(s)}",
        "color": color,
        "fields": [
            {"name": "Credit", "value": f"${(s.get('credit') or 0):.2f}", "inline": True},
            {"name": "Max Loss", "value": f"${(s.get('max_loss') or 0):.2f}", "inline": True},
            {"name": "R:R", "value": f"{rr:.1f}%", "inline": True},
            {"name": "PoP", "value": f"{s.get('pop_pct', 0):.0f}%", "inline": True},
            {"name": "Δ short", "value": f"{s.get('short_delta', 0):.3f}", "inline": True},
            {"name": "Score", "value": f"{s.get('composite_score', 0):.0f}", "inline": True},
        ],
        "timestamp": datetime.now(_TZ).isoformat(),
    }


def sms_summary_text(sigs: list, kind: str, cap: int = 5) -> str:
    n = len(sigs)
    head = f"{n} new {kind} signal" + ("" if n == 1 else "s")
    lines = [head]
    for s in sigs[:cap]:
        lines.append(f"{s.get('symbol')} {s.get('type')} "
                     f"{_strikes_str(s)} Cr ${ (s.get('credit') or 0):.2f} "
                     f"R:R {(s.get('rr_pct') or 0):.0f}%")
    if n > cap:
        lines.append(f"…+{n - cap} more")
    return "\n".join(lines)
```

**Step 4:** Run — Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): telegram/discord/sms formatters"
```

---

### Task 5: Senders (thin I/O, mocked)

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
def test_send_telegram_posts_to_bot_api(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_telegram("TOK", 42, "hello")
    assert calls["url"].endswith("/botTOK/sendMessage")
    assert calls["json"]["chat_id"] == 42 and calls["json"]["parse_mode"] == "HTML"


def test_send_discord_posts_embed(monkeypatch):
    calls = {}
    monkeypatch.setattr(pn.requests, "post",
                        lambda url, **kw: calls.update(url=url, json=kw.get("json")))
    pn.send_discord("https://hook", {"title": "x"})
    assert calls["url"] == "https://hook"
    assert calls["json"]["embeds"] == [{"title": "x"}]


def test_send_sms_emails_fi_gateway(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None): sent["host"] = host
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["to"] = msg["To"]; sent["body"] = msg.get_payload()

    monkeypatch.setattr(pn.smtplib, "SMTP", FakeSMTP)
    pn.send_sms("5551234567", "u@gmail.com", "pw", "2 new signals")
    assert sent["to"] == "5551234567@msg.fi.google.com"
    assert sent["login"] == ("u@gmail.com", "pw")
    assert "2 new signals" in sent["body"]


def test_senders_never_raise(monkeypatch):
    def boom(*a, **k): raise RuntimeError("net")
    monkeypatch.setattr(pn.requests, "post", boom)
    pn.send_telegram("T", 1, "x")   # no exception
    pn.send_discord("https://h", {})
```

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement** — append:

```python
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_SMTP_HOST, _SMTP_PORT = "smtp.gmail.com", 587
_FI_GATEWAY = "@msg.fi.google.com"


def send_telegram(token: str, chat_id, text: str) -> None:
    if not token or not chat_id:
        return
    try:
        requests.post(_TELEGRAM_API.format(token=token), json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=8)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Telegram send failed: %s", exc)


def send_discord(webhook_url: str, embed: dict) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=8)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord send failed: %s", exc)


def send_sms(fi_number: str, smtp_user: str, smtp_pw: str, body: str,
             subject: str = "") -> None:
    if not (fi_number and smtp_user and smtp_pw):
        return
    try:
        msg = MIMEText(body)
        msg["From"] = smtp_user
        msg["To"] = f"{fi_number}{_FI_GATEWAY}"
        msg["Subject"] = subject
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pw)
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("Fi SMS send failed: %s", exc)
```

**Step 4:** Run — Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): telegram/discord/sms senders (best-effort)"
```

---

### Task 6: Date-scoped new-key diff + Redis-backed seen-set

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test** (pure diff, plus a fakeredis-backed set)

```python
def test_new_keys_diff_and_reset():
    prev = {"date": "2026-07-05", "keys": ["a", "b"]}
    # same day: only c is new
    new, nxt = pn.new_keys(["a", "b", "c"], prev, today="2026-07-05")
    assert new == ["c"] and set(nxt["keys"]) == {"a", "b", "c"}
    # new day: set resets, everything is new
    new2, nxt2 = pn.new_keys(["a"], nxt, today="2026-07-06")
    assert new2 == ["a"] and nxt2 == {"date": "2026-07-06", "keys": ["a"]}


def test_new_keys_preserves_order_and_dedups():
    new, nxt = pn.new_keys(["x", "x", "y"], None, today="2026-07-05")
    assert new == ["x", "y"]


def test_seen_roundtrip_via_bus():
    from shared.bus import Bus
    bus = Bus(namespace="test-notify")
    pn.save_seen(bus, "cache:options:notified_scan", {"date": "2026-07-05", "keys": ["k"]})
    got = pn.load_seen(bus, "cache:options:notified_scan")
    assert got["keys"] == ["k"]
```

> Check the existing tests (`services/options_svc/tests/test_handlers.py`) for how they
> construct a test `Bus`/fakeredis; match that fixture. `load_seen` returns `None` when
> the key is absent.

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement** — append:

```python
def new_keys(current: list, prev: dict | None, today: str):
    """Return (new_keys_in_order, next_state).

    `prev` is {"date", "keys"} or None. On a date change the seen-set resets
    (a persisting signal doesn't re-spam, but each new day's signals fire once).
    Order-preserving + deduped.
    """
    seen = set(prev["keys"]) if (prev and prev.get("date") == today) else set()
    out, ordered_seen = [], list(prev["keys"]) if (prev and prev.get("date") == today) else []
    for k in current:
        if k not in seen:
            seen.add(k)
            out.append(k)
            ordered_seen.append(k)
    # dedup `out` while preserving order (current may repeat)
    out = list(dict.fromkeys(out))
    return out, {"date": today, "keys": list(dict.fromkeys(ordered_seen))}


def load_seen(bus, key: str):
    env = bus.cache_get(key)
    return env.payload if (env is not None and isinstance(env.payload, dict)) else None


def save_seen(bus, key: str, state: dict) -> None:
    bus.cache_set(key, state)
```

**Step 4:** Run — Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): date-scoped new-key diff + redis seen-set"
```

---

### Task 7: Dispatcher (fan-out + gates + seed)

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
def test_notify_signals_sends_per_channel_and_updates_seen(monkeypatch):
    from shared.bus import Bus
    bus = Bus(namespace="test-notify-dispatch")
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": "https://h"},
           "sms": {"fi_number": "555", "smtp_user": "u", "smtp_app_password": "p"}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg, dc, sms = [], [], []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    monkeypatch.setattr(pn, "send_discord", lambda *a: dc.append(a))
    monkeypatch.setattr(pn, "send_sms", lambda *a, **k: sms.append(a))

    sigs = [_sig(), dict(_sig(), symbol="QQQ")]
    pn.notify_signals(bus, sigs, kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(tg) == 2 and len(dc) == 2   # one per signal
    assert len(sms) == 1                    # one batched summary
    # second call with same signals: nothing new
    pn.notify_signals(bus, sigs, kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert len(tg) == 2


def test_min_score_gate_scanner(monkeypatch):
    from shared.bus import Bus
    bus = Bus(namespace="test-notify-score")
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 90,
           "telegram": {"bot_token": "T", "chat_id": 1},
           "discord": {"webhook_url": ""}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",   # score 80 < 90
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []


def test_seed_run_does_not_send(monkeypatch):
    from shared.bus import Bus
    bus = Bus(namespace="test-notify-seed")
    cfg = {"enabled": True, "market_hours_only": False, "min_score": 0,
           "telegram": {"bot_token": "T", "chat_id": 1}, "discord": {}, "sms": {}}
    monkeypatch.setattr(pn, "load_config", lambda: cfg)
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05", seed=True)
    assert tg == []  # seeded silently
    # now a real run with the same signal also stays silent (already seen)
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []


def test_disabled_config_no_send(monkeypatch):
    from shared.bus import Bus
    bus = Bus(namespace="test-notify-off")
    monkeypatch.setattr(pn, "load_config",
                        lambda: {"enabled": False, "telegram": {"bot_token": "T", "chat_id": 1}})
    tg = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a: tg.append(a))
    pn.notify_signals(bus, [_sig()], kind="scanner",
                      seen_key="cache:options:notified_scan", today="2026-07-05")
    assert tg == []
```

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement** — append. `key_fn` selects `signal_key` (scanner) vs `captured_key` (captured):

```python
import datetime as _dt


def _today_ct() -> str:
    return _dt.datetime.now(_TZ).date().isoformat()


def notify_signals(bus, signals: list, *, kind: str, seen_key: str,
                   today: str | None = None, seed: bool = False,
                   config: dict | None = None) -> list:
    """Diff `signals` against the date-scoped seen-set and notify the new ones.

    kind: "scanner" | "captured". Returns the list of newly-notified keys.
    seed=True updates the seen-set WITHOUT sending (first run after restart).
    Never raises — a send failure is swallowed per-channel.
    """
    cfg = config or load_config()
    today = today or _today_ct()
    key_fn = captured_key if kind == "captured" else signal_key

    prev = load_seen(bus, seen_key)
    keys = [key_fn(s) for s in signals]
    new, nxt = new_keys(keys, prev, today)
    save_seen(bus, seen_key, nxt)

    if seed or not cfg.get("enabled", True) or not new:
        return new if seed else []

    if cfg.get("market_hours_only") and not _in_market_hours():
        return []

    min_score = cfg.get("min_score", 0) or 0
    new_set = set(new)
    fresh = [s for s, k in zip(signals, keys) if k in new_set]
    if kind == "scanner" and min_score:
        fresh = [s for s in fresh if (s.get("composite_score") or 0) >= min_score]
    if not fresh:
        return []

    tg = cfg.get("telegram", {})
    dc = cfg.get("discord", {})
    sms = cfg.get("sms", {})
    for s in fresh:
        send_telegram(tg.get("bot_token"), tg.get("chat_id"), telegram_signal_text(s))
        send_discord(dc.get("webhook_url"), discord_signal_embed(s))
    send_sms(sms.get("fi_number"), sms.get("smtp_user"), sms.get("smtp_app_password"),
             sms_summary_text(fresh, kind), subject=f"{len(fresh)} new {kind} signal(s)")
    return [signal_key(s) if kind != "captured" else captured_key(s) for s in fresh]
```

Also add the market-hours gate (reuse the service's holiday list to stay consistent — import lazily to avoid a cycle):

```python
def _in_market_hours() -> bool:
    """Trading-day 08:00–15:00 CT. Reuses the alerts module's calendar so the
    gate agrees with the rest of the stack. Defensive → True on any import error
    (fail-open: better a rare off-hours notify than silently dropping all)."""
    try:
        import sys, pathlib
        webgui = pathlib.Path(__file__).resolve().parents[2] / "webgui"
        if str(webgui) not in sys.path:
            sys.path.insert(0, str(webgui))
        from alerts import in_market_hours  # noqa: WPS433
        return in_market_hours(_dt.datetime.now(_dt.timezone.utc))
    except Exception:
        return True
```

> If importing `webgui/alerts.py` from the service proves awkward (sys.path / the
> `pages.options.scanner` import it pulls in), instead inline a small local
> weekday + 08:00–15:00 CT check and a copy of the holiday frozenset. Prefer the
> lightest thing that passes `test_in_market_hours`. Add a test:
> ```python
> def test_market_hours_gate_is_bool():
>     assert isinstance(pn._in_market_hours(), bool)
> ```

**Step 4:** Run — Expected: PASS.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): dispatcher with gates + seed + per-channel fan-out"
```

---

### Task 8: Wire scanner trigger into `handlers.rescan`

**Files:**
- Modify: `services/options_svc/handlers.py` (`rescan`, ~line 230; add cache-key constant near line 111)
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing test** (mirror the existing `rescan` handler test; assert the dispatcher is invoked with the published signals)

```python
def test_rescan_calls_notify(monkeypatch, <bus fixture>):
    monkeypatch.setattr(compute, "run_scan", lambda: {
        "signals_0dte": [{"symbol": "SPY", "type": "PCS", "short_strike": 500,
                          "long_strike": 495, "expiration": "2026-07-10",
                          "composite_score": 80}],
        "signals_swing": [], "errors": [], "warnings": []})
    seen = {}
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: seen.update(n=len(sigs), key=kw["seen_key"]))
    handlers.rescan(bus)
    assert seen["n"] == 1
    assert seen["key"] == handlers.CACHE_NOTIFIED_SCAN
```

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement**

Add near the other cache-key constants (line ~111):

```python
CACHE_NOTIFIED_SCAN = "cache:options:notified_scan"
CACHE_NOTIFIED_CAPTURED = "cache:options:notified_captured"
```

Add the import at top (~line 19):

```python
from services.options_svc import push_notify
```

In `rescan`, after `bus.publish(EVENT_SCAN, …)`:

```python
    # Server-side phone push on genuinely-new signals (Telegram/Discord/Fi-SMS).
    # Best-effort; must never break the scan/publish path. First run after start
    # seeds silently so a restart mid-session doesn't blast every open signal.
    try:
        all_sigs = (scan.signals_0dte or []) + (scan.signals_swing or [])
        seed = push_notify.load_seen(bus, CACHE_NOTIFIED_SCAN) is None
        push_notify.notify_signals(bus, all_sigs, kind="scanner",
                                   seen_key=CACHE_NOTIFIED_SCAN, seed=seed)
    except Exception:  # noqa: BLE001
        log.exception("scanner push-notify failed (non-fatal)")
```

> Confirm `scan.signals_0dte` is a list of dicts on the `ScanResult` model
> (`scan.model_dump()` is cached at line 240 — the model holds the same lists).
> If the model stores typed objects, use the dicts from `result` instead:
> `(result.get("signals_0dte") or []) + (result.get("signals_swing") or [])`.

**Step 4:** Run — Expected: PASS. Also run the whole handlers test file to catch regressions.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): fire phone push on new scanner signals"
```

---

### Task 9: Wire captured trigger into `refresh_captured` + `captured_reprice`

**Files:**
- Modify: `services/options_svc/handlers.py` (`refresh_captured` ~line 419; the `captured_reprice` branch ~line 768)
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing test**

```python
def test_refresh_captured_calls_notify(monkeypatch, <bus fixture>):
    monkeypatch.setattr(compute, "captured_view", lambda: {"signals": [
        {"signal_id": "s1", "symbol": "SPY", "type": "PCS"}]})
    got = {}
    monkeypatch.setattr(handlers.push_notify, "notify_signals",
                        lambda bus, sigs, **kw: got.update(n=len(sigs), kind=kw["kind"],
                                                           key=kw["seen_key"]))
    handlers.refresh_captured(bus)
    assert got["n"] == 1 and got["kind"] == "captured"
    assert got["key"] == handlers.CACHE_NOTIFIED_CAPTURED
```

**Step 2:** Run — Expected: FAIL.

**Step 3: Implement** — add a private helper and call it from BOTH publish sites so the logic is DRY:

```python
def _notify_captured(bus, signals) -> None:
    try:
        seed = push_notify.load_seen(bus, CACHE_NOTIFIED_CAPTURED) is None
        push_notify.notify_signals(bus, signals or [], kind="captured",
                                   seen_key=CACHE_NOTIFIED_CAPTURED, seed=seed)
    except Exception:  # noqa: BLE001
        log.exception("captured push-notify failed (non-fatal)")
```

In `refresh_captured`, after the publish:

```python
    _notify_captured(bus, (data or {}).get("signals"))
```

In the `captured_reprice` branch (after `bus.publish(EVENT_CAPTURED, …)` at ~line 773):

```python
        _notify_captured(bus, res.get("signals"))
```

> Note: `remove_closed_from_captured` (manual close) must NOT notify — closing a
> trade is not a new signal, and the seen-set already holds those ids, so even if
> it did call through nothing new would fire. Leave it unwired.

**Step 4:** Run — Expected: PASS. Run the full handlers file.

**Step 5: Commit**

```bash
git add -A && git commit -m "feat(notify): fire phone push on new captured signals"
```

---

### Task 10: Create the real (gitignored) config + docs

**Files:**
- Create: `shared/notifications.json` (gitignored — real creds; leave blank if not yet obtained)
- Modify: `services/options_svc/CLAUDE.md` and/or root `CLAUDE.md` (brief note under the options service)

**Step 1:** Copy the example to the real file (blanks are fine — channels self-gate):

```bash
cp shared/notifications.example.json shared/notifications.json
```

Fill in when creds are ready:
- **Telegram:** create a bot via `@BotFather` → `bot_token`; message the bot, then `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat_id`.
- **Discord:** channel → Integrations → Webhooks → New Webhook → copy URL.
- **SMS (Google Fi):** `fi_number` = your 10-digit Fi number; `smtp_user` = `fernandesj@gmail.com`; `smtp_app_password` = a Gmail **App Password** (Google Account → Security → 2-Step Verification → App passwords). Messages arrive from `<fi_number>@msg.fi.google.com`.

Verify `shared/notifications.json` is gitignored:

```bash
git check-ignore shared/notifications.json   # expect it to print the path
```

**Step 2:** Add a short note to `services/options_svc/CLAUDE.md` (or root CLAUDE.md) documenting: server-side push on new scanner/captured signals via `push_notify.py`, config in `shared/notifications.json`, three channels, date-scoped Redis seen-sets, best-effort/never-fatal, restart-seeds-silently.

**Step 3: Commit** (docs only — the real json is ignored):

```bash
git add services/options_svc/CLAUDE.md CLAUDE.md
git commit -m "docs(notify): document server-side signal push notifications"
```

---

### Task 11: Full suite + manual live check

**Step 1:** Run the whole service suite:

```bash
.venv\Scripts\python -m pytest services\options_svc -q
```

Expected: all green (no regressions vs the ~334 baseline + the new push_notify tests).

**Step 2 (optional live check):** With `shared/notifications.json` filled and Memurai + `options_svc` running, delete the seen-set so the next scan re-notifies, then trigger a rescan:

```python
from shared.bus import Bus
b = Bus()
b.cache_set("cache:options:notified_scan", {"date": "1970-01-01", "keys": []})
b.enqueue_command("cmd:options", {"type": "rescan"})
```

Confirm a Telegram message + Discord embed + one Fi text arrive (whichever channels are configured). If a channel is silent, check `services/options_svc/logs/options.log` for the best-effort warning.

**Step 3: Commit** any doc tweaks from what you learned.

---

## Notes for the executor
- **DRY:** the scanner + captured paths share `push_notify.notify_signals`; the two handler hooks differ only by `kind`/`seen_key`.
- **YAGNI:** no Settings-page toggles, no trade-executed/error notifications this pass.
- **Never fatal:** both handler hooks are try/except-wrapped — a notify failure must never stop a scan from publishing.
- **Restart-safe:** the date-scoped Redis seen-set + silent seed prevent a re-notify storm on service restart.
- Match the existing `test_handlers.py` bus/fakeredis fixture rather than inventing one.
