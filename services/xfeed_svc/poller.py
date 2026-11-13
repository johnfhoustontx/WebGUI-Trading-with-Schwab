"""The X poller, ported line for line from the Node feed's ``src/poller.js``.

Baseline on add (page 1 becomes hidden ``baseline`` rows), then posts going
forward only: catch-up after downtime follows the cursor until it reaches known
territory (up to ``max_pages``; an unfinished catch-up leaves a visible gap),
reposts below the known frontier are never back-filled, and a post counts as
live only on page 1 of a recent poll and within ``live_slack_min`` of the last
good check.

Node constants come from the ``[poller]`` config section: ``3 * intervalMs`` is
``recent_factor * interval``, ``OLD_POST_SLACK_MS`` is ``live_slack_min``,
``NOT_FOUND_LIMIT`` is ``not_found_limit``, ``maxPages`` is ``max_pages``,
``PAGE_GAP_MS`` / ``ACCOUNT_GAP_MS`` are ``page_gap_s`` / ``account_gap_s`` and
the backoff steps are ``backoff_s``.

``Poller`` replaces ``createPoller`` minus its timers (the scheduler loop owns
those). ``run_cycle`` runs in a worker thread while ``add_account`` may run in a
command thread, so each handle has its own lock: a cycle holds an account's lock
only while polling that account, and an add never waits behind other accounts.
"""
import logging
import threading
import time

from services.xfeed_svc import config
from services.xfeed_svc.mapping import map_status

log = logging.getLogger("xfeed_svc.poller")

_MIN_INTERVAL_S = 30


def _now_ms() -> int:
    return int(time.time() * 1000)


def _interval_s(cfg) -> int:
    return max(_MIN_INTERVAL_S, int(cfg["interval_s"]))


def backoff_ms(fail_count: int, steps_s=(60, 120, 300)) -> int:
    steps = list(steps_s)
    i = min(max(int(fail_count), 1), len(steps)) - 1
    return int(steps[i] * 1000)


def assign_sort_times(items, *, live, first_seen_at, last_ok_at):
    """Sort times for new items, given in timeline order (newest first).

    Live items sort at the moment they were first seen; caught-up items at their
    real time. Caught-up reposts borrow the time of the nearest newer original
    post, clamped to [last_ok_at, first_seen_at].
    """
    if live:
        return [first_seen_at for _ in items]
    lo = last_ok_at if last_ok_at is not None else 0
    out = []
    for i, p in enumerate(items):
        if p["type"] != "repost":
            out.append(p["created_at"])
            continue
        t = None
        j = i - 1
        while j >= 0 and t is None:
            if items[j]["type"] != "repost":
                t = items[j]["created_at"]
            j -= 1
        j = i + 1
        while j < len(items) and t is None:
            if items[j]["type"] != "repost":
                t = items[j]["created_at"]
            j += 1
        if t is None:
            t = lo
        out.append(min(max(t, lo), first_seen_at))
    return out


def _fetch_page(fx, handle, cursor=None, with_replies=True):
    """Fetch one page. Page 1 tries the with-replies timeline and falls back to
    the plain timeline; later pages must stay on whichever timeline served page 1
    (cursors are not interchangeable)."""
    if cursor:
        return fx.fetch_statuses(handle, cursor=cursor, with_replies=with_replies), with_replies
    r = fx.fetch_statuses(handle, with_replies=True)
    if r["status"] == "ok":
        return r, True
    plain = fx.fetch_statuses(handle, with_replies=False)
    return (plain, False) if plain["status"] == "ok" else (r, True)


def _map_all(results, handle):
    return [p for p in (map_status(s, handle) for s in results) if p]


def _profile_from(results, handle):
    for s in results:
        author = s.get("author") or {}
        if (author.get("screen_name") or "").lower() == handle:
            return {"display_name": author.get("name"), "avatar_url": author.get("avatar_url") or None}
    for s in results:
        rt = s.get("reposted_by") or {}
        if (rt.get("screen_name") or "").lower() == handle:
            return {"display_name": rt.get("name"), "avatar_url": rt.get("avatar_url") or None}
    return {"display_name": handle, "avatar_url": None}


