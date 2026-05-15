"""
=============================================================================
LISFLOOD TOPOGRAPHY MAPS GENERATOR (GEE Version)
Calculates elevation std, LDD (PCRaster 1-9 format), gradient, and mask.
Uses Google Earth Engine for DEM extraction, Gradient, and Elvstd.
Uses pysheds locally for D8 flow direction (LDD).
=============================================================================
"""

import os
import sys
import math
import subprocess
import numpy as np
import warnings
import rasterio
warnings.filterwarnings("ignore")

import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            gdal_convert_pcraster, gdal_convert_netcdf, load_grid, save_aligned, reproject_to_grid,
                            init_ee)

AREA_TIF        = _cfg.AREA_TIF
OUTPUT_DIR      = _cfg.OUTPUT_TOPO
RESOLUTION_M    = _cfg.RESOLUTION_M
OWN_WATERSHED_PATH = _cfg.ROI_SHAPEFILE

def _run(cmd, name):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"{name} failed:\n{r.stderr.strip()}", "ERROR")
        sys.exit(1)

def rasterize_watershed(shp_path):
    log("STEP 1 — Rasterising watershed to create master grid", "STEP")
    make_dirs(os.path.join(OUTPUT_DIR, "maps"))
    out_path = os.path.join(OUTPUT_DIR, "maps", "area.tif")

    _run([
        "gdal_rasterize",
        "-burn", "1",
        "-init", "0",
        "-tr",   str(RESOLUTION_M), str(RESOLUTION_M),
        "-tap",
        "-ot",   "Byte",
        "-a_nodata", "0",
        "-co",   "COMPRESS=LZW",
        shp_path, out_path,
    ], "gdal_rasterize")

    info, mask_arr = load_grid(out_path)
    inside = int(mask_arr.sum())
    log(f"  Inside cells: {inside:,}  ({inside * RESOLUTION_M**2 / 10_000:.1f} ha)")
    log(f"  → {out_path}")
    return out_path, info, mask_arr

def compute_and_download_gee_topo(info):
    log("STEP 2 - Computing Gradient and Elvstd in GEE...", "STEP")
    
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

    buf = 2000
    region = ee.Geometry.Rectangle(
        [xmin - buf, ymin - buf, xmax + buf, ymax + buf], 
        proj=str(info.crs), 
        geodesic=False
    )

    dem = ee.Image("USGS/SRTMGL1_003")
    
    slope_deg = ee.Terrain.slope(dem)
    gradient = slope_deg.divide(180).multiply(math.pi).tan().rename('gradient')
    
    elvstd = dem.reduceNeighborhood(
        reducer=ee.Reducer.stdDev(),
        kernel=ee.Kernel.square(1, 'pixels')
    ).rename('elvstd')

    combined = ee.Image([dem.rename('dem'), gradient.toFloat(), elvstd.toFloat()])
    
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)
    
    raw_tif = os.path.join(raw_dir, "topo_raw_gee.tif")
    if not os.path.exists(raw_tif):
        log(f"Downloading GEE topo data to {raw_tif}...")
        geemap.ee_export_image(
            combined,
            filename=raw_tif,
            scale=30,
            crs=str(info.crs),
            region=region,
            file_per_band=False
        )
    else:
        log(f"Raw GEE data already exists: {raw_tif}")
        
    return raw_tif

def compute_ldd_snapped(dem_path, mask_arr, area_tif_path):
    log("STEP 4 — Calculating LDD (Pit filling and D8 routing via pysheds)", "STEP")
    try:
        from pysheds.grid import Grid
    except ImportError:
        log("Install pysheds: pip install pysheds", "ERROR")
        sys.exit(1)

    grid   = Grid.from_raster(dem_path)
    dem    = grid.read_raster(dem_path)
    
    # Fill pits, depressions, and flats
    filled = grid.fill_pits(dem)
    filled = grid.fill_depressions(filled)
    filled = grid.resolve_flats(filled)

    dirmap   = (64, 128, 1, 2, 4, 8, 16, 32)
    fdir     = grid.flowdir(filled, dirmap=dirmap)

    # PCRaster 1-9 keypad formatting
    code_map = {64:6, 128:9, 1:8, 2:7, 4:4, 8:1, 16:2, 32:3}
    fdir_arr = np.array(fdir).astype(np.int16)
    ldd_raw  = np.full_like(fdir_arr, 255, dtype=np.uint8)
    for ps, pcr in code_map.items():
        ldd_raw[fdir_arr == ps] = pcr
        
    # Mask to watershed
    ldd_masked = np.where(mask_arr > 0, ldd_raw, np.uint8(255))
    
    out_path = os.path.join(OUTPUT_DIR, "maps", "ldd.tif")
    save_aligned(ldd_masked, out_path, "uint8", 255, like=area_tif_path)
    return out_path, ldd_masked

