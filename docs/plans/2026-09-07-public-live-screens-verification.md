# Public live screens — verification checklist

**Run this before `tools/promote.sh`.** Every step says what it proves and what
it costs if skipped. Commands run on the box unless marked LOCAL.

## ⚠ Read this first: there is no dev environment

`/home/administrator/dev` **is prod**. Its `config/env.local.toml` says
`name = "prod"`; it resolves `NICEGUI_PORT=8500`, `OWNS_PROXY=True`,
`REDIS_DB=0`, and only `trading-prod-*` units exist. The directory name is a
fossil of the 2026-08-30 migration, which stood the box up as dev and then
promoted it in place.

So the usual "verify in dev on :9501" loop cannot be followed, and
**`git checkout` in that directory would put a feature branch into the live
trading stack.**

This checklist works around it with a **scratch checkout that runs one process**
— `webgui/live_main.py`, which binds `127.0.0.1:8501`, is read-only by
construction, and collides with nothing. Real prod data, no risk to the stack.

⚠ **In the scratch checkout, NEVER run** `tools/promote.sh`,
`deploy/systemd/generate_units.py --install`, `webgui/main.py`, any
`services/*/app.py`, or `schwab-proxy`. It resolves to `prod`, so those would
fight the live stack for ports, Redis and the Schwab token.

## What this checklist cannot prove before promoting

Two things need the real `options_svc` running the new code, which only happens
at promote:

- **The four gamma screens** (`/gamma`, `/net-premium`,
  `/premium-divergence/spy`, `/premium-divergence/qqq`) read
  `cache:options:gamma_pub:<SYMBOL>`, which today's running service does not
  write. Pre-promote they will show their honest "no snapshot" state. **That is
  expected, not a bug.** They are verified in Phase 6.
- **The published-refresh CPU cost** on the collection tick (measured at
  0.7–0.8% of the 60 s budget on synthetic chains, never on the box).

---

## Phase 0 — the static grid (LOCAL, no box)

Anything under `deploy/site` is served by Caddy as static files, so this is not
a weaker substitute for running it — it is the same thing Caddy will do.

```bash
python -m http.server 8790 --directory deploy/site
```

Open `http://127.0.0.1:8790/live.html` and check:

- [ ] Fourteen tiles, each captioned, in a sensible grid
- [ ] With `deploy/site/live/` **empty**, tiles render as labelled boxes at full
      size — not a collapsed pile of captions. This is the first-run state.
- [ ] Every tile links to `https://live.neuralstrike.co/<route>`
- [ ] No link anywhere to `app.neuralstrike.co`
- [ ] Nav, footer and the brand mark still render

Stop the server when done.

---

## Phase 1 — push the branch

```bash
git push -u origin claude/add-live-pages-website-e223c5
```

---

## Phase 2 — the Redis ACL user ✅ DONE 2026-09-07

Done before the scratch run, deliberately, so that run also proves the grant is
sufficient.

**What exists now:**

```
user live on #<hash> ~cache:* resetchannels &events:*
     -@all +@connection +@read +@transaction -watch -unwatch
     +subscribe +psubscribe -keys
```

### ⚠ `+@transaction` is required, and omitting it reads as stale data

The first grant here left it out, and **every screen rendered "data age
unknown"** with 365 `NoPermissionError`s in the log:

```
redis.exceptions.NoPermissionError: this user has no permissions to run the 'multi' command
```

`bus_client.read_versions` / `read_metas` batch their probes through a redis-py
pipeline, and **`pipeline()` defaults to `transaction=True`**, which issues
`MULTI`/`EXEC`. Those are `@transaction`, not `@read`. Denied, the batched
freshness reads fail — and the page has no way to say so, it just reports an
age it could not read.

**This grants no write ability.** Every command queued inside a transaction is
ACL-checked *at queue time*, verified here: a `MULTI` + `SET` returns `NOPERM`
on the SET and the whole thing `EXECABORT`s. `WATCH`/`UNWATCH` are excluded —
a reader needs no optimistic locking.

