"""Captions and hashtags for the Facebook Page and the Instagram account.

``shared/notify/meta_post.py`` posts what this module writes. A Facebook post
carries its link as the post's own ``link`` (Facebook draws the page's preview),
so its text is the body and a few tags. An Instagram caption cannot carry a
working link: it writes the page's address out without ``https://``, so it reads
as words rather than as a link that does nothing, and points at the profile's
link. Instagram allows 5 hashtags and 2,200 characters; a caption that would run
over is cut in its BODY, never in the address or the tags.

Stdlib, ``repo_paths`` and ``shared.config_toml`` only. The settings are
``config/meta_post.toml``; the credentials and the on / dry-run switches are the
``meta`` block of the gitignored ``shared/notifications.json``.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
import re

from repo_paths import META_POST_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "graph": {"version": "v26.0", "timeout_sec": 30, "ig_poll_tries": 5, "ig_poll_sec": 2},
    "facebook": {"enabled": True, "daily_cap": 20, "max_tags": 2},
    "instagram": {"enabled": True, "daily_cap": 20, "max_tags": 5, "max_chars": 2200},
    "kinds": {"trade_idea": True, "report": True},
    "hashtags": {
        "instagram": {
            "trade_idea": ["#optionstrading", "#stockmarket", "#trading", "#options"],
            "report": ["#stockmarket", "#trading", "#stocks", "#investing", "#SPY"],
        },
        "facebook": {
            "trade_idea": ["#optionstrading"],
            "report": ["#stockmarket", "#optionstrading"],
        },
    },
    "caption": {"link_line_idea": "Full trade: {address}",
                "link_line_report": "Full report: {address}",
                "bio_line": "Link in bio."},
}

config, reset_cache = toml_loader(META_POST_TOML, DEFAULTS, label="meta_post.toml")

NETWORKS = ("facebook", "instagram")
_SCHEME_RE = re.compile(r"^https?://", re.I)


def _section(cfg, name):
    v = (cfg if isinstance(cfg, dict) else {}).get(name)
    return v if isinstance(v, dict) else {}


def _int(value, default):
    if isinstance(value, bool):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def hashtag(symbol):
    """``#`` + the letters and digits of a symbol (``$SPX`` -> ``#SPX``,
    ``BRK/B`` -> ``#BRKB``), or None when it has none."""
    body = "".join(ch for ch in str(symbol or "") if ch.isascii() and ch.isalnum())
    return f"#{body.upper()}" if body else None


def tags(kind, network, symbol=None, cfg=None):
    """The symbol's own tag first, then ``[hashtags.<network>] <kind>``, without a
    repeat (case does not matter), at most ``[<network>] max_tags``."""
    cfg = cfg if cfg is not None else config()
    cap = _int(_section(cfg, network).get("max_tags"), DEFAULTS[network]["max_tags"])
    configured = _section(_section(cfg, "hashtags"), network).get(kind)
    out, seen = [], set()
    for tag in [hashtag(symbol)] + (configured if isinstance(configured, list) else []):
        if not isinstance(tag, str):
            continue
        tag = tag.strip()
        if not tag.startswith("#"):
            tag = "#" + tag
        if len(tag) < 2 or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    return out[:cap]


def facebook_text(body, tags):
    """The body, a blank line, the tags."""
    body = str(body or "").strip()
    line = " ".join(tags or [])
    return f"{body}\n\n{line}" if line else body


def bare_address(address):
    """``https://neuralstrike.co/t/x/`` -> ``neuralstrike.co/t/x/``."""
    return _SCHEME_RE.sub("", str(address or "").strip())


def instagram_text(body, kind, address, tags, cfg=None):
    """The body, a blank line, ``Full trade: <address>`` and ``Link in bio.``, a
    blank line, the tags. No address, no link lines. Cut to ``[instagram]
    max_chars`` in the body alone (ending ``…``)."""
    cfg = cfg if cfg is not None else config()
    cap = _section(cfg, "caption")
    words = DEFAULTS["caption"]
    limit = _int(_section(cfg, "instagram").get("max_chars"),
                 DEFAULTS["instagram"]["max_chars"]) or DEFAULTS["instagram"]["max_chars"]
    key = "link_line_report" if kind == "report" else "link_line_idea"
    tail = []
    if address:
        line = str(cap.get(key) or words[key])
        try:
            tail.append(line.format(address=bare_address(address)))
        except (KeyError, IndexError, ValueError):
            tail.append(words[key].format(address=bare_address(address)))
        bio = str(cap.get("bio_line") if cap.get("bio_line") is not None
                  else words["bio_line"]).strip()
        if bio:
            tail.append(bio)
    after = ""
    if tail:
        after += "\n\n" + "\n".join(tail)
    if tags:
        after += "\n\n" + " ".join(tags)
    body = str(body or "").strip()
    room = limit - len(after)
    if len(body) > room:
        body = body[:max(room - 1, 0)].rstrip() + "…"
    return body + after
