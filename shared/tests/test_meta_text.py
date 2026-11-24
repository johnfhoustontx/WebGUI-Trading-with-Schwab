"""Captions and hashtags for Facebook and Instagram (``shared/meta_text.py``).

Instagram allows five hashtags and 2,200 characters and does not make a link in a
caption clickable, so its caption writes the address out as words and points at
the profile's link. Facebook's post carries the link itself and two tags.
"""
import pathlib

from shared import meta_text as M

ADDR = "https://neuralstrike.co/t/2026-11-23-0930-SPY/"


def test_hashtag_strips_sigils():
    assert M.hashtag("$SPX") == "#SPX"
    assert M.hashtag("BRK/B") == "#BRKB"
    assert M.hashtag("") is None
    assert M.hashtag(None) is None


def test_instagram_tags_cap_at_five():
    assert M.tags("trade_idea", "instagram", "SPY") == [
        "#SPY", "#optionstrading", "#stockmarket", "#trading", "#options"]


def test_tags_dedupe_case_insensitively():
    out = M.tags("report", "instagram", "spy")
    assert [t.lower() for t in out].count("#spy") == 1
    assert len(out) <= 5


def test_facebook_tags_cap_at_two():
    assert M.tags("trade_idea", "facebook", "QQQ") == ["#QQQ", "#optionstrading"]
    assert len(M.tags("report", "facebook")) == 2


def test_a_bad_cap_falls_back_and_a_bad_list_is_none():
    cfg = {"instagram": {"max_tags": "lots"}, "hashtags": {"instagram": {"report": 5}}}
    assert M.tags("report", "instagram", "SPY", cfg=cfg) == ["#SPY"]


def test_facebook_text_puts_the_tags_after_a_blank_line():
    assert M.facebook_text("Trade idea: SPY", ["#SPY", "#options"]) == \
        "Trade idea: SPY\n\n#SPY #options"
    assert M.facebook_text("Trade idea: SPY", []) == "Trade idea: SPY"


def test_instagram_text_writes_the_address_out():
    out = M.instagram_text("Trade idea: SPY put credit spread", "trade_idea", ADDR,
                           ["#SPY", "#options"])
    assert "Full trade: neuralstrike.co/t/2026-11-23-0930-SPY/" in out
    assert "Link in bio." in out
    assert "https://" not in out
    assert out.endswith("#SPY #options")
    report = M.instagram_text("Midday: buyers hold", "report",
                              "https://neuralstrike.co/r/2026-11-23-4-midday/", [])
    assert "Full report: neuralstrike.co/r/2026-11-23-4-midday/" in report


def test_instagram_text_cuts_the_body_not_the_tags():
    tags = ["#SPY", "#optionstrading", "#stockmarket", "#trading", "#options"]
    out = M.instagram_text("x" * 3000, "trade_idea", ADDR, tags)
    assert len(out) <= 2200
    assert out.endswith(" ".join(tags))
    assert "neuralstrike.co/t/2026-11-23-0930-SPY/" in out
    assert "…" in out


def test_instagram_text_without_an_address_has_no_link_line():
    out = M.instagram_text("Trade idea: SPY", "trade_idea", None, ["#SPY"])
    assert "Full trade" not in out and "Link in bio." not in out
    assert out == "Trade idea: SPY\n\n#SPY"


def test_config_defaults_on_a_missing_file(monkeypatch, tmp_path):
    import importlib
    import repo_paths
    monkeypatch.setattr(repo_paths, "META_POST_TOML", tmp_path / "missing.toml")
    mod = importlib.reload(M)
    try:
        assert mod.config()["instagram"]["max_tags"] == 5
        assert mod.config()["graph"]["version"] == "v26.0"
    finally:
        monkeypatch.undo()
        importlib.reload(M)


def test_the_shipped_file_matches_the_defaults():
    import tomllib
    import repo_paths
    shipped = tomllib.loads(pathlib.Path(repo_paths.META_POST_TOML).read_text("utf-8"))
    assert shipped == M.DEFAULTS


def test_imports_stay_stdlib_and_config():
    import ast
    tree = ast.parse(pathlib.Path(M.__file__).read_text("utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names <= {"re", "repo_paths", "shared.config_toml"}, names
