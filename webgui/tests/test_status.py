"""Tests for the System Status pure builders (webgui/pages/status.py)."""
import datetime as _dt

from pages import status

_NOW = _dt.datetime(2026, 6, 19, 12, 0, 0, tzinfo=_dt.timezone.utc)


def _iso(seconds_ago):
    return (_NOW - _dt.timedelta(seconds=seconds_ago)).isoformat()


# --- component_targets --------------------------------------------------------
def test_component_targets_covers_every_tier():
    keys = [t["key"] for t in status.component_targets()]
    for expected in ("memurai", "proxy", "sentiment", "options", "portfolio",
                     "trade", "driver", "market", "webgui"):
        assert expected in keys, f"{expected} missing from {keys}"


def test_component_targets_have_required_fields():
    for t in status.component_targets():
        assert set(t) >= {"key", "label", "tier", "kind", "url"}
        assert t["kind"] in {"memurai", "proxy", "service", "self", "auth",
                             "peer"}


def test_memurai_is_tier3_and_proxy_tier1():
    by_key = {t["key"]: t for t in status.component_targets()}
    assert by_key["memurai"]["tier"] == "Tier 3"
    assert by_key["proxy"]["tier"] == "Tier 1"
    assert by_key["sentiment"]["tier"] == "Tier 2"


# --- status wording / colors --------------------------------------------------
def test_status_word():
    assert status.status_word(True) == "Online"
    assert status.status_word(False) == "Offline"
    assert status.status_word(None) == "Checking…"


def test_status_color():
    assert status.status_color(True) == "positive"
    assert status.status_color(False) == "negative"
    assert status.status_color(None) == "grey"


# --- overall rollup -----------------------------------------------------------
def test_overall_all_up():
    res = [{"label": "A", "up": True}, {"label": "B", "up": True}]
    ov = status.overall_status(res)
    assert ov["all_up"] is True
    assert ov["color"] == "positive"
    assert "operational" in ov["text"]


def test_overall_some_down_lists_them():
    res = [{"label": "A", "up": True}, {"label": "B", "up": False},
           {"label": "C", "up": False}]
    ov = status.overall_status(res)
    assert ov["all_up"] is False
    assert ov["color"] == "negative"
    assert ov["down"] == ["B", "C"]
    assert "2 components down" in ov["text"]
    assert "B" in ov["text"] and "C" in ov["text"]


def test_overall_singular_grammar():
    ov = status.overall_status([{"label": "A", "up": True},
                                {"label": "B", "up": False}])
    assert "1 component down" in ov["text"]


def test_overall_all_checking_is_grey():
    ov = status.overall_status([{"label": "A", "up": None}])
    assert ov["color"] == "grey"
    assert ov["all_up"] is False


def test_overall_ignores_still_checking_components():
    # One known-up, one still-checking → treated as all up.
    ov = status.overall_status([{"label": "A", "up": True},
                                {"label": "B", "up": None}])
    assert ov["all_up"] is True


# --- age_text -----------------------------------------------------------------
def test_age_text_missing():
    assert status.age_text(None, _NOW) == "—"
    assert status.age_text("not-a-date", _NOW) == "—"


def test_age_text_buckets():
    assert status.age_text(_iso(10), _NOW) == "10s ago"
    assert status.age_text(_iso(120), _NOW) == "2m ago"
    assert status.age_text(_iso(2 * 3600), _NOW) == "2h ago"
    assert status.age_text(_iso(3 * 86400), _NOW) == "3d ago"


def test_age_text_handles_naive_timestamp_as_utc():
    naive = (_NOW.replace(tzinfo=None) - _dt.timedelta(seconds=30)).isoformat()
    assert status.age_text(naive, _NOW) == "30s ago"


def test_age_text_future_clamps_to_zero():
    assert status.age_text(_iso(-50), _NOW) == "0s ago"


# --- is_stale -----------------------------------------------------------------
def test_is_stale_scheduled():
    assert status.is_stale(_iso(5), _NOW, scheduled=True) is False
    assert status.is_stale(_iso(10_000), _NOW, scheduled=True) is True
    assert status.is_stale(None, _NOW, scheduled=True) is True


def test_is_stale_on_demand_never_flags():
    assert status.is_stale(_iso(10_000), _NOW, scheduled=False) is False
    assert status.is_stale(None, _NOW, scheduled=False) is False


# --- freshness_row ------------------------------------------------------------
def test_freshness_row_present_fresh():
    row = status.freshness_row("Sentiment", "sentiment:composite", 12,
                               _iso(30), _NOW, scheduled=True)
    assert row["present"] is True
    assert row["stale"] is False
    assert row["version"] == 12
    assert row["view"] == "cache:sentiment:composite"
    assert row["age"] == "30s ago"


def test_freshness_row_absent():
    row = status.freshness_row("Trade", "trade:analysis", None, None,
                               _NOW, scheduled=False)
    assert row["present"] is False
    assert row["version"] == "—"
    assert row["age"] == "no data yet"
    assert row["stale"] is False


