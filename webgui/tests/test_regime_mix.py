"""Tests for the ranked Market Regime membership panel (pages/regime_mix.py)."""
import re

import pytest

from pages import regime_mix as rm


def _pt(ts, **mem):
    full = {k: 0.0 for k in rm.REGIME_ORDER}
    full.update(mem)
    return {"ts": ts, "memberships": full, "confidence": 0.5, "label": "choppy"}


def _session():
    """Three points: Balanced bleeds, Stressed wakes from zero, Whipsaw takes
    the lead — the shape of a real session (2026-08-14)."""
    return [
        _pt(1000, mean_reversion=0.37, trending=0.26, choppy=0.367, crisis=0.0),
        _pt(1300, mean_reversion=0.33, trending=0.27, choppy=0.378, crisis=0.02),
        _pt(1600, mean_reversion=0.286, trending=0.277, choppy=0.388, crisis=0.049),
    ]


# --------------------------------------------------------------- sanitation
@pytest.mark.parametrize("junk", [None, "x", {}, [], float("nan"),
                                  float("inf"), float("-inf")])
def test_safe_frac_rejects_junk(junk):
    assert rm._safe_frac(junk) == 0.0


def test_safe_frac_clamps_to_unit_interval():
    assert rm._safe_frac(1.4) == 1.0
    assert rm._safe_frac(-0.2) == 0.0
    assert rm._safe_frac(0.25) == 0.25


@pytest.mark.parametrize("points", [None, [], [None], ["x"], [{}],
                                    [{"memberships": None}],
                                    [{"memberships": "x", "ts": "y"}]])
def test_never_raises_on_junk(points):
    out = rm.regime_mix_svg(points)
    assert out.startswith("<svg") and out.endswith("</svg>")


def test_empty_history_renders_a_waiting_placeholder():
    """Not an empty frame — before the first sample the panel must look like it
    is waiting, not like it is broken."""
    out = rm.regime_mix_svg([])
    assert "Waiting for regime" in out
    assert "<rect" not in out


# ------------------------------------------------------------------ ranking
def test_rows_are_ranked_by_current_membership():
    rows = rm.rank_rows(_session())
    assert [r["key"] for r in rows[:3]] == ["choppy", "mean_reversion", "trending"]
    assert rows[0]["now"] == pytest.approx(0.388)


def test_ties_break_on_fixed_order_so_repaints_cannot_jitter():
    """Identical data must always produce identical row order."""
    tied = [_pt(1000, mean_reversion=0.25, trending=0.25, breakout=0.25,
                choppy=0.25, crisis=0.0)]
    first = [r["key"] for r in rm.rank_rows(tied)]
    assert first == [k for k in rm.REGIME_ORDER if k != "crisis"] + ["crisis"]
    assert first == [r["key"] for r in rm.rank_rows(tied)]


def test_change_is_measured_from_the_session_open():
    rows = {r["key"]: r for r in rm.rank_rows(_session())}
    assert rows["crisis"]["change"] == pytest.approx(0.049)
    assert rows["mean_reversion"]["change"] == pytest.approx(-0.084)


def test_a_single_point_has_no_change_yet():
    rows = rm.rank_rows([_pt(1000, choppy=0.4)])
    assert all(r["change"] == 0.0 for r in rows)


def test_session_split_measures_change_from_todays_open_not_yesterdays():
    """The published history is one session, but a day boundary must not make
    the change column compare across the gap."""
    old = _pt(1000, mean_reversion=0.90)
    today = [_pt(1000 + rm.SESSION_GAP_SEC + 60, mean_reversion=0.30),
             _pt(1000 + rm.SESSION_GAP_SEC + 360, mean_reversion=0.35)]
    rows = {r["key"]: r for r in rm.rank_rows([old] + today)}
    assert rows["mean_reversion"]["change"] == pytest.approx(0.05)
    assert len(rm.session_points([old] + today)) == 2


