"""Fetch EVERY gauge Google Flood Hub exposes - all countries, no quality filter.

Companion to fetch_floodhub_gauges_india.py, which is deliberately narrow (India,
qualityVerified only). This one is the full inventory.

searchGaugesByArea only accepts `regionCode` (an ISO 3166-1 alpha-2 country code) --
there is no bounding-box field -- so "everything" means sweeping every country code
and de-duplicating on gaugeId. A gauge on a border may come back for both neighbours.

SECURITY: the API key is read from the environment, never hard-coded.
    export GOOGLE_FLOOD_API_KEY='your-key-here'

Run:
    python streamflow/google/fetch_floodhub_gauges_all.py

Output: streamflow/google/floodhub_all_gauges_world.csv
Docs:   https://developers.google.com/flood-forecasting/rest
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

API_KEY = os.environ.get("GOOGLE_FLOOD_API_KEY")
if not API_KEY:
    sys.exit("Set your key first:  export GOOGLE_FLOOD_API_KEY='...'  then re-run.")

BASE = "https://floodforecasting.googleapis.com/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "floodhub_all_gauges_world.csv")

FIELDS = ["gauge_id", "latitude", "longitude", "river", "siteName",
          "source", "hasModel", "qualityVerified", "region"]

# ISO 3166-1 alpha-2. Countries with no gauges simply return an empty list.
REGIONS = """
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN
BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL
GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM
JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME
MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP
NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD
SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split()


def parse_args():
    p = argparse.ArgumentParser(description="Fetch all Flood Hub gauges worldwide.")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--regions", default=None,
                   help="comma-separated ISO codes instead of the full sweep")
    p.add_argument("--raw-json", default=None, help="also dump the raw API records")
    return p.parse_args()


def _req(body):
    url = f"{BASE}/gauges:searchGaugesByArea?{urllib.parse.urlencode({'key': API_KEY})}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code}", "_body": e.read().decode()[:200]}
    except Exception as e:
        # Catch broadly on purpose: a socket read timeout raises TimeoutError, which is
        # an OSError and NOT a urllib URLError, so an except-URLError clause misses it
        # and one slow country kills a 249-request sweep.
        return {"_error": type(e).__name__, "_body": str(e)[:200]}


def fetch_region(code, tries=3):
    """Every gauge in one country, following nextPageToken, retrying transients."""
    got, token = [], None
    while True:
        body = {"regionCode": code, "includeNonQualityVerified": True,
                "includeGaugesWithoutHydroModel": True, "pageSize": 50000}
        if token:
            body["pageToken"] = token

        for attempt in range(tries):
            resp = _req(body)
            if "_error" not in resp:
                break
            if attempt < tries - 1:
                wait = 5 * (2 ** attempt)
                print(f"      {code}: {resp['_error']}, retry "
                      f"{attempt + 1}/{tries - 1} in {wait}s", flush=True)
                time.sleep(wait)
        else:
            return got, resp["_error"]

        got.extend(resp.get("gauges", []))
        token = resp.get("nextPageToken")
        if not token:
            return got, None


def main():
    args = parse_args()
    regions = [r.strip().upper() for r in args.regions.split(",")] if args.regions else REGIONS
    print(f"### Flood Hub: sweeping {len(regions)} region codes ###", flush=True)

    ckpt = args.out + ".partial.json"
    by_id, errors = {}, []
    if os.path.exists(ckpt):
        with open(ckpt) as f:
            by_id = {g["gaugeId"]: g for g in json.load(f)}
        done = {g["_region"] for g in by_id.values()}
        regions = [r for r in regions if r not in done]
        print(f"resuming: {len(by_id):,} gauges already held, "
              f"{len(regions)} region(s) left", flush=True)

    for i, code in enumerate(regions, start=1):
        batch, err = fetch_region(code)
        if err:
            errors.append((code, err))
        new = 0
        for g in batch:
            gid = g.get("gaugeId")
            if gid and gid not in by_id:
                g["_region"] = code
                by_id[gid] = g
                new += 1
        if batch or err:
            print(f"  [{i:>3}/{len(regions)}] {code}  got {len(batch):>6}  "
                  f"new {new:>6}  total {len(by_id):>7}"
                  + (f"  ERROR {err}" if err else ""), flush=True)
        if i % 10 == 0 or i == len(regions):
            with open(ckpt, "w") as f:
                json.dump(list(by_id.values()), f)

    gauges = list(by_id.values())
    if not gauges:
        sys.exit("No gauges returned - check key permissions.")

    verified = sum(1 for g in gauges if g.get("qualityVerified"))
    modelled = sum(1 for g in gauges if g.get("hasModel"))
    print(f"\ntotal unique gauges : {len(gauges):,}")
    print(f"  qualityVerified   : {verified:,}  ({100*verified/len(gauges):.1f}%)")
    print(f"  hasModel          : {modelled:,}  ({100*modelled/len(gauges):.1f}%)")

    print(f"\ntop regions:")
    for code, n in Counter(g["_region"] for g in gauges).most_common(15):
        print(f"  {code}  {n:>7,}")
    print(f"\ntop sources:")
    for name, n in Counter(g.get("source") or "(none)" for g in gauges).most_common(15):
        print(f"  {name:<18}{n:>7,}")
    if errors:
        print(f"\n{len(errors)} region(s) errored: {[c for c, _ in errors][:20]}")

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for g in sorted(gauges, key=lambda x: x.get("gaugeId", "")):
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
                "region": g.get("_region", ""),
            })
    print(f"\nwrote {len(gauges):,} gauges -> {args.out}")
    if os.path.exists(ckpt):
        os.remove(ckpt)

    if args.raw_json:
        with open(args.raw_json, "w") as f:
            json.dump(gauges, f)
        print(f"wrote raw records -> {args.raw_json}")


if __name__ == "__main__":
    main()
