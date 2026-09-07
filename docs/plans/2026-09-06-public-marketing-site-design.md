# The public marketing site at neuralstrike.co

**Status:** built 2026-09-06.

`deploy/site/` has been served at `neuralstrike.co` + `www.neuralstrike.co` since
the Caddy work landed, holding a REPLACE-ME one-pager. This replaces it with the
three-page site drawn in *Options trading workbench design* (a Claude Design
canvas: a landing page, a 16-screen gallery and a live-screens placeholder, over
a design system called **Nocturne**).

Nothing here changes the edge. `generate_caddyfile.py` already points a
`file_server` at this directory; the work is entirely inside the served root.

## The design bundle is a preview, not a website

The zip ships each page twice — an editable `.dc.html` source and a "bundled"
`dist/*.html`. **The bundled ones are not shippable.** Each is a ~700 KB
JavaScript wrapper around the Design Canvas runtime: it paints a loading spinner,
mounts custom elements, and renders **nothing at all** with JavaScript off. There
is no server-rendered HTML in them for a crawler to read, and they carry the
design tool's own chrome (`#__bundler_loading`, a thumbnail `<svg>` overlay).

The `.dc.html` **sources**, by contrast, are close to plain HTML — inline
`style=` attributes over the design system's `var(--*)` tokens — with four
canvas-specific constructs layered on:

| Construct | What it is | How it was resolved |
|---|---|---|
| `<x-dc>` / `<helmet>` | the canvas document wrapper | stripped; `<helmet>` contents became a real `<head>` |
| `style-hover="…"` | a hover declaration on the element | lifted into real CSS rules in `assets/site.css` |
| `<sc-if>` / `<sc-for>` / `{{ }}` | canvas templating | `sc-if` conditions all defaulted true, so inlined; `sc-for` unrolled |
| `<script type="text/x-dc">` | a `DCLogic` class | rewritten as vanilla JS |

So this is a **faithful rebuild**, not a re-design: layout, copy, colour, spacing
and structure are the design's. Only what could not survive contact with a static
file server changed, and each of those is recorded below.

## What was served, and where it lives

```
deploy/site/
  index.html            landing
  gallery.html          the 16 screens
  live.html             coming-soon
  robots.txt
  assets/
    nocturne.css        the design system's sheet, verbatim
    site.css            hover/focus states + the small page layer
    gallery.js          panel switching only -- no content
    inter-*.woff2       self-hosted Inter
    logo.jpg            the hero mark
    favicon.svg
    shots/*.webp        the 24 screenshots
```

## The four decisions that were not taste

### 1. Inter is self-hosted, not fetched from Google

The design links `fonts.googleapis.com`. The placeholder this replaces carried an
explicit note that the stream slot is a link rather than an embed because *"this
keeps Google's script off the page"* — and a Google Fonts stylesheet still hands
Google every visitor's IP and referrer. Self-hosting is two `.woff2` files and is
also faster (no second connection, no redirect chain).

⚠ **This required widening a security guard**, which is why it is written down.
`test_the_site_directory_holds_nothing_but_site_assets` refuses any file in the
served root outside an allow-list of extensions, because anything landing here is
published to the internet the moment it arrives. `.woff2` was added to that list.
The guard is doing real work — do not widen it again without the same
deliberation.

### 2. The screenshots are lossless WebP

Measured on the 24 supplied files: **PNG 5.95 MB → WebP lossless 3.34 MB**,
pixel-for-pixel identical. Lossy q92 reaches 2.14 MB but softens thin UI text,
and thin UI text is the entire content of a screenshot gallery — so the 1.2 MB
was not worth it.

Combined with `loading="lazy"`, a visitor pays for the screens they look at
(~140 KB each), not for all 24.

### 3. The gallery works with JavaScript off

All 16 screens are real elements in `gallery.html`. JavaScript hides the inactive
ones and wires the rail, the sub-tabs and prev/next. With JS off the panels
simply stack, which is still a working gallery — as against the `dist/` bundle's
blank page. This is the whole reason the pages were rebuilt rather than copied.

⚠ **The first build of this got it exactly wrong, and every test passed.**
Inactive panels were marked with the `hidden` **attribute** and the stylesheet
carried `.js .ns-screen[hidden] { display: none }`, on the belief that the hiding
was opt-in behind the `.js` hook. It was not. **`hidden` is native HTML and the
browser's own stylesheet already carries `[hidden] { display: none }`** — so that
selector was pure decoration, the UA rule did all the work, and a visitor with
scripting off saw one screen with no way to reach the other fifteen. The
fallback the rebuild existed to provide did not exist.

Visibility is therefore an `is-active` **class**, which means nothing to the UA
stylesheet: with no `.js` on `<html>` neither rule matches and every panel
renders.

