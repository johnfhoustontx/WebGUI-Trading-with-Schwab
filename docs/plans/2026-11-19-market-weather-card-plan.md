# Market Weather Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A compact "market weather" card at the top right of neuralstrike.co's homepage, drawn from the Market read and refreshed every slot.

**Architecture:** `market_svc` turns each published Market read into `deploy/site/weather.json` (new module `services/market_svc/site_weather.py`, called from `scheduler.refresh_read`). A new same-origin script, `deploy/site/assets/weather.js`, reads that file, decides with the shipped market clock whether the sky is live, foggy, after-hours or absent, and draws the card. This is the same pattern as `ideas.json` + `ideas.js`.

**Tech Stack:** Python 3.11 (market_svc, pytest, fakeredis bus), plain ES5 browser JavaScript (no build, no dependencies), static HTML/CSS served by Caddy.

**Spec:** [docs/plans/2026-11-19-market-weather-card-design.md](2026-11-19-market-weather-card-design.md). Read it first. Mockup: the "Market Weather Card" Claude Design canvas linked there.

## Global Constraints

- The site makes **zero off-origin requests**, has **no link to `app.`**, carries **no form or input** (`deploy/tests/test_site.py`). The card's one outbound link is `https://live.neuralstrike.co/desk`.
- **Code writes every word**; no Claude call; no new Schwab, proxy or bus *write*. The only bus read added is `cache:sentiment:regime`.
- No sentence on the card names a future move.
- `site_weather.publish` never raises; it runs **after** `handlers.publish_read`; a failure calls `_degrade.degraded("market.weather")`.
- `weather.json` and its temp files are gitignored: `deploy/site/weather.json`, `deploy/site/.weather.json.*`. It is regenerable and is **not** added to `tools/backup_local.py` `EXTRA_FILES`.
- Configurable by default: thresholds live in `config/market_read.toml` `[weather]`, each with a `webgui/config_schema.py` field.
- Negative numbers use the minus sign `−` (U+2212), positive `+`. Write that character with the editor, never as an escape in a heredoc.
- Tailwind amber `#f2b65a`; headwind blue `#5b8fd9` (bars) / `#8fb8f2` (text); neutral `#75798c`; no-reading bar outlined `#595d6c`.
- Tests: each service suite runs on its own from the repo root. Python: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (written `$PY` below). Compare failing and skipped *sets*, never counts.

## Review Focus

1. **Early-close afternoon** (Black Friday): `market-clock.js` thinks the session runs to 16:00 ET, but the 12:00 CT reading is `final`. Expected: the moon, not fog. Pinned in Task 4's decide check, case `early_close_final_is_moon`.
2. **Market read made non-public while a sky is up.** Expected: the card disappears within a poll (file becomes `{"v":1,"hidden":true}`), not at the next slot. Pinned in Task 3, `test_public_off_hides_between_slots`.
3. **Flow's own public switch off.** Expected: no Umbrellas row, its tally bar drawn as no reading, and the sky computed from that masked tally. Pinned in Task 2, `test_flow_not_public_is_masked_everywhere`.
4. **Service dead since mid-session, page opened that evening.** Expected: card hidden (a non-final file from today, session closed). Pinned in Task 4's decide check, case `dead_since_midday_hides_in_evening`.
5. **First 15 minutes of the session, before the 08:45 slot.** Expected: yesterday's final under the moon, not fog. Pinned in Task 4's decide check, case `pre_first_slot_is_moon`.

---

### Task 1: `[weather]` settings

**Files:**
- Modify: `shared/market_read_config.py` (add to `DEFAULTS`, clean in `load()`)
- Modify: `config/market_read.toml` (new `[weather]` table with comments)
- Modify: `webgui/config_schema.py` (new `Section("Homepage weather card", …)` in `_MARKET_READ`)
- Test: `shared/tests/test_market_read_config.py`

