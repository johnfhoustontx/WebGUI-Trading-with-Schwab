"""The Strategy Finder builds every structure on every listed expiry (2026-09-14).

The builders each take the NEAREST expiry in their window, so widening the window
alone changed almost nothing. ``swing_scan(every_expiry=True)`` runs them on the
chain cut to one expiry at a time; ``every_expiry=False`` keeps the nearest-expiry
path the Income Window relies on. The ``scan_env`` fixture lives in conftest.py.
"""
from services.options_svc import compute

BANDS = (-0.2, -0.1, 0.1, 0.2, 0.1)


def _key(s):
    return (s["type"], s["expiration"], tuple(l["strike"] for l in s["legs"]))


def _front(s):
    return min(l["expiration"] for l in s["legs"] if l.get("expiration"))


def _listed(chain):
    return compute.listed_expiry_dtes(chain, 0, compute._NO_DTE_MAX)


def _no_timestamps(obj):
    """Two scans stamp different build times; everything else must match."""
    if isinstance(obj, dict):
        return {k: _no_timestamps(v) for k, v in obj.items() if k != "timestamp"}
    if isinstance(obj, list):
        return [_no_timestamps(v) for v in obj]
    return obj


def test_every_expiry_equals_each_builder_run_by_hand_on_each_expiry(scan_env):
    ssn = scan_env.ssn
    out = compute.swing_scan("SPY", 0, None, *BANDS,
                             families=("DIRECTIONAL",), every_expiry=True, payoff=False)
    want = []
    for exp, dte in _listed(scan_env.chain):
        want += ssn.build_directional(compute.chain_slice(scan_env.chain, {exp}), "SPY",
                                      scan_env.spot, scan_env.atm_iv, dte, dte,
                                      put_band=(-0.2, -0.1), call_band=(0.1, 0.2))
    assert want
    assert sorted(map(_key, out["signals"])) == sorted(map(_key, want))
    assert {s["expiration"] for s in out["signals"]} == {e for e, _ in _listed(scan_env.chain)}


def test_the_seven_day_builders_build_each_expiry_from_seven_days(scan_env):
    """Straddles, butterflies and share structures build on every expiry at least
    a week out - here 10, 38 and 400 - where the default path built only the 10."""
    out = compute.swing_scan("SPY", 0, None, *BANDS, every_expiry=True, payoff=False,
                             families=("STRADDLE", "BUTTERFLY", "STOCK"))
    by_group = {}
    for s in out["signals"]:
        by_group.setdefault(s["group"], set()).add(s["expiration"])
    e = scan_env.exp_by_dte
    assert by_group["STRADDLE"] == {e[10], e[38], e[400]}
    assert by_group["STOCK"] == {e[10], e[38], e[400]}
    assert e[3] not in by_group.get("BUTTERFLY", set())
    assert len(by_group.get("BUTTERFLY", ())) >= 2


def test_default_mode_is_unchanged(scan_env):
    """every_expiry=False keeps today's nearest-expiry path exactly."""
    a = compute.swing_scan("SPY", 0, 120, *BANDS, payoff=False)
    b = compute.swing_scan("SPY", 0, 120, *BANDS, payoff=False, every_expiry=False)
    assert a["signals"]
    assert _no_timestamps(a) == _no_timestamps(b)
    assert {s["expiration"] for s in a["signals"]
            if s["group"] == "DIRECTIONAL"} == {scan_env.nearest}
    # Every single-expiry group still builds on ONE expiry, and at most three
    # iron condors come out of the whole scan.
    for group in ("DIRECTIONAL", "STRADDLE", "BUTTERFLY", "STOCK"):
        assert len({s["expiration"] for s in a["signals"] if s["group"] == group}) == 1
    assert len([s for s in a["signals"] if s["type"] == "IC"]) <= 3


def test_every_expiry_scan_builds_more_than_the_default(scan_env):
    default = compute.swing_scan("SPY", 0, None, *BANDS, payoff=False)
    every = compute.swing_scan("SPY", 0, None, *BANDS, payoff=False, every_expiry=True)
    assert len(every["signals"]) > len(default["signals"])
    # Credit spreads were already built on every expiry: the same set either way.
    spreads = lambda r: sorted(_key(s) for s in r["signals"] if s["type"] in ("PCS", "CCS"))
    assert spreads(every) and spreads(every) == spreads(default)


