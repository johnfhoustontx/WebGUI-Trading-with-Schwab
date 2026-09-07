# The Flip — the NeuralStrike mark

**Status:** built 2026-09-07. Chosen from three directions put to the owner as a
specimen board; this records the one that shipped and why.

## The problem

There were two brands and they disagreed.

The logo was gold and royal blue, maximalist, loaded with AI signifiers —
circuit traces, a neural starburst, candlesticks, a swoosh — with the tagline
*"AI OPTION SIGNALS | TRADING IDEAS"*. The public site runs **Nocturne**: near
black `#161826`, one muted blurple `#9184d9`, Inter at weight 500, outlined
buttons, and a stated principle that *"contrast comes from the tonal ramps, not
from saturation."* On the live hero the logo was the only saturated object on a
deliberately desaturated page.

It also failed as a mark, independently of taste:

* **Portrait raster with the wordmark and tagline baked in** — unusable small,
  hence the separate square crop that existed only to feed the app header.
* **Needed `mix-blend-mode: lighten`** to sit on the page at all, because it was
  a JPG on a black ground. That is a workaround, and it meant the mark could
  never go on a light background, a printed page, or a light-mode email.
* **No one-colour version**, so no stamp, no vinyl, no embroidery.
* **Gold gradients and hairline traces vanish below ~64px** — no favicon, no
  avatar, no stream watermark.
* **The tagline was the same sentence every signal-selling account uses**, on a
  site whose community section promises no signal-selling.

## The mark

Two chevrons converging on a level line: dealer hedging pinned to the gamma
flip. The idea is **inevitability, not intelligence** — the one thing the app
teaches is that somebody is *forced* to hedge, and nobody else in retail options
is selling mechanics. Three shapes, which is what lets it survive 16px.

It has a legitimate **second state** — chevrons diverging — for negative gamma,
where hedging amplifies instead of pinning. That gives an alert glyph and a
loading frame for free, from the same parts.

The wordmark drops the gold/blue split for one accent. The tagline becomes
**"dealer flow, measured."**

## Decisions that were not taste

### The mark is carried by its FORM, not by a hex

The site runs Nocturne (`#9184d9`) and the app runs its own dark-navy theme
(`#6b86ff`). Both are the same blue-violet family at different tunings. The mark
takes **each surface's own accent** rather than forcing one colour across both —
which is precisely the fault it replaced, a foreign colour imported onto a page
that had its own.

### Two optical sizes, not one drawing scaled

`assets/mark.svg` (large, ~24px and up) and `assets/favicon.svg` (small) are
**different drawings**. At 16px the large variant's 2.5-unit rule lands on 0.6 of
a device pixel and disappears, taking the level — and therefore the meaning —
with it. The small variant is drawn heavier so the rule holds one whole pixel.

### The rule is solid; only the hero's fades

Nocturne fades its page dividers at both ends. The **mark** does not: it has to
survive one-colour reproduction, and a gradient cannot be cut in vinyl or
stamped. The single exception is the hero instance, where the mark is page
furniture as much as mark and has to die away rather than stop dead against the
radial wash.

### The geometry lives in seven files, on purpose

An `<img>` gets no stylesheet, so the favicon, the app header and the social card
need literal colours, while the site's nav and hero inline the geometry to take
the accent from CSS. One shared file cannot do both.
`deploy/tests/test_site.py` is the rent on that duplication — it reads the path
data out of every copy and fails when they drift, because a mark that differs
between the tab and the header does not look broken anywhere; it looks *slightly
off* everywhere, which nobody reports.

## ⚠ The first draw was wrong and only the render showed it

The chevron apexes sat ~2 units from the rule. Once stroke width was added they
touched it, and the mark read as an **X struck through** — a cancel icon. **The
gap is the meaning**: they are converging, not joined. Every test passed;
rendering the social card at 60px is what caught it.

`test_the_apex_clears_the_rule` now checks it as arithmetic — apex plus half the
stroke must clear the rule's edge by at least 2 units — rather than by eye.

## ⚠ A guard that could not fail, again

`test_the_landing_page_declares_a_social_preview` checked `"og:image" in page`.
That substring is also inside `og:image:width` and `og:image:alt`, so **deleting
the actual image tag left the test green**. Caught by mutation testing, not by
reading it. It now matches the whole property value, closing quote included.

This is the second guard in two days on this site whose first draft asserted
nothing. Both were found the same way. **Put the bug back and watch the suite go
red** — 19/19 mutations are caught as of this change.

## The social card

`assets/social.png`, 1200×630, generated rather than exported so it can be
rebuilt when the copy or the mark changes — and so the mark on it is provably
the same geometry the site draws.

It leads with the **claim, not the name** (*"This one shows you who has to trade
next."*), because a preview card has one job: earn the click. Before this there
were **zero `og:` tags on any page**, so every link pasted into Discord or
Telegram — the project's actual distribution channels — previewed as a bare URL.

Type is real Inter, instanced from the variable font at the brand's weights. The
shipped face is woff2 and fontTools cannot decompress it without `brotli`, so the
generator uses a variable TTF fetched to the scratchpad; it never enters the repo.

## The app

Config only — `config/theme.toml [brand]` — so it is one edit to revert:

* the four sampled gold/blue gradient stops become two flat colours
* `mark` points at `neuralstrike-mark.svg`

The built-in `_DEFAULTS` in `webgui/pages/options/theme.py` moved with it. A
fallback that restores a retired brand when `theme.toml` is missing is worse
than a crash, because nothing looks wrong.

**The old artwork is kept, unreferenced.** `neuralstrike-mark.png` and
`neuralstrike-logo.jpg` stay in `webgui/static/img/`, pinned by test, so the
revert is a line in a TOML rather than a redraw.

**Not done:** the app's wordmark tracking. The board's lockup is Inter 600 at
0.15em; `build_brand_css` hardcodes `letter-spacing: .01em` and the font comes
from config. Matching it means editing a shared theme builder, which is a code
change to live trading-UI chrome and out of scope for a mark decision.

## Test-surface note

`webgui/tests/test_shell.py` used to write `neuralstrike-mark.png` and assert
that exact path. It tests the **mechanism** — configured and present yields the
URL, absent yields `""` — so pinning the filename made it fail on a format
change that said nothing about whether the mechanism worked. The filename is now
derived from `theme.BRAND_MARK`.
