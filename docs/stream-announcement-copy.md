# Wall stream — announcement copy

Post text for X, Facebook and Instagram announcing the `/wall` YouTube stream.

**Replace `YOURHANDLE` with the YouTube handle** (Studio → Customization → Basic
info → Handle) in all three.

⚠ **This copy states facts that drift.** The window (08:00–15:20 CT), the three
panels, and "paper-traded" are all claims about the running system. If
[`config/sessions.toml`](../config/sessions.toml)'s `[windows.stream]` moves, or
the rotation in [`webgui/wall.py`](../webgui/wall.py)'s `PAGES` changes, this
file is wrong until someone edits it. That is why it lives beside the
[design doc](plans/2026-08-31-wall-display-rotation-stream-design.md) rather than
in a notes app.

---

## Order of operations

1. **Verify the link works first.** Open `youtube.com/@YOURHANDLE/live` in a
   private window. A 404 or "video unavailable" means the stream is not public,
   and every post will send people to a dead page.
2. **Instagram bio link BEFORE the Instagram post** — the caption says "link in
   bio", so the bio has to already have it.
3. Post, then pin, on each platform.

---

## X

```
Live dashboard, every US market session.

Dealer gamma positioning for SPX, SPY, NDX and QQQ — flip levels, call and put walls, net GEX. Plus a macro board and sector momentum, rotating on screen.

08:00–15:20 CT. Paper-traded. Built solo in Python.

youtube.com/@YOURHANDLE/live
```

Post → on your profile, find it → **⋯** → **Pin to your profile**.

---

## Facebook

Decide Page or personal profile first. A Page is public by default and gets
insights; a personal post reaches only friends **unless you change the audience
selector to Public**, which is the step most often missed.

```
I've put my trading dashboard on a live stream.

It runs every US market session, 8:00am–3:20pm Central, rotating through three screens:

• Dealer positioning — gamma flip levels, call and put walls, and net GEX for SPX, SPY, NDX and QQQ
• Macro board — around 50 tickers across volatility, breadth, index futures, sectors and factors
• Momentum — where sectors and industries sit in the rotation cycle

Everything is paper-traded, so the positions and P&L on screen are simulated rather than a live account. It's a research tool I built for myself in Python; the stream is just it running.

Nothing on it is advice.

youtube.com/@YOURHANDLE/live
```

Page: **⋯** → **Pin to Top**. Personal: **⋯** → **Feature on profile**.

---

## Instagram

Needs an image; captions carry no clickable link.

```
My trading dashboard now runs live through every US market session.

Three screens on rotation — dealer gamma positioning, a ~50-ticker macro board, and sector momentum. 8:00am–3:20pm Central.

All paper-traded: the positions on screen are simulated, not a live account. Built solo in Python.

Link in bio 🔗

#options #optionsflow #gammaexposure #trading #python #dataviz
```

**Use the Macro Board panel, not the Desk.** The Desk screen shows the position
table with entries, marks and P&L; a still of that sitting permanently on a
social profile is a different exposure from it passing by in a rotation. The
Macro Board is also the better picture — dense and colourful, with no book on it.

**Crop to 4:5, deliberately.** Instagram's default 1:1 cuts roughly a third off
the width of a 1920×1080 dashboard whose text is already small. 4:5 is the
tallest portrait it allows. Choose the region with the volatility and breadth
tiles rather than centring.

---

## Why every post says "paper-traded"

The stream shows a position table with entries, marks and P&L. Without that
clause, a public post carrying those numbers reads as a performance claim. The
on-screen disclaimer slot in `webgui/wall.py` (`DISCLAIMER`) is deliberately
empty by operator decision, which makes the post copy the only place this is
stated. Cutting it is a choice, not a tidy-up.
