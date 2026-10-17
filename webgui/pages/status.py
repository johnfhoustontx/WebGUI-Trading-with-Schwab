"""System Status page — at-a-glance health of every component in the stack.

A pure-webgui page (imports only ``nicegui`` + ``bus_client``/``proxy`` + the
shared ``repo_paths``), it probes each tier of the 3-tier architecture and tells
you whether everything is up:

* **Tier 3 — Memurai** (Redis backbone, :6379) via a ``PING``.
* **Tier 1 — schwab-proxy** (:8100) via its ``/health`` (also surfaces whether a
  Schwab token is loaded).
* **Tier 2 — the five domain services** (sentiment/options/portfolio/trade/
  market, :8210–8213 and :8215) via each service's ``/health`` probe.
* **Tier 1 — webgui** itself (it's serving this page, so it's up by definition).
* **Tier 1 — webgui_live**, the PUBLIC read-only screens: a peer process on its
  own port and origin, probed over HTTP. Down, it is named here and nowhere
  else — it is absent from the health fan-out behind the rail badge and the
  chime, because the public site falling over is not a trading-stack alarm.

Below the component checks it shows a **data-freshness** table: for each domain
it reads the representative cache view's version + timestamp, so you can tell a
service is not just *up* but actively *publishing* (a service can answer
``/health`` while its scheduler is wedged). BOTH blocking halves — the component
sweep and the seven freshness probes — run off-thread via
``nicegui.run.io_bound``; the page auto-refreshes and has a manual Refresh.

Since the Phase 6 kit migration the frame is ``pages/ui_kit.py``: one header
line, two regions (the cards and the freshness table, each with its own wait),
and the palette in place of the page's own colours. ⚠ The header's stamp is
driven BY HAND — see the note in :func:`render`.

The pure builders (status wording/colors, overall rollup, age formatting,
target/freshness layout) are unit-tested in ``webgui/tests/test_status.py``; the
network/redis probes are thin and verified by screenshot.
"""
import datetime as _dt
import math
import subprocess
import time as _time
from urllib.parse import parse_qs, urlparse

import requests
from nicegui import run, ui

import alerts
import bus_client
import proxy
import shell as _shell
from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard, guard_async
from repo_paths import (
    ENV_NAME,
    IS_DEV,  # noqa: F401 - not read here; test_status patches it by name
    MEMURAI_PORT,
    NICEGUI_LIVE_PORT,
    NICEGUI_LIVE_URL,
    NICEGUI_PORT,
    NICEGUI_URL,
    OWNS_PROXY,
    PROXY_PORT,
    PROXY_PUBLIC_URL,
    PROXY_URL,
    REPO_ROOT,
    SERVICE_PORTS,
    SERVICE_URLS,
)

# Service-probe timeout — short so a dead service fails fast on the status sweep.
_HTTP_TIMEOUT = 2.5

# The proxy's OWN re-login page (its Step-1/2/3 form), as a BROWSER reaches it -
# which is why it is PROXY_PUBLIC_URL (the tailnet address) and not PROXY_URL,
# where 127.0.0.1 would mean the viewer's own device. Until 2026-10-11 the
# Authorize button opened this and nothing else, so a viewer who could not reach
# the tailnet could not sign in at all. The button now opens the dialog below,
# which needs only the app; this stays as the dialog's second way in.
AUTH_URL = f"{PROXY_PUBLIC_URL}/auth"

# ── the Sign in to Schwab dialog ─────────────────────────────────────────────
SIGN_IN_TITLE = "Sign in to Schwab"
SIGN_IN_BODY = ("Schwab's sign-in lasts 7 days. Renewing it takes three steps, "
                "all in this browser, and works on a phone.")
SIGN_IN_STEP_1 = "1. Log in at Schwab. It opens in a new tab."
SIGN_IN_STEP_2 = ("2. After you log in, Schwab sends that tab to a page that "
                  "will not load. That is expected. Copy the page's whole "
                  "address: on a phone, tap the address bar, then its copy "
                  "button.")
SIGN_IN_STEP_3 = ("3. Come back to this tab and press Paste and sign in. Do it "
                  "straight away: the code in the address lasts about 30 "
                  "seconds.")
SIGN_IN_PASTE_BUTTON = "Paste and sign in"
SIGN_IN_PASTE_LABEL = "Or paste the address here and press Authorize"
SIGN_IN_PASTE_EXAMPLE = "https://127.0.0.1:8182/?code=..."
SIGN_IN_PASTE_EMPTY = "Paste the address first."
SIGN_IN_CLIPBOARD_REFUSED = ("This browser would not let the app read what you "
                             "copied. Press and hold the box below, choose "
                             "Paste, and it signs in.")
# ``/status?signin=1`` opens the dialog as the page loads. It is the link the
# sign-in warning push carries (tools/token_watch.py), and the dialog writes it
# into the address bar while it is open: a phone that drops the app's
# connection while you are away logging in at Schwab reloads the page when you
# come back, and the reload lands in the dialog rather than on a bare board.
SIGN_IN_QUERY = "signin"
# A paste puts the whole address in at once; typing adds a character at a time.
# A jump of at least this many characters with a code in it signs in without
# waiting for Authorize, because the code's 30 seconds are already running.
_PASTE_JUMP_CHARS = 20
# Reads the clipboard IN THE BROWSER, inside the tap, which is what lets a phone
# allow it; the server only ever sees the text the browser hands back.
_PASTE_JS = ("() => { const c = navigator.clipboard; "
             "if (!c || !c.readText) { emit({text: null}); return; } "
             "c.readText().then((t) => emit({text: t}), "
             "() => emit({text: null})); }")
