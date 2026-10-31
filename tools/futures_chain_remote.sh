#!/usr/bin/env bash
# What the /ES and /NQ capture on the PC runs over ssh, once per interval:
#
#     ssh vps2 /home/administrator/dev/tools/futures_chain_remote.sh plan
#     ssh vps2 /home/administrator/dev/tools/futures_chain_remote.sh ingest < snapshot.json
#
# It exists so the capture needs to know ONE path. The CLI needs the stack's .env
# (Redis has a password, which only the services' systemd units load for
# themselves) and the checkout's venv; an ssh command line is the worst place to
# spell either out. The checkout is found from this script's own location, so
# the one it lives in is the one it feeds.
#
# Standard input is passed through untouched: `exec` replaces this shell with
# python, which reads it.
set -e
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
exec .venv/bin/python -X utf8 tools/futures_chain_cli.py "$@"
