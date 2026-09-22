# X Posting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Post the daily market reports, the hourly trade ideas and ad-hoc marketing
material to X, each with capped, config-driven hashtags.

**Architecture:** ONE posting path — `shared/notify/x_post.py` (OAuth 1.0a over
`requests-oauthlib`, v2 media upload + v2 create-post, daily cap, a Redis log) — called
ONLY from `options_svc`. The pure text rules (weighted length, hashtags, fitting) live
in `shared/x_text.py`, stdlib-only so Tier 1 may import it for the live count.
`market_svc` does not post: when the published report changes it enqueues
`x_post_report` on `cmd:options`. The ad-hoc page `/x` enqueues `x_post`. Both
commands are replay-guarded.

**Tech Stack:** Python 3.11, `requests-oauthlib` (already locked), Pillow, NiceGUI,
the Redis bus (fakeredis under pytest), pytest.

**Design:** [2026-09-22-x-posting-design.md](2026-09-22-x-posting-design.md). One
change from it, made while planning: the Redis keys live under the options domain
(`cache:options:x_log`, `cache:options:x_count`, `cache:options:x_reports`) so the
web app reads them with the ordinary `bus_client.read("options:x_log")`, and
report posting moved from market_svc into options_svc (market_svc only enqueues),
so the report card can reuse `trade_idea_card`'s canvas without a cross-service
import.

## Conventions for every task

- Python: `PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (a worktree
  has no venv). Run service/shared tests **from the worktree root**, one folder at a
  time; webgui tests from `webgui/`.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Stage files **by name** (another session may share the tree). Never `git add -A`.
- Never weaken an existing assertion to make a test pass.

---

### Task 1: `shared/x_text.py` — weighted length, hashtags, fitting (pure)

**Files:**
- Create: `shared/x_text.py`
- Test: `shared/tests/test_x_text.py`

**Step 1: Write the failing tests**

```python
"""X's text rules, pure. Tier 1 imports this module (the /x page's live count)."""
import pathlib
import subprocess
import sys

from shared import x_text as xt

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_a_url_counts_23_whatever_its_length():
    assert xt.weighted_len("https://neuralstrike.co/report.html") == 23
    assert xt.weighted_len("see https://a.co") == 4 + 23


def test_emoji_and_cjk_count_two():
    assert xt.weighted_len("a") == 1
    assert xt.weighted_len("\U0001F7E2") == 2
    assert xt.weighted_len("日") == 2


def test_tags_are_normalised_deduped_and_capped_derived_first():
    tags = xt.hashtags(["$nvda", "#0DTE"], ["options", "#Options", "#trading"], max_tags=3)
    assert tags == ["$NVDA", "#0DTE", "#options"]


def test_a_cashtag_keeps_its_dollar_and_a_bare_word_gets_a_hash():
    assert xt.hashtags([], ["stocks", "$spy"], max_tags=5) == ["#stocks", "$SPY"]


def test_junk_tags_are_dropped():
    assert xt.hashtags([], ["", "  ", "#", "two words", None, 5], max_tags=5) == []


def test_fit_keeps_everything_when_it_fits():
    out = xt.fit_text("Body", "https://neuralstrike.co", ["#a", "#b"])
    assert out == "Body\n\nhttps://neuralstrike.co\n#a #b"
    assert xt.weighted_len(out) <= 280


def test_fit_drops_tags_from_the_end_before_touching_the_body():
    body = "x" * 240
    out = xt.fit_text(body, "https://neuralstrike.co", ["#one", "#two", "#three"])
    assert out.startswith(body)
    assert "#three" not in out
    assert xt.weighted_len(out) <= 280


def test_fit_truncates_the_body_with_an_ellipsis_only_when_no_tag_is_left():
    out = xt.fit_text("y" * 400, "https://neuralstrike.co", ["#a"])
    assert "…" in out and "#a" not in out
    assert out.endswith("https://neuralstrike.co")
    assert xt.weighted_len(out) <= 280


def test_fit_without_a_link():
    assert xt.fit_text("Hi", "", ["#a"]) == "Hi\n\n#a"


PROBE = """
import sys
before = set(sys.modules)
import shared.x_text
print("NEW:" + ",".join(sorted(set(sys.modules) - before)))
"""


def test_x_text_imports_nothing_but_the_stdlib():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    ours = {m for m in new if m.split(".")[0] in ("shared", "services", "webgui",
                                                  "repo_paths", "requests", "redis")}
    assert ours == {"shared.x_text"}, sorted(ours)
```

**Step 2: Run** `"$PY" -m pytest shared/tests/test_x_text.py -q` — expect FAIL (no module).

**Step 3: Implement `shared/x_text.py`**

```python
"""X's text rules — weighted length, hashtags, fitting a post into 280.

Stdlib only: Tier 1 imports it so the /x page's live count is the SAME
computation the service posts with (pinned by shared/tests/test_x_text.py).

X counts a URL as 23 (t.co wrapping) and weights characters outside a few Latin
and punctuation ranges as 2 (twitter-text's v3 config), so an emoji or a CJK
glyph costs two.
"""
import re

LIMIT = 280
URL_WEIGHT = 23
_URL = re.compile(r"https?://\S+")
# twitter-text v3: these code point ranges weigh 1; everything else weighs 2.
_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_TAG_OK = re.compile(r"^[A-Za-z0-9_]+$")


def _char_weight(ch):
    o = ord(ch)
    return 1 if any(lo <= o <= hi for lo, hi in _LIGHT) else 2


def weighted_len(text):
    text = str(text or "")
    n, pos = 0, 0
    for m in _URL.finditer(text):
        n += sum(_char_weight(c) for c in text[pos:m.start()]) + URL_WEIGHT
        pos = m.end()
    return n + sum(_char_weight(c) for c in text[pos:])


def _norm_tag(raw):
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("$"):
        word = s[1:]
        return f"${word.upper()}" if word and _TAG_OK.match(word) else None
    word = s.lstrip("#")
    return f"#{word}" if word and _TAG_OK.match(word) else None