_URL_WITH_SIGN_IN_JS = (f"history.replaceState(null, '', location.pathname + "
                        f"'?{SIGN_IN_QUERY}=1')")
_URL_WITHOUT_SIGN_IN_JS = "history.replaceState(null, '', location.pathname)"
SIGN_IN_PASTE_NO_CODE = ("That address has no code in it. Copy the whole "
                         "address of the page Schwab sent you to after the "
                         "login; it contains code=.")
SIGN_IN_RETRY = ("The code lasts about 30 seconds and works once. Log in again "
                 "and paste the new address straight away.")
SIGN_IN_NO_LINK = ("The proxy did not give a Schwab login link. Check that the "
                   "proxy is up, then try again.")
SIGN_IN_DONE = "Signed in to Schwab. The sign-in lasts 7 days."
SIGN_IN_TAILNET = "Or open the proxy's own sign-in page (Tailscale only)"

# The freshness table's row rule. The palette's own hairline, not Tailwind's
# ``border-gray-700``: a warm grey rule on a navy page reads as a smudge, and a
# neutral is SURFACE wherever it lives.
_CARD_BORDER = theme.THEME["palette"]["card_border"]

# A domain's published cache is considered "stale" past this age (services that
# publish on a timer should refresh well inside this). Trade is on-demand, so its
# row is allowed to be old without flagging.
_STALE_AFTER_SEC = 600

# Per-domain representative cache view + whether it is published on a schedule
# (scheduled → stale-after applies) or only on-demand (never flagged stale).
_FRESHNESS = [
    ("Sentiment", "sentiment:composite", True),
    ("Options · Scanner", "options:scan", True),
    ("Options · Gamma collector", "options:gex_status", True),
    ("Portfolio", "portfolio:positions", True),
    ("Trade (on-demand)", "trade:analysis", False),
    ("Market Dashboard", "market:dashboard", True),
]


# ── pure builders (unit-tested) ──────────────────────────────────────────────
def component_targets():
    """The ordered list of components the status sweep probes.

    Each entry is ``{"key", "label", "tier", "kind", "url"}``. ``kind`` drives
    how the probe is performed: ``memurai`` (Redis ping), ``proxy`` /
    ``service`` (HTTP ``/health``), ``auth`` (Schwab OAuth token validity, read
    from the proxy ``/health``), ``self`` (the webgui, always up), ``peer`` (a
    sibling web process on this box -- an HTTP liveness probe, no ``/health``).

    The proxy entry also carries ``owned`` and says so in its label: dev borrows
    PROD's proxy on :8100, so its card has no Restart button (see
    :func:`restart_spec`) and the label is what explains the absence.
    """
    targets = [
        {"key": "memurai", "label": "Memurai (Redis backbone)", "tier": "Tier 3",
         "kind": "memurai", "url": f"redis://127.0.0.1:{MEMURAI_PORT}"},
        {"key": "proxy", "owned": OWNS_PROXY,
         "label": ("schwab-proxy (market data / auth)" if OWNS_PROXY
                   else "schwab-proxy (shared — owned by prod)"),
         "tier": "Tier 1", "kind": "proxy", "url": PROXY_URL},
        {"key": "schwab_auth", "label": "Schwab Authorization (OAuth)",
         "tier": "Tier 1", "kind": "auth", "url": AUTH_URL},
    ]
    svc_labels = {
        "sentiment": "sentiment_svc (composite + rotation)",
        "options": "options_svc (scan / gamma / paper)",
        "portfolio": "portfolio_svc (holdings + live P&L)",
        "trade": "trade_svc (on-demand analysis)",
        "market": "market_svc (macro-ticker dashboard)",
        "news": "news_svc (public news feeds)",
        "blog": "blog_svc (site blog drafts + entries)",
    }
    for domain, label in svc_labels.items():
        url = SERVICE_URLS.get(domain)
        if url:
            targets.append({"key": domain, "label": label, "tier": "Tier 2",
                            "kind": "service", "url": url})
    targets.append({"key": "webgui", "label": "webgui (this app)", "tier": "Tier 1",
                    "kind": "self", "url": NICEGUI_URL})
    # The PUBLIC read-only screens (webgui/live_main.py) -- a PEER of this app,
    # not a dependency of it. It is a service on this box like any other, so it
    # gets a card and a Restart button; but it is deliberately absent from
    # ``alerts.unhealthy_keys`` (main.py's 2s health fan-out, which reads
    # SERVICE_URLS), so a dead public origin never puts a warning badge on the
    # rail or chimes. Down, it is named here and nowhere else.
    targets.append({"key": "webgui_live",
                    "label": "webgui_live (public live screens)",
                    "tier": "Tier 1", "kind": "peer", "url": NICEGUI_LIVE_URL})
    return targets


def status_word(up):
    """'Online' / 'Offline' / 'Checking…' for up True / False / None."""
    return "Checking…" if up is None else ("Online" if up else "Offline")


def status_color(up):
    """Quasar color token for an up state (green / red / grey)."""
    return "grey" if up is None else ("positive" if up else "negative")


def status_icon(up):
    """Material icon name for an up state."""
    return "hourglass_empty" if up is None else (
        "check_circle" if up else "cancel")


