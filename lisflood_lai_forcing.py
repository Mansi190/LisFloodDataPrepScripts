import os
import sys
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
START_DATE = "2024-01-01"
END_DATE   = "2024-12-31"

AREA_TIF   = _cfg.AREA_TIF
OUTPUT_DIR = _cfg.OUTPUT_LAI
NODATA_VAL = _cfg.NODATA_FLOAT

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

    # Get 10m LULC corestack masks
    lulc10m = ee.Image("projects/corestack-datasets/assets/datasets/LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
    lulc_label = lulc10m.select('predicted_label')
    forestMask = lulc_label.eq(6)
    otherMask = lulc_label.eq(5).Or(lulc_label.eq(7)).Or(lulc_label.gte(8).And(lulc_label.lte(12)))

    # Helper function to process each 4-day image (Forest)
    def process_lai_forest(img):
        lai = img.select('Lai')
        valid_mask = lai.lte(100)
        # Apply valid mask, then apply 10m forest mask, then scale
        val = lai.updateMask(valid_mask).updateMask(forestMask).multiply(0.1)
        
        upscaled = val.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                      .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
                      .unmask(-9999)
        date_str = img.date().format('YYYYMMdd')
        return upscaled.rename([date_str]).set('system:time_start', img.get('system:time_start'))

    # Helper function to process each 4-day image (Other)
    def process_lai_other(img):
        lai = img.select('Lai')
        valid_mask = lai.lte(100)
        # Apply valid mask, then apply 10m other mask, then scale
        val = lai.updateMask(valid_mask).updateMask(otherMask).multiply(0.1)
        
        upscaled = val.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                      .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
                      .unmask(-9999)
        date_str = img.date().format('YYYYMMdd')
        return upscaled.rename([date_str]).set('system:time_start', img.get('system:time_start'))

    log("  Preparing MODIS LAI (MCD15A3H)...")
    
    # Pad dates by 8 days to ensure we have boundary points for interpolation
    fetch_start = pd.to_datetime(start_date) - pd.Timedelta(days=8)
    fetch_end = pd.to_datetime(end_date) + pd.Timedelta(days=8)
    
    lai_col = ee.ImageCollection("MODIS/061/MCD15A3H") \
                .filterBounds(region) \
                .filterDate(fetch_start, fetch_end)
    
    lai_forest_col = lai_col.map(process_lai_forest)
    lai_other_col = lai_col.map(process_lai_other)
    
    # Extract the actual dates
    dates_list_gee = lai_forest_col.aggregate_array('system:time_start').getInfo()
    obs_dates = pd.to_datetime(dates_list_gee, unit='ms')
    
    lai_forest_img = lai_forest_col.toBands()
    lai_other_img = lai_other_col.toBands()

    # --- DOWNLOAD ---
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)
    
    def download_in_chunks(img, prefix, chunk_size=60):
        band_names = img.bandNames().getInfo()
        total_bands = len(band_names)
        chunk_files = []
        for i in range(0, total_bands, chunk_size):
            chunk_bands = band_names[i:i+chunk_size]
            chunk_img = img.select(chunk_bands)
            chunk_file = os.path.join(raw_dir, f"{prefix}_raw_{i}.tif")
            if not os.path.exists(chunk_file):
                log(f"  Downloading {prefix} chunk {i//chunk_size + 1}/{(total_bands+chunk_size-1)//chunk_size}...")
                geemap.ee_export_image(chunk_img, filename=chunk_file, scale=_cfg.RESOLUTION_M, crs=str(info.crs), region=region, file_per_band=False)
            else:
                log(f"  Chunk already exists: {chunk_file}")
            chunk_files.append(chunk_file)
        return chunk_files

    log("  Downloading Forest LAI (chunked)...")
    lai_forest_tifs = download_in_chunks(lai_forest_img, "lai_forest")
    
    log("  Downloading Other LAI (chunked)...")
    lai_other_tifs = download_in_chunks(lai_other_img, "lai_other")
        
    return lai_forest_tifs, lai_other_tifs, obs_dates

def assemble_netcdf(tif_paths, info, base_mask, lulc_mask, obs_dates, start_date, end_date, nc_path):
    log(f"  Processing NetCDF for {os.path.basename(nc_path)}...")
    
    # Generate coordinates exactly from area.tif
    x_coords = [info.transform.c + (i + 0.5) * info.transform.a for i in range(info.width)]
    y_coords = [info.transform.f + (i + 0.5) * info.transform.e for i in range(info.height)]
    
    raw_data_list = []
    for tif_path in tif_paths:
        with rasterio.open(tif_path) as src:
            raw_data_list.append(src.read())
            
    raw_data = np.concatenate(raw_data_list, axis=0) if raw_data_list else np.empty((0, info.height, info.width))
        
    num_obs = len(obs_dates)
    if raw_data.shape[0] < num_obs:
        log(f"Warning: Fetched {raw_data.shape[0]} bands from GEE, expected {num_obs}.", "WARN")
        num_obs = raw_data.shape[0]
        obs_dates = obs_dates[:num_obs]
        
    # Initialize the 3D cube for observed dates (time, y, x)
    cube_obs = np.full((num_obs, info.height, info.width), NODATA_VAL, dtype=np.float32)
    
    for i in range(num_obs):
        band_data = raw_data[i].astype(np.float32)
        # Any value <= -9999 is considered nodata
        band_data[band_data <= -9999] = np.nan
        
        # Snap and mask to the canonical grid and LULC fraction mask
        aligned = snap_to_grid(band_data, AREA_TIF, np.nan)
        final = np.where((base_mask > 0) & (lulc_mask > 0), aligned, np.nan)
        
        cube_obs[i, :, :] = final

    # Create xarray Dataset with 4-day intervals
    ds_obs = xr.Dataset(
        data_vars={
            "lai": (["time", "y", "x"], cube_obs)
        },
        coords={
            "time": obs_dates,
            "y": y_coords,
            "x": x_coords
        }
    )
    
    log("  Interpolating 4-day LAI to daily time series...")
    # Generate daily dates
    daily_dates = pd.date_range(start=start_date, end=end_date, freq='D')
    
    # Interpolate linearly over time and then reindex to daily dates
    # We use resample('1D').interpolate('linear') to fill the gaps, then sel(time=daily_dates)
    ds_daily = ds_obs.resample(time="1D").interpolate("linear").sel(time=daily_dates)
    
    # Replace remaining NaNs with NODATA_VAL
    lai_daily_vals = ds_daily["lai"].values
    lai_daily_vals[np.isnan(lai_daily_vals)] = NODATA_VAL
    ds_daily["lai"].values = lai_daily_vals

    ds_daily = ds_daily.assign_attrs({
        "description": "LISFLOOD LAI Forcing",
        "crs": str(info.crs),
        "source": "GEE (MODIS MCD15A3H)"
    })
    
    # Save to NetCDF
    ds_daily.to_netcdf(nc_path, encoding={"lai": {"_FillValue": NODATA_VAL, "zlib": True, "complevel": 4}})
    log(f"  ✔ Saved {nc_path}")
    return ds_daily

