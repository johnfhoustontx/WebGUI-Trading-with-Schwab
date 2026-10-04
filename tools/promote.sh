#!/usr/bin/env bash
# Move prod to the current origin/main. THE ONLY sanctioned way prod advances.
#
# Never `git pull` in the prod checkout directly: that skips every guard below,
# each of which exists because prod is a live trading stack.
#   .claude/hooks/guard_prod_promote.py blocks the bypass mechanically, because
#   knowing the rule was not enough -- the environment split was built in a
#   session that then bypassed promote on every commit, `git pull` being one
#   keystroke shorter.
#
# THE SHAPE (audit AR-01, 2026-10-03). Until then this stopped production FIRST
# and then did everything that can fail -- the fetch, the pull, the dependency
# install -- with no way back: a failure left prod down on whatever half-state
# it reached. Now:
#
#   BEFORE THE STOP   everything that needs the network or can refuse:
#                     the identity and dirty-tree checks, the fetch, the
#                     fast-forward check, and a dry run of the dependency
#                     install when the lock moved.
#   AFTER THE STOP    only local steps: move the code, install, regenerate the
#                     units, start, and probe every process.
#   ON ANY FAILURE    after the stop, an EXIT trap puts the previous commit
#                     back, restarts the stack on it, and exits non-zero.
#
# Usage:
#   tools/promote.sh              promote, or say "nothing to promote" and stop
#   tools/promote.sh --restart    go through the cycle even with nothing new
#   tools/promote.sh --rollback   put back the commit the LAST promote replaced
#                                 (logs/promote_previous_commit) and restart on it
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

MODE="promote"
FORCE_RESTART=0
case "${1:-}" in
  "") ;;
  --restart) FORCE_RESTART=1 ;;
  --rollback) MODE="rollback" ;;
  *) echo "[promote] unknown argument '$1' (use --restart or --rollback)"; exit 2 ;;
esac

# Identity and ports in ONE Python start (tools/promote_facts.py prints
# KEY='value' lines restricted to a shell-safe alphabet).
FACTS="$("$PY" tools/promote_facts.py)"
eval "$FACTS"
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

# 3. Everything that needs the network, BEFORE the stop.
PREV="$(git rev-parse HEAD)"
if [ "$MODE" = "rollback" ]; then
  # Going BACK to the commit the last promote replaced. It is already in this
  # repository, so nothing is fetched.
  if [ ! -s "$ROOT/logs/promote_previous_commit" ]; then
    echo "[promote] no logs/promote_previous_commit - there is no recorded promote to undo."
    exit 1
  fi
  NEW="$(tr -d '[:space:]' < "$ROOT/logs/promote_previous_commit")"
  if ! git cat-file -e "$NEW^{commit}" 2>/dev/null; then
    echo "[promote] the recorded commit '$NEW' is not in this repository - refusing."
    exit 1
  fi
  if [ "$PREV" = "$NEW" ]; then
    echo "[promote] already at the recorded commit ${NEW:0:9} - nothing to roll back."
    exit 0
  fi
  if ! git merge-base --is-ancestor "$NEW" "$PREV"; then
    echo "[promote] the recorded commit ${NEW:0:9} is not an ancestor of this checkout - refusing."
    exit 1
  fi
  echo "[promote] ROLLING BACK ${PREV:0:9} -> ${NEW:0:9}"
else
  echo "[promote] fetching origin/main (prod is still running)"
  git fetch origin main --quiet
  NEW="$(git rev-parse origin/main)"

  if [ "$PREV" = "$NEW" ] && [ "$FORCE_RESTART" = 0 ]; then
    echo "[promote] already at origin/main ($(git rev-parse --short HEAD)) - nothing to promote."
    echo "[promote]   (was main pushed? use --restart to cycle the stack anyway)"
    exit 0
  fi
  if ! git merge-base --is-ancestor "$PREV" "$NEW"; then
    echo "[promote] origin/main is not a fast-forward of this checkout - refusing."
    echo "[promote]   prod is at $PREV and has commits origin/main does not."
    exit 1
  fi
fi

