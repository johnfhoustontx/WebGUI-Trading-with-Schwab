"""``cmd:xfeed``: the page's four commands, one answer key per request, two views.

    add_account      baseline a handle (slow: it fetches from FxEmbed)
    remove_account   stop watching a handle
    set_group        change or clear a handle's group
    query            one filtered page of the feed (filters, "load more")

Every command carries a ``request_id`` (32 hex chars, made by the page) and is
ANSWERED on ``cache:xfeed:answer:{request_id}`` - one key per request, with a TTL
and no event, so the page that asked polls its own key and concurrent requests
never overwrite each other. Error messages are the Node app's, verbatim.

The two views the page reads are ``cache:xfeed:latest`` (newest rows, top
tickers) and ``cache:xfeed:accounts`` (watched accounts, counts, poll interval,
page tunables), written ``skip_unchanged``.
"""
import logging
import re

from services import _degrade
from services.xfeed_svc import _trace, config, runtime
from services.xfeed_svc.mapping import normalize_handle

log = logging.getLogger("xfeed_svc.handlers")

AREA = "xfeed.handlers"

CACHE_LATEST = "cache:xfeed:latest"
EVENT_LATEST = "events:xfeed:latest"
CACHE_ACCOUNTS = "cache:xfeed:accounts"
EVENT_ACCOUNTS = "events:xfeed:accounts"

COMMANDS = ("add_account", "remove_account", "set_group", "query")
SLOW = frozenset({"add_account"})

RESTARTED = "The X feed service restarted - try again."
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_DEFAULT_LIMIT = 300


def answer_key(request_id) -> str:
    return f"cache:xfeed:answer:{request_id}"


def _valid_id(request_id) -> bool:
    return isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id) is not None


# -- views ----------------------------------------------------------------

def top_tickers(rows, n) -> list:
    counts = {}
    for r in rows:
        if r.get("kind") == "post":
            for t in r["cashtags"]:
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"tag": t, "n": c} for t, c in ranked[:n]]


def publish_latest(bus, rt) -> int:
    views = config.load()["views"]
    rows = rt.store.query_feed(limit=views["latest_rows"])
    payload = {"rows": rows, "tickers": top_tickers(rows, views["top_tickers"]), "total": len(rows)}
    return bus.cache_set(CACHE_LATEST, payload, event=EVENT_LATEST, skip_unchanged=True)


def publish_accounts(bus, rt) -> int:
    cfg = config.load()
    payload = {
        "accounts": rt.store.list_accounts(active_only=True),
        "counts": rt.store.account_counts(),
        "interval_s": rt.poller.interval_s(),
        "page": cfg["page"],
    }
    return bus.cache_set(CACHE_ACCOUNTS, payload, event=EVENT_ACCOUNTS, skip_unchanged=True)


def publish_all(bus, rt) -> None:
    publish_latest(bus, rt)
    publish_accounts(bus, rt)


# -- helpers --------------------------------------------------------------

def resolve_group(store, text, max_len):
    """Trim and collapse spaces; empty -> None; reuse an existing group's spelling."""
    if text is None:
        return None
    name = re.sub(r"\s+", " ", str(text).strip())
    if not name:
        return None
    if len(name) > max_len:
        raise ValueError(f"Group names can be at most {max_len} characters")
    lowered = name.lower()
    for a in store.list_accounts():
        g = a["group_name"]
        if g and g.lower() == lowered:
            return g
    return name


def _answer(bus, request_id, command, ok, error="", **extra) -> None:
    ttl = config.load()["views"]["answer_ttl_s"]
    payload = {"request_id": request_id, "command": command, "ok": ok, "error": "" if ok else error}
    payload.update(extra)
    bus.cache_set(answer_key(request_id), payload, ttl=ttl)


def _int_or(value, fallback):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


# -- commands -------------------------------------------------------------

def _add_account(bus, rt, request_id, args) -> None:
    handle = normalize_handle(args.get("handle"))
    if not handle:
        return _answer(bus, request_id, "add_account", False, "Invalid handle")
    try:
        group = resolve_group(rt.store, args.get("group"), config.load()["views"]["group_name_max"])
    except ValueError as e:
        return _answer(bus, request_id, "add_account", False, str(e))
    existing = rt.store.get_account(handle)
    if existing and existing["active"]:
        return _answer(bus, request_id, "add_account", False, f"Already watching @{handle}")
    r = rt.poller.add_account(handle, group)
    if not r["ok"]:
        if r["reason"] == "not_found":
            return _answer(bus, request_id, "add_account", False, "Account not found")
        return _answer(bus, request_id, "add_account", False, f"Could not reach FxEmbed ({r['reason']})")
    publish_all(bus, rt)
    _answer(bus, request_id, "add_account", True, account=r["account"])


