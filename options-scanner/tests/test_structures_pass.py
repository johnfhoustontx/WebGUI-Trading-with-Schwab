"""``run_full_scan``'s structures pass - everything that is not a credit spread.

The pass is ADDITIVE, and that is what these tests are mostly about: it builds
into its own two lists, it must never move a row in the three lists that existed
before it, and a crash inside it must cost nothing but itself.

The fixture chain (``_chain_at``) is deliberately not a realistic surface - most
candidates built from it fail a hard gate - so the tests that need rows open the
quality cut (``_open``) and the tests about the cut say so.

Design: docs/plans/2026-10-06-scanner-multi-structure-design.md.
"""
import importlib
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner_engine  # noqa: E402
import structure_scan  # noqa: E402

# The scan fixture and its helpers live with the tests they were written for.
# ``fake_client`` is a pytest fixture: importing the name is what registers it
# here (read by pytest, not by this module).
from tests.test_scanner_engine import (  # noqa: E402,F401
    _AFTER_CLOSE, _FUNNEL_SYMBOLS, _PRE_OPEN, _boom, _per_symbol, _signal_ids,
    fake_client)

LISTS = (("structures_0dte", "STRUCT_0DTE"), ("structures_swing", "STRUCT_SWING"))
OLD_LISTS = ("signals_0dte", "signals_swing", "signals_directional")


def _cfg(monkeypatch, **over):
    monkeypatch.setattr(scanner_engine, "STRUCTURES_CFG",
                        {**scanner_engine.STRUCTURES_CFG, **over})


def _open(monkeypatch, **over):
    """No quality cut and no cap: every candidate the builders offer is a row."""
    _cfg(monkeypatch, **{"min_score": 0.0, "excluded_grades": [],
                         "max_per_family": 0, **over})


def _scan(client, **kw):
    return scanner_engine.run_full_scan(client, symbols=_FUNNEL_SYMBOLS, **kw)


def _partition(b):
    return (b["vol_gate"] + b["earnings"] + b["score_cut"] + b["capped"]
            + b["outside_rth"] + b["emitted"])


