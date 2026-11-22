"""tools/standby_failover.py: takeover, drill, demotion and the neighbours' fence.

Everything external (systemctl, curl, uv, python, the socket) is a fake; the home
and the checkout are tmp_path folders."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import repo_paths  # noqa: E402
from deploy.systemd import generate_units as gu  # noqa: E402
from shared import standby_config as sc  # noqa: E402
from shared.standby_config import Identity  # noqa: E402
from tools import standby_failover as sf  # noqa: E402

BOXES = {
    "vps2": {"ssh": ["a"], "public_ip": "10.0.0.2",
             "proxy_health": "https://vps2.tail.ts.net:8100/health"},
    "vps3": {"ssh": ["b"], "public_ip": "10.0.0.3",
             "proxy_health": "https://vps3.tail.ts.net:8100/health"},
}
SPECS = [
    {"name": "cos-hub", "dir": "cos-hub", "exclude": [], "files": [],
     "units": ["cos-hub.service"]},
    {"name": "reports", "dir": "reports", "exclude": [], "files": [],
     "units": ["rep@.service", "rep-failed@.service", "rep-*.timer", "rep@globex.service.d"]},
]
STANDBY = Identity("vps3", "standby", False)
FENCE = gu.fence_name()


@pytest.fixture(autouse=True)
def _boxes(monkeypatch):
    monkeypatch.setattr(sc, "boxes", lambda: {k: dict(v) for k, v in BOXES.items()})


class Fake:
    """A ``subprocess.run`` stand-in: records argv, fails what ``fail`` matches."""

    def __init__(self, fail=None, raises=None):
        self.calls = []
        self.fail = fail or (lambda argv: False)
        self.raises = raises or (lambda argv: False)

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if self.raises(argv):
            raise RuntimeError("boom")
        rc = 1 if self.fail(argv) else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="bad" if rc else "")

    def has(self, *words):
        return [c for c in self.calls if all(w in c for w in words)]


class Sock:
    closed = False

    def close(self):
        self.closed = True


def _down(addr, timeout=None):
    raise OSError("refused")


def _up(addr, timeout=None):
    return Sock()


def _curl_down(argv):
    return argv[0] == "curl"


def _app(tmp_path):
    """A home and a checkout with a config folder."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    home.mkdir()
    return home, repo


def _settings(home):
    return {"generations": home / "standby" / "generations", "max_age_h": 80}


def _kept(home, name, files, freeze=None):
    kept = home / "standby" / "units" / name
    for rel, text in files.items():
        f = kept / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    if freeze is not None:
        (kept / "venv-freeze.txt").write_text(freeze, encoding="utf-8")


def _go(tmp_path, identity=STANDBY, run=None, connect=_down, wait=None, **kw):
    home, repo = _app(tmp_path) if not (tmp_path / "home").exists() else (
        tmp_path / "home", tmp_path / "repo")
    run = run or Fake(fail=_curl_down)
    lines = []
    ipath = tmp_path / "box.toml"
    if identity is not None and not ipath.exists():
        sc.write_identity(identity, ipath)
    rc = sf.takeover(identity, home=home, repo=repo, run=run, connect=connect,
                     wait=wait or (lambda url, timeout: True), identity_path=ipath,
                     settings=_settings(home), specs=SPECS,
                     now=dt.datetime(2026, 11, 22, 8, 0), out=lines.append, **kw)
    return rc, run, lines, home, repo, ipath


# --- probes -------------------------------------------------------------------

def test_public_probe_targets_peer_ip_not_dns():
    run = Fake()
    sf.probe_peer(BOXES["vps2"], "app.example.co", run=run, connect=_down)
    curl = [c for c in run.calls if "--resolve" in c][0]
    assert "app.example.co:443:10.0.0.2" in curl
    assert curl[-1] == "https://app.example.co/login"
    proxy = [c for c in run.calls if BOXES["vps2"]["proxy_health"] in c][0]
    assert "--max-time" in proxy and "-sS" in proxy


def test_probe_peer_names_what_answered():
    assert sf.probe_peer(BOXES["vps2"], "a.co", run=Fake(), connect=_up) == [
        "tailnet proxy", "public app", "public ssh"]
    assert sf.probe_peer(BOXES["vps2"], "a.co", run=Fake(fail=_curl_down),
                         connect=_down) == []


def test_probe_peer_skips_what_has_no_address():
    peer = {"ssh": [], "public_ip": "", "proxy_health": ""}
    answered, skipped = sf.probe_peer_detail(peer, "a.co", run=Fake(), connect=_up)
    assert answered == [] and len(skipped) == 3


