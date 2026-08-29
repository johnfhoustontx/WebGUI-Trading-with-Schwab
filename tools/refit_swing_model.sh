#!/usr/bin/env bash
# Monthly refit of the swing factor model (Phase 6).
#
# Archives the current artifact + report under a DATED folder, re-runs the fit,
# and diffs the new report against the prior one so a decay is visible without
# anyone remembering to look.
#
# Why a script on a timer rather than a service job: the fit must stay
# un-importable by any service (it pulls 5 years of history for ~78 symbols and
# takes minutes), and dev runs with schedulers=False, so a service job would sit
# inert there.
#
# !! It refuses to run without the proxy. A fit against a dead proxy produces an
# artifact from whatever partial history it managed, which would then SHIP as the
# model -- worse than not refitting at all.
#
# Schedule it as a systemd user timer (monthly, 1st at 19:00):
#   ~/.config/systemd/user/swing-refit.service  -> ExecStart=<this script>
#   ~/.config/systemd/user/swing-refit.timer    -> OnCalendar=*-*-01 19:00:00
#   systemctl --user enable --now swing-refit.timer
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

[ -x "$PY" ] || { echo "[refit] no venv at $PY - aborting."; exit 1; }

# Resolve the proxy port from repo_paths rather than hardcoding it: a dev
# checkout borrows prod's proxy, so the number is environment-dependent.
PROXY_PORT="$("$PY" -c 'import sys;sys.path.insert(0,".");import repo_paths;print(repo_paths.PROXY_PORT)' 2>/dev/null || echo 8100)"
PROXY_HOST="$("$PY" -c 'import sys;sys.path.insert(0,".");import repo_paths;print(repo_paths.PROXY_HOST)' 2>/dev/null || echo 127.0.0.1)"

if ! "$PY" -c "import urllib.request;urllib.request.urlopen('http://${PROXY_HOST}:${PROXY_PORT}/health',timeout=5)" >/dev/null 2>&1; then
  echo "[refit] proxy not answering on ${PROXY_HOST}:${PROXY_PORT} - aborting rather than"
  echo "        fitting on whatever partial history a dead proxy returns."
  exit 1
fi

TODAY="$("$PY" -c 'import datetime;print(datetime.date.today().isoformat())')"
DATA="$ROOT/trade-analyzer/data"
ARCHIVE="$DATA/archive/$TODAY"
PREV="$(mktemp)"
trap 'rm -f "$PREV"' EXIT

if [ -f "$DATA/swing_model.json" ]; then
  mkdir -p "$ARCHIVE"
  cp -f "$DATA/swing_model.json" "$ARCHIVE/"
  if [ -f "$DATA/swing_model_report.md" ]; then
    cp -f "$DATA/swing_model_report.md" "$ARCHIVE/"
    cp -f "$DATA/swing_model_report.md" "$PREV"
  fi
  echo "[refit] archived the current artifact to $ARCHIVE"
else
  echo "[refit] no existing artifact to archive - this is a first fit."
fi

echo "[refit] fitting..."
if ! (cd "$ROOT/trade-analyzer" && "$PY" fit_swing_model.py); then
  echo "[refit] FIT FAILED - the previous artifact is untouched and still live."
  exit 1
fi

if [ -s "$PREV" ]; then
  echo
  echo "[refit] report diff against the prior fit:"
  "$PY" tools/diff_swing_report.py "$PREV" "$DATA/swing_model_report.md"
fi
echo "[refit] done."
