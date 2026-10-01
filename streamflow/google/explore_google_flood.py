"""Explore the Google Flood Forecasting API for our basin.

Phase-1 discovery script: maps the API, finds the gauge(s) nearest our outlet,
and dumps raw JSON for the gauge model + latest flood status + any forecast
time series, so we can see the real response structure before plotting.

SECURITY: the API key is read from the environment, never hard-coded.
    export GOOGLE_FLOOD_API_KEY='your-key-here'
    python streamflow/google/explore_google_flood.py

Docs: https://developers.google.com/flood-forecasting/rest
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request

API_KEY = os.environ.get("GOOGLE_FLOOD_API_KEY")
if not API_KEY:
    sys.exit("Set your key first:  export GOOGLE_FLOOD_API_KEY='...'  then re-run.")

BASE = "https://floodforecasting.googleapis.com/v1"
OUTLET_LAT, OUTLET_LON = 16.68444, 74.60222   


def _req(method, path, params=None, body=None):
    params = dict(params or {}); params["key"] = API_KEY
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:500]}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def dump(label, obj, n=1500):
    print(f"\n===== {label} =====")
    s = json.dumps(obj, indent=2)
    print(s[:n] + (" …(truncated)" if len(s) > n else ""))


# ── 1. discovery: what methods/fields actually exist ───────────────────────
disc = _req("GET", "").get if False else None  # discovery is at a special path
try:
    url = f"https://floodforecasting.googleapis.com/$discovery/rest?version=v1&key={API_KEY}"
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.loads(r.read().decode())
    print("### API methods available ###")
    def walk(res):
        for _, m in (res.get("methods") or {}).items():
            print(f"  {m.get('httpMethod'):5} {m.get('flatPath')}")
        for _, sub in (res.get("resources") or {}).items():
            walk(sub)
    walk(d)
    print("\n### schemas mentioning Forecast / Timed / Threshold ###")
    for s in d.get("schemas", {}):
        if any(k in s for k in ("Forecast", "Timed", "Threshold", "FloodStatus")):
            props = list(d["schemas"][s].get("properties", {}).keys())
            print(f"  {s}: {props}")
except Exception as e:
    print("discovery failed:", e)

# ── 2. find gauges in India, nearest to our outlet ─────────────────────────
print("\n### searching gauges (regionCode=IN) ###")
resp = _req("POST", "gauges:searchGaugesByArea", body={
    "regionCode": "IN",
    "includeNonQualityVerified": True,
    "includeGaugesWithoutHydroModel": True,
    "pageSize": 50000,
})
gauges = resp.get("gauges", [])
if "_error" in resp:
    dump("searchGaugesByArea ERROR", resp)
print(f"gauges returned for IN: {len(gauges)}")
for g in gauges:
    loc = g.get("location", {})
    g["_dist_km"] = haversine_km(OUTLET_LAT, OUTLET_LON,
                                 loc.get("latitude", 0), loc.get("longitude", 0))
gauges.sort(key=lambda g: g["_dist_km"])
print("\n### 10 nearest gauges to the Panchganga outlet ###")
for g in gauges[:10]:
    loc = g.get("location", {})
    print(f"  {g.get('_dist_km'):7.1f} km  {g.get('gaugeId','?'):22} "
          f"{loc.get('latitude'):.3f},{loc.get('longitude'):.3f}  "
          f"model={g.get('hasModel')} qv={g.get('qualityVerified')}  "
          f"{g.get('river','')} / {g.get('siteName','')} [{g.get('source','')}]")

if not gauges:
    sys.exit("\nNo gauges found — check the region search / key permissions.")

# ── 3. raw dumps for the nearest gauge that has a model ────────────────────
target = next((g for g in gauges if g.get("hasModel")), gauges[0])
gid = target["gaugeId"]
print(f"\n>>> inspecting nearest modelled gauge: {gid} ({target['_dist_km']:.1f} km away)")
dump("gauge object", target)
dump("gaugeModels:batchGet", _req("GET", "gaugeModels:batchGet", params={"names": f"gaugeModels/{gid}"}))
dump("floodStatus:queryLatestFloodStatusByGaugeIds",
     _req("GET", "floodStatus:queryLatestFloodStatusByGaugeIds", params={"gaugeIds": gid}))
