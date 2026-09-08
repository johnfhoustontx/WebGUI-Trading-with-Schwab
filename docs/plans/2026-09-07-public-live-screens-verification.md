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
     -@all +@connection +@read +subscribe -keys +psubscribe
```

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

## Phase 3 — scratch checkout, and walk all fourteen screens

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

## Phase 4 — the capture script

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

## Phase 6 — promote, then check

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