def _watched(rt, args):
    handle = normalize_handle(args.get("handle"))
    if not handle:
        return None
    acct = rt.store.get_account(handle)
    return handle if acct and acct["active"] else None


def _remove_account(bus, rt, request_id, args) -> None:
    handle = _watched(rt, args)
    if not handle:
        return _answer(bus, request_id, "remove_account", False, "Not watching that account")
    with rt.poller.account_lock(handle):
        rt.store.deactivate_account(handle)
    publish_all(bus, rt)
    _answer(bus, request_id, "remove_account", True)


def _set_group(bus, rt, request_id, args) -> None:
    handle = _watched(rt, args)
    if not handle:
        return _answer(bus, request_id, "set_group", False, "Not watching that account")
    try:
        group = resolve_group(rt.store, args.get("group"), config.load()["views"]["group_name_max"])
    except ValueError as e:
        return _answer(bus, request_id, "set_group", False, str(e))
    with rt.poller.account_lock(handle):
        rt.store.set_account_group(handle, group)
    publish_all(bus, rt)
    _answer(bus, request_id, "set_group", True, account=rt.store.get_account(handle))


def _query(bus, rt, request_id, args) -> None:
    views = config.load()["views"]
    limit = min(max(_int_or(args.get("limit"), 0) or _DEFAULT_LIMIT, 1), views["query_limit_max"])
    before = args.get("before")
    rows = rt.store.query_feed(
        account=args.get("account") or None,
        group=args.get("group") or None,
        ungrouped=args.get("ungrouped") in (True, 1, "1"),
        type=args.get("type") or None,
        q=args.get("q") or None,
        ticker=args.get("ticker") or None,
        before=_int_or(before, None) if before not in (None, "") else None,
        before_key=args.get("before_key") or None,
        limit=limit,
    )
    _answer(bus, request_id, "query", True, rows=rows,
            tickers=top_tickers(rows, views["top_tickers"]), counts=rt.store.account_counts())


_DISPATCH = {
    "add_account": _add_account,
    "remove_account": _remove_account,
    "set_group": _set_group,
    "query": _query,
}


def _safe_answer(bus, request_id, command, error) -> None:
    """Answer a fault; when the fault IS the bus there is nobody to tell, so count it."""
    try:
        _answer(bus, request_id, command, False, error)
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("answering after a fault", exc), exc_info=False)


def _read(command):
    """(type, args, request_id) with request_id None unless valid. May raise on a malformed command."""
    args = command.args
    if not isinstance(args, dict):
        args = {}
    request_id = args.get("request_id")
    return command.type, args, request_id if _valid_id(request_id) else None


def handle_command(bus, command) -> None:
    """One ``cmd:xfeed`` command. NEVER raises: a fault is counted, answered and kept
    from the dead-letter list."""
    request_id = None
    type_ = ""
    try:
        type_, args, request_id = _read(command)
        if request_id is None:
            log.warning("xfeed: a %s command with no usable request id was dropped", _kind(type_))
            return
        fn = _DISPATCH.get(type_)
        if fn is None:
            log.warning("xfeed: an unknown command (%s) was dropped", _kind(type_))
            _safe_answer(bus, request_id, _kind(type_), "Unknown command")
            return
        fn(bus, runtime.get(), request_id, args)
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("handling an xfeed command", exc), exc_info=False)
        if request_id:
            _safe_answer(bus, request_id, _kind(type_), "Server error")


def _kind(type_) -> str:
    """The command type for a log line or an answer: known names whole, anything else cut short."""
    return type_ if type_ in COMMANDS else str(type_)[:20]


def on_dropped(bus, command, why) -> None:
    """The scaffold's notice that a command will never run. Never raises."""
    try:
        type_, _args, request_id = _read(command)
        if request_id:
            _safe_answer(bus, request_id, _kind(type_), RESTARTED)
    except Exception as exc:
        _degrade.degraded(AREA, detail=_trace.where("answering a dropped command", exc), exc_info=False)
