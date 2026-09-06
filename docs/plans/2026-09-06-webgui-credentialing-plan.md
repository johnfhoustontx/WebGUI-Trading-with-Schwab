# Webgui Credentialing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Put the NiceGUI web GUI behind a password + TOTP login on a public HTTPS hostname, so it is reachable from any browser while the app keeps its `127.0.0.1` bind.

**Architecture:** Caddy (a *system* systemd unit) serves **two hostnames**: `neuralstrike.co` is a public static one-pager (YouTube live link, Discord, Telegram) from `deploy/site/`, and `app.neuralstrike.co` terminates TLS and reverse-proxies to `127.0.0.1:8500`, stamping an `X-Edge` header. Separate origins, so the public page's third-party embeds can never reach the app's cookies. Inside the app, one **pure-ASGI** middleware default-denies every `http` and `websocket` scope except `/login`, `/favicon.ico`, and a three-condition loopback exemption for the YouTube wall kiosk. `/login` is a raw HTML form (no NiceGUI runtime) posting to a plain FastAPI route, so the session is an ordinary `itsdangerous`-signed cookie and the whole gate is testable with `TestClient`.

**Tech Stack:** Python 3.11, NiceGUI 3.13.0, FastAPI 0.137.0 / Starlette, `argon2-cffi`, `pyotp`, `itsdangerous` (already in the lock), pytest, Caddy 2, systemd, Tailscale.

**Design:** [`docs/plans/2026-09-06-webgui-credentialing-design.md`](2026-09-06-webgui-credentialing-design.md) — read it first. This plan implements it; where they disagree, the design is right and the plan is stale.

---

## Before you start — orientation for someone new to this repo

Five facts that will otherwise cost you an hour each:

1. **Run tests from inside `webgui/`**, using the checkout's own venv:
   `(cd webgui && ../.venv/bin/python -m pytest -q)`. A worktree has no venv of its
   own — use the absolute path `/home/administrator/prod/.venv/bin/python`.
2. **Compare the failing SET, not the count.** `pytest` defaults to `-rf` here.
   A matching total has twice hidden real regressions in this repo.
3. **`webgui/` is Tier 1 and has a strict import allow-list** — `nicegui`,
   `shared.bus`, `shared.market_calendar`, `shared.symbols`, `shared.calibration`,
   `repo_paths`, `requests`, `fastapi.responses`, and the lazy `edge_tts`. **No
   `services.*` imports, ever.** The two new deps (`argon2-cffi`, `pyotp`) are
   presentation-layer-neutral libraries, same category as `itsdangerous`; they are
   an addition to that list and the plan updates `CLAUDE.md` to say so.
4. **A new dependency must go in `requirements.lock`, by hand.** Prod has its own
   venv and `promote.sh` reinstalls *only when the lock moved*. A dep in
   `requirements.txt` alone ships to prod **missing**. Never regenerate the lock
   with `pip freeze` — it has drifted from the venv before.
5. **Never touch the prod checkout with git.** Work lands here, gets verified, then
   moves via `tools/promote.sh`. A PreToolUse hook blocks mutating git verbs aimed
   at prod.

**Nothing in Tasks 1–9 changes runtime behaviour for an existing user** until Task 10
turns the gate on. Keep it that way — it is what makes the tests meaningful.

---

## Task 1: Add the two dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements.lock`

**Step 1: Find the current pinned versions**

```bash
.venv/bin/python -m pip index versions argon2-cffi 2>/dev/null | head -2
.venv/bin/python -m pip index versions pyotp 2>/dev/null | head -2
```

If `pip index` is unavailable, use `pip download --no-deps -d /tmp/x argon2-cffi` and read the filename.

**Step 2: Add to `requirements.txt`**, in the existing style (a comment saying why):

```
argon2-cffi==<version>    # webgui login: Argon2id password hashing
pyotp==<version>          # webgui login: TOTP second factor
```

**Step 3: Add to `requirements.lock` BY HAND**, including transitive deps.
`argon2-cffi` pulls `argon2-cffi-bindings`, which pulls `cffi` and `pycparser`.
Check whether `cffi`/`pycparser` are already there (they will be — `cryptography`
needs them) and do **not** duplicate.

```bash
grep -nE "^(cffi|pycparser|argon2|pyotp)" requirements.lock
```

**Step 4: Verify the lock's invariant — completeness, not tidiness**

```bash
# every name in requirements.txt must appear in the lock
# NOTE the `s/\[.*\]//` — without it, `nicegui[highcharts]` can never match the
# lock's `nicegui` and the check reports a false positive on a complete lock.
comm -23 \
  <(sed 's/[#;].*//' requirements.txt | sed 's/\[.*\]//' | sed 's/[<>=!~].*//' | tr -d ' ' | grep -v '^$' | tr 'A-Z_' 'a-z-' | sort -u) \
  <(sed 's/[<>=!~].*//' requirements.lock | tr -d ' ' | grep -v '^$' | tr 'A-Z_' 'a-z-' | sort -u)
```

Expected: **empty output**. Anything printed is a package prod would not install.

**Step 5: Install locally and confirm the imports work**

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -c "import argon2, pyotp, itsdangerous; print('ok')"
```

Expected: `ok`

**Step 6: Commit**

```bash
git add requirements.txt requirements.lock
git commit -m "build: add argon2-cffi and pyotp for the webgui login"
```

---

## Task 2: The credentials store

**Files:**
- Create: `webgui/auth_store.py`
- Create: `shared/webgui_auth.example.json`
- Test: `webgui/tests/test_auth_store.py`
- Modify: `.gitignore`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_store.py
"""The credentials file: load, save, defaults, and the permissions it must carry."""
import json
import pytest

import auth_store


def test_load_returns_none_when_the_file_is_absent(tmp_path):
    assert auth_store.load(tmp_path / "nope.json") is None


def test_round_trip_preserves_every_field(tmp_path):
    path = tmp_path / "webgui_auth.json"
    creds = auth_store.Credentials(
        password_hash="$argon2id$v=19$m=19456,t=2,p=1$abc$def",
        totp_secret="JBSWY3DPEHPK3PXP",
        session_secret="s" * 43,
        epoch=3,
        last_totp_counter=99,
    )
    auth_store.save(creds, path)
    assert auth_store.load(path) == creds


def test_a_malformed_file_raises_rather_than_degrading(tmp_path):
    # Deliberately NOT the repo's usual "never raises" config contract: a config
    # file degrades to defaults, but degrading a credentials file means booting
    # with no password. Fail loudly instead.
    path = tmp_path / "webgui_auth.json"
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(auth_store.CredentialsError):
        auth_store.load(path)