# --- fence on the neighbours --------------------------------------------------

def _units(home, names):
    d = home / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("[Unit]\n", encoding="utf-8")
    return d


def test_fence_neighbours_writes_dropins_for_present_units_only(tmp_path):
    home = tmp_path
    d = _units(home, ["cos-hub.service", "rep@.service", "rep-daily.timer", "other.service"])
    (d / "rep@globex.service.d").mkdir()
    run = Fake()
    got = sf.fence_neighbours(SPECS, home, run=run)
    assert sorted(p.parent.name for p in got) == ["cos-hub.service.d", "rep@.service.d"]
    for p in got:
        assert p.read_text() == f"[Unit]\nRequires={FENCE}\nAfter={FENCE}\n"
    assert not (d / "rep-failed@.service.d").exists()      # not present
    assert not (d / "other.service.d").exists()            # not a neighbour
    assert not (d / "rep-daily.timer.d").exists()          # never on a timer
    assert not (d / "rep@globex.service.d" / "fence.conf").exists()
    assert run.has("daemon-reload")
    before = {p: p.read_text() for p in got}
    again = sf.fence_neighbours(SPECS, home, run=run)
    assert {p: p.read_text() for p in again} == before


def test_neighbour_units_get_fence_dropin(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    _kept(home, "cos-hub", {"cos-hub.service": "[Unit]\nDescription=hub\n"})
    _kept(home, "reports", {
        "rep@.service": "[Unit]\n", "rep-daily.timer": "[Timer]\n",
        "rep@globex.service.d/o.conf": "[Service]\n"})
    rc, run, _lines, home, *_ = _go(tmp_path)
    assert rc == 0
    d = home / ".config" / "systemd" / "user"
    assert FENCE in (d / "cos-hub.service.d" / "fence.conf").read_text()
    assert FENCE in (d / "rep@.service.d" / "fence.conf").read_text()
    assert (d / "rep@globex.service.d" / "o.conf").is_file()
    assert not (d / "rep@globex.service.d" / "fence.conf").exists()
    assert not (d / "rep-daily.timer.d").exists()


# --- takeover -------------------------------------------------------------------

def test_takeover_refuses_while_peer_answers(tmp_path):
    for answered in ("tailnet", "app", "ssh"):
        sub = tmp_path / answered
        sub.mkdir()
        if answered == "ssh":
            run, connect = Fake(fail=_curl_down), _up
        else:
            word = BOXES["vps2"]["proxy_health"] if answered == "tailnet" else "--resolve"
            run = Fake(fail=lambda a, w=word: a[0] == "curl" and w not in a)
            connect = _down
        rc, run, lines, home, repo, ipath = _go(sub, run=run, connect=connect)
        assert rc == 1, answered
        assert sc.read_identity(ipath) == STANDBY
        assert not run.has("start")
        assert not (repo / "config" / "env.local.toml").exists()
        assert not (home / ".config").exists()
        assert any("still answers" in line for line in lines)


def test_takeover_peer_is_down_overrides(tmp_path):
    rc, run, lines, *_ = _go(tmp_path, run=Fake(), connect=_up, peer_is_down=True)
    assert rc == 0
    assert any("--peer-is-down" in line for line in lines)


def test_takeover_all_probes_skipped_refuses_without_flag(tmp_path, monkeypatch):
    empty = {"ssh": [], "public_ip": "", "proxy_health": ""}
    monkeypatch.setattr(sc, "boxes", lambda: {"vps2": dict(empty), "vps3": dict(BOXES["vps3"])})
    rc, run, lines, home, _repo, ipath = _go(tmp_path)
    assert rc == 1 and sc.read_identity(ipath) == STANDBY
    assert any("no probe" in line for line in lines)
    assert any("skipped" in line for line in lines)
    sub = tmp_path / "again"
    sub.mkdir()
    rc, *_ = _go(sub, peer_is_down=True)
    assert rc == 0


def test_takeover_refuses_without_identity_or_when_already_prod(tmp_path):
    rc, run, lines, *_ = _go(tmp_path, identity=None)
    assert rc == 1 and not run.calls and any("no identity" in x for x in lines)
    sub = tmp_path / "p"
    sub.mkdir()
    rc, run, lines, *_ = _go(sub, identity=Identity("vps3", "prod", False))
    assert rc == 1 and not run.calls and any("already prod" in x for x in lines)


def test_takeover_marks_taking_over_and_installs(tmp_path):
    _app(tmp_path)
    env = tmp_path / "repo" / "config" / "env.local.toml"
    env.write_text('# kept\nname = "prod"\nproxy_public_url = "https://old:8100"\n',
                   encoding="utf-8")
    order = []

    class Ordered(Fake):
        def __call__(self, argv, **kw):
            if argv[:3] == ["systemctl", "--user", "start"]:
                order.append(("start", sc.read_identity(tmp_path / "box.toml")))
            return super().__call__(argv, **kw)

    rc, run, lines, home, repo, ipath = _go(tmp_path, run=Ordered(fail=_curl_down))
    assert rc == 0, lines
    assert sc.read_identity(ipath) == Identity("vps3", "prod", True)
    # the fence reads the identity, so it was prod before the first start
    assert order and all(i == Identity("vps3", "prod", True) for _, i in order)
    text = env.read_text(encoding="utf-8")
    assert 'proxy_public_url = "https://vps3.tail.ts.net:8100"' in text
    assert "# kept" in text and 'name = "prod"' in text and "old" not in text
    d = home / ".config" / "systemd" / "user"
    assert (d / gu.target_name()).is_file() and (d / FENCE).is_file()
    assert not (d / "standby-sync.timer").exists()
    assert run.has("start", gu.target_name())
    assert any("operator" in x.lower() or "STEPS" in x for x in lines)
    assert any("10.0.0.3" in x for x in lines)
    assert any("tailscale serve" in x for x in lines)


def test_takeover_creates_proxy_public_url_when_file_is_absent(tmp_path):
    rc, _run, _lines, _home, repo, _i = _go(tmp_path)
    assert rc == 0
    assert (repo / "config" / "env.local.toml").read_text(encoding="utf-8") == (
        'proxy_public_url = "https://vps3.tail.ts.net:8100"\n')


def test_takeover_starts_and_enables_what_it_installed(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    _kept(home, "cos-hub", {"cos-hub.service": "[Unit]\n"})
    _kept(home, "reports", {"rep@.service": "[Unit]\n", "rep-failed@.service": "[Unit]\n",
                            "rep-a.timer": "[Timer]\n", "rep-b.timer": "[Timer]\n"})
    rc, run, *_ = _go(tmp_path)
    assert rc == 0
    assert run.has("start", "cos-hub.service")
    assert not run.has("start", "rep@.service")            # a template starts nothing
    assert run.has("enable", "--now", "rep-a.timer") and run.has("enable", "--now", "rep-b.timer")


def test_takeover_prints_data_age_before_changing_anything(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    gens = home / "standby" / "generations"
    for name, synced in (("prod_2026-11-18_2000", True), ("prod_2026-11-21_2000", False)):
        (gens / name).mkdir(parents=True)
        if synced:
            (gens / name / "SYNCED").write_text("x")
    (home / "standby" / "status.json").write_text(
        '{"result": "failed", "detail": "the peer stopped"}', encoding="utf-8")
    rc, _run, lines, *_ = _go(tmp_path)
    text = "\n".join(lines)
    assert rc == 0
    assert "prod_2026-11-18_2000" in text and "84.0 h old" in text
    assert "OLDER than the limit" in text
    assert "failed - the peer stopped" in text and "did not finish cleanly" in text


def test_takeover_says_loudly_when_no_data(tmp_path):
    rc, _run, lines, *_ = _go(tmp_path)
    assert rc == 0 and any("NO synced generation" in x for x in lines)


def test_takeover_editable_install_only_with_self_line(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    home_posix = (home.resolve()).as_posix()
    _kept(home, "cos-hub", {"cos-hub.service": "[Unit]\n"},
          freeze=f"-e file://{home_posix}/cos-hub\nfoo==1.0\n")
    _kept(home, "reports", {"rep@.service": "[Unit]\n"}, freeze="foo==1.0\n")
    rc, run, *_ = _go(tmp_path)
    assert rc == 0
    uv = run.has("pip", "install", "-e")
    assert len(uv) == 1
    assert uv[0][uv[0].index("-e") + 1] == str(home / "cos-hub")
    assert uv[0][-2:] == ["--python", str(home / "cos-hub" / ".venv" / "bin" / "python")]


def test_takeover_failure_after_identity_keeps_it_and_says_so(tmp_path):
    run = Fake(fail=lambda a: a[0] == "curl" or a[:3] == ["systemctl", "--user", "start"])
    rc, _run, lines, _home, _repo, ipath = _go(tmp_path, run=run)
    assert rc == 1
    assert sc.read_identity(ipath) == Identity("vps3", "prod", True)
    text = "\n".join(lines)
    assert "FAILED" in text and "DONE:" in text and "NOT DONE:" in text
    assert "NOT rolled back" in text


def test_takeover_probe_failure_exits_1(tmp_path):
    rc, _run, lines, *_ = _go(tmp_path, wait=lambda url, timeout: ":8210/" not in url)
    assert rc == 1 and any(x.startswith("  DOWN") for x in lines)


# --- drill ----------------------------------------------------------------------

class Render(Fake):
    """A Fake whose ``generate_units`` call writes the named files into the unit folder."""

    def __init__(self, unit_dir, names=("trading-dev.target",), **kw):
        super().__init__(**kw)
        self.unit_dir, self.names = unit_dir, names

    def __call__(self, argv, **kw):
        if "generate_units" in " ".join(str(a) for a in argv):
            self.unit_dir.mkdir(parents=True, exist_ok=True)
            for n in self.names:
                (self.unit_dir / n).write_text("x")
        return super().__call__(argv, **kw)


def _drill(tmp_path, identity=STANDBY, run=None, wait=None):
    home, repo = _app(tmp_path) if not (tmp_path / "home").exists() else (
        tmp_path / "home", tmp_path / "repo")
    for name in ("environments.toml", "ports.toml"):
        (repo / "config" / name).write_text((ROOT / "config" / name).read_text(
            encoding="utf-8"), encoding="utf-8")
    run = run or Render(home / ".config" / "systemd" / "user")
    lines = []
    rc = sf.drill(identity, home=home, repo=repo, run=run,
                  wait=wait or (lambda url, timeout: True), out=lines.append)
    return rc, run, lines, home, repo


def test_drill_refuses_on_prod(tmp_path):
    rc, run, lines, _h, repo = _drill(tmp_path, identity=Identity("vps3", "prod", False))
    assert rc == 1 and not run.calls and any("standby" in x for x in lines)
    assert not (repo / "config" / "env.local.toml").exists()
    sub = tmp_path / "none"
    sub.mkdir()
    assert _drill(sub, identity=None)[0] == 1


def test_drill_refuses_on_promoted_box(tmp_path):
    _app(tmp_path)
    _units(tmp_path / "home", ["trading-prod.target"])
    rc, run, lines, *_ = _drill(tmp_path)
    assert rc == 1 and not run.calls and any("promoted" in x for x in lines)


def test_drill_probes_dev_ports_and_cleans_up(tmp_path):
    _app(tmp_path)
    home, repo = tmp_path / "home", tmp_path / "repo"
    env = repo / "config" / "env.local.toml"
    env.write_bytes(b'# mine\r\nproxy_public_url = "https://x:8100"\r\n')
    original = env.read_bytes()
    d = _units(home, ["trading-dev-leftover.service"])
    urls = []

    class Writes(Fake):
        def __call__(self, argv, **kw):
            if "generate_units" in " ".join(argv):
                assert 'name = "dev"' in env.read_text(encoding="utf-8")
                for n in ("trading-dev.target", "trading-dev-webgui.service",
                          "trading-dev-options_svc.service"):
                    (d / n).write_text("x")
            return super().__call__(argv, **kw)

    rc, run, lines, *_ = _drill(tmp_path, run=Writes(),
                                wait=lambda url, timeout: urls.append(url) or True)
    assert rc == 0, lines
    assert env.read_bytes() == original
    assert sorted(p.name for p in d.glob("trading-dev*")) == ["trading-dev-leftover.service"]
    assert run.has("start", "trading-dev.target") and run.has("stop", "trading-dev.target")
    assert run.calls[-1][-1] == "daemon-reload"
    assert any("--no-activate" in c for c in run.calls)
    assert "http://127.0.0.1:9500/" in urls and "http://127.0.0.1:9211/health" in urls
    assert not any(":8100" in u for u in urls)
    assert sum(x.startswith("PASS") for x in lines) == len(urls)


def test_drill_restores_env_local_on_failure(tmp_path):
    _app(tmp_path)
    repo = tmp_path / "repo"
    env = repo / "config" / "env.local.toml"
    env.write_bytes(b'name = "prod"\n')
    run = Render(tmp_path / "home" / ".config" / "systemd" / "user",
                 fail=lambda a: a[:3] == ["systemctl", "--user", "start"])
    rc, run, lines, home, _r = _drill(tmp_path, run=run)
    assert rc == 1 and env.read_bytes() == b'name = "prod"\n'
    assert run.has("stop", "trading-dev.target")
    sub = tmp_path / "raises"
    sub.mkdir()
    _app(sub)
    env2 = sub / "repo" / "config" / "env.local.toml"
    env2.write_bytes(b"a = 1\n")
    rc, *_ = _drill(sub, run=Fake(raises=lambda a: "generate_units" in " ".join(a)))
    assert rc == 1 and env2.read_bytes() == b"a = 1\n"


def test_drill_restores_a_missing_env_local_by_deleting_it(tmp_path):
    rc, _run, _lines, _h, repo = _drill(tmp_path, wait=lambda url, timeout: False)
    assert rc == 1                                        # nothing answered: a failed drill
    assert not (repo / "config" / "env.local.toml").exists()


def test_drill_refuses_a_dev_render_with_a_proxy(tmp_path):
    _app(tmp_path)
    d = _units(tmp_path / "home", [])

    class Proxy(Fake):
        def __call__(self, argv, **kw):
            if "generate_units" in " ".join(argv):
                (d / "trading-dev-proxy.service").write_text("x")
                (d / "trading-dev.target").write_text("x")
            return super().__call__(argv, **kw)

    rc, run, lines, *_ = _drill(tmp_path, run=Proxy())
    assert rc == 1 and not run.has("start") and any("proxy" in x for x in lines)
    assert not (d / "trading-dev-proxy.service").exists()


# --- demotion -----------------------------------------------------------------

PROD = Identity("vps2", "prod", False)


def _demote(tmp_path, addresses=("10.0.0.3",), run=None, identity=PROD):
    home, repo = _app(tmp_path) if not (tmp_path / "home").exists() else (
        tmp_path / "home", tmp_path / "repo")
    run = run or Fake()
    lines = []
    ipath = tmp_path / "box.toml"
    if identity is not None:
        sc.write_identity(identity, ipath)
    rc = sf.become_standby(identity, home=home, repo=repo, run=run, specs=SPECS,
                           lookup=lambda host, t: None if addresses is None else list(addresses),
                           identity_path=ipath, out=lines.append)
    return rc, run, lines, home, ipath


def test_become_standby_refuses_when_dns_is_self(tmp_path):
    rc, run, lines, _h, ipath = _demote(tmp_path, addresses=("10.0.0.2",))
    assert rc == 1 and not run.calls and sc.read_identity(ipath) == PROD
    assert any("resolves to this box" in x for x in lines)


def test_become_standby_refuses_when_lookup_fails(tmp_path):
    rc, run, lines, _h, ipath = _demote(tmp_path, addresses=None)
    assert rc == 1 and not run.calls and sc.read_identity(ipath) == PROD
    assert any("retry" in x for x in lines)


def test_become_standby_takes_predemote_backup_first(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    d = _units(home, ["trading-prod.target", "trading-prod-webgui.service",
                      "trading-prod-backup.timer", "trading-prod-notify-failure@.service",
                      "cos-hub.service", "rep-daily.timer", "unrelated.service"])
    rc, run, _lines, _h, ipath = _demote(tmp_path)
    assert rc == 0
    backup = run.calls[0]
    assert "tools/backup_local.py" in backup and "--no-offsite" in backup
    assert backup[backup.index("--dest") + 1] == str(home / "backups" / "predemote")
    first_stop = next(i for i, c in enumerate(run.calls) if c[:3] == ["systemctl", "--user", "stop"]
                      or "disable" in c)
    assert first_stop > 0
    assert run.has("stop", "trading-prod.target") and run.has("disable", "--now", "cos-hub.service")
    assert run.has("disable", "--now", "trading-prod-backup.timer")
    assert run.has("disable", "--now", "rep-daily.timer")
    assert not run.has("stop", "unrelated.service")
    assert not (d / "trading-prod.target").exists()
    assert not (d / "trading-prod-webgui.service").exists()
    assert (d / "cos-hub.service").exists()
    assert (d / "standby-sync.timer").is_file()
    assert sc.read_identity(ipath) == Identity("vps2", "standby", False)


def test_become_standby_failed_backup_changes_nothing(tmp_path):
    rc, run, lines, _h, ipath = _demote(tmp_path, run=Fake(fail=lambda a: "backup_local.py" in " ".join(a)))
    assert rc == 1 and sc.read_identity(ipath) == PROD and len(run.calls) == 1
    assert any("backup failed" in x for x in lines)


def test_become_standby_failed_stop_keeps_role(tmp_path):
    _app(tmp_path)
    _units(tmp_path / "home", ["trading-prod.target"])
    rc, _run, lines, _h, ipath = _demote(
        tmp_path, run=Fake(fail=lambda a: "stop" in a))
    assert rc == 1 and sc.read_identity(ipath) == PROD
    assert (tmp_path / "home" / ".config" / "systemd" / "user" / "trading-prod.target").exists()


def test_constants_match_repo():
    assert repo_paths.ENV_NAME == "prod"      # pytest presents as prod


# --- drill cleanup is robust ----------------------------------------------------

def test_drill_removes_prod_files_a_misresolved_render_wrote(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    run = Render(d, names=("trading-prod.target", "trading-prod-proxy.service",
                           "trading-dev.target"))
    rc, run, lines, _h, repo = _drill(tmp_path, run=run)
    assert rc == 1 and list(d.iterdir()) == []
    assert not run.has("start")
    assert any("trading-prod" in x for x in lines)
    assert not (repo / "config" / "env.local.toml").exists()


def test_drill_fails_when_render_wrote_no_dev_target(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    rc, run, lines, *_ = _drill(tmp_path, run=Render(d, names=("trading-dev-x.service",)))
    assert rc == 1 and not run.has("start") and not list(d.iterdir())


def test_drill_interrupt_in_render_cleans_up_and_reraises(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    env = tmp_path / "repo" / "config" / "env.local.toml"
    env.write_bytes(b"a = 1\n")

    class Interrupt(Render):
        def __call__(self, argv, **kw):
            if "generate_units" in " ".join(argv):
                d.mkdir(parents=True, exist_ok=True)
                (d / "trading-prod.target").write_text("x")
                raise KeyboardInterrupt
            return super().__call__(argv, **kw)

    with pytest.raises(KeyboardInterrupt):
        _drill(tmp_path, run=Interrupt(d))
    assert env.read_bytes() == b"a = 1\n"
    assert not (d / "trading-prod.target").exists()


def test_drill_failed_stop_keeps_unit_files_and_fails(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    env = tmp_path / "repo" / "config" / "env.local.toml"
    env.write_bytes(b"a = 1\n")
    run = Render(d, names=("trading-dev.target", "trading-dev-webgui.service"),
                 fail=lambda a: a[:3] == ["systemctl", "--user", "stop"]
                 and "trading-dev-webgui.service" in a)
    rc, _run, lines, *_ = _drill(tmp_path, run=run)
    assert rc == 1
    assert env.read_bytes() == b"a = 1\n"
    assert (d / "trading-dev.target").exists() and (d / "trading-dev-webgui.service").exists()
    text = "\n".join(lines)
    assert "trading-dev-webgui.service" in text and "Finish by hand" in text
    assert "PASSED" not in text


def test_drill_restores_env_before_any_stop(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    env = tmp_path / "repo" / "config" / "env.local.toml"
    env.write_bytes(b"a = 1\n")
    seen = []

    class Stop(Render):
        def __call__(self, argv, **kw):
            if argv[:3] == ["systemctl", "--user", "stop"]:
                seen.append(env.read_bytes())
                raise KeyboardInterrupt
            return super().__call__(argv, **kw)

    with pytest.raises(KeyboardInterrupt):
        _drill(tmp_path, run=Stop(d))
    assert seen == [b"a = 1\n"]


def test_drill_rejects_an_env_local_that_does_not_say_dev(tmp_path, monkeypatch):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    monkeypatch.setattr(sf, "set_toml_key", lambda text, key, value: 'name = "prod"\n')
    rc, run, lines, *_ = _drill(tmp_path, run=Render(d))
    assert rc == 1 and not any("generate_units" in " ".join(c) for c in run.calls)


# --- demotion on the real neighbour layout, takeover enabling -------------------

REAL = ["cos-hub.service", "neuralstrike-report@.service",
        "neuralstrike-report-failed@.service", "neuralstrike-report-a.timer",
        "neuralstrike-report-b.timer", "trading-prod.target",
        "trading-prod-webgui.service"]
REAL_SPECS = [
    {"name": "cos-hub", "dir": "cos-hub", "exclude": [], "files": [],
     "units": ["cos-hub.service"]},
    {"name": "reports", "dir": "r", "exclude": [], "files": [],
     "units": ["neuralstrike-report@.service", "neuralstrike-report-failed@.service",
               "neuralstrike-report-*.timer", "neuralstrike-report@globex.service.d"]},
]


def test_become_standby_on_the_real_reports_layout(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    d = _units(home, REAL)
    (d / "neuralstrike-report@globex.service.d").mkdir()
    run = Fake()
    lines = []
    ipath = tmp_path / "box.toml"
    sc.write_identity(PROD, ipath)
    rc = sf.become_standby(PROD, home=home, repo=tmp_path / "repo", run=run,
                           specs=REAL_SPECS, lookup=lambda h, t: ["10.0.0.3"],
                           identity_path=ipath, out=lines.append)
    assert rc == 0, lines
    for c in run.calls:
        if "stop" in c or "disable" in c:
            assert not c[-1].endswith("@.service")
    assert run.has("stop", "neuralstrike-report@*.service")
    assert run.has("stop", "neuralstrike-report-failed@*.service")
    assert run.has("disable", "--now", "cos-hub.service")
    assert run.has("disable", "--now", "neuralstrike-report-a.timer")
    assert run.has("disable", gu.target_name()) and run.has("stop", gu.target_name())
    for unit in ("cos-hub.service", "neuralstrike-report@.service",
                 "neuralstrike-report-failed@.service"):
        assert FENCE in (d / (unit + ".d") / "fence.conf").read_text()
    assert not (d / "neuralstrike-report-a.timer.d").exists()
    assert not (d / gu.target_name()).exists()
    assert sc.read_identity(ipath) == Identity("vps2", "standby", False)


def test_takeover_enables_target_and_neighbour_services_after_identity(tmp_path):
    _app(tmp_path)
    home = tmp_path / "home"
    _kept(home, "cos-hub", {"cos-hub.service": "[Unit]\n"})
    _kept(home, "reports", {"rep@.service": "[Unit]\n"})
    seen = []

    class Watch(Fake):
        def __call__(self, argv, **kw):
            if argv[:3] == ["systemctl", "--user", "enable"]:
                seen.append(sc.read_identity(tmp_path / "box.toml"))
            return super().__call__(argv, **kw)

    rc, run, *_ = _go(tmp_path, run=Watch(fail=_curl_down))
    assert rc == 0
    assert run.has("enable", gu.target_name()) and run.has("enable", "cos-hub.service")
    assert not run.has("enable", "rep@.service")
    assert seen and all(i == Identity("vps3", "prod", True) for i in seen)


def test_drill_never_stops_a_bare_template(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    names = ("trading-dev.target", "trading-dev-webgui.service",
             "trading-dev-notify-failure@.service")
    run = Render(d, names=names, fail=lambda a: a[:3] == ["systemctl", "--user", "stop"]
                 and a[-1].endswith("@.service"))
    rc, run, lines, *_ = _drill(tmp_path, run=run)
    assert rc == 0, lines
    assert list(d.iterdir()) == []
    assert not any(c[-1].endswith("@.service") for c in run.calls if "stop" in c)
    assert run.has("stop", "trading-dev-notify-failure@*.service")


def test_drill_failed_instance_stop_counts_as_stuck(tmp_path):
    _app(tmp_path)
    d = tmp_path / "home" / ".config" / "systemd" / "user"
    run = Render(d, names=("trading-dev.target", "trading-dev-notify-failure@.service"),
                 fail=lambda a: "stop" in a and a[-1].endswith("@*.service"))
    rc, _r, lines, *_ = _drill(tmp_path, run=run)
    assert rc == 1 and (d / "trading-dev-notify-failure@.service").exists()
    assert any("notify-failure@*.service" in x and "Finish" in x for x in lines)


# --- T8: any HTTP status from the tailnet proxy is an answer ----------------------

def test_tailnet_probe_counts_a_5xx_as_answering():
    """A curl stand-in that behaves like curl against a proxy answering 503: with
    -f it exits 22, without it exits 0."""
    class Curl503(Fake):
        def __call__(self, argv, **kw):
            argv = [str(a) for a in argv]
            self.calls.append(argv)
            if argv[0] == "curl" and BOXES["vps2"]["proxy_health"] in argv:
                rc = 22 if any(a.startswith("-") and not a.startswith("--") and "f" in a
                               for a in argv) else 0
            else:
                rc = 1
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")
    assert sf.probe_peer(BOXES["vps2"], "a.co", run=Curl503(), connect=_down) == [
        "tailnet proxy"]


# --- takeover stops a running sync first ------------------------------------------

def test_takeover_stops_the_sync_before_removing_it(tmp_path):
    _app(tmp_path)
    d = _units(tmp_path / "home", ["standby-sync.service", "standby-sync.timer"])
    seen = []

    class Stop(Fake):
        def __call__(self, argv, **kw):
            if list(argv)[:4] == ["systemctl", "--user", "stop", "standby-sync.service"]:
                seen.append((d / "standby-sync.service").exists())
            return super().__call__(argv, **kw)

    rc, run, lines, *_ = _go(tmp_path, run=Stop(fail=_curl_down))
    assert rc == 0, lines
    assert seen == [True]                        # stopped while its file was still there
    assert not (d / "standby-sync.service").exists()


def test_takeover_refuses_when_the_sync_will_not_stop(tmp_path):
    _app(tmp_path)
    d = _units(tmp_path / "home", ["standby-sync.service", "standby-sync.timer"])
    run = Fake(fail=lambda a: a[0] == "curl" or a[2:4] == ["stop", "standby-sync.service"])
    rc, run, lines, _h, _r, ipath = _go(tmp_path, run=run)
    assert rc == 1 and sc.read_identity(ipath) == STANDBY
    assert (d / "standby-sync.service").exists() and not run.has("start")
    assert any("standby-sync.service" in x and "REFUSED" in x for x in lines)


def test_operator_steps_use_the_configured_proxy_port(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_paths, "PROXY_PORT", 8765)
    lines = []
    sf._operator_steps(BOXES["vps3"], lines.append)
    serve = [x for x in lines if "tailscale serve" in x][0]
    assert "--https=8765 http://127.0.0.1:8765" in serve and "8100" not in serve


# --- stand-down (a planned move) ------------------------------------------------

def _stand_down(tmp_path, identity=PROD, run=None, names=REAL):
    _app(tmp_path)
    d = _units(tmp_path / "home", list(names) + [
        "trading-prod-backup.timer", "trading-prod-backup.service", FENCE,
        "trading-prod-notify-failure@.service"])
    ipath = tmp_path / "box.toml"
    if identity is not None:
        sc.write_identity(identity, ipath)
    run = run or Fake()
    lines = []
    rc = sf.stand_down(identity, home=tmp_path / "home", run=run, specs=REAL_SPECS,
                       out=lines.append)
    return rc, run, lines, d, ipath


def test_stand_down_refuses_unless_prod(tmp_path):
    rc, run, lines, d, ipath = _stand_down(tmp_path, identity=STANDBY)
    assert rc == 1 and not run.calls and sc.read_identity(ipath) == STANDBY
    sub = tmp_path / "none"
    sub.mkdir()
    rc, run, *_ = _stand_down(sub, identity=None)
    assert rc == 1 and not run.calls


def test_stand_down_stops_everything_in_order_and_keeps_the_role(tmp_path):
    rc, run, lines, d, ipath = _stand_down(tmp_path)
    assert rc == 0, lines
    ctl = [c[2:] for c in run.calls if c[:2] == ["systemctl", "--user"]]
    for c in ctl:
        assert not c[-1].endswith("@.service"), c          # never a bare template

    def at(*words):
        return next(i for i, c in enumerate(ctl) if list(words) == c)
    timers = [at("disable", "--now", "trading-prod-backup.timer"),
              at("disable", "--now", "neuralstrike-report-a.timer"),
              at("disable", "--now", "neuralstrike-report-b.timer")]
    hub = at("disable", "--now", "cos-hub.service")
    instances = at("stop", "neuralstrike-report@*.service")
    target = (at("stop", gu.target_name()), at("disable", gu.target_name()))
    stack = at("stop", "trading-prod-webgui.service")
    fence = at("stop", FENCE)
    assert max(timers) < hub < instances < target[0] < target[1] < stack < fence
    assert fence == len(ctl) - 1                         # the fence goes last
    assert not [c for c in ctl if "notify-failure" in c[-1]]
    assert sc.read_identity(ipath) == PROD               # the role is not changed
    assert (d / gu.target_name()).exists()               # nothing deleted
    assert any("become-standby" in x for x in lines)


def test_stand_down_reports_a_failed_stop(tmp_path):
    rc, _run, lines, _d, ipath = _stand_down(
        tmp_path, run=Fake(fail=lambda a: a[-1] == "cos-hub.service"))
    assert rc == 1 and sc.read_identity(ipath) == PROD
    assert any("cos-hub.service" in x for x in lines)


def test_main_has_a_stand_down_command(monkeypatch):
    got = []
    monkeypatch.setattr(sf, "stand_down", lambda identity, **kw: got.append(identity) or 0)
    monkeypatch.setattr(sc, "read_identity", lambda *a, **k: PROD)
    assert sf.main(["stand-down"]) == 0 and got == [PROD]


def test_become_standby_failed_instance_stop_keeps_role(tmp_path):
    _app(tmp_path)
    d = _units(tmp_path / "home", REAL)
    run = Fake(fail=lambda a: "stop" in a and a[-1].endswith("@*.service"))
    ipath = tmp_path / "box.toml"
    sc.write_identity(PROD, ipath)
    lines = []
    rc = sf.become_standby(PROD, home=tmp_path / "home", repo=tmp_path / "repo", run=run,
                           specs=REAL_SPECS, lookup=lambda h, t: ["10.0.0.3"],
                           identity_path=ipath, out=lines.append)
    assert rc == 1 and sc.read_identity(ipath) == PROD
    assert (d / "trading-prod.target").exists()
