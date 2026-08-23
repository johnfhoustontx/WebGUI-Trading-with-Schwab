"""Tests for the rank board (Phase 5).

The board scores today's whole universe cross-section and shows the top and
bottom of it. Four things decide whether it is honest:

  * **One code path.** A board row's composite must be what `score_symbol`
    returns for that symbol — a second scoring path would drift from the card
    silently, and the two are read side by side.
  * **Deciles come from the LIVE cross-section**, not from the artifact's
    historical bands. The bands say where a score sat against five years; the
    board answers "what is best TODAY", which is a different question.
  * **Gate-disqualified rows are MARKED, not dropped.** A dropped row is
    invisible; a marked one is a research finding ("the best-ranked name is
    three days from earnings").
  * **An empty short pool must say WHY.** In a strong uptrend the model's
    bottom band predicts LAGGING, not falling — that is a market filter, not an
    absence of candidates, and the two look identical if nothing says so.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_rank_board.py -v
"""
import copy

import pytest

from services.trade_svc import rank_board as rb
from services.trade_svc import swing_model as sm

_ARTIFACT = {
    "version": "2026-08-22", "horizon": 20,
    "regimes": {"all": {
        "weights": {"mom_12_1": 0.5, "low_vol": -0.5},
        "factor_ic": {"mom_12_1": {"mean_ic": 0.04}},
        "norm": {"mom_12_1": {"mean": 0.0, "std": 0.1},
                 "low_vol": {"mean": -0.02, "std": 0.01}},
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -0.5, "mean_fwd": -0.008,
             "hit_rate": 0.43, "n": 100},
            {"band": 1, "score_lo": -0.5, "score_hi": 0.5, "mean_fwd": 0.0,
             "hit_rate": 0.49, "n": 100},
            {"band": 2, "score_lo": 0.5, "score_hi": 3, "mean_fwd": 0.0135,
             "hit_rate": 0.523, "n": 100}],
        "oos_ic": 0.0206}}}


def _snapshot(n=20):
    """A spread cross-section.

    ⚠ `low_vol` is CONSTANT on purpose. Two linear ramps standardize to the
    same z, so against weights of +0.5 and −0.5 they cancel exactly and every
    composite comes out −0.0 — a degenerate fixture in which decile order is
    whatever the sort happened to do. A constant factor z-scores to 0 for every
    symbol, leaving `mom_12_1` to do the ranking."""
    return {"by_symbol": {
        f"S{i:02d}": {"mom_12_1": -0.25 + i * 0.025,
                      "low_vol": -0.02,
                      "below_200ema": 0.0}
        for i in range(n)}}


CLEARED = {"long": {"state": "cleared", "reasons": []},
           "short": {"state": "cleared", "reasons": []}}
UPTREND = {"long": {"state": "cleared", "reasons": ["SPY above a rising 200-DMA"]},
           "short": {"state": "relative_only",
                     "reasons": ["SPY above a rising 200-DMA"]}}


def _build(**over):
    kw = dict(snapshot=_snapshot(), artifact=_ARTIFACT, regime=None,
              clearance=CLEARED, gate_ctx=None, matrix=None)
    kw.update(over)
    return rb.build(**kw)


class TestOneCodePath:
    def test_a_row_carries_exactly_what_score_symbol_returns(self):
        snap = _snapshot()
        board = _build(snapshot=snap)
        row = next(r for r in board["rows"] if r["symbol"] == "S07")
        direct = sm.score_symbol(snap["by_symbol"]["S07"],
                                 rb.flat_basis(snap), _ARTIFACT)
        assert row["composite"] == direct["score"]
        assert row["percentile"] == direct["percentile"]
        assert row["verdict"] == direct["verdict"]

    def test_the_regime_selection_reaches_every_row(self):
        """The card scores under today's regime. A board scored under pooled
        weights would rank names by a different model than the card grades
        them with — invisible while the artifact has only `all`."""
        art = copy.deepcopy(_ARTIFACT)
        art["regimes"]["highvol"] = copy.deepcopy(art["regimes"]["all"])
        art["regimes"]["highvol"]["weights"] = {"mom_12_1": -0.5, "low_vol": 0.5}
        pooled = _build(artifact=art, regime=None)
        hv = _build(artifact=art, regime="highvol")
        assert [r["symbol"] for r in pooled["rows"]] != [r["symbol"] for r in hv["rows"]]
        assert hv["regime_key"] == "highvol"

    def test_a_symbol_the_scorer_declines_is_omitted_not_zeroed(self):
        snap = _snapshot()
        snap["by_symbol"]["DEAD"] = {}          # no usable factors
        board = _build(snapshot=snap)
        assert "DEAD" not in [r["symbol"] for r in board["rows"]]


