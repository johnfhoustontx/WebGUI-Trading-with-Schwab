"""
gamma_infographic - Render the $SPX gamma desk-read as a self-contained HTML infographic.
Version: 1.1.0
Last Updated: 2026-06-17

Version 1.1.0 Changes:
- Added Vanna (VEX) content: vex_notional_usd + iv_trend resolve into a vanna lean
  (vanna rally / vol bleed / waterfall risk / neutral)
- Added Dealer Pinch content: pinch_strike + pinch_strength render a magnet read and,
  when present, a magnet marker on the level map / spine / number-line
- Pinch strike now participates in axis min/max so it scales into the level map
- All three styles render real Vanna/Pinch blocks when data is present, else "awaiting data"

Version 1.0.0 Changes:
- Initial implementation
- GammaRead dataclass capturing the OptionsAnalytics output (walls, flips, charm
  strikes, DEX flow, sentiment)
- Derives regime label, per-signal leans, pin/neg zones, and axis percentages
  automatically from the raw levels
- Three render styles: "terminal" (dark vertical ladder), "desknote" (light
  research note), "commandbar" (wide dark HUD)
- render_infographic() returns an HTML string; save_infographic() writes a file

Designed to slot into the OptionsAnalytics service: feed it the same GEX/DEX/Charm
values the service already computes and it returns a ready-to-serve HTML page,
replacing the prose explain.html.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict


#############################################
# INPUT MODEL
#############################################

@dataclass
class GammaRead:
    """All inputs needed to render a gamma infographic.

    Pass the raw values the OptionsAnalytics service already computes. Anything
    derived (regime, leans, zone bounds, axis %) is calculated downstream, not
    supplied here.

    DEX convention: dex_flow_usd is SIGNED. Positive = dealers must BUY (tailwind),
    negative = dealers must SELL (headwind). Pass dollars, e.g. 1.6e9.
    """
    spot: float
    call_wall: float          # GEX call wall (resistance)
    put_wall: float           # GEX put wall (support)
    gamma_flip: float         # GEX flip point
    charm_flip: float         # Charm flip strike
    charm_max_pos: float      # strike of max positive (+) charm decay
    charm_max_neg: float      # strike of max negative (-) charm decay
    dex_flow_usd: float       # signed net dealer hedge flow, in USD

    sentiment_score: int = 6          # 0-10
    sentiment_trend: str = "neutral"  # free text, e.g. "bull_trend"
    sentiment_confidence: int = 100   # 0-100

    symbol: str = "$SPX"
    vix: Optional[float] = None       # None -> "unknown" / "n/a"

    # --- Vanna (VEX) --- leave None -> card shows "awaiting data"
    # vex_notional_usd: signed net vanna exposure in USD. Positive VEX means IV
    # falling pushes dealer delta long (the 'vanna rally'); negative VEX is the
    # waterfall-risk regime where a vol spike forces dealers to chase.
    vex_notional_usd: Optional[float] = None
    iv_trend: Optional[str] = None    # "falling" | "rising" | "flat" -> resolves the vanna bias

    # --- Dealer Pinch (charm + vanna exhaustion) --- leave None -> "awaiting data"
    pinch_strike: Optional[float] = None    # the level the pinch magnets toward
    pinch_strength: Optional[float] = None  # 0-100 intensity of the pin

    vanna_available: bool = False     # legacy flag; presence is auto-derived from the fields above

    # Optional explicit override of the axis bounds. If None they are taken from
    # the min/max of all supplied levels.
    axis_min: Optional[float] = None
    axis_max: Optional[float] = None


#############################################
# DERIVATION
#############################################

def _fmt_px(x: float) -> str:
    """Format a price with thousands separators, no decimals."""
    return f"{x:,.0f}"


def _fmt_flow(usd: float) -> str:
    """'BUY $1.60B' / 'SELL $0.85B' from a signed USD figure."""
    direction = "BUY" if usd >= 0 else "SELL"
    billions = abs(usd) / 1e9
    if billions >= 1:
        return f"{direction} ${billions:.2f}B"
    millions = abs(usd) / 1e6
    return f"{direction} ${millions:.0f}M"


def _fmt_signed_b(usd: float) -> str:
    """'+$0.85B' / '\u2212$0.40B' from a signed USD figure (for VEX)."""
    sign = "+" if usd >= 0 else "\u2212"
    billions = abs(usd) / 1e9
    if billions >= 1:
        return f"{sign}${billions:.2f}B"
    millions = abs(usd) / 1e6
    return f"{sign}${millions:.0f}M"


def _sentiment_bars(score: int, on_class: str = "on", total: int = 10) -> str:
    """Build the 10-segment sentiment meter as <i> tags."""
    score = max(0, min(total, int(round(score))))
    return "".join(
        f'<i class="{on_class}"></i>' if i < score else "<i></i>"
        for i in range(total)
    )


def _derive(data: GammaRead) -> Dict:
    """Compute everything the templates need from the raw GammaRead."""
    levels = [
        data.charm_max_pos, data.charm_max_neg,
        data.call_wall, data.put_wall,
        data.gamma_flip, data.charm_flip, data.spot,
    ]
    if data.pinch_strike is not None:
        levels.append(data.pinch_strike)
    lo = data.axis_min if data.axis_min is not None else min(levels)
    hi = data.axis_max if data.axis_max is not None else max(levels)
    span = (hi - lo) or 1.0

    def pct(x: float) -> float:
        return round((x - lo) / span * 100, 2)

    long_gamma = data.spot > data.gamma_flip
    charm_up = data.spot >= data.charm_flip
    dex_buy = data.dex_flow_usd >= 0

    # per-signal lean -> (css_class, label_text)
    gex_lean = ("range", "\u25c7 Pin / fade edges") if long_gamma \
        else ("down", "\u25bc Trend / breakout")
    charm_lean = ("up", "\u25b2 Drift up") if charm_up \
        else ("down", "\u25bc Drift down")
    dex_lean = ("up", "\u25b2 Tailwind") if dex_buy \
        else ("down", "\u25bc Headwind")

    regime_label = "Long \u03b3 \u00b7 Pinned" if long_gamma else "Short \u03b3 \u00b7 Trending"
    regime_class = "sup" if long_gamma else "res"

    # pin zone spans put wall -> call wall; neg-gamma zone is below the flip
    pin_lo, pin_hi = sorted([pct(data.put_wall), pct(data.call_wall)])

    drift_word = "up into the close" if charm_up else "down into the close"

    # Per-signal reader-first reads (what YOU should do, not what dealers are doing).
    # Precomputed here \u2014 normal strings, so em-dashes/apostrophes are unconstrained
    # (unlike inside the big terminal f-string body, where they'd be f-expr backslashes).
    if long_gamma:
        gex_read = ('You\u2019re <b>above the flip</b>, so trade a <b>pinned</b> tape. Moves get '
                    'dampened \u2014 fade the edges (sell into the call wall, buy the put wall) '
                    'and expect chop, not breakouts.')
    else:
        gex_read = ('You\u2019re <b>below the flip</b>, so trade a <b>trending</b> tape. Moves '
                    'amplify \u2014 go with the trend, give it room, and don\u2019t fade strength.')

    charm_read = (f'Expect price to <b>drift {drift_word}</b> (charm pull) \u2014 <b>lean '
                  f'{"up" if charm_up else "down"}</b>. Strongest in the last hour and on '
                  f'OPEX Thu\u2013Fri.')

    flow_amt = _fmt_flow(data.dex_flow_usd).split('$')[-1]
    if dex_buy:
        dex_read = (f'A standing <b>bid under</b> the tape (${flow_amt} of hedging) \u2014 '
                    f'lean long and buy dips.')
    else:
        dex_read = (f'A standing <b>offer over</b> the tape (${flow_amt} of hedging) \u2014 '
                    f'respect the supply; sell rallies.')

    net_read = (
        f'All three signals line up \u2014 trade it as a '
        f'<b>{"range-bound" if long_gamma else "trending"} tape drifting '
        f'{"higher" if charm_up else "lower"}</b>. '
        + (f'Fade the edges: sell into <b>{_fmt_px(data.call_wall)}</b>, buy toward '
           f'<b>{_fmt_px(data.put_wall)}</b>, and lean with the drift. ' if long_gamma
           else f'Go with the move and give it room past <b>{_fmt_px(data.put_wall)}</b> / '
                f'<b>{_fmt_px(data.call_wall)}</b> \u2014 don\u2019t fade strength. ')
        + f'A <b>{"standing bid" if dex_buy else "standing offer"}</b> '
          f'({_fmt_flow(data.dex_flow_usd)} of hedging) sits '
          f'{"under" if dex_buy else "over"} you. '
          f'<span class="risk">Reassess if {_fmt_px(data.gamma_flip)} fails on volume '
          f'\u2014 the regime flips.</span>'
    )

    # --- Vanna (VEX) ---
    vex = data.vex_notional_usd
    vanna_present = vex is not None
    iv = (data.iv_trend or "flat").lower()
    if vanna_present:
        vex_pos = vex >= 0
        if vex_pos and iv == "falling":
            vanna_lean = ("up", "\u25b2 Vanna rally")
            vanna_read = ("Falling IV is a tailwind here (positive VEX) \u2014 expect a grind "
                          "higher; lean long and buy dips.")
        elif vex_pos and iv == "rising":
            vanna_lean = ("down", "\u25bc Vol bleed")
            vanna_read = ("Rising IV is a drag here (positive VEX) \u2014 fade rallies; expect "
                          "a slow bleed as vol firms.")
        elif not vex_pos:
            vanna_lean = ("down", "\u25bc Waterfall risk")
            vanna_read = ("Negative VEX \u2014 a vol spike accelerates declines. Tighten stops, "
                          "don\u2019t buy falling knives, and respect the downside.")
        else:  # positive VEX, flat IV
            vanna_lean = ("range", "\u25c7 Neutral drift")
            vanna_read = "Positive VEX, IV flat \u2014 little vol-driven push; trade the levels, not vol."
        vex_str = _fmt_signed_b(vex)
        iv_label = iv
    else:
        vanna_lean = ("pend", "\u25cb Awaiting data")
        vanna_read = ("No Vanna/drift data yet \u2014 matters most around IV shocks "
                      "(FOMC / CPI / earnings).")
        vex_str = "\u2014"
        iv_label = "n/a"

    # --- Dealer Pinch (charm + vanna exhaustion) ---
    pinch_present = data.pinch_strike is not None
    p_pinch = pct(data.pinch_strike) if pinch_present else None
    if pinch_present:
        strg = data.pinch_strength if data.pinch_strength is not None else 0
        pstrength_label = "strong" if strg >= 66 else "moderate" if strg >= 33 else "weak"
        pinch_strike_str = _fmt_px(data.pinch_strike)
        pinch_strength_str = f"{strg:.0f}%"
        pinch_read = (f"Charm + vanna pin toward <b>{pinch_strike_str}</b> \u2014 a "
                      f"{pstrength_label} magnet that exhausts into the last hour.")
    else:
        pinch_strike_str = "\u2014"
        pinch_strength_str = "\u2014"
        pinch_read = "No pinch data yet \u2014 waiting for the first market-data fetch."

    vix_str = "n/a" if data.vix is None else f"{data.vix:.1f}"

    return dict(
        symbol_raw=data.symbol.lstrip("$"),
        spot=_fmt_px(data.spot),
        call_wall=_fmt_px(data.call_wall),
        put_wall=_fmt_px(data.put_wall),
        gamma_flip=_fmt_px(data.gamma_flip),
        charm_flip=_fmt_px(data.charm_flip),
        charm_max_pos=_fmt_px(data.charm_max_pos),
        charm_max_neg=_fmt_px(data.charm_max_neg),
        axis_lo=_fmt_px(lo),
        axis_hi=_fmt_px(hi),
        flow=_fmt_flow(data.dex_flow_usd),
        flow_class="sup" if dex_buy else "res",
        p_spot=pct(data.spot),
        p_call=pct(data.call_wall),
        p_put=pct(data.put_wall),
        p_flip=pct(data.gamma_flip),
        p_cmax_pos=pct(data.charm_max_pos),
        p_cmax_neg=pct(data.charm_max_neg),
        pin_lo=pin_lo,
        pin_width=round(pin_hi - pin_lo, 2),
        neg_width=pct(data.gamma_flip),
        regime_label=regime_label,
        regime_class=regime_class,
        gex_lean_class=gex_lean[0], gex_lean_text=gex_lean[1],
        charm_lean_class=charm_lean[0], charm_lean_text=charm_lean[1],
        dex_lean_class=dex_lean[0], dex_lean_text=dex_lean[1],
        drift_word=drift_word,
        gex_read=gex_read,
        charm_read=charm_read,
        dex_read=dex_read,
        sentiment_score=int(data.sentiment_score),
        sentiment_trend=data.sentiment_trend.replace("_", " "),
        sentiment_trend_bull="bull" in data.sentiment_trend.lower(),
        sentiment_conf=int(data.sentiment_confidence),
        vix_str=vix_str,
        vanna_available=data.vanna_available,
        vanna_present=vanna_present,
        pinch_present=pinch_present,
        vex_str=vex_str,
        iv_label=iv_label,
        vanna_lean_class=vanna_lean[0], vanna_lean_text=vanna_lean[1],
        vanna_read=vanna_read,
        pinch_strike_str=pinch_strike_str,
        pinch_strength_str=pinch_strength_str,
        pinch_read=pinch_read,
        p_pinch=p_pinch,
        net_read=net_read,
    )


#############################################
# CSS BLOCKS (static per style)
#############################################

_CSS_TERMINAL = """
:root{--ink:#0c0f15;--ink-2:#11151d;--card:#161b25;--card-2:#1b212c;--line:#262d3a;--line-soft:#1f2530;
--txt:#e9edf3;--txt-mid:#a3acbd;--txt-dim:#6b7589;--res:#e8635a;--sup:#37c08a;--spot:#f5b942;--flip:#9d8cf2;--pinch:#56c4d6;--pend:#525c6e;
--disp:'Space Grotesk',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace;--body:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box;}html,body{margin:0;}
body{background:radial-gradient(1200px 600px at 78% -10%,rgba(157,140,242,.10),transparent 60%),radial-gradient(900px 500px at -5% 110%,rgba(55,192,138,.07),transparent 55%),var(--ink);
color:var(--txt);font-family:var(--body);-webkit-font-smoothing:antialiased;padding:28px 20px 40px;display:flex;justify-content:center;}
.wrap{width:100%;max-width:1080px;}
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;flex-wrap:wrap;padding-bottom:18px;margin-bottom:22px;border-bottom:1px solid var(--line);}
.id{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}
.ticker{font-family:var(--disp);font-weight:700;font-size:2rem;letter-spacing:-.5px;color:#fff;line-height:1;}
.ticker em{color:var(--spot);font-style:normal;}
.kicker{font-family:var(--mono);font-size:.7rem;letter-spacing:2.5px;text-transform:uppercase;color:var(--txt-dim);}
.spot-box{display:flex;align-items:baseline;gap:8px;}
.spot-box .lab{font-family:var(--mono);font-size:.66rem;letter-spacing:2px;text-transform:uppercase;color:var(--txt-dim);}
.spot-box .val{font-family:var(--mono);font-weight:600;font-size:1.25rem;color:var(--spot);}
.head-right{display:flex;flex-direction:column;align-items:flex-end;gap:10px;}
.regime{display:inline-flex;align-items:center;gap:9px;padding:8px 14px;border-radius:999px;
background:linear-gradient(180deg,rgba(55,192,138,.16),rgba(55,192,138,.06));border:1px solid rgba(55,192,138,.35);
font-family:var(--mono);font-weight:600;font-size:.78rem;letter-spacing:1px;color:#7ee0b6;text-transform:uppercase;}
.regime.res{background:linear-gradient(180deg,rgba(232,99,90,.16),rgba(232,99,90,.06));border-color:rgba(232,99,90,.35);color:#f0938b;}
.regime .dot{width:8px;height:8px;border-radius:50%;background:var(--sup);box-shadow:0 0 10px var(--sup);}
.regime.res .dot{background:var(--res);box-shadow:0 0 10px var(--res);}
.meta{font-family:var(--mono);font-size:.72rem;color:var(--txt-dim);letter-spacing:.5px;}
.meta b{color:var(--txt-mid);font-weight:600;}
.grid{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:stretch;}
@media (max-width:760px){.grid{grid-template-columns:1fr;}}
.ladder-card{background:linear-gradient(180deg,var(--card),var(--ink-2));border:1px solid var(--line);border-radius:16px;padding:18px 18px 16px;position:relative;overflow:hidden;display:flex;flex-direction:column;}
.ladder-card .ttl{font-family:var(--mono);font-size:.7rem;letter-spacing:2px;text-transform:uppercase;color:var(--txt-mid);margin-bottom:4px;}
.ladder-card .sub{font-size:.74rem;color:var(--txt-dim);margin-bottom:14px;}
.ladder{position:relative;flex:1;min-height:520px;margin:0 4px;}
.axis{position:absolute;left:50%;top:0;bottom:0;width:2px;transform:translateX(-50%);background:linear-gradient(180deg,transparent,var(--line) 8%,var(--line) 92%,transparent);}
.zone{position:absolute;left:8px;right:8px;border-radius:8px;}
.zone-pin{background:linear-gradient(90deg,rgba(245,185,66,.05),rgba(245,185,66,.10),rgba(245,185,66,.05));border:1px dashed rgba(245,185,66,.22);}
.zone-neg{background:linear-gradient(180deg,rgba(232,99,90,.02),rgba(232,99,90,.12));}
.zone-lab{position:absolute;font-family:var(--mono);font-size:.6rem;letter-spacing:1.5px;text-transform:uppercase;opacity:.8;}
.lv{position:absolute;left:0;right:0;height:0;}
.lv .bar{position:absolute;left:8px;right:8px;height:1px;background:var(--line);}
.lv.major .bar{height:2px;}
.lv .px{position:absolute;font-family:var(--mono);font-weight:600;font-size:.82rem;top:-9px;left:10px;}
.lv .tag{position:absolute;font-family:var(--mono);font-size:.6rem;letter-spacing:1px;text-transform:uppercase;top:-7px;right:10px;}
.lv.call .bar{background:var(--res);box-shadow:0 0 14px rgba(232,99,90,.4);}
.lv.call .px,.lv.call .tag{color:var(--res);}
.lv.put .bar{background:var(--sup);box-shadow:0 0 14px rgba(55,192,138,.4);}
.lv.put .px,.lv.put .tag{color:var(--sup);}
.lv.flip .bar{background:transparent;border-top:2px dashed var(--flip);}
.lv.flip .px,.lv.flip .tag{color:var(--flip);}
.lv.pinch .bar{background:transparent;border-top:2px dotted var(--pinch);}
.lv.pinch .px,.lv.pinch .tag{color:var(--pinch);}
.lv.minor .bar{background:var(--line-soft);}
.lv.minor .px{color:var(--txt-dim);font-weight:400;font-size:.74rem;}
.lv.minor .tag{color:var(--txt-dim);}
.spot-mark{position:absolute;left:50%;transform:translate(-50%,-50%);display:flex;align-items:center;gap:7px;background:var(--spot);color:#1a1304;font-family:var(--mono);font-weight:700;font-size:.82rem;padding:5px 11px;border-radius:999px;box-shadow:0 0 0 4px rgba(245,185,66,.16),0 6px 18px rgba(245,185,66,.30);white-space:nowrap;z-index:3;}
.spot-mark::before{content:"";width:6px;height:6px;border-radius:50%;background:#1a1304;animation:pulse 2.4s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.3;}}
.signals{display:grid;grid-template-rows:repeat(3,1fr) auto;gap:14px;}
.sig{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px;position:relative;border-left:3px solid var(--line);}
.sig.up{border-left-color:var(--sup);}.sig.range{border-left-color:var(--spot);}.sig.down{border-left-color:var(--res);}.sig.pending{border-left-color:var(--pend);opacity:.72;}
.sig-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px;}
.sig-name{font-family:var(--disp);font-weight:600;font-size:1.02rem;color:#fff;letter-spacing:.2px;}
.sig-name span{color:var(--txt-dim);font-family:var(--mono);font-size:.7rem;font-weight:400;letter-spacing:1px;margin-left:6px;}
.lean{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:.66rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;padding:4px 9px;border-radius:6px;}
.lean.up{color:#7ee0b6;background:rgba(55,192,138,.12);}.lean.range{color:#f3c969;background:rgba(245,185,66,.12);}.lean.down{color:#f0938b;background:rgba(232,99,90,.12);}.lean.pending{color:var(--txt-dim);background:rgba(82,92,110,.16);}
.sig-read{font-size:.84rem;color:var(--txt-mid);line-height:1.5;margin:0 0 10px;}
.sig-read b{color:var(--txt);font-weight:600;}
.sig-data{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:10px;}
.dpt{display:flex;flex-direction:column;gap:2px;}
.dpt .k{font-family:var(--mono);font-size:.58rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--txt-dim);}
.dpt .v{font-family:var(--mono);font-weight:600;font-size:.9rem;color:var(--txt);}
.dpt .v.res{color:var(--res);}.dpt .v.sup{color:var(--sup);}.dpt .v.flip{color:var(--flip);}
.play{font-size:.78rem;color:var(--txt-mid);line-height:1.45;padding-top:9px;border-top:1px solid var(--line-soft);}
.play .t{font-family:var(--mono);font-size:.58rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--txt-dim);}
.play .risk{color:var(--res);opacity:.9;}
.senti{background:linear-gradient(90deg,var(--card-2),var(--card));border:1px solid var(--line);border-radius:14px;padding:14px 17px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;}
.gauge{display:flex;align-items:center;gap:11px;}
.gauge .num{font-family:var(--mono);font-weight:700;font-size:1.35rem;color:var(--spot);}
.gauge .num small{font-size:.8rem;color:var(--txt-dim);font-weight:500;}
.bars{display:flex;gap:3px;}.bars i{width:6px;height:20px;border-radius:2px;background:var(--line);display:block;}.bars i.on{background:var(--spot);}
.senti .desc{font-size:.8rem;color:var(--txt-mid);line-height:1.45;flex:1;min-width:220px;}
.senti .desc b{color:var(--txt);}
.chips{display:flex;gap:8px;flex-wrap:wrap;}
.chip{font-family:var(--mono);font-size:.62rem;letter-spacing:1px;text-transform:uppercase;padding:4px 9px;border-radius:6px;background:rgba(82,92,110,.16);color:var(--txt-mid);}
.chip.bull{color:#7ee0b6;background:rgba(55,192,138,.12);}
.synth{margin-top:18px;padding:16px 18px;border-radius:14px;background:linear-gradient(180deg,rgba(245,185,66,.06),transparent);border:1px solid rgba(245,185,66,.2);display:flex;gap:14px;align-items:flex-start;}
.synth .badge{font-family:var(--mono);font-size:.6rem;letter-spacing:2px;text-transform:uppercase;color:var(--spot);border:1px solid rgba(245,185,66,.4);border-radius:6px;padding:4px 8px;white-space:nowrap;margin-top:2px;}
.synth p{margin:0;font-size:.86rem;color:var(--txt-mid);line-height:1.55;}
.synth p b{color:var(--txt);}.synth p .risk{color:var(--res);}
.foot{margin-top:18px;text-align:center;font-family:var(--mono);font-size:.66rem;letter-spacing:1px;color:var(--txt-dim);}
.foot span{color:var(--spot);}
@media (prefers-reduced-motion:reduce){.spot-mark::before{animation:none;}}
/* horizontal Level Map (across the top) + signal grid below */
.lmap-card{background:linear-gradient(180deg,var(--card),var(--ink-2));border:1px solid var(--line);border-radius:16px;padding:18px 22px 14px;margin-bottom:18px;}
.lmap-card .ttl{font-family:var(--mono);font-size:.7rem;letter-spacing:2px;text-transform:uppercase;color:var(--txt-mid);margin-bottom:2px;}
.lmap-card .sub{font-size:.74rem;color:var(--txt-dim);margin-bottom:36px;}
.lmap{position:relative;height:104px;margin:0 12px;}
.lmap-axis{position:absolute;left:0;right:0;top:52px;height:2px;background:linear-gradient(90deg,transparent,var(--line) 4%,var(--line) 96%,transparent);}
.lzone-pin{position:absolute;top:46px;height:14px;border-radius:3px;background:linear-gradient(90deg,rgba(245,185,66,.05),rgba(245,185,66,.12),rgba(245,185,66,.05));border-left:1px dashed rgba(245,185,66,.25);border-right:1px dashed rgba(245,185,66,.25);}
.lzone-neg{position:absolute;top:46px;height:14px;border-radius:3px 0 0 3px;background:linear-gradient(90deg,rgba(232,99,90,.20),transparent);}
.lzlab{position:absolute;font-family:var(--mono);font-size:.58rem;letter-spacing:1.5px;text-transform:uppercase;top:68px;opacity:.85;}
.lm{position:absolute;top:53px;transform:translateX(-50%);}
.lm .stem{position:absolute;left:50%;width:2px;transform:translateX(-50%);background:var(--line);}
.lm.up .stem{bottom:0;height:24px;}.lm.dn .stem{top:0;height:24px;}
.lm .lab{position:absolute;left:50%;transform:translateX(-50%);white-space:nowrap;text-align:center;}
.lm.up .lab{bottom:28px;}.lm.dn .lab{top:28px;}
.lm .px{font-family:var(--mono);font-weight:600;font-size:.82rem;display:block;}
.lm .nm{font-family:var(--mono);font-size:.56rem;letter-spacing:1px;text-transform:uppercase;color:var(--txt-dim);display:block;}
.lm.call .px,.lm.call .nm{color:var(--res);}.lm.call .stem{background:var(--res);box-shadow:0 0 10px rgba(232,99,90,.4);}
.lm.put .px,.lm.put .nm{color:var(--sup);}.lm.put .stem{background:var(--sup);box-shadow:0 0 10px rgba(55,192,138,.4);}
.lm.flip .px,.lm.flip .nm{color:var(--flip);}.lm.flip .stem{background:var(--flip);}
.lm.pinch .px,.lm.pinch .nm{color:var(--pinch);}.lm.pinch .stem{background:var(--pinch);}
.lm.minor .px{color:var(--txt-dim);font-weight:500;font-size:.74rem;}.lm.minor .nm{color:var(--txt-dim);}.lm.minor .stem{height:16px !important;background:var(--line-soft);}
.lspot{position:absolute;top:53px;transform:translate(-50%,-50%);z-index:5;}
.lspot .ring{width:16px;height:16px;border-radius:50%;background:var(--spot);box-shadow:0 0 0 4px rgba(245,185,66,.16),0 0 14px rgba(245,185,66,.5);margin:0 auto;}
.lspot .lab{position:absolute;left:50%;top:12px;transform:translateX(-50%);font-family:var(--mono);font-weight:700;font-size:.78rem;color:var(--spot);background:var(--ink);padding:0 4px;white-space:nowrap;}
.sig-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;}
@media (max-width:760px){.sig-grid{grid-template-columns:1fr;}}
"""

_CSS_DESKNOTE = """
:root{--paper:#f4f2ec;--paper-2:#eceadf;--ink:#1c1b18;--ink-2:#3a3833;--mid:#6f6b61;--dim:#9a958a;
--rule:#d8d3c6;--rule-bold:#b9b2a1;--res:#a8392b;--sup:#1f6b4c;--flip:#5a4fa0;--pinch:#2f7d8c;--gold:#9c7b2e;
--serif:'Spectral',Georgia,serif;--mono:'IBM Plex Mono',ui-monospace,monospace;--sans:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box;}
body{margin:0;background:#dad6cb;padding:30px 16px;font-family:var(--sans);color:var(--ink);-webkit-font-smoothing:antialiased;display:flex;justify-content:center;}
.sheet{width:100%;max-width:940px;background:var(--paper);background-image:radial-gradient(circle at 50% 0,rgba(0,0,0,.015),transparent 70%);border:1px solid var(--rule-bold);padding:34px 40px 30px;box-shadow:0 20px 60px rgba(0,0,0,.28);}
.mast{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--ink);padding-bottom:9px;}
.mast .name{font-family:var(--serif);font-weight:700;font-size:1.5rem;letter-spacing:-.3px;line-height:1;}
.mast .name b{color:var(--gold);}
.mast .dline{font-family:var(--mono);font-size:.66rem;letter-spacing:1.2px;text-transform:uppercase;color:var(--mid);text-align:right;line-height:1.7;}
.mast .dline b{color:var(--ink);}
.rule-pair{border-top:1px solid var(--rule-bold);border-bottom:1px solid var(--rule-bold);height:4px;margin-top:2px;}
.lede{margin:20px 0 6px;display:flex;gap:18px;align-items:flex-start;}
.lede .verdict{font-family:var(--mono);font-size:.62rem;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--sup);border:1px solid var(--sup);border-radius:3px;padding:5px 9px;white-space:nowrap;margin-top:4px;}
.lede .verdict.res{color:var(--res);border-color:var(--res);}
.lede h1{font-family:var(--serif);font-weight:600;font-size:1.55rem;line-height:1.25;margin:0;letter-spacing:-.2px;}
.lede h1 em{font-style:italic;color:var(--gold);}
.axis-wrap{margin:26px 0 8px;}
.axis-cap{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);font-size:.64rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--mid);margin-bottom:30px;}
.axis-cap .spot{color:var(--ink);font-weight:600;font-size:.78rem;letter-spacing:.5px;}
.axis-cap .spot b{color:var(--gold);}
.number-line{position:relative;height:120px;margin:0 8px;}
.nl-base{position:absolute;left:0;right:0;top:60px;height:2px;background:var(--ink);}
.nl-pin{position:absolute;top:54px;height:14px;border-radius:2px;background:repeating-linear-gradient(45deg,rgba(156,123,46,.16),rgba(156,123,46,.16) 4px,transparent 4px,transparent 8px);border-left:1px dashed var(--gold);border-right:1px dashed var(--gold);}
.nl-neg{position:absolute;top:54px;height:14px;background:linear-gradient(90deg,rgba(168,57,43,.18),transparent);}
.tick{position:absolute;top:54px;width:1px;height:14px;background:var(--rule-bold);}
.mark{position:absolute;top:60px;transform:translateX(-50%);}
.mark .stem{position:absolute;left:50%;width:2px;background:var(--ink-2);transform:translateX(-50%);}
.mark.up .stem{bottom:0;height:26px;}.mark.dn .stem{top:0;height:26px;}
.mark .lab{position:absolute;left:50%;transform:translateX(-50%);text-align:center;white-space:nowrap;}
.mark.up .lab{bottom:30px;}.mark.dn .lab{top:30px;}
.mark .px{font-family:var(--mono);font-weight:600;font-size:.84rem;display:block;}
.mark .nm{font-family:var(--mono);font-size:.58rem;letter-spacing:1px;text-transform:uppercase;color:var(--mid);display:block;}
.mark.call .px,.mark.call .nm{color:var(--res);}.mark.call .stem{background:var(--res);}
.mark.put .px,.mark.put .nm{color:var(--sup);}.mark.put .stem{background:var(--sup);}
.mark.flip .px,.mark.flip .nm{color:var(--flip);}.mark.flip .stem{background:var(--flip);}
.mark.pinch .px,.mark.pinch .nm{color:var(--pinch);}.mark.pinch .stem{background:var(--pinch);}
.mark.minor .px{color:var(--mid);font-weight:500;font-size:.74rem;}.mark.minor .nm{color:var(--dim);}.mark.minor .stem{height:18px !important;background:var(--rule-bold);}
.spot-pin{position:absolute;top:60px;transform:translate(-50%,-50%);z-index:4;}
.spot-pin .dia{width:15px;height:15px;background:var(--gold);transform:rotate(45deg);border:2px solid var(--paper);box-shadow:0 2px 6px rgba(0,0,0,.3);margin:0 auto;}
.spot-pin .lab{position:absolute;left:50%;top:16px;transform:translateX(-50%);font-family:var(--mono);font-weight:700;font-size:.8rem;color:var(--ink);white-space:nowrap;background:var(--paper);padding:1px 4px;}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin-top:34px;border-top:2px solid var(--ink);}
@media (max-width:640px){.cols{grid-template-columns:1fr;}}
.col{padding:16px 18px 6px;border-right:1px solid var(--rule);}
.col:last-child{border-right:0;}
@media (max-width:640px){.col{border-right:0;border-bottom:1px solid var(--rule);}}
.col-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:9px;}
.col-h .nm{font-family:var(--serif);font-weight:600;font-size:1.08rem;}
.col-h .gk{font-family:var(--mono);font-size:.58rem;letter-spacing:1px;color:var(--dim);text-transform:uppercase;}
.lean{font-family:var(--mono);font-size:.6rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;}
.lean.up{color:var(--sup);}.lean.range{color:var(--gold);}.lean.down{color:var(--res);}.lean.pend{color:var(--dim);}
.col p{font-size:.81rem;color:var(--ink-2);line-height:1.5;margin:0 0 10px;}
.col p b{color:var(--ink);}
.kv{border-top:1px solid var(--rule);padding-top:8px;margin-bottom:9px;}
.kv .row{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.78rem;padding:2px 0;}
.kv .row .k{color:var(--mid);font-size:.7rem;letter-spacing:.5px;}
.kv .row .v{font-weight:600;}
.kv .row .v.res{color:var(--res);}.kv .row .v.sup{color:var(--sup);}.kv .row .v.flip{color:var(--flip);}
.col .play{font-size:.74rem;color:var(--mid);line-height:1.45;border-top:1px solid var(--rule);padding-top:8px;}
.col .play b{color:var(--ink-2);}.col .play .risk{color:var(--res);}
.vp{display:grid;grid-template-columns:1fr 1fr;border-top:2px solid var(--ink);}
.vp .vp-col{padding:14px 18px 6px;border-right:1px solid var(--rule);}
.vp .vp-col:last-child{border-right:0;}
@media (max-width:640px){.vp{grid-template-columns:1fr;}.vp .vp-col{border-right:0;border-bottom:1px solid var(--rule);}}
.vp .vp-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:5px;}
.vp .vp-h .nm{font-family:var(--serif);font-weight:600;font-size:1.02rem;}
.vp .vp-h .gk{font-family:var(--mono);font-size:.56rem;letter-spacing:1px;color:var(--dim);text-transform:uppercase;}
.vp .vp-lean{font-family:var(--mono);font-size:.58rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;}
.vp .vp-lean.up{color:var(--sup);}.vp .vp-lean.down{color:var(--res);}.vp .vp-lean.range{color:var(--gold);}.vp .vp-lean.pend{color:var(--dim);}.vp .vp-lean.pinch{color:var(--pinch);}
.vp p{font-size:.78rem;color:var(--ink-2);line-height:1.45;margin:0 0 7px;}.vp p b{color:var(--ink);}
.vp .vp-data{font-family:var(--mono);font-size:.72rem;color:var(--mid);border-top:1px solid var(--rule);padding-top:6px;}
.vp .vp-data b{color:var(--ink);}
.band{border-top:2px solid var(--ink);border-bottom:1px solid var(--rule-bold);display:flex;align-items:center;gap:20px;padding:13px 4px;flex-wrap:wrap;}
.band .senti{display:flex;align-items:center;gap:10px;}
.band .score{font-family:var(--serif);font-weight:700;font-size:1.5rem;}
.band .score small{font-size:.8rem;color:var(--mid);font-weight:400;}
.ticks{display:flex;gap:2px;}.ticks i{width:7px;height:16px;background:var(--rule-bold);display:block;}.ticks i.on{background:var(--gold);}
.band .txt{font-size:.8rem;color:var(--ink-2);flex:1;min-width:200px;line-height:1.45;}
.band .txt b{color:var(--ink);}
.band .tags{font-family:var(--mono);font-size:.62rem;letter-spacing:1px;text-transform:uppercase;color:var(--mid);}
.band .tags b{color:var(--sup);}
.net{margin-top:14px;font-family:var(--serif);font-size:.95rem;line-height:1.55;color:var(--ink-2);}
.net::first-letter{font-weight:700;font-size:1.7em;float:left;line-height:.8;padding:3px 8px 0 0;color:var(--gold);}
.net b{color:var(--ink);font-weight:600;}.net .risk{color:var(--res);}
.colophon{margin-top:16px;border-top:1px solid var(--rule);padding-top:8px;font-family:var(--mono);font-size:.6rem;letter-spacing:1px;text-transform:uppercase;color:var(--dim);display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;}
"""

_CSS_COMMANDBAR = """
:root{--bg:#080b12;--panel:#0f1421;--panel-2:#141b2c;--line:#1f2a3f;--line-soft:#18202f;--cyan:#3fd0e0;
--txt:#e7edf6;--mid:#94a2bb;--dim:#5d6a82;--res:#ff5d6c;--sup:#2fd49a;--spot:#ffc24b;--flip:#b29bff;
--disp:'Chakra Petch',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace;--sans:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box;}
body{margin:0;background:radial-gradient(900px 400px at 12% -10%,rgba(63,208,224,.10),transparent 60%),radial-gradient(800px 360px at 95% 120%,rgba(255,194,75,.06),transparent 55%),var(--bg);color:var(--txt);font-family:var(--sans);-webkit-font-smoothing:antialiased;padding:24px 18px;display:flex;justify-content:center;}
.bar{width:100%;max-width:1180px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,var(--panel),var(--bg));overflow:hidden;}
.topedge{height:3px;background:linear-gradient(90deg,var(--cyan),transparent 40%);}
.hdr{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 22px;border-bottom:1px solid var(--line);flex-wrap:wrap;}
.hdr-l{display:flex;align-items:center;gap:18px;flex-wrap:wrap;}
.tk{display:flex;align-items:baseline;gap:10px;}
.tk .t{font-family:var(--disp);font-weight:700;font-size:1.5rem;letter-spacing:.5px;}
.tk .t b{color:var(--spot);}
.tk .p{font-family:var(--mono);font-weight:600;font-size:1.05rem;color:var(--spot);}
.tk .p small{color:var(--dim);font-size:.62rem;letter-spacing:1.5px;text-transform:uppercase;margin-right:5px;}
.verdict{display:inline-flex;align-items:center;gap:9px;padding:7px 14px;border-radius:7px;background:rgba(47,212,154,.1);border:1px solid rgba(47,212,154,.35);font-family:var(--mono);font-weight:600;font-size:.74rem;letter-spacing:1.5px;text-transform:uppercase;color:#76e7bf;}
.verdict.res{background:rgba(255,93,108,.1);border-color:rgba(255,93,108,.35);color:#ff9aa4;}
.verdict .d{width:7px;height:7px;border-radius:50%;background:var(--sup);box-shadow:0 0 9px var(--sup);animation:bl 2.2s ease-in-out infinite;}
.verdict.res .d{background:var(--res);box-shadow:0 0 9px var(--res);}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.35}}
.hdr-r{display:flex;align-items:center;gap:18px;}
.sgauge{display:flex;align-items:center;gap:9px;}
.sgauge .n{font-family:var(--disp);font-weight:700;font-size:1.15rem;color:var(--spot);}
.sgauge .n small{font-size:.7rem;color:var(--dim);}
.sgauge .bars{display:flex;gap:2px;}.sgauge .bars i{width:5px;height:16px;border-radius:1px;background:var(--line);}.sgauge .bars i.on{background:var(--spot);}
.sgauge .lab{font-family:var(--mono);font-size:.6rem;letter-spacing:1px;text-transform:uppercase;color:var(--dim);}
.vix{font-family:var(--mono);font-size:.66rem;letter-spacing:1px;text-transform:uppercase;color:var(--dim);}.vix b{color:var(--mid);}
.spine-wrap{padding:38px 40px 30px;border-bottom:1px solid var(--line);background:radial-gradient(700px 200px at 25% 50%,rgba(255,194,75,.04),transparent 60%);}
.spine-cap{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.6rem;letter-spacing:2px;text-transform:uppercase;color:var(--dim);margin-bottom:46px;}
.spine{position:relative;height:96px;margin:0 6px;}
.sp-base{position:absolute;left:0;right:0;top:48px;height:2px;background:linear-gradient(90deg,transparent,var(--line) 4%,var(--line) 96%,transparent);}
.sp-pin{position:absolute;top:42px;height:14px;border-radius:3px;background:linear-gradient(90deg,rgba(255,194,75,.06),rgba(255,194,75,.14),rgba(255,194,75,.06));border-top:1px dashed rgba(255,194,75,.3);border-bottom:1px dashed rgba(255,194,75,.3);}
.sp-neg{position:absolute;top:42px;height:14px;border-radius:3px 0 0 3px;background:linear-gradient(90deg,rgba(255,93,108,.22),transparent);}
.sp-zlab{position:absolute;font-family:var(--mono);font-size:.56rem;letter-spacing:1.5px;text-transform:uppercase;top:66px;}
.m{position:absolute;top:49px;transform:translateX(-50%);}
.m .stem{position:absolute;left:50%;width:2px;transform:translateX(-50%);}
.m.up .stem{bottom:0;height:22px;}.m.dn .stem{top:0;height:22px;}
.m .lab{position:absolute;left:50%;transform:translateX(-50%);white-space:nowrap;text-align:center;}
.m.up .lab{bottom:26px;}.m.dn .lab{top:26px;}
.m .px{font-family:var(--mono);font-weight:600;font-size:.84rem;display:block;}
.m .nm{font-family:var(--mono);font-size:.55rem;letter-spacing:1px;text-transform:uppercase;display:block;}
.m.call .px,.m.call .nm{color:var(--res);}.m.call .stem{background:var(--res);box-shadow:0 0 8px rgba(255,93,108,.5);}
.m.put .px,.m.put .nm{color:var(--sup);}.m.put .stem{background:var(--sup);box-shadow:0 0 8px rgba(47,212,154,.5);}
.m.flip .px,.m.flip .nm{color:var(--flip);}.m.flip .stem{background:var(--flip);}
.m.pinch .px,.m.pinch .nm{color:var(--cyan);}.m.pinch .stem{background:var(--cyan);box-shadow:0 0 8px rgba(63,208,224,.5);}
.m.minor .px{color:var(--mid);font-size:.74rem;font-weight:500;}.m.minor .nm{color:var(--dim);}.m.minor .stem{background:var(--line);height:14px !important;}
.spot-m{position:absolute;top:49px;transform:translate(-50%,-50%);z-index:5;}
.spot-m .ring{width:18px;height:18px;border-radius:50%;background:var(--spot);box-shadow:0 0 0 4px rgba(255,194,75,.18),0 0 16px rgba(255,194,75,.55);margin:0 auto;}
.spot-m .lab{position:absolute;left:50%;top:13px;transform:translateX(-50%);font-family:var(--mono);font-weight:700;font-size:.78rem;color:var(--spot);background:var(--bg);padding:0 4px;white-space:nowrap;}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);}
@media (max-width:820px){.tiles{grid-template-columns:repeat(2,1fr);}}
@media (max-width:480px){.tiles{grid-template-columns:1fr;}}
.tile{padding:15px 18px;border-right:1px solid var(--line);position:relative;}
.tile:last-child{border-right:0;}
@media (max-width:820px){.tile:nth-child(2){border-right:0;}.tile{border-bottom:1px solid var(--line);}}
.tile::before{content:"";position:absolute;left:0;top:14px;bottom:14px;width:3px;border-radius:2px;background:var(--line);}
.tile.up::before{background:var(--sup);}.tile.range::before{background:var(--spot);}.tile.down::before{background:var(--res);}.tile.pend::before{background:var(--dim);}
.tile-h{display:flex;align-items:baseline;gap:8px;margin-bottom:8px;}
.tile-h .nm{font-family:var(--disp);font-weight:600;font-size:.98rem;}
.tile-h .gk{font-family:var(--mono);font-size:.55rem;letter-spacing:1px;color:var(--dim);text-transform:uppercase;}
.tlean{font-family:var(--mono);font-size:.62rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;display:inline-block;margin-bottom:8px;}
.tlean.up{color:var(--sup);}.tlean.range{color:var(--spot);}.tlean.down{color:var(--res);}.tlean.pend{color:var(--dim);}
.tstat{font-family:var(--mono);font-weight:700;font-size:1.15rem;margin-bottom:3px;}
.tstat.sup{color:var(--sup);}.tstat.spot{color:var(--spot);}.tstat.res{color:var(--res);}
.tstat small{font-size:.6rem;color:var(--dim);letter-spacing:1px;text-transform:uppercase;display:block;font-weight:400;margin-bottom:1px;}
.tdesc{font-size:.76rem;color:var(--mid);line-height:1.45;}.tdesc b{color:var(--txt);}
.net{display:flex;align-items:center;gap:14px;padding:14px 22px;background:var(--panel-2);}
.net .tag{font-family:var(--mono);font-size:.58rem;letter-spacing:2px;text-transform:uppercase;color:var(--cyan);border:1px solid rgba(63,208,224,.4);border-radius:5px;padding:4px 8px;white-space:nowrap;}
.net p{margin:0;font-size:.8rem;color:var(--mid);line-height:1.5;}.net p b{color:var(--txt);}.net p .risk{color:var(--res);}
@media (prefers-reduced-motion:reduce){.verdict .d{animation:none;}}
"""

_FONTS = {
    "terminal": "Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600",
    "desknote": "Spectral:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600;700",
    "commandbar": "Chakra+Petch:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600",
}

_CSS = {"terminal": _CSS_TERMINAL, "desknote": _CSS_DESKNOTE, "commandbar": _CSS_COMMANDBAR}


def _doc(style: str, body: str, c: Dict) -> str:
    """Wrap a body fragment in the full HTML document with the right CSS/fonts."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{c['symbol_raw']} \u2014 Gamma Read</title>\n"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
        f"<link href=\"https://fonts.googleapis.com/css2?family={_FONTS[style]}&display=swap\" rel=\"stylesheet\">\n"
        f"<style>{_CSS[style]}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


