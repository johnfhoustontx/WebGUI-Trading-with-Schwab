"""Order-execution foundation for the portfolio analyzer.

This is the CLIENT-side foundation for live trading. It mirrors the proxy-backed
style of :mod:`src.data`: every Schwab call funnels through ``schwab-proxy`` (the
proxy owns OAuth/tokens — this client owns none).

Scope (YAGNI): single-leg equity orders only. Multi-leg / options-spread bodies
are intentionally NOT built here.

# TODO(live-trading): add multi-leg / options-spread order bodies when the
# roadmap reaches options execution.
"""
from __future__ import annotations

import sys
import pathlib

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import PROXY_URL

_VALID_INSTRUCTIONS = {
    "BUY", "SELL",
    "BUY_TO_OPEN", "SELL_TO_CLOSE",
    "SELL_TO_OPEN", "BUY_TO_CLOSE",
}
_VALID_ORDER_TYPES = {"MARKET", "LIMIT"}


def build_order_body(
    symbol: str,
    instruction: str,
    quantity,
    *,
    order_type: str = "MARKET",
    price=None,
    asset_type: str = "EQUITY",
    session: str = "NORMAL",
    duration: str = "DAY",
) -> dict:
    """Build a Schwab single-leg order body (pure — sends nothing).

    Args:
        symbol: instrument symbol (uppercased in the output).
        instruction: one of the supported open/close/buy/sell instructions.
        quantity: order quantity; must be > 0 (int or float, stored as given).
        order_type: ``"MARKET"`` or ``"LIMIT"``.
        price: required for ``LIMIT`` orders; included as top-level ``"price"``.
            Omitted entirely for ``MARKET`` orders.
        asset_type: instrument asset type (default ``"EQUITY"``).
        session: trading session (default ``"NORMAL"``).
        duration: order duration (default ``"DAY"``).

    Returns:
        The Schwab order body dict, ready to POST to the proxy.

    Raises:
        ValueError: on an invalid instruction, non-positive quantity, invalid
            order type, or a LIMIT order missing a price.
    """
    if instruction not in _VALID_INSTRUCTIONS:
        raise ValueError(f"invalid instruction: {instruction!r}")
    if not quantity > 0:
        raise ValueError(f"quantity must be > 0, got {quantity!r}")
    if order_type not in _VALID_ORDER_TYPES:
        raise ValueError(f"invalid order_type: {order_type!r}")
    if order_type == "LIMIT" and price is None:
        raise ValueError("price is required for a LIMIT order")

    body: dict = {
        "orderType": order_type,
        "session": session,
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": instruction,
                "quantity": quantity,
                "instrument": {
                    "symbol": symbol.upper(),
                    "assetType": asset_type,
                },
            }
        ],
    }
    if order_type == "LIMIT":
        body["price"] = price
    return body


class ExecutionClient:
    """Proxy-backed order-execution client.

    Foundation for the live-trading roadmap. ``preview_order`` is a safe
    "what would be sent" helper that makes no network call; ``place_order`` is
    the live path that actually submits to Schwab via the proxy.
    """

    def __init__(self, base_url: str = PROXY_URL):
        self.base = base_url
        self.session = requests.Session()

    def preview_order(self, symbol: str, instruction: str, quantity, **kwargs) -> dict:
        """Return the order body that *would* be sent — no HTTP call is made.

        Delegates to :func:`build_order_body`; useful for confirmation UIs and
        dry-run inspection before any live submission.
        """
        return build_order_body(symbol, instruction, quantity, **kwargs)

    def place_order(self, account_hash: str, body: dict) -> dict:
        """POST an order body to the proxy and return the parsed response.

        This is the LIVE path: it submits a real order via
        ``POST {base}/orders/{account_hash}`` (the proxy forwards it to the
        Schwab Trader API). It works, but is foundation for the live-trading
        roadmap and is intentionally NOT yet wired to any GUI button.

        Args:
            account_hash: a linked account's ``hashValue`` (from ``/accounts``).
            body: a Schwab order body, e.g. from :func:`build_order_body`.

        Returns:
            The proxy's parsed JSON response.
        """
        r = self.session.post(f"{self.base}/orders/{account_hash}", json=body, timeout=30)
        r.raise_for_status()
        return r.json()
