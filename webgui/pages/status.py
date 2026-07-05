"""System Status page — at-a-glance health of every component in the stack.

A pure-webgui page (imports only ``nicegui`` + ``bus_client``/``proxy`` + the
shared ``repo_paths``), it probes each tier of the 3-tier architecture and tells
you whether everything is up:

* **Tier 3 — Memurai** (Redis backbone, :6379) via a ``PING``.
* **Tier 1 — schwab-proxy** (:8100) via its ``/health`` (also surfaces whether a
  Schwab token is loaded).
* **Tier 2 — the five domain services** (sentiment/options/portfolio/trade/driver,
  :8210–8214) via each service's ``/health`` probe.
* **Tier 1 — webgui** itself (it's serving this page, so it's up by definition).

Below the component checks it shows a **data-freshness** table: for each domain
it reads the representative cache view's version + timestamp, so you can tell a
service is not just *up* but actively *publishing* (a service can answer
``/health`` while its scheduler is wedged). Heavy/blocking probes run off-thread
via ``nicegui.run.io_bound``; the page auto-refreshes and has a manual Refresh.

The pure builders (status wording/colors, overall rollup, age formatting,
target/freshness layout) are unit-tested in ``webgui/tests/test_status.py``; the
network/redis probes are thin and verified by screenshot.
"""
import datetime as _dt
import subprocess

import requests
from nicegui import run, ui

import bus_client
import proxy
from pages.options.theme import BTN_3D
from repo_paths import (
    MEMURAI_PORT,
    NICEGUI_URL,
    PROXY_PORT,
    PROXY_URL,
    REPO_ROOT,
    SERVICE_PORTS,
    SERVICE_URLS,
)

# Service-probe timeout — short so a dead service fails fast on the status sweep.
_HTTP_TIMEOUT = 2.5

# Schwab OAuth re-login page served by the proxy (Step-1/2/3 login flow). Opened
# in a new tab by the "Authorize" button on the Schwab Authorization card.
AUTH_URL = f"{PROXY_URL}/auth"

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
    ("Driver", "driver:approvals", False),
]


# ── pure builders (unit-tested) ──────────────────────────────────────────────
def component_targets():
    """The ordered list of components the status sweep probes.

    Each entry is ``{"key", "label", "tier", "kind", "url"}``. ``kind`` drives
    how the probe is performed: ``memurai`` (Redis ping), ``proxy`` /
    ``service`` (HTTP ``/health``), ``auth`` (Schwab OAuth token validity, read
    from the proxy ``/health``), ``self`` (the webgui, always up).
    """
    targets = [
        {"key": "memurai", "label": "Memurai (Redis backbone)", "tier": "Tier 3",
         "kind": "memurai", "url": f"redis://127.0.0.1:{MEMURAI_PORT}"},
        {"key": "proxy", "label": "schwab-proxy (market data / auth)",
         "tier": "Tier 1", "kind": "proxy", "url": PROXY_URL},
        {"key": "schwab_auth", "label": "Schwab Authorization (OAuth)",
         "tier": "Tier 1", "kind": "auth", "url": AUTH_URL},
    ]
    svc_labels = {
        "sentiment": "sentiment_svc (composite + rotation)",
        "options": "options_svc (scan / gamma / paper)",
        "portfolio": "portfolio_svc (holdings + live P&L)",
        "trade": "trade_svc (on-demand analysis)",
        "driver": "driver_svc (morning approvals)",
    }
    for domain, label in svc_labels.items():
        url = SERVICE_URLS.get(domain)
        if url:
            targets.append({"key": domain, "label": label, "tier": "Tier 2",
                            "kind": "service", "url": url})
    targets.append({"key": "webgui", "label": "webgui (this app)", "tier": "Tier 1",
                    "kind": "self", "url": NICEGUI_URL})
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
        "stale": present and is_stale(ts, now, scheduled),
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
    if health.get("refresh_token_expired"):
        return (False, "refresh token expired — re-authorization required")
    if health.get("token_expired"):
        return (True, "access token expired — proxy auto-refreshes")
    return (True, "authorized — token valid")


