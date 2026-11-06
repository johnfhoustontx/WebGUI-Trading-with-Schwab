"""Phase 0 of the live-order design: ask Schwab which orders it would accept.

Design: docs/plans/2026-11-05-live-order-to-thinkorswim-design.md.

Every request this tool sends goes to the proxy's ``POST /orders/preview``,
which asks Schwab's ``previewOrder`` to CHECK an order. **Nothing is placed**:
no route that places an order exists. The proxy refuses unless
``config/live_orders.toml`` says ``mode = "preview"``.

Run on the box where the proxy is up, with the stack's ``.env`` loaded (the
account routes need ``PROXY_SHARED_SECRET``):

    set -a; . ./.env; set +a
    .venv/bin/python tools/live_order_preview.py accounts
    .venv/bin/python tools/live_order_preview.py matrix --account 1234
    .venv/bin/python tools/live_order_preview.py matrix --account 1234 \\
        --symbols SPY,'$SPX',INTC --out /tmp/preview.json

``accounts`` lists the linked accounts by their last four digits only.
``matrix`` builds a single long call, a debit call vertical, a credit put
vertical and an iron condor on each symbol's nearest expiry 1 to 10 days out,
each in three shapes (the opening order alone, with a profit target, and the
full target-and-stop bracket), at 1 contract, and prints Schwab's verdict on
each. ``--out`` saves every order and answer as JSON for the write-up.

An account number or hash is never printed or saved: Schwab's answer echoes the
full account number, so :func:`scrub` cuts every account-naming key to its last
four digits before anything is printed or saved.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
# webgui for the paste script's own settings (pages/options/tos_order.py is
# pure apart from reading config/tos_order.toml).
for p in (_ROOT, _ROOT / "schwab-proxy", _ROOT / "webgui"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import requests  # noqa: E402

from repo_paths import PROXY_URL  # noqa: E402
from shared import live_order, live_orders_config  # noqa: E402

DEFAULT_SYMBOLS = ("SPY", "$SPX", "INTC")
STRUCTURES = ("long_call", "debit_call_vertical", "credit_put_vertical", "iron_condor")
MIN_DTE, MAX_DTE = 1, 10


# ── reading a Schwab chain (pure) ───────────────────────────────────────────

def _mid(contract):
    """A contract's mid, or None when it has no two-sided market."""
    bid, ask = contract.get("bid"), contract.get("ask")
    if not all(isinstance(v, (int, float)) for v in (bid, ask)) or bid <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def nearest_expiry(chain, today):
    """The ``callExpDateMap`` key of the nearest expiry MIN_DTE to MAX_DTE days
    out that also has puts, or None."""
    keys = sorted(set(chain.get("callExpDateMap") or {}) & set(chain.get("putExpDateMap") or {}))
    for key in keys:
        try:
            d = dt.date.fromisoformat(key.split(":", 1)[0])
        except ValueError:
            continue
        if MIN_DTE <= (d - today).days <= MAX_DTE:
            return key
    return None


def _side(chain, map_name, key):
    """``[(strike, contract)]`` with a usable mid, sorted by strike."""
    out = []
    for strike_text, contracts in (chain.get(map_name, {}).get(key) or {}).items():
        if not contracts:
            continue
        c = contracts[0]
        if _mid(c) is None or not isinstance(c.get("symbol"), str):
            continue
        out.append((float(strike_text), c))
    return sorted(out, key=lambda x: x[0])


def _leg(kind, side, strike, contract, expiration):
    return {"kind": kind, "side": side, "strike": strike, "qty": 1,
            "expiration": expiration, "symbol": contract["symbol"],
            "_mid": _mid(contract)}


def pick_structures(chain, today):
    """``{structure: legs}`` near the money on the nearest usable expiry.
    A structure the chain cannot fill is left out."""
    key = nearest_expiry(chain, today)
    spot = chain.get("underlyingPrice")
    if key is None or not isinstance(spot, (int, float)) or spot <= 0:
        return {}
    exp = key.split(":", 1)[0]
    calls = [x for x in _side(chain, "callExpDateMap", key) if x[0] > spot]
    puts = [x for x in reversed(_side(chain, "putExpDateMap", key)) if x[0] < spot]
    out = {}
    if calls:
        out["long_call"] = [_leg("call", "long", *calls[0], exp)]
    if len(calls) >= 2:
        out["debit_call_vertical"] = [_leg("call", "long", *calls[0], exp),
                                      _leg("call", "short", *calls[1], exp)]
    if len(puts) >= 2:
        out["credit_put_vertical"] = [_leg("put", "short", *puts[0], exp),
                                      _leg("put", "long", *puts[1], exp)]
    if len(calls) >= 3 and len(puts) >= 3:
        out["iron_condor"] = [_leg("put", "long", *puts[2], exp),
                              _leg("put", "short", *puts[1], exp),
                              _leg("call", "short", *calls[1], exp),
                              _leg("call", "long", *calls[2], exp)]
    return out


def net_price(legs):
    """The per-unit price to open at (always positive), or None when the mids
    net to nothing."""
    net = sum((1 if l["side"] == "long" else -1) * l["_mid"] * l["qty"] for l in legs)
    return abs(net) if abs(net) >= 0.01 else None


#: Keys of Schwab's answer that name the account. Schwab's preview echoes the
#: FULL account number (``orderStrategy.accountNumber``, found 2026-11-05 on
#: the first run), so every answer is scrubbed before it is printed or saved.
_ACCOUNT_KEYS = frozenset({"accountNumber", "accountId", "hashValue",
                           "accountHash", "account_hash"})


