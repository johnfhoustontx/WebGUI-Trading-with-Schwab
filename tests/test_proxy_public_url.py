"""`proxy_public_url` — where a BROWSER reaches the Schwab proxy.

WHY IT EXISTS. ``PROXY_URL`` is where THIS PROCESS reaches the proxy, and on the
VPS that is ``http://127.0.0.1:8100``. The System Status page's Authorize button
used to open ``{PROXY_URL}/auth`` in a new tab — so a browser on a phone or a
workstation resolved 127.0.0.1 to ITSELF and found nothing listening. It worked
only while browser and stack shared a machine (Windows), or through the SSH
tunnel fallback. Behind https://app.neuralstrike.co it could never work.

⚠ SCOPE. It moves ONE link a human clicks. The server keeps talking to the proxy
over ``PROXY_URL``; routing server traffic through a tailnet name would add a
hop and a failure mode for no gain.
"""
import repo_paths
from repo_paths import _public_proxy_url

LOOP = "http://127.0.0.1:8100"


def test_absent_falls_back_to_the_server_url():
    """No marker key keeps the old behaviour byte-identical — which is also what
    the SSH-tunnel fallback (tools/open_webgui.ps1 forwards :8100) needs."""
    assert _public_proxy_url({}, LOOP) == LOOP
    assert _public_proxy_url({"proxy_public_url": ""}, LOOP) == LOOP


def test_a_tailnet_url_is_used_verbatim():
    url = "https://trading-prod-2.tail3050ed.ts.net:8100"
    assert _public_proxy_url({"proxy_public_url": url}, LOOP) == url


def test_a_trailing_slash_is_trimmed_so_the_auth_path_does_not_double():
    url = "https://box.ts.net:8100/"
    assert _public_proxy_url({"proxy_public_url": url}, LOOP) == "https://box.ts.net:8100"


def test_a_value_that_is_not_a_web_url_is_refused():
    """A bare host, a typo'd scheme or a non-string would render a button that
    opens nothing — the bug this knob exists to fix. Fall back instead."""
    for bad in ("box.ts.net:8100", "htps://box", "javascript:alert(1)", 8100, ["x"]):
        assert _public_proxy_url({"proxy_public_url": bad}, LOOP) == LOOP, bad


def test_pytest_presents_the_server_url():
    """Hermetic: a prod checkout whose marker sets the key must not change what
    the suite sees."""
    assert repo_paths.PROXY_PUBLIC_URL == repo_paths.PROXY_URL