def hashtags(derived, configured, *, max_tags=4):
    """Derived tags first (a trade's own cashtag matters most), then configured,
    normalised, de-duplicated case-insensitively, at most ``max_tags``."""
    out, seen = [], set()
    for raw in list(derived or []) + list(configured or []):
        tag = _norm_tag(raw)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:max(0, int(max_tags or 0))]


def _assemble(body, link, tags):
    tail = "\n".join(p for p in (link, " ".join(tags)) if p)
    return f"{body}\n\n{tail}" if tail else body


def fit_text(body, link="", tags=(), *, limit=LIMIT):
    """``body`` + link + tags within ``limit``. Tags drop from the END first;
    only with no tag left is the body cut, with an ellipsis. The link survives."""
    body, link, tags = str(body or "").strip(), str(link or "").strip(), list(tags or [])
    while tags and weighted_len(_assemble(body, link, tags)) > limit:
        tags.pop()
    out = _assemble(body, link, tags)
    if weighted_len(out) <= limit:
        return out
    while body and weighted_len(_assemble(body + "…", link, [])) > limit:
        body = body[:-1]
    return _assemble(body.rstrip() + "…", link, [])
```

**Step 4: Run** the tests — expect PASS.

**Step 5: Commit** `feat(x): pure X text rules - weighted length, hashtags, fitting`

---

### Task 2: the `x` config block (and the `twitter` credential fallback)

**Files:**
- Modify: `shared/notify/channels.py` (`_DEFAULTS`, `load_config`)
- Modify: `shared/notifications.example.json`
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Failing tests** (append to `test_channels.py`; reuse its existing
`tmp_path`/`_CONFIG_PATH` pattern — read the top of the file first):

```python
def test_x_block_defaults_ship_off_and_dry(tmp_path, monkeypatch):
    p = tmp_path / "n.json"; p.write_text("{}")
    cfg = channels.load_config(p)
    x = cfg["x"]
    assert x["enabled"] is False and x["dry_run"] is True
    assert x["daily_cap"] == 15 and x["max_tags"] == 4
    assert set(x["kinds"]) == {"report", "trade_idea", "marketing"}
    assert x["hashtags"]["trade_idea"] == ["#options", "#optionstrading", "#trading"]


def test_x_credentials_fall_back_to_the_old_twitter_block(tmp_path):
    p = tmp_path / "n.json"
    p.write_text('{"twitter": {"api_key": "k", "api_secret": "s", '
                 '"access_token": "t", "access_secret": "a"}}')
    x = channels.load_config(p)["x"]
    assert (x["api_key"], x["access_secret"]) == ("k", "a")


def test_x_env_credentials_win(tmp_path, monkeypatch):
    p = tmp_path / "n.json"; p.write_text('{"x": {"api_key": "file"}}')
    monkeypatch.setenv("X_API_KEY", "env")
    assert channels.load_config(p)["x"]["api_key"] == "env"


def test_a_suppressed_environment_turns_x_off(tmp_path, monkeypatch):
    import repo_paths
    p = tmp_path / "n.json"; p.write_text('{"x": {"enabled": true}}')
    monkeypatch.setitem(repo_paths.ENV_FLAGS, "allow_notifications", False)
    monkeypatch.setattr(channels, "ENV_FLAGS", repo_paths.ENV_FLAGS)
    cfg = channels.load_config(p)
    assert cfg["x"]["enabled"] is False
    assert all(k["enabled"] is False for k in cfg["x"]["kinds"].values())
```

**Step 2: Run** `"$PY" -m pytest shared/notify/tests/test_channels.py -q` — the new tests FAIL.

**Step 3: Implement.** In `_DEFAULTS`, **replace** the `twitter` entry with:

```python
    # X (Twitter): the ONE public posting channel (shared/notify/x_post.py). Ships
    # OFF and dry: nothing posts until OAuth 1.0a keys are set AND enabled + dry_run
    # are flipped. `daily_cap` guards X's per-user allowance (~17/24h on the free
    # tier); `kinds` switch each source off without touching the others;
    # `hashtags` are per kind, `max_tags` caps the total (derived cashtags count).
    "x": {
        "enabled": False,
        "dry_run": True,
        "daily_cap": 15,
        "max_tags": 4,
        "link": "https://neuralstrike.co",
        "report_max_age_min": 45,
        "api_key": "", "api_secret": "", "access_token": "", "access_secret": "",
        "kinds": {"report": {"enabled": True}, "trade_idea": {"enabled": True},
                  "marketing": {"enabled": True}},
        "hashtags": {
            "report": ["#stocks", "#StockMarket", "#trading"],
            "trade_idea": ["#options", "#optionstrading", "#trading"],
            "marketing": ["#options", "#trading"],
        },
    },
```

In `load_config`, replace the `TWITTER_*` block with:

```python
    # X OAuth 1.0a keys: the old `twitter` block is a fallback (keys saved before
    # 2026-09-22 carry over), and X_* / TWITTER_* env win over both files.
    old = raw_twitter if isinstance(raw_twitter, dict) else {}
    for key in ("api_key", "api_secret", "access_token", "access_secret"):
        if not cfg["x"].get(key) and old.get(key):
            cfg["x"][key] = old[key]
        env = os.environ.get(f"X_{key.upper()}") or os.environ.get(f"TWITTER_{key.upper()}")
        if env:
            cfg["x"][key] = env
    if os.environ.get("X_ENABLED"):
        cfg["x"]["enabled"] = os.environ["X_ENABLED"].lower() not in ("0", "false", "no")