class TestDecilesComeFromTodaysCrossSection:
    def test_the_highest_composite_is_in_the_top_decile(self):
        board = _build()
        top = max(board["rows"], key=lambda r: r["composite"])
        assert top["decile"] == 10

    def test_the_lowest_composite_is_in_the_bottom_decile(self):
        board = _build()
        bot = min(board["rows"], key=lambda r: r["composite"])
        assert bot["decile"] == 1

    def test_deciles_are_monotone_in_the_composite(self):
        rows = sorted(_build()["rows"], key=lambda r: r["composite"])
        deciles = [r["decile"] for r in rows]
        assert deciles == sorted(deciles)

    def test_they_are_NOT_the_artifact_bands(self):
        """The calibration bands rank a score against five years; the board
        ranks it against today. Given an artifact with ONE band, every name is
        historically indistinguishable — and the board must still find the best
        and the worst of them, because that is a different question."""
        one_band = copy.deepcopy(_ARTIFACT)
        one_band["regimes"]["all"]["calibration"] = [
            {"band": 0, "score_lo": -3, "score_hi": 3, "mean_fwd": 0.0,
             "hit_rate": 0.5, "n": 300}]
        board = _build(artifact=one_band)
        assert {r["band"] for r in board["rows"]} == {0}        # historically flat
        assert len({r["decile"] for r in board["rows"]}) > 1    # still ranked today


class TestPools:
    def test_the_long_pool_is_the_top_decile_and_the_short_pool_the_bottom(self):
        board = _build()
        assert all(r["decile"] == 10 for r in board["rows"] if r["pool"] == "long")
        assert all(r["decile"] == 1 for r in board["rows"] if r["pool"] == "short")
        assert board["long_pool"] and board["short_pool"]

    def test_a_thin_cross_section_forms_no_pools_and_says_so(self):
        """Distinct from an empty pool caused by the market filter — with six
        names there is no bottom decile, and reading that as 'no short
        candidates today' would be a market claim made from a sample size."""
        board = _build(snapshot=_snapshot(6))
        assert board["long_pool"] == [] and board["short_pool"] == []
        assert board["thin_cross_section"] is True
        assert board["rows"]                      # rows still render


class TestGatesAreMarkedNotDropped:
    def test_a_gated_row_is_still_present(self):
        ctx = {"earnings_days": {"S19": 2}}
        board = _build(gate_ctx=ctx)
        row = next(r for r in board["rows"] if r["symbol"] == "S19")
        assert row["gates"]
        assert row["disqualified"] is True

    def test_earnings_inside_the_horizon_gates_BOTH_sides(self):
        board = _build(gate_ctx={"earnings_days": {"S19": 2}})
        row = next(r for r in board["rows"] if r["symbol"] == "S19")
        assert any("earnings" in g.lower() for g in row["gates"])

    def test_a_name_below_its_200_ema_is_gated_for_LONGS_only(self):
        snap = _snapshot()
        snap["by_symbol"]["S19"]["below_200ema"] = -0.12
        board = _build(snapshot=snap)
        row = next(r for r in board["rows"] if r["symbol"] == "S19")
        assert any("200" in g for g in row["gates"])
        assert row["gated_long"] is True and row["gated_short"] is False

    def test_a_squeeze_gates_SHORTS_only(self):
        board = _build(gate_ctx={"squeeze": {"S00": "days-to-cover 17.1"}})
        row = next(r for r in board["rows"] if r["symbol"] == "S00")
        assert row["gated_short"] is True and row["gated_long"] is False

    def test_an_ungated_row_does_not_claim_the_cards_full_clearance(self):
        """The board evaluates a SUBSET of the card's gates. A row with no
        gates must not read as 'passed everything', so the payload names what
        was actually checked."""
        board = _build()
        assert board["gates_evaluated"]
        assert all(isinstance(g, str) for g in board["gates_evaluated"])


class TestTheMarketFilterExplainsAnEmptyShortPool:
    def test_a_relative_only_short_side_is_reported_as_a_market_filter(self):
        board = _build(clearance=UPTREND)
        assert board["market_filter"]["short"]["state"] == "relative_only"
        assert board["market_filter"]["short"]["reasons"]

    def test_the_short_pool_still_populates_but_is_labelled_relative(self):
        """A bottom-band name in an uptrend is predicted to LAG, not to fall.
        Dropping the pool would hide a real research output; leaving it
        unlabelled would invite a directional short the tape has refused."""
        board = _build(clearance=UPTREND)
        assert board["short_pool"]
        assert board["short_expression"] == "relative"

    def test_a_cleared_tape_expresses_the_short_directionally(self):
        assert _build(clearance=CLEARED)["short_expression"] == "directional"


