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
OUTPUT_DIR = _cfg.DIR_METEO
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
    raw_dir = _cfg.DIR_RAW
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
    maps_dir = OUTPUT_DIR
    make_dirs(maps_dir)
    
    dates = pd.date_range(start=START_DATE, end=END_DATE, freq='D')
    
    pr_nc = os.path.join(maps_dir, "pr.nc")
    ta_nc = os.path.join(maps_dir, "ta.nc")
    
    if os.path.exists(pr_nc) and os.path.exists(ta_nc):
        log("  Existing NetCDF files found. Loading them...")
        ds_pr = xr.open_dataset(pr_nc)
        ds_ta = xr.open_dataset(ta_nc)
    else:
        ds_pr = assemble_netcdf(pr_tifs, "pr", info, mask, dates, pr_nc)
        ds_ta = assemble_netcdf(ta_tifs, "ta", info, mask, dates, ta_nc)
    
    return ds_pr, ds_ta

def visualize(ds_pr, ds_ta, info):
    log("STEP 3 - Creating METEO_VISUAL_CHECK.png (Timeseries Graph)", "STEP")
    try:
        import matplotlib.pyplot as plt
        import pandas as pd
        
        # Calculate the spatial mean for each time step, ignoring NODATA
        pr_masked = ds_pr['pr'].where(ds_pr['pr'] != NODATA_VAL)
        ta_masked = ds_ta['ta'].where(ds_ta['ta'] != NODATA_VAL)
        
        # Calculate spatial mean over 'y' and 'x' dimensions
        mean_pr = pr_masked.mean(dim=['y', 'x'])
        mean_ta = ta_masked.mean(dim=['y', 'x'])
        
        # Extract times and values
        times = pd.to_datetime(mean_pr.time.values)
        vals_pr = mean_pr.values
        vals_ta = mean_ta.values
        
        fig, ax1 = plt.subplots(figsize=(12, 6))
        fig.suptitle(
            f"Basin-Average Meteorological Timeseries  |  {info.crs}",
            fontsize=14, fontweight="bold"
        )
        
        # Plot Temperature on left y-axis
        color_ta = 'tab:red'
        ax1.set_xlabel('Date', fontweight="bold")
        ax1.set_ylabel('Air Temperature (°C)', color=color_ta, fontweight="bold")
        ax1.plot(times, vals_ta, color=color_ta, linewidth=2, label='Mean Temperature')
        ax1.tick_params(axis='y', labelcolor=color_ta)
        ax1.grid(True, linestyle='--', alpha=0.7)
        
        # Plot Precipitation on right y-axis (inverted or normal, let's do normal bar chart)
        ax2 = ax1.twinx()
        color_pr = 'tab:blue'
        ax2.set_ylabel('Precipitation (mm/day)', color=color_pr, fontweight="bold")
        ax2.bar(times, vals_pr, color=color_pr, alpha=0.6, width=1.0, label='Precipitation')
        ax2.tick_params(axis='y', labelcolor=color_pr)
        
        # Add a combined legend
        lines_1, labels_1 = ax1.get_legend_handles_labels()
        lines_2, labels_2 = ax2.get_legend_handles_labels()
        ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper center', bbox_to_anchor=(0.5, -0.1), ncol=2)
        
        plt.tight_layout()
        out = os.path.join(_cfg.BASE_DIR, "METEO_VISUAL_CHECK.png")
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