def scrub(value):
    """``value`` with every account-naming key's value cut to its last 4."""
    if isinstance(value, dict):
        return {k: (("…" + str(v)[-4:]) if k in _ACCOUNT_KEYS and v is not None
                    else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def summarize(status, answer):
    """``(verdict, messages)`` from Schwab's preview answer. ``verdict`` is
    ``accepted``, ``rejected`` or ``error <status>``."""
    schwab = (answer or {}).get("schwab") if isinstance(answer, dict) else None
    result = (schwab or {}).get("orderValidationResult") if isinstance(schwab, dict) else None
    messages = []
    if isinstance(result, dict):
        for kind in ("rejects", "reviews", "warns", "alerts"):
            for item in result.get(kind) or ():
                text = item.get("message") or item.get("activityMessage") \
                    if isinstance(item, dict) else str(item)
                messages.append(f"{kind[:-1]}: {text}")
    if status not in (200, 201):
        err = (answer or {}).get("error") or (answer or {}).get("detail") \
            if isinstance(answer, dict) else answer
        if not messages and err:
            messages.append(str(err)[:300])
        return f"error {status}", messages
    if isinstance(result, dict) and result.get("rejects"):
        return "rejected", messages
    return "accepted", messages


# ── talking to the proxy ────────────────────────────────────────────────────

def _session():
    from proxy_client import _client_secret
    s = requests.Session()
    secret = _client_secret()
    if not secret:
        sys.exit("PROXY_SHARED_SECRET is not set: load the stack's .env first "
                 "(set -a; . ./.env; set +a)")
    s.headers["X-Proxy-Secret"] = secret
    s.headers["X-Caller"] = "live_order_preview"
    return s


def _accounts(session):
    r = session.get(f"{PROXY_URL}/accounts", timeout=30)
    if r.status_code != 200:
        sys.exit(f"/accounts answered {r.status_code}: {r.text[:200]}")
    return [a for a in r.json() if isinstance(a, dict)]


def _hash_for(session, last4):
    matches = [a for a in _accounts(session)
               if str(a.get("accountNumber", "")).endswith(str(last4))]
    if len(matches) != 1:
        sys.exit(f"{len(matches)} linked accounts end in {last4}; give four digits "
                 "that name exactly one (see the accounts command)")
    return matches[0]["hashValue"]


def cmd_accounts(args):
    for a in _accounts(_session()):
        print(f"account ending {str(a.get('accountNumber', ''))[-4:]}")
    return 0


def cmd_matrix(args):
    mode = live_orders_config.mode()
    if mode != "preview":
        print(f'config/live_orders.toml mode is "{mode}" here; the proxy refuses '
              'previews unless it is "preview".', file=sys.stderr)
    session = _session()
    account_hash = _hash_for(session, args.account)
    ticks = live_orders_config.ticks()
    from pages.options import tos_order
    cfg = tos_order.settings()
    today = dt.date.today()
    records = []
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        r = session.get(f"{PROXY_URL}/chains", params={
            "symbol": symbol, "contractType": "ALL",
            "fromDate": (today + dt.timedelta(days=MIN_DTE)).isoformat(),
            "toDate": (today + dt.timedelta(days=MAX_DTE)).isoformat()}, timeout=60)
        if r.status_code != 200:
            print(f"{symbol}: chain answered {r.status_code}; skipped")
            continue
        picked = pick_structures(r.json(), today)
        for name in STRUCTURES:
            legs = picked.get(name)
            if not legs:
                print(f"{symbol:<6} {name:<20} no strikes to build it from")
                continue
            price = net_price(legs)
            if price is None:
                print(f"{symbol:<6} {name:<20} the mids net to nothing; skipped")
                continue
            clean = [{k: v for k, v in l.items() if k != "_mid"} for l in legs]
            debit = live_order.is_debit(clean)
            pcts = cfg["debit"] if debit else cfg["credit"]
            for variant in live_order.VARIANTS:
                order = live_order.schwab_order(
                    clean, contracts=1, limit=price, variant=variant, pcts=pcts,
                    exit_tif=cfg["exit_tif"], root=symbol, ticks=ticks)
                resp = session.post(f"{PROXY_URL}/orders/preview", timeout=60,
                                    json={"account_hash": account_hash, "order": order})
                try:
                    answer = scrub(resp.json())
                except ValueError:
                    answer = resp.text
                verdict, messages = summarize(resp.status_code, answer)
                print(f"{symbol:<6} {name:<20} {variant:<8} {verdict}")
                for m in messages[:6]:
                    print(f"{'':<36}{m}")
                records.append({"symbol": symbol, "structure": name, "variant": variant,
                                "account_last4": str(args.account)[-4:],
                                "order": order, "http_status": resp.status_code,
                                "verdict": verdict, "messages": messages,
                                "answer": answer})
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"wrote {len(records)} previews to {args.out}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("accounts", help="list linked accounts by their last four digits")
    m = sub.add_parser("matrix", help="preview each structure in each shape")
    m.add_argument("--account", required=True, help="the account's last four digits")
    m.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    m.add_argument("--out", help="save every order and answer to this JSON file")
    args = ap.parse_args(argv)
    return {"accounts": cmd_accounts, "matrix": cmd_matrix}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
