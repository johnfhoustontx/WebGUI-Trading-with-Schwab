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
  runs the paper books and stops the stack.
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
    # ⚠ ``.pdf`` was added on 2026-09-14 for the market reports' downloads,
    # uploaded into reports/ on the box. Same deliberation as .woff2 above.
    allowed = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico", ".webp",
               ".txt", ".woff2", ".pdf"}
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


def test_the_trade_ideas_manifest_revalidates(cfg):
    """ideas.json is rewritten after every hourly trade idea, under one name, so
    it takes the pages' policy rather than a lifetime."""
    block = _block(cfg, repo_paths.SITE_HOST)
    m = re.search(r"@revalidate\s+path\s+([^\n]+)", block)
    assert m and "*.json" in m.group(1).split()


def test_the_trade_idea_cards_live_a_day(cfg):
    """A card is named for its day and minute, so it never changes under its
    name; a day is safe, and must-revalidate keeps a pruned day from lingering."""
    block = _block(cfg, repo_paths.SITE_HOST)
    assert re.search(r"@ideas\s+path\s+/ideas/\*", block)
    m = re.search(r'header\s+@ideas\s+Cache-Control\s+"max-age=(\d+), must-revalidate"',
                  block)
    assert m and int(m.group(1)) == 86400


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


# --- the Blog's entry documents ----------------------------------------------
#
# An entry on the public site is an HTML document somebody else's tool wrote.
# Three layers keep script out of it, any one of which is enough: the service
# cleans it, the entry page frames it in a sandbox, and the edge sends a policy
# on the document itself -- which is the only one of the three still standing
# when a visitor opens ``/blog/<slug>/entry.html`` directly, outside its frame.
#
# The policy is ONE string, ``shared.blog_inbox.ENTRY_CSP``, read here, by the
# service and by the private preview. These tests assert the edge sends exactly
# that string, on exactly those documents, from exactly one block.

def _blog_path_pattern(cfg):
    """The ``@blog_entries`` expression as Caddy will read it: one unquoted
    token, taken from the RENDERED config and not from the generator's
    constant, so what is asserted is what would be installed."""
    public = _block(cfg, repo_paths.SITE_HOST)
    found = re.findall(r"^[ \t]*@blog_entries path_regexp (.*)$", public, re.M)
    assert len(found) == 1, f"expected one @blog_entries matcher, found {found}"
    return found[0]


def test_a_blog_entry_is_served_under_the_shared_policy(cfg):
    from shared import blog_inbox

    public = _block(cfg, repo_paths.SITE_HOST)
    assert "@blog_entries path_regexp" in public
    assert (f'header @blog_entries Content-Security-Policy "{blog_inbox.ENTRY_CSP}"'
            in public)
    # Declared before it is used, and used exactly once.
    assert public.index("@blog_entries path_regexp") < public.index("header @blog_entries")
    assert public.count("header @blog_entries") == 1


def test_the_blog_policy_names_only_entry_documents(cfg):
    """The header carries ``sandbox`` and ``default-src 'none'``. Sent on the
    entry's PAGE it would switch off the site's own stylesheet and menu; sent on
    a typeface it is noise. So the expression names the framed document and
    nothing beside it.

    Compiled with Python's ``re`` and applied with ``fullmatch``. Caddy uses
    Go's RE2, where an unflagged ``$`` is the end of the text; Python's ``$``
    also matches before a final newline, and ``fullmatch`` removes exactly that
    difference. The expression uses nothing the two engines read differently:
    a literal path, one character class, one escaped dot."""
    pattern = _blog_path_pattern(cfg)
    assert pattern == r"^/blog/[a-z0-9-]+/entry\.html$"
    assert pattern == caddy.BLOG_ENTRY_PATH
    assert pattern.startswith("^") and pattern.endswith("$"), (
        "unanchored, a path_regexp matches anywhere in the path")
    rx = re.compile(pattern)

    for path in ("/blog/a-b/entry.html", "/blog/a/entry.html",
                 "/blog/nuclear-stocks-thesis/entry.html", "/blog/2026-q3/entry.html"):
        assert rx.fullmatch(path), f"{path} would be served with no policy"

    for path in ("/blog/a/index.html",          # the entry's page: the site's own markup
                 "/blog/a/",                    # the same page, as it is linked
                 "/blog/fonts/x.woff2",         # a typeface
                 "/blog/sitemap.txt",
                 "/blog/A/entry.html",          # a slug is lower-case; so is the folder
                 "/blog/a/b/entry.html",        # one folder deep, never two
                 "/blog/entry.html",            # no slug at all
                 "/blog//entry.html",
                 "/blog/a/entry.html/",
                 "/blog/a/entry.html.bak",
                 "/blog/a/entryxhtml",          # the dot is a dot, not "any character"
                 "/x/blog/a/entry.html",
                 "/blog.html", "/blog.json", "/reports/latest.html", "/"):
        assert not rx.fullmatch(path), f"{path} must not carry the entry policy"


