"""The Dealer Positioning colours an operator can choose (``config/theme.toml``
``[gamma]``, Settings -> Appearance): the put and the call colour, and the three
other shades each side of the heat map grades through.

Asked for by the user 2026-10-11.
"""
import copy

import pytest

from pages.options import theme

SHIPPED = {
    "put_peak": "#ffbadc", "put": "#ff4d8d", "put_mid": "#96247a", "put_low": "#7a2c5c",
    "call_low": "#2a5f9b", "call_mid": "#2a76e0", "call": "#35c8ff", "call_peak": "#bef8ff",
}


def _theme(**gamma):
    t = copy.deepcopy(theme._DEFAULTS)
    t["gamma"].update(gamma)
    return t


def test_the_shipped_colours_are_the_plasma_ramp_the_page_always_drew():
    """Nothing moves until an operator chooses: these are the eight colours the
    heat map's stops held as literals before they could be chosen."""
    assert theme._DEFAULTS["gamma"] == SHIPPED
    assert theme.gamma_colors(theme._DEFAULTS) == SHIPPED


def test_the_keys_run_the_way_the_scale_reads():
    """Largest put on the left to largest call on the right, like the legend."""
    assert list(theme.GAMMA_KEYS) == list(SHIPPED)


def test_a_chosen_colour_is_handed_back_as_six_lower_case_digits():
    got = theme.gamma_colors(_theme(call="#00FF88", put="#f40"))
    assert (got["call"], got["put"]) == ("#00ff88", "#ff4400")
    assert got["call_mid"] == SHIPPED["call_mid"]          # the rest untouched


@pytest.mark.parametrize("bad", ["green", "rgba(1,2,3,.5)", "#12", "#12345", "#gggggg",
                                 "", "  ", None, 7, "#1234567"])
def test_a_colour_that_is_not_a_hex_falls_back_to_the_shipped_one(bad):
    """A hand-edited file must never reach a chart as a colour it cannot parse."""
    assert theme.gamma_colors(_theme(call=bad))["call"] == SHIPPED["call"]


@pytest.mark.parametrize("junk", [None, {}, {"gamma": "x"}, {"gamma": None}, "theme"])
def test_no_section_at_all_is_the_shipped_colours(junk):
    assert theme.gamma_colors(junk) == SHIPPED


def test_the_theme_file_can_set_them(tmp_path):
    p = tmp_path / "theme.toml"
    p.write_text('[gamma]\ncall = "#00ff88"\nput_low = "#402010"\nnope = "#111111"\n',
                 encoding="utf-8")
    loaded = theme.load_theme(p)
    assert loaded["gamma"]["call"] == "#00ff88"
    assert loaded["gamma"]["put_low"] == "#402010"
    assert "nope" not in loaded["gamma"]
    assert loaded["gamma"]["put"] == SHIPPED["put"]


def test_the_shipped_theme_file_holds_the_same_eight():
    """``config/theme.toml`` documents them; a value there that drifted from the
    built-in default would change the page on a box that lost the file."""
    import tomllib

    from repo_paths import THEME_TOML
    with open(THEME_TOML, "rb") as f:
        shipped = tomllib.load(f)
    assert shipped["gamma"] == SHIPPED


def test_saving_one_writes_only_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    p = tmp_path / "theme.toml"
    p.write_text('[gamma]\ncall = "#35c8ff"\n', encoding="utf-8")
    saved = theme.save_theme_values({"gamma": {"call": "#00ff88"}}, path=p)
    assert saved["gamma"]["call"] == "#00ff88"
    assert 'call = "#35c8ff"' in p.read_text(encoding="utf-8")     # tracked file untouched
    assert "#00ff88" in (tmp_path / "local" / "theme.toml").read_text(encoding="utf-8")
