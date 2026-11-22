#!/usr/bin/env bash
# One-time provisioning of the warm standby (vps3). Run it ONCE, by the operator,
# as `administrator`, with sudo prompts. A second run changes nothing.
#
#   deploy/standby/provision.sh <self> "<status-read public key>" "<age public key line>"
#
#   <self>        this box's name in config/standby.toml [boxes.*], e.g. vps3
#   <status key>  the PUBLIC half of the key prod reads the status file with,
#                 e.g. "ssh-ed25519 AAAA... standby-watch@vps2"
#   <age line>    the "# public key: age1..." line of prod's backup key (the
#                 public line only; this box never holds the private key)
#
# It installs the one standby unit pair and NO stack unit. It signs in to
# nothing and starts nothing of the stack. Redis passwords and the rclone config
# need files from vps2 and from the first sync: they are PRINTED at the end for
# you to run, never run here.
# Design: docs/plans/2026-11-22-warm-standby-design.md, section 2.
set -euo pipefail

REPO_URL="https://github.com/johnfhoustontx/WebGUI-Trading-with-Schwab.git"
DEV="/home/administrator/dev"
PY="$DEV/.venv/bin/python"
PKGS="acl age apt-transport-https build-essential caddy curl debian-archive-keyring debian-keyring fontconfig fonts-liberation fonts-noto-color-emoji fonts-noto-core google-chrome-stable net-tools pkg-config rclone redis-server rsync wget xvfb zstd"

# Refuse first: under sudo $HOME is /root and the clone, keys and units would land there.
[ "$(id -u)" -ne 0 ] || { echo "run as administrator, not with sudo; it prompts for sudo itself" >&2; exit 2; }
[ "$HOME" = "/home/administrator" ] || { echo "refused: HOME is $HOME; run as administrator (the checkout path is fixed at /home/administrator/dev)" >&2; exit 2; }

usage() {
  echo "run as administrator; it asks for sudo itself" >&2
  echo "usage: $0 <self> <status-read-public-key> <age-public-key-line>" >&2
  echo "  <self>  this box's name, e.g. vps3" >&2
  echo "  <status-read-public-key>  \"ssh-ed25519 AAAA... standby-watch@vps2\"" >&2
  echo "  <age-public-key-line>     \"# public key: age1...\"" >&2
  exit 2
}

[ "$#" -eq 3 ] || usage
SELF="$1"; STATUS_KEY="$2"; AGE_LINE="$3"

case "$SELF" in
  ""|*[!A-Za-z0-9_-]*) echo "refused: <self> must be letters, digits, - or _" >&2; exit 2 ;;
esac
case "$STATUS_KEY" in
  "ssh-ed25519 "*) ;;
  *) echo "refused: the second argument must be an 'ssh-ed25519 ' public key" >&2; exit 2 ;;
esac
case "$STATUS_KEY" in
  *'"'*|*$'\n'*) echo "refused: the status key must be one plain line" >&2; exit 2 ;;
esac
case "$AGE_LINE" in
  "# public key: age1"*) ;;
  *) echo "refused: the third argument must start with '# public key: age1'" >&2; exit 2 ;;
esac
case "$AGE_LINE" in
  *$'\n'*) echo "refused: the age line must be one line" >&2; exit 2 ;;
esac

step() { echo; echo "== $*"; }
# Before an append: a non-empty file whose last byte is not a newline gets one,
# so the new line is never glued onto the end of the last key.
ensure_newline() { if [ -s "$1" ] && [ -n "$(tail -c1 "$1")" ]; then printf '\n' >> "$1"; fi; }

# ---------------------------------------------------------------- packages
step "apt packages"
sudo apt-get update -qq
sudo install -d -m 0755 /usr/share/keyrings