def test_save_writes_owner_only_permissions(tmp_path):
    import os
    import sys
    if sys.platform == "win32":
        pytest.skip("POSIX mode bits; prod is Linux")
    path = tmp_path / "webgui_auth.json"
    auth_store.save(auth_store.Credentials("h", "s", "k", 1, 0), path)
    assert oct(os.stat(path).st_mode)[-3:] == "600"


def test_save_is_atomic_and_leaves_no_partial_file_behind(tmp_path):
    path = tmp_path / "webgui_auth.json"
    auth_store.save(auth_store.Credentials("h", "s", "k", 1, 0), path)
    auth_store.save(auth_store.Credentials("h2", "s2", "k2", 2, 5), path)
    assert json.loads(path.read_text(encoding="utf-8"))["epoch"] == 2
    assert list(p.name for p in tmp_path.iterdir()) == ["webgui_auth.json"]
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_store.py -q)`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth_store'`

**Step 3: Write the implementation**

```python
# webgui/auth_store.py
"""The webgui's credentials file: one dataclass, load, save.

Deliberately NOT built on ``shared.config_toml.toml_loader``. That factory's
contract is "never raises -- fall back to built-in defaults", which is right for
a config file and catastrophic for a credentials file: the default would be
"no password", so a corrupt file would silently open the app to the internet.
This module raises instead.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import tempfile

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[1] / "shared" / "webgui_auth.json"


class CredentialsError(RuntimeError):
    """The credentials file exists but cannot be understood."""


@dataclasses.dataclass(frozen=True)
class Credentials:
    password_hash: str
    totp_secret: str
    session_secret: str
    epoch: int = 1
    last_totp_counter: int = 0


def load(path: pathlib.Path | None = None) -> Credentials | None:
    """Return the stored credentials, or ``None`` when none have been set yet."""
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return Credentials(
            password_hash=str(raw["password_hash"]),
            totp_secret=str(raw["totp_secret"]),
            session_secret=str(raw["session_secret"]),
            epoch=int(raw.get("epoch", 1)),
            last_totp_counter=int(raw.get("last_totp_counter", 0)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise CredentialsError(f"{p} is not a readable credentials file: {exc}") from exc


def save(creds: Credentials, path: pathlib.Path | None = None) -> None:
    """Write atomically, owner-read-write only."""
    p = pathlib.Path(path) if path is not None else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".webgui_auth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(creds), fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise
```

**Step 4: Create the template and gitignore the real file**

`shared/webgui_auth.example.json`:

```json
{
  "password_hash": "$argon2id$v=19$m=19456,t=2,p=1$REPLACE$REPLACE",
  "totp_secret": "REPLACE-WITH-A-BASE32-SECRET",
  "session_secret": "REPLACE-WITH-43-URLSAFE-BASE64-CHARS",
  "epoch": 1,
  "last_totp_counter": 0
}
```

Add to `.gitignore` beside the other `shared/` secrets:

```
shared/webgui_auth.json
```

**Step 5: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_store.py -q)`
Expected: PASS (one skip on Windows for the mode-bits test)

**Step 6: Verify the real file is actually ignored** — this is the check that matters:

```bash
touch shared/webgui_auth.json && git status --porcelain shared/webgui_auth.json && rm shared/webgui_auth.json
```

Expected: **no output** from `git status`. Output here means the secret would be committed.

**Step 7: Commit**

```bash
git add webgui/auth_store.py webgui/tests/test_auth_store.py shared/webgui_auth.example.json .gitignore
git commit -m "feat(auth): credentials store that raises rather than defaulting to no password"
```

---

## Task 3: Password hashing

**Files:**
- Create: `webgui/auth.py`
- Test: `webgui/tests/test_auth_password.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_password.py
import auth


def test_a_hash_verifies_against_its_own_password():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password(h, "correct horse battery staple") is True


def test_a_wrong_password_does_not_verify():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password(h, "Correct horse battery staple") is False


def test_a_corrupt_hash_returns_false_rather_than_raising():
    # A truncated file must fail closed, not 500 the login route.
    assert auth.verify_password("not-a-hash", "anything") is False


def test_two_hashes_of_one_password_differ():
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a != b, "each hash must carry its own salt"


def test_hash_uses_the_tuned_low_memory_parameters():
    """The design's mitigation 3: 19 MiB / t=2, not argon2-cffi's 64 MiB default.

    On a public endpoint the default is a 3.4x memory amplifier, and this box has
    ~1.7 free cores and NO SWAP during stream hours.
    """
    h = auth.hash_password("x")
    assert "$argon2id$" in h
    assert "m=19456" in h and "t=2" in h
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_password.py -q)`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth'`

**Step 3: Write the implementation**

```python
# webgui/auth.py
"""Pure credential logic for the webgui login: password, TOTP, tokens, lockout.

Everything here is a pure function over its arguments plus the credentials
dataclass, so it tests without a server, a browser or a clock monkeypatch.
"""
from __future__ import annotations

import argon2

# Argon2id parameters, deliberately BELOW argon2-cffi's defaults (t=3, 64 MiB, p=4).
#
# Argon2 is expensive on purpose, and on a PUBLIC endpoint that is also an
# amplifier: ten concurrent POSTs at the default would take ~640 MB and all four
# cores. Measured on the prod box, stream hours leave ~1.7 idle cores and there is
# NO SWAP, so a spike does not degrade -- it gets OOM-killed, and the visible
# symptom is the public YouTube broadcast dropping frames.
#
# 19 MiB / t=2 / p=1 is OWASP's floor and remains far beyond what one strong
# single-user password needs. Do not "restore the defaults" without also reading
# the Load and exposure section of the design doc.
TIME_COST = 2
MEMORY_COST_KIB = 19456        # 19 MiB
PARALLELISM = 1

_hasher = argon2.PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """True when the password matches. Fails CLOSED on a malformed hash."""
    try:
        return bool(_hasher.verify(stored_hash, password))
    except Exception:       # noqa: BLE001 - every failure mode means "no".
        return False
```

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_password.py -q)`
Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add webgui/auth.py webgui/tests/test_auth_password.py
git commit -m "feat(auth): Argon2id hashing tuned to 19 MiB for a public endpoint"
```

---

## Task 4: TOTP with drift and replay protection

