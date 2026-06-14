"""
test_gex_endpoint.py - Test OptionsAnalytics GEX snapshot endpoint directly
"""
import requests, json

URL = "http://127.0.0.1:8200/options/snapshot/SPX"

print(f"\nGET {URL}\n" + "-"*60)
try:
    r = requests.get(URL, timeout=30)
    print(f"Status: {r.status_code}")
    print(f"Headers: {dict(r.headers)}")
    print(f"\nBody:\n{r.text[:3000]}")
    if r.status_code == 200:
        data = r.json()
        print(f"\nKeys: {list(data.keys())}")
        print(f"\nnet_gex:        {data.get('net_gex')}")
        print(f"charm_flip:     {data.get('charm_flip_level')}")
        print(f"charm_bias:     {data.get('charm_bias')}")
        print(f"bilateral:      {data.get('bilateral')}")
except Exception as e:
    print(f"Error: {e}")

# Also check /health and /docs
print("\n" + "-"*60)
for path in ["/health", "/docs", "/openapi.json"]:
    try:
        r2 = requests.get(f"http://127.0.0.1:8200{path}", timeout=5)
        print(f"GET {path}: {r2.status_code}")
        if path == "/openapi.json" and r2.status_code == 200:
            routes = list(r2.json().get("paths", {}).keys())
            print(f"  Routes: {routes}")
    except Exception as e:
        print(f"GET {path}: ERROR {e}")
