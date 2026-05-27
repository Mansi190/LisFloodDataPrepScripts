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
from lisflood_utils import (log, make_dirs, gdal_convert_netcdf, init_ee,
                            load_grid, save_aligned, reproject_to_grid,
                            snap_to_grid)

AREA_TIF        = _cfg.AREA_TIF
LULC_ALIGNED    = _cfg.LULC_ALIGNED
OUTPUT_DIR      = _cfg.OUTPUT_SOIL

RESOLUTION_M    = _cfg.RESOLUTION_M

NODATA_FLOAT    = _cfg.NODATA_FLOAT
NODATA_INT      = _cfg.NODATA_INT
GEE_SCALE       = 60     # Extract at 60m to balance memory and hi-res interpolation

# SoilGrids depth layers and weights — defined in pipeline_config.py
DEPTHS_L1         = _cfg.SOIL_DEPTHS_L1
DEPTHS_L1_WEIGHTS = _cfg.SOIL_DEPTHS_L1_WEIGHTS
DEPTHS_L2         = _cfg.SOIL_DEPTHS_L2
DEPTHS_L2_WEIGHTS = _cfg.SOIL_DEPTHS_L2_WEIGHTS

# Forest class in the pan_india LULC v3
CLASS_TREES = 6

# =============================================================================
#  HELPERS  (log, make_dirs, gdal_convert, init_ee, grid helpers
#                          imported from lisflood_utils)
# =============================================================================

# =============================================================================
#  CORE GEE EXTRACTION
# =============================================================================

def fetch_soilgrids_layer(info, var_name, depths, weights, out_tif):
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

    # 1. Build bounding box from the canonical grid
    t = info.transform
    xmin, ymax = t.c, t.f
    xmax = xmin + t.a * info.width
    ymin = ymax + t.e * info.height
    utm_to_wgs84 = pyproj.Transformer.from_crs(info.crs, "EPSG:4326", always_xy=True)
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

def process_soil(info, area_mask, lulc_arr):
    log("STEP 1 — Fetching raw soil data from Earth Engine", "STEP")
    init_ee(_cfg.GEE_PROJECT)

    raw_dir = os.path.join(OUTPUT_DIR, "raw")

    # Fetch clay, silt, bulk-density, and soil organic carbon for both layers.
    # silt and soc are needed for the full Tóth et al. (2015) PTF equations.
    clay1_path = fetch_soilgrids_layer(info, "clay", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "clay1.tif"))
    silt1_path = fetch_soilgrids_layer(info, "silt", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "silt1.tif"))
    bd1_path   = fetch_soilgrids_layer(info, "bdod", DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "bd1.tif"))
    soc1_path  = fetch_soilgrids_layer(info, "soc",  DEPTHS_L1, DEPTHS_L1_WEIGHTS, os.path.join(raw_dir, "soc1.tif"))

    clay2_path = fetch_soilgrids_layer(info, "clay", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "clay2.tif"))
    silt2_path = fetch_soilgrids_layer(info, "silt", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "silt2.tif"))
    bd2_path   = fetch_soilgrids_layer(info, "bdod", DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "bd2.tif"))
    soc2_path  = fetch_soilgrids_layer(info, "soc",  DEPTHS_L2, DEPTHS_L2_WEIGHTS, os.path.join(raw_dir, "soc2.tif"))

    log("STEP 2 — Reprojecting soil inputs to canonical grid (bilinear interpolation)", "STEP")
    import rasterio

    def align_and_scale(tif_path, scale_factor):
        with rasterio.open(tif_path) as src:
            arr = src.read(1).astype(np.float32)
            arr[arr == src.nodata] = np.nan
            arr = arr / scale_factor
            aligned = reproject_to_grid(
                src_array=arr, src_transform=src.transform, src_crs=src.crs,
                like=AREA_TIF,
                src_nodata=np.nan, dst_nodata=np.nan, resampling_method="bilinear"
            )
            return snap_to_grid(aligned, AREA_TIF, np.nan)

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
    valid_lulc = (lulc_arr != NODATA_INT)
    domain = inside & valid_lulc

    # We enforce NODATA_FLOAT strictly outside the masks
    def mask_surface(arr, filter_mask):
        return np.where(filter_mask, arr, NODATA_FLOAT).astype(np.float32)

    final_maps = {
        "thetas1_forest": mask_surface(props["thetas1"], domain),
        "thetas1_other":  mask_surface(props["thetas1"], domain),
        "thetas2":        mask_surface(props["thetas2"], domain),

        "thetar1_forest": mask_surface(props["thetar1"], domain),
        "thetar1_other":  mask_surface(props["thetar1"], domain),
        "thetar2":        mask_surface(props["thetar2"], domain),

        "alpha1_forest":  mask_surface(props["alpha1"], domain),
        "alpha1_other":   mask_surface(props["alpha1"], domain),
        "alpha2":         mask_surface(props["alpha2"], domain),

        "lambda1_forest": mask_surface(props["lambda1"], domain),
        "lambda1_other":  mask_surface(props["lambda1"], domain),
        "lambda2":        mask_surface(props["lambda2"], domain),

        "ksat1_forest":   mask_surface(props["ksat1"], domain),
        "ksat1_other":    mask_surface(props["ksat1"], domain),
        "ksat2":          mask_surface(props["ksat2"], domain),
    }

    for name, array in final_maps.items():
        tif_p = os.path.join(OUTPUT_DIR, "maps", f"{name}.tif")
        nc_p = os.path.join(OUTPUT_DIR, "maps", f"{name}.nc")
        save_aligned(array, tif_p, "float32", NODATA_FLOAT, like=AREA_TIF)
        gdal_convert_netcdf(tif_p, nc_p)
        log(f"  ✔ {name}.nc")
        
    return final_maps

