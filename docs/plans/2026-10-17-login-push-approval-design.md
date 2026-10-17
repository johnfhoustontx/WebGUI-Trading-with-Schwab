# Approve a sign-in from the phone (ntfy, number matching) — design

2026-10-17. Asked for by the owner.

## Intent

Signing in to `app.neuralstrike.co` takes the password and the current 6-digit
code from an authenticator app. The owner wants the second step to be their
Android phone instead: the sign-in page shows a two-digit number, the phone
gets a push through the **ntfy** app (already installed) showing three numbers,
and picking the matching one approves the sign-in, which then finishes on the
waiting page by itself.

Since 2026-10-17 a sign-in with **Keep me signed in on this device for 30 days**
ticked lasts 30 days, so this comes up about once a month per browser, and on
any new browser.

## Decided by the owner

- The push replaces **the 6-digit code only**. The password is always typed.
- **Number matching**: the page shows the number; the phone asks for it.
- The push goes through the **ntfy Android app**. Not Telegram, not Discord.
- **Approval happens inside the app, on a phone that is already signed in.**
  Tapping a number in the push opens the app in the phone's browser on a page
  asking "Approve the sign-in showing 47?"; one more tap approves. (Chosen after
  review: approving straight from the notification would let anyone holding the
  password AND the ntfy channel name approve their own sign-in, because a
  public ntfy channel can be read by whoever learns its name, and by the ntfy
  server's operator.)
- The authenticator code keeps working as before. An ntfy outage, a phone out
  of reach, or a phone whose own sign-in has lapsed means: type the code.

## Goals

1. Password typed, code box left empty, Sign in: the page changes to "Approve on
   your phone" with a two-digit number, and the phone gets the push within a
   couple of seconds.
2. On the phone: tap the number shown on the waiting page, then Approve. The
   waiting browser signs itself in within a few seconds, honouring the "keep me
   signed in" box ticked on the form.
3. Picking a wrong number, pressing "Not me", or doing nothing for 2 minutes
   ends that attempt; the waiting page says so and offers the form again.

## Non-goals

- No push replaces the PASSWORD. No passwordless sign-in.
- No Telegram, Discord or Chrome web-push route.
- No self-hosted ntfy server and no paid ntfy channel.
- No list of signed-in devices and no "your 30 days end tomorrow" push.
- A phone cannot approve its own sign-in (it has no session yet); it uses the
  code. Accepted.

## Anti-goals (a failure even if it works)

- **The password plus anything public being enough.** Reading the ntfy channel
  (every push, every link in it) together with the password must still get
  nobody in. That holds because approving needs a signed-in session on the
  owner's phone, which no push carries.
- **A tap the owner did not mean letting someone in.** Number matching plus a
  separate Approve press.
- **Widening the auth gate.** `auth_middleware.OPEN_PATHS` stays `/login` and
  `/favicon.ico`. The approve page is an ordinary gated route.
- **Locking the owner out.** Answers to a push are never counted by the sign-in
  lockout (only a signed-in session can send one), so nobody can use the push
  to lock the owner's address out of the code path either.

## Constraints from the project

- The login page is hand-written HTML with no NiceGUI runtime, deliberately
  (`login_page.py`'s note). The waiting page and the approve page are the same
  kind of page, in the login page's look.
- `login_page.attempt` owns the order of checks; every refusal funnels through
  `_refuse`. The new branch sits after the password check and opens no exit
  that skips the counter.
- Tier 1 may use `requests` only for named purposes (root `CLAUDE.md`); this
  adds one, the publish to ntfy. The allow-list text is updated in the same
  change.
- Tunables go in a `config/*.toml` with a `config_schema.py` entry. Secrets go
  in the stack `.env`, which the webgui unit already loads.
- Under pytest the network is blocked; tests stub the publish.

## How it works

### 1. The attempt

`attempt()` gains a third outcome. Same order as today through the password:

1. throttle, global lock, credentials, form token, password (unchanged);
2. trusted device (remember cookie): accepted, as today;
3. a code was typed: TOTP as today;
4. **no code typed, and the push is configured**: create a pending approval
   and return "approval pending" (neither success nor failure; the lockout's
   success is recorded only when the approval is collected);
5. no code, push not configured: refused as today ("code").

A pending approval therefore always means someone typed the right password.

### 2. The pending approval (in memory)

Held in the webgui process. Each holds: the right number (10-99), two decoys
(distinct, 10-99), a **pending id** and an **approval id** (each 32 random
bytes, URL-safe, independent), the client address, the "keep me signed in"
choice, the `next` path, created-at, and a state: `waiting` · `approved` ·
`refused` · `expired`.

- Lives `ttl_sec` (config, 120 s). A webgui restart drops them; the owner signs
  in again.
- At most `max_pending` (config, 5) at once. Past that the attempt is refused
  through `_refuse` (it follows a correct password, so it is not a lockout
  risk; it stops a password holder flooding the phone).
- Single use: the first answer settles it.
- The **pending id** lives only in an HttpOnly, Secure, SameSite=Lax, host-only
  cookie on the waiting browser (`ns_pending`, max-age = ttl). The **approval
  id** lives only in the push. Collecting a session needs the cookie;
  approving needs the approval id AND a signed-in session.

### 3. The push (ntfy)

One JSON publish to `{server}` with the topic from the environment:

- Title: "Sign in to NeuralStrike?"
- Message: "A sign-in is waiting. Tap the number shown on that screen, then
  Approve. If you did not just sign in, ignore this." plus the client address.
- Priority from config (4, high). Three **view actions**, one per offered
  number, in random order. Each opens
  `https://{APP_HOST}/approve-sign-in?a=<approval id>&n=<number>` in the
  phone's browser, `clear=true`.
- Topic `NTFY_TOPIC`, optional bearer `NTFY_TOKEN`, both from the stack `.env`.
  No topic = the push path is off and the form behaves exactly as today.
- Published off the event loop with a timeout (config, 5 s). A failed publish
  marks the approval `refused` with "The push could not be sent; use your
  authenticator code", logged, never raised.

Leaking the topic reveals that sign-ins happened and from which address, and
gives links that only work in a browser already signed in to the app.

### 4. Routes

Under `/login` (open):

- `GET /login` with a live `ns_pending` cookie: **the waiting page**. It
  refreshes itself every 2 s with `<meta http-equiv="refresh">` (no script).
  - `waiting`: the number, "Approve on your phone", and a link to type the code
    instead (which drops the pending approval and clears the cookie).
  - `approved`: mints the session (30-day kind when "keep" was ticked, plus the
    remember cookie, exactly as the code path does), records the lockout
    success, clears `ns_pending`, 303 to `next`. The approval is consumed.
  - `refused` / `expired` / unknown: clears `ns_pending` and shows the form with
    one line: "Not approved. Sign in again, or use your authenticator code."
- `GET /login` without the cookie: the form, as today.

Gated (an ordinary signed-in route, NOT in `OPEN_PATHS`):

- `GET /approve-sign-in?a=…&n=…`: **the approve page**, in the login page's
  look. For a waiting approval: "A sign-in from <address> is waiting. Approve
  the one showing **47**?" with **Approve** and **Not me**, posting back with a
  form token. Unknown, settled or expired: "This sign-in request has ended."
  A phone with no session is sent to `/login` by the gate as usual.
- `POST /approve-sign-in`: form token checked; Approve with the right number:
  `approved`; Approve with a wrong number, or Not me: `refused`. Plain answer
  page either way. Not counted by the lockout.

### 5. The form

- The code field loses `required` when the push is configured, and gains a
  hint: "Leave empty to approve on your phone".
- Unconfigured, the form is unchanged.

### 6. Configuration

`config/login_push.toml`, read through `toml_loader`, each key in
`config_schema.py`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | off = the form behaves exactly as today even with a topic set |
| `server` | `https://ntfy.sh` | the ntfy server the push is published to |
| `ttl_sec` | 120 | how long an approval waits |
| `max_pending` | 5 | approvals waiting at once |
| `publish_timeout_sec` | 5 | how long the publish may take |
| `priority` | 4 | ntfy priority (1-5) |

`NTFY_TOPIC` and `NTFY_TOKEN` stay in `.env`, never on a page.

## Setup for the owner (goes in the User Guide)

1. A tool prints a random topic name.
2. Add `NTFY_TOPIC=<name>` to the server's `.env`; restart the web app.
3. In the ntfy app on each phone: Subscribe → that topic (server ntfy.sh).
4. Sign in to the app once in each phone's browser with "Keep me signed in"
   ticked, so the phone can approve.

## Left to the builder

- Module layout (a `login_push.py` beside `login_page.py` is expected).
- Markup of the two new pages, in the login page's existing look.
- Log wording; an accepted push sign-in logs an INFO line like the code path's.
- Whether an unknown approval id on the approve page is 404 or 200 with the
  "ended" text.

## Testing expectations

- `attempt` returns pending only after the password verified; never for a
  wrong password; never when unconfigured or `enabled = false`.
- Approve page and POST are gated: no session = redirected, nothing changes.
- Right number + Approve → the waiting browser's next refresh gets the session
  (each kind as ticked) and the cookie is cleared; wrong number / Not me →
  refused; second answer refused; expiry; max_pending.
- The approval id alone, without a session, approves nothing (the stolen-topic
  case, end to end); the pending cookie alone collects nothing while waiting.
- `OPEN_PATHS` unchanged; the lockout records nothing for answers.
- The publish is stubbed; its body has three view actions with distinct
  numbers including the right one, the approval id, and no pending id.
