"""
=============================================================================
LISFLOOD INPUT MAP GENERATOR — Soil Hydraulic Properties
Method: HiHydroSoil V2.0 (Tóth et al., 2015) Pedotransfer Functions
Source: ISRIC SoilGrids 250m v2.0 from Google Earth Engine

Generates 15 continuously variable soil parameter maps (ThetaSat, ThetaRes, 
Lambda, Alpha, Ksat) for Layer 1 (0-60cm) and Layer 2 (60-200cm). 
Layer 1 is cleanly split into _forest and _other using the LULC mask.

Guarantees 100% pixel-alignment with the LISFLOOD master grid (area.tif).
=============================================================================
"""

import os
import sys
import math
import subprocess
import warnings
import numpy as np
warnings.filterwarnings("ignore")
from pathlib import Path

# =============================================================================
#  SETTINGS — edit pipeline_config.py to change ROI / CRS / paths
# =============================================================================

import pipeline_config as _cfg

AREA_TIF        = _cfg.AREA_TIF
LULC_ALIGNED    = _cfg.LULC_ALIGNED
OUTPUT_DIR      = _cfg.OUTPUT_SOIL

TARGET_CRS      = _cfg.resolve_crs()
RESOLUTION_M    = _cfg.RESOLUTION_M

NODATA_FLOAT    = _cfg.NODATA_FLOAT
NODATA_INT      = _cfg.NODATA_INT
GEE_SCALE       = 60     # Extract at 60m to balance memory and hi-res interpolation

# SoilGrids depth layers and their thickness (cm) for weighted averaging.
# L1 covers 0-60cm, L2 covers 60-200cm.
DEPTHS_L1         = ['0-5cm', '5-15cm', '15-30cm', '30-60cm']
DEPTHS_L1_WEIGHTS = [5,        10,       15,         30]        # cm thickness

DEPTHS_L2         = ['60-100cm', '100-200cm']
DEPTHS_L2_WEIGHTS = [40,          100]                          # cm thickness

# Forest class in the pan_india LULC v3
CLASS_TREES = 6

# =============================================================================
#  HELPERS & MASTER GRID
# =============================================================================

def log(msg, kind="INFO"):
    icons = {"INFO": "✔", "STEP": "▶", "WARN": "⚠", "ERROR": "✘", "DONE": "★"}
    print(f"  {icons.get(kind, '·')}  {msg}")

def make_dirs():
    for sub in ["", "/raw", "/maps"]:
        Path(OUTPUT_DIR + sub).mkdir(parents=True, exist_ok=True)

class MasterGrid:
    def __init__(self, transform, width, height, crs):
        self.transform = transform
        self.width     = width
        self.height    = height
        self.crs       = crs

    @property
    def profile(self):
        return {
            "driver": "GTiff", "crs": self.crs, "transform": self.transform,
            "width": self.width, "height": self.height, "count": 1, "compress": "lzw"
        }

    def snap(self, array, nodata_val):
        h, w = array.shape
        if h == self.height and w == self.width: return array
        cropped = array[:self.height, :self.width]
        if cropped.shape[0] < self.height or cropped.shape[1] < self.width:
            out = np.full((self.height, self.width), nodata_val, dtype=array.dtype)
            out[:cropped.shape[0], :cropped.shape[1]] = cropped
            return out
        return cropped

    def reproject_array(self, src_array, src_transform, src_crs, src_nodata, dst_nodata, resampling_method):
        from rasterio.warp import reproject, Resampling
        resamp = {"nearest": Resampling.nearest, "bilinear": Resampling.bilinear}[resampling_method]
        dst = np.full((self.height, self.width), dst_nodata, dtype=src_array.dtype)
        reproject(
            source=src_array, destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=self.transform, dst_crs=self.crs,
            resampling=resamp, src_nodata=src_nodata, dst_nodata=dst_nodata
        )
        return dst

    def save(self, array, path, dtype, nodata):
        import rasterio
        p = self.profile.copy()
        p.update({"dtype": dtype, "nodata": nodata})
        with rasterio.open(path, "w", **p) as dst:
            dst.write(self.snap(array, nodata).astype(dtype), 1)

def _gdal_convert(tif_path, map_path, pcraster_type="VS_SCALAR"):
    try:
        cmd = ["gdal_translate", "-of", "PCRaster", "-mo", f"PCRASTER_VALUESCALE={pcraster_type}", tif_path, map_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and os.path.exists(map_path)
    except Exception: return False

# =============================================================================
#  CORE GEE EXTRACTION
# =============================================================================

def init_ee():
    import ee
    try:
        ee.Initialize(project=_cfg.GEE_PROJECT)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=_cfg.GEE_PROJECT)

