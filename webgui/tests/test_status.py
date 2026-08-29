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
        assert t["kind"] in {"memurai", "proxy", "service", "self", "auth"}


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
                           "trade", "driver", "market", "webgui"}


def test_dev_can_still_restart_everything_it_owns(monkeypatch):
    # The guards must not over-fire. Dev owns its six services (offset ports)
    # and its own web GUI; leaving an operator unable to restart ANYTHING would
    # be a worse outcome than the cross-environment hazard being fixed.
    monkeypatch.setattr(status, "IS_DEV", True)
    monkeypatch.setattr(status, "OWNS_PROXY", False)
    restartable = {t["key"] for t in status.component_targets()
                   if status.restart_spec(t) is not None}
    assert restartable == {"sentiment", "options", "portfolio", "trade",
                           "driver", "market", "webgui"}


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


def test_render_merges_auth_into_proxy_card_with_aligned_buttons():
    # The Schwab Authorization probe renders merged into the schwab-proxy card
    # (Authorize + Restart side by side); every card puts its buttons in the
    # same fixed-width right-justified slot so they align in one column.
    import inspect

    src = inspect.getsource(status.render)
    assert "merged into the schwab-proxy card" in src
    assert "w-[280px]" in src and "justify-end" in src
    # No stray ml-auto button placement remains (the old drift-prone layout).
    assert "ml-auto {BTN_3D}" not in src


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
