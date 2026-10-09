# Dealer Positioning Heatmap: Value, Scale and Frame — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the intraday heatmap on `/options/gamma` four controls (Value, Show, Scale, Frame) and a legend, so a balanced strike is visible, a colour has a fixed meaning, and the reader can see positioning relative to price.

**Architecture:** Every mode is a pure transform of the history rows the page already holds, applied before the existing figure builders. The transforms live in a new pure module, `webgui/pages/options/gamma_heat.py`; `gamma.py`'s builders gain keyword arguments that default to today's behaviour. The heatmap keeps its nine series and its one colour axis in every mode. The service gains two small additive pieces (a published scale lock, a wider history crop) in a new sibling module, because `compute.py` is one line under its ceiling.

**Tech Stack:** Python 3.11, NiceGUI, Highcharts 12.4.0 (`nicegui[highcharts]`), pytest, Redis through `shared.bus` (fakeredis under pytest).

**Design:** [2026-10-09-gamma-heatmap-value-scale-frame-design.md](2026-10-09-gamma-heatmap-value-scale-frame-design.md). Read it first.

**Decisions taken as defaults** (the three questions the design left open were not answered, so the plan uses the recommended answer for each; change them here before starting if they are wrong):

1. Size mode, with balanced-strike markers and split bars, replaces the mock-up's two-value cell.
2. Locked is the default scale from the first day.
3. The legend prints a dollar unit for GEX ("$ gamma per 1% move") and DEX ("$ delta"), read from `gamma_tool.py`. Charm and Vanna print the bare number, as the bars' axis does today.

---

## Before you start

**Where you are.** This worktree. Never `git pull`, `merge`, `checkout` or `reset` in `/home/administrator/dev` on the server: that checkout is prod, and it moves only through `tools/promote.sh`.

**Commands.** Shell state does not persist between tool calls, so set `PY` at the start of each command:

```bash
PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
```

| Suite | Command (from the worktree root) |
|---|---|
| webgui, one file | `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py -q)` |
| webgui, all | `(cd webgui && "$PY" -m pytest . -q)` |
| options service | `"$PY" -m pytest services/options_svc -q` |
| shared | `"$PY" -m pytest shared/tests -q` |
| repo guards | `"$PY" -m pytest tests -q` |
| tools | `"$PY" -m pytest tools/tests -q` |

Never run `pytest services` over several services at once.

**Rules that have each cost real time in this repo:**

- **Do not edit a page module while the webgui suite is running.** Several tests read page source; an edit mid-run makes them fail falsely. Commit, run, touch nothing.
- **Write test files with the editor, never a heredoc.** A heredoc turns `\b` into a backspace byte and the test then asserts nothing.
- **Never weaken an existing assertion to make a test pass.** If an existing test fails, the change is wrong or the test pins a location that moved. Only a location (a source slice marker, a line of expected text) may be updated, and the commit message says which and why.
- **Compare the failing set, not the count.** Task 0.1 records it.
- **Styling is Tailwind classes through `.classes()`.** No `.style(...)`, no inline `style=` on a NiceGUI element. A `ui.html` SVG fragment is outside that rule.
- **`bus_client.read_shared` hands every tab the same object.** A transform must build new lists and never write to a row, a grid or a cell.
- **`gamma.render` is exactly at its ceiling** (1,484 lines, 62 nested functions; `webgui/tests/test_render_size.py`). It may not grow by one line. Task 0.2 buys room. After every task that touches `render`, run that test.
- **`compute.py` is one line under its ceiling** (10,524 of 10,525; `services/options_svc/tests/test_compute_module_shape.py`). Task 2.1 buys room.
- **A tunable goes in `config/*.toml`** with a catalogue entry in `webgui/config_schema.py`, never as a literal.

**Shipping.** Each phase ends with a commit on this branch. Merging to `main`, pushing and promoting are the user's call at each phase boundary. Stop and ask. A promote stops the whole stack, so the default window is 15:25–16:15 CT.

---

## Phase 0 — Room to work

### Task 0.1: Record the baseline

**Step 1: Run the three suites this plan touches and save the failing set**

```bash
PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
(cd webgui && "$PY" -m pytest . -q -rf 2>&1 | tail -15)
"$PY" -m pytest services/options_svc -q -rf 2>&1 | tail -15
"$PY" -m pytest shared/tests -q -rf 2>&1 | tail -15
```

Expected: no failures. If there are any, write their node IDs into the first commit message of Task 0.2 as "failing before this work", and compare against that set (never the count) at the end of every phase.

### Task 0.2: Move the three overlay handlers out of `render`

`render` holds three near-identical handlers (`_on_tracks_toggle`, `_on_spot_style`, `_on_spot_interval`, around `gamma.py:3406-3424`). Each persists one setting and repaints. Replacing them with one module-level factory frees about 14 lines and 3 nested functions.

**Files:**
- Modify: `webgui/pages/options/gamma.py` (add `overlay_handler` above `render`; replace the three handlers inside it)
- Modify: `webgui/tests/test_render_size.py` (lower the ceiling)
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing test** (append to `webgui/tests/test_options_gamma.py`)

```python
def test_overlay_handler_persists_then_repaints(monkeypatch):
    """One factory serves every overlay control: it stores the choice under its
    key, cast to the setting's own type, and then runs the repaint."""
    stored, ran = {}, []
    monkeypatch.setattr(gamma.app_settings, "set",
                        lambda k, v: stored.__setitem__(k, v))
    handler = gamma.overlay_handler("gamma_spot_interval", int, lambda: ran.append(1))

    class _Event:
        value = "15"
    handler(_Event())
    assert stored == {"gamma_spot_interval": 15}
    assert ran == [1]


def test_the_overlay_handlers_no_longer_live_in_render():
    src = inspect.getsource(gamma.render)
    for name in ("_on_tracks_toggle", "_on_spot_style", "_on_spot_interval"):
        assert f"def {name}(" not in src
```

**Step 2: Run it to see it fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q -k "overlay_handler or no_longer_live")`
Expected: FAIL, `module 'pages.options.gamma' has no attribute 'overlay_handler'`.

**Step 3: Implement.** Add at module level, directly above `def render(`:

```python
def overlay_handler(key, cast, after):
    """A change handler for one persisted overlay choice: store it, then repaint.

    Module-level so ``render`` holds no per-control handler. ``after`` is the
    page's repaint; the store is a no-op on the public origin, where settings
    are frozen and the choice lives in the element."""
    @guard
    def _on_change(e):
        app_settings.set(key, cast(e.value))
        after()
    return _on_change
```

Inside `render`, delete the three `@guard def _on_…` functions and the line `tracks_sw.on_value_change(_on_tracks_toggle)`. In their place, at the spot where `tracks_sw.on_value_change(...)` was:

```python
    tracks_sw.on_value_change(
        overlay_handler("gamma_level_tracks", bool, _render_view))
```

The two lines after `_sync_spot_controls` currently read `spot_style_sel.on_value_change(_on_spot_style)` and `spot_int_sel.on_value_change(_on_spot_interval)`. Replace them with:

```python
    spot_style_sel.on_value_change(overlay_handler(
        "gamma_spot_style", str, lambda: (_sync_spot_controls(), _render_view())))
    spot_int_sel.on_value_change(
        overlay_handler("gamma_spot_interval", int, _render_view))
```

⚠ `test_symbol_scoped_controls_hide_on_net_prem` slices `render`'s source from `def _sync_spot_controls(` to `"\n    spot_style_sel.on_value_change"`. Keep that exact line start, at that indent, as the first statement after the function. Do not touch the test.

**Step 4: Lower the ceiling.** Measure:

```bash
(cd webgui && "$PY" -c "
import ast, inspect, sys
sys.path[:0] = ['.', '..']
from pages.options import gamma
src = inspect.getsource(gamma.render); fn = ast.parse(src.lstrip()).body[0]
print(len(src.splitlines()), sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(fn)) - 1)")
```

Set `"pages.options.gamma": (<lines>, <nested>)` in `CEILINGS` to exactly what it prints.

**Step 5: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py tests/test_render_size.py -q)`
Expected: PASS, including the two new tests and `test_symbol_scoped_controls_hide_on_net_prem`.

**Step 6: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py webgui/tests/test_render_size.py
git commit -m "refactor(gamma): one module-level handler for the overlay controls"
```

---

## Phase 1 — Value, split bars, balanced markers, legend (page only)

### Task 1.1: The config file and its loader

**Files:**
- Create: `config/gamma_heat.toml`
- Create: `shared/gamma_heat_config.py`
- Modify: `repo_paths.py` (next to `GAMMA_PUBLIC_TOML`, line 95)
- Modify: `webgui/config_schema.py` (a `ConfigFile` modelled on `_GAMMA_PUBLIC`, line 871, and add it wherever `_GAMMA_PUBLIC` is listed)
- Test: `shared/tests/test_gamma_heat_config.py`

**Step 1: Write the failing tests** (`shared/tests/test_gamma_heat_config.py`)

```python
"""config/gamma_heat.toml: the Dealer Positioning heatmap's tunables."""
import ast
import pathlib

from shared import gamma_heat_config as cfg


def test_defaults_when_nothing_is_overridden():
    assert cfg.balanced() == {"max_polarity": 0.15, "min_size_quantile": 0.8,
                              "max_marks": 3}


def test_a_bad_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"balanced": {
        "max_polarity": "wide", "min_size_quantile": True, "max_marks": -2}})
    assert cfg.balanced() == {"max_polarity": 0.15, "min_size_quantile": 0.8,
                              "max_marks": 3}


def test_an_override_is_read(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"balanced": {"max_polarity": 0.3}})
    assert cfg.balanced()["max_polarity"] == 0.3