def overall_status(results):
    """Roll component probe results up into a single banner verdict.

    ``results`` is the list of probed component dicts (each with ``up`` +
    ``label``). Returns ``{"all_up", "down", "text", "color"}``.
    """
    known = [r for r in results if r.get("up") is not None]
    down = [r["label"] for r in known if not r.get("up")]
    if not known:
        return {"all_up": False, "down": [], "color": "grey",
                "text": "Checking components…"}
    if not down:
        return {"all_up": True, "down": [], "color": "positive",
                "text": f"All systems operational — {len(known)} components online."}
    n = len(down)
    return {"all_up": False, "down": down, "color": "negative",
            "text": f"{n} component{'s' if n != 1 else ''} down: " + ", ".join(down)}


def age_text(ts, now):
    """Human 'time ago' for an ISO-8601 timestamp relative to ``now`` (aware dt).

    Returns '—' when ``ts`` is missing/unparseable. Buckets: seconds, minutes,
    hours, then days.
    """
    if not ts:
        return "—"
    try:
        when = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return "—"
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    secs = (now - when).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 90 * 60:
        return f"{int(secs // 60)}m ago"
    if secs < 36 * 3600:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def is_stale(ts, now, scheduled, max_age_sec=_STALE_AFTER_SEC):
    """True if a scheduled view's timestamp is older than ``max_age_sec``.

    On-demand views (``scheduled=False``) are never stale; a missing timestamp
    on a scheduled view counts as stale (nothing has been published).
    """
    if not scheduled:
        return False
    if not ts:
        return True
    try:
        when = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (now - when).total_seconds() > max_age_sec


def freshness_row(label, view, version, ts, now, scheduled):
    """Build one data-freshness display row.

    Returns ``{"label", "view", "version", "age", "stale", "present"}``.
    """
    present = version is not None
    return {
        "label": label,
        "view": f"cache:{view}",
        "version": version if present else "—",
        "age": age_text(ts, now) if present else "no data yet",
        # Per-view threshold (alerts.stale_after) so a slow-cadence view like the
        # 15-min options:scan isn't flagged stale between its scans — matches the
        # app-wide toast watcher (main.py) so both surfaces agree.
        # ``expects_updates`` folds into ``scheduled`` for the same reason: a view
        # whose publisher only runs during the session is not "scheduled" right
        # now, so outside the session its age is not evidence of anything. Without
        # it this table, the nav badge and the drawer's status card all reported a
        # dead scanner from Friday's close to Monday's open.
        "stale": present and is_stale(
            ts, now, scheduled and alerts.expects_updates(view, now),
            alerts.stale_after(view, now)),
        "present": present,
    }


def auth_status(health):
    """Map a proxy ``/health`` dict to ``(up, detail)`` for the Schwab Auth card.

    * ``up=None`` — can't tell (proxy down / no health) → grey, not counted.
    * ``up=False`` — manual re-auth needed: no token, or the **refresh** token is
      expired (the proxy can't silently recover from that).
    * ``up=True`` — authorized. An expired **access** token is still ``True`` because
      the proxy auto-refreshes it from a valid refresh token.
    """
    if not health or not health.get("up"):
        return (None, "proxy down — can't check authorization")
    if not health.get("has_token"):
        return (False, "no token — authorization required")
    # Schwab REJECTING the token outranks our stamped expiry. Observed live
    # 2026-08-22: the proxy reported refresh_token_expired False for over an
    # hour — the stamp said 7 days left — while every refresh came back
    # invalid_grant and no market data flowed. This card rendered "authorized"
    # throughout. `.get` keeps an older proxy (no such field) reading as before.
    if health.get("refresh_token_rejected"):
        return (False, "refresh token rejected by Schwab — re-authorization required")
    if health.get("refresh_token_expired"):
        return (False, "refresh token expired — re-authorization required")
    if health.get("token_expired"):
        return (True, "access token expired — proxy auto-refreshes")
    left = sign_in_left_text(health.get("refresh_token_hours_left"))
    if left is not None:
        return (True, f"authorized — sign-in lasts another {left}")
    return (True, "authorized — token valid")


def sign_in_left_text(hours):
    """'6 days 22 hours' / '23 hours' / 'under an hour', or None when ``hours``
    is not a reading (missing, text, a bool, NaN, negative). Whole hours,
    rounded DOWN: time left must never read as more than there is."""
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        return None
    if not math.isfinite(hours) or hours < 0:
        return None
    whole = int(hours)
    if whole < 1:
        return "under an hour"
    days, rest = divmod(whole, 24)
    parts = []
    if days:
        parts.append(f"{days} day" + ("" if days == 1 else "s"))
    if rest:
        parts.append(f"{rest} hour" + ("" if rest == 1 else "s"))
    return " ".join(parts)


def sign_in_paste_problem(pasted):
    """Why the pasted text cannot be a sign-in, or None when it can.

    Checked here so an empty box or the wrong address is answered at once and
    in the dialog, without asking the proxy (which checks again)."""
    text = pasted.strip() if isinstance(pasted, str) else ""
    if not text:
        return SIGN_IN_PASTE_EMPTY
    try:
        codes = parse_qs(urlparse(text).query).get("code", [])
    except ValueError:
        codes = []
    if not (codes and codes[0]):
        return SIGN_IN_PASTE_NO_CODE
    return None


def sign_in_failure_text(error):
    """The proxy's own words for a refused sign-in, then what to do next."""
    return f"{error} {SIGN_IN_RETRY}"