class TestTheListsExist:
    def test_the_result_carries_both_lists(self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            assert isinstance(res[key], list) and res[key], key

    def test_rows_are_the_normalized_shape_with_a_score_and_a_group(
            self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            for row in res[key]:
                assert row["legs"] and row["type"] and row["id"], key
                assert row["group"] in ("VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR")
                assert isinstance(row["composite_score"], (int, float))
                assert row["grade"]

    def test_each_list_holds_its_own_window(self, fake_client, monkeypatch):
        """The fixture lists 1 DTE and 7 DTE. A straddle on the 0-DTE list at 1
        DTE is the operator decision of 2026-10-06 (the Finder would refuse it)."""
        _open(monkeypatch)
        res = _scan(fake_client)
        assert {r["dte"] for r in res["structures_0dte"]} == {1}
        assert {r["dte"] for r in res["structures_swing"]} == {7}
        assert "LONG_STRADDLE" in {r["type"] for r in res["structures_0dte"]}

    def test_rows_are_sorted_best_first(self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            scores = [r["composite_score"] for r in res[key]]
            assert scores == sorted(scores, reverse=True), key
            assert len({r["symbol"] for r in res[key]}) == 2      # both symbols mixed

    def test_rows_carry_the_symbols_iv_block(self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            for row in res[key]:
                iv = res["iv_data"][row["symbol"]]
                assert row["iv_rank"] == (iv.get("iv_rank") or 0)
                assert row["current_iv"] == iv.get("current_iv")
                assert "expected_moves" in row and "iv_low_52w" in row

    def test_the_lists_serialize(self, fake_client, monkeypatch):
        """They exist to be published."""
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            assert json.loads(json.dumps(res[key])) == json.loads(
                json.dumps(res[key]))
            json.dumps(res["funnel"])


class TestThePassIsAdditive:
    def test_the_old_lists_do_not_move_when_the_pass_runs(
            self, fake_client, monkeypatch):
        _cfg(monkeypatch, enabled=False)
        off = _scan(fake_client)
        _open(monkeypatch, enabled=True)
        on = _scan(fake_client)
        assert on["structures_swing"]                             # vacuity
        for key in OLD_LISTS:
            assert off[key], key                                  # vacuity
            assert _signal_ids(on[key]) == _signal_ids(off[key]), key

    def test_switched_off_builds_nothing_and_reports_no_failure(
            self, fake_client, monkeypatch):
        _cfg(monkeypatch, enabled=False)
        res = _scan(fake_client)
        for key, bucket in LISTS:
            assert res[key] == []
            for entry in res["funnel"].values():
                b = entry["buckets"][bucket]
                assert b["built"] == 0 and b["emitted"] == 0
                assert b["build_failed"] is False

    def test_a_crash_in_the_pass_costs_only_the_pass(self, fake_client, monkeypatch):
        _cfg(monkeypatch, enabled=False)
        off = _scan(fake_client)
        _open(monkeypatch, enabled=True)
        monkeypatch.setattr(structure_scan, "build_window", _boom)
        res = _scan(fake_client)
        for key in OLD_LISTS:
            assert _signal_ids(res[key]) == _signal_ids(off[key]), key
        for key, bucket in LISTS:
            assert res[key] == []
            for entry in res["funnel"].values():
                assert entry["buckets"][bucket]["build_failed"] is True

    def test_a_crash_in_one_window_leaves_the_other(self, fake_client, monkeypatch):
        _open(monkeypatch)
        real = structure_scan.build_window

        def _zero_dte_raises(chain, symbol, spot, atm_iv, dte_min, dte_max, **kw):
            if dte_min == 0:
                raise RuntimeError("boom")
            return real(chain, symbol, spot, atm_iv, dte_min, dte_max, **kw)

        monkeypatch.setattr(structure_scan, "build_window", _zero_dte_raises)
        res = _scan(fake_client)
        assert res["structures_0dte"] == [] and res["structures_swing"]
        for entry in res["funnel"].values():
            assert entry["buckets"]["STRUCT_0DTE"]["build_failed"] is True
            assert entry["buckets"]["STRUCT_SWING"]["build_failed"] is False

    def test_a_crash_in_select_is_caught_too(self, fake_client, monkeypatch):
        _open(monkeypatch)
        monkeypatch.setattr(structure_scan, "select", _boom)
        res = _scan(fake_client)
        assert res["structures_0dte"] == [] and res["signals_0dte"]
        assert all(e["buckets"]["STRUCT_SWING"]["build_failed"]
                   for e in res["funnel"].values())

    def test_nothing_from_the_pass_reaches_the_recorder(self, fake_client, monkeypatch):
        """Capture is a later phase, and it will be its own call: the paper
        Account enters from what ``record_signals`` writes."""
        import signal_recorder

        _open(monkeypatch)
        seen = []
        monkeypatch.setattr(signal_recorder, "record_signals",
                            lambda sigs, kind, **kw: seen.append(
                                (kind, {s["type"] for s in sigs})))
        res = _scan(fake_client)
        assert res["structures_swing"]                            # vacuity
        assert [k for k, _ in seen] == ["0DTE", "SWING"]
        for _kind, types in seen:
            assert types <= {"PCS", "CCS", "IC"}, types


class TestTheFunnel:
    def test_every_symbol_has_both_structure_buckets(self, fake_client):
        res = _scan(fake_client)
        for entry in res["funnel"].values():
            for _key, bucket in LISTS:
                assert sorted(entry["buckets"][bucket]) == sorted(
                    scanner_engine.STRUCT_FUNNEL_KEYS + ("build_failed",))

    def test_the_buckets_partition_at_the_shipped_settings(self, fake_client):
        res = _scan(fake_client)
        built = 0
        for entry in res["funnel"].values():
            for _key, bucket in LISTS:
                b = entry["buckets"][bucket]
                assert b["built"] == _partition(b), bucket
                assert b["build_failed"] is False
                built += b["built"]
        assert built > 0                                          # vacuity

    def test_the_buckets_partition_with_every_door_in_use(
            self, fake_client, monkeypatch):
        """A cap of one per family and a cut in the middle of the score range,
        so ``score_cut`` and ``capped`` are both non-zero."""
        _cfg(monkeypatch, min_score=30.0, excluded_grades=[], max_per_family=1)
        res = _scan(fake_client)
        used = {"score_cut": 0, "capped": 0, "emitted": 0}
        for entry in res["funnel"].values():
            for _key, bucket in LISTS:
                b = entry["buckets"][bucket]
                assert b["built"] == _partition(b), bucket
                for k in used:
                    used[k] += b[k]
        assert used["capped"] > 0 and used["emitted"] > 0, used

    def test_emitted_is_the_symbols_count_in_the_final_list(
            self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for key, bucket in LISTS:
            counts = _per_symbol(res[key])
            assert sum(counts.values()) > 0, key                  # vacuity
            for sym, entry in res["funnel"].items():
                assert entry["buckets"][bucket]["emitted"] == counts.get(sym, 0)

    def test_counting_never_moves_a_structure(self, fake_client, monkeypatch):
        _cfg(monkeypatch, min_score=30.0, excluded_grades=[], max_per_family=1)
        counted = _scan(fake_client)
        plain = _scan(fake_client, collect_funnel=False)
        assert plain["funnel"] == {}
        for key, _bucket in LISTS:
            assert counted[key], key                              # vacuity
            assert _signal_ids(counted[key]) == _signal_ids(plain[key]), key


class TestRegularHours:
    @pytest.mark.parametrize("when", [_PRE_OPEN, _AFTER_CLOSE])
    def test_outside_the_session_the_lists_are_held(
            self, fake_client, monkeypatch, when):
        _open(monkeypatch)
        base = _scan(fake_client)
        monkeypatch.setattr(scanner_engine, "_signal_clock", lambda: when)
        out = _scan(fake_client)
        for key, bucket in LISTS:
            assert base[key], key                                 # vacuity
            assert out[key] == [], key
            for sym in _FUNNEL_SYMBOLS:
                was = base["funnel"][sym]["buckets"][bucket]["emitted"]
                b = out["funnel"][sym]["buckets"][bucket]
                assert b["outside_rth"] == was and b["emitted"] == 0, (sym, bucket)
                assert b["built"] == _partition(b), (sym, bucket)

    def test_in_session_nothing_is_held(self, fake_client, monkeypatch):
        _open(monkeypatch)
        res = _scan(fake_client)
        for entry in res["funnel"].values():
            for _key, bucket in LISTS:
                assert entry["buckets"][bucket]["outside_rth"] == 0


class TestTheGates:
    def test_the_family_list_is_read(self, fake_client, monkeypatch):
        _open(monkeypatch, families=["VERTICAL"])
        res = _scan(fake_client)
        types = {r["type"] for key, _b in LISTS for r in res[key]}
        assert types and types <= {"BULL_CALL", "BEAR_PUT"}

    def test_the_quality_cut_is_read(self, fake_client, monkeypatch):
        _open(monkeypatch)
        everything = _scan(fake_client)
        _open(monkeypatch, min_score=101.0)
        nothing = _scan(fake_client)
        for key, bucket in LISTS:
            assert everything[key] and nothing[key] == []
            for entry in nothing["funnel"].values():
                b = entry["buckets"][bucket]
                assert b["score_cut"] == b["built"] > 0

    def test_the_cap_is_per_family(self, fake_client, monkeypatch):
        _open(monkeypatch, max_per_family=1)
        res = _scan(fake_client)
        for key, _bucket in LISTS:
            seen = {}
            for r in res[key]:
                seen[(r["symbol"], r["group"])] = seen.get((r["symbol"], r["group"]), 0) + 1
            assert seen and set(seen.values()) == {1}, key

    def test_short_premium_under_the_iv_floor_is_gated(self, fake_client, monkeypatch):
        """The fixture measures SPY at IV rank 100 and QQQ at 50; a floor of 75
        gates QQQ's short strangle and leaves SPY's, in the same scan."""
        _open(monkeypatch)
        monkeypatch.setattr(scanner_engine, "MIN_IV_RANK", {"0-DTE": 75, "SWING": 75})
        res = _scan(fake_client)
        rows = [r for key, _b in LISTS for r in res[key]]
        short = {r["symbol"] for r in rows if (r.get("net_vega") or 0) < 0}
        assert short == {"SPY"}
        assert {r["symbol"] for r in rows if (r.get("net_vega") or 0) > 0} == {"SPY", "QQQ"}
        gated = sum(e["buckets"][b]["vol_gate"] for e in res["funnel"].values()
                    for _k, b in LISTS)
        assert gated > 0 and res["funnel"]["SPY"]["buckets"]["STRUCT_SWING"]["vol_gate"] == 0


class TestEarnings:
    def _report_today(self, monkeypatch, symbol="SPY"):
        today = date.today().isoformat()
        monkeypatch.setattr(scanner_engine, "scan_earnings_dates",
                            lambda symbols, db_path=None: {
                                s: (today if s == symbol else None) for s in symbols})
        return today

    def test_long_premium_is_kept_and_flagged_and_short_premium_dropped(
            self, fake_client, monkeypatch):
        _open(monkeypatch)
        today = self._report_today(monkeypatch)
        res = _scan(fake_client)
        spy = [r for key, _b in LISTS for r in res[key] if r["symbol"] == "SPY"]
        qqq = [r for key, _b in LISTS for r in res[key] if r["symbol"] == "QQQ"]
        assert spy, "long premium should survive a report"
        for r in spy:
            assert r["net_vega"] > 0, r["type"]
            assert r["spans_earnings"] is True and r["earnings_date"] == today
        # The other symbol, with no report, keeps its short premium and no flag.
        assert any((r.get("net_vega") or 0) < 0 for r in qqq)
        assert not any(r.get("spans_earnings") for r in qqq)
        assert res["funnel"]["SPY"]["buckets"]["STRUCT_SWING"]["earnings"] > 0
        assert res["funnel"]["QQQ"]["buckets"]["STRUCT_SWING"]["earnings"] == 0

    def test_drop_mode_removes_every_row_held_through_the_report(
            self, fake_client, monkeypatch):
        _open(monkeypatch, earnings_long_premium="drop")
        self._report_today(monkeypatch)
        res = _scan(fake_client)
        rows = [r for key, _b in LISTS for r in res[key]]
        assert rows and {r["symbol"] for r in rows} == {"QQQ"}
        b = res["funnel"]["SPY"]["buckets"]["STRUCT_SWING"]
        assert b["earnings"] == b["built"] - b["vol_gate"] > 0

    def test_the_credit_lists_keep_their_own_earnings_rule(
            self, fake_client, monkeypatch):
        """Flag mode is about the structures pass alone: a credit spread held
        through a report is still dropped."""
        _open(monkeypatch)
        self._report_today(monkeypatch)
        res = _scan(fake_client)
        for key in ("signals_0dte", "signals_swing"):
            assert "SPY" not in {r["symbol"] for r in res[key]}, key
            assert "QQQ" in {r["symbol"] for r in res[key]}, key


class TestConfigIsRead:
    def test_the_engine_constant_is_the_config(self):
        from shared import scanner_config
        assert scanner_engine.STRUCTURES_CFG == scanner_config.structures()

    def test_the_engine_actually_reads_it(self, monkeypatch):
        """Equality proves nothing on its own. Move the config and require the
        engine to follow; module constants resolve at import, so this reloads."""
        from shared import scanner_config
        moved = {**scanner_config.structures(), "max_per_family": 7,
                 "families": ["STRADDLE"]}
        monkeypatch.setattr(scanner_config, "structures", lambda: dict(moved))
        try:
            importlib.reload(scanner_engine)
            assert scanner_engine.STRUCTURES_CFG == moved
        finally:
            monkeypatch.undo()
            importlib.reload(scanner_engine)


class TestDecimalAtmIv:
    """Extracted from the single-leg block so both passes share one reading."""

    def test_it_comes_from_the_daily_expected_move(self):
        import math
        iv = scanner_engine.decimal_atm_iv(1.4655, 100.0, {"current_iv": 55.0})
        assert iv == pytest.approx(1.4655 * math.sqrt(365.0) / 100.0)

    @pytest.mark.parametrize("civ,expected", [(28.0, 0.28), (0.28, 0.28),
                                              (None, 0.20), (0, 0.20)])
    def test_the_fallback_reads_a_percent_as_a_percent(self, civ, expected):
        assert scanner_engine.decimal_atm_iv(0, 100.0, {"current_iv": civ}) == \
            pytest.approx(expected)

    def test_a_missing_price_takes_the_fallback(self):
        assert scanner_engine.decimal_atm_iv(1.5, 0, {"current_iv": 30.0}) == \
            pytest.approx(0.30)
        assert scanner_engine.decimal_atm_iv(1.5, 100.0, None) > 0