# ── restart (pure spec/command builders + thin spawn) ────────────────────────
def restart_spec(target):
    """How to (re)start a component, or ``None`` if it can't be restarted here.

    * **proxy / service** → a ``script`` spec: free the port then launch the
      venv python on the entry script (services wait for the proxy first).
    * **memurai** → a ``service`` spec: start the Memurai Windows service.
    * **self** (the webgui) / unknown → ``None`` — the app can't restart itself.
    """
    kind = target.get("kind")
    key = target.get("key")
    if kind == "proxy":
        return {"kind": "script", "title": f"Schwab Proxy :{PROXY_PORT}",
                "kill_port": PROXY_PORT, "wait_port": 0,
                "script": r"schwab-proxy\schwab_proxy.py"}
    if kind == "service":
        port = SERVICE_PORTS.get(key)
        if port is None:
            return None
        # Services wait for the proxy (:8100) before starting.
        return {"kind": "script", "title": f"{key}_svc :{port}",
                "kill_port": port, "wait_port": PROXY_PORT,
                "script": rf"services\{key}_svc\app.py"}
    if kind == "memurai":
        return {"kind": "service", "title": "Memurai", "service": "Memurai"}
    return None


def restart_command(spec):
    """argv to spawn for a restart spec (``None`` → ``None``).

    A ``script`` spec opens its own console (``cmd /c start`` → ``cmd /k``) running
    ``tools\\restart_one.bat`` so the restarted component has live logs and the
    spawn is fully detached from the web app. A ``service`` spec starts the
    Windows service via PowerShell.
    """
    if not spec:
        return None
    if spec["kind"] == "service":
        return ["powershell", "-NoProfile", "-Command",
                f"Start-Service -Name '{spec['service']}'"]
    return ["cmd", "/c", "start", spec["title"], "cmd", "/k", "call",
            r"tools\restart_one.bat", str(spec["kill_port"]),
            str(spec["wait_port"]), spec["script"]]


def _do_restart(target):
    """Spawn the restart for ``target`` (detached). Returns False if not restartable."""
    cmd = restart_command(restart_spec(target))
    if cmd is None:
        return False
    subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    return True