class TestItCarriesThePhase4Disclosure:
    def test_the_board_states_the_models_volatility_weight(self):
        """The board RANKS by this composite, so 'the top decile is the
        highest-beta names' is the single most important thing about it."""
        board = _build()
        assert board["risk_share"] == pytest.approx(0.5)

    def test_the_model_version_travels_with_the_board(self):
        assert _build()["model_version"] == "2026-08-22"


class TestNeverRaises:
    @pytest.mark.parametrize("kw", [
        {"snapshot": None}, {"snapshot": {}}, {"artifact": None},
        {"snapshot": {"by_symbol": {}}}, {"clearance": None},
    ])
    def test_a_degraded_input_yields_a_board_shaped_dict(self, kw):
        board = _build(**kw)
        assert set(board) >= {"rows", "long_pool", "short_pool", "market_filter"}
        assert board["rows"] == [] or isinstance(board["rows"], list)


# ── Handler wiring ───────────────────────────────────────────────────────────

class TestTheHandlerPublishesOnce:
    def test_an_unchanged_board_does_not_bump_the_version(self, monkeypatch):
        """`skip_unchanged` is the whole point: the board only moves when the
        daily universe snapshot does, and a page polling its version must not
        repaint on every identical rebuild."""
        from shared.bus import Bus
        from services.trade_svc import handlers as H
        from services.trade_svc import compute as C

        monkeypatch.setattr(C, "build_rank_board", lambda: _build())
        bus = Bus(fake=True)
        H.rank_board(bus)
        v1 = bus.cache_version(H.CACHE_RANK_BOARD)
        H.rank_board(bus)
        assert bus.cache_version(H.CACHE_RANK_BOARD) == v1

    def test_the_published_payload_survives_the_contract(self, monkeypatch):
        from shared.bus import Bus
        from services.trade_svc import handlers as H
        from services.trade_svc import compute as C

        monkeypatch.setattr(C, "build_rank_board", lambda: _build())
        bus = Bus(fake=True)
        H.rank_board(bus)
        payload = bus.cache_get(H.CACHE_RANK_BOARD).payload
        assert payload["rows"] and payload["long_pool"]
        assert payload["gates_evaluated"]
        assert payload["risk_share"] == pytest.approx(0.5)

    def test_the_command_type_is_dispatched(self, monkeypatch):
        from shared.bus import Bus
        from services.trade_svc import handlers as H
        from services.trade_svc import compute as C

        monkeypatch.setattr(C, "build_rank_board", lambda: _build())
        bus = Bus(fake=True)

        class _Cmd:
            type = "rank_board"
            args = {}

        H.handle_command(bus, _Cmd())
        assert bus.cache_get(H.CACHE_RANK_BOARD) is not None


# ── The legacy snapshot shape (found live, 2026-08-22) ───────────────────────
# `get_universe_snapshot` deliberately tolerates a payload written by older code
# that carries only the FLAT `{factor: [values]}` basis — scoring works fine
# against it, and its docstring says so. Ranking does not: the board needs the
# symbol NAMES, which the flat shape does not carry.
#
# Found on the first live build, where it rendered as a board with zero rows —
# indistinguishable from "the market offered nothing today". An empty board must
# say which kind of empty it is.

class TestItDistinguishesKindsOfEmpty:
    def test_a_healthy_build_reports_ok(self):
        assert _build()["status"] == "ok"

    def test_no_snapshot_at_all_says_so(self):
        assert _build(snapshot=None)["status"] == "no_snapshot"
        assert _build(snapshot={})["status"] == "no_snapshot"

    def test_a_LEGACY_flat_snapshot_is_named_rather_than_rendered_empty(self):
        """The flat basis has values but no symbols. A board built from it is
        empty for a DATA-SHAPE reason, not a market reason, and the two look
        identical without this."""
        legacy = {"factors": {"mom_12_1": [0.1, 0.2, 0.3], "low_vol": [-0.02] * 3}}
        board = _build(snapshot=legacy)
        assert board["status"] == "legacy_snapshot"
        assert board["rows"] == []

    def test_a_missing_artifact_is_its_own_status(self):
        assert _build(artifact=None)["status"] == "no_artifact"

    def test_a_snapshot_the_scorer_declines_entirely_is_not_called_ok(self):
        board = _build(snapshot={"by_symbol": {"A": {}, "B": {}}})
        assert board["status"] == "unscoreable"


