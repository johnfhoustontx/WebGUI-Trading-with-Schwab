# Approve a sign-in from the phone — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Password typed and the code box left empty sends an ntfy push with three numbers; the owner picks the one on the waiting page and approves it in the app on their already-signed-in phone; the waiting browser then signs itself in.

**Architecture:** A new Tier-1 module `webgui/login_push.py` holds the config accessors, an in-memory store of pending approvals, and the ntfy message builder and publisher. `login_page.attempt` gains a third outcome ("pending") after the password check. `main.py` turns that into a waiting page under `/login` (open) and an approve page at `/approve-sign-in` (gated like every other route).

**Tech Stack:** FastAPI/Starlette routes on NiceGUI's app, `requests` (publish), `secrets`, `shared.config_toml.toml_loader`, pytest + Starlette `TestClient`.

**Spec:** `docs/plans/2026-10-17-login-push-approval-design.md`

## Global Constraints

- The password is always required; the push replaces the 6-digit code only.
- `auth_middleware.OPEN_PATHS` stays exactly `{"/login", "/favicon.ico"}`.
- Approving needs a signed-in session; answers are never counted by the lockout.
- Config `config/login_push.toml`: `enabled = true`, `server = "https://ntfy.sh"`, `ttl_sec = 120`, `max_pending = 5`, `publish_timeout_sec = 5`, `priority = 4`.
- Secrets `NTFY_TOPIC` (required for the push) and `NTFY_TOKEN` (optional bearer) from the environment only.
- Numbers are two digits, 10..99; three distinct ones are offered.
- Pending cookie `ns_pending`: HttpOnly, Secure, SameSite=Lax, host-only, path `/`, max-age `ttl_sec` (main.py's `_COOKIE_KW`).
- Waiting page refreshes with `<meta http-equiv="refresh" content="2">`; no script.
- Copy: push title "Sign in to NeuralStrike?"; form hint "Leave empty to approve on your phone"; refused line "Not approved. Sign in again, or use your authenticator code."; publish failure "The push could not be sent; use your authenticator code."; approve page ended "This sign-in request has ended."
- No `.style()` / inline style rules apply only to NiceGUI pages; these are raw HTML pages and reuse `login_page._CSS`.

## Review Focus

1. Two browsers waiting at once: each collects only its own approval (Task 3 test `test_two_waiting_browsers_collect_only_their_own`).
2. The waiting browser cancels ("type the code instead") and the phone then opens the push: the approve page says the request has ended (Task 3 `test_approve_after_cancel_has_ended`).
3. A webgui restart (store emptied) while a browser waits: the refresh shows the refused line and clears the cookie (Task 3 `test_unknown_pending_cookie_shows_the_form_and_clears_it`).
4. The publish raises or times out: the form comes back with the publish-failure line and no `ns_pending` cookie is set (Task 3 `test_publish_failure_falls_back_to_the_code`).
5. The `next` path stored on a pending approval is the validated one (`main.login_target`), so an approval cannot redirect off-site (Task 3 `test_collect_redirects_to_the_validated_next`).

---

### Task 1: The push module — config, pending store, message

**Files:**
- Create: `webgui/login_push.py`
- Create: `config/login_push.toml`
- Modify: `repo_paths.py` (add `LOGIN_PUSH_TOML = REPO_ROOT / "config" / "login_push.toml"` beside the other `*_TOML` paths)
- Modify: `webgui/config_schema.py` (a `_LOGIN_PUSH` `ConfigFile`, restart `(WEBGUI,)`, one Section with the six keys; append to `FILES`)
- Test: `webgui/tests/test_login_push.py`

**Interfaces:**
- Produces:
  - `DEFAULTS: dict` (the six keys at the top level), `load, reset_cache = toml_loader(LOGIN_PUSH_TOML, DEFAULTS, label="login_push.toml")`
  - `enabled() -> bool`, `server() -> str`, `ttl_sec() -> int`, `max_pending() -> int`, `publish_timeout_sec() -> float`, `priority() -> int` — each falls back to its default for a missing/wrong-typed/out-of-range value (ttl 10..3600, max_pending 1..50, timeout 0.5..30, priority 1..5, server must start `https://`).
  - `topic() -> str | None` (stripped `NTFY_TOPIC`, None if empty), `configured() -> bool` (`enabled() and topic() is not None`).
  - `@dataclass Pending`: `pending_id: str, approval_id: str, number: int, offered: tuple[int, int, int], client: str, keep: bool, next_path: str, created: float, state: str` (`"waiting" | "approved" | "refused"`).
  - `class PendingStore` with `has_room(now=None) -> bool`, `create(*, client, keep, next_path, now=None) -> Pending`, `by_pending(pending_id, now=None) -> Pending | None`, `by_approval(approval_id, now=None) -> Pending | None`, `answer(approval_id, number, approve: bool, now=None) -> str` (`"approved" | "refused" | "ended"`), `collect(pending_id, now=None) -> Pending | None` (removes and returns it only when approved), `drop(pending_id) -> None`, `refuse(pending_id) -> None`. Expired entries (older than `ttl_sec()`) are removed on every call and read as absent.
  - Module store `STORE = PendingStore()` and `reset_store()` (tests).
  - `build_message(p: Pending, *, topic: str, app_url: str) -> dict` — pure; the ntfy JSON.
  - `publish(p: Pending, *, app_url: str, post=None) -> bool` — never raises; `post` defaults to `requests.post`.

- [ ] **Step 1: Write the failing tests** in `webgui/tests/test_login_push.py`:
  - `test_create_offers_three_distinct_two_digit_numbers_including_the_right_one`: over 200 creates, `len(set(p.offered)) == 3`, `p.number in p.offered`, all in `range(10, 100)`; `p.pending_id != p.approval_id`; both `len >= 40`.
  - `test_answer_right_number_approves_and_collect_consumes`: `answer(aid, p.number, True) == "approved"`; `collect(pid).pending_id == pid`; second `collect(pid) is None`.
  - `test_answer_wrong_number_or_deny_refuses`: wrong number → `"refused"`; a fresh one with `approve=False` → `"refused"`; `collect` returns None for both.
  - `test_a_second_answer_has_ended`: after one answer, any answer → `"ended"`.
  - `test_expired_approval_reads_as_absent`: create at `now=0`, `by_pending(pid, now=121) is None`, `answer(aid, n, True, now=121) == "ended"`.
  - `test_has_room_respects_max_pending`: with `max_pending` monkeypatched to 2, two creates then `has_room() is False`; after one expires, `True`.
  - `test_configured_needs_the_topic_and_the_switch` (monkeypatch env `NTFY_TOPIC` and `enabled`).
  - `test_bad_config_values_fall_back` (parametrized over each accessor with a junk value via monkeypatched `load`).
  - `test_message_has_three_view_actions_and_no_pending_id`: `msg["topic"] == topic`, `msg["title"] == "Sign in to NeuralStrike?"`, `msg["priority"] == 4`, `len(msg["actions"]) == 3`, each `action == "view"`, `clear is True`, url starts `f"{app_url}/approve-sign-in?a={p.approval_id}&n="`, the three `n=` values equal `set(p.offered)`, labels are the numbers as text, and `p.pending_id not in json.dumps(msg)`.
  - `test_publish_posts_json_with_the_token_and_timeout` (fake `post` records `url == server()`, `json`, `timeout == publish_timeout_sec()`, `Authorization: Bearer <tok>` only when `NTFY_TOKEN` set) and `test_publish_never_raises` (fake post raises / returns 500 → `False`).
  - `test_every_key_is_in_the_settings_catalogue`: each `DEFAULTS` key appears as a `Field` key in `config_schema.BY_NAME["login_push.toml"]`.
- [ ] **Step 2: Run** `cd webgui; python -m pytest tests/test_login_push.py -q` — expect ImportError / failures.
- [ ] **Step 3: Implement** `webgui/login_push.py`, `config/login_push.toml` (the six keys with one-line comments), `repo_paths.LOGIN_PUSH_TOML`, the `config_schema` entry. Numbers from `secrets.SystemRandom().sample(range(10, 100), 3)`, the right one `secrets.choice` of them, ids `secrets.token_urlsafe(32)`. The store guards its dict with a `threading.Lock`.
- [ ] **Step 4: Run** the new file plus `tests/test_config_schema.py` — expect PASS.
- [ ] **Step 5: Commit** `feat(login): pending approvals and the ntfy push message`.

### Task 2: The attempt's third outcome and the form

**Files:**
- Modify: `webgui/login_page.py` (`AttemptResult`, `attempt`, `render_form`, new `render_card`, `record_success`)
- Test: `webgui/tests/test_login_page.py`

**Interfaces:**
- Consumes: `login_push.configured()`, `login_push.STORE.has_room()`.
- Produces:
  - `AttemptResult` gains `pending: bool = False`; `_PENDING = AttemptResult(False, "", pending=True)`.
  - `attempt(...)` unchanged signature. After the password verified and no trusted device: if `code` is empty/whitespace and `login_push.configured()`: `has_room()` false → `_refuse(client, "too many waiting approvals", now=at, hashed=True)`; else return `_PENDING` (no lockout success yet). Code typed → TOTP as today.
  - `record_success(client: str) -> None` (wraps `_lockout.record_success`).
  - `render_form(*, next_path, error, form_token, push: bool = False)`: with `push`, the code input has no `required` and a `<p class="hint">Leave empty to approve on your phone</p>` under it.
  - `render_card(*, title: str, eyebrow: str, body_html: str, refresh_sec: int | None = None) -> str` — the login page's document and CSS around a pre-escaped body; with `refresh_sec`, a `<meta http-equiv="refresh" content="N">`.

- [ ] **Step 1: Write the failing tests** (in `test_login_page.py`, push configured by monkeypatching `login_push.configured` → True and `login_push.STORE.has_room` → True):
  - `test_empty_code_with_push_configured_is_pending_only_after_the_password`: right password, `code=""` → `pending is True and ok is False`; wrong password, `code=""` → `pending is False`, failure counted.
  - `test_empty_code_without_push_is_refused_as_today` (configured → False) → failure, `pending False`.
  - `test_a_typed_code_still_uses_totp_with_push_configured` → `ok is True`.
  - `test_a_trusted_device_never_goes_pending` (valid remember token, `code=""`) → `ok is True`.
  - `test_no_room_refuses_and_counts` (`has_room` → False) → failure, `lockout_state()` shows one failure for the client.
  - `test_pending_records_no_success` (a prior failure for the client stays recorded after a pending result).
  - `test_form_with_push_drops_required_and_shows_the_hint` / `test_form_without_push_is_unchanged` (`'required' in` the code input tag, no hint).
  - `test_render_card_escapes_nothing_itself_and_adds_refresh_only_when_asked`.
- [ ] **Step 2: Run** `python -m pytest tests/test_login_page.py -q` — expect the new tests to fail.
- [ ] **Step 3: Implement** in `login_page.py` (import `login_push` at module top; it imports nothing from `login_page`).
- [ ] **Step 4: Run** `tests/test_login_page.py tests/test_login_routes.py` — PASS.
- [ ] **Step 5: Commit** `feat(login): an empty code can wait for the phone`.

### Task 3: The routes

**Files:**
- Modify: `webgui/main.py` (`_login_form`, `_login_submit`, new `_approve_form`, `_approve_submit`, constants `PENDING_COOKIE = "ns_pending"`, `APPROVE_ROUTE = "/approve-sign-in"`)
- Test: `webgui/tests/test_login_push_routes.py`

**Interfaces:**
- Consumes: Task 1's store and `publish`; Task 2's `_PENDING`, `record_success`, `render_card`, `render_form(push=)`.
- Produces: routes only.
  - `GET /login` (params `next`, `cancel`): `cancel` present → drop the pending approval named by the cookie, clear it, show the form. Cookie names a `waiting` approval → waiting card (`refresh_sec=2`) with the number in large type and a "Type the code instead" link to `/login?cancel=1`. Cookie names an `approved` one → `collect`, mint session exactly as `_login_submit` does for its `keep` (trusted kind + remember cookie, or plain), `record_success(p.client)`, clear `ns_pending`, 303 to `p.next_path`. Cookie names nothing (refused, expired, unknown) → clear cookie, form with the refused line. No cookie → form as today, `push=login_push.configured()`.
  - `POST /login`: a `pending` result → `p = STORE.create(client=, keep=<box ticked>, next_path=target)`; `ok = await run_in_threadpool(login_push.publish, p, app_url=f"https://{APP_HOST}")`; not ok → `STORE.drop(p.pending_id)`, form with the publish-failure line; ok → 303 to `/login` setting `ns_pending` (max-age `ttl_sec()`).
  - `GET /approve-sign-in?a=&n=` (gated): approval waiting → card "A sign-in from {client} is waiting. Approve the one showing {n}?" with a POST form (`a`, `n`, `form_token` from `issue_form_token()`, buttons `decision=approve` / `decision=deny`, labelled "Approve" / "Not me"); otherwise the ended card.
  - `POST /approve-sign-in` (gated): form token must verify (`login_page.verify_form_token`), else the ended card and nothing changes; `STORE.answer(a, int(n) or None, decision == "approve")`; card "Approved. The other screen signs in within a few seconds." / "Not approved." / ended.

- [ ] **Step 1: Write the failing tests** in `test_login_push_routes.py`, using `test_login_routes.py`'s fixtures pattern (real `main.app`, `https://testserver`, EDGE header, credentials file in tmp), `NTFY_TOPIC` set, `login_push.publish` monkeypatched to record and return True, store reset per test. Two clients: `browser` (signing in) and `phone` (holding a valid session cookie).
  - `test_empty_code_sends_one_push_and_shows_the_number`: POST /login with password, `code=""` → 303 `/login`, `ns_pending` set; GET /login → 200 with the pending's number and `http-equiv="refresh"`; publish called once.
  - `test_phone_approves_and_the_browser_signs_in`: phone GET approve page (200, number shown), POST approve → browser GET /login → 303 to `/desk`, session cookie of kind `session`, `ns_pending` cleared.
  - `test_keep_ticked_gives_the_30_day_session_and_remember_cookie`.
  - `test_wrong_number_or_not_me_refuses_and_the_browser_sees_the_form`.
  - `test_the_approval_link_without_a_session_approves_nothing` (stolen-topic case): a client with no session GETs and POSTs the approve URL → 303 to `/login?next=`; browser still waiting.
  - `test_the_pending_cookie_alone_collects_nothing_while_waiting`: browser GET /login repeatedly → still the waiting card, no session cookie.
  - `test_approve_post_needs_a_form_token`.
  - `test_answers_are_not_counted_by_the_lockout`: after a wrong-number answer, `lockout_state()` has no failure for the phone's client.
  - Review Focus tests: `test_two_waiting_browsers_collect_only_their_own`, `test_approve_after_cancel_has_ended`, `test_unknown_pending_cookie_shows_the_form_and_clears_it`, `test_publish_failure_falls_back_to_the_code`, `test_collect_redirects_to_the_validated_next` (POST with `next=//evil.example` → collect redirects to `/desk`).
  - `test_open_paths_are_unchanged`: `auth_middleware.OPEN_PATHS == {"/login", "/favicon.ico"}`.
- [ ] **Step 2: Run** `python -m pytest tests/test_login_push_routes.py -q` — expect failures.
- [ ] **Step 3: Implement** the routes in `main.py`.
- [ ] **Step 4: Run** `tests/test_login_push_routes.py tests/test_login_routes.py tests/test_auth_covers_every_route.py tests/test_auth_middleware.py` — PASS (the new gated route is covered by the enumeration automatically).
- [ ] **Step 5: Commit** `feat(login): approve a sign-in from the phone`.

### Task 4: Docs and the whole suite

**Files:**
- Modify: `CLAUDE.md` (Tier-1 allow-list: `requests` also for `login_push.publish` to ntfy; config file list gains `login_push`), `docs/manuals/user-guide/user-guide.md` (the sign-in section: setup steps 1-4 from the design, with `python -c "import secrets; print(secrets.token_urlsafe(24))"` as the topic generator — a plan-time call in place of a separate tool), `docs/manuals/reference-guide/reference-guide.md` (Sign out / sign-in paragraph), `docs/manuals/technical-reference/technical-reference.md` (the config table if it lists config files), `docs/CHANGELOG.md`, rebuilt manuals.
- [ ] **Step 1:** Edit the docs.
- [ ] **Step 2:** `python docs/manuals/build_docs.py`; keep only the rebuilt manuals whose source changed.
- [ ] **Step 3:** Full webgui suite `cd webgui; python -m pytest tests -q` and root `python -m pytest tests -q` — PASS (no edits while it runs).
- [ ] **Step 4: Commit** `docs(login): approving a sign-in from the phone`.
