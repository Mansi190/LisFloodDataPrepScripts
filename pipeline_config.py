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
  2. lisflood_frac_lulc_preprocessing.py
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

# ── Spatial grid ──────────────────────────────────────────────────────────────
RESOLUTION_M     = 300          # pixel size in metres

# ── CRS ───────────────────────────────────────────────────────────────────────
# None  → auto-detect UTM zone from ROI_SHAPEFILE centroid (recommended)
# str   → override, e.g. "EPSG:32645"  (UTM Zone 45N, Bihar)
TARGET_CRS       = None


# ── Output directories ────────────────────────────────────────────────────────
OUTPUT_TOPO      = "./lisflood_topography"
OUTPUT_LULC      = "./lisflood_lulc"
OUTPUT_LULC_COVER = "./lisflood_lulc_cover"
OUTPUT_SOIL      = "./lisflood_soil"
OUTPUT_METEO     = "./lisflood_meteo"
OUTPUT_LAI       = "./lisflood_lai"
OUTPUT_CHANNELS  = "./lisflood_channels"
OUTPUT_GAUGES    = "./lisflood_gauges"

# ── Gauges & sites ────────────────────────────────────────────────────────────
# Maximum search radius (m) when snapping a coordinate to the nearest channel cell.
GAUGE_SNAP_DIST_M = 500

# User-specified gauge locations (WGS84). Leave empty to use only the
# auto-detected outlet gauge.  Format: [("Name", lat_deg, lon_deg), ...]
GAUGE_LOCATIONS = [
    # ("Araria_Bridge", 26.15, 87.47),
]

# Additional monitoring sites (WGS84). NOT required to be on the channel.
# These are written to sites.map but NOT gauges.map.
# Format: [("Name", lat_deg, lon_deg), ...]
SITE_LOCATIONS = [
]

# ── GEE project ───────────────────────────────────────────────────────────────
GEE_PROJECT      = "gssha-480613"

# =============================================================================
#  SOIL DEPTH CONFIGURATION
# =============================================================================

# ── SoilGrids depth bands used when querying GEE (lisflood_soil_preprocessing)
# L1 covers 0–60 cm (rooting zone), L2 covers 60–200 cm (sub-rooting zone).
# Weights are layer thickness in cm, used for thickness-weighted averaging.
SOIL_DEPTHS_L1         = ["0-5cm", "5-15cm", "15-30cm", "30-60cm"]
SOIL_DEPTHS_L1_WEIGHTS = [5,        10,        15,         30]      # cm

SOIL_DEPTHS_L2         = ["60-100cm", "100-200cm"]
SOIL_DEPTHS_L2_WEIGHTS = [40,          100]                         # cm

# ── LISFLOOD soildep1 / soildep2 — derived from the SoilGrids layer thicknesses
# so that the depth used for water storage exactly matches the depth over which
# the hydraulic properties (ThetaSat, Ksat, …) were averaged.
#   Layer 1 total: sum([5,10,15,30]) cm  = 60 cm  = 600 mm
#   Layer 2 total: sum([40,100])     cm  = 140 cm = 1400 mm
SOIL_DEPTH_L1_MM = sum(SOIL_DEPTHS_L1_WEIGHTS) * 10   # 600 mm
SOIL_DEPTH_L2_MM = sum(SOIL_DEPTHS_L2_WEIGHTS) * 10   # 1400 mm

# =============================================================================
#  DERIVED PATHS — do not edit
# =============================================================================

# area.tif is the master grid written by topographyMapsScript.py
AREA_TIF      = os.path.join(OUTPUT_TOPO, "maps", "area.tif")

# lulc.tif is written by lisflood_frac_lulc_preprocessing.py
LULC_ALIGNED  = os.path.join(OUTPUT_LULC, "maps", "lulc.tif")

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

