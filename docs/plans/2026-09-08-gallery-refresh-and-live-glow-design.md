# Gallery refresh, and a live-market glow — design

**Date:** 2026-09-08
**Status:** approved, not yet built

Three changes to the public site at `neuralstrike.co`, all of them consequences
of the live screens having actually shipped:

1. **Live screens** takes the primary-button slot; **App gallery** becomes the
   plain nav link.
2. The Live screens link carries a **green neon glow while the market is open**.
3. The gallery's screenshots are **recaptured every trading day**, because the
   committed ones predate the branding.

## Why now

The site's nav carries a comment explaining its own inversion:

> The design made Live Screens the primary button. That page is a deliberate
> placeholder, so as drawn the most prominent control on the site led to an empty
> room; the gallery took the slot.

That reason is spent. Part 1 is not a redesign — it is **restoring the original
design intent** now that the room has something in it. The comment gets corrected
in place rather than deleted, so the next reader finds the history rather than an
unexplained swap.

---

## Part 1 — the nav swap

`index.html`: `Live screens` and `App gallery` exchange treatments —
`ns-navlink` ↔ `btn btn-primary`. `gallery.html` and `live.html` get the same
ordering so the three pages read consistently.

Nothing else moves. The hero's own "See the screens" / "Open the gallery"
call-to-action buttons are separate controls and are out of scope.

---

## Part 2 — the green neon glow