The credential is at **`/home/administrator/.config/neuralstrike/live.env`**
(mode 600) — deliberately **outside the checkout**, see the ordering trap in
Phase 5.

⚠ `+@read` alone is **not** enough. `SUBSCRIBE` is `@pubsub`; `SELECT` and
`PING` are `@connection`. Under-granted, every screen renders one frame and then
never repaints — and `EventListener` swallows the failure, so it reads as a
frozen tape, not an error. Verified working.

⚠ **`-keys` is not optional.** `KEYS` is `@read`, so the grant included it by
default — and it is O(N) on a **single-threaded Redis the whole trading stack
shares**. `bus_client` never calls it (GET / MGET / PING / SUBSCRIBE only), so
an unauthenticated path to it is a way to stall the trading stack for nothing.

**Verified in both directions** (all as the `live` user):

| Probe | Result |
|---|---|
| `SET` · `DEL` · `XADD cmd:options` · `PUBLISH` · `FLUSHDB` · `CONFIG GET` | NOPERM ✅ |
| `GET cmd:options` (outside `~cache:*`) | NOPERM ✅ |
| `KEYS *` | NOPERM ✅ |
| `PING` · `EXISTS` · `STRLEN` · `GET …:ver` · `MGET` | work ✅ |
| `SUBSCRIBE events:options:scan` | subscribes ✅ |

### ⚠ `ACL SAVE` does not work on this box — use `CONFIG REWRITE`

This checklist originally said to run `ACL SAVE`. **It fails here:**

```
ERR This Redis instance is not configured to use an ACL file.
```

No `aclfile` is set, so ACL users live in `redis.conf` instead. The persist step
is therefore:

```bash
redis-cli CONFIG REWRITE
```

which Redis performs *as itself* — it runs as `redis` and owns `/etc/redis`,
where `administrator` has neither read access nor password-less `sudo`.

⚠ **Do not redirect that command's output to `/dev/null`.** The first run of
this phase did, swallowed the `ACL SAVE` error, and reported success for a user
that would have vanished on the next Redis restart — the exact swallowed-error
class this repo documents elsewhere.

⚠ **`CONFIG REWRITE` returning `OK` is the strongest available proof, and it is
not complete proof.** Full confirmation needs a Redis restart, which is a
stack-wide cache blip and was deliberately not done. If Redis is ever restarted
and the live screens all degrade to "waiting", check `redis-cli ACL LIST` first
— a missing `live` user looks exactly like a service outage.

A recovery reference (the full running config, mode 600) is at
`/home/administrator/.config/neuralstrike/redis-running-config-*.txt`, because
this account cannot read `redis.conf` to back it up.

### Also learned here

**Redis now has `requirepass`.** Earlier notes recorded its absence as an open
item; it is closed. `MEMURAI_PASSWORD` is in the checkout's `.env`, and
`redis-cli` needs it for anything.

⚠ Pass it via **`REDISCLI_AUTH`**, never `--pass` — an argv password is
readable by any local process with `pgrep -af`, which is how this repo's YouTube
stream key leaks.

---

## Phase 3 — scratch checkout, and walk all fourteen screens ✅ DONE 2026-09-07

**Result: all fourteen serve, in under 260 ms each, with a completely clean
log** (0 NOPERM, 0 tracebacks, 0 ERROR) once `+@transaction` was granted.

Confirmed in a real browser against live prod data:

| | |
|---|---|
| 14 published routes | all 200 |
| `/terminate` `/settings` `/status` `/driver` `/options/paper` `/options/captured` `/login` `/logout` `/wall` `/eod` `/options/gamma` `/options/matrix` | all **404** |
| `?_s=PWNED` and `?symbol=…` | byte-identical responses — no reflection, no injection |
| App host / paths / credentials in page source | none |
| Macro Board | wrapper carries `macro-b`; HEAT LATTICE toggle active ✅ |
| Momentum | "Momentum Industries" ✅ |
| Sector & Industry | collapsed, Expand all / Collapse present, **no Refresh** ✅ |
| Sentiment · Bull/Bear · Rotation · RRG · Momentum | **no Refresh button on any** ✅ |
| Gamma | no Refresh / Explain / Analyze / Briefings, no view subtabs ✅ |
| RRG | no `viewBox`, no `vector-effect` — the percentage-endpoint fix is in place ✅ |
| Charts | `/sentiment` 821×200; `/gamma` 434×680, 434×680, 521×150 with 3/9/2 series — **none collapsed** ✅ |
| Flow Alerts row click | lands on **`/gamma`**, not `/options/gamma` ✅ |

