# shared/notify/tests/conftest.py
import pytest


@pytest.fixture(autouse=True)
def _x_posts_log_in_tmp(tmp_path, monkeypatch):
    """Every X attempt appends to ``x_posts.jsonl``; a test that forgot its own
    patch would write into the checkout's real log. Per-test patches still win."""
    from shared.notify import x_post
    monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x_posts.jsonl")


@pytest.fixture(autouse=True)
def _meta_posts_log_in_tmp(tmp_path, monkeypatch):
    """The same for ``meta_posts.jsonl`` (Facebook and Instagram)."""
    from shared.notify import meta_post
    monkeypatch.setattr(meta_post, "META_POSTS_LOG", tmp_path / "meta_posts.jsonl")
