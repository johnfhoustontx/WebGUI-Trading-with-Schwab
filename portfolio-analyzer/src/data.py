"""Proxy-backed data access for the portfolio analyzer.

All Schwab access funnels through schwab-proxy — this client owns no tokens.
"""
import sys, pathlib
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import PROXY_URL, SCHWAB_PROXY
if str(SCHWAB_PROXY) not in sys.path:      # hyphenated folder: import by path
    sys.path.insert(0, str(SCHWAB_PROXY))
import proxy_client as _proxy_client  # noqa: E402


def _checked(r):
    """``r`` if the proxy answered, else raise - with the PROXY'S OWN reason when
    it refused the account routes (401 wrong secret, 503 none configured), so the
    Portfolio page says what to do instead of "503 Server Error"."""
    if r.status_code in (401, 503):
        try:
            detail = r.json().get("detail")
        except Exception:  # noqa: BLE001 - no JSON body: fall through to raise_for_status
            detail = None
        if detail:
            raise RuntimeError(f"Schwab proxy refused the account request: {detail}")
    r.raise_for_status()
    return r


class PortfolioData:
    def __init__(self, base_url: str = PROXY_URL):
        self.base = base_url
        self.session = requests.Session()
        # The proxy's account routes (/accounts, /positions, /transactions)
        # refuse a caller without the shared secret, and this client is the
        # only thing that calls them. X-Caller is the per-caller count label.
        _proxy_client._apply_secret(self.session)
        _proxy_client._apply_identity(self.session)

    def get_positions(self) -> list[dict]:
        r = _checked(self.session.get(f"{self.base}/positions", timeout=30))
        return r.json().get("positions", [])

    def get_accounts(self) -> list[dict]:
        """Return the linked accounts (Schwab list of objects with hashValue)."""
        r = _checked(self.session.get(f"{self.base}/accounts", timeout=30))
        return r.json()

    def first_account_hash(self) -> str | None:
        """First linked account's hashValue, or None if there are no accounts."""
        accounts = self.get_accounts()
        if not accounts:
            return None
        return accounts[0].get("hashValue")

    def account_hashes(self) -> list[str]:
        """Every linked account's hashValue (empty list if there are none).

        Used by the transaction sync to pull trades from all accounts, mirroring
        the proxy's ``/positions`` aggregation across the whole linked book.
        """
        return [a.get("hashValue") for a in self.get_accounts() if a.get("hashValue")]

    def get_transactions(self, account_hash: str, start_date: str, end_date: str) -> list[dict]:
        r = self.session.get(f"{self.base}/transactions/{account_hash}",
                             params={"start_date": start_date, "end_date": end_date}, timeout=30)
        _checked(r)
        return r.json().get("transactions", [])

    def get_daily_history(self, symbol: str, months: int = 12):
        import pandas as pd
        if months <= 1:
            period_type, period = "month", 1
        elif months <= 3:
            period_type, period = "month", 3
        elif months <= 6:
            period_type, period = "month", 6
        else:
            period_type, period = "year", 1
        r = self.session.get(f"{self.base}/pricehistory", params={
            "symbol": symbol, "periodType": period_type, "period": period,
            "frequencyType": "daily", "frequency": 1}, timeout=30)
        r.raise_for_status()
        data = r.json()
        candles = (data or {}).get("candles") or []
        if not candles:
            return None
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        return df

    def stream_quotes(self, symbols, on_tick, should_stop) -> None:
        """Consume the proxy's SSE quote stream, calling ``on_tick`` per tick.

        Opens ``GET {base}/stream/quotes?symbols=...`` with ``stream=True``,
        iterates lines, and for every ``data: {...}`` line (parsed via
        :func:`src.live.parse_sse_line`) calls ``on_tick(tick)``.

        Runs until ``should_stop()`` returns truthy or the connection drops /
        errors, then **returns** — the caller is responsible for reconnect. This
        is blocking I/O meant to run on a background thread; it is not unit
        tested (the pure parse/apply logic in ``src.live`` is).

        Args:
            symbols: iterable of symbol strings to subscribe to.
            on_tick: callable ``tick_dict -> None`` invoked per quote.
            should_stop: zero-arg callable; when it returns truthy the loop
                stops (checked between lines).
        """
        from src.live import parse_sse_line

        syms = ",".join(symbols)
        if not syms:
            return
        try:
            with self.session.get(
                f"{self.base}/stream/quotes",
                params={"symbols": syms},
                stream=True,
                timeout=(10, None),  # connect timeout; no read timeout for SSE.
            ) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if should_stop():
                        return
                    if raw is None:
                        continue
                    tick = parse_sse_line(raw)
                    if tick is not None:
                        on_tick(tick)
        except Exception:
            # Connection drop / parse / HTTP error: return so the caller can
            # decide whether to reconnect. Never raise out of the stream loop.
            return
