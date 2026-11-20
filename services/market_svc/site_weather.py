"""The market weather card on the public homepage (neuralstrike.co).

After each Market read slot, ``scheduler.refresh_read`` hands the reading here
and this module writes ``deploy/site/weather.json``, which
``deploy/site/assets/weather.js`` draws as a card at the top right of the
homepage. The file is GITIGNORED generated state, exactly like ``ideas.json``:
committed, the first write would dirty prod's tree and ``tools/promote.sh``
refuses a dirty tree.

Everything on the card is a translation of facts already in the published
reading (``cache:market:read``, which the public Desk shows), plus the regime's
label for one chip. Code writes every word; nothing here predicts a move.
Whether a sky is still current is decided in the BROWSER against the market
clock, because this file cannot know that its writer has died.

Design: docs/plans/2026-11-19-market-weather-card-design.md.
"""
from services.market_svc import market_read
from shared.numeric import finite

HIDDEN = {"v": 1, "hidden": True}

LABELS = {"fog": "Fog", "storm": "Severe storm", "showers": "Scattered showers",
          "overcast": "Overcast", "fair": "Mostly sunny", "clear": "Clear skies"}

_TAILWIND, _HEADWIND, _NEUTRAL, _NONE = "tailwind", "headwind", "neutral", "none"
_TONES = {_TAILWIND: "warm", _HEADWIND: "cold"}

# The opening of each headline. Overcast names no coverage: no front moved.
_FRONTS = {"storm": "Cold front sweeps", "showers": "Light rain falls over",
           "fair": "Sun breaks through over", "clear": "A warm front lifts",
           "overcast": "No front either way"}
_COLD_SKIES = ("storm", "showers")

_MINUS = "−"   # the minus sign, not a hyphen


# ── small pieces ─────────────────────────────────────────────────────────────
def _tone(verdict) -> str:
    return _TONES.get(verdict, "plain")


def _signed(x: float, places: int = 1) -> str:
    text = f"{abs(x):.{places}f}"
    if float(text) == 0:
        return text
    return ("+" if x > 0 else _MINUS) + text


def _slot_label(slot) -> str | None:
    try:
        hh, mm = (int(p) for p in str(slot).split(":"))
    except ValueError:
        return None
    return f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'} CT"


def _rows(reading) -> dict:
    rows = reading.get("rows") if isinstance(reading.get("rows"), list) else []
    return {r.get("key"): r for r in rows if isinstance(r, dict)}


def _facts(row) -> dict:
    f = row.get("facts") if isinstance(row, dict) else None
    return f if isinstance(f, dict) else {}


def _flow_public(row) -> bool:
    return isinstance(row, dict) and row.get("public") is True


def _tally(rows) -> list:
    """Each row's verdict in ROW_KEYS order. The flow estimate is shown only
    where its own public switch allows; masked, it is no reading."""
    out = []
    for key in market_read.ROW_KEYS:
        row = rows.get(key)
        verdict = row.get("verdict") if isinstance(row, dict) else _NONE
        if verdict not in (_TAILWIND, _HEADWIND, _NEUTRAL):
            verdict = _NONE
        if key == "flow" and not _flow_public(row):
            verdict = _NONE
        out.append(verdict)
    return out


def _tally_text(tally) -> str:
    t, h, n, x = (tally.count(v) for v in (_TAILWIND, _HEADWIND, _NEUTRAL, _NONE))
    parts = []
    if t:
        parts.append(f"{t} tailwind{'s' if t != 1 else ''}")
    if h:
        parts.append(f"{h} headwind{'s' if h != 1 else ''}")
    if n:
        parts.append(f"{n} neutral")
    if x:
        parts.append(f"{x} no reading")
    return " · ".join(parts)


def sky(tally: list, regime: dict | None, wcfg: dict) -> str:
    """The card's sky from the (masked) tally. First match wins."""
    t, h = tally.count(_TAILWIND), tally.count(_HEADWIND)
    read = sum(1 for v in tally if v != _NONE)
    if read < wcfg["min_readings"]:
        return "fog"
    stressed = isinstance(regime, dict) and regime.get("committed_label") == "crisis"
    if h >= wcfg["storm_headwinds"] or (stressed and h > t):
        return "storm"
    if t >= wcfg["clear_tailwinds"] and h == 0:
        return "clear"
    if t > h:
        return "fair"
    if h > t:
        return "showers"
    return "overcast"


# ── the readings ─────────────────────────────────────────────────────────────
def _usable(row) -> bool:
    return isinstance(row, dict) and row.get("verdict") in (_TAILWIND, _HEADWIND, _NEUTRAL)


def _reading(key, name, sub, value, row) -> dict:
    return {"key": key, "name": name, "sub": sub, "value": value, "tone": _tone(row.get("verdict"))}


def _temperature(row):
    f = _facts(row)
    spx, ndx = finite(f.get("spx_pct")), finite(f.get("ndx_pct"))
    if spx is not None and ndx is not None:
        return _reading("temperature", "Temperature", "S&P 500 · Nasdaq-100",
                        f"{_signed(spx)}° · {_signed(ndx)}°", row)
    if spx is not None:
        return _reading("temperature", "Temperature", "S&P 500", f"{_signed(spx)}°", row)
    if ndx is not None:
        return _reading("temperature", "Temperature", "Nasdaq-100", f"{_signed(ndx)}°", row)
    return None


def _breadth_counts(row):
    f = _facts(row)
    adv, dec, flat = (finite(f.get(k)) for k in ("advancing", "declining", "flat"))
    if adv is None or dec is None:
        return None
    total = int(adv + dec + (flat or 0))
    return (int(adv), int(dec), total) if total > 0 else None


