# Public Live Screens Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish fourteen read-only trading screens live and unauthenticated at `live.neuralstrike.co`, reached from a thumbnail grid on the existing static site.

**Architecture:** A second NiceGUI process (`webgui/live_main.py`, port 8501) imports the *same* `pages/*` modules the app uses, behind a minimal shell with no nav rail. It is a fourth Caddy origin — a peer of the app on :8500, not a window onto it. Read-only is enforced at four layers: a Redis ACL, a `bus_client.request` refusal, a frozen `app_settings`, and the fact that the process never imports `main` and so has no `/terminate` route to serve.

**Tech Stack:** NiceGUI, FastAPI/uvicorn, Redis (`shared.bus`), Caddy, systemd user units, headless Chrome, pytest.

**Design doc:** `docs/plans/2026-09-07-public-live-screens-design.md`

---

## Before you start

Read the design doc. Then read these three, because each records a trap this plan walks past:

- `webgui/wall.py` module docstring — why the live screens compose the real pages instead of re-implementing them.
- `deploy/site/index.html` header comment + `deploy/tests/test_site.py` — the three constraints the static site holds, none of which this plan relaxes.
- `CLAUDE.md` § "3-tier architecture" — the Tier-1 import allow-list. The live process is Tier 1 and must stay inside it.

**Environment.** You are in a git worktree. A worktree has **no venv of its own** — use the checkout's absolute path. On this Windows machine the tests run as:

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q
```

On the Linux prod/dev boxes it is `../.venv/bin/python -m pytest -q`. Adapt per host; the plan writes the Linux form.

**⚠ Do not preview from this worktree.** A worktree has no `config/env.local.toml`, so `repo_paths` resolves it to **prod** and it would bind :8500 — where the live prod stack already is. See CLAUDE.md § "Verify in the browser".

---

## Task 1: The live port, offset by environment

**Files:**
- Modify: `config/ports.toml`
- Modify: `repo_paths.py` (`_derive_ports`, constants block)
- Test: `tests/test_env_profile.py`

**Step 1: Write the failing test**

Add to `tests/test_env_profile.py`:

```python
def test_the_live_port_is_offset_like_the_app_port():
    """⚠ ports.toml warns that a TOP-LEVEL port is not offset unless
    _derive_ports is taught about it, and that this is "a BUG for one it does
    [start]". nicegui_live is one this repo starts: unoffset, a dev checkout
    would bind prod's 8501 and the collision would be invisible in the config.

    Pinned against the app port rather than against a literal, so the two can
    never drift apart."""
    import repo_paths
    base = repo_paths._load_ports()          # whatever the loader is named
    dev = repo_paths._derive_ports(base, {"port_offset": 1000})
    prod = repo_paths._derive_ports(base, {"port_offset": 0})

    assert prod["nicegui_live_port"] == prod["nicegui_port"] + 1
    assert dev["nicegui_live_port"] == prod["nicegui_live_port"] + 1000
```

Read `tests/test_env_profile.py` first and match how it already obtains the base
port table — do not invent `_load_ports` if the module names it otherwise.

**Step 2: Run it and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_env_profile.py -k live_port -q
```

Expected: `KeyError: 'nicegui_live_port'`.

**Step 3: Add the port**

In `config/ports.toml`, directly under `nicegui = 8500`:

```toml
nicegui = 8500
# The PUBLIC read-only screens (live.neuralstrike.co), a second NiceGUI process.
# Offset with `nicegui` -- see the header note: a top-level port this repo
# STARTS must be offset, or dev binds prod's and the collision is invisible.
nicegui_live = 8501
```

In `repo_paths.py`, inside `_derive_ports`'s return dict:

```python
"nicegui_port": int(ports["nicegui"]) + off,
"nicegui_live_port": int(ports["nicegui_live"]) + off,
```

And in the constants block beside `NICEGUI_PORT`:

```python
NICEGUI_LIVE_PORT = _derived["nicegui_live_port"]
NICEGUI_LIVE_URL  = f"http://127.0.0.1:{NICEGUI_LIVE_PORT}"

# The public read-only origin. Sibling of APP_HOST, and deliberately NOT a path
# under it: the app is behind a login and the live screens are not, so they are
# separated by ORIGIN rather than by a path filter someone has to get right.
LIVE_HOST = str(_env_local.get("live_host") or f"live.{SITE_HOST}").strip().lower()
```

**Step 4: Run and confirm green**

```bash
.venv/bin/python -m pytest tests/test_env_profile.py -q
```

**Step 5: Commit**

```bash
git add config/ports.toml repo_paths.py tests/test_env_profile.py
git commit -m "feat(live): the live screens' port, offset by environment"
```

---

## Task 2: Extract the page↔shell seam into `webgui/shell.py`

This is the task the whole design turns on. `pages/options/gamma.py` does
`import main as _shell`; in the live process that executes `main.py`'s body and
registers **every** `@_page` route, publishing `/terminate` to the internet.

**Files:**
- Create: `webgui/shell.py`
- Modify: `webgui/main.py` (move four functions + three module constants out, re-import them)
- Modify: `webgui/pages/options/gamma.py`, `webgui/pages/options/rescue.py`, `webgui/pages/options/scanner.py`, `webgui/pages/options/simulator.py`, `webgui/pages/portfolio.py`
- Test: `webgui/tests/test_shell_seam.py` (new)

**Step 1: Write the failing test**

Create `webgui/tests/test_shell_seam.py`:

```python
"""The page-to-shell seam, and the reason it is a module of its own.

``pages/options/gamma.py`` used to do ``import main``. In a SECOND NiceGUI
process that executes main.py's module body, and that body registers every
``@_page`` route -- so a public live process importing it would serve
``/terminate`` (Stop All Services) and ``/settings`` to the internet, silently,
while looking entirely correct.

These tests pin the seam as a leaf module both entrypoints can provide.
"""
import ast
import pathlib

_PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def _imports_main(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "main" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "main":
            return True
    return False


def test_no_page_imports_main():
    """THE GUARD THAT KEEPS /terminate OFF THE PUBLIC ORIGIN.

    A page that imports ``main`` cannot be rendered by any entrypoint other than
    main.py without dragging the whole route table along. Every page reaches the
    shell through ``shell.py`` instead."""
    offenders = [p.relative_to(_PAGES).as_posix()
                 for p in _PAGES.rglob("*.py") if _imports_main(p)]
    assert offenders == [], (
        f"these pages import main and so cannot be served by the live "
        f"process: {offenders}")


def test_the_seam_exposes_what_the_pages_actually_call():
    import shell
    for name in ("subtab_slot", "bind_breadcrumb_leaf",
                 "set_breadcrumb_leaf", "_view_name"):
        assert hasattr(shell, name), f"shell.py is missing {name}"


def test_main_still_exposes_the_seam_for_backwards_compatibility():
    """main.py re-exports the seam so anything still reaching for
    ``main.set_breadcrumb_leaf`` keeps working."""
    import main
    import shell
    assert main.subtab_slot is shell.subtab_slot
    assert main.set_breadcrumb_leaf is shell.set_breadcrumb_leaf
```

**Step 2: Run it and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_shell_seam.py -q
```

Expected: `test_no_page_imports_main` fails listing five pages; `import shell` fails.

**Step 3: Create `webgui/shell.py`**

Move these out of `webgui/main.py` **verbatim**, docstrings included:

- `_CRUMB_LEAF` and `_CRUMB_CONTEXT` (main.py:833-834)
- `_breadcrumb_leaf` (main.py:840)
- `_view_name` (main.py:843)
- `set_breadcrumb_leaf` (main.py:858)
- `bind_breadcrumb_leaf` (main.py:886)
- `_SUBTAB_SLOT` (main.py:1248)
- `subtab_slot` (main.py:1251)

Header for the new file:

```python
"""The page-to-shell seam: the handful of shell services a PAGE may call.

⚠ This module exists so that a page never has to ``import main``. main.py's
module body registers every ``@_page`` route, so importing it from a second
NiceGUI process -- the public live screens on ``live_main.py`` -- would publish
``/terminate`` and ``/settings`` to the internet while looking correct. Keeping
the seam here means the public process is STRUCTURALLY incapable of holding the
app's route table, rather than incapable by inspection.

Both entrypoints provide it: ``main.py`` populates the slots from ``_layout``;
``live_main.py`` leaves them empty, and every function here is already a no-op
without a mounted header. Not to be confused with ``pages/trade_shell.py``,
which is the Trade section's shared page body.
"""
```

`bind_breadcrumb_leaf` uses `@guard` — carry `from pages.ui_guard import guard`
across with it. Check main.py's actual import line and mirror it.

In `main.py`, replace the moved definitions with a re-export:

```python
# The page-to-shell seam now lives in shell.py so a page never imports main --
# see that module's docstring. Re-exported here because pages and tests have
# reached for main.set_breadcrumb_leaf since 2026-07.
from shell import (_CRUMB_CONTEXT, _CRUMB_LEAF, _SUBTAB_SLOT,  # noqa: F401
                   _breadcrumb_leaf, _view_name, bind_breadcrumb_leaf,
                   set_breadcrumb_leaf, subtab_slot)
