"""Tests for Settings -> Appearance (pages/appearance.py)."""
from pages import appearance
from pages.options import theme


def _keys():
    return [(s, k) for _label, _kind, keys in appearance.GROUPS for s, k in keys]


def test_every_editable_key_is_on_the_screen_exactly_once():
    seen = _keys()
    assert len(seen) == len(set(seen)), "a key is on the screen twice"
    want = {(s, k) for s in appearance.EDITABLE_SECTIONS for k in theme._DEFAULTS[s]}
    assert set(seen) == want, (f"missing {sorted(want - set(seen))}, "
                               f"extra {sorted(set(seen) - want)}")


def test_no_retired_or_page_scoped_section_is_editable():
    sections = {s for s, _k in _keys()}
    assert sections.isdisjoint({"buttons_3d", "brand", "console", "macro",
                                "sectors", "rotation", "calc", "flow"})


def test_group_kinds_are_known():
    assert {kind for _l, kind, _k in appearance.GROUPS} <= {"color", "text", "menu"}


def test_size_error():
    for ok in ("14", "14px", "1.1rem", " 16 "):
        assert appearance.size_error(ok) is None, ok
    assert appearance.size_error("") == "Enter a size, like 14"
    assert appearance.size_error("big") == "A number of pixels, like 14 or 14px"


def test_edited_theme_overlays_without_mutating_the_base():
    base = theme.load_theme("Z:/nope.toml")
    out = appearance.edited_theme(base, {("palette", "card_bg"): "#123456"})
    assert out["palette"]["card_bg"] == "#123456"
    assert base["palette"]["card_bg"] == "#101a30"


def test_updates_from_groups_by_section():
    assert appearance.updates_from({("palette", "card_bg"): "#111111",
                                    ("semantic", "positive"): "#222222"}) == {
        "palette": {"card_bg": "#111111"}, "semantic": {"positive": "#222222"}}
