#!/usr/bin/env bash
# Wrapper for the Monday post-close instrumentation run.
#
# Exists so a systemd timer has one thing to invoke rather than an interpreter
# plus args plus a redirect. ROOT is derived from this script's own location, so
# the checkout it lives in is the checkout it measures.
#
# Output appends to logs/flow_delta_instr.log; the report itself lands under
# options-scanner/data/flow_delta_instrumentation/<date>/.
#
# Schedule as a systemd user timer (Mondays, after the close):
#   OnCalendar=Mon *-*-* 16:30:00
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$ROOT/logs/flow_delta_instr.log"
mkdir -p "$ROOT/logs"

{
  echo "==== $(date '+%Y-%m-%d %H:%M:%S %Z') ===="
  "$PY" -X utf8 "$ROOT/tools/flow_delta_instrumentation.py"
  echo "exit=$?"
} >> "$LOG" 2>&1
