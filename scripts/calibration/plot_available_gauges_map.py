#!/usr/bin/env python3
"""Plot every non-CWC Flood Hub gauge inside the ROI and highlight the shortlist."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
import numpy as np
import pandas as pd
from osgeo import gdal, ogr, osr


ROOT = Path(__file__).resolve().parents[2]
ROI_FILE = ROOT / "shapefiles/hydrobasins_roi_lev6_4061001260.shp"
GAUGES_FILE = ROOT / "shapefiles/floodhub_all_noncwc_gauges_india_in_hydrobasins_roi_lev6_4061001260.csv"
SHORTLIST_FILE = ROOT / "shapefiles/recommended_non_cwc_gauges_4061001260.csv"
STATIONS_FILE = ROOT / "Calibration/stations.csv"
FACC_FILE = ROOT / "inputs/raw/facc_snapped.tif"
OUTPUT_FILE = ROOT / "shapefiles/recommended_non_cwc_gauges_4061001260.png"


def projected_roi(roi_layer, transform):
    """Transform ROI geometries to the raster CRS and return them as matplotlib paths."""
    paths = []
    bounds = [np.inf, np.inf, -np.inf, -np.inf]
    for feature in roi_layer:
        geom = feature.GetGeometryRef().Clone()
        geom.Transform(transform)
        polygons = [geom]
        if ogr.GT_Flatten(geom.GetGeometryType()) == ogr.wkbMultiPolygon:
            polygons = [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())]
        for polygon in polygons:
            ring = polygon.GetGeometryRef(0)
            points = np.asarray(ring.GetPoints(), dtype=float)[:, :2]
            if len(points) < 4:
                continue
            codes = np.full(len(points), MplPath.LINETO, dtype=np.uint8)
            codes[0] = MplPath.MOVETO
            codes[-1] = MplPath.CLOSEPOLY
            paths.append(MplPath(points, codes))
            bounds[:2] = np.minimum(bounds[:2], points.min(axis=0))
            bounds[2:] = np.maximum(bounds[2:], points.max(axis=0))
    return paths, bounds


def make_roi_mask(raster, roi_file, projected_srs):
    """Rasterize the basin polygon to the flow-accumulation raster grid."""
    source = ogr.Open(str(roi_file))
    source_layer = source.GetLayer(0)
    source_srs = source_layer.GetSpatialRef().Clone()
    source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source_srs, projected_srs)

    memory = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = memory.CreateLayer("roi", srs=projected_srs, geom_type=ogr.wkbUnknown)
    for feature in source_layer:
        geom = feature.GetGeometryRef().Clone()
        geom.Transform(transform)
        out_feature = ogr.Feature(layer.GetLayerDefn())
        out_feature.SetGeometry(geom)
        layer.CreateFeature(out_feature)

    mask_ds = gdal.GetDriverByName("MEM").Create(
        "", raster.RasterXSize, raster.RasterYSize, 1, gdal.GDT_Byte)
    mask_ds.SetGeoTransform(raster.GetGeoTransform())
    mask_ds.SetProjection(raster.GetProjection())
    gdal.RasterizeLayer(mask_ds, [1], layer, burn_values=[1])
    return mask_ds.GetRasterBand(1).ReadAsArray(), transform


def main():
    gauges = pd.read_csv(GAUGES_FILE)
    shortlist = pd.read_csv(SHORTLIST_FILE)
    stations = pd.read_csv(STATIONS_FILE)
    obsid_by_gauge = dict(zip(stations["GaugeID"].astype(str), stations["ObsID"].astype(int)))
    short_ids = set(shortlist["gauge_id"].astype(str))
    if len(gauges) != 38 or len(short_ids) != 5:
        raise ValueError(f"Expected 38 available gauges and 5 shortlisted; found {len(gauges)} and {len(short_ids)}")
    if not short_ids.issubset(set(gauges["gauge_id"].astype(str))):
        raise ValueError("Shortlisted gauge IDs must all occur in the in-ROI gauge catalogue")

    raster = gdal.Open(str(FACC_FILE))
    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(raster.GetProjection())
    raster_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_raster = osr.CoordinateTransformation(wgs84, raster_srs)

    roi_source = ogr.Open(str(ROI_FILE))
    roi_layer = roi_source.GetLayer(0)
    roi_srs = roi_layer.GetSpatialRef().Clone()
    roi_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_raster_from_roi = osr.CoordinateTransformation(roi_srs, raster_srs)
    roi_paths, bounds = projected_roi(roi_layer, to_raster_from_roi)

    mask, _ = make_roi_mask(raster, ROI_FILE, raster_srs)
    facc = raster.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = raster.GetRasterBand(1).GetNoDataValue()
    if nodata is not None:
        facc[facc == nodata] = np.nan
    gt = raster.GetGeoTransform()
    cell_area_km2 = abs(gt[1] * gt[5]) / 1_000_000
    drainage = np.where((mask == 1) & (facc * cell_area_km2 > 50), 1.0, np.nan)
    extent = (gt[0], gt[0] + raster.RasterXSize * gt[1],
              gt[3] + raster.RasterYSize * gt[5], gt[3])

    positions = {}
    for _, row in gauges.iterrows():
        x, y, *_ = to_raster.TransformPoint(float(row.longitude), float(row.latitude))
        positions[str(row.gauge_id)] = (x, y)

    fig, ax = plt.subplots(figsize=(10, 12), constrained_layout=True)
    ax.imshow(drainage, extent=extent, origin="upper", cmap="Blues", vmin=0, vmax=1,
              interpolation="nearest", alpha=0.72, zorder=1)
    for path in roi_paths:
        ax.add_patch(PathPatch(path, facecolor="#f4f7fa", edgecolor="none", zorder=0))
        ax.add_patch(PathPatch(path, facecolor="none", edgecolor="#34495e", linewidth=1.8, zorder=2))

    other = gauges[~gauges["gauge_id"].astype(str).isin(short_ids)]
    for _, row in other.iterrows():
        x, y = positions[str(row.gauge_id)]
        ax.scatter(x, y, s=36, color="#92999e", edgecolor="white", linewidth=0.45,
                   alpha=0.88, zorder=3)

    for _, row in shortlist.iterrows():
        gauge_id = str(row.gauge_id)
        x, y = positions[gauge_id]
        verified = str(row.qualityVerified).strip().lower() == "true"
        color = "#13865b" if verified else "#e87522"
        ax.scatter(x, y, s=112, color=color, edgecolor="white", linewidth=1.2, zorder=5)
        ax.annotate(str(obsid_by_gauge[gauge_id]), (x, y), xytext=(9, 8), textcoords="offset points",
                    fontsize=11, fontweight="bold", color="#111111", zorder=6)

    padx = (bounds[2] - bounds[0]) * 0.07
    pady = (bounds[3] - bounds[1]) * 0.07
    ax.set_xlim(bounds[0] - padx, bounds[2] + padx)
    ax.set_ylim(bounds[1] - pady, bounds[3] + pady)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Flood Hub non-CWC gauges — basin 4061001260\n"
                 "All 38 in-ROI gauges shown; selected points labeled with calibration IDs",
                 fontsize=14, pad=12)
    ax.set_xlabel("Easting (m), UTM 45N")
    ax.set_ylabel("Northing (m), UTM 45N")
    ax.grid(True, color="#c7cdd1", linewidth=0.55, alpha=0.55, zorder=0)

    handles = [
        Line2D([0], [0], marker="o", linestyle="", markersize=7,
               markerfacecolor="#92999e", markeredgecolor="white",
               label=f"Available, not shortlisted ({len(other)})"),
        Line2D([0], [0], marker="o", linestyle="", markersize=9,
               markerfacecolor="#13865b", markeredgecolor="white",
               label="Shortlisted; high confidence"),
        Line2D([0], [0], marker="o", linestyle="", markersize=9,
               markerfacecolor="#e87522", markeredgecolor="white",
               label="Shortlisted; not high confidence"),
        Line2D([0], [0], color="#6ca6c2", linewidth=2,
               label="Model drainage (>50 km²)"),
    ]
    ax.legend(handles=handles, loc="upper right", framealpha=0.95, fontsize=9)
    fig.savefig(OUTPUT_FILE, dpi=180, facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUTPUT_FILE}; {len(other)} unshortlisted and {len(short_ids)} shortlisted gauges")


if __name__ == "__main__":
    main()