def test_freshness_row_stale_scheduled():
    # _NOW is 12:00 UTC = 07:00 CT, i.e. BEFORE the 08:00 open — and options:scan
    # only publishes during the session, so its age says nothing at that hour.
    # Use an in-session instant for the "a wedged scanner is caught" case.
    # NB the 17th, not _NOW's 19th: 2026-06-19 is Juneteenth, an NYSE holiday, so
    # the session gate correctly refuses it. (The first attempt used it and this
    # test caught it — which is the calendar working.)
    in_session = _dt.datetime(2026, 6, 17, 16, 0, 0, tzinfo=_dt.timezone.utc)  # Wed 11:00 CT
    row = status.freshness_row(
        "Options", "options:scan", 99,
        (in_session - _dt.timedelta(seconds=10_000)).isoformat(),
        in_session, scheduled=True)
    assert row["present"] is True
    assert row["stale"] is True


def test_freshness_row_does_not_flag_the_scanner_outside_the_session():
    """The weekend "degraded" report. The scanner autoscans 08:00-15:15 CT on
    trading days, so on a Sunday its newest write is legitimately ~43h old
    (measured in prod) — real age, not a fault. The board, the nav badge and the
    drawer's status card all read it as one dead component until this landed."""
    sunday = _dt.datetime(2026, 6, 21, 17, 0, 0, tzinfo=_dt.timezone.utc)  # 12:00 CT Sun
    row = status.freshness_row(
        "Options", "options:scan", 99,
        (sunday - _dt.timedelta(days=2)).isoformat(), sunday, scheduled=True)
    assert row["present"] is True
    assert row["stale"] is False
    # A view that publishes round the clock is STILL checked on that same Sunday —
    # the gate is per-view, not a blanket off-hours amnesty.
    other = status.freshness_row(
        "Sentiment", "sentiment:composite", 5,
        (sunday - _dt.timedelta(hours=6)).isoformat(), sunday, scheduled=True)
    assert other["stale"] is True


# --- restart_spec -------------------------------------------------------------
def _target(key, kind):
    return {"key": key, "kind": kind, "label": key, "tier": "x", "url": "x"}


def test_restart_spec_proxy():
    spec = status.restart_spec(_target("proxy", "proxy"))
    assert spec["kind"] == "unit"
    assert spec["name"] == "proxy"


def test_restart_spec_service_names_the_unit_verbatim():
    """`name` IS the unit suffix, so it must be `options_svc`, not `options`.
    restart_command interpolates it with no translation table -- a mapping here
    would be a second list of component names free to drift from this one."""
    spec = status.restart_spec(_target("options", "service"))
    assert spec["kind"] == "unit"
    assert spec["name"] == "options_svc"


def test_restart_spec_market_service():
    spec = status.restart_spec(_target("market", "service"))
    assert spec["kind"] == "unit"
    assert spec["name"] == "market_svc"


def test_restart_spec_redis_is_never_restartable(monkeypatch):
    """Redis is a SYSTEM unit; `systemctl --user` cannot reach it, and one server
    serves both environments. Read-only in prod as well as dev now -- previously
    only dev withheld it."""
    for is_dev in (True, False):
        monkeypatch.setattr(status, "IS_DEV", is_dev)
        assert status.restart_spec(_target("memurai", "memurai")) is None


def test_restart_spec_self_restarts_webgui():
    spec = status.restart_spec(_target("webgui", "self"))
    assert spec["kind"] == "unit"
    assert spec["name"] == "webgui"


def test_every_component_target_is_restartable_except_auth(monkeypatch):
    # Every card has a Restart action now (incl. the webgui, which relaunches
    # itself). Only the auth card is excepted — its action is Authorize (a link
    # to /auth), not a process restart.
    #
    # Pinned to PROD explicitly. repo_paths now forces the whole prod identity
    # under pytest (name included), so this holds without the patch — but the
    # proxy and Memurai cards ARE withheld in a dev checkout (borrowed / shared
    # — see below), and stating the environment beats depending on a guard two
    # modules away for a test whose whole subject is environment ownership.
    monkeypatch.setattr(status, "IS_DEV", False)
    monkeypatch.setattr(status, "OWNS_PROXY", True)
    for t in status.component_targets():
        spec = status.restart_spec(t)
        if t["kind"] in ("auth", "memurai"):
            # auth's action is Authorize, not a restart; redis is a SYSTEM unit
            # `systemctl --user` cannot reach, shared by both environments.
            assert spec is None
        else:
            assert spec is not None, f"{t['key']} should be restartable"


# --- environment ownership: don't offer a restart this checkout doesn't own ---
def test_proxy_restart_withheld_when_not_owned(monkeypatch):
    # Dev borrows PROD's proxy on :8100 (one rotating OAuth refresh token, so
    # only one proxy can exist) — restarting it from here bounces the LIVE
    # stack's market data. Same hazard tools/stop_all.py guards against.
    monkeypatch.setattr(status, "OWNS_PROXY", False)
    assert status.restart_spec(_target("proxy", "proxy")) is None


def test_proxy_restart_offered_when_owned(monkeypatch):
    # Non-vacuity partner: pins that the guard is conditional, not a blanket
    # removal. (Cannot fail if the guard is deleted — see the guard test above.)
    monkeypatch.setattr(status, "OWNS_PROXY", True)
    spec = status.restart_spec(_target("proxy", "proxy"))
    assert spec is not None
    assert spec["name"] == "proxy"


