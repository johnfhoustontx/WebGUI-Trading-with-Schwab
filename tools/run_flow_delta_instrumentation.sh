#!/usr/bin/env bash
# Wrapper for the post-close instrumentation run.
#
# Exists so a systemd timer has one thing to invoke rather than an interpreter
# plus args plus a redirect. ROOT is derived from this script's own location, so
# the checkout it lives in is the checkout it measures.
#
# Output appends to logs/flow_delta_instr.log; the report itself lands under
# options-scanner/data/flow_delta_instrumentation/<date>/.
#
# CADENCE IS EVERY TRADING DAY, not Mondays. The .bat this replaced said "Monday"
# in its header too, and the schtasks job that actually invoked it ran daily at
# 15:30 CT -- every weekday from 2026-08-11 to 2026-08-28 has a report, each
# stamped 15:30. The header was simply never corrected, and the Linux conversion
# copied the wrong word across. Schedule as a systemd user timer:
#
#   OnCalendar=Mon..Fri *-*-* 15:30:00
#
# No holiday filter is needed: flow_delta_instrumentation.py gates on
# shared/market_calendar.is_trading_day and exits 0 on a holiday, so a timer that
# fires is not the same as a run that measures.
#
# ⚠ NOTHING SCHEDULES THIS TODAY. The schedule lived in Windows Task Scheduler,
# outside the repo, and did not survive the move to systemd -- deploy/systemd/
# generate_units.py emits only the backup and stream timers. The last scheduled
# report is 2026-08-28.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$ROOT/logs/flow_delta_instr.log"
mkdir -p "$ROOT/logs"

# The report reconciles against the live channel in Redis, which needs
# MEMURAI_PASSWORD. The services get it from the units' EnvironmentFile=; a bare
# run inherits nothing, so source it the way docs/dev-prod-environments.md
# prescribes. Values in .env must stay QUOTED -- systemd takes them literally
# while bash parses them, and that disagreement is what this line walks into.
# Without it the run still exits 0 and the report just says "Not reconciled".
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') ===="
  "$PY" -X utf8 "$ROOT/tools/flow_delta_instrumentation.py"
  echo "exit=$?"
} >> "$LOG" 2>&1