def test_it_imports_only_the_config_loader_and_the_paths():
    """Tier 1 imports this module, so its import set is pinned (CLAUDE.md, the
    Tier-1 allow-list): stdlib, shared.config_toml and repo_paths."""
    tree = ast.parse(pathlib.Path(cfg.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert mods == {"repo_paths", "shared.config_toml"}
```

**Step 2: Run to see them fail**

Run: `"$PY" -m pytest shared/tests/test_gamma_heat_config.py -q`
Expected: FAIL, `cannot import name 'gamma_heat_config'`.

**Step 3: Implement.**

`repo_paths.py`, after the `GAMMA_PUBLIC_TOML` line:

```python
GAMMA_HEAT_TOML = REPO_ROOT / "config" / "gamma_heat.toml"
```

`config/gamma_heat.toml`:

```toml
# The Dealer Positioning heatmap's controls (design:
# docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md).
# Read through shared/gamma_heat_config.py, per paint, so a saved change
# applies with no restart.

[balanced]
# A strike is marked "balanced" when its calls and puts nearly cancel:
# abs(net) / (abs(calls) + abs(puts)) at or under this.
max_polarity = 0.15
# ...and it is among the largest strikes on screen: at or above this quantile
# of size across the visible window.
min_size_quantile = 0.8
# At most this many markers, largest first. 0 turns the markers off.
max_marks = 3
```

`shared/gamma_heat_config.py`:

```python
"""``config/gamma_heat.toml``: the Dealer Positioning heatmap's tunables.

Read by the page (the balanced-strike markers, the change window) and by
options_svc (the scale lock, the display window). Stdlib, ``shared.config_toml``
and ``repo_paths`` only: Tier 1 imports it, and
``shared/tests/test_gamma_heat_config.py`` pins that set.
"""
from repo_paths import GAMMA_HEAT_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "balanced": {"max_polarity": 0.15, "min_size_quantile": 0.8, "max_marks": 3},
}

load, reset_cache = toml_loader(GAMMA_HEAT_TOML, DEFAULTS, label="gamma_heat.toml")


def _setting(section, key, *, minimum, maximum=None):
    """A config number of the default's own type inside its range, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[section][key]
    raw = (load().get(section) or {}).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = type(default)(raw)
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def balanced() -> dict:
    """``{"max_polarity", "min_size_quantile", "max_marks"}``."""
    return {"max_polarity": _setting("balanced", "max_polarity",
                                     minimum=0.0, maximum=1.0),
            "min_size_quantile": _setting("balanced", "min_size_quantile",
                                          minimum=0.0, maximum=1.0),
            "max_marks": _setting("balanced", "max_marks", minimum=0)}
```

`webgui/config_schema.py`, after `_GAMMA_PUBLIC`:

```python
# Dealer Positioning heatmap — config/gamma_heat.toml
# Read per paint through the mtime-cached loader: no restart.
_GAMMA_HEAT = ConfigFile(
    name="gamma_heat.toml", title="Dealer Positioning heatmap", icon="grid_on",
    summary="What the heatmap marks and how its colour scale is set.",
    restart=(),
    sections=(
        Section("Balanced strikes",
                "A strike holding large calls and large puts that nearly cancel.", (
            Field("balanced.max_polarity", "Lean at most",
                  "Net as a share of calls plus puts.", kind="fraction",
                  min=0, max=1, step=0.01),
            Field("balanced.min_size_quantile", "Among the largest",
                  "Only strikes at or above this rank by size are marked.",
                  kind="fraction", min=0, max=1, step=0.05),
            Field("balanced.max_marks", "Markers at most", "0 turns them off.",
                  kind="int", min=0, max=10, step=1),
        )),
    ),
)
```

Then find where `_GAMMA_PUBLIC` is added to the tuple of files (search for `_GAMMA_PUBLIC,`) and add `_GAMMA_HEAT,` after it.

**Step 4: Run**

Run: `"$PY" -m pytest shared/tests/test_gamma_heat_config.py -q && (cd webgui && "$PY" -m pytest tests/test_config_schema.py -q)`
Expected: PASS. `test_config_schema.py` discovers the new file by itself; if it reports a field that fails to round-trip, fix the `Field`, not the test.

**Step 5: Record the two invariants in `CLAUDE.md`** (two short edits; the file has a size ceiling):

- In the Tier-1 allow-list sentence, after the `shared.news_config` entry, add: `` `shared.gamma_heat_config` (since 2026-10-09; `config/gamma_heat.toml`'s loader - stdlib + `shared.config_toml` + `repo_paths` only, pinned by `shared/tests/test_gamma_heat_config.py`) · ``
- In "The files:" list under *Paths, ports and configuration*, add `` `gamma_heat` `` after `` `blog` ``.

Run: `"$PY" -m pytest tests/test_claude_md_size.py -q` — Expected: PASS.

**Step 6: Commit**

```bash
git add config/gamma_heat.toml shared/gamma_heat_config.py shared/tests/test_gamma_heat_config.py repo_paths.py webgui/config_schema.py CLAUDE.md
git commit -m "feat(gamma): config/gamma_heat.toml and its loader"
```

### Task 1.2: The value of a cell

**Files:**
- Create: `webgui/pages/options/gamma_heat.py`
- Test: `webgui/tests/test_gamma_heat.py`

**Step 1: Write the failing tests** (`webgui/tests/test_gamma_heat.py`)

```python
"""The Dealer Positioning heatmap's pure transforms (value, show, scale, frame)."""
import ast
import math
import pathlib

import pytest

from pages.options import gamma_heat as gh

CELL = {"call": 300.0, "put": -280.0, "net": 20.0}


@pytest.mark.parametrize("mode, want", [
    ("net", 20.0), ("call", 300.0), ("put", -280.0), ("size", 580.0)])
def test_cell_value(mode, want):
    assert gh.cell_value(CELL, mode) == want


def test_size_takes_its_sign_from_net():
    assert gh.cell_value({"call": 100.0, "put": -400.0, "net": -300.0}, "size") == -500.0


def test_size_of_a_cell_with_zero_net_sits_on_the_call_side():
    assert gh.cell_value({"call": 250.0, "put": -250.0, "net": 0.0}, "size") == 500.0


def test_size_uses_magnitudes_whatever_the_stored_signs():
    """Charm and vanna store calls and puts of either sign."""
    assert gh.cell_value({"call": -60.0, "put": -40.0, "net": -100.0}, "size") == -100.0


@pytest.mark.parametrize("cell", [7.0, None, {}, {"net": 3.0}, {"call": 1.0},
                                  {"call": float("nan"), "put": 1.0, "net": 1.0},
                                  {"call": True, "put": 1.0, "net": 1.0}])
def test_size_needs_both_sides(cell):
    """A bare number, a missing side, a NaN or a flag is absent, never zero."""
    assert gh.cell_value(cell, "size") is None


@pytest.mark.parametrize("cell", [7.0, None, {}, {"net": 3.0},
                                  {"call": float("nan"), "put": 1.0, "net": 1.0},
                                  {"call": True, "put": 1.0, "net": 1.0}])
def test_a_cell_with_no_usable_call_has_no_call_value(cell):
    assert gh.cell_value(cell, "call") is None


def test_one_side_is_readable_without_the_other():
    assert gh.cell_value({"call": float("nan"), "put": -4.0}, "put") == -4.0


def test_a_bare_number_is_its_own_net():
    assert gh.cell_value(7.0, "net") == 7.0


def test_an_unknown_value_is_refused():
    with pytest.raises(ValueError):
        gh.cell_value(CELL, "gross")


def test_has_sides():
    assert gh.has_sides([{100.0: {"net": 1.0}}, {100.0: CELL}])
    assert not gh.has_sides([{100.0: {"net": 1.0}}, {100.0: 5.0}, {}, None])


def test_it_imports_nothing_from_gamma():
    """gamma imports this module; the reverse would be a cycle."""
    tree = ast.parse(pathlib.Path(gh.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "gamma" not in {a.name for a in node.names} or node.module != "."
            assert not (node.module or "").endswith("options.gamma")
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py -q)`
Expected: FAIL, `cannot import name 'gamma_heat'`.

**Step 3: Implement** (`webgui/pages/options/gamma_heat.py`)

```python
"""The Dealer Positioning heatmap's transforms: which number a cell holds, how it
is scaled, and what the vertical axis measures.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new lists: the history rows arrive through
``bus_client.read_shared`` and are shared by every open tab, so a transform that
wrote to its input would corrupt every other tab's chart.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
from pages import fmt as _fmt

# value key -> the control's label. The order is the picker's order.
VALUES = {"net": "Net", "call": "Calls", "put": "Puts", "size": "Size"}


def cell_value(cell, mode="net"):
    """The number one stored cell draws for ``mode``, or None when it has none.

    ``size`` is ``abs(call) + abs(put)`` carrying the sign of ``net``: how much is
    at the strike, coloured by which way it leans. A strike whose calls and puts
    cancel is large here and zero in ``net``, which is the point of the mode.
    A cell with exactly zero net sits on the call side by convention.
    """
    if mode not in VALUES:
        raise ValueError(f"unknown heatmap value: {mode!r}")
    if not isinstance(cell, dict):
        return _fmt.num(cell) if mode == "net" else None
    if mode == "net":
        return _fmt.num(cell.get("net"))
    call, put = _fmt.num(cell.get("call")), _fmt.num(cell.get("put"))
    if mode == "call":
        return call
    if mode == "put":
        return put
    if call is None or put is None:
        return None
    net = _fmt.num(cell.get("net"))
    size = abs(call) + abs(put)
    return -size if (call + put if net is None else net) < 0 else size


def has_sides(grids):
    """Whether any cell in these grids carries BOTH a call and a put.

    Rows stored before the cells were split are bare numbers; with none that
    carry sides, Calls, Puts and Size have nothing to draw and are switched off
    rather than drawn as zero."""
    for grid in grids or ():
        if not isinstance(grid, dict):
            continue
        for cell in grid.values():
            if (isinstance(cell, dict) and _fmt.num(cell.get("call")) is not None
                    and _fmt.num(cell.get("put")) is not None):
                return True
    return False
```

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py -q)`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma_heat.py webgui/tests/test_gamma_heat.py
git commit -m "feat(gamma): the value a heatmap cell draws - net, calls, puts or size"
```

### Task 1.3: The heatmap draws the chosen value

**Files:**
- Modify: `webgui/pages/options/gamma.py` — imports (line 35), `heatmap_matrix` (line 661), `heatmap_figure` (line 904)
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests** (append to `webgui/tests/test_options_gamma.py`)

```python
def _sided_rows():
    return [("09:30", 100.0, None, None, None, 0,
             {99.0: {"call": 2.0, "put": -8.0, "net": -6.0},
              100.0: {"call": 50.0, "put": -50.0, "net": 0.0},
              101.0: {"call": 9.0, "put": -1.0, "net": 8.0}}),
            ("09:31", 100.0, None, None, None, 0,
             {99.0: {"call": 2.0, "put": -9.0, "net": -7.0},
              100.0: {"call": 55.0, "put": -55.0, "net": 0.0},
              101.0: {"call": 9.0, "put": -2.0, "net": 7.0}})]


def test_heatmap_matrix_net_is_unchanged_by_the_new_argument():
    assert gamma.heatmap_matrix(_sided_rows()) == gamma.heatmap_matrix(_sided_rows(), "net")
    # The balanced strike has zero net in every column, so net drops its row.
    assert gamma.heatmap_matrix(_sided_rows())["y"] == [99.0, 101.0]


def test_heatmap_matrix_size_keeps_the_balanced_strike():
    m = gamma.heatmap_matrix(_sided_rows(), "size")
    assert m["y"] == [99.0, 100.0, 101.0]
    assert m["z"][1] == [100.0, 110.0]          # the largest row on the board
    assert m["z"][0] == [-10.0, -11.0]          # signed by net


def test_heatmap_matrix_calls_and_puts():
    assert gamma.heatmap_matrix(_sided_rows(), "call")["z"][2] == [9.0, 9.0]
    assert gamma.heatmap_matrix(_sided_rows(), "put")["z"][0] == [-8.0, -9.0]


@pytest.mark.parametrize("mode", ["net", "call", "put", "size"])
def test_heatmap_series_count_is_nine_in_every_value(mode):
    fig = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0], mode=mode)
    assert len(fig["series"]) == 9
    assert fig["colorAxis"]["stops"] == gamma.HEAT_STOPS


def test_heatmap_title_and_tooltip_name_the_value():
    net = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0])
    size = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0], mode="size")
    assert net["title"]["text"] == "GAMMA intraday (strike × time)"
    assert size["title"]["text"] == "GAMMA intraday (strike × time) · Size"
    assert "net {point.value" in net["series"][0]["tooltip"]["pointFormat"]
    assert "size {point.value" in size["series"][0]["tooltip"]["pointFormat"]


def test_heatmap_does_not_write_to_its_rows():
    rows = _sided_rows()
    before = json.dumps(rows, sort_keys=True)
    for mode in ("net", "call", "put", "size"):
        gamma.heatmap_figure(rows, "GEX", yrange=[95.0, 105.0], mode=mode)
    assert json.dumps(rows, sort_keys=True) == before
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q -k "sided or heatmap_matrix_ or in_every_value or name_the_value or write_to_its_rows")`
Expected: FAIL, `heatmap_matrix() takes 1 positional argument but 2 were given`.

**Step 3: Implement.**

Imports, next to `from . import flow_panels as _fx`:

```python
from . import gamma_heat as _heat
```

`heatmap_matrix` — change the signature and the two lines that read cells. Net keeps `_cell_net` exactly, so today's output does not move by a byte:

```python
def heatmap_matrix(rows, mode="net"):
```

and add one sentence to the end of its docstring: ``` ``mode`` picks the number a cell draws (``gamma_heat.cell_value``); net is the default and is unchanged.```

```python
    val = _cell_net if mode == "net" else (lambda cell: _heat.cell_value(cell, mode))
    strikes = sorted({s for g in grids for s, cell in g.items() if val(cell)})
    z = [[val(g.get(s) or {}) for g in grids] for s in strikes]
```

`heatmap_figure` — add `mode="net"` as the last keyword in the signature, then three edits in the body:

```python
    m = heatmap_matrix(rows, mode)
```

```python
               "tooltip": {"headerFormat": "",
                           "pointFormat": "Strike {point.y:.2f} · "
                                          + _heat.VALUES[mode].lower()
                                          + " {point.value:,.0f}"}}]
```

```python
        "title": {"text": f"{_view_label(view)} intraday (strike × time)"
                          + ("" if mode == "net" else f" · {_heat.VALUES[mode]}"),
                  "style": {"color": FONT}},
```

The forward projection grid is net only. Nothing in `heatmap_figure` enforces that; the caller decides (Task 1.7), because the hedge panel under the heatmap must be built on the same category list.

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q)`
Expected: PASS, every existing test included.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): the heatmap draws calls, puts or size as well as net"
```

### Task 1.4: The bars follow the value, and split in Size

**Files:**
- Modify: `webgui/pages/options/gamma.py` — `bars_from_gex` (line 423), `bar_figure` (line 554)
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

```python
SIDED = {"spot": 100.0, "strike_count": 3, "gex": {
    99.0: {"call": 2.0, "put": -8.0, "net": -6.0},
    100.0: {"call": 50.0, "put": -50.0, "net": 0.0},
    101.0: {"call": 9.0, "put": -1.0, "net": 8.0}}}


def test_bars_default_is_todays_net_bars():
    assert gamma.bars_from_gex(SIDED, 100.0) == gamma.bars_from_gex(SIDED, 100.0, mode="net")
    assert gamma.bars_from_gex(SIDED, 100.0)["nets"] == [-6.0, 0.0, 8.0]


def test_bars_draw_one_side_in_calls_and_puts():
    assert gamma.bars_from_gex(SIDED, 100.0, mode="call")["nets"] == [2.0, 50.0, 9.0]
    assert gamma.bars_from_gex(SIDED, 100.0, mode="put")["nets"] == [-8.0, -50.0, -1.0]


def test_size_bars_are_two_opposing_bars_per_strike():
    fig = gamma.bar_figure(SIDED, 100.0, mode="size")
    calls, puts, projected = fig["series"]
    assert [p["y"] for p in calls["data"]] == [2.0, 50.0, 9.0]
    assert [p["y"] for p in puts["data"]] == [-8.0, -50.0, -1.0]
    assert [p["x"] for p in calls["data"]] == [p["x"] for p in puts["data"]] == [99.0, 100.0, 101.0]
    assert projected["data"] == []          # the projected close is a net figure


@pytest.mark.parametrize("mode", ["net", "call", "put", "size"])
def test_bar_series_count_is_three_in_every_value(mode):
    assert len(gamma.bar_figure(SIDED, 100.0, mode=mode)["series"]) == 3
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q -k "bars_default or one_side or opposing or three_in_every")`
Expected: FAIL, `unexpected keyword argument 'mode'`.

**Step 3: Implement.**

`bars_from_gex(data, spot, n_side=N_SIDE, mode="net")`. Inside the loop, replace `net = cell.get("net", 0.0)` with:

```python
        net = cell.get("net", 0.0) if mode == "net" else _heat.cell_value(cell, mode)
        if net is None:
            continue
```

and add two parallel lists to what it collects and returns, `calls` and `puts` (`cell.get("call", 0.0)`, `cell.get("put", 0.0)`), appended after the `continue` so all lists stay the same length. Add both keys to the empty return at the top of the function. `projected` is appended as `None` whenever `mode != "net"`.

`bar_figure(..., yrange=None, mode="net")`: pass `mode` to `bars_from_gex`. Replace the loop that fills `pos_pts` / `neg_pts` with:

```python
    def _pt(strike, value, colour, hover):
        return {"x": strike, "y": value, "color": bevel_fill(colour, mirrored=True),
                "borderColor": _darker(colour), "borderWidth": 1,
                "custom": {"hover": hover}}

    pos_pts, neg_pts = [], []
    if mode == "size":
        # Calls and puts as two opposing bars per strike, in the two per-sign
        # series the panel already has. A balanced strike is then two equal
        # bars. (Charm and vanna can hold both sides on one side of zero; the
        # bars then overlap and the tooltip carries both numbers.)
        for s, c, p, h in zip(b["strikes"], b["calls"], b["puts"], b["hovers"]):
            pos_pts.append(_pt(s, c, POS_COLOR, h))
            neg_pts.append(_pt(s, p, NEG_COLOR, h))
    else:
        for s, n, c, h in zip(b["strikes"], b["nets"], b["colors"], b["hovers"]):
            (pos_pts if n >= 0 else neg_pts).append(_pt(s, n, c, h))
```

Leave the title as it is for net; for any other mode append ` · {_heat.VALUES[mode]}` the same way Task 1.3 did.

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q)`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): the by-strike bars follow the value and split calls from puts in Size"
```

### Task 1.5: Balanced-strike markers

**Files:**
- Modify: `webgui/pages/options/gamma_heat.py` (add `balanced_marks`)
- Modify: `webgui/pages/options/gamma.py` — colour constants (line 53), `line_annotations` (309), `wall_plot_lines` (339), `bar_figure`, `heatmap_figure`
- Test: `webgui/tests/test_gamma_heat.py`, `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

`webgui/tests/test_gamma_heat.py`:

```python
GRID = {
    95.0: {"call": 1.0, "put": -1.0, "net": 0.0},          # balanced but tiny
    100.0: {"call": 500.0, "put": -480.0, "net": 20.0},    # balanced and large
    105.0: {"call": 400.0, "put": -10.0, "net": 390.0},    # large, one-sided
    110.0: {"call": 300.0, "put": -290.0, "net": 10.0},    # balanced and large
    115.0: 7.0,                                            # a legacy bare number
}
KW = dict(max_polarity=0.15, min_size_quantile=0.5, max_marks=3)


def test_balanced_marks_are_the_large_strikes_that_barely_lean():
    assert gh.balanced_marks(GRID, sorted(GRID), **KW) == [100.0, 110.0]


def test_balanced_marks_are_capped_largest_first():
    assert gh.balanced_marks(GRID, sorted(GRID), **{**KW, "max_marks": 1}) == [100.0]
    assert gh.balanced_marks(GRID, sorted(GRID), **{**KW, "max_marks": 0}) == []


def test_balanced_marks_look_only_at_the_strikes_on_screen():
    assert gh.balanced_marks(GRID, [105.0, 110.0], **KW) == [110.0]


def test_balanced_marks_of_nothing():
    assert gh.balanced_marks({}, [], **KW) == []
    assert gh.balanced_marks({100.0: 5.0}, [100.0], **KW) == []
```

`webgui/tests/test_options_gamma.py`:

```python
def test_a_balanced_strike_is_marked_on_both_panels():
    bars = gamma.bar_figure(SIDED, 100.0, balanced=[100.0])
    heat = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0],
                                balanced=[100.0])
    for lines in (bars["xAxis"]["plotLines"], heat["yAxis"]["plotLines"]):
        mark = [pl for pl in lines if pl["label"]["text"].startswith("Balanced")]
        assert len(mark) == 1 and mark[0]["value"] == 100.0
        assert mark[0]["color"] == gamma.BALANCED_COLOR


def test_the_balanced_colour_is_no_other_levels_colour():
    taken = {gamma.POS_COLOR, gamma.NEG_COLOR, gamma.FLIP_COLOR,
             gamma.PROJ_FLIP_COLOR, gamma.SPOT_COLOR, gamma.PRICE_LINE}
    assert gamma.BALANCED_COLOR not in taken


def test_no_marks_means_no_balanced_lines():
    heat = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0])
    assert not [pl for pl in heat["yAxis"]["plotLines"]
                if pl["label"]["text"].startswith("Balanced")]
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py tests/test_options_gamma.py -q -k balanced)`
Expected: FAIL, `no attribute 'balanced_marks'`.

**Step 3: Implement.**

`gamma_heat.py`:

```python
def balanced_marks(grid, strikes, *, max_polarity, min_size_quantile, max_marks):
    """The strikes in ``strikes`` that hold a lot and lean little, largest first.

    A strike qualifies when ``abs(net) / size`` is at or under ``max_polarity``
    and its size is at or above the ``min_size_quantile`` rank of the strikes
    given. These are the strikes ``net`` draws as empty and ``size`` draws in a
    hue that a small change of net can flip, so they are named on the axis."""
    sized = []
    for strike in strikes or ():
        cell = (grid or {}).get(strike)
        size, net = cell_value(cell, "size"), cell_value(cell, "net")
        if not size or net is None:
            continue
        sized.append((abs(size), abs(net) / abs(size), strike))
    if not sized:
        return []
    ranked = sorted(s for s, _, _ in sized)
    floor = ranked[min(len(ranked) - 1, int(min_size_quantile * (len(ranked) - 1)))]
    hits = sorted(((s, k) for s, lean, k in sized
                   if s >= floor and lean <= max_polarity), reverse=True)
    return [k for _, k in hits[:max(0, int(max_marks))]]
```

`gamma.py`, with the other level colours:

```python
# A strike whose calls and puts nearly cancel. A cool grey: amber is the
# projected flip's, lavender the flip's, and both ramp colours are the walls'.
BALANCED_COLOR = "#9fb3c8"
```

`line_annotations(spot, flip, walls, balanced=())` — append before `return anns`:

```python
    for k in balanced or ():
        anns.append({"value": k, "text": f"Balanced {_fmt.price(k)}",
                     "color": BALANCED_COLOR})
```

`wall_plot_lines(spot, walls, flip=None, projected_flip=None, balanced=())` — append before `return out`:

```python
    for k in balanced or ():
        if _is_level(k):
            out.append(_level_plot_line(k, f"Balanced {_fmt.price(k)}", BALANCED_COLOR))
```

`bar_figure(..., mode="net", balanced=())` passes `balanced` to `line_annotations`. `heatmap_figure(..., mode="net", balanced=())` passes it to `wall_plot_lines`. (In `bar_figure` the dash style is chosen from the label text; "Balanced …" falls through to `"Dot"`, which is what we want.)

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py tests/test_options_gamma.py -q)`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma_heat.py webgui/pages/options/gamma.py webgui/tests/test_gamma_heat.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): mark the strikes whose calls and puts nearly cancel"
```

### Task 1.6: The legend

The legend is a horizontal strip in the controls row, not a column beside the chart: the heatmap runs flush to the window's right edge by design (`chart_row`'s `w-[calc(100%+1rem)]`), and it must keep the same width as the hedge panel under it.

**Files:**
- Modify: `webgui/pages/options/gamma_heat.py` (add `UNITS`, `legend_svg`)
- Modify: `webgui/pages/options/gamma.py` — `heatmap_figure` (add `legend=None`)
- Test: `webgui/tests/test_gamma_heat.py`, `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

`webgui/tests/test_gamma_heat.py`:

```python
import re

STOPS = [[0.0, "rgba(255,186,220,0.98)"], [0.5, "rgba(0,0,0,0.0)"],
         [1.0, "rgba(190,248,255,0.98)"]]


def _dompurify_allowlist():
    """The names DOMPurify keeps, read from the copy NiceGUI ships. Mirrors
    test_flow_panels.py; runs containing ``script`` are deny lists and skipped."""
    from nicegui import ui
    src = (pathlib.Path(ui.__file__).parent / "static" / "dompurify.mjs") \
        .read_text(encoding="utf-8", errors="replace")
    names = set()
    for run in re.findall(r'(?:"[a-z][a-z0-9-]*",){19,}"[a-z][a-z0-9-]*"', src):
        tokens = set(re.findall(r'"([a-z][a-z0-9-]*)"', run))
        if "script" not in tokens:
            names |= tokens
    assert len(names) > 300
    return names


def test_legend_prints_both_ends_and_the_caption():
    svg = gh.legend_svg(1_200_000_000.0, "adapts to what is visible", STOPS,
                        unit="$ gamma per 1% move")
    assert "-$1.20B" in svg and "+$1.20B" in svg
    assert "adapts to what is visible" in svg and "$ gamma per 1% move" in svg


def test_legend_without_a_dollar_unit_prints_the_bare_number():
    svg = gh.legend_svg(4_500_000.0, "adapts to what is visible", STOPS, unit="")
    assert "+4.50M" in svg and "$" not in svg


def test_legend_of_no_scale_is_empty():
    assert gh.legend_svg(None, "x", STOPS, unit="") == ""
    assert gh.legend_svg(0, "x", STOPS, unit="") == ""


def test_legend_runs_put_side_to_call_side_with_every_stop():
    svg = gh.legend_svg(10.0, "x", STOPS, unit="")
    assert svg.count("<stop ") == len(STOPS)
    assert svg.index("rgb(255,186,220)") < svg.index("rgb(190,248,255)")


def test_legend_survives_the_sanitizer():
    """ui.html runs DOMPurify; a stripped tag or attribute fails silently."""
    allow = _dompurify_allowlist()
    svg = gh.legend_svg(10.0, "held since 09:30", STOPS, unit="$ delta")
    names = set(re.findall(r"<([a-zA-Z][\w-]*)", svg)) | set(re.findall(r'([a-zA-Z][\w-]*)="', svg))
    stripped = sorted(n for n in names if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    assert {"svg", "linearGradient", "stop", "rect", "text"} <= names
    assert "dominant-baseline" not in svg and "data-" not in svg
```

`webgui/tests/test_options_gamma.py`:

```python
def test_heatmap_reports_its_scale_to_the_legend():
    legend = {}
    fig = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0],
                               mode="size", legend=legend)
    assert legend["zmax"] == fig["colorAxis"]["max"] > 0
    assert legend["mode"] == "size"
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py tests/test_options_gamma.py -q -k legend)`
Expected: FAIL, `no attribute 'legend_svg'`.

**Step 3: Implement.**

`gamma_heat.py`:

```python
import re

# The unit a view's cells are in, where it is a plain dollar figure
# (options-scanner/gamma_tool.py). Charm and vanna print the bare number.
UNITS = {"GEX": "$ gamma per 1% move", "DEX": "$ delta"}

_RGBA = re.compile(r"rgba?\(([^)]+)\)")
_LEGEND_W, _LEGEND_H, _RAMP_X, _RAMP_W = 420, 16, 70, 120
_BASELINE_DY = "0.35em"        # dominant-baseline is stripped by the sanitizer


def _stop(colour):
    """``rgba(r,g,b,a)`` -> (``rgb(r,g,b)``, opacity): SVG takes them apart."""
    parts = [p.strip() for p in _RGBA.match(colour).group(1).split(",")]
    r, g, b = (int(float(p)) for p in parts[:3])
    return f"rgb({r},{g},{b})", (float(parts[3]) if len(parts) > 3 else 1.0)


def legend_svg(zmax, caption, stops, *, unit, uid="gheat"):
    """The heatmap's colour scale as one SVG strip: the two ends of the scale in
    numbers, the ramp between them, the unit and what the scale is tied to.

    ``stops`` is the colour axis's own list, so the strip cannot drift from the
    chart. Empty when there is no scale to describe."""
    top = _fmt.num(zmax)
    if not top:
        return ""
    if unit.startswith("$"):
        lo, hi = _fmt.money_short(-top), _fmt.money_short(top, signed=True)
    else:
        text, suffix = _fmt.scaled(top)
        lo, hi = f"-{text}{suffix}", f"+{text}{suffix}"
    grad = "".join(
        f'<stop offset="{pos:.2f}" stop-color="{rgb}" stop-opacity="{alpha:.2f}"></stop>'
        for pos, (rgb, alpha) in ((p, _stop(c)) for p, c in stops))
    mid = _LEGEND_H / 2
    tail = " · ".join(t for t in (unit, caption) if t)
    return (
        f'<svg width="{_LEGEND_W}" height="{_LEGEND_H}" '
        f'viewBox="0 0 {_LEGEND_W} {_LEGEND_H}">'
        f'<defs><linearGradient id="{uid}-ramp" x1="0" y1="0" x2="1" y2="0">'
        f'{grad}</linearGradient></defs>'
        f'<text x="{_RAMP_X - 6}" y="{mid}" dy="{_BASELINE_DY}" text-anchor="end" '
        f'font-size="10" fill="#a8adb8">{lo}</text>'
        f'<rect x="{_RAMP_X}" y="3" width="{_RAMP_W}" height="{_LEGEND_H - 6}" '
        f'fill="#1c2238"></rect>'
        f'<rect x="{_RAMP_X}" y="3" width="{_RAMP_W}" height="{_LEGEND_H - 6}" '
        f'fill="url(#{uid}-ramp)"></rect>'
        f'<text x="{_RAMP_X + _RAMP_W + 6}" y="{mid}" dy="{_BASELINE_DY}" '
        f'font-size="10" fill="#a8adb8">{hi}</text>'
        f'<text x="{_RAMP_X + _RAMP_W + 70}" y="{mid}" dy="{_BASELINE_DY}" '
        f'font-size="10" fill="#6e7482">{tail}</text>'
        f'</svg>')
```

Check `_fmt.money_short(-top)` prints `-$1.20B` (read `pages/fmt.py:173`); the test pins it.

`heatmap_figure(..., balanced=(), legend=None)` — add to the docstring: "``legend`` (a dict, when given) receives the scale this paint used, for the strip beside the controls." Then, immediately before `fig = _base_chart("heatmap", height)`:

```python
    if legend is not None:
        legend.update(zmax=zmax, mode=mode)
```

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_gamma_heat.py tests/test_options_gamma.py -q)`
Expected: PASS. If `test_legend_survives_the_sanitizer` names a stripped attribute, remove that attribute from the SVG; do not add it to an allow-list.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma_heat.py webgui/pages/options/gamma.py webgui/tests/test_gamma_heat.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): a legend strip that puts numbers on the heatmap's colours"
```

### Task 1.7: The Value control, wired into the page

**Files:**
- Modify: `webgui/app_settings.py` (DEFAULTS, after `gamma_spot_interval`, line 40)
- Modify: `webgui/tests/test_live_screens.py` (`PUBLIC_SAFE_DEFAULTS`, line 197)
- Modify: `webgui/pages/options/gamma.py` — a module-level `HeatControls` above `render`; four small edits inside `render`
- Modify: `webgui/tests/test_render_size.py`
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

```python
def test_heat_setting_falls_back_on_a_value_it_does_not_know(monkeypatch):
    monkeypatch.setattr(gamma.app_settings, "get", lambda k: "gross")
    assert gamma._heat_setting("gamma_heat_value", gamma._heat.VALUES, "net") == "net"
    monkeypatch.setattr(gamma.app_settings, "get", lambda k: "size")
    assert gamma._heat_setting("gamma_heat_value", gamma._heat.VALUES, "net") == "size"


def test_the_projection_band_is_kept_only_for_net():
    """The forward projection is a net grid. In any other value the band is
    dropped by the CALLER, so the hedge panel is built on the same columns."""
    assert gamma.heat_keeps_projection("net")
    for mode in ("call", "put", "size"):
        assert not gamma.heat_keeps_projection(mode)


def test_render_builds_the_heat_controls_and_feeds_both_panels():
    src = inspect.getsource(gamma.render)
    assert "heat = HeatControls()" in src
    assert "heat.on_change(_render_view)" in src
    paint = src[src.index("def _render_view("):]
    assert "**heat.args()" in paint                  # heatmap_figure and bar_figure
    assert paint.count("heat_keeps_projection(") == 1
    assert "heat.show_legend(" in paint
    sync = src[src.index("def _sync_spot_controls("):]
    assert "heat.sync(" in sync[:sync.index("\n    spot_style_sel.on_value_change")]
```

**Step 2: Run to see them fail**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py -q -k "heat_setting or projection_band or heat_controls")`
Expected: FAIL, `no attribute '_heat_setting'`.

**Step 3: Implement.**

`webgui/app_settings.py`, after the `gamma_spot_interval` line:

```python
    "gamma_heat_value": "net",       # heatmap cell: net | call | put | size
```

`webgui/tests/test_live_screens.py`: add `"gamma_heat_value"` to `PUBLIC_SAFE_DEFAULTS`. It is read purely to choose what to draw, and its write is a no-op on a frozen store, the same reasoning as `gamma_level_tracks` beside it.

`gamma.py`, module level, above `overlay_handler`:

```python
def _heat_setting(key, allowed, default):
    """A stored heatmap choice, or ``default`` when it is not one of ``allowed``
    (an older build's value, or a hand-edited settings file)."""
    value = app_settings.get(key)
    return value if value in allowed else default


def heat_keeps_projection(mode):
    """Whether the GEX forward band is drawn. It is a NET grid, so only net keeps
    it. The caller decides, never ``heatmap_figure``: the hedge panel under the
    heatmap must be built on the same category list."""
    return mode == "net"


class HeatControls:
    """The heatmap's pickers and its legend strip.

    Module-level so ``render`` holds one object and no per-control handler.
    Built inside the symbol-scoped controls row; ``on_change`` is attached once
    the page's repaint exists."""

    def __init__(self):
        self.value = ui.select(
            dict(_heat.VALUES),
            value=_heat_setting("gamma_heat_value", _heat.VALUES, "net"),
            label="Value").props("dense options-dense").classes("w-24")
        self.value.tooltip(
            "What each cell holds. Size is calls plus puts, coloured by which "
            "way the strike leans: a strike whose calls and puts cancel is "
            "empty in Net and bright in Size.")
        self.legend = ui.html("").classes("ml-auto")
        self._sided = True

    def on_change(self, repaint):
        self.value.on_value_change(
            overlay_handler("gamma_heat_value", str, repaint))

    def sync(self, view):
        """Shown on the four Greek views only: the other views have no cells."""
        on = view in _VIEWS
        self.value.set_visibility(on)
        self.legend.set_visibility(on)

    def set_sides(self, sided):
        """Rows stored before cells carried a call and a put can draw net only."""
        if sided == self._sided:
            return
        self._sided = sided
        self.value.set_enabled(sided)
        if not sided:
            self.value.set_value("net")

    def args(self):
        return {"mode": self.value.value if self._sided else "net"}

    def marks(self, grid, strikes):
        if not self._sided:
            return []
        return _heat.balanced_marks(grid, strikes, **_heat_cfg.balanced())

    def show_legend(self, view, legend):
        self.legend.content = _heat.legend_svg(
            legend.get("zmax"), "adapts to what is visible", HEAT_STOPS,
            unit=_heat.UNITS.get(view, ""))
```

and with the other imports at the top of `gamma.py`:

```python
from shared import gamma_heat_config as _heat_cfg  # Tier-1 allow-listed
```

Inside `render`, four edits. Count lines as you go; the ceiling test is the judge.

1. In the symbol-scoped controls row, directly after the `spot_int_sel.tooltip(...)` statement (around line 2412):

```python
            heat = HeatControls()
```

2. In `_sync_spot_controls`, as its last statement (it must stay inside the function, before the `spot_style_sel.on_value_change` line):

```python
        heat.sync(view_toggle.value)
```

3. Next to `tracks_sw.on_value_change(...)`:

```python
    heat.on_change(_render_view)
```

4. In `_render_view`, the Greek-view branch (from `entry = (snap.get("views") or {}).get(view) or {}`, around line 2864). After the `rows` list is built:

```python
        heat.set_sides(_heat.has_sides([data["gex"]] + [r[6] for r in rows if len(r) > 6]))
```

Change the bars call to:

```python
        _bar_strikes = bars_from_gex(data, view_spot)["strikes"]
        yr = union_range(bar_yrange(_bar_strikes, view_spot), spot_path)
        _marks = heat.marks(data["gex"], _bar_strikes)
        _set_chart(bar_figure(data, view_spot, view=view, walls=walls, flip=flip,
                              yrange=yr, balanced=_marks, **heat.args()))
```

(this replaces the two existing `yr = …` lines and the existing `_set_chart(bar_figure(...))` line). Change the projection guard from `if view == "GEX":` to:

```python
            if view == "GEX" and heat_keeps_projection(heat.args()["mode"]):
```

and the heatmap call gains three keywords and is followed by the legend:

```python
            _legend = {}
            _set_figure(heat_plot, heatmap_figure(rows, view, yrange=yr,
                                                  ...existing keywords...,
                                                  balanced=_marks, legend=_legend,
                                                  **heat.args()))
            heat.show_legend(view, _legend)
```

**Step 4: Lower the ceiling.** Run the measuring command from Task 0.2 and set `CEILINGS["pages.options.gamma"]` to what it prints. It must be at or under the pair Task 0.2 set plus zero nested functions; if the line count is over, tighten the edits above (the `heat.set_sides(...)` argument can move into a `HeatControls.read(data, rows)` method), never the ceiling.

**Step 5: Run**

Run: `(cd webgui && "$PY" -m pytest tests/test_options_gamma.py tests/test_render_size.py tests/test_live_screens.py tests/test_gamma_public_page.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py -q)`
Expected: PASS.

**Step 6: Commit**

```bash
git add webgui/app_settings.py webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py webgui/tests/test_render_size.py webgui/tests/test_live_screens.py
git commit -m "feat(gamma): a Value picker for the heatmap and the bars, with a legend"
```

### Task 1.8: See it in a browser

There is no dev environment, so the substitute is the local page harness on a fake bus. Say so in the phase's final commit message.

**Step 1: Write a seed** (in the scratchpad, not the repo). Save as `seed_gamma.py` and run it with `"$PY" seed_gamma.py > seed.json`:

```python
"""A synthetic $SPX session for the page harness: 60 one-minute rows, 41 strikes,
with strike 5850 holding equal calls and puts."""
import json
import math

strikes = [5700.0 + 5 * i for i in range(41)]
t0 = 1791462600                     # any 08:30 CT epoch; only differences matter


def cell(k, spot, minute):
    near = math.exp(-((k - spot) ** 2) / (2 * 30 ** 2)) * (1 + minute / 60)
    call = 4e8 * near * (1.6 if k >= spot else 0.5) + (9e8 if k == 5850 else 0)
    put = -(4e8 * near * (1.6 if k < spot else 0.5) + (9e8 if k == 5850 else 0))
    return {"call": call, "put": put, "net": call + put}


rows, spot = [], 5806.0
for m in range(60):
    spot += math.sin(m / 9) * 1.5
    rows.append([t0 + 60 * m, spot, 5790.0, None, None, 0,
                 {str(k): cell(k, spot, m) for k in strikes}])
grid = rows[-1][6]
snap = {"symbol": "$SPX", "spot": spot, "dte": 0, "session_dte": 0, "term": {},
        "flow": [], "hedge_history": [], "prem_ladder": [], "projected_flip": None,
        "views": {v: {"data": {"spot": spot, "gex": grid, "strike_count": len(grid)},
                      "summary": {}, "walls": [5850.0, 5750.0], "flip": 5790.0,
                      "levels": {"flip": [5790.0] * 60, "call_wall": [5850.0] * 60,
                                 "put_wall": [5750.0] * 60}}
                  for v in ("GEX", "Charm", "DEX", "Vanna")}}
seed = {"options:gamma": snap, "options:gamma_symbols": ["$SPX", "SPY", "QQQ"]}
for v in ("gex", "charm", "dex", "vanna"):
    seed[f"options:gamma_hist_{v}"] = {"symbol": "$SPX", "view": v.upper(), "rows": rows}
print(json.dumps(seed))
```

If the page shows "No intraday snapshots yet", read `gamma.history_rows` and `gamma._load_history` and correct the seed's key or shape; do not change the page.

**Step 2: Run the harness.** First confirm the port answers nothing (a taken port fails to bind silently):

```bash
"$PY" tools/ui_harness.py options.gamma --seed seed.json --port 9591
```

**Step 3: Check, in the Browser pane at `http://127.0.0.1:9591`:**

- Value → Size: the 5850 row is the brightest on the heatmap; the left panel shows two equal opposing bars at 5850; a grey dotted "Balanced 5850.00" line crosses both panels.
- Value → Net: the 5850 row is empty, and the Balanced line is still there.
- Value → Calls, Puts: one side only, on both panels.
- The legend strip shows two dollar figures and "$ gamma per 1% move" on GEX, and bare numbers on Charm.
- Switch view tabs with Size selected: no stray lines, no blank heatmap (the series count held).
- No console errors (`read_console_messages`, errors only).
- Repeat once with `--public` added to the harness command.

Take one screenshot of Size on GEX for the user.

**Step 4: Stop the harness.** It does not stop itself.

### Task 1.9: Documents, full suites, commit

**Files:**
- Modify: `webgui/page_help.py` — the `/options/gamma` guide (line 733) and the lens entry (line 1848)
- Modify: the Dealer Positioning sections under `docs/manuals/user-guide/`, `docs/manuals/reference-guide/` and `docs/manuals/technical-reference/` (search each for "Dealer Positioning")
- Modify: `docs/webgui-routes.md` (`## /options/gamma`), `docs/CHANGELOG.md`

**Step 1: Write the text.** Whole words, plain sentences, from the reader's side. What each must say:

- `page_help.py`: what the Value picker does in one line each; that Size shows how much is at a strike and which way it leans; that a "Balanced" line marks a strike whose calls and puts nearly cancel; that the strip beside the controls gives the dollar value of the brightest colours.
- User Guide: how to find a balanced strike (pick Size).
- Reference Guide: when to open each value.
- Technical Reference: `size = abs(call) + abs(put)`, signed by net; the balanced rule and its three config keys; that the scale is the 95th percentile of the visible cells.
- `docs/webgui-routes.md`: the new keyword arguments, `HeatControls`, the legend strip's placement and why it is not a column.
- `docs/CHANGELOG.md`: a dated entry.

**Step 2: Run everything this phase touched**

```bash
(cd webgui && "$PY" -m pytest . -q -rf 2>&1 | tail -8)
"$PY" -m pytest shared/tests tests -q -rf 2>&1 | tail -8
```

Expected: the failing set equals Task 0.1's.

**Step 3: Commit**

```bash
git add webgui/page_help.py docs/manuals docs/webgui-routes.md docs/CHANGELOG.md
git commit -m "docs(gamma): the heatmap's Value picker, balanced markers and legend"
```

**Step 4: Stop.** Phase 1 is shippable alone. Ask the user whether to merge, push and promote now or carry on to Phase 2.

---

## Phases 0 and 1 as built (2026-10-09)

Both are done and committed on this branch. Five things were built differently from
the tasks above. **Phase 2 onward must start from these, not from the task text.**

1. **`HeatControls.read(grid, rows, strikes)` replaced `args()` and `marks()`.** It
   returns one dict, `{"mode": …, "balanced": […]}`, that `_render_view` passes to
   BOTH builders as `**_hk`. It also sets whether the session has sides and clears
   the legend. When Phase 2 adds `scale` and `lock`, add them to that dict (both
   builders must then accept them) and give `read` the view's `entry`.
2. **`bars_from_gex` kept its return shape.** An existing test pins the exact keys of
   its empty return, so it gained no `calls` / `puts` lists. In Size, `bar_figure`
   reads each strike's two sides from `data["gex"]` itself.
3. **Two builders moved out of `render` to pay for the new lines:** `refloat_rows`
   and `projection_arg`, both module-level and tested. `render` is **1,469 lines and
   59 nested functions**, which is exactly the ceiling Task 0.2 set. It has no
   slack: every line a later phase adds to `render` needs a line moved out.
4. **A session with no sided cells leaves the picker's value alone.** The plan had
   `set_sides` write "net" into the picker; that would fire the change handler from
   inside a paint. The paint falls back to net and the picker is disabled.
5. **The legend is 520 px wide**, not 420, so the unit and the caption fit.

Also found while verifying, and not caused by this work: loading the page while the
Browser pane is hidden logs `<rect> height -1` and `scale(NaN NaN)` errors, because
the charts mount at near-zero width. A view switch and a value change log none.
`test_live_screens.py`'s comment on the `gamma_*` settings was reworded to count
`gamma_heat_value`; no assertion changed.

Not done from Task 1.8: the checks were made on a synthetic session. Nothing has been
seen on prod data, and the public render was checked for building with the picker,
not with a live symbol.

---

## Phase 2 — The locked scale, and Share of column

### Task 2.1: Buy room in `compute.py`

`compute._window_around` (line 4046) is pure and has no other user outside `compute.py`. Moving it to a sibling frees its lines.

**Files:**
- Create: `services/options_svc/gamma_window.py`
- Modify: `services/options_svc/compute.py` (delete the function body; import it)
- Modify: `services/options_svc/tests/test_compute_module_shape.py` (lower `COMPUTE_MAX_LINES`)
- Test: `services/options_svc/tests/test_gamma_window.py`

**Step 1: Write the failing tests** (`services/options_svc/tests/test_gamma_window.py`)

```python
"""The display window around spot, and what is computed inside it."""
from services.options_svc import compute, gamma_window as gw


def test_window_is_n_strikes_each_side_and_the_strike_at_spot():
    strikes = [float(k) for k in range(90, 111)]
    assert gw.window_around(strikes, 100.0, 2) == {98.0, 99.0, 100.0, 101.0, 102.0}
    assert gw.window_around(strikes, 100.5, 2) == {99.0, 100.0, 101.0, 102.0}


def test_no_usable_spot_keeps_every_numeric_strike():
    assert gw.window_around([1.0, 2.0, "x", None], None, 2) == {1.0, 2.0}


def test_compute_uses_the_one_window():
    assert compute._window_around is gw.window_around
```

**Step 2: Run to see them fail**

Run: `"$PY" -m pytest services/options_svc/tests/test_gamma_window.py -q`
Expected: FAIL, `cannot import name 'gamma_window'`.

**Step 3: Implement.** `services/options_svc/gamma_window.py` (it imports nothing from `compute`):

```python
"""The Dealer Positioning display window: which strikes sit around spot, and the
figures computed inside that window.

A sibling of ``compute`` and never an importer of it (``compute.py`` has a line
ceiling; see tests/test_compute_module_shape.py).
"""
from shared.numeric import finite


def window_around(strikes, spot, n_side):
    """The nearest ``n_side`` strikes below spot, the strike at spot, and the
    nearest ``n_side`` above: mirrors the page's ``gamma.strikes_around``.
    A set of floats; an unusable spot keeps every numeric strike."""
    s = sorted({x for x in (strikes or []) if isinstance(x, (int, float))
                and not isinstance(x, bool)})
    if finite(spot) is None:
        return set(s)
    below = [x for x in s if x < spot][-n_side:]
    above = [x for x in s if x > spot][:n_side]
    return set(below + [x for x in s if x == spot] + above)
```

In `compute.py`: delete the body of `def _window_around` and replace the whole function with

```python
from services.options_svc.gamma_window import window_around as _window_around  # noqa: E402
```

placed where the function was (keep the `GAMMA_N_SIDE = 20` line above it; Phase 3 moves it). Every existing call passes `n_side` positionally or relies on the old default: search `compute.py` for `_window_around(` and make every call pass `n_side` explicitly, since the sibling has no default.

Lower `COMPUTE_MAX_LINES` to the file's new line count plus 20 (`wc -l services/options_svc/compute.py`).

**Step 4: Run**

Run: `"$PY" -m pytest services/options_svc -q -rf 2>&1 | tail -8`
Expected: the failing set equals Task 0.1's.

**Step 5: Commit**

```bash
git add services/options_svc/gamma_window.py services/options_svc/compute.py services/options_svc/tests/test_gamma_window.py services/options_svc/tests/test_compute_module_shape.py
git commit -m "refactor(options_svc): the gamma display window moves to its own module"
```

### Task 2.2: Compute the lock

**Files:**
- Modify: `services/options_svc/gamma_window.py`
- Test: `services/options_svc/tests/test_gamma_window.py`

**Step 1: Write the failing tests**

```python
def _rows(minutes, scale=1.0, spot=100.0):
    """One row a minute from t=1000; five strikes; the cell at 100 is the largest."""
    out = []
    for m in range(minutes):
        grid = {k: {"call": 10.0 * scale * w, "put": -4.0 * scale * w,
                    "net": 6.0 * scale * w}
                for k, w in ((98.0, 1), (99.0, 2), (100.0, 5), (101.0, 2), (102.0, 1))}
        grid[500.0] = {"call": 9e9, "put": -9e9, "net": 9e9}     # far outside the window
        out.append((1000 + 60 * m, spot, None, None, None, 0, grid))
    return out


LOCK = dict(n_side=2, minutes=30, quantile=1.0, headroom=1.5)


def test_no_lock_until_the_window_has_passed():
    assert gw.scale_lock(_rows(30), **LOCK) is None       # the 30th minute not yet over
    assert gw.scale_lock(_rows(31), **LOCK) is not None


def test_the_lock_is_the_quantile_times_the_headroom_per_value():
    lock = gw.scale_lock(_rows(40), **LOCK)
    assert lock == {"minutes": 30, "net": 45.0, "call": 75.0, "put": 30.0, "size": 105.0}


def test_the_lock_ignores_strikes_outside_the_display_window():
    assert gw.scale_lock(_rows(40), **LOCK)["net"] < 1e6


def test_the_lock_does_not_move_after_it_is_set():
    """Later rows, however large, are outside the window it was computed from."""
    early = gw.scale_lock(_rows(40), **LOCK)
    late = gw.scale_lock(_rows(31) + _rows(300, scale=50.0)[31:], **LOCK)
    assert early == late


def test_the_lock_is_none_for_rows_it_cannot_read():
    for rows in (None, [], [("x", 1.0, 0, 0, 0, 0, {})], [(1, 2, 3)],
                 [(1000 + 60 * m, None, 0, 0, 0, 0, {}) for m in range(40)]):
        assert gw.scale_lock(rows, **LOCK) is None


def test_bare_number_cells_lock_net_only():
    rows = [(1000 + 60 * m, 100.0, 0, 0, 0, 0, {99.0: 4.0, 100.0: -8.0}) for m in range(40)]
    lock = gw.scale_lock(rows, **LOCK)
    assert lock["net"] == 12.0 and lock["call"] is None and lock["size"] is None
```

**Step 2: Run to see them fail**

Run: `"$PY" -m pytest services/options_svc/tests/test_gamma_window.py -q`
Expected: FAIL, `no attribute 'scale_lock'`.

**Step 3: Implement** (append to `gamma_window.py`)

```python
_LOCK_VALUES = ("net", "call", "put", "size")


def _cell_values(cell):
    """``{value: number}`` for one stored cell; a side it lacks is absent."""
    if not isinstance(cell, dict):
        net = finite(cell)
        return {} if net is None else {"net": net}
    out = {}
    net, call, put = finite(cell.get("net")), finite(cell.get("call")), finite(cell.get("put"))
    if net is not None:
        out["net"] = net
    if call is not None:
        out["call"] = call
    if put is not None:
        out["put"] = put
    if call is not None and put is not None:
        out["size"] = abs(call) + abs(put)
    return out


def scale_lock(rows, *, n_side, minutes, quantile, headroom):
    """The heatmap's locked colour maximum per value, or None before it exists.

    Taken from the session's first ``minutes`` only: the ``quantile`` of the
    absolute cell, inside the display window around each row's OWN spot, times
    ``headroom``. Rows are append-only for a session, so the answer is the same
    on every build once those minutes have passed, which is what lets the page
    print it as a fixed dollar figure. A value no cell carries is None.

    ``rows`` are the uncropped history rows ``(ts, spot, …, grid)``. Total:
    anything unreadable yields None, never an exception."""
    usable = [r for r in rows or ()
              if isinstance(r, (list, tuple)) and len(r) > 6 and finite(r[0]) is not None]
    if not usable:
        return None
    end = usable[0][0] + minutes * 60
    if usable[-1][0] < end:
        return None
    seen = {name: [] for name in _LOCK_VALUES}
    for row in usable:
        if row[0] >= end:
            break
        spot, grid = finite(row[1]), row[6]
        if spot is None or not isinstance(grid, dict):
            continue
        by_strike = {}
        for raw, cell in grid.items():
            strike = finite(raw) if not isinstance(raw, str) else None
            if strike is None:
                try:
                    strike = float(raw)
                except (TypeError, ValueError):
                    continue
            by_strike[strike] = cell
        for strike in window_around(by_strike, spot, n_side):
            for name, value in _cell_values(by_strike[strike]).items():
                if value:
                    seen[name].append(abs(value))
    if not seen["net"]:
        return None
    out = {"minutes": minutes}
    for name, values in seen.items():
        values.sort()
        out[name] = (values[min(len(values) - 1, int(quantile * (len(values) - 1)))]
                     * headroom) if values else None
    return out
```

Read `shared/numeric.py:32` before relying on `finite`: it refuses a bool and text, which is why string strike keys are parsed separately.

**Step 4: Run**

Run: `"$PY" -m pytest services/options_svc/tests/test_gamma_window.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/gamma_window.py services/options_svc/tests/test_gamma_window.py
git commit -m "feat(options_svc): a colour-scale lock from the session's first hour"
```

### Task 2.3: Publish the lock

**Files:**
- Modify: `config/gamma_heat.toml`, `shared/gamma_heat_config.py`, `webgui/config_schema.py`
- Modify: `services/options_svc/compute.py` — the `entry = {…}` dict in `gamma_snapshot` (line 4583)
- Test: `shared/tests/test_gamma_heat_config.py`, `services/options_svc/tests/test_gamma_window.py`

**Step 1: Write the failing tests**

`shared/tests/test_gamma_heat_config.py`:

```python
def test_lock_defaults():
    assert cfg.lock() == {"minutes": 60, "quantile": 0.95, "headroom": 1.5}
```

`services/options_svc/tests/test_gamma_window.py`:

```python
def test_the_snapshot_carries_a_lock_for_every_view():
    """Read at the source: the lock is built from the rows BEFORE the crop, the
    same place the level tracks are, because the crop follows the current spot."""
    import inspect
    src = inspect.getsource(compute.gamma_snapshot)
    entry = src[src.index("entry = {"):src.index("if vname == \"DEX\":")]
    assert '"scale_lock": _gw.scale_lock(_rows' in entry
    assert src.index('"scale_lock"') < src.index("_crop_gamma_views(views, spot)")
```

**Step 2: Run to see them fail.** Expected: FAIL, `no attribute 'lock'` and `substring not found`.

**Step 3: Implement.**

`config/gamma_heat.toml`, append:

```toml

[lock]
# The colour scale is fixed from the session's first this-many minutes.
minutes = 60
# The percentile of the absolute cell taken over those minutes.
quantile = 0.95
# Multiplied on, so the first hour's largest cells are not already saturated.
headroom = 1.5
```

`shared/gamma_heat_config.py`: add `"lock": {"minutes": 60, "quantile": 0.95, "headroom": 1.5}` to `DEFAULTS` and

```python
def lock() -> dict:
    """``{"minutes", "quantile", "headroom"}`` for the service's scale lock."""
    return {"minutes": _setting("lock", "minutes", minimum=5),
            "quantile": _setting("lock", "quantile", minimum=0.5, maximum=1.0),
            "headroom": _setting("lock", "headroom", minimum=1.0)}
```

`webgui/config_schema.py`: a second `Section("Locked colour scale", "Set once each session by the options service.", (…), restart=())` with the three fields (`int` minutes 5–390, `fraction` quantile, `float` headroom 1–5). The service reads it per build, so no restart.

`compute.py`: with the other sibling imports, `from services.options_svc import gamma_window as _gw` and `from shared import gamma_heat_config as _heat_cfg` (reuse an existing import of either if one is there). In the `entry = {…}` dict, after the `"levels"` item:

```python
                "scale_lock": _gw.scale_lock(_rows, n_side=GAMMA_N_SIDE,
                                             **_heat_cfg.lock()),
```

`scale_lock` is total, so it needs no guard of its own. Run the shape test; if `compute.py` is over, the import line of Task 2.1 can absorb `_gw` (import the module once and write `_window_around = _gw.window_around`).

**Step 4: Run**

Run: `"$PY" -m pytest services/options_svc shared/tests -q -rf 2>&1 | tail -8 && (cd webgui && "$PY" -m pytest tests/test_config_schema.py -q)`
Expected: the failing set equals Task 0.1's.

**Step 5: Commit**

```bash
git add config/gamma_heat.toml shared/gamma_heat_config.py shared/tests/test_gamma_heat_config.py webgui/config_schema.py services/options_svc/compute.py services/options_svc/tests/test_gamma_window.py
git commit -m "feat(options_svc): publish each view's scale lock in the gamma snapshot"
```

### Task 2.4: The page's three scales

**Files:**
- Modify: `webgui/pages/options/gamma_heat.py` (add `SCALES`, `robust_max`, `share_of_column`, `scale_max`, `scale_caption`)
- Modify: `webgui/pages/options/gamma.py` — `_robust_zmax` becomes an alias; `heatmap_figure`, `bar_figure`, `HeatControls`
- Modify: `webgui/app_settings.py`, `webgui/tests/test_live_screens.py`
- Test: `webgui/tests/test_gamma_heat.py`, `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

`webgui/tests/test_gamma_heat.py`:

```python
Z = [[1.0, -2.0, None], [3.0, 6.0, 0.0], [-4.0, 2.0, 5.0]]
LOCKED = {"minutes": 60, "net": 40.0, "call": 70.0, "put": 30.0, "size": 100.0}


def test_share_of_column_is_each_cell_over_its_columns_total_size():
    out = gh.share_of_column(Z)
    assert out[0] == [12.5, -20.0, None]
    assert out[2] == [-50.0, 20.0, 100.0]
    assert Z[0] == [1.0, -2.0, None]            # the input is untouched


def test_share_of_an_empty_column_is_a_gap():
    assert gh.share_of_column([[0.0], [None]]) == [[None], [None]]


def test_scale_max_locked_uses_the_published_lock():
    assert gh.scale_max(Z, "locked", LOCKED, "size") == 100.0
    assert gh.scale_max(Z, "locked", LOCKED, "net") == 40.0


@pytest.mark.parametrize("lock", [None, {}, {"net": None}, {"net": 0}, {"net": "x"}])
def test_scale_max_falls_back_to_adaptive_without_a_lock(lock):
    assert gh.scale_max(Z, "locked", lock, "net") == gh.scale_max(Z, "adaptive", None, "net")


def test_scale_caption_says_what_the_scale_is_tied_to():
    assert gh.scale_caption("adaptive", None, None) == "adapts to what is visible"
    assert gh.scale_caption("share", None, None) == "share of each column"
    assert gh.scale_caption("locked", {"minutes": 60, "net": 4.0}, "09:30") == "held since 09:30"
    assert gh.scale_caption("locked", None, "09:30") == "settling until 09:30"
```

`webgui/tests/test_options_gamma.py`:

```python
def test_robust_zmax_is_still_reachable_under_its_old_name():
    assert gamma._robust_zmax is gamma._heat.robust_max


def test_locked_heatmap_uses_the_lock_and_adaptive_does_not():
    lock = {"minutes": 1, "net": 123.0, "call": 9.0, "put": 9.0, "size": 9.0}
    locked = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0],
                                  scale="locked", lock=lock)
    adaptive = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0],
                                    scale="adaptive", lock=lock)
    assert locked["colorAxis"]["max"] == 123.0
    assert adaptive["colorAxis"]["max"] != 123.0


