"""Tests for the options engine import shim (scoring name collision guard)."""
import sys

from pages.options import engines


def test_options_scoring_binds_options_module():
    with engines.options_scoring():
        import scoring
        assert hasattr(scoring, "score_all_signals")


def test_options_scoring_restores_previous():
    sentinel = object()
    sys.modules["scoring"] = sentinel
    try:
        with engines.options_scoring():
            import scoring  # noqa: F401
        assert sys.modules.get("scoring") is sentinel  # restored
    finally:
        sys.modules.pop("scoring", None)
