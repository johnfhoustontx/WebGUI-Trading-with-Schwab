# Dev and prod environments — operator runbook

How to stand the two environments up, run them, snapshot data between them, and
promote code. Reference only — for *why* it is shaped this way, see
[the design](plans/2026-08-08-dev-prod-environments-design.md).

Every port, path and command below was checked against the **running VPS** on
2026-08-29, the day dev was stood up beside prod on it. Commands are bash on the
Linux host — the PowerShell spellings that used to be here died with the Windows
box, along with the twelve `.bat` launchers.

---

## 1. Which folder is which

> ⚠ **The current server runs ONE checkout, and it is production: it lives at
> `/home/administrator/dev`.** It was stood up as dev on the replacement server
> (2026-08-30) and promoted in place by changing `name` in
> `config/env.local.toml`; the directory kept its name. **There is no
> `/home/administrator/prod`.** The table below is the two-checkout layout this
> repository supports and ran until that date. Wherever this runbook says
> `/home/administrator/prod`, read it as "the production checkout", which today
> is `/home/administrator/dev`. A second checkout stood up as real dev must be
> given a different directory name.

| | prod | dev |
|---|---|---|
| Folder | `/home/administrator/prod` | `/home/administrator/dev` |
| Git | pinned to `main` | feature branches |
| schwab-proxy | **owns** it, `:8100` | **borrows** prod's — starts none |
| sentiment / options / portfolio / trade / market / news / blog / xfeed | 8210–8213, 8215–8217, 8219 | 9210–9213, 9215–9217, 9219 |
| webgui | `:8500` | `:9500` |
| webgui_live (public screens) | `:8501` | `:9501` |
| Redis (one server, `:6379`) | **db 0** | **db 1** |
| SQLite, `logs/`, `webgui/data` | its own | its own |
| Schedulers · Claude · notifications | live | **off** |
| Units | `trading-prod.target` — **11** units | `trading-dev.target` — **10** (no proxy) |
| Start / stop | `systemctl --user start trading-prod.target` | `systemctl --user start trading-dev.target` |
| Nightly backup timer | **enabled** | **not enabled** — its data is a disposable snapshot of prod |
| Secrets in `.env` | `MEMURAI_PASSWORD` | `MEMURAI_PASSWORD` only |
| Schwab credentials on disk | `appsettings.json`, `tokens.json`, `proxy_tokens.json` | **none** — only `schwab-proxy` reads them, and dev runs no proxy |

Prod's numbers are byte-identical to what this repo used before environments
existed, so prod is a relocation, not a reconfiguration.

### The two config files that decide it

**`config/environments.toml` — tracked.** The two profiles: `port_offset`
(prod 0, dev 1000), `proxy_port` (dev pins 8100), `redis_db`, `owns_proxy`, and
the four behaviour flags.

**`config/env.local.toml` — gitignored.** Which profile *this checkout* is:

```toml
name = "dev"                          # "dev" | "prod"
peer_root = '/home/administrator/prod'  # optional; the snapshot tool's SOURCE
```

Rules worth knowing:

- **A missing file resolves to `prod`.** A checkout without a marker behaves
  exactly as the repo did before environments existed.
- Because it is gitignored, **`git pull` can never carry an identity between
  checkouts**, in either direction.
- **A parse error is silent in the direction that hurts.** A marker that exists
  but will not parse warns on stderr and then falls back to **prod** — so a
  broken dev marker gives you a second prod. On a POSIX path the old
  single-quote rule no longer bites (`'...'` is kept only because it costs
  nothing), but the failure MODE is unchanged: if dev is behaving like prod,
  suspect the marker before anything else, and settle it with the check below
  rather than by reading the file.
- A **git worktree has no marker** — it is gitignored, so it cannot travel —
  and therefore resolves to **prod** and binds `:8500`. On the VPS that is the
  port the live stack is on. Drop a `name = "dev"` marker in the worktree
  before previewing from one, or do not preview from one.

Check what a checkout thinks it is — do this **before** generating units, since
`generate_units.py --install` writes units named for whatever it resolves:

```bash
cd /home/administrator/dev && .venv/bin/python -c "import repo_paths as r; print(r.ENV_NAME, r.SERVICE_PORTS, r.NICEGUI_PORT, r.PROXY_URL, r.REDIS_DB)"
```

Expected in dev: `dev {…9210-9216} 9500 http://127.0.0.1:8100 1`.

Template: `config/env.local.example.toml`.

---

## 2. Standing a checkout up from scratch

Both environments already exist. This is the procedure that built dev on
2026-08-29, kept because it is what you would follow to rebuild either one —
after a provider loss, or on a replacement host.

⚠ **Order matters, and step 3 is the dangerous one.** Until the marker is
written, a fresh checkout resolves to **prod** — so `generate_units.py --install`
run before it would emit `trading-prod-*` units pointing at the NEW checkout and
overwrite the live stack's units. Write the marker, verify it, then generate.

**1. Clone, and pin line endings.**

```bash
git clone https://github.com/johnfhoustontx/WebGUI-Trading-with-Schwab.git /home/administrator/dev
cd /home/administrator/dev && git config core.autocrlf false && git checkout main
```

**2. Its own venv** — never shared, so a dependency bump in one environment
cannot move the other.

```bash
uv venv --python 3.11 .venv && uv pip install -r requirements.lock
```

Verify it is complete rather than assuming: `.venv/bin/python -m pip install
--dry-run -r requirements.lock` must offer to install **nothing**.

**3. The marker**, `config/env.local.toml` — then run the identity check from §1
and confirm it says `dev` before going further.

```toml
name = "dev"
peer_root = '/home/administrator/prod'
```

No `proxy_host`: dev borrows prod's proxy at `127.0.0.1:8100`, which co-location
makes free.

**4. `.env`, mode 600.** The units read it via `EnvironmentFile=` with no
leading dash, so a missing file fails the unit **loudly** — deliberately, since
the alternative is a stack that comes up mute. Copy the one line dev needs
without ever printing it:

```bash
umask 077 && grep '^MEMURAI_PASSWORD=' /home/administrator/prod/.env > /home/administrator/dev/.env
```

⚠ **`ANTHROPIC_API_KEY` is deliberately absent from dev's file.** `allow_claude`
is already false in the profile; leaving the key out is the second belt.

⚠ **QUOTE ANY VALUE CONTAINING A SPACE.** `.env` is read by **two parsers that
disagree**, and only one of them complains:

* **systemd** (`EnvironmentFile=`) is not a shell. `KEY=two words (x)` is taken
  literally and works.
* **bash**, when you `set -a && . ./.env` — which §3 and §4 tell you to do before
  running `snapshot_from_prod.py`, because that tool needs `MEMURAI_PASSWORD` and
  does not get the units' environment.

So an unquoted value with a space starts the services perfectly and breaks the
snapshot tool later, at a moment unconnected to the edit that caused it. Found
2026-08-31 adding `EDGAR_USER_AGENT=NeuralStrike/1.0 (fernandesj@gmail.com)`:
bash died on the parentheses. Write it as

```
EDGAR_USER_AGENT="NeuralStrike/1.0 (contact@example.com)"
```

systemd **strips** the surrounding quotes, so the service still sees the bare
string — verify that rather than assuming, by reading the running process's
environment:

```bash
tr '\0' '\n' < /proc/$(systemctl --user show -p MainPID --value trading-prod-trade_svc)/environ | grep EDGAR
```

Quotes still present there would mean the remote end sees them too, which for a
User-Agent is a silent wrong value rather than an error.

**What lives in `.env`, and why each is there:**

| Key | Read by | Notes |
|---|---|---|
| `MEMURAI_PASSWORD` | every service via `shared/bus`, and `backup_local.py` via `REDISCLI_AUTH` | unset or empty = no AUTH, so an unauthenticated Redis still works |
| `ALPHAVANTAGE_API_KEY` | `trade_svc` earnings calendar/history | absent = the earnings gate stays quiet, logged at INFO |
| `EDGAR_USER_AGENT` | `trade_svc` EDGAR fundamentals | **not a key** — SEC issues none. A contact string their fair-access policy asks for; without one they answer **403** |
| `ANTHROPIC_API_KEY` | the three Claude client factories | prod only; dev omits it beside `allow_claude = false` |
| `FRED_API_KEY` | `news_svc` economic calendar (`econ_calendar`, read from the process environment at call time) | **optional.** Present: the calendar's values come from the FRED API. Absent or empty: from the key-free `fredgraph.csv` download, with the same result. It is a query parameter, so every error that could carry it is redacted before it is logged or stored. **Never in config, never in `.env.live`** — the public process reads no calendar source |
| `TRADING_ENABLE_SCHEDULERS` | `_scaffold` | the §5 escape hatch, and only ever temporary |

Adding the FRED key to prod (mode stays 600; restart only the news service):

```bash
cd /home/administrator/dev && umask 077 && printf 'FRED_API_KEY=%s\n' '<key>' >> .env \
  && ! grep -q FRED_API_KEY .env.live && systemctl --user restart trading-prod-news_svc
```

Then `journalctl --user -u trading-prod-news_svc --since -10min | grep -c api_key`
must print **0**.

