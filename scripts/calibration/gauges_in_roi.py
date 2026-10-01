"""Subset a gauge CSV to the points falling inside an ROI shapefile.

Pure local point-in-polygon (geopandas) - no Earth Engine call, so it runs in a second
and can be re-run freely as you try different ROIs from get_hydrobasins_roi.py.

Run from the repo root:
    python scripts/calibration/gauges_in_roi.py \
        --roi shapefiles/hydrobasins_roi_lev6_4060027940.shp \
        --gauges streamflow/google/floodhub_high_conf_gauges_india.csv

Defaults to pipeline_config.ROI_SHAPEFILE when --roi is omitted.

Notes:
  * The ROI is dissolved to one geometry first, so a multi-polygon ROI (or a gauge on a
    shared edge) can never duplicate a gauge row in the output.
  * The ROI is reprojected to EPSG:4326 to match the gauge lat/lon if it isn't already.
"""
import argparse
import os

import geopandas as gpd
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
PIPELINE_DIR = os.path.join(REPO_ROOT, "scripts", "pipeline")   # where pipeline_config lives
DEFAULT_GAUGES = os.path.join(REPO_ROOT, "streamflow", "google",
                              "floodhub_high_conf_gauges_india.csv")


def parse_args():
    p = argparse.ArgumentParser(description="Keep only the gauges inside an ROI polygon.")
    p.add_argument("--roi", default=None,
                   help="ROI .shp/.gpkg/.geojson (default: pipeline_config.ROI_SHAPEFILE)")
    p.add_argument("--gauges", default=DEFAULT_GAUGES, help="CSV with latitude/longitude")
    p.add_argument("--lat", default="latitude")
    p.add_argument("--lon", default="longitude")
    p.add_argument("--out", default=None,
                   help="output CSV (default: <gauges stem>_in_<roi stem>.csv beside the ROI)")
    p.add_argument("--plot", action="store_true", help="also write a PNG of ROI + kept gauges")
    return p.parse_args()


def resolve_roi(path):
    if path:
        return path
    import sys
    sys.path.insert(0, PIPELINE_DIR)
    import pipeline_config as cfg
    return cfg.ROI_SHAPEFILE


def main():
    args = parse_args()
    roi_path = resolve_roi(args.roi)

    roi = gpd.read_file(roi_path)
    if roi.crs is None:
        raise SystemExit(f"{os.path.basename(roi_path)} has no CRS; cannot locate it on Earth.")
    if roi.crs.to_epsg() != 4326:
        roi = roi.to_crs(4326)
    # one geometry -> a gauge can match at most once, whatever the ROI's feature count
    roi_geom = roi.geometry.union_all()

    df = pd.read_csv(args.gauges).dropna(subset=[args.lat, args.lon])
    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df[args.lon], df[args.lat]), crs=4326)

    inside = pts[pts.within(roi_geom)].drop(columns="geometry")

    stem_g = os.path.splitext(os.path.basename(args.gauges))[0]
    stem_r = os.path.splitext(os.path.basename(roi_path))[0]
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(roi_path)),
                                   f"{stem_g}_in_{stem_r}.csv")
    inside.to_csv(out, index=False)

    print(f"ROI    : {roi_path}")
    print(f"gauges : {len(df)} in {os.path.basename(args.gauges)}")
    print(f"inside : {len(inside)}")
    print(f"wrote  : {out}")
    if len(inside):
        cols = [c for c in ("gauge_id", "siteName", "river") if c in inside.columns]
        print("\n" + inside[cols + [args.lat, args.lon]].to_string(index=False))

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ax = roi.plot(figsize=(8, 8), facecolor="#cfe3ff", edgecolor="#1f6feb", linewidth=1.2)
        ax.scatter(pts[args.lon], pts[args.lat], s=10, c="0.75", zorder=3, label="all gauges")
        ax.scatter(inside[args.lon], inside[args.lat], s=55, c="red", edgecolor="k",
                   linewidth=0.5, zorder=4, label=f"inside ROI ({len(inside)})")
        b = roi.total_bounds
        pad = 0.15 * max(b[2] - b[0], b[3] - b[1])
        ax.set_xlim(b[0] - pad, b[2] + pad); ax.set_ylim(b[1] - pad, b[3] + pad)
        ax.set_title(f"Gauges inside {stem_r}", fontweight="bold")
        ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, ls=":", lw=0.4, alpha=0.5)
        png = os.path.splitext(out)[0] + ".png"
        plt.savefig(png, dpi=140, bbox_inches="tight"); plt.close()
        print(f"map    : {png}")


if __name__ == "__main__":
    main()
