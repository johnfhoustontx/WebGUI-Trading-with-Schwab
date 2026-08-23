"""Tests for the live IC monitor (Phase 6).

The monitor answers "is the edge holding?" from the journal's labelled rows.
Four things decide whether its answer means anything:

  * **The live statistic is NOT the fit's statistic.** The artifact's OOS IC is a
    mean of per-DATE cross-sectional Spearman correlations. Live readings are
    sparse — a handful of symbols a day — so a per-date IC mostly cannot be
    computed at all. The monitor reports a POOLED correlation and says plainly
    that it is not the same number, rather than printing it under the same name.
  * **It must be beta-aware.** This model's measured edge is beta (Phase 4), so
    an IC computed on raw excess return would read healthy through any rising
    market. The monitor computes both and splits on the market's direction.
  * **Too little data is an answer.** Three readings is not a thin edge, it is
    no measurement, and it must not render as one.
  * **Long and short are reported separately**, because a model whose longs work
    and whose shorts do not is a different product from one that works.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_live_ic.py -v
"""
import pytest

from services.trade_svc import live_ic


def _rows(n=40, signal=1.0, key="fwd_20d", start_day=1, mkt=0.01):
    """`n` labelled readings whose composite predicts the forward with `signal`
    as a multiplier (negative inverts the relationship)."""
    out = []
    for i in range(n):
        comp = (i - n / 2) / n
        out.append({
            "symbol": f"S{i:02d}",
            "reading_date": f"2026-06-{(start_day + i % 20):02d}",
            "composite": comp,
            "band": 4 if comp > 0.2 else (0 if comp < -0.2 else 2),
            "swing_verdict": "BUY" if comp > 0.2 else ("SELL" if comp < -0.2 else "HOLD"),
            key: signal * comp * 0.05,
            "mkt_fwd_20d": mkt,
            "labeled_at": "2026-07-25T00:00:00Z",
        })
    return out


class TestItRefusesToMeasureTooLittle:
    @pytest.mark.parametrize("n", [0, 1, 5])
    def test_a_thin_journal_reports_no_reading_rather_than_a_number(self, n):
        out = live_ic.compute(_rows(n))
        assert out["status"] == "insufficient"
        assert out["pooled_ic"] is None

    def test_it_says_how_many_more_it_needs(self):
        out = live_ic.compute(_rows(5))
        assert out["n_labelled"] == 5
        assert out["min_required"] > 5

    def test_enough_rows_produce_a_reading(self):
        assert live_ic.compute(_rows(40))["status"] == "ok"


class TestThePooledStatisticIsLabelledAsDifferent:
    def test_it_reports_a_pooled_ic(self):
        out = live_ic.compute(_rows(40, signal=1.0))
        assert out["pooled_ic"] > 0.5

    def test_an_inverted_relationship_reads_negative(self):
        out = live_ic.compute(_rows(40, signal=-1.0))
        assert out["pooled_ic"] < -0.5

    def test_the_by_date_ic_is_None_when_no_date_has_enough_names(self):
        """The comparable-to-the-artifact statistic. Live readings are sparse,
        so this is usually absent — and absent is the honest answer, not a
        pooled number wearing its name."""
        rows = _rows(12)
        for i, r in enumerate(rows):
            r["reading_date"] = f"2026-06-{i + 1:02d}"      # one name per date
        out = live_ic.compute(rows)
        assert out["by_date_ic"] is None
        assert out["comparable_to_artifact"] is False

    def test_a_dense_day_does_produce_the_comparable_statistic(self):
        rows = _rows(40)
        for r in rows:
            r["reading_date"] = "2026-06-15"                # all on one date
        out = live_ic.compute(rows)
        assert out["by_date_ic"] is not None
        assert out["comparable_to_artifact"] is True


class TestBetaAwareness:
    def test_it_computes_the_ic_on_the_BETA_ADJUSTED_label_too(self):
        """The shape Phase 4 found: a strong raw relationship with nothing left
        once leverage is paid for."""
        rows = _rows(40, signal=1.0)
        for i, r in enumerate(rows):            # varies, but unrelated to score
            r["fwd_20d_ba"] = ((i * 7919) % 101 - 50) / 1000.0
        out = live_ic.compute(rows)
        assert out["pooled_ic"] > 0.5
        assert out["pooled_ic_beta_adj"] is not None
        assert abs(out["pooled_ic_beta_adj"]) < 0.35

    def test_a_CONSTANT_label_yields_None_rather_than_a_zero_correlation(self):
        """A constant outcome has no ordering, so a rank correlation is
        undefined. It matters because the labeler stores NULL for an unmatured
        horizon — if it ever wrote 0.0 instead, this is what stops the monitor
        printing a confident 0.00 IC over a column of nothing."""
        rows = _rows(40)
        for r in rows:
            r["fwd_20d_ba"] = 0.0
        assert live_ic.compute(rows)["pooled_ic_beta_adj"] is None

    def test_an_unlabelled_beta_column_yields_None_not_zero(self):
        out = live_ic.compute(_rows(40))         # no fwd_20d_ba at all
        assert out["pooled_ic_beta_adj"] is None

    def test_it_splits_the_reading_by_the_markets_own_direction(self):
        """The split Phase 4 showed to be decisive: an edge that only exists
        while the market rises is exposure, not skill."""
        up = _rows(20, signal=1.0, mkt=0.02)
        down = _rows(20, signal=-1.0, mkt=-0.02, start_day=1)
        out = live_ic.compute(up + down)
        assert out["ic_market_up"] > 0.3
        assert out["ic_market_down"] < -0.3

    def test_a_one_sided_sample_reports_the_missing_half_as_None(self):
        out = live_ic.compute(_rows(40, mkt=0.02))
        assert out["ic_market_up"] is not None
        assert out["ic_market_down"] is None