**The calendar's User-Agents are per source, and not interchangeable** — each is
`user_agent` in its `[calendar.sources.<name>]` table (`""` = `[collector]
feed_user_agent`, the repo's contact-bearing string). **BLS answers 403 to a browser
User-Agent** and **FRED resets a bare Chrome one from a datacenter IP**, so both keep
the repo UA; **Nasdaq refuses the repo UA** and gets a Chrome string plus
`Accept: application/json`. Measured from a different datacenter than prod's; a
source reading `error` in `cache:news:calendar_status` after a promote is the first
thing to check from prod's own IP.

⚠ **`.env` is in the backup** (`backup_local.EXTRA_FILES`) — it was not until
2026-08-31, which meant a restore produced a stack that could not start, from an
archive that looked complete.

**4b. `.env.live`, mode 600 — the PUBLIC process's own file.** The
`webgui_live` unit loads **this** and **not** `.env`. It is the one
internet-facing, unauthenticated process in the fleet, and `.env` supplies
`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PROXY_SHARED_SECRET`,
`SMS_SMTP_APP_PASSWORD`, `DISCORD_WEBHOOK_URL` and
`GAMMA_BRIEFING_WEBHOOK_URL` — the strongest credential set on the box, held by
the weakest-protected process. No code path in it reads any of them today; the
split is about what an RCE in NiceGUI would cost, not about a live bug.

```bash
umask 077 && cat > /home/administrator/prod/.env.live <<'EOF'
REDIS_LIVE_URL=redis://live:<the ACL user's password>@127.0.0.1:6379/0
EOF
```

⚠ **`MEMURAI_PASSWORD` does not belong in this file.** It is the stack's admin
Redis password, and this is the one process on the public internet. It was
listed here until 2026-10-04; **if your `.env.live` still has that line, delete
it** and restart `trading-prod-webgui_live`. Nothing reads it: a URL that names
its own user is never given the stack password (`shared.bus.client.env_password`).

| Key | Read by | Notes |
|---|---|---|
| `REDIS_LIVE_URL` | `webgui/live_main.py` and nothing else | the read-only Redis ACL user. **Unset or empty, `live_main` refuses to serve in prod** — the Bus would otherwise fall back to the stack's ordinary full read/write credential, on an origin with no login |


**Two checks run before the public process serves in prod**, and either one
stops it (the unit fails, with the reason in its journal):

1. `REDIS_LIVE_URL` must name a user, and not `default`. A URL with no user is
   the default user, which is the stack's full read/write identity.
2. The process asks Redis: it tries one write to a cache key and one to a
   command stream. **Both must be refused.** If either goes through, or Redis
   does not answer after six tries five seconds apart, it does not serve. This
   is the check a URL that merely looks right cannot pass.

⚠ **The DB index is in the URL**, so it bypasses the `redis_db` the profile
selects. Prod is `/0`, **dev is `/1`**. Copying prod's line into dev's file aims
dev's public process at prod's data, and every published screen then renders
prod's real book while the checkout looks like dev. `live_main` warns on a
mismatch — into the journal, which is not where anyone is looking.

⚠ **No leading dash on this `EnvironmentFile=` either**, so a missing file
fails the unit. That is not merely the house rule here: a dash would start the
process with no `REDIS_LIVE_URL`, which the prod refusal above kills anyway, so
it could only ever trade a systemd error naming the exact path for a Python one
naming the variable.

⚠ **`.env.live` is in the backup and in `.gitignore`** — both as their own
line, because neither `.env` entry matches this name.

**The public Market News screen (`/news`, 2026-09-26; its SEC panel and calendar
the same day) needs NO ACL change.**
The `live` user's key pattern is `~cache:*` (its grant, as recorded in
[the public-screens verification](plans/2026-09-07-public-live-screens-verification.md)
Phase 2: `~cache:* resetchannels &events:* -@all +@connection +@read
+@transaction … +subscribe +psubscribe -keys`), which already covers
`cache:news:feed_public`, `cache:news:sec_public` and `cache:news:calendar_public`.
⚠ **It equally covers `cache:news:feed`, `:sec`, `:calendar` and
`:calendar_status` — the PRIVATE views — and no ACL can take that back:** Redis key patterns only grant, so a
wildcard cannot exclude one key beneath it, and adding a `%R~cache:news:*`
selector would grant nothing new and exclude nothing. What keeps the private
views off the public origin is **code** — the Desk's `bus_key` and
`webgui/pages/news_live.py` read only the three public views in this process —
pinned by `webgui/tests/test_news_live.py` and the Desk's public-key tests. The
screen writes nothing, so it needs no write selector either.

**4c. The live ACL user's FIRST write (public Strategy Finder).** The `live`
user is read-only except for its Redis 7 selectors, one per public stream, each
allowing `XADD` on that one key and nothing else. This first one is for
`cmd:finder_public`. Steps 4c, 4d, 4e and 4f are applied in that order, and all
four before the promote that ships the pages using them. That stream carries a visitor's
request to scan one symbol (`shared/public_scan.py`); options_svc answers it on
a consumer loop of its own. Add the selector, then persist it:

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL SETUSER live '(%W~cmd:finder_public +xadd)'
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli CONFIG REWRITE
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL GETUSER live
```

`%W~` is a WRITE-only key selector: the user cannot read that stream, and the
selector's `+xadd` applies to that key alone. ⚠ `ACL SAVE` does not work on this
box (no `aclfile`; users live in `redis.conf`), so `CONFIG REWRITE` is the
persist step. Read its output rather than redirecting it: an unpersisted user
vanishes on the next Redis restart. ⚠ Pass the password through
`REDISCLI_AUTH`, never `--pass`, which puts it in the process list.

**Verify both directions as the `live` user.** Read its URL from `.env.live`
inside Python, so the password never reaches a command line or the process
list (`redis-cli -u` would put it there):

```bash
cd /home/administrator/dev && set -a && . ./.env.live && set +a && .venv/bin/python - <<"PY"
import os, redis
r = redis.Redis.from_url(os.environ["REDIS_LIVE_URL"], decode_responses=True)
for label, fn in [
    ("XADD cmd:finder_public (allowed)", lambda: r.xadd("cmd:finder_public", {"probe": "1"})),
    ("XADD cmd:options", lambda: r.xadd("cmd:options", {"probe": "1"})),
    ("SET cache:options:x", lambda: r.set("cache:options:x", 1)),
    ("PUBLISH events:options:scan", lambda: r.publish("events:options:scan", "x")),
    ("XRANGE cmd:finder_public", lambda: r.xrange("cmd:finder_public")),
    ("DEL cmd:finder_public", lambda: r.delete("cmd:finder_public")),
]:
    try:
        print("ALLOWED", label, fn())
    except redis.exceptions.NoPermissionError:
        print("NOPERM ", label)
PY
```

**Applied on prod 2026-09-21 07:58 CT and verified:** the one `XADD` allowed;
`XADD cmd:options`, `XADD` on a lookalike key, `SET`, `PUBLISH`, `XRANGE`,
`XTRIM`, `DEL` and `KEYS` all `NOPERM`; `PING` and cache reads unchanged. The
probe entry was removed afterwards with `XDEL` as the admin user.

A probe entry has no `data` field, so if it is not removed options_svc
dead-letters it to `cmd:finder_public:dead` on its next read, where it is
harmless.
Without this selector every public scan request fails with `NOPERM`, which the
public page words as an error rather than a permissions problem.

**4d. The live ACL user's SECOND write (public Rescue form).** ⚠ **Not yet
applied on prod.** Since 2026-09-21 the `live` user needs one more selector,
for `cmd:rescue_public`: a visitor's request for a symbol's strikes, or for the
rescue menu of one trade (`shared/public_rescue.py`, which validates every
field before anything is written). options_svc answers it on a third consumer
loop. A second `(...)` ADDS a selector; it does not replace the Finder's:

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL SETUSER live '(%W~cmd:rescue_public +xadd)'
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli CONFIG REWRITE
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL GETUSER live
```

`ACL GETUSER` must now list **two** selectors. Verify with the 4c probe, adding
these two lines to its list, and expect the first `ALLOWED`, the second
`NOPERM`, and every existing line unchanged:

```python
    ("XADD cmd:rescue_public (allowed)", lambda: r.xadd("cmd:rescue_public", {"probe": "1"})),
    ("XRANGE cmd:rescue_public", lambda: r.xrange("cmd:rescue_public")),
```

Remove the probe entry afterwards with `XDEL` as the admin user, as in 4c.
Without this selector the public Rescue page's Load and Compute both fail with
`NOPERM`, which the page words as "The request could not be sent".

**4e. The live ACL user's THIRD write (public Calculator and Simulator,
Schwab-spending requests).** ⚠ **Not yet applied on prod.** Since 2026-09-21
the `live` user needs a selector for `cmd:tools_public`: a visitor's request for
a symbol's chain, one more expiration, a trade rating, or a Simulator snapshot
and its expirations (`shared/public_tools.py`, which validates every field
before anything is written). options_svc answers it on a fourth consumer loop
(`services/options_svc/tools_public.py`). Apply it after 4d:

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL SETUSER live '(%W~cmd:tools_public +xadd)'
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli CONFIG REWRITE
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL GETUSER live
```

`ACL GETUSER` must now list **three** selectors. Verify with the 4c probe, adding
these two lines to its list (beside 4d's), and expect the first `ALLOWED`, the
second `NOPERM`, and every existing line unchanged:

```python
    ("XADD cmd:tools_public (allowed)", lambda: r.xadd("cmd:tools_public", {"probe": "1"})),
    ("XRANGE cmd:tools_public", lambda: r.xrange("cmd:tools_public")),
```

Remove the probe entry afterwards with `XDEL` as the admin user, as in 4c.
Without this selector the public Calculator's Load and Rate my trade, and the
public Simulator's snapshot load, all fail with `NOPERM`, which both pages word
as "The request could not be sent".

**4f. The live ACL user's FOURTH write (public Calculator and Simulator,
pricing).** ⚠ **Not yet applied on prod.** The same pages put their pricing
requests - reprice a position, imply one contract's volatility, run a Simulator
what-if sweep - on a stream of their own, `cmd:tools_public_math`, so a
snapshot fetch never stalls the reprice every edit triggers. These spend no
Schwab call, but they are still a write, so they need their own selector.
Apply it after 4e:

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL SETUSER live '(%W~cmd:tools_public_math +xadd)'
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli CONFIG REWRITE
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL GETUSER live
```

`ACL GETUSER` must now list **four** selectors: the Finder's, Rescue's and the
two above. Verify with the 4c probe, adding these two lines, and expect the
first `ALLOWED`, the second `NOPERM`, and every existing line unchanged:

```python
    ("XADD cmd:tools_public_math (allowed)", lambda: r.xadd("cmd:tools_public_math", {"probe": "1"})),
    ("XRANGE cmd:tools_public_math", lambda: r.xrange("cmd:tools_public_math")),
```

Remove the probe entry afterwards with `XDEL` as the admin user, as in 4c.
Without this selector a loaded chain never prices: every edit on the public
Calculator, and every slider move on the public Simulator, fails with `NOPERM`,
which both pages word as "The request could not be sent". As in 4c, a probe
entry left on either stream has no `data` field, so options_svc dead-letters it
to `<stream>:dead` on its next read, where it is harmless.

**4g. The live ACL user's FIFTH write (public Gamma page, live symbols).**
⚠ **Not yet applied on prod.** A visitor's pick on the public Gamma page puts one
dropdown symbol on `cmd:gamma_public` (`bus_client.request_public_gamma`), and
options_svc keeps it live on the 1-minute GEX tick (`services/options_svc/
gamma_public.py`). It spends no Schwab call, but it is a write, so it needs its
own selector:

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL SETUSER live '(%W~cmd:gamma_public +xadd)'
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli CONFIG REWRITE
REDISCLI_AUTH="$MEMURAI_PASSWORD" redis-cli ACL GETUSER live
```

`ACL GETUSER` must now list **five** selectors. Verify with the 4c probe, adding
these two lines, and expect `ALLOWED` then `NOPERM`:

```python
    ("XADD cmd:gamma_public (allowed)", lambda: r.xadd("cmd:gamma_public", {"probe": "1"})),
    ("XRANGE cmd:gamma_public", lambda: r.xrange("cmd:gamma_public")),
