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


# The origins that face the world with NO login. The app host is deliberately
# absent -- it is the thing these must not advertise.
_PUBLIC_HOSTS = (repo_paths.SITE_HOST, repo_paths.LIVE_HOST)


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
    published to the internet the moment it lands there.

    ⚠ ``.woff2`` was added on 2026-09-06 so the site could SELF-HOST Inter
    instead of linking Google Fonts, which hands Google the IP of every visitor.
    Recorded because widening this set is the one edit that makes the guard
    quietly weaker, and each addition should cost the same deliberation:
    everything the list admits is world-readable by definition.

    ⚠ It also caught its first real thing that day. The site's own tests were
    first written to ``deploy/site/tests/`` -- beside the thing they test, which
    is the ordinary habit everywhere else in this repo and exactly wrong here,
    because Caddy would have served the .py files as downloads. They live in
    ``deploy/tests/`` instead. **Nothing that is not served belongs under this
    directory**, however natural its placement looks.
    """
    allowed = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp",
               ".txt", ".woff2"}
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


def test_every_hostname_is_served(cfg):
    assert _block(cfg, repo_paths.SITE_HOST).startswith(
        f"{repo_paths.SITE_HOST}, www.{repo_paths.SITE_HOST} {{")
    assert _block(cfg, repo_paths.APP_HOST).startswith(f"{repo_paths.APP_HOST} {{")
    assert _block(cfg, repo_paths.LIVE_HOST).startswith(f"{repo_paths.LIVE_HOST} {{")


def test_the_app_host_is_not_advertised_by_the_public_blocks(cfg):
    """Recorded as noise reduction, not as a security control -- the subdomain is
    in Certificate Transparency regardless -- but the public page and its block
    should not point at it.

    ⚠ Widened from the site block alone when the LIVE origin arrived. Both of
    those blocks face the world with no login, and the live one is the easier
    place to leak the app by accident: it was written by copying the app block,
    so it starts life holding the app's own hostname."""
    for host in _PUBLIC_HOSTS:
        assert repo_paths.APP_HOST not in _block(cfg, host), host


def test_every_block_sends_hsts(cfg):
    """Every name, not just two. The header carries no ``includeSubDomains``
    (deliberately -- see the generator), so ``live.`` does NOT inherit the apex
    policy and a downgrade there would be a foothold on a neighbouring name."""
    for host in (repo_paths.SITE_HOST, repo_paths.APP_HOST, repo_paths.LIVE_HOST):
        assert "Strict-Transport-Security" in _block(cfg, host), host


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


# --- the live block: public BY DESIGN ----------------------------------------
def test_the_live_host_is_reverse_proxied_to_the_live_port(cfg):
    assert f"{repo_paths.LIVE_HOST} {{" in cfg
    assert f"reverse_proxy 127.0.0.1:{repo_paths.NICEGUI_LIVE_PORT}" in cfg


def test_the_live_port_comes_from_repo_paths_not_a_literal(cfg, monkeypatch):
    """Partner to the app block's version of this. 8500 and 8501 differ by one
    character, and a literal here would front the PRIVATE app on a hostname
    with no login -- which is the single worst outcome this file can produce."""
    monkeypatch.setattr(caddy, "NICEGUI_LIVE_PORT", 9501)
    assert "127.0.0.1:9501" in caddy.render()


def test_the_live_block_carries_no_authentication(cfg):
    """The live screens are public BY DESIGN. Pinned so that a later copy-paste
    of the app block does not quietly put a login in front of them -- or, worse,
    a login that does not work and reads as an outage.

    Brace-counted via ``_block`` rather than split on the first ``
}``: the app
    block this was copied from nests ``header { ... }`` and ``handle { ... }``,
    so a naive split would end the block early and stop seeing exactly the
    directives it is looking for."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    for directive in ("basicauth", "basic_auth", "forward_auth", "jwt"):
        assert directive not in block, directive


def test_the_live_block_cannot_reach_the_private_app(cfg):
    """It fronts :8501 and must never name :8500. The two origins exist to keep
    the public internet out of the process that serves /terminate; a stray
    upstream here would hand it over on a hostname with no login."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert f"127.0.0.1:{repo_paths.NICEGUI_PORT}" not in block
    assert repo_paths.APP_HOST not in block


