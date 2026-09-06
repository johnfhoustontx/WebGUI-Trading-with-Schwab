"""The public edge config that fronts both hostnames.

DERIVED from ``repo_paths``, never hand-written, for the same reason the systemd
units are: a committed Caddyfile would be a second copy of the checkout root and
the web GUI's port, free to drift -- and the drift would surface as a 502 in
production rather than as a failing test.

⚠ **Caddy itself has validated none of this.** These tests assert on the
GENERATED STRING. Nothing here proves Caddy will parse it, so a syntax error
would still take both sites down at reload; ``caddy validate --config`` on the
box is the missing half and belongs in the deployment step, not here.

The two sharpest tests are the ones whose subject is not this file's syntax but
its meaning:

* ``test_the_file_server_root_is_the_site_dir_and_never_the_checkout_root`` --
  a root one level too high publishes ``shared/tokens.json``,
  ``shared/appsettings.json``, ``shared/webgui_auth.json`` and
  ``config/env.local.toml`` to the internet, and the site would look perfect
  while it happened.
* ``test_every_reverse_proxy_stamps_the_edge_header`` -- without that header
  ``webgui.main._client_ip`` degrades to the peer, every request through the
  edge lands in ONE lockout bucket, and the per-client backoff (which ramps to
  900 s, where the global one is deliberately 60 s) silently becomes global. A
  bot spraying the advertised hostname would lock the owner out of the UI that
  arms the trading driver and stops the stack.
"""
import pathlib
import re

import pytest

import repo_paths

# NOT importorskip. A missing generator must fail this suite, not skip it --
# a skipped test reads as a passing one, which this repo has been bitten by.
from deploy.caddy import generate_caddyfile as caddy


POSIX_SITE_ROOT = pathlib.PurePosixPath("/home/administrator/prod/deploy/site")


@pytest.fixture(autouse=True)
def _posix_root(monkeypatch):
    r"""Pin the served root to a POSIX path for every test in this file.

    A Caddyfile is a Linux artifact unconditionally, so its shape must be
    asserted as Linux regardless of the host running the suite. Without this the
    path tests would assert a ``D:\...`` root on the Windows dev box and a real
    one on the VPS -- the same test, two meanings, decided by machine state.
    (Mirrors ``tests/test_systemd_units.py::_posix_root``.)

    ⚠ It patches the GENERATOR's copy only. The test that walks the served
    directory for stray files reads ``repo_paths.SITE_ROOT``, and must, since its
    subject is this checkout's real tree.
    """
    monkeypatch.setattr(caddy, "SITE_ROOT", POSIX_SITE_ROOT)


@pytest.fixture
def cfg():
    return caddy.render()


