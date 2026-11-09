"""shared.social_cards: the contract between the card renderers and the pages."""
import pathlib
import subprocess
import sys

import pytest

from shared import social_cards as S

REPO = str(pathlib.Path(__file__).resolve().parents[2])


def test_defaults_without_file(monkeypatch, tmp_path):
    import importlib
    from shared import config_toml
    real = config_toml.toml_loader
    monkeypatch.setattr(config_toml, "toml_loader",
                        lambda path, defaults, **kw: real(tmp_path / "none.toml", defaults, **kw))
    mod = importlib.reload(S)
    try:
        assert mod.config() == {
            "cards": {"jpeg_quality": 90, "listed_from": "2026-11-09"},
            "blog": {"title_max_px": 50, "title_min_px": 34, "title_max_lines": 4,
                     "summary_px": 20, "summary_max_lines": 3},
            "caption": {"max_hashtags": 4}}
    finally:
        monkeypatch.undo()
        importlib.reload(S)


def test_shipped_file_matches_the_defaults():
    import tomllib
    with open(pathlib.Path(REPO) / "config" / "social_cards.toml", "rb") as f:
        assert tomllib.load(f) == S.DEFAULTS


def test_listed_on_and_after_cutoff():
    assert S.listed("2026-11-09T08:40:00-06:00") is True
    assert S.listed("2026-11-08T15:00:00-06:00") is False
    assert S.listed("garbage") is False


def test_listed_uses_the_central_date():
    # 23:30 on the 8th in Central is the 9th in UTC: still the 8th.
    assert S.listed("2026-11-09T05:30:00+00:00") is False
    assert S.listed("2026-11-09T06:30:00+00:00") is True


def test_listed_takes_a_config_override():
    cfg = {"cards": {"listed_from": "2026-12-01"}}
    assert S.listed("2026-11-20T10:00:00-06:00", cfg) is False
    assert S.listed("2026-12-01T10:00:00-06:00", cfg) is True


def test_hashtag_strips_punctuation():
    assert S.hashtag("0-DTE") == "#0DTE"
    assert S.hashtag("Gamma") == "#Gamma"
    assert S.hashtag("--") is None and S.hashtag("") is None


def test_blog_caption_lines():
    out = S.blog_caption("T", "S", "https://neuralstrike.co/blog/t/", ["Gamma", "0-DTE"], 4)
    assert out == "T\n\nS\n\nRead it at https://neuralstrike.co/blog/t/\n\n#Gamma #0DTE"


def test_blog_caption_no_summary_no_tags():
    out = S.blog_caption("T", "  ", "https://x/t/", ["!!"], 4)
    assert out == "T\n\nRead it at https://x/t/"


def test_blog_caption_caps_the_hashtags():
    out = S.blog_caption("T", "", "a", ["A", "B", "C"], 2)
    assert out.endswith("#A #B")


def _row(key, at, **kw):
    return S.row(key, "blog", at, "title", "cap", "https://x/", f"cards/{key[-1]}.png",
                 f"cards/{key[-1]}.jpg") | kw


def test_row_has_exactly_its_keys_and_a_listed_flag():
    r = S.row("blog:a", "blog", "2026-11-09T09:00:00-06:00", "t", "c", "l", "p.png", "p.jpg")
    assert set(r) == {"key", "kind", "at", "title", "caption", "link", "png", "jpg", "listed"}
    assert r["listed"] is True


def test_merged_newest_first_listed_only():
    blog = {"cards": [_row("blog:a", "2026-11-09T09:00:00-06:00"),
                      _row("blog:b", "2026-11-08T09:00:00-06:00")]}
    ideas = {"cards": [_row("idea:c", "2026-11-10T09:00:00-06:00"), "junk", {"key": "x"},
                       {"key": "idea:d", "at": "garbage", "listed": True}]}
    out = S.merged(blog, ideas)
    assert [r["key"] for r in out] == ["idea:c", "blog:a"]


def test_merged_tolerates_missing_payloads():
    assert S.merged(None, {"cards": "nope"}) == []


def test_read_card_refuses_unlisted_paths(tmp_path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards" / "a.png").write_bytes(b"PNGDATA")
    (tmp_path / "index.html").write_text("x")
    (tmp_path / "x.png").write_bytes(b"secret")
    rows = [S.row("blog:a", "blog", "2026-11-09T09:00:00-06:00", "t", "c", "l",
                  "cards/a.png", "cards/a.jpg")]
    assert S.read_card(tmp_path, "cards/a.png", rows) == b"PNGDATA"
    assert S.read_card(tmp_path, "cards/a.jpg", rows) is None           # row lists it, no file
    for bad in ("../x.png", "index.html", str(tmp_path / "x.png"), "cards/../x.png",
                "x.png", "", None):
        assert S.read_card(tmp_path, bad, rows) is None


def test_read_card_refuses_a_wrong_extension_and_a_symlink(tmp_path):
    (tmp_path / "a.html").write_text("x")
    rows = [S.row("blog:a", "blog", "2026-11-09T09:00:00-06:00", "t", "c", "l",
                  "a.html", "a.jpg")]
    assert S.read_card(tmp_path, "a.html", rows) is None
    (tmp_path / "real.jpg").write_bytes(b"J")
    try:
        (tmp_path / "a.jpg").symlink_to(tmp_path / "real.jpg")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert S.read_card(tmp_path, "a.jpg", rows) is None


# ── Tier 1 may import it ────────────────────────────────────────────────────

EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.social_cards"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.social_cards
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names
                              and not m.startswith("_sysconfigdata"))))
""" % REPO


def test_import_set():
    """Tier 1 imports this module, so its import set is pinned, in a FRESH interpreter."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert "shared.social_cards" in new