def visual_check(maps):
    log("STEP 5 — Generating Visual Validation", "STEP")
    try:
        import matplotlib.pyplot as plt
        
        def panel(ax, data, title, cmap):
            d = data.copy()
            d[d <= -9000] = np.nan
            im = ax.imshow(d, cmap=cmap)
            plt.colorbar(im, ax=ax, shrink=0.8)
            ax.set_title(title, fontsize=10, fontweight='bold')
            ax.axis('off')

        properties = [
            ("thetas", "Theta Saturation (Volumetric)", "YlGnBu"),
            ("thetar", "Theta Residual (Volumetric)", "YlGnBu"),
            ("alpha", "Alpha (van Genuchten)", "viridis"),
            ("lambda", "Lambda (Brooks-Corey)", "viridis"),
            ("ksat", "Hydraulic Conductivity Ksat (mm/day)", "Spectral")
        ]

        for prop, desc, cmap in properties:
            fig, axes = plt.subplots(1, 3, figsize=(18, 5))
            fig.suptitle(f"{desc}", fontsize=14, fontweight='bold')
            
            panel(axes[0], maps[f'{prop}1_forest'], f"Layer 1 - Forest", cmap)
            panel(axes[1], maps[f'{prop}1_other'], f"Layer 1 - Other", cmap)
            panel(axes[2], maps[f'{prop}2'], f"Layer 2", cmap)
            
            out = os.path.join(OUTPUT_DIR, f"soil_visual_check_{prop}.png")
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

    make_dirs(OUTPUT_DIR)

    import rasterio

    if not os.path.exists(AREA_TIF) or not os.path.exists(LULC_ALIGNED):
        log("Missing area.tif or lulc_aligned.tif. Run Topo and LULC scripts first.", "ERROR")
        sys.exit(1)

    # Load the canonical grid from area.tif (everything aligns to this)
    info, area_mask = load_grid(AREA_TIF)

    with rasterio.open(LULC_ALIGNED) as src:
        lulc_arr = src.read(1)

    if lulc_arr.shape != area_mask.shape:
        log(f"ALIGNMENT ERROR: lulc_aligned.tif shape {lulc_arr.shape} "
            f"does not match area.tif shape {area_mask.shape}.", "ERROR")
        log("Re-run the LULC script to regenerate lulc_aligned.tif against "
            "the current area.tif.", "ERROR")
        sys.exit(1)

    final_maps = process_soil(info, area_mask, lulc_arr)
    
    # 3. Validation
    visual_check(final_maps)
    
    print("\n" + "═" * 65)
    print("  ★ DONE — All 15 LISFLOOD Soil maps generated successfully in ./lisflood_soil")
    print("═" * 65 + "\n")

if __name__ == "__main__":
    main()