**The chart-mount risk is resolved.** The Highcharts panels do mount hidden
(`display:none`) when there is no snapshot, which is the documented 0×0 trap in
its latent form — but seeded with real data they render at full size, so the
reflow path works. Net Prem's three hidden Highcharts are correct: that view
draws through a raw `ui.html` SVG fragment, not Highcharts.

⚠ To test that, `cache:options:gamma` was copied to
`cache:options:gamma_pub:$SPX` as a **temporary fixture** and deleted
immediately after. Additive and inert — nothing deployed reads `gamma_pub`.
Verified after: 0 `gamma_pub` keys, DBSIZE back to 213, private slot unchanged,
prod checkout clean and unmoved.

⚠ **"Data age unknown" and "Walls hidden" on the Desk are HONEST**, not a
defect: `cache:options:gex_status` carries `"age_seconds": null` and
`"session": "Closed"` (2026-09-07 is Labor Day). The Desk refuses to state an
age it did not read, and hides levels it cannot date.

⚠ **Two traps hit while running this, both documented and both real.** A
`pkill` pattern that did not match left the old process holding 8501, so the new
one failed to bind **silently** and the old one kept serving — kill by port, and
grep the log for `address already in use`. And a loose `grep 8500` "found" the
app port in `/sentiment`, which turned out to be the float
`5.6850000000000005`. Check the context before reporting a leak.

### The steps, for a re-run

This is the real verification. It reads live prod data through the read-only
ACL user and runs nothing else.

```bash
git clone /home/administrator/dev /home/administrator/live-check
```

```bash
cd /home/administrator/live-check && git remote set-url origin "$(git -C /home/administrator/dev remote get-url origin)" && git fetch origin claude/add-live-pages-website-e223c5 && git checkout claude/add-live-pages-website-e223c5
```

Give it an identity and the credential (heredoc, so nothing is expanded):

```bash
cd /home/administrator/live-check && cat > config/env.local.toml <<'EOF'
name = "prod"
EOF
```

Copy the credential Phase 2 created rather than retyping it — the password is
never printed anywhere, so a placeholder here would just be wrong:

```bash
cp /home/administrator/.config/neuralstrike/live.env /home/administrator/live-check/.env.live && chmod 600 /home/administrator/live-check/.env.live
```

⚠ The DB index in that URL is `/0` — prod's. `REDIS_LIVE_URL` carries its own
index and bypasses `repo_paths.REDIS_DB`, so a URL copied between environments
points the wrong way. Here `/0` is correct because this scratch run is reading
prod's cache deliberately.

⚠ This scratch checkout resolves to **prod**, which is what gives it prod's
Redis DB and a free port at 8501. It also means an accidental
`generate_units.py --install`, `promote.sh`, `webgui/main.py` or any
`services/*/app.py` from here would fight the live stack. **Run only
`webgui/live_main.py` from this directory.**

Run it, using prod's venv for its packages (the code comes from this checkout):

```bash
cd /home/administrator/live-check && set -a && . ./.env.live && set +a && /home/administrator/dev/.venv/bin/python webgui/live_main.py
```

- [ ] It **starts** — no refusal. A refusal here means `REDIS_LIVE_URL` did not
      reach the process, which is exactly the check working.
- [ ] `ss -ltnp | grep 8501` shows it listening on 127.0.0.1 only

From **Windows**, tunnel and browse:

```bash
ssh -L 8501:127.0.0.1:8501 vps2-ts
```

Open `http://127.0.0.1:8501/<route>` for each. **Ten are fully verifiable now:**

