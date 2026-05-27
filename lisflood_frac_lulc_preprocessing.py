"""
=============================================================================
LISFLOOD LULC FRACTIONS MAP GENERATOR (GEE Version)
=============================================================================
"""

import os
import sys
import numpy as np
import warnings
warnings.filterwarnings("ignore")

import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            gdal_convert_netcdf, load_grid, save_aligned, reproject_to_grid,
                            init_ee)

AREA_TIF        = _cfg.AREA_TIF
OUTPUT_DIR      = _cfg.OUTPUT_LULC
RESOLUTION_M    = _cfg.RESOLUTION_M

def _write_convert_script(tif_paths: dict):
    sh_path = os.path.join(OUTPUT_DIR, "manual_convert.sh")
    with open(sh_path, "w") as f:
        f.write("#!/bin/bash\n# Run this if gdal_translate was not found during script execution\n")
        f.write("# sudo apt install gdal-bin\n\n")
        for name, tif in tif_paths.items():
            out = tif.replace(".tif", ".nc")
            f.write(f"gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE {tif} {out}\n")
    log(f"  manual_convert.sh written -> {sh_path}")

def convert_to_netcdf(tif_paths: dict) -> dict:
    log("STEP 4 - Converting GeoTIFFs -> NetCDF .nc", "STEP")
    nc_paths = {}
    for name, tif_path in tif_paths.items():
        nc_path = tif_path.replace(".tif", ".nc")
        if gdal_convert_netcdf(tif_path, nc_path):
            nc_paths[name] = nc_path
            log(f"  {name}.nc  V")
        else:
            log(f"  {name}.tif  V   (.nc conversion failed)", "WARN")
    _write_convert_script(tif_paths)
    return nc_paths

def validate_alignment(tif_paths: dict, info: GridInfo):
    log("STEP 5 - Validating alignment of all outputs", "STEP")
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

def validate_fractions(results, mask):
    log("Validating if fractions sum to 1.0", "STEP")
    fsealed = results.get('fracsealed')
    fwater = results.get('fracwater')
    fforest = results.get('fracforest')
    fother = results.get('fracother')
    
    if any(x is None for x in [fsealed, fwater, fforest, fother]):
        return
        
    total = fsealed + fwater + fforest + fother
    
    # We only care about pixels inside the mask
    valid_pixels = mask > 0
    
    # Check where sum is exactly 1.0 (with a small tolerance for float precision)
    sum_is_1 = np.abs(total[valid_pixels] - 1.0) < 1e-4
    
    total_valid = np.sum(valid_pixels)
    total_perfect = np.sum(sum_is_1)
    total_failed = total_valid - total_perfect
    
    if total_failed == 0:
        log(f"  All {total_valid} valid pixels sum perfectly to 1.0!  V", "DONE")
    else:
        log(f"  WARNING: {total_failed} out of {total_valid} pixels DO NOT sum to 1.0!", "WARN")

