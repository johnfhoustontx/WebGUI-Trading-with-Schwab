"""
test_ml_schema.py - Get full model info and warmup schema from ML servers
Run from claude-driver

Usage:
    python test_ml_schema.py
"""

import json
import requests

ML_SERVERS = {
    "MES": "http://127.0.0.1:8000",
    "MNQ": "http://127.0.0.1:8001",
    "ES":  "http://127.0.0.1:8004",
    "NQ":  "http://127.0.0.1:8005",
}

def get(url, path):
    try:
        r = requests.get(f"{url}{path}", timeout=5)
        return r.status_code, r.json() if r.text else {}
    except Exception as e:
        return None, str(e)

def post_schema(url, path):
    """Get POST body schema from OpenAPI spec for a given path."""
    try:
        spec   = requests.get(f"{url}/openapi.json", timeout=5).json()
        route  = spec.get("paths", {}).get(path, {})
        post   = route.get("post", {})
        body   = post.get("requestBody", {})
        schema = body.get("content", {}).get("application/json", {}).get("schema", {})
        # Resolve $ref
        if "$ref" in schema:
            parts = schema["$ref"].lstrip("#/").split("/")
            node  = spec
            for p in parts:
                node = node.get(p, {})
            schema = node
        return schema
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    # Use MES as representative
    url = ML_SERVERS["MES"]
    print(f"Probing {url}\n")

    # 1. Full /models response
    print("=" * 60)
    print("/models  (full response)")
    print("=" * 60)
    _, body = get(url, "/models")
    print(json.dumps(body, indent=2))

    # 2. Full /config response
    print("\n" + "=" * 60)
    print("/config  (full response)")
    print("=" * 60)
    _, body = get(url, "/config")
    print(json.dumps(body, indent=2))

    # 3. Full /session response
    print("\n" + "=" * 60)
    print("/session  (full response)")
    print("=" * 60)
    _, body = get(url, "/session")
    print(json.dumps(body, indent=2))

    # 4. /warmup POST schema
    print("\n" + "=" * 60)
    print("/warmup  POST schema")
    print("=" * 60)
    schema = post_schema(url, "/warmup")
    print(json.dumps(schema, indent=2))

    # 5. Full OpenAPI paths list
    print("\n" + "=" * 60)
    print("All POST endpoints and their schemas")
    print("=" * 60)
    try:
        spec = requests.get(f"{url}/openapi.json", timeout=5).json()
        for path, methods in spec.get("paths", {}).items():
            if "post" in methods:
                s = post_schema(url, path)
                required = s.get("required", [])
                props    = list(s.get("properties", {}).keys())
                print(f"  POST {path}")
                print(f"    required : {required}")
                print(f"    properties: {props}\n")
    except Exception as e:
        print(f"Error: {e}")
