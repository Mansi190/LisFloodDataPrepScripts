import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import xarray as xr
import rasterio

import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            load_grid, snap_to_grid, init_ee)

# =============================================================================
#  CONFIGURATION
# =============================================================================
# Dates come from pipeline_config so all forcing scripts stay in lockstep; one
# continuous span covers prerun + cold + warm runs, which the settings XMLs slice.
START_DATE = _cfg.FORCING_START
END_DATE   = _cfg.FORCING_END

AREA_TIF   = _cfg.AREA_TIF
OUTPUT_DIR = _cfg.DIR_LAI
NODATA_VAL = _cfg.NODATA_FLOAT

# LISFLOOD reads LAI as a sparse stack of one climatological year, reused every
# year (see LAIOfDay in the settings XML). 36 ten-day maps is the JRC standard.
N_LAI_SLOTS  = 36
LAI_CLIM_YEAR = 2000   # nominal year the 36 slots are stamped with

# =============================================================================

def fetch_gee_timeseries(info, start_date, end_date):
    log(f"STEP 1 - Fetching LAI data from GEE ({start_date} to {end_date})", "STEP")
    
    try:
        import ee
        import geemap
    except ImportError:
        log("Missing: earthengine-api geemap", "ERROR")
        sys.exit(1)
        
    init_ee(_cfg.GEE_PROJECT)

    # 1. Define region based on the canonical grid
    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    buf = 5000  # 5km buffer to prevent edge artifacts
    region = ee.Geometry.Rectangle(
        [xmin - buf, ymin - buf, xmax + buf, ymax + buf], 
        proj=str(info.crs), 
        geodesic=False
    )

    # Helper function to process each 4-day image (Bulk LAI)
    def process_lai_bulk(img):
        lai = img.select('Lai')
        valid_mask = lai.lte(100)
        # Apply valid mask, then scale
        val = lai.updateMask(valid_mask).multiply(0.1)
        
        upscaled = val.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                      .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
                      .unmask(-9999)
        date_str = img.date().format('YYYYMMdd')
        # .multiply(0.1) promotes to double; cast back so a request is 4 bytes/px
        return (upscaled.toFloat().rename([date_str])
                .set('system:time_start', img.get('system:time_start')))

    log("  Preparing MODIS LAI (MCD15A3H)...")
    
    # Pad dates by 8 days to ensure we have boundary points for interpolation
    fetch_start = pd.to_datetime(start_date) - pd.Timedelta(days=8)
    fetch_end = pd.to_datetime(end_date) + pd.Timedelta(days=8)
    
    lai_col = ee.ImageCollection("MODIS/061/MCD15A3H") \
                .filterBounds(region) \
                .filterDate(fetch_start, fetch_end)
    
    lai_bulk_col = lai_col.map(process_lai_bulk)
    
    # Extract the actual dates
    dates_list_gee = lai_bulk_col.aggregate_array('system:time_start').getInfo()
    obs_dates = pd.to_datetime(dates_list_gee, unit='ms')
    
    lai_bulk_img = lai_bulk_col.toBands()

    # --- DOWNLOAD ---
    raw_dir = _cfg.DIR_RAW
    make_dirs(raw_dir)
    
    def download_in_chunks(img, prefix, chunk_size=None):
        # getDownloadURL caps one request at ~48 MB. A fixed 60 bands works on a small
        # basin and fails on a large one (this grid: 60 bands = 133 MB), and geemap
        # SWALLOWS the failure -- it returns normally leaving no file behind -- so the
        # only reliable signal is "did the file appear".
        if chunk_size is None:
            mb_per_band = info.width * info.height * 4 / 1e6
            chunk_size = max(1, int(25 / mb_per_band))
            log(f"  {prefix}: {mb_per_band:.2f} MB/band -> {chunk_size} bands/request")

        band_names = img.bandNames().getInfo()
        total_bands = len(band_names)
        chunk_files = []
        for i in range(0, total_bands, chunk_size):
            chunk_img = img.select(band_names[i:i + chunk_size])
            chunk_file = os.path.join(raw_dir, f"{prefix}_raw_{i}.tif")
            if not os.path.exists(chunk_file):
                log(f"  Downloading {prefix} chunk {i//chunk_size + 1}"
                    f"/{(total_bands+chunk_size-1)//chunk_size}...")
                for attempt in range(4):
                    geemap.ee_export_image(chunk_img, filename=chunk_file,
                                           scale=_cfg.RESOLUTION_M, crs=str(info.crs),
                                           region=region, file_per_band=False)
                    if os.path.exists(chunk_file):
                        break
                    if attempt < 3:
                        wait = 5 * (3 ** attempt)
                        log(f"    {os.path.basename(chunk_file)} failed; retry "
                            f"{attempt + 1}/3 in {wait}s", "WARN")
                        time.sleep(wait)
                else:
                    raise RuntimeError(f"export produced no file after 4 tries: {chunk_file}")
            chunk_files.append(chunk_file)
        return chunk_files

    log("  Downloading Bulk LAI (chunked)...")
    lai_bulk_tifs = download_in_chunks(lai_bulk_img, "lai_bulk")
        
    return lai_bulk_tifs, obs_dates