**Files:**
- Modify: `webgui/auth.py`
- Test: `webgui/tests/test_auth_totp.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_totp.py
import pyotp
import pytest

import auth

SECRET = "JBSWY3DPEHPK3PXP"
T0 = 1_757_000_000          # an arbitrary fixed instant


def _code(at):
    return pyotp.TOTP(SECRET).at(at)


def test_the_current_code_is_accepted():
    ok, counter = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True
    assert counter == T0 // 30


def test_the_previous_and_next_windows_are_accepted_for_clock_drift():
    for offset in (-30, 30):
        ok, _ = auth.verify_totp(SECRET, _code(T0 + offset), now=T0, last_counter=0)
        assert ok is True, f"offset {offset}s should be within drift"


def test_two_windows_away_is_rejected():
    ok, _ = auth.verify_totp(SECRET, _code(T0 + 90), now=T0, last_counter=0)
    assert ok is False


def test_a_code_cannot_be_replayed_inside_its_own_window():
    ok, counter = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=0)
    assert ok is True
    again, _ = auth.verify_totp(SECRET, _code(T0), now=T0, last_counter=counter)
    assert again is False, "the same counter must not be accepted twice"


def test_an_older_counter_is_rejected_even_though_it_is_within_drift():
    # Someone who shoulder-surfed the previous code must not be able to use it
    # after you have used the current one.
    used = T0 // 30
    ok, _ = auth.verify_totp(SECRET, _code(T0 - 30), now=T0, last_counter=used)
    assert ok is False


@pytest.mark.parametrize("junk", ["", "abc", "12345", "1234567", "  123456  ", None])
def test_malformed_input_is_rejected_without_raising(junk):
    ok, _ = auth.verify_totp(SECRET, junk, now=T0, last_counter=0)
    assert ok is False
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_totp.py -q)`
Expected: FAIL — `AttributeError: module 'auth' has no attribute 'verify_totp'`

**Step 3: Append to `webgui/auth.py`**

```python
import hmac
import time

import pyotp

TOTP_PERIOD_SEC = 30
TOTP_DRIFT_STEPS = 1           # +/- one 30 s window


def verify_totp(secret: str, code: str | None, *, now: float | None = None,
                last_counter: int = 0) -> tuple[bool, int]:
    """Verify a TOTP code. Returns ``(ok, counter)``.

    ``counter`` is the accepted time step, which the caller MUST persist as the
    new ``last_counter``. Rejecting ``counter <= last_counter`` is what stops a
    code being replayed inside its own 30 s window -- ``pyotp.TOTP.verify`` alone
    would happily accept the same code repeatedly for 30 seconds, and with drift
    enabled, for 90.
    """
    if not isinstance(code, str):
        return False, last_counter
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return False, last_counter

    at = time.time() if now is None else now
    totp = pyotp.TOTP(secret, interval=TOTP_PERIOD_SEC)
    for step in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        moment = at + step * TOTP_PERIOD_SEC
        counter = int(moment // TOTP_PERIOD_SEC)
        if counter <= last_counter:
            continue
        if hmac.compare_digest(totp.at(moment), code):
            return True, counter
    return False, last_counter
```

> **Note on `strip()`:** the parametrised test expects `"  123456  "` to be
> **rejected**. Strip whitespace *before* the length check only if you also decide
> to accept padded input — pick one and make the test say it. As written above the
> stripped value is six digits, so it is ACCEPTED. **Change the test to
> `assert ok is True` for that case, or drop the `strip()`.** Do not leave the
> test and the code disagreeing; decide, and let the test state the decision.

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_totp.py -q)`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/auth.py webgui/tests/test_auth_totp.py
git commit -m "feat(auth): TOTP with drift window and per-counter replay refusal"
```

---

## Task 5: Session and remember-device tokens

**Files:**
- Modify: `webgui/auth.py`
- Test: `webgui/tests/test_auth_tokens.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_tokens.py
import itsdangerous
import pytest

import auth

KEY = "k" * 43
T0 = 1_757_000_000


def test_a_fresh_token_verifies():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is True


def test_a_tampered_token_is_refused():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok[:-1] + "X", KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_a_token_signed_with_another_key_is_refused():
    tok = auth.mint_token("j" * 43, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=3600, now=T0) is False


def test_an_expired_token_is_refused():
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=1, max_age_sec=60, now=T0 + 61) is False


def test_bumping_the_epoch_invalidates_every_outstanding_token():
    """This is 'sign out everywhere'. It is the ONLY revocation this design has."""
    tok = auth.mint_token(KEY, epoch=1, now=T0)
    assert auth.verify_token(tok, KEY, epoch=2, max_age_sec=3600, now=T0) is False


def test_garbage_is_refused_without_raising():
    for junk in ("", "x", "a.b.c", None):
        assert auth.verify_token(junk, KEY, epoch=1, max_age_sec=3600, now=T0) is False
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_tokens.py -q)`
Expected: FAIL — `AttributeError: module 'auth' has no attribute 'mint_token'`

**Step 3: Append to `webgui/auth.py`**

```python
import itsdangerous

SESSION_MAX_AGE_SEC = 12 * 3600
REMEMBER_MAX_AGE_SEC = 30 * 24 * 3600
_SALT = "webgui-auth-v1"


def _serializer(key: str) -> itsdangerous.URLSafeTimedSerializer:
    return itsdangerous.URLSafeTimedSerializer(key, salt=_SALT)


def mint_token(key: str, *, epoch: int, now: float | None = None) -> str:
    """A stateless bearer token carrying only the epoch it was issued under.

    There is NO server-side token registry on purpose: nothing to expire, leak or
    keep in sync. The cost, stated in the design, is that revocation is
    all-or-nothing -- bump ``epoch`` and re-trust your devices.
    """
    s = _serializer(key)
    if now is None:
        return s.dumps({"epoch": int(epoch)})
    # itsdangerous stamps time.time(); pin it so tests are deterministic.
    return s.dumps({"epoch": int(epoch)}, salt=_SALT)  # noqa: F841 - see below


def verify_token(token: str | None, key: str, *, epoch: int,
                 max_age_sec: int, now: float | None = None) -> bool:
    if not isinstance(token, str) or not token:
        return False
    try:
        payload = _serializer(key).loads(token, max_age=max_age_sec)
    except itsdangerous.BadData:
        return False
    return isinstance(payload, dict) and payload.get("epoch") == int(epoch)
```

