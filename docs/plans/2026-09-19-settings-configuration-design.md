# Settings → Configuration — design (2026-09-19)

## Ask

"Under the setting menu … a configuration section where all the config items can
be tweaked … suitably categorized … as user friendly and self explanatory as
possible", plus a standing rule that hard-coded items become configurable.

## Decisions

**1. Overrides, not edits.** The page never writes a tracked `config/*.toml`. It
writes `config/local/<name>.toml` (gitignored) holding only the values that differ
from the shipped file. Reason: prod is a git checkout and `tools/promote.sh` refuses
a dirty tree, so editing a tracked file from the app would block every later
promote. The Appearance editor had this bug (it rewrote `theme.toml`) and was moved
onto the same layer. Consequences: app updates never clobber a tuned value, Reset
is "delete the override", and the shipped value is always known.

**2. One loader seam.** `shared/config_toml.read_layered` / `layered_mtime` /
`read_overrides` / `write_overrides`. Every reader goes through it: the
`toml_loader` factory (scanner, trade_mgmt, driver, symbols, sectors), the two older
hand-rolled loaders (`market_calendar`, `flow_alerts`), both commission modules and
`theme.load_theme`. The overlay is ignored under pytest unless a test opts in, so
the suite still runs against the shipped values on a tuned checkout.

**3. Organised by purpose, described once.** `webgui/config_schema.py` is the
catalogue: category (one per file), sections, and per key a label, a help
sentence, kind, unit, bounds and restart targets. Fractions are typed as percents.
Wildcard entries cover open-ended tables (named schedule slots, Net Prem groups, the
sector map). Coverage is enforced: every TOML key must have an entry, every
required entry must exist, and every shipped value must round-trip through its own
field's parser — which catches a wrong kind or bound before it reaches the screen.

**4. Apply = restart, offered not forced.** Most consumers bind config at import, so
Save lists the affected units (`restart_for`: field → section → file) and offers a
restart through the Status page's `systemctl --user` mechanism; `timers` means
`generate_units --install` for the three systemd-timer slots. During RTH or the
collection window the dialog warns that restarting options_svc costs GEX minutes.
"Later" leaves a banner.

**5. Read-only where editing is unsafe.** `ports.toml` and `environments.toml` are
shown, not edited: changing them means regenerating units and restarting the whole
stack. `theme.toml` stays in its own Appearance editor.

## Not done

- Values still hard-coded in code (the audit's configurability list) were not moved
  in this change; the standing rule moves them as their files are touched.
- `config_paper.py` (the manual book's risk caps) is a code-level config module,
  not TOML, so it is not on the page yet.
- Deleting a shipped key via an override is impossible (a deep merge only adds), so
  an optional key the file ships cannot be cleared from the page — it must be set.