#############################################
# STYLE: TERMINAL (dark vertical ladder)
#############################################

def _render_terminal(c: Dict, projection: str = "") -> str:
    if c['vanna_present'] or c['pinch_present']:
        lean_cls = 'pending' if c['vanna_lean_class'] == 'pend' else c['vanna_lean_class']
        pinch_dpts = (
            f'<div class="dpt"><span class="k">Pinch magnet</span><span class="v" style="color:var(--pinch);">{c["pinch_strike_str"]}</span></div>'
            f'<div class="dpt"><span class="k">Strength</span><span class="v">{c["pinch_strength_str"]}</span></div>'
        ) if c['pinch_present'] else ''
        vanna_card = (
            f'<article class="sig {lean_cls}"><div class="sig-head">'
            f'<div class="sig-name">Vanna <span>VEX</span> &nbsp;\u00b7&nbsp; Dealer Pinch</div>'
            f'<div class="lean {lean_cls}">{c["vanna_lean_text"]}</div></div>'
            f'<p class="sig-read">{c["vanna_read"]}</p>'
            f'<div class="sig-data">'
            f'<div class="dpt"><span class="k">Net VEX</span><span class="v">{c["vex_str"]}</span></div>'
            f'<div class="dpt"><span class="k">IV trend</span><span class="v">{c["iv_label"]}</span></div>'
            f'{pinch_dpts}</div>'
            f'<div class="play"><span class="t">Pinch</span> &nbsp;{c["pinch_read"]}</div></article>'
        )
    else:
        vanna_card = (
            '<article class="sig pending"><div class="sig-head">'
            '<div class="sig-name">Vanna <span>VEX</span> &nbsp;\u00b7&nbsp; Dealer Pinch</div>'
            '<div class="lean pending">\u25cb Awaiting data</div></div>'
            '<p class="sig-read" style="margin-bottom:0;">No Vanna/drift or pinch data yet \u2014 '
            'waiting on the first market-data fetch. Vanna matters most around IV shocks (FOMC / CPI / earnings).</p></article>'
        )
    # pin-zone label only when no pinch magnet sits in the zone; otherwise the magnet labels it
    pin_zone_lab = '' if c['pinch_present'] else (
        f'<div class="lzlab" style="left:{c["pin_lo"]+c["pin_width"]/2}%;transform:translateX(-50%);'
        f'color:var(--spot);">Pin zone</div>'
    )
    pinch_marker = (
        f'<div class="lm pinch up" style="left:{c["p_pinch"]}%;"><div class="stem"></div>'
        f'<div class="lab"><span class="px">{c["pinch_strike_str"]}</span>'
        f'<span class="nm">Pinch</span></div></div>'
    ) if c['pinch_present'] else ''
    import html as _html
    proj_block = (
        '<section class="gi-close" style="margin:14px 0;padding:12px 16px;'
        'border:1px solid #213152;border-radius:12px;background:#101a30;'
        'color:#c7d2e8;font-size:.92rem;line-height:1.5;">'
        '<div style="color:#8a93a3;text-transform:uppercase;letter-spacing:.04em;'
        'font-size:.72rem;margin-bottom:4px;">Into the close</div>'
        f'{_html.escape(projection)}</section>'
    ) if projection else ''
    body = f"""<div class="wrap">
  <header class="head">
    <div>
      <div class="kicker">Gamma Desk Read \u00b7 Your Playbook</div>
      <div class="id"><div class="ticker">$<em>{c['symbol_raw']}</em></div>
        <div class="spot-box"><span class="lab">Spot</span><span class="val">{c['spot']}</span></div></div>
    </div>
    <div class="head-right">
      <div class="regime {('' if c['regime_class']=='sup' else 'res')}"><span class="dot"></span>{c['regime_label']}</div>
      <div class="meta">VIX <b>{c['vix_str']}</b> &nbsp;\u00b7&nbsp; Sentiment <b>{c['sentiment_score']}/10 {c['sentiment_trend']}</b></div>
    </div>
  </header>
  {proj_block}
  <section class="lmap-card">
    <div class="ttl">Level Map</div>
    <div class="sub">Walls, flips &amp; decay strikes \u00b7 {c['axis_lo']}\u2013{c['axis_hi']}</div>
    <div class="lmap">
      <div class="lmap-axis"></div>
      <div class="lzone-pin" style="left:{c['pin_lo']}%;width:{c['pin_width']}%;"></div>
      <div class="lzone-neg" style="left:0;width:{c['neg_width']}%;"></div>
      <div class="lzlab" style="left:1%;color:var(--res);">Neg-\u03b3 \u00b7 accelerates</div>
      {pin_zone_lab}
      <div class="lm minor up" style="left:{c['p_cmax_neg']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_neg']}</span><span class="nm">max \u2212charm</span></div></div>
      <div class="lm call up" style="left:{c['p_call']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['call_wall']}</span><span class="nm">Call wall \u00b7 R</span></div></div>
      <div class="lm put dn" style="left:{c['p_put']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['put_wall']}</span><span class="nm">Put wall \u00b7 S</span></div></div>
      <div class="lm flip dn" style="left:{c['p_flip']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['gamma_flip']}</span><span class="nm">Gamma flip</span></div></div>
      {pinch_marker}
      <div class="lm minor dn" style="left:{c['p_cmax_pos']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_pos']}</span><span class="nm">max +charm</span></div></div>
      <div class="lspot" style="left:{c['p_spot']}%;"><div class="ring"></div><div class="lab">{c['spot']} spot</div></div>
    </div>
  </section>
  <section class="sig-grid">
      <article class="sig {c['gex_lean_class']}">
        <div class="sig-head"><div class="sig-name">Gamma Exposure <span>GEX</span></div>
          <div class="lean {c['gex_lean_class']}">{c['gex_lean_text']}</div></div>
        <p class="sig-read">{c['gex_read']}</p>
        <div class="sig-data">
          <div class="dpt"><span class="k">Call wall</span><span class="v res">{c['call_wall']}</span></div>
          <div class="dpt"><span class="k">Put wall</span><span class="v sup">{c['put_wall']}</span></div>
          <div class="dpt"><span class="k">Flip</span><span class="v flip">{c['gamma_flip']}</span></div>
        </div>
        <div class="play"><span class="t">Play</span> &nbsp;{'Fade pushes into '+c['call_wall']+', buy dips toward '+c['put_wall']+'.' if c['regime_class']=='sup' else 'Trade with the trend; expect range expansion.'} <span class="risk">{'Breaks if '+c['gamma_flip']+' fails on volume → gamma flips negative, moves accelerate.' if c['regime_class']=='sup' else 'Regime calms once back above '+c['gamma_flip']+'.'}</span></div>
      </article>
      <article class="sig {c['charm_lean_class']}">
        <div class="sig-head"><div class="sig-name">Charm Pressure <span>\u0394-DECAY</span></div>
          <div class="lean {c['charm_lean_class']}">{c['charm_lean_text']}</div></div>
        <p class="sig-read">{c['charm_read']}</p>
        <div class="sig-data">
          <div class="dpt"><span class="k">Charm flip</span><span class="v">{c['charm_flip']}</span></div>
          <div class="dpt"><span class="k">Max +decay</span><span class="v sup">{c['charm_max_pos']}</span></div>
          <div class="dpt"><span class="k">Max \u2212decay</span><span class="v res">{c['charm_max_neg']}</span></div>
        </div>
        <div class="play"><span class="t">Play</span> &nbsp;Lean with the pin-drift. <span class="risk">Slow force \u2014 a catalyst overrides it instantly; size down near events.</span></div>
      </article>
      <article class="sig {c['dex_lean_class']}">
        <div class="sig-head"><div class="sig-name">Delta Exposure <span>DEX</span></div>
          <div class="lean {c['dex_lean_class']}">{c['dex_lean_text']}</div></div>
        <p class="sig-read">{c['dex_read']}</p>
        <div class="sig-data"><div class="dpt"><span class="k">Net hedge flow</span><span class="v {c['flow_class']}">{c['flow']}</span></div></div>
        <div class="play"><span class="t">Play</span> &nbsp;{'Favor the long side.' if c['flow_class']=='sup' else 'Respect the supply.'} <span class="risk">Watch the projected EOD flip \u2014 once hedge pressure crosses zero the {'tailwind turns headwind' if c['flow_class']=='sup' else 'pressure lifts'}.</span></div>
      </article>
      {vanna_card}
  </section>
  <div class="senti" style="margin-top:18px;">
    <div class="gauge"><div class="num">{c['sentiment_score']}<small>/10</small></div>
      <div class="bars">{_sentiment_bars(c['sentiment_score'])}</div></div>
    <div class="desc"><b>{c['sentiment_trend'].title()}.</b> Sentiment read driving sizing &amp; directional bias.</div>
    <div class="chips"><span class="chip {'bull' if c['sentiment_trend_bull'] else ''}">trend \u00b7 {c['sentiment_trend']}</span>
      <span class="chip">confidence {c['sentiment_conf']}%</span></div>
  </div>
  <div class="synth"><span class="badge">Net read</span><p>{c['net_read']}</p></div>
  <div class="foot">Greeks read \u00b7 not a forecast \u00b7 ignore around CPI / FOMC / earnings \u2014 <span>event flow overwhelms positioning</span></div>
</div>"""
    return _doc("terminal", body, c)