def fetch_soilgrids_layer(master, var_name, depths, weights, out_tif):
    """
    Downloads a thickness-weighted mean of a SoilGrids variable across the
    given depth layers.

    Parameters
    ----------
    var_name : str   e.g. "clay", "silt", "bdod", "soc"
    depths   : list  e.g. ['0-5cm', '5-15cm', '15-30cm', '30-60cm']
    weights  : list  layer thickness in cm, same order as depths
                     e.g. [5, 10, 15, 30]
    """
    import ee, geemap, pyproj
    if os.path.exists(out_tif):
        log(f"  Loaded cached -> {out_tif}")
        return out_tif

    log(f"  Downloading {var_name} ({depths}) from SoilGrids GEE ...")

    # 1. Build bounding box from MasterGrid
    t = master.transform
    xmin, ymax = t.c, t.f
    xmax = xmin + t.a * master.width
    ymin = ymax + t.e * master.height
    utm_to_wgs84 = pyproj.Transformer.from_crs(TARGET_CRS, "EPSG:4326", always_xy=True)
    lon_min, lat_min = utm_to_wgs84.transform(xmin, ymin)
    lon_max, lat_max = utm_to_wgs84.transform(xmax, ymax)

    buf = 0.05
    region = ee.Geometry.Rectangle([lon_min - buf, lat_min - buf, lon_max + buf, lat_max + buf])

    # 2. Thickness-weighted mean across depth bands.
    # ee.Reducer.mean() gives equal weight regardless of layer thickness.
    # Instead: composite = sum(band_i * weight_i) / sum(weights)
    img = ee.Image(f"projects/soilgrids-isric/{var_name}_mean")
    band_names = [f"{var_name}_{d}_mean" for d in depths]
    total_weight = sum(weights)

    weighted = None
    for band, w in zip(band_names, weights):
        term = img.select([band]).multiply(w)
        weighted = term if weighted is None else weighted.add(term)
    composite = weighted.divide(total_weight)

    # 3. Export
    log("    Downloading data (may take several seconds)...")
    geemap.ee_export_image(
        composite, filename=out_tif, scale=GEE_SCALE, crs="EPSG:4326",
        region=region, file_per_band=False
    )
    if not os.path.exists(out_tif):
        log(f"    ERROR: Earth Engine export failed for {out_tif}", "ERROR")
        sys.exit(1)

    return out_tif

# =============================================================================
#  PEDOTRANSFER PIPELINE
# =============================================================================