class TestLongShortSplit:
    def test_the_two_sides_are_reported_separately(self):
        out = live_ic.compute(_rows(40))
        assert out["long"]["n"] > 0 and out["short"]["n"] > 0

    def test_each_side_carries_its_own_mean_outcome(self):
        out = live_ic.compute(_rows(40, signal=1.0))
        assert out["long"]["mean_fwd"] > 0
        assert out["short"]["mean_fwd"] < 0

    def test_a_side_with_no_readings_is_None_rather_than_zero(self):
        rows = [r for r in _rows(40) if r["swing_verdict"] != "SELL"]
        out = live_ic.compute(rows)
        assert out["short"]["n"] == 0
        assert out["short"]["mean_fwd"] is None


class TestDecayAgainstTheArtifact:
    def test_it_compares_the_comparable_statistic_only(self):
        rows = _rows(40)
        for r in rows:
            r["reading_date"] = "2026-06-15"
        out = live_ic.compute(rows, artifact_oos_ic=0.02)
        assert out["artifact_oos_ic"] == 0.02
        assert out["decay"] is not None

    def test_it_refuses_to_compare_when_only_the_pooled_stat_exists(self):
        """Comparing a pooled correlation to a mean of per-date correlations is
        an apples-to-oranges error that would look like a decay finding."""
        out = live_ic.compute(_rows(40), artifact_oos_ic=0.02)
        assert out["by_date_ic"] is None
        assert out["decay"] is None

    def test_no_artifact_ic_means_no_comparison(self):
        rows = _rows(40)
        for r in rows:
            r["reading_date"] = "2026-06-15"
        assert live_ic.compute(rows)["decay"] is None


class TestNeverRaises:
    @pytest.mark.parametrize("rows", [None, [], [{}], [{"composite": None}]])
    def test_a_degraded_input_yields_a_monitor_shaped_dict(self, rows):
        out = live_ic.compute(rows)
        assert set(out) >= {"status", "pooled_ic", "n_labelled", "long", "short"}


# ── Per-symbol history (Phase 7 / terminal redesign) ─────────────────────────
# The Evidence screen shows "this name's last five reads and what followed",
# which is the journal filtered to one symbol. It is a different question from
# the model-wide IC: five reads can never support a correlation, so this returns
# ROWS, not a statistic — and every row says plainly whether its outcome is
# known yet.

class TestSymbolHistory:
    def _rows(self):
        return [
            {"symbol": "AAPL", "reading_date": "2026-07-01", "percentile": 90,
             "swing_verdict": "BUY", "fwd_20d": 0.021, "composite": 0.8},
            {"symbol": "AAPL", "reading_date": "2026-06-15", "percentile": 70,
             "swing_verdict": "HOLD", "fwd_20d": -0.004, "composite": 0.2},
            {"symbol": "AAPL", "reading_date": "2026-08-20", "percentile": 90,
             "swing_verdict": "BUY", "fwd_20d": None, "composite": 0.9},
        ]

    def test_it_returns_rows_newest_first(self):
        out = live_ic.symbol_history(self._rows())
        assert [r["date"] for r in out] == ["2026-08-20", "2026-07-01", "2026-06-15"]

    def test_an_unmatured_read_says_PENDING_rather_than_showing_a_zero(self):
        out = live_ic.symbol_history(self._rows())
        assert out[0]["result"] is None
        assert out[0]["pending"] is True

    def test_a_matured_read_carries_its_outcome_and_direction(self):
        out = live_ic.symbol_history(self._rows())
        row = next(r for r in out if r["date"] == "2026-07-01")
        assert row["result"] == pytest.approx(0.021)
        assert row["pending"] is False

    def test_it_caps_the_list(self):
        many = [dict(self._rows()[0], reading_date=f"2026-07-{d:02d}")
                for d in range(1, 12)]
        assert len(live_ic.symbol_history(many, limit=5)) == 5

    def test_no_rows_is_an_empty_list(self):
        assert live_ic.symbol_history(None) == []
        assert live_ic.symbol_history([]) == []