def test_proxy_label_says_whether_this_checkout_owns_it(monkeypatch):
    # A dev operator seeing no Restart button must be able to tell WHY.
    monkeypatch.setattr(status, "OWNS_PROXY", True)
    owned = {t["key"]: t for t in status.component_targets()}["proxy"]
    assert owned["owned"] is True
    assert "market data" in owned["label"]

    monkeypatch.setattr(status, "OWNS_PROXY", False)
    borrowed = {t["key"]: t for t in status.component_targets()}["proxy"]
    assert borrowed["owned"] is False
    assert "shared" in borrowed["label"] and "prod" in borrowed["label"]
    assert borrowed["label"] != owned["label"]


def test_memurai_restart_withheld_in_dev(monkeypatch):
    # One Redis server serves both environments (separated by logical DB), so
    # restarting the Windows service from dev takes prod's Redis down too.
    monkeypatch.setattr(status, "IS_DEV", True)
    assert status.restart_spec(_target("memurai", "memurai")) is None


def test_everything_else_stays_restartable_in_prod(monkeypatch):
    """Non-vacuity partner for the Redis guard: withholding Redis must not
    withhold everything. If this ever equals the empty set the guards have
    over-fired and the Status page has no working buttons at all."""
    monkeypatch.setattr(status, "IS_DEV", False)
    monkeypatch.setattr(status, "OWNS_PROXY", True)
    restartable = {t["key"] for t in status.component_targets()
                   if status.restart_spec(t) is not None}
    assert restartable == {"proxy", "sentiment", "options", "portfolio",
                           "trade", "driver", "market", "webgui", "webgui_live"}


def test_dev_can_still_restart_everything_it_owns(monkeypatch):
    # The guards must not over-fire. Dev owns its six services (offset ports)
    # and its own web GUI; leaving an operator unable to restart ANYTHING would
    # be a worse outcome than the cross-environment hazard being fixed.
    monkeypatch.setattr(status, "IS_DEV", True)
    monkeypatch.setattr(status, "OWNS_PROXY", False)
    restartable = {t["key"] for t in status.component_targets()
                   if status.restart_spec(t) is not None}
    # webgui_live is here on purpose. The two withheld cards above are
    # withheld because the RESOURCE is shared -- one proxy holding one rotating
    # OAuth token, one Redis server behind two logical DBs. The public live
    # screens share nothing: dev binds its own offset port and runs its own
    # process, so this button reaches only this checkout.
    assert restartable == {"sentiment", "options", "portfolio", "trade",
                           "driver", "market", "webgui", "webgui_live"}


# --- restart_command ----------------------------------------------------------
def test_restart_command_is_a_systemctl_user_restart():
    spec = status.restart_spec(_target("proxy", "proxy"))
    assert status.restart_command(spec) == [
        "systemctl", "--user", "restart", f"trading-{status.ENV_NAME}-proxy"]


def test_restart_command_uses_the_spec_name_verbatim():
    """`options` the card key vs `options_svc` the unit. Getting this wrong
    restarts nothing and reports success, because systemctl exits 0 for a unit
    it does not know about only when... it does not. It errors -- but the button
    would still have named the wrong thing."""
    spec = status.restart_spec(_target("options", "service"))
    cmd = status.restart_command(spec)
    assert cmd[-1] == f"trading-{status.ENV_NAME}-options_svc"


def test_restart_command_is_user_scoped_never_system():
    """`--user` is what keeps this working with NO polkit rule and NO sudoers
    entry. Dropping it would need root for a network-facing app."""
    for tgt in status.component_targets():
        spec = status.restart_spec(tgt)
        if spec is None:
            continue
        cmd = status.restart_command(spec)
        assert cmd[:3] == ["systemctl", "--user", "restart"], cmd


def test_restart_command_carries_no_windows_machinery():
    for tgt in status.component_targets():
        spec = status.restart_spec(tgt)
        if spec is None:
            continue
        joined = " ".join(status.restart_command(spec)).lower()
        for banned in (".bat", "cmd", "powershell", "taskkill", "start-service"):
            assert banned not in joined, (banned, joined)


def test_restart_command_none_passthrough():
    assert status.restart_command(None) is None


# --- Schwab Authorization -----------------------------------------------------
def test_auth_target_present_and_links_to_auth_page():
    by_key = {t["key"]: t for t in status.component_targets()}
    assert "schwab_auth" in by_key
    t = by_key["schwab_auth"]
    assert t["kind"] == "auth"
    assert t["url"].endswith("/auth")
    assert status.AUTH_URL.endswith(":8100/auth")


def test_auth_link_is_the_browser_facing_url_not_the_server_one():
    """The button opens in the VIEWER's browser, where 127.0.0.1 is the viewer's
    own device. It must come from PROXY_PUBLIC_URL (the tailnet address on the
    VPS), never from PROXY_URL, which is where the SERVER reaches the proxy."""
    import inspect
    src = inspect.getsource(status)
    assert 'AUTH_URL = f"{PROXY_PUBLIC_URL}/auth"' in src
    assert 'f"{PROXY_URL}/auth"' not in src


def test_auth_status_proxy_down_is_unknown():
    up, detail = status.auth_status({"up": False})
    assert up is None
    assert "proxy down" in detail
    # None-health also unknown (never raises).
    assert status.auth_status(None)[0] is None


