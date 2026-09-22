# Posting to X through headless Chrome — assessment

**Date:** 2026-09-22 · **Status:** assessment for an operator decision. Nothing built.

## The question

X posting shipped on 2026-09-22 against the official API. The operator asked
whether headless Chrome could post instead, to reduce exposure to third-party
change, the posting limit and cost.

## What prompted it: the API is now pay-per-use

X replaced tiered pricing on **2026-02-06**. Per
[X's pricing page](https://docs.x.com/x-api/getting-started/pricing):

| item | price |
|---|---|
| a post | **$0.015** |
| **a post containing a URL** | **$0.200** |
| a summoned post | $0.010 |

No subscription, no minimum spend, **no free tier**, and no daily posting cap.

**Our volume:** 5 reports + 7 trade ideas ≈ 12 a weekday ≈ 252 a month. Every
post as built carries `neuralstrike.co` in its text, so every one is in the
$0.200 bracket.

| configuration | monthly |
|---|---|
| as built (link in the text) | **~$50** |
| identical posts, link only on the card image | **~$3.80** |
| 2 reports + 3 ideas a day, link in text | ~$21 |

⚠ Unknown: whether the media-upload request is billed separately. The pricing
page does not say, and it is not worth guessing — the first live week's invoice
settles it.

**So the "limit" concern is already gone** (pay-per-use has no daily cap), and
the shipped `daily_cap: 15` has quietly become a **spend** cap rather than a
quota guard — at $0.20 a post it bounds a runaway bug at $3/day. That is worth
keeping whatever is decided here.

## What the browser route would actually take

The box has Chrome, but only as `chrome --headless --screenshot`
(`tools/capture_live_shots.py`). That takes pictures; it cannot click, type or
attach a file. Posting needs:

- **Playwright or Selenium** plus a driver — a real dependency, and with
  Playwright its own browser download (~150 MB), on a box whose one browser
  today is used for thumbnails.
- **A logged-in session on disk**, created by hand, re-created whenever it
  expires or the password or second factor changes.
- **The account password and TOTP in the automation path.** Today only four API
  keys sit there, and they can be revoked without touching the login.
- **A rewrite of the seam.** `shared/notify/x_post.py` is one function
  (`_send`), so the code change is small — the surrounding operational surface
  is what grows.

## The rule, quoted

X's [Developer Guidelines](https://docs.x.com/developer-guidelines) name this
case exactly, not by inference:

- *"Are you only using the official API (not scraping/browser automation)?"*
- *"App scrapes X via browser automation (not API)"* — listed as prohibited.
- *"Non-API automation (scraping, browser automation) results in permanent
  suspension."*

The Terms of Service add: *"You may not access the Services in any way other
than through the currently available, published interfaces that we provide."*

This is posting our own marketing to our own account, which is the sympathetic
end of the range. It is still the named prohibition, and the stated penalty is
permanent.

## What a suspension would cost

The account **is** the channel: the handle, the followers, the posting history
and the link every card points at. Losing it permanently is not comparable to a
$50 monthly bill, and there is no appeal path for a violation the guidelines
name in a list. The asymmetry, not the ethics, is the decisive argument: the
saving is ~$46/month against an account that would have to be rebuilt from zero.

## The other failure mode: it breaks quietly, and more often

The stated motivation was *less* exposure to third-party change. Browser
automation inverts it:

| | the API | the web interface |
|---|---|---|
| changes | versioned, deprecations announced (we dodged the v1.1 media retirement that way) | unannounced, any week |
| a failure says | `403 duplicate content`, `daily cap` | "button not found" |
| the post's id and link | a field in the response | scraped back, or lost |
| bot detection | not applicable | X fingerprints headless browsers; a login can raise a challenge no script can answer |

The poster would fail silently on a redesign, into a log that cannot say why —
the exact defect class this repo keeps writing guards against.

## Build cost against the saving

The saving over the recommended alternative is **~$46/month**. A browser poster
is a few hours to build, plus a session to babysit and a redesign to chase at
unpredictable times. It does not pay back, even before the suspension risk.

## Options, ranked

1. **Drop the link from the post text.** ~$50 → ~$3.80/month, no code change
   (`x.link = ""`), card image and profile bio carry the address. Costs a tap.
   Link posts are also widely reported to reach fewer people, so the reach cost
   may be negative.
2. **Keep links, tighten `daily_cap`.** ~$50/month, everything tappable.
3. **Keep links, post less.** ~$21/month at 5 posts a day.
4. **Headless Chrome.** ~$0/month, against permanent suspension as the stated
   penalty and a silent-breakage failure mode. **Not recommended.**

## Recommendation

Option 1, and keep `daily_cap` as the spend guard. If the link in the text
turns out to matter for conversion, option 3 buys it back at ~$21/month.