| Route | Check |
|---|---|
| `/desk` | Renders. Positions listed. **Rows are NOT clickable** (no pointer cursor). Opportunity / Flow / Bull-Bear cards click through to `/opportunity`, `/flow`, `/bullbear` — **not** a 404. No "ENABLE SPOKEN ALERTS" button. |
| `/opportunity` | Rows render; **sticky table header** works when scrolling |
| `/flow` | Alerts render; a row click goes to `/gamma`, not `/options/gamma` |
| `/macro` | **Heat Lattice** skin (continuous heat tiles, no panel chrome) — not the Instrument skin |
| `/sentiment` | Rings render. **No Refresh button.** |
| `/bullbear` | Tree renders. No Refresh button. |
| `/sectors` | Heat grid, **collapsed**. Expand all / Collapse still work. No Refresh. |
| `/rotation` | Gauge + flow band. No Refresh. |
| `/rrg` | Plot renders, trails visible, strokes **even** (not thick-horizontal/hairline-vertical). No Refresh. |
| `/momentum` | **Industries** level. No Refresh. |

- [ ] No nav rail, no header, no marquee on any of them
- [ ] Nothing links to `app.neuralstrike.co`
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8501/terminate` → **404**.
      Repeat for `/settings`, `/driver`, `/options/paper`, `/login`, `/wall`.

**Watch one screen repaint.** Leave `/desk` or `/flow` open for two minutes
during market hours and confirm a value changes. A first paint proves the reads;
only a repaint proves the ACL's pubsub grant.

- [ ] A value visibly updated without a manual reload

**Charts are the known risk.** These pages render outside `main._layout`, and
`ui.highchart` has no ResizeObserver — a chart that mounts in a zero-size
container renders collapsed and never recovers.

- [ ] No chart is squashed to title-height or oddly narrow on any screen

Leave it running for Phase 4, then Ctrl-C.

---

## Phase 4 — the capture script ✅ DONE 2026-09-07

**The settle delay is fine. The worry recorded below was unfounded** — the
captures are full renders, not skeletons.

| | |
|---|---|
| Browser | `google-chrome` at `/usr/bin/google-chrome` |
| Window gate, unmodified | `INFO outside the live-capture window … standing down`, **exit 0**, zero files ✅ |
| Gate bypassed | **14/14 captured in 42.3 s** (~3 s each), exit 0, no per-screen failures |
| File sizes | 36–144 KB for the eleven data screens; **7.6–9.0 KB** for the three gamma-family ones |
| Zero-byte or <5 KB | none |
| Chrome processes left behind | none |

**Inspected the actual images**, which is the only check that counts here:

- `desk.webp` — a **complete** Desk. Sentiment/trend rings, all eleven sectors,
  dealer positioning with a `SHORT GAMMA · RUNS` badge, the Opportunity Board,
  live flow alerts, and the Positions book with real ORCL spreads and P&L.
  Nothing is a placeholder.
- `rrg.webp` — full plot: quadrant washes, eleven labelled markers, trails,
  verdict strip. **Strokes are even**, confirming the percentage-endpoint fix
  survives DOMPurify stripping `vector-effect`.

⚠ **The three small files are expected, not a failure.** `gamma`,
`premium-divergence-spy` and `premium-divergence-qqq` captured the honest
"Fetch a symbol… (no snapshot yet)" state, because `cache:options:gamma_pub:*`
does not exist until the new `options_svc` runs. `gamma.webp` and
`premium-divergence-qqq.webp` are byte-identical in size for exactly that
reason. **Re-run this phase after the promote** and confirm all three grow.

**Timing headroom:** the generated unit derives `TimeoutStartSec` as
`SCREEN_TIMEOUT_SEC × 14 + 60` = 690 s. The real run took 42 s.

⚠ The captures landed in the **scratch** tree
(`/home/administrator/live-check/deploy/site/live/`), not prod's served
`deploy/site/`. Prod's is still empty, which is correct until Phase 6.

### The steps, for a re-run

With the live process still up:

```bash
cd /home/administrator/live-check && /home/administrator/dev/.venv/bin/python tools/capture_live_shots.py
```

```bash
ls -la /home/administrator/live-check/deploy/site/live/
```

- [ ] Fourteen `.webp` files
- [ ] None is 0 bytes

Copy a couple back and **look at them**:

```bash
scp vps2-ts:/home/administrator/live-check/deploy/site/live/desk.webp .
```

- [ ] The image shows a **rendered screen**, not a skeleton or a white box

⚠ **This is the step most likely to disappoint.** `--virtual-time-budget=8000`
waits on pending *network fetches*; NiceGUI paints over a **WebSocket**, which
it does not wait for. If the captures are skeletons, raising the budget is the
cheap thing to try and is not guaranteed — the real fix is a CDP capture that
waits on a selector. Say so rather than shipping blank tiles.

Outside `[windows.live_capture]` (08:00–15:20 CT, trading days) the script
stands down and exits 0 with no files. That is correct behaviour, not a failure
— run it inside the window.

---

## Phase 5 — promote prerequisites

- [ ] **DNS**: `live.neuralstrike.co` A record → the box's public IP. Confirm
      with `dig +short live.neuralstrike.co`.
### ⚠ `.env.live` goes in AFTER the promote, not before

The original ordering here was **wrong** and would have blocked the promote.

`tools/promote.sh` refuses on `git status --porcelain`, which includes
**untracked** files. Prod's current `.gitignore` has `.env` — an exact match,
not `.env*` — so `.env.live` would sit there untracked, dirty the tree, and
`promote.sh` would refuse before touching anything. The `.gitignore` entry that
covers it **arrives with the promote itself**.

That is why Phase 2 stored the credential outside the checkout. The file is
created in Phase 6, between the `git pull` and starting the unit.

- [ ] **DNS**: `live.neuralstrike.co` A record → the box's public IP. Confirm
      with `dig +short live.neuralstrike.co`.
- [ ] **Timing.** `promote.sh` stops the *whole* target — the public stream
      drops and GEX collection slots are lost. Default to **15:25–16:15 CT**.

⚠ **Without `.env.live` the live unit refuses to start in prod.** That is
deliberate — the alternative is a public process silently holding a full
read/write Redis credential. It fails alone and loudly; the trading stack is
unaffected. So the unit will not come up until Phase 6 puts the file in place.

---

## Phase 6 — promote, then check ✅ DONE 2026-09-07 (except Caddy)

**Promoted `7069d35` → `e9b3a81`, 44 commits.** `requirements.lock` unchanged,
so no dependency reinstall.

| | |
|---|---|
| Trading stack after | 8 units **active running**; proxy `:8100/health` 200, webgui `:8500/desk` 200 |
| `webgui_live` on first start | **failed, alone** — `Failed to load environment files` (no `.env.live` yet). Exactly the designed refusal; nothing else affected |
| After `cp` of the credential | `git check-ignore` confirms `.gitignore:37:.env.live`, **tree stays clean**, unit active on 127.0.0.1:8501 |
| 14 published routes | all 200 |
| `/terminate` `/settings` `/driver` `/options/paper` `/login` | all 404 |
| Capture timer | enabled, first fire 22:00 (stood down — outside the window) |
| Staleness | units started 21:58 against a 21:30 commit — not stale |

**The per-symbol publish works in production**, which was the last real unknown:

```
$SPX -> symbol='$SPX'  spot=7718.6   views=[Charm, DEX, GEX, Vanna]
SPY  -> symbol='SPY'   spot=770.19   views=[Charm, DEX, GEX, Vanna]
QQQ  -> symbol='QQQ'   spot=718.96   views=[Charm, DEX, GEX, Vanna]
```

and **only `gamma_pub_hist_$SPX_gex` exists** — the Task-3c history narrowing
holds on the real box, so SPY and QQQ pay for no history at all.

Rendered and confirmed in a browser: `/premium-divergence/spy` shows SPY
(`SPOT 769.96 · NET +7.48`), `/premium-divergence/qqq` shows QQQ
(`SPOT 718.72 · NET +98.25`), neither mentions the other or `$SPX`; `/gamma`
draws `Spot 7718.6 · Call wall 7720` with charts at 550×680, 826×680, 826×150.

⚠ **A 0×0 chart reading is usually the PANE, not the app.** Measured 20×680
once and nearly reported a collapse — `innerWidth` was **0** because the browser
pane had collapsed. Always print the viewport beside the chart size.

### ✅ Caddy installed and reloaded — the surface is LIVE

Verified end to end from an external machine over the public internet:

| | |
|---|---|
| 14 routes on `https://live.neuralstrike.co` | all **200**, 0.23–0.71 s |
| `/terminate` `/settings` `/status` `/driver` `/options/paper` `/options/captured` `/login` `/logout` `/wall` `/eod` `/options/gamma` `/options/matrix` `/manuals` | all **404** |
| `/robots.txt` | `User-agent: * / Disallow: /`, `text/plain` ✅ |
| `?_s=PWNED` | byte-identical to plain — no reflection |
| App host / paths / creds in `/desk` `/gamma` `/flow` `/opportunity` | **clean** |
| HSTS | `max-age=31536000` ✅ |
| `neuralstrike.co` · `app.neuralstrike.co` | 200 · **303 → `/login?next=%2Fdesk`** — unaffected, still gated |
| `neuralstrike.co/live.html` | 200, 14 tiles, every link to the live origin, no app host |
| `/desk` in a browser | fully rendered with live data |