def take_baseline(store, fx, handle, *, now, group_name=None) -> dict:
    """Add (or re-add) an account: everything currently on page 1 becomes the hidden baseline."""
    r, _ = _fetch_page(fx, handle)
    if r["status"] != "ok":
        return {"ok": False, "reason": "not_found" if r["status"] == "not_found" else r.get("error")}
    existing = store.get_account(handle)
    profile = _profile_from(r["results"], handle)
    if existing and profile["display_name"] == handle:
        profile["display_name"] = existing["display_name"]
        profile["avatar_url"] = existing["avatar_url"]
    store.upsert_account(handle, display_name=profile["display_name"], avatar_url=profile["avatar_url"],
                         now=now, group_name=group_name)
    store.insert_posts([{**p, "first_seen_at": now, "sort_at": p["created_at"], "origin": "baseline"}
                        for p in _map_all(r["results"], handle)])
    store.record_check(handle, now=now, ok=True, next_check_at=0)
    return {"ok": True, "account": store.get_account(handle)}


def poll_account(store, fx, account, *, now, cfg, sleep=time.sleep, force_catchup=False) -> dict:
    """Poll one account once. Returns the newly visible feed rows and any gap that was recorded."""
    handle = account["handle"]
    interval_ms = _interval_s(cfg) * 1000
    max_pages = int(cfg["max_pages"])
    old_post_slack_ms = cfg["live_slack_min"] * 60_000
    baseline_at = account["baseline_at"] if account.get("baseline_at") is not None else 0
    last_ok_at = account["last_ok_at"] if account.get("last_ok_at") is not None else baseline_at
    live = (not force_catchup and account.get("last_ok_at") is not None
            and now - account["last_ok_at"] < cfg["recent_factor"] * interval_ms)

    def fail(r):
        not_found = r["status"] == "not_found"
        streak = (account.get("not_found_streak") or 0) + 1 if not_found else 0
        if not_found:
            error = "not found" if streak >= cfg["not_found_limit"] else "not found (retrying)"
        else:
            error = r.get("error")
        store.record_check(handle, now=now, ok=False, error=error, not_found_streak=streak,
                           next_check_at=now + backoff_ms((account.get("fail_count") or 0) + 1, cfg["backoff_s"]))
        return {"new_posts": [], "gap": None, "status": r["status"]}

    # An item is "known" if we've stored it before, or it's an original post from before the last good check.
    def known(it):
        return it["seen"] or (it["post"]["type"] != "repost" and it["post"]["created_at"] < last_ok_at)

    items = []  # {post, page, seen} in timeline order across pages
    ids = set()
    cursor = None
    with_replies = True
    page = 0
    done = False

    while page < max_pages:
        if page > 0:
            sleep(cfg["page_gap_s"])
        r, res_with_replies = _fetch_page(fx, handle, cursor, with_replies)
        if page == 0:
            with_replies = res_with_replies
        if r["status"] != "ok":
            if page == 0:
                return fail(r)
            break  # keep earlier pages; the unfetched remainder becomes a visible gap below
        posts = [p for p in _map_all(r["results"], handle) if p["status_id"] not in ids]
        ids.update(p["status_id"] for p in posts)
        seen = store.seen_ids(handle, [p["status_id"] for p in posts])
        page_items = [{"post": p, "page": page, "seen": p["status_id"] in seen} for p in posts]
        items.extend(page_items)
        page += 1
        # Stop once this page reaches known territory at its bottom. A seen item higher up is not enough:
        # the with-replies timeline puts an old (seen) thread parent right above a new reply.
        bottom_original = next((it for it in reversed(page_items) if it["post"]["type"] != "repost"), None)
        if not page_items or (any(known(it) for it in page_items)
                              and (bottom_original is None or known(bottom_original))):
            done = True
            break
        if not r.get("cursor_bottom"):
            done = True
            break
        cursor = r["cursor_bottom"]

    gap = None
    if not done:
        originals = [it["post"]["created_at"] for it in items if it["post"]["type"] != "repost"]
        gap = store.insert_gap(handle=handle, from_at=last_ok_at,
                               to_at=min(originals) if originals else last_ok_at, now=now)

    # Unseen reposts are new only above the known frontier. Below it they are older timeline items that slid
    # onto the page (a deletion, a bigger page, or the plain-timeline fallback) and must not be back-filled.
    first_known = next((i for i, it in enumerate(items) if known(it)), -1)
    last_fresh_original = next(
        (i for i in range(len(items) - 1, -1, -1)
         if not items[i]["seen"] and items[i]["post"]["type"] != "repost"
         and items[i]["post"]["created_at"] >= last_ok_at), -1)
    frontier = max(float("inf") if first_known == -1 else first_known, last_fresh_original + 1)

    shown = []
    hidden = []
    for i, it in enumerate(items):
        if it["seen"]:
            continue
        p = it["post"]
        # older originals pre-date the account being added
        is_new = i < frontier if p["type"] == "repost" else p["created_at"] >= baseline_at
        (shown if is_new else hidden).append(it)

    def is_live(it):
        return (live and not gap and it["page"] == 0
                and (it["post"]["type"] == "repost" or it["post"]["created_at"] >= last_ok_at - old_post_slack_ms))

    live_items = [it["post"] for it in shown if is_live(it)]
    catch_items = [it["post"] for it in shown if not is_live(it)]
    live_times = assign_sort_times(live_items, live=True, first_seen_at=now, last_ok_at=last_ok_at)
    catch_times = assign_sort_times(catch_items, live=False, first_seen_at=now, last_ok_at=last_ok_at)
    rows = [
        *({**p, "first_seen_at": now, "sort_at": live_times[i], "origin": "live"} for i, p in enumerate(live_items)),
        *({**p, "first_seen_at": now, "sort_at": catch_times[i], "origin": "catchup"}
          for i, p in enumerate(catch_items)),
        *({**it["post"], "first_seen_at": now, "sort_at": it["post"]["created_at"], "origin": "baseline"}
          for it in hidden),
    ]
    store.insert_posts(rows)
    store.record_check(handle, now=now, ok=True, next_check_at=0)
    visible_ids = [row["status_id"] for row in rows if row["origin"] != "baseline"]
    return {"new_posts": store.get_posts(handle, visible_ids), "gap": gap, "status": "ok"}


