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
OUTPUT_DIR = _cfg.OUTPUT_METEO
NODATA_VAL = _cfg.NODATA_FLOAT

# =============================================================================

def fetch_gee_timeseries(info, start_date, end_date):
    log(f"STEP 1 - Fetching meteorological data from GEE ({start_date} to {end_date})", "STEP")
    
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

    # Helper function to process each daily image
    def process_image(img, band_name, var_name, scale_factor=1.0, offset=0.0):
        # Apply scaling and offset (e.g. converting K to C)
        val = img.select(band_name).multiply(scale_factor).add(offset)
        # Average upscale to our grid
        upscaled = val.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                      .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M)
        # Rename band to the original image date string (e.g. "20240101")
        date_str = img.date().format('YYYYMMdd')
        return upscaled.rename([date_str]).set('system:time_start', img.get('system:time_start'))

    # --- PRECIPITATION (CHIRPS) ---
    log("  Preparing CHIRPS Precipitation (pr)...")
    pr_col = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY") \
                .filterBounds(region) \
                .filterDate(start_date, pd.to_datetime(end_date) + pd.Timedelta(days=1))
    
    pr_mapped = pr_col.map(lambda img: process_image(img, 'precipitation', 'pr'))
    pr_img = pr_mapped.toBands()  # Collapse time to bands

    # --- TEMPERATURE (ERA5-Land) ---
    log("  Preparing ERA5-Land Mean Air Temperature (ta)...")
    ta_col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR") \
                .filterBounds(region) \
                .filterDate(start_date, pd.to_datetime(end_date) + pd.Timedelta(days=1))
    
    # ERA5 temperature is in Kelvin. Subtract 273.15 to get Celsius.
    ta_mapped = ta_col.map(lambda img: process_image(img, 'temperature_2m', 'ta', offset=-273.15))
    ta_img = ta_mapped.toBands()  # Collapse time to bands

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

    log("  Downloading Precipitation (chunked)...")
    pr_tifs = download_in_chunks(pr_img, "pr")
    
    log("  Downloading Temperature (chunked)...")
    ta_tifs = download_in_chunks(ta_img, "ta")
        
    return pr_tifs, ta_tifs

def assemble_netcdf(tif_paths, var_name, info, mask, dates, nc_path):
    log(f"  Processing NetCDF for {var_name}...")
    
    # Generate coordinates exactly from area.tif
    x_coords = [info.transform.c + (i + 0.5) * info.transform.a for i in range(info.width)]
    y_coords = [info.transform.f + (i + 0.5) * info.transform.e for i in range(info.height)]
    
    raw_data_list = []
    for tif_path in tif_paths:
        with rasterio.open(tif_path) as src:
            raw_data_list.append(src.read())
            src_nd = src.nodata if src.nodata is not None else -9999
            
    raw_data = np.concatenate(raw_data_list, axis=0) if raw_data_list else np.empty((0, info.height, info.width))
        
    # The number of bands in the downloaded TIF should match our date range length
    num_days = len(dates)
    if raw_data.shape[0] < num_days:
        log(f"Warning: Fetched {raw_data.shape[0]} days from GEE, expected {num_days}.", "WARN")
        num_days = raw_data.shape[0]
        dates = dates[:num_days]
        
    # Initialize the 3D cube (time, y, x)
    cube = np.full((num_days, info.height, info.width), NODATA_VAL, dtype=np.float32)
    
    for i in range(num_days):
        band_data = raw_data[i].astype(np.float32)
        band_data[band_data == src_nd] = np.nan
        
        # Snap and mask to the canonical grid
        aligned = snap_to_grid(band_data, AREA_TIF, np.nan)
        final = np.where((mask > 0), aligned, NODATA_VAL)
        
        # Replace remaining NaNs with NODATA_VAL
        final[np.isnan(final)] = NODATA_VAL
        cube[i, :, :] = final

    # Create xarray Dataset
    ds = xr.Dataset(
        data_vars={
            var_name: (["time", "y", "x"], cube)
        },
        coords={
            "time": dates,
            "y": y_coords,
            "x": x_coords
        },
        attrs={
            "description": f"LISFLOOD Meteorological Forcing: {var_name}",
            "crs": str(info.crs),
            "source": "GEE (CHIRPS / ERA5-Land)"
        }
    )
    
    # Save to NetCDF
    ds.to_netcdf(nc_path, encoding={var_name: {"_FillValue": NODATA_VAL, "zlib": True, "complevel": 4}})
    log(f"  ✔ Saved {nc_path}")
    return ds

