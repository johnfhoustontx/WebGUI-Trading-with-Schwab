"""
test_ml_ensemble.py - Discover /ensemble POST schema and validate response format
Run from claude-driver

Usage:
    python test_ml_ensemble.py
"""

import json
import requests

ML_SERVERS = {
    "MES": "http://127.0.0.1:8000",
    "MNQ": "http://127.0.0.1:8001",
    "ES":  "http://127.0.0.1:8004",
    "NQ":  "http://127.0.0.1:8005",
}

def fetch_schema(url: str) -> dict:
    """Get full OpenAPI spec and extract /ensemble POST schema."""
    try:
        resp = requests.get(f"{url}/openapi.json", timeout=5)
        spec = resp.json()
        ensemble = spec.get("paths", {}).get("/ensemble", {})
        post     = ensemble.get("post", {})
        body     = post.get("requestBody", {})
        schema   = (body.get("content", {})
                       .get("application/json", {})
                       .get("schema", {}))
        # Resolve $ref if present
        if "$ref" in schema:
            ref_path = schema["$ref"].replace("#/", "").replace("/", ".")
            parts    = ref_path.split(".")
            node     = spec
            for p in parts:
                node = node.get(p, {})
            schema = node
        return schema
    except Exception as exc:
        return {"error": str(exc)}


def try_post(url: str, payload: dict, label: str) -> tuple:
    try:
        resp = requests.post(f"{url}/ensemble", json=payload, timeout=5)
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:300]
        return resp.status_code, body
    except Exception as exc:
        return None, str(exc)


if __name__ == "__main__":
    print(f"\n{'='*65}")
    print("  /ensemble POST Schema Discovery")
    print(f"{'='*65}\n")

    # Use MES as representative — all servers share the same architecture
    base_url = ML_SERVERS["MES"]

    # 1. Print OpenAPI schema for /ensemble
    print(f"[Schema from {base_url}/openapi.json]\n")
    schema = fetch_schema(base_url)
    print(json.dumps(schema, indent=2))

    # 2. Try progressively fuller POST payloads on all servers
    payloads = [
        ({},                                    "empty body"),
        ({"symbol": "MES"},                     "symbol only"),
        ({"instrument": "MES"},                 "instrument only"),
        ({"session": "RTH"},                    "session only"),
        ({"use_buffer": True},                  "use_buffer flag"),
        ({"symbol": "MES", "use_buffer": True}, "symbol + use_buffer"),
    ]

    print(f"\n{'='*65}")
    print("  POST /ensemble payload probes (MES only)")
    print(f"{'='*65}\n")
    for payload, label in payloads:
        code, body = try_post(base_url, payload, label)
        print(f"  [{label}]")
        print(f"  Payload : {json.dumps(payload)}")
        print(f"  Response: {code} | {str(body)[:120]}")
        print()
        if code == 200:
            print(f"  *** WORKING PAYLOAD FOUND: {json.dumps(payload)} ***")
            print(f"  Full response:")
            print(json.dumps(body, indent=4))
            break

    print(f"\n{'='*65}")
    print("  Paste full output — I will fix fetch_ml_signal() accordingly")
    print(f"{'='*65}\n")