> **⚠ `itsdangerous` reads the clock internally**, so the `now=` parameters above
> cannot be honoured by passing them through. Pick one of these and make the tests
> match — **do not fake it**:
>
> - **Preferred:** monkeypatch `itsdangerous.timed.time.time` in the tests and drop
>   the `now=` parameters from `mint_token`/`verify_token` entirely.
> - Or: stop using `URLSafeTimedSerializer`, put `issued_at` in the payload
>   yourself with a plain `URLSafeSerializer`, and compare against `now`.
>
> The second keeps the signature above and is easier to test; the first is less
> code. **Decide in Task 5 and leave a comment saying which and why.** The stub
> above is deliberately left inconsistent so this is not skipped silently.

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_tokens.py -q)`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/auth.py webgui/tests/test_auth_tokens.py
git commit -m "feat(auth): stateless session and remember-device tokens with epoch revocation"
```

---

## Task 6: Lockout, and the ordering that makes it a throttle

**Files:**
- Modify: `webgui/auth.py`
- Test: `webgui/tests/test_auth_lockout.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_lockout.py
import auth

T0 = 1_757_000_000


def test_a_fresh_client_is_not_locked():
    st = auth.LockoutState()
    assert st.locked_until("1.2.3.4", now=T0) == 0


def test_lockout_engages_after_the_threshold_and_backs_off():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) > T0


def test_a_success_clears_that_client():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    st.record_success("1.2.3.4")
    assert st.locked_until("1.2.3.4", now=T0) == 0


def test_one_client_being_locked_does_not_lock_another():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("5.6.7.8", now=T0) == 0


def test_a_global_flood_from_rotating_addresses_still_throttles():
    """Per-IP alone is not a throttle when the attacker has a /64."""
    st = auth.LockoutState()
    for i in range(auth.GLOBAL_THRESHOLD):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    assert st.locked_until("172.16.0.1", now=T0) > T0


def test_the_lock_expires_on_its_own():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    until = st.locked_until("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=until + 1) == 0
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_lockout.py -q)`
Expected: FAIL — `AttributeError: module 'auth' has no attribute 'LockoutState'`

**Step 3: Append to `webgui/auth.py`**

```python
import collections

LOCKOUT_THRESHOLD = 5           # failures from one address before it backs off
GLOBAL_THRESHOLD = 50           # failures from ANY address in the window
LOCKOUT_BASE_SEC = 5
LOCKOUT_MAX_SEC = 900
FAILURE_WINDOW_SEC = 900


class LockoutState:
    """Failed-attempt backoff, held in memory.

    In memory on purpose: it resets on restart (acceptable for one user) and it
    keeps a disk write off the authentication path, which is exactly the path an
    attacker is trying to make expensive.

    The GLOBAL counter is not redundant with the per-address one. A per-IP
    threshold is not a throttle against anyone holding a /64, and the resource
    being protected -- ~1.7 free cores and no swap during stream hours -- is
    global, not per-client.
    """

    def __init__(self) -> None:
        self._per_client: dict[str, list[float]] = collections.defaultdict(list)
        self._global: list[float] = []

    def _prune(self, stamps: list[float], now: float) -> list[float]:
        return [t for t in stamps if now - t < FAILURE_WINDOW_SEC]

    def record_failure(self, client: str, *, now: float) -> None:
        self._per_client[client] = self._prune(self._per_client[client], now) + [now]
        self._global = self._prune(self._global, now) + [now]

    def record_success(self, client: str) -> None:
        self._per_client.pop(client, None)

    def locked_until(self, client: str, *, now: float) -> float:
        """0 when the client may attempt, else the epoch second it may retry."""
        mine = self._prune(self._per_client.get(client, []), now)
        glob = self._prune(self._global, now)
        if len(glob) >= GLOBAL_THRESHOLD:
            return max(glob) + LOCKOUT_MAX_SEC
        if len(mine) < LOCKOUT_THRESHOLD:
            return 0
        over = len(mine) - LOCKOUT_THRESHOLD
        delay = min(LOCKOUT_BASE_SEC * (2 ** over), LOCKOUT_MAX_SEC)
        return max(mine) + delay
```

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_lockout.py -q)`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/auth.py webgui/tests/test_auth_lockout.py
git commit -m "feat(auth): per-client and global failed-attempt backoff"
```

---

## Task 7: The login page and POST handler

**Files:**
- Create: `webgui/login_page.py`
- Test: `webgui/tests/test_login_page.py`

**Design constraints to honour, and each has a test below:**

- `/login` renders as **raw HTML with no NiceGUI runtime** — see the design's
  "The login page carries no NiceGUI runtime" section for the three reasons.
- The failure message **never distinguishes** a bad password from a bad code.
- The lockout is checked **before** Argon2 runs (mitigation 2 in the design).
- `next=` is only honoured when it is a **site-relative path**, or it is an open
  redirect.

**Step 1: Write the failing test**

