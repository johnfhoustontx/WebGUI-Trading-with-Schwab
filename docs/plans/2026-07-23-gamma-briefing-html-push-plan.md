# Gamma Briefing HTML Push Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Push each scheduled Gamma Analyze briefing to Telegram and Discord as a self-contained HTML file attachment.

**Architecture:** Two new multipart-upload primitives in the shared channel layer (`shared/notify/channels.py`), a pure caption/filename formatter plus a gated fan-out sender in `services/options_svc/push_notify.py`, and one guarded hook at the tail of `handlers.run_scheduled_gamma_analyze`. The briefing HTML itself is untouched — `compute._analyze_doc` already emits a dependency-free document.

**Tech Stack:** Python 3.11, `requests` (multipart), pytest, Redis/Memurai (unchanged), Telegram Bot API `sendDocument`, Discord webhook multipart.

**Design doc:** [2026-07-23-gamma-briefing-html-push-design.md](2026-07-23-gamma-briefing-html-push-design.md)

**One deviation from the design doc:** the caption is sent as **plain text with no `parse_mode`**. The design table showed `parse_mode:"HTML"`, but that would require HTML-escaping every caption field and buys nothing — the caption is a single unstyled line. Dropping it removes an entire escaping bug class.

**Test commands** (run from the repo root, one folder at a time — never `pytest services`, which collides top-level module names):

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

```bash
.venv/Scripts/python -m pytest services/options_svc -q
```

---

### Task 1: Config block defaults

**Files:**
- Modify: `shared/notify/channels.py` (the `_DEFAULTS` dict, ~line 31-53)
- Modify: `shared/notifications.example.json`
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Write the failing test**

Append to `shared/notify/tests/test_channels.py`:

```python
def test_defaults_include_gamma_briefing_block(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"telegram": {"bot_token": "T", "chat_id": 1}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    cfg = ch.load_config()
    gb = cfg["gamma_briefing"]
    assert gb["enabled"] is True
    assert gb["slots"] == ["premarket", "open", "midday", "close"]
    assert gb["webhook_url"] == ""


def test_gamma_briefing_file_values_override_defaults(tmp_path, monkeypatch):
    p = tmp_path / "notifications.json"
    p.write_text(json.dumps({"gamma_briefing": {"enabled": False,
                                                "webhook_url": "https://hook"}}))
    monkeypatch.setattr(ch, "_CONFIG_PATH", p)
    cfg = ch.load_config()
    assert cfg["gamma_briefing"]["enabled"] is False
    assert cfg["gamma_briefing"]["webhook_url"] == "https://hook"
    # unspecified key still falls back to the default (deep merge, not replace)
    assert cfg["gamma_briefing"]["slots"] == ["premarket", "open", "midday", "close"]
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest shared/notify -q -k gamma_briefing
```

Expected: FAIL with `KeyError: 'gamma_briefing'`.

**Step 3: Write minimal implementation**

In `shared/notify/channels.py`, add to `_DEFAULTS` immediately after the `"sms"` entry:

```python
    # Gamma Analyze briefing → HTML file attachment (options_svc, scheduled slots
    # only). `slots` subsets which of the four push, so thinning the cadence needs
    # no code change. `webhook_url` is a DEDICATED Discord webhook (falls back to
    # discord.webhook_url when blank) so briefings stay out of the signal feed.
    "gamma_briefing": {
        "enabled": True,
        "slots": ["premarket", "open", "midday", "close"],
        "webhook_url": "",
    },
```

In `shared/notifications.example.json`, add after the `"sms"` line (note the trailing comma on the line above):