def test_the_live_block_is_still_covered_by_the_edge_header_count(cfg):
    """Non-vacuity partner for ``test_every_reverse_proxy_stamps_the_edge_header``,
    which is a whole-file COUNT and would stay green if a new block added
    neither a proxy nor a header.

    ``live_main`` mounts no auth middleware and reads this header nowhere -- it
    is stamped so the file-wide invariant stays unconditional, because an
    exception carved out for "the block that does not need it" is a hole a
    future block proxying the app could sit in."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert block.count("reverse_proxy") == 1
    assert block.count("header_up X-Edge 1") == 1


# --- what a crawler is told, per origin --------------------------------------
def test_the_live_origin_answers_robots_and_refuses_indexing(cfg):
    """⚠ A 404 on ``/robots.txt`` is not neutral -- crawlers read it as
    crawl-everything, so the archived state was arriving by DEFAULT.

    ``Disallow: /`` is the decided answer: publishing a live trading book is
    reversible, permanently archiving it in a search cache, the Wayback Machine
    and Common Crawl is not, and that step was never decided. Discoverability is
    unaffected -- ``SITE_HOST`` stays crawlable and its live.html links every
    screen. See ``_live_block``'s docstring."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert "handle /robots.txt" in block
    assert "User-agent: *" in block
    assert "Disallow: /" in block
    assert "Allow: /" not in block