```

**⚠ `_SUBTAB_SLOT` and `_breadcrumb_leaf` are mutable dicts mutated by
`_layout`.** The re-export binds the *same object*, so `_layout`'s
`_SUBTAB_SLOT["el"] = ...` still reaches `shell.subtab_slot()`. Do **not**
rewrite those to reassignment (`_SUBTAB_SLOT = {...}`) — that would rebind
main's name only and silently break every subtab row.

**Step 4: Update the five pages**

In each of `gamma.py`, `rescue.py`, `scanner.py`, `simulator.py`,
`portfolio.py`, change:

```python
import main as _shell
```

to:

```python
import shell as _shell
```

Every `_shell.subtab_slot()` / `_shell.bind_breadcrumb_leaf(...)` /
`_shell._view_name(...)` call site is unchanged. Update the neighbouring
comments that say "main.subtab_slot()" to "shell.subtab_slot()".

**Step 5: Run the seam tests, then the whole webgui suite**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_shell_seam.py -q
cd webgui && ../.venv/bin/python -m pytest -q
```

Expected: seam tests pass; the full suite's failing **set** is unchanged from
your pre-task baseline. **Capture that baseline before Task 1 and diff the node
IDs, never the count** — CLAUDE.md documents an incident where two real
regressions hid behind two tests flipping to skipped while the total held.

**Step 6: Commit**

```bash
git add webgui/shell.py webgui/main.py webgui/pages webgui/tests/test_shell_seam.py
git commit -m "refactor(webgui): the page-shell seam becomes its own module

A page that imports main drags the whole @_page route table with it, so the
public live process would serve /terminate. shell.py is the seam both
entrypoints provide instead, and a test pins that no page imports main."
```

---

## Task 3: `gamma.render()` takes a pinned symbol and view

Four of the fourteen screens are views of `/options/gamma`. Precedent for the
shape: `sentiment_momentum.render(level="industry")` already does exactly this.

**Files:**
- Modify: `webgui/pages/options/gamma.py` (`render`, ~line 1883; the view tabs, ~1919)
- Test: `webgui/tests/test_gamma.py`

**Step 1: Write the failing test**

```python
def test_render_accepts_a_pinned_symbol_and_view():
    """The public live screens pin Gamma to $SPX/GEX, Net Prem, and Flow on SPY
    and QQQ. Pinning is an optional keyword on the REAL render so there is one
    implementation and the public screen cannot drift from the private one."""
    import inspect
    from pages.options import gamma
    sig = inspect.signature(gamma.render)
    assert sig.parameters["symbol"].default is None
    assert sig.parameters["view"].default is None


def test_a_pinned_view_must_be_one_the_page_actually_has():
    """A typo'd pin would otherwise render the default view and look correct."""
    from pages.options import gamma
    assert gamma._resolve_view("Net Prem") == "Net Prem"
    assert gamma._resolve_view("Flow") == "Flow"
    assert gamma._resolve_view("nonsense") == "GEX"
    assert gamma._resolve_view(None) == "GEX"
```

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_gamma.py -k "pinned" -q
```

**Step 3: Implement**

Beside `_VIEW_ORDER` (gamma.py:1780):

```python
def _resolve_view(view):
    """A pinned view name, or the page's default.

    Total on purpose: an unknown name falls back to GEX rather than raising, so
    a bad pin degrades to the normal page instead of a 500 on a public origin.
    Callers that care assert against _VIEW_ORDER themselves."""
    return view if view in _VIEW_ORDER else "GEX"
```

Change the signature:

```python
def render(symbol: str | None = None, view: str | None = None):
```

Extend the docstring with a line saying both are pins used by the public live
screens and default to today's behaviour.

At the view-tabs construction (currently `ui.tabs(value="GEX")`):

```python
_pinned_view = _resolve_view(view)
tabs = ui.tabs(value=_pinned_view).classes("compact-subtabs").props(...)
```

For the symbol: find where the page seeds its symbol input/state and seed it
from `symbol` when given. Read that block before editing — do not guess the
state key. Keep `None` meaning "behave exactly as today".

**Step 4: Run**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_gamma.py -q
```

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_gamma.py
git commit -m "feat(gamma): render() accepts a pinned symbol and view"
```

---

## Task 3b: Per-symbol gamma snapshots for the published screens

**⚠ Added mid-execution, 2026-09-07.** Task 3's implementer found, and the
controller independently confirmed, that Task 3's pin is **necessary but not
sufficient** for three of the four gamma-derived screens.

**The problem.** `cache:options:gamma` is a **single, symbol-agnostic key**
holding whichever symbol was last refreshed
(`services/options_svc/handlers.py:239`). Worse, `refresh_gamma_current` reads
the symbol back *out* of that key (`handlers.py:1187-1208`), so the slot is
**sticky and driven by whatever the private app last looked at**. The per-view
history keys have the same shape: `gamma_history_key(view)` is keyed by view
only, with the symbol carried *inside* the payload for the page to check.

Three consequences, all of which would have been found in a browser at Task 12
and none of which Task 3 could fix:

| Screen | Without this task |
|---|---|
| Gamma (`$SPX`) | Renders whatever symbol the private app last selected. Open `/options/gamma` and pick AMD, and the public "$SPX" screen shows AMD. |
| Premium Divergence · SPY | Renders `$SPX` data. The panel labels itself from `snap["symbol"]`, so it is honest rather than wrong — but it is not SPY. |
| Premium Divergence · QQQ | Same. |

Net Prem is unaffected: it has its own multi-symbol key
(`cache:options:net_premium`) and is symbol-independent by construction.

**The cost is near zero, which is what makes this the right fix.**
`config/symbols.toml` `[collection] base` is
`["$SPX", "$VIX", "SPY", "QQQ", "$NDX"]` — the collector **already fetches SPY
and QQQ chains every minute**. Publishing per-symbol snapshots for the three
published symbols reuses chains the service has already paid for, *provided*
you extend the existing tick-chain stash rather than refetching.

**⚠ Read `handlers._stash_tick_chain` / `_take_tick_chain` before writing
anything.** They exist precisely to stop the same tick fetching one symbol's
chain twice, and today they hold exactly ONE symbol. Extending them to hold the
three published symbols is the difference between this task costing nothing and
costing ~880 extra Schwab calls/day against a budget already at 68–76k.
**If you cannot make the stash serve all three, stop and report the measured
call cost rather than shipping the refetch.**

**Files:**
- Modify: `services/options_svc/handlers.py` (publish + schedule)
- Modify: `webgui/pages/options/gamma.py` (read the per-symbol key when pinned)
- Test: `services/options_svc/tests/` and `webgui/tests/test_options_gamma.py`

**Design constraints:**

1. **The published keys are ADDITIVE.** `cache:options:gamma` and its history
   keys keep their exact shape and meaning; the private page is untouched. A
   new `cache:options:gamma_pub:<symbol>` (and its per-symbol history keys)
   serves the public screens. Do **not** re-key the existing cache — that would
   change the private app's behaviour for a public feature, and this repo's
   standing rule is that the app's screens are the source of truth the public
   ones mirror, never the reverse.
2. **`gamma.render(symbol=...)` reads the published key; `render()` bare reads
   today's.** One implementation, one renderer, two data sources — the drift
   risk is in the data, not the drawing, which is the acceptable half.
3. **Publish only the three symbols the screens name.** `$SPX`, `SPY`, `QQQ`,
   sourced from `live_screens.SCREENS` if that is importable from Tier 2
   without breaking the tier rule — **it is not** (`services` may not import
   `webgui`), so this is a documented cross-tier mirror. Add it to
   `shared/tests/test_cross_tier_mirrors.py`, which exists for exactly this and
   already pins two other such pairs.
4. **A symbol with no published snapshot must degrade to the page's existing
   "no data" state**, never to another symbol's data. `refresh_gamma` already
   caches a graceful-empty `{"symbol", views:{}}` for a failed chain fetch —
   reuse that shape rather than inventing one.
5. **Ordering is load-bearing and already documented.** `_publish_gamma` writes
   history keys FIRST, then the main payload, "because the page reacts to the
   MAIN key's version bump and then reads the history it needs". Preserve that
   for the published keys, and keep the symbol inside every history payload so
   the reader can still refuse a mismatch.

**Verification that actually proves it:** enqueue nothing and read the keys
directly —

```bash
.venv/bin/python -c "
from shared.bus import Bus
b = Bus()
for s in ('\$SPX','SPY','QQQ'):
    env = b.cache_get(f'cache:options:gamma_pub:{s}')
    p = (env.payload if env else None) or {}
    print(s, '->', p.get('symbol'), 'views:', sorted((p.get('views') or {})))"