def test_calendars_take_each_front_of_at_least_seven_days(scan_env):
    out = compute.swing_scan("SPY", 0, None, *BANDS,
                             families=("CALENDAR",), every_expiry=True, payoff=False)
    fronts = {_front(s) for s in out["signals"]}
    e = scan_env.exp_by_dte
    assert out["signals"]
    assert e[3] not in fronts                              # under 7 DTE: none
    assert fronts == {e[10], e[38]}                        # 400 has no later expiry
    assert len(out["signals"]) == len({(s["type"], _front(s)) for s in out["signals"]})


def test_a_front_with_no_usable_leg_does_not_build_the_next_expirys_calendar(scan_env):
    """The builder takes the nearest expiry with a usable leg as its front. With
    every 10-DTE call carrying Schwab's -999 IV sentinel, the 10-DTE turn would
    build the 38-DTE call calendar - which the 38-DTE turn builds as well."""
    e = scan_env.exp_by_dte
    key = f"{e[10]}:10"
    for contracts in scan_env.chain["callExpDateMap"][key].values():
        contracts[0]["volatility"] = -999.0
    out = compute.swing_scan("SPY", 0, None, *BANDS,
                             families=("CALENDAR",), every_expiry=True, payoff=False)
    calls = [s for s in out["signals"] if s["type"] in ("CALENDAR_CALL", "DIAGONAL_CALL")]
    assert calls                                               # the 38-DTE turn built them
    assert {_front(s) for s in calls} == {e[38]}
    assert len(calls) == len({(s["type"], _front(s)) for s in calls})


def test_a_calendar_on_each_front_matches_the_builder_run_by_hand(scan_env):
    ssn = scan_env.ssn
    out = compute.swing_scan("SPY", 0, None, *BANDS,
                             families=("CALENDAR",), every_expiry=True, payoff=False)
    listed = _listed(scan_env.chain)
    want = []
    for exp, dte in listed:
        if dte < ssn._MIN_FRONT_DTE:
            continue
        later = compute.chain_slice(scan_env.chain, {e for e, d in listed if d >= dte})
        want += [s for s in ssn.build_calendars(later, "SPY", scan_env.spot, scan_env.atm_iv,
                                                dte, compute._NO_DTE_MAX)
                 if _front(s) == exp]
    assert want
    assert sorted(map(_key, out["signals"])) == sorted(map(_key, want))


def test_iron_condors_are_paired_within_each_expiry(scan_env, monkeypatch):
    calls = []
    real = compute.se.build_iron_condors
    monkeypatch.setattr(compute.se, "build_iron_condors",
                        lambda spreads, **k: calls.append({s["expiration"] for s in spreads})
                        or real(spreads, **k))
    out = compute.swing_scan("SPY", 0, None, *BANDS,
                             families=("NEUTRAL",), every_expiry=True, payoff=False)
    assert calls and all(len(exps) <= 1 for exps in calls)
    # Condors come out of more than one expiry - no longer the top three overall.
    assert len({s["expiration"] for s in out["signals"] if s["type"] == "IC"}) >= 2


def test_ids_stay_unique_across_expiries(scan_env):
    out = compute.swing_scan("SPY", 0, None, *BANDS, every_expiry=True, payoff=False)
    ids = [s["id"] for s in out["signals"]]
    assert ids and len(ids) == len(set(ids))
    assert all(s.get("group") for s in out["signals"])


def test_chain_slice_keeps_only_the_named_expiries_and_top_level_fields(scan_env):
    exp = scan_env.nearest
    s = compute.chain_slice(scan_env.chain, {exp})
    assert s["underlyingPrice"] == scan_env.chain["underlyingPrice"]
    assert {k.split(":")[0] for k in s["callExpDateMap"]} == {exp}
    assert {k.split(":")[0] for k in s["putExpDateMap"]} == {exp}
    # Shared, not copied: a slice costs dict entries, never contracts.
    key = next(iter(s["callExpDateMap"]))
    assert s["callExpDateMap"][key] is scan_env.chain["callExpDateMap"][key]
    assert len(scan_env.chain["callExpDateMap"]) == 4       # the input is not cut


def test_listed_expiry_dtes_reads_the_chain_key_dte_inside_the_window(scan_env):
    e = scan_env.exp_by_dte
    assert compute.listed_expiry_dtes(scan_env.chain, 5, 40) == [(e[10], 10), (e[38], 38)]
    assert [d for _, d in _listed(scan_env.chain)] == [3, 10, 38, 400]
