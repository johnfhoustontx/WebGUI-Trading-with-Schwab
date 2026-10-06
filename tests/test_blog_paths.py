"""blog_svc has what a service needs before any of its code exists: a port (and
so a unit), its paths, and ignore rules for everything it will generate."""
import subprocess

import repo_paths


def test_the_blog_service_has_a_port_and_so_a_unit():
    """``generate_units.components()`` builds a unit from every SERVICE_PORTS
    key, so the port IS what puts the service in the stack."""
    assert repo_paths.SERVICE_PORTS["blog"] == 8217
    assert repo_paths.SERVICE_URLS["blog"] == "http://127.0.0.1:8217"


def test_the_blog_paths_live_under_the_service_and_the_site():
    assert repo_paths.BLOG_DATA == repo_paths.REPO_ROOT / "services" / "blog_svc" / "data"
    assert repo_paths.BLOG_DB == repo_paths.BLOG_DATA / "blog.db"
    assert repo_paths.BLOG_TOML == repo_paths.REPO_ROOT / "config" / "blog.toml"


def test_the_blog_store_is_guarded_from_the_suite():
    """The repo-root guard refuses ``sqlite3.connect`` on a listed folder's
    databases. Unlisted, a test that made a default ``Store()`` would open - or
    create - the live blog.db."""
    import conftest
    assert repo_paths.BLOG_DATA in conftest._LIVE_DIRS
    assert conftest.is_protected(repo_paths.BLOG_DB)
    assert conftest.is_protected(f"file:{repo_paths.BLOG_DB}?mode=ro")


def test_the_blog_store_is_backed_up():
    """The data tree is swept WHOLE: the entries' documents and the typefaces
    are plain files, which the ``*.db`` pass alone would leave behind. The site
    folder is rebuilt from the store, so the store is the only copy."""
    from tools import backup_local
    assert "services/blog_svc/data" in backup_local.DATA_TREES


def test_the_config_file_ships():
    """The loader degrades to its defaults without the file, so a missing file
    would go unnoticed: the Settings catalogue and the operator both read it."""
    assert repo_paths.BLOG_TOML.is_file()


def test_generated_blog_state_is_never_committed():
    """Everything blog_svc writes on the box that serves the site. Tracked, the
    first published entry would dirty prod's tree and tools/promote.sh refuses a
    dirty tree. ``git check-ignore`` exits 0 for an ignored path."""
    for probe in ("deploy/site/blog/x/index.html", "deploy/site/blog/fonts/a.woff2",
                  "deploy/site/blog.json", "services/blog_svc/data/blog.db",
                  "services/blog_svc/data/staging/x/entry.html"):
        res = subprocess.run(["git", "check-ignore", "-q", probe],
                             cwd=repo_paths.REPO_ROOT, capture_output=True)
        assert res.returncode == 0, f"{probe} is not gitignored"


def test_the_tracked_blog_page_is_not_swept_up_by_the_ignore():
    """``deploy/site/blog/`` and ``blog.json`` are generated; ``blog.html`` and
    ``assets/blog.js`` are tracked site pages. An ignore pattern one character
    too wide would keep them out of every commit, silently."""
    for probe in ("deploy/site/blog.html", "deploy/site/assets/blog.js"):
        res = subprocess.run(["git", "check-ignore", "-q", probe],
                             cwd=repo_paths.REPO_ROOT, capture_output=True)
        assert res.returncode == 1, f"{probe} would be gitignored"
