"""Rules-based High / Med / Low impact for a news item — PURE, no I/O.

Every function takes the ``[impact]`` config as an ARGUMENT (the shape
``shared.news_config.impact_config()`` returns), so the scorer never reads a
file and a test pins the rules rather than the shipped values. Design:
``docs/plans/2026-09-26-news-v2-design.md`` §2.

* ``score(row, cfg, universe) -> (int, reasons)`` — the points and the rules
  that produced them. Reasons name the RULE, never private data: ``watchlist``
  names no ticker.
* ``band(score, cfg)`` — ``high`` / ``med`` / ``low``.
* ``apply(row, cfg, universe)`` — the payload shape ``{"band","score","reasons"}``.
* ``cap_stale(band, published_at, now, cfg) -> (band, capped)`` — a HIGH older
  than ``stale_after_h`` publishes as MED. Stored uncapped, capped at publish.
* ``fingerprint(cfg, universe)`` — changes when the config or the ticker set
  does, so the service knows a stored score is out of date.

Nothing here raises on a junk row or a junk config: a malformed input scores 0.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

# The built-in thresholds, used when the config's pair is unusable. The loader
# (shared.news_config.impact_config) validates too; this is the scorer's own
# floor so a hand-built dict can never raise or invert the bands.
_HIGH_AT, _MED_AT = 6, 3

# Relationship labels the EDGAR adapter writes that are NOT an officer or director.
_NOT_OFFICER = frozenset({"", "10% owner", "insider"})


# ── small total helpers ───────────────────────────────────────────────────────
def _num(v):
    """A finite real number, or ``None`` (rejects bool, NaN, inf, strings)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _strs(v) -> list[str]:
    """A list of non-empty strings; a bare string is NOT a list of its characters."""
    if not isinstance(v, (list, tuple)):
        return []
    return [s for s in v if isinstance(s, str) and s]


def _points(v) -> int:
    n = _num(v)
    return int(n) if n is not None else 0


# Copied from webgui/pages/news_view._money (Tier 1 cannot import services, and
# the reverse would drag a page module into the service) so a ``form4:`` reason
# reads the same figure the page's detail line does.
_MONEY_UNITS = ((1e9, "B", 1), (1e6, "M", 1), (1e3, "K", 0), (1.0, "", 0))


def _money(v):
    sign, a = ("-" if v < 0 else ""), abs(v)
    i = next((k for k, (scale, _, _) in enumerate(_MONEY_UNITS) if a >= scale),
             len(_MONEY_UNITS) - 1)
    while True:
        scale, suffix, places = _MONEY_UNITS[i]
        text = f"{a / scale:.{places}f}"
        if i == 0 or float(text) < 1000:
            return f"{sign}${text}{suffix}"
        i -= 1