**The lesson is about the tests, not the CSS.** The suite checked that all 16
headings, 16 captions and 24 images were in the page source — and they were, so
it was green. Source presence is not visibility. It took loading the page with
the `<script>` tags stripped to see it. Two guards now stand in for that
(`test_no_panel_or_shot_uses_the_hidden_ATTRIBUTE` and
`test_the_visibility_rules_are_scoped_to_the_js_hook`), and ⚠ **the first draft
of the first one could not fail either** — a shell-escaping slip left a literal
backspace byte where each `\b` belonged, so the pattern was `<BS>hidden<BS>` and
matched nothing. Both were then checked by putting the bug back and confirming
the suite went red. Do that after touching either.

### 3b. Two things measured, and reverted

Both looked like improvements and neither was:

* **`<link rel="preload">` for the font.** The pages fetched the face **twice**
  per load — once for the preload, once for the `@font-face` — and every load
  logged *"preloaded using link preload but not used within a few seconds"*.
  Removing the tag took it to exactly one request. The stylesheet is small and
  first in the head, so the round trip it was meant to save was never there.
* **Shrinking the mobile nav's type and button padding** to pull it from three
  visual rows to two. Measured at 141px → 140px, still four rows: the links plus
  the button need ~349px against 335px of usable width at 375px, and 14px is not
  recoverable from type. Reverted rather than left in looking useful.

### 3c. The nav had to be made responsive

Not a decision so much as a defect the design could not have covered: it was
drawn at desktop width, and at 375px the landing nav needs 587px. Because
`.ns-page` clips horizontal overflow the three links and the primary button were
not merely awkward — they were **invisible**, taking the page's main call to
action with them. One media query fixes all three pages by giving `.ns-spacer` a
full-width basis, since it already sits at exactly the right break point on each.

Known and accepted: below ~920px the gallery's rail stacks above the viewer, so a
phone scrolls past 16 screen names before the first screenshot. That is the
design's own flex-wrap behaviour and it reads as a table of contents rather than
as breakage, so it was left alone.

### 4. Gallery is the primary nav action, not Live Screens

The design makes **Live Screens** the nav's primary button. That page is a
deliberate placeholder, so as drawn the most prominent control on the site leads
to an empty room. Gallery took the primary slot; Live Screens is a plain link.

## Copy that changed, and why

The site is a **showcase**: there is no build to download and no backend to
receive anything. Three places in the design assumed otherwise.

| Where | Design | Site | Why |
|---|---|---|---|
| Hero primary | "Run it locally" | "See the screens" → gallery | nothing to run |
| `#start` | "Install locally, connect the Schwab gateway…" + email field + "Get the build" | a close pointing at the gallery and Discord | **a form on a file server can only lie** — there is nothing to POST to |
| Community | Telegram primary / Discord ghost, both `href="#community"` | Discord primary; Telegram ghost, labelled as a bot | the links exist now; and `@SchwabOptionsBot` is a bot, so "Join Telegram" under "Read the flow with other traders" would have oversold it |

Everything else ships verbatim — the hero headline, the four-step dealer-gamma
explainer, the four lenses, the stat band, both safety cards, all 24 captions and
the footer disclaimer. The safety cards ("It does not place real orders", "Your
Schwab portfolio, untouched") are accurate against this repo, so they stayed.

## What the site must never do

* **No link to `APP_HOST`, on any page.** Recorded as noise reduction rather than
  as a security control — the subdomain is in Certificate Transparency logs
  regardless — but the public face should not point at the door. The Caddy half
  is already pinned by `test_the_app_host_is_not_advertised_by_the_public_block`;
  `deploy/tests/` now pins the HTML half.
* **Nothing dynamic and nothing secret.** No form, no call to the app, no
  analytics. This tree is world-readable by definition.
* **No third-party origin at all.** Once Inter is local, the site makes zero
  off-origin requests. A test pins that, so adding one is a decision rather than
  an accident.

## Tests

`deploy/tests/test_site.py`, run with `pytest deploy/tests`:

* every internal `href`/`src` resolves to a file that exists — **the one that
  catches a renamed screenshot**, which is otherwise invisible until someone
  loads the page
* the gallery's shot list and `assets/shots/` agree **in both directions** (a
  file with no reference is dead weight published to the internet; a reference
  with no file is a broken image)
* no page names `APP_HOST`, a loopback address, or the NiceGUI port
* no external origin, against an explicitly empty allow-list
* the pages are real HTML — each carries its `<h1>` and its nav in the source,
  not assembled by script

## Open items

* `live.html` is a coming-soon page by decision, not an oversight. Whatever
  eventually mounts there must not be the app itself.
* **The gallery's content model is `gallery.html` itself**, not a JavaScript
  array. The design kept `SCREENS` in its logic block and derived the rail, the
  tabs and the pager from it at runtime; a page that must work without
  JavaScript cannot do that, so the screens are markup and `gallery.js` only
  decides which one is visible. Adding a screen means editing three things in
  step — the rail button, the panel, and the `rail-N`/`screen-N` ids that pair
  them — and `deploy/tests/test_site.py` checks all three.
* It was generated once from the design's array by a throwaway script kept in
  the session scratchpad, not in the repo: it would be a second content model,
  free to drift from the page it wrote.