⚠ **Captures 404 until the first in-window run**, so the grid shows its empty
state — verified in production as clean labelled panels, not a broken page.

### ⚠ The install reverted once, and the tell was in the journal

The first run reported success and the surface still did not answer. The
evidence, worth recognising again:

- `grep -c live.neuralstrike.co /etc/caddy/Caddyfile` → **0**
- `Caddyfile` and `Caddyfile.pre-live` both **1178 bytes** — identical, i.e. the
  original 38-line file, not the generated 66-line one
- but the journal showed `certificate obtained successfully` for
  `live.neuralstrike.co` at 22:07:06 — **so the config HAD loaded** — and then
  at 22:07:33 `enabling automatic TLS` listed only
  `www.neuralstrike.co, app.neuralstrike.co, neuralstrike.co`

Installed, loaded, then reverted 27 seconds later. **Always `grep -c` the
installed file** rather than trusting the exit code, which is why the command
below ends with one.

⚠ Windows `curl` reports this failure as
`schannel: SEC_E_INTERNAL_ERROR - The Local Security Authority cannot be
contacted`, which reads like a client-side TLS fault and is not one. The
discriminating check is that the **sibling hosts still answer**: if the apex and
app host are fine and only the new name fails, look at the Caddyfile, not at
TLS.

### For reference: the install, which needs sudo