def wants_sign_in(query):
    """Whether the page's address asks for the sign-in dialog (``?signin=1``).

    ``query`` is the request's query parameters, any mapping; anything else is
    no. Only the key's presence with a true-looking value counts, so a stray
    ``?signin=0`` does not open it."""
    try:
        value = query.get(SIGN_IN_QUERY)
    except Exception:  # noqa: BLE001 - not a mapping: not a request for it
        return False
    return isinstance(value, str) and value.strip().lower() in ("1", "true", "yes")


def clipboard_text(args):
    """The text the Paste button's browser handler sent, or None when the
    browser refused to read the clipboard (or sent nothing usable)."""
    text = args.get("text") if isinstance(args, dict) else None
    return text if isinstance(text, str) and text.strip() else None


def looks_pasted(before, after):
    """Whether the box just received a whole sign-in address in one go.

    True for a jump of :data:`_PASTE_JUMP_CHARS` or more that leaves an address
    with a code in it - a paste, which signs in at once. Typing, and editing an
    address already there, never does."""
    before = before if isinstance(before, str) else ""
    if not isinstance(after, str):
        return False
    return (len(after) - len(before) >= _PASTE_JUMP_CHARS
            and sign_in_paste_problem(after) is None)


def _page_query():
    """This page's query parameters, or ``{}`` where there is no request (the
    page harness, a test). Never raises."""
    try:
        request = ui.context.client.request
        return request.query_params if request is not None else {}
    except Exception:  # noqa: BLE001 - no request is no deep link
        return {}


# ── restart (pure spec/command builders + thin spawn) ────────────────────────
def restart_spec(target):
    """What to restart for a Status card, or ``None`` when it is not restartable.

    Returns ``{"kind": "unit", "title": ..., "name": ...}``. ``name`` is the
    systemd unit SUFFIX and is consumed verbatim by :func:`restart_command`, so
    there is no translation table between card keys and unit names -- the same
    reasoning that put the symbol universe in one config file.

    ``peer`` is the PUBLIC live-screens process (``webgui/live_main.py``). It is
    restartable in every environment, including dev: unlike the proxy and Redis
    below, nothing about it is shared -- each environment binds its own offset
    port and runs its own process, so a restart here reaches only this checkout.

    Not restartable:

    * **auth** / unknown -- the auth card's action is Authorize, not a restart.
    * **proxy when this environment does NOT own it.** Dev runs no proxy of its
      own (one rotating Schwab OAuth refresh token, so there can be only one)
      and borrows prod's, so a dev checkout's ``PROXY_PORT`` *is* prod's and
      this button would bounce the LIVE stack's market data mid-session.
    * **redis, in every environment.** It is a *system* unit -- ``systemctl
      --user`` cannot reach it without root -- and one server serves both
      environments, separated by logical DB rather than by a second install.
      Restarting it from either side takes the other's bus down. Dev already
      withheld this; prod now does too, which is the honest position rather
      than a regression.
    """
    kind = target.get("kind")
    key = target.get("key")
    if kind == "proxy":
        if not OWNS_PROXY:
            return None
        return {"kind": "unit", "title": f"Schwab Proxy :{PROXY_PORT}",
                "name": "proxy"}
    if kind == "service":
        if key not in SERVICE_PORTS:
            return None
        return {"kind": "unit", "title": f"{key}_svc :{SERVICE_PORTS[key]}",
                "name": f"{key}_svc"}
    if kind == "self":
        return {"kind": "unit", "title": f"Web GUI :{NICEGUI_PORT}",
                "name": "webgui"}
    if kind == "peer":
        return {"kind": "unit", "title": f"Live Screens :{NICEGUI_LIVE_PORT}",
                "name": "webgui_live"}
    return None


UNIT_PREFIX = f"trading-{ENV_NAME}-"


def restart_command(spec):
    """argv to restart a component's systemd **user** unit (``None`` -> ``None``).

    ``--user`` is load-bearing: it is what lets this app restart its own siblings
    with no polkit rule and no sudoers entry. A system-unit equivalent would
    require handing root to a network-facing process.
    """
    if not spec:
        return None
    return ["systemctl", "--user", "restart", f"{UNIT_PREFIX}{spec['name']}"]


def _do_restart(target):
    """Spawn the restart for ``target``. Returns False if not restartable."""
    cmd = restart_command(restart_spec(target))
    if cmd is None:
        return False
    subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    return True


# ── the restart confirm ──────────────────────────────────────────────────────
# Ten of the twelve cards carry a Restart, and every one of them bounces a live
# process on this box: the seven services, the proxy when this checkout owns it,
# the PUBLIC live screens, and this web app itself. Two of those ten cost more
# than the component they name, so the dialog says which one you are about to
# pay. The sentences are module constants rather than literals inside render()
# so a test can pin them without copying prose.
RESTART_CONFIRM = "Restart now"
RESTART_BODY = ("It stops and starts again, and should be back online within "
                "about 15 seconds.")
RESTART_SELF_BODY = ("This web app restarts. The page you are looking at "
                     "disconnects and needs a reload in a few seconds.")
RESTART_PROXY_BUSY_BODY = (
    "The market is open. Every service reads market data through the proxy, so "
    "the whole stack loses it for about 15 seconds — after the close is safer.")


def restart_body(target, market_busy=False):
    """The one sentence the Restart confirm shows for ``target``. PURE.

    ``market_busy`` is "a restart now costs live work" — see :func:`_market_busy`
    — and only the PROXY reads it: it is the one component every service depends
    on, so restarting it mid-session is a stack-wide market-data outage rather
    than one card going amber. Restarting the web app is special for the other
    reason: it takes the page that asked down with it.
    """
    kind = target.get("kind")
    if kind == "self":
        return RESTART_SELF_BODY
    if kind == "proxy" and market_busy:
        return RESTART_PROXY_BUSY_BODY
    return RESTART_BODY