LOCK_BEFORE="$(git rev-parse "$PREV:requirements.lock" 2>/dev/null || echo none)"
LOCK_AFTER="$(git rev-parse "$NEW:requirements.lock" 2>/dev/null || echo none)"
LOCK_MOVED=0
[ "$LOCK_BEFORE" != "$LOCK_AFTER" ] && LOCK_MOVED=1
if [ "$LOCK_MOVED" = 1 ]; then
  # Prod has its own venv. Resolve the NEW lock now, while the stack is up: a
  # package index that is unreachable or a pin that does not exist must not be
  # discovered with production already stopped.
  echo "[promote] requirements.lock moves - checking the install will resolve"
  NEW_LOCK="$(mktemp)"
  git show "$NEW:requirements.lock" > "$NEW_LOCK"
  if ! "$PY" -m pip install --dry-run --quiet -r "$NEW_LOCK"; then
    rm -f "$NEW_LOCK"
    echo "[promote] the new requirements.lock does not install - refusing before the stop."
    exit 1
  fi
  rm -f "$NEW_LOCK"
fi

# The commit to come back to, on disk as well as in this shell: if this script
# is killed outright, the line below is how a person finds it.
mkdir -p "$ROOT/logs"
echo "$PREV $(date -u +%Y-%m-%dT%H:%M:%SZ) $MODE -> $NEW" >> "$ROOT/logs/promote_history.log"
# A rollback does not overwrite the record: rolling back twice must not turn
# into a roll FORWARD onto the commit that was just abandoned.
[ "$MODE" = "rollback" ] || echo "$PREV" > "$ROOT/logs/promote_previous_commit"

stop_stack() {
  systemctl --user --no-block stop "$TARGET" || true
  # --no-block returns immediately, so wait for the units to actually be down
  # before swapping the code under them.
  #
  # Measured 2026-08-29: the TARGET reports inactive about a second BEFORE its
  # member units' listening sockets close -- so is-active alone is not proof the
  # ports are free. systemd serialises a start behind a stop per unit, so this
  # cannot race a systemctl start; it matters for anything else that binds, and
  # this repo has a documented scar from a silent bind failure serving stale code.
  for _ in $(seq 1 30); do
    systemctl --user is-active --quiet "$TARGET" || break
    sleep 1
  done
  for _ in $(seq 1 15); do
    ss -tln 2>/dev/null | grep -qE ":(8[0-9]{3}|9[0-9]{3}) " || break
    sleep 1
  done
}

# Verify the stack ANSWERS, not merely that the launcher returned. A dead accept
# loop stays bound and passes a TCP connect -- which is how a promote once
# printed success and left prod with no UI at all. EVERY process is probed: the
# old check looked at the proxy and the web GUI only, so a service that died on
# the new code was found by whoever next opened its page.
probe_stack() {
  "$PY" tools/wait_http.py --port "$PROXY_PORT" --timeout 90 --label "the proxy" || return 1
  local i=1 name port
  for port in $SERVICE_PORTS; do
    name="$(echo "$SERVICE_NAMES" | cut -d' ' -f"$i")"
    "$PY" tools/wait_http.py --url "http://127.0.0.1:$port/health" --timeout 90 \
        --label "the $name service" || return 1
    i=$((i + 1))
  done
  "$PY" tools/wait_http.py --port "$NICEGUI_PORT" --timeout 90 --label "the web GUI" || return 1
}

STOPPED=0
DONE=0

# 4. The way back. Fires on ANY exit after the stop that is not a completed
#    promote: a failed install, units that will not start, a process that does
#    not answer. It restores the previous commit and restarts the stack on it.
rollback() {
  local code=$?
  trap - EXIT
  if [ "$STOPPED" = 1 ] && [ "$DONE" = 0 ]; then
    echo "[promote] FAILED (exit $code) with the stack stopped - rolling back to ${PREV:0:9}"
    set +e
    systemctl --user --no-block stop "$TARGET"
    for _ in $(seq 1 30); do
      systemctl --user is-active --quiet "$TARGET" || break
      sleep 1
    done
    git reset --hard --quiet "$PREV"
    if [ "$LOCK_MOVED" = 1 ]; then
      echo "[promote] restoring the previous dependencies"
      "$PY" -m pip install --quiet -r requirements.lock
    fi
    "$PY" -m deploy.systemd.generate_units --install
    systemctl --user daemon-reload
    systemctl --user start "$TARGET"
    if probe_stack; then
      echo "[promote] ROLLED BACK: prod is running on ${PREV:0:9} again. Nothing was promoted."
    else
      echo "[promote] ROLLBACK DID NOT COME UP CLEAN: prod is on ${PREV:0:9} but a"
      echo "[promote]   process is not answering. systemctl --user status '$TARGET'"
    fi
    [ "$code" = 0 ] && code=1
  fi
  exit "$code"
}
trap rollback EXIT