def test_every_address_the_service_can_write_is_covered(cfg):
    """The other direction. The expression's slug class is deliberately looser
    than ``SLUG_RE`` (it admits a doubled hyphen, which only ever names a 404),
    but it must never be TIGHTER: an entry whose address it missed would be
    served outside its frame with no policy at all."""
    from shared import blog_inbox

    rx = re.compile(_blog_path_pattern(cfg))
    slugs = ("a", "z9", "a-b", "nuclear-stocks-thesis", "2026-q3-review", "0", "a-1-b-2")
    for slug in slugs:
        assert blog_inbox.SLUG_RE.match(slug), f"{slug!r} is not a slug; fix this test"
        assert rx.fullmatch(f"/blog/{slug}/entry.html"), slug
    # The slug alphabet, read from the pattern rather than restated: every
    # character SLUG_RE can accept is one the edge's class accepts.
    for ch in "abcdefghijklmnopqrstuvwxyz0123456789":
        assert blog_inbox.SLUG_RE.match(ch) and rx.fullmatch(f"/blog/{ch}/entry.html"), ch


def test_the_blog_policy_allows_no_script_and_no_form(cfg):
    """Read from the header the edge would SEND, directive by directive, so a
    widened policy fails here even though the string still equals the shared
    constant (which only proves the two were widened together)."""
    public = _block(cfg, repo_paths.SITE_HOST)
    m = re.search(r'header @blog_entries Content-Security-Policy "([^"]*)"', public)
    assert m, "no policy on blog entries"
    policy = {}
    for part in m.group(1).split(";"):
        words = part.split()
        assert words, f"an empty directive in {m.group(1)!r}"
        assert words[0] not in policy, f"{words[0]} is declared twice; the second is ignored"
        policy[words[0]] = words[1:]

    assert policy["default-src"] == ["'none'"]
    # No script-src at all, so default-src 'none' governs script. Any script-src
    # is a widening: it could only ALLOW something.
    for name in ("script-src", "script-src-elem", "script-src-attr", "worker-src",
                 "connect-src", "frame-src", "child-src", "object-src"):
        assert name not in policy, f"{name} widens default-src 'none'"
    assert policy["form-action"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["frame-ancestors"] == ["'self'"]

    sandbox = policy["sandbox"]
    for flag in ("allow-scripts", "allow-forms", "allow-top-navigation",
                 "allow-top-navigation-by-user-activation", "allow-modals",
                 "allow-downloads", "allow-pointer-lock"):
        assert flag not in sandbox, f"the entry sandbox grants {flag}"
    for words in policy.values():
        for word in words:
            assert word not in ("*", "'unsafe-eval'", "https:", "http:"), word
    # 'unsafe-inline' is for the document's own <style>, and only there.
    assert [k for k, v in policy.items() if "'unsafe-inline'" in v] == ["style-src"]


def test_the_blog_policy_is_sent_by_the_public_site_only(cfg):
    """The app and the live screens are proxies with policies of their own (the
    app's forbids framing; the live block deliberately sends none). A
    ``sandbox`` directive on either would stop every script NiceGUI runs."""
    from shared import blog_inbox

    assert cfg.count(blog_inbox.ENTRY_CSP) == 1
    assert cfg.count("@blog_entries path_regexp") == 1
    assert blog_inbox.ENTRY_CSP in _block(cfg, repo_paths.SITE_HOST)
    for host in (repo_paths.APP_HOST, repo_paths.LIVE_HOST):
        block = _block(cfg, host)
        assert "@blog_entries" not in block, host
        assert "sandbox" not in block, host
        assert blog_inbox.ENTRY_CSP not in block, host
    assert "Content-Security-Policy" not in _block(cfg, repo_paths.LIVE_HOST)


def test_the_blog_policy_survives_caddys_quoting(cfg):
    """The policy is full of single quotes and sits inside a double-quoted
    Caddyfile token. That holds as long as the value has no double quote (which
    would end the token early and turn the rest into arguments), no backslash
    (the one escape character inside quotes), no backtick (the other quote
    character), no brace (a placeholder, which Caddy would try to expand) and no
    line break. The expression is an UNQUOTED token, so it also may not contain
    whitespace.

    ⚠ Like everything in this file, a statement about the STRING. ``caddy
    validate`` on the box is still the only proof that Caddy parses it."""
    from shared import blog_inbox

    for ch in ('"', "\\", "`", "{", "}", "\n", "\r", "\t"):
        assert ch not in blog_inbox.ENTRY_CSP, f"ENTRY_CSP contains {ch!r}"
    assert blog_inbox.ENTRY_CSP.isascii() and blog_inbox.ENTRY_CSP.isprintable()

    pattern = _blog_path_pattern(cfg)
    for ch in ('"', "`", "{", "}", " ", "\t"):
        assert ch not in pattern, f"the entry expression contains {ch!r}"
    assert pattern.isascii() and pattern.isprintable()
    # The line is exactly: header, matcher, field, ONE quoted value.
    public = _block(cfg, repo_paths.SITE_HOST)
    line = next(ln for ln in public.splitlines() if "header @blog_entries" in ln)
    assert line.count('"') == 2 and line.rstrip().endswith('"'), line


# ── neuralstrike.co/blog ─────────────────────────────────────────────────────
#
# The Blog's list is ``blog.html``. ``/blog/`` is a folder of entries with no
# index of its own, so the address people actually type was a 404.

def _redirects(block):
    """Every ``redir`` line in ``block`` as ``(matcher, to, code)``, and the
    count of lines that START with ``redir`` at all - so a redirect written in
    any other shape is seen rather than skipped."""
    shaped = re.findall(r"^[ \t]*redir[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]*$", block, re.M)
    return shaped, len(re.findall(r"^[ \t]*redir\b", block, re.M))


def _caddy_path_matches(matcher, path):
    """Caddy's ``path`` matcher for a pattern with NO ``*``: the whole path,
    compared without case. (A ``*`` makes it a prefix, suffix or glob match,
    which is why the test below refuses one outright.)"""
    assert "*" not in matcher
    return matcher.lower() == path.lower()


def test_the_bare_blog_address_goes_to_the_blog_page(cfg):
    public = _block(cfg, repo_paths.SITE_HOST)
    shaped, lines = _redirects(public)
    assert sorted(shaped) == [("/blog", "/blog.html", "308"), ("/blog/", "/blog.html", "308")]
    assert lines == 2, "a redirect in a shape this test does not read"
    assert tuple(m for m, _to, _code in shaped) == caddy.BLOG_BARE_PATHS
    assert {to for _m, to, _code in shaped} == {caddy.BLOG_LIST_PAGE}
    # The page it sends people to is a tracked file of the site.
    assert (pathlib.Path(repo_paths.SITE_ROOT) / caddy.BLOG_LIST_PAGE.lstrip("/")).is_file()


def test_the_blog_redirects_cannot_reach_an_entry_a_typeface_or_the_page_itself(cfg):
    """An exact path, never a pattern. ``redir /blog/* ...`` would send every
    entry, its document and every typeface to the list; and a matcher that took
    ``/blog.html`` would redirect the page to itself for ever."""
    shaped, _lines = _redirects(_block(cfg, repo_paths.SITE_HOST))
    assert shaped
    for matcher, to, _code in shaped:
        assert matcher.startswith("/") and "*" not in matcher and not matcher.startswith("@")
        for ch in ("{", "}", '"', "`", " ", "\t"):
            assert ch not in matcher and ch not in to
        for path in ("/blog/a/", "/blog/a", "/blog/a/index.html", "/blog/a/entry.html",
                     "/blog/nuclear-stocks-thesis/", "/blog/fonts/", "/blog/fonts/x.woff2",
                     "/blog/sitemap.txt", "/blog.html", "/blog.json", "/blogs", "/blog//",
                     "/x/blog", "/"):
            assert not _caddy_path_matches(matcher, path), f"{matcher} would redirect {path}"
        assert not _caddy_path_matches(matcher, to), "a redirect onto itself"
    assert {m for m, _to, _code in shaped} == {"/blog", "/blog/"}


def test_the_blog_redirects_are_the_public_sites_only(cfg):
    assert len(re.findall(r"^[ \t]*redir\b", cfg, re.M)) == 2
    for host in (repo_paths.APP_HOST, repo_paths.LIVE_HOST):
        assert not re.search(r"^[ \t]*redir\b", _block(cfg, host), re.M), host
    assert cfg.isascii()


def test_every_matcher_any_header_names_is_declared(cfg):
    """``test_every_matcher_the_cache_rules_name_is_defined`` reads the
    Cache-Control rules and the ``path`` declarations, which was every rule
    there was. The entry policy is a different header behind a different kind of
    matcher, so this is the same check over EVERY ``header @name`` in the file
    and every way a named matcher is declared. The failure is the same one: an
    undeclared matcher is a parse error, and one bad file stops Caddy loading."""
    used = set(re.findall(r"^[ \t]*header\s+(@\w+)\s", cfg, re.M))
    declared = set(re.findall(r"^[ \t]*(@\w+)\s+(?:path|path_regexp)\s", cfg, re.M))
    declared |= set(re.findall(r"^[ \t]*(@\w+)\s*\{", cfg, re.M))
    assert "@blog_entries" in used and "@revalidate" in used, "nothing parsed"
    assert used <= declared, f"undeclared matchers: {sorted(used - declared)}"


def test_the_blog_typefaces_take_the_rule_the_site_faces_already_have(cfg):
    """A typeface under ``/blog/fonts/`` is named by a hash of its content, so
    it is byte-stable under its name exactly as the two site faces are, and the
    existing ``*.woff2`` rule already matches it at any depth. Pinned so nobody
    adds a second, competing rule for the folder."""
    public = _block(cfg, repo_paths.SITE_HOST)
    assert re.search(r"^[ \t]*@fonts path \*\.woff2$", public, re.M)
    declared = re.findall(r"^[ \t]*(@\w+)\s+(?:path|path_regexp)\s+(.*)$", public, re.M)
    woff2_rules = [name for name, what in declared if "woff2" in what]
    assert woff2_rules == ["@fonts"], woff2_rules
    assert not [name for name, what in declared if "/blog/fonts" in what]
    # And neither Blog matcher reaches into the folder.
    font = "/blog/fonts/0123456789abcdef0123.woff2"
    assert not re.compile(_blog_path_pattern(cfg)).fullmatch(font)
    assert not _go_glob(caddy.BLOG_PAGE_PATH).fullmatch(font)


def _go_glob(pattern):
    """A Caddy ``path`` pattern with a ``*`` in the MIDDLE, as a Python regex.

    Caddy special-cases a leading or trailing ``*`` (suffix and prefix matches)
    and hands everything else to Go's ``path.Match``, where ``*`` is any run of
    characters that are not a slash. This translates that one rule and refuses
    a pattern it would get wrong, rather than quietly testing something else."""
    assert not pattern.startswith("*") and not pattern.endswith("*"), (
        f"{pattern!r} is a prefix or suffix match in Caddy, not a glob")
    for ch in "?[]\\":
        assert ch not in pattern, f"{pattern!r} uses {ch!r}, which this does not translate"
    return re.compile("[^/]*".join(re.escape(part) for part in pattern.split("*")))


def test_a_blog_entrys_page_revalidates_at_the_address_it_is_linked_by(cfg):
    """THE ENTRY PAGE IS ASKED FOR AS A FOLDER, and ``*.html`` cannot see one.

    The list, the entry's own canonical address and its sitemap all say
    ``/blog/<slug>/``. The matcher is applied to the address as REQUESTED, not
    to the ``index.html`` the file server answers with -- which is the reason
    ``/`` has always been listed beside ``*.html`` for the home page. Left out,
    an entry page falls to heuristic caching: the failure this whole section
    exists for, where a returning visitor holds a page from before a promote.

    The page carries the site's menu, so it takes the pages' policy."""
    public = _block(cfg, repo_paths.SITE_HOST)
    m = re.search(r"^[ \t]*@revalidate\s+path\s+([^\n]+)$", public, re.M)
    assert m, "no revalidation rule"
    patterns = m.group(1).split()
    assert caddy.BLOG_PAGE_PATH in patterns
    assert "/" in patterns, "the home page's folder address fell out of the rule"

    glob = _go_glob(caddy.BLOG_PAGE_PATH)
    for path in ("/blog/a-b/", "/blog/nuclear-stocks-thesis/", "/blog/a/"):
        assert glob.fullmatch(path), f"{path} would be cached heuristically"
    for path in ("/blog/", "/blog/a", "/blog/a/b/", "/blog/fonts/x.woff2",
                 "/ideas/2026-09-29/", "/blog/a/entry.html", "/"):
        assert not glob.fullmatch(path), path
    # entry.html is not the glob's business: the extension rule has it.
    assert "*.html" in patterns


# --- the public host's rate limit (config/edge.toml) -------------------------

def _blocks(text):
    """``{hostname: block text}`` for each top-level site block."""
    out, host, depth, buf = {}, None, 0, []
    for line in text.splitlines():
        if depth == 0 and line.rstrip().endswith("{") and not line.startswith(("#", " ")):
            # "neuralstrike.co, www.neuralstrike.co {" - key on the first name.
            host, buf = line.split("{")[0].split(",")[0].strip(), []
        if host:
            buf.append(line)
        depth += line.count("{") - line.count("}")
        if host and depth == 0:
            out[host] = "\n".join(buf)
            host = None
    return out


def _with_edge(monkeypatch, **cfg):
    monkeypatch.setattr(caddy, "load_edge", lambda: {"live_rate_limit": cfg})


def test_the_limit_ships_off_and_the_file_says_so():
    import tomllib
    shipped = tomllib.loads((pathlib.Path(repo_paths.EDGE_TOML)).read_text("utf-8"))
    assert shipped["live_rate_limit"]["enabled"] is False
    assert shipped["live_rate_limit"] == {**caddy.EDGE_DEFAULTS["live_rate_limit"]}


def test_off_emits_no_rate_limit_anywhere(monkeypatch):
    _with_edge(monkeypatch, enabled=False, events=30, window_sec=60, ipv6_prefix=64)
    assert "rate_limit" not in caddy.render()


def test_on_limits_the_public_host_only(monkeypatch):
    _with_edge(monkeypatch, enabled=True, events=30, window_sec=60, ipv6_prefix=64)
    blocks = _blocks(caddy.render())
    live = blocks[repo_paths.LIVE_HOST]
    assert "rate_limit {" in live
    assert "events 30" in live and "window 60s" in live and "ipv6_prefix 64" in live
    assert "key {remote_host}" in live
    for host in (repo_paths.APP_HOST, repo_paths.SITE_HOST):
        assert "rate_limit" not in blocks[host], host


def test_nicegui_assets_and_the_socket_never_count(monkeypatch):
    """A page load fetches dozens of these; counting them would throttle one
    ordinary visit. Only the page itself allocates a session."""
    _with_edge(monkeypatch, enabled=True, events=30, window_sec=60, ipv6_prefix=64)
    live = _blocks(caddy.render())[repo_paths.LIVE_HOST]
    assert "not path /_nicegui/* /_nicegui_ws/* /static/* /favicon.ico" in live


def test_the_limit_sits_before_the_proxy(monkeypatch):
    _with_edge(monkeypatch, enabled=True, events=30, window_sec=60, ipv6_prefix=64)
    live = _blocks(caddy.render())[repo_paths.LIVE_HOST]
    assert live.index("rate_limit") < live.index("reverse_proxy")


@pytest.mark.parametrize("bad", [
    {"events": 0}, {"events": -5}, {"events": "30"}, {"events": True},
    {"window_sec": 0}, {"ipv6_prefix": 200}, {"ipv6_prefix": 1.5},
])
def test_a_bad_value_turns_the_limit_off_rather_than_breaking_the_reload(
        monkeypatch, bad):
    cfg = {"enabled": True, "events": 30, "window_sec": 60, "ipv6_prefix": 64, **bad}
    _with_edge(monkeypatch, **cfg)
    assert caddy.live_rate_limit() is None
    assert "rate_limit" not in caddy.render()


def test_enabled_must_be_a_real_true(monkeypatch):
    _with_edge(monkeypatch, enabled="yes", events=30, window_sec=60, ipv6_prefix=64)
    assert caddy.live_rate_limit() is None


# --- request body limits (audit SE-03) ----------------------------------------
# POST /login is open to the internet without a session. The edge refuses a body
# over a few kilobytes before the app sees it; the app refuses again on its own.

def test_the_app_block_limits_the_login_body(cfg):
    block = cfg.split(caddy.APP_HOST + " {", 1)[1]
    assert "@login path /login" in block
    assert "request_body @login {" in block
    assert f"max_size {caddy.EDGE_DEFAULTS['limits']['login_body_kb']}KB" in block


def test_the_login_limit_does_not_bound_the_rest_of_the_app(cfg):
    """The /x page uploads an image of up to 5 MB through the app. A site-wide
    limit at the login's size would break it."""
    block = cfg.split(caddy.APP_HOST + " {", 1)[1]
    assert block.count("request_body") == 1


def test_the_login_limit_is_read_from_the_edge_config(monkeypatch):
    monkeypatch.setattr(caddy, "load_edge", lambda: {"limits": {"login_body_kb": 8}})
    assert "max_size 8KB" in caddy.render()


@pytest.mark.parametrize("bad", [0, -1, True, "16", 1.5, None, 100000])
def test_an_unusable_login_limit_reads_as_the_shipped_one(monkeypatch, bad):
    monkeypatch.setattr(caddy, "load_edge", lambda: {"limits": {"login_body_kb": bad}})
    assert caddy.login_body_kb() == caddy.EDGE_DEFAULTS["limits"]["login_body_kb"]
