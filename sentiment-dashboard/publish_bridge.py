#!/usr/bin/env python
"""Standalone: compute the live composite and write shared/sentiment_bridge.json.
Run by the GEX collector each cycle (in a subprocess) and usable manually.
Rooted in sentiment-dashboard so `import scoring` resolves to THIS package, not
the options-scanner scoring.py (the cross-app collision the root CLAUDE.md warns of)."""
import logging, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # sentiment scoring/ package wins
sys.path.insert(0, str(HERE.parent))     # repo root for repo_paths
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main() -> int:
    import live_composite
    payload = live_composite.publish_bridge()
    return 0 if payload else 1

if __name__ == "__main__":
    sys.exit(main())