def process_and_save(pr_tifs, ta_tifs, info, mask):
    log("STEP 2 - Assembling NetCDF time-series", "STEP")
    maps_dir = os.path.join(OUTPUT_DIR, "maps")
    make_dirs(maps_dir)
    
    dates = pd.date_range(start=START_DATE, end=END_DATE, freq='D')
    
    pr_nc = os.path.join(maps_dir, "pr.nc")
    ta_nc = os.path.join(maps_dir, "ta.nc")
    
    ds_pr = assemble_netcdf(pr_tifs, "pr", info, mask, dates, pr_nc)
    ds_ta = assemble_netcdf(ta_tifs, "ta", info, mask, dates, ta_nc)
    
    return ds_pr, ds_ta

def visualize(ds_pr, ds_ta, info, plot_date=START_DATE):
    log(f"STEP 3 - Creating METEO_VISUAL_CHECK.png for date {plot_date}", "STEP")
    try:
        import matplotlib.pyplot as plt
        
        # Select the specific date to plot
        try:
            day_pr = ds_pr.sel(time=plot_date)
            day_ta = ds_ta.sel(time=plot_date)
        except KeyError:
            log(f"Date {plot_date} not found in dataset. Plotting the first day instead.", "WARN")
            day_pr = ds_pr.isel(time=0)
            day_ta = ds_ta.isel(time=0)
            plot_date = str(day_pr.time.values)[:10]
            
        fig, axes = plt.subplots(1, 2, figsize=(15, 6))
        fig.suptitle(
            f"LISFLOOD Meteorological Forcing  |  {info.crs}\n"
            f"Showing data for specific date: {plot_date}",
            fontsize=14, fontweight="bold"
        )
        
        pr_data = day_pr['pr'].where(day_pr['pr'] != NODATA_VAL).values
        ta_data = day_ta['ta'].where(day_ta['ta'] != NODATA_VAL).values
        
        # Panel 1: Precipitation
        im1 = axes[0].imshow(pr_data, cmap="Blues", interpolation="nearest", vmin=0)
        plt.colorbar(im1, ax=axes[0], shrink=0.8)
        axes[0].set_title("Precipitation (mm/day)", fontweight="bold")
        axes[0].axis("off")
        
        # Panel 2: Temperature
        im2 = axes[1].imshow(ta_data, cmap="coolwarm", interpolation="nearest")
        plt.colorbar(im2, ax=axes[1], shrink=0.8)
        axes[1].set_title("Air Temperature (°C)", fontweight="bold")
        axes[1].axis("off")
        
        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "METEO_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        pass

def main():
    print("\n" + "=" * 65)
    print("  LISFLOOD METEOROLOGICAL FORCING GENERATOR")
    print("  Reference raster : " + AREA_TIF)
    print("=" * 65 + "\n")
    
    check_imports(["ee", "geemap", "rasterio", "xarray", "pandas"])
    
    info, mask = load_grid(AREA_TIF)
    
    pr_tifs, ta_tifs = fetch_gee_timeseries(info, START_DATE, END_DATE)
    
    ds_pr, ds_ta = process_and_save(pr_tifs, ta_tifs, info, mask)
    
    visualize(ds_pr, ds_ta, info)
    
    print("\n" + "=" * 65)
    print("  ★ DONE - Meteorological data perfectly aligned and stacked")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