def assemble_netcdf(tif_paths, info, base_mask, lulc_mask, obs_dates, start_date, end_date, nc_path):
    log(f"  Processing NetCDF for {os.path.basename(nc_path)}...")
    
    # Generate coordinates exactly from area.tif
    x_coords = [info.transform.c + (i + 0.5) * info.transform.a for i in range(info.width)]
    y_coords = [info.transform.f + (i + 0.5) * info.transform.e for i in range(info.height)]
    
    # ------------------------------------------------------------------ OOM FIX
    # WAS: every chunk read into a list and concatenated -> a 2021 x 865 x 642
    # float32 array (4.5 GB) held alongside cube_obs (another 4.5 GB).
    #
    #   raw_data_list = []
    #   for tif_path in tif_paths:
    #       with rasterio.open(tif_path) as src:
    #           raw_data_list.append(src.read())
    #   raw_data = np.concatenate(raw_data_list, axis=0)
    #
    # NOW: chunks are streamed one at a time and folded straight into 36 ten-day
    # climatological accumulators, so nothing bigger than one chunk is ever held.
    # 36 is LISFLOOD's documented LAI design: a sparse map stack of one
    # climatological year, reused for every simulated year.
    num_obs = len(obs_dates)
    obs_dates = pd.DatetimeIndex(obs_dates)

    # ten-day slot for every observation: doy 1-10 -> 0, 11-20 -> 1, ... (36 slots)
    slots = np.minimum((obs_dates.dayofyear.values - 1) // 10, N_LAI_SLOTS - 1)

    acc = np.zeros((N_LAI_SLOTS, info.height, info.width), dtype=np.float64)
    cnt = np.zeros((N_LAI_SLOTS, info.height, info.width), dtype=np.int32)

    b = 0
    for tif_path in tif_paths:
        with rasterio.open(tif_path) as src:
            chunk = src.read()
        for k in range(chunk.shape[0]):
            if b >= num_obs:
                break
            band = chunk[k].astype(np.float32)
            band[band <= -9999] = np.nan
            aligned = snap_to_grid(band, AREA_TIF, np.nan)
            final = np.where((base_mask > 0) & (lulc_mask > 0), aligned, np.nan)
            ok = ~np.isnan(final)
            sl = slots[b]
            acc[sl][ok] += final[ok]
            cnt[sl][ok] += 1
            b += 1
        del chunk
    log(f"    folded {b} observations into {N_LAI_SLOTS} ten-day slots")

    with np.errstate(invalid="ignore"):
        clim = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan).astype(np.float32)
    del acc, cnt

    # a slot with no valid observation anywhere borrows from its neighbours in time
    for sl in range(N_LAI_SLOTS):
        if np.all(np.isnan(clim[sl])):
            nxt = clim[(sl + 1) % N_LAI_SLOTS]
            clim[sl] = nxt

    # nominal calendar year carrying the 36 slots (mid-slot dates)
    clim_dates = pd.to_datetime(
        [f"{LAI_CLIM_YEAR}-01-01" for _ in range(N_LAI_SLOTS)]
    ) + pd.to_timedelta(np.arange(N_LAI_SLOTS) * 10 + 5, unit="D")

    ds_clim = xr.Dataset(
        data_vars={"lai": (["time", "y", "x"], clim)},
        coords={"time": clim_dates, "y": y_coords, "x": x_coords},
    ).assign_attrs({
        "description": "LISFLOOD LAI forcing - 36 ten-day climatology",
        "crs": str(info.crs),
        "source": "GEE (MODIS MCD15A3H)",
        "climatology_from": f"{obs_dates.min():%Y-%m-%d}..{obs_dates.max():%Y-%m-%d}",
        "n_observations": int(b),
    })

    vals = ds_clim["lai"].values
    vals[np.isnan(vals)] = NODATA_VAL
    ds_clim["lai"].values = vals

    ds_clim.to_netcdf(nc_path, encoding={"lai": {"_FillValue": NODATA_VAL,
                                                 "zlib": True, "complevel": 4}})
    log(f"  ✔ Saved {nc_path}  ({N_LAI_SLOTS} steps)")
    return ds_clim