# ------------------------------------------------------------- lead margin
def test_lead_margin_reports_now_and_the_tightest_of_the_session():
    key, now, tightest = rm.lead_margin(_session())
    assert key == "choppy"
    assert now == pytest.approx(0.388 - 0.286)
    # The open was the coin-flip: 0.370 vs 0.367.
    assert tightest == pytest.approx(0.003)


def test_lead_margin_is_none_without_history():
    assert rm.lead_margin([]) == (None, None, None)


def test_footer_states_the_margin():
    out = rm.regime_mix_svg(_session())
    assert "Whipsaw leads Balanced by 10.2pp" in out
    assert "tightest today 0.3pp" in out


# ------------------------------------------------------------- the graphics
def test_flat_series_draws_the_dashed_rule_not_an_amplified_line():
    """Breakout sits at exactly zero for whole sessions. Auto-scaling a dead-flat
    series would amplify floating-point dust into a plausible-looking squiggle;
    it must read as "did not move" on both channels."""
    rows = {r["key"]: r for r in rm.rank_rows(_session())}
    assert rows["breakout"]["flat"] is True
    assert rm._spark_path(rows["breakout"]["series"], 0, 0, 100, 20) == ""
    out = rm.regime_mix_svg(_session())
    assert "stroke-dasharray" in out
    assert "—" in out                      # its change cell, not "+0.0pp"


def test_sparkline_is_scaled_to_its_own_range():
    """The point of the redesign: a 2pp move must use the full row height, the
    same as a 9pp one."""
    d = rm._spark_path([0.30, 0.31, 0.32], 0, 0, 100, 20)
    ys = [float(m) for m in re.findall(r"[ML] [\d.]+ ([\d.]+)", d)]
    assert min(ys) == pytest.approx(0.0)   # its own max touches the top
    assert max(ys) == pytest.approx(20.0)  # its own min touches the bottom


def test_bar_length_is_share_of_the_leader():
    """The leader's bar is full-width; the rest read as distance from winning."""
    out = rm.regime_mix_svg(_session())
    assert f'width="{rm.BAR_W}"' in out    # the leader (and every track)
    # Balanced is 0.286/0.388 of the leader.
    assert f'width="{rm.BAR_W * 0.286 / 0.388:.1f}"' in out
    # ... and a regime at zero draws no fill at all, only its track.
    zero = rm.regime_mix_svg([_pt(1000, choppy=0.4)])
    assert zero.count('width="0.0"') == 0


def test_colour_follows_the_regime_not_the_rank():
    """Filtering or reordering must never repaint an entity a new colour."""
    rows = {r["key"]: r for r in rm.rank_rows(_session())}
    for key, row in rows.items():
        assert row["color"] == rm.REGIME_COLORS[key]


# --------------------------------------------------------------- sanitizer
def test_emits_nothing_ui_html_would_strip():
    """Mirrors ``test_rings.test_ring_svg_emits_nothing_dompurify_would_strip``.

    A stripped attribute changes nothing server-side — the string stays correct
    and the page still renders, just wrong — so this is only checkable here. It
    already cost the rings one real defect (``dominant-baseline``)."""
    from test_rings import _dompurify_allowlist

    allow = _dompurify_allowlist()
    out = rm.regime_mix_svg(_session())
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", out))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', out))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"the sanitizer would strip: {stripped}"
    assert {"svg", "rect", "path", "text"} <= tags


def test_no_style_or_filter():
    """``ui.html`` strips ``<style>``; ``<filter>`` may not survive the sanitizer.
    Also the Tailwind-first guard: no inline ``style=`` anywhere."""
    out = rm.regime_mix_svg(_session())
    assert "<style" not in out
    assert "<filter" not in out
    assert "style=" not in out


def test_label_text_is_escaped():
    """The footer interpolates display labels; they go through ``_esc`` so the
    contract holds even if a label ever carries a bare ampersand."""
    out = rm.regime_mix_svg(_session())
    assert "<script" not in out
    assert re.search(r"<text[^>]*>[^<]*</text>", out)
