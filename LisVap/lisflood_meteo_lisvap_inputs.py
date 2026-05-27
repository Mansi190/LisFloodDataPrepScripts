import os
import sys
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import xarray as xr
import rasterio

# Add parent directory to path to import pipeline_config and lisflood_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            load_grid, snap_to_grid, init_ee)

# =============================================================================
#  CONFIGURATION
# =============================================================================
START_DATE = "2024-01-01"
END_DATE   = "2024-12-31"

AREA_TIF   = _cfg.AREA_TIF
# Output goes inside a lisvap directory
OUTPUT_DIR = os.path.join(_cfg.OUTPUT_METEO, "lisvap_input")
NODATA_VAL = _cfg.NODATA_FLOAT

# =============================================================================

def fetch_gee_timeseries(info, start_date, end_date):
    log(f"STEP 1 - Fetching LisVap meteo data from GEE ({start_date} to {end_date})", "STEP")
    
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

    col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR") \
            .filterBounds(region) \
            .filterDate(start_date, pd.to_datetime(end_date) + pd.Timedelta(days=1))

    def get_upscaled(img, band):
        return band.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                   .reproject(crs=str(info.crs), scale=_cfg.RESOLUTION_M) \
                   .rename([img.date().format('YYYYMMdd')]) \
                   .set('system:time_start', img.get('system:time_start'))

    def process_tn(img):
        tn = img.select('temperature_2m_min').subtract(273.15)
        return get_upscaled(img, tn)

    def process_tx(img):
        tx = img.select('temperature_2m_max').subtract(273.15)
        return get_upscaled(img, tx)

    def process_rg(img):
        rg = img.select('surface_solar_radiation_downwards_sum')
        return get_upscaled(img, rg)

    def process_ws(img):
        u = img.select('u_component_of_wind_10m')
        v = img.select('v_component_of_wind_10m')
        ws = u.pow(2).add(v.pow(2)).sqrt()
        return get_upscaled(img, ws)

    def process_pd(img):
        # pd (mbar) = 6.11 * exp( (17.27 * td) / (td + 237.3) )
        td = img.select('dewpoint_temperature_2m').subtract(273.15)
        num = td.multiply(17.27)
        den = td.add(237.3)
        pd_band = num.divide(den).exp().multiply(6.11)
        return get_upscaled(img, pd_band)

    log("  Preparing TN (Minimum Temperature)...")
    tn_img = col.map(process_tn).toBands()

    log("  Preparing TX (Maximum Temperature)...")
    tx_img = col.map(process_tx).toBands()

    log("  Preparing RG (Solar Radiation)...")
    rg_img = col.map(process_rg).toBands()

    log("  Preparing WS (Wind Speed)...")
    ws_img = col.map(process_ws).toBands()

    log("  Preparing PD (Actual Vapour Pressure)...")
    pd_img = col.map(process_pd).toBands()

    # --- DOWNLOAD ---
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)
    
    downloads = {
        "tn": tn_img,
        "tx": tx_img,
        "rg": rg_img,
        "ws": ws_img,
        "pd": pd_img
    }
    
    tif_paths = {}
    
    for var_name, img in downloads.items():
        tif_path = os.path.join(raw_dir, f"{var_name}_raw.tif")
        tif_paths[var_name] = tif_path
        if not os.path.exists(tif_path):
            log(f"  Downloading {var_name.upper()} to {tif_path}...")
            geemap.ee_export_image(img, filename=tif_path, scale=_cfg.RESOLUTION_M, crs=str(info.crs), region=region, file_per_band=False)
        else:
            log(f"  Raw {var_name.upper()} data already exists: {tif_path}")

    return tif_paths

def assemble_netcdf(tif_path, var_name, info, mask, dates, nc_path):
    log(f"  Processing NetCDF for {var_name}...")
    
    x_coords = [info.transform.c + (i + 0.5) * info.transform.a for i in range(info.width)]
    y_coords = [info.transform.f + (i + 0.5) * info.transform.e for i in range(info.height)]
    
    with rasterio.open(tif_path) as src:
        raw_data = src.read()
        src_nd = src.nodata if src.nodata is not None else -9999
        
    num_days = len(dates)
    if raw_data.shape[0] < num_days:
        log(f"Warning: Fetched {raw_data.shape[0]} days from GEE, expected {num_days}.", "WARN")
        num_days = raw_data.shape[0]
        dates = dates[:num_days]
        
    cube = np.full((num_days, info.height, info.width), NODATA_VAL, dtype=np.float32)
    
    for i in range(num_days):
        band_data = raw_data[i].astype(np.float32)
        band_data[band_data == src_nd] = np.nan
        
        aligned = snap_to_grid(band_data, AREA_TIF, np.nan)
        final = np.where((mask > 0), aligned, NODATA_VAL)
        final[np.isnan(final)] = NODATA_VAL
        cube[i, :, :] = final

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
            "description": f"LISVAP Meteorological Input: {var_name}",
            "crs": str(info.crs),
            "source": "GEE (ERA5-Land)"
        }
    )
    
    unit_map = {
        "tn": "degree_celsius",
        "tx": "degree_celsius",
        "ws": "m/s",
        "pd": "hpa",
        "rg": "j/m2"
    }
    ds[var_name].attrs["units"] = unit_map.get(var_name, "")
    
    ds.to_netcdf(nc_path, encoding={var_name: {"_FillValue": NODATA_VAL, "zlib": True, "complevel": 4}})
    log(f"  ✔ Saved {nc_path}")
    return ds

def process_and_save(tif_paths, info, mask):
    log("STEP 2 - Assembling NetCDF time-series", "STEP")
    maps_dir = os.path.join(OUTPUT_DIR, "maps")
    make_dirs(maps_dir)
    
    dates = pd.date_range(start=START_DATE, end=END_DATE, freq='D')
    
    ds_dict = {}
    for var_name, tif_path in tif_paths.items():
        nc_path = os.path.join(maps_dir, f"{var_name}.nc")
        ds_dict[var_name] = assemble_netcdf(tif_path, var_name, info, mask, dates, nc_path)
    
    return ds_dict

def main():
    print("\n" + "=" * 65)
    print("  LISVAP METEOROLOGICAL INPUTS GENERATOR")
    print("  Reference raster : " + AREA_TIF)
    print("=" * 65 + "\n")
    
    check_imports(["ee", "geemap", "rasterio", "xarray", "pandas"])
    
    info, mask = load_grid(AREA_TIF)
    
    tif_paths = fetch_gee_timeseries(info, START_DATE, END_DATE)
    
    ds_dict = process_and_save(tif_paths, info, mask)
    
    print("\n" + "=" * 65)
    print("  ★ DONE - LISVAP Inputs perfectly aligned and stacked")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
