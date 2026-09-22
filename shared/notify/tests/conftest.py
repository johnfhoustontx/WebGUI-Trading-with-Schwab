# shared/notify/tests/conftest.py
import pytest


@pytest.fixture(autouse=True)
def _x_posts_log_in_tmp(tmp_path, monkeypatch):
    """Every X attempt appends to ``x_posts.jsonl``; a test that forgot its own
    patch would write into the checkout's real log. Per-test patches still win."""
    from shared.notify import x_post
    monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x_posts.jsonl")
