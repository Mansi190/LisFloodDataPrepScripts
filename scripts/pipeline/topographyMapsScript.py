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
                            gdal_convert_netcdf, load_grid, save_aligned, reproject_to_grid,
                            init_ee)

AREA_TIF        = _cfg.AREA_TIF
OUTPUT_DIR      = _cfg.DIR_MAPS
RESOLUTION_M    = _cfg.RESOLUTION_M
OWN_WATERSHED_PATH = _cfg.ROI_SHAPEFILE

def _run(cmd, name):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"{name} failed:\n{r.stderr.strip()}", "ERROR")
        sys.exit(1)

def reproject_shapefile(shp_path):
    if not _cfg.TARGET_CRS:
        return shp_path
        
    import geopandas as gpd
    gdf = gpd.read_file(shp_path)
    
    if gdf.crs and gdf.crs.to_string() == _cfg.TARGET_CRS:
        return shp_path
        
    log(f"Reprojecting shapefile to {_cfg.TARGET_CRS}...")
    out_shp = os.path.join(_cfg.DIR_RAW, "watershed_projected.shp")
    gdf.to_crs(_cfg.TARGET_CRS).to_file(out_shp)
    return out_shp

def rasterize_watershed(shp_path):
    log("STEP 1 — Rasterising watershed to create fractional master grid", "STEP")
    
    out_path = os.path.join(OUTPUT_DIR, "area.tif")
    highres_path = os.path.join(_cfg.DIR_RAW, "area_30m.tif")
    fraction_path = os.path.join(_cfg.DIR_RAW, "area_fraction.tif")
    
    proj_shp_path = reproject_shapefile(shp_path)

    # 1. Rasterize at 30m high-resolution
    _run([
        "gdal_rasterize",
        "-burn", "1",
        "-init", "0",
        "-tr",   "30.0", "30.0",
        "-tap",
        "-ot",   "Byte",
        "-a_nodata", "0",
        "-co",   "COMPRESS=LZW",
        proj_shp_path, highres_path,
    ], "gdal_rasterize_30m")

    # 2. Upscale to target resolution using exact fractional coverage (average)
    _run([
        "gdalwarp",
        "-r", "average",
        "-tr", str(RESOLUTION_M), str(RESOLUTION_M),
        "-tap",
        "-overwrite",
        "-co", "COMPRESS=LZW",
        highres_path, fraction_path
    ], "gdalwarp_fraction")

    # 3. Create boolean area.tif (any-touch rule)
    info, fraction_arr = load_grid(fraction_path)
    mask_arr = np.where(fraction_arr > 0, 1, 0).astype(np.uint8)
    
    save_aligned(mask_arr, out_path, "uint8", 0, like=fraction_path)

    inside = int(mask_arr.sum())
    log(f"  Inside cells: {inside:,}  ({inside * RESOLUTION_M**2 / 10_000:.1f} ha)")
    log(f"  → {out_path}")
    return out_path, info, mask_arr

def compute_and_download_gee_topo(info):
    log("STEP 2 - Extracting DEM and Gradient at 30m from GEE...", "STEP")
    
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
    
    # Export only DEM at 30m
    combined = dem.rename('dem').toFloat()
    
    raw_dir = _cfg.DIR_RAW
    
    raw_tif = os.path.join(raw_dir, "topo_raw_gee_30m.tif")
    if not os.path.exists(raw_tif):
        log(f"Downloading GEE topo data to {raw_tif}...")
        geemap.ee_export_image(
            combined,
            filename=raw_tif,
            scale=30.0,
            crs=str(info.crs),
            region=region,
            file_per_band=False
        )
    else:
        log(f"Raw GEE data already exists: {raw_tif}")
        
    return raw_tif

