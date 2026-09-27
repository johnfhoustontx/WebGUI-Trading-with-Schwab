"""config/momentum.toml — shipped values, and every bad value degrades."""
import pytest

from services.sentiment_svc import momentum_config as mc


def test_shipped_file_values():
    mc.reset_cache()
    assert mc.min_basket_members() == 2
    assert mc.quote_batch() == 375


@pytest.mark.parametrize("bad", [0, -3, 2.5, True, "4", None])
def test_an_unusable_value_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setattr(mc, "load", lambda: {
        "subindustry": {"min_members": bad}, "bullbear": {"quote_batch": bad}})

    assert mc.min_basket_members() == mc.DEFAULTS["subindustry"]["min_members"]
    assert mc.quote_batch() == mc.DEFAULTS["bullbear"]["quote_batch"]


def test_a_valid_override_is_honoured(monkeypatch):
    monkeypatch.setattr(mc, "load", lambda: {
        "subindustry": {"min_members": 3}, "bullbear": {"quote_batch": 200}})

    assert mc.min_basket_members() == 3
    assert mc.quote_batch() == 200


def test_a_missing_section_falls_back(monkeypatch):
    monkeypatch.setattr(mc, "load", lambda: {})

    assert mc.min_basket_members() == 2
    assert mc.quote_batch() == 375
