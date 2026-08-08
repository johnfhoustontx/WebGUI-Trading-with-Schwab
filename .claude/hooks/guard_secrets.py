#!/usr/bin/env python
"""PreToolUse hook: block edits/writes to files that hold live secrets.

Claude Code passes the tool call as JSON on stdin. If the target path is one of
the gitignored credential files (Schwab keys/tokens, notification/proxy secrets,
Anthropic key), exit 2 with a message -> the edit is blocked and the message is
shown to Claude. Anything else (incl. the committed *.example templates) passes.
Fail-open on an unexpected error so a hook bug never blocks all edits.
"""
import json
import os
import sys

# Basenames that must never be edited by the agent (real secrets, gitignored).
SECRET_BASENAMES = {
    "appsettings.json", "tokens.json", "proxy_tokens.json", "notifications.json",
    "config_notifications.py", "anthropic_key.txt", "driver_model.txt",
    "proxy_secret.txt", ".credentials.json",
}


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # can't parse -> don't block
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path:
        return 0
    base = os.path.basename(path.replace("\\", "/")).lower()
    # The committed templates are safe to edit.
    if ".example" in base:
        return 0
    if base in SECRET_BASENAMES:
        sys.stderr.write(
            f"Blocked: {base} holds live secrets (gitignored). Do not edit it via "
            f"the agent — ask the user to change credentials themselves. Edit the "
            f"committed *.example template instead if you need to document a field.\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