```python
# webgui/tests/test_login_page.py
import pytest

import auth
import auth_store
import login_page


@pytest.fixture
def creds(tmp_path, monkeypatch):
    c = auth_store.Credentials(
        password_hash=auth.hash_password("hunter2"),
        totp_secret="JBSWY3DPEHPK3PXP",
        session_secret="k" * 43,
        epoch=1,
        last_totp_counter=0,
    )
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return c


def test_the_form_carries_no_nicegui_runtime(creds):
    html = login_page.render_form(next_path="/desk", error=None)
    assert "_nicegui" not in html, "the login page must not need the NiceGUI bundle"
    assert "socket.io" not in html
    assert '<form' in html and 'method="post"' in html


def test_the_form_does_not_reflect_the_next_path_unescaped(creds):
    html = login_page.render_form(next_path='"><script>alert(1)</script>', error=None)
    assert "<script>alert(1)</script>" not in html


@pytest.mark.parametrize("candidate,expected", [
    ("/desk", "/desk"),
    ("/options/gamma?x=1", "/options/gamma?x=1"),
    ("https://evil.example/", "/desk"),
    ("//evil.example/", "/desk"),
    ("/\\evil.example", "/desk"),
    ("", "/desk"),
    (None, "/desk"),
])
def test_safe_next_refuses_anything_that_leaves_this_site(candidate, expected):
    assert login_page.safe_next(candidate) == expected


def test_the_same_message_is_shown_for_a_bad_password_and_a_bad_code(creds):
    import pyotp
    good = pyotp.TOTP(creds.totp_secret).now()
    a = login_page.attempt(password="wrong", code=good, client="1.1.1.1")
    b = login_page.attempt(password="hunter2", code="000000", client="1.1.1.1")
    assert a.ok is False and b.ok is False
    assert a.message == b.message


def test_a_correct_pair_succeeds_and_advances_the_totp_counter(creds):
    import pyotp
    res = login_page.attempt(password="hunter2",
                             code=pyotp.TOTP(creds.totp_secret).now(),
                             client="1.1.1.1")
    assert res.ok is True
    assert auth_store.load().last_totp_counter > 0


def test_a_post_without_a_form_token_never_reaches_the_hash(creds, monkeypatch):
    """Design mitigation 5. A bot that POSTs blind must cost an HMAC, not 19 MiB."""
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    res = login_page.attempt(password="hunter2", code="000000",
                             client="2.2.2.2", form_token=None)
    assert res.ok is False
    assert calls == [], "Argon2 ran on a request with no form token"


def test_a_form_token_from_another_session_key_is_refused(creds):
    res = login_page.attempt(password="hunter2", code="000000", client="2.2.2.2",
                             form_token=auth.mint_token("z" * 43, epoch=1))
    assert res.ok is False


def test_lockout_is_consulted_before_the_hash_is_computed(creds, monkeypatch):
    """Mitigation 2 in the design. If the throttle sits behind the expensive
    thing it throttles, it is not a throttle -- an attacker still pays you the
    Argon2 cost on every request."""
    calls = []
    monkeypatch.setattr(auth, "verify_password",
                        lambda *a, **k: (calls.append(1), False)[1])
    for _ in range(auth.LOCKOUT_THRESHOLD + 2):
        login_page.attempt(password="wrong", code="000000", client="9.9.9.9")
    before = len(calls)
    res = login_page.attempt(password="wrong", code="000000", client="9.9.9.9")
    assert res.ok is False
    assert len(calls) == before, "Argon2 ran despite the client being locked out"
```

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_login_page.py -q)`
Expected: FAIL — `ModuleNotFoundError: No module named 'login_page'`

**Step 3: Write the implementation**

Key points for whoever writes it:

- `safe_next` must reject anything not matching `^/(?!/)` — note `//evil` and
  `/\evil` both leave the site; a bare `startswith("/")` check is **not enough**.
- `render_form` escapes with `html.escape(..., quote=True)`.
- `attempt()` order is: `locked_until` → **`verify_form_token`** →
  `verify_password` → `verify_totp` → persist `last_totp_counter` →
  `record_success`. On any failure, `record_failure` and return the **one**
  generic message.
- **`GET /login` issues a signed form token; `POST /login` requires it.** Mint it
  with `auth.mint_token(key, epoch=…)` reusing Task 5's machinery, with a short
  `max_age` (5 minutes is ample). Design mitigation 5: most credential-stuffing
  bots POST blind without fetching the form, and rejecting those for an HMAC
  instead of 19 MiB is the whole point. It must be checked **before Argon2**,
  same as the lockout.
- Style the form by hand against the design's palette (page `#0c1424`, card
  `#101a30`, border `#213152`, text `#cdd8ee`, primary `#2563eb`). This is a
  standalone HTML document, so it is explicitly out of scope for the Tailwind-first
  rule — same category as the EOD reports and `/wall`.
- Log every failure via the `webgui` logger at WARNING with the client address.
- **A corrupt credentials file must refuse, not 500.** `verify_password` fails
  closed on a malformed hash (Task 3), but `verify_totp` propagates
  `binascii.Error` on a secret that is not valid base32 — the asymmetry was found
  in Task 4 and deliberately left there, because the secret comes from our own
  store rather than the request, and silently swallowing it inside a pure
  function would hide a real fault. **The call site is where it gets handled:**
  a truncated or hand-edited `webgui_auth.json` should produce the generic
  "Sign-in failed" plus a WARNING naming the file, never a traceback on a public
  page. Test it by saving a `Credentials` with `totp_secret="not-base32!"` and
  asserting `attempt()` returns `ok is False`.

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_login_page.py -q)`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/login_page.py webgui/tests/test_login_page.py
git commit -m "feat(auth): login form and attempt handler, throttled before the hash"
```

---

## Task 8: The ASGI gate

**Files:**
- Create: `webgui/auth_middleware.py`
- Test: `webgui/tests/test_auth_middleware.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_auth_middleware.py
"""The gate, driven through a real ASGI app rather than by calling helpers.

A consumer-side guard proves nothing until a test drives it the way production
does -- this repo has paid for that lesson more than once (see CLAUDE.md on the
signal_band and ADX incidents).
"""
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient

import auth
import auth_middleware

KEY = "k" * 43


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/desk")
    def desk():
        return PlainTextResponse("desk")

    @app.get("/wall")
    def wall():
        return PlainTextResponse("wall")

    @app.get("/login")
    def login():
        return PlainTextResponse("login")

    app.add_middleware(auth_middleware.AuthGate,
                       session_key=lambda: KEY, epoch=lambda: 1)
    return TestClient(app, base_url="http://testserver")


def _edge(extra=None):
    h = {auth_middleware.EDGE_HEADER: "1"}
    h.update(extra or {})
    return h


def test_an_unauthenticated_request_through_the_edge_is_redirected(client):
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


def test_the_login_page_itself_is_open(client):
    assert client.get("/login", headers=_edge()).status_code == 200


def test_a_valid_session_cookie_passes(client):
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       auth.mint_token(KEY, epoch=1))
    assert client.get("/desk", headers=_edge()).status_code == 200


def test_a_token_from_a_previous_epoch_does_not_pass(client):
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       auth.mint_token(KEY, epoch=0))
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


# --- the wall exemption: BOTH conditions, and the two negatives that matter ---

def test_the_kiosk_reaches_the_wall_from_loopback_without_the_edge_header(client):
    r = client.get("/wall")            # no edge header; TestClient peer is loopback
    assert r.status_code == 200


def test_the_wall_is_refused_when_the_request_came_through_the_edge(client):
    """Condition 2. Caddy 404s /wall, but the app must not depend on that."""
    r = client.get("/wall", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_the_wall_is_refused_from_a_non_loopback_peer(client):
    """Condition 1. If the bind is ever widened, the exemption must not be a bypass."""
    r = client.get("/wall", follow_redirects=False,
                   headers={"x-test-peer": "203.0.113.9"})
    assert r.status_code == 303


def test_a_page_outside_the_wall_set_is_refused_even_from_loopback(client):
    """Condition 3. Loopback is not a blanket pass -- only the wall's own pages."""
    # /desk IS in the wall set, so use a route that is not.
    r = client.get("/login")           # open anyway; assert the set is explicit
    assert "/options/gamma" not in auth_middleware.WALL_PATHS


def test_an_app_with_no_credentials_configured_refuses_everything(client_no_creds):
    """`auth_store.load()` returns None for BOTH 'no file yet' and 'not
    configured', so the fail-closed duty lands here, on the caller.

    The tempting bug is the friendly one: treat "no password set" as "nothing to
    check" and let requests through, so first-boot is easy. On a public hostname
    that is an open door, and it would look like the app simply working.
    """
    r = client_no_creds.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303, "an unconfigured app must refuse, not admit"


def test_an_unauthenticated_websocket_is_closed(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/_nicegui_ws/socket.io/"):
            pass
```