def compute_ldd_snapped(fdir_30m, acc_30m, mask_arr, area_tif_path):
    log("STEP 4 — Calculating LDD (Yamazaki Upscaling from 30m to target resolution)", "STEP")
    
    H_300m, W_300m = mask_arr.shape
    H_30m, W_30m = fdir_30m.shape
    factor = H_30m // H_300m
    
    ldd_300m = np.full((H_300m, W_300m), 255, dtype=np.uint8)
    chanleng_300m = np.zeros((H_300m, W_300m), dtype=np.float32)
    
    dir_map_30m = {
        64: (-1, 0),  128: (-1, 1),   1: (0, 1),   2: (1, 1),
         4: (1, 0),     8: (1, -1),  16: (0, -1), 32: (-1, -1)
    }

    def convert_delta_to_pcraster(dy, dx):
        mapping = {
            (-1, -1): 7, (-1, 0): 8, (-1, 1): 9,
            ( 0, -1): 4, ( 0, 0): 5, ( 0, 1): 6,
            ( 1, -1): 1, ( 1, 0): 2, ( 1, 1): 3
        }
        return mapping.get((dy, dx), 5)
        
    fdir_arr = np.array(fdir_30m)
    acc_arr = np.array(acc_30m).astype(np.float32)
    
    # Tracing loop
    for Y in range(H_300m):
        for X in range(W_300m):
            if mask_arr[Y, X] == 0:
                continue
                
            block_acc = acc_arr[Y*factor:(Y+1)*factor, X*factor:(X+1)*factor]
            if np.isnan(block_acc).all() or np.nanmax(block_acc) == 0:
                ldd_300m[Y, X] = 5
                continue
                
            # Local idx in block
            max_idx = np.unravel_index(np.nanargmax(block_acc), block_acc.shape)
            curr_y, curr_x = Y*factor + max_idx[0], X*factor + max_idx[1]
            
            # --- MEANDERING LENGTH CALCULATION ---
            length = 0.0
            ly, lx = max_idx[0], max_idx[1]
            while True:
                dir_30m = fdir_arr[Y*factor + ly, X*factor + lx]
                if dir_30m in [128, 2, 8, 32]:
                    length += 30.0 * 1.41421356
                elif dir_30m in [64, 1, 4, 16]:
                    length += 30.0
                    
                best_up_y, best_up_x = -1, -1
                max_up_acc = -1
                
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        if dy == 0 and dx == 0: continue
                        ny, nx = ly + dy, lx + dx
                        if 0 <= ny < factor and 0 <= nx < factor:
                            n_dir = fdir_arr[Y*factor + ny, X*factor + nx]
                            required_dir = {
                                (-1, 0): 4, (-1, 1): 8, (0, 1): 16, (1, 1): 32,
                                (1, 0): 64, (1, -1): 128, (0, -1): 1, (-1, -1): 2
                            }.get((dy, dx), 0)
                            
                            if n_dir == required_dir:
                                up_acc = block_acc[ny, nx]
                                if up_acc > max_up_acc:
                                    max_up_acc = up_acc
                                    best_up_y, best_up_x = ny, nx
                                    
                if best_up_y != -1:
                    ly, lx = best_up_y, best_up_x
                else:
                    break
            chanleng_300m[Y, X] = length
            # ---------------------------------------
            
            steps = 0
            while True:
                dir_30m = fdir_arr[curr_y, curr_x]
                if dir_30m == 0 or dir_30m not in dir_map_30m: 
                    ldd_300m[Y, X] = 5
                    break
                    
                dy, dx = dir_map_30m[dir_30m]
                curr_y += dy
                curr_x += dx
                steps += 1
                
                if curr_y < 0 or curr_x < 0 or curr_y >= H_30m or curr_x >= W_30m:
                    ldd_300m[Y, X] = 5
                    break
                
                new_Y, new_X = curr_y // factor, curr_x // factor
                if new_Y != Y or new_X != X:
                    ldd_300m[Y, X] = convert_delta_to_pcraster(new_Y - Y, new_X - X)
                    break
                    
                if steps > 400:
                    ldd_300m[Y, X] = 5
                    break
                    
    out_path = os.path.join(OUTPUT_DIR, "ldd.tif")
    save_aligned(ldd_300m, out_path, "uint8", 255, like=area_tif_path)
    
    len_path = os.path.join(OUTPUT_DIR, "chanleng_300m.tif")
    save_aligned(chanleng_300m, len_path, "float32", -9999, like=area_tif_path)
    
    return out_path, ldd_300m

def calc_d8_gradient(dem, res):
    grad = np.zeros_like(dem)
    padded = np.pad(dem, pad_width=1, mode='constant', constant_values=np.nan)
    d_ortho = res
    d_diag = res * np.sqrt(2)
    
    neighbors = [
        (-1, 0, d_ortho), (1, 0, d_ortho), (0, -1, d_ortho), (0, 1, d_ortho),
        (-1, -1, d_diag), (-1, 1, d_diag), (1, -1, d_diag), (1, 1, d_diag)
    ]
    
    for dy, dx, dist in neighbors:
        shifted = padded[1+dy:padded.shape[0]-1+dy, 1+dx:padded.shape[1]-1+dx]
        slope = (dem - shifted) / dist
        grad = np.fmax(grad, slope)
        
    grad[np.isnan(dem)] = np.nan
    return grad

