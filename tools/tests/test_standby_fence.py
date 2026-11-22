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


# --- the second refusal: the peer's proxy answers (Ruling 17) -------------------
def test_fence_refuses_when_the_peer_proxy_answers():
    ok, reason, clear = fence.decide(_ident(), "prod", ME, PEER, [ME], peer_answers=True)
    assert not ok and not clear and "peer's proxy answers" in reason


def test_fence_peer_answer_is_skipped_while_taking_over():
    ok, _, _ = fence.decide(_ident(taking_over=True), "prod", ME, PEER, [PEER],
                            peer_answers=True)
    assert ok


@pytest.mark.parametrize("answers", [None, False])
def test_fence_peer_probe_error_is_no_evidence(answers):
    ok, reason, _ = fence.decide(_ident(), "prod", ME, PEER, [ME], peer_answers=answers)
    assert ok and reason == "public name points at this box"


def test_fence_no_identity_allows_before_the_peer_rule():
    ok, reason, _ = fence.decide(None, "prod", ME, PEER, [PEER], peer_answers=True)
    assert ok and "no box identity" in reason


def _wired_peer(monkeypatch, health="https://peer.ts.net:8100/health"):
    def boxes():
        b = _boxes()
        b["vps3"]["proxy_health"] = health
        return b
    monkeypatch.setattr(sc, "boxes", boxes)
    seen = []

    def probe(url, timeout_s):
        seen.append((url, timeout_s))
        return probe.answer
    probe.answer = None
    monkeypatch.setattr(fence, "probe_proxy", probe)
    return probe, seen


def test_main_refuses_when_the_peer_proxy_answers(wired, monkeypatch, capsys):
    sc.write_identity(_ident(), wired)
    probe, seen = _wired_peer(monkeypatch)
    probe.answer = True
    looked = []
    monkeypatch.setattr(fence, "resolve", lambda host, t: looked.append(host) or [ME])
    assert fence.main() == 1
    assert "peer's proxy answers" in capsys.readouterr().out
    assert seen and seen[0][0] == "https://peer.ts.net:8100/health"


def test_main_does_not_probe_the_peer_while_taking_over(wired, monkeypatch):
    sc.write_identity(_ident(taking_over=True), wired)
    probe, seen = _wired_peer(monkeypatch)
    probe.answer = True
    monkeypatch.setattr(fence, "resolve", lambda host, t: [PEER])
    assert fence.main() == 0 and seen == []


def test_main_allows_when_the_peer_probe_finds_nothing(wired, monkeypatch, capsys):
    sc.write_identity(_ident(), wired)
    probe, seen = _wired_peer(monkeypatch)
    probe.answer = None
    assert fence.main() == 0 and seen
    assert "public name points at this box" in capsys.readouterr().out


def test_main_never_probes_without_an_identity(wired, monkeypatch):
    probe, seen = _wired_peer(monkeypatch)
    looked = []
    monkeypatch.setattr(fence, "resolve", lambda host, t: looked.append(host) or [PEER])
    assert fence.main() == 0 and seen == [] and looked == []


def test_probe_proxy_any_http_status_is_an_answer(monkeypatch):
    import urllib.error

    def http_500(url, timeout=None, context=None):
        raise urllib.error.HTTPError(url, 500, "boom", {}, None)
    monkeypatch.setattr(fence.urllib.request, "urlopen", http_500)
    assert fence.probe_proxy("https://peer/health", 1) is True

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(fence.urllib.request, "urlopen",
                        lambda url, timeout=None, context=None: Resp())
    assert fence.probe_proxy("https://peer/health", 1) is True


@pytest.mark.parametrize("exc", [OSError("refused"), TimeoutError("slow")])
def test_probe_proxy_no_connection_is_no_evidence(monkeypatch, exc):
    import urllib.error

    def fail(url, timeout=None, context=None):
        raise urllib.error.URLError(exc)
    monkeypatch.setattr(fence.urllib.request, "urlopen", fail)
    assert fence.probe_proxy("https://peer/health", 1) is None
    assert fence.probe_proxy("", 1) is None


def test_probe_proxy_that_hangs_is_no_evidence(monkeypatch):
    monkeypatch.setattr(fence.urllib.request, "urlopen",
                        lambda url, timeout=None, context=None: time.sleep(2))
    t0 = time.monotonic()
    assert fence.probe_proxy("https://peer/health", 0.2) is None
    assert time.monotonic() - t0 < 1.5


# --- the lookup is retried within the unit's budget -----------------------------
SET = {"lookup_timeout_s": 4, "lookup_retry_s": 12, "lookup_pause_s": 2,
       "peer_probe_timeout_s": 5}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_lookup_retries_a_failure_then_succeeds():
    clock, answers = Clock(), [None, None, [ME]]

    def resolve(host, timeout_s):
        clock.t += 0.5
        return answers.pop(0)
    assert fence.lookup("h", SET, resolve_fn=resolve, sleep=clock.sleep, clock=clock) == [ME]


def test_lookup_gives_up_inside_the_window():
    clock, calls = Clock(), []

    def resolve(host, timeout_s):
        calls.append(clock.t)
        clock.t += timeout_s                      # every attempt times out
        return None
    assert fence.lookup("h", SET, resolve_fn=resolve, sleep=clock.sleep, clock=clock) is None
    assert len(calls) >= 2 and clock.t <= SET["lookup_retry_s"]


def test_lookup_with_no_retry_window_tries_once():
    calls = []
    got = fence.lookup("h", dict(SET, lookup_retry_s=0),
                       resolve_fn=lambda h, t: calls.append(h), sleep=lambda s: None)
    assert got is None and calls == ["h"]


def test_fence_budget_fits_the_unit_timeout(monkeypatch):
    """Retried lookup + peer probe stay well under TimeoutStartSec, at the defaults
    and at the largest values the loader accepts."""
    import re
    from deploy.systemd import generate_units as gu
    unit = gu._fence_unit()[gu.fence_name()]
    limit = int(re.search(r"^TimeoutStartSec=(\d+)$", unit, re.M).group(1))
    startup = 5                                   # python start and imports, generously
    d = sc.DEFAULTS["fence"]
    assert max(d["lookup_retry_s"], d["lookup_timeout_s"]) + d["peer_probe_timeout_s"] \
        + startup < limit
    hi = {k: v[1] for k, v in sc.FENCE_BOUNDS.items()}
    assert max(hi["lookup_retry_s"], hi["lookup_timeout_s"]) + hi["peer_probe_timeout_s"] \
        + startup < limit


# --- a failed import still allows (I2) -------------------------------------------
def test_main_allows_when_the_config_module_cannot_import(wired, monkeypatch, capsys):
    import sys
    monkeypatch.setitem(sys.modules, "shared.standby_config", None)
    assert fence.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("fence: allow - could not decide") and "standby_config" in out


def test_fence_imports_nothing_but_stdlib_and_repo_paths_at_module_level():
    import ast
    import sys
    tree = ast.parse(open(fence.__file__, encoding="utf-8").read())
    mods = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mods.add(node.module)
    extra = {m for m in mods if m.split(".")[0] not in sys.stdlib_module_names}
    assert extra == {"repo_paths"}, extra
