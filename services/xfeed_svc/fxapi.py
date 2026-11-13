"""Client for the FxEmbed (FxTwitter) v2 API - the only network call in xfeed_svc.

``fetch_statuses`` never raises; it returns exactly one of
``{"status": "ok", "results", "cursor_bottom"}``, ``{"status": "not_found"}`` or
``{"status": "error", "error"}``. FxEmbed intermittently 404s for real accounts,
so a not-found is retried ``not_found_retries`` times before it is believed.

``requests``' ``timeout`` bounds each READ, not the request, so a stuck socket
could hold the poller for ever. Each attempt therefore runs under the same total
deadline watchdog as ``news_svc.fetch`` (``3 x timeout_s``, ``shared.http_watchdog``):
a timer that shuts the attempt's sockets down, waking whatever read is blocked.
A fresh session per attempt (when none is injected) keeps every socket one the
watchdog saw opened. An INJECTED session is used unwatched (tests pass a fake), so
the 3x guard applies only to the default session.
"""
import json
import threading
import time
from urllib.parse import quote

import requests

from services import _degrade
from services.xfeed_svc import _trace
from shared import http_watchdog as _wd


def _err(msg):
    return {"status": "error", "error": msg}


class FxClient:
    def __init__(self, cfg: dict, *, session=None, sleep=time.sleep):
        self._base = str(cfg["base_url"]).rstrip("/")
        self._ua = cfg["user_agent"]
        self._timeout = cfg["timeout_s"]
        self._retries = int(cfg["not_found_retries"])
        self._delay = cfg["retry_delay_s"]
        self._session = session
        self._sleep = sleep

    def _get(self, url, headers):
        if self._session is not None:
            return self._session.get(url, headers=headers, timeout=self._timeout)
        return _wd.get(url, headers=headers, timeout=self._timeout)

    def _once(self, url):
        headers = {"User-Agent": self._ua, "Accept": "application/json"}
        watch = _wd.Watch()
        timer = threading.Timer(float(self._timeout) * _wd.DEADLINE_FACTOR, watch.fire)
        timer.name = "xfeed-fx-deadline"
        timer.daemon = True
        _wd.ACTIVE.watch = watch
        timer.start()
        r = None
        try:
            try:
                r = self._get(url, headers)
            finally:
                _wd.ACTIVE.watch = None
            if watch.expired:                      # a cut body must not pass as a short one
                return _err("timeout")
            return self._classify(r)
        except requests.Timeout:
            return _err("timeout")
        except Exception as exc:                   # never raises
            if watch.expired:
                return _err("timeout")
            return _err(f"network: {exc}")
        finally:
            timer.cancel()
            if r is not None and hasattr(r, "close"):
                try:
                    r.close()
                except Exception:
                    pass
            watch.close()

    @staticmethod
    def _classify(r):
        code = r.status_code
        if code == 204:
            return {"status": "ok", "results": [], "cursor_bottom": None}
        if code == 404:
            return {"status": "not_found"}
        if code == 429:
            return _err("rate limited")
        if not 200 <= code < 300:
            return _err(f"HTTP {code}")
        try:
            body = json.loads(r.text)
        except ValueError:
            return _err("bad response from FxEmbed")
        if not isinstance(body, dict):
            return _err("bad response from FxEmbed")
        api_code = body.get("code")
        if api_code == 404:
            return {"status": "not_found"}
        if api_code and api_code != 200:
            return _err(f"API {api_code}")
        cursor = body.get("cursor")
        bottom = cursor.get("bottom") if isinstance(cursor, dict) else None
        return {"status": "ok", "results": body.get("results") or [],
                "cursor_bottom": bottom or None}

    def fetch_statuses(self, handle: str, *, cursor: str | None = None,
                       with_replies: bool = True) -> dict:
        try:
            params = []
            if with_replies:
                params.append("with_replies=1")
            if cursor:
                params.append("cursor=" + quote(cursor, safe=""))
            url = f"{self._base}/2/profile/{quote(handle, safe='')}/statuses"
            if params:
                url += "?" + "&".join(params)
            result = self._once(url)
            # FxEmbed intermittently 404s for real accounts; retry before believing it.
            for _ in range(self._retries):
                if result["status"] != "not_found":
                    break
                self._sleep(self._delay)
                result = self._once(url)
            return result
        except Exception as exc:                   # belt and braces: never raises
            _degrade.degraded("xfeed.fxapi", detail=_trace.where("fetching statuses", exc), exc_info=False)
            return _err(f"network: {exc}")
