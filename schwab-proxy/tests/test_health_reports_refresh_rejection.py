"""The health probe must be able to see a REVOKED refresh token.

Observed live 2026-08-22: prod's proxy answered ``/health`` with
``status: ok`` and ``refresh_token_expired: false`` for over an hour while
every single market-data call returned 500 and the error log filled with

    Token refresh failed (400): {"error_description": "Refresh token is
    invalid, expired or revoked", "error": "invalid_grant"}

The whole live stack was blind to its own outage, because
``_is_refresh_expired()`` reads ``RefreshTokenExpiresAt`` out of the stored
token FILE — a locally stamped timestamp — rather than anything Schwab said.
A token revoked before its stamped expiry is invisible to it, and revocation is
exactly what happened.

Schwab's rejection is authoritative in a way the stamp never is, so a failed
refresh is recorded and surfaced. The stamped-expiry check stays: it still
catches the ordinary case where the token simply aged out.
"""
import importlib.util
import pathlib
import sys
import types

import pytest


def _load_proxy():
    """Import schwab_proxy without running its server, and without requiring
    the repo's launch environment."""
    root = pathlib.Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    path = root / "schwab-proxy" / "schwab_proxy.py"
    spec = importlib.util.spec_from_file_location("schwab_proxy_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


proxy = _load_proxy()


def _manager(refresh_expires_at="2099-01-01T00:00:00Z"):
    """A token manager whose STAMPED refresh expiry is far in the future —
    the state prod was in while Schwab was rejecting the token."""
    tm = proxy.TokenManager.__new__(proxy.TokenManager)
    tm.app_key = "k"
    tm.app_secret = "s"
    tm.tokens = {"AccessToken": "a", "RefreshToken": "r",
                 "RefreshTokenExpiresAt": refresh_expires_at}
    tm.refresh_rejected = False
    tm.refresh_error = None
    return tm


class TestRefreshRejectionIsRecorded:
    def test_a_fresh_manager_reports_no_rejection(self):
        tm = _manager()
        assert tm.refresh_rejected is False
        assert tm.refresh_error is None

    def test_a_400_invalid_grant_marks_the_token_rejected(self, monkeypatch):
        """This is the state that was invisible. The stamped expiry still says
        2099; only Schwab's answer reveals the truth."""
        tm = _manager()

        class Resp:
            status_code = 400
            text = ('{"error":"unsupported_token_type","error_description":'
                    '"Refresh token is invalid, expired or revoked"}')

        monkeypatch.setattr(proxy.requests, "post", lambda *a, **k: Resp())
        with pytest.raises(RuntimeError):
            tm._refresh()

        assert tm.refresh_rejected is True
        assert "invalid" in (tm.refresh_error or "").lower()
        # The stamped expiry is untouched and still claims the token is fine —
        # which is precisely why it cannot be the only signal.
        assert tm._is_refresh_expired() is False

    def test_a_successful_refresh_clears_a_previous_rejection(self, monkeypatch):
        """Re-authorization must make the alarm stop; a latched error that
        never clears is its own kind of lie."""
        tm = _manager()
        tm.refresh_rejected = True
        tm.refresh_error = "old failure"

        class Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"access_token": "new", "refresh_token": "r2",
                        "expires_in": 1800}

        monkeypatch.setattr(proxy.requests, "post", lambda *a, **k: Resp())
        monkeypatch.setattr(tm, "_save_tokens", lambda *a, **k: None)
        tm._refresh()

        assert tm.refresh_rejected is False
        assert tm.refresh_error is None


class TestHealthSurfacesIt:
    def test_health_reports_the_rejection_and_stops_saying_ok(self, monkeypatch):
        """`status: ok` beside a dead credential is the failure this fixes:
        the Status page renders that green while nothing can fetch data."""
        tm = _manager()
        tm.refresh_rejected = True
        tm.refresh_error = "Refresh token is invalid, expired or revoked"
        monkeypatch.setattr(proxy, "token_mgr", tm)

        h = proxy.health()
        assert h["refresh_token_rejected"] is True
        assert h["status"] != "ok"
        assert "revoked" in (h.get("refresh_error") or "").lower()

    def test_a_healthy_proxy_still_reports_ok(self, monkeypatch):
        tm = _manager()
        monkeypatch.setattr(proxy, "token_mgr", tm)
        h = proxy.health()
        assert h["status"] == "ok"
        assert h["refresh_token_rejected"] is False

    def test_the_stamped_expiry_check_is_kept_not_replaced(self, monkeypatch):
        """It still catches the ordinary case — a token that simply aged out
        without anyone calling Schwab."""
        tm = _manager(refresh_expires_at="2020-01-01T00:00:00Z")
        monkeypatch.setattr(proxy, "token_mgr", tm)
        h = proxy.health()
        assert h["refresh_token_expired"] is True