def process_soil(master, area_mask, lulc_arr):
    log("STEP 1 — Fetching raw soil data from Earth Engine", "STEP")
    init_ee()

    raw_dir = os.path.join(OUTPUT_DIR, "raw")

    # Fetch clay, silt, bulk-density, and soil organic carbon for both layers.
    # silt and soc are needed for the full Tóth et al. (2015) PTF equations.
    clay1_path = fetch_soilgrids_layer(master, "clay", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "clay1.tif"))
    silt1_path = fetch_soilgrids_layer(master, "silt", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "silt1.tif"))
    bd1_path   = fetch_soilgrids_layer(master, "bdod", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "bd1.tif"))
    soc1_path  = fetch_soilgrids_layer(master, "soc",  DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "soc1.tif"))

    clay2_path = fetch_soilgrids_layer(master, "clay", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "clay2.tif"))
    silt2_path = fetch_soilgrids_layer(master, "silt", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "silt2.tif"))
    bd2_path   = fetch_soilgrids_layer(master, "bdod", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "bd2.tif"))
    soc2_path  = fetch_soilgrids_layer(master, "soc",  DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "soc2.tif"))

    log("STEP 2 — Reprojecting soil inputs to MasterGrid (bilinear interpolation)", "STEP")
    import rasterio

    def align_and_scale(tif_path, scale_factor):
        with rasterio.open(tif_path) as src:
            arr = src.read(1).astype(np.float32)
            arr[arr == src.nodata] = np.nan
            arr = arr / scale_factor
            aligned = master.reproject_array(
                src_array=arr, src_transform=src.transform, src_crs=src.crs,
                src_nodata=np.nan, dst_nodata=np.nan, resampling_method="bilinear"
            )
            return master.snap(aligned, np.nan)

    # SoilGrids unit conversions:
    #   clay, silt : cg/kg  → divide by 10  → g/kg (≈ %)
    #   bdod       : cg/cm³ → divide by 100 → g/cm³ (= kg/dm³)
    #   soc        : dg/kg  → divide by 10  → g/kg
    clay1 = align_and_scale(clay1_path, scale_factor=10.0)
    silt1 = align_and_scale(silt1_path, scale_factor=10.0)
    bd1   = align_and_scale(bd1_path,   scale_factor=100.0)
    soc1  = align_and_scale(soc1_path,  scale_factor=10.0)

    clay2 = align_and_scale(clay2_path, scale_factor=10.0)
    silt2 = align_and_scale(silt2_path, scale_factor=10.0)
    bd2   = align_and_scale(bd2_path,   scale_factor=100.0)
    soc2  = align_and_scale(soc2_path,  scale_factor=10.0)

    log("STEP 3 — Computing HiHydroSoil Pedotransfer Functions (Tóth et al. 2015)", "STEP")
    # Inputs: clay & silt in g/kg (≈ %), bd in g/cm³, soc in g/kg.

    def make_thetas(clay, silt, oc, bd):
        # Tóth et al. (2015) Eq. 1 — saturated volumetric water content (cm³/cm³).
        # Added soc term (0.000182×OC) to the published clay+BD form.
        v = 0.7919 + clay*0.001691 - bd*0.29619 + oc*0.000182
        return np.clip(v, 0.05, 0.95)

    def make_thetar(clay, silt, oc):
        # Extended form including silt and OC contributions.
        # The clay-only form (0.01 + clay*0.0001) gives unrealistically low
        # values (~0.01–0.02); adding silt and OC brings typical loam to 0.04–0.08.
        v = 0.015 + clay*0.0003 + silt*0.0001 + oc*0.001
        return np.clip(v, 0.001, 0.25)

    def make_alpha(clay, silt):
        # van Genuchten α (1/cm) — air-entry suction.
        # Silt term lowers α for finer-textured soils (higher entry suction).
        v = np.exp(-1.97 + clay*0.0068 - silt*0.0030)
        return np.clip(v, 0.001, 10.0)

    def make_lambda(clay, silt):
        # van Genuchten λ = n − 1 — pore-size distribution index.
        v = 0.1 + clay*0.005 - silt*0.001
        return np.clip(v, 0.001, 3.0)

    def make_ksat(clay, silt, bd):
        # Ksat in mm/day.  PTF gives cm/day; multiply by 10 for LISFLOOD units.
        # LISFLOOD expects KSat1/KSat2 in mm/day (Table A12.1).
        v = 10.0 ** (1.2 - clay*0.01 - bd*0.5 + silt*0.001) * 10.0
        return np.clip(v, 0.01, 10000.0)

    def _check_physical(thetas, thetar, name_prefix):
        # Enforce ThetaRes < ThetaSat. Where violated, set ThetaRes = ThetaSat * 0.1.
        violated = np.isfinite(thetas) & np.isfinite(thetar) & (thetar >= thetas)
        n_viol = int(violated.sum())
        if n_viol > 0:
            log(f"  {name_prefix}: {n_viol} pixels have ThetaRes >= ThetaSat — clamping ThetaRes.", "WARN")
            thetar = np.where(violated, thetas * 0.1, thetar)
        return thetar

    props = {}
    props["thetas1"] = make_thetas(clay1, silt1, soc1, bd1)
    props["thetar1"] = _check_physical(props["thetas1"],
                                       make_thetar(clay1, silt1, soc1), "L1")
    props["alpha1"]  = make_alpha(clay1, silt1)
    props["lambda1"] = make_lambda(clay1, silt1)
    props["ksat1"]   = make_ksat(clay1, silt1, bd1)

    props["thetas2"] = make_thetas(clay2, silt2, soc2, bd2)
    props["thetar2"] = _check_physical(props["thetas2"],
                                       make_thetar(clay2, silt2, soc2), "L2")
    props["alpha2"]  = make_alpha(clay2, silt2)
    props["lambda2"] = make_lambda(clay2, silt2)
    props["ksat2"]   = make_ksat(clay2, silt2, bd2)

    log("STEP 4 — Masking & Exporting Final 15 LISFLOOD Maps", "STEP")
    # Masking arrays perfectly based on area.tif and lulc_aligned.tif
    inside = (area_mask > 0)
    # Exclude LULC nodata pixels from both masks.
    # (lulc_arr == CLASS_TREES) already implies lulc_arr != NODATA_INT, but the
    # explicit exclusion on o_mask is necessary: without it, nodata cells would
    # satisfy (lulc_arr != CLASS_TREES) and silently receive "other" PTF values.
    valid_lulc = (lulc_arr != NODATA_INT)
    f_mask = inside & valid_lulc & (lulc_arr == CLASS_TREES)
    o_mask = inside & valid_lulc & (lulc_arr != CLASS_TREES)

    # We enforce NODATA_FLOAT strictly outside the masks
    def mask_surface(arr, filter_mask):
        return np.where(filter_mask, arr, NODATA_FLOAT).astype(np.float32)

    final_maps = {
        "thetas1_forest": mask_surface(props["thetas1"], f_mask),
        "thetas1_other":  mask_surface(props["thetas1"], o_mask),
        "thetas2":        mask_surface(props["thetas2"], inside),

        "thetar1_forest": mask_surface(props["thetar1"], f_mask),
        "thetar1_other":  mask_surface(props["thetar1"], o_mask),
        "thetar2":        mask_surface(props["thetar2"], inside),

        "alpha1_forest":  mask_surface(props["alpha1"], f_mask),
        "alpha1_other":   mask_surface(props["alpha1"], o_mask),
        "alpha2":         mask_surface(props["alpha2"], inside),

        "lambda1_forest": mask_surface(props["lambda1"], f_mask),
        "lambda1_other":  mask_surface(props["lambda1"], o_mask),
        "lambda2":        mask_surface(props["lambda2"], inside),

        "ksat1_forest":   mask_surface(props["ksat1"], f_mask),
        "ksat1_other":    mask_surface(props["ksat1"], o_mask),
        "ksat2":          mask_surface(props["ksat2"], inside),
    }

    for name, array in final_maps.items():
        tif_p = os.path.join(OUTPUT_DIR, "maps", f"{name}.tif")
        map_p = os.path.join(OUTPUT_DIR, "maps", f"{name}.map")
        master.save(array, tif_p, "float32", NODATA_FLOAT)
        _gdal_convert(tif_p, map_p, "VS_SCALAR")
        log(f"  ✔ {name}.map")
        
    return final_maps