def process_local_topo(raw_tif, mask_arr, info, area_tif_path):
    log("STEP 3 - Aligning outputs to area.tif...", "STEP")
    tif_paths = {}
    maps_dir = os.path.join(OUTPUT_DIR, "maps")
    
    band_names = ['dem', 'gradient', 'elvstd']
    aligned_bands = {}
    
    with rasterio.open(raw_tif) as src:
        raw_data = src.read()
        for i, name in enumerate(band_names):
            band_arr = raw_data[i]
            src_nd = src.nodata if src.nodata is not None else -9999
            band_arr[band_arr == src_nd] = np.nan
            
            aligned = reproject_to_grid(
                src_array=band_arr,
                src_transform=src.transform,
                src_crs=src.crs,
                like=area_tif_path,
                src_nodata=np.nan,
                dst_nodata=-9999,
                resampling_method="bilinear"
            ).astype(np.float32)
            aligned_bands[name] = aligned

    # Save DEM for pysheds
    dem_snapped = aligned_bands['dem']
    dem_path = os.path.join(OUTPUT_DIR, "raw", "dem_aligned.tif")
    save_aligned(dem_snapped, dem_path, "float32", -9999, like=area_tif_path)

    results = {}
    
    for name in ['gradient', 'elvstd']:
        arr = aligned_bands[name]
        
        if name == 'gradient':
            arr = np.where((arr <= 0) | np.isnan(arr), 1e-5, arr)
        elif name == 'elvstd':
            arr = np.where((arr < 0) | np.isnan(arr), 0.0, arr)
            
        final = np.where((mask_arr > 0), arr, -9999).astype(np.float32)
        tif_paths[name] = os.path.join(maps_dir, f"{name}.tif")
        save_aligned(final, tif_paths[name], "float32", -9999, like=area_tif_path)
        results[name] = final
        
    return tif_paths, results, dem_path

def convert_to_netcdf(tif_paths: dict, area_tif_path) -> dict:
    log("STEP 5 - Converting GeoTIFFs -> NetCDF .nc", "STEP")
    
    tif_paths['area'] = area_tif_path
    
    nc_paths = {}
    for name, tif_path in tif_paths.items():
        nc_path = tif_path.replace(".tif", ".nc")
        if gdal_convert_netcdf(tif_path, nc_path):
            nc_paths[name] = nc_path
            log(f"  {name}.nc  V")
        else:
            log(f"  {name}.tif  V   (.nc conversion failed)", "WARN")
    return nc_paths

def visualize(ldd, gradient, elvstd, mask, info):
    log("STEP 6 - Creating TOPO_VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        
        fig, axes = plt.subplots(1, 4, figsize=(20, 6))
        fig.suptitle(
            f"LISFLOOD Topography Maps (GEE Version) - {RESOLUTION_M}m  |  {info.crs}\n"
            f"Grid: origin=({info.transform.c:.1f}, {info.transform.f:.1f})  "
            f"{info.width}x{info.height} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, nodata, vmin=None, vmax=None):
            bg = np.zeros_like(mask, dtype=np.float32)
            bg[mask == 0] = np.nan
            ax.imshow(bg, cmap="Greys", vmin=-1, vmax=1, alpha=0.15, interpolation="nearest")

            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan
            
            im_args = {'cmap': cmap, 'interpolation': 'nearest'}
            if vmin is not None: im_args['vmin'] = vmin
            if vmax is not None: im_args['vmax'] = vmax
                
            im = ax.imshow(d, **im_args)
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        # Create discrete colormap for LDD 1-9
        ldd_cmap = plt.cm.get_cmap('Set3', 9)
        panel(axes[0], mask,     "area.nc\n(Mask)",    "Blues",  "Boolean", -0.5)
        panel(axes[1], ldd,      "ldd.nc\n(NetCDF)",   ldd_cmap, "Flow Dir", -0.5, 1, 9)
        panel(axes[2], gradient, "gradient.nc\n[m/m]", "YlOrRd", "Slope", 0)
        panel(axes[3], elvstd,   "elvstd.nc\n[m]",     "viridis","StdDev", 0)

        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "TOPO_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")

def validate_alignment(tif_paths: dict, info: GridInfo):
    log("STEP 7 - Validating alignment of all outputs", "STEP")
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
    print("  LISFLOOD TOPOGRAPHY MAP GENERATOR (GEE Version)")
    print(f"  Shapefile : {OWN_WATERSHED_PATH}")
    print("=" * 62 + "\n")

    check_imports(["rasterio", "numpy", "geemap", "pysheds"])
    make_dirs(OUTPUT_DIR)

    area_tif_path, info, mask_arr = rasterize_watershed(OWN_WATERSHED_PATH)

    raw_tif = compute_and_download_gee_topo(info)
    tif_paths, results, dem_path = process_local_topo(raw_tif, mask_arr, info, area_tif_path)
    
    ldd_path, ldd_masked = compute_ldd_snapped(dem_path, mask_arr, area_tif_path)
    tif_paths['ldd'] = ldd_path
    
    nc_paths = convert_to_netcdf(tif_paths, area_tif_path)
    
    tif_paths['area'] = area_tif_path
    validate_alignment(tif_paths, info)
    
    visualize(ldd_masked, results['gradient'], results['elvstd'], mask_arr, info)

if __name__ == "__main__":
    main()