def process_and_save(lai_bulk_tifs, obs_dates, info, mask):
    log("STEP 2 - Assembling and Interpolating NetCDF time-series", "STEP")
    maps_dir = OUTPUT_DIR
    make_dirs(maps_dir)
    make_dirs(_cfg.DIR_LAI_FOREST)
    make_dirs(_cfg.DIR_LAI_OTHER)
    
    # Load fracforest fraction map
    fracforest_path = os.path.join(_cfg.DIR_FRACTION, "fracforest.tif")
    
    if not os.path.exists(fracforest_path):
        log("fracforest.tif not found! Please run lisflood_frac_lulc_preprocessing.py first.", "ERROR")
        sys.exit(1)
        
    with rasterio.open(fracforest_path) as src:
        fracforest = src.read(1)
        # Handle nan or nodata
        fracforest = np.where((fracforest == src.nodata) | np.isnan(fracforest), 0, fracforest)
    
    lai_forest_nc = os.path.join(_cfg.DIR_LAI_FOREST, "laif.nc")
    lai_other_nc = os.path.join(_cfg.DIR_LAI_OTHER, "laio.nc")
    
    # Assemble one master NetCDF from the bulk tifs
    ds_bulk = assemble_netcdf(lai_bulk_tifs, info, mask, np.ones_like(mask), obs_dates, START_DATE, END_DATE, os.path.join(maps_dir, "lai_bulk_temp.nc"))
    
    bulk_lai = ds_bulk["lai"].values
    num_times, height, width = bulk_lai.shape
    
    log("  Performing Inverse Distance Weighting (IDW) Interpolation based on pure forest/non-forest pixels...")
    from scipy.spatial import cKDTree
    
    # Define pure masks based on LISFLOOD manual
    forest_mask = (mask > 0) & (fracforest >= 0.70)
    if not np.any(forest_mask):
        log("  No pixels with >= 70% forest found! Falling back to top 5% forest pixels...", "WARN")
        threshold = np.percentile(fracforest[mask > 0], 95)
        forest_mask = (mask > 0) & (fracforest >= threshold)
        
    other_mask = (mask > 0) & (fracforest <= 0.20)
    if not np.any(other_mask):
        log("  No pixels with <= 20% forest found! Falling back to bottom 5% forest pixels...", "WARN")
        threshold = np.percentile(fracforest[mask > 0], 5)
        other_mask = (mask > 0) & (fracforest <= threshold)
        
    # Target coordinates for IDW (all cells in the basin)
    ty, tx = np.where(mask > 0)
    target_coords = np.column_stack((ty, tx))
    
    lai_forest_cube = np.full_like(bulk_lai, NODATA_VAL)
    lai_other_cube = np.full_like(bulk_lai, NODATA_VAL)
    
    def idw_interpolate_t(bulk_t, src_mask, tgt_coords, k=5, p=2):
        # Find valid source points for this time step
        valid_src = src_mask & (bulk_t != NODATA_VAL) & ~np.isnan(bulk_t)
        sy, sx = np.where(valid_src)
        
        if len(sy) == 0:
            return np.full(len(tgt_coords), NODATA_VAL)
            
        src_coords = np.column_stack((sy, sx))
        src_vals = bulk_t[sy, sx]
        
        if len(sy) == 1:
            return np.full(len(tgt_coords), src_vals[0])
            
        actual_k = min(k, len(sy))
        tree = cKDTree(src_coords)
        dist, idx = tree.query(tgt_coords, k=actual_k)
        
        if actual_k == 1:
            dist = dist.reshape(-1, 1)
            idx = idx.reshape(-1, 1)
            
        dist = np.maximum(dist, 1e-12)
        weights = 1.0 / (dist ** p)
        
        # Handle exact matches
        exact = dist < 1e-8
        for i in range(len(tgt_coords)):
            if np.any(exact[i]):
                weights[i, :] = 0
                weights[i, exact[i]] = 1.0
                
        sum_weights = np.sum(weights, axis=1, keepdims=True)
        norm_weights = weights / sum_weights
        return np.sum(norm_weights * src_vals[idx], axis=1)

    for t in range(num_times):
        bulk_t = bulk_lai[t]
        
        # Interpolate for Forest
        f_interp = idw_interpolate_t(bulk_t, forest_mask, target_coords)
        lai_forest_cube[t, ty, tx] = f_interp
        
        # Interpolate for Other
        o_interp = idw_interpolate_t(bulk_t, other_mask, target_coords)
        lai_other_cube[t, ty, tx] = o_interp
    
    ds_forest = ds_bulk.copy(deep=True)
    ds_forest["lai"].values = lai_forest_cube
    ds_forest.to_netcdf(lai_forest_nc, encoding={"lai": {"_FillValue": NODATA_VAL, "zlib": True, "complevel": 4}})
    
    ds_other = ds_bulk.copy(deep=True)
    ds_other["lai"].values = lai_other_cube
    ds_other.to_netcdf(lai_other_nc, encoding={"lai": {"_FillValue": NODATA_VAL, "zlib": True, "complevel": 4}})
    
    log(f"  ✔ Saved {lai_forest_nc}")
    log(f"  ✔ Saved {lai_other_nc}")
    
    # Clean up temp bulk file
    if os.path.exists(os.path.join(maps_dir, "lai_bulk_temp.nc")):
        os.remove(os.path.join(maps_dir, "lai_bulk_temp.nc"))
    
    return ds_forest, ds_other


def main():
    print("\n" + "=" * 65)
    print("  LISFLOOD LAI FORCING GENERATOR")
    print("  Reference raster : " + AREA_TIF)
    print("=" * 65 + "\n")
    
    check_imports(["ee", "geemap", "rasterio", "xarray", "pandas"])
    
    info, mask = load_grid(AREA_TIF)
    
    lai_bulk_tifs, obs_dates = fetch_gee_timeseries(info, START_DATE, END_DATE)
    
    ds_forest, ds_other = process_and_save(lai_bulk_tifs, obs_dates, info, mask)
    
    
    print("\n" + "=" * 65)
    print("  ★ DONE - LAI data perfectly aligned and stacked")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