def test_auth_status_no_token_needs_auth():
    up, detail = status.auth_status({"up": True, "has_token": False})
    assert up is False
    assert "authorization required" in detail


def test_auth_status_refresh_expired_needs_reauth():
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": True,
        "token_expired": True})
    assert up is False
    assert "re-authorization required" in detail


def test_auth_status_access_expired_but_refresh_ok_is_authorized():
    # Access token expired but refresh valid → proxy auto-refreshes → still up.
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "token_expired": True})
    assert up is True
    assert "auto-refresh" in detail


def test_auth_status_fully_valid_is_authorized():
    up, detail = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "token_expired": False})
    assert up is True
    assert "authorized" in detail


def test_auth_is_not_restartable():
    # The auth card's action is Authorize (a link), not a process restart.
    auth_target = {"key": "schwab_auth", "kind": "auth"}
    assert status.restart_spec(auth_target) is None


def test_render_merges_auth_into_proxy_card(monkeypatch):
    """The Schwab Authorization probe renders MERGED into the schwab-proxy card
    (Authorize + Restart side by side) rather than as a card of its own.

    ⚠ RE-AIMED for the Phase 6 kit migration, not weakened. The old version
    presence-asserted ``w-[280px]`` and ``justify-end`` in the source; the fixed
    280px slot went with the migration (inside a ``no-wrap`` row it overflowed a
    375px phone), so the layout half moved to
    ``test_every_cards_buttons_line_up_in_one_column``, which asks the RENDERED
    page instead of grepping for a class literal."""
    import inspect

    src = inspect.getsource(status.render)
    assert "merged into the schwab-proxy card" in src
    host = _render_status(monkeypatch)
    _run_timer(host, "_refresh")
    cards = _cards(host)
    labels = [t for c in cards for t in _texts(c)]
    assert "Schwab Authorization (OAuth)" not in labels, \
        "the auth probe grew a card of its own"
    assert any("Auth: authorized" in str(t) for t in labels), \
        "the auth detail is nowhere on the page"
    assert len(cards) == len([r for r in _SWEEP if r["kind"] != "auth"])


# --- degrade counts surfaced from /health ------------------------------------

def test_service_detail_reports_degrades_when_present():
    """A silent swallowed exception becomes a number here.

    services/_degrade counts them and _scaffold puts the total on /health; a
    counter nobody can see is not observability, so the service card says
    "healthy - 12 degraded" and the operator has somewhere to start."""
    assert status.service_detail({"up": True, "degrades_total": 12}) == \
        "healthy - 12 degraded"
    assert status.service_detail({"up": True, "degrades_total": 1}) == \
        "healthy - 1 degraded"


def test_service_detail_is_plain_healthy_at_zero():
    """Zero is the normal case; showing "- 0 degraded" on every card is noise."""
    assert status.service_detail({"up": True, "degrades_total": 0}) == "healthy"
    assert status.service_detail({"up": True}) == "healthy"


def test_service_detail_tolerates_junk():
    """An older service predating the counter, or a garbled payload, must not
    break the card."""
    for junk in ({"up": True, "degrades_total": None},
                 {"up": True, "degrades_total": "lots"},
                 {"up": True, "degrades_total": -3}):
        assert status.service_detail(junk) == "healthy"


def test_schwab_auth_state_sees_a_REJECTED_refresh_token():
    """Observed live 2026-08-22: the proxy answered `refresh_token_expired:
    false` for over an hour — from a locally stamped expiry — while Schwab was
    rejecting the token and no market data flowed. The Status page rendered
    "authorized — token valid" throughout.

    The proxy now reports Schwab's own verdict; the page has to read it, or the
    blindness just moves one layer up."""
    ok, note = status.auth_status({
        "up": True, "has_token": True,
        "refresh_token_expired": False,      # the stamp still says fine
        "refresh_token_rejected": True,      # Schwab disagrees
        "token_expired": True,
    })
    assert ok is False
    assert "re-auth" in note.lower() or "reauth" in note.lower()


def test_schwab_auth_state_unchanged_when_nothing_is_rejected():
    ok, note = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "refresh_token_rejected": False, "token_expired": False,
    })
    assert ok is True and "authorized" in note.lower()


def test_schwab_auth_state_tolerates_an_older_proxy_without_the_field():
    """A proxy that predates the rejection field must not read as rejected."""
    ok, _ = status.auth_status({
        "up": True, "has_token": True, "refresh_token_expired": False,
        "token_expired": False,
    })
    assert ok is True


# --- the public live screens: a peer, probed but never nagged about ----------
def test_the_live_screens_get_a_card_of_their_own():
    """It is a process on this box like any other, so it is probed and named.
    Its unit suffix is pinned against the generator by
    ``tests/test_systemd_units.py`` -- the seam that would otherwise surface
    only as a Restart button that errors."""
    by_key = {t["key"]: t for t in status.component_targets()}
    live = by_key["webgui_live"]
    assert live["kind"] == "peer"
    assert live["tier"] == "Tier 1"
    assert str(status.NICEGUI_LIVE_PORT) in live["url"]
    assert status.restart_spec(live)["name"] == "webgui_live"