```

Each must report **its own** symbol. That is the whole point of the task, and
it is the check Task 12 will repeat in a browser.

---

## Task 3c: A pinned screen shows one view, and pays for one history

**⚠ Added mid-execution, 2026-09-07,** narrowing Task 3b after it measured and
escalated its own cost: twelve published history keys ran the tick's gamma
writes to ~4x the private page's own, a multiplication of exactly the cost the
2026-08-20 history split was written to remove.

**The product decision that unlocks the saving.** The public gamma screens show
**only their pinned view**; the subtab row is not rendered on them. That is the
more faithful reading of the request ("Premium Divergence — will display SPY"),
and it means nothing can render empty, because there is no control to click.

**What follows from it.**

1. **No picker when a view is pinned** — `gamma.shows_view_picker(view)` gates
   the build, and the `bind_breadcrumb_leaf` call goes with it (it binds *to* the
   tabs element, and the live shell has no breadcrumb anyway). Gated on the PIN,
   not on the shell slot being absent: "no slot" already means "mount inline".
   `_PinnedView` stands in for the tabs so the dozen `view_toggle.value` readers
   downstream are untouched.
2. **One history key, not twelve** — `handlers.PUBLISHED_GAMMA_HISTORY_VIEWS`
   maps symbol → the views whose history is published. `$SPX` pins GEX and draws
   the intraday heatmap, which IS the history; SPY and QQQ pin Flow, whose
   `_render_view` branch draws `snap["flow"]` + `snap["prem_ladder"]` from the
   MAIN payload and returns before it touches the per-view cache. Its **keys are
   also the published symbol list**, so a symbol cannot be published without an
   entry saying why. The rows are still POPPED from every payload — not writing a
   key must not mean leaving them inline.
3. **No enqueue from a pinned render** — `gamma.may_enqueue(symbol, view)`
   (named `may_enqueue_refresh` until Task 3d generalised it) gates the 120 s
   timer, the Refresh-now button and the hand-off path, and the strip stops counting down to a refresh it will
   never make. On a public origin that enqueue let every anonymous visitor drive
   a Schwab chain fetch; the read-only bus client of Task 5 is the backstop, this
   is the design.
4. **Startup seeds all three** — `$SPX` has a private page to warm it, SPY and
   QQQ do not, so a cold Redis left their screens on a key nobody had written
   until the first collection tick (outside market hours: the next trading day).
   Two extra chain fetches per service restart.

**Measured** through the real `_publish_gamma` against a fakeredis bus,
close-of-session shape (376 rows x 80 strikes), one `refresh_gamma_current` tick
with the private page parked on `$SPX`:

| | writes | bytes | vs private alone |
|---|---|---|---|
| private key alone (pre-3b) | 5 | 4.94 MB | — |
| Task 3b | 21 | 19.75 MB | 4.00x |
| Task 3c | 10 | 6.28 MB | 1.27x |

⚠ **Un-pinning a public screen's view means adding that symbol's views back to
`PUBLISHED_GAMMA_HISTORY_VIEWS`**, or it draws an empty heatmap — silently, since
a missing history key reads as "no history yet".

⚠ **Still open at Task 3c** — closed by Task 3d below: Explain, Analyze and the
history-report button also enqueue commands, and `gamma_analyze` is a **paid
Claude call**.

---

## Task 3d: A pinned screen sends no command at all

**⚠ Added mid-execution, 2026-09-07,** closing the hole Task 3c flagged in its own
report. Task 3c gated the refresh; this gates the other three, which are the
expensive ones.

`may_enqueue_refresh` is now **`may_enqueue`** — same signature, same semantics
(`symbol is None and view is None`), renamed because it governs every command
this page can send, not one of them. No alias: an alias is how two names for one
concept survive.

**The four commands, and what each costs on an unauthenticated origin.**

| command | cost per anonymous click | reached from |
|---|---|---|
| `gamma_refresh` | a Schwab chain fetch + a full engine pass | Refresh now, the Symbol dropdown, the 120 s timer, the Flow-Alerts hand-off |
| `gamma_explain` | a standalone infographic build | Explain |
| `gamma_analyze` | a **paid Claude API call** | Analyze |
| `gamma_history` | a server-side report build | the History row's Open |

**Two gates, not one, and they prove different things.**

1. **Every enqueue site opens with `if not _may_enqueue: return`.** That is a
   *total* proof — it covers the control, the timer, the hand-off path and any
   closure that reaches the function, which no "is the control built?" check can
   do on its own.
2. **No control that reaches one is BUILT.** Refresh now, Explain, Analyze and
   the whole History row (Date, Slot, Open) are `None` on a pinned render, and
   the Symbol dropdown becomes a `_PinnedSymbol` stand-in — the same shape
   `_PinnedView` already uses for the view picker. A button that cannot work must
   not be drawn; the design doc says so, and the read-only bus client of Task 5
   is the backstop that makes it true rather than merely tidy. Both, not either.

**The three report watchers go too**, and this is the part that is easy to miss.
`_watch_explain` / `_watch_analyze` / `_watch_history` open a new browser tab when
their cache version moves. With the buttons gone there is no click of *ours* to
complete — but the version still moves, because the OWNER can click Explain on the
private app. Left wired, one private click would pop a tab in every anonymous
visitor's browser, pointed at a route the live process does not even serve.

**The test that matters is source-level** —
`test_every_command_this_page_can_send_is_gated_on_the_pin` AST-walks `gamma.py`
for every `bus_client.request(`, finds each call's innermost enclosing function,
and asserts it opens with the guard. A fifth command added next year is covered
without anyone remembering to add it. Its companion,
`test_the_walker_finds_every_enqueue_in_the_source`, asserts the walk found as
many call sites as the file has occurrences — an AST walk that silently matched
nothing would make the whole thing vacuously true. Same idiom as
`test_no_inline_style.py` and `test_auth_covers_every_route.py`.

⚠ **`render()` bare is unchanged**, proved by element-tree fingerprint (element
count, kinds, tab names, timer callbacks, label texts, event listeners) taken
before and after: identical. A textual diff of the source is not proof, because
every change here is conditional.

⚠ **Briefings is deliberately NOT gated.** Its menu items only
`ui.navigate.to("/options/analyze?slot=...")` — they send no command, so this
task's rule does not reach them. It *is* a dead control on a public screen (that
route is private), which belongs to whichever task registers the live process's
routes.

---

## Task 4: `app_settings.freeze()`

**Files:**
- Modify: `webgui/app_settings.py`
- Test: `webgui/tests/test_app_settings.py`

**Step 1: Write the failing test**

```python
def test_freeze_pins_values_and_makes_set_a_no_op(tmp_path, monkeypatch):
    """⚠ This is not only about pinning the public screens' defaults.

    settings.json is a SINGLE-USER store whose in-memory cache assumes one
    writer in one process (see the module docstring). Unfrozen, the live process
    would read the user's live preferences -- changing your own Macro Board skin
    would re-skin the public site -- and race the app for the file."""
    import app_settings
    monkeypatch.setattr(app_settings, "_PATH", tmp_path / "settings.json")
    app_settings.reset_cache()
    try:
        app_settings.freeze({"macro_skin": "B"})
        assert app_settings.get("macro_skin") == "B"

        app_settings.set("macro_skin", "A")
        assert app_settings.get("macro_skin") == "B", "a frozen store accepted a write"
        assert not (tmp_path / "settings.json").exists(), "a frozen store touched disk"

        # Keys with no pin still read their defaults.
        assert app_settings.get("alert_sound") == app_settings.DEFAULTS["alert_sound"]
    finally:
        app_settings.unfreeze()
        app_settings.reset_cache()


def test_unfreeze_restores_normal_behaviour(tmp_path, monkeypatch):
    import app_settings
    monkeypatch.setattr(app_settings, "_PATH", tmp_path / "settings.json")
    app_settings.reset_cache()
    app_settings.freeze({"macro_skin": "B"})
    app_settings.unfreeze()
    app_settings.reset_cache()
    app_settings.set("macro_skin", "A")
    assert app_settings.get("macro_skin") == "A"
```

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_app_settings.py -k freeze -q
```

**Step 3: Implement**

```python
# A frozen store is the public live screens' settings layer: DEFAULTS with a
# fixed overlay, and set() disabled. Two jobs in one primitive -- it pins the
# published screens' state, and it stops the public process reading or racing
# the single-user settings.json the app writes.
_frozen: dict | None = None


def freeze(overrides: dict) -> None:
    """Pin settings to DEFAULTS + ``overrides`` and disable ``set``."""
    global _frozen
    _frozen = {**DEFAULTS, **overrides}
    _cache["data"] = None


def unfreeze() -> None:
    """Undo :func:`freeze` (test helper; the live process never calls it)."""
    global _frozen
    _frozen = None
    _cache["data"] = None


def is_frozen() -> bool:
    return _frozen is not None
```

Then in `load()`, first line:

```python
if _frozen is not None:
    return dict(_frozen)
```

And in `set()`, first line:

```python
if _frozen is not None:
    return          # read-only store: the public live screens cannot write
```

Read the existing `load`/`set`/`get`/`reset_cache` bodies and place these so the
cache logic is not bypassed in the unfrozen path.

**Step 4: Run**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_app_settings.py -q
```

**Step 5: Commit**

```bash
git add webgui/app_settings.py webgui/tests/test_app_settings.py
git commit -m "feat(settings): a frozen, read-only settings store"
```

---

## Task 5: `bus_client` read-only mode and an ACL connection URL

**Files:**
- Modify: `webgui/bus_client.py`
- Test: `webgui/tests/test_bus_client.py`

**Step 1: Write the failing test**

```python
def test_read_only_refuses_every_command(monkeypatch):
    """bus_client.request is the SINGLE Tier-1 write chokepoint, and on the
    published pages it reaches gamma_analyze and gamma_explain -- PAID Claude
    calls -- plus gamma_refresh and sentiment refresh, which fan out Schwab
    fetches against a budget already running 68-76k/day. Unauthenticated and
    unrefused, that is an open tap on money."""
    import bus_client
    bus_client.set_read_only(True)
    try:
        with pytest.raises(PermissionError):
            bus_client.request("options", {"type": "gamma_analyze"})
    finally:
        bus_client.set_read_only(False)


def test_reads_still_work_when_read_only(monkeypatch):
    """Refusing writes must not refuse the reads the screens exist to do."""
    import bus_client
    bus_client.reset()
    bus_client.set_read_only(True)
    try:
        bus_client.bus().cache_set("cache:test:view", {"ok": True})
    except PermissionError:
        pytest.fail("read-only mode must not break the fake bus fixture setup")
    finally:
        bus_client.set_read_only(False)
        bus_client.reset()


def test_a_connection_url_can_be_pinned(monkeypatch):
    """The live process connects as a Redis ACL user with read commands only.
    Bus.__init__ already accepts a url, so this is a credential passed in, not a
    redesign."""
    import bus_client
    bus_client.reset()
    bus_client.set_url("redis://live:secret@127.0.0.1:6379/0")
    assert bus_client._url == "redis://live:secret@127.0.0.1:6379/0"
    bus_client.set_url(None)
    bus_client.reset()
```

**⚠ On the second test:** under pytest `Bus` is fakeredis, so `cache_set` will
not raise regardless. That test documents intent; the *real* proof that reads
survive is Task 9's route-render test, which renders a live page end to end.
Say so in the test's docstring rather than letting it look stronger than it is.

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_bus_client.py -k "read_only or pinned" -q
```

**Step 3: Implement**

```python
_bus: Bus | None = None
_url: str | None = None
_read_only = False


def set_url(url: str | None) -> None:
    """Pin the Redis connection URL for the next ``bus()``.

    The public live process passes an ACL user granted read commands only. That
    is the STRUCTURAL half of read-only: ``set_read_only`` is an application
    control, and this is the one the server enforces."""
    global _url
    _url = url


def set_read_only(flag: bool) -> None:
    """Refuse every command enqueue from this process."""
    global _read_only
    _read_only = flag


def is_read_only() -> bool:
    return _read_only
```

`bus()` becomes:

```python
    if _bus is None:
        _bus = Bus(url=_url) if _url else Bus()
    return _bus
```

`request()` gains a first line:

```python
    if _read_only:
        raise PermissionError(
            f"read-only process refused command {command.get('type')!r} "
            f"on cmd:{domain}")
```

Extend `reset()` to leave `_url`/`_read_only` alone (they are process
configuration, not cache) and say so in its docstring.

**Step 4: Run**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_bus_client.py -q
```

**Step 5: Commit**

```bash
git add webgui/bus_client.py webgui/tests/test_bus_client.py
git commit -m "feat(bus): read-only mode and a pinnable connection URL"
```

---

## Task 6: The live screen table, as data

**Files:**
- Create: `webgui/live_screens.py`
- Test: `webgui/tests/test_live_screens.py`

Keeping the table as pure data — no NiceGUI import — is what lets the route-set
guard and the site's thumbnail test read the same single source.

**Step 1: Write the failing test**

```python
"""The published screen table. Pure data, so the route guard, the capture
script and the static grid all read ONE source."""
import pytest

FORBIDDEN = {"/terminate", "/settings", "/status", "/driver", "/manuals",
             "/options/paper", "/options/captured", "/options/portfolio",
             "/options/shares", "/eod", "/trade"}


def test_there_are_exactly_fourteen_screens():
    import live_screens
    assert len(live_screens.SCREENS) == 14


def test_no_screen_publishes_a_control_surface():
    """THE GUARD THAT MATTERS. Every route here is served unauthenticated on a
    public origin, so a route added carelessly is a control surface on the
    internet -- /terminate stops the whole stack."""
    import live_screens
    routes = {s.route for s in live_screens.SCREENS}
    assert routes & FORBIDDEN == set(), f"published a control surface: {routes & FORBIDDEN}"


def test_routes_and_slugs_are_unique():
    """A duplicate slug would make two screens overwrite one capture file."""
    import live_screens
    routes = [s.route for s in live_screens.SCREENS]
    slugs = [s.slug for s in live_screens.SCREENS]
    assert len(set(routes)) == len(routes)
    assert len(set(slugs)) == len(slugs)


def test_every_route_starts_with_a_slash_and_every_slug_is_url_safe():
    import re
    import live_screens
    for s in live_screens.SCREENS:
        assert s.route.startswith("/")
        assert re.fullmatch(r"[a-z0-9-]+", s.slug), f"{s.slug} is not URL-safe"


def test_the_pins_name_real_settings_keys():
    """A typo'd pin key would silently do nothing and the screen would publish
    the wrong default."""
    import app_settings
    import live_screens
    for s in live_screens.SCREENS:
        for key in s.settings:
            assert key in app_settings.DEFAULTS, f"{s.slug} pins unknown key {key}"


def test_the_net_prem_pin_matches_the_requested_screen():
    """SPY, QQQ and BIG10 in Dollars. BIG10 is a SYMBOL inside the `indices`
    group in config/symbols.toml, not a group of its own."""
    import live_screens
    np = next(s for s in live_screens.SCREENS if s.slug == "net-premium")
    assert np.settings["gamma_netprem_group"] == "indices"
    assert np.settings["gamma_netprem_symbols"] == ["SPY", "QQQ", "BIG10"]
    assert np.settings["gamma_netprem_mode"] == "dollars"
```

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_live_screens.py -q
```

**Step 3: Implement**

```python
"""The fourteen screens published on the public live origin.

PURE DATA -- no NiceGUI import -- so the route registration, the thumbnail
capture script and the static grid on neuralstrike.co all read one source and
cannot disagree about what is published.

⚠ Every route here is served UNAUTHENTICATED. Adding one is publishing it.
``tests/test_live_screens.py`` refuses the known control surfaces, but that list
cannot be exhaustive: the question to ask of a new entry is not "is it on the
forbidden list" but "would I put this on a billboard".
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Screen:
    slug: str            # URL segment AND capture filename stem
    route: str           # the public route
    title: str           # the tile caption and the browser title
    module: str          # dotted path under `pages`
    kwargs: dict = field(default_factory=dict)   # pins passed to render()
    settings: dict = field(default_factory=dict) # app_settings pins for this screen


_NETPREM = {"gamma_netprem_group": "indices",
            "gamma_netprem_symbols": ["SPY", "QQQ", "BIG10"],
            "gamma_netprem_mode": "dollars"}

SCREENS = (
    Screen("desk", "/desk", "The Desk", "desk"),
    Screen("opportunity", "/opportunity", "Opportunity Board", "options.matrix"),
    Screen("flow", "/flow", "Flow Alerts", "options.flow"),
    # Skin B is the Heat Lattice. The page reads it from app_settings, so this
    # is a settings pin rather than a render kwarg.
    Screen("macro", "/macro", "Macro Board", "market", settings={"macro_skin": "B"}),
    Screen("sentiment", "/sentiment", "Sentiment", "sentiment"),
    Screen("bullbear", "/bullbear", "Bull / Bear Map", "sentiment_bullbear"),
    # Collapsed is already the page's build state (state["expanded"] = set()).
    Screen("sectors", "/sectors", "Sector & Industry", "sentiment_sectors"),
    Screen("rotation", "/rotation", "Sector Rotation", "sentiment_rotation"),
    Screen("rrg", "/rrg", "RRG", "sentiment_rrg"),
    Screen("momentum", "/momentum", "Momentum", "sentiment_momentum",
           kwargs={"level": "industry"}),
    Screen("gamma", "/gamma", "Gamma", "options.gamma",
           kwargs={"symbol": "$SPX", "view": "GEX"}),
    Screen("net-premium", "/net-premium", "Net Prem", "options.gamma",
           kwargs={"view": "Net Prem"}, settings=_NETPREM),
    Screen("premium-divergence-spy", "/premium-divergence/spy",
           "Premium Divergence · SPY", "options.gamma",
           kwargs={"symbol": "SPY", "view": "Flow"}),
    Screen("premium-divergence-qqq", "/premium-divergence/qqq",
           "Premium Divergence · QQQ", "options.gamma",
           kwargs={"symbol": "QQQ", "view": "Flow"}),
)

# The union of every screen's settings pins, which is what the live entrypoint
# freezes. ⚠ Pins are process-wide, not per-request: app_settings is a module
# singleton. That is fine only because no two screens pin the SAME key to
# DIFFERENT values -- asserted in tests/test_live_screens.py.
SETTINGS_PINS = {k: v for s in SCREENS for k, v in s.settings.items()}
```

**⚠ Add the conflict test too**, because `SETTINGS_PINS` silently resolves a
collision by last-wins:

```python
def test_no_two_screens_pin_the_same_key_to_different_values():
    """SETTINGS_PINS is process-wide -- app_settings is a module singleton, not
    per-request state. Two screens disagreeing about a key would silently
    last-wins, and one of them would publish the wrong view."""
    import collections
    import live_screens
    seen = collections.defaultdict(set)
    for s in live_screens.SCREENS:
        for k, v in s.settings.items():
            seen[k].add(repr(v))
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert clashes == {}, f"screens disagree on {clashes}"
```

**Step 4: Run**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_live_screens.py -q
```

**Step 5: Commit**

```bash
git add webgui/live_screens.py webgui/tests/test_live_screens.py
git commit -m "feat(live): the published screen table, as data"
```

---

## Task 7: The live entrypoint

**Files:**
- Create: `webgui/live_main.py`
- Test: `webgui/tests/test_live_main.py`

**Step 1: Write the failing test**

```python
"""The public live entrypoint.

⚠ ORDERING TRAP. ``Client.page_routes`` is a NiceGUI GLOBAL. main.py and
live_main.py both register ``/desk`` and ``/sentiment``, so in one pytest
process the registry holds both sets and an absolute assertion would be
meaningless. Every route test here therefore imports ``main`` FIRST to make the
ordering deterministic, snapshots the registry, and asserts on the DIFF.
"""
import ast
import pathlib

import pytest


def _live_routes():
    import main            # noqa: F401 -- imported FIRST, deliberately: see module docstring
    from nicegui import Client
    before = set(Client.page_routes.values())
    import live_main       # noqa: F401
    return set(Client.page_routes.values()) - before


def test_it_registers_every_published_screen_and_nothing_else():
    import live_screens
    new = _live_routes()
    expected = {s.route for s in live_screens.SCREENS}
    assert new == expected, f"unexpected: {new - expected}; missing: {expected - new}"


def test_live_main_does_not_import_main():
    """THE GUARD THAT KEEPS /terminate OFF THE PUBLIC ORIGIN.

    Source-level, because an import test that merely checks behaviour would pass
    in a process where main happened to be imported already -- which is exactly
    the case in this suite."""
    src = pathlib.Path(__file__).resolve().parents[1] / "live_main.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name == "main" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "main"


def test_it_puts_the_bus_in_read_only_mode_and_freezes_settings():
    """Driven from the ENTRYPOINT, not by calling the primitives directly.

    ⚠ A consumer-side assertion that is never driven from the producer proves
    nothing -- CLAUDE.md's signal_band incident is the standing example, where a
    correct test passed for weeks against a payload the service never wrote."""
    import app_settings
    import bus_client
    import live_main       # noqa: F401 -- importing installs the refusals

    assert bus_client.is_read_only()
    assert app_settings.is_frozen()
    with pytest.raises(PermissionError):
        bus_client.request("options", {"type": "gamma_analyze"})


def test_the_frozen_settings_carry_every_screen_pin():
    import app_settings
    import live_main       # noqa: F401
    import live_screens
    for key, value in live_screens.SETTINGS_PINS.items():
        assert app_settings.get(key) == value
```

**⚠ These tests mutate process-wide state** (`bus_client._read_only`,
`app_settings._frozen`) and cannot be undone without unpicking the entrypoint.
Put them in their **own file** and add a module-scoped autouse fixture that
calls `bus_client.set_read_only(False)` / `app_settings.unfreeze()` /
`app_settings.reset_cache()` on teardown, or the rest of the webgui suite will
fail behind them. Verify by running the **full** suite, not just this file.

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_live_main.py -q
```

**Step 3: Implement**

```python
"""The PUBLIC read-only screens: a second NiceGUI process on its own origin.

Serves the fourteen screens in ``live_screens.SCREENS`` at
``live.neuralstrike.co``, unauthenticated, to anyone. It renders the REAL page
modules -- the same ones the app renders -- so a published screen cannot drift
from the private one. ``webgui/wall.py`` makes the same argument at length.

⚠ THIS MODULE MUST NEVER ``import main``. main.py's body registers every
``@_page`` route, so importing it here would publish ``/terminate`` (Stop All
Services) and ``/settings`` to the internet. The seam the pages need lives in
``shell.py``; ``tests/test_live_main.py`` pins the absence at source level.

Read-only is enforced at four layers, of which this file installs three:

1. a Redis ACL user with read commands only (from the environment) -- the
   structural one, enforced by the server;
2. ``bus_client.set_read_only(True)`` -- refuses every command enqueue, and on
   these pages that covers gamma_analyze and gamma_explain, which are PAID
   Claude calls;
3. ``app_settings.freeze(...)`` -- pins each screen's published state AND stops
   this process reading or racing the app's single-user settings.json;
4. (structural) no rail, no Settings, no Terminate, no Sign-out -- those routes
   do not exist in this process at all.

Order matters: 1-3 are installed BEFORE any page module is imported, so a page
cannot capture an unrefused bus at import time.
"""
import importlib
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app_settings                                   # noqa: E402
import bus_client                                     # noqa: E402
import live_screens                                   # noqa: E402
from repo_paths import LIVE_HOST, NICEGUI_LIVE_PORT   # noqa: E402

# ── The refusals, installed before any page is imported ──────────────────────
# REDIS_LIVE_URL carries the read-only ACL user. Unset -> the ordinary URL, so a
# dev box without the ACL still runs; prod's unit always sets it.
bus_client.set_url(os.environ.get("REDIS_LIVE_URL") or None)
bus_client.set_read_only(True)
app_settings.freeze(live_screens.SETTINGS_PINS)

from nicegui import ui                                # noqa: E402
from pages.options import theme                       # noqa: E402


def _render(screen):
    """Import and render one screen inside the minimal public shell."""
    module = importlib.import_module(f"pages.{screen.module}")
    with ui.column().classes(f"{theme.PAGE} w-full min-h-screen gap-0"):
        module.render(**screen.kwargs)


def _register(screen):
    @ui.page(screen.route, title=f"{screen.title} - NeuralStrike")
    def _page(_s=screen):        # bind per iteration, or all fourteen share the last
        _render(_s)
    return _page


for _screen in live_screens.SCREENS:
    _register(_screen)


if __name__ in {"__main__", "__mp_main__"}:
    # 127.0.0.1 only. Caddy terminates TLS for LIVE_HOST and is the only thing
    # that should ever talk to this port -- the same rule the app follows, and
    # for the same reason. ⚠ Never widen this to 0.0.0.0.
    ui.run(host="127.0.0.1", port=NICEGUI_LIVE_PORT,
           title=f"NeuralStrike Live ({LIVE_HOST})",
           dark=True, reload=False, show=False)
```

**⚠ The `_s=screen` default argument is load-bearing.** A closure over the loop
variable makes all fourteen routes render the *last* screen — a bug that would
look like "the site works" until you clicked a second tile.

**⚠ NEVER call `main`'s three `sync_*` helpers from here, and do not grow a copy
of them "for symmetry"** (found while building Task 4). `sync_ticker_setting`,
`sync_captured_autoclose_setting` and `sync_manual_paper_lifecycle_setting`
(`main.py:125-176`) look like harmless readers of `app_settings.get()`, and in
`main` they are. But each one then does `bus_client.request(...)` — they are
**cross-process writers to Tier-2 services**.

Against a *frozen* store they would read the PINNED value and re-assert it to
the shared services, overriding what the user configured in their own app.
Concretely: `ticker_enabled` defaults `True`, so a live process calling
`sync_ticker_setting()` would enqueue `enable_summary` to `market_svc` and
**re-enable the ~20-minute paid Claude verdict the user may have deliberately
switched off**. `captured_autoclose_enabled` defaults `True` too, and would
re-arm auto-close on the paper book.

This is latent rather than live only because all three are registered via
`app.on_startup(...)` inside `main.py`'s `__main__` guard, so importing `main`
does not arm them — and this process does not import `main` at all. The failure
would be silent and would cost money, which is exactly the class this feature's
read-only layers exist to prevent. `bus_client.set_read_only(True)` would in
fact refuse them, but do not rely on that: the refusal is the backstop, not the
design.

Confirm the shell wrapper actually suits these pages; if `theme.PAGE` fights a
page that supplies its own wrapper, drop the column and render bare. Verify in
the browser at Task 12, not by reasoning.

**Step 4: ⚠ Stop `test_auth_covers_every_route.py` vouching for the live routes**

`webgui/tests/test_auth_covers_every_route.py` enumerates `main.app.routes` and
asserts every path is either documented-open or refuses a stranger with a 303.
**`main.app` is NiceGUI's GLOBAL `app`**, so the moment any test imports
`live_main`, the fourteen public routes join that enumeration.

They will *pass* — the test process mounted the gate via `main`, so they refuse
— and that pass is a lie. In production those routes are served by a process
that mounts no gate at all. Left alone, the one test in the app whose whole
purpose is to catch a route nobody thought about would start silently
guaranteeing the opposite of the truth for fourteen public routes.

That file's own docstring names this failure mode ("a consumer-side guard that
passed while the producer never emitted the shape being tested — `signal_band`,
and the ADX characterization test"). This is the same shape, so fix it the way
that docstring would want.

Exclude them explicitly, keyed off the one source of truth:

```python
# ⚠ The public live screens are NOT this app's routes.
#
# main.app is NiceGUI's GLOBAL app object, so importing live_main -- which any
# test in this suite may do -- registers its fourteen routes here too. In THIS
# process they refuse, because main mounted the gate on the shared app; in
# production they are served by live_main.py, which mounts no gate and is
# public by design.
#
# So a pass on them below would assert the exact opposite of the truth. They
# are excluded here and covered instead by tests/test_live_main.py, which pins
# the route set, and by the four read-only layers that entrypoint installs.
#
# Keyed off live_screens.SCREENS rather than a literal list: a fifteenth screen
# must not silently re-enter this enumeration.
import live_screens
_LIVE_ROUTES = frozenset(s.route for s in live_screens.SCREENS)
```

and subtract it in `_all_routes()`:

```python
    return {p for r in main.app.routes
            if (p := getattr(r, "path", None)) and "{" not in p} - _LIVE_ROUTES
```

Then add a test that the exclusion cannot rot into a blanket hole:

```python
def test_the_live_route_exclusion_covers_exactly_the_published_screens():
    """The exclusion above is a hole in the app's strongest auth guard, so it
    must be exactly the size of the thing it exists for -- and it must not
    overlap a route main.py serves, which WOULD be gated and must stay checked."""
    import live_screens
    import main
    assert _LIVE_ROUTES == {s.route for s in live_screens.SCREENS}

    # /desk and /sentiment are served by BOTH entrypoints. Excluding them from
    # this sweep is the accepted cost of the shared global app; assert it is a
    # known, small set rather than something that grew.
    shared = _LIVE_ROUTES & {"/desk", "/sentiment"}
    assert shared == {"/desk", "/sentiment"}
```

**⚠ Read that last assertion before you write it.** `/desk` and `/sentiment`
are registered by *both* entrypoints, so excluding them costs real coverage of
the app's own routes. If that trade is unacceptable, the alternative is to give
the live routes distinct paths (`/live/desk`) and have Caddy strip the prefix
with `handle_path`. **Raise this rather than deciding it alone** — it is a
security-coverage trade, and the controller should make the call.

**Step 5: Run this file, then the whole suite**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_live_main.py tests/test_auth_covers_every_route.py -q
cd webgui && ../.venv/bin/python -m pytest -q
```

The full run is not optional here — it is how you find out whether the teardown
fixture really contained the process-wide state, and whether the auth sweep
still covers everything it should.

**Step 6: Commit**

```bash
git add webgui/live_main.py webgui/tests/test_live_main.py webgui/tests/test_auth_covers_every_route.py
git commit -m "feat(live): the public read-only entrypoint"
```

---

## Task 8: The systemd unit

**Files:**
- Modify: `deploy/systemd/generate_units.py` (`components()`, and `_service_text` if the live unit needs different ordering)
- Test: `tests/test_systemd_units.py`

**Step 1: Write the failing test**

```python
def test_the_live_screens_have_their_own_unit():
    """A separate unit is the point: a public traffic spike or a crash on the
    open origin must not take the trading UI down with it."""
    from deploy.systemd import generate_units as g
    names = {c for c, _p, _s in g.components()}
    assert "webgui_live" in names


def test_the_live_unit_binds_the_live_port():
    from deploy.systemd import generate_units as g
    import repo_paths
    port = next(p for c, p, _s in g.components() if c == "webgui_live")
    assert port == repo_paths.NICEGUI_LIVE_PORT


def test_the_live_unit_runs_the_live_entrypoint():
    from deploy.systemd import generate_units as g
    script = next(s for c, _p, s in g.components() if c == "webgui_live")
    assert script == "webgui/live_main.py"
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tests/test_systemd_units.py -k live -q
```

**Step 3: Implement**

In `components()`, after the webgui line:

```python
    out.append(("webgui", NICEGUI_PORT, "webgui/main.py"))
    # The PUBLIC read-only screens, a separate process on a separate origin.
    # Separate so a public traffic spike or a crash cannot reach the trading UI.
    out.append(("webgui_live", NICEGUI_LIVE_PORT, "webgui/live_main.py"))
    return out
```

Import `NICEGUI_LIVE_PORT` at the top of the module beside `NICEGUI_PORT`.

**⚠ `components()`'s docstring says the component string is consumed verbatim as
the unit suffix and must equal what `webgui.pages.status.restart_spec` puts in
`spec["name"]`.** Check `restart_spec` and add `webgui_live` there too, or the
Status page grows a Restart button that errors. If `test_systemd_units.py`
already pins that equality, it will tell you.

**Step 4: Run**

```bash
.venv/bin/python -m pytest tests/test_systemd_units.py -q
```

**Step 5: Commit**

```bash
git add deploy/systemd/generate_units.py tests/test_systemd_units.py webgui/pages/status.py
git commit -m "feat(deploy): a systemd unit for the live screens"
```

---

## Task 9: The Caddy origin

**Files:**
- Modify: `deploy/caddy/generate_caddyfile.py`
- Test: `deploy/caddy/tests/test_caddyfile.py`

**Step 1: Write the failing test**

```python
def test_the_live_host_is_reverse_proxied_to_the_live_port():
    from deploy.caddy import generate_caddyfile as g
    import repo_paths
    text = g.render()          # match the module's actual entrypoint name
    assert f"{repo_paths.LIVE_HOST} {{" in text
    assert f"reverse_proxy 127.0.0.1:{repo_paths.NICEGUI_LIVE_PORT}" in text


def test_the_live_block_carries_no_authentication():
    """The live screens are public BY DESIGN. Pinned so that a later copy-paste
    of the app block does not quietly put a login in front of them -- or, worse,
    a login that does not work and reads as an outage."""
    from deploy.caddy import generate_caddyfile as g
    import repo_paths
    text = g.render()
    block = text.split(f"{repo_paths.LIVE_HOST} {{", 1)[1].split("\n}", 1)[0]
    assert "basicauth" not in block
    assert "forward_auth" not in block


def test_the_app_host_is_still_not_advertised_by_the_public_blocks():
    """Unchanged behaviour, re-asserted: adding a third public origin must not
    have leaked the app's hostname into the site or live blocks."""
    # Extend whatever the existing test of this name already does to cover the
    # new block -- do not write a second, weaker copy.
```

Read `deploy/caddy/tests/test_caddyfile.py` first: it already has a test named
close to the third one. **Extend it rather than adding a parallel copy.**

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest deploy/caddy -q
```

**Step 3: Implement**

Mirror the existing `APP_HOST` block, minus anything auth-related:

```python
def _live_block():
    """The PUBLIC read-only screens.

    A separate origin from APP_HOST, not a path under it: the app is behind a
    login and these are not, so they are separated by ORIGIN rather than by a
    path filter someone has to get right. The upstream still binds 127.0.0.1 --
    Caddy is the only thing that talks to it.
    """
    return f"""{LIVE_HOST} {{
    encode zstd gzip
    reverse_proxy 127.0.0.1:{NICEGUI_LIVE_PORT}
}}
"""
```

Match the existing blocks' formatting exactly — the tests compare strings. If
the app block sets websocket or header directives NiceGUI needs, carry those
across; the live screens are NiceGUI too and need the same treatment.

**Step 4: Run**

```bash
.venv/bin/python -m pytest deploy/caddy -q
```

**Step 5: Commit**

```bash
git add deploy/caddy tests
git commit -m "feat(deploy): serve the live screens on their own origin"
```

---

## Task 10: The capture script and its window

**Files:**
- Create: `tools/capture_live_shots.py`
- Modify: `config/sessions.toml`
- Modify: `deploy/systemd/generate_units.py` (timer + oneshot service)
- Modify: `.gitignore`
- Test: `tools/tests/test_capture_live_shots.py`

**Step 1: Write the failing test**

```python
def test_it_captures_every_published_screen():
    """One source: the capture list IS live_screens.SCREENS, so a screen added
    to the site cannot be missing a thumbnail."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "webgui"))
    import live_screens
    from tools import capture_live_shots as c
    targets = c.targets()
    assert len(targets) == len(live_screens.SCREENS)
    for screen, (url, out) in zip(live_screens.SCREENS, targets):
        assert url.endswith(screen.route)
        assert out.name == f"{screen.slug}.webp"


def test_it_captures_from_loopback_never_the_public_host():
    """Capturing over the public origin would make the thumbnails depend on
    DNS, TLS and Caddy being healthy -- three things that have nothing to do
    with whether the screens render."""
    import repo_paths
    from tools import capture_live_shots as c
    for url, _out in c.targets():
        assert url.startswith(f"http://127.0.0.1:{repo_paths.NICEGUI_LIVE_PORT}")
        assert repo_paths.LIVE_HOST not in url


def test_outside_the_window_it_stands_down_with_exit_zero(monkeypatch):
    """A holiday is a normal outcome. A non-zero exit would restart-storm into
    StartLimitBurst and leave the unit `failed`, a state someone has to clear by
    hand -- the same reasoning tools/stream_wall.sh records."""
    from tools import capture_live_shots as c
    monkeypatch.setattr(c, "_in_window", lambda: False)
    assert c.main() == 0
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tools/tests/test_capture_live_shots.py -q
```

**Step 3: Add the window**

In `config/sessions.toml`, after `[windows.stream]`:

```toml
[windows.live_capture]
# Thumbnail captures for the public grid on neuralstrike.co/live.html.
# Held SEPARATE from [windows.collection] for the reason [windows.stream] gives:
# widening collection must not silently extend a public surface.
start = "08:00"
end   = "15:20"
```

**Step 4: Write the script**

`tools/capture_live_shots.py` — headless Chrome per screen, mirroring
`stream_wall.sh`'s binary resolution (`google-chrome || chromium-browser`) and
its stand-down semantics. Key points:

- `targets()` is pure and returns `[(url, Path), ...]` from `live_screens.SCREENS`.
- `_in_window()` calls `shared.market_calendar.in_window("live_capture", now)`.
- Write to a temp file and `os.replace` into place, so a half-written capture is
  never served. `os.replace` is atomic on the same filesystem.
- One screen failing must not abort the rest — log it and carry on, then exit 0.
  A missing tile is a gap in a menu; a dead timer is fourteen stale tiles.
- Give Chrome a settle delay before the screenshot: these pages paint from Redis
  on a watcher tick, and a capture taken at load shows skeletons. Start at
  `--virtual-time-budget=8000` and check the output.

**Step 5: Gitignore the captures**

```gitignore
# Thumbnail captures for the public live grid. Generated on the box that serves
# them, every 15 min inside [windows.live_capture]. ⚠ NOT committed: tracked,
# they would dirty prod's tree the moment the timer first fires, and
# tools/promote.sh refuses a dirty tree.
deploy/site/live/*.webp
```

**Step 6: Add the timer**

Extend `generate_units.py` with a `trading-<env>-live-capture.service` (Type=oneshot)
and `.timer` (`OnCalendar=*:0/15`, `Persistent=false`). Add a test asserting the
timer exists and names the 15-minute cadence. **⚠ `StartLimitIntervalSec` /
`StartLimitBurst` belong in `[Unit]`, not `[Service]`** — systemd silently
ignores them in the wrong section, so a storm cap would look configured and not
exist.

**Step 7: Run and commit**

```bash
.venv/bin/python -m pytest tools/tests/test_capture_live_shots.py tests/test_systemd_units.py -q
git add tools/capture_live_shots.py tools/tests config/sessions.toml deploy/systemd .gitignore
git commit -m "feat(live): capture the thumbnail grid every 15 minutes"
```

---

## Task 11: The thumbnail grid on the static site

**Files:**
- Modify: `deploy/site/live.html`
- Modify: `deploy/tests/test_site.py`
- Modify: `deploy/site/assets/site.css` (grid styles, if the existing tokens do not cover it)

**Step 1: Write the failing test**

Add to `deploy/tests/test_site.py`:

```python
def test_the_live_grid_offers_every_published_screen():
    """The grid and the live app read ONE source, so a screen cannot be
    published without a tile or tiled without being published."""
    import sys, pathlib
    sys.path.insert(0, str(REPO_ROOT / "webgui"))
    import live_screens
    text = _text("live.html")
    for s in live_screens.SCREENS:
        assert f"{s.slug}.webp" in text, f"no tile for {s.slug}"
        assert s.title in text, f"no caption for {s.title}"


def test_the_grid_links_to_the_live_origin_and_not_the_app():
    import repo_paths
    text = _text("live.html")
    assert repo_paths.LIVE_HOST in text
    assert repo_paths.APP_HOST not in text     # unchanged, re-asserted here


def test_the_placeholder_copy_is_gone():
    """live.html shipped saying "Not published yet" and "Nothing mounted"."""
    text = _text("live.html")
    for gone in ("Not published yet", "Nothing mounted", "Live view slot"):
        assert gone not in text
```

**Step 2: Extend the existing site guards**

Three existing tests need a decision, not a rewrite:

- `test_every_internal_reference_resolves_to_a_file` — the fourteen
  `live/<slug>.webp` do **not** exist in the repo. Add an explicit exemption for
  `live/` with a comment saying they are runtime-generated state written by
  `tools/capture_live_shots.py` on the serving box, and gitignored so promote's
  dirty-tree check stays clean. **An exemption, not a deletion** — the test is
  the one that catches a renamed screenshot.
- `ALLOWED_OUTBOUND` — add `https://{LIVE_HOST}` so
  `test_no_page_reaches_an_external_origin` permits the tile links. Note in the
  comment that these are LINKS a visitor clicks, not resources the page loads,
  which is the distinction that test already draws.
- `test_every_image_declares_its_size` — the tiles must carry `width`/`height`.
  That is also what reserves their space before the first capture exists.

**Step 3: Rewrite `live.html`**

Replace the placeholder `<main>` with the grid. Keep the nav, the footer and
every `<link>` in the head. Each tile:

```html
<a class="ns-live-tile" href="https://LIVE_HOST/desk">
  <img src="live/desk.webp" width="640" height="400" alt="The Desk, captured live" loading="lazy">
  <span class="ns-live-cap">The Desk</span>
</a>
```

Update the head comment: it currently explains why the slot is empty. Replace it
with what now mounts there and why it is captures rather than an iframe —
**correct in place, do not append a note under the stale text.** That is the
house rule in CLAUDE.md's maintenance banner.

Remove `<meta name="robots" content="noindex, follow">`: it is there because a
placeholder in search results is worse than no result, and the page is no longer
a placeholder.

**⚠ Do not add a timestamp.** The design records why: it would make a committed
source file a build artifact, and the live page carries its own staleness.

**Step 4: Run**

```bash
.venv/bin/python -m pytest deploy -q
```

**Step 5: Commit**

```bash
git add deploy/site deploy/tests
git commit -m "feat(site): the live screens grid replaces the placeholder"
```

---

## Task 12: Verify it running, in dev

**⚠ Nothing above proves the screens render.** Every test so far is structural.
CLAUDE.md is explicit that "tests pass" is not "verified in dev" for anything
with a runtime surface — the DEV chip, the Status-page restart gating and the
launcher guards were all green in tests and wrong in practice.

**Step 1: Land the work in dev**

Commit here, fast-forward `Using_Highcharts` and `main`, then work in the dev
checkout. **Do not preview from this worktree** — it has no `env.local.toml`, so
it resolves to prod and would bind :8500.

**Step 2: Start the live process by hand first**

```bash
.venv/bin/python webgui/live_main.py
```

Watch for a bind error. **A failed bind is silent** — if something already holds
the port, the new server exits and the old one keeps serving, so you verify
stale code while everything looks healthy. The tell is
`[Errno 98] Address already in use`. Confirm with
`ss -ltnp | grep 9501` rather than trusting the absence of a message.

**Step 3: Walk all fourteen**

Open each of `http://127.0.0.1:9501<route>` and confirm:

- it renders (not a NiceGUI error page, not an empty shell);
- the pins hold — Macro Board is the Heat Lattice, Gamma is `$SPX` on GEX, Net
  Prem shows SPY/QQQ/BIG10 in Dollars, Momentum is Industries, Sector & Industry
  is collapsed, Premium Divergence is SPY on one route and QQQ on the other;
- **the Analyze and Explain buttons are gone from Gamma**, and Sentiment has no
  Refresh. If any survives, click it and confirm the `PermissionError` in the
  log rather than a paid call.

**⚠ Charts.** `ui.highchart` has no ResizeObserver, and a chart that mounts in a
zero-size container renders collapsed and never recovers. The live shell is a
different wrapper from `_layout`, so this is a genuine risk on Gamma, Net Prem
and Premium Divergence. If a chart is collapsed, give it an explicit
`chart.height` or reflow after mount — CLAUDE.md's NiceGUI gotchas section has
the exact idiom.

**Step 4: Prove the refusal from the outside**

With the live process running, confirm the command stream is untouched:

```bash
.venv/bin/python -c "
from shared.bus import Bus
print(Bus().redis.xlen('cmd:options'))"
```

Note the length, click everything clickable on `/gamma`, and check it has not
moved.

**Step 5: Prove settings isolation**

Change `macro_skin` to `A` in the app's Settings page, reload
`http://127.0.0.1:9501/macro`, and confirm it is **still** the Heat Lattice.
Then confirm `webgui/data/settings.json` was not written by the live process
(`stat` its mtime before and after a full walk of all fourteen).

**Step 6: Run the captures once by hand**

```bash
.venv/bin/python tools/capture_live_shots.py
ls -la deploy/site/live/
```

Fourteen `.webp` files, each a real screenshot rather than a skeleton or a white
box. If they are skeletons, raise the settle delay. Open `live.html` from disk
and confirm the grid renders with the captures and no broken images.

**Step 7: Record what you saw**

Add a dated entry to `docs/CHANGELOG.md` — what shipped, and the live
verification log. Per CLAUDE.md, the CHANGELOG is where shipping narrative goes,
not CLAUDE.md.

---

## Task 13: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/CHANGELOG.md`, `docs/webgui-routes.md`
- Modify: `docs/manuals/*` and `webgui/page_help.py` **only if user-visible app behaviour changed**

**CLAUDE.md gets only what is durable** — per its own maintenance banner, a
shipped feature is a CHANGELOG entry, and this file changes only for a new
invariant. Here there are four, and they are all genuine:

1. `webgui/shell.py` is the page-to-shell seam, and **no page may import
   `main`** — with the reason (the route table follows it into any second
   process).
2. There is a **second Tier-1 process**, `webgui/live_main.py` on
   `nicegui_live`, serving `LIVE_HOST` unauthenticated.
3. `deploy/site/live/*.webp` is generated, gitignored state under `SITE_ROOT`,
   and why (promote's dirty-tree refusal).
4. `app_settings.freeze()` exists and what it is for.

Add the fourteen routes to `docs/webgui-routes.md` under a "Public live screens"
heading, noting they render the same modules as the private routes.

**Step: Commit**

```bash
git add CLAUDE.md docs
git commit -m "docs: record the public live screens and the shell seam"
```

---

## Task 14: Deploy

Follow the standing rule: **dev first, verified, then `tools/promote.sh` and
nothing else.** Never `git pull` in the prod checkout.

1. **Redis ACL.** On the box, create the read-only user and put its URL in the
   live unit's environment as `REDIS_LIVE_URL`:

   ```
   ACL SETUSER live on >PASSWORD ~cache:* ~events:* &events:* +@read +subscribe +psubscribe +ping
   ```

   Verify it: connect as `live` and confirm `SET` and `XADD` are refused while
   `GET` and `SUBSCRIBE` work. **Then confirm the live pages still render** — an
   over-tight ACL degrades to "Waiting for … service" on every screen, which
   looks like a service outage rather than a permissions problem.

2. **DNS + TLS.** Point `live.neuralstrike.co` at the box. Regenerate and
   install the Caddyfile, reload Caddy, confirm the certificate issues.

3. **Units.** Regenerate:

   ```bash
   .venv/bin/python -m deploy.systemd.generate_units --install && systemctl --user daemon-reload
   ```

   Start `trading-prod-webgui_live` and enable `trading-prod-live-capture.timer`.

4. **Dependency check.** If anything new landed in `requirements.txt`, it must
   also be in `requirements.lock` **by hand** — prod has its own venv and
   promote reinstalls only when the lock moved. Dry-run against the prod venv
   and confirm it names exactly what you intended:

   ```bash
   /home/administrator/prod/.venv/bin/python -m pip install --dry-run -r requirements.lock
   ```

   Do **not** regenerate the lock with `pip freeze`.

5. **Promote timing.** `tools/promote.sh` stops the whole target — the public
   stream drops and GEX collection slots are lost. Default to **15:25–16:15 CT**.

6. **Post-deploy.** Load all fourteen over `https://live.neuralstrike.co`, then
   confirm from a machine **outside** the tailnet that the app host is still
   unreachable and the live host does not require a login.

---

## Definition of done

- [ ] Fourteen screens render over `https://live.neuralstrike.co`, unauthenticated, with every pin holding.
- [ ] No page imports `main`; `live_main` does not either — both pinned by test.
- [ ] `bus_client.request` refuses in the live process, driven from the entrypoint in test and confirmed against `cmd:options` in dev.
- [ ] The Redis ACL user cannot write, and the screens still render under it.
- [ ] `app_settings.json` is never written by the live process; the app's own preferences do not move the public screens.
- [ ] The grid on `neuralstrike.co/live.html` shows fourteen current captures and links out; `test_site.py` green, with `APP_HOST` still absent.
- [ ] The full webgui suite's failing **set** is unchanged from the pre-task baseline, compared by node ID.
- [ ] CLAUDE.md carries the four new invariants; the CHANGELOG carries the shipping entry and the verification log.