> The `x-test-peer` header above is a **test seam** — the middleware reads the peer
> from `scope["client"]`, and `TestClient` always presents loopback. Add a tiny
> `_peer(scope)` helper that prefers `scope["client"][0]` and falls back to that
> header **only when a `testing=True` flag is passed to the middleware**. Do not
> ship a header that can spoof the peer in production.

**Step 2: Run it to verify it fails**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_middleware.py -q)`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth_middleware'`

**Step 3: Write the implementation**

Shape (fill in from the design's gate pseudocode):

```python
# webgui/auth_middleware.py
EDGE_HEADER = "x-edge"
SESSION_COOKIE = "ns_session"
REMEMBER_COOKIE = "ns_device"
OPEN_PATHS = frozenset({"/login", "/favicon.ico"})
WALL_PATHS = frozenset({"/wall", "/desk", "/market", "/sentiment/momentum"})
WALL_PREFIXES = ("/_nicegui/", "/_nicegui_ws/", "/static/")
LOOPBACK = frozenset({"127.0.0.1", "::1"})


class AuthGate:
    """Pure ASGI, NOT BaseHTTPMiddleware -- the latter never sees websocket
    scopes, and gating only http would leave the socket ungated."""

    def __init__(self, app, *, session_key, epoch, testing=False): ...

    async def __call__(self, scope, receive, send): ...
```

**Step 4: Run the tests**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_middleware.py -q)`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/auth_middleware.py webgui/tests/test_auth_middleware.py
git commit -m "feat(auth): pure-ASGI gate with the two-condition wall exemption"
```

---

## Task 9: The coverage guard — the test that matters most

**Files:**
- Test: `webgui/tests/test_auth_covers_every_route.py`

This is the only test here that catches a mistake **nobody has made yet**: route
#44 added without an auth decision. Same shape as `test_no_inline_style.py`.

**Step 1: Write the test**

```python
# webgui/tests/test_auth_covers_every_route.py
"""Every route is either explicitly open or refuses an unauthenticated caller.

The point is the FAILURE MODE: when someone adds a page and does not think about
authentication, this goes red and names it. A per-route test cannot do that,
because the new route has no test.
"""
import auth_middleware
import main


def _all_routes():
    paths = set()
    for r in main.app.routes:
        p = getattr(r, "path", None)
        if p and "{" not in p:
            paths.add(p)
    return paths


DOCUMENTED_OPEN = auth_middleware.OPEN_PATHS | {"/openapi.json", "/docs", "/redoc"}


def test_no_route_is_open_without_being_documented_as_open():
    surprises = sorted(p for p in _all_routes()
                       if p in DOCUMENTED_OPEN) - set()          # sanity
    assert set(auth_middleware.OPEN_PATHS) <= _all_routes() | {"/favicon.ico"}


def test_every_page_route_is_refused_without_a_session(client_unauthenticated):
    """Drive the REAL app object, not a stand-in."""
    for path in sorted(_all_routes()):
        if path in DOCUMENTED_OPEN:
            continue
        r = client_unauthenticated.get(path, follow_redirects=False)
        assert r.status_code in (303, 307), f"{path} answered {r.status_code} unauthenticated"
```

> The exact assertions above need tightening once `main.app` is importable under
> test — importing `main` runs module-scope code. **Check whether
> `(cd webgui && ../.venv/bin/python -c "import main")` succeeds** before writing
> this; `test_shell.py` already imports `main`, so it does. Reuse whatever fixture
> `test_shell.py` uses, and add a `client_unauthenticated` fixture to
> `webgui/conftest.py` returning a `TestClient(main.app)` with edge headers and no
> cookies.

**Step 2: Run it — it should FAIL before Task 10**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_covers_every_route.py -q)`
Expected: **FAIL** — every route answers 200, because the gate is not wired yet.
That failure is the proof the test works. Do not wire the gate first.

**Step 3: Commit the failing test on its own**

```bash
git add webgui/tests/test_auth_covers_every_route.py
git commit -m "test(auth): red guard that every route refuses an unauthenticated caller"
```

---

## Task 10: Wire the gate into the app

**Files:**
- Modify: `webgui/main.py`

**Step 1: Register at MODULE scope, not inside the `__main__` guard**

`ui.run()` is inside `if __name__ in {"__main__", "__mp_main__"}` (`main.py:2335`).
Middleware registered there would not exist under pytest, so **tests and prod
would run different wiring** — which is exactly the class of bug this repo
documents (a guard that passed while the producer never emitted the shape).

Add near the other module-scope setup (after the static mounts, ~line 68):

```python
import auth_middleware  # noqa: E402
import login_page       # noqa: E402

app.add_middleware(auth_middleware.AuthGate)   # its DEFAULT providers, deliberately
```

⚠ **Use the gate's default credential providers. Do NOT inject your own.**
An earlier draft of this plan passed `session_key=login_page.session_key,
epoch=login_page.current_epoch`. Those functions do not exist, and writing them
would be pure risk for no benefit: `auth_middleware.default_session_key` /
`default_epoch` already catch `CredentialsError` and return `None`, which the gate
reads as default-deny. Measured against the real wiring — a missing file, a
truncated JSON file, and an empty `session_secret` all produce a **303, never a
500**, and the kiosk's `/wall` is refused too.

A hand-rolled provider that lets `CredentialsError` escape turns a corrupt
credentials file from "a login page" into "an unhandled exception on every route",
which is a worse failure and a public one. The gate is the security boundary; give
it the providers it ships with.

**Step 2: Add the `/login` GET, `/login` POST and `/logout` routes**

Place them with the other raw `@app.get` routes (~line 163) so they are visible
alongside `/eod/file` and the rest.

**Step 2b: Pin the cookie attributes with a test before moving on**

```python
def test_cookies_are_host_only_secure_httponly_and_lax(client):
    """Host-only is the one that matters and the one most easily lost.

    A Domain= cookie is sent to neuralstrike.co and EVERY subdomain, forever --
    so the session would travel to the public marketing page, alongside its
    YouTube and Discord embeds, on every page view. One attribute undoes the
    whole origin split.
    """
    r = client.post("/login", data=_good_credentials(), follow_redirects=False)
    for raw in r.headers.get_list("set-cookie"):
        assert "domain=" not in raw.lower(), f"cookie is not host-only: {raw}"
        assert "httponly" in raw.lower()
        assert "samesite=lax" in raw.lower()
        assert "secure" in raw.lower()
