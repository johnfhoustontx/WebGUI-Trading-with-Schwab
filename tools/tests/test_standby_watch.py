"""Prod's morning check that the standby synced: pure assessment, the ssh fetch,
the silent-on-the-wrong-box rule, and the rendered timer."""
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.standby_config import Identity  # noqa: E402
from tools import standby_watch as sw  # noqa: E402

NOW = dt.datetime(2026, 11, 22, 7, 40)
GEN = "prod_2026-11-20_2000"          # 35.67 h before NOW


def _text(**kw):
    d = {"result": "synced", "generation": GEN, "detail": ""}
    d.update(kw)
    return json.dumps(d)


def test_assess_healthy_is_none():
    assert sw.assess(_text(), NOW, 80) is None


def test_assess_no_answer():
    assert sw.assess(None, NOW, 80) == "standby did not answer"


def test_assess_not_json():
    assert sw.assess("not json", NOW, 80) == "standby status unreadable"


def test_assess_json_that_is_not_an_object():
    assert sw.assess("[1, 2]", NOW, 80) == "standby status unreadable"


def test_assess_missing_result():
    assert sw.assess(json.dumps({"generation": GEN}), NOW, 80) == "standby status unreadable"


def test_assess_failed_run():
    assert sw.assess(_text(result="failed", detail="disk full"), NOW, 80) \
        == "last run failed: disk full"


def test_assess_refused_run():
    assert sw.assess(_text(result="refused", detail="box was promoted"), NOW, 80) \
        == "last run refused: box was promoted"


def test_assess_missing_generation():
    assert sw.assess(_text(generation=None), NOW, 80) == "standby status unreadable"


def test_assess_unparseable_generation():
    assert sw.assess(_text(generation="nonsense"), NOW, 80) == "standby status unreadable"


def test_assess_stale_generation():
    assert sw.assess(_text(), NOW, 30) == f"last good sync 35 h ago ({GEN})"


def test_assess_age_equal_to_the_limit_is_healthy():
    now = dt.datetime(2026, 11, 22, 8, 0)           # exactly 36 h after the generation
    assert sw.assess(_text(), now, 36) is None
    assert sw.assess(_text(), now, 35) is not None


class _Run:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        code, out = self.results.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, "")


def test_fetch_tries_targets_in_order_and_returns_first_output():
    run = _Run([(255, ""), (0, '{"result": "synced"}\n')])
    assert sw.fetch_status(["a", "b"], 15, run=run) == '{"result": "synced"}\n'
    first = run.calls[0][0]
    assert first[0] == "ssh" and first[-1] == "a" and run.calls[1][0][-1] == "b"
    assert "BatchMode=yes" in first and "ConnectTimeout=15" in first
    assert "IdentitiesOnly=yes" in first
    key = first[first.index("-i") + 1]
    assert key.endswith("standby_watch") and "~" not in key


def test_fetch_none_when_every_target_fails_or_is_empty():
    assert sw.fetch_status(["a", "b"], 15, run=_Run([(255, ""), (0, "  \n")])) is None
    assert sw.fetch_status([], 15, run=_Run([])) is None


def test_fetch_survives_a_timeout():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    assert sw.fetch_status(["a"], 15, run=run) is None


PROD = Identity("vps2", "prod", False)
SETTINGS = {"max_age_h": 80, "connect_timeout_sec": 15}


def _wire(monkeypatch, identity, entry=None, fetched=None, sent=None, peer_error=False):
    monkeypatch.setattr(sw.standby_config, "read_identity", lambda *a, **k: identity)
    monkeypatch.setattr(sw.standby_config, "sync_settings", lambda: SETTINGS)

    def peer(_identity):
        if peer_error:
            raise ValueError("bad config")
        return "vps3", entry if entry is not None else {"ssh": ["vps3-ts", "1.2.3.4"]}
    monkeypatch.setattr(sw.standby_config, "peer", peer)
    fetch_calls = []

    def fetch(targets, timeout, **kw):
        fetch_calls.append((targets, timeout))
        return fetched
    monkeypatch.setattr(sw, "fetch_status", fetch)
    sent = sent if sent is not None else []
    monkeypatch.setattr(sw, "_send", lambda title, lines: sent.append((title, lines)) or ["x"])
    monkeypatch.setattr(sw, "_now", lambda: NOW)
    return fetch_calls, sent


def test_watch_silent_on_standby_box(monkeypatch, capsys):
    calls, sent = _wire(monkeypatch, Identity("vps3", "standby", False))
    assert sw.main() == 0
    assert calls == [] and sent == []
    assert "not prod / no identity" in capsys.readouterr().out


def test_watch_silent_without_identity(monkeypatch, capsys):
    calls, sent = _wire(monkeypatch, None)
    assert sw.main() == 0
    assert calls == [] and sent == []
    assert "not prod / no identity" in capsys.readouterr().out


def test_watch_healthy_sends_nothing(monkeypatch):
    calls, sent = _wire(monkeypatch, PROD, fetched=_text())
    assert sw.main() == 0
    assert calls == [(["vps3-ts", "1.2.3.4"], 15)] and sent == []


def test_watch_alerts_with_the_reason(monkeypatch):
    _, sent = _wire(monkeypatch, PROD, fetched=None)
    assert sw.main() == 0
    assert len(sent) == 1 and "standby did not answer" in " ".join(sent[0][1])


def test_watch_alerts_when_the_config_is_unusable(monkeypatch):
    calls, sent = _wire(monkeypatch, PROD, peer_error=True)
    assert sw.main() == 0
    assert calls == [] and len(sent) == 1 and "unusable" in sent[0][0].lower()