if [ ! -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
fi
if [ ! -f /etc/apt/sources.list.d/caddy-stable.list ]; then
  echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
fi

if [ ! -f /usr/share/keyrings/google-chrome.gpg ]; then
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
fi
if [ ! -f /etc/apt/sources.list.d/google-chrome.sources ]; then
  sudo tee /etc/apt/sources.list.d/google-chrome.sources >/dev/null <<'EOF'
Types: deb
URIs: https://dl.google.com/linux/chrome-stable/deb/
Suites: stable
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/google-chrome.gpg
EOF
fi

sudo apt-get update -qq
# shellcheck disable=SC2086
sudo apt-get install -y $PKGS
# Caddy is installed so a takeover is quick, but it must NOT serve until then.
sudo systemctl disable --now caddy

# ---------------------------------------------------------------- uv, Claude Code
step "uv and Claude Code"
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if [ ! -x "$HOME/.local/bin/claude" ]; then
  # Installs only; no sign-in here.
  curl -fsSL https://claude.ai/install.sh | bash
fi

# ---------------------------------------------------------------- the checkout
step "the checkout and its venv"
if [ ! -d "$DEV/.git" ]; then
  git clone "$REPO_URL" "$DEV"
else
  echo "$DEV is already a clone: left as it is (the nightly sync moves it)"
fi
cd "$DEV"
# Prod's venv is uv-made on uv's managed CPython 3.11 with pip seeded.
uv python install 3.11
if [ ! -x "$DEV/.venv/bin/python" ]; then
  uv venv --seed --python 3.11 "$DEV/.venv"
fi
"$PY" -m pip install -r requirements.lock

# ---------------------------------------------------------------- ssh keys
step "ssh keys"
install -d -m 0700 "$HOME/.ssh"
if [ ! -f "$HOME/.ssh/standby_pull" ]; then
  ssh-keygen -t ed25519 -f "$HOME/.ssh/standby_pull" -N '' -C "standby-pull@$SELF"
fi
touch "$HOME/.ssh/authorized_keys"
chmod 0600 "$HOME/.ssh/authorized_keys"
STATUS_LINE="restrict,command=\"cat /home/administrator/standby/status.json\" $STATUS_KEY"
if grep -qxF -- "$STATUS_LINE" "$HOME/.ssh/authorized_keys"; then
  echo "status-read key already in authorized_keys"
else
  ensure_newline "$HOME/.ssh/authorized_keys"
  echo "$STATUS_LINE" >> "$HOME/.ssh/authorized_keys"
  echo "added the status-read key (it can read status.json and nothing else)"
fi
install -d -m 0700 "$HOME/standby"

# ---------------------------------------------------------------- the peer's host keys
step "the peer's ssh host keys"
# The nightly pull runs ssh with BatchMode=yes, so an unknown host key is a
# failed night, not a question. The peer is the box in config/standby.toml that
# is not <self>; each of its ssh targets is scanned. COMPARE every fingerprint
# printed below with the one the peer itself shows (run there):
#     ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# A mismatch means do not trust it: remove it with  ssh-keygen -R <target>.
PEER_TARGETS="$("$PY" - "$SELF" <<'PYEOF'
import sys
from shared import standby_config as sc
boxes = sc.boxes()
if sys.argv[1] not in boxes or len(boxes) != 2:
    sys.exit(f"config/standby.toml must hold exactly two boxes including {sys.argv[1]!r}")
for name, entry in boxes.items():
    if name != sys.argv[1]:
        for target in entry["ssh"]:
            print(target)
PYEOF
)"
KNOWN="$HOME/.ssh/known_hosts"
touch "$KNOWN"
chmod 0600 "$KNOWN"
for T in $PEER_TARGETS; do
  KEYS="$(ssh-keyscan -T 10 -t ed25519 "$T" 2>/dev/null || true)"
  if [ -z "$KEYS" ]; then
    echo "!! $T did not answer ssh-keyscan: add its host key by hand before the first sync"
    continue
  fi
  echo "  $T  (compare with the peer's: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)"
  printf '%s\n' "$KEYS" | ssh-keygen -lf - | sed 's/^/    /'
  while IFS= read -r LINE; do
    [ -n "$LINE" ] || continue
    if grep -qxF -- "$LINE" "$KNOWN"; then
      echo "    already in known_hosts"
    else
      ensure_newline "$KNOWN"
      printf '%s\n' "$LINE" >> "$KNOWN"
      echo "    added to known_hosts"
    fi
  done <<< "$KEYS"
done
# This box's own targets, for the matching step on the peer (printed at the end).
SELF_TARGETS="$("$PY" - "$SELF" <<'PYEOF'
import sys
from shared import standby_config as sc
print(" ".join(sc.boxes()[sys.argv[1]]["ssh"]))
PYEOF
)"

# ---------------------------------------------------------------- age public line
step "age public key line"
AGE_FILE="$HOME/.config/age/backup-key.txt"
install -d -m 0700 "$HOME/.config/age"
if [ -f "$AGE_FILE" ] && grep -q "AGE-SECRET-KEY" "$AGE_FILE"; then
  echo "$AGE_FILE holds a private key: left untouched"
else
  ( umask 077; printf '%s\n' "$AGE_LINE" > "$AGE_FILE" )
  chmod 0600 "$AGE_FILE"
  echo "wrote $AGE_FILE (the public line only)"