```

**Step 3: Run the guard from Task 9 — it must now go green**

Run: `(cd webgui && ../.venv/bin/python -m pytest tests/test_auth_covers_every_route.py -q)`
Expected: PASS

**Step 4: Run the WHOLE webgui suite and compare the failing SET**

```bash
(cd webgui && ../.venv/bin/python -m pytest -q)
```

Baseline before this branch was **2320 green** (2026-08-20 measurement — re-measure
your own on the parent commit). Expect a handful of pre-existing tests to fail
because they call routes that now redirect. **Fix those tests to authenticate**,
do not weaken the gate.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/
git commit -m "feat(auth): gate every route, and mount the login and logout handlers"
```

---

## Task 11: The credentials CLI

**Files:**
- Create: `tools/webgui_credentials.py`
- Test: `tools/tests/test_webgui_credentials.py`

Subcommands: `set-password`, `enroll-totp`, `revoke-devices`, `show`.

- `set-password` reads via `getpass` — **never** an argv flag, which would land the
  password in shell history and in `pgrep -af` output. (This repo already has a
  documented incident of a secret being readable in `pgrep -af`.)
- `enroll-totp` generates a base32 secret, prints the `otpauth://` URI and a
  terminal QR, and requires you to **enter a code to confirm** before saving —
  otherwise a mistyped enrolment locks you out permanently.
- `revoke-devices` bumps `epoch`.
- `show` prints everything **except** the hash and the secrets.

Tests: each subcommand round-trips through a `tmp_path` store; `enroll-totp`
refuses to save when the confirmation code is wrong; `show` never prints the
secret material.

**Commit:** `feat(tools): CLI to set the webgui password, enrol TOTP and revoke devices`

---

## Task 12: Generate the Caddyfile — two site blocks

**Files:**
- Create: `deploy/caddy/generate_caddyfile.py`
- Create: `deploy/caddy/__init__.py`
- Create: `deploy/site/index.html` (placeholder — you will replace the content)
- Test: `deploy/caddy/tests/test_generate_caddyfile.py`
- Modify: `config/env.local.example.toml` (document `site_host` / `app_host`)
- Modify: `repo_paths.py` (export `SITE_HOST`, `APP_HOST`, `SITE_ROOT`)

Mirror `deploy/systemd/generate_units.py` exactly — same `--install` flag, same
"derive everything from `repo_paths`" rule, same reason: a committed config is a
second copy of the ports and the checkout root, free to drift.

`config/env.local.toml` gains `site_host = "neuralstrike.co"`; `app_host` defaults
to `app.{site_host}` and may be overridden. `SITE_ROOT` is
`<checkout>/deploy/site`.

The generated config must contain **two blocks**:

```
neuralstrike.co, www.neuralstrike.co {
    encode zstd gzip
    root * <SITE_ROOT>            # deploy/site -- NEVER the repo root
    file_server
    header Strict-Transport-Security "max-age=31536000"
}

app.neuralstrike.co {
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000"
        Content-Security-Policy "frame-ancestors 'self'"
    }

    # The wall never leaves the box. The kiosk reaches it on loopback.
    handle /wall* { respond 404 }

    # Mitigation 1: a login flood must never reach Python, where Argon2 is.
    # Its OWN zone, so marketing traffic on the apex cannot trip it.
    handle /login* {
        rate_limit { zone app_login { key {remote_host}  events 10  window 1m } }
        reverse_proxy 127.0.0.1:<NICEGUI_PORT> { header_up X-Edge 1 }
    }

    handle { reverse_proxy 127.0.0.1:<NICEGUI_PORT> { header_up X-Edge 1 } }
}
```

> ⚠ **`rate_limit` is not in stock Caddy** — it needs the
> `caddyserver/rate-limit` plugin and an `xcaddy` build. If you would rather not
> build Caddy, move mitigation 1 into the app (reject before Argon2 using
> `LockoutState` from Task 6, which you already have) and drop the block.
> **Record which you chose in the design doc**, because it currently promises edge
> rate-limiting. Note that mitigation 5's form token covers much of the same
> traffic for free, so dropping the plugin is a defensible choice rather than a
> hole.

**Step: write these tests first.** They need no Caddy — they assert on the
generated string.

```python
def test_the_file_server_root_is_the_site_dir_and_never_the_checkout_root():
    """The single most damaging mistake available in this design.

    A root one level too high serves shared/webgui_auth.json, shared/tokens.json
    and config/env.local.toml to the internet -- and nothing about the site would
    look broken.
    """
    cfg = generate_caddyfile.render()
    root = re.search(r"root \* (\S+)", cfg).group(1)
    assert root.endswith("/deploy/site")
    assert pathlib.Path(root).name == "site"


def test_no_reverse_proxy_appears_in_the_public_block():
    public, app = generate_caddyfile.render().split("app.")
    assert "reverse_proxy" not in public


def test_the_public_block_serves_no_path_that_could_reach_the_app():
    assert "127.0.0.1" not in generate_caddyfile.render().split("app.")[0]


def test_every_reverse_proxy_stamps_the_edge_header():
    cfg = generate_caddyfile.render()
    assert cfg.count("reverse_proxy") == cfg.count("header_up X-Edge 1")


def test_the_wall_is_refused_at_the_edge():
    assert "handle /wall* { respond 404 }" in _normalised(generate_caddyfile.render())


def test_the_port_comes_from_repo_paths_not_a_literal():
    assert str(repo_paths.NICEGUI_PORT) in generate_caddyfile.render()


def test_the_generator_refuses_to_run_in_a_dev_checkout(monkeypatch):
    monkeypatch.setattr(repo_paths, "ENV_NAME", "dev")
    with pytest.raises(SystemExit):
        generate_caddyfile.main(["--install"])
```