def test_watch_alerts_when_the_peer_has_no_ssh_target(monkeypatch):
    calls, sent = _wire(monkeypatch, PROD, entry={"ssh": []})
    assert sw.main() == 0
    assert calls == [] and len(sent) == 1 and "unusable" in sent[0][0].lower()


# --- the status file's own age ----------------------------------------------
def test_assess_status_older_than_its_limit():
    text = _text(checked_at="2026-11-21T05:00:00")             # 26.67 h before NOW
    assert sw.assess(text, NOW, 80, status_max_age_h=26) \
        == "the standby has not run since 2026-11-21T05:00:00"
    assert sw.assess(text, NOW, 80, status_max_age_h=27) is None


def test_assess_status_age_is_checked_before_the_last_result():
    text = _text(result="failed", detail="disk full", checked_at="2026-11-19T22:15:00")
    assert sw.assess(text, NOW, 80, status_max_age_h=26).startswith(
        "the standby has not run since")


def test_assess_unparseable_checked_at_is_unreadable():
    assert sw.assess(_text(checked_at="yesterday"), NOW, 80, status_max_age_h=26) \
        == "standby status unreadable"


def test_watch_alerts_on_a_stale_status_file(monkeypatch):
    _, sent = _wire(monkeypatch, PROD, fetched=_text(checked_at="2026-11-20T22:15:00"))
    monkeypatch.setattr(sw.standby_config, "watch_settings", lambda: {"status_max_age_h": 26})
    assert sw.main() == 0
    assert len(sent) == 1 and "has not run since 2026-11-20T22:15:00" in " ".join(sent[0][1])


# --- the takeover marker (C1) -----------------------------------------------
def _box_file(monkeypatch, tmp_path, identity):
    import repo_paths
    from shared import standby_config as sc
    path = tmp_path / "box.toml"
    monkeypatch.setattr(repo_paths, "BOX_IDENTITY", path)
    sc.write_identity(identity, path)
    monkeypatch.setattr(sc, "boxes", lambda: {
        "vps2": {"ssh": [], "public_ip": "10.0.0.2", "proxy_health": ""},
        "vps3": {"ssh": ["vps3-ts"], "public_ip": "10.0.0.3", "proxy_health": ""}})
    monkeypatch.setattr(sw.standby_config, "sync_settings", lambda: SETTINGS)
    monkeypatch.setattr(sw, "fetch_status", lambda targets, timeout, **kw: None)
    monkeypatch.setattr(sw, "_now", lambda: NOW)
    sent = []
    monkeypatch.setattr(sw, "_send", lambda title, lines: sent.append((title, lines)) or ["x"])
    return path, sent


def test_takeover_marker_is_cleared_once_dns_moves_with_no_reboot(monkeypatch, tmp_path, capsys):
    from shared import standby_config as sc
    path, sent = _box_file(monkeypatch, tmp_path, Identity("vps2", "prod", True))
    # day 1: the public name still points at the old prod
    monkeypatch.setattr(sw, "_lookup", lambda host: ["10.0.0.3"])
    assert sw.main() == 0
    assert sc.read_identity(path) == Identity("vps2", "prod", True)
    marker = [s for s in sent if "taking over" in s[0].lower()]
    assert len(marker) == 1
    assert "does not point at it yet" in " ".join(marker[0][1])
    # day 2: Wix moved; the watch clears the marker, no fence run, no reboot
    sent.clear()
    capsys.readouterr()
    monkeypatch.setattr(sw, "_lookup", lambda host: ["10.0.0.2"])
    assert sw.main() == 0
    assert sc.read_identity(path) == Identity("vps2", "prod", False)
    assert "takeover marker cleared" in capsys.readouterr().out
    assert not [s for s in sent if "taking over" in s[0].lower()]
    # day 3: nothing to clear, no lookup at all
    looked = []
    monkeypatch.setattr(sw, "_lookup", lambda host: looked.append(host) or ["10.0.0.2"])
    assert sw.main() == 0 and looked == []


def test_takeover_marker_alerts_every_morning_while_dns_points_at_the_peer(monkeypatch, tmp_path):
    from shared import standby_config as sc
    path, sent = _box_file(monkeypatch, tmp_path, Identity("vps2", "prod", True))
    monkeypatch.setattr(sw, "_lookup", lambda host: ["10.0.0.3"])
    for _ in range(2):
        assert sw.main() == 0
    assert len([s for s in sent if "taking over" in s[0].lower()]) == 2
    assert sc.read_identity(path).taking_over is True


def test_takeover_marker_failed_lookup_alerts_and_keeps_it(monkeypatch, tmp_path):
    from shared import standby_config as sc
    path, sent = _box_file(monkeypatch, tmp_path, Identity("vps2", "prod", True))
    monkeypatch.setattr(sw, "_lookup", lambda host: None)
    assert sw.main() == 0
    marker = [s for s in sent if "taking over" in s[0].lower()]
    assert len(marker) == 1 and "lookup failed" in " ".join(marker[0][1])
    assert sc.read_identity(path).taking_over is True


def test_watch_survives_a_failed_send(monkeypatch, capsys):
    _wire(monkeypatch, PROD, fetched=None)

    def boom(title, lines):
        raise RuntimeError("no network")
    monkeypatch.setattr(sw, "_send", boom)
    assert sw.main() == 0
    assert "not sent" in capsys.readouterr().out.lower()