def _coverage(row):
    counts = _breadth_counts(row)
    if counts is None:
        return None
    adv, dec, total = counts
    if dec >= adv:
        return _reading("coverage", "Rain coverage", "Markets falling on our board",
                        f"{dec} of {total}", row)
    return _reading("coverage", "Sun coverage", "Markets rising on our board",
                    f"{adv} of {total}", row)


def _wind(row):
    f = _facts(row)
    vix, pct = finite(f.get("vix_level")), finite(f.get("vix_pct"))
    if vix is None:
        return None
    if pct is None:
        sub = "VIX"
    else:
        sub = f"VIX, {'up' if pct >= 0 else 'down'} {abs(pct):.1f}%"
    return _reading("wind", "Wind", sub, f"{vix:.1f}", row)


def _pressure(row):
    symbols = _facts(row).get("symbols")
    first = symbols[0] if isinstance(symbols, list) and symbols and isinstance(symbols[0], dict) else {}
    spot, flip = finite(first.get("spot")), finite(first.get("flip"))
    name = first.get("symbol")
    if spot is None or flip is None or not isinstance(name, str):
        return None
    value = "Below" if spot < flip else "Above" if spot > flip else "At the flip"
    return _reading("pressure", "Pressure", f"{name} {spot:.0f} vs the gamma flip {flip:.0f}",
                    value, row)


def _umbrellas(row):
    if not _flow_public(row):
        return None
    f = _facts(row)
    puts, calls = finite(f.get("puts")), finite(f.get("calls"))
    if puts is None or calls is None:
        return None
    return _reading("umbrellas", "Umbrellas : sunglasses", "Puts to calls in the option flow",
                    f"{int(puts)} : {int(calls)}", row)


def _shelter(row):
    f = _facts(row)
    tiles = {t.get("name"): finite(t.get("pct"))
             for t in (f.get("tiles") if isinstance(f.get("tiles"), list) else [])
             if isinstance(t, dict)}
    parts = []
    for name, word in (("TLT", "bonds"), ("HYG", "high yield")):
        pct = tiles.get(name)
        if pct is not None and pct != 0:
            parts.append(f"{word} {'up' if pct > 0 else 'down'}")
    sub = ", ".join(parts) if parts else "Bonds, the dollar and high yield"
    sub = sub[0].upper() + sub[1:]
    on, off = finite(f.get("risk_on")) or 0, finite(f.get("risk_off")) or 0
    value = "Risk-off" if off > on else "Risk-on" if on > off else "Mixed"
    return _reading("shelter", "Shelter", sub, value, row)


_READINGS = (("direction", _temperature), ("breadth", _coverage), ("volatility", _wind),
             ("structure", _pressure), ("flow", _umbrellas), ("cross_asset", _shelter))


def _readings(rows) -> list:
    out = []
    for key, fn in _READINGS:
        row = rows.get(key)
        if _usable(row):
            got = fn(row)
            if got is not None:
                out.append(got)
    return out


# ── the headline ─────────────────────────────────────────────────────────────
def _headline(sky_name, rows, cfg) -> str:
    if sky_name == "fog":
        return ""
    head = _FRONTS[sky_name]
    if sky_name != "overcast":
        counts = _breadth_counts(rows.get("breadth")) if _usable(rows.get("breadth")) else None
        if counts is None:
            coverage = "the board"
        else:
            adv, dec, total = counts
            n = dec if sky_name in _COLD_SKIES else adv
            coverage = f"all {total} markets" if n == total else f"{n} of {total} markets"
        head = f"{head} {coverage}"
    vol = rows.get("volatility")
    f = _facts(vol) if _usable(vol) else {}
    vix, pct = finite(f.get("vix_level")), finite(f.get("vix_pct"))
    if vix is None:
        return head + "."
    move = cfg["volatility"]["vix_move_pct"]
    if pct is not None and pct >= move:
        wind = f"the fear gauge gusts to {vix:.1f}"
    elif pct is not None and pct <= -move:
        wind = f"the fear gauge sinks to {vix:.1f}"
    else:
        wind = f"the fear gauge holds near {vix:.1f}"
    return f"{head}; {wind}."


# ── the payload ──────────────────────────────────────────────────────────────
def build(reading: dict | None, regime: dict | None, cfg: dict) -> dict:
    """The card's v1 payload for ``reading``, or ``HIDDEN`` when there is no
    reading to show the public. Never raises on a malformed reading's facts:
    a fact that is missing or not finite is simply absent."""
    if (not isinstance(reading, dict) or reading.get("enabled") is False
            or reading.get("public") is not True or cfg["weather"]["enabled"] is not True):
        return dict(HIDDEN)
    rows = _rows(reading)
    tally = _tally(rows)
    sky_name = sky(tally, regime, cfg["weather"])
    direction = _facts(rows.get("direction"))
    label = regime.get("label") if isinstance(regime, dict) else None
    return {
        "v": 1,
        "date": reading.get("date"), "slot": reading.get("slot"), "ts": reading.get("ts"),
        "final": reading.get("final") is True, "next_slot": reading.get("next_slot"),
        "slot_label": _slot_label(reading.get("slot")),
        "interval_min": cfg["interval_min"], "refetch_sec": cfg["weather"]["refetch_sec"],
        "sky": sky_name, "label": LABELS[sky_name],
        "temp": {"spx_pct": finite(direction.get("spx_pct")),
                 "ndx_pct": finite(direction.get("ndx_pct")),
                 "tone": _tone((rows.get("direction") or {}).get("verdict"))},
        "headline": _headline(sky_name, rows, cfg),
        "tally": tally, "tally_text": _tally_text(tally),
        "regime": label if isinstance(label, str) else None,
        "readings": _readings(rows),
    }