def test_a_dead_live_process_never_reaches_the_apps_own_health_signal():
    """The rail badge, the drawer status card and the chime all come from
    ``alerts.unhealthy_keys`` over main.py's health fan-out, which iterates
    ``SERVICE_URLS`` -- the Tier-2 services. The live screens are a PEER of
    this app, not a dependency of it: the public origin going down must not put
    a warning on the trading UI's rail.

    Pinned as the structural fact that makes it true (the peer is not a Tier-2
    service key), because the alternative is discovering it the first time the
    public site falls over mid-session and the rail claims a service alert."""
    from repo_paths import SERVICE_PORTS, SERVICE_URLS
    peers = [t for t in status.component_targets() if t["kind"] == "peer"]
    assert peers, "no peer target at all -- this test would be vacuous"
    for t in peers:
        assert t["key"] not in SERVICE_PORTS
        assert t["key"] not in SERVICE_URLS


def test_the_live_probe_reads_alive_on_any_http_answer(monkeypatch):
    """The question a liveness probe asks is whether the process is speaking
    HTTP, not whether one route exists. ``/favicon.ico`` is registered by
    NiceGUI unconditionally today; a future version that moved it must report
    the truth rather than a false Offline.

    And it must be HTTP, never a TCP connect -- a dead accept loop stays bound
    and passes a connect, which is how a promote once left prod serving no UI.
    """
    seen = {}

    class _Resp:
        status_code = 404

    def _get(url, timeout=None):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(status.requests, "get", _get)
    target = {"key": "webgui_live", "label": "L", "tier": "Tier 1",
              "kind": "peer", "url": "http://127.0.0.1:8501"}
    out = status._probe_one(target)
    assert out["up"] is True
    assert seen["url"].startswith("http://127.0.0.1:8501/")
    assert not seen["url"].endswith("/health")   # it serves no /health at all


