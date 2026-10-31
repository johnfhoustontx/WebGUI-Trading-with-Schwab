# The Gamma Archive

**Why.** `purge_keep_sessions` deletes every per-strike grid older than 5 sessions,
and that is right for the live store: the Gamma page needs a few days, and the file
would grow about 250 MB a day. But any study that needs months of strike-level
history can never run, and a deleted session cannot be recovered. The E: backups
rotate, so they do not extend it.

**What.** A second store, `options-scanner/gex_archive.db`, kept forever and never
purged. A nightly oneshot (`tools/archive_gex.py`, `[slots.gex_archive]`, 17:00 CT)
copies each closed session it does not yet hold, thinned by `config/gex_archive.toml`.
It reads `gex_history.db` read-only and calls no proxy, Redis or Claude.

## Measured on the 2026-10-29 session (137 symbols, prod backup)

| Kept | Grid MB per session |
|---|---|
| Everything, every minute | 274 |
| 5 minutes, spot +-25% | 41.7 |
| **5 minutes, spot +-15%** (shipped) | **32.7** |
| 5 minutes, spot +-10% | 25.3 |
| 15 minutes, spot +-15% | 11.2 |

With the summary columns, the term table and the indexes, a real run over nine stored
sessions (2026-10-20 to 10-30) made a 487 MB file: **54 MB a session, about 14 GB a
year**. Of its 656,362 rows only 1,224 were carried. Prod had 105 GB free on
2026-10-31. The store joins the nightly backup by itself: `db_paths` takes every
`*.db`.

## Decisions

- **The same table shapes as `gex_history.db`** (`snapshots`, `gex_term_snapshots`,
  the same `gex_json` encoding), so `gex_history_db`'s readers and `_decode_grid`
  work on it unchanged. A `archived_sessions` row records what thinning produced each
  session and the commit that ran.
- **Each 5-minute bucket keeps its latest FETCHED row**, and a carried row only when
  the bucket has no fetched one. 155,314 of that session's 332,134 rows were carried
  (modelled Greeks, repeated volume). Sampling on the clock minute would have stored
  mostly those; `carried_age_sec` still marks any that remain.
- **A row's own spot centres its band.** A row with no usable spot keeps its whole
  grid: wider beats wrong. Walls, flip and net total are their own columns, computed
  from the full chain, so the crop does not change them.
- **The term table (per-expiry, $SPX only) is thinned to 15 minutes**, with the same
  band around its underlying price.
- **One transaction per session, the marker inside it**, so a killed run leaves
  nothing half archived and the next run redoes that session. A session is taken only
  once closed: before today, or today after `[windows.collection] stop`.
- **A failure stops the run and exits non-zero**, so the unit ends FAILED and pushes.
  The live store keeps 5 sessions, which gives about four days to fix it.
- **A systemd timer, not a service branch.** A read of 330,000 rows belongs nowhere
  near the collector, and a change here needs no options service restart.

## Left out on purpose

- `hiro_minutes` and `flow_contract_days` keep 20 sessions and are not archived.
  `flow_contract_days` rows are also updated the next day, so a nightly copy would
  need care.
- Per-expiry IV and the volatility smile are not in the store at all: that is the
  separate daily logger.
- Nothing reads the archive yet. `--db` lets studies and the backfill point at a copy.