echo "[promote] stopping $TARGET"
STOPPED=1
stop_stack

# No network from here on: the ref was fetched above.
if [ "$MODE" = "rollback" ]; then
  git reset --hard --quiet "$NEW"
else
  echo "[promote] fast-forwarding ${PREV:0:9} -> ${NEW:0:9}"
  git merge --ff-only --quiet "$NEW"
fi

# 5. Reinstall ONLY when the lock moved. Prod has its own venv, so a dependency
#    added to requirements.txt alone never arrives here -- and depending on how
#    the importer degrades, that can ship as a feature that silently does
#    nothing rather than as an error.
if [ "$LOCK_MOVED" = 1 ]; then
  echo "[promote] requirements.lock moved - reinstalling"
  "$PY" -m pip install -r requirements.lock
else
  echo "[promote] requirements.lock unchanged - skipping install"
fi

echo "[promote] regenerating units and arming timers"
# --install does its own daemon-reload and `enable --now`s every timer it wrote,
# because a written .timer is a FILE and not a schedule -- that gap ate eleven
# days of flow-delta reports while every file on disk looked correct.
#
# NOT fatal: a schedule that did not arm is a tomorrow problem; a stack that did
# not start is a now problem. Reported loudly after the stack is verified.
ARMED=1
"$PY" -m deploy.systemd.generate_units --install || ARMED=0
# Kept even though --install reloads: it costs nothing, and it means a reload
# still happens on any path where arming is skipped.
systemctl --user daemon-reload

# The PUBLIC live unit loads .env.live -- its own minimal file, NOT the stack's
# .env -- with no leading dash, so a missing one fails that unit. Warned here
# rather than refused: the whole point of the split is that the public screens
# fail ALONE, and blocking a trading-stack promote on the public site's config
# would invert exactly that. See docs/dev-prod-environments.md 2.4b.
if [ ! -f "$ROOT/.env.live" ]; then
  echo "[promote] WARNING: $ROOT/.env.live is missing."
  echo "[promote]   trading-$ENV_NAME-webgui_live will fail to start (the public"
  echo "[promote]   screens only). It needs REDIS_LIVE_URL + MEMURAI_PASSWORD;"
  echo "[promote]   see docs/dev-prod-environments.md section 2, step 4b."
fi

echo "[promote] starting $TARGET"
systemctl --user start "$TARGET"

probe_stack
DONE=1

# The public screens are allowed to fail alone, so this one only warns.
"$PY" tools/wait_http.py --port "$NICEGUI_LIVE_PORT" --timeout 30 --label "the public screens" \
  || echo "[promote] WARNING: the public screens are not answering (the trading stack is up)."

if [ "$ARMED" = 0 ]; then
  echo "[promote] WARNING: one or more TIMERS ARE NOT ARMED - they will not fire."
  echo "[promote]   The stack itself is up and on the new code; this is the"
  echo "[promote]   schedules only. See the generate_units output above, then:"
  echo "[promote]   systemctl --user enable --now trading-$ENV_NAME-<name>.timer"
  echo "[promote]   systemctl --user list-timers --all"
fi
if [ "$MODE" = "rollback" ]; then
  echo "[promote] rolled back ${PREV:0:9} -> $(git rev-parse --short HEAD)."
  echo "[promote]   origin/main still holds the newer commit: fix or revert it there"
  echo "[promote]   before the next promote, or that promote brings it straight back."
else
  echo "[promote] promoted ${PREV:0:9} -> $(git rev-parse --short HEAD)."
  echo "[promote]   to undo: tools/promote.sh --rollback"
fi

[ "$ARMED" = 1 ] || exit 1
