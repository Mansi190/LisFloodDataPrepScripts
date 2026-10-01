"""Extract a HydroBASINS study-area shapefile (any level, any basin) for LISFLOOD.

Pulls WWF/HydroATLAS Basins from Google Earth Engine (mirrors get_watershed.py) and writes a
shapefile you can point `pipeline_config.ROI_SHAPEFILE` at.

Run from the repo root, in your usual prep env (needs `ee` + `geemap`, and GEE auth):
    python scripts/pipeline/get_hydrobasins_roi.py

Two knobs (edit the CONFIG block):
  LEVEL     HydroBASINS level 1 (coarse, whole rivers) .. 12 (fine, ~130 km^2 units).
  basin     Pick it by a POINT (lon, lat) OR an exact HYBAS_ID. Set exactly one; leave the
            other None.
  EXTENT    "single"    -> just the one HydroBASINS polygon at LEVEL for that basin.
            "catchment" -> the FULL upstream catchment draining to it, merged into one polygon
                           (hydrologically complete = a valid LISFLOOD study area). LEVEL then
                           sets the size of the building-block sub-basins that get merged.

Notes:
  * Output CRS is WGS84 (EPSG:4326); the topography step reprojects the ROI to UTM itself.
  * "catchment" walks the NEXT_DOWN topology within the target's major basin (MAIN_BAS). Very
    large rivers (e.g. Ganga at level 12) can be slow to union server-side; pick a coarser LEVEL
    or a smaller sub-basin if it times out.
"""
import os

import ee
import geemap

# ----------------------------- CONFIG -----------------------------
GEE_PROJECT = "gssha-480613"            # same project as get_watershed.py
LEVEL       = 6                         # 1..12
EXTENT      = "single"                # "single" or "catchment"

# Pick the basin by ONE of these (set the other to None):
POINT_LONLAT = [79.64977786674567, 12.225030812731992]     # [lon, lat]; or None
HYBAS_ID     = None                      # e.g. 4121234560; or None

# Filename is built in main(), once find_target() has resolved the basin — HYBAS_ID is
# None when you select by POINT_LONLAT, so it cannot be interpolated here.
OUTPUT_DIR  = "./shapefiles"

# HydroATLAS carries ~295 attributes per basin; a shapefile caps at 255 fields, so the
# "single" export must be trimmed to the topology/area columns before download.
KEEP_FIELDS = ["HYBAS_ID", "NEXT_DOWN", "NEXT_SINK", "MAIN_BAS",
               "DIST_SINK", "DIST_MAIN", "SUB_AREA", "UP_AREA", "PFAF_ID"]
# ------------------------------------------------------------------


def basins_fc(level):
    """HydroATLAS Basins FeatureCollection for a given level (zero-padded id)."""
    return ee.FeatureCollection(f"WWF/HydroATLAS/v1/Basins/level{level:02d}")


def find_target(basins):
    """Resolve the selected basin -> (hybas_id, main_bas)."""
    if HYBAS_ID is not None:
        feat = basins.filter(ee.Filter.eq("HYBAS_ID", HYBAS_ID)).first()
    elif POINT_LONLAT is not None:
        feat = basins.filterBounds(ee.Geometry.Point(POINT_LONLAT)).first()
    else:
        raise SystemExit("Set exactly one of POINT_LONLAT or HYBAS_ID (the other must be None).")

    info = ee.Feature(feat).toDictionary(["HYBAS_ID", "MAIN_BAS"]).getInfo()
    if not info or "HYBAS_ID" not in info:
        raise SystemExit("No basin found for the given selector at this level.")
    return int(info["HYBAS_ID"]), int(info["MAIN_BAS"])


def upstream_ids(basins, target_id, main_bas):
    """Every HYBAS_ID upstream of (and including) target, via NEXT_DOWN topology.

    Restrict to the target's major basin (MAIN_BAS) so we only pull one river system's
    id/next_down table client-side, then walk it upstream.
    """
    same = basins.filter(ee.Filter.eq("MAIN_BAS", main_bas))
    ids = [int(x) for x in same.aggregate_array("HYBAS_ID").getInfo()]
    nexts = [int(x) for x in same.aggregate_array("NEXT_DOWN").getInfo()]

    # children[d] = basins that flow directly INTO basin d
    children = {}
    for b, nd in zip(ids, nexts):
        children.setdefault(nd, []).append(b)

    keep, stack = set(), [target_id]
    while stack:
        cur = stack.pop()
        if cur in keep:
            continue
        keep.add(cur)
        stack.extend(children.get(cur, []))
    return sorted(keep)


def visualize(shp_path, level, mode):
    """Plot the extracted shapefile (+ outlet point + area) and save a PNG beside it."""
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = gpd.read_file(shp_path)
    # area in km^2 via the local UTM zone (accurate near the basin)
    c = g.geometry.union_all().centroid
    zone = int((c.x + 180) // 6) + 1
    epsg = (32600 if c.y >= 0 else 32700) + zone
    area = g.to_crs(epsg).area.sum() / 1e6

    ax = g.plot(figsize=(8, 8), facecolor="#cfe3ff", edgecolor="#1f6feb", linewidth=1.2)
    if POINT_LONLAT is not None:
        ax.scatter([POINT_LONLAT[0]], [POINT_LONLAT[1]], c="red", s=80, marker="*",
                   zorder=5, label="outlet point")
        ax.legend(loc="best")
    ax.set_title(f"HydroBASINS ROI — level {level}, {mode}\n"
                 f"{len(g)} feature(s), {area:,.0f} km²", fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.grid(True, ls=":", lw=0.4, alpha=0.5)

    png = shp_path.rsplit(".", 1)[0] + ".png"
    plt.savefig(png, dpi=140, bbox_inches="tight")
    plt.close()
    print(f"Map -> {png}   ({len(g)} feature(s), {area:,.0f} km^2)")


def main():
    ee.Initialize(project=GEE_PROJECT)
    basins = basins_fc(LEVEL)

    target_id, main_bas = find_target(basins)
    print(f"target HYBAS_ID={target_id}  MAIN_BAS={main_bas}  level={LEVEL}")

    # name after the RESOLVED basin so point- and id-selection land on the same file
    out_shp = os.path.join(OUTPUT_DIR, f"hydrobasins_roi_lev{LEVEL}_{target_id}.shp")

    if EXTENT == "single":
        fc = basins.filter(ee.Filter.eq("HYBAS_ID", target_id)).select(KEEP_FIELDS)
        print("mode: single HydroBASINS unit")
    elif EXTENT == "catchment":
        ids = upstream_ids(basins, target_id, main_bas)
        print(f"mode: upstream catchment — merging {len(ids)} sub-basins")
        fc = basins.filter(ee.Filter.inList("HYBAS_ID", ids)).union(ee.ErrorMargin(1))
    else:
        raise SystemExit('EXTENT must be "single" or "catchment".')

    print("Downloading ->", out_shp)
    geemap.ee_export_vector(fc, filename=out_shp)

    try:
        visualize(out_shp, LEVEL, EXTENT)
    except Exception as e:
        print(f"(visualization skipped: {e})")

    print("Done. Point pipeline_config.ROI_SHAPEFILE at this file to use it as the study area.")


if __name__ == "__main__":
    main()