def _block(text, host):
    """The site block whose address line starts with ``host``.

    Brace-counted rather than regexed: the app block nests ``header { ... }``
    and ``handle { ... }``, so the first ``}`` is not the end of anything.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(host) and line.rstrip().endswith("{"):
            depth, out = 0, []
            for cur in lines[i:]:
                out.append(cur)
                depth += cur.count("{") - cur.count("}")
                if depth == 0:
                    return "\n".join(out)
    raise AssertionError(f"no site block for {host!r} in:\n{text}")


# --- A. the served root ------------------------------------------------------
def test_the_file_server_root_is_the_site_dir_and_never_the_checkout_root(cfg):
    """One level too high and the secrets are on the internet, silently."""
    root = re.search(r'root \* "([^"]+)"', cfg).group(1)
    assert root.endswith("/deploy/site")
    assert pathlib.Path(root).name == "site"


def test_a_checkout_path_containing_a_space_stays_one_argument(monkeypatch):
    r"""The served root is QUOTED, and this is why.

    Caddy splits a directive on whitespace, so an unquoted
    ``root * /home/my dir/deploy/site`` is two arguments. The failure is either a
    parse error -- which takes down BOTH hostnames, since one bad file stops
    Caddy loading at all -- or, far worse, a root of ``/home/my`` that quietly
    serves a tree ABOVE the checkout. That is exactly the catastrophe the test
    above exists to prevent, arriving through a door it cannot see, because the
    POSIX-root fixture never contains a space.

    Not hypothetical: rendered on the Windows dev checkout
    (``D:/WebGUI Trading with Schwab/...``) the unquoted form truncated to
    ``D:/WebGUI``.
    """
    spaced = pathlib.PurePosixPath("/home/my dir/deploy/site")
    monkeypatch.setattr(caddy, "_site_root", lambda: spaced)
    root = re.search(r'root \* "([^"]+)"', caddy.render()).group(1)
    assert root == str(spaced), "the whole path must survive as one argument"


def test_the_served_root_is_a_posix_path(cfg):
    r"""A Caddyfile is a Linux artifact unconditionally.

    Rendering ``str(Path)`` on the Windows dev box would emit ``D:\...\site``,
    which Caddy would take as a relative path and serve the wrong tree -- and
    the test above would pass, because ``.name`` is backslash-aware on Windows.
    """
    root = re.search(r'root \* "([^"]+)"', cfg).group(1)
    assert "\\" not in root
    assert root.startswith("/")


def test_no_reverse_proxy_appears_in_the_public_block(cfg):
    assert "reverse_proxy" not in _block(cfg, repo_paths.SITE_HOST)


def test_the_public_block_cannot_reach_the_app(cfg):
    """No loopback address of any kind in the block that faces the world."""
    public = _block(cfg, repo_paths.SITE_HOST)
    assert "127.0.0.1" not in public
    assert "localhost" not in public
    assert str(repo_paths.NICEGUI_PORT) not in public


def test_the_site_directory_holds_nothing_but_site_assets():
    """A stray symlink, a copied config, or a debug dump in deploy/site is
    published to the internet the moment it lands there."""
    allowed = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp", ".txt"}
    root = pathlib.Path(repo_paths.SITE_ROOT)
    # Not vacuous: an empty or missing served root would pass every assertion
    # below while Caddy served a 404 for the whole public site.
    assert (root / "index.html").is_file(), f"{root} has no index.html to serve"
    for p in root.rglob("*"):
        assert not p.is_symlink(), f"{p} is a symlink out of the served root"
        if p.is_file():
            assert p.suffix.lower() in allowed, f"{p} is not a site asset"


# --- B. the client address the lockout counts against ------------------------
def test_every_reverse_proxy_stamps_the_edge_header(cfg):
    assert cfg.count("reverse_proxy") == cfg.count("header_up X-Edge 1")


def test_nothing_suppresses_x_forwarded_for(cfg):
    """Caddy sets XFF by default and APPENDS the peer it observed, which is what
    makes reading the tail safe. Suppressing or replacing it would leave
    ``_client_ip`` reading a hop the client chose.

    Case-folded because a Caddyfile header name may be written in any case, so
    ``header_up -x-forwarded-for`` is a real spelling of the same mistake. The
    second assertion is the load-bearing one: the first catches SUPPRESSION,
    only this catches REPLACEMENT with a fixed value."""
    lowered = cfg.lower()
    assert "-x-forwarded-for" not in lowered
    assert "x-forwarded-for" not in lowered      # not even set to a fixed value


def test_the_edge_header_is_the_one_the_app_reads(cfg):
    """The name is a contract with ``auth_middleware.EDGE_HEADER``; a rename on
    either side is invisible until the lockout quietly goes global."""
    import sys
    sys.path.insert(0, str(repo_paths.WEBGUI))
    import auth_middleware

    m = re.search(r"header_up (\S+) 1", cfg)
    assert m, "nothing stamps an edge header at all"
    assert m.group(1).lower() == auth_middleware.EDGE_HEADER.lower()


# --- the app block -----------------------------------------------------------
def test_the_wall_is_refused_at_the_edge(cfg):
    """The wall authenticates by being loopback with no edge header. Refusing it
    here means the edge never even offers the route to the internet."""
    app = _block(cfg, repo_paths.APP_HOST)
    assert re.search(r"handle /wall\*\s*\{\s*respond 404\s*\}", app)


def test_the_port_comes_from_repo_paths_not_a_literal(cfg, monkeypatch):
    assert f"127.0.0.1:{repo_paths.NICEGUI_PORT}" in cfg
    monkeypatch.setattr(caddy, "NICEGUI_PORT", 9500)
    assert "127.0.0.1:9500" in caddy.render()


def test_both_hostnames_are_served(cfg):
    assert _block(cfg, repo_paths.SITE_HOST).startswith(
        f"{repo_paths.SITE_HOST}, www.{repo_paths.SITE_HOST} {{")
    assert _block(cfg, repo_paths.APP_HOST).startswith(f"{repo_paths.APP_HOST} {{")


def test_the_app_host_is_not_advertised_by_the_public_block(cfg):
    """Recorded as noise reduction, not as a security control -- the subdomain is
    in Certificate Transparency regardless -- but the public page and its block
    should not point at it."""
    assert repo_paths.APP_HOST not in _block(cfg, repo_paths.SITE_HOST)


def test_both_blocks_send_hsts(cfg):
    for host in (repo_paths.SITE_HOST, repo_paths.APP_HOST):
        assert "Strict-Transport-Security" in _block(cfg, host)


def test_the_app_block_refuses_to_be_framed_off_origin(cfg):
    assert "frame-ancestors 'self'" in _block(cfg, repo_paths.APP_HOST)


def test_no_rate_limit_directive(cfg):
    """DECIDED, and pinned because the failure is total. Caddy's rate limiter
    ships in no prebuilt binary; it needs an xcaddy build and a manual rebuild on
    every Caddy update, with no apt security updates. An unknown directive makes
    Caddy refuse the whole config, taking BOTH sites down at reload. Throttling
    lives in the app, where ``LockoutState`` refuses before Argon2."""
    assert "rate_limit" not in cfg


# --- the environment guard ---------------------------------------------------
def test_the_generator_refuses_to_run_in_a_dev_checkout(monkeypatch):
    """There is one Caddy on the box and it fronts prod. A dev checkout emitting
    an edge config could only ever overwrite prod's.

    ⚠ Monkeypatched, not ambient: ``repo_paths`` pins ENV_NAME to ``prod`` under
    pytest regardless of the local marker, so the dev branch is unreachable
    otherwise."""
    monkeypatch.setattr(caddy, "ENV_NAME", "dev")
    with pytest.raises(SystemExit):
        caddy.render()


def test_install_writes_the_rendered_config(tmp_path):
    dest = tmp_path / "Caddyfile"
    written = caddy.install(dest)
    assert written == dest
    assert dest.read_text(encoding="utf-8") == caddy.render()