`live.neuralstrike.co` is **not yet served** — the generated Caddyfile is not
installed, so nothing answers on :443 for that name. The generator's `--install`
writes a root-owned path and `sudo` prompts for a password on this box, so these
three are yours to run:

```bash
cd /home/administrator/dev && sudo .venv/bin/python -m deploy.caddy.generate_caddyfile --install
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

```bash
sudo systemctl reload caddy
```

The diff is **purely additive** — one `live.neuralstrike.co` block between the
apex and app blocks, 38 → 66 lines, nothing removed or changed. ⚠ Validate
before reloading: a bad Caddyfile takes **both** existing sites down.

Then finish:

- [ ] `https://live.neuralstrike.co/desk` loads over TLS (Caddy will request the
      certificate on reload; DNS already resolves to 63.141.255.25)
- [ ] `https://live.neuralstrike.co/robots.txt` returns `Disallow: /`
- [ ] `https://neuralstrike.co/live.html` shows the grid — **it will show empty
      tiles until the first in-window capture**, since prod's
      `deploy/site/live/` is still empty
- [ ] **Re-run Phase 4 in-window** and confirm the three gamma-family captures
      grow past ~9 KB now that `gamma_pub` exists
- [ ] The **collection-cadence** check below, after an hour of market hours
- [ ] Delete the scratch checkout: `rm -rf /home/administrator/live-check`