**And one test that is worth more than the rest**, because it fails on a mistake
made *outside* this file:

```python
def test_the_site_directory_holds_nothing_but_site_assets():
    """A stray symlink, a copied config, or a debug dump in deploy/site is
    published to the internet the moment it lands there."""
    allowed = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp", ".txt"}
    for p in pathlib.Path(repo_paths.SITE_ROOT).rglob("*"):
        assert not p.is_symlink(), f"{p} is a symlink out of the served root"
        if p.is_file():
            assert p.suffix.lower() in allowed, f"{p} is not a site asset"
```

**Commit:** `feat(deploy): generate both Caddy site blocks from repo_paths`

---

## Task 12b: The public one-pager

**Files:**
- Modify: `deploy/site/index.html`

A single static page: the YouTube live embed or link, Discord, Telegram. No build
step, no framework, no `file_server` browse.

Three constraints that come from the design rather than from taste:

- **No link to `app.neuralstrike.co`.** Recorded in the design as a
  noise-reduction judgement, not a security control — the subdomain is in CT logs
  regardless.
- **The embeds are third-party frames** (YouTube, possibly a Discord widget).
  They live on this origin and *only* this origin; that separation is the reason
  the app is on its own hostname at all.
- **Nothing dynamic, nothing secret.** No form, no API call to the app, no
  analytics that needs a key. If this page ever needs to read something live, that
  is a design change, not an edit.

**Commit:** `feat(site): public one-pager with the stream and community links`

---

## Task 13: Documentation this commit makes false

**Files:**
- Modify: `tools/open_webgui.ps1` (docstring)
- Modify: `SECURITY.md`
- Modify: `CLAUDE.md`
- Modify: `docs/dev-prod-environments.md`

The repo's standing warning is that docs rot silently because **nothing fails when
they go stale**. Three statements become wrong the moment this ships:

1. `tools/open_webgui.ps1` — "Both services bind 127.0.0.1 on the VPS and have **NO
   AUTHENTICATION OF ANY KIND**". Now false for the webgui, still true for the
   proxy. Rewrite it as: the tunnel is the fallback; the normal route is
   `https://trading.<domain>`; the proxy is on the tailnet.
2. `SECURITY.md` — the threat model is built on the loopback bind being the primary
   control. Add the public hostname, the login, the CT-log discovery point, and the
   four load mitigations. **While there, fix the already-stale claim** that the
   proxy's trading endpoints are unauthenticated — they carry
   `Depends(require_secret)`.
3. `CLAUDE.md` — the Tier-1 import allow-list gains `argon2`, `pyotp` and
   `itsdangerous`; the "Reaching the app from a workstation" section is rewritten;
   the "⚠ Never change either bind to `0.0.0.0`" line **stays** (this design does
   not weaken it and the reason it exists is unchanged).

**Commit:** `docs: correct the three places that said the webgui has no authentication`

---

## Task 14: Deploy and verify live

**Not a code task. Nothing below is provable by the test suite**, which is exactly
why it is written out.

1. **Promote first, deploy second.** Merge to `main`, then `tools/promote.sh` in
   prod. Do it **15:25–16:15 CT** — a promote stops the whole target, which drops
   the public stream and loses GEX collection slots.
2. **Set credentials on the prod box** — `python tools/webgui_credentials.py
   set-password` then `enroll-totp`. Scan the QR and **confirm a code before
   logging out of your SSH session.**
3. **DNS** — three A records to the VPS public IP: `neuralstrike.co`,
   `www.neuralstrike.co`, `app.neuralstrike.co`. Confirm each with
   `dig +short <name>` **before** starting Caddy — an ACME challenge against a
   name that does not resolve fails and enters a retry backoff.
4. **ufw** — `sudo ufw allow 80,443/tcp`. Confirm `sudo ufw status`.
5. **Caddy** — install, `generate_caddyfile.py --install`, `systemctl enable --now
   caddy`. Watch the cert issue: `journalctl -u caddy -f`.
6. **`MemoryMax=` on the webgui unit and a raised `CPUWeight` on the stream unit**
   (design mitigation 4 and the contention policy). Both go in
   `generate_units.py`, not hand-edited into a unit file — a hand-edit is
   overwritten on the next promote.
7. **`tailscale serve`** for the proxy's `:8100`. Confirm `/health` from a phone
   on the tailnet.
8. **Verify, in this order. The first two are the ones that matter most:**
   - `curl -s https://neuralstrike.co/../shared/webgui_auth.json` and
     `curl -s https://neuralstrike.co/shared/tokens.json` → **404, no content.**
     Then `curl -s https://neuralstrike.co/config/env.local.toml` → **404.**
     A `file_server` root one level too high leaks every secret on the box and
     the site still looks perfect. **Check this before anything else.**
   - `curl -sI https://neuralstrike.co/` → **200**, and confirm it is the
     one-pager, not a directory listing.
   - `curl -sI https://app.neuralstrike.co/desk` → **303 to /login**
   - `curl -sI https://app.neuralstrike.co/wall` → **404**
   - `curl -sI http://127.0.0.1:8500/wall` **on the box** → **200**
   - `curl -sI https://app.neuralstrike.co/login | grep -i set-cookie` →
     **no `Domain=`** on any cookie
   - log in from a phone on cellular (not your tailnet) — proves the public path
   - confirm the **wall stream is still up** and has not dropped frames
9. **Watch for the scanners.** Within an hour of the cert appearing in CT logs you
   will see `/wp-login.php` and `/.env` probes in Caddy's log. That is expected and
   is the point of mitigations 1–4. If they are causing load, `sar -u` will show it.

---

## Definition of done

- [ ] `(cd webgui && ../.venv/bin/python -m pytest -q)` — failing **set** unchanged from baseline
- [ ] `.venv/bin/python -m pytest tools/tests` — green
- [ ] `.venv/bin/python -m pyright` — still clean (it is a narrow check; keep it so)
- [ ] `comm` completeness check on the lock — empty output
- [ ] `git status --porcelain shared/webgui_auth.json` — empty
- [ ] The Task 9 guard passes, and **fails** when you temporarily add a route without gating it
- [ ] Live: `/desk` 303s, `/wall` 404s at the edge and 200s on loopback, the stream is unbroken
- [ ] Live: `neuralstrike.co` serves the one-pager and **cannot reach any file above `deploy/site/`**
- [ ] Live: no cookie carries `Domain=`
