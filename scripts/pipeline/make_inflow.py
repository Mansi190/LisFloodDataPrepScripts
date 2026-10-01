"""Outflow point of every gauge in a HydroBASINS area.

Give it coordinates. It resolves the HydroBASINS unit there, finds the high-confidence
non-CWC Flood Hub gauges inside it, builds the area's LDD, and follows each gauge
downstream until its water leaves the unit. That exit cell is the gauge's OUTFLOW POINT.

    python scripts/pipeline/make_inflow.py --point 79.7255,12.7089

Outputs in --out-dir (default inputs/inflow/):
    basin_<HYBAS_ID>.shp     the area
    gauges_in_basin.csv      gauges found inside it
    outflow_points.csv       gauge -> outflow point (lon/lat), path length, status
    outflow_points.png       map: area, gauges, outflow points

Reuses the pipeline's own parts: gauges_in_roi.py for the point-in-polygon, ee_export_tiled
for the DEM, lisflood_utils.condition_dem for pysheds conditioning + D8 (same steps as
topographyMapsScript.py), make_outlets.align_station to put a gauge on the channel.
"""
import argparse
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

import pipeline_config as _cfg
from lisflood_utils import (log, make_dirs, check_imports, init_ee,
                            ee_export_tiled, condition_dem)
from make_outlets import align_station

sys.path.insert(0, os.path.join(_cfg.REPO_ROOT, "scripts", "calibration"))
GAUGES_IN_ROI = os.path.join(_cfg.REPO_ROOT, "scripts", "calibration", "gauges_in_roi.py")

DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
STEP = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
        4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}
PCR = {64: 8, 128: 9, 1: 6, 2: 3, 4: 2, 8: 1, 16: 4, 32: 7}   # pysheds -> PCRaster LDD
DEM_M = 30.0
DEM_KM2 = DEM_M * DEM_M / 1e6


def parse_args():
    p = argparse.ArgumentParser(description="Outflow point per gauge in a HydroBASINS area.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--point", help="lon,lat")
    g.add_argument("--hybas-id", type=int)
    p.add_argument("--level", type=int, default=12, help="HydroBASINS level (default 12)")
    p.add_argument("--gauges", default=os.path.join(
        _cfg.REPO_ROOT, "streamflow", "google", "floodhub_high_conf_noncwc_gauges_india.csv"))
    p.add_argument("--out-dir", default=os.path.join(_cfg.BASE_DIR, "inflow"))
    p.add_argument("--snap-m", type=float, default=150.0,
                   help="radius for putting a gauge on the channel (default 150)")
    p.add_argument("--force-dem", action="store_true")
    return p.parse_args()


def get_basin(args, out_dir):
    """HydroBASINS unit at the point -> (GeoDataFrame 4326, hybas_id, shapefile path)."""
    import ee
    import geopandas as gpd
    from shapely.geometry import shape

    init_ee(_cfg.GEE_PROJECT)
    fc = ee.FeatureCollection(f"WWF/HydroATLAS/v1/Basins/level{args.level:02d}")
    if args.hybas_id:
        feat = fc.filter(ee.Filter.eq("HYBAS_ID", args.hybas_id)).first()
    else:
        lon, lat = [float(v) for v in args.point.split(",")]
        feat = fc.filterBounds(ee.Geometry.Point([lon, lat])).first()

    info = ee.Feature(feat).toDictionary(["HYBAS_ID", "SUB_AREA", "UP_AREA"]).getInfo()
    if not info:
        sys.exit("No HydroBASINS unit there at this level.")
    hid = int(info["HYBAS_ID"])
    gdf = gpd.GeoDataFrame({"HYBAS_ID": [hid]},
                           geometry=[shape(ee.Feature(feat).geometry().getInfo())], crs=4326)
    shp = os.path.join(out_dir, f"basin_{hid}.shp")
    gdf.to_file(shp)
    log(f"  basin {hid}: SUB_AREA={info['SUB_AREA']:,.1f} km2 -> {shp}")
    return gdf, hid, shp


def build_ldd(basin_utm, crs, force, hid):
    """SRTM 30 m over the basin -> (fdir, acc, transform, shape).

    The cache file is named after the basin: a different basin (or level) is a different
    window, and an id-less name would silently reuse the previous run's DEM.
    """
    import ee

    make_dirs(_cfg.DIR_RAW)
    dem_tif = os.path.join(_cfg.DIR_RAW, f"basin_{hid}_dem_30m.tif")
    if force or not os.path.exists(dem_tif):
        xmin, ymin, xmax, ymax = basin_utm.total_bounds
        xmin, ymin, xmax, ymax = xmin - 2000, ymin - 2000, xmax + 2000, ymax + 2000
        log(f"  DEM window {(xmax-xmin)/1000:.0f} x {(ymax-ymin)/1000:.0f} km")
        region = ee.Geometry.Rectangle([xmin, ymin, xmax, ymax], proj=str(crs), geodesic=False)
        ee_export_tiled(ee.Image("USGS/SRTMGL1_003").rename("dem").toFloat(),
                        dem_tif, scale=DEM_M, crs=str(crs), region=region)
    else:
        log(f"  reusing {dem_tif}  (--force-dem to refetch)")

    log("  conditioning DEM + D8 (condition_dem)...")
    fdir_r, acc_r, _ = condition_dem(dem_tif, dirmap=DIRMAP)
    with rasterio.open(dem_tif) as src:
        transform, shape_ = src.transform, (src.height, src.width)
    return np.asarray(fdir_r), np.asarray(acc_r, dtype=np.float64), transform, shape_


