"""
lisflood_lulc_parameters.py
Generates the land cover depending maps for LISFLOOD:
- cropcoef (forest, other)
- crgrnum (forest, other)
- mannings (forest, other)
- soildep1 (forest, other)
- soildep2 (forest, other)

Output format: NetCDF (.nc)
"""

import os
import sys
import numpy as np
import pipeline_config as _cfg
from lisflood_utils import (log, make_dirs, load_grid, save_aligned, gdal_convert_netcdf)

OUTPUT_DIR = _cfg.DIR_TABLE2MAP
AREA_TIF = _cfg.AREA_TIF

CRGRNUM_F = 3.5
CRGRNUM_O = 4.5
MANNINGS = 0.05


def generate_parameters():
    log("STEP 1 - Loading mask, grid, and LULC", "STEP")
    import rasterio
    info, mask = load_grid(AREA_TIF)
    
    with rasterio.open(_cfg.LULC_ALIGNED) as src:
        lulc_arr = src.read(1)
        
    CLASS_TREES = 6
    inside = (mask > 0)
    valid_lulc = (lulc_arr != _cfg.NODATA_INT)
    domain = inside & valid_lulc

    def mask_surface(val, filter_mask, dtype=np.float32):
        arr = np.full((info.height, info.width), val, dtype=dtype)
        return np.where(filter_mask, arr, -9999).astype(dtype)

    maps = {
        "cropcoef_forest": mask_surface(1.15, domain),
        "cropcoef_other":  mask_surface(1.10, domain),
        "crgrnum_forest":  mask_surface(3.5, domain, np.int16),
        "crgrnum_other":   mask_surface(1, domain, np.int16),
        "mannings_forest": mask_surface(0.3, domain),
        "mannings_other":  mask_surface(0.1, domain),
        "soildep1_forest": mask_surface(1500, domain),
        "soildep1_other":  mask_surface(600, domain),
        "soildep2_forest": mask_surface(1000, domain),
        "soildep2_other":  mask_surface(1400, domain),
    }

    maps_dir = OUTPUT_DIR
    make_dirs(OUTPUT_DIR)

    tif_paths = {}
    log("STEP 2 - Saving TIFs and converting to NetCDF", "STEP")
    for name, arr in maps.items():
        tif_p = os.path.join(maps_dir, f"{name}.tif")
        nc_p = os.path.join(maps_dir, f"{name}.nc")
        
        # Save TIF
        dtype_str = "float32" if arr.dtype == np.float32 else "int16"
        save_aligned(arr, tif_p, dtype_str, -9999, like=AREA_TIF)
        tif_paths[name] = tif_p
        
        # Convert to NC
        if gdal_convert_netcdf(tif_p, nc_p):
            log(f"  ✔ {name}.nc")
        else:
            log(f"  ✘ Failed to convert {name} to .nc", "WARN")
            
    return maps, tif_paths, info, mask

def validate_alignment(tif_paths: dict, info):
    log("STEP 3 - Validating alignment of all outputs", "STEP")
    import rasterio
    TOLERANCE = 0.01

    print("\n  ALIGNMENT PROOF - every file must show identical values:")
    print(f"  {'File':<20} {'Origin (E, N)':<32} {'Size':<14} {'Pixel':<8} {'CRS'}")
    print(f"  {'-'*20} {'-'*32} {'-'*14} {'-'*8} {'-'*20}")

    errors = []
    for name, path in tif_paths.items():
        if not os.path.exists(path):
            errors.append(f"    {name}: file missing")
            continue
        with rasterio.open(path) as src:
            t     = src.transform
            crs   = str(src.crs)
            w, h  = src.width, src.height

            print(f"  {name+'.tif':<20} "
                  f"({t.c:.2f}, {t.f:.2f}){'':<14} "
                  f"{w}x{h}{'':<6} "
                  f"{t.a:.1f}m    "
                  f"{crs}")

            if src.crs and src.crs.to_epsg() != info.crs.to_epsg():
                errors.append(f"    {name}: CRS {src.crs} != {info.crs}")
            if w != info.width or h != info.height:
                errors.append(f"    {name}: size {w}x{h} != {info.width}x{info.height}")
            if abs(t.c - info.transform.c) > TOLERANCE:
                errors.append(f"    {name}: origin_x {t.c:.4f} != {info.transform.c:.4f}")
            if abs(t.f - info.transform.f) > TOLERANCE:
                errors.append(f"    {name}: origin_y {t.f:.4f} != {info.transform.f:.4f}")

    print()
    if errors:
        print("  X  ALIGNMENT FAILURES:")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        log("  All outputs pass alignment check  V  *", "DONE")


def main():
    print("\n" + "=" * 62)
    print("  LISFLOOD LULC PARAMETERS GENERATOR")
    print("=" * 62 + "\n")
    
    maps, tif_paths, info, mask = generate_parameters()
    validate_alignment(tif_paths, info)
    
    print("\n  ★ DONE\n")

if __name__ == "__main__":
    main()
