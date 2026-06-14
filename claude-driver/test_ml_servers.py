"""
test_ml_servers.py - Discover ML server endpoints and validate signal format
Run from claude-driver

Usage:
    python test_ml_servers.py
"""

import json
import requests

ML_SERVERS = {
    "MES": "http://127.0.0.1:8000",
    "MNQ": "http://127.0.0.1:8001",
    "ES":  "http://127.0.0.1:8004",
    "NQ":  "http://127.0.0.1:8005",
}

# Common FastAPI endpoint candidates to probe
CANDIDATES = [
    "/predict",
    "/prediction",
    "/signal",
    "/forecast",
    "/v1/predict",
    "/warmup",
    "/health",
    "/",
]

REQUIRED_FIELDS  = ["signal", "confidence", "direction"]
VALID_SIGNALS    = {"Bullish", "Bearish", "Neutral"}
VALID_DIRECTIONS = {"long", "bullish", "short", "bearish", "flat"}


def discover_endpoints(url: str) -> list:
    """Try /openapi.json first, then probe candidates."""
    # FastAPI auto-generates OpenAPI spec — this reveals all routes
    try:
        resp = requests.get(f"{url}/openapi.json", timeout=5)
        if resp.status_code == 200:
            spec   = resp.json()
            routes = list(spec.get("paths", {}).keys())
            print(f"  OpenAPI routes: {routes}")
            return routes
    except Exception:
        pass
    return []


def probe_endpoint(url: str, path: str) -> tuple:
    """Return (status_code, json_or_None)."""
    try:
        resp = requests.get(f"{url}{path}", timeout=5)
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:200]
        return resp.status_code, body
    except Exception as exc:
        return None, str(exc)


def validate_signal(data: dict) -> list:
    issues = []
    for field in REQUIRED_FIELDS:
        if field not in data:
            issues.append(f"Missing field: '{field}'")

    sig  = data.get("signal",     "")
    conf = data.get("confidence", None)
    dire = data.get("direction",  "")

    if sig not in VALID_SIGNALS:
        issues.append(f"signal='{sig}' not in {VALID_SIGNALS}")
    if conf is None:
        issues.append("confidence is None")
    else:
        try:
            conf_f = float(conf)
            if not (0.0 <= conf_f <= 100.0):
                issues.append(f"confidence {conf_f} out of 0-100 range")
        except (TypeError, ValueError):
            issues.append(f"confidence not numeric: {conf!r}")
    if dire not in VALID_DIRECTIONS:
        issues.append(f"direction='{dire}' not in {VALID_DIRECTIONS}")
    return issues


if __name__ == "__main__":
    print(f"\n{'='*65}")
    print("  ML Server Endpoint Discovery & Validation")
    print(f"{'='*65}\n")

    for instrument, url in ML_SERVERS.items():
        print(f"\n[{instrument}]  {url}")
        print("-" * 50)

        # 1. Try OpenAPI spec
        routes = discover_endpoints(url)

        # 2. Probe all candidates
        working = []
        for path in (routes if routes else CANDIDATES):
            code, body = probe_endpoint(url, path)
            if code == 200:
                print(f"  {path:<20} -> 200 OK  | {str(body)[:80]}")
                working.append((path, body))
            elif code is None:
                print(f"  {path:<20} -> ERROR  | {body}")
            else:
                print(f"  {path:<20} -> {code}")

        # 3. Validate signal fields on any 200 endpoint that looks like a signal
        for path, body in working:
            if isinstance(body, dict):
                issues = validate_signal(body)
                if not issues:
                    print(f"\n  SIGNAL ENDPOINT: {path}")
                    print(f"  signal={body.get('signal')}  "
                          f"confidence={body.get('confidence')}  "
                          f"direction={body.get('direction')}")
                    print(f"  -> Ready to use in config")
                elif any(f in body for f in REQUIRED_FIELDS):
                    print(f"\n  PARTIAL MATCH at {path} — issues: {issues}")
                    print(f"  Raw: {json.dumps(body, indent=4)}")

    print(f"\n{'='*65}")
    print("  Paste this output to update the /predict endpoint in config")
    print(f"{'='*65}\n")