def visualize(results, mask, info):
    log("STEP 6 - Creating LULC_VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 5, figsize=(25, 5))
        fig.suptitle(
            f"LISFLOOD LULC Fractions (GEE Logic) - {RESOLUTION_M}m  |  {info.crs}\n"
            f"Grid: origin=({info.transform.c:.1f}, {info.transform.f:.1f})  "
            f"{info.width}x{info.height} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, vmin=0, vmax=1):
            bg = np.zeros_like(mask, dtype=np.float32)
            bg[mask == 0] = np.nan
            ax.imshow(bg, cmap="Greys", vmin=-1, vmax=1, alpha=0.15, interpolation="nearest")

            d = data.astype(np.float32).copy()
            d[d <= -9000] = np.nan
                
            im = ax.imshow(d, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        panel(axes[0], results['lulc'], "lulc.map\n(Class)", "tab20", "Class", 0, 12)
        panel(axes[1], results['fracsealed'], "fracsealed.map", "Reds", "Fraction")
        panel(axes[2], results['fracwater'], "fracwater.map", "Blues", "Fraction")
        panel(axes[3], results['fracforest'], "fracforest.map", "Greens", "Fraction")
        panel(axes[4], results['fracother'], "fracother.map", "Oranges", "Fraction")

        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "LULC_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")

def compute_and_download_gee_lulc(info):
    log("STEP 2 - Computing LULC fractions in GEE...", "STEP")
    
    try:
        import ee
        import geemap
    except ImportError:
        log("Missing: earthengine-api geemap", "ERROR")
        sys.exit(1)
        
    init_ee(_cfg.GEE_PROJECT)

    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    buf = 2000  # 2km buffer
    region = ee.Geometry.Rectangle(
        [xmin - buf, ymin - buf, xmax + buf, ymax + buf], 
        proj=str(info.crs), 
        geodesic=False
    )

    lulc10m = ee.Image("projects/corestack-datasets/assets/datasets/LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
    lulc_label = lulc10m.select('predicted_label')

    builtUpMask = lulc_label.eq(1)
    waterMask = lulc_label.gte(2).And(lulc_label.lte(4))
    forestMask = lulc_label.eq(6)
    
    otherMask = lulc_label.eq(5) \
        .Or(lulc_label.eq(7)) \
        .Or(lulc_label.gte(8).And(lulc_label.lte(12)))

    # Process categorical LULC (Mode)
    lulc30m = lulc_label \
        .reduceResolution(reducer=ee.Reducer.mode(), maxPixels=1024) \
        .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
        .rename('lulc')

    # Function to process Fractions (Mean)
    def calculateFraction(image, name):
        return image.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                    .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
                    .rename(name)

    fImpermeable = calculateFraction(builtUpMask, 'fracsealed')
    fWater = calculateFraction(waterMask, 'fracwater')
    fForest = calculateFraction(forestMask, 'fracforest')
    fOther = calculateFraction(otherMask, 'fracother')

    combined = ee.Image([
        lulc30m.toFloat(), 
        fImpermeable.toFloat(), 
        fWater.toFloat(), 
        fForest.toFloat(), 
        fOther.toFloat()
    ])
    
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)
    make_dirs(os.path.join(OUTPUT_DIR, "maps"))
    
    raw_tif = os.path.join(raw_dir, "lulc_raw_gee.tif")
    if not os.path.exists(raw_tif):
        log(f"Downloading GEE LULC data to {raw_tif}...")
        geemap.ee_export_image(
            combined,
            filename=raw_tif,
            scale=_cfg.RESOLUTION_M,
            crs=str(info.crs),
            region=region,
            file_per_band=False
        )
    else:
        log(f"Raw GEE data already exists: {raw_tif}")
        
    return raw_tif

def process_local_lulc(raw_tif, mask_arr, info):
    log("STEP 3 - Aligning and masking outputs to area.tif...", "STEP")
    import rasterio
    tif_paths = {}
    maps_dir = os.path.join(OUTPUT_DIR, "maps")
    
    band_names = ['lulc', 'fracsealed', 'fracwater', 'fracforest', 'fracother']
    aligned_bands = {}
    
    with rasterio.open(raw_tif) as src:
        raw_data = src.read()
        for i, name in enumerate(band_names):
            band_arr = raw_data[i]
            src_nd = src.nodata if src.nodata is not None else -9999
            band_arr[band_arr == src_nd] = np.nan
            
            # LULC gets nearest neighbor, fractions get bilinear
            resamp = "nearest" if name == 'lulc' else "bilinear"
            
            aligned = reproject_to_grid(
                src_array=band_arr,
                src_transform=src.transform,
                src_crs=src.crs,
                like=AREA_TIF,
                src_nodata=np.nan,
                dst_nodata=-9999,
                resampling_method=resamp
            ).astype(np.float32)
            aligned_bands[name] = aligned

    results = {}
    for name in band_names:
        arr = aligned_bands[name]
        
        # Clamp fractions between 0 and 1
        if name != 'lulc':
            arr = np.clip(arr, 0.0, 1.0)
            # Fill NaNs with 0 in the fractions before masking
            arr = np.where(np.isnan(arr), 0.0, arr)
            
        final = np.where((mask_arr > 0), arr, -9999).astype(np.float32)
        tif_paths[name] = os.path.join(maps_dir, f"{name}.tif")
        save_aligned(final, tif_paths[name], "float32", -9999, like=AREA_TIF)
        results[name] = final
        
    return tif_paths, results

def print_summary(tif_paths: dict, map_paths: dict, info: GridInfo):
    print("\n" + "=" * 62)
    print("  *  DONE - All LISFLOOD LULC Maps perfectly aligned")
    print("=" * 62)
    print(f"\n  Grid    : {info.width} cols x {info.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS     : {info.crs}")
    print(f"  Origin  : ({info.transform.c:.2f} m E, {info.transform.f:.2f} m N)\n")

    print("  LISFLOOD .ini (LULC section):")
    for key in ["lulc", "fracsealed", "fracwater", "fracforest", "fracother"]:
        val = map_paths.get(key, f"W  run manual_convert.sh for {key}.nc")
        print(f"    {key} = {val}")
    print(f"\n  Output : {OUTPUT_DIR}/maps/")
    print(f"  Check  : {OUTPUT_DIR}/LULC_VISUAL_CHECK.png")
    print("=" * 62 + "\n")

def main():
    print("\n" + "=" * 62)
    print("  LISFLOOD LULC MAP GENERATOR (GEE Version)")
    print(f"  Reference raster : {AREA_TIF}")
    print("=" * 62 + "\n")

    check_imports(["rasterio", "numpy", "geemap"])
    make_dirs(OUTPUT_DIR)

    log("STEP 1 - Load canonical grid (locks all alignment to area.tif)", "STEP")
    info, mask = load_grid(AREA_TIF)

    raw_tif = compute_and_download_gee_lulc(info)
    tif_paths, results = process_local_lulc(raw_tif, mask, info)
    map_paths = convert_to_netcdf(tif_paths)
    validate_alignment(tif_paths, info)
    validate_fractions(results, mask)
    
    visualize(results, mask, info)
    print_summary(tif_paths, map_paths, info)

if __name__ == "__main__":
    main()
