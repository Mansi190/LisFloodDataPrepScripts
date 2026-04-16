"""
pipeline_config.py — Single source of truth for all LISFLOOD data-prep scripts.

HOW TO USE
----------
Edit only the "USER SETTINGS" block below. All scripts (topography, LULC,
soil, meteo) import this module and will pick up the changes automatically.

CHANGING THE ROI
----------------
Set ROI_SHAPEFILE to the path of your watershed shapefile (.shp/.gpkg/.geojson).
Set TARGET_CRS to None to auto-detect the correct UTM zone, or override manually.

PIPELINE ORDER
--------------
  1. topographyMapsScript.py     → generates area.tif (master grid)
  2. lisflood_lulc_preprocessing.py
  3. lisflood_soil_preprocessing.py
  4. lisflood_meteo_*.py
"""

import math
import os

# =============================================================================
#  USER SETTINGS — only edit this block
# =============================================================================

# ── ROI ───────────────────────────────────────────────────────────────────────
# Path to your watershed boundary file.
# Supported formats: .shp (with .shx/.dbf/.prj), .gpkg, .geojson
# Any CRS is accepted — the pipeline reprojects automatically.
ROI_SHAPEFILE    = "./ShapeFile/ArariaShapefile.shp"

# How to obtain the watershed polygon. One of:
#   "OWN_FILE"   → use ROI_SHAPEFILE above
#   "HYDROSHEDS" → auto-download from HydroSHEDS (requires internet)
#   "SYNTHETIC"  → built-in test polygon (no file needed)
WATERSHED_SOURCE = "OWN_FILE"

# ── Spatial grid ──────────────────────────────────────────────────────────────
RESOLUTION_M     = 30          # pixel size in metres

# ── CRS ───────────────────────────────────────────────────────────────────────
# None  → auto-detect UTM zone from ROI_SHAPEFILE centroid (recommended)
# str   → override, e.g. "EPSG:32645"  (UTM Zone 45N, Bihar)
TARGET_CRS       = None

# ── Output directories ────────────────────────────────────────────────────────
OUTPUT_TOPO      = "./lisflood_outputs"
OUTPUT_LULC      = "./lisflood_lulc"
OUTPUT_SOIL      = "./lisflood_soil"
OUTPUT_METEO     = "./lisflood_meteo"

# ── GEE project ───────────────────────────────────────────────────────────────
GEE_PROJECT      = "gssha-480613"

# ── HydroSHEDS options (only used when WATERSHED_SOURCE = "HYDROSHEDS") ───────
HYDROSHEDS_BBOX      = [87.0, 25.8, 88.0, 26.6]   # [west, south, east, north]
HYDROSHEDS_TARGET_HA = 1000
HYDROSHEDS_MAX_CANDS = 5

# =============================================================================
#  DERIVED PATHS — do not edit
# =============================================================================

# area.tif is the master grid written by topographyMapsScript.py
AREA_TIF      = os.path.join(OUTPUT_TOPO, "maps", "area.tif")

# lulc_aligned.tif is written by lisflood_lulc_preprocessing.py
LULC_ALIGNED  = os.path.join(OUTPUT_LULC, "raw", "lulc_aligned.tif")

# Common nodata sentinels
NODATA_FLOAT  = -9999.0
NODATA_INT    = -9999

# =============================================================================
#  CRS AUTO-DETECTION  &  BASIN GEOMETRY HELPERS
# =============================================================================

def resolve_centroid():
    """
    Return (lat_deg, lon_deg) of the ROI centroid in WGS84.

    Used by the Penman-Monteith script to obtain the basin latitude (for
    extraterrestrial radiation Ra) without requiring the user to hardcode it.
    Falls back to (0.0, 0.0) if the shapefile is unavailable.
    """
    if not os.path.exists(ROI_SHAPEFILE):
        return 0.0, 0.0
    try:
        import geopandas as gpd
        gdf = gpd.read_file(ROI_SHAPEFILE)
        if gdf.crs is None or gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        c = gdf.geometry.unary_union.centroid
        return float(c.y), float(c.x)   # (lat, lon)
    except Exception:
        return 0.0, 0.0


def resolve_mean_elevation():
    """
    Return the mean elevation (m) of the basin from area.tif.

    Used by the Penman-Monteith script for the atmospheric pressure term.
    Requires topographyMapsScript.py to have run first.
    Falls back to 0 m (sea level) if area.tif is not yet available.
    """
    dem_tif = os.path.join(OUTPUT_TOPO, "maps", "dem.tif")
    if not os.path.exists(dem_tif):
        return 0.0
    try:
        import rasterio, numpy as np
        with rasterio.open(dem_tif) as src:
            data = src.read(1).astype(np.float32)
            nodata = src.nodata if src.nodata is not None else -9999
            valid = data[data > nodata / 2]
            return float(np.nanmean(valid)) if valid.size else 0.0
    except Exception:
        return 0.0


def resolve_crs():
    """
    Return the effective TARGET_CRS string.

    If TARGET_CRS is already set, return it unchanged.
    Otherwise read ROI_SHAPEFILE, find the centroid in WGS84, and compute the
    matching UTM zone EPSG code.

    UTM zone formula:
      zone   = floor((lon + 180) / 6) + 1
      EPSG   = 32600 + zone  (Northern hemisphere)
               32700 + zone  (Southern hemisphere)
    """
    if TARGET_CRS is not None:
        return TARGET_CRS

    if not os.path.exists(ROI_SHAPEFILE):
        raise FileNotFoundError(
            f"ROI_SHAPEFILE not found: {ROI_SHAPEFILE}\n"
            "Set the correct path or set TARGET_CRS manually."
        )

    try:
        import geopandas as gpd
        gdf = gpd.read_file(ROI_SHAPEFILE)
        if gdf.crs is None or gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
        centroid = gdf.geometry.unary_union.centroid
        lon, lat = centroid.x, centroid.y
        zone = int(math.floor((lon + 180.0) / 6.0)) + 1
        epsg = 32600 + zone if lat >= 0 else 32700 + zone
        crs  = f"EPSG:{epsg}"
        print(f"  [config] Auto-detected CRS: {crs}  "
              f"(centroid lon={lon:.3f}° lat={lat:.3f}°)")
        return crs
    except ImportError:
        raise ImportError("geopandas is required for CRS auto-detection. "
                          "Run: pip install geopandas")
    except Exception as e:
        raise RuntimeError(
            f"CRS auto-detection failed for {ROI_SHAPEFILE}: {e}\n"
            "Set TARGET_CRS manually in pipeline_config.py."
        )
