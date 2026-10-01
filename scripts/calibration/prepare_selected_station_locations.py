"""Prepare five provisional station locations using the existing outlet script.

Run in the data-preparation environment. Does not run LISFLOOD or calibration.
Catalogue coordinates are preserved in the audit CSV. Review FOLLOW_UP.md before
accepting the existing snapping algorithm for calibration.
"""
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[2]


def main():
    selected = pd.read_csv(ROOT / "shapefiles/recommended_non_cwc_gauges_4061001260.csv")
    # Preserve the archived station's calibration ID 1.
    ids = {4120998630: 1, 4120978780: 2, 4120983650: 3,
           4120992110: 4, 4121001150: 5}
    selected["ObsID"] = selected.HYBAS_ID.map(ids)
    selected = selected.sort_values("ObsID")
    reporting = ROOT / "inputs/reportingStations"
    lines = ["# Original catalogue lon lat ObsID upstream_area_km2; provisional area-based snapping"]
    for g in selected.itertuples():
        lines.append(f"{g.longitude:.12f} {g.latitude:.12f} {g.ObsID} {g.UP_AREA} # {g.gauge_id}")
    (reporting / "stations.csv").write_text("\n".join(lines) + "\n")
    subprocess.run([sys.executable, str(ROOT / "scripts/pipeline/make_outlets.py")], check=True)
    with xr.open_dataset(reporting / "outlets.nc") as ds:
        outlets = next(ds[v].values for v in ds.data_vars if ds[v].ndim == 2)
    with rasterio.open(ROOT / "inputs/maps/area.tif") as src:
        transform, crs = src.transform, src.crs
    to_model = Transformer.from_crs(4326, crs, always_xy=True)
    to_geo = Transformer.from_crs(crs, 4326, always_xy=True)
    with rasterio.open(ROOT / "inputs/raw/facc_snapped.tif") as src:
        acc = src.read(1)
    rows, audit = [], []
    for g in selected.itertuples():
        rr, cc = np.where(outlets == g.ObsID)
        if len(rr) != 1:
            raise ValueError(f"Station {g.ObsID}: expected exactly one outlet cell")
        r, c = int(rr[0]), int(cc[0])
        x, y = rasterio.transform.xy(transform, r, c)
        ox, oy = to_model.transform(g.longitude, g.latitude)
        lon, lat = to_geo.transform(x, y)
        area = float(acc[r, c] * abs(transform.a * transform.e) / 1e6)
        rows.append(dict(ObsID=g.ObsID, StationName=f"FloodHub {g.gauge_id}",
                         GaugeID=g.gauge_id, EC_calib=1, CAL_TYPE=24,
                         Spinup_days=365, Min_calib_days=1095,
                         LisfloodX=x, LisfloodY=y, DrainingArea=g.UP_AREA))
        audit.append(dict(ObsID=g.ObsID, shortlist_map_label=g.map_label,
                          gauge_id=g.gauge_id, qualityVerified=g.qualityVerified,
                          original_longitude=g.longitude, original_latitude=g.latitude,
                          model_longitude=lon, model_latitude=lat, LisfloodX=x, LisfloodY=y,
                          model_row=r, model_col=c, displacement_m=np.hypot(x-ox,y-oy),
                          published_upstream_km2=g.UP_AREA, cached_model_upstream_km2=area,
                          placement_status="PROVISIONAL: review FOLLOW_UP.md"))
    pd.DataFrame(rows).to_csv(ROOT / "Calibration/stations.csv", index=False)
    pd.DataFrame(audit).to_csv(ROOT / "Calibration/gauge_placement_audit.csv", index=False)
    print(pd.DataFrame(audit).to_string(index=False))


if __name__ == "__main__":
    main()