def process_and_save(lai_forest_tifs, lai_other_tifs, obs_dates, info, mask):
    log("STEP 2 - Assembling and Interpolating NetCDF time-series", "STEP")
    maps_dir = os.path.join(OUTPUT_DIR, "maps")
    make_dirs(maps_dir)
    
    # Load LULC fraction maps
    fracforest_path = os.path.join(_cfg.OUTPUT_LULC, "maps", "fracforest.tif")
    fracother_path = os.path.join(_cfg.OUTPUT_LULC, "maps", "fracother.tif")
    
    if not os.path.exists(fracforest_path) or not os.path.exists(fracother_path):
        log("LULC fraction maps not found! Please run lisflood_frac_lulc_preprocessing.py first.", "ERROR")
        sys.exit(1)
        
    with rasterio.open(fracforest_path) as src:
        fracforest = src.read(1)
        # Handle nan or nodata
        fracforest = np.where((fracforest == src.nodata) | np.isnan(fracforest), 0, fracforest)
        
    with rasterio.open(fracother_path) as src:
        fracother = src.read(1)
        fracother = np.where((fracother == src.nodata) | np.isnan(fracother), 0, fracother)
    
    lai_forest_nc = os.path.join(maps_dir, "lai_forest.nc")
    lai_other_nc = os.path.join(maps_dir, "lai_other.nc")
    
    ds_forest = assemble_netcdf(lai_forest_tifs, info, mask, fracforest, obs_dates, START_DATE, END_DATE, lai_forest_nc)
    ds_other = assemble_netcdf(lai_other_tifs, info, mask, fracother, obs_dates, START_DATE, END_DATE, lai_other_nc)
    
    return ds_forest, ds_other

def visualize(ds_forest, ds_other, info):
    log("STEP 3 - Creating LAI_VISUAL_CHECK.png (Timeseries Graph)", "STEP")
    try:
        import matplotlib.pyplot as plt
        
        # Calculate the spatial mean for each time step, ignoring NODATA
        # ds_forest['lai'] is (time, y, x). We want to mean over y and x.
        lai_forest_masked = ds_forest['lai'].where(ds_forest['lai'] != NODATA_VAL)
        lai_other_masked = ds_other['lai'].where(ds_other['lai'] != NODATA_VAL)
        
        # Calculate spatial mean over 'y' and 'x' dimensions
        mean_forest = lai_forest_masked.mean(dim=['y', 'x'])
        mean_other = lai_other_masked.mean(dim=['y', 'x'])
        
        # Extract times and values
        times = pd.to_datetime(mean_forest.time.values)
        vals_forest = mean_forest.values
        vals_other = mean_other.values
        
        fig, ax = plt.subplots(figsize=(10, 5))
        fig.suptitle(
            f"Basin-Average LAI Timeseries (Forest vs Other)  |  {info.crs}",
            fontsize=14, fontweight="bold"
        )
        
        ax.plot(times, vals_forest, color='forestgreen', linewidth=4, marker='o', markersize=4, label="Forest LAI", alpha=0.7)
        ax.plot(times, vals_other, color='darkorange', linewidth=2, linestyle='--', marker='s', markersize=3, label="Other LAI")
        ax.set_ylabel("Leaf Area Index (LAI)", fontweight="bold")
        ax.set_xlabel("Date", fontweight="bold")
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.legend()
        
        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "LAI_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        pass

def main():
    print("\n" + "=" * 65)
    print("  LISFLOOD LAI FORCING GENERATOR")
    print("  Reference raster : " + AREA_TIF)
    print("=" * 65 + "\n")
    
    check_imports(["ee", "geemap", "rasterio", "xarray", "pandas"])
    
    info, mask = load_grid(AREA_TIF)
    
    lai_forest_tifs, lai_other_tifs, obs_dates = fetch_gee_timeseries(info, START_DATE, END_DATE)
    
    ds_forest, ds_other = process_and_save(lai_forest_tifs, lai_other_tifs, obs_dates, info, mask)
    
    visualize(ds_forest, ds_other, info)
    
    print("\n" + "=" * 65)
    print("  ★ DONE - LAI data perfectly aligned and stacked")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
