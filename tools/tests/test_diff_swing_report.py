"""Tests for the refit report diff (Phase 6).

Phase 0 re-ran the SAME methodology on the SAME universe two months later and
the composite's OOS IC fell 44%. Nothing announced it — the artifact just
quietly became a weaker model. This diff exists so the monthly refit cannot do
that silently again, which means the two things it must never miss are a large
drop in the measured edge and a factor whose weight changed SIGN.

Run from the repo root:
    .venv\\Scripts\\python -m pytest tools\\tests\\test_diff_swing_report.py -v
"""
from tools import diff_swing_report as D


def _report(oos, factors):
    """A minimal report in the shape `fit_swing_model.write_report` emits."""
    rows = "\n".join(
        f"| {n} | {ic:+.4f} | +0.100 | 1200 | {w:.3f} | +0.0 | +0.0 | +0.0 |"
        for n, (ic, w) in factors.items())
    return (f"# Swing model research report — 2026-08-22\n\n"
            f"**Composite OOS IC: {oos:+.4f}**  (per fold: +0.01)\n\n"
            "## Per-factor IC\n\n"
            "| factor | mean IC | ICIR | n_days | weight | IC@10 | IC@20 | IC@40 |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|\n" + rows + "\n")


BASE = {"mom_12_1": (0.0407, 0.211), "low_vol": (-0.0614, -0.344)}


class TestParsing:
    def test_it_finds_the_composite_oos_ic(self):
        assert D.parse(_report(0.0367, BASE))["oos_ic"] == 0.0367

    def test_it_finds_each_factors_ic_and_weight(self):
        got = D.parse(_report(0.02, BASE))["factors"]
        assert got["low_vol"] == (-0.0614, -0.344)

    def test_an_unparseable_report_yields_None_rather_than_a_guess(self):
        assert D.parse("not a report")["oos_ic"] is None
        assert D.parse("")["factors"] == {}


class TestTheThingsItMustNeverMiss:
    def test_a_large_drop_in_the_edge_is_called_out(self):
        """The Phase 0 case, exactly: +0.0367 → +0.0206."""
        out = D.render(D.parse(_report(0.0367, BASE)),
                       D.parse(_report(0.0206, BASE)))
        assert "-44%" in out or "-44" in out
        assert D.WARN in out

    def test_a_modest_drop_does_not_cry_wolf(self):
        out = D.render(D.parse(_report(0.0367, BASE)),
                       D.parse(_report(0.0340, BASE)))
        assert D.WARN not in out

    def test_a_WEIGHT_SIGN_FLIP_is_flagged(self):
        """A factor that changed sign is now recommending the opposite of what
        it did last month — the single most consequential silent change a refit
        can make."""
        flipped = {"mom_12_1": (0.0407, 0.211), "low_vol": (0.02, +0.344)}
        out = D.render(D.parse(_report(0.03, BASE)),
                       D.parse(_report(0.03, flipped)))
        assert "SIGN FLIPPED" in out
        assert "low_vol" in out

    def test_a_dropped_factor_is_named(self):
        out = D.render(D.parse(_report(0.03, BASE)),
                       D.parse(_report(0.03, {"mom_12_1": (0.04, 0.5)})))
        assert "dropped" in out and "low_vol" in out

    def test_a_new_factor_is_named(self):
        grown = dict(BASE, semivol=(-0.03, -0.1))
        out = D.render(D.parse(_report(0.03, BASE)),
                       D.parse(_report(0.03, grown)))
        assert "new in the fit" in out and "semivol" in out


class TestItDegrades:
    def test_an_unparseable_side_says_so_rather_than_printing_zeros(self):
        out = D.render(D.parse("garbage"), D.parse(_report(0.02, BASE)))
        assert "could not be parsed" in out
        assert "+0.0000" not in out

    def test_identical_reports_show_no_movement(self):
        r = D.parse(_report(0.03, BASE))
        out = D.render(r, r)
        assert D.WARN not in out
        assert D.FLAT in out


def test_the_output_is_ASCII_because_a_bat_pipes_it():
    """Found by running it for real: a redirected Windows stdout encodes as
    cp1252, which has no arrow. The scheduled refit died with a
    UnicodeEncodeError at exactly the moment it had a decay to report."""
    out = D.render(D.parse(_report(0.0367, BASE)),
                   D.parse(_report(0.0206, {"low_vol": (0.02, +0.3)})))
    out.encode("cp1252")          # raises if anything non-encodable slipped in
    assert out.isascii()