class TestTheBoardSelfHealsALegacySnapshot:
    def test_it_rebuilds_when_the_snapshot_carries_no_symbol_names(self, monkeypatch):
        """Waiting out the day would leave the board empty for a data-shape
        reason while looking like a quiet market. The rebuild costs the daily
        fan-out we would have paid tomorrow anyway."""
        from services.trade_svc import compute as C

        calls = []
        monkeypatch.setattr(C, "get_universe_snapshot",
                            lambda: {"factors": {"mom_12_1": [0.1, 0.2]}})
        monkeypatch.setattr(C, "build_universe_factor_snapshot",
                            lambda: (calls.append(1), _snapshot())[1])
        monkeypatch.setattr(C, "_write_universe_snapshot", lambda s: None)
        monkeypatch.setattr(C, "_price_history", lambda *a, **k: None)
        monkeypatch.setattr(C, "_board_gate_ctx", lambda syms: {})
        from services.trade_svc import swing_model as _sw
        monkeypatch.setattr(_sw, "load_artifact", lambda: _ARTIFACT)

        board = C.build_rank_board()
        assert calls, "a legacy-shaped snapshot must trigger one rebuild"
        assert board["status"] == "ok" and board["rows"]

    def test_a_healthy_snapshot_does_NOT_trigger_a_rebuild(self, monkeypatch):
        from services.trade_svc import compute as C

        calls = []
        monkeypatch.setattr(C, "get_universe_snapshot", lambda: _snapshot())
        monkeypatch.setattr(C, "build_universe_factor_snapshot",
                            lambda: calls.append(1))
        monkeypatch.setattr(C, "_price_history", lambda *a, **k: None)
        monkeypatch.setattr(C, "_board_gate_ctx", lambda syms: {})
        from services.trade_svc import swing_model as _sw
        monkeypatch.setattr(_sw, "load_artifact", lambda: _ARTIFACT)

        C.build_rank_board()
        assert not calls


def test_the_contract_projection_preserves_every_field_the_board_sets(monkeypatch):
    """The handler projects the board onto `RankBoard`, so a field the contract
    lacks is dropped between the service and the page — silently, and only
    visible end to end. `status` was added to the builder and the page and was
    missing here."""
    from shared.contracts.trade import RankBoard
    from services.trade_svc import handlers as H

    built = set(_build())
    modelled = set(RankBoard.model_fields)
    projected = set(H._BOARD_FIELDS)
    missing = built - modelled - {"pool"}
    assert not missing, f"board fields the contract drops: {sorted(missing)}"
    assert (built & modelled) <= projected, (
        "fields the contract models but the handler never projects: "
        f"{sorted((built & modelled) - projected)}")


# ── Dealer / IV / side metric columns (terminal redesign) ────────────────────
# The Signal Desk rank tables carry a dealer regime, an IV reading and a
# side-specific metric. All three are joined from data the app ALREADY holds —
# one read of the options matrix for the whole board, and the short-interest
# store for the short side. Nothing here is computed from scratch, and a symbol
# the matrix does not carry reads as absent rather than as a neutral value.

class TestJoinedColumns:
    _MATRIX = {"NVDA": {"dealer_regime": "Above flip", "atm_iv": 31.4,
                        "iv_state": "cheap"},
               "S00": {"dealer_regime": "Below flip", "atm_iv": 58.0,
                       "iv_state": "rich"}}

    def test_a_row_carries_its_dealer_regime_and_iv(self):
        board = _build(matrix=self._MATRIX)
        row = next(r for r in board["rows"] if r["symbol"] == "S00")
        assert row["dealer"] == "Below flip"
        assert row["atm_iv"] == 58.0
        assert row["iv_state"] == "rich"

    def test_a_symbol_the_matrix_lacks_reads_ABSENT_not_neutral(self):
        """`not collected` and `at the flip` are different claims, and the
        off-hours case turns on the distinction."""
        board = _build(matrix=self._MATRIX)
        row = next(r for r in board["rows"] if r["symbol"] == "S05")
        assert row["dealer"] is None
        assert row["atm_iv"] is None

    def test_no_matrix_at_all_leaves_every_row_absent(self):
        board = _build(matrix=None)
        assert all(r["dealer"] is None for r in board["rows"])

    def test_the_short_side_metric_is_days_to_cover(self):
        board = _build(gate_ctx={"days_to_cover": {"S00": 17.1}})
        row = next(r for r in board["rows"] if r["symbol"] == "S00")
        assert row["dtc"] == 17.1

    def test_a_name_with_no_short_interest_has_no_days_to_cover(self):
        board = _build()
        assert all(r["dtc"] is None for r in board["rows"])