### The steps, for reference

```bash
cd /home/administrator/dev && ./tools/promote.sh
```

**Now** put the credential in place — the `git pull` above brought the
`.gitignore` entry that makes this file invisible to the dirty-tree check:

```bash
cp /home/administrator/.config/neuralstrike/live.env /home/administrator/dev/.env.live && chmod 600 /home/administrator/dev/.env.live
```

- [ ] `git -C /home/administrator/dev status --porcelain` is **empty**. If
      `.env.live` shows as `??`, the promote did not bring the ignore entry and
      the NEXT promote will refuse — fix that before going further.

```bash
cd /home/administrator/dev && .venv/bin/python -m deploy.systemd.generate_units --install && systemctl --user daemon-reload
```

⚠ **Validate Caddy before reloading it.** A bad Caddyfile takes *both* sites
down.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

```bash
sudo systemctl reload caddy
```

```bash
systemctl --user start trading-prod-webgui_live && systemctl --user enable --now trading-prod-live-capture.timer
```

Now check:

- [ ] `systemctl --user status trading-prod-webgui_live` — active
- [ ] **The serving process is not stale.** Compare its start against the commit;
      a stack can serve old code while looking perfectly healthy.

```bash
systemctl --user show -p ActiveEnterTimestamp trading-prod-webgui.service && cd /home/administrator/dev && git log -1 --format=%cd --date=iso HEAD
```

- [ ] `https://live.neuralstrike.co/desk` loads over TLS
- [ ] `https://live.neuralstrike.co/robots.txt` returns `Disallow: /`
- [ ] `https://neuralstrike.co/live.html` shows the grid with real captures
- [ ] **The private app still works** — log in, load `/options/gamma`, confirm
      the view subtabs, Refresh, Explain, Analyze and Briefings are all still
      there, and the six sentiment screens still have their Refresh buttons

**The four gamma screens, now verifiable:**

```bash
cd /home/administrator/dev && .venv/bin/python -c "
from shared.bus import Bus
b = Bus()
for s in ('\$SPX','SPY','QQQ'):
    env = b.cache_get(f'cache:options:gamma_pub:{s}')
    p = (env.payload if env else None) or {}
    print(s, '->', p.get('symbol'), 'views:', sorted((p.get('views') or {})))"
```

- [ ] Each line reports **its own** symbol
- [ ] `/gamma` shows `$SPX`, `/premium-divergence/spy` shows SPY,
      `/premium-divergence/qqq` shows QQQ
- [ ] `/net-premium` plots SPY, QQQ and BIG10 in **Dollars**

**Collection cadence — the one measured risk.** The published refreshes now run
on the collection tick. After an hour of market hours:

```bash
cd /home/administrator/dev && .venv/bin/python -c "
import sqlite3, collections
c = sqlite3.connect('options-scanner/gex_history.db')
ts = [r[0] for r in c.execute('SELECT DISTINCT ts FROM gex_snapshots ORDER BY ts DESC LIMIT 120')]
gaps = collections.Counter(round((a-b)/60) for a, b in zip(ts, ts[1:]))
print('minute gaps:', dict(gaps))"
```

- [ ] Gaps are overwhelmingly `1`. A crop of `2`s means the tick is overrunning
      its budget — the documented failure this repo has hit before.

---

## Rollback

The public surface is separable from the trading stack, so back it out alone:

```bash
systemctl --user disable --now trading-prod-live-capture.timer && systemctl --user stop trading-prod-webgui_live
```

That leaves the private app and every service untouched. The static grid stays
up but its links 502 — acceptable for the minutes it takes to decide.

To back out the code as well, revert on `main` and promote again. The
`options_svc` changes are additive (new keys, existing ones unchanged), so a
revert needs no data cleanup — but the orphaned `cache:options:gamma_pub:*` and
`gamma_pub_hist_*` keys will linger:

```bash
redis-cli --scan --pattern 'cache:options:gamma_pub*' | xargs -r redis-cli DEL
```
