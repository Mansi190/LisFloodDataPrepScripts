"""
=============================================================================
LISFLOOD INPUT MAP GENERATOR — LULC (Land Use / Land Cover)
Generates all fraction maps and land-cover-dependent maps for LISFLOOD.

Source  : GEE asset → projects/corestack-datasets/assets/datasets/
          LULC_v3_river_basin/pan_india_lulc_v3_2024_2025
Bounding: area.tif  (same raster used as DEM reference grid)
CRS     : EPSG:32645  (UTM Zone 45N)
Res     : 30 m

LEGEND (GEE asset classes)
  0  Background
  1  Built up
  2  Kharif water
  3  Kharif and rabi water
  4  Kharif and rabi and zaid water
  5  Crops            (single kharif + generic)
  6  Trees
  7  Barren land
  8  Single Kharif Cropping

OUTPUTS (LISFLOOD names — Table A12.1)
  Fraction maps  (0-1 Scalar)
    fracwater.map      — inland water fraction
    fracsealed.map     — impermeable surface fraction  (built-up)
    fracforest.map     — forest fraction
    fracother.map      — other (agriculture + barren + non-forest)

  Land-cover-dependent maps  (Scalar / Nominal)
    cropcoef_forest.map   — crop coefficient forest        (0.8–1.2)
    cropcoef_other.map    — crop coefficient other         (0.8–1.2)
    crgrnum_forest.map    — crop group number forest       (1–5)
    crgrnum_other.map     — crop group number other        (1–5)
    mannings_forest.map   — Manning's n forest             (0.2–0.4)
    mannings_other.map    — Manning's n other              (0.01–0.3)
    soildep1_forest.map   — soil layer-1 depth forest [mm] (>=50)
    soildep1_other.map    — soil layer-1 depth other  [mm] (>=50)
    soildep2_forest.map   — soil layer-2 depth forest [mm] (>=50)
    soildep2_other.map    — soil layer-2 depth other  [mm] (>=50)

WORKFLOW
  Step 0 — Load reference (master) grid from area.tif
  Step 1 — Export LULC from GEE (saves GeoTIFF locally)
  Step 2 — Reproject + align LULC to master grid (nearest-neighbor)
  Step 3 — Build fraction maps (pixel-wise class counting)
  Step 4 — Assign land-cover-dependent scalar/nominal maps
  Step 5 — Save GeoTIFFs + convert to PCRaster .map
  Step 6 — Validate all outputs against master grid
  Step 7 — Generate visual check PNG

INSTALL
  pip install rasterio numpy scipy matplotlib geopandas pyproj
  pip install earthengine-api geemap

AUTHENTICATION (first run only)
  earthengine authenticate
=============================================================================
"""

import os
import sys
import subprocess
import warnings
import numpy as np
warnings.filterwarnings("ignore")
from pathlib import Path

# =============================================================================
#  SETTINGS — edit this section only
# =============================================================================

AREA_TIF        = "./area.tif"          # reference / bounding raster (master grid)
OUTPUT_DIR      = "./lisflood_lulc"
TARGET_CRS      = "EPSG:32645"
RESOLUTION_M    = 30

# GEE asset
GEE_ASSET       = ("projects/corestack-datasets/assets/datasets/"
                   "LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
GEE_SCALE       = 30          # export scale (m) — matches DEM resolution
GEE_CRS         = "EPSG:32645"

# Intermediate file path (GEE export -> local)
LULC_RAW_TIF    = os.path.join(OUTPUT_DIR, "raw", "lulc_raw.tif")

# ── LULC class IDs in the GEE asset ──────────────────────────────────────────
CLASS_BACKGROUND = 0
CLASS_BUILTUP    = 1
CLASS_KH_WATER   = 2   # Kharif water
CLASS_KR_WATER   = 3   # Kharif + rabi water
CLASS_KRZ_WATER  = 4   # Kharif + rabi + zaid water
CLASS_CROPS      = 5   # Generic crops
CLASS_TREES      = 6
CLASS_BARREN     = 7
CLASS_KHARIF     = 8   # Single kharif cropping

# Convenience groupings for fraction maps
WATER_CLASSES    = {CLASS_KH_WATER, CLASS_KR_WATER, CLASS_KRZ_WATER}
SEALED_CLASSES   = {CLASS_BUILTUP}
FOREST_CLASSES   = {CLASS_TREES}
OTHER_CLASSES    = {CLASS_CROPS, CLASS_BARREN, CLASS_KHARIF}
# Background = 0 is treated as nodata outside the mask

# ── Per-class LISFLOOD parameter look-up tables ───────────────────────────────
# Literature-reasonable defaults for India (monsoon climate).
# Calibrate these to your study area before running LISFLOOD.

# cropcoef: Kc (dimensionless, 0.8–1.2)
CROPCOEF_FOREST = {
    CLASS_TREES   : 1.0,
    CLASS_BARREN  : 0.85,
    CLASS_CROPS   : 0.90,
    CLASS_KHARIF  : 0.90,
    CLASS_BUILTUP : 0.80,
    CLASS_KH_WATER: 1.05,
    CLASS_KR_WATER: 1.05,
    CLASS_KRZ_WATER: 1.05,
    CLASS_BACKGROUND: 1.0,
}
CROPCOEF_OTHER = {
    CLASS_TREES   : 0.85,
    CLASS_BARREN  : 0.85,
    CLASS_CROPS   : 1.10,
    CLASS_KHARIF  : 1.05,
    CLASS_BUILTUP : 0.80,
    CLASS_KH_WATER: 1.10,
    CLASS_KR_WATER: 1.10,
    CLASS_KRZ_WATER: 1.10,
    CLASS_BACKGROUND: 1.0,
}

# crgrnum: 1–5  (FAO crop group)
CRGRNUM_FOREST = {
    CLASS_TREES   : 4,
    CLASS_BARREN  : 1,
    CLASS_CROPS   : 3,
    CLASS_KHARIF  : 3,
    CLASS_BUILTUP : 1,
    CLASS_KH_WATER: 1,
    CLASS_KR_WATER: 1,
    CLASS_KRZ_WATER: 1,
    CLASS_BACKGROUND: 1,
}
CRGRNUM_OTHER = {
    CLASS_TREES   : 3,
    CLASS_BARREN  : 1,
    CLASS_CROPS   : 3,
    CLASS_KHARIF  : 3,
    CLASS_BUILTUP : 1,
    CLASS_KH_WATER: 1,
    CLASS_KR_WATER: 1,
    CLASS_KRZ_WATER: 1,
    CLASS_BACKGROUND: 1,
}

# mannings n (dimensionless, forest: 0.2–0.4, other: 0.01–0.3)
MANNINGS_FOREST = {
    CLASS_TREES   : 0.35,
    CLASS_BARREN  : 0.22,
    CLASS_CROPS   : 0.28,
    CLASS_KHARIF  : 0.28,
    CLASS_BUILTUP : 0.20,
    CLASS_KH_WATER: 0.25,
    CLASS_KR_WATER: 0.25,
    CLASS_KRZ_WATER: 0.25,
    CLASS_BACKGROUND: 0.25,
}
MANNINGS_OTHER = {
    CLASS_TREES   : 0.12,
    CLASS_BARREN  : 0.01,
    CLASS_CROPS   : 0.05,
    CLASS_KHARIF  : 0.05,
    CLASS_BUILTUP : 0.02,
    CLASS_KH_WATER: 0.03,
    CLASS_KR_WATER: 0.03,
    CLASS_KRZ_WATER: 0.03,
    CLASS_BACKGROUND: 0.05,
}

# soil layer-1 depth (rooting depth, mm) — must be >= 50
SOILDEP1_FOREST = {
    CLASS_TREES   : 500,
    CLASS_BARREN  : 50,
    CLASS_CROPS   : 200,
    CLASS_KHARIF  : 200,
    CLASS_BUILTUP : 50,
    CLASS_KH_WATER: 50,
    CLASS_KR_WATER: 50,
    CLASS_KRZ_WATER: 50,
    CLASS_BACKGROUND: 50,
}
SOILDEP1_OTHER = {
    CLASS_TREES   : 200,
    CLASS_BARREN  : 50,
    CLASS_CROPS   : 300,
    CLASS_KHARIF  : 250,
    CLASS_BUILTUP : 50,
    CLASS_KH_WATER: 50,
    CLASS_KR_WATER: 50,
    CLASS_KRZ_WATER: 50,
    CLASS_BACKGROUND: 50,
}

# soil layer-2 depth (sub-rooting, mm) — must be >= 50
SOILDEP2_FOREST = {
    CLASS_TREES   : 1000,
    CLASS_BARREN  : 100,
    CLASS_CROPS   : 500,
    CLASS_KHARIF  : 500,
    CLASS_BUILTUP : 100,
    CLASS_KH_WATER: 100,
    CLASS_KR_WATER: 100,
    CLASS_KRZ_WATER: 100,
    CLASS_BACKGROUND: 100,
}
SOILDEP2_OTHER = {
    CLASS_TREES   : 500,
    CLASS_BARREN  : 100,
    CLASS_CROPS   : 800,
    CLASS_KHARIF  : 700,
    CLASS_BUILTUP : 100,
    CLASS_KH_WATER: 100,
    CLASS_KR_WATER: 100,
    CLASS_KRZ_WATER: 100,
    CLASS_BACKGROUND: 100,
}

NODATA_FLOAT = -9999.0
NODATA_INT   = -9999

# =============================================================================
#  HELPERS
# =============================================================================

def log(msg, kind="INFO"):
    icons = {"INFO": "✔", "STEP": "▶", "WARN": "⚠", "ERROR": "✘", "DONE": "★"}
    print(f"  {icons.get(kind, '·')}  {msg}")


def make_dirs():
    for sub in ["", "/raw", "/maps"]:
        Path(OUTPUT_DIR + sub).mkdir(parents=True, exist_ok=True)


def check_imports():
    missing = []
    for pkg in ["rasterio", "numpy", "scipy", "matplotlib",
                "geopandas", "shapely", "pyproj"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        log(f"Missing packages: {', '.join(missing)}", "ERROR")
        log(f"Run: pip install {' '.join(missing)}", "ERROR")
        sys.exit(1)


def _gdal_convert(tif_path, map_path, pcraster_type="VS_SCALAR"):
    """
    Convert GeoTIFF -> PCRaster .map via gdal_translate.

    pcraster_type:
      VS_SCALAR   — real-valued (float32)
      VS_NOMINAL  — classified integer
      VS_BOOLEAN  — 0/1 mask
    """
    try:
        cmd = [
            "gdal_translate",
            "-of", "PCRaster",
            "-mo", f"PCRASTER_VALUESCALE={pcraster_type}",
            tif_path, map_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(map_path):
            return True
        log(f"gdal_translate stderr: {r.stderr.strip()}", "WARN")
        return False
    except Exception as e:
        log(f"gdal_translate failed: {e}", "WARN")
        return False


def _write_manual_convert_sh(tif_map_pairs):
    """Fallback shell script for manual PCRaster conversion."""
    sh = os.path.join(OUTPUT_DIR, "manual_convert.sh")
    lines = ["#!/bin/bash", "# sudo apt install gdal-bin", ""]
    for tif, map_, ptype in tif_map_pairs:
        lines.append(
            f"gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE={ptype} "
            f"{tif} {map_}"
        )
    with open(sh, "w") as f:
        f.write("\n".join(lines) + "\n")
    log(f"Fallback convert script -> {sh}")


# =============================================================================
#  MASTER GRID — mirrors MasterGrid from topographyMapsScript.py exactly
# =============================================================================

class MasterGrid:
    """
    Canonical spatial definition shared by ALL LISFLOOD input rasters.

    This is an exact replica of the MasterGrid class from the DEM reference
    script (topographyMapsScript.py). Do not modify the alignment logic here
    — it must stay byte-identical to the DEM script's implementation.

    Fields:
      transform — affine transform (top-left origin + pixel size)
      width     — number of columns
      height    — number of rows
      crs       — coordinate reference system
    """

    def __init__(self, transform, width, height, crs):
        self.transform = transform
        self.width     = width
        self.height    = height
        self.crs       = crs

    @property
    def profile(self):
        return {
            "driver":    "GTiff",
            "crs":       self.crs,
            "transform": self.transform,
            "width":     self.width,
            "height":    self.height,
            "count":     1,
            "compress":  "lzw",
        }

    def snap(self, array, nodata_val):
        """Crop or pad array to exactly (height, width)."""
        h, w = array.shape
        if h == self.height and w == self.width:
            return array
        cropped = array[:self.height, :self.width]
        if cropped.shape[0] < self.height or cropped.shape[1] < self.width:
            out = np.full((self.height, self.width), nodata_val, dtype=array.dtype)
            out[:cropped.shape[0], :cropped.shape[1]] = cropped
            return out
        return cropped

    def reproject_array(self, src_array, src_transform, src_crs,
                        src_nodata, dst_nodata, resampling_method):
        """
        Reprojects src_array onto the master grid using exactly:
          master.transform, master.width, master.height, master.crs

        This guarantees identical alignment to every other raster
        produced by this script and the DEM reference script.
        """
        from rasterio.warp import reproject, Resampling
        resamp = {
            "nearest":  Resampling.nearest,
            "bilinear": Resampling.bilinear,
            "mode":     Resampling.mode,
        }[resampling_method]
        dst = np.full((self.height, self.width), dst_nodata, dtype=src_array.dtype)
        reproject(
            source=src_array,        destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=self.transform, dst_crs=self.crs,
            resampling=resamp,
            src_nodata=src_nodata,   dst_nodata=dst_nodata,
        )
        return dst

    def save(self, array, path, dtype, nodata):
        """Save array as GeoTIFF with master grid spatial metadata."""
        import rasterio
        p = self.profile.copy()
        p.update({"dtype": dtype, "nodata": nodata})
        with rasterio.open(path, "w", **p) as dst:
            dst.write(self.snap(array, nodata).astype(dtype), 1)

    def __str__(self):
        return (
            f"MasterGrid | origin=({self.transform.c:.2f}, {self.transform.f:.2f}) | "
            f"{self.width} cols x {self.height} rows | {RESOLUTION_M}m/px | {self.crs}"
        )


# =============================================================================
#  STEP 0 — LOAD MASTER GRID FROM area.tif
# =============================================================================

def load_master_grid(area_tif_path):
    """
    Reads area.tif (output of topographyMapsScript.py) and builds
    a MasterGrid from its exact transform, width, height, and CRS.

    All LULC outputs will be forced onto this exact grid —
    guaranteeing pixel-perfect alignment with the DEM outputs.
    """
    log("STEP 0 — Loading master grid from area.tif", "STEP")
    import rasterio

    if not os.path.exists(area_tif_path):
        log(f"area.tif not found: {area_tif_path}", "ERROR")
        log("Run topographyMapsScript.py first to generate area.tif", "ERROR")
        sys.exit(1)

    with rasterio.open(area_tif_path) as src:
        transform = src.transform
        width     = src.width
        height    = src.height
        crs       = src.crs
        mask_arr  = src.read(1)           # 1 = inside watershed, 0 = outside

    master = MasterGrid(transform, width, height, crs)
    log(f"  {master}")
    log(f"  Inside-mask cells : {int((mask_arr > 0).sum()):,}")

    # Warn if CRS does not match configured target
    if crs.to_epsg() != int(TARGET_CRS.split(":")[1]):
        log(f"  CRS mismatch: area.tif={crs}  expected={TARGET_CRS}", "WARN")
        log("  Proceeding — but verify before running LISFLOOD", "WARN")

    return master, mask_arr


# =============================================================================
#  STEP 1 — EXPORT LULC FROM GOOGLE EARTH ENGINE
# =============================================================================

def export_lulc_from_gee(master):
    """
    Exports the LULC GEE asset to a local GeoTIFF using the Earth Engine
    Python API and geemap.

    The export region is derived from the master grid's transform so the
    downloaded tile at minimum covers the full study area.

    Skips the download if lulc_raw.tif already exists on disk.

    Authentication: run `earthengine authenticate` once before first use.
    """
    log("STEP 1 — Exporting LULC from Google Earth Engine", "STEP")

    raw_tif = LULC_RAW_TIF
    if os.path.exists(raw_tif):
        log(f"  Raw LULC already exists, skipping GEE export: {raw_tif}")
        return raw_tif

    try:
        import ee
        import geemap
    except ImportError:
        log("Missing: earthengine-api  geemap", "ERROR")
        log("Run: pip install earthengine-api geemap", "ERROR")
        sys.exit(1)

    # ── Initialise Earth Engine ────────────────────────────────────────────────
    try:
        ee.Initialize(project='gssha-480613')
        log("  Earth Engine initialized")
    except Exception:
        try:
            ee.Authenticate()
            ee.Initialize(project='gssha-480613')
            log("  Earth Engine authenticated + initialized")
        except Exception as e:
            log(f"  EE init failed: {e}", "ERROR")
            log("  Run: earthengine authenticate", "ERROR")
            sys.exit(1)

    # ── Derive bounding box from master grid (UTM -> WGS84) ───────────────────
    t    = master.transform
    xmin = t.c                              # left  edge (UTM metres)
    ymax = t.f                              # top   edge
    xmax = xmin + t.a * master.width       # right edge (t.a = +pixel width)
    ymin = ymax + t.e * master.height      # bottom edge (t.e = -pixel height)

    log(f"  Master UTM bounds: W={xmin:.1f} S={ymin:.1f} E={xmax:.1f} N={ymax:.1f}")

    import pyproj
    utm_to_wgs84 = pyproj.Transformer.from_crs(TARGET_CRS, "EPSG:4326",
                                                always_xy=True)
    lon_min, lat_min = utm_to_wgs84.transform(xmin, ymin)
    lon_max, lat_max = utm_to_wgs84.transform(xmax, ymax)

    buf = 0.02   # 0.02 degree buffer so GEE edge pixels are not clipped
    region = ee.Geometry.Rectangle(
        [lon_min - buf, lat_min - buf, lon_max + buf, lat_max + buf]
    )
    log(f"  GEE region (WGS84+buffer): "
        f"W={lon_min-buf:.4f} S={lat_min-buf:.4f} "
        f"E={lon_max+buf:.4f} N={lat_max+buf:.4f}")

    # ── Load asset and select band ─────────────────────────────────────────────
    # Assumption: the LULC image has a single band named "b1".
    # Adjust the select() call if the asset uses a different band name.
    lulc_image = ee.Image(GEE_ASSET).select("predicted_label")
    log(f"  Asset: {GEE_ASSET}")

    # ── Download via geemap (handles chunking internally) ─────────────────────
    log(f"  Downloading to: {raw_tif}  (may take several minutes for large areas...)")
    geemap.ee_export_image(
        lulc_image,
        filename=raw_tif,
        scale=GEE_SCALE,
        crs=GEE_CRS,
        region=region,
        file_per_band=False,
    )

    if not os.path.exists(raw_tif):
        log("  GEE export produced no output file", "ERROR")
        sys.exit(1)

    log(f"  LULC exported -> {raw_tif}")
    return raw_tif


# =============================================================================
#  STEP 2 — REPROJECT + ALIGN LULC TO MASTER GRID
# =============================================================================

def align_lulc_to_master(raw_tif, master):
    """
    Reprojects and clips the raw LULC GeoTIFF onto the master grid.

    Resampling: NEAREST NEIGHBOR.
      LULC values are integer class codes. Bilinear/cubic would produce
      fractional values (e.g. 3.7) which are meaningless for categorical
      data. Nearest-neighbor preserves original class labels exactly —
      same approach used for LDD in the DEM reference script.

    The reproject call passes master.transform, master.width, master.height
    as destination grid parameters — identical to get_dem_snapped() in
    topographyMapsScript.py.
    """
    log("STEP 2 — Reprojecting + aligning LULC to master grid", "STEP")
    import rasterio

    aligned_tif = os.path.join(OUTPUT_DIR, "raw", "lulc_aligned.tif")
    if os.path.exists(aligned_tif):
        log(f"  Aligned LULC already exists, loading: {aligned_tif}")
        with rasterio.open(aligned_tif) as src:
            return src.read(1), aligned_tif

    with rasterio.open(raw_tif) as src:
        log(f"  Raw LULC: {src.width}x{src.height}  CRS={src.crs}  res={src.res}")

        raw_arr = src.read(1).astype(np.int16)

        # Normalise nodata
        src_nd  = int(src.nodata) if src.nodata is not None else 255
        raw_arr[raw_arr == src_nd] = NODATA_INT
        raw_arr[raw_arr < 0]       = NODATA_INT   # GEE sometimes writes -1 edges

        # Reproject onto master grid — using master's transform/size/crs
        lulc_aligned = master.reproject_array(
            src_array=raw_arr,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=NODATA_INT,
            dst_nodata=NODATA_INT,
            resampling_method="nearest",
        ).astype(np.int16)

    # Snap shape (handles potential 1-pixel rounding from reprojection)
    lulc_aligned = master.snap(lulc_aligned, NODATA_INT).astype(np.int16)

    master.save(lulc_aligned, aligned_tif, "int16", NODATA_INT)

    # Print class distribution
    class_names = {
        0: "Background", 1: "Built-up", 2: "Kharif water",
        3: "Kharif+rabi water", 4: "Kharif+rabi+zaid water",
        5: "Crops", 6: "Trees", 7: "Barren", 8: "Single Kharif",
    }
    valid = lulc_aligned[lulc_aligned != NODATA_INT]
    unique, counts = np.unique(valid, return_counts=True)
    log("  Class distribution after alignment:")
    for cls, cnt in zip(unique, counts):
        pct  = 100.0 * cnt / lulc_aligned.size
        name = class_names.get(int(cls), "Unknown")
        log(f"    Class {int(cls):2d}  ({name:<30}): {cnt:8,} px  ({pct:.1f}%)")

    log(f"  -> {aligned_tif}")
    return lulc_aligned, aligned_tif


# =============================================================================
#  STEP 3 — BUILD FRACTION MAPS
# =============================================================================

def build_fraction_maps(lulc_arr, mask_arr, master):
    """
    Derives the four LISFLOOD land-use fraction maps.

    At 30 m resolution each pixel belongs to exactly one class,
    so each fraction is binary (0 or 1). The formula generalises to
    sub-pixel fractions when finer LULC is aggregated to a coarser
    model grid — LISFLOOD handles both cases identically.

    Class groupings:
      fracwater  = union of WATER_CLASSES  (kharif/rabi/zaid water bodies)
      fracsealed = union of SEALED_CLASSES (built-up / impermeable surface)
      fracforest = union of FOREST_CLASSES (trees)
      fracother  = union of OTHER_CLASSES  (crops, barren, kharif cropland)
    """
    log("STEP 3 — Building fraction maps", "STEP")

    inside = mask_arr > 0
    nd     = NODATA_FLOAT

    def make_frac(class_set, name):
        arr = np.where(
            inside,
            np.isin(lulc_arr, list(class_set)).astype(np.float32),
            nd,
        ).astype(np.float32)
        valid = arr[inside]
        log(f"  {name:<18}: mean={valid.mean():.4f}  "
            f"active cells={int((valid > 0).sum()):,}")
        return arr

    fracs = {
        "fracwater":  make_frac(WATER_CLASSES,  "fracwater"),
        "fracsealed": make_frac(SEALED_CLASSES, "fracsealed"),
        "fracforest": make_frac(FOREST_CLASSES, "fracforest"),
        "fracother":  make_frac(OTHER_CLASSES,  "fracother"),
    }

    # Sanity: sum of fractions inside mask must be <= 1.0 per pixel
    total   = sum(np.where(inside, fracs[k], 0.0) for k in fracs)
    max_sum = float(total[inside].max()) if inside.any() else 0.0
    log(f"  Max fraction sum per pixel: {max_sum:.4f}  (must be <= 1.0)")
    if max_sum > 1.001:
        log("  Fraction sum > 1.0 — review class groupings!", "WARN")

    return fracs


# =============================================================================
#  STEP 4 — BUILD LAND-COVER-DEPENDENT MAPS
# =============================================================================

def _apply_lut(lulc_arr, mask_arr, lut, default_val, name):
    """
    Maps each LULC class ID to a scalar value using the look-up table.

    Pixels inside the mask with no matching LUT entry receive default_val.
    Pixels outside the mask receive NODATA_FLOAT.
    """
    out    = np.full(lulc_arr.shape, NODATA_FLOAT, dtype=np.float32)
    inside = mask_arr > 0

    for cls_id, val in lut.items():
        out[inside & (lulc_arr == cls_id)] = float(val)

    # Any unmatched inside-mask pixel gets the fallback default
    unmatched = inside & (out == NODATA_FLOAT)
    out[unmatched] = float(default_val)

    valid = out[inside]
    log(f"  {name:<30}: min={valid.min():.3f}  max={valid.max():.3f}  "
        f"mean={valid.mean():.3f}")
    return out


def build_lc_dependent_maps(lulc_arr, mask_arr):
    """
    Produces all ten land-cover-dependent LISFLOOD inputs using the
    per-class look-up tables defined in the SETTINGS section.

    Returns dict of {lisflood_name: numpy_float32_array}.
    """
    log("STEP 4 — Building land-cover-dependent maps", "STEP")

    maps = {}

    # Crop coefficients (0.8 – 1.2)
    maps["cropcoef_forest"] = _apply_lut(
        lulc_arr, mask_arr, CROPCOEF_FOREST, 1.0, "cropcoef_forest")
    maps["cropcoef_other"]  = _apply_lut(
        lulc_arr, mask_arr, CROPCOEF_OTHER, 1.0,  "cropcoef_other")

    # Crop group numbers (1 – 5, FAO)
    maps["crgrnum_forest"] = _apply_lut(
        lulc_arr, mask_arr, CRGRNUM_FOREST, 3, "crgrnum_forest")
    maps["crgrnum_other"]  = _apply_lut(
        lulc_arr, mask_arr, CRGRNUM_OTHER, 3,  "crgrnum_other")

    # Manning's roughness
    maps["mannings_forest"] = _apply_lut(
        lulc_arr, mask_arr, MANNINGS_FOREST, 0.25, "mannings_forest")
    maps["mannings_other"]  = _apply_lut(
        lulc_arr, mask_arr, MANNINGS_OTHER, 0.05,  "mannings_other")

    # Soil depth layer-1 (rooting, mm)
    maps["soildep1_forest"] = _apply_lut(
        lulc_arr, mask_arr, SOILDEP1_FOREST, 200, "soildep1_forest")
    maps["soildep1_other"]  = _apply_lut(
        lulc_arr, mask_arr, SOILDEP1_OTHER, 200,  "soildep1_other")

    # Soil depth layer-2 (sub-rooting, mm)
    maps["soildep2_forest"] = _apply_lut(
        lulc_arr, mask_arr, SOILDEP2_FOREST, 500, "soildep2_forest")
    maps["soildep2_other"]  = _apply_lut(
        lulc_arr, mask_arr, SOILDEP2_OTHER, 500,  "soildep2_other")

    return maps


# =============================================================================
#  STEP 5 — SAVE GeoTIFFs + CONVERT TO PCRaster .map
# =============================================================================

def save_and_convert(fracs, lc_maps, master):
    """
    Saves all arrays as GeoTIFFs (using master.save — identical transform)
    then converts each to PCRaster .map via gdal_translate.

    PCRaster value-scale mapping:
      fracwater / fracsealed / fracforest / fracother -> VS_SCALAR
      cropcoef_*        -> VS_SCALAR
      crgrnum_*         -> VS_NOMINAL  (integer, class-coded)
      mannings_*        -> VS_SCALAR
      soildep*          -> VS_SCALAR   (mm, float)
    """
    log("STEP 5 — Saving GeoTIFFs and converting to PCRaster .map", "STEP")

    maps_dir      = os.path.join(OUTPUT_DIR, "maps")
    tif_map_pairs = []   # for manual_convert.sh
    all_tifs      = {}

    # ── Fraction maps — float32, VS_SCALAR ────────────────────────────────────
    for name, arr in fracs.items():
        tif  = os.path.join(maps_dir, f"{name}.tif")
        map_ = os.path.join(maps_dir, f"{name}.map")
        master.save(arr, tif, "float32", NODATA_FLOAT)
        ok = _gdal_convert(tif, map_, "VS_SCALAR")
        all_tifs[name] = tif
        tif_map_pairs.append((tif, map_, "VS_SCALAR"))
        log(f"  {name}.map  {'✔' if ok else '⚠ run manual_convert.sh'}")

    # ── Land-cover-dependent maps ─────────────────────────────────────────────
    pcraster_types = {
        "cropcoef_forest" : "VS_SCALAR",
        "cropcoef_other"  : "VS_SCALAR",
        "crgrnum_forest"  : "VS_NOMINAL",
        "crgrnum_other"   : "VS_NOMINAL",
        "mannings_forest" : "VS_SCALAR",
        "mannings_other"  : "VS_SCALAR",
        "soildep1_forest" : "VS_SCALAR",
        "soildep1_other"  : "VS_SCALAR",
        "soildep2_forest" : "VS_SCALAR",
        "soildep2_other"  : "VS_SCALAR",
    }

    for name, arr in lc_maps.items():
        ptype = pcraster_types[name]
        if "crgrnum" in name:
            # Nominal: store as int16 so class codes are preserved
            save_dtype = "int16"
            save_nd    = NODATA_INT
            save_arr   = arr.astype(np.int16)
        else:
            save_dtype = "float32"
            save_nd    = NODATA_FLOAT
            save_arr   = arr

        tif  = os.path.join(maps_dir, f"{name}.tif")
        map_ = os.path.join(maps_dir, f"{name}.map")
        master.save(save_arr, tif, save_dtype, save_nd)
        ok = _gdal_convert(tif, map_, ptype)
        all_tifs[name] = tif
        tif_map_pairs.append((tif, map_, ptype))
        log(f"  {name}.map  {'✔' if ok else '⚠ run manual_convert.sh'}")

    _write_manual_convert_sh(tif_map_pairs)
    return all_tifs


# =============================================================================
#  STEP 6 — VALIDATE ALIGNMENT (MANDATORY)
# =============================================================================

def validate_alignment(all_tifs, master):
    """
    Opens every output GeoTIFF and verifies four alignment properties:
      1. CRS   matches master (EPSG code comparison)
      2. Width x Height match master
      3. Transform origin matches master (to 0.01 m tolerance)
      4. Pixel size matches RESOLUTION_M (to 0.01 m tolerance)

    Prints a proof table in the same format as topographyMapsScript.py.
    EXITS with code 1 if any file fails — misaligned inputs will cause
    LISFLOOD to crash or produce wrong results.
    """
    log("STEP 6 — Validating alignment of all outputs", "STEP")
    import rasterio
    from rasterio.crs import CRS

    expected_epsg = int(TARGET_CRS.split(":")[1])
    errors = []

    print()
    header = f"  {'File':<32} {'Origin (E, N)':<32} {'Size':<16} {'Pixel'}"
    print(header)
    print(f"  {'-'*32} {'-'*32} {'-'*16} {'-'*8}")

    for name, path in all_tifs.items():
        if not os.path.exists(path):
            errors.append(f"  {name}: FILE MISSING — {path}")
            continue

        with rasterio.open(path) as src:
            t = src.transform

            ok_crs    = src.crs.to_epsg() == expected_epsg
            ok_width  = src.width  == master.width
            ok_height = src.height == master.height
            ok_xori   = abs(t.c - master.transform.c) < 0.01
            ok_yori   = abs(t.f - master.transform.f) < 0.01
            ok_res    = abs(t.a - RESOLUTION_M)        < 0.01
            ok_all    = ok_crs and ok_width and ok_height and ok_xori and ok_yori and ok_res

            status = "✔" if ok_all else "✘"
            fname  = name + ".tif"
            print(f"  {status} {fname:<30} "
                  f"({t.c:.2f}, {t.f:.2f}){'':<10} "
                  f"{src.width}x{src.height}{'':<4} "
                  f"{t.a:.1f}m")

            if not ok_all:
                if not ok_crs:
                    errors.append(
                        f"    {name}: CRS EPSG:{src.crs.to_epsg()} != "
                        f"EPSG:{expected_epsg}")
                if not ok_width or not ok_height:
                    errors.append(
                        f"    {name}: size {src.width}x{src.height} != "
                        f"{master.width}x{master.height}")
                if not ok_xori or not ok_yori:
                    errors.append(
                        f"    {name}: origin ({t.c:.3f},{t.f:.3f}) != "
                        f"({master.transform.c:.3f},{master.transform.f:.3f})")
                if not ok_res:
                    errors.append(
                        f"    {name}: pixel {t.a:.3f}m != {RESOLUTION_M}m")

    print()

    if errors:
        log("ALIGNMENT FAILURES — LISFLOOD will reject these inputs:", "ERROR")
        for e in errors:
            print(e)
        sys.exit(1)

    log(f"  All {len(all_tifs)} files pass alignment check — grid is consistent")


# =============================================================================
#  STEP 7 — VISUAL CHECK
# =============================================================================

def visualize(lulc_arr, fracs, mask_arr, master):
    """
    Generates LULC_VISUAL_CHECK.png for a quick sanity check.
    Shows the classified LULC map alongside the four fraction maps.
    """
    log("STEP 7 — Generating visual check PNG", "STEP")
    try:
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors

        fig, axes = plt.subplots(2, 3, figsize=(18, 11))
        fig.suptitle(
            f"LISFLOOD LULC Maps — pan_india_lulc_v3_2024_2025\n"
            f"Grid: {master.width}x{master.height} cells | "
            f"{RESOLUTION_M}m | {TARGET_CRS}\n"
            f"Origin: ({master.transform.c:.1f} E, {master.transform.f:.1f} N)",
            fontsize=10, fontweight="bold",
        )

        def panel(ax, data, title, cmap, label, vmin=None, vmax=None, nodata=-9000):
            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan
            kw = {}
            if vmin is not None:
                kw["vmin"] = vmin
            if vmax is not None:
                kw["vmax"] = vmax
            im = ax.imshow(d, cmap=cmap, interpolation="nearest", **kw)
            plt.colorbar(im, ax=ax, label=label, shrink=0.8)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        # ── LULC class map ────────────────────────────────────────────────────
        lulc_vis = lulc_arr.astype(np.float32).copy()
        lulc_vis[mask_arr == 0] = np.nan

        lulc_colors = [
            "#808080",  # 0 Background
            "#e31a1c",  # 1 Built-up
            "#a6cee3",  # 2 Kharif water
            "#1f78b4",  # 3 Kharif+rabi water
            "#33a02c",  # 4 Kharif+rabi+zaid water
            "#b2df8a",  # 5 Crops
            "#006400",  # 6 Trees
            "#d2b48c",  # 7 Barren
            "#ffff99",  # 8 Single kharif
        ]
        cmap_lulc  = mcolors.ListedColormap(lulc_colors)
        norm_lulc  = mcolors.BoundaryNorm(np.arange(-0.5, 9, 1), cmap_lulc.N)
        im = axes[0, 0].imshow(lulc_vis, cmap=cmap_lulc, norm=norm_lulc,
                                interpolation="nearest")
        cb = plt.colorbar(im, ax=axes[0, 0], shrink=0.8)
        cb.set_ticks(range(9))
        cb.set_ticklabels(
            ["BG", "Built", "KhW", "KrW", "KRZ", "Crops", "Trees", "Barren", "KhCrop"],
            fontsize=7,
        )
        axes[0, 0].set_title("LULC Classes", fontsize=9, fontweight="bold")
        axes[0, 0].axis("off")

        # ── Four fraction maps ────────────────────────────────────────────────
        frac_cfg = [
            ("fracwater",  "fracwater.map\n(inland water fraction)",   "Blues"),
            ("fracsealed", "fracsealed.map\n(impermeable / built-up)", "Reds"),
            ("fracforest", "fracforest.map\n(forest fraction)",        "Greens"),
            ("fracother",  "fracother.map\n(crops + barren + other)",  "YlOrBr"),
        ]
        pos_list = [(0, 1), (0, 2), (1, 0), (1, 1)]
        for (r, c), (key, title, cmap) in zip(pos_list, frac_cfg):
            panel(axes[r, c], fracs[key], title, cmap, "fraction (0-1)",
                  vmin=0, vmax=1)

        # ── Summary / alignment proof ─────────────────────────────────────────
        ax = axes[1, 2]
        ax.axis("off")
        summary = "\n".join([
            "ALIGNMENT PROOF",
            "=" * 30,
            "",
            f"  Rows   : {master.height}",
            f"  Cols   : {master.width}",
            f"  Pixel  : {RESOLUTION_M}m x {RESOLUTION_M}m",
            f"  CRS    : {TARGET_CRS}",
            f"  Origin :",
            f"    E = {master.transform.c:.2f} m",
            f"    N = {master.transform.f:.2f} m",
            "",
            "=" * 30,
            "",
            "LISFLOOD .ini keys:",
            "  FracWater  = fracwater.map",
            "  FracSealed = fracsealed.map",
            "  FracForest = fracforest.map",
            "  FracOther  = fracother.map",
        ])
        ax.text(0.05, 0.97, summary, transform=ax.transAxes,
                fontsize=8.5, va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor="#fffde7", alpha=0.9))

        plt.tight_layout()
        out_png = os.path.join(OUTPUT_DIR, "LULC_VISUAL_CHECK.png")
        plt.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  -> {out_png}")

    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")


# =============================================================================
#  SUMMARY
# =============================================================================

def print_summary(master, all_tifs):
    maps_dir = os.path.join(OUTPUT_DIR, "maps")

    print("\n" + "=" * 68)
    print("  DONE — LISFLOOD LULC maps generated and validated")
    print("=" * 68)
    print(f"\n  Grid   : {master.width} cols x {master.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS    : {TARGET_CRS}")
    print(f"  Origin : ({master.transform.c:.2f} m E, {master.transform.f:.2f} m N)")
    print(f"\n  Output : {maps_dir}/\n")

    print("  Fraction maps (LISFLOOD Table A12.1 — Land Use):")
    for k in ["fracwater", "fracsealed", "fracforest", "fracother"]:
        mp = os.path.join(maps_dir, f"{k}.map")
        print(f"    {k}.map   {'✔' if os.path.exists(mp) else '⚠ run manual_convert.sh'}")

    print()
    print("  Land Cover dependent maps:")
    lc_keys = [
        "cropcoef_forest", "cropcoef_other",
        "crgrnum_forest",  "crgrnum_other",
        "mannings_forest", "mannings_other",
        "soildep1_forest", "soildep1_other",
        "soildep2_forest", "soildep2_other",
    ]
    for k in lc_keys:
        mp = os.path.join(maps_dir, f"{k}.map")
        print(f"    {k}.map   {'✔' if os.path.exists(mp) else '⚠ run manual_convert.sh'}")

    print()
    print(f"  Visual : {OUTPUT_DIR}/LULC_VISUAL_CHECK.png")
    print("=" * 68 + "\n")


# =============================================================================
#  MAIN
# =============================================================================

def main():
    print("\n" + "=" * 68)
    print("  LISFLOOD LULC MAP GENERATOR")
    print(f"  Source : pan_india_lulc_v3_2024_2025 (GEE asset)")
    print(f"  Grid   : {RESOLUTION_M}m  |  {TARGET_CRS}")
    print(f"  Ref    : {AREA_TIF}")
    print("=" * 68 + "\n")

    check_imports()
    make_dirs()

    # Step 0 — Load master grid from area.tif (all outputs must match this)
    master, mask_arr = load_master_grid(AREA_TIF)

    # Step 1 — Export LULC from GEE to local GeoTIFF
    raw_tif = export_lulc_from_gee(master)

    # Step 2 — Reproject + nearest-neighbor resample onto master grid
    lulc_arr, aligned_tif = align_lulc_to_master(raw_tif, master)

    # Step 3 — Build binary fraction maps (fracwater, fracsealed, fracforest, fracother)
    fracs = build_fraction_maps(lulc_arr, mask_arr, master)

    # Step 4 — Build scalar/nominal land-cover-dependent maps
    lc_maps = build_lc_dependent_maps(lulc_arr, mask_arr)

    # Step 5 — Save GeoTIFFs and convert to PCRaster .map
    all_tifs = save_and_convert(fracs, lc_maps, master)

    # Step 6 — Validate alignment (mandatory — exits on failure)
    validate_alignment(all_tifs, master)

    # Step 7 — Generate LULC_VISUAL_CHECK.png
    visualize(lulc_arr, fracs, mask_arr, master)

    # Summary table
    print_summary(master, all_tifs)


if __name__ == "__main__":
    main()