#############################################
# STYLE: DESKNOTE (light research note)
#############################################

def _render_desknote(c: Dict, projection: str = "") -> str:
    pinch_mark = (
        f'<div class="mark pinch dn" style="left:{c["p_pinch"]}%;"><div class="stem"></div>'
        f'<div class="lab"><span class="px">{c["pinch_strike_str"]}</span><span class="nm">Pinch magnet</span></div></div>'
    ) if c['pinch_present'] else ''
    vp_block = (
        f'<div class="vp"><div class="vp-col">'
        f'<div class="vp-h"><span class="nm">Vanna</span><span class="gk">VEX</span></div>'
        f'<span class="vp-lean {c["vanna_lean_class"]}">{c["vanna_lean_text"]}</span>'
        f'<p style="margin-top:6px;">{c["vanna_read"]}</p>'
        f'<div class="vp-data">Net VEX <b>{c["vex_str"]}</b> &nbsp;\u00b7&nbsp; IV trend <b>{c["iv_label"]}</b></div>'
        f'</div><div class="vp-col">'
        f'<div class="vp-h"><span class="nm">Dealer Pinch</span><span class="gk">\u03b3 + vanna</span></div>'
        f'<span class="vp-lean {"pinch" if c["pinch_present"] else "pend"}">\u25c8 {"Exhaustion magnet" if c["pinch_present"] else "Awaiting data"}</span>'
        f'<p style="margin-top:6px;">{c["pinch_read"]}</p>'
        f'<div class="vp-data">Magnet <b>{c["pinch_strike_str"]}</b> &nbsp;\u00b7&nbsp; Strength <b>{c["pinch_strength_str"]}</b></div>'
        f'</div></div>'
    )
    body = f"""<div class="sheet">
  <div class="mast">
    <div class="name">The Gamma <b>Desk</b></div>
    <div class="dline">Underlying <b>${c['symbol_raw']}</b> \u00b7 Spot <b>{c['spot']}</b><br>Positioning note \u00b7 VIX <b>{c['vix_str']}</b></div>
  </div>
  <div class="rule-pair"></div>
  <div class="lede">
    <span class="verdict {('' if c['regime_class']=='sup' else 'res')}">{c['regime_label']}</span>
    <h1>Dealers are <em>{'pinning' if c['regime_class']=='sup' else 'chasing'}</em> the tape \u2014 a {'low-energy grind' if c['regime_class']=='sup' else 'trending move'} {c['drift_word'].split(' into')[0]} between the walls.</h1>
  </div>
  <div class="axis-wrap">
    <div class="axis-cap"><span>Level map \u00b7 {c['axis_lo']}\u2013{c['axis_hi']}</span><span class="spot">Spot <b>{c['spot']}</b></span></div>
    <div class="number-line">
      <div class="nl-base"></div>
      <div class="nl-pin" style="left:{c['pin_lo']}%;width:{c['pin_width']}%;"></div>
      <div class="nl-neg" style="left:0;width:{c['neg_width']}%;"></div>
      <div class="tick" style="left:0;"></div><div class="tick" style="left:100%;"></div>
      <div class="mark minor up" style="left:{c['p_cmax_neg']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_neg']}</span><span class="nm">max \u2212charm</span></div></div>
      <div class="mark call up" style="left:{c['p_call']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['call_wall']}</span><span class="nm">Call wall</span></div></div>
      <div class="mark put up" style="left:{c['p_put']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['put_wall']}</span><span class="nm">Put wall</span></div></div>
      <div class="mark flip dn" style="left:{c['p_flip']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['gamma_flip']}</span><span class="nm">Gamma flip</span></div></div>
      {pinch_mark}
      <div class="mark minor dn" style="left:{c['p_cmax_pos']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_pos']}</span><span class="nm">max +charm</span></div></div>
      <div class="spot-pin" style="left:{c['p_spot']}%;"><div class="dia"></div><div class="lab">{c['spot']}</div></div>
    </div>
  </div>
  <div class="cols">
    <div class="col"><div class="col-h"><span class="nm">Gamma</span><span class="gk">GEX</span></div>
      <span class="lean {c['gex_lean_class']}">{c['gex_lean_text']}</span>
      <p style="margin-top:7px;">Spot <b>{'above' if c['regime_class']=='sup' else 'below'} the flip</b> \u2014 dealers {'long' if c['regime_class']=='sup' else 'short'} gamma. {'They buy dips, sell rips; vol gets dampened and price pins between the walls.' if c['regime_class']=='sup' else 'They sell dips, buy rips; moves trend and amplify.'}</p>
      <div class="kv"><div class="row"><span class="k">Call wall \u00b7 R</span><span class="v res">{c['call_wall']}</span></div>
        <div class="row"><span class="k">Put wall \u00b7 S</span><span class="v sup">{c['put_wall']}</span></div>
        <div class="row"><span class="k">Flip</span><span class="v flip">{c['gamma_flip']}</span></div></div>
      <div class="play"><b>Play \u2014</b> {'fade pushes into '+c['call_wall']+', buy dips to '+c['put_wall']+'.' if c['regime_class']=='sup' else 'trade with the trend.'} <span class="risk">Inverts if {c['gamma_flip']} fails on volume.</span></div>
    </div>
    <div class="col"><div class="col-h"><span class="nm">Charm</span><span class="gk">\u0394-decay</span></div>
      <span class="lean {c['charm_lean_class']}">{c['charm_lean_text']}</span>
      <p style="margin-top:7px;">Spot {'above' if c['charm_lean_class']=='up' else 'below'} the charm flip \u2014 should <b>drift price {c['drift_word']}</b>. Strongest late-day and OPEX Thu\u2013Fri.</p>
      <div class="kv"><div class="row"><span class="k">Charm flip</span><span class="v">{c['charm_flip']}</span></div>
        <div class="row"><span class="k">Max +decay</span><span class="v sup">{c['charm_max_pos']}</span></div>
        <div class="row"><span class="k">Max \u2212decay</span><span class="v res">{c['charm_max_neg']}</span></div></div>
      <div class="play"><b>Play \u2014</b> lean with the drift. <span class="risk">Slow force; a catalyst overrides it instantly.</span></div>
    </div>
    <div class="col"><div class="col-h"><span class="nm">Delta</span><span class="gk">DEX</span></div>
      <span class="lean {c['dex_lean_class']}">{c['dex_lean_text']}</span>
      <p style="margin-top:7px;">Dealers must <b>{c['flow'].split()[0].lower()} {c['flow'].split('$')[1]}</b> of shares to stay hedged \u2014 a directional {'bid' if c['flow_class']=='sup' else 'offer'} sitting {'under' if c['flow_class']=='sup' else 'over'} the tape.</p>
      <div class="kv"><div class="row"><span class="k">Net hedge flow</span><span class="v {c['flow_class']}">{c['flow']}</span></div></div>
      <div class="play"><b>Play \u2014</b> {'favor the long side.' if c['flow_class']=='sup' else 'respect the supply.'} <span class="risk">{'Tailwind' if c['flow_class']=='sup' else 'Pressure'} flips once the EOD flow crosses zero.</span></div>
    </div>
  </div>
  {vp_block}
  <div class="band">
    <div class="senti"><span class="score">{c['sentiment_score']}<small>/10</small></span>
      <span class="ticks">{_sentiment_bars(c['sentiment_score'])}</span></div>
    <div class="txt"><b>Sentiment: {c['sentiment_trend']}.</b> Drives position sizing and directional bias.</div>
    <div class="tags">trend <b>{c['sentiment_trend']}</b> \u00b7 confidence {c['sentiment_conf']}%</div>
  </div>
  <p class="net">{c['net_read']}</p>
  <div class="colophon"><span>Greeks read \u00b7 not a forecast</span><span>Ignore around CPI / FOMC / earnings</span></div>
</div>"""
    return _doc("desknote", body, c)