```

where `raw_twitter` is captured in the file-read `try` (`raw.get("twitter")`,
initialised to `None` before the `try`). `_disable_all` is recursive, so the kinds'
`enabled` flags are already zeroed in a suppressed environment.

In `notifications.example.json`, replace the `"twitter"` object with the `"x"` block
above (credentials empty).

**Step 4: Run** the channels tests — PASS. Then
`grep -rn "\"twitter\"\]\|cfg\[.twitter.\]" shared services` must return nothing.

**Step 5: Commit** `feat(x): the x notifications block; old twitter keys carry over`

---

### Task 3: `shared/notify/x_post.py` — the one path to X

**Files:**
- Create: `shared/notify/x_post.py`
- Test: `shared/notify/tests/test_x_post.py`

**Step 1: Failing tests**

```python
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared.bus import Bus
from shared.notify import x_post

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 22, 10, 0, tzinfo=CT)
CREDS = {"api_key": "k", "api_secret": "s", "access_token": "t", "access_secret": "a"}


def _cfg(**over):
    x = {"enabled": True, "dry_run": False, "daily_cap": 2, **CREDS,
         "kinds": {"report": {"enabled": True}, "trade_idea": {"enabled": True},
                   "marketing": {"enabled": True}}}
    x.update(over)
    return {"x": x}


class FakeSession:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.fail:
            raise self.fail
        class R:
            status_code = 200
            def __init__(s, body): s._b = body
            def json(s): return s._b
            text = ""
        if url.endswith("/media/upload"):
            return R({"data": {"id": "m1"}})
        return R({"data": {"id": "99", "text": "t"}})


@pytest.fixture
def bus():
    return Bus(fake=True)


def test_posts_text_and_image_and_logs(bus, monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(x_post, "_session", lambda creds: s)
    out = x_post.post(bus, "hello", b"PNG", kind="marketing", now=NOW, config=_cfg())
    assert out["ok"] and out["id"] == "99" and out["url"].endswith("/99")
    assert s.calls[0][0].endswith("/2/media/upload")
    assert s.calls[1][1]["json"] == {"text": "hello", "media": {"media_ids": ["m1"]}}
    log = bus.cache_get(x_post.LOG_KEY).payload["posts"]
    assert log[0]["kind"] == "marketing" and log[0]["status"] == "posted"


def test_dry_run_calls_nothing_but_logs_and_counts_nothing(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(dry_run=True))
    assert out["ok"] and out["dry_run"]
    assert bus.cache_get(x_post.LOG_KEY).payload["posts"][0]["status"] == "dry_run"
    assert x_post.posted_today(bus, NOW) == 0


@pytest.mark.parametrize("over,reason", [
    ({"enabled": False}, "disabled"),
    ({"kinds": {"report": {"enabled": False}}}, "report disabled"),
    ({"api_key": ""}, "no credentials"),
])
def test_refusals_are_logged_with_their_reason(bus, over, reason):
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(**over))
    assert not out["ok"] and out["error"] == reason
    assert bus.cache_get(x_post.LOG_KEY).payload["posts"][0]["reason"] == reason


def test_the_daily_cap_refuses_the_third_post(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    for _ in range(2):
        assert x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())["ok"]
    out = x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())
    assert out["error"] == "daily cap (2) reached"
    tomorrow = NOW + dt.timedelta(days=1)
    assert x_post.post(bus, "a", None, kind="marketing", now=tomorrow, config=_cfg())["ok"]


