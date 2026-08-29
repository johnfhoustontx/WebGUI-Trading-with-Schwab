#!/usr/bin/env bash
# Move prod to the current origin/main. THE ONLY sanctioned way prod advances.
#
# Never `git pull` in the prod checkout directly: that skips every guard below,
# each of which exists because prod is a live trading stack.
#   .claude/hooks/guard_prod_promote.py blocks the bypass mechanically, because
#   knowing the rule was not enough -- the environment split was built in a
#   session that then bypassed promote on every commit, `git pull` being one
#   keystroke shorter.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

ENV_NAME="$("$PY" -c 'import sys;sys.path.insert(0,".");import repo_paths;print(repo_paths.ENV_NAME)')"
TARGET="trading-${ENV_NAME}.target"

# 1. Refuse to run anywhere but prod. Promoting a dev checkout would restart the
#    wrong stack and leave the real one on stale code.
if [ "$ENV_NAME" != "prod" ]; then
  echo "[promote] this checkout resolves to '$ENV_NAME', not 'prod' - refusing."
  exit 1
fi

# 2. Dirty-tree refusal BEFORE stopping anything. Ordering is the point: a
#    refusal after the stop leaves prod down AND unpromoted.
if [ -n "$(git status --porcelain)" ]; then
  echo "[promote] working tree is dirty - refusing before touching the stack:"
  git status --short
  exit 1
fi

LOCK_BEFORE="$(git rev-parse HEAD:requirements.lock 2>/dev/null || echo none)"

echo "[promote] stopping $TARGET"
systemctl --user --no-block stop "$TARGET" || true
# --no-block returns immediately; wait for the units to actually be down before
# swapping the code under them.
for _ in $(seq 1 30); do
  systemctl --user is-active --quiet "$TARGET" || break
  sleep 1
done

echo "[promote] fast-forwarding to origin/main"
git fetch origin main --quiet
git pull --ff-only origin main

# 3. Reinstall ONLY when the lock moved. Prod has its own venv, so a dependency
#    added to requirements.txt alone never arrives here -- and depending on how
#    the importer degrades, that can ship as a feature that silently does
#    nothing rather than as an error.
LOCK_AFTER="$(git rev-parse HEAD:requirements.lock 2>/dev/null || echo none)"
if [ "$LOCK_BEFORE" != "$LOCK_AFTER" ]; then
  echo "[promote] requirements.lock moved - reinstalling"
  "$PY" -m pip install -r requirements.lock
else
  echo "[promote] requirements.lock unchanged - skipping install"
fi

echo "[promote] regenerating units (ports/paths are derived, not committed)"
"$PY" -m deploy.systemd.generate_units --install
systemctl --user daemon-reload

echo "[promote] starting $TARGET"
systemctl --user start "$TARGET"

# 4. Verify the stack ANSWERS, not merely that the launcher returned. A dead
#    accept loop stays bound and passes a TCP connect -- which is how a promote
#    once printed success and left prod with no UI at all.
NICEGUI_PORT="$("$PY" -c 'import sys;sys.path.insert(0,".");import repo_paths;print(repo_paths.NICEGUI_PORT)')"
PROXY_PORT="$("$PY" -c 'import sys;sys.path.insert(0,".");import repo_paths;print(repo_paths.PROXY_PORT)')"
"$PY" tools/wait_http.py --port "$PROXY_PORT" --timeout 90 --label "the proxy"
"$PY" tools/wait_http.py --port "$NICEGUI_PORT" --timeout 90 --label "the web GUI"

echo "[promote] promoted to $(git rev-parse --short HEAD)."