**Interfaces:**
- Produces: `market_read_config.load()["weather"] == {"enabled": bool, "min_readings": int, "storm_headwinds": int, "clear_tailwinds": int, "refetch_sec": int}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_weather_defaults():
    w = market_read_config.load()["weather"]
    assert w == {"enabled": True, "min_readings": 4, "storm_headwinds": 5,
                 "clear_tailwinds": 5, "refetch_sec": 300}

@pytest.mark.parametrize("key,bad", [("min_readings", 0), ("min_readings", 8),
    ("storm_headwinds", "5"), ("clear_tailwinds", 9.5), ("refetch_sec", 30), ("refetch_sec", 99999)])
def test_weather_bad_values_fall_back(monkeypatch, key, bad):
    monkeypatch.setattr(market_read_config, "load_raw", lambda: {"weather": {key: bad}})
    assert market_read_config.load()["weather"][key] == market_read_config.DEFAULTS["weather"][key]

def test_weather_switch_is_on_only_for_literal_true(monkeypatch):
    monkeypatch.setattr(market_read_config, "load_raw", lambda: {"weather": {"enabled": "yes"}})
    assert market_read_config.load()["weather"]["enabled"] is False

def test_a_scalar_weather_does_not_take_the_table_down(monkeypatch):
    monkeypatch.setattr(market_read_config, "load_raw", lambda: {"weather": 5})
    assert market_read_config.load()["weather"]["min_readings"] == 4
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest shared/tests/test_market_read_config.py -q -k weather`
Expected: FAIL, `KeyError: 'weather'`.

- [ ] **Step 3: Implement**