class Poller:
    """Runs one poll cycle on demand; the scheduler loop decides when."""

    def __init__(self, store, fx, *, load_cfg=config.load, now_ms=_now_ms, sleep=time.sleep):
        self._store = store
        self._fx = fx
        self._load_cfg = load_cfg
        self._now_ms = now_ms
        self._sleep = sleep
        # Handles polled OK (or baselined) since this Poller was created; each
        # handle is only touched under its own account lock.
        self._polled_since_start = set()
        self._locks = {}
        self._locks_guard = threading.Lock()
        self._cycle_guard = threading.Lock()

    def _cfg(self):
        return self._load_cfg()["poller"]

    def interval_s(self) -> int:
        return _interval_s(self._cfg())

    def account_lock(self, handle) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(handle)
            if lock is None:
                lock = self._locks[handle] = threading.Lock()
            return lock

    def add_account(self, handle, group_name) -> dict:
        """Baseline under the handle's lock, then mark it polled so its next poll can be live."""
        with self.account_lock(handle):
            r = take_baseline(self._store, self._fx, handle, now=self._now_ms(), group_name=group_name)
            if r["ok"]:
                self._polled_since_start.add(handle)
            return r

    def run_cycle(self, on_change) -> None:
        if not self._cycle_guard.acquire(blocking=False):
            return  # a cycle is already running
        try:
            due = [a for a in self._store.list_accounts(active_only=True)
                   if (a["next_check_at"] or 0) <= self._now_ms()]
            for i, due_account in enumerate(due):
                if i > 0:
                    self._sleep(self._cfg()["account_gap_s"])
                handle = due_account["handle"]
                with self.account_lock(handle):
                    account = self._store.get_account(handle)
                    if not account or not account["active"]:
                        continue
                    force_catchup = handle not in self._polled_since_start or account["fail_count"] > 0
                    try:
                        r = poll_account(self._store, self._fx, account, now=self._now_ms(), cfg=self._cfg(),
                                         sleep=self._sleep, force_catchup=force_catchup)
                        if r["status"] == "ok":
                            self._polled_since_start.add(handle)
                    except Exception:
                        log.exception("poll @%s failed", handle)
                try:
                    on_change()
                except Exception:
                    log.exception("account update broadcast failed")
        except Exception:
            log.exception("poll cycle failed")
        finally:
            self._cycle_guard.release()