def test_the_default_scale_is_todays():
    """A caller that passes nothing gets the adaptive scale it always got."""
    plain = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0])
    adaptive = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0],
                                    scale="adaptive")
    assert plain["colorAxis"] == adaptive["colorAxis"]
```

**Step 2: Run to see them fail.** Expected: FAIL, `no attribute 'share_of_column'`.

**Step 3: Implement.**

`gamma_heat.py`:

```python
SCALES = {"locked": "Locked", "adaptive": "Adaptive", "share": "Share of column"}


def robust_max(z, q=0.95):
    """Symmetric colour clamp for a z-grid: the ``q`` percentile of the absolute
    cell, so a few extreme strikes do not wash the mid-range to transparent.
    None when there is no non-zero cell."""
    vals = sorted(abs(v) for row in (z or []) for v in row if v)
    if not vals:
        return None
    idx = min(len(vals) - 1, int(q * (len(vals) - 1)))
    return vals[idx] or vals[-1]


def share_of_column(z):
    """Each cell as a percentage of its column's total absolute size: the shape
    of the positioning at each time, with the level taken out. A column that
    holds nothing is a gap."""
    if not z:
        return []
    totals = [sum(abs(row[c]) for row in z if row[c]) for c in range(len(z[0]))]
    return [[(100.0 * v / totals[c]) if v is not None and totals[c] else None
             for c, v in enumerate(row)] for row in z]


def scale_max(z, scale, lock, mode):
    """The colour axis's maximum. ``locked`` uses the service's lock for this
    value and falls back to the adaptive figure until one exists."""
    held = _fmt.num((lock or {}).get(mode)) if scale == "locked" else None
    return held if held else robust_max(z)


def scale_caption(scale, lock, lock_time):
    """What the legend says the scale is tied to."""
    if scale == "share":
        return "share of each column"
    if scale == "locked":
        return (f"held since {lock_time}" if (lock or {}).get("net")
                else f"settling until {lock_time}")
    return "adapts to what is visible"
```

`gamma.py`: replace the body of `_robust_zmax` with the alias `_robust_zmax = _heat.robust_max` (keep its position; `term_heatmap` and the tests use the name).

`heatmap_figure(..., scale="adaptive", lock=None)`:

- after `vstrikes, vz = uniform_strike_grid(...)`, add `if scale == "share": vz = _heat.share_of_column(vz)`;
- replace `zmax = _robust_zmax(vz) or None` with `zmax = _heat.scale_max(vz, scale, lock, mode) or None`;
- in the projection block, replace the re-clamp with `zmax = _heat.scale_max(vz + proj_rows_for_zmax, scale, lock, mode) or None`;
- the legend sink becomes `legend.update(zmax=zmax, mode=mode, scale=scale)`;
- in share mode the tooltip suffix is `"{point.value:.1f}%"`.

`bar_figure(..., scale="adaptive", lock=None)`: when `scale == "locked"` and the lock holds a number for `mode`, set the exposure axis to it: `fig["yAxis"]["min"], fig["yAxis"]["max"] = -held, held`. A bar's length and a cell's colour then agree.

`HeatControls`: add a `scale` select the same way as `value` (`dict(_heat.SCALES)`, setting `gamma_heat_scale`, default `"locked"`, label "Scale", tooltip: "Locked keeps one colour meaning one amount all session. Adaptive stretches the colours over whatever is on screen. Share of column shows each time's shape."), register it in `on_change` and `sync`, and change:

```python
    def args(self, entry, rows):
        """The keywords both figure builders take for the current choices."""
        return {"mode": self.value.value if self._sided else "net",
                "scale": self.scale.value, "lock": (entry or {}).get("scale_lock")}

    def show_legend(self, view, legend, entry, rows):
        lock = (entry or {}).get("scale_lock")
        minutes = (lock or {}).get("minutes") or _heat_cfg.lock()["minutes"]
        at = _fmt_ts(rows[0][0] + 60 * minutes) if rows and _is_level(rows[0][0]) else ""
        unit = "%" if legend.get("scale") == "share" else _heat.UNITS.get(view, "")
        self.legend.content = _heat.legend_svg(
            legend.get("zmax"), _heat.scale_caption(legend.get("scale"), lock, at),
            HEAT_STOPS, unit=unit)
```

Update the three call sites in `_render_view` to the new signatures (`heat.args(entry, rows)` computed once into a local, then `**_hk` on both builders, and `heat_keeps_projection(_hk["mode"])`). In share mode `legend_svg` should print percentages: extend it so `unit == "%"` formats the ends as `f"-{top:.0f}%"` / `f"+{top:.0f}%"` and omits the unit from the tail, with a test.

`app_settings.py`: `"gamma_heat_scale": "locked",`. `test_live_screens.py`: add it to `PUBLIC_SAFE_DEFAULTS`.

Update `test_render_builds_the_heat_controls_and_feeds_both_panels` for the new `args` signature (a location, not a behaviour). Re-measure and lower the render ceiling.

**Step 4: Run**

Run: `(cd webgui && "$PY" -m pytest . -q -rf 2>&1 | tail -8)`
Expected: the failing set equals Task 0.1's.

**Step 5: Commit**

```bash
git add webgui
git commit -m "feat(gamma): a locked colour scale, and each column's share, for the heatmap"
```

### Task 2.5: See it, document it, stop

**Step 1:** Add `"scale_lock": {"minutes": 30, "net": 6e8, "call": 9e8, "put": 9e8, "size": 1.6e9}` to each view in the Task 1.8 seed and run the harness. Check: the legend reads "held since 09:00" and the same two figures whichever rows are on screen; Adaptive changes the figures; Share prints percentages; with the lock removed from the seed the caption reads "settling until …" and the chart still draws.

**Step 2:** Documents, as Task 1.9: the page guide, the three manuals (the Technical Reference gets the lock's formula and its three config keys), the route notes, the changelog.

**Step 3:** Run the webgui, options service and shared suites; compare the failing sets.

**Step 4:** Add `tools/show_gamma_scale_lock.py`, the read-only check for after the promote. The user runs remote commands from PowerShell, which drops inner double quotes over ssh, so the check is a script with no arguments and never a `python -c` one-liner:

```python
"""Print each Dealer Positioning view's scale lock from the live cache. Read-only."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared.bus import Bus  # noqa: E402


def main():
    # cache_get returns an envelope; the snapshot is its payload.
    snap = Bus().cache_get("cache:options:gamma")
    views = ((snap.payload if snap else None) or {}).get("views") or {}
    for name, entry in views.items():
        print(name, (entry or {}).get("scale_lock"))
    return 0 if views else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Check how another script under `tools/` builds its `Bus()` and names its key before trusting the two lines above; copy that script's form if it differs.

**Step 5:** Commit, then stop and ask about shipping. After a promote:

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/show_gamma_scale_lock.py'
```

Expected after 09:30 CT on a session day: four lines, each a dict with five keys. Before 09:30 each reads `None`, and the page's legend reads "settling until 09:30".

---

## Phase 2 as built (2026-10-09)

Done and committed on this branch. What differs from the tasks above, and what
**Phase 3 must start from**:

1. **`HeatControls.read(grid, rows, strikes, entry)`** returns four keywords,
   `mode`, `balanced`, `scale` and `lock`, and `_render_view` still passes one
   `**_hk` to both builders. A Frame control adds a fifth the same way.
   `show_legend(view, legend)` kept its two arguments: the lock and its clock time
   are held on the object from the last `read`.
2. **The bars take the lock as a soft extent** (`softMin` / `softMax`, both always
   emitted), not a hard maximum. A hard one clips the largest bars flat. In Size
   the extent is the larger of the call and put locks (`gamma_heat.bar_max`).
3. **`heat_keeps_projection(mode, scale)`** drops the band on a share scale too.
   Phase 3 adds `frame` to it.
4. **No lock after its time has passed reads "adapts to what is visible"**, not
   "settling". Found on the harness.
5. **The lock is recomputed on every snapshot build**, with no memo: it is one
   pass over the first hour's rows. So a `[lock]` config change moves the current
   session's scale within a minute. Documented; not a bug.
6. **`compute.py` is 10,522 lines against a ceiling of 10,523**, two shorter than
   it started. Task 3.2 replaces nine lines of `_crop_gamma_views` with one call,
   which buys Phase 3 its room. `render` is still 1,469 lines and 59 nested
   functions, with no slack.
7. **`tools/show_gamma_scale_lock.py`** exists, with tests. Redis needs its
   password, so the command after a promote loads `.env` first:

   ```bash
   ssh vps2 'cd /home/administrator/dev && set -a && . ./.env && set +a && .venv/bin/python tools/show_gamma_scale_lock.py'
   ```

8. The page now holds two selects captioned "Scale" (this one, and the hidden one
   that keeps Net Prem's state). Find a control by that caption with care.

Not done: nothing has been seen on prod data. The harness checks for this phase
were read from the page's DOM, because screenshots were not available.

---

## Phase 3 — Frame: from spot

### Task 3.1: One display window for both tiers

`N_SIDE = 20` (`gamma.py:403`) and `GAMMA_N_SIDE = 20` (`compute.py:4043`) are the same number in two tiers. This phase touches both, so it moves them to config.

**Files:** `config/gamma_heat.toml` (`[window] n_side = 20`), `shared/gamma_heat_config.py` (`n_side()`, minimum 4, maximum 60), `webgui/config_schema.py` (restart `(OPTIONS, WEBGUI)`: both resolve it at import), `gamma.py` (`N_SIDE = _heat_cfg.n_side()`), `compute.py` (`GAMMA_N_SIDE = _heat_cfg.n_side()`).

**Test** (the repo's rule for a constant resolved at import: monkeypatch the accessor and reload the consumer; asserting equality proves nothing):

```python
def test_the_page_window_is_read_from_config(monkeypatch):
    import importlib
    from shared import gamma_heat_config as cfg
    monkeypatch.setattr(cfg, "n_side", lambda: 7)
    try:
        assert importlib.reload(gamma).N_SIDE == 7
    finally:
        monkeypatch.undo()
        importlib.reload(gamma)
```

and the same shape for `compute.GAMMA_N_SIDE` in the service suite. Commit: `refactor(gamma): the display window is one config key for both tiers`.

### Task 3.2: Measure, then widen the crop

**Step 1: Write the crop rule, test first** (`gamma_window.py`)

```python
def test_the_crop_keeps_a_full_window_around_the_sessions_low_and_high():
    strikes = [float(k) for k in range(0, 201)]
    keep = gw.crop_keep(strikes, 150.0, [100.0, 150.0], 5)
    assert {95.0, 100.0, 105.0} <= keep          # a window around the low
    assert {145.0, 150.0, 155.0} <= keep         # and around now
    assert set(range(100, 151)) <= {int(k) for k in keep}     # and the path between
    assert 94.0 not in keep and 156.0 not in keep


def test_the_crop_without_a_path_is_the_window_around_spot():
    strikes = [float(k) for k in range(0, 201)]
    assert gw.crop_keep(strikes, 150.0, [], 5) == gw.window_around(strikes, 150.0, 5)


def test_the_crop_without_any_spot_keeps_nothing_back():
    assert gw.crop_keep([1.0, 2.0], None, [], 5) is None
```

```python
def crop_keep(strikes, spot, path, n_side):
    """The strikes a view's history keeps: the display window around the current
    spot, every strike the session's path crossed, and a full window around the
    session's low and high. The last part is what lets the page centre each
    column on its own spot: without it a column at the day's low has almost
    nothing below it. None when there is no spot at all (no crop)."""
    path = [p for p in path or () if finite(p) is not None]
    if finite(spot) is None and not path:
        return None
    keep = window_around(strikes, spot if finite(spot) is not None else path[0], n_side)
    if path:
        lo, hi = min(path), max(path)
        keep |= {k for k in strikes if isinstance(k, (int, float)) and lo <= k <= hi}
        keep |= window_around(strikes, lo, n_side) | window_around(strikes, hi, n_side)
    return keep
```

**Step 2: Measure before changing the service.** Add `tools/measure_gamma_crop.py`: read-only over `gex_history.db`, one symbol and one date, it applies the old rule (window around the last spot plus the path) and `crop_keep` to that session's `gex` rows and prints strikes per row and JSON bytes for each. Model the file, its argument parsing and its test on `tools/measure_chain_carry.py` and `tools/tests/test_measure_chain_carry.py` (a temporary database, never the live store). It accepts a symbol without `$` and tries `$` + the name when the bare one has no rows, so the command needs no quoting. It rides the next promote. Then, on a session with a wide range:

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/measure_gamma_crop.py SPX 2026-10-07'
```

**Decision gate:** if the widest session grows the `gex` history by more than half, stop and tell the user before Step 3. The design estimates under a third.

**Step 3: Swap the rule in.** In `compute._crop_gamma_views`, the nine lines from `anchor = …` through the `keep |= …` path widening become `keep = _gw.crop_keep(all_strikes, spot, path_spots, n_side)`, and the early `continue` for "no usable spot" becomes `if keep is None: continue`. Lower `COMPUTE_MAX_LINES`. The existing crop tests must pass unchanged, except one that asserts the exact kept set on a trending path: that is a behaviour this task changes on purpose, so update its expected set and say so in the commit message.

Commit: `feat(options_svc): keep a full window around the session's low and high in gamma history`.

### Task 3.3: Resample a column onto distance from spot

**Step 1: Tests** (`webgui/tests/test_gamma_heat.py`)

```python
def test_spot_frame_centres_each_column_on_its_own_spot():
    strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
    z = [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [4.0, 40.0], [5.0, 50.0]]
    offsets, out = gh.to_spot_frame(strikes, z, [100.0, 95.0], step=5.0, half=1)
    assert offsets == [-5.0, 0.0, 5.0]
    assert [row[0] for row in out] == [2.0, 3.0, 4.0]      # around 100
    assert [row[1] for row in out] == [10.0, 20.0, 30.0]   # around 95


def test_spot_frame_interpolates_between_strikes():
    _, out = gh.to_spot_frame([100.0, 105.0], [[10.0], [20.0]], [102.5], step=5.0, half=0)
    assert out == [[15.0]]


def test_spot_frame_never_extrapolates():
    """An offset with no strikes on one side of it is a gap."""
    _, out = gh.to_spot_frame([100.0, 105.0], [[10.0], [20.0]], [100.0], step=5.0, half=1)
    assert [row[0] for row in out] == [None, 10.0, 20.0]


def test_spot_frame_does_not_bridge_a_hole_in_the_ladder():
    strikes = [100.0, 105.0, 130.0]
    _, out = gh.to_spot_frame(strikes, [[1.0], [2.0], [3.0]], [115.0], step=5.0, half=0)
    assert out == [[None]]


def test_spot_frame_of_a_column_with_no_spot_is_a_gap():
    _, out = gh.to_spot_frame([100.0, 105.0], [[1.0, 1.0], [2.0, 2.0]],
                              [None, 100.0], step=5.0, half=0)
    assert out == [[None, 1.0]]
```

**Step 2: Implement**

```python
def to_spot_frame(strikes, z, spots, *, step, half):
    """Resample ``z[strike][time]`` onto offsets from each column's OWN spot.

    Returns ``(offsets, z2)`` with ``offsets`` the uniform ladder
    ``-half*step … +half*step`` and ``z2[offset][time]``. A value is the linear
    interpolation between the two strikes around ``spot + offset``. It is a gap
    when that point is outside the column's strikes, or when those two strikes
    are further apart than a hole in the ladder (2.5 steps): nothing is
    extrapolated and nothing is bridged."""
    offsets = [i * step for i in range(-half, half + 1)]
    out = [[None] * len(spots) for _ in offsets]
    for c, raw in enumerate(spots):
        spot = _fmt.num(raw)
        if spot is None:
            continue
        pts = [(k, z[r][c]) for r, k in enumerate(strikes) if z[r][c] is not None]
        if not pts:
            continue
        j = 0
        for oi, d in enumerate(offsets):
            target = spot + d
            if target < pts[0][0] or target > pts[-1][0]:
                continue
            while j + 2 < len(pts) and pts[j + 1][0] < target:
                j += 1
            (k0, v0), (k1, v1) = pts[j], pts[min(j + 1, len(pts) - 1)]
            if k1 == k0:
                out[oi][c] = v0 if target == k0 else None
            elif k1 - k0 <= 2.5 * step:
                out[oi][c] = v0 + (v1 - v0) * (target - k0) / (k1 - k0)
    return offsets, out
```

Commit: `feat(gamma): resample the heatmap onto distance from spot`.

### Task 3.4: The heatmap in the spot frame

`heatmap_figure(..., frame="strike")`. With `frame == "spot"`:

- skip the `yrange` crop (`vis` is every strike), run `uniform_strike_grid` as now, then `offsets, vz = _heat.to_spot_frame(vstrikes, vz, spots, step=_strike_step(vstrikes), half=N_SIDE)` and use `offsets` in place of `vstrikes` from there on;
- the y-axis range is `[offsets[0] - step/2, offsets[-1] + step/2]`; the caller passes the same pair to `bar_figure`;
- the Spot series is `[[i, 0.0] for i, s in enumerate(spots) if s is not None]`; the candle and wick series are empty whatever `spot_style` says;
- the three level tracks are `level − spot` per column and are drawn regardless of `show_tracks`;
- `wall_plot_lines` receives `flip`, `walls`, `projected_flip` and `balanced` each minus the current `spot`, and a `signed=True` flag so labels read `Call wall +38.00` (add the flag with a test; `_fmt.price` with a leading sign);
- the tooltip reads `"{point.y:+.2f} from spot · …"`.

**Tests** (write first; each is one assertion group):

```python
def test_spot_frame_keeps_nine_series_and_the_palette(): ...
def test_spot_frame_draws_spot_as_a_flat_zero_line(): ...
def test_spot_frame_draws_the_level_tracks_even_when_the_switch_is_off(): ...
def test_spot_frame_levels_are_offsets_from_the_current_spot(): ...
def test_spot_frame_draws_no_candles(): ...
def test_strike_frame_is_unchanged_by_the_new_argument():
    a = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0])
    b = gamma.heatmap_figure(_sided_rows(), "GEX", yrange=[95.0, 105.0], frame="strike")
    assert a == b
```

Extend `heat_keeps_projection(mode, frame="strike")` to return False for the spot frame, with a test, and confirm `heatmap_categories(rows, projection)` is called by `_render_view` with the same (now `None`) projection: the existing test that pins the two guards together must still pass untouched.

Commit: `feat(gamma): the heatmap can measure strikes from spot`.

### Task 3.5: The bars in the spot frame

`bar_figure(..., origin=None)`: when `origin` is a number, every point's `x`, every plot line's `value` and the axis range are shifted by `-origin`, the axis title is "From spot" and its label format is `"{value:+.2f}"`. Tests: the points' `x` are `strike − origin`; three series; `origin=None` equals today's figure exactly.

Commit: `feat(gamma): the by-strike bars follow the heatmap into the spot frame`.

### Task 3.6: The Frame control

`_heat.FRAMES = {"strike": "Strike", "spot": "From spot"}`; `HeatControls.frame` (setting `gamma_heat_frame`, default `"strike"`; tooltip: "From spot puts price on a flat line through the middle, so the walls move. A wall sliding toward the centre is one price is approaching."); `args()` adds `frame`; a `bar_args(spot)` returns `{"origin": spot}` in the spot frame and `{}` otherwise; the caller builds the shared range from `gamma_heat.spot_frame_range(step, half)` in that frame.

`_sync_spot_controls` hides `spot_style_sel` and `spot_int_sel` in the spot frame (one more condition on the existing visibility lines; the pinned substrings `view_toggle.value != "Net Prem"` and `spot_style_sel.value != "line"` must both remain).

`app_settings.py`, `PUBLIC_SAFE_DEFAULTS`, the render ceiling: as before.

### Task 3.7: See it, document it, stop

Harness, with a seed whose spot path trends 60 points: price is a flat white line at zero; the call wall track slides toward it; both panels show the same offsets and the crosshair marks the same row in each; the Spot and Bar pickers are gone; switching back to Strike restores today's chart exactly; the hedge panel's bars sit under the right columns in both frames. Documents as Task 1.9. Suites, commit, stop and ask.

---

## Phase 3 as built (2026-10-09)

Done and committed on this branch. The tasks above were written before the
measurement; what was built differs in substance, and **Phase 4 must start from
this**:

1. **The measurement ran on a local prod backup, not after a promote.**
   `E:\TradingBackups\prod_2026-09-25_1737\options-scanner\gex_history.db` holds six
   sessions. `tools/measure_gamma_crop.py SYMBOL... --date D --db PATH --edge-side N`
   opens a copy immutably. The widest stored `$SPX` day has a 1.1% range; no real
   trend day has been measured.
2. **The frame is 10 strikes tall, not 20, and that height is a config key.** The
   full window cost 40% more `$SPX` history on a 1.1% day. `[window] spot_side`
   (10) is both the frame's `half` and the service's edge window. At 10: +11.9%
   `$SPX`, +9.1% QQQ, +8.7% `$NDX`, nothing on SPY, NVDA or a quiet day.
3. **`gamma_window.crop_keep(strikes, spot, path, n_side, edge_side=0)`** holds the
   whole crop rule and keeps `edge_side + 1` strikes each side of the low and the
   high. The extra one brackets the frame's outermost row. `edge_side=0` is the rule
   exactly as it was.
4. **Both window sizes are read at CALL time**, in both tiers, with no restart:
   `gamma._window_side()` and `_heat_cfg.n_side()` / `spot_side()` in
   `_crop_gamma_views`. `N_SIDE` and `GAMMA_N_SIDE` no longer exist. The tests prove
   the value is read by patching the accessor; nothing is reloaded.
5. **`_hk` now carries six keywords** (`mode`, `balanced`, `scale`, `lock`, `frame`,
   `half`), all accepted by both builders, and `heat_keeps_projection(**_hk)` takes
   the dict whole. `heat_yrange(strikes, spot, spot_path, frame, half)` gives both
   panels one range. A Show control adds a seventh keyword the same way.
6. **`HeatControls.on_change(repaint, resync)`** and **`sync(view, overlays)`**: a
   frame change resyncs the page's controls first, and `sync` hides Level movement,
   Spot and Bar in the spot frame.
7. **No `+` in a Highcharts format string.** It is ignored.
8. One existing assertion changed where it looks:
   `test_render_view_updates_in_place_not_clear` now finds `bar_yrange` through
   `heat_yrange`. What it requires did not change.
9. `render` is still 1,469 lines and 59 nested functions. `compute.py` is 10,514
   lines against a ceiling of 10,515.

Not done: nothing has been seen on prod data. The harness checks were read from
the page's DOM; screenshots were not available. The public render was not
re-checked in a browser this phase (its unit renders pass).

⚠ **Use the editor for test files and patch scripts, never a shell heredoc.** A
heredoc turned a `\n` inside a string literal into a real line break in one of this
phase's tests; it was caught because the file stopped compiling.

---

## Phase 4 — Show: change

### Task 4.1: The difference

Config: `[show] change_window_min = 30` (page-read, no restart), accessor `change_window_min()` (minimum 5), catalogue entry.

**Tests**

```python
TS = [1000, 1060, 1120, 2800, 2860]


def test_change_since_open_subtracts_the_first_column():
    assert gh.delta([[5.0, 7.0, 4.0, 9.0, 9.5]], TS) == [[0.0, 2.0, -1.0, 4.0, 4.5]]


def test_change_over_a_window_uses_the_latest_column_at_least_that_old():
    out = gh.delta([[5.0, 7.0, 4.0, 9.0, 9.5]], TS, window_min=28)
    assert out == [[None, None, None, 5.0, 5.5]]      # 2800-1680=1120 -> col 2; 2860-1680=1180 -> col 2


def test_a_strike_absent_at_the_basis_is_a_gap_not_a_zero():
    assert gh.delta([[None, 7.0], [3.0, None]], [1000, 1060]) == [[None, None], [0.0, None]]


def test_change_does_not_write_to_its_input():
    z = [[1.0, 2.0]]
    gh.delta(z, [0, 60])
    assert z == [[1.0, 2.0]]
```

**Implementation**

```python
SHOWS = {"level": "Level", "open": "Change since open", "window": "Change over {n} min"}


def delta(z, ts, window_min=None):
    """Each cell minus the same strike's cell at a basis column.

    ``window_min`` None: the basis is the session's first column. Otherwise it
    is the latest column at least that many minutes older; a column with none
    that old is a gap. A strike with no reading at the basis is a gap, never a
    zero: zero would claim the exposure was flat."""
    n = len(ts)
    if window_min is None:
        basis = [0] * n
    else:
        basis, j = [], 0
        for i in range(n):
            want = ts[i] - window_min * 60
            while j + 1 < i and ts[j + 1] <= want:
                j += 1
            basis.append(j if ts[j] <= want and j < i else None)
    return [[(row[i] - row[b]) if b is not None and row[i] is not None
             and row[b] is not None else None
             for i, b in enumerate(basis)] for row in z]
```

Run the second test's arithmetic by hand before trusting it; if the implementation and the test disagree, the test's stated intent (the latest column at least `window_min` old) is the authority.

### Task 4.2: Wire it

`heatmap_figure(..., show="level", change_window_min=30)`: after the value matrix is built and before the frame transform, `if show != "level": z = _heat.delta(z, [r[0] for r in rows], None if show == "open" else change_window_min)`. The scale for a change is the same value's lock (the design's rule: one legend for both), so `scale_max` is called exactly as before. The title gains " · change since open" or " · change over 30 min". The projection band is dropped (`heat_keeps_projection` gains `show`).

`HeatControls.show` (setting `gamma_heat_show`, default `"level"`), with this tooltip, which is the honest part of the feature: "Open interest updates once a day. A change here is the same positions repricing as price, time and volatility move. It is not new trades."

Tests: nine series; `show="level"` equals today's figure; the first column of "since open" is all zeros or gaps; the tooltip text is present in `HeatControls` (read from source). Harness: on the Phase 1 seed, Change since open goes dark except near the money, where the seed's exposure grows. Documents (the page guide repeats the tooltip's sentence). Suites, commit, stop and ask.

---

## Phase 4 as built (2026-10-09)

Done and committed on this branch. What differs from the tasks above, and what
**Phase 5 must start from**:

1. **The bars show the change too.** The tasks covered the heatmap only.
   `bar_figure(show=, change_window_min=, basis=)` draws each strike's value now
   less its value in `basis`, the row the heatmap's last column is measured from
   (`gamma_heat.basis_grid`, through `HeatControls.basis(rows)`). No basis means no
   bars.
2. **`_hk` carries eight keywords**: `mode`, `balanced`, `scale`, `lock`, `frame`,
   `half`, `show`, `change_window_min`. `basis` is the bars' alone and is passed
   beside `**_hk` on the existing bar call line, so `render` did not grow.
3. **`delta(z, ts, window_min=None)`** takes the columns' epoch seconds. Rows whose
   first field is clock text have no age: a window view of them is all gaps, and
   "since open" still works.
4. **The tooltip is `gamma_heat.SHOW_HELP`**, a constant, so its test checks the
   sentence and not the source's line breaks. It does not mention Premium, which
   does not exist yet. **Phase 5 should add that pointer** to the tooltip, the page
   guide and the manuals once Premium ships.
5. **For Phase 5's own Change:** stored premium is day-cumulative, so "Change over
   30 min" of Premium is the premium that moved in that window, subject to the
   re-marking question Task 5.1 measures. `delta` and `basis_grid` need nothing
   new for it.
6. `render` is still 1,469 lines and 59 nested functions.

Not done: nothing has been seen on prod data, and the harness checks were read
from the page's DOM. Not checked in a browser: Change combined with the spot frame
and with Share of column (their unit tests pass).

---

## Phase 5 stopped at its gate (2026-10-09)

Task 5.1 ran. **Nothing after it was built, and nothing after it should be built
from the tasks below.** They are kept as the record of what the gate ruled out.

- **The tool exists:** `tools/measure_prem_remark.py`, with tests. It ran on the
  local prod backup (`--db`, immutably), not after a promote.
- **The gate failed, by a wide margin.** Minute to minute, the stored premium's falls
  are 61% to 89% of its rises on every symbol measured. Over 30 minutes, four of
  seven sessions are over the tenth the gate allowed, and about one comparison in six
  is negative. The numbers are in the design, section 7.
- **The fallback failed too.** "Level only" assumed the level was sound. It is the
  day's volume valued at the current mark: the day's total fell in 33% to 41% of
  minutes, and `$SPX` on 09-24 closed 30% under its own peak. As a heatmap over
  time it would show premium fading from strikes where nothing left.
- **So Premium is not in this build.** Tasks 5.2 to 5.4 publish and draw that same
  quantity. An honest view needs a per-minute increment
  (`Δvolume × mark × 100` per strike and side) stored by the collector as its own
  view: a separate design, with its own cost measurement on the one-minute branch.
- **Not decided here, and the user's to decide:** whether to design that collector
  change, and what to do about the Flow ribbon and the Net Prem lines, which plot
  the same quantity and call it cumulative.

The Show picker's tooltip and the manuals still do not point at Premium "for new
activity", because there is no Premium view. The Reference Guide points at Flow.

---

## Phase 5 — Value: premium (private page)

The public page does not get Premium in this version: a fifth history key per leased symbol is a write cost that has not been measured. `HeatControls` leaves it out of the picker when the page is public.

### Task 5.1: Measure the re-marking first

Stored premium is `Σ mark × totalVolume × 100` since the open. When a mark falls, the value of volume already traded falls with it, so a difference of two readings holds re-marking as well as new trades.

Add `tools/measure_prem_remark.py` (read-only; same model and test pattern as Task 3.2's tool). For one symbol and date it loads the `prem` rows and, per strike and side, sums the minute-to-minute rises and the falls of the cumulative figure, and prints: cells that fell as a share of all cells, and dollars fallen as a share of dollars risen. Run it on prod for an index, an ETF and a single name:

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/measure_prem_remark.py SPX 2026-10-07'
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/measure_prem_remark.py SPY 2026-10-07'
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/measure_prem_remark.py NVDA 2026-10-07'
```

**Decision gate. Show the user the three results and stop.** Proposed rule: if dollars fallen are under a tenth of dollars risen on all three, Premium ships with Level and Change. Otherwise it ships with Level only, the Show picker is disabled in Premium with a one-line reason, and a per-minute increment (`Δvolume × mark`) is raised as a separate collector change. Write the numbers and the decision into the design doc's section 7.

### Task 5.2: Publish the rows

- `compute.gamma_snapshot`: the `prem` rows are already loaded for the ladder. Keep them in a local, crop them with `_gw.crop_rows(rows, _gw.crop_keep(strikes, spot, path, GAMMA_N_SIDE))` (add `crop_rows` to `gamma_window.py`, with tests: it rebuilds each row tuple and never writes to the memo's rows), and return them as `"prem_history"`.
- `handlers._publish_gamma`: `rows_by_view["Prem"] = snap.pop("prem_history", None) or []`, before the target loop. The private target's `wanted` is `None`, so it writes `cache:options:gamma_hist_prem`; the public targets list their views explicitly, so they do not.
- Tests, in `services/options_svc/tests/test_gamma_published.py`'s style: the key is written before the main key; it is written empty when the snapshot carries no premium rows; no `gamma_pub_hist_*_prem` key is ever written; the main payload does not carry `prem_history`.
- Measure the publish before and after on a close-of-session shape (the method `tools/measure_gamma_public.py` uses) and put the two figures in the commit message.

### Task 5.3: Draw it

- `_heat.VALUES` gains `"prem": "Premium"`; `cell_value(cell, "prem")` is the cell's `net` (a `prem` cell's net is call dollars minus put dollars). `UNITS` is looked up by value as well as view so the legend prints dollars.
- `gamma.history_key("Prem")` already yields `options:gamma_hist_prem`. In `_render_view`, when the value is Premium, the rows come from `state["hist"]["Prem"]` and the bars from the last row's grid; `_load_history("Prem")` is awaited by the Value handler before the repaint, the way `_on_view_change` awaits the view's history.
- Premium has no lock: `scale_max` is asked for `"prem"`, finds none, and is adaptive; the Scale picker shows Locked as unavailable (`set_enabled` on the option is not available in Quasar's select, so switch the picker to Adaptive and say why in its tooltip while Premium is selected).
- Tests: nine series; the title reads "… · Premium"; a missing history draws the empty message, not a zero field.

### Task 5.4: See it, document it, stop

Harness with a `options:gamma_hist_prem` seed. Documents: the page guide and the Technical Reference both say the figure is unsigned, mid-based and day-cumulative, and is not a buy/sell split. Suites, commit, stop and ask.

---

## When every phase is in

1. `(cd webgui && "$PY" -m pytest . -q -rf)`, `"$PY" -m pytest services/options_svc -q -rf`, `"$PY" -m pytest shared/tests tests tools/tests -q -rf`: the failing sets equal Task 0.1's.
2. `test_render_size.py` and `test_compute_module_shape.py` pass, with both ceilings at or below where they started.
3. The design doc matches what was built. Where a phase changed a decision, edit the sentence in place.
4. Use superpowers:finishing-a-development-branch.