`DEFAULTS["weather"] = {"enabled": True, "min_readings": 4, "storm_headwinds": 5, "clear_tailwinds": 5, "refetch_sec": 300}`. In `load()`: `_table(raw, "weather")`; `enabled` only for literal `True`; the three counts through `_whole(v, default, 1, 7)`; `refetch_sec` through `_whole(v, default, 60, 3600)`. Add the TOML table with one comment per key (copy the meanings from the spec's Configuration table). Add a schema `Section("Homepage weather card", "The card at the top right of neuralstrike.co, drawn from each reading.", …)` with five `Field`s (`weather.enabled` bool; the three counts `kind="int", min=1, max=7`; `weather.refetch_sec` `kind="int", unit="sec", min=60, max=3600, step=30`).

- [ ] **Step 4: Run the config tests and the schema guard**

Run: `$PY -m pytest shared/tests/test_market_read_config.py -q` then `cd webgui && $PY -m pytest tests/test_config_schema.py -q`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/market_read_config.py config/market_read.toml webgui/config_schema.py shared/tests/test_market_read_config.py
git commit -m "feat(weather): [weather] settings for the homepage card"
```

---

### Task 2: Building the card's payload

**Files:**
- Create: `services/market_svc/site_weather.py` (pure functions only in this task)
- Test: `services/market_svc/tests/test_site_weather.py`

**Interfaces:**
- Consumes: a Market read payload dict (`cache:market:read` `.payload`), a regime dict (`cache:sentiment:regime` `.payload`) or `None`, `market_read_config.load()`.
- Produces:
  - `HIDDEN: dict = {"v": 1, "hidden": True}`
  - `sky(tally: list[str], regime: dict | None, wcfg: dict) -> str` — one of `"fog","storm","clear","fair","showers","overcast"`.
  - `build(reading: dict | None, regime: dict | None, cfg: dict) -> dict` — the v1 payload in the spec, or `HIDDEN`.

Rules the tests pin (from the spec; copy exactly):

- `tally` = each row's verdict in `market_read.ROW_KEYS` order; the flow row becomes `"none"` when its `public` is not `True`. `sky()` counts T, H, R (verdicts that are not `"none"`) on that masked list. First match: `fog` if `R < min_readings`; `storm` if `H >= storm_headwinds`, or regime `committed_label == "crisis"` and `H > T`; `clear` if `T >= clear_tailwinds and H == 0`; `fair` if `T > H`; `showers` if `H > T`; else `overcast`.
- `label` by sky: `Severe storm`, `Scattered showers`, `Overcast`, `Mostly sunny`, `Clear skies`, `Fog`.
- `build()` returns `HIDDEN` when `reading` is `None`, `reading["enabled"] is False`, `reading["public"] is not True`, or `cfg["weather"]["enabled"] is not True`.
- Copied from the reading: `date`, `slot`, `ts`, `final`, `next_slot`; `interval_min` from `cfg`; `refetch_sec` from `cfg["weather"]`; `slot_label` = the slot in 12-hour form plus ` CT` (`"15:00"` → `"3:00 PM CT"`, `"08:45"` → `"8:45 AM CT"`); `regime` = regime `label` or `None`.
- `tally_text`: counts in the order tailwinds, headwinds, neutral, no reading; zeros skipped; `1 tailwind` / `2 tailwinds`, `1 headwind` / `2 headwinds`, `N neutral`, `N no reading`; joined with ` · `.
- `temp = {"spx_pct", "ndx_pct", "tone"}`; `tone` is `warm`/`cold`/`plain` from the direction verdict (tailwind/headwind/else). Every reading's `tone` comes from its own row's verdict the same way.
- Headline: `{front} {coverage}; {wind}.` Front: storm `Cold front sweeps`, showers `Light rain falls over`, fair `Sun breaks through over`, clear `A warm front lifts`, overcast `No front either way` (no coverage part). Coverage counts declining when `declining >= advancing`, else advancing: `{n} of {total} markets`, or `all {total} markets` when `n == total`, or `the board` when breadth is `none` or the total is 0. `total = advancing + declining + flat`. Wind uses `cfg["volatility"]["vix_move_pct"]`: `vix_pct >= m` → `the fear gauge gusts to {vix:.1f}`, `vix_pct <= -m` → `the fear gauge sinks to {vix:.1f}`, else `the fear gauge holds near {vix:.1f}`; no `vix_level` → drop `; {wind}`. Fog → headline `""`.
- Readings, in this order, each `{"key","name","sub","value","tone"}`, a row whose verdict is `none` left out:
  - `temperature`: `Temperature`, `S&P 500 · Nasdaq-100`, `{signed spx:.1f}° · {signed ndx:.1f}°`.
  - `coverage`: `Rain coverage` / `Markets falling on our board` when `declining >= advancing`, else `Sun coverage` / `Markets rising on our board`; value `{n} of {total}`.
  - `wind`: `Wind`, `VIX, up {p:.1f}%` / `VIX, down {p:.1f}%` (or `VIX` with no pct), value `{vix:.1f}`.
  - `pressure`: from `structure.facts.symbols[0]`: `Pressure`, `{symbol} {spot:.0f} vs the gamma flip {flip:.0f}`, value `Below` / `Above` / `At the flip`.
  - `umbrellas`: only when flow `public is True`: `Umbrellas : sunglasses`, `Puts to calls in the option flow`, value `{puts} : {calls}`.
  - `shelter`: `Shelter`; sub from tiles `TLT` and `HYG` pct signs: `Bonds up` / `Bonds down`, `high yield up` / `high yield down`, joined `, ` (a tile with no pct skipped; none → `Bonds, the dollar and high yield`); value `Risk-off` if `risk_off > risk_on`, `Risk-on` if the reverse, else `Mixed`.

- [ ] **Step 1: Write the failing tests**

Build a fixture `_reading()` that is the real 2026-11-19 15:00 reading (the facts quoted in the spec's sample; every row with its verdict and facts). Then:

```python
def test_the_storm_day_matches_the_spec_sample():
    out = site_weather.build(_reading(), {"label": "Stressed", "committed_label": "crisis"}, CFG)
    assert out["sky"] == "storm" and out["label"] == "Severe storm"
    assert out["headline"] == "Cold front sweeps 35 of 37 markets; the fear gauge gusts to 28.7."
    assert out["tally"] == ["headwind"] * 4 + ["neutral", "headwind", "none"]
    assert out["tally_text"] == "5 headwinds · 1 neutral · 1 no reading"
    assert out["slot_label"] == "3:00 PM CT" and out["final"] is True
    assert [r["key"] for r in out["readings"]] == ["temperature", "coverage", "wind", "pressure", "umbrellas", "shelter"]
    assert out["readings"][0]["value"] == "−2.0° · −2.2°"
    assert out["readings"][3]["sub"] == "SPY 716 vs the gamma flip 722"
    assert out["readings"][4]["value"] == "161 : 39"
    assert out["readings"][5] == {"key": "shelter", "name": "Shelter", "sub": "Bonds up, high yield down",
                                   "value": "Risk-off", "tone": "cold"}

@pytest.mark.parametrize("tally,regime,want", [
    (["none"] * 4 + ["headwind"] * 3, None, "fog"),
    (["headwind"] * 5 + ["tailwind"] * 2, None, "storm"),
    (["headwind"] * 3 + ["tailwind"] * 2 + ["neutral"] * 2, {"committed_label": "crisis"}, "storm"),
    (["tailwind"] * 3 + ["headwind"] * 2 + ["neutral"] * 2, {"committed_label": "crisis"}, "fair"),
    (["tailwind"] * 5 + ["neutral"] * 2, None, "clear"),
    (["tailwind"] * 5 + ["headwind"] + ["neutral"], None, "fair"),
    (["headwind"] * 4 + ["neutral"] * 3, None, "showers"),
    (["headwind"] * 2 + ["tailwind"] * 2 + ["neutral"] * 3, None, "overcast"),
])
def test_sky_rules(tally, regime, want):
    assert site_weather.sky(tally, regime, CFG["weather"]) == want

def test_flow_not_public_is_masked_everywhere(): ...
    # flow row public=False, verdict headwind, plus four other headwinds:
    # tally[4] == "none"; no "umbrellas" reading; sky == "showers" (4 H), not "storm".

def test_a_none_row_is_left_out_but_keeps_its_bar(): ...
def test_hidden_when_switched_off_or_not_public(): ...   # enabled False, public False, weather off, reading None
def test_wind_clause_dropped_without_a_vix(): ...
def test_coverage_says_all_when_one_side_is_empty(): ...  # "all 37 markets"
def test_warm_sky_counts_advancing(): ...                 # fair: "Sun breaks through over 26 of 37 markets; …", reading name "Sun coverage"
def test_no_word_names_a_future_move(): ...
    # every headline and sub for all six skies: none contains "will", "ahead", "expect", "forecast", "tomorrow"
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest services/market_svc/tests/test_site_weather.py -q`
Expected: FAIL, `ModuleNotFoundError: services.market_svc.site_weather`.

- [ ] **Step 3: Implement the pure functions in `site_weather.py`**

Module docstring states what the file is, who reads it, and links the spec. Private helpers per reading (`_temperature(rows)`, `_coverage(...)`, …) keep `build()` short. Number formatting through one `_signed(x: float, places: int = 1) -> str`. Read facts through `shared.numeric.finite`; a fact that is not finite is absent.

- [ ] **Step 4: Run them to see them pass**

Run: `$PY -m pytest services/market_svc/tests/test_site_weather.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/market_svc/site_weather.py services/market_svc/tests/test_site_weather.py
git commit -m "feat(weather): build the homepage card from the Market read"
```

---

### Task 3: Writing `weather.json` on each slot and switch

**Files:**
- Modify: `services/market_svc/site_weather.py` (add `SITE_ROOT`, `FILE`, `publish`, `regime_of`)
- Modify: `services/market_svc/scheduler.py` (`refresh_read` calls a new `_sync_weather`)
- Modify: `.gitignore` (two lines beside the `ideas.json` ones)
- Test: `services/market_svc/tests/test_site_weather.py`, `services/market_svc/tests/test_market_read_publish.py`

**Interfaces:**
- Consumes: `build`, `HIDDEN` (Task 2); `load()["weather"]` (Task 1).
- Produces:
  - `SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)` (tests redirect it), `FILE = "weather.json"`.
  - `regime_of(bus) -> dict | None` — `cache:sentiment:regime` payload, `None` when absent or not a dict.
  - `publish(payload: dict, *, root: pathlib.Path | None = None) -> bool` — atomic write (temp file `.weather.json.*` in the same folder, `chmod 0o644`, `os.replace`; copy the shape of `site_ideas._write_atomic`), `True` on success, `False` and a degrade on any exception.
  - In `scheduler`: `_sync_weather(bus, state: dict, cfg: dict) -> None` — builds from `state["last"]`, writes only when `(last.date, last.slot, last.ts, last.enabled, last.public, cfg["weather"]["enabled"])` differs from `state["weather_sig"]`; sets `state["weather_sig"]` only after a successful publish. Called on every `refresh_read` pass after `cfg` is loaded and `state["last"]` is current (after a slot publish, a retraction, a public flip, and on a pass with nothing due), so a switch reaches the site within one poll.

- [ ] **Step 1: Write the failing tests**

```python
def test_publish_writes_atomically_and_readably(tmp_path):
    assert site_weather.publish({"v": 1, "sky": "fog"}, root=tmp_path) is True
    assert json.loads((tmp_path / "weather.json").read_text("utf-8"))["sky"] == "fog"
    assert not list(tmp_path.glob(".weather.json.*"))