def process_local_topo(raw_tif, mask_arr, info, area_tif_path):
    log("STEP 3 - Processing 30m DEM and Upscaling...", "STEP")
    tif_paths = {}
    maps_dir = OUTPUT_DIR
    
    try:
        from pysheds.grid import Grid
    except ImportError:
        log("Install pysheds: pip install pysheds", "ERROR")
        sys.exit(1)

    log("  Filling pits, depressions, and resolving flats on 30m DEM via pysheds...")
    grid = Grid.from_raster(raw_tif)
    dem_30m = grid.read_raster(raw_tif)
    
    dem_30m = grid.fill_pits(dem_30m)
    dem_30m = grid.fill_depressions(dem_30m)
    dem_30m = grid.resolve_flats(dem_30m)
    
    log("  Calculating 30m flow direction and accumulation...")
    dirmap = (64, 128, 1, 2, 4, 8, 16, 32)
    fdir_30m = grid.flowdir(dem_30m, dirmap=dirmap)
    acc_30m = grid.accumulation(fdir_30m)
    
    dem_30m_arr = dem_30m.astype(np.float32)
    
    with rasterio.open(raw_tif) as src:
        # Pysheds keeps the nodata values, we can set them to nan to avoid interfering with average
        src_nd = src.nodata if src.nodata is not None else -9999
        dem_30m_arr[dem_30m_arr == src_nd] = np.nan
        
        # Save processed 30m DEM
        dem_30m_out = os.path.join(maps_dir, "dem_30m.tif")
        profile = src.profile.copy()
        profile.update(dtype="float32", nodata=-9999, compress="lzw")
        out_arr = np.where(np.isnan(dem_30m_arr), -9999, dem_30m_arr).astype(np.float32)
        with rasterio.open(dem_30m_out, "w", **profile) as dst:
            dst.write(out_arr, 1)
        tif_paths['dem_30m'] = dem_30m_out
        log(f"  Saved processed 30m DEM -> {dem_30m_out}")
        
        log("  Upscaling Processed 30m DEM (average)...")
        # Upscale DEM using average
        dem_aligned = reproject_to_grid(
            src_array=dem_30m_arr, src_transform=src.transform, src_crs=src.crs,
            like=area_tif_path, src_nodata=np.nan, dst_nodata=-9999,
            resampling_method="average"
        ).astype(np.float32)

        log("  Calculating Elvstd locally via sub-grid standard deviation on processed 30m DEM...")
        factor = max(1, int(RESOLUTION_M / 30.0))
        
        import affine
        hr_transform = info.transform * affine.Affine.scale(1.0/factor, 1.0/factor)
        
        from rasterio.warp import reproject, Resampling
        dem_hr_exact = np.full((info.height * factor, info.width * factor), np.nan, dtype=np.float32)
        reproject(
            source=dem_30m_arr, destination=dem_hr_exact,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=hr_transform, dst_crs=info.crs,
            resampling=Resampling.bilinear,
            src_nodata=np.nan, dst_nodata=np.nan
        )
        
        blocks = dem_hr_exact.reshape(info.height, factor, info.width, factor)
        elvstd_aligned = np.nanstd(blocks, axis=(1, 3)).astype(np.float32)
        elvstd_aligned[np.isnan(elvstd_aligned)] = -9999
        
    log("  Calculating Gradient at target resolution via D8 (steepest descent)...")
    grad_aligned = calc_d8_gradient(dem_aligned.copy(), float(RESOLUTION_M))

    aligned_bands = {
        'dem': dem_aligned,
        'gradient': grad_aligned,
        'elvstd': elvstd_aligned
    }

    results = {}
    dem_path = None
    
    for name in ['gradient', 'elvstd', 'dem']:
        arr = aligned_bands[name]
        
        if name == 'gradient':
            arr = np.where((arr <= 0) | np.isnan(arr), 1e-5, arr)
        elif name == 'elvstd':
            arr = np.where((arr < 0) | np.isnan(arr), 0.0, arr)
        # For dem, just use the array directly
            
        out_name = f"dem_{int(RESOLUTION_M)}m" if name == 'dem' else name
        final = np.where((mask_arr > 0), arr, -9999).astype(np.float32)
        tif_paths[out_name] = os.path.join(maps_dir, f"{out_name}.tif")
        save_aligned(final, tif_paths[out_name], "float32", -9999, like=area_tif_path)
        results[name] = final
        
        if name == 'dem':
            dem_path = tif_paths[out_name]
            
    results['fdir_30m'] = fdir_30m
    results['acc_30m'] = acc_30m
            
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

        # Create continuous colormaps for physical properties
        panel(axes[0], mask,     "area.tif\n(Master Mask)", "Blues", "0 or 1", nodata=-1)
        panel(axes[1], ldd,      "ldd.nc\n(NetCDF)",   plt.cm.get_cmap('Set3', 9), "Flow Dir", -0.5, 1, 9)
        panel(axes[2], gradient, "gradient.nc\n[m/m]",  "YlOrRd", "m/m", 0)
        panel(axes[3], elvstd,   "elvstd.nc\n[m]",      "Purples", "m", 0)

        plt.tight_layout()
        out = os.path.join(_cfg.BASE_DIR, "TOPO_VISUAL_CHECK.png")
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
    
    ldd_path, ldd_masked = compute_ldd_snapped(results['fdir_30m'], results['acc_30m'], mask_arr, area_tif_path)
    tif_paths['ldd'] = ldd_path
    
    nc_paths = convert_to_netcdf(tif_paths, area_tif_path)
    
    # Exclude 30m outputs from 300m alignment validation
    val_paths = {k: v for k, v in tif_paths.items() if not k.endswith('_30m')}
    val_paths['area'] = area_tif_path
    validate_alignment(val_paths, info)
    
    visualize(ldd_masked, results['gradient'], results['elvstd'], mask_arr, info)

if __name__ == "__main__":
    main()