```

Remove the probe entry afterwards with `XDEL`, as in 4c. Without this selector
the public Gamma page still draws $SPX, SPY and QQQ, but no other symbol ever
goes live.

**What the quotes switch does once these are applied.** Settings →
Configuration → **Show per-leg bid and ask** (`show_leg_quotes` in
`config/finder_public.toml`) is read by the pages on every chain they land, so
turning it off stops the public Calculator drawing quotes at once: no chain
grid, no Bid / Mark / Ask select, no delta column, no "Quotes as of" stamp.
Redis keys written while it was on (`cache:options:pub_chain:<SYMBOL>`) keep
their quotes block until the next request for that symbol rewrites them, or
until they expire (`ladder_keep_min` in `config/rescue_public.toml`). The public process can read those keys,
but no page draws them.

**5. Carry the gitignored artifacts.** Most arrive with the snapshot in §4 —
including `Top 20.xlsx` and the sentiment bridge — so the only hand-copy is the
one store no tool knows about:

```bash
mkdir -p trade-analyzer/data && cp -p /home/administrator/prod/trade-analyzer/data/swing_model.json trade-analyzer/data/
```

⚠ **Dev needs NO Schwab credentials.** `APPSETTINGS` and `TOKENS` are read by
`schwab-proxy/schwab_proxy.py` and nothing else, and dev runs no proxy. Copying
`tokens.json` in would put a second copy of the single rotating refresh token on
disk for no benefit — the exact hazard `owns_proxy = false` exists to avoid.

**6. Generate and install the units.** They are derived from `repo_paths`, never
committed, so this is also how you repair them after any port or path change.

```bash
.venv/bin/python -m deploy.systemd.generate_units --install
```

It reloads the daemon itself, and **arms every timer it wrote** (`enable --now`)
— on a prod checkout. A written `.timer` nothing enables is a file, not a
schedule, and on this box that silently cost eleven days of flow-delta reports.
In a **dev** checkout it arms nothing, deliberately: see the warning at step 8.
Read its output — a timer it could not arm is printed as `FAILED` and the
command exits non-zero.

Confirm the shape before starting anything: **ten** `trading-dev-*` units and
**no proxy unit** — ownership is encoded in which units exist, not in a kill-list
filter. `systemd-analyze --user verify ~/.config/systemd/user/trading-dev.target`
should print nothing at all; any output is an error.

**7. Load the data** — §4. Do this before the first start, so the services come
up on prod's snapshot rather than creating empty stores.

**8. Start it, and enable it at boot.**

```bash
systemctl --user enable --now trading-dev.target
```

⚠ **Do not enable `trading-dev-backup.timer`.** The generator emits a backup
unit for every environment, but dev's stores are a disposable copy of prod's by
construction; enabling it would encrypt and ship ~1.5 GB of duplicate data every
night. Prod's timer is the one that matters.

This is now ENFORCED rather than requested: `--install` arms timers only in a
prod checkout, so a dev install writes all of them and enables none. Arming one
by hand still works if you genuinely want it.

**9. Verify.** On `http://127.0.0.1:9500` (tunnelled — see §9) the tab title is
prefixed `DEV ·` and the header carries a **DEV** chip; prod's is bare. Then
confirm the four suppressions are **enforced**, not merely configured — read
them from the enforcement points rather than from the profile, since the profile
is the thing you would be checking against itself:

```bash
cd /home/administrator/dev && .venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from services import _scaffold; import repo_paths as r
from shared.notify import channels
print('schedulers  :', _scaffold._schedulers_enabled())
print('claude      :', r.ENV_FLAGS['allow_claude'])
print('autonomous  :', r.ENV_FLAGS['autonomous_trading'])
def flags(d, path=''):
    for k, v in (d.items() if isinstance(d, dict) else ()):
        if k == 'enabled': print('notify      :', path or '(root)', v)
        else: flags(v, path + '.' + k if path else k)
flags(channels.load_config())"
```

All four must be false, and every `enabled` flag the last lines print must be
false — `load_config` zeroes them recursively and **last**, so it overrides the
`NOTIFY_ENABLED`/`X_ENABLED` env escapes too. ⚠ It prints the `enabled` flags
only, never the whole config: that dict carries the Telegram bot token, the
Discord webhook, the SMS app password and the four X OAuth credentials, and this
check's output tends to get pasted into a chat or a log.

⚠ **`/health` will not answer this question, and it looks as though it does.**
`scheduler_alive` is `true` on a dev service: it means "the restart budget is
not exhausted", not "a scheduler is running", and it defaults true. The field
that actually discriminates is **`scheduler_uptime_s`** — `null` in dev,
because the task never started. On prod it is time since the scheduler last
started, so a large value is normal. **`scheduler_last_tick_age_s`** (since
2026-09-16) is the real heartbeat: seconds since the loop last went round, also
`null` in dev. On prod it should stay under the loop's own interval (30 s for
options, 120 s for sentiment, a few seconds for market and portfolio);
a value climbing past that means the loop has stalled without crashing, which
`scheduler_alive` cannot see.
## 3. Daily dev loop

```bash
cd /home/administrator/dev && systemctl --user stop trading-dev.target
```

```bash
cd /home/administrator/dev && set -a && . ./.env && set +a && .venv/bin/python tools/snapshot_from_prod.py
```

```bash
systemctl --user start trading-dev.target
```

⚠ **The `. ./.env` is load-bearing.** Redis runs with `requirepass`, and the
services get the password from their unit's `EnvironmentFile=` — a shell does
not. Without it the SQLite half completes and the Redis half dies on
`AuthenticationError`, leaving dev with fresh stores and stale cache.

Then work at **http://127.0.0.1:9500**. The header carries a `DEV` chip and the
browser tab reads `DEV · NeuralStrike` — that is how you tell the two tabs apart.

`systemctl --user start trading-dev.target` brings up **ten** units (eight
services + webgui + webgui_live, and no proxy — dev borrows prod's; the live
screens are NOT withheld the way the proxy is, because nothing about them is a
shared exclusive credential). Output goes to the
journal: `journalctl --user -u trading-dev-options_svc -f`.

It cannot start the wrong stack. A unit's `ExecStart` and `WorkingDirectory` are
generated from that checkout's own `repo_paths`, so `trading-dev-*` runs dev's
code on dev's ports by construction — there is no shared launcher to point at
the wrong tree, which is what the old `IS_DEV` refusal existed to catch.

Prod keeps running the whole time. You do not stop it to snapshot.

---

## 4. Snapshotting prod's data

```bash
set -a && . ./.env && set +a && .venv/bin/python tools/snapshot_from_prod.py [--dry-run] [--redis-only] [--skip-gex] [--peer PATH]
```

| Flag | Effect |
|---|---|
| `--dry-run` | report the plan and sizes; write nothing |
| `--redis-only` | skip the file/SQLite copies |
| `--skip-gex` | skip `gex_history.db` (the ~1.4 GB one) |
| `--peer PATH` | prod checkout root (else `peer_root` from `config/env.local.toml`) |

What it does: SQLite via the online-backup API (**prod keeps running and
writing**), then `FLUSHDB` on dev's db and a `DUMP`/`RESTORE` of every key from
prod's, types and TTLs preserved. Expect roughly 30–60 s and ~1.5 GB, dominated by
`gex_history.db`.

⚠ **It needs `MEMURAI_PASSWORD` in the environment** — see §3. The tool builds
its own Redis clients (`redis_connect_kwargs`), so it does not inherit the
services' `EnvironmentFile`.

Five structural guards, each of which will just refuse:

- **Wrong direction** — it only writes into the checkout it is run from, and
  refuses unless that checkout resolves to `dev`. `--peer` names the SOURCE only;
  there is no flag that makes prod a destination.
- **Same Redis db** — the copy flushes the destination first, so identical
  indices would wipe prod's cache. Prod's index is read from *prod's* profile,
  never assumed to be 0.
- **`--peer` is this same checkout, or is not a checkout** (no `repo_paths.py`).
- **Dev is still up** — dev's services hold DB handles and would write over the
  restore. It names the ports still listening. (Prod's proxy on `:8100` is
  deliberately not probed — a listener there is prod doing its job.)
- **`redis` is missing from this interpreter** — checked *before* the first byte
  is copied, not where it is used, so running under the system python fails
  cleanly instead of after ~1.5 GB has landed.

One thing it deliberately does **not** carry over:

- **`cmd:*`** — the command streams and their `cmd:*:dead` lists. A stream is a
  queue dev would drain and *execute* on startup; a stranded
  `paper_create` or `rescue_apply` would double-open a position.

---

## 5. Turning collection on in dev (rarely)

Dev's schedulers are off. To run them for one session — only when the collectors
themselves are what you are testing:

⚠ **Setting it in your shell does nothing.** That was the `.bat` answer, and it
does not survive the move to systemd: a unit's environment comes from its
`Environment=` and `EnvironmentFile=`, never from the shell that ran
`systemctl`. `_scaffold._schedulers_enabled` reads `os.environ`, so the variable
has to reach the *unit*. Put it in dev's `EnvironmentFile` for the session:

```bash
echo 'TRADING_ENABLE_SCHEDULERS=1' >> /home/administrator/dev/.env && systemctl --user restart trading-dev.target
```

Take it out the same way when you are done — this is a session escape hatch, not
a setting:

```bash
sed -i '/^TRADING_ENABLE_SCHEDULERS=/d' /home/administrator/dev/.env && systemctl --user restart trading-dev.target
```

Confirm which state you are in from the service rather than the file:
`curl -s 127.0.0.1:9215/health` reports `has_scheduler`.

(`systemctl --user set-environment` also works, but it is **manager-wide** — it
would apply to prod's units on their next restart too. Prefer the file, which is
scoped to dev by construction.)

**Why rarely:** this makes dev issue real Schwab calls, through prod's proxy, on
top of prod's own ~68–76k calls/day. Everything else stays suppressed — no Claude,
no notifications, no autonomous trading.

---

## 6. Promotion

**Work is verified running in dev first.** "Tests pass" is not "verified in dev"
for anything with a runtime surface — the DEV chip, the Status-page restart
gating and the old launcher guards were each green in tests and wrong in
practice.

In **dev**: merge to `main` and push.

```bash
cd /home/administrator/dev && git checkout main && git merge --ff-only <branch> && git push
```

⚠ **The VPS can fetch but cannot push** (measured 2026-08-29): the remote is
HTTPS and the box holds no GitHub credential, so `git push` there dies with
`could not read Username for 'https://github.com'`. Anonymous read works, which
is why everything else in this runbook does. Until a deploy key or PAT is
configured, do the push from a workstation that is authenticated:

```bash
git push origin <branch>:main
```

That fast-forwards `main` to the branch commit — identical in effect to the
merge above, and the reason the merge is spelled `--ff-only`: if it would not
fast-forward, the two routes are no longer equivalent and you want to know.

In **prod** — and by this route only:

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