def test_a_network_error_never_raises(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession(fail=OSError("down")))
    out = x_post.post(bus, "a", b"P", kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "down" in out["error"]
    assert x_post.posted_today(bus, NOW) == 0


def test_an_http_error_is_reported_not_raised(bus, monkeypatch):
    class Bad(FakeSession):
        def post(self, url, **kw):
            class R:
                status_code = 403; text = '{"detail":"forbidden"}'
                def json(s): return {"detail": "forbidden"}
            return R()
    monkeypatch.setattr(x_post, "_session", lambda c: Bad())
    out = x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "403" in out["error"]


def test_the_log_keeps_the_last_100(bus):
    for i in range(105):
        x_post.post(bus, str(i), None, kind="marketing", now=NOW,
                    config=_cfg(enabled=False))
    assert len(bus.cache_get(x_post.LOG_KEY).payload["posts"]) == 100
```

**Step 2: Run** `"$PY" -m pytest shared/notify/tests/test_x_post.py -q` — FAIL.

**Step 3: Implement `shared/notify/x_post.py`**

```python
"""The ONE path to X. Nothing else in the repo calls the X API.

v2 media upload (``POST /2/media/upload``, one-shot, image < 5 MB) then v2 create
post (``POST /2/tweets``), OAuth 1.0a user context through requests-oauthlib.
tweepy's ``media_upload`` is the v1.1 endpoint X has been retiring, which is why
this does not use tweepy.

Every attempt is appended to ``cache:options:x_log`` (the /x page reads it) and to
``x_posts.jsonl`` on disk. **Never raises**: a refusal or a failure comes back as
``{"ok": False, "error": ...}``. The daily cap counts real posts only, per CT day.
⚠ The cap is read-then-written, not atomic — safe because options_svc is the only
caller and runs one command consumer; a second posting process needs a lock.
"""
import datetime as _dt
import json
import logging
from zoneinfo import ZoneInfo

from repo_paths import X_POSTS_LOG
from shared.notify.channels import load_config

log = logging.getLogger(__name__)

API = "https://api.x.com/2"
LOG_KEY = "cache:options:x_log"
COUNT_KEY = "cache:options:x_count"
LOG_KEEP = 100
KINDS = ("report", "trade_idea", "marketing")
_CREDS = ("api_key", "api_secret", "access_token", "access_secret")
_CT = ZoneInfo("America/Chicago")
_TIMEOUT = 30


def _session(creds):
    from requests_oauthlib import OAuth1Session
    return OAuth1Session(creds["api_key"], client_secret=creds["api_secret"],
                         resource_owner_key=creds["access_token"],
                         resource_owner_secret=creds["access_secret"])


def _day(now):
    return now.astimezone(_CT).date().isoformat()


def posted_today(bus, now):
    env = bus.cache_get(COUNT_KEY)
    p = env.payload if env is not None else None
    return int(p.get("count") or 0) if isinstance(p, dict) and p.get("day") == _day(now) else 0


def _record(bus, entry):
    try:
        env = bus.cache_get(LOG_KEY)
        posts = list((env.payload or {}).get("posts") or []) if env is not None else []
        bus.cache_set(LOG_KEY, {"posts": ([entry] + posts)[:LOG_KEEP]})
    except Exception:  # noqa: BLE001 -- a lost log line must not lose the post
        log.warning("x log write failed", exc_info=True)
    try:
        X_POSTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with X_POSTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        log.warning("x jsonl append failed", exc_info=True)


def _check(resp, what):
    if resp.status_code >= 300:
        raise RuntimeError(f"{what} HTTP {resp.status_code}: {str(resp.text)[:200]}")
    return resp.json()


def _send(creds, text, png):
    s = _session(creds)
    body = {"text": text}
    if png:
        up = _check(s.post(f"{API}/media/upload",
                           files={"media": ("card.png", png, "image/png")},
                           data={"media_category": "tweet_image", "media_type": "image/png"},
                           timeout=_TIMEOUT), "media upload")
        body["media"] = {"media_ids": [str(up["data"]["id"])]}
    out = _check(s.post(f"{API}/tweets", json=body, timeout=_TIMEOUT), "create post")
    return str(out["data"]["id"])


def post(bus, text, png=None, *, kind, now=None, config=None, meta=None):
    now = now or _dt.datetime.now(_CT)
    entry = {"at": now.isoformat(), "kind": kind, "text": text,
             "image": bool(png), "meta": meta or {}, "status": "refused",
             "reason": None, "id": None, "url": None}
    result = {"ok": False, "id": None, "url": None, "error": None, "dry_run": False}

    def _done(status, reason=None):
        entry["status"], entry["reason"] = status, reason
        result["error"] = reason
        _record(bus, entry)
        log.info("x %s: %s %s", kind, status, reason or entry["id"] or "")
        return result

    try:
        x = (config or load_config()).get("x") or {}
        if not x.get("enabled"):
            return _done("refused", "disabled")
        if not ((x.get("kinds") or {}).get(kind) or {}).get("enabled"):
            return _done("refused", f"{kind} disabled")
        if x.get("dry_run"):
            result.update(ok=True, dry_run=True)
            return _done("dry_run")
        creds = {k: x.get(k) for k in _CREDS}
        if not all(creds.values()):
            return _done("refused", "no credentials")
        cap = int(x.get("daily_cap") or 0)
        count = posted_today(bus, now)
        if cap and count >= cap:
            return _done("refused", f"daily cap ({cap}) reached")
        post_id = _send(creds, text, png)
        bus.cache_set(COUNT_KEY, {"day": _day(now), "count": count + 1})
        entry["id"] = post_id
        entry["url"] = f"https://x.com/i/web/status/{post_id}"
        result.update(ok=True, id=post_id, url=entry["url"])
        return _done("posted")
    except Exception as exc:  # noqa: BLE001 -- never raises, by contract
        log.warning("x %s post failed: %s", kind, exc)
        return _done("failed", str(exc)[:300])
```

Add to `repo_paths.py`, beside `TRADE_IDEAS_DIR`:
`X_POSTS_LOG = <same data dir as TRADE_IDEAS_DIR>.parent / "x_posts.jsonl"` —
read how `TRADE_IDEAS_DIR` is built and put the log in the same data directory
(it is under `options-scanner/data`, which the live-DB guard does not touch because
this is not SQLite). In the test module add an autouse fixture that
`monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x.jsonl")`.

**Step 4: Run** the tests — PASS.

**Step 5: Commit** `feat(x): x_post - the one path to X, capped and logged`

---

### Task 4: remove the old per-signal X poster; swap tweepy for requests-oauthlib

**Files:**
- Modify: `services/options_svc/push_notify.py` — delete `_TWEET_MAX`,
  `_TWEET_DISCLAIMER`, `_tweet_footer`, `twitter_signal_text`, `_TWITTER_CRED_KEYS`,
  `_has_twitter_creds`, `_twitter_client`, `send_twitter`, `load_post_count`,
  `save_post_count`, `notify_twitter`, and the `if kind == "scanner": ... notify_twitter`
  block in `notify_signals`.
- Modify: `services/options_svc/tests/test_push_notify.py` — delete the tests of those
  functions only. Keep every other test unchanged.
- Modify: `requirements.txt` — replace the `tweepy` line with
  `requests-oauthlib>=2.0     # OAuth 1.0a for the X API (shared/notify/x_post.py)`.
- Modify: `requirements.lock` — delete `tweepy==4.17.0`; keep `oauthlib` and
  `requests-oauthlib` (now a direct dependency).

**Step 1:** `grep -rn "twitter\|tweepy\|load_post_count" services shared webgui --include=*.py`
and list every hit before deleting.

**Step 2:** Delete the functions and their tests.

**Step 3:** Add one guard test to `test_push_notify.py`:

```python
def test_nothing_but_x_post_talks_to_x():
    """Every X post goes through shared/notify/x_post.py."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    hits = []
    for base in ("services", "shared", "webgui"):
        for p in (root / base).rglob("*.py"):
            if "tests" in p.parts or p.name == "x_post.py":
                continue
            src = p.read_text(encoding="utf-8", errors="ignore")
            if "import tweepy" in src or "api.x.com" in src or "api.twitter.com" in src:
                hits.append(str(p.relative_to(root)))
    assert hits == []
```

**Step 4: Run** `"$PY" -m pytest services/options_svc/tests/test_push_notify.py -q` and
`"$PY" -m pytest shared/notify -q` — PASS. Confirm every name in
`requirements.txt` still appears in `requirements.lock` (the lock invariant in CLAUDE.md).

**Step 5: Commit** `refactor(x): retire the per-signal X poster; requests-oauthlib replaces tweepy`

---

### Task 5: the trade idea's X text, and the post after Discord/Telegram

**Files:**
- Modify: `services/options_svc/trade_idea.py` (add `x_text`)
- Modify: `services/options_svc/push_notify.py` (`send_trade_idea` takes `png=`; add `trade_idea_png`)
- Modify: `services/options_svc/handlers.py:run_trade_idea`
- Test: `services/options_svc/tests/test_trade_idea.py`, `.../test_trade_idea_handler.py`
  (read the existing trade-idea tests first and reuse their idea/scan fixtures)

**Step 1: Failing tests**

```python
# test_trade_idea.py
def test_x_text_carries_the_cashtag_and_fits(idea):          # existing fixture
    from shared import x_text as xt
    cfg = {"link": "https://neuralstrike.co", "max_tags": 4,
           "hashtags": {"trade_idea": ["#options", "#optionstrading", "#trading"]}}
    out = trade_idea.x_text(idea, cfg, today=dt.date(2026, 9, 22))
    assert f"${idea['symbol'].lstrip('$')}" in out
    assert "https://neuralstrike.co" in out
    assert xt.weighted_len(out) <= 280


def test_x_text_adds_0dte_only_inside_a_day(idea):
    cfg = {"max_tags": 5, "hashtags": {"trade_idea": []}}
    exp = dt.date.fromisoformat(idea["expiration"][:10])
    assert "#0DTE" in trade_idea.x_text(idea, cfg, today=exp - dt.timedelta(days=1))
    assert "#0DTE" not in trade_idea.x_text(idea, cfg, today=exp - dt.timedelta(days=5))
```

```python
# handler test: X runs after the private sends, and cannot break them
def test_an_x_failure_still_posts_to_discord_and_telegram(bus, monkeypatch, fresh_scan):
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_trade_idea",
                        lambda idea, **kw: sent.append(kw.get("png")) or True)
    monkeypatch.setattr(handlers.push_notify, "trade_idea_png", lambda idea, **kw: b"PNG")
    def boom(*a, **k): raise RuntimeError("x down")
    monkeypatch.setattr(handlers.x_post, "post", boom)
    res = handlers.run_trade_idea(bus, "08:35", now=NOW)
    assert res["status"] == "posted" and sent == [b"PNG"]
    assert res["x"]["ok"] is False


def test_the_x_post_gets_the_same_png(bus, monkeypatch, fresh_scan):
    got = {}
    monkeypatch.setattr(handlers.push_notify, "send_trade_idea", lambda idea, **kw: True)
    monkeypatch.setattr(handlers.push_notify, "trade_idea_png", lambda idea, **kw: b"PNG")
    monkeypatch.setattr(handlers.x_post, "post",
                        lambda bus, text, png, **kw: got.update(png=png, kind=kw["kind"])
                        or {"ok": True})
    handlers.run_trade_idea(bus, "08:35", now=NOW)
    assert got == {"png": b"PNG", "kind": "trade_idea"}
```

**Step 2: Run** — FAIL.

**Step 3: Implement.**

`trade_idea.py`:

```python
def x_text(idea, x_cfg, *, today):
    """The trade idea for X: the caption, the link, and capped tags led by the
    symbol's cashtag (plus #0DTE inside a day)."""
    from shared import x_text as xt
    derived = [f"${str(idea.get('symbol') or '').lstrip('$')}"]
    dte = days_to_expiry(idea.get("expiration"), today)
    if dte is not None and dte <= 1:
        derived.append("#0DTE")
    tags = xt.hashtags(derived, (x_cfg.get("hashtags") or {}).get("trade_idea"),
                       max_tags=x_cfg.get("max_tags", 4))
    return xt.fit_text(caption(idea), x_cfg.get("link", ""), tags)
```

(check `days_to_expiry`'s signature and return type first; adapt the call.)

`push_notify.py`: add

```python
def trade_idea_png(idea, *, now, config=None):
    block = trade_idea_config(config or load_config())
    return trade_idea_card.render_trade_idea_png(
        idea, now=now, footer=str(block.get("footer") or ""))
```

and give `send_trade_idea` a `png=None` kwarg: when given, use it instead of
rendering. Existing callers and tests are unchanged.

`handlers.run_trade_idea`: import `from shared.notify import x_post` at module top
(as `x_post`). Replace the send block with:

```python
    result["idea"] = idea
    png = None
    try:
        from repo_paths import TRADE_IDEAS_DIR
        png = push_notify.trade_idea_png(idea, now=now)
        sent = push_notify.send_trade_idea(idea, now=now, archive_dir=TRADE_IDEAS_DIR,
                                           png=png)
    except Exception:  # noqa: BLE001 -- the primitives never raise; belt and braces
        _degrade.degraded("options.run_trade_idea.send")
        sent = False
    if sent:
        result["status"] = "posted"
        result["posted"] = trade_idea.next_posted(posted, idea, today)
        result["x"] = _post_trade_idea_x(bus, idea, png, now)
    else:
        result["reason"] = "send failed"
    return _finish()
```

with

```python
def _post_trade_idea_x(bus, idea, png, now):
    """X after the private sends; a failure here is recorded, never raised."""
    from services.options_svc import trade_idea
    try:
        cfg = push_notify.load_config()
        text = trade_idea.x_text(idea, cfg.get("x") or {}, today=now.date())
        return x_post.post(bus, text, png, kind="trade_idea", now=now, config=cfg,
                           meta={"symbol": idea.get("symbol"), "id": idea.get("id")})
    except Exception as exc:  # noqa: BLE001
        _degrade.degraded("options.run_trade_idea.x")
        return {"ok": False, "error": str(exc)[:300]}
```

**Step 4: Run** `"$PY" -m pytest services/options_svc/tests/test_trade_idea*.py services/options_svc/tests/test_push_notify.py -q` — PASS.

**Step 5: Commit** `feat(x): the hourly trade idea posts to X after Discord and Telegram`

---

### Task 6: the report card (Pillow)

**Files:**
- Modify: `services/options_svc/trade_idea_card.py` — `_header(c, now, label="TRADE IDEA")`
- Create: `services/options_svc/report_card.py`
- Test: `services/options_svc/tests/test_report_card.py`

**Step 1: Failing tests**

```python
import datetime as dt
import io

from services.options_svc import report_card

REPORT = {"headline": "Buyers defend the flip; breadth thin",
          "highlights": ["SPX holds 6,610 gamma flip", "Semis lead, staples lag",
                         "VIX term back in contango", "Flow: calls bid in NVDA",
                         "a fifth one is not drawn"],
          "slot": "midday", "slot_label": "Midday Report", "report_date": "2026-09-22",
          "as_of": "11:30 CT", "report_url": "https://neuralstrike.co/report.html"}


def test_renders_a_1200x675_card_at_2x():
    from PIL import Image
    png = report_card.render_report_png(REPORT, now=dt.datetime(2026, 9, 22, 11, 35))
    im = Image.open(io.BytesIO(png))
    assert im.size == (2400, 1350)


def test_never_raises_on_garbage():
    assert report_card.render_report_png({"headline": None, "highlights": 7}) is None \
        or isinstance(report_card.render_report_png({"headline": None, "highlights": 7}), bytes)


def test_draws_at_most_four_highlights():
    assert report_card.highlights(REPORT) == REPORT["highlights"][:4]
```

**Step 2: Run** — FAIL.

**Step 3: Implement `report_card.py`** using `trade_idea_card`'s `_Canvas`, `_header`,
`WIDTH`, `HEIGHT`, `PAD`, `SCALE`, `BRAND_ACCENT` and `card_kit` (`K.BG`, `K.TITLE`,
`K.TEXT`, `K.MUTED`, `K.EDGE`, `K.CARD`). Layout at 1200×675:
- header: `_header(c, now, label=f"{slot_label or 'MARKET REPORT'}")`
- verdict: headline at size 34 extrabold `K.TITLE`, wrapped with `K.wrap` to
  `WIDTH - 2*PAD`, at most 2 lines, from y=120
- highlights: up to 4 rows, each a 6px `BRAND_ACCENT` rounded square bullet + text
  size 20 regular `K.TEXT`, one line each (truncate with "…" by width), 58px apart
- footer: `neuralstrike.co/report.html` size 13 semibold `K.MUTED` at y=HEIGHT-34,
  and the `as_of` right-aligned

`render_report_png(report, *, now=None) -> bytes | None` wraps `_render` in
`try/except Exception` → `log.warning(...); return None`, exactly like
`render_trade_idea_png`. `highlights(report)` returns the first four string
highlights (non-strings dropped). Coerce every field through `str(... or "")`.

Render the fixture once to the scratchpad and **look at it** (Read the PNG) before
committing — a layout bug passes every size test.

**Step 4: Run** the card tests plus `test_trade_idea_card*.py` — PASS.

**Step 5: Commit** `feat(x): the market report card`

---

### Task 7: `x_post_report` and `x_post` commands in options_svc

**Files:**
- Modify: `services/options_svc/handlers.py` (`_REPLAY_GUARDED`, `handle_command`, two new functions)
- Test: `services/options_svc/tests/test_x_commands.py`

**Step 1: Failing tests**

```python
import base64
import datetime as dt
import time
from zoneinfo import ZoneInfo

from shared.bus import Bus
from shared.bus.command import Command          # check the real import path first
from services.options_svc import handlers

CT = ZoneInfo("America/Chicago")
REPORT = {...}  # same fixture as test_report_card


def _cmd(type_, args, ts=None):
    return Command(type=type_, args=args, ts=ts or time.time())


def test_a_report_posts_once_per_identity(monkeypatch):
    bus, calls = Bus(fake=True), []
    monkeypatch.setattr(handlers.x_post, "post",
                        lambda b, text, png, **kw: calls.append((text, kw["kind"])) or {"ok": True})
    args = {"report": REPORT, "mtime": time.time()}
    handlers.handle_command(bus, _cmd("x_post_report", args))
    handlers.handle_command(bus, _cmd("x_post_report", args))
    assert len(calls) == 1 and calls[0][1] == "report"
    assert "Buyers defend the flip" in calls[0][0] and "$SPY" in calls[0][0]


def test_a_stale_report_is_not_posted(monkeypatch):
    bus, calls = Bus(fake=True), []
    monkeypatch.setattr(handlers.x_post, "post", lambda *a, **k: calls.append(1))
    old = time.time() - 46 * 60
    handlers.handle_command(bus, _cmd("x_post_report", {"report": REPORT, "mtime": old}))
    assert calls == []


def test_ad_hoc_post_decodes_the_image_and_fits_the_text(monkeypatch):
    bus, got = Bus(fake=True), {}
    monkeypatch.setattr(handlers.x_post, "post",
                        lambda b, text, png, **kw: got.update(text=text, png=png, kind=kw["kind"]))
    handlers.handle_command(bus, _cmd("x_post", {
        "text": "New: the public Gamma page", "tags": ["#options", "gamma"],
        "link": "https://neuralstrike.co/live.html",
        "image_b64": base64.b64encode(b"\x89PNG").decode()}))
    assert got["png"] == b"\x89PNG" and got["kind"] == "marketing"
    assert got["text"].endswith("#options #gamma")


def test_an_oversized_image_is_refused_and_logged(monkeypatch):
    bus = Bus(fake=True)
    big = base64.b64encode(b"0" * (5 * 1024 * 1024 + 1)).decode()
    handlers.handle_command(bus, _cmd("x_post", {"text": "x", "image_b64": big}))
    log = bus.cache_get(handlers.x_post.LOG_KEY).payload["posts"]
    assert log[0]["reason"] == "image over 5 MB"


def test_both_commands_are_replay_guarded():
    assert {"x_post", "x_post_report"} <= set(handlers._REPLAY_GUARDED)
```

**Step 2: Run** — FAIL.

**Step 3: Implement.** Add `"x_post", "x_post_report"` to `_REPLAY_GUARDED` with a
comment line each (a replayed stream must not re-post publicly). In `handle_command`:

```python
    elif command.type in ("x_post", "x_post_report"):
        if _is_stale_side_effect(command):
            log.warning("REJECTED stale %s: a replayed command must not re-post to X",
                        command.type)
            return
        (run_x_post_report if command.type == "x_post_report" else run_x_post)(
            bus, command.args or {})
```

```python
X_REPORTS_KEY = "cache:options:x_reports"
_X_IMAGE_MAX = 5 * 1024 * 1024
_REPORT_TAGS = ("$SPY", "$QQQ")


def _report_identity(report):
    return "|".join(str(report.get(k) or "") for k in ("report_date", "slot", "as_of", "headline"))


def run_x_post_report(bus, args, now=None):
    """Post one published market report to X, once. Never raises."""
    import time as _time
    from services.options_svc import report_card
    from shared import x_text as xt
    report = args.get("report") if isinstance(args.get("report"), dict) else None
    if not report:
        return None
    cfg = push_notify.load_config()
    x = cfg.get("x") or {}
    ident = _report_identity(report)
    env = bus.cache_get(X_REPORTS_KEY)
    done = list((env.payload or {}).get("posted") or []) if env is not None else []
    if ident in done:
        return None
    age_min = (_time.time() - float(args.get("mtime") or 0)) / 60
    if age_min > float(x.get("report_max_age_min") or 45):
        log.info("x report %s: skipped, %.0f min old", ident, age_min)
        bus.cache_set(X_REPORTS_KEY, {"posted": ([ident] + done)[:50]})
        return None
    tags = xt.hashtags(_REPORT_TAGS, (x.get("hashtags") or {}).get("report"),
                       max_tags=x.get("max_tags", 4))
    label = report.get("slot_label") or "Market report"
    text = xt.fit_text(f"{label}: {report.get('headline')}",
                       report.get("report_url") or x.get("link", ""), tags)
    png = report_card.render_report_png(report)
    out = x_post.post(bus, text, png, kind="report", config=cfg, now=now,
                      meta={"report": ident})
    if out.get("ok"):
        bus.cache_set(X_REPORTS_KEY, {"posted": ([ident] + done)[:50]})
    return out


def run_x_post(bus, args, now=None):
    """One ad-hoc marketing post from the /x page. Never raises."""
    import base64
    from shared import x_text as xt
    cfg = push_notify.load_config()
    x = cfg.get("x") or {}
    png = None
    raw = args.get("image_b64")
    if raw:
        try:
            png = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            return x_post.post(bus, "", None, kind="marketing", now=now,
                               config={"x": {**x, "enabled": False}})  # logs "disabled"
        if len(png) > _X_IMAGE_MAX:
            x_post._record(bus, {"at": _dt.datetime.now().isoformat(), "kind": "marketing",
                                 "text": args.get("text"), "image": True, "meta": {},
                                 "status": "refused", "reason": "image over 5 MB",
                                 "id": None, "url": None})
            return None
    tags = xt.hashtags([], args.get("tags") or [], max_tags=x.get("max_tags", 4))
    text = xt.fit_text(args.get("text") or "", args.get("link") or "", tags)
    return x_post.post(bus, text, png, kind="marketing", config=cfg, now=now)
```

⚠ Replace the bad-base64 branch above with a direct `_record` call with reason
`"image did not decode"` — do not fake a "disabled" refusal. Promote `_record` to a
public `x_post.record_refusal(bus, kind, text, reason, now=None)` helper and use it
for both refusals (add a test for "image did not decode").

**Step 4: Run** `"$PY" -m pytest services/options_svc/tests/test_x_commands.py -q` — PASS.

**Step 5: Commit** `feat(x): x_post and x_post_report commands, replay-guarded`

---

### Task 8: market_svc enqueues a changed report

**Files:**
- Modify: `services/market_svc/scheduler.py:refresh_summary`
- Test: `services/market_svc/tests/test_scheduler.py`

**Step 1: Failing test** (reuse the existing `refresh_summary` test's `reports_dir`
fixture that writes `latest.html`/`latest.txt`):

```python
def test_a_changed_report_asks_options_to_post_it(reports_dir, bus):
    stamp = scheduler.refresh_summary(bus, None, reports_dir=reports_dir)
    cmds = bus._r.xrange("cmd:options")          # check the fake bus's raw client name
    assert len(cmds) == 1
    assert '"x_post_report"' in cmds[0][1]["data"]
    scheduler.refresh_summary(bus, stamp, reports_dir=reports_dir)
    assert len(bus._r.xrange("cmd:options")) == 1   # unchanged stamp: nothing new
```

**Step 2: Run** — FAIL.

**Step 3: Implement.** After `handlers.publish_summary(bus, payload)`:

```python
    try:
        mtime = (reports_dir or report_summary.REPORTS_DIR).joinpath("latest.html").stat().st_mtime
        bus.enqueue_command("cmd:options", {"type": "x_post_report",
                                            "args": {"report": payload, "mtime": mtime}})
    except Exception:  # noqa: BLE001 -- the X post is optional; the summary is not
        _log.warning("x report enqueue failed", exc_info=True)
```

A service restart re-enqueues the current report (the loop starts at `last_stamp =
None`); options_svc's identity dedup and the 45-minute age gate make that a no-op.
Say so in the function's docstring.

**Step 4: Run** `"$PY" -m pytest services/market_svc -q` — PASS.

**Step 5: Commit** `feat(x): market_svc hands each new report to options_svc for X`

---

### Task 9: the `/x` page (private app)

**Files:**
- Create: `webgui/pages/x_post.py` (pure helpers + `render()`)
- Modify: `webgui/main.py` — `MORE_CHILDREN` gets `("/x", "Post to X", "campaign")`;
  `_NAV_COLOR`-style dict gets `"/x": "#1d9bf0"`; a `@_page("/x")` beside `/eod`
- Modify: `webgui/tests/test_shell.py` — add `/x` to the expected route set
- Modify: `webgui/tests/test_no_inline_style.py` — add `x_post.py` to a guarded list
- Modify: `webgui/page_help.py` — a `/x` entry (read its shape first)
- Test: `webgui/tests/test_x_post_page.py`

**Step 1: Failing tests** for the pure helpers:

```python
from pages import x_post as page


def test_preview_uses_the_service_rules():
    text, n = page.preview("Hello", "https://neuralstrike.co", ["options", "#trading"], 4)
    assert text == "Hello\n\nhttps://neuralstrike.co\n#options #trading"
    assert n == 5 + 23 + len("#options #trading") + 3


def test_over_limit_is_flagged():
    _, n = page.preview("z" * 400, "", [], 4)
    assert n <= 280          # fit_text cut it
    assert page.over_limit("z" * 400, "", [])


def test_log_rows_are_newest_first_with_a_link():
    rows = page.log_rows({"posts": [{"at": "2026-09-22T10:00:00-05:00", "kind": "report",
                                      "status": "posted", "url": "https://x.com/i/web/status/1",
                                      "text": "a", "reason": None}]})
    assert rows[0]["kind"] == "Report" and rows[0]["link"].endswith("/1")


def test_command_carries_base64_only_with_an_image():
    cmd = page.command("hi", ["#a"], "", None)
    assert cmd == {"type": "x_post", "args": {"text": "hi", "tags": ["#a"], "link": ""}}
    assert "image_b64" in page.command("hi", [], "", b"PNG")["args"]


def test_the_page_imports_only_the_allow_list():
    import ast, pathlib
    src = (pathlib.Path(page.__file__)).read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import)
             for a in n.names}
    assert not any(m and m.startswith(("services", "shared.notify", "requests")) for m in mods)
```

**Step 2: Run** `(cd webgui && "$PY" -m pytest tests/test_x_post_page.py -q)` — FAIL.

**Step 3: Implement.** Pure helpers: `preview(text, link, tags, max_tags)` →
`(fitted, weighted_len)` via `shared.x_text`; `over_limit(text, link, tags)` → the
UNFITTED weighted length > 280; `log_rows(payload)`; `command(text, tags, link, png)`.

`render()` with the kit (see CLAUDE.md "Apply to a new page"):
- `kit.page()`, `kit.header("Post to X", view="options:x_log")`
- a `ui.textarea` (Text), a link `kit.text_field` pre-filled with
  `https://neuralstrike.co`, a tags `kit.text_field` pre-filled from
  `bus_client.read("options:x_config")` — **no**: the page cannot read
  `notifications.json`. Pre-fill the tags with the literal `"#options #trading"` and
  say in `page_help` that the defaults live in `x.hashtags.marketing`.
- a live counter label (`N / 280`, `text-rose-400` over the limit) repainted on
  every change through `preview`
- image: `ui.upload(auto_upload=True, max_file_size=5_000_000)` accepting
  `image/png,image/jpeg`; keep the bytes in the page's state dict; convert JPEG to
  PNG is NOT needed (X accepts both) — send the bytes as they are and set the
  `image/...` type in the command args (extend `command()` + `run_x_post` with
  `image_type`; default `image/png`).
- `kit.confirm("Post to X?", body=<the fitted text>, confirm_text="Post",
  on_confirm=_send)` behind a primary `kit.button("Post", ...)`; `_send` calls
  `bus_client.request("options", page.command(...))` and `kit.toast("info", "Sent to
  the options service - the log below shows the result")`.
- the log: `kit.table` over `log_rows(bus_client.read("options:x_log"))`, repainted
  with `view_watch.watch_view("options:x_log", ...)`; columns When · Source · Status
  · Text · Link/Reason.

Picking a gallery screenshot is **deferred** (YAGNI until asked): upload covers it.
Record that in the design doc's "Not done".

**Step 4: Run** the new tests, then the whole webgui suite
`(cd webgui && "$PY" -m pytest -q -rf)` — compare the failing SET to the pre-change run.

**Step 5: Commit** `feat(x): the Post to X page`

---

### Task 10: allow-list, docs, manuals

**Files:**
- Modify: `CLAUDE.md` — add `shared.x_text` to the Tier-1 allow-list sentence
  (stdlib only, pinned by `shared/tests/test_x_text.py`), and one invariant line
  under the notification suppression table: *every X post goes through
  `shared/notify/x_post.py`, called only from options_svc; nothing else imports
  tweepy or calls the X API (pinned by `test_nothing_but_x_post_talks_to_x`)*.
  Add `/x` to the Routes table. Nothing else.
- Modify: `docs/CHANGELOG.md` — dated entry.
- Modify: `docs/manuals/` User Guide (the Post to X page + turning X on) and the API
  Reference (the two commands, the three keys). Rebuild with `build_docs.py` if the
  README says the built files are committed.
- Modify: `docs/plans/2026-09-22-x-posting-design.md` — the key names, the
  report-posting move, and "Not done" (gallery picker).
- Modify: `docs/plans/2026-09-17-hourly-trade-idea-post-design.md` — its "No X
  post" line now points here.

**Commit** `docs(x): allow-list, routes, changelog, manuals`

---

### Task 11: verify

1. Run each suite touched: `shared/tests`, `shared/notify`, `services/options_svc`,
   `services/market_svc`, `webgui` — each separately, `-rf`, and compare failing sets.
2. `"$PY" -m pyright` — clean (`shared/bus` etc. unchanged, but confirm).
3. Render both cards (trade idea + report) to the scratchpad and look at them.
4. Local page harness (see memory "Local page harness replaces missing dev"): render
   `/x` against a fake bus, type text, check the counter and the log table.
5. Report to the user: what shipped, that X is **off and dry** until they add keys,
   the exact `notifications.json` block to edit, and that the v2 media endpoint is
   unverified until the first live post. Do NOT promote without asking.
