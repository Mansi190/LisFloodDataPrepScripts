
import numpy as np
if not hasattr(np, 'in1d'):
    # Monkeypatch for pysheds compatibility with numpy 2.0+
    np.in1d = lambda ar1, ar2, assume_unique=False, invert=False: np.isin(ar1, ar2, assume_unique=assume_unique, invert=invert)

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

# ── Repo root anchor ──────────────────────────────────────────────────────────
# This config lives in scripts/pipeline/ ; the repo root is two levels up.
# All paths below are anchored to it, so scripts work regardless of CWD.
REPO_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── ROI ───────────────────────────────────────────────────────────────────────
# Path to your watershed boundary file.
# Supported formats: .shp (with .shx/.dbf/.prj), .gpkg, .geojson
# Any CRS is accepted — the pipeline reprojects automatically.
ROI_SHAPEFILE    = os.path.join(REPO_ROOT, "shapefiles", "hydrobasins_roi_lev6_4061001260.shp")

# ── Spatial grid ──────────────────────────────────────────────────────────────
RESOLUTION_M     = 300          # pixel size in metres

# ── CRS ───────────────────────────────────────────────────────────────────────
# None  → auto-detect UTM zone from ROI_SHAPEFILE centroid (recommended)
# str   → override, e.g. "EPSG:32645"  (UTM Zone 45N, Bihar)
TARGET_CRS       = None         # auto-detect (derive the zone from the restored basin)


# ── Output directories ────────────────────────────────────────────────────────
# BASE_DIR = the LISFLOOD *input* tree (what the pipeline generates).
# LISFLOOD *run outputs* live separately under outputs/ (DIR_OUT).
BASE_DIR         = os.path.join(REPO_ROOT, "inputs")
DIR_MAPS         = os.path.join(BASE_DIR, "maps")
DIR_FRACTION     = os.path.join(DIR_MAPS, "fraction")
DIR_SOILHYD      = os.path.join(DIR_MAPS, "soilhyd")
DIR_TABLE2MAP    = os.path.join(DIR_MAPS, "table2map")
DIR_TABLES       = os.path.join(BASE_DIR, "tables")
DIR_METEO        = os.path.join(BASE_DIR, "meteo")
DIR_LAI          = os.path.join(BASE_DIR, "lai")
DIR_LAI_FOREST   = os.path.join(DIR_LAI, "forest")
DIR_LAI_OTHER    = os.path.join(DIR_LAI, "other")
DIR_OUT          = os.path.join(REPO_ROOT, "outputs", "cold")  # LISFLOOD cold-run output
DIR_RAW          = os.path.join(BASE_DIR, "raw")

# ── Gauges & sites ────────────────────────────────────────────────────────────
# Maximum search radius (m) when snapping a gauge onto the model's own channel.
# Was 500 m and, until now, never actually read — make_outlets.py had max_radius=2
# hardcoded (+/-600 m). That is far too small: the model's D8 network, derived from
# the 300 m upscaled DEM, sits 2-4 km from the real river here, so every gauge
# snapped onto hillslope. 5 km is safe ONLY because make_outlets now snaps by
# matching the gauge's known upstream area (4th column of stations.csv); a radius
# this wide with the old max-accumulation rule would jump to the neighbouring river.
GAUGE_SNAP_DIST_M = 5000

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

# ── Simulation period ─────────────────────────────────────────────────────────
# ONE continuous forcing dataset covers all three LISFLOOD runs; the settings XMLs
# slice it via StepStart/StepEnd. Do not download three separate forcing sets.
#   prerun      2003-01-01 .. 2015-12-31   (settings/prerun.xml)
#   cold run    2016-01-01 .. 2018-12-31   (settings/cold_start.xml)
#   warm run    2019-01-01 .. 2019-12-31   (settings/warm_start.xml)
# Each run starts the day after the previous one ends; the cold run reads the
# prerun's end maps and the warm run reads the cold run's (timestepInit = the
# previous StepEnd). 2003 is a deliberate choice, not a data floor (CHIRPS reaches
# back to 1981): a 13-year prerun is ample to equilibrate the lower groundwater
# zone, and starting in 2003 means MODIS LAI (from 2002-07-04) covers the ENTIRE
# simulation period.
# FORCING_END is 2020-12-31: the study period runs to 2020, and capping the forcing
# there keeps the run windows (prerun / calibration / validation) inside the data
# rather than relying on the settings XMLs to slice a longer span. Raise it if a
# later validation period is wanted — CHIRPS and ERA5-Land both run past 2024.
FORCING_START    = "2003-01-01"
FORCING_END      = "2020-12-31"

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

# ── LISFLOOD soildep1 / soildep2 — SINGLE SOURCE OF TRUTH for soil-storage depth.
# Every script that needs a soil depth reads these (lisflood_lulc_cover builds the
# soildep1/soildep2 maps from them). They are derived from the SoilGrids layer
# thicknesses so that the depth used for water storage (w_s = ThetaSat * depth)
# exactly matches the depth over which the hydraulic properties (ThetaSat, Ksat, …)
# were averaged in lisflood_soil_preprocessing. Do NOT hardcode depths elsewhere.
#   Layer 1 total: sum([5,10,15,30]) cm  = 60 cm  = 600 mm
#   Layer 2 total: sum([40,100])     cm  = 140 cm = 1400 mm
SOIL_DEPTH_L1_MM = sum(SOIL_DEPTHS_L1_WEIGHTS) * 10   # 600 mm
SOIL_DEPTH_L2_MM = sum(SOIL_DEPTHS_L2_WEIGHTS) * 10   # 1400 mm

# =============================================================================
#  DERIVED PATHS — do not edit
# =============================================================================

# area.tif is the master grid written by topographyMapsScript.py
AREA_TIF      = os.path.join(DIR_MAPS, "area.tif")

# lulc.tif is written by lisflood_frac_lulc_preprocessing.py
LULC_ALIGNED  = os.path.join(DIR_FRACTION, "lulc.tif")

# Common nodata sentinels
NODATA_FLOAT  = -9999.0
NODATA_INT    = -9999

# =============================================================================
#  CRS AUTO-DETECTION  &  BASIN GEOMETRY HELPERS
# =============================================================================

def resolve_crs():
    """Return the projected CRS the grid is built in.

    TARGET_CRS wins when set. When it is None the docstring at the top of this file
    promises auto-detection, so derive the UTM zone from the ROI centroid rather than
    silently leaving the ROI in its native (usually geographic) CRS -- rasterising
    degrees at a metre resolution collapses the grid to a single cell.
    """
    if TARGET_CRS:
        return TARGET_CRS
    lat, lon = resolve_centroid()
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


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
    dem_tif = os.path.join(DIR_MAPS, "dem.tif")
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