(That directory IS production on the current server; see the note at the top of
section 1. The script checks the checkout's identity, not its folder name.)

`promote.sh` does everything that can refuse or needs the network **before it
stops anything**, so none of these can leave prod down: it refuses in a dev
checkout and on a dirty tree, fetches `origin/main`, exits with "nothing to
promote" when there is nothing new, refuses a `main` that is not a fast-forward,
and — when `requirements.lock` moves — dry-runs the new install.

Then it records the current commit in `logs/promote_previous_commit`, stops the
target, waits for both the units and their **listening sockets** to go
(`is-active` clears about a second early), fast-forwards, reinstalls
dependencies **only if `requirements.lock` moved**, regenerates the units,
starts, and probes **every process** over HTTP — the proxy, the eight services'
`/health` and the web GUI (a dead accept loop stays bound and would pass a TCP
connect). The public screens are probed too, but only warned about.

**If anything fails after the stop, it rolls back by itself**: the previous
commit is restored (and its dependencies, if the lock had moved), the units are
regenerated, the stack is restarted and probed again, and the script exits
non-zero saying `ROLLED BACK`. Check `/status` afterwards either way.

| Command | What it does |
|---|---|
| `tools/promote.sh` | Promote to `origin/main`, or say there is nothing to promote |
| `tools/promote.sh --restart` | Go through the stop/start/probe cycle with nothing new |
| `tools/promote.sh --rollback` | Put back the commit the **last** promote replaced and restart on it. `origin/main` still holds the newer commit, so fix or revert it there before promoting again |

`logs/promote_history.log` keeps one line per run (previous commit, time, mode,
target commit).

⚠ **Never `git pull`, `merge`, `checkout` or `reset` in the prod checkout.**
Every guard above is skipped, and prod is a live trading stack.
`.claude/hooks/guard_prod_promote.py` blocks the mutating verbs mechanically —
it knows the old Windows path fragment, `/home/administrator/prod`, and
`/home/administrator/dev`, the checkout that is production today. A git
worktree under that checkout is not blocked.

If it refuses on a dirty tree, look at the diff — an unexpected edit in the prod
checkout is for a human to decide about, not a restart script.

---

## 7. Known limits

None of these are defects. Each is a way to be surprised.

**1. Dev is quiet at rest, not incapable.** Schedulers are off, so dev polls
nothing on its own. But **command handlers are not gated** — clicking *Run scan*,
*Analyze*, or loading a Calculator chain in dev still reaches Schwab through
prod's shared proxy. That is deliberate (dev needs on-demand fetches to be
usable), but "dev makes no API calls" is only true while nobody is using it.

**2. Dev needs prod's proxy running** for any on-demand fetch, because it borrows
`:8100`. **Nothing waits for it** — `generate_units.py` emits the
`ExecStartPre=tools/wait_http.py` probe only when `OWNS_PROXY` is true, so dev
services have no readiness gate at all. They start cleanly and every fetch fails
until a proxy answers. That is the right behaviour (a dev stack must come up to
read its snapshot whether or not prod is running), but it does mean a missing
proxy is invisible at startup and shows up only as failing fetches.

⚠ This entry claimed the opposite until 2026-08-30 — that each dev service waited
via `ExecStartPre`. It was inherited from the pre-systemd runbook and repeated
through the Linux rewrite without being checked against the generator. Verified
against `deploy/systemd/generate_units.py:140` and against a real dev stack
started with no proxy present at all.

**3. Restarting Redis affects both environments.** One server, two logical DBs.
The Status page hides the Redis restart button in dev for exactly this reason —
if you restart it from prod, dev goes with it.

**4. Dev's own behaviour cannot be verified by the test suite.** Under pytest,
`repo_paths` pins identity *and* topology to prod, so every `IS_DEV=True` branch
is only ever exercised via monkeypatch. Confirming that dev really withholds the
proxy and Redis restart buttons, and shows the `DEV` chip, is a **manual check
with the app running**.

**5. Both halves of the snapshot have now run for real** (2026-08-29, standing
dev up on the VPS). The file half moved **1,542 MB across 14 stores** — the
1.52 GB `gex_history.db` included — in about 13 s, with prod live and writing
throughout. It had never run before that day: the old dev checkout *was* the
former prod, so it already held every database and never needed them copied.

⚠ **The Redis half failed on that first run**, and the failure is the useful
part: the tool builds its own clients, and Redis gained `requirepass` during the
Linux migration, so it died on `AuthenticationError` **after** the 1.5 GB had
landed — the half-applied state the deferred `import redis` check exists to
avoid, arriving through a door that check does not cover. Fixed
(`redis_connect_kwargs`, mirroring `shared/bus/client.py`: unset or empty means
no AUTH). The operational residue is in §3 — a manual run needs the password in
its environment, because only the *units* have an `EnvironmentFile`.

---

## 8. Edge rate limit (public site)

`config/edge.toml` `[live_rate_limit]` limits how many PAGES one visitor may
load on `live.neuralstrike.co` (default 30 a minute, IPv6 grouped by /64).
NiceGUI's own assets, its websocket, `/static` and the favicon do not count.
It ships **off**, because the stock apt Caddy cannot parse the directive: it
needs a build with `github.com/mholt/caddy-ratelimit`.

⚠ **The standing cost, and the reason it is off by default.** A custom binary
gets **no apt security updates**. `apt upgrade` keeps upgrading the packaged
binary, but the diversion below means the one Caddy actually runs stays the
custom one until you download a new build. Check after every Caddy release:
`caddy version` against `apt policy caddy`.

**1. Install the custom build beside the packaged one** (Caddy's own documented
route for custom builds on Debian/Ubuntu; no Go toolchain needed). The build
comes from Caddy's official build server:

```bash
cd /tmp && curl -fL -o caddy.custom "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com%2Fmholt%2Fcaddy-ratelimit"
chmod +x caddy.custom && ./caddy.custom list-modules | grep rate_limit
sudo dpkg-divert --divert /usr/bin/caddy.default --rename /usr/bin/caddy
sudo mv /tmp/caddy.custom /usr/bin/caddy.custom
sudo update-alternatives --install /usr/bin/caddy caddy /usr/bin/caddy.default 10
sudo update-alternatives --install /usr/bin/caddy caddy /usr/bin/caddy.custom 50
sudo systemctl restart caddy && caddy list-modules | grep rate_limit
```

The `list-modules` line must print `http.handlers.rate_limit` before and after.

**2. Turn it on** — Settings → Configuration → Public site rate limit →
*Limit page loads*, which writes `config/local/edge.toml`. Then regenerate and
reload; `caddy validate` failing leaves the running config untouched:

```bash
cd /home/administrator/dev
sudo .venv/bin/python -m deploy.caddy.generate_caddyfile --install
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

**3. Verify from a workstation.** `robots.txt` is limited like a page but
creates no NiceGUI session, so it is the cheap probe. With the default 30 a
minute, expect 30 × 200 and then 429s:

```bash
for i in $(seq 35); do curl -s -o /dev/null -w "%{http_code}\n" https://live.neuralstrike.co/robots.txt; done | sort | uniq -c
```

⚠ Your own browsing from the same address counts too. Loading a screen, then
flipping through all 21 in a minute, is under the default.

**Rollback:** turn the switch off in Settings, regenerate and reload as in
step 2, then `sudo update-alternatives --set caddy /usr/bin/caddy.default &&
sudo systemctl restart caddy`.

### Visitor log (neuralstrike.co and live.)

Caddy writes one JSON line per request to **neuralstrike.co** (and www.) and
**live.neuralstrike.co**: the visitor's IP address, the time, the page, the
answer, the referrer and the browser's own description. Nothing sits in front of
the edge, so the address is the visitor's own. **app.neuralstrike.co is never
logged.** On the live host NiceGUI's assets and live connection are left out, so
a visit reads as its pages.

- Files: `/var/log/caddy/site.log` and `live.log` (0644, readable without sudo).
  A new file starts each day; rolled files are gzipped and deleted after 90 days.
- Settings: `config/edge.toml [access_log]` (Settings → Configuration → Public
  edge limits → Visitor log): on/off, folder, days kept, size of a busy day.
- Applying a change, or turning it on the first time: regenerate and reload, as
  root, exactly as in step 2 above. No rate-limit build is needed for this.
- ⚠ Validate as the `caddy` user (`sudo -u caddy caddy validate ...`), never
  as root: validating creates any missing log file, and a root-owned one makes
  the reload fail with `permission denied` (the sites keep the old config).
  Fix: `sudo chown caddy:caddy /var/log/caddy/*.log`, then reload.

Reading it, on the box from the repo root:

```bash
.venv/bin/python tools/visitor_log.py
```

`--days 7` widens the window (`0` = everything kept), `--host site|live`,
`--ip <address>` lists one visitor's requests, `--requests` one line per request,
`--csv > visitors.csv` for a spreadsheet. Times print in Central.

⚠ The log lives in `/var/log`, outside the checkout, so the nightly backup does
not carry it. IP addresses count as personal data under some laws (the EU's
GDPR among them); the 90-day age-out is why.

### The Chief of Staff host (cos.neuralstrike.co)

The generator also emits `cos.neuralstrike.co`, although no code behind it is in
this repo: `/feed/` is X News Feed (`~/x-news-feed`, user unit `x-news-feed`,
:4300) behind its own login, and everything else is cos-hub (the Chief of Staff
repo's system unit `cos-hub`, :8787), which the phone app and the PC sync with.
It was added by hand on 2026-11-07, and the visitor-log install on 2026-11-18
deleted it, because `--install` rewrites the whole file. Caddy then had no
certificate for the name and the phone app was offline until 2026-11-22.
**Never add a host to `/etc/caddy/Caddyfile` by hand; add it to the generator.**

The feed's login is NOT in git (this repo is public). It is one line in
`shared/feed_basic_auth.txt` in the prod checkout, gitignored and carried by the
nightly backup:

```bash
cd /home/administrator/dev
caddy hash-password          # asks for the password twice, prints a $2a$... hash
nano shared/feed_basic_auth.txt   # one line:  johnf <that hash>
chmod 600 shared/feed_basic_auth.txt
```

To keep the current password, copy the `johnf $2a$...` line out of the live
Caddyfile's `basic_auth` block instead of making a new hash. Without a usable file the generator warns, and `/feed/` answers 503
instead of opening without a login; cos-hub is unaffected. A change to the file
takes effect on the next regenerate and reload, as in step 2 above.

## 9. Gotchas

- **Dev's Terminate stops only dev**, structurally rather than by a filter:
  `systemctl --user --no-block stop trading-dev.target` reaches exactly the units
  `PartOf=` binds to it, and dev has **no proxy unit to begin with** when
  `owns_proxy` is false. Redis survives either way — it is a *system* unit a
  `--user` stop cannot reach even in principle, which is also why the Status
  page's Redis card is read-only in **both** environments.
- **`.sh` files must be LF**, the exact inverse of the rule that used to live here
  for `.bat`. A shell script with CRLF does not mis-parse — it does not run at
  all: the kernel reads `#!/usr/bin/env bash` plus a stray CR as a request for an
  interpreter named `bash\r`, and reports `bad interpreter: ...^M`, naming
  neither the real problem nor the file. `.gitattributes` pins it and
  `tools/tests/test_shell_line_endings.py` guards it — that guard caught
  `promote.sh` the first time it ran, after an edit made on Windows.
- **A port in a unit's `Description=` is a label; `repo_paths` holds the value.**
  Both come from the same generator run, so they cannot disagree unless the units
  are stale — regenerate after any port or path change, which `promote.sh` does
  for you.
- **Reach prod's web GUI at `https://app.neuralstrike.co`** (Caddy + a password
  and TOTP login, since 2026-09-06). `tools/open_webgui.ps1` is the **fallback**
  for when the cert or Caddy breaks. **The Schwab sign-in (every 7 days) is
  renewed in the app:** System Status → **Authorize** opens a dialog, and the web
  app hands the pasted redirect address to the proxy over loopback, so it works
  from any browser signed in to the app. The proxy's own `:8100` `/auth` page is
  deliberately not on the public domain: prod publishes it to the tailnet with
  `tailscale serve --bg --https=8100 http://127.0.0.1:8100`, and
  `proxy_public_url` in `config/env.local.toml` points the dialog's second link
  at that address. ⚠ Without that key the link opens
  `http://127.0.0.1:8100/auth`, which works only through the SSH tunnel — from
  a phone or over `app.neuralstrike.co` it opens the viewer's own device. **Dev is not on the domain at
  all**; forward `:9500`, or reach it over the tailnet. ⚠ Never change either
  bind to `0.0.0.0`: the gate's wall exemption is scoped by a loopback check, so
  a widened bind turns a local carve-out into an open door.
- ⚠ **DEV NEEDS ITS OWN CREDENTIALS, or you cannot log into it.** The gate is
  registered at module scope in `main.py`, so **dev runs it too** — deliberately,
  since a dev that skipped authentication would be taking a code path prod never
  takes, which is exactly what the four suppression flags are careful not to do.
  But `auth_store.DEFAULT_PATH` is `<checkout>/shared/webgui_auth.json` and
  `shared/` is gitignored, so **prod's credentials do not travel to dev and
  `promote.sh` does not carry them**. Run `python tools/webgui_credentials.py
  set-password` and `enroll-totp` **inside the dev checkout** once. Two entries in
  your authenticator is the intended outcome; they can share a password if you
  like, but not a file. Symptom if you skip it: a normal-looking login page on
  `:9500` where every attempt fails — that is default-deny working, not a bug.
- **The Status page's freshness table will look stale in dev**, because dev
  publishes nothing at rest. That is the snapshot ageing, not a broken service.
- **systemd owns the PIDs, so the process archaeology is over.** Two long gotchas
  lived here: batch metacharacter traps (`for /f "usebackq"` eating quotes around
  a path with spaces, `%` eaten inside a `cmd -c`), and the `pythonw.exe` re-exec
  that made every service appear as a PARENT/CHILD PAIR — so a port check
  reported the "wrong" interpreter and a duplicate-launch check had to tell pairs
  apart from real duplicates.

  Both died with `cmd.exe` and `pythonw`. `systemctl --user status <unit>` reports
  one MainPID, `PartOf=` scopes a stop to one environment's units, and
  `systemctl start` on an already-running unit is a no-op rather than a ninth
  process.

  ⚠ One survives in a new form: **`is-active` going inactive is NOT proof the
  ports are free.** Measured 2026-08-29 — the target reports inactive about a
  second before its members' listening sockets close. systemd serialises
  start-behind-stop per unit so it cannot race a `systemctl start`, but anything
  else that binds must wait for the sockets. `tools/promote.sh` waits for both.

---

## 10. Where the behaviour lives

| Concern | File |
|---|---|
| Identity + profile + derived ports | `repo_paths.py` (`ENV_NAME`, `ENV_FLAGS`, `IS_DEV`, `OWNS_PROXY`, `REDIS_DB`, `PEER_ROOT`) |
| Profiles | `config/environments.toml` |
| Marker | `config/env.local.toml` (gitignored), template `config/env.local.example.toml` |
| Notification gate | `shared/notify/channels.py:load_config` |
| Claude gate | `options_svc/compute.py`, `market_svc/compute.py` — the client factories |
| Scheduler gate | `services/_scaffold.py:_schedulers_enabled` / `make_app` |
| Cross-env kill safety | `PartOf=` in the generated units — plus the absence of a dev proxy unit |
| Restart-button safety | `webgui/pages/status.py` |
| Dev chip / tab title | `webgui/main.py` |
| Unit generation | `deploy/systemd/generate_units.py` (nothing under `deploy/systemd/` is committed as a `.service`) |
| Start / stop | `systemctl --user start\|stop trading-{dev,prod}.target`; the GUI's More → Stop All Services runs the same target stop |
| Driving it from Windows | `tools/trading.bat` — `start` / `stop` / `restart [svc]` / `status` / `health` / `logs [svc]` / `tunnel`. Sends systemctl over SSH; supervises nothing |
| Promotion | `tools/promote.sh` + `.claude/hooks/guard_prod_promote.py` |
| Snapshot | `tools/snapshot_from_prod.py` |
| Backups and restore | `tools/backup_local.py`, `tools/restore_backup.py`, `trading-prod-backup.timer`, `tools/pull_backups.ps1` (section 11) |
| The standby and failing over | `tools/standby_*.py`, `deploy/standby/provision.sh` (section 13) |
| Logs | `journalctl --user -u trading-{env}-{svc}`; webgui also writes `logs/webgui.log` |

---

## 11. Backups, and restoring from one

**What runs.** `trading-prod-backup.timer` runs `tools/backup_local.py` on
weeknights. It copies every SQLite store through the online-backup API (the
stack keeps running), dumps Redis, copies the gitignored files a checkout needs
to start (`backup_local.EXTRA_FILES`) and the gitignored data trees
(`DATA_TREES`), then compresses the generation with zstd (`config/backup.toml`,
level 3: about 30% smaller), encrypts it and uploads it to Google Drive as
`<generation>.tar.zst.age`. Archives from before 2026-11-11 are `.tar.age`
(not compressed) and age out of the same rotation.

Old archives are deleted from Drive **past the trash**
(`rclone deletefile --drive-use-trash=false`). A plain delete puts the file in
the Drive trash, which still counts against the quota: by 2026-11-11 the trash
held 53.4 GiB of pruned archives and the upload failed with
`storageQuotaExceeded`. If that error comes back, look at `rclone about gdrive:`
and its `Trashed` line first.

**What is kept.**

| Where | Kept |
|---|---|
| `~/backups/` on the server | the newest 3 generations, plus the newest generation from each of the 4 weeks before those |
| Google Drive (`gdrive:TradingBackups`) | the newest 3 archives, plus one from the week before |
| The workstation (`E:\TradingBackups`) | the newest 5 generations, pulled by `tools/pull_backups.ps1` |

**The workstation pull.** The Windows scheduled task **NeuralStrike backup pull**
runs daily at 02:30 UTC, which is 20:30 Central in winter and 21:30 in summer
(the trigger is pinned to UTC, so it moves with daylight saving time); it runs at
the next logon if the PC was off then. It runs
`C:\Users\john_\NeuralStrike\backup-pull\run_pull.ps1`, which appends each run to
`pull_backups.log` beside it and exits 1 when the pull fails, so the task's Last
Run Result shows a failure. The pull asks the server for ONE zstd-compressed tar
stream of the newest generation over ssh (`-ZstdLevel`, 3 by default), saves it
as `<generation>.tar.zst` beside the destination, unpacks it with Windows' own
`tar` into `<generation>.partial`, and only then verifies every file's size and
every database's `quick_check` and renames it; the `.tar.zst` is deleted once
unpacked. E: still holds plain folders. Until 2026-11-11 it was `scp -r` of 1,200
separate files with no compression. Three things about that folder:

- `pull_backups.ps1` there is a **copy** of `tools/pull_backups.ps1` from `main`.
  Re-copy it when the tool changes. Never point the task at the old `D:\` checkout,
  whose copy is the pre-2026-09-25 version that could delete `_keys`.
- It is not under `AppData\Local`. Files the Claude desktop app writes there are
  redirected into the app's private package folder, and a scheduled task, running
  outside the app, does not see them.
- The task runs `powershell.exe` directly. `conhost --headless` hides the window
  but reports every exit as 0, so a failed pull would look like a success.

**The social cards pull.** A second task, **NeuralStrike social cards pull**, runs
daily at 18:00 (and when the PC is next on if it was off then). It copies every
blog and trade idea card on the public site into
`D:\WebGUI Trading with Schwab\Social Media\` (`Blog\` and `Trade Ideas\`, each card
as `.png`, `.jpg` and a caption `.txt`), and never deletes one there, so it keeps
the trade ideas the site prunes after six days. It runs
`C:\Users\john_\NeuralStrike\social-cards-pull\pull_social_cards.ps1`, a **copy** of
`tools/pull_social_cards.ps1`, beside a copy of `tools/social_cards_export.py`, which
it pipes to the server's Python over one ssh connection to `vps2`. Re-copy both when
either changes. The log is `pull_social_cards.log` beside them; a failed run exits 1.
The same two rules as above hold: not under `AppData\Local`, and `powershell.exe`
directly. The folder is ignored by git through the main checkout's
`.git/info/exclude`, not `.gitignore`, so it exists only on that PC.

A generation whose local part finished with no failure carries a `BACKUP_OK`
file. **The newest generation carrying it is never pruned**, however many failed
nights follow, and pruning happens after the run's result is known.

**What is in it** is decided by two lists in `backup_local.py`, and a test
derives the expected `shared/` set from `.gitignore`, so a new credential file
that is in neither `EXTRA_FILES` nor `NOT_BACKED_UP` fails the suite. The login
store (`shared/webgui_auth.json`) was missing until 2026-10-03: a restore before
that date produced an app nobody could sign in to.

A failed backup now sends a **Server alert** to your phone (User Guide, "Server
alerts" under System Status); before 2026-10-03 the only sign was a missing date.

### Restoring

The drill below has been run by the test suite on every run since 2026-10-03
(`tools/tests/test_restore_backup.py` backs up a checkout with the real tool and
restores it with the real tool). It has **not** been run against a real
production generation: do step 3 into a scratch directory once, when convenient.

**1. Get a generation.** On the server they are already directories under
`~/backups/`. From Drive:

```bash
mkdir -p /tmp/restore && rclone copy gdrive:TradingBackups/prod_2026-11-12_2000.tar.zst.age /tmp/restore/
```

```bash
age -d -i ~/.config/age/backup-key.txt /tmp/restore/prod_2026-11-12_2000.tar.zst.age | zstd -dc | tar xf - -C /tmp/restore
```

An archive from before 2026-11-11 ends `.tar.age` and is not compressed: leave
out `| zstd -dc`.

The private key is the backup. It lives at `~/.config/age/backup-key.txt` on the
server and is escrowed on the workstation; without a copy the archive cannot be
read.

**2. Look first.** Nothing is written:

```bash
.venv/bin/python tools/restore_backup.py /tmp/restore/prod_2026-10-02_2004 --into /home/administrator/dev --dry-run
```

**3. Restore.** Into a live checkout, stop the stack first
(`systemctl --user stop trading-prod.target`). Into a scratch directory, nothing
needs stopping:

```bash
.venv/bin/python tools/restore_backup.py /tmp/restore/prod_2026-10-02_2004 --into /home/administrator/dev
```

A file that already exists is **left alone and listed**. Add `--force` to
replace existing files, which is what a real recovery of damaged stores needs.
Every restored database gets an integrity check; the command exits 1 and names
any that fail. Restored files are set to owner-only (0600).

**4. Redis is not restored by the tool.** The generation holds
`redis/db0.rdb`, a dump of the whole Redis instance. The stack does not need
it: every cache view is rebuilt by its service, and what is lost is short-lived
state (today's flow alerts, the X post log's cache, cooldown maps). To put it
back anyway, as root: stop `redis-server`, replace its dump file (the `dir` and
`dbfilename` in `/etc/redis/redis.conf`, normally `/var/lib/redis/dump.rdb`)
with the backup's, make it owned by `redis`, and start `redis-server`. If
`appendonly yes` is set, turn it off for that start or Redis loads the
append-only file and ignores the dump.

**5. Bring it up.** On a rebuilt box, in order: create the venv and
`pip install -r requirements.lock`, then

```bash
.venv/bin/python -m deploy.systemd.generate_units --install
```

```bash
systemctl --user start trading-prod.target
```

Then sign in to Schwab again on the proxy's `/auth` page if the restored token
is more than 7 days old.

## 12. The site blog

The Blog (`blog_svc`, port 8217, and the **More → Blog** page) needs **one**
operator step after the promote that carries it. Everything else arrives with
the promote: the unit for `blog_svc` is generated from `repo_paths` like every
other service's, and its data folder and the site's `blog/` folder are created
when they are first written.

**The step: regenerate and reload Caddy.** The Caddyfile is generated, and the
promote does not regenerate it. Until this is done Caddy does not send the
policy header on an entry's document.

**Before the first Publish.** Do this step first. Publishing works without it,
but until the reload an entry's document is served without its policy header,
which is the one layer that still applies when a visitor opens the document
outside its frame.

Open a session on the box (`ssh vps2`)
and run these, one line at a time; `sudo` may ask for your password, and
`caddy validate` failing leaves the running config untouched:

```bash
cd /home/administrator/dev && sudo .venv/bin/python -m deploy.caddy.generate_caddyfile --install
```

```bash
sudo -u caddy caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

**What works before the step is done.** The Blog page, uploads, previews,
Publish and Unpublish all work, and published entries are served. Each entry is
still cleaned before it is stored, and its page still frames it with the
`sandbox` attribute that grants no scripts. Three things are missing until the
reload:

- the `Content-Security-Policy` header on `/blog/<address>/entry.html`, which
  is the one layer that still applies when a visitor opens the entry's document
  directly, outside its frame;
- the revalidation rule for an entry's page at `/blog/<address>/`, so a
  returning visitor's browser may show that page from its own cache after a
  later promote changes the site menu;
- the redirect from `neuralstrike.co/blog` (and `/blog/`) to the Blog's list at
  `/blog.html`. Until the reload both of those addresses are a 404; the Blog
  item in the site menu links to `blog.html` and works either way.

**How to check it.** Publish an entry first (the header is sent on an entry's
document, so one has to exist), then, in the same session on the box:

```bash
curl -sI https://neuralstrike.co/blog/my-entry/entry.html
```

Replace `my-entry` with the entry's address, as the Blog page prints it. The
answer must include a `Content-Security-Policy` line (curl prints the name in
lower case over HTTP/2) beginning
`default-src 'none'` and ending
`sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox`. From
Windows PowerShell the same check is `curl.exe -sI` followed by the address:
plain `curl` there is a different command.

The entry's own page must **not** carry that header: it is the site's markup,
and the policy would switch off its stylesheet and menu.

```bash
curl -sI https://neuralstrike.co/blog/my-entry/
```

That answer should show `cache-control: no-cache` and no
`content-security-policy` line.

The address people type must reach the list. This one needs no entry to exist:

```bash
curl -sI https://neuralstrike.co/blog
```

The answer should be a `308` with `location: /blog.html`, and the same for
`https://neuralstrike.co/blog/`.

**Not yet checked on a live Caddy.** The generated Caddyfile is tested as text.
The three checks above are the first proof that Caddy sends what the file says.

**Where the policy lives.** `shared/blog_inbox.py` (`ENTRY_CSP`,
`ENTRY_SANDBOX`). The Caddyfile generator imports the string; change it there and
regenerate, never in `/etc/caddy/Caddyfile`. Detail:
[reference/blog.md](reference/blog.md).

**The first upload also copies typefaces from Google Fonts**, which no test
could do. What to look at on that first draft is step 5 of Task 12 in
[the plan](plans/2026-10-06-site-blog-plan.md).

**Rolling back.** `tools/promote.sh --rollback` to a commit from BEFORE the blog
leaves three things behind that the older commit has never heard of:

- `services/blog_svc/data/` (the drafts, the entries and their typefaces);
- `deploy/site/blog/`;
- `deploy/site/blog.json`.

At that commit they are not in `.gitignore`, so git reports them as untracked,
the tree counts as dirty, and the next `tools/promote.sh` refuses to run. Move
them aside first, out of the checkout, one line at a time:

```bash
mkdir -p /home/administrator/blog-aside
```

```bash
mv /home/administrator/dev/services/blog_svc/data /home/administrator/blog-aside/data
```

```bash
mv /home/administrator/dev/deploy/site/blog /home/administrator/dev/deploy/site/blog.json /home/administrator/blog-aside/
```

⚠ **Do not delete `services/blog_svc/data/`.** It is the only copy of the drafts
and the published entries apart from the nightly backup. The other two are
rebuilt from it. Until the site folder is moved aside the published entries stay
reachable on the site, because Caddy serves the files that are there.

To bring the blog back, promote a commit that has it FIRST (moving the folder
back before the promote would dirty the tree again). The service then starts
with an empty store of its own. Put the kept one in its place, one line at a
time, and the service rebuilds `deploy/site/blog/` and `blog.json` as it starts:

```bash
systemctl --user stop trading-prod-blog_svc
```

```bash
mv /home/administrator/dev/services/blog_svc/data /home/administrator/blog-aside/data-empty
```

```bash
mv /home/administrator/blog-aside/data /home/administrator/dev/services/blog_svc/data
```

```bash
systemctl --user start trading-prod-blog_svc
```

---

## 13. The standby and failing over

A second box on a different provider (today **vps3**, RackNerd, tailnet
`trading-standby`) holds a **warm standby** of the stack. It exists because the
first server was suspended by its provider on 2026-08-30 and everything was dark
until a replacement was restored by hand. Design and reasons:
[the warm standby design](plans/2026-11-22-warm-standby-design.md).

**What it is, in one paragraph.** Every night the standby pulls the newest
complete backup generation from prod and the commit prod was running, restores
both into a ready, **stopped** checkout at `/home/administrator/dev` (the same
path as prod), and checks every database. Nothing of the stack runs on it. If
prod fails, the operator runs **one command** on the standby and changes the
addresses at Wix. A failed or silent standby reaches the phone as a Server alert.
Failover is manual on purpose: a box that wrongly decides the other is dead
starts a second proxy and breaks the Schwab sign-in.

**The roles are symmetric.** Neither box is "prod for good". `config/standby.toml`
holds both boxes' ssh targets, public IP and proxy health URL, plus the nightly
sync's settings. Which box THIS is lives in the **identity file**
`~/.config/neuralstrike/box.toml` (`self`, `role`, `taking_over`), outside the
checkout and outside every backup, so a git checkout or a restore can never carry
it from one box to the other. Never put it under `config/local/`: that folder is
swept into every backup. `config/local/standby.toml` is deliberately **not**
restored onto the standby (the sync names it as skipped): each box keeps its own
guard settings, so a standby setting changed in Settings → Configuration on one
box must be set on the other by hand.

### What runs where

| | prod (vps2 today) | standby (vps3 today) |
|---|---|---|
| Identity | `role = "prod"` | `role = "standby"` |
| Stack (`trading-prod.target`) | running | **not installed** |
| `standby-sync.timer` | absent | **22:15 CT** daily, `Persistent=true` |
| `trading-prod-standby-watch.timer` | **07:40 CT** daily | absent |
| Start fence | on every stack service | nothing to fence |
| Redis | the stack's | installed and empty |
| Caddy | serving | installed, **disabled** |

### The identity file

```toml
self = "vps3"            # the box's name in config/standby.toml [boxes.*]
role = "standby"         # "prod" or "standby"
taking_over = false      # true only between a takeover and the first time DNS shows this box
```

`taking_over` is cleared by whichever sees the public name point at this box
first: the fence at a start, or prod's 07:40 watch (below). Until then the watch
alerts every morning, because a marker left set would let this box start a
second stack after a later failover the other way.

Written by `provision.sh` (standby) and by `standby_failover.py takeover` /
`become-standby`; edit it by hand only to recover a failed takeover. A box with
**no** identity file is unconfigured: the fence allows it, the sync and the watch
do nothing. That is how the fence reaches vps2 through an ordinary promote before
vps2 has an identity.

### The start fence

`trading-prod-fence.service` is a oneshot that **every** prod stack service
requires (`generate_units.render_all` adds `Requires=` and `After=` to each
`.service`, so a new unit is fenced without anyone remembering to). It refuses
only on positive evidence, in three cases: the role is not `prod`; the **peer's
proxy answers** its `proxy_health` URL with an HTTP status (a gateway error, 502/503/504,
is `tailscale serve` with no proxy behind it and does not count) and this box is not
marked `taking_over` (*"the peer's proxy answers; this box is not prod"*); or the
public app name (`app.` host) resolves to the **peer's** public IP and this box is
not marked `taking_over`. Any doubt allows the start (no identity, an unreadable
file, a failed lookup, a peer probe that cannot connect or times out, even a
module that fails to import), because a fence that wrongly refuses takes live
trading down. The two neighbour apps (below) get the same fence as a drop-in.

Its limits are `config/standby.toml [fence]`: a failed DNS lookup is retried for
`lookup_retry_s` (12 s), and the peer probe waits `peer_probe_timeout_s` (5 s).
The unit allows 30 s in all and a fence that runs out of time **fails and holds
the stack**, so the loader refuses values past `FENCE_BOUNDS` (at most 15 + 8 s).

Things to know when reading it:

- It prints one line to the journal:
  `journalctl --user -u trading-prod-fence.service`. When checking a returned
  old prod, read that line. **"lookup failed; not evidence" means the DNS rule
  did not decide.** User units cannot order after `network-online`, so at boot
  the lookup can fail, even after its retries, and the fence then allows the
  start. The role rule and the peer-proxy rule still hold; the DNS rule is one
  line of several.
- The peer-proxy rule fires only while the peer runs a proxy behind
  `tailscale serve`, which only a takeover sets up. Today's standby runs none, so
  this rule never holds today's prod.
- **Restarting the fence restarts the whole stack**: every stack service
  `Requires=` it, so `systemctl --user restart trading-prod-fence.service` stops
  and starts all of them. To see what it would decide, run it by hand instead (it
  changes nothing unless `taking_over` is true):
  `.venv/bin/python tools/standby_fence.py`.
- The DNS rule can never fire if the `app` A record is behind a CDN or proxy
  (the name would not resolve to either box's IP). It must be a plain A record.
- **A fence that passed stays passed** until the next reboot or an explicit
  `systemctl --user stop trading-prod-fence.service` (which also stops every
  service that requires it). Changing the identity does not re-run it.
- "no box identity" in its status is the expected state on a box that has not
  been configured.

### The nightly sync (`tools/standby_sync.py`, on the standby)

At 22:15 CT (after the 20:00 weeknight backup and the workstation pull). Each run:

1. Refuses if the box is promoted: the identity says `prod`, or
   `trading-prod.target` is installed.
2. Lists prod's `~/backups` through the read-only key (tailnet first, then the
   public address) and takes the newest generation that carries **both**
   `BACKUP_OK` and `GIT_HEAD`. On weekends and holidays nothing is newer: it
   records "unchanged".
3. Copies it with `rsync` into `~/standby/generations/<name>.partial`, with the
   previous generation as the basis so only changed bytes move.
4. Checks it. It **refuses**: any file outside the paths `backup_local.py`
   produces (a planted script fails the night); a main store that shrank more than
   `max_shrink_pct` (50) against the previous generation (`paper_account.db`,
   `signals.db`, `gex_history.db`); a commit that is not on `origin/main`; a
   generation **older than the last one synced** (it never goes backwards); a
   neighbour file not in that neighbour's `files` list; and a neighbour
   `venv-freeze.txt` line that is not a plain `name==version` pin (exactly one
   `-e file://~/<its own folder>` line is accepted, and is installed only at
   takeover). Neighbour packages install as **wheels only**.
5. Moves the checkout to the commit (`git fetch`, `checkout --detach`;
   `pip install -r requirements.lock` if the lock changed) and restores the data
   with `tools/restore_backup.py`, which integrity-checks every database.
6. Writes `SYNCED` into the generation folder **last** ("unchanged" needs it),
   keeps 3 generations, and writes `~/standby/status.json` (`result` is `synced`,
   `unchanged`, `failed` or `refused`).

Redis is **not** restored: the stack rebuilds every cache view by itself. A failed
night exits non-zero and the unit's `OnFailure=` sends a Server alert.
`max_age_h` (80, which covers a weekend) is the age past which the sync fails and
prod's morning watch alerts. **A takeover never refuses on stale data**: it prints
the data's age loudly and goes on, because old data beats none.

### The watch (`tools/standby_watch.py`, on prod, 07:40 CT)

A switched-off, suspended or stalled standby sends nothing itself, so prod reads
the standby's `status.json` through a read-only ssh key and sends a Server alert
(*The standby is not in step with prod*) when the first of these is true:
**standby did not answer**, **standby status unreadable**, **last run failed** or
**refused** (with the reason), **the standby has not run since <time>** (its
`status.json` is older than `[watch] status_max_age_h`, 26 h: the sync itself has
stopped), or **last good sync N h ago**. If the standby's
addresses in `config/standby.toml` are unusable, a second alert, *The standby
check is unusable*, says so. On a box still marked `taking_over` it first looks
the public name up: when it points here it clears the marker (*"takeover marker
cleared"* in its journal), and when it does not it sends *This box is still marked
as taking over* every morning until the Wix records move. The watch always exits 0
(a failed alert is printed, never a second failure) and does nothing on a box that
is not prod.

### Setting a standby up (once, with the operator)

Everything root-owned is the operator's; nothing here needs passwordless sudo.

1. **Key on prod.** On prod (vps2) run exactly this (no passphrase: the watch
   runs with `BatchMode=yes`, so a passphrase would make every morning alert
   "standby did not answer"):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/standby_watch -N '' -C standby-watch@vps2
   ```

   The contents of `~/.ssh/standby_watch.pub` are the second argument to
   `provision.sh` in step 2.
2. **Provision the standby.** Copy `deploy/standby/provision.sh` from prod's
   checkout to the standby and run it as `administrator`, not under sudo (it
   prompts for sudo itself, and a second run changes nothing):

   ```bash
   bash provision.sh vps3 "ssh-ed25519 AAAA... standby-watch@vps2" "# public key: age1..."
   ```

   It installs the packages, uv and the Claude Code CLI, the checkout and its
   venv, the pull key `~/.ssh/standby_pull`, the status-read `authorized_keys`
   line, **the peer's ssh host keys** in `~/.ssh/known_hosts`, the age **public**
   line (the standby never holds the private key), the identity file
   (`role = "standby"`), and the one unit pair `standby-sync.service` + `.timer`.
   It installs **no** stack unit and leaves Caddy disabled.

   The host keys matter because both the pull and the watch run ssh with
   `BatchMode=yes`: an unknown host key is "Host key verification failed", a
   failed night, never a prompt. provision runs `ssh-keyscan -t ed25519` for each
   of the peer's `ssh` targets in `config/standby.toml` and prints each
   fingerprint. **Compare every one** with the peer's own, run on the peer:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

   On a mismatch do not go on: remove the entry (`ssh-keygen -R <target>`) and
   find out why. A target that did not answer is reported with `!!`; add it the
   same way once it does.
3. **The things it prints**, which are yours to do:
   - the `authorized_keys` line to add **on prod**, which lets the standby read
     `~/backups` and nothing else:
     `restrict,command="/usr/bin/rrsync -ro /home/administrator/backups" ssh-ed25519 ... standby-pull@vps3`;
   - **on prod**, the standby's host keys for the morning watch (its two
     targets, `trading-standby` and `192.227.249.22` today). Compare the printed
     fingerprints with the standby's own `ssh-keygen -lf
     /etc/ssh/ssh_host_ed25519_key.pub` before the last two lines:

     ```bash
     touch ~/.ssh/known_hosts
     ssh-keyscan -T 10 -t ed25519 trading-standby 192.227.249.22 2>/dev/null > ~/standby-hostkeys.txt
     ssh-keygen -lf ~/standby-hostkeys.txt
     [ -s ~/.ssh/known_hosts ] && [ -n "$(tail -c1 ~/.ssh/known_hosts)" ] && echo >> ~/.ssh/known_hosts
     grep -vxFf ~/.ssh/known_hosts ~/standby-hostkeys.txt >> ~/.ssh/known_hosts; rm ~/standby-hostkeys.txt
     ```
   - the copy of prod's `~/.config/rclone/rclone.conf` to the standby (the Drive
     login a promoted box needs to write its own backups), mode 600;
   - **after the first good sync**, the Redis block: the stack's `requirepass`,
     the read-only `live` ACL user and the `cos` user, copied from prod's
     `ACL GETUSER` with passwords through a pipe, never an argument, then
     `CONFIG REWRITE`.
4. **Identity on prod.** Write `~/.config/neuralstrike/box.toml` with
   `self = "vps2"`, `role = "prod"` and `taking_over = false`. Then check what
   the fence would decide with a **dry run** on prod, which starts and stops
   nothing and, with `taking_over = false`, writes nothing:

   ```bash
   cd /home/administrator/dev && .venv/bin/python tools/standby_fence.py
   ```

   It must print exactly `fence: allow - public name points at this box`. Any
   other `allow` line is not good enough: `lookup failed; not evidence`,
   `unknown address; not evidence` and `an address is unknown` all mean the DNS
   rule could not decide, so fix the cause (the `app` A record, `public_ip` in
   `config/standby.toml`) and run it again. A `REFUSE` line means the next start
   of the stack would be held: remove the identity file until it is understood.
   **Do not `restart` the fence unit to check it**: every stack service requires
   it, so a restart stops and starts the whole live stack. Then run on prod
   `.venv/bin/python tools/standby_failover.py fence-neighbours` and check
   `systemctl --user show cos-hub.service -p Requires` lists the fence.
5. **Run the first sync by hand**: `systemctl --user start standby-sync.service`,
   then `journalctl --user -u standby-sync -n 50`. The failure notifier needs the
   restored `.env`, so a failure **before the first good sync does not push**:
   watch this one yourself. Read `~/standby/status.json` (`"result": "synced"`).
6. **On the first live night**, check by hand that `rsync --list-only` through
   `rrsync -ro` really lists the generations, and that a missing file exits
   **23**: the sync's parsing of both was tested only against fakes. Also check
   the standby's checkout is at prod's commit, every database reports ok, and
   `~/cos-hub` and `~/neuralstrike-reports` exist.
7. **Run the drill** (below), then `.venv/bin/python tools/standby_watch.py` on
   prod: it should print `standby healthy`.
8. **Lower the TTL** of the four Wix A records (`@`, `app`, `live`, `cos`; `www`
   is a CNAME) to the lowest Wix allows, so the DNS step of a failover is short.
   Record the value here once it is set: **TTL: not yet set.**

### The drill (monthly, on the standby)

```bash
cd /home/administrator/dev && .venv/bin/python tools/standby_failover.py drill
```

Runs the restored stack under the **dev profile**: ports +1000, Redis db 1, no
proxy of its own, and schedulers, Claude and notifications off, so it touches
neither Schwab nor prod and nothing leaves the box. It probes every component and
then **always** restores the standby: the dev units are removed and
`config/env.local.toml` is put back. If a stop fails the files are **kept** and
the exact stop command is printed: run it, delete the files it names, and run `systemctl --user daemon-reload`. It refuses
on a promoted box. Run it once after setup and about monthly.

### Failing over (prod has failed)

Decide first that prod is really down: **do not fail over a box that is still
serving.** On the standby:

```bash
cd /home/administrator/dev && .venv/bin/python tools/standby_failover.py takeover
```

It **refuses while any of three probes answers**: the peer's proxy health over the
tailnet, the peer's public app (the `app.` host sent to the peer's IP, so it hits
the peer however DNS points) and the peer's public ssh port. A broken tailnet
alone cannot let a failover through. If the peer is half-alive and cannot be
stopped, decide that and pass `--peer-is-down`. It also refuses without an
identity file, when the box is already prod, and when the checkout is not the
prod profile.

Then it prints the data's age and the last sync result (loudly if older than
`max_age_h`), and:

1. stops `standby-sync.service` if it is running (and **refuses**, changing
   nothing, if that stop fails: a sync would move the checkout under the new
   stack), removes the standby sync units and writes the identity as `prod` with
   `taking_over = true` **before** anything starts (the fence reads it);
2. points `proxy_public_url` in `config/env.local.toml` at this box's tailnet name;
3. installs the neighbour units, fenced, and each neighbour's editable install;
4. runs `generate_units --install`, enables and starts `trading-prod.target` and
   the neighbours (cos-hub, the report timers), and probes every component's
   health.

A step that fails after the identity write stops the run, prints **DONE** and
**NOT DONE**, exits 1, and does **not** roll the identity back. Finish the NOT
DONE steps by hand, or set `role = "standby"` and run it again.

**The steps only you can do, which it prints in order:**

1. `sudo ufw allow 80,443/tcp`
2. `sudo tailscale serve --bg --https=8100 http://127.0.0.1:8100` (the proxy's
   port from `config/ports.toml`, 8100 today, so the peer's probes and the tailnet
   Authorize link reach this box's proxy). From then on a returning old prod's
   fence sees this proxy answer and holds its stack.
3. `sudo .venv/bin/python -m deploy.caddy.generate_caddyfile --install`, then
   enable and start Caddy (certificates arrive once the new A records resolve)
4. At Wix, point `@`, `app`, `live` and `cos` at this box's public IP
5. Sign in to Schwab again (Status → Authorize) if the restored sign-in is past
   its 7 days
6. Sign Claude Code in (`claude`, then `/login`): the market reports need it, and
   its login is deliberately not copied
7. Re-point the two workstation tasks (the backup pull and the social-cards pull)
   at this box
8. When the old prod comes back, run `become-standby` on it **before** it starts
   anything

The data is as of the last synced backup (a weeknight 20:00 generation): a
mid-session failover loses that day's minute gamma history, paper-book changes and
flow alerts. Redis is rebuilt by the stack. The neighbours' code came from the
backup, not from GitHub (neither is a git repo), the one place a compromised old
prod could influence what the new one runs.

### A returned old prod

If the box that lost the role boots again, its identity file still says `prod`, so
the role rule does not hold it. Two rules do: the **peer-proxy rule** (the new
prod's proxy answers over the tailnet once its `tailscale serve` step is done,
which holds the stack even before the Wix records move and even when the boot-time
lookup fails), and the DNS rule (the `app.` name resolves to the peer once Wix has
moved). Read the fence's journal line before trusting it (see "lookup failed"
above): an old prod that comes back before the new prod's `tailscale serve` and
before Wix has moved has neither rule to stop it. To turn it into the standby run
on it:

```bash
cd /home/administrator/dev && .venv/bin/python tools/standby_failover.py become-standby
```

It needs **positive** evidence that the public name no longer points at this box
(a failed lookup refuses, unlike the fence). It then fences the neighbours first,
takes one local backup of its own data kept under `backups/predemote/` (never
pruned by the rotation: the only copy of the day it went down; whether any of it is
merged back is a manual decision), stops and removes the `trading-prod-*` units
and timers, disables the target and the neighbour services, sets
`role = "standby"` and installs the standby sync units. Its next 22:15 sync
replaces its data with the new prod's. Then exchange the keys (next section).
Moving prod back later, while the current prod is alive, is a **planned move**
(below), not this procedure.

### Keys after a role swap

After `become-standby` the boxes have swapped roles, so the keys go the other way
round: the new standby pulls from the new prod, and the new prod reads the new
standby's status. `<new-prod>` / `<new-standby>` below are the box names in
`config/standby.toml`; `<standby targets>` are the new standby's `ssh` entries
there (for vps2 today: `trading-prod-2 63.141.255.25`).

1. **On the new prod**, the watch key (skip if `~/.ssh/standby_watch` exists):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/standby_watch -N '' -C standby-watch@<new-prod>
   cat ~/.ssh/standby_watch.pub
   ```

2. **On the new standby** (the demoted box), provision with that public half and
   prod's age public line (the `# public key: age1...` line of
   `~/.config/age/backup-key.txt` on the new prod, the public line only). It
   creates `~/.ssh/standby_pull`, adds the status-read line, seeds the new prod's
   host keys in `known_hosts` (compare the fingerprints as in setup step 2), and
   leaves the existing identity alone:

   ```bash
   cd /home/administrator/dev && bash deploy/standby/provision.sh <new-standby> "ssh-ed25519 AAAA... standby-watch@<new-prod>" "# public key: age1..."
   ```

   The demoted box holds the old private age key in that file: provision then
   leaves it untouched, which is fine (the standby only ever needs the public
   line).
3. **On the new prod**, add the `rrsync` line provision printed (step 1 of its
   block) to `~/.ssh/authorized_keys`, and seed the new standby's host keys for the
   watch, comparing the fingerprints with the new standby's own
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` before the last two lines:

   ```bash
   touch ~/.ssh/known_hosts
   ssh-keyscan -T 10 -t ed25519 <standby targets> 2>/dev/null > ~/standby-hostkeys.txt
   ssh-keygen -lf ~/standby-hostkeys.txt
   [ -s ~/.ssh/known_hosts ] && [ -n "$(tail -c1 ~/.ssh/known_hosts)" ] && echo >> ~/.ssh/known_hosts
   grep -vxFf ~/.ssh/known_hosts ~/standby-hostkeys.txt >> ~/.ssh/known_hosts; rm ~/standby-hostkeys.txt
   ```

4. Run the first sync by hand and the watch, as in setup steps 5 and 7.

### Moving prod on purpose (a planned move)

Both boxes are healthy and prod should move, for maintenance or to move back.
`takeover` refuses while the current prod answers, and `become-standby` refuses
while DNS still points at it, so the old prod is first **stood down**: stopped,
with its role kept. In this order:

1. **On the current prod**, outside market hours:

   ```bash
   cd /home/administrator/dev && .venv/bin/python tools/standby_failover.py stand-down
   ```

   It disables every `trading-prod-*` and neighbour timer, disables the
   neighbour services, stops their template instances by pattern, stops and
   disables `trading-prod.target`, stops the stack's services and finally the
   fence. It refuses on a box whose role is not prod, and it does **not** change
   the role. Caddy (a system unit) keeps answering with an error page until Wix
   moves.
2. *(To carry today's data)* still on the current prod, take a backup with the
   stack stopped, then on the other box pull it:

   ```bash
   cd /home/administrator/dev && .venv/bin/python tools/backup_local.py
   ```

   ```bash
   systemctl --user start standby-sync.service && cat ~/standby/status.json
   ```

3. **On the other box**: `takeover --peer-is-down` (the stood-down box still
   answers ssh and its Caddy, so the flag is needed), and the steps it prints.
4. At Wix, point the four A records at the new prod.
5. **On the old prod**, once `getent hosts app.neuralstrike.co` shows the new
   prod: `become-standby`, then the keys (above).

### After a takeover: things to know

- **The new prod's checkout is on a detached HEAD** (the nightly sync checks a
  commit out with `--detach`). `tools/promote.sh` works on it as it is: it
  fetches `origin/main`, fast-forwards with `git merge --ff-only` and rolls back
  with `git reset --hard`, all of which move a detached HEAD. To put the checkout
  back on `main` without changing a file (the commit is on `origin/main`, the
  sync refuses any other):

  ```bash
  cd /home/administrator/dev && git switch -C main HEAD && git branch -u origin/main
  ```

- **A promote of this branch that rolls back** leaves the new watch timer armed:
  the rollback re-renders the older units but deletes nothing. Disable it by
  hand:

  ```bash
  systemctl --user disable --now trading-prod-standby-watch.timer
  rm -f ~/.config/systemd/user/trading-prod-standby-watch.timer ~/.config/systemd/user/trading-prod-standby-watch.service
  systemctl --user daemon-reload
  ```

- **Aborting a half-finished takeover** (it failed after the identity write, and
  the peer is to stay prod after all). On the box that was taking over:

  ```bash
  cd /home/administrator/dev
  systemctl --user stop trading-prod.target
  systemctl --user disable trading-prod.target
  .venv/bin/python -c "from shared.standby_config import Identity, read_identity, write_identity; i = read_identity(); write_identity(Identity(i.self_name, 'standby', False))"
  ls ~/.config/systemd/user/ | grep '^trading-prod'
  ```

  Stop and disable any neighbour unit it started (`cos-hub.service`, the
  `neuralstrike-report-*.timer`s) and delete the neighbour unit files and `.d`
  folders it copied in (the names kept under `~/standby/units/`; a standby keeps
  them there, uninstalled). Then delete every `trading-prod*` file the `ls`
  listed **except** `trading-prod-notify-failure@.service` (the standby keeps
  that one): the sync refuses while `trading-prod.target` is installed. Put the
  standby's units back and check the sync accepts the box again:

  ```bash
  systemctl --user daemon-reload
  .venv/bin/python -m deploy.systemd.standby_units --install
  systemctl --user start standby-sync.service && cat ~/standby/status.json
  ```

  If `proxy_public_url` in `config/env.local.toml` was changed, the next sync's
  restore puts prod's copy back. If `sudo tailscale serve` or Caddy were already
  set up, undo them too (`sudo tailscale serve reset`;
  `sudo systemctl disable --now caddy`).

### The two neighbour apps

`cos-hub` (`cos.neuralstrike.co`) and the market reports
(`~/neuralstrike-reports`) are not in this repo; `[[neighbours]]` in
`config/standby.toml` lists them. `backup_local.py` carries them in the nightly
generation under `neighbours/`, the standby restores them to the same paths and
keeps their unit files in `~/standby/units/` **uninstalled**, and a takeover
installs, fences and starts them. A third neighbour is a config entry.
`fence-neighbours` puts the start fence on their service units (a drop-in; a
template's drop-in covers its instances) so a returned old prod cannot start a
second hub or a second set of reports.

### Where the behaviour lives

| Concern | File |
|---|---|
| Settings, boxes, neighbours, the identity | `config/standby.toml`, `shared/standby_config.py`, `~/.config/neuralstrike/box.toml` |
| Nightly sync | `tools/standby_sync.py`, `deploy/systemd/standby_units.py` |
| Morning watch | `tools/standby_watch.py`, `[slots.standby_watch]` in `config/sessions.toml` |
| The fence | `tools/standby_fence.py`, `generate_units._fence_unit` / `_with_fence` |
| Takeover, drill, demotion, stand-down, neighbour fences | `tools/standby_failover.py` |
| One-time provisioning | `deploy/standby/provision.sh` |
