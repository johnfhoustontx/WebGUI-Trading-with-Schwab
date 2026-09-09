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

### The app's wordmark tracking became config (2026-09-07, later)

`build_brand_css` hardcoded `letter-spacing: .01em` — effectively none — which
is why the app's lockup sat tight while the public site's ran wide. It was the
one property of the lockup not reachable from `[brand]`, so the two surfaces
drifted on the one axis nobody could edit without touching the function.

It is now `[brand].tracking`, defaulting to `.14em`. **Uppercase wordmarks need
tracking; it is not a refinement** — capitals are drawn to sit inside lowercase
words, so set solid they read as cramped, and a value near zero undoes the
`text-transform: uppercase` above it. Chosen by rendering the real `BRAND_CSS`
and the real lockup at .01 / .08 / .14 / .20em and looking: .01 reads as one
dense block, .20 starts to fragment.

⚠ **The obvious test proves nothing.** Asserting `BRAND_CSS` contains `.14em`
passes whether or not the config is read, because `.14em` is also the built-in
default — the exact trap CLAUDE.md documents for config extractions. The guard
drives `build_brand_css` with `0.42em`, a value the defaults do not contain, and
was verified by re-hardcoding the literal and watching it fail.

### ⚠ The app mark was on the wrong optical variant

`.brand-mark` renders at **44px**, not the 28px an earlier comment in the SVG
claimed — nobody measured it. It shipped briefly with the SMALL drawing, whose
heavier strokes read as clumsy at that size. It now carries the large one, and
the test asserts both that it has the large paths and that it does *not* have
the small ones.

The same audit found `.brand-mark`'s own comment stale: it described artwork
"on black", and the SVG is transparent. `border-radius` and `object-fit: cover`
are inherited from the old raster and now do nothing — a 64-unit square viewBox
fits a 44px box exactly, and there is no ground to round. Left in place, with a
note, because a future mark may be a raster again.

### The app's browser tab (2026-09-07, later still)

The header was right and the tab was not: the app's favicon is **generated per
route** — a plain rounded square in one of thirty colours, so a trader with a
dozen tabs open can tell them apart. Useful, and completely anonymous.

The fix keeps the feature and adds the brand: **the route colour becomes the
GROUND and the mark rides on top**. That split is the whole design — the colour
exists to separate tabs at 16px, and only a full-bleed field does that; a tinted
rule on a dark square would make every tab identical.

It draws the SMALL optical variant, matching `deploy/site/assets/favicon.svg`, so
the tab icon does not change meaning when you cross from the marketing site to
the app.

⚠ **The ink was a luminance threshold, and the threshold was wrong.** At `> 140`
two routes failed WCAG's 3:1 for graphics — `/driver` `#ff7043` at **2.50:1** and
`/options/portfolio` `#26a69a` at **2.73:1**, both mid-tones handed the light ink
when the dark one read better. Retuning to 110 would have fixed those two and
stayed right only until the next route was added: **a threshold encodes a guess
about a palette that grows.**

It now picks whichever of the two inks actually contrasts more, measured. That
cannot be defeated by a new colour, because the best of two is the best of two.
Worst case across all thirty went **2.50:1 → 4.21:1**, and 24 of 30 flipped to
dark ink — the threshold had it backwards for most of the palette.

**The test asserts the RATIO, not the branch.** A guard that checked which side
of the threshold a colour fell on would have been green while `/driver` was
illegible. It was verified by restoring the threshold and watching it fail.

### ⚠ The tab icon was never reaching the browser at all

Swapping the artwork did not fix the tab, because the mechanism was wrong. The
favicon was injected with `ui.add_head_html`, which appends a **second**
`<link rel="icon">` — NiceGUI's template already emits its own at line 11
(`rel="shortcut icon"`, pointing at `/_nicegui/…/favicon.ico`) and renders
`head_html` at line 46. Two icons were declared and the browser kept NiceGUI's,
so every tab showed the framework's logo while the header showed the brand.

**The markup was never malformed** — verified by decoding the emitted data URI
back to valid XML. It simply lost. (An early test harness *did* emit broken
markup, because I hand-wrote the URI without escaping; that was the test's bug,
not the code's, and it briefly pointed at the wrong culprit.)

`@ui.page(favicon=…)` **replaces** the line-11 link rather than arguing with it.
Measured against a live server: one icon link, no `favicon.ico`, drawing the
mark on the route's colour — against three competing links before.

Every route now registers through `main._page`, a wrapper that supplies the
favicon, so a page **cannot** be added without one.
`test_every_page_is_registered_through_the_favicon_wrapper` fails on a bare
`@ui.page`, which is how this would otherwise return one route at a time.

### The same gap on the public site, and the raster icons that close it

The site declared **only** an SVG favicon. Chrome, Firefox and Edge take it
happily; two things never ask for it:

* **Safari does not support SVG favicons at all.** It falls back to
  `/favicon.ico`, which this static tree answered with **404** — so on Safari the
  site had no icon.
* **iOS asks for `/apple-touch-icon.png`.** Without one, a home-screen shortcut
  saves a screenshot thumbnail instead of the mark.

Both now exist at the site ROOT, because that is where the request goes whether
or not a page mentions them, and both are drawn from the same small-variant
geometry as `assets/favicon.svg` by `make_icons.py`.

`favicon.ico` is genuinely multi-resolution (16/32/48) — a single-size `.ico` is
a `.png` with extra steps. The apple-touch icon is **180×180, opaque and
full-bleed**: iOS masks the corners itself and composites onto black, so a
transparent icon shows black behind the mark and a pre-rounded one shows its own
corners inside Apple's squircle.

### And the app had it too

`app.neuralstrike.co/favicon.ico` returned **200 with NiceGUI's own logo**,
byte-identical to `nicegui/static/favicon.ico` — so Safari on the app got the
framework's icon no matter what the pages declared.
`nicegui.favicon.create_favicon_route` registers that route **only when handed a
real file**, so `ui.run(favicon=<path>)` now points it at the app's own `.ico`.

⚠ That is the app-wide FALLBACK, not the per-page icon. `get_favicon_url` reads
`page.favicon or app.config.favicon`, so the thirty route colours still win on
every page that has one. Verified on a live server: `/favicon.ico` serves our
bytes, a page without its own falls back to it, and a page with one keeps it.

## Test-surface note

`webgui/tests/test_shell.py` used to write `neuralstrike-mark.png` and assert
that exact path. It tests the **mechanism** — configured and present yields the
URL, absent yields `""` — so pinning the filename made it fail on a format
change that said nothing about whether the mechanism worked. The filename is now
derived from `theme.BRAND_MARK`.