def _market_busy():
    """Whether a restart now costs live work — regular hours OR the gamma
    collection window.

    Settings → Configuration's own predicate, IMPORTED rather than restated: a
    second copy here would be free to drift from the warning that tab already
    shows for the same reason. Imported lazily, so this page carries no
    module-level dependency on a Settings tab. It never raises — an unreadable
    calendar degrades to False, which is right: an unknown session must not put
    a market-hours warning on a dialog at midnight, and the base sentence still
    names the outage.
    """
    from pages.config_editor import market_busy
    return market_busy()


# ── network / redis probes (thin, screenshot-verified) ───────────────────────
def service_detail(body) -> str:
    """The detail line for a healthy service card, given its ``/health`` JSON.

    Surfaces ``degrades_total`` - the count of swallowed exceptions since the
    process started (``services/_degrade``). One degrade is noise; a few hundred
    in one domain is a bug that would otherwise never appear anywhere, which is
    the whole reason the counter exists. Zero is the normal case and stays a
    plain "healthy" rather than putting "- 0 degraded" on every card.

    PURE + defensive: a service predating the counter has no key, and a garbled
    value must not break the card, so anything that is not a positive int is
    treated as no reading.
    """
    n = body.get("degrades_total") if isinstance(body, dict) else None
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        text = "healthy"
    else:
        text = f"healthy - {n} degraded"
    for extra in (dead_letter_text(body), scheduler_age_text(body)):
        if extra:
            text = f"{text} · {extra}"
    return text


def proxy_detail(health) -> str:
    """The proxy card's detail line, from its ``/health`` dict.

    Says which mode the local market-data store is in, and how many store
    faults fell back to a plain fetch since the proxy started: those were
    visible only on ``/stats/api_calls``, which no screen reads. An older proxy
    has no ``market_store`` block and a garbled one adds nothing. PURE."""
    if not isinstance(health, dict) or not health.get("up"):
        sc = health.get("status_code") if isinstance(health, dict) else None
        return f"HTTP {sc}" if sc else "unreachable"
    text = "healthy"
    block = health.get("market_store")
    if isinstance(block, dict) and isinstance(block.get("mode"), str):
        text = f"{text} · market data store: {block['mode']}"
        n = block.get("faults")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            text = f"{text} · {n} store fault{'' if n == 1 else 's'}"
    return text


def dead_letter_text(body) -> str:
    """"3 commands not run", or "" at zero. These are commands the service could
    not run: a handler failed, or a restart stranded them. They are kept for a
    person to read and are never re-run. PURE."""
    n = body.get("dead_letters") if isinstance(body, dict) else None
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return ""
    return f"{n} command{'' if n == 1 else 's'} not run"


def redis_detail(up: bool, persistence: str) -> str:
    """The Redis card's detail line. Some operator and day state lives only in
    Redis (alert cooldowns, the day's X post count), so whether it is saved to
    disk at all is said here. An unknown reading adds nothing rather than
    claiming either. PURE."""
    if not up:
        return "no PING response"
    if persistence in ("snapshots", "append-only file"):
        return f"PING ok · saved to disk ({persistence})"
    if persistence == "none":
        return "PING ok · NOT saved to disk: a restart empties it"
    return "PING ok"


def scheduler_age_text(body) -> str:
    """"scheduler ran 18 s ago", or "" when the service runs no scheduler or has
    not made a pass yet. The process answering is not the service working; this
    is the number that says the loop is still going round. PURE."""
    age = body.get("scheduler_last_tick_age_s") if isinstance(body, dict) else None
    if isinstance(age, bool) or not isinstance(age, (int, float)) or age != age or age < 0:
        return ""
    if age < 90:
        return f"scheduler ran {int(age)} s ago"
    return f"scheduler ran {int(age // 60)} min ago"


def unhealthy_detail(status_code, body) -> str:
    """The detail line for a service card that is NOT up. A service that
    answered 200 and said why it is not up gets its own reason; anything else is
    the HTTP status. PURE."""
    reason = body.get("reason") if isinstance(body, dict) else None
    if status_code == 200 and isinstance(reason, str) and reason.strip():
        return reason.strip()
    return f"HTTP {status_code}"


def _probe_one(target, proxy_health=None):
    """Probe a single component target → a result dict with ``up`` + ``detail``.

    ``proxy_health`` lets the caller pass a single shared ``proxy.health()`` result
    so the ``proxy`` and ``auth`` cards don't each pay the HTTP round-trip.
    """
    kind = target["kind"]
    out = dict(target)
    try:
        if kind == "self":
            out.update(up=True, detail="serving this page")
        elif kind == "memurai":
            up = bus_client.ping()
            out.update(up=up, detail=redis_detail(
                up, bus_client.persistence() if up else "unknown"))
        elif kind == "proxy":
            h = proxy_health if proxy_health is not None else proxy.health()
            out.update(up=bool(h.get("up")), detail=proxy_detail(h))
        elif kind == "peer":
            # An HTTP probe, never a TCP connect: a dead accept loop stays bound
            # and passes a connect, which is how a promote once left prod
            # serving no UI at all.
            #
            # ``/favicon.ico`` because NiceGUI registers it unconditionally and
            # it renders no page -- the live app's own routes each read ten
            # cache views, which is not what a liveness probe should cost. ANY
            # HTTP answer counts as up: the question here is whether the process
            # is alive and speaking HTTP, not whether one route exists, so a
            # future NiceGUI that moved this path reports the truth rather than
            # a false Offline.
            resp = requests.get(f"{target['url']}/favicon.ico",
                                timeout=_HTTP_TIMEOUT)
            out.update(up=True,
                       detail="serving" if resp.status_code == 200
                       else f"serving (HTTP {resp.status_code})")
        elif kind == "auth":
            h = proxy_health if proxy_health is not None else proxy.health()
            up, detail = auth_status(h)
            out.update(up=up, detail=detail)
        else:  # service
            resp = requests.get(f"{target['url']}/health", timeout=_HTTP_TIMEOUT)
            body = resp.json() if resp.status_code == 200 else {}
            up = resp.status_code == 200 and body.get("up") is True
            out.update(up=up,
                       detail=service_detail(body) if up
                       else unhealthy_detail(resp.status_code, body))
    except Exception as exc:  # noqa: BLE001 — a probe must never raise.
        out.update(up=False, detail=f"unreachable ({type(exc).__name__})")
    return out


