"""config/sentiment.toml's loader: defaults, and a bad value never raises."""
import composite_config as C


def test_the_shipped_defaults():
    assert C.DEFAULTS["breadth"]["min_issues"] == 500
    assert C.DEFAULTS["vix"]["max_skew_min"] == 30


def test_the_accessors_return_the_shipped_values():
    assert C.min_breadth_issues() == 500
    assert C.vix_max_skew_min() == 30


def test_a_bad_value_falls_back_to_the_default(monkeypatch):
    for bad in (None, "x", -1, 0, True, float("nan")):
        monkeypatch.setattr(C, "load", lambda bad=bad: {
            "breadth": {"min_issues": bad}, "vix": {"max_skew_min": bad}})
        assert C.min_breadth_issues() == 500
        assert C.vix_max_skew_min() == 30


def test_a_section_that_is_not_a_table_falls_back(monkeypatch):
    monkeypatch.setattr(C, "load", lambda: {"breadth": 3, "vix": "x"})
    assert C.min_breadth_issues() == 500
    assert C.vix_max_skew_min() == 30