**The problem.** `neuralstrike.co` is a static file server. It cannot know
whether the market is open, and constraint 1 of the site
(`deploy/site/index.html`'s header comment) forbids anything dynamic: no form,
no call to the app, no analytics.

**The mechanism.** Client-side progressive enhancement — a small deferred
`assets/market-clock.js` that computes the NYSE session state and toggles a class
on the Live screens link; CSS supplies the glow.

This is **precedented, not a new licence**: `gallery.js` already ships with
`defer`, and `test_each_page_carries_its_own_content_without_scripting` asserts
only that a heading and a nav are present in the source. A decorative glow is not
content. With scripting off there is no glow and the link still works, which is
the correct degradation.

**Session state is computed, not fetched.** `Intl.DateTimeFormat` with
`timeZone: "America/New_York"` gives the exchange's own wall clock from any
visitor's machine, so the answer does not depend on where they are or how their
clock is set relative to UTC.

⚠ **The holiday list is GENERATED from `shared/market_calendar`, never typed.**
CLAUDE.md is explicit — *"Do not add a new holiday literal or window constant
anywhere"* — and that module derives NYSE holidays algorithmically, so a hand
list in JavaScript would be an eleventh copy that silently rots. A small
generator emits the current and next year into the JS file.

⚠ **Half-days matter, and NOTHING IN THIS REPO KNOWS ABOUT THEM.** The NYSE
closes at 13:00 ET on the day after Thanksgiving, on Christmas Eve and on July
3rd when those fall on a weekday. A glow still lit at 15:00 on Black Friday is
exactly the kind of small, confident, wrong claim this project keeps writing
down.

An earlier draft of this document asserted that `shared/market_calendar` knows
them and the generator need only emit them. **That was false** — measured
2026-09-08, there is no `is_half_day`, no early-close field and no 13:00
reference in `market_calendar.py`, `config/sessions.toml`, or anywhere else in
the tree. The module derives *holidays* algorithmically and stops there.

So this is a fork, and it is recorded as an open decision rather than assumed:

* **Add early-close support to `shared/market_calendar`.** The right home — it
  is the calendar module, and the standing rule is that no calendar fact lives
  anywhere else. The three dates are derivable from rules the module already
  has (`_nth_weekday` for Thanksgiving, `_observed` for the shifting ones), and
  everything downstream gains a fact it currently lacks. Costs real logic and
  real tests, on rules with genuine corner cases (when July 4th falls on a
  Saturday, July 3rd is the *observed holiday*, not a half day).
* **Ship without it, and say so.** The glow is decoration on a marketing page;
  being lit on roughly three afternoons a year is a cosmetic error with no
  downstream consumer. Cheaper, and honest **only if the gap is written into
  the code rather than left for someone to discover.**

**Three alternatives were rejected:**

- **A marker file the capture timer writes.** Simpler logic, but the timer only
  runs *inside* the window, so nothing would ever write the "closed" state — the
  glow would latch on at the close and stay until the next morning.
- **A same-origin fetch of session state.** Adds a request to a page whose whole
  design is that it makes none, to learn something the client can compute.
- **An always-on glow.** Not what was asked, and it would make the one signal on
  the page meaningless.

---

## Part 3 — the gallery refresh

### What is actually wrong

The committed shots were captured in `2e53ba9 feat(site): the public marketing
site`. **Six brand commits landed after it**, including
`a4f3d2a fix(brand): the wordmark's tracking is config, and the app mark was
small`. So every shot carries the pre-fix header lockup: the wordmark without the
chevron mark, at the old tracking.

### ⚠ Why the live captures cannot be reused

The obvious cheap answer — reuse `deploy/site/live/*.webp`, which already
refresh every 15 minutes — **cannot satisfy this requirement at all**, and the
reason is worth stating because it is not obvious:

**The branding lives in the app header, and the public live shell has no
header.** `live_main.py` renders no rail, no header and no breadcrumb — that is
its design. A gallery rebuilt from those captures would carry *no* brand mark
anywhere, which is worse than the old lockup it replaced.

It also reaches only 12 of the 15 screens; Strategy Calculator, Strategy Finder
and Stock Evaluation are behind the login and have no public mirror.

### The mechanism

`tools/capture_gallery_shots.py` mints an `ns_session` cookie from
`shared/webgui_auth.json` and drives headless Chrome against
`127.0.0.1:8500` — the **private** app, with its header.

This works because `auth.mint_token` is **stateless with no server-side
registry**: given the store's `session_secret` and `epoch`, a token is a pure
computation. No password, no TOTP, no network.

⚠ **This is the one genuinely new capability, and it should be recorded as
such.** Today `tools/capture_live_shots.py` has *zero* app access — it reads a
public origin. Afterwards, a capture tool can authenticate as the owner. That is
not privilege escalation (it already runs as the owner, on the owner's box,
reading a file the owner owns), but the blast radius of that script changes, and
`.env.live`'s whole existence is an argument about blast radius.

### The screen map — 15 screens, 22 shots

**Daily Briefings is dropped** (2 shots), per the decision recorded below.

| # | Screen | Shots | Route(s) |
|---|---|---|---|
| 0 | The Desk | 1 | `/desk` |
| 1 | Gamma Heatmap | 1 | `/options/gamma` |
| 2 | Premium Divergence | 1 | `/options/gamma?view=Flow` |
| 3 | Net Options Premium | 1 | `/options/gamma?view=Net+Prem` |
| 4 | Opportunity Board | 1 | `/options/matrix` |
| 5 | Flow Alerts | 1 | `/options/flow` |
| 6 | Macro Board | 1 | `/market` |
| 7 | Market Regime Control | 1 | `/sentiment` |
| 8 | Where the Market Stands | 1 | `/sentiment/bullbear` |
| 9 | Sector & Industry Performance | 1 | `/sentiment/sectors` |
| 10 | Sector Rotation | 2 | `/sentiment/rotation`, `/sentiment/rrg` |
| 11 | Momentum | 1 | `/sentiment/momentum` |
| 12 | Strategy Calculator | 5 | `/options/calculator`, `/options/expected-move`, `/options/simulator` … |
| 13 | Strategy Finder | 1 | `/options/swing` |
| 14 | Stock Evaluation | 3 | `/trade`, `/trade/evidence`, `/trade/plan` |

⚠ **Screen 12's five routes are inferred from its captions** ("Build the
structure", "Expected moves", "Volatility vs the Greeks", "What-if: pricing")
and must be **confirmed against the existing images during implementation**. A
wrong mapping does not fail a test — it silently publishes the wrong screenshot,
which is the same class as the mis-mapped screen this design already corrected
("Where the Market Stands" is `/sentiment/bullbear`, not a `/sentiment` variant).

### Gamma's three views come from query parameters

`/options/gamma` renders one view per page build, so the three gamma shots need
three URLs. The private page gains `symbol` and `view` parameters, exactly
mirroring the precedent already in `main.py`:

```python
@_page("/sentiment/momentum")
def sentiment_momentum_page(level: str = "industry") -> None:
    # ?level=stock deep-links the Stocks view (the dropdown still switches it
    # in place); render() coerces anything unknown back to industry.
```

⚠ **This is not the injection hazard closed on 2026-09-07, and the difference is
the point.** That one was an *accidental* parameter — `def _page(_s=screen)`, a
late-binding idiom that handed FastAPI a `Screen` object and let
`GET /desk?_s=anything` reach `importlib.import_module`. This is a **declared,
typed, coerced `str`** on a page behind the login, and `gamma._resolve_view()` is
already total: anything unknown falls back to `GEX`. The rule that separates them
is "never let a parameter reach code" — not "never take a parameter".

### Three consequences, each with a decision

**1. The shots become gitignored.** They are tracked today. A daily rewrite on
prod would dirty the tree, and `tools/promote.sh` refuses a dirty tree before it
touches anything. Same treatment as `deploy/site/live/`, including the explicit
exemption in `test_every_internal_reference_resolves_to_a_file` — an exemption
with a comment, never a deletion, because that test is what catches a renamed
screenshot. **Accepted cost:** a fresh clone has no gallery images until the
timer first runs.

**2. The nav rail returns.** The committed shots are hand-cropped at **23
distinct sizes** to remove the 68px icon rail. An automated capture is uniform
and includes it. Keeping the rail is the decision: it is honest product imagery,
and the alternative is a per-screen cropping rule nobody will maintain.

**3. `gallery.html`'s declared dimensions are rewritten once** to the uniform
capture size, so `test_every_image_declares_its_size` keeps holding and the
layout does not shift as shots are replaced.

### Schedule

A new `[slots]` entry in `config/sessions.toml`, ~30 minutes after the open, so
the screens carry live data rather than an empty pre-open state. A slot rather
than a window: this runs **once** per trading day, and `[slots]` is the
established shape for a named clock mark.

`shared/market_calendar.in_window` / the slot machinery already gates on
`is_trading_day`, so weekends and holidays fall out with no second calendar.
Outside the slot the script stands down and **exits 0** — a holiday is a normal
outcome, and a non-zero exit restart-storms into `StartLimitBurst`.

---

## Failure modes

| Failure | Result |
|---|---|
| Auth store missing or unreadable | Capture refuses and exits non-zero — a `oneshot` with no `Restart=` lands in `--failed`, visible, and the previous shots stay. |
| Session epoch rotated (a password change) | Same: the mint produces a token the app rejects, every page 303s to `/login`, and the run must fail loudly rather than publish 22 screenshots of a login form. **Assert on a DOM marker, not on HTTP 200.** |
| One screen fails to render | Logged and skipped; the rest publish; exit 0. A missing tile is a gap; a dead timer is 22 stale ones. |
| Chrome absent | Exit non-zero. It never self-heals. |
| No JS (Part 2) | No glow; the link works. |
| Clock skew on a visitor's machine (Part 2) | The glow is wrong for that visitor only, by their own clock error. Acceptable for decoration; it is why nothing else depends on it. |

## Tests

- The nav swap is asserted **structurally** — Live screens carries the primary
  class and App gallery does not — rather than by matching a string that a copy
  edit would break.
- The generated holiday list is **compared against `shared/market_calendar`**, so
  a regenerated file that lost a holiday fails rather than shipping a glow on
  Thanksgiving.
- The screen map is pinned: every route in the map resolves to a registered app
  route, and every shot referenced by `gallery.html` is produced by the capture
  list. That is the guard against the silent mis-map above.
- Half-day handling gets its own case **if it is built** — it is the one a naive
  implementation gets wrong. If it is not built, the test asserts the documented
  gap instead, so the limitation is pinned rather than forgotten.
- The existing site guards keep holding: no app host, no off-origin resource,
  every image declares its size.

## Deliberately not built

- **Capturing Daily Briefings.** Dropped from the gallery by decision; its two
  shots are removed with it.
- **Cropping the rail** to match the old hand-cropped geometry.
- **A live-market indicator anywhere but the Live screens link.** One signal, one
  place.
- **Server-side session state.** The site makes no requests; that is the point.