def trace_out(row, col, fdir, inside):
    """Follow the LDD until the water leaves the basin. Returns (status, exit_cell, steps)."""
    seen = set()
    r, c = row, col
    for step in range(200_000):
        if not (0 <= r < fdir.shape[0] and 0 <= c < fdir.shape[1]):
            return "left_grid", None, step
        if not inside[r, c]:
            return "exited", (r, c), step      # first cell beyond the basin edge
        if (r, c) in seen:
            return "loop", None, step
        seen.add((r, c))
        d = int(fdir[r, c])
        if d not in STEP:
            return "pit_inside", None, step
        dr, dc = STEP[d]
        r, c = r + dr, c + dc
    return "too_long", None, 200_000


def plot(basin, gauges, points, png, hid):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ax = basin.plot(figsize=(9, 9), facecolor="#e8f1fb", edgecolor="#1f6feb", linewidth=1.3)
    ax.scatter(gauges.longitude, gauges.latitude, s=55, c="#1d4f75", zorder=4, label="gauge")
    if len(points):
        ax.scatter(points.outflow_lon, points.outflow_lat, s=90, marker="*", c="#c1121f",
                   edgecolor="white", linewidth=0.6, zorder=5, label="outflow point")
        for _, r in points.iterrows():
            ax.annotate(r.gauge_id.replace("hybas_", ""),
                        (r.outflow_lon, r.outflow_lat), fontsize=7,
                        xytext=(4, 4), textcoords="offset points")
    ax.set_title(f"Gauge outflow points — HydroBASINS {hid}", fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.grid(True, ls=":", lw=0.4, alpha=0.5)
    ax.legend(loc="best", fontsize=8)
    plt.savefig(png, dpi=140, bbox_inches="tight")
    plt.close()


def main():
    args = parse_args()
    check_imports(["rasterio", "numpy", "geopandas", "pysheds", "ee"])
    import geopandas as gpd
    from rasterio.features import rasterize

    out_dir = args.out_dir
    make_dirs(out_dir)
    crs = _cfg.resolve_crs()
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)

    log("STEP 1 — basin", "STEP")
    basin, hid, shp = get_basin(args, out_dir)

    log("STEP 2 — gauges inside it", "STEP")
    gcsv = os.path.join(out_dir, "gauges_in_basin.csv")
    subprocess.run([sys.executable, GAUGES_IN_ROI, "--roi", shp,
                    "--gauges", args.gauges, "--out", gcsv], check=True)
    gauges = pd.read_csv(gcsv, dtype={"gauge_id": str})
    if gauges.empty:
        sys.exit(f"No gauges inside basin {hid}. Try --level 10 or a coarser level.")

    log("STEP 3 — LDD", "STEP")
    basin_utm = basin.to_crs(crs)
    fdir, acc, transform, shape_ = build_ldd(basin_utm, crs, args.force_dem, hid)
    inside = rasterize([(g, 1) for g in basin_utm.geometry], out_shape=shape_,
                       transform=transform, fill=0, dtype="uint8").astype(bool)
    ldd_pcr = np.full(fdir.shape, 5, dtype=np.uint8)
    for k, v in PCR.items():
        ldd_pcr[fdir == k] = v
    log(f"  {shape_[0]}x{shape_[1]} cells, basin covers {inside.sum() * DEM_KM2:,.1f} km2")

    log("STEP 4 — outflow point per gauge", "STEP")
    inv = ~transform
    g_utm = gpd.GeoDataFrame(gauges, geometry=gpd.points_from_xy(gauges.longitude,
                                                                gauges.latitude),
                             crs=4326).to_crs(crs)
    rad = max(1, int(round(args.snap_m / DEM_M)))
    rows = []
    for (_, g), geom in zip(gauges.iterrows(), g_utm.geometry):
        c0, r0 = inv * (geom.x, geom.y)
        r, c, acc_km2, _ = align_station(int(r0), int(c0), acc, ldd_pcr,
                                         inside.astype(np.uint8), max_radius=rad,
                                         target_km2=None, px_km2=DEM_KM2)
        rec = {"gauge_id": g.gauge_id, "latitude": g.latitude, "longitude": g.longitude}
        if r is None:
            rec["status"] = "no_channel_near_gauge"
            rows.append(rec)
            log(f"  {g.gauge_id}: no channel within {args.snap_m:.0f} m", "WARN")
            continue
        rec["gauge_acc_km2"] = round(acc_km2, 2)
        status, cell, steps = trace_out(r, c, fdir, inside)
        rec["status"] = status
        rec["path_km"] = round(steps * DEM_M / 1000, 2)
        if cell:
            x, y = transform * (cell[1] + 0.5, cell[0] + 0.5)
            lon, lat = to_ll.transform(x, y)
            rec.update(outflow_lon=round(lon, 6), outflow_lat=round(lat, 6),
                       outflow_x=round(x, 1), outflow_y=round(y, 1))
        rows.append(rec)
        log(f"  {g.gauge_id}: {status} after {rec['path_km']} km")

    df = pd.DataFrame(rows)
    csv = os.path.join(out_dir, "outflow_points.csv")
    df.to_csv(csv, index=False)
    png = os.path.join(out_dir, "outflow_points.png")
    plot(basin, gauges, df[df.status == "exited"] if "outflow_lon" in df else df.iloc[0:0],
         png, hid)
    log(f"wrote {csv} and {png}", "DONE")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