#############################################
# STYLE: COMMANDBAR (wide dark HUD)
#############################################

def _render_commandbar(c: Dict, projection: str = "") -> str:
    pin_zone_lab = '' if c['pinch_present'] else (
        f'<div class="sp-zlab" style="left:{c["pin_lo"]+c["pin_width"]/2-3}%;color:var(--spot);">Pin zone</div>'
    )
    pinch_mark = (
        f'<div class="m pinch dn" style="left:{c["p_pinch"]}%;"><div class="stem"></div>'
        f'<div class="lab"><span class="px">{c["pinch_strike_str"]}</span><span class="nm">Pinch magnet</span></div></div>'
    ) if c['pinch_present'] else ''
    if c['vanna_present'] or c['pinch_present']:
        vstat_cls = 'sup' if c['vanna_lean_class'] == 'up' else 'res' if c['vanna_lean_class'] == 'down' else ''
        pinch_line = (f' Pinch toward <b>{c["pinch_strike_str"]}</b> ({c["pinch_strength_str"]}).'
                      if c['pinch_present'] else '')
        vanna_tile = (
            f'<div class="tile {c["vanna_lean_class"]}"><div class="tile-h"><span class="nm">Vanna</span>'
            f'<span class="gk">VEX \u00b7 pinch</span></div>'
            f'<span class="tlean {c["vanna_lean_class"]}">{c["vanna_lean_text"]}</span>'
            f'<div class="tstat {vstat_cls}"><small>Net VEX</small>{c["vex_str"]}</div>'
            f'<p class="tdesc">{c["vanna_read"]}{pinch_line}</p></div>'
        )
    else:
        vanna_tile = (
            '<div class="tile pend"><div class="tile-h"><span class="nm">Vanna</span>'
            '<span class="gk">VEX \u00b7 pinch</span></div>'
            '<span class="tlean pend">\u25cb Awaiting data</span>'
            '<div class="tstat" style="color:var(--dim);"><small>Status</small>\u2014 \u2014 \u2014</div>'
            '<p class="tdesc">No vanna/drift or pinch data yet. Matters most around <b>IV shocks</b> (FOMC / CPI).</p></div>'
        )
    body = f"""<div class="bar">
  <div class="topedge"></div>
  <div class="hdr">
    <div class="hdr-l">
      <div class="tk"><span class="t">$<b>{c['symbol_raw']}</b></span><span class="p"><small>spot</small>{c['spot']}</span></div>
      <span class="verdict {('' if c['regime_class']=='sup' else 'res')}"><span class="d"></span>{c['regime_label']} \u00b7 {c['drift_word'].split(' into')[0]}</span>
    </div>
    <div class="hdr-r">
      <div class="sgauge"><span class="lab">Sentiment</span><span class="n">{c['sentiment_score']}<small>/10</small></span>
        <span class="bars">{_sentiment_bars(c['sentiment_score'])}</span></div>
      <span class="vix">VIX <b>{c['vix_str']}</b></span>
    </div>
  </div>
  <div class="spine-wrap">
    <div class="spine-cap"><span>Level map</span><span>{c['axis_lo']} \u2014 {c['axis_hi']}</span></div>
    <div class="spine">
      <div class="sp-base"></div>
      <div class="sp-pin" style="left:{c['pin_lo']}%;width:{c['pin_width']}%;"></div>
      <div class="sp-neg" style="left:0;width:{c['neg_width']}%;"></div>
      <div class="sp-zlab" style="left:1%;color:var(--res);">Neg-\u03b3</div>
      {pin_zone_lab}
      <div class="m minor up" style="left:{c['p_cmax_neg']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_neg']}</span><span class="nm">max \u2212charm</span></div></div>
      <div class="m call up" style="left:{c['p_call']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['call_wall']}</span><span class="nm">Call wall \u00b7 R</span></div></div>
      <div class="m put up" style="left:{c['p_put']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['put_wall']}</span><span class="nm">Put wall \u00b7 S</span></div></div>
      <div class="m flip dn" style="left:{c['p_flip']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['gamma_flip']}</span><span class="nm">Gamma flip</span></div></div>
      {pinch_mark}
      <div class="m minor dn" style="left:{c['p_cmax_pos']}%;"><div class="stem"></div><div class="lab"><span class="px">{c['charm_max_pos']}</span><span class="nm">max +charm</span></div></div>
      <div class="spot-m" style="left:{c['p_spot']}%;"><div class="ring"></div><div class="lab">{c['spot']} spot</div></div>
    </div>
  </div>
  <div class="tiles">
    <div class="tile {c['gex_lean_class']}"><div class="tile-h"><span class="nm">Gamma</span><span class="gk">GEX</span></div>
      <span class="tlean {c['gex_lean_class']}">{c['gex_lean_text']}</span>
      <div class="tstat spot"><small>Walls</small>{c['put_wall']} \u2013 {c['call_wall']}</div>
      <p class="tdesc">{'Above the flip → dealers long gamma, vol dampened.' if c['regime_class']=='sup' else 'Below the flip → short gamma, moves amplify.'} <b>{'Fade the call wall, buy the put wall.' if c['regime_class']=='sup' else 'Trade with the trend.'}</b></p>
    </div>
    <div class="tile {c['charm_lean_class']}"><div class="tile-h"><span class="nm">Charm</span><span class="gk">\u0394-decay</span></div>
      <span class="tlean {c['charm_lean_class']}">{c['charm_lean_text']}</span>
      <div class="tstat"><small>Charm flip</small>{c['charm_flip']}</div>
      <p class="tdesc">Should <b>drift price {c['drift_word']}</b>. A catalyst overrides it.</p>
    </div>
    <div class="tile {c['dex_lean_class']}"><div class="tile-h"><span class="nm">Delta</span><span class="gk">DEX</span></div>
      <span class="tlean {c['dex_lean_class']}">{c['dex_lean_text']}</span>
      <div class="tstat {c['flow_class']}"><small>Net hedge flow</small>{c['flow']}</div>
      <p class="tdesc">Dealers must hedge \u2014 <b>a {'bid under' if c['flow_class']=='sup' else 'offer over'} the tape</b> until the EOD flip.</p>
    </div>
    {vanna_tile}
  </div>
  <div class="net"><span class="tag">Net read</span><p>{c['net_read']}</p></div>
</div>"""
    return _doc("commandbar", body, c)


#############################################
# PUBLIC API
#############################################

_RENDERERS = {
    "terminal": _render_terminal,
    "desknote": _render_desknote,
    "commandbar": _render_commandbar,
}


def render_infographic(data: GammaRead, style: str = "terminal", projection: str = "") -> str:
    """Return a complete, self-contained HTML page for the given gamma read.

    style: 'terminal' | 'desknote' | 'commandbar'
    projection: optional forward "into the close" read; rendered as a block in
        the terminal style (ignored by the other styles). Empty -> no block.
    """
    if style not in _RENDERERS:
        raise ValueError(f"Unknown style '{style}'. Choose from {list(_RENDERERS)}.")
    return _RENDERERS[style](_derive(data), projection)


def save_infographic(data: GammaRead, path: str, style: str = "terminal") -> str:
    """Render and write the HTML to `path`. Returns the path."""
    html = render_infographic(data, style)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


#############################################
# DEMO
#############################################

if __name__ == "__main__":
    # Current $SPX read (matches the source explain.html)
    read = GammaRead(
        spot=7521,
        call_wall=7570,
        put_wall=7525,
        gamma_flip=7507,
        charm_flip=7521,
        charm_max_pos=7495,
        charm_max_neg=7600,
        dex_flow_usd=1.6e9,     # +1.6B -> dealers BUY -> tailwind
        sentiment_score=6,
        sentiment_trend="bull_trend",
        sentiment_confidence=100,
        vix=None,               # unknown
        # --- Vanna / Pinch (illustrative values; feed real ones from OptionsAnalytics) ---
        vex_notional_usd=0.85e9,  # +0.85B positive VEX
        iv_trend="falling",       # falling IV + positive VEX -> vanna rally
        pinch_strike=7545,        # magnet inside the pin zone
        pinch_strength=68,        # 0-100
    )
    for s in ("terminal", "desknote", "commandbar"):
        out = save_infographic(read, f"gamma_{s}.html", s)
        print(f"wrote {out}")
