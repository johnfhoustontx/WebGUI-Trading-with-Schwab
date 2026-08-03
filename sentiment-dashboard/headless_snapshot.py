"""Headless daily sentiment snapshot.

Run by the Windows Scheduled Task registered by the dashboard. Produces
the most-recent completed trading day's snapshot using the same
scoring/backfill code path the GUI uses, then appends it to
sentiment_history.json (de-dup by date, 90-entry cap).

Exits with code 0 on success, 1 on failure. Logs to
sentiment_headless.log next to this script.
"""
from __future__ import annotations

import json
import logging
import sys
import pathlib
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import SCHWAB_PROXY  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
# Canonical proxy install — resolved via repo_paths so launches
# under schtasks (cwd = C:\Windows\System32) still resolve correctly.
PROXY_DIR = SCHWAB_PROXY

# Ensure imports resolve regardless of cwd when launched by schtasks.
for p in (HERE, PROXY_DIR, PROJECT_ROOT / "SchwabProxy", PROJECT_ROOT / "src"):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(HERE / "sentiment_headless.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("headless_snapshot")

HISTORY_FILE = HERE / "sentiment_history.json"


def _load_history() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("history load failed: %s", e)
        return []


def _save_history(entries: list) -> None:
    HISTORY_FILE.write_text(
        json.dumps(entries, indent=2), encoding="utf-8")


def _make_client():
    """Prefer the local Schwab API proxy at ``PROXY_DIR``
    (repo_paths.SCHWAB_PROXY). Under schtasks the direct
    SchwabClient typically can't refresh its OAuth token (re-auth
    requires an interactive browser), so if the proxy isn't running we
    bail rather than silently producing zero entries. The dashboard's
    next-launch catch-up will fill the gap."""
    try:
        from proxy_client import SchwabProxyClient, proxy_available
        if proxy_available():
            log.info("using Schwab proxy from %s", PROXY_DIR)
            return SchwabProxyClient()
        log.warning(
            "Schwab proxy (%s) is not "
            "running — skipping headless snapshot. Dashboard catch-up "
            "will fill this day on next launch.", PROXY_DIR)
        return None
    except Exception as e:
        log.warning("proxy import failed (looked in %s): %s",
                    PROXY_DIR, e)
    # Direct client only as last resort — likely to fail under schtasks.
    try:
        from schwab_client import SchwabClient
        log.info("using direct SchwabClient (token may be expired "
                 "under non-interactive schtasks runs)")
        return SchwabClient()
    except Exception as e:
        log.error("no Schwab client available: %s", e)
        return None


def main() -> int:
    sys.path.insert(0, str(HERE))
    from history_backfill import backfill_history
    from shared.market_calendar import is_trading_day
    from sentiment_dashboard import load_sectors_data, SECTORS_XLSX  # type: ignore
    from datetime import date

    today = date.today()
    if not is_trading_day(today):
        log.info("today %s is a non-trading day (weekend/NYSE holiday) — "
                 "no snapshot to capture", today)
        return 0

    schwab = _make_client()
    if schwab is None:
        return 1
    sector_data = load_sectors_data(SECTORS_XLSX)
    history = _load_history()

    # Use the backfill path with days=1 — it will produce the single most
    # recent completed trading day, which is exactly "today's close" once
    # the market has closed.
    new_entries, stats = backfill_history(
        schwab, sector_data, history, days=1)
    log.info("stats: %s", stats)
    if not new_entries:
        log.warning("no new entries produced (skipped=%d, missing=%s)",
                    stats.get("skipped"), stats.get("missing_symbols"))
        return 0

    existing = {h.get("date") for h in history}
    for e in new_entries:
        e["source"] = "scheduled"
        if e.get("date") in existing:
            continue
        history.append(e)
        existing.add(e.get("date"))
    history.sort(key=lambda h: h.get("date", ""))
    if len(history) > 90:
        history = history[-90:]
    _save_history(history)
    log.info(
        "appended %d entry(s); history now %d days",
        len(new_entries), len(history))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("headless snapshot crashed")
        sys.exit(1)
