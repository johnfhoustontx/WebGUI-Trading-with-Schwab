# Sentiment screen redesign — design (2026-06-16)

Layout/UX refresh of `webgui/pages/sentiment.py`. Pure-GUI; reads the same
`sentiment_svc` cache (`sentiment:composite/history/sectors`). No service change.

## Decisions (with user)
- Market Trend → speedometer, needle = **hybrid (C)** number.
- Drop the Modifier tile.
- Bias/Signal/Yesterday/Change → 2×2 matrix.
- Top row L→R: ① Market Sentiment · ② Market Trend · ③ Bias 2×2.
- Component table → press-and-hold popup button inside the Sentiment panel.
- 30-Day History → collapsible, collapsed by default.

## Changes

1. **Top row = 3 columns.**
   - **① Market Sentiment:** existing `speedometer_svg(gauge_score(total), bias)`
     + `total · bias` line + agg-conf line + a **"Components"** button.
   - **② Market Trend:** NEW speedometer via pure `trend_gauge_value(trend)`:
     ```
     ANCHORS = {bull_trend:85, pullback_in_bull:65, range:50,
                bear_rally:35, bear_trend:15}
     value = clamp(ANCHORS.get(state,50)
                   + clamp(slope_pct*50, -8, 8)
                   + clamp(drawdown_pct*0.3, -5, 5), 0, 100)
     ```
     Dial label = trend label; small caption keeps SPY/50d/200d/slope/dd/conf.
     Needle lands in the matching speedometer zone (bear=red … bull=green).
   - **③ Bias 2×2:** grid of Bias / Signal / Yesterday / Change (no Modifier),
     traffic-light tinted (`traffic_color(total)`), reusing the `tiles()` dict.

2. **Component popup (press-and-hold).** A "Components" button inside ①; a
   `ui.menu().props('no-parent-event')` anchored to it, opened on `mousedown`
   and closed on `mouseup`/`mouseleave`. The menu hosts the existing
   `_render_components` table (its column is re-rendered on each data repaint via
   `_apply`). Removed from the always-on layout.

3. **30-Day History collapsible.** Wrap the history `ui.plotly` + rolling/
   velocity/divergence labels in `ui.expansion("30-Day History", value=False)`.

4. **Untouched:** Sector & Industry Performance, refresh/version-poll plumbing,
   all `sentiment_svc`/cache code.

## Testing
- TDD `trend_gauge_value`: bull→high (>75), bear→low (<40), range→~50, nudge
  stays within the state band, clamps to [0,100], missing/empty trend→50.
- Visual: 3-column layout, trend needle in the right zone, press-and-hold popup
  shows/hides with the mouse button, history collapsed by default.
