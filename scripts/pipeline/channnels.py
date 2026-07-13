"""
=============================================================================
LISFLOOD CHANNEL GEOMETRY MAP GENERATOR (GEE + Local Alignment)
Replaces offline pysheds extraction with Google Earth Engine computations,
but retains the precise area.tif master grid alignment and PCRaster exports.
=============================================================================
"""

import os
import sys
import math
import warnings
import numpy as np
if not hasattr(np, 'in1d'):
    np.in1d = np.isin
warnings.filterwarnings("ignore")

import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            gdal_convert_netcdf, load_grid, save_aligned, reproject_to_grid,
                            init_ee)
AREA_TIF        = _cfg.AREA_TIF
OUTPUT_DIR      = _cfg.DIR_MAPS
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
            log(f"  {name}.tif  V   (.nc conversion failed - see manual_convert.sh)", "WARN")
            
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
            if abs(abs(t.a) - RESOLUTION_M) > 0.01:
                errors.append(f"    {name}: pixel_x {abs(t.a):.2f} != {RESOLUTION_M}")

    print()
    if errors:
        print("  X  ALIGNMENT FAILURES:")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        log("  All outputs pass alignment check  V  *", "DONE")

def visualize(chan, changrad, chanbw, chanbnkf, chanman, chanleng, mask, info):
    log("STEP 6 - Creating CHANNEL_VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 4, figsize=(24, 11))
        fig.suptitle(
            f"LISFLOOD Channel Maps (GEE Logic) - {RESOLUTION_M}m  |  {info.crs}\n"
            f"Grid: origin=({info.transform.c:.1f}, {info.transform.f:.1f})  "
            f"{info.width}x{info.height} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, nodata=-9000, is_boolean=False):
            bg = np.zeros_like(mask, dtype=np.float32)
            bg[mask == 0] = np.nan
            ax.imshow(bg, cmap="Greys", vmin=-1, vmax=1, alpha=0.15, interpolation="nearest")

            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan
            if is_boolean:
                d[d == 0] = np.nan
                
            im = ax.imshow(d, cmap=cmap, interpolation="nearest")
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        panel(axes[0, 0], chan,     "chan.nc\n(Boolean)", "Blues",   "1",  nodata=-0.5, is_boolean=True)
        panel(axes[0, 1], changrad, "changrad.nc\n[m/m]", "YlOrRd", "m/m")
        panel(axes[0, 2], chanbw,   "chanbw.nc\n[m]",    "GnBu",   "m")
        panel(axes[0, 3], chanleng, "chanleng.nc\n[m]",  "Purples", "m")
        panel(axes[1, 0], chanbnkf, "chanbnkf.nc\n[m]",  "PuBu",   "m")
        panel(axes[1, 1], chanman,  "chanman.nc\n[-]",   "RdYlGn", "n")

        # Hide empty subplot
        axes[1, 2].axis("off")

        ax = axes[1, 3]
        ax.axis("off")
        proof = (
            "ALIGNMENT PROOF\n"
            "=======================\n\n"
            "All 7 files share:\n\n"
            f"  Rows   : {info.height}\n"
            f"  Cols   : {info.width}\n"
            f"  Pixel  : {RESOLUTION_M}m x {RESOLUTION_M}m\n"
            f"  CRS    : {info.crs}\n"
            f"  Origin :\n"
            f"    E = {info.transform.c:.2f} m\n"
            f"    N = {info.transform.f:.2f} m\n\n"
            "=======================\n\n"
            "LISFLOOD .ini settings:\n\n"
            "  Channels      = chan.nc\n"
            "  ChanGrad      = changrad.nc\n"
            "  ChanMan       = chanman.nc\n"
            "  ChanLength    = chanleng.nc\n"
            "  ChanBottomWidth = chanbw.nc\n"
            "  ChanSdXdY     = chans.nc\n"
            "  ChanDepthThreshold = chanbnkf.nc"
        )
        ax.text(0.05, 0.97, proof, transform=ax.transAxes,
                fontsize=9, va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor="#e8f4f8", alpha=0.9))

        plt.tight_layout()
        out = os.path.join(_cfg.BASE_DIR, "CHANNEL_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")

def print_summary(tif_paths: dict, map_paths: dict, info: GridInfo):
    print("\n" + "=" * 62)
    print("  *  DONE - All LISFLOOD Channel Maps perfectly aligned")
    print("=" * 62)
    print(f"\n  Grid    : {info.width} cols x {info.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS     : {info.crs}")
    print(f"  Origin  : ({info.transform.c:.2f} m E, {info.transform.f:.2f} m N)\n")

    ini_vars = {
        "chan":     "Channels          ",
        "changrad": "ChanGrad          ",
        "chanman":  "ChanMan           ",
        "chanleng": "ChanLength        ",
        "chanbw":   "ChanBottomWidth   ",
        "chans":    "ChanSdXdY         ",
        "chanbnkf": "ChanDepthThreshold",
    }
    print("  LISFLOOD .ini (channel section):")
    for key, ini_name in ini_vars.items():
        val = map_paths.get(key, f"W  run manual_convert.sh for {key}.nc")
        print(f"    {ini_name} = {val}")
    print(f"\n  Output : {OUTPUT_DIR}/maps/")
    print(f"  Check  : {OUTPUT_DIR}/CHANNEL_VISUAL_CHECK.png")
    print("=" * 62 + "\n")


def compute_and_download_gee_channels(info):
    log("STEP 2 - Computing channel parameters in GEE...", "STEP")
    
    try:
        import ee
        import geemap
    except ImportError:
        log("Missing: earthengine-api geemap", "ERROR")
        sys.exit(1)
        
    init_ee(_cfg.GEE_PROJECT)

    # Bounding box for export
    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    buf = 2000  # 2km buffer
    region = ee.Geometry.Rectangle(
        [xmin - buf, ymin - buf, xmax + buf, ymax + buf], 
        proj=str(info.crs), 
        geodesic=False
    )

    lulc = ee.Image("projects/corestack-datasets/assets/datasets/LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
    merit = ee.Image("MERIT/Hydro/v1_0_1")

    pred_label = lulc.select('predicted_label')
    chanMan = pred_label \
        .where(pred_label.eq(1), 0.080) \
        .where(pred_label.gte(2).And(pred_label.lte(4)), 0.035) \
        .where(pred_label.eq(6), 0.100) \
        .where(pred_label.eq(5), 0.040) \
        .where(pred_label.gte(7), 0.045) \
        .rename('chanman')

    # 1) Channel Mask using provided FeatureCollection
    drainage_fc = ee.FeatureCollection("projects/corestack-datasets/assets/datasets/drainage-line/pan_india_drainage_lines")
    channelMask = ee.Image(0).byte().paint(drainage_fc, 1).rename('chan')

    # 2) Channel Gradient: 10-90 Quantile Method
    srtm_dem = ee.Image("USGS/SRTMGL1_003")
    dem_masked = srtm_dem.updateMask(channelMask.eq(1))
    
    # Get 10th and 90th elevation percentiles for channel pixels within each target grid cell
    dem_p = dem_masked.reduceResolution(
        reducer=ee.Reducer.percentile([10, 90]), 
        maxPixels=4096
    ).reproject(crs=str(info.crs), scale=RESOLUTION_M)
    
    p10 = dem_p.select('elevation_p10')
    p90 = dem_p.select('elevation_p90')
    
    # Approximate flow distance (Resolution * 1.1)
    flow_dist = ee.Image.constant(RESOLUTION_M).multiply(1.1)
    
    chanGrad = p90.subtract(p10).divide(flow_dist) \
                  .unmask(0.0001) \
                  .clamp(0.0001, 0.05) \
                  .rename('changrad')

    # 3) Channel Length: Meandering length inside target pixel
    # Paint lines at 30m resolution (1 pixel wide = 30m wide)
    channelMask_30m = ee.Image(0).paint(drainage_fc, 1).reproject(crs=str(info.crs), scale=30)
    
    # Calculate the fraction of 30m river pixels in the target pixel (e.g., 300m)
    # Area = Fraction * (RES*RES). Length = Area / 30m.
    chanLength = channelMask_30m.reduceResolution(
        reducer=ee.Reducer.mean(), 
        maxPixels=4096
    ).reproject(crs=str(info.crs), scale=RESOLUTION_M) \
     .multiply((RESOLUTION_M * RESOLUTION_M) / 30.0) \
     .max(ee.Image.constant(RESOLUTION_M)) \
     .rename('chanleng')
    
    chanSdXdY = ee.Image.constant(1.0).rename('chans')
    
    combined = ee.Image([
        chanGrad.toFloat(), chanMan.toFloat(),
        chanLength.toFloat(), chanSdXdY.toFloat(),
        channelMask.byte()
    ])
    
    raw_dir = _cfg.DIR_RAW
    make_dirs(raw_dir)
    make_dirs(OUTPUT_DIR)
    
    raw_tif = os.path.join(raw_dir, "channels_raw_gee.tif")
    if not os.path.exists(raw_tif):
        log(f"Downloading GEE channel data to {raw_tif}...")
        geemap.ee_export_image(
            combined,
            filename=raw_tif,
            scale=RESOLUTION_M,
            crs=str(info.crs),
            region=region,
            file_per_band=False
        )
    else:
        log(f"Raw GEE data already exists: {raw_tif}")
        
    return raw_tif

def process_local_channels(raw_tif, mask_arr, info):
    log("STEP 3 - Aligning and masking outputs to area.tif...", "STEP")
    import rasterio
    tif_paths = {}
    maps_dir = OUTPUT_DIR
    
    # 'chanbnkf' and 'chanbw' are computed locally, 'chan' is derived from area mask.
    aligned_bands = {}
    names = ['changrad', 'chanman', 'chanleng', 'chans', 'chan']
    
    with rasterio.open(raw_tif) as src:
        raw_data = src.read()
        
        for i, name in enumerate(names):
            band_arr = raw_data[i]
            src_nd = src.nodata if src.nodata is not None else -9999
            band_arr[band_arr == src_nd] = np.nan
            
            # Use nearest neighbor for channel mask (categorical) and bilinear for others
            resampling = "nearest" if name == 'chan' else "bilinear"
            
            aligned = reproject_to_grid(
                src_array=band_arr,
                src_transform=src.transform,
                src_crs=src.crs,
                like=AREA_TIF,
                src_nodata=np.nan,
                dst_nodata=-9999,
                resampling_method=resampling
            ).astype(np.float32)
            aligned_bands[name] = aligned

    # --- Local chanbnkf calculation using pysheds ---
    from pysheds.grid import Grid
    import sys
    ldd_path = os.path.join(_cfg.DIR_MAPS, "ldd.tif")
    if not os.path.exists(ldd_path):
        log("ldd.tif not found! Run topographyMapsScript.py first.", "ERROR")
        sys.exit(1)
        
    grid = Grid.from_raster(ldd_path)
    ldd = grid.read_raster(ldd_path)
    
    # Convert PCRaster LDD to pysheds flowdir format
    pcr_to_pysheds = {
        8: 64,  # N
        9: 128, # NE
        6: 1,   # E
        3: 2,   # SE
        2: 4,   # S
        1: 8,   # SW
        4: 16,  # W
        7: 32,  # NW
        5: 0,   # Pit/Sink
    }
    
    fdir_pysheds = np.zeros_like(ldd, dtype=np.int16)
    for pcr, pysh in pcr_to_pysheds.items():
        fdir_pysheds[ldd == pcr] = pysh
        
    # Calculate accumulation EXACTLY on the Yamazaki LDD routing graph
    acc = grid.accumulation(fdir_pysheds)
    
    acc_arr = np.array(acc).astype(np.float32)
    
    # Save facc_snapped.tif which is required by lisflood_gauges_sites.py
    facc_path = os.path.join(_cfg.DIR_RAW, "facc_snapped.tif")
    save_aligned(acc_arr, facc_path, "float32", -9999, like=AREA_TIF)
    
    area_km2 = acc_arr * (RESOLUTION_M**2 / 1_000_000)
    
    chanbnkf = 0.27 * (area_km2 ** 0.33)
    chanbw = area_km2 * 0.0032
    
    # Load the physically traced meandering channel length from the topography script
    chanleng_path = os.path.join(_cfg.DIR_MAPS, "chanleng_300m.tif")
    if os.path.exists(chanleng_path):
        import rasterio
        with rasterio.open(chanleng_path) as src:
            chanleng_raw = src.read(1).astype(np.float32)
        # Ensure minimum length of pixel resolution
        chanleng_raw = np.where(chanleng_raw < RESOLUTION_M, RESOLUTION_M, chanleng_raw)
        aligned_bands['chanleng'] = chanleng_raw
    else:
        log("chanleng_300m.tif not found from topography. Using GEE approximation instead.", "WARN")
    
    aligned_bands['chanbnkf'] = chanbnkf
    aligned_bands['chanbw'] = chanbw
    # ------------------------------------------------

    # The chan variable is dynamically defined using a 10 km2 upstream area threshold
    UPSTREAM_AREA_THRESHOLD_KM2 = 5.0
    log(f"  Defining channels using upstream area threshold >= {UPSTREAM_AREA_THRESHOLD_KM2} km2...")
    chan = np.where((area_km2 >= UPSTREAM_AREA_THRESHOLD_KM2) & (mask_arr > 0), 1, 0).astype(np.uint8)
    
    tif_paths['chan'] = os.path.join(maps_dir, "chan.tif")
    save_aligned(chan, tif_paths['chan'], "uint8", 0, like=AREA_TIF)
    
    results = {'chan': chan}
    for name in ['changrad', 'chanman', 'chanleng', 'chanbw', 'chans', 'chanbnkf']:
        arr = aligned_bands[name]
        
        if name in ['changrad', 'chanman', 'chanleng', 'chanbw', 'chanbnkf']:
            arr = np.where((arr <= 0) | np.isnan(arr), 1e-5, arr)
        elif name == 'chans':
            arr = np.where((arr < 0) | np.isnan(arr), 0.0, arr)
            
        final = np.where((chan == 1) & (mask_arr > 0), arr, -9999).astype(np.float32)
        tif_paths[name] = os.path.join(maps_dir, f"{name}.tif")
        save_aligned(final, tif_paths[name], "float32", -9999, like=AREA_TIF)
        results[name] = final
        
    return tif_paths, results

def main():
    print("\n" + "=" * 62)
    print("  LISFLOOD CHANNEL MAP GENERATOR (GEE Version)")
    print(f"  Reference raster : {AREA_TIF}")
    print("=" * 62 + "\n")

    check_imports(["rasterio", "numpy", "geopandas", "pyproj", "pysheds"])
    make_dirs(OUTPUT_DIR)

    log("STEP 1 - Load canonical grid (locks all alignment to area.tif)", "STEP")
    info, mask = load_grid(AREA_TIF)

    raw_tif = compute_and_download_gee_channels(info)
    tif_paths, results = process_local_channels(raw_tif, mask, info)
    map_paths = convert_to_netcdf(tif_paths)
    validate_alignment(tif_paths, info)
    
    visualize(results['chan'], results['changrad'], results['chanbw'], 
              results['chanbnkf'], results['chanman'], results['chanleng'], mask, info)

    print_summary(tif_paths, map_paths, info)

if __name__ == "__main__":
    main()