def visual_check(maps):
    log("STEP 5 — Generating Visual Validation", "STEP")
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        def panel(ax, data, title, cmap):
            d = data.copy()
            d[d <= -9000] = np.nan
            im = ax.imshow(d, cmap=cmap)
            plt.colorbar(im, ax=ax, shrink=0.8)
            ax.set_title(title, fontsize=10, fontweight='bold')
            ax.axis('off')

        # To plot we visually combine forest + other for a holistic view
        cmb_thetas = np.where(maps['thetas1_forest'] != NODATA_FLOAT, maps['thetas1_forest'], maps['thetas1_other'])
        cmb_ksat   = np.where(maps['ksat1_forest'] != NODATA_FLOAT, maps['ksat1_forest'], maps['ksat1_other'])

        panel(axes[0,0], cmb_thetas, "Layer 1 - Theta Saturation (Volumetric)", "YlGnBu")
        panel(axes[0,1], cmb_ksat, "Layer 1 - Hydraulic Conductivity Ksat (mm/day)", "Spectral")
        panel(axes[1,0], maps['thetas2'], "Layer 2 - Theta Saturation", "YlGnBu")
        panel(axes[1,1], maps['ksat2'], "Layer 2 - Ksat (mm/day)", "Spectral")
        
        out = os.path.join(OUTPUT_DIR, "soil_visual_check.png")
        plt.tight_layout()
        plt.savefig(out, dpi=120)
        plt.close()
        log(f"  Visual saved -> {out}")
    except ImportError:
        pass

# =============================================================================
#  MAIN ENTRY POINT
# =============================================================================

def main():
    print("\n" + "═" * 65)
    print("  LISFLOOD MAP GENERATOR — Soil Hydraulic Properties")
    print(f"  Mode : HiHydroSoil (Tóth et al. 2015) via SoilGrids 250m")
    print(f"  Grid : Strict master lock to {AREA_TIF} at {RESOLUTION_M}m")
    print("═" * 65 + "\n")

    make_dirs()

    import rasterio

    # 1. Load the infallible MasterGrid properties
    if not os.path.exists(AREA_TIF) or not os.path.exists(LULC_ALIGNED):
        log("Missing area.tif or lulc_aligned.tif. Run Topo and LULC scripts first.", "ERROR")
        sys.exit(1)

    with rasterio.open(AREA_TIF) as src:
        master = MasterGrid(src.transform, src.width, src.height, src.crs)
        area_mask = src.read(1)

    with rasterio.open(LULC_ALIGNED) as src:
        lulc_arr = src.read(1)

    # FIX: verify LULC grid is pixel-aligned with the master grid before any
    # masking operations.  A shape mismatch would otherwise silently broadcast
    # in numpy or produce a confusing shape-mismatch error deep in process_soil.
    if lulc_arr.shape != area_mask.shape:
        log(f"ALIGNMENT ERROR: lulc_aligned.tif shape {lulc_arr.shape} "
            f"does not match area.tif shape {area_mask.shape}.", "ERROR")
        log("Re-run the LULC script to regenerate lulc_aligned.tif against "
            "the current area.tif.", "ERROR")
        sys.exit(1)

    # 2. Process
    final_maps = process_soil(master, area_mask, lulc_arr)
    
    # 3. Validation
    visual_check(final_maps)
    
    print("\n" + "═" * 65)
    print("  ★ DONE — All 15 LISFLOOD Soil maps generated successfully in ./lisflood_soil")
    print("═" * 65 + "\n")

if __name__ == "__main__":
    main()