# ── network / redis probes (thin, screenshot-verified) ───────────────────────
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
            out.update(up=up, detail="PING ok" if up else "no PING response")
        elif kind == "proxy":
            h = proxy_health if proxy_health is not None else proxy.health()
            up = bool(h.get("up"))
            if up:
                detail = "healthy"
            else:
                sc = h.get("status_code")
                detail = f"HTTP {sc}" if sc else "unreachable"
            out.update(up=up, detail=detail)
        elif kind == "auth":
            h = proxy_health if proxy_health is not None else proxy.health()
            up, detail = auth_status(h)
            out.update(up=up, detail=detail)
        else:  # service
            resp = requests.get(f"{target['url']}/health", timeout=_HTTP_TIMEOUT)
            up = resp.status_code == 200 and resp.json().get("up") is True
            out.update(up=up, detail="healthy" if up else f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 — a probe must never raise.
        out.update(up=False, detail=f"unreachable ({type(exc).__name__})")
    return out


def _sweep():
    """Probe every component (blocking) → list of result dicts. Runs off-thread.

    ``proxy.health()`` is fetched once and shared by the proxy + auth cards.
    """
    health = proxy.health()
    return [_probe_one(t, proxy_health=health) for t in component_targets()]


# ── render ───────────────────────────────────────────────────────────────────
def render():
    ui.label("System Status").classes("text-h5")
    ui.label("Live health of every tier — Memurai backbone, schwab-proxy, the "
             "five domain services, and this app.").classes("opacity-70 text-sm")

    state = {"results": [], "checked_at": None, "busy": False}

    banner = ui.row().classes("w-full")
    with ui.row().classes("items-center gap-3"):
        refresh_btn = ui.button("Refresh", icon="refresh", color=None).props("no-caps").classes(BTN_3D)
        spinner = ui.spinner(size="sm")
        spinner.set_visibility(False)
        checked_lbl = ui.label("").classes("text-sm opacity-60")

    comps = ui.column().classes("w-full gap-2")

    ui.separator()
    ui.label("Published data freshness").classes("text-subtitle1 font-bold mt-2")
    ui.label("Each domain's latest cache write — confirms a service is not just "
             "up but actively publishing.").classes("opacity-70 text-sm")
    fresh_box = ui.column().classes("w-full")

    def _paint_components():
        results = state["results"]
        banner.clear()
        ov = overall_status(results)
        with banner:
            cls = {"positive": "bg-green-2 text-green-10",
                   "negative": "bg-red-2 text-red-10",
                   "grey": "bg-grey-3 text-grey-9"}[ov["color"]]
            with ui.row().classes(
                    f"w-full {cls} rounded p-3 items-center gap-2"):
                ui.icon("check_circle" if ov["all_up"] else (
                    "warning" if ov["color"] == "negative" else "hourglass_empty"))
                ui.label(ov["text"]).classes("font-bold")

        comps.clear()
        with comps:
            for r in results:
                up = r.get("up")
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center gap-3 w-full no-wrap"):
                        ui.icon(status_icon(up)).props(
                            f"color={status_color(up)}").classes("text-2xl")
                        with ui.column().classes("gap-0"):
                            ui.label(r["label"]).classes("font-medium")
                            ui.label(f"{r['tier']} · {r['url']}").classes(
                                "text-xs opacity-60")
                        # Action button: Authorize (auth card, proxy reachable) or
                        # Restart (any other offline + restartable component).
                        if r.get("kind") == "auth" and up is not None:
                            ui.button(
                                "Re-authorize" if up else "Authorize", icon="login",
                                color=None,
                                on_click=lambda: ui.navigate.to(AUTH_URL, new_tab=True)) \
                                .props("no-caps").classes(f"ml-auto {BTN_3D}")
                        elif up is False and restart_spec(r) is not None:
                            ui.button("Restart", icon="restart_alt", color=None,
                                      on_click=lambda t=r: _restart_clicked(t)) \
                                .props("no-caps").classes(f"ml-auto {BTN_3D}")
                        with ui.column().classes("gap-0 items-end ml-auto"):
                            ui.badge(status_word(up)).props(
                                f"color={status_color(up)}")
                            ui.label(r.get("detail", "")).classes(
                                "text-xs opacity-60")

    def _paint_freshness():
        now = _dt.datetime.now(_dt.timezone.utc)
        fresh_box.clear()
        with fresh_box:
            rows = []
            for label, view, scheduled in _FRESHNESS:
                ver, ts = bus_client.read_meta(view)
                rows.append((freshness_row(label, view, ver, ts, now, scheduled),
                             scheduled))
            with ui.element("div").classes("w-full"):
                for row, _scheduled in rows:
                    with ui.row().classes("items-center gap-3 w-full no-wrap "
                                          "py-1 border-b border-gray-700"):
                        ok = row["present"] and not row["stale"]
                        color = "positive" if ok else (
                            "grey" if not row["present"] else "warning")
                        ui.icon("circle").props(f"color={color}").classes("text-xs")
                        ui.label(row["label"]).classes("font-medium min-w-[220px]")
                        ui.label(row["view"]).classes(
                            "text-xs opacity-60 min-w-[200px]")
                        ui.label(f"v{row['version']}").classes(
                            "text-xs opacity-70 min-w-[60px]")
                        age = row["age"] + (" · STALE" if row["stale"] else "")
                        ui.label(age).classes(
                            "text-sm " + ("text-orange" if row["stale"] else
                                          "opacity-80"))

    def _restart_clicked(target):
        try:
            ok = _do_restart(target)
        except Exception as exc:  # noqa: BLE001 — surface, never crash the page.
            ui.notify(f"Couldn't restart {target['label']}: {exc}", type="negative")
            return
        if not ok:
            ui.notify(f"{target['label']} can't be restarted from here.",
                      type="warning")
            return
        ui.notify(f"Restarting {target['label']}… it should come back online "
                  "within ~15s.", type="warning", timeout=8000)
        # Re-sweep a bit sooner than the 15s auto-refresh to reflect the change.
        ui.timer(7.0, _refresh, once=True)

    async def _refresh():
        if state["busy"]:
            return
        state["busy"] = True
        spinner.set_visibility(True)
        refresh_btn.disable()
        try:
            state["results"] = await run.io_bound(_sweep)
            state["checked_at"] = _dt.datetime.now()
            checked_lbl.text = "Last checked " + state["checked_at"].strftime(
                "%H:%M:%S")
            _paint_components()
            _paint_freshness()
        finally:
            spinner.set_visibility(False)
            refresh_btn.enable()
            state["busy"] = False

    refresh_btn.on_click(_refresh)

    # Paint placeholders immediately, kick the first real sweep, then auto-refresh.
    _paint_components()
    _paint_freshness()
    ui.timer(0.1, _refresh, once=True)
    ui.timer(15.0, _refresh)