def _sweep():
    """Probe every component (blocking) → list of result dicts. Runs off-thread.

    ``proxy.health()`` is fetched once and shared by the proxy + auth cards.
    """
    health = proxy.health()
    return [_probe_one(t, proxy_health=health) for t in component_targets()]


def _freshness_rows():
    """The published-data table's rows: seven ``read_meta`` probes → seven
    :func:`freshness_row` dicts. BLOCKING — runs off the event loop.

    Separated from the painting for exactly that reason. ``_sweep`` has always
    crossed ``run.io_bound`` and this half never did, so every 15 s tick, in
    every open tab, seven blocking bus reads ran on the loop.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    rows = []
    for label, view, scheduled in _FRESHNESS:
        ver, ts = bus_client.read_meta(view)
        rows.append(freshness_row(label, view, ver, ts, now, scheduled))
    return rows


# ── render ───────────────────────────────────────────────────────────────────
# A restart is followed by a re-sweep sooner than the 15s auto-refresh, and the
# wait on the components region is held until that sweep lands.
_RESTART_RESWEEP_SEC = 7.0
# The auto-refresh cadence, and the first tick that fills the page.
_AUTO_REFRESH_SEC = 15.0
_FIRST_SWEEP_SEC = 0.1


def render():
    """The health board: one header line with a hand-driven stamp, the overall
    verdict, a card per component, then the published-data freshness table."""
    state = {"results": [], "busy": False, "hold_until": 0.0,
             "restart_target": None}

    with kit.page():
        head = kit.header("System Status")
        # ⚠ THE STAMP IS DRIVEN BY HAND, and the kit is deliberately NOT edited.
        # kit.header binds its stamp to a bus view's ``:ts`` side key — the time
        # a publisher last confirmed that view current. This page has no such
        # view: its freshness is a PROBE, and nothing publishes "the stack was
        # last swept at". So the header is built with no ``view=`` (which is
        # also what keeps it from registering a poll timer that would have
        # nothing to read), the stamp is shown once here, and ``_refresh`` sets
        # it from the sweep's own clock through the same public ``set_stamp``.
        # Putting this ``set_visibility`` inside ``kit.set_stamp`` was
        # considered and rejected: a kit edit inside a page phase changes every
        # migrated page's behaviour for one page's benefit.
        head.stamp.set_visibility(True)
        head.set_stamp(None)     # "Waiting for data", never a made-up time
        with head.actions:
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh")
        banner = ui.row().classes("w-full")
        region = kit.region("Checking components…")
        comps = region.content
        kit.section_title("Published data freshness")
        fresh = kit.region("Reading the published views…")

    def _paint_banner(ov):
        """The one-line verdict.

        The three raw Quasar fills (``bg-green-2`` / ``bg-red-2`` /
        ``bg-grey-3`` — the last use of that palette anywhere in the app) are
        gone. A component DOWN is the kit's one attention band, which is what a
        notice is for; all-up and still-checking are a plain line in the
        palette's own positive and muted colours, because a full-width band
        across the page for "nothing is wrong" is noise.
        """
        banner.clear()
        with banner:
            if ov["color"] == "negative":
                kit.notice(ov["text"], icon="warning")
                return
            cls = theme.TXT_POS if ov["all_up"] else theme.MUTED
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("check_circle" if ov["all_up"]
                        else "hourglass_empty").classes(cls)
                ui.label(ov["text"]).classes(f"text-sm font-medium {cls}")

    def _paint_components():
        results = state["results"]
        _paint_banner(overall_status(results))

        comps.clear()
        # The Schwab Authorization probe renders MERGED into the proxy card (one
        # line, Authorize + Restart side by side) — it still counts as its own
        # component in the overall banner above.
        auth = next((r for r in results if r.get("kind") == "auth"), None)
        with comps:
            for r in results:
                if r.get("kind") == "auth":
                    continue  # merged into the schwab-proxy card
                up = r.get("up")
                merged_auth = auth if r.get("kind") == "proxy" else None
                # The card icon folds a failed authorization into an otherwise-up
                # proxy so the red flag is visible at a glance.
                icon_up = up
                if merged_auth is not None and up and merged_auth.get("up") is False:
                    icon_up = False
                with ui.card().classes("w-full"):
                    # ⚠ The row WRAPS. It used to be ``no-wrap`` around a fixed
                    # 280px button slot, which cannot fit a 375px phone — and
                    # the failure is a card that scrolls sideways rather than
                    # anything that looks broken. The buttons still line up in
                    # one column, and never depended on that width: the label
                    # column grows, so the slot is flush right on every card and
                    # Restart — always last — sits in the same place on each.
                    with ui.row().classes("items-center gap-3 w-full flex-wrap"):
                        ui.icon(status_icon(icon_up)).props(
                            f"color={status_color(icon_up)}").classes("text-2xl")
                        with ui.column().classes("gap-0 grow min-w-[180px]"):
                            ui.label(r["label"]).classes(
                                f"font-medium {theme.LABEL}")
                            ui.label(f"{r['tier']} · {r['url']}").classes(
                                f"text-xs {theme.MUTED}")
                        with ui.column().classes("gap-0 items-end shrink-0"):
                            ui.badge(status_word(up)).props(
                                f"color={status_color(up)}")
                            ui.label(r.get("detail", "")).classes(
                                f"text-xs {theme.MUTED}")
                            if merged_auth is not None:
                                ui.label(f"Auth: {merged_auth.get('detail', '')}") \
                                    .classes("text-xs " + (
                                        theme.TXT_NEG
                                        if merged_auth.get("up") is False
                                        else theme.MUTED))
                        with ui.row().classes("shrink-0 min-w-[120px] justify-end "
                                              "items-center gap-2 flex-wrap"):
                            if merged_auth is not None and merged_auth.get("up") is not None:
                                kit.button(
                                    "Re-authorize" if merged_auth.get("up") else "Authorize",
                                    kind="secondary", icon="login",
                                    on_click=_authorize_clicked)
                            if restart_spec(r) is not None:
                                kit.button("Restart", kind="danger",
                                           icon="restart_alt",
                                           on_click=lambda t=r: _restart_clicked(t))

    def _paint_freshness(rows):
        """Draw rows ALREADY READ (see :func:`_freshness_rows`)."""
        fresh.content.clear()
        with fresh.content:
            for row in rows:
                with ui.row().classes(
                        f"items-center gap-3 w-full flex-wrap py-1 "
                        f"border-b border-[{_CARD_BORDER}] last:border-b-0"):
                    ok = row["present"] and not row["stale"]
                    color = "positive" if ok else (
                        "grey" if not row["present"] else "warning")
                    ui.icon("circle").props(f"color={color}").classes("text-xs")
                    ui.label(row["label"]).classes(
                        f"font-medium min-w-[220px] {theme.LABEL}")
                    ui.label(row["view"]).classes(
                        f"text-xs min-w-[200px] {theme.MUTED}")
                    ui.label(f"v{row['version']}").classes(
                        f"text-xs min-w-[60px] {theme.MUTED}")
                    age = row["age"] + (" · STALE" if row["stale"] else "")
                    ui.label(age).classes(
                        "text-sm " + (theme.TXT_WARN if row["stale"]
                                      else theme.MUTED))

    @guard
    def _restart_clicked(target):
        """Ask first. Every Restart on this page bounces a live process, and
        two of them cost more than the card they sit on — see
        :func:`restart_body`. The dialog is RETITLED here rather than rebuilt:
        one dialog, built at this function's own level, is what keeps it out of
        ``comps`` (cleared every 15 s, and a dialog deletes itself with its
        slot) and stops one being left behind in the page on every click."""
        state["restart_target"] = target
        restart_dlg.title.text = f"Restart {target['label']}?"
        restart_dlg.body.text = restart_body(target, market_busy=_market_busy())
        restart_dlg.open()

    def _restart_confirmed():
        """The confirmed restart. Returning anything but ``False`` closes the
        dialog, which is right for all four outcomes here: each is reported by
        a toast or a spinner, and none of them is something a second press of
        the same button would answer differently."""
        target = state["restart_target"]
        try:
            ok = _do_restart(target)
        except Exception as exc:  # noqa: BLE001 — surface, never crash the page.
            kit.toast("error", f"Couldn't restart {target['label']}: {exc}")
            return
        if not ok:
            # ⚠ UNREACHABLE from the UI: the Restart button is only built where
            # ``restart_spec(target)`` is not None, which is exactly what
            # ``_do_restart`` re-checks. Kept deliberately — it is the one
            # branch that would otherwise report a restart that never happened.
            kit.toast("warn", f"{target['label']} can't be restarted from here.")
            return
        if target.get("kind") == "self":
            # Restarting the web app kills THIS page — no point re-sweeping it,
            # and no point spinning a wait that can never be dismissed.
            kit.toast("warn", "Restarting the web app — this page will "
                              "disconnect. Reload in a few seconds.")
            return
        # ⚠ The wait goes on the REGION, never on the Restart button:
        # ``_paint_components`` rebuilds every card every 15 s and would take
        # the button's busy timer with it, mid-wait. ``hold_until`` is what
        # stops the auto-refresh — which can land a second after the click —
        # taking the spinner down eight seconds into a fifteen-second wait.
        region.busy.show(f"Restarting {target['label']}… it should come back "
                         "online within ~15s.")
        state["hold_until"] = _time.monotonic() + _RESTART_RESWEEP_SEC
        ui.timer(_RESTART_RESWEEP_SEC, _refresh, once=True)

    # Built ONCE, at render()'s own level, and deliberately not ephemeral: the
    # same dialog serves all ten restartable cards, retitled per click. AFTER
    # ``_restart_confirmed``, so the name is bound when the kit calls it.
    restart_dlg = kit.confirm("Restart?", "", confirm_text=RESTART_CONFIRM,
                              danger=True, on_confirm=_restart_confirmed)

    # ── signing in to Schwab again ───────────────────────────────────────────
    # In the app, so it needs no tailnet and no tunnel: the browser goes to
    # Schwab for the login, and the web app hands the redirect address to the
    # proxy over loopback. The dialog is built ONCE at this level, like the
    # restart one, because ``comps`` is cleared every 15 s.
    @guard_async
    async def _authorize_clicked(_e=None):
        if _shell.is_public():
            return
        info = await run.io_bound(proxy.auth_info)
        if not info:
            kit.toast("error", SIGN_IN_NO_LINK)
            return
        state["auth_url"] = info["auth_url"]
        state["pasted"] = ""
        paste.value = ""
        sign_in_error.set_visibility(False)
        sign_in_dlg.open()
        ui.run_javascript(_URL_WITH_SIGN_IN_JS)

    @guard
    def _sign_in_toggled(e):
        if not e.value:
            ui.run_javascript(_URL_WITHOUT_SIGN_IN_JS)

    @guard
    def _open_schwab(_e=None):
        url = state.get("auth_url")
        if url:
            ui.navigate.to(url, new_tab=True)

    def _show_sign_in_problem(text):
        sign_in_error.text = text
        sign_in_error.set_visibility(True)

    @guard_async
    async def _paste_clicked(e):
        text = clipboard_text(getattr(e, "args", None))
        if text is None:
            _show_sign_in_problem(SIGN_IN_CLIPBOARD_REFUSED)
            return
        state["pasted"] = text
        paste.value = text
        await sign_in_dlg.run()

    @guard_async
    async def _paste_changed(e):
        before, state["pasted"] = state.get("pasted", ""), e.value
        if looks_pasted(before, e.value):
            await sign_in_dlg.run()

    async def _sign_in_confirmed():
        """Returning ``False`` keeps the dialog open with the reason shown."""
        if _shell.is_public():
            return None
        problem = sign_in_paste_problem(paste.value)
        if problem is None:
            kit.set_busy(sign_in_dlg.confirm)
            try:
                # Blocking for as long as Schwab takes: never on the event loop.
                answer = await run.io_bound(proxy.auth_exchange,
                                            paste.value.strip())
            finally:
                kit.set_busy(sign_in_dlg.confirm, False)
            ok, message = answer or (False, "The web app is stopping.")
            if ok:
                state["pasted"] = ""
                paste.value = ""
                kit.toast("ok", SIGN_IN_DONE)
                ui.timer(0.1, _refresh, once=True)
                return None
            problem = sign_in_failure_text(message)
        _show_sign_in_problem(problem)
        return False

    sign_in_dlg = kit.confirm(SIGN_IN_TITLE, SIGN_IN_BODY,
                              confirm_text="Authorize",
                              on_confirm=_sign_in_confirmed)
    sign_in_dlg.dialog.on_value_change(_sign_in_toggled)
    with sign_in_dlg.content:
        ui.label(SIGN_IN_STEP_1).classes(f"text-sm {theme.LABEL}")
        kit.button("Log in to Schwab", kind="secondary", icon="open_in_new",
                   on_click=_open_schwab)
        ui.label(SIGN_IN_STEP_2).classes(f"text-sm {theme.LABEL}")
        ui.label(SIGN_IN_STEP_3).classes(f"text-sm {theme.LABEL}")
        kit.button(SIGN_IN_PASTE_BUTTON, kind="primary", icon="content_paste") \
            .on("click", _paste_clicked, js_handler=_PASTE_JS)
        paste = kit.text_field(SIGN_IN_PASTE_LABEL, width="w-full",
                               placeholder=SIGN_IN_PASTE_EXAMPLE,
                               on_change=_paste_changed)
        sign_in_error = ui.label("").classes(f"text-sm {theme.TXT_NEG}")
        sign_in_error.set_visibility(False)
        ui.link(SIGN_IN_TAILNET, AUTH_URL, new_tab=True).classes(
            f"text-xs {theme.MUTED}")

    @guard_async
    async def _refresh():
        if state["busy"]:
            return
        state["busy"] = True
        kit.set_busy(refresh_btn)
        try:
            # ``or []`` / ``or ()``: run.io_bound answers None once the app is
            # stopping, and an empty board is the honest reading of that.
            state["results"] = await run.io_bound(_sweep) or []
            _paint_components()
            _paint_freshness(await run.io_bound(_freshness_rows) or ())
            # LAST, and only on a sweep that completed: a stamp that advanced
            # after a failed probe would report a health check that never
            # happened. A raise leaves the previous time standing and ageing.
            head.set_stamp(_dt.datetime.now(_dt.timezone.utc).isoformat())
        finally:
            kit.set_busy(refresh_btn, False)
            fresh.busy.hide()
            if _time.monotonic() >= state["hold_until"]:
                region.busy.hide()
            state["busy"] = False

    refresh_btn.on_click(_refresh)

    # The first sweep's wait, then the sweep, then the auto-refresh. Both
    # regions spin until the first one lands; after that the board stays
    # readable and only Refresh's own button spins.
    _paint_components()          # the "Checking components…" verdict
    region.busy.show()
    fresh.busy.show()
    ui.timer(_FIRST_SWEEP_SEC, _refresh, once=True)
    ui.timer(_AUTO_REFRESH_SEC, _refresh)
    # The push's link, and a phone's reload mid-sign-in: open the dialog.
    if wants_sign_in(_page_query()):
        ui.timer(_FIRST_SWEEP_SEC, _authorize_clicked, once=True)