fi

# ---------------------------------------------------------------- identity
step "box identity"
IDENT="$HOME/.config/neuralstrike/box.toml"
if [ -f "$IDENT" ]; then
  echo "$IDENT already exists and is left alone. It says:"
  sed 's/^/    /' "$IDENT"
else
  "$PY" - "$SELF" <<'PYEOF'
import sys
from shared.standby_config import Identity, write_identity
write_identity(Identity(self_name=sys.argv[1], role="standby", taking_over=False))
PYEOF
  echo "wrote $IDENT:"
  sed 's/^/    /' "$IDENT"
fi

# ---------------------------------------------------------------- the unit pair
step "the standby unit pair"
# Without linger a --user timer stops at logout and never starts at boot.
sudo loginctl enable-linger "$USER"
"$PY" -m deploy.systemd.standby_units --install

# ---------------------------------------------------------------- operator blocks
PULL_PUB="$(cat "$HOME/.ssh/standby_pull.pub")"
cat <<EOF

============================================================================
DONE on this box. Four things are left, all yours. First check that every
fingerprint printed under "the peer's ssh host keys" matched the peer's own.

(1) ON THE PEER (prod), add this line to ~/.ssh/authorized_keys (it can only
    read the backups):

restrict,command="/usr/bin/rrsync -ro /home/administrator/backups" $PULL_PUB

(1b) ON THE PEER (prod), seed THIS box's host keys for its morning watch (it
    also runs ssh with BatchMode=yes). Compare the fingerprints with this box's
    own (here: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub) before the last
    two lines:

    touch ~/.ssh/known_hosts
    ssh-keyscan -T 10 -t ed25519 $SELF_TARGETS 2>/dev/null > ~/standby-hostkeys.txt
    ssh-keygen -lf ~/standby-hostkeys.txt
    [ -s ~/.ssh/known_hosts ] && [ -n "\$(tail -c1 ~/.ssh/known_hosts)" ] && echo >> ~/.ssh/known_hosts
    grep -vxFf ~/.ssh/known_hosts ~/standby-hostkeys.txt >> ~/.ssh/known_hosts; rm ~/standby-hostkeys.txt

(2) FROM THE PC, copy vps2's rclone config here once (the Drive login a promoted
    box needs to write its own backups). Adjust the host names to your ssh config:

    scp vps2:.config/rclone/rclone.conf $SELF:.config/rclone/rclone.conf
    ssh $SELF 'chmod 600 ~/.config/rclone/rclone.conf'

    (make the folder first if it is missing:  ssh $SELF 'mkdir -p ~/.config/rclone')

(3) AFTER THE FIRST SUCCESSFUL SYNC (it restores .env, .env.live and the hub's
    config, which hold the Redis passwords), set Redis up as on vps2. Run this on
    this box. Passwords go through a pipe or the environment, never an argument,
    and are never printed:

    cd $DEV
    # a) the stack's password (MEMURAI_PASSWORD in .env):
    set -a; . ./.env; set +a
    printf 'CONFIG SET requirepass %s\n' "\$MEMURAI_PASSWORD" | redis-cli
    export REDISCLI_AUTH="\$MEMURAI_PASSWORD"

    # b) the read-only 'live' user. Take its rules from vps2 (run
    #    'redis-cli ACL GETUSER live' there, authenticated the same way) and its
    #    password from REDIS_LIVE_URL in .env.live. Create it with ACL SETUSER
    #    using the SAME key patterns, command grants and the per-stream
    #    '(%W~cmd:<stream> +xadd)' selectors that vps2 lists. The password goes
    #    in through a pipe, e.g.:
    #      set -a; . ./.env.live; set +a
    #      PW=\$($PY -c 'import os,urllib.parse as u;print(u.urlparse(os.environ["REDIS_LIVE_URL"]).password)')
    #      printf 'ACL SETUSER live on >%s <rules from vps2>\n' "\$PW" | redis-cli
    #    Rules, in docs/dev-prod-environments.md section 2 steps 4b to 4f.

    # c) the 'cos' user: this repo does not say how vps2's 'cos' Redis user is
    #    set up, so nothing is invented here. Copy it from vps2's ACL GETUSER cos
    #    and the hub's config, the same way as (b).

    # d) persist all of it (ACL SAVE does not work here; users live in redis.conf):
    redis-cli CONFIG REWRITE
    redis-cli ACL LIST        # read the result: every user present, none with a plain password
============================================================================
EOF
