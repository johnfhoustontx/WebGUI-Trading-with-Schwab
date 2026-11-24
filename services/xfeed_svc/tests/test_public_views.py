"""The public site's pair of views (``handlers.publish_public``).

The public X Feed reads ONLY these two keys, so what they hold is exactly what
the site publishes: only the accounts config/xfeed.toml ``[public]`` names, and
of each account only what the page draws - never the service's error text.
"""
import pytest

from services.xfeed_svc import config, handlers, runtime
from services.xfeed_svc.poller import Poller
from services.xfeed_svc.store import Store
from shared.bus.client import Bus, reset_fake_bus


def post(handle, sid, *, tags=(), sort_at=1):
    return {"status_id": sid, "account_handle": handle, "type": "post", "text": sid, "author_name": handle,
            "author_handle": handle, "url": "u", "created_at": 1, "cashtags": list(tags), "media": [],
            "quote": None, "counts": {}, "raw": None, "first_seen_at": 1, "sort_at": sort_at, "origin": "live"}


class NoFx:
    def fetch_statuses(self, handle, *, cursor=None, with_replies=True):
        return {"status": "not_found"}


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture
def rt():
    store = Store(":memory:")
    fx = NoFx()
    r = runtime.Runtime(store, fx, Poller(store, fx, sleep=lambda s: None))
    runtime.set_for_tests(r)
    # macro (Macro), news (News), loose (no group); one post each
    for handle, group in (("macro", "Macro"), ("news", "News"), ("loose", None)):
        store.upsert_account(handle, display_name=handle.title(), avatar_url=None, now=1, group_name=group)
    store.insert_posts([post("macro", "m1", tags=["SPY"], sort_at=3), post("news", "n1", sort_at=2),
                        post("loose", "l1", sort_at=1)])
    yield r
    runtime.set_for_tests(None)


def _public(monkeypatch, **public):
    real = config.load()
    patched = {**real, "public": {**real["public"], **public}}
    monkeypatch.setattr(config, "load", lambda: patched)


def payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env is not None else None


def _handles(bus):
    latest = payload(bus, handlers.CACHE_LATEST_PUBLIC)
    accounts = payload(bus, handlers.CACHE_ACCOUNTS_PUBLIC)
    return ([r["account_handle"] for r in latest["rows"]],
            sorted(a["handle"] for a in accounts["accounts"]))


def test_the_keys_are_the_public_pair():
    assert handlers.CACHE_LATEST_PUBLIC == "cache:xfeed:latest_public"
    assert handlers.CACHE_ACCOUNTS_PUBLIC == "cache:xfeed:accounts_public"


def test_shipped_rule_publishes_every_account(bus, rt):
    handlers.publish_all(bus, rt)
    assert _handles(bus) == (["macro", "news", "loose"], ["loose", "macro", "news"])


def test_named_groups_publish_only_those_accounts(bus, rt, monkeypatch):
    _public(monkeypatch, groups=["macro"], ungrouped=False)      # any case
    handlers.publish_all(bus, rt)
    assert _handles(bus) == (["macro"], ["macro"])
    latest = payload(bus, handlers.CACHE_LATEST_PUBLIC)
    assert latest["tickers"] == [{"tag": "SPY", "n": 1}] and latest["total"] == 1
    assert payload(bus, handlers.CACHE_ACCOUNTS_PUBLIC)["counts"] == {"macro": 1}
    # the private views still hold everything
    assert len(payload(bus, handlers.CACHE_LATEST)["rows"]) == 3


def test_ungrouped_adds_the_accounts_in_no_group(bus, rt, monkeypatch):
    _public(monkeypatch, groups=["News"], ungrouped=True)
    handlers.publish_all(bus, rt)
    assert _handles(bus) == (["news", "loose"], ["loose", "news"])


def test_disabled_or_no_groups_publishes_nothing(bus, rt, monkeypatch):
    """An empty account set is NO rows - never the unfiltered feed."""
    for public in ({"enabled": False}, {"groups": [], "ungrouped": False}):
        _public(monkeypatch, **public)
        handlers.publish_all(bus, rt)
        assert _handles(bus) == ([], [])
        assert payload(bus, handlers.CACHE_ACCOUNTS_PUBLIC)["counts"] == {}


def test_gaps_follow_the_account_filter(bus, rt, monkeypatch):
    rt.store.insert_gap(handle="news", from_at=1, to_at=2, now=5)
    _public(monkeypatch, groups=["Macro"], ungrouped=False)
    handlers.publish_all(bus, rt)
    assert all(r["account_handle"] == "macro" for r in payload(bus, handlers.CACHE_LATEST_PUBLIC)["rows"])
    _public(monkeypatch, groups=["News"], ungrouped=False)
    handlers.publish_all(bus, rt)
    kinds = [r["kind"] for r in payload(bus, handlers.CACHE_LATEST_PUBLIC)["rows"]]
    assert "gap" in kinds


def test_public_accounts_carry_no_error_text_or_schedule(bus, rt):
    rt.store.record_check("news", now=9, ok=False, error="HTTP 503 from upstream", next_check_at=99)
    handlers.publish_all(bus, rt)
    accounts = payload(bus, handlers.CACHE_ACCOUNTS_PUBLIC)
    for a in accounts["accounts"]:
        assert set(a) == {"handle", "display_name", "avatar_url", "group_name", "last_checked_at"}
    assert "503" not in repr(accounts)
    assert accounts["page"] == {"new_highlight_s": config.load()["page"]["new_highlight_s"]}
    assert accounts["interval_s"] == rt.poller.interval_s()


@pytest.mark.parametrize("raw, want", [
    (None, {"enabled": True, "groups": ["*"], "ungrouped": True}),
    ({"enabled": "yes", "groups": "Macro", "ungrouped": 1}, {"enabled": True, "groups": ["*"], "ungrouped": True}),
    ({"enabled": False, "groups": [" Macro ", ""], "ungrouped": False},
     {"enabled": False, "groups": ["Macro"], "ungrouped": False}),
])
def test_public_rule_reads_a_bad_value_as_the_shipped_one(raw, want):
    assert handlers.public_rule(raw) == want


def test_shipped_file_matches_the_defaults():
    config.reset()
    assert config.load()["public"] == config.DEFAULTS["public"]


def test_publish_public_is_skip_unchanged(bus, rt):
    """Published on every poll change, like the private pair: an unchanged
    payload must not bump the version every public page watches."""
    handlers.publish_public(bus, rt)
    versions = (bus.cache_version(handlers.CACHE_LATEST_PUBLIC),
                bus.cache_version(handlers.CACHE_ACCOUNTS_PUBLIC))
    handlers.publish_public(bus, rt)
    assert (bus.cache_version(handlers.CACHE_LATEST_PUBLIC),
            bus.cache_version(handlers.CACHE_ACCOUNTS_PUBLIC)) == versions