def test_the_live_probe_reports_down_when_nothing_answers(monkeypatch):
    """Non-vacuity partner: 'any answer is up' must not degrade to 'always up'."""
    def _boom(url, timeout=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(status.requests, "get", _boom)
    out = status._probe_one({"key": "webgui_live", "label": "L",
                             "tier": "Tier 1", "kind": "peer",
                             "url": "http://127.0.0.1:8501"})
    assert out["up"] is False
    assert "unreachable" in out["detail"]


# ── Phase 6, Task 6: the frame, the stamp and the toasts ─────────────────────
# ⚠ Every test above this line is against the PURE builders and touches no
# widget. None of them changed, and none of them may: ``status_word`` /
# ``status_color`` / ``status_icon`` / ``restart_spec`` / ``restart_command``
# are the page's behaviour, and this migration is presentation.
import inspect

from pages import ui_kit as kit
from pages.options import theme

# One of each KIND the page can draw, including the auth probe that merges into
# the proxy card.
_SWEEP = [
    {"key": "memurai", "label": "Memurai (Redis backbone)", "tier": "Tier 3",
     "kind": "memurai", "url": "redis://127.0.0.1:6379", "up": True,
     "detail": "PING ok"},
    {"key": "proxy", "owned": True, "label": "schwab-proxy (market data / auth)",
     "tier": "Tier 1", "kind": "proxy", "url": "http://127.0.0.1:8100",
     "up": True, "detail": "healthy"},
    {"key": "schwab_auth", "label": "Schwab Authorization (OAuth)",
     "tier": "Tier 1", "kind": "auth", "url": "http://127.0.0.1:8100/auth",
     "up": True, "detail": "authorized — token valid"},
    {"key": "options", "label": "options_svc (scan / gamma / paper)",
     "tier": "Tier 2", "kind": "service", "url": "http://127.0.0.1:8211",
     "up": True, "detail": "healthy"},
    {"key": "webgui", "label": "webgui (this app)", "tier": "Tier 1",
     "kind": "self", "url": "http://127.0.0.1:8500", "up": True,
     "detail": "serving this page"},
]
# The one restartable component, for the tests that need exactly one Restart
# button on the page.
_ONE_SERVICE = [_SWEEP[3]]


def _fresh_meta():
    return (7, _dt.datetime.now(_dt.timezone.utc).isoformat())


def _render_status(monkeypatch, results=None, meta=None):
    """Render the page over a pinned sweep and pinned bus metadata."""
    from nicegui import ui
    rows = list(results if results is not None else _SWEEP)
    monkeypatch.setattr(status, "_sweep", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(status.bus_client, "read_meta",
                        lambda _view: meta or _fresh_meta())
    with ui.card() as host:
        status.render()
    return host


def _texts(el):
    return [getattr(e, "text", None) for e in el.descendants()]


def _labels(el):
    from nicegui import ui
    return [e for e in el.descendants() if isinstance(e, ui.label)]


def _buttons(el):
    from nicegui import ui
    return [b for b in el.descendants() if isinstance(b, ui.button)]


def _cards(host):
    from nicegui import ui
    return [c for c in host.descendants() if isinstance(c, ui.card)
            and c is not host]


def _fire(el, kind, args=None):
    """Fire an element's OWN registered listener - what the browser would send."""
    from nicegui.events import GenericEventArguments
    fired = [li.handler(GenericEventArguments(sender=el, client=el.client, args=args))
             for li in list(el._event_listeners.values())
             if li.type.split(".")[0] == kind and li.handler is not None]
    assert fired, f"no {kind} listener to fire"


def _click(host, text):
    btn = [b for b in _buttons(host) if b.text == text]
    assert btn, f"no {text!r} button on the page"
    _fire(btn[-1], "click")


def _restart(host):
    """Press Restart. Task 7 puts a confirm dialog behind it; every caller goes
    through here so that change lands in ONE place."""
    _click(host, "Restart")


def _timer(host, name):
    from nicegui import ui
    found = [e for e in host.descendants()
             if isinstance(e, ui.timer)
             and getattr(e.callback, "__name__", "") == name]
    assert found, f"no ui.timer registered for {name}()"
    return found[-1]


def _run_timer(host, name):
    """Drive a page's timer the way the browser's first tick would."""
    import asyncio
    t = _timer(host, name)

    async def _drive():
        with t.parent_slot:
            result = t.callback()
            if inspect.isawaitable(result):
                await result

    asyncio.run(_drive())


def _said(monkeypatch):
    """Everything the page reports, in order: ``(kind, text)``. Both spellings,
    so a stray ``ui.notify`` is caught rather than missed."""
    seen = []
    monkeypatch.setattr(status.ui, "notify",
                        lambda msg="", **kw: seen.append((kw.get("type"), msg)))
    monkeypatch.setattr(kit, "toast", lambda kind, text: seen.append((kind, text)))
    return seen


def _scrims(host):
    """Each ``kit.region``'s spinner scrim - the element whose visibility
    ``show()``/``hide()`` toggles.

    ⚠ Identified by the scrim's OWN classes, not merely as "the spinner's
    parent". The pre-migration page had a bare ``ui.spinner`` sitting in a row
    whose ``.visible`` is always True, so a parent-of-the-spinner helper made
    every "is the wait on screen" assertion pass vacuously - caught by proving
    these tests red against the old page (rule 10), which is the whole reason
    that step exists."""
    from nicegui import ui
    out = []
    for s in host.descendants():
        if not isinstance(s, ui.spinner):
            continue
        parent = s.parent_slot.parent
        assert {"absolute", "inset-0"} <= set(parent.classes), \
            "a spinner that is not a region scrim: its visibility is nobody's wait"
        out.append(parent)
    return out


def test_the_frame_is_the_kit_and_carries_no_chrome_of_its_own():
    src = inspect.getsource(status.render)
    assert "kit.page()" in src
    assert 'kit.header("System Status")' in src
    assert 'kit.section_title("Published data freshness")' in src
    # ⚠ The three raw Quasar fills are NOT grepped for here - the page's own
    # prose names them while explaining why they went, which is the manuals.py
    # lesson. They are asked of the RENDERED page instead, in
    # ``test_the_banner_drops_the_raw_quasar_palette``, which is the stronger
    # question anyway.
    for token in ("text-h5", "text-subtitle1 font-bold", "opacity-",
                  "ui.notify(", "ui.spinner(", "ui.separator(", "BTN_3D",
                  "text-orange", "text-negative"):
        assert token not in src, f"{token} is the page building its own chrome"
    assert "BTN_3D" not in inspect.getsource(status), "the module still imports it"


def test_the_page_description_moved_to_the_hover_help():
    """The standard gives a page ONE header line and no description line - the
    hover help already explains it. Checked against ``page_help`` so the
    sentence is not simply deleted."""
    import page_help
    help_text = page_help.HELP_MD["/status"]
    assert "public live screens" in help_text
    assert "publishing" in help_text, \
        "the freshness table's own sentence landed nowhere"
    src = inspect.getsource(status.render)
    assert "Live health of every tier" not in src
    assert "actively publishing" not in src


def test_the_naive_machine_local_clock_is_gone():
    """``state["checked_at"] = datetime.now()`` rendered ``%H:%M:%S`` with no
    zone - the only clock in the app that was neither Central nor a data stamp.
    The header's own stamp replaces it, in CT like every other one."""
    src = inspect.getsource(status.render)
    for gone in ("%H:%M:%S", "Last checked", "checked_at", "checked_lbl"):
        assert gone not in src, f"{gone} survived"


def test_the_header_stamp_is_hand_driven_and_reads_the_sweeps_own_clock(monkeypatch):
    """``kit.header``'s stamp is bound to a bus view's ``:ts`` side key, and
    this page's freshness is a PROBE - no view publishes "the stack was last
    swept at". So the page shows the stamp itself and sets it from the sweep.

    Before the first sweep it must say ``Waiting for data``, never a made-up
    time; afterwards ``Updated <clock> CT``."""
    host = _render_status(monkeypatch)
    waiting = [lbl for lbl in _labels(host) if lbl.text == kit.WAITING_TEXT]
    assert waiting, "the stamp is hidden or absent before the first sweep"
    assert waiting[0].visible, "the hand-driven stamp was never made visible"
    _run_timer(host, "_refresh")
    stamped = [str(t) for t in _texts(host) if str(t).startswith("Updated ")]
    assert stamped, f"no stamp after the sweep: {_texts(host)}"
    assert stamped[0].endswith(" CT")
    assert kit.WAITING_TEXT not in _texts(host)


def test_the_kit_itself_is_not_edited_for_this_page():
    """Decision 1 of the phase plan. ``kit.set_stamp`` must still leave
    visibility alone, or every migrated page's hidden stamp would start
    showing to buy this one page a convenience."""
    assert "stamp.set_visibility(view is not None)" in inspect.getsource(kit.header)
    set_stamp = inspect.getsource(kit).split("def set_stamp(", 1)[1] \
        .split("@guard_async", 1)[0]
    assert "set_visibility" not in set_stamp


def test_a_failed_sweep_leaves_the_last_good_stamp_alone(monkeypatch):
    """A stamp that advanced on a sweep that never completed would report a
    health check that did not happen."""
    host = _render_status(monkeypatch)
    _run_timer(host, "_refresh")
    first = [str(t) for t in _texts(host) if str(t).startswith("Updated ")][0]

    def _boom():
        raise OSError("bus down")

    monkeypatch.setattr(status, "_sweep", _boom)
    try:
        _run_timer(host, "_refresh")
    except OSError:
        pass
    after = [str(t) for t in _texts(host) if str(t).startswith("Updated ")]
    assert after and after[0] == first


def test_the_components_sit_in_a_region_whose_spinner_a_repaint_cannot_delete(
        monkeypatch):
    """⚠ This page never had the cleared-container spinner bug the other five
    had: its spinner was already a SIBLING of both cleared containers, which is
    the one thing it got right. ``kit.region`` keeps that property - the scrim
    lives on ``outer`` and only ``content`` is cleared - so this pins what was
    already true rather than claiming a fix.

    What IS new is that BOTH blocks a repaint replaces have one: the component
    cards and the freshness table are cleared and rebuilt on every sweep, and
    only the cards had any wait over them before."""
    host = _render_status(monkeypatch)
    before = _scrims(host)
    assert len(before) == 2, \
        f"expected a region over each repainted block, found {len(before)}"
    _run_timer(host, "_refresh")
    assert len(_scrims(host)) == 2, "a repaint deleted a region's spinner"


def test_refresh_holds_its_own_button_while_the_sweep_runs(monkeypatch):
    """``kit.set_busy`` replaces the hand-rolled disable()/enable() pair and the
    bare ``ui.spinner`` beside the button: one spelling, and a backstop that
    releases it if the answer never comes."""
    host = _render_status(monkeypatch)
    btn = [b for b in _buttons(host) if b.text == "Refresh"][0]
    seen = {}

    def _slow():
        seen["enabled"] = btn.enabled
        seen["loading"] = "loading" in btn._props
        return [dict(r) for r in _SWEEP]

    monkeypatch.setattr(status, "_sweep", _slow)
    _run_timer(host, "_refresh")
    assert seen["enabled"] is False, "the sweep ran with Refresh still clickable"
    assert seen["loading"] is True, "Refresh never showed its own spinner"
    assert btn.enabled is True, "Refresh was left disabled"


def test_the_freshness_probes_run_off_the_event_loop(monkeypatch):
    """Seven blocking ``read_meta`` calls, every 15 s, per open tab. ``_sweep``
    already crossed ``run.io_bound``; these did not. Asserts the THREAD, not
    the spelling."""
    import threading
    where = []

    def _meta(_view):
        where.append(threading.current_thread())
        return _fresh_meta()

    host = _render_status(monkeypatch)
    monkeypatch.setattr(status.bus_client, "read_meta", _meta)
    assert not where, "the freshness table was read on the event loop at build"
    _run_timer(host, "_refresh")
    assert where, "the freshness table was never read"
    assert all(t is not threading.main_thread() for t in where), \
        "read_meta still runs on the event loop"
    assert len(where) == len(status._FRESHNESS)


def test_the_freshness_table_wears_the_apps_own_colours(monkeypatch):
    """``text-orange`` and the ``opacity-*`` mutings were the page's own
    vocabulary; the palette's warning and muted tokens are the app's."""
    stale = (7, (_dt.datetime.now(_dt.timezone.utc)
                 - _dt.timedelta(days=30)).isoformat())
    host = _render_status(monkeypatch, meta=stale)
    _run_timer(host, "_refresh")
    assert any("STALE" in str(lbl.text) and theme.TXT_WARN in " ".join(lbl.classes)
               for lbl in _labels(host)), "a stale row is not amber"
    classes = [" ".join(lbl.classes) for lbl in _labels(host)]
    assert not [c for c in classes if "text-orange" in c or "opacity-" in c]


def test_the_banner_drops_the_raw_quasar_palette(monkeypatch):
    """``bg-green-2 text-green-10`` / ``bg-red-2`` / ``bg-grey-3`` was the only
    place in the app using Quasar's own palette. All up is a green line; a
    component down is the kit's one attention band."""
    host = _render_status(monkeypatch)
    _run_timer(host, "_refresh")
    classes = " ".join(" ".join(e.classes) for e in host.descendants())
    for banned in ("bg-green-2", "text-green-10", "bg-red-2", "text-red-10",
                   "bg-grey-3", "text-grey-9"):
        assert banned not in classes, banned
    assert any(theme.TXT_POS in " ".join(lbl.classes)
               and "operational" in str(lbl.text) for lbl in _labels(host)), \
        "the all-up verdict is not drawn in the palette's positive colour"

    down = [dict(r) for r in _SWEEP]
    down[3] = dict(down[3], up=False, detail="HTTP 502")
    host = _render_status(monkeypatch, results=down)
    _run_timer(host, "_refresh")
    notices = [e for e in host.descendants()
               if set(kit.NOTICE.split()) <= set(e.classes)]
    assert notices, "a component down draws no notice band"
    assert any("1 component down" in str(t) for t in _texts(notices[0]))


def test_the_restart_wait_shows_on_the_region_and_never_as_a_toast(monkeypatch):
    """A toast reports an OUTCOME. "Restarting … ~15s" is work STARTED, which
    is a spinner.

    ⚠ On the REGION, not ``kit.set_busy`` on the Restart button:
    ``_paint_components`` rebuilds every card every 15 s and would take the
    button's busy timer with it, mid-wait."""
    host = _render_status(monkeypatch, results=_ONE_SERVICE)
    _run_timer(host, "_refresh")
    said = _said(monkeypatch)
    monkeypatch.setattr(status, "_do_restart", lambda _t: True)
    _restart(host)
    assert not said, f"the restart reported through a toast: {said}"
    scrims = [s for s in _scrims(host) if s.visible]
    assert scrims, "nothing on screen says the restart is running"
    messages = [str(t) for s in scrims for t in _texts(s)]
    assert any("Restarting" in m for m in messages), messages


def test_the_restart_spinner_survives_the_15s_auto_refresh(monkeypatch):
    """The auto-refresh can land a second after the click. Hiding the spinner
    there would take the wait off the screen eight seconds into a
    fifteen-second one."""
    host = _render_status(monkeypatch, results=_ONE_SERVICE)
    _run_timer(host, "_refresh")
    _said(monkeypatch)
    monkeypatch.setattr(status, "_do_restart", lambda _t: True)
    _restart(host)
    _run_timer(host, "_refresh")
    assert [s for s in _scrims(host) if s.visible], \
        "the auto-refresh took the restart wait off the screen"


def test_every_restart_outcome_is_a_kit_toast_with_the_right_kind(monkeypatch):
    """Three outcomes: a spawn that raised, a target that cannot be restarted,
    and the web app restarting itself (which kills this page)."""
    said = _said(monkeypatch)

    host = _render_status(monkeypatch, results=_ONE_SERVICE)
    _run_timer(host, "_refresh")

    def _boom(_t):
        raise OSError("systemctl: no such unit")

    monkeypatch.setattr(status, "_do_restart", _boom)
    _restart(host)
    assert said[-1][0] == "error" and "no such unit" in said[-1][1]

    # ⚠ UNREACHABLE from the UI - the Restart button is only built where
    # ``restart_spec(target)`` is not None, so ``_do_restart`` cannot answer
    # False under it. Kept defensively and driven here, because it is the one
    # branch that would otherwise report a restart that never happened.
    monkeypatch.setattr(status, "_do_restart", lambda _t: False)
    _restart(host)
    assert said[-1][0] == "warn" and "can't be restarted" in said[-1][1]

    host = _render_status(monkeypatch, results=[_SWEEP[4]])
    _run_timer(host, "_refresh")
    monkeypatch.setattr(status, "_do_restart", lambda _t: True)
    _restart(host)
    assert said[-1][0] == "warn" and "disconnect" in said[-1][1]
    assert not [s for s in _scrims(host) if s.visible], \
        "the page that is about to die shows a wait that will never end"


def test_every_cards_buttons_line_up_in_one_column(monkeypatch):
    """The layout half of the auth-merge test, asked of the RENDERED page.

    Right edges align because the button slot is LAST, right-justified and does
    not shrink, while the label column grows to push it flush right - not
    because of any one width literal. ⚠ The old fixed ``w-[280px]`` slot inside
    a ``no-wrap`` row overflowed a 375px phone, which is why the literal went."""
    host = _render_status(monkeypatch)
    _run_timer(host, "_refresh")
    slots = 0
    for card in _cards(host):
        buttons = _buttons(card)
        if not buttons:
            continue
        slot = buttons[-1].parent_slot.parent
        assert "justify-end" in slot.classes, slot.classes
        assert "shrink-0" in slot.classes, slot.classes
        assert buttons[-1].text == "Restart", \
            "Restart is not the rightmost button, so it moves card to card"
        slots += 1
    assert slots >= 2, "too few button slots to be checking alignment at all"


def test_the_card_row_can_wrap_so_it_fits_a_phone(monkeypatch):
    """375px. A ``no-wrap`` row holding an icon, a growing label column, a
    status column and a fixed 280px button slot cannot fit one, and the failure
    is a horizontally scrolling card rather than anything that looks broken."""
    host = _render_status(monkeypatch)
    _run_timer(host, "_refresh")
    checked = 0
    for card in _cards(host):
        buttons = _buttons(card)
        if not buttons:
            continue
        row = buttons[-1].parent_slot.parent.parent_slot.parent
        assert "no-wrap" not in row.classes, \
            "the card's own row still refuses to wrap"
        assert "flex-wrap" in row.classes
        checked += 1
    assert checked >= 2
