"""X's text rules, pure. Tier 1 imports this module (the /x page's live count)."""
import pathlib
import subprocess
import sys

import pytest

from shared import x_text as xt

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_a_url_counts_23_whatever_its_length():
    assert xt.weighted_len("https://neuralstrike.co/report.html") == 23
    assert xt.weighted_len("see https://a.co") == 4 + 23


def test_emoji_and_cjk_count_two():
    assert xt.weighted_len("a") == 1
    assert xt.weighted_len("\U0001F7E2") == 2
    assert xt.weighted_len("日") == 2


def test_tags_are_normalised_deduped_and_capped_derived_first():
    tags = xt.hashtags(["$nvda", "#0DTE"], ["options", "#Options", "#trading"], max_tags=3)
    assert tags == ["$NVDA", "#0DTE", "#options"]


def test_a_cashtag_keeps_its_dollar_and_a_bare_word_gets_a_hash():
    assert xt.hashtags([], ["stocks", "$spy"], max_tags=5) == ["#stocks", "$SPY"]


def test_junk_tags_are_dropped():
    assert xt.hashtags([], ["", "  ", "#", "two words", None, 5], max_tags=5) == []


def test_fit_keeps_everything_when_it_fits():
    out = xt.fit_text("Body", "https://neuralstrike.co", ["#a", "#b"])
    assert out == "Body\n\nhttps://neuralstrike.co\n#a #b"
    assert xt.weighted_len(out) <= 280


def test_fit_drops_tags_from_the_end_before_touching_the_body():
    body = "x" * 240
    out = xt.fit_text(body, "https://neuralstrike.co", ["#one", "#two", "#three"])
    assert out.startswith(body)
    assert "#three" not in out
    assert xt.weighted_len(out) <= 280


def test_fit_truncates_the_body_with_an_ellipsis_only_when_no_tag_is_left():
    out = xt.fit_text("y" * 400, "https://neuralstrike.co", ["#a"])
    assert "…" in out and "#a" not in out
    assert out.endswith("https://neuralstrike.co")
    assert xt.weighted_len(out) <= 280


def test_fit_without_a_link():
    assert xt.fit_text("Hi", "", ["#a"]) == "Hi\n\n#a"


PROBE = """
import sys
before = set(sys.modules)
import shared.x_text
print("NEW:" + ",".join(sorted(set(sys.modules) - before)))
"""


def test_x_text_imports_nothing_but_the_stdlib():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    ours = {m for m in new if m.split(".")[0] in ("shared", "services", "webgui",
                                                  "repo_paths", "requests", "redis")}
    # `shared` is a namespace package, so importing shared.x_text also imports
    # `shared` itself the first time - that is the package, not a dependency.
    assert ours == {"shared", "shared.x_text"}, sorted(ours)
    third_party = {m for m in new - ours
                   if m.split(".")[0] not in sys.stdlib_module_names}
    assert not third_party, sorted(third_party)


# ── Review fixes: count links X's way, never over-promise the limit ─────────

def test_a_bare_domain_counts_as_a_link():
    assert xt.weighted_len("Visit neuralstrike.co") == 6 + 23


def test_trailing_punctuation_is_not_part_of_the_link():
    assert xt.weighted_len("go to https://x.co.") == 6 + 23 + 1
    assert xt.weighted_len("(https://x.co)") == 1 + 23 + 1


def test_the_scheme_is_matched_case_insensitively():
    assert xt.weighted_len("HTTPS://A.CO") == 23


def test_a_www_link_counts_as_a_link():
    assert xt.weighted_len("www.a.co") == 23


def test_a_word_ending_a_sentence_is_not_a_link():
    assert xt.weighted_len("end. Next") == 9


def test_a_long_body_with_a_short_link_and_no_tags_fits():
    assert xt.weighted_len(xt.fit_text("y" * 400, "https://a.co", [])) <= 280


def test_an_empty_body_is_omitted_like_an_empty_link():
    assert xt.fit_text("   ", "https://a.co", ["#a"]) == "https://a.co\n#a"


def test_max_tags_none_means_the_default_four():
    tags = xt.hashtags([], ["a", "b", "c", "d", "e"], max_tags=None)
    assert tags == ["#a", "#b", "#c", "#d"]


# --- max_tags_from: the one reader of x.max_tags ------------------------------

@pytest.mark.parametrize("raw,want", [
    (None, xt.DEFAULT_MAX_TAGS), ("four", xt.DEFAULT_MAX_TAGS),
    (True, xt.DEFAULT_MAX_TAGS), (False, xt.DEFAULT_MAX_TAGS),
    ([3], xt.DEFAULT_MAX_TAGS), (float("nan"), xt.DEFAULT_MAX_TAGS),
    (2, 2), ("3", 3), (0, 0), (-1, 0), (2.9, 2),
])
def test_max_tags_from(raw, want):
    assert xt.max_tags_from(raw) == want
