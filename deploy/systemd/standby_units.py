"""The standby box's one unit pair: the nightly pull (`tools/standby_sync.py`).

    .venv/bin/python -m deploy.systemd.standby_units --install     # write + arm
    .venv/bin/python -m deploy.systemd.standby_units --uninstall

The standby installs NO stack unit (`trading-<env>-*`) and no target: with none
present nothing on it can be started by mistake, and the sync's promoted-box lock
reads `trading-prod.target`, which a takeover installs and a standby never has.
The one `trading-*` file it does carry is the failure notifier TEMPLATE, so a
failed night sends the same push every other unit's failure does.

The service has NO start fence (`generate_units._with_fence`): the fence refuses
every stack unit on a box that is not prod, and this unit exists to run on one.
Design: docs/plans/2026-11-22-warm-standby-design.md, section 2.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from deploy.systemd import generate_units as gu  # noqa: E402
from shared import standby_config  # noqa: E402

SERVICE = "standby-sync.service"
TIMER = "standby-sync.timer"
SYNC_TIMEOUT_SEC = 7200


def _at():
    """``(hour, minute)`` of ``[sync] at``, which may arrive unpadded ("7:5")."""
    hh, mm = str(standby_config.sync_settings()["at"]).split(":")
    return int(hh), int(mm)


def render() -> dict:
    """``{unit filename: text}`` for the standby."""
    hh, mm = _at()
    svc = f"""[Unit]
Description=NeuralStrike standby - nightly pull from prod
# No PartOf, no [Install] and no start fence: the timer owns this, and the fence
# would refuse it on exactly the box it is for.

[Service]
Type=oneshot
WorkingDirectory={gu._workdir()}
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/Chicago
TimeoutStartSec={SYNC_TIMEOUT_SEC}
ExecStart={gu._python()} tools/standby_sync.py
"""
    tmr = f"""[Unit]
Description=NeuralStrike standby - nightly pull timer

[Timer]
# Derived from [sync] at in config/standby.toml.
OnCalendar=*-*-* {hh:02d}:{mm:02d}:00
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
"""
    out = {SERVICE: gu._with_failure_alert(SERVICE, svc), TIMER: tmr}
    out.update(gu._notifier_unit())
    return out


def _dest(dest):
    return pathlib.Path(dest or (pathlib.Path.home() / ".config" / "systemd" / "user"))


def install(dest=None) -> list:
    """Write the units into the systemd user directory. Returns written paths."""
    dest = _dest(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render().items():
        p = dest / name
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written


def activate(runner=None) -> list:
    """``daemon-reload``, then ``enable --now`` the timer. ``[(unit, status)]``."""
    ok, out = gu._run_systemctl(["daemon-reload"], runner)
    if not ok:
        return [(TIMER, f"FAILED daemon-reload: {out}")]
    ok, out = gu._run_systemctl(["enable", "--now", TIMER], runner)
    return [(TIMER, "ENABLED" if ok else f"FAILED: {out}")]


def uninstall(dest=None, runner=None) -> list:
    """Disable the timer and delete the files. Returns the paths removed."""
    gu._run_systemctl(["disable", "--now", TIMER], runner)
    dest = _dest(dest)
    removed = []
    for name in (SERVICE, TIMER):
        p = dest / name
        if p.exists():
            p.unlink()
            removed.append(p)
    gu._run_systemctl(["daemon-reload"], runner)
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--install", action="store_true",
                    help="write the units and arm the timer")
    ap.add_argument("--uninstall", action="store_true",
                    help="disable the timer and delete the units")
    args = ap.parse_args(argv)
    if args.install and args.uninstall:
        ap.error("--install and --uninstall are exclusive")
    if args.install:
        for p in install():
            print(f"wrote {p}")
        failed = False
        for unit, status in activate():
            print(f"{status:>16}  {unit}")
            failed |= status.startswith("FAILED")
        return 1 if failed else 0
    if args.uninstall:
        for p in uninstall():
            print(f"removed {p}")
        return 0
    for name, text in render().items():
        print(f"# ===== {name} " + "=" * (60 - len(name)))
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