def test_publish_never_raises(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(site_weather._degrade, "degraded", lambda area: calls.append(area))
    assert site_weather.publish({"v": 1}, root=tmp_path / "missing" / "dir") is False
    assert calls == ["market.weather"]
```

In `test_market_read_publish.py` (reuse its `bus` fixture and `_at`; monkeypatch `site_weather.SITE_ROOT` to `tmp_path`):

```python
def test_a_slot_writes_the_card(bus, tmp_path, monkeypatch): ...
    # refresh_read at 12:46 -> weather.json exists, slot "12:45", not hidden
def test_the_card_is_not_rewritten_between_slots(bus, tmp_path, monkeypatch): ...
    # second pass at 12:50: file mtime unchanged (or spy publish: called once)
def test_public_off_hides_between_slots(bus, tmp_path, monkeypatch): ...
    # publish at 12:46, flip cfg public False, pass at 12:50 -> file == {"v": 1, "hidden": True}
def test_switching_the_read_off_hides_the_card(bus, tmp_path, monkeypatch): ...
def test_weather_switch_off_hides_the_card(bus, tmp_path, monkeypatch): ...
def test_a_site_failure_does_not_cost_the_reading(bus, tmp_path, monkeypatch): ...
    # site_weather.publish patched to raise -> cache:market:read still published, refresh_read returns the slot
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest services/market_svc/tests/test_site_weather.py services/market_svc/tests/test_market_read_publish.py -q`
Expected: the new tests FAIL (`AttributeError: publish`), the existing ones pass.

- [ ] **Step 3: Implement `publish`, `regime_of`, `_sync_weather`, and the call sites; add the `.gitignore` lines**

`_sync_weather` wraps its own body so a bug in it cannot reach the reading's retry path; `publish` already degrades on a write failure.

- [ ] **Step 4: Run the whole market_svc suite and the guards it touches**

Run: `$PY -m pytest services/market_svc -q` then `$PY -m pytest tests/test_no_silent_degrades.py tools/tests/test_backup_local.py -q`
Expected: all PASS; the failing set is empty.

- [ ] **Step 5: Commit**

```bash
git add services/market_svc/site_weather.py services/market_svc/scheduler.py .gitignore services/market_svc/tests/
git commit -m "feat(weather): write weather.json after each Market read slot and switch"
```

---

### Task 4: The card on the homepage

**Files:**
- Create: `deploy/site/assets/weather.js`
- Create: `tools/check_weather_decide.mjs` (a node check of `decide`, run by hand; not a pytest suite)
- Modify: `deploy/site/index.html` (hero: class instead of inline padding, the `aside`, the script tag)
- Modify: `deploy/site/assets/site.css` (`.ns-hero`, `.ns-weather*`)
- Test: `deploy/tests/test_site.py`

**Interfaces:**
- Consumes: `weather.json` v1 (Tasks 2–3); `window.NS_MARKET_CLOCK` (`holidays`, `openET`, `closeET`, `timeZone`).
- Produces:
  - `window.NS_WEATHER = {decide: function (file, now, clock) -> {mode, next}}`, where `now = {date: "YYYY-MM-DD", weekday: 0..6, minutes: int, epoch: seconds}` in the clock's zone, `mode` is `"live" | "fog" | "moon" | "hide"`, and `next` is the moon's "next forecast" text (`"8:45 AM CT Friday"`, or `"8:45 AM CT today"`).
  - Markup: `<aside class="ns-weather" data-weather aria-label="Market weather" hidden></aside>` placed right after the hero's text column, before `<figure class="ns-hero-mark">`.

`decide` rules, first match (spec §Fog and the moon, plus Review Focus 1, 4, 5):

1. no file, `hidden`, or `v !== 1` → `hide`.
2. `file.date === now.date && !file.final && now.epoch - file.ts <= 2 * file.interval_min * 60` → `live`.
3. `file.date === now.date && file.final` → `moon` (covers an early close the clock does not know).
4. in session (trading day, `open <= minutes < close`): if `minutes < open + 2 * interval_min` and `file.final` and `file.date === prevTradingDay(now.date)` → `moon`; else `fog`.
5. closed: the most recent session is `now.date` when today is a trading day and `minutes >= open`, else `prevTradingDay(now.date)`; `file.final && file.date === mostRecent` → `moon`; else `hide`.

`next`: the first slot (`open + interval_min`, shown in CT, which is ET − 1 hour) of today if today is a trading day and `minutes < open`, else of the next trading day; the day as `today` or its weekday name.

Drawing (match the mockup's compact card):
- Build every node with `document.createElement` and `textContent`; strings from the file never go through `innerHTML`. The thirteen icons are constant SVG strings in one object, `ICONS`, keyed by sky or reading key (the mockup's symbol sheet, same paths), and `el.innerHTML = ICONS[key];` is the only `innerHTML` in the file.
- `live`: dot, `Live · {slot_label}`, sky icon, `label`, temperature (S&P, `°`), headline, tally strip + `tally_text` + regime chip, footer.
- `fog`: fog icon, `Fog`, headline `Readings are delayed, so there is no sky to report right now.`, no temperature, no tally, no readings button.
- `moon`: moon icon, `After the close`, the file's temperature, sub `Last sky: {label}`, headline `Next forecast at {next}.`, tally, and the readings (the final reading) on request.
- Footer: `<button type="button" aria-expanded="false" aria-controls="ns-weather-rows">Show the readings</button>` toggling a `<ul id="ns-weather-rows" hidden>` and its label to `Hide the readings`; `<a href="https://live.neuralstrike.co/desk">Live Desk →</a>`; under the expanded rows: `From the Market read at {slot_label}. Measured readings, not a forecast.`
- When a card is drawn: unhide the aside, hide `.ns-hero-mark`, add `ns-hero--weather` to the hero section. On `hide`: the reverse, so the page is exactly as today.
- Fetch `weather.json` with `{cache: "no-cache"}` on load and every `refetch_sec` while `document.visibilityState === "visible"`; re-run `decide` every 60 s without fetching, so a sky turns to fog on time. Any exception leaves the card hidden. No `aria-live`.

- [ ] **Step 1: Write the failing site tests**

```python
def test_the_weather_card_ships_hidden_and_empty():
    html = (SITE / "index.html").read_text("utf-8")
    m = re.search(r'<aside class="ns-weather" data-weather[^>]*>(.*?)</aside>', html, re.S)
    assert m and " hidden" in m.group(0) and m.group(1).strip() == ""

def test_the_weather_script_loads_after_the_clock():
    srcs = re.findall(r'<script src="([^"]+)" defer>', (SITE / "index.html").read_text("utf-8"))
    assert srcs.index("assets/market-clock.js") < srcs.index("assets/weather.js")

def test_the_weather_script_reaches_only_its_own_file_and_the_desk():
    js = (SITE / "assets" / "weather.js").read_text("utf-8")
    assert re.findall(r'https?://[^\s"\']+', js) == ["https://live.neuralstrike.co/desk"]
    assert 'fetch("weather.json"' in js
    assert "app.neuralstrike" not in js

def test_the_weather_script_writes_no_file_text_as_html():
    js = (SITE / "assets" / "weather.js").read_text("utf-8")
    sets = re.findall(r"\.innerHTML\s*=\s*([^;]+);", js)
    assert sets and all(s.strip().startswith("ICONS[") for s in sets)   # constant icons only
```

The existing `test_no_page_reaches_an_external_origin`, `test_outbound_links_are_safe`, `test_no_page_carries_a_form_or_an_input` and `test_each_page_carries_its_own_content_without_scripting` must keep passing unchanged.

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest deploy/tests/test_site.py -q -k weather`
Expected: FAIL (no aside, no `weather.js`).

- [ ] **Step 3: Write `tools/check_weather_decide.mjs`**

It loads `deploy/site/assets/market-clock.js` and `weather.js` into a `vm` context with stub `window`/`document`, then asserts `decide` for these cases, using `interval_min: 15` and the 2026 clock:
`live_mid_session` (Thu 2026-11-19 11:20 ET-clock, file 11:15 not final, 5 min old → live) · `stale_is_fog` (same, file 40 min old → fog) · `pre_first_slot_is_moon` (Fri 2026-11-20 09:35 ET, file Thu final → moon, next `"8:45 AM CT today"`) · `early_close_final_is_moon` (Fri 2026-11-27 13:30 ET, file that date `12:00` final → moon) · `dead_since_midday_hides_in_evening` (Thu 19:00 ET, file Thu 13:00 not final → hide) · `weekend_moon` (Sat 2026-11-21, file Fri final → moon, next `"8:45 AM CT Monday"`) · `stale_final_hides` (Sat, file Wed final → hide) · `hidden_hides`.
Prints one line per case and exits non-zero on any failure.

- [ ] **Step 4: Implement `weather.js`, the markup and the CSS**

Move the hero section's inline `padding-top`/`padding-bottom`/flex styles into `.ns-hero` in `site.css` (same values), add `.ns-hero.ns-hero--weather { padding-top: 20px; }` and its first child's `padding-top: clamp(28px, 5vw, 84px)`, and `.ns-weather { flex: 0 1 380px; align-self: flex-start; }` plus the card classes against the existing tokens (`--color-surface`, `--color-text`, `--color-accent-*`). Add `<script src="assets/weather.js" defer></script>` after `ideas.js`.

- [ ] **Step 5: Run the node check and the site suite**

Run: `node tools/check_weather_decide.mjs` then `$PY -m pytest deploy -q`
Expected: eight `ok` lines and exit 0; the deploy suite PASS with no new failures or skips.

- [ ] **Step 6: Commit**

```bash
git add deploy/site/index.html deploy/site/assets/weather.js deploy/site/assets/site.css deploy/tests/test_site.py tools/check_weather_decide.mjs
git commit -m "feat(weather): the market weather card on the homepage"
```

---

### Task 5: See it, and write it down

**Files:**
- Modify: `docs/CHANGELOG.md`, `docs/manuals/user-guide/user-guide.md` (beside **The Market read**, line ~654), `docs/manuals/technical-reference/technical-reference.md` (a subsection under **## Market read**, line ~2829), then rebuild both manuals with `build_docs.py`.

- [ ] **Step 1: Render the real page with real payloads**

Write a payload from Task 2's storm fixture into a scratch copy of `deploy/site/` (never into the checkout's `deploy/site/weather.json`), serve that copy with `$PY -m http.server 8765 --bind 127.0.0.1`, and open `http://127.0.0.1:8765/` in the built-in browser. Check, with a screenshot of each: the live storm card top right under the menu bar; the toggle opening and closing the six rows; a `fair` payload; a payload older than 30 minutes (fog, during market hours) or a final one (moon); `{"v":1,"hidden":true}` (the hero mark is back); phone width 375 px (the card below the headline, no horizontal scroll). Stop the server afterwards.
Expected: each matches the mockup's compact card; the console shows no errors.

- [ ] **Step 2: Write the docs**

CHANGELOG: a dated entry naming the pieces. User Guide: what the card shows a visitor, its six skies, fog and the moon, and the `[weather]` switch. Technical Reference: the sky table, the tally masking, the reading sources table and the browser freshness rules (copy from the spec). Rebuild the HTML and `.docx`.

- [ ] **Step 3: Run the docs and CLAUDE.md guards**

Run: `$PY -m pytest tests/test_claude_md_size.py -q` and the manuals' own tests if they exist under `docs/manuals`.
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md docs/manuals/
git commit -m "docs(weather): the homepage weather card in the changelog and manuals"
```

Going live is not part of this plan: the branch is merged to `main` and reaches prod only through `tools/promote.sh`, at a time the operator picks (not during a trading session). After the promote, the first slot writes `weather.json` and the card appears; until then the homepage is unchanged.
