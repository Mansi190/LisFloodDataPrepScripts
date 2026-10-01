"""Fetch Google Flood Hub gauges for India and keep the high-confidence ones.

"High confidence" == qualityVerified: the gauges Google actually displays on
Flood Hub, i.e. the ones whose model skill passed their internal bar. We pull
*all* India gauges (verified + not) so we can report the split, then write the
verified subset for the stream-order snap.

SECURITY: the API key is read from the environment, never hard-coded.
    export GOOGLE_FLOOD_API_KEY='your-key-here'
    python streamflow/google/fetch_floodhub_gauges_india.py

Outputs (both written from ONE API pull, so they stay consistent):
  floodhub_high_conf_gauges_india.csv         all 440 qualityVerified gauges
  floodhub_high_conf_noncwc_gauges_india.csv  the 67 that are NOT CWC stations

WHY THE NON-CWC SPLIT: every CWC station is qualityVerified automatically (373/373),
so "high confidence" tells you nothing about them -- it just means "is a real station".
The non-CWC gauges are HYBAS model reaches, and only 67 of India's 15,967 (0.4%) cleared
Google's skill bar, so THAT subset is genuinely selective. They are also keyed on
HydroBASINS ids, which means they resolve directly to GRRR reaches with no snapping.
Docs:   https://developers.google.com/flood-forecasting/rest
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_KEY = os.environ.get("GOOGLE_FLOOD_API_KEY")
if not API_KEY:
    sys.exit("Set your key first:  export GOOGLE_FLOOD_API_KEY='...'  then re-run.")

BASE = "https://floodforecasting.googleapis.com/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "floodhub_high_conf_gauges_india.csv")
OUT_NONCWC = os.path.join(HERE, "floodhub_high_conf_noncwc_gauges_india.csv")

FIELDS = ["gauge_id", "latitude", "longitude", "river", "siteName",
          "source", "hasModel", "qualityVerified"]


def write_csv(path, gauges):
    """Flatten the API's nested location into flat lat/lon columns."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for g in gauges:
            loc = g.get("location", {})
            w.writerow({
                "gauge_id": g.get("gaugeId", ""),
                "latitude": loc.get("latitude", ""),
                "longitude": loc.get("longitude", ""),
                "river": g.get("river", ""),
                "siteName": g.get("siteName", ""),
                "source": g.get("source", ""),
                "hasModel": g.get("hasModel", ""),
                "qualityVerified": g.get("qualityVerified", ""),
            })
    return len(gauges)


def _req(method, path, params=None, body=None):
    params = dict(params or {}); params["key"] = API_KEY
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:800]}


def fetch_all_india():
    """searchGaugesByArea pages via nextPageToken; accumulate every gauge."""
    gauges, token, page = [], None, 0
    while True:
        body = {
            "regionCode": "IN",
            "includeNonQualityVerified": True,
            "includeGaugesWithoutHydroModel": True,
            "pageSize": 50000,
        }
        if token:
            body["pageToken"] = token
        resp = _req("POST", "gauges:searchGaugesByArea", body=body)
        if "_error" in resp:
            sys.exit(f"API error {resp['_error']}: {resp.get('_body')}")
        batch = resp.get("gauges", [])
        gauges.extend(batch)
        page += 1
        token = resp.get("nextPageToken")
        print(f"  page {page}: +{len(batch)} gauges (running total {len(gauges)})")
        if not token:
            break
    return gauges


def main():
    print("### searching Flood Hub gauges (regionCode=IN) ###")
    gauges = fetch_all_india()
    if not gauges:
        sys.exit("No gauges returned - check key permissions / region.")

    verified = [g for g in gauges if g.get("qualityVerified")]
    modelled = [g for g in verified if g.get("hasModel")]
    print(f"\ntotal India gauges      : {len(gauges)}")
    print(f"qualityVerified (kept)  : {len(verified)}")
    print(f"  ...of which hasModel  : {len(modelled)}")

    # CWC == real agency stations; everything else in India is a HYBAS model reach.
    noncwc = [g for g in verified if (g.get("source") or "") != "CWC"]
    print(f"  ...of which NOT CWC   : {len(noncwc)}")

    n_all = write_csv(OUT_CSV, verified)
    n_nc = write_csv(OUT_NONCWC, noncwc)
    print(f"\nwrote {n_all} high-confidence gauges          -> {OUT_CSV}")
    print(f"wrote {n_nc} high-confidence non-CWC gauges -> {OUT_NONCWC}")


if __name__ == "__main__":
    main()
