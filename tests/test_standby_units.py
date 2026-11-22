"""The standby's own unit pair and its one-time provisioning script.

The standby runs ONE timer: the nightly pull. It must install no
`trading-<env>-*` unit except the failure notifier template, because the sync's
promoted-box lock reads `trading-prod.target` and a standby that had the stack's
units could be started by mistake.
"""
import pathlib
import shutil
import subprocess

import pytest

from deploy.systemd import generate_units as gu
from deploy.systemd import standby_units as su
from shared import standby_config

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROVISION = ROOT / "deploy" / "standby" / "provision.sh"
POSIX_ROOT = pathlib.PurePosixPath("/home/administrator/dev")


@pytest.fixture(autouse=True)
def _posix_root(monkeypatch):
    monkeypatch.setattr(gu, "REPO_ROOT", POSIX_ROOT)


def _at(monkeypatch, value):
    real = standby_config.sync_settings()
    monkeypatch.setattr(standby_config, "sync_settings",
                        lambda: {**real, "at": value})


def test_sync_timer_from_config(monkeypatch):
    _at(monkeypatch, "23:05")
    timer = su.render()["standby-sync.timer"]
    assert "OnCalendar=*-*-* 23:05:00" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_sync_timer_pads_an_unpadded_time(monkeypatch):
    _at(monkeypatch, "7:5")
    assert "OnCalendar=*-*-* 07:05:00" in su.render()["standby-sync.timer"]


def test_sync_service_alerts_on_failure():
    svc = su.render()["standby-sync.service"]
    assert (f"OnFailure=trading-{gu.ENV_NAME}-notify-failure@%n.service" in svc)
    assert "Type=oneshot" in svc
    assert "TimeoutStartSec=7200" in svc
    assert "Environment=TZ=America/Chicago" in svc
    assert "WorkingDirectory=/home/administrator/dev" in svc
    assert ("ExecStart=/home/administrator/dev/.venv/bin/python "
            "tools/standby_sync.py") in svc


def test_sync_service_is_not_start_fenced():
    # The fence refuses a unit on a standby box; this one must run there.
    svc = su.render()["standby-sync.service"]
    assert gu.fence_name() not in svc


def test_no_trading_prod_unit_rendered():
    out = su.render()
    assert set(out) == {"standby-sync.service", "standby-sync.timer",
                        gu.notifier_name()}
    for name, text in out.items():
        assert ".target" not in name
        assert "trading-prod.target" not in text
        assert "WantedBy=trading" not in text
    extra = [n for n in out if n.startswith("trading-")
             and n != gu.notifier_name()]
    assert extra == []


def test_install_writes_every_unit(tmp_path):
    written = su.install(tmp_path)
    assert {p.name for p in written} == set(su.render())
    assert all(p.read_text(encoding="utf-8") for p in written)


def test_activate_reloads_then_enables_the_timer():
    calls = []

    def runner(cmd):
        calls.append(cmd[2:])
        return True, ""

    su.activate(runner)
    assert calls[0] == ["daemon-reload"]
    assert ["enable", "--now", "standby-sync.timer"] in calls


def test_uninstall_disables_and_deletes(tmp_path):
    su.install(tmp_path)
    calls = []
    su.uninstall(tmp_path, runner=lambda cmd: (calls.append(cmd[2:]) or (True, "")))
    assert ["disable", "--now", "standby-sync.timer"] in calls
    assert not (tmp_path / "standby-sync.service").exists()
    assert not (tmp_path / "standby-sync.timer").exists()


def test_provision_script_shape():
    raw = PROVISION.read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_provision_script_parses():
    r = subprocess.run(["bash", "-n", str(PROVISION)], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
