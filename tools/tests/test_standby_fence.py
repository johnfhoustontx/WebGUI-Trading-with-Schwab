"""tools/standby_fence.py: the stack starts only on the box that is prod.

The property that matters most: with no identity file, or any doubt, the fence
ALLOWS the start. It refuses in exactly two cases (role is standby; the public
name provably points at the other box)."""
import time

import pytest

import repo_paths
from shared import standby_config as sc
from tools import standby_fence as fence

ME, PEER = "10.0.0.1", "10.0.0.2"


def _ident(role="prod", taking_over=False):
    return sc.Identity("vps2", role, taking_over)


def _decide(identity, addresses, env="prod", self_ip=ME, peer_ip=PEER):
    return fence.decide(identity, env, self_ip, peer_ip, addresses)


def test_fence_passes_for_dev():
    ok, reason, clear = _decide(_ident("standby"), [PEER], env="dev")
    assert ok and not clear and "dev" in reason


def test_fence_passes_without_identity():
    ok, reason, clear = _decide(None, [PEER])
    assert ok and not clear and "no box identity" in reason


def test_fence_refuses_standby_role():
    ok, reason, clear = _decide(_ident("standby"), [ME])
    assert not ok and not clear and "standby" in reason


def test_fence_passes_when_lookup_fails():
    ok, reason, clear = _decide(_ident(), None)
    assert ok and not clear and "not evidence" in reason


def test_fence_passes_and_clears_takeover_when_dns_is_self():
    ok, _, clear = _decide(_ident(taking_over=True), [ME])
    assert ok and clear
    ok, _, clear = _decide(_ident(taking_over=False), [ME])
    assert ok and not clear


def test_fence_passes_while_taking_over():
    ok, reason, clear = _decide(_ident(taking_over=True), [PEER])
    assert ok and not clear and "taking over" in reason


def test_fence_refuses_when_dns_points_at_peer():
    ok, reason, clear = _decide(_ident(), [PEER, PEER])
    assert not ok and not clear and PEER in reason


def test_fence_passes_unknown_address():
    ok, reason, clear = _decide(_ident(), ["192.0.2.9"])
    assert ok and not clear and "unknown address" in reason


def test_fence_passes_with_several_addresses_including_self():
    ok, _, _ = _decide(_ident(), [PEER, ME])
    assert ok


def test_fence_passes_with_a_mix_of_peer_and_unknown():
    ok, _, _ = _decide(_ident(), [PEER, "192.0.2.9"])
    assert ok


@pytest.mark.parametrize("self_ip,peer_ip", [(None, PEER), (ME, None), ("", PEER), (ME, "")])
def test_fence_passes_when_an_ip_is_unknown(self_ip, peer_ip):
    """An empty or missing address is unknown: never a reason to refuse."""
    ok, _, _ = fence.decide(_ident(), "prod", self_ip, peer_ip, [PEER])
    assert ok


def test_fence_passes_with_an_empty_address_list():
    ok, _, _ = _decide(_ident(), [])
    assert ok


def test_resolve_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(fence.socket, "getaddrinfo", lambda *a, **k: time.sleep(2))
    t0 = time.monotonic()
    assert fence.resolve("example.test", 0.2) is None
    assert time.monotonic() - t0 < 1.5


def test_resolve_returns_ipv4_addresses(monkeypatch):
    monkeypatch.setattr(
        fence.socket, "getaddrinfo",
        lambda host, port, family=0, type=0, **k: [(2, 1, 6, "", (ME, 0)), (2, 1, 6, "", (PEER, 0))])
    assert sorted(fence.resolve("example.test", 1)) == [ME, PEER]


def test_resolve_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such host")
    monkeypatch.setattr(fence.socket, "getaddrinfo", boom)
    assert fence.resolve("example.test", 1) is None


# --- main(): every unexpected condition allows ------------------------------
def _boxes(self_ip=ME, peer_ip=PEER):
    return {"vps2": {"ssh": [], "public_ip": self_ip, "proxy_health": ""},
            "vps3": {"ssh": [], "public_ip": peer_ip, "proxy_health": ""}}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    ident = tmp_path / "box.toml"
    monkeypatch.setattr(repo_paths, "BOX_IDENTITY", ident)
    monkeypatch.setattr(repo_paths, "ENV_NAME", "prod")
    monkeypatch.setattr(fence, "ENV_NAME", "prod")
    monkeypatch.setattr(sc, "boxes", _boxes)
    monkeypatch.setattr(fence, "resolve", lambda host, timeout_s: [ME])
    return ident


def test_main_allows_with_no_identity_file(wired, capsys):
    assert fence.main() == 0
    assert "no box identity" in capsys.readouterr().out


def test_main_refuses_a_standby(wired, capsys):
    sc.write_identity(_ident("standby"), wired)
    assert fence.main() == 1
    assert "standby" in capsys.readouterr().out


def test_main_refuses_when_dns_points_at_peer(wired, monkeypatch, capsys):
    sc.write_identity(_ident(), wired)
    monkeypatch.setattr(fence, "resolve", lambda host, timeout_s: [PEER])
    assert fence.main() == 1


def test_main_clears_takeover_when_dns_is_self(wired, capsys):
    sc.write_identity(_ident(taking_over=True), wired)
    assert fence.main() == 0
    assert sc.read_identity(wired) == sc.Identity("vps2", "prod", False)


def test_main_keeps_takeover_while_dns_has_not_moved(wired, monkeypatch):
    sc.write_identity(_ident(taking_over=True), wired)
    monkeypatch.setattr(fence, "resolve", lambda host, timeout_s: [PEER])
    assert fence.main() == 0
    assert sc.read_identity(wired).taking_over is True


def test_main_allows_when_the_identity_write_fails(wired, monkeypatch, capsys):
    sc.write_identity(_ident(taking_over=True), wired)

    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(sc, "write_identity", boom)
    assert fence.main() == 0
    assert "read-only" in capsys.readouterr().out


def test_main_allows_when_peer_raises(wired, monkeypatch, capsys):
    sc.write_identity(_ident(), wired)
    monkeypatch.setattr(sc, "boxes", lambda: {"vps2": _boxes()["vps2"]})
    assert fence.main() == 0
    assert capsys.readouterr().out.strip()


def test_main_allows_when_resolve_raises(wired, monkeypatch):
    sc.write_identity(_ident(), wired)

    def boom(host, timeout_s):
        raise RuntimeError("boom")
    monkeypatch.setattr(fence, "resolve", boom)
    assert fence.main() == 0


def test_main_allows_when_reading_the_identity_raises(wired, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(sc, "read_identity", boom)
    assert fence.main() == 0


def test_main_allows_with_an_empty_public_ip(wired, monkeypatch):
    """An empty address in the config is unknown, never a reason to refuse."""
    sc.write_identity(_ident(), wired)
    monkeypatch.setattr(fence, "resolve", lambda host, timeout_s: [PEER])
    monkeypatch.setattr(sc, "boxes", lambda: _boxes(self_ip="", peer_ip=PEER))
    assert fence.main() == 0
    monkeypatch.setattr(sc, "boxes", lambda: _boxes(self_ip=ME, peer_ip=""))
    assert fence.main() == 0