def test_the_live_robots_body_is_labelled_text(cfg):
    """``respond`` sets no Content-Type of its own, and an unlabelled body is
    left to the crawler to sniff."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert 'header Content-Type "text/plain; charset=utf-8"' in block


def test_the_robots_rule_precedes_the_upstream(cfg):
    """``handle`` blocks are mutually exclusive and evaluated in order. Behind
    the catch-all, ``/robots.txt`` would reach the app and 404 -- the exact state
    this replaces, with a rule above it that reads as a control."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert block.index("handle /robots.txt") < block.index("handle {")


def test_the_upstream_is_inside_a_catch_all_handle(cfg):
    """Mixing a bare directive with ``handle`` blocks in one site is a routing
    order someone has to reason about; the app block already solved this by
    putting its upstream in a catch-all ``handle``, and this mirrors it."""
    block = _block(cfg, repo_paths.LIVE_HOST)
    assert re.search(r"handle \{\s*reverse_proxy ", block), block


def test_the_public_site_still_invites_crawlers():
    """Non-vacuity partner: the decision is per-origin, and the apex is the
    findable way in. A `Disallow: /` that spread to the one-pager would make the
    project unsearchable while the screens stayed exactly as exposed."""
    robots = (pathlib.Path(repo_paths.SITE_ROOT) / "robots.txt").read_text(encoding="utf-8")
    directives = [ln.strip() for ln in robots.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    assert "Allow: /" in directives
    assert "Disallow: /" not in directives


def test_the_apex_robots_file_no_longer_describes_a_placeholder():
    """It claimed the live-screens page was an empty placeholder carrying its
    own ``noindex`` meta tag. Both halves are false -- ``test_site.py`` asserts
    that tag is GONE -- and the house rule is to correct in place, never to
    append under stale text."""
    robots = (pathlib.Path(repo_paths.SITE_ROOT) / "robots.txt").read_text(encoding="utf-8")
    assert "placeholder" not in robots
    assert "noindex" not in robots
    # The correction is not archaeology: the file states the CURRENT division of
    # labour, and points at the origin that owns the other half.
    assert "_live_block" in robots


# --- caching -----------------------------------------------------------------
#
# The static site shipped with NO Cache-Control at all. Caddy's file_server
# sends ETag and Last-Modified but no freshness directive, so browsers fall back
# to HEURISTIC caching -- they invent a lifetime and will not even revalidate
# until it expires. Measured in production the day the live grid shipped: the
# grid rendered completely unstyled for a returning visitor, because their
# browser held a `site.css` from before the deploy while serving fresh HTML.
#
# Every filename here is UNVERSIONED (`site.css`, not `site.abc123.css`), so
# nothing may be cached without revalidation except the fonts.


def test_html_css_and_js_revalidate(cfg):
    """`no-cache` does NOT mean "do not cache" -- it means "cache, but always
    revalidate", which with the ETag Caddy already sends makes the common case a
    cheap 304 rather than a re-download.

    These filenames are unversioned, so this is the only correct policy: a
    freshness lifetime on `site.css` is a promise the deploy cannot keep."""
    block = _block(cfg, repo_paths.SITE_HOST)
    assert "@revalidate" in block
    assert re.search(r'header\s+@revalidate\s+Cache-Control\s+"no-cache"', block)
    for ext in ("*.html", "*.css", "*.js"):
        assert ext in block, f"{ext} is not matched by the revalidate rule"


def test_the_live_captures_go_stale_on_their_own(cfg):
    """The thumbnails are REWRITTEN under the same filenames every 15 minutes,
    so they cannot be cached long -- but a page view pulls fourteen of them, and
    revalidating every one on every view is fourteen round trips for images that
    change four times an hour. A short lifetime under the capture interval is
    the trade."""
    block = _block(cfg, repo_paths.SITE_HOST)
    assert "@captures" in block
    m = re.search(r'header\s+@captures\s+Cache-Control\s+"max-age=(\d+)', block)
    assert m, "no Cache-Control on the captures"
    assert int(m.group(1)) < 900, (
        "a capture may not outlive the 15-minute capture interval, or the grid "
        "shows a thumbnail older than the one on disk")


def test_the_fonts_are_the_only_thing_cached_without_revalidation(cfg):
    """Two self-hosted woff2 faces, byte-stable for the life of the brand, and
    the largest repeated download on the site. Everything else revalidates."""
    block = _block(cfg, repo_paths.SITE_HOST)
    assert "*.woff2" in block
    m = re.search(r'header\s+@fonts\s+Cache-Control\s+"max-age=(\d+)', block)
    assert m and int(m.group(1)) >= 86400


def test_caching_is_declared_only_where_the_files_are(cfg):
    """The app and live blocks are reverse proxies -- their upstream owns its own
    caching, and a header here would silently override what the app decides."""
    for host in (repo_paths.APP_HOST, repo_paths.LIVE_HOST):
        assert "Cache-Control" not in _block(cfg, host), (
            f"{host} is a proxy; it must not dictate caching for its upstream")


def test_every_matcher_the_cache_rules_name_is_defined(cfg):
    """A `header @foo` naming a matcher that was never declared is a Caddy
    parse error, which takes BOTH sites down at reload -- and these tests assert
    on a string, so nothing else here would catch it."""
    block = _block(cfg, repo_paths.SITE_HOST)
    used = set(re.findall(r'header\s+(@\w+)\s+Cache-Control', block))
    declared = set(re.findall(r'^\s*(@\w+)\s*\{', block, re.M))
    declared |= set(re.findall(r'^\s*(@\w+)\s+path\s', block, re.M))
    assert used, "no cache rules found at all"
    assert used <= declared, f"undeclared matchers: {sorted(used - declared)}"


def test_the_generated_config_is_pure_ascii(cfg):
    """A decorative box-drawing character in a comment is a real, if small,
    hazard in a file whose failure mode is BOTH sites going down at reload: it
    survives only as long as every tool in the path -- the generator's write,
    an editor, a `cat` over ssh, whatever inspects it next -- agrees on UTF-8.

    Caught by writing one: a `⚠` added to a comment here raised
    UnicodeEncodeError on a Windows console the moment the config was printed.
    Nothing in the output needs a character outside ASCII, and the file was
    already clean before that, so this pins what was already true."""
    offenders = [(i, line) for i, line in enumerate(cfg.splitlines(), 1)
                 if any(ord(ch) > 127 for ch in line)]
    assert offenders == [], (
        "non-ASCII in a generated system config: "
        + "; ".join(f"line {i}: {line.encode('ascii', 'replace').decode()}"
                    for i, line in offenders))
