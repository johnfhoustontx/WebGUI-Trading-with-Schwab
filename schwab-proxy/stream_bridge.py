"""
SchwabProxy - schwab-py Client bridge for streaming
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation
- Builds an asyncio schwab-py StreamClient backed by the proxy's hand-managed
  TokenManager, so the proxy remains the single owner of the Schwab token.
- Adaptation for schwab-py 1.5.1: client_from_access_functions wraps the token
  in metadata via TokenMetadata.from_loaded_token, which REQUIRES the read func
  to return {"creation_timestamp": <int>, "token": {<raw oauth dict>}} (it
  raises ValueError if "creation_timestamp" is missing). The write func likewise
  receives that metadata-wrapped object, so the raw OAuth dict is unwrapped from
  token["token"] before being copied back into the proxy's TokenManager.
"""

#############################################
# IMPORTS
#############################################

import time
import logging
from datetime import datetime, timezone

from schwab.auth import client_from_access_functions
from schwab.streaming import StreamClient

log = logging.getLogger("stream_bridge")


#############################################
# HELPERS
#############################################

def _creation_timestamp(token_mgr) -> int:
    """Derive a stable unix creation timestamp for the metadata wrapper.

    schwab-py only uses creation_timestamp for token-age reporting, not for
    refresh decisions (the underlying OAuth session refreshes on expiry). We
    approximate it from the proxy's ExpiresAt/ExpiresIn so the value is sane;
    fall back to now() if neither is available.
    """
    tokens = token_mgr.tokens
    expires_at = tokens.get("ExpiresAt")
    expires_in = tokens.get("ExpiresIn", 1800)
    if expires_at:
        try:
            iso = expires_at.rstrip("Z")
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
            return int(dt.timestamp()) - int(expires_in)
        except (ValueError, TypeError):
            pass
    return int(time.time())


#############################################
# STREAM CLIENT BRIDGE
#############################################

def build_stream_client(token_mgr, app_key, app_secret):
    """Construct an asyncio StreamClient whose token read/write delegate to the
    proxy's TokenManager. The StreamClient fetches its own streamer connection
    info (user preferences) through the underlying client.

    Args:
        token_mgr: proxy TokenManager with a .tokens dict and ._save_tokens().
        app_key:   Schwab application app key.
        app_secret: Schwab application secret.

    Returns:
        schwab.streaming.StreamClient (asyncio-backed).
    """
    def read_token():
        # schwab-py 1.5.1 expects a metadata-wrapped token; the raw OAuth dict
        # lives under "token" and "creation_timestamp" is mandatory.
        raw = {
            "access_token": token_mgr.tokens["AccessToken"],
            "refresh_token": token_mgr.tokens.get("RefreshToken", ""),
            "token_type": "Bearer",
            "expires_in": token_mgr.tokens.get("ExpiresIn", 1800),
        }
        return {
            "creation_timestamp": _creation_timestamp(token_mgr),
            "token": raw,
        }

    def write_token(token, *args, **kwargs):
        # token is the metadata-wrapped object; unwrap to the raw OAuth dict.
        raw = token.get("token", token) if isinstance(token, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        token_mgr.tokens["AccessToken"] = raw.get(
            "access_token", token_mgr.tokens.get("AccessToken"))
        if raw.get("refresh_token"):
            token_mgr.tokens["RefreshToken"] = raw["refresh_token"]
        if raw.get("expires_in"):
            token_mgr.tokens["ExpiresIn"] = raw["expires_in"]
        token_mgr._save_tokens()
        log.info("stream_bridge: token written back to proxy TokenManager")

    client = client_from_access_functions(
        app_key, app_secret, read_token, write_token, asyncio=True)
    return StreamClient(client)