```json
  "gamma_briefing": { "enabled": true, "slots": ["premarket", "open", "midday", "close"], "webhook_url": "" },
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

Expected: PASS, all pre-existing tests still green.

**Step 5: Commit**

```bash
git add shared/notify/channels.py shared/notifications.example.json shared/notify/tests/test_channels.py
git commit -m "feat(notify): gamma_briefing config block defaults"
```

---

### Task 2: `send_telegram_document` primitive

**Files:**
- Modify: `shared/notify/channels.py` (after `send_telegram`, ~line 126)
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Write the failing test**

```python
def test_send_telegram_document_posts_multipart(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append((url, kw)))
    ch.send_telegram_document("TOK", 42, "b.html", b"<html>hi</html>", "cap")
    url, kw = calls[0]
    assert url == "https://api.telegram.org/botTOK/sendDocument"
    assert kw["data"] == {"chat_id": 42, "caption": "cap"}
    assert kw["files"]["document"] == ("b.html", b"<html>hi</html>", "text/html")
    # plain-text caption: no parse_mode, so nothing needs HTML-escaping
    assert "parse_mode" not in kw["data"]


def test_send_telegram_document_noop_without_creds(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_telegram_document("", 42, "b.html", b"x")
    ch.send_telegram_document("TOK", None, "b.html", b"x")
    assert calls == []


def test_send_telegram_document_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_telegram_document("TOK", 42, "b.html", b"x")   # must not raise


def test_send_telegram_document_truncates_caption(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append(kw))
    ch.send_telegram_document("TOK", 42, "b.html", b"x", "z" * 2000)
    assert len(calls[0]["data"]["caption"]) == 1024
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest shared/notify -q -k telegram_document
```

Expected: FAIL with `AttributeError: module ... has no attribute 'send_telegram_document'`.

**Step 3: Write minimal implementation**

In `shared/notify/channels.py`, add the constant beside `_TELEGRAM_API` (~line 111):

```python
_TELEGRAM_DOC_API = "https://api.telegram.org/bot{token}/sendDocument"
_TG_CAPTION_MAX = 1024      # Telegram sendDocument caption ceiling
_DISCORD_CONTENT_MAX = 2000  # Discord webhook message content ceiling
```

Then add after `send_telegram`:

```python
def send_telegram_document(token: str, chat_id, filename: str, content: bytes,
                           caption: str = "") -> None:
    """Upload a file to Telegram via sendDocument (multipart).

    Used for artifacts a chat message cannot express — Telegram's HTML parse mode
    accepts only a ~10-tag subset, so a full infographic must travel as a file the
    recipient opens in a browser. Caption is PLAIN text (no parse_mode), so no
    field needs HTML-escaping. Best-effort, like every sender here: no creds → no-op,
    any failure → warn, never raises."""
    if not token or not chat_id:
        return
    try:
        requests.post(
            _TELEGRAM_DOC_API.format(token=token),
            data={"chat_id": chat_id, "caption": (caption or "")[:_TG_CAPTION_MAX]},
            files={"document": (filename, content, "text/html")},
            timeout=20,   # longer than the 8s text timeout — this is an upload
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("Telegram document send failed: %s", exc)
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add shared/notify/channels.py shared/notify/tests/test_channels.py
git commit -m "feat(notify): send_telegram_document multipart primitive"
```

---

### Task 3: `send_discord_file` primitive

**Files:**
- Modify: `shared/notify/channels.py` (after `send_discord`)
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Write the failing test**

```python
def test_send_discord_file_posts_multipart(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post",
                        lambda url, **kw: calls.append((url, kw)))
    ch.send_discord_file("https://hook", "b.html", b"<html>hi</html>", "cap")
    url, kw = calls[0]
    assert url == "https://hook"
    assert json.loads(kw["data"]["payload_json"]) == {"content": "cap"}
    assert kw["files"]["files[0]"] == ("b.html", b"<html>hi</html>", "text/html")


def test_send_discord_file_noop_without_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr(ch.requests, "post", lambda *a, **k: calls.append(a))
    ch.send_discord_file("", "b.html", b"x")
    assert calls == []


def test_send_discord_file_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("429")
    monkeypatch.setattr(ch.requests, "post", boom)
    ch.send_discord_file("https://hook", "b.html", b"x")   # must not raise
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest shared/notify -q -k discord_file
```

Expected: FAIL with `AttributeError: ... has no attribute 'send_discord_file'`.

**Step 3: Write minimal implementation**

Add after `send_discord` in `shared/notify/channels.py` (`json` is already imported at module top):

```python
def send_discord_file(webhook_url: str, filename: str, content: bytes,
                      caption: str = "") -> None:
    """Upload a file to a Discord webhook (multipart).

    Discord accepts embeds, not HTML, so a rich document travels as an attachment
    with an optional plain-text caption. Best-effort: no webhook → no-op, any
    failure (incl. a 429 rate limit) → warn, never raises."""
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            data={"payload_json": json.dumps(
                {"content": (caption or "")[:_DISCORD_CONTENT_MAX]})},
            files={"files[0]": (filename, content, "text/html")},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord file send failed: %s", exc)
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add shared/notify/channels.py shared/notify/tests/test_channels.py
git commit -m "feat(notify): send_discord_file multipart primitive"
```

---

### Task 4: Re-export the primitives

**Files:**
- Modify: `shared/notify/__init__.py`
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Write the failing test**

```python
def test_package_reexports_file_senders():
    import shared.notify as sn
    assert sn.send_telegram_document is ch.send_telegram_document
    assert sn.send_discord_file is ch.send_discord_file
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest shared/notify -q -k reexports
```

Expected: FAIL with `AttributeError: module 'shared.notify' has no attribute 'send_telegram_document'`.

**Step 3: Write minimal implementation**

In `shared/notify/__init__.py`, add `send_telegram_document,` and `send_discord_file,` to both the `from .channels import (...)` block and `__all__`.

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add shared/notify/__init__.py shared/notify/tests/test_channels.py
git commit -m "feat(notify): re-export file senders from package"
```

---

### Task 5: `briefing_caption` pure formatter

**Files:**
- Modify: `services/options_svc/push_notify.py` (new section near the EOD helpers)
- Test: `services/options_svc/tests/test_push_notify.py`

The caption is budget-defended the same way `twitter_signal_text` defends its 280 chars: the **leading identifiers always survive**, only the headline truncates.

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_push_notify.py`:

```python
def test_briefing_caption_full():
    res = {"analysis": {"regime": "Positive gamma · pinned",
                        "bias": 18.0,
                        "headline": "SPX pinned between 6350 and 6400 into the close."}}
    cap = pn.briefing_caption(res, "midday")
    assert cap.startswith("Gamma · Midday · Positive gamma · pinned · Bias +18")
    assert "SPX pinned between 6350 and 6400" in cap


def test_briefing_caption_missing_fields():
    assert pn.briefing_caption({"analysis": {}}, "open") == "Gamma · After open"
    assert pn.briefing_caption({}, "close") == "Gamma · At close"
    assert pn.briefing_caption({"analysis": {}}, "") == "Gamma · Briefing"


def test_briefing_caption_negative_bias_signed():
    res = {"analysis": {"bias": -42}}
    assert "Bias -42" in pn.briefing_caption(res, "premarket")


def test_briefing_caption_non_numeric_bias_dropped():
    res = {"analysis": {"bias": "very bearish"}}
    assert "Bias" not in pn.briefing_caption(res, "premarket")


def test_briefing_caption_truncates_headline_not_lead():
    res = {"analysis": {"regime": "Neg gamma", "bias": 5, "headline": "z" * 4000}}
    cap = pn.briefing_caption(res, "close")
    assert len(cap) <= 1024
    assert cap.startswith("Gamma · At close · Neg gamma · Bias +5")
    assert cap.endswith("…")
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q -k briefing_caption
```

Expected: FAIL with `AttributeError: ... has no attribute 'briefing_caption'`.

**Step 3: Write minimal implementation**

Add to `services/options_svc/push_notify.py`:

```python
# ---------------------------------------------------------------- gamma briefing
# The 4x/day Gamma Analyze briefing ships as a self-contained HTML file (see
# shared.notify.send_telegram_document / send_discord_file). Labels are duplicated
# from handlers.ANALYZE_SLOT_TITLES deliberately — push_notify must not import
# handlers (handlers imports THIS module; the reverse would be circular).
_BRIEFING_SLOT_LABELS = {
    "premarket": "Premarket",
    "open": "After open",
    "midday": "Midday",
    "close": "At close",
}
_CAPTION_MAX = 1024   # Telegram's sendDocument ceiling; Discord's 2000 is looser


def briefing_caption(res: dict, slot: str = "") -> str:
    """One scannable plain-text line to ride along with the attached briefing.

    Budget-defended: the leading identifiers (slot / regime / bias) ALWAYS survive
    and only the headline truncates, so a runaway model headline can never push the
    slot label out of the caption."""
    a = (res or {}).get("analysis") or {}
    bits = [f"Gamma · {_BRIEFING_SLOT_LABELS.get(slot, slot or 'Briefing')}"]
    regime = (a.get("regime") or "").strip()
    if regime:
        bits.append(regime)
    bias = a.get("bias")
    if isinstance(bias, (int, float)) and not isinstance(bias, bool):
        bits.append(f"Bias {bias:+.0f}")
    lead = " · ".join(bits)
    headline = (a.get("headline") or "").strip()
    room = _CAPTION_MAX - len(lead) - 1          # -1 for the newline
    if not headline or room <= 0:
        return lead[:_CAPTION_MAX]
    if len(headline) > room:
        headline = headline[:max(0, room - 1)].rstrip() + "…"
    return f"{lead}\n{headline}"
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/push_notify.py services/options_svc/tests/test_push_notify.py
git commit -m "feat(options): briefing_caption formatter"
```

---

### Task 6: `briefing_filename`

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
def test_briefing_filename_uses_generated_at_date():
    res = {"generated_at": "2026-07-23T11:30:04-05:00"}
    assert pn.briefing_filename(res, "midday") == "gamma-briefing-2026-07-23-midday.html"


def test_briefing_filename_falls_back_to_now():
    import datetime as dt
    now = dt.datetime(2026, 7, 23, 9, 0)
    assert pn.briefing_filename({}, "open", now=now) == "gamma-briefing-2026-07-23-open.html"


def test_briefing_filename_sanitizes_slot():
    res = {"generated_at": "2026-07-23T09:00:00"}
    assert pn.briefing_filename(res, "adhoc 18:42") == "gamma-briefing-2026-07-23-adhoc-18-42.html"
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q -k briefing_filename
```

Expected: FAIL with `AttributeError`.

**Step 3: Write minimal implementation**

```python
def briefing_filename(res: dict, slot: str = "", now=None) -> str:
    """Stable, sortable attachment name: gamma-briefing-YYYY-MM-DD-{slot}.html.

    Prefers the run's own ``generated_at`` so the filename matches the briefing's
    session even if the push is retried later. Slot is sanitized because the ad-hoc
    label carries a colon, which is illegal in a Windows filename."""
    ts = (res or {}).get("generated_at")
    day = ts[:10] if isinstance(ts, str) and len(ts) >= 10 else ""
    if not day:
        day = (now or datetime.now(_TZ)).date().isoformat()
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (slot or "briefing"))
    return f"gamma-briefing-{day}-{safe}.html"
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/push_notify.py services/options_svc/tests/test_push_notify.py
git commit -m "feat(options): briefing_filename builder"
```

---

### Task 7: `send_gamma_briefing` gated fan-out

**Files:**
- Modify: `services/options_svc/push_notify.py` (import block + new sender)
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Write the failing test**

```python
import pytest


@pytest.fixture
def briefing_cfg():
    return {"enabled": True,
            "telegram": {"bot_token": "TOK", "chat_id": 7},
            "discord": {"webhook_url": "https://main"},
            "gamma_briefing": {"enabled": True,
                               "slots": ["premarket", "open", "midday", "close"],
                               "webhook_url": "https://briefings"}}


@pytest.fixture
def briefing_res():
    return {"html": "<html>doc</html>",
            "analysis": {"regime": "Pinned", "bias": 10, "headline": "hi"},
            "generated_at": "2026-07-23T11:30:00"}


def _capture(monkeypatch):
    sent = {"tg": [], "dc": []}
    monkeypatch.setattr(pn, "send_telegram_document",
                        lambda *a, **k: sent["tg"].append(a))
    monkeypatch.setattr(pn, "send_discord_file",
                        lambda *a, **k: sent["dc"].append(a))
    return sent


def test_send_gamma_briefing_happy_path(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    tok, chat, name, content, caption = sent["tg"][0]
    assert (tok, chat) == ("TOK", 7)
    assert name == "gamma-briefing-2026-07-23-midday.html"
    assert content == b"<html>doc</html>"
    assert caption.startswith("Gamma · Midday")
    hook, dname, dcontent, dcaption = sent["dc"][0]
    assert hook == "https://briefings"          # dedicated webhook wins
    assert (dname, dcontent) == (name, content)


def test_send_gamma_briefing_falls_back_to_main_webhook(monkeypatch, briefing_cfg,
                                                        briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["webhook_url"] = ""
    pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg)
    assert sent["dc"][0][0] == "https://main"


def test_send_gamma_briefing_master_gate(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["enabled"] = False
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["tg"] == [] and sent["dc"] == []


def test_send_gamma_briefing_feature_gate(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["enabled"] = False
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["tg"] == []


def test_send_gamma_briefing_slot_not_selected(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg["gamma_briefing"]["slots"] = ["premarket", "close"]
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["tg"] == []


def test_send_gamma_briefing_skips_degraded_run(monkeypatch, briefing_cfg):
    """A no-chains / no-API-key run still produces readable HTML but carries no
    `analysis` — pushing 'no chains available' 4x/day is exactly the spam to avoid."""
    sent = _capture(monkeypatch)
    degraded = {"html": "<html>No chains available</html>", "analysis": None}
    assert pn.send_gamma_briefing(degraded, slot="midday", config=briefing_cfg) is False
    assert sent["tg"] == [] and sent["dc"] == []


def test_send_gamma_briefing_skips_empty_html(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_res["html"] = ""
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False


def test_send_gamma_briefing_skips_oversize(monkeypatch, briefing_cfg, briefing_res):
    sent = _capture(monkeypatch)
    briefing_res["html"] = "z" * (pn._BRIEFING_MAX_BYTES + 1)
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is False
    assert sent["dc"] == []


def test_send_gamma_briefing_missing_block_defaults_on(monkeypatch, briefing_cfg,
                                                       briefing_res):
    sent = _capture(monkeypatch)
    briefing_cfg.pop("gamma_briefing")
    assert pn.send_gamma_briefing(briefing_res, slot="midday", config=briefing_cfg) is True
    assert sent["dc"][0][0] == "https://main"
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q -k send_gamma_briefing
```

Expected: FAIL with `AttributeError: ... has no attribute 'send_gamma_briefing'`.

**Step 3: Write minimal implementation**

First extend the existing import block at the top of `push_notify.py`:

```python
from shared.notify.channels import (
    load_config as _shared_load_config,
    send_telegram,
    send_discord,
    send_sms,
    send_telegram_document,
    send_discord_file,
    _in_market_hours,
    _today_ct,
)
```

Then add the sender:

```python
_BRIEFING_MAX_BYTES = 7_500_000   # under Discord's 8 MB webhook ceiling


def send_gamma_briefing(res: dict, *, slot: str, config: dict | None = None) -> bool:
    """Push a scheduled Gamma briefing to Telegram + Discord as an HTML attachment.

    Returns True if a send was attempted. THREE independent gates: the master
    `enabled`, the `gamma_briefing.enabled`/`slots` block, and a content gate that
    requires a real `analysis` (a degraded page has HTML but no analysis).
    No SMS — a file cannot ride SMS and a bare text line there is noise.
    Best-effort per channel (the primitives never raise)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    gb = cfg.get("gamma_briefing") or {}
    if not gb.get("enabled", True):
        return False
    slots = gb.get("slots")
    if slots and slot not in slots:
        return False
    res = res or {}
    if not res.get("analysis"):        # degraded run — never push
        return False
    html = res.get("html") or ""
    if not html:
        return False
    content = html.encode("utf-8")
    if len(content) > _BRIEFING_MAX_BYTES:
        # Not reachable in practice (~30-60 KB measured) but a silent 413 would be
        # invisible, so fail loudly-in-the-log instead.
        log.warning("gamma briefing %s too large to push (%d bytes)", slot, len(content))
        return False
    caption = briefing_caption(res, slot)
    filename = briefing_filename(res, slot)
    tg = cfg.get("telegram", {})
    webhook = gb.get("webhook_url") or (cfg.get("discord", {}) or {}).get("webhook_url")
    send_telegram_document(tg.get("bot_token"), tg.get("chat_id"),
                           filename, content, caption)
    send_discord_file(webhook, filename, content, caption)
    return True
```

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/push_notify.py services/options_svc/tests/test_push_notify.py
git commit -m "feat(options): send_gamma_briefing gated HTML fan-out"
```

---

### Task 8: Wire the handler hook

**Files:**
- Modify: `services/options_svc/handlers.py:1046-1071` (`run_scheduled_gamma_analyze`)
- Test: `services/options_svc/tests/test_handlers.py`

First confirm `push_notify` is already imported in `handlers.py`:

```bash
grep -n "import push_notify\|from . import push_notify" services/options_svc/handlers.py
```

If absent, add it beside the other local imports at the top.

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_handlers.py` (match the file's existing fixture style for the fake bus — reuse whatever `run_scheduled_gamma_analyze` tests already use):

```python
def test_scheduled_analyze_pushes_briefing(monkeypatch, fake_bus):
    pushed = []
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "<html>x</html>", "analysis": {"bias": 1}})
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing",
                        lambda res, **kw: pushed.append((res, kw)))
    handlers.run_scheduled_gamma_analyze(fake_bus, "midday")
    assert pushed and pushed[0][1]["slot"] == "midday"
    # the push carries the SAME payload that was cached (slot/generated_at stamped)
    assert pushed[0][0]["slot"] == "midday"


def test_scheduled_analyze_push_failure_does_not_break_handler(monkeypatch, fake_bus):
    """The load-bearing guarantee: a push failure must never cost us the briefing.
    Caching and history persistence run BEFORE the push and must still complete."""
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **k: {"html": "<html>x</html>", "analysis": {"bias": 1}})

    def boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing", boom)
    handlers.run_scheduled_gamma_analyze(fake_bus, "midday")     # must not raise
    assert fake_bus.cache_get("cache:options:gamma_analyze_midday") is not None


def test_scheduled_analyze_unknown_slot_does_not_push(monkeypatch, fake_bus):
    pushed = []
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing",
                        lambda *a, **k: pushed.append(a))
    handlers.run_scheduled_gamma_analyze(fake_bus, "nonsense")
    assert pushed == []
```

**Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest services/options_svc/tests/test_handlers.py -q -k scheduled_analyze
```

Expected: FAIL — `pushed` stays empty (no hook yet).

**Step 3: Write minimal implementation**

At the end of `run_scheduled_gamma_analyze`, after `publish_gamma_briefing_index(bus)`:

```python
    # Phone push: ship the briefing as a self-contained HTML attachment (Telegram +
    # Discord). LAST and guarded — publish/persist first, notify second (the house
    # pattern from rescan/refresh_captured), so a channel outage can never cost us
    # the cached briefing or its history row.
    try:
        push_notify.send_gamma_briefing(res, slot=slot)
    except Exception:
        log.exception("gamma briefing push degraded")
```

Also extend the docstring's closing line to mention the push.

**Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest services/options_svc -q
```

Expected: PASS — the whole options_svc suite green (baseline was 432+).

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options): push scheduled gamma briefing to Telegram + Discord"
```

---

### Task 9: Local credential config (NOT committed)

**Files:**
- Modify: `shared/notifications.json` — **gitignored; never `git add` this file**

**Step 1: Add the block**

Add to `shared/notifications.json`, using the dedicated webhook the user supplied in chat (it is a live credential — it must appear in this file only, never in the example file, code, tests, docs, or a commit message):

```json
  "gamma_briefing": {
    "enabled": true,
    "slots": ["premarket", "open", "midday", "close"],
    "webhook_url": "<the webhook URL from chat>"
  }
```

**Step 2: Verify the file stays untracked**

```bash
git status --porcelain shared/notifications.json
```

Expected: **no output** (gitignored). If it prints anything, STOP — do not commit.

**Step 3: Verify config resolution**

```bash
.venv/Scripts/python -c "from shared.notify import load_config; gb=load_config()['gamma_briefing']; print(gb['enabled'], gb['slots'], bool(gb['webhook_url']))"
```

Expected: `True ['premarket', 'open', 'midday', 'close'] True`

**Step 4: No commit** — this task produces no tracked change.

---

### Task 10: Live end-to-end verification

**Step 1: Restart the service**

`options_svc` holds the old code in memory. Restart it from the `/status` page's per-component Restart button, or:

```bash
.venv/Scripts/python services/options_svc/app.py
```

**Step 2: Trigger one real push**

From the repo root, generate a live briefing and push it (needs the proxy on :8100, an `ANTHROPIC_API_KEY`, and market chains available):

```bash
.venv/Scripts/python -c "import sys; sys.path.insert(0,'services/options_svc'); import compute, push_notify; res = compute.gamma_analyze(label='Manual verify'); res['generated_at']='2026-07-23T12:00:00'; print('analysis:', bool(res.get('analysis')), 'bytes:', len(res.get('html') or '')); print('sent:', push_notify.send_gamma_briefing(res, slot='midday'))"
```

Expected: `analysis: True`, a non-zero byte count, `sent: True`.

**Step 3: Confirm on the phone**

- The file lands in **both** Telegram and the dedicated Discord channel.
- The caption line reads correctly (slot · regime · bias · headline).
- Tapping the attachment opens the **full infographic** — banner, bias meter, per-index cards, SVG ladder, metric tiles — identical to `/options/analyze`.

**Step 4: Confirm the degraded path stays silent**

```bash
.venv/Scripts/python -c "import sys; sys.path.insert(0,'services/options_svc'); import push_notify; print(push_notify.send_gamma_briefing({'html':'<html>no chains</html>','analysis':None}, slot='midday'))"
```

Expected: `False`, and **nothing** arrives in either channel.

**Step 5: Full suites green**

```bash
.venv/Scripts/python -m pytest shared/notify -q
```

```bash
.venv/Scripts/python -m pytest services/options_svc -q
```

```bash
.venv/Scripts/python -m ruff check shared/notify services/options_svc
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md` (the "Last updated" block + the Gamma Analyze section)

Record: the four scheduled briefings now push as HTML file attachments; the three gates; the dedicated `gamma_briefing.webhook_url`; the "restart `options_svc`" note; and the constraint worth remembering — **Telegram/Discord render no HTML in a message body, so rich artifacts must travel as file attachments**.

```bash
git add CLAUDE.md
git commit -m "docs: gamma briefing HTML push"
```