# ── fingerprint + keyword compilation ─────────────────────────────────────────
def fingerprint(cfg, universe) -> str:
    """A short, stable id of (config, ticker set); ticker ORDER does not matter."""
    u = sorted(s for s in (universe or ()) if isinstance(s, str))
    blob = json.dumps({"cfg": cfg, "u": u}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _tiers(cfg) -> list[tuple[str, int, list[str]]]:
    """``(name, points, words)`` for every usable tier, in config order."""
    out = []
    for name, tier in _dict(_dict(cfg).get("keywords")).items():
        tier = _dict(tier)
        words = _strs(tier.get("words"))
        if not isinstance(name, str) or not words or _num(tier.get("points")) is None:
            continue
        out.append((name, _points(tier.get("points")), words))
    return out


def _word_pattern(word: str) -> re.Pattern:
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", re.IGNORECASE)


# One compiled entry per keyword fingerprint; bounded so a config that keeps
# changing (a Settings edit per minute) cannot grow it without limit.
_COMPILED: dict[str, list] = {}
_COMPILED_MAX = 8


def _compile(cfg) -> list[tuple[str, int, list[tuple[str, re.Pattern]]]]:
    tiers = _tiers(cfg)
    key = fingerprint({"keywords": [[n, p, w] for n, p, w in tiers]}, ())
    hit = _COMPILED.get(key)
    if hit is None:
        # One pattern PER WORD (not one alternation per tier): the reason must
        # name the first word in LIST order that matches, and a single
        # alternation reports the first match in TEXT order instead.
        hit = [(name, pts, [(w, _word_pattern(w)) for w in words if w.split()])
               for name, pts, words in tiers]
        if len(_COMPILED) >= _COMPILED_MAX:
            _COMPILED.clear()
        _COMPILED[key] = hit
    return hit


# ── the rules ─────────────────────────────────────────────────────────────────
def _form4(detail: dict, f4: dict, reasons: list[str]) -> int:
    value = _num(detail.get("total_value"))
    if value is None or value <= 0:
        return 0                      # a missing / NaN / bool value is never a band
    pts = 0
    for band_name in ("huge", "large", "small"):
        threshold = _num(f4.get(f"{band_name}_usd"))
        if threshold is not None and value >= threshold:
            pts = _points(f4.get(band_name))
            break
    reasons.append(f"form4:{_money(value)}")
    labels = [p.strip().lower() for p in _str(detail.get("relationship")).split(",")]
    if any(lab not in _NOT_OFFICER for lab in labels):
        officer = _points(f4.get("officer"))
        if officer:
            pts += officer
            reasons.append("officer")
    return pts


def score(row, cfg, universe) -> tuple[int, list[str]]:
    """The impact points of ``row`` and the rules that produced them."""
    row, cfg = _dict(row), _dict(cfg)
    reasons: list[str] = []
    total = 0

    text = _str(row.get("title"))
    if cfg.get("match_teaser") is True:
        text = f"{text}\n{_str(row.get('teaser'))}"
    if text.strip():
        for name, pts, words in _compile(cfg):
            for word, pat in words:       # a tier counts ONCE, whatever matches in it
                if pat.search(text):
                    total += pts
                    reasons.append(f"kw:{name}:{word}")
                    break

    sources = _strs(row.get("sources")) or _strs([row.get("source")])
    src_points = _dict(cfg.get("source_points"))
    if sources:
        # MAX over the row's feeds, not the sum: two outlets add only through
        # the multi-source boost below.
        best = max(sources, key=lambda s: _points(src_points.get(s)))
        best_pts = _points(src_points.get(best))
        if best_pts:
            total += best_pts
            reasons.append(f"source:{best}")
    if len(set(sources)) >= 2:
        boost = _points(cfg.get("multi_source"))
        if boost:
            total += boost
            reasons.append(f"sources:{len(set(sources))}")

    tickers = set(_strs(row.get("tickers")))
    if tickers and tickers & set(_strs(list(universe or ()))):
        boost = _points(cfg.get("watchlist"))
        if boost:
            total += boost
            reasons.append("watchlist")

    kind = _str(row.get("kind"))
    detail = _dict(row.get("detail"))
    if kind == "edgar_form4":
        total += _form4(detail, _dict(cfg.get("form4")), reasons)
    elif kind == "edgar_filings":
        filings = _dict(cfg.get("filings"))
        form = _str(detail.get("form")).strip()
        if form and form != "untracked" and form in filings:   # EXACT: S-3 never takes S-3ASR's
            pts = _points(filings.get(form))
            if pts:
                total += pts
                reasons.append(f"filing:{form}")
        if not tickers:
            pts = _points(filings.get("untracked"))
            if pts:
                total += pts
                reasons.append("untracked")

    return total, reasons


def _thresholds(cfg) -> tuple[float, float]:
    cfg = _dict(cfg)
    high, med = _num(cfg.get("high_at")), _num(cfg.get("med_at"))
    if high is None or med is None or not high > med:
        return _HIGH_AT, _MED_AT
    return high, med


def band(points, cfg) -> str:
    high, med = _thresholds(cfg)
    p = _num(points)
    if p is None:
        return "low"
    return "high" if p >= high else "med" if p >= med else "low"


def apply(row, cfg, universe) -> dict:
    """The stored / published shape: ``{"band", "score", "reasons"}``."""
    pts, reasons = score(row, cfg, universe)
    return {"band": band(pts, cfg), "score": pts, "reasons": reasons}


def _instant(v):
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str) and v.strip():
        try:
            dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def cap_stale(band_name, published_at, now, cfg) -> tuple[str, bool]:
    """A HIGH older than ``stale_after_h`` shows as MED; anything else unchanged.

    An unparseable ``published_at`` (the store's ``"undated"`` sentinel) is
    never stale — capping needs an age, and an unknown age is not an old one."""
    if band_name != "high":
        return band_name, False
    hours = _num(_dict(cfg).get("stale_after_h"))
    pub, ref = _instant(published_at), _instant(now)
    if hours is None or pub is None or ref is None:
        return band_name, False
    if (ref - pub).total_seconds() > hours * 3600:
        return "med", True
    return band_name, False
