"""
=============================================================================
LISFLOOD METEOROLOGICAL PREPROCESSING — LISVAP RAW INPUTS (SCRIPT 1/2)

This script downloads 365 days of highly optimized meteorology data from 
ERA5-Land via Google Earth Engine and formats it perfectly for the LISVAP 
Penman-Monteith tool.

Outputs (NetCDF stacks aligned to area.tif):
  • pr.nc   : Precipitation (mm/day)
  • tavg.nc : Mean Air Temperature (°C)
  • tmax.nc : Max Air Temperature (°C)
  • tmin.nc : Min Air Temperature (°C)
  • ws.nc   : Wind Speed at 10m (m/s)
  • srad.nc : Downward Solar Radiation (J/m2/day)
  • vpr.nc  : Actual Vapor Pressure (kPa)
=============================================================================
"""

import os
import sys
import ee
import urllib.request
import zipfile
import numpy as np
import rasterio
import xarray as xr
import pandas as pd
from rasterio.crs import CRS

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS — edit pipeline_config.py to change ROI / CRS / paths
# ─────────────────────────────────────────────────────────────────────────────
import pipeline_config as _cfg

START_DATE  = "2024-01-01"
END_DATE    = "2025-01-01"        # Exclusive
AREA_RASTER = _cfg.AREA_TIF
OUTPUT_DIR  = _cfg.OUTPUT_METEO + "/lisvap_inputs"

# ─────────────────────────────────────────────────────────────────────────────
# CORE
# ─────────────────────────────────────────────────────────────────────────────

def init_ee():
    try:
        ee.Initialize(project=_cfg.GEE_PROJECT)
    except Exception as e:
        print(f"EE Auth failed: {e}")
        sys.exit(1)

def get_master_grid():
    with rasterio.open(AREA_RASTER) as src:
        t = src.transform
        return {
            "width": src.width, "height": src.height,
            "crs": str(src.crs), "transform": t,
            "bbox": src.bounds, "mask": src.read(1)
        }

def _utm_bbox_to_wgs84(master):
    """Convert master grid UTM bounding box to WGS84 degrees for GEE."""
    from pyproj import Transformer
    b = master['bbox']
    crs = master['crs']
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon_min, lat_min = tr.transform(b.left,  b.bottom)
    lon_max, lat_max = tr.transform(b.right, b.top)
    buf = 0.05
    return [lon_min - buf, lat_min - buf, lon_max + buf, lat_max + buf]

def download_ee_stack(variable, master, out_name, math_expr=None, expr_vars=None):
    print(f"  ▶ Downloading 365-day stack: {out_name} ...")

    col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(START_DATE, END_DATE)

    def process_day(img):
        # Apply mathematical scaling natively in the cloud to save local RAM
        if math_expr and expr_vars:
            v_dict = {k: img.select(v) for k,v in expr_vars.items()}
            val = img.expression(math_expr, v_dict).rename('val')
        else:
            val = img.select(variable).rename('val')
        # Mask to region to prevent downloading global data
        return val.updateMask(val.mask())

    # Convert TimeSeries collection into a single massive Multi-Band Image
    stack = col.map(process_day).toBands()

    # master['bbox'] is in UTM metres — convert to WGS84 degrees for GEE.
    geom = ee.Geometry.Rectangle(_utm_bbox_to_wgs84(master))

    url = stack.getDownloadURL({
        'dimensions': f"{master['width']}x{master['height']}",
        'crs': master['crs'],
        'region': geom,
        'format': 'GEO_TIFF'
    })
    
    zip_path = os.path.join(OUTPUT_DIR, f"{out_name}.zip")
    tif_path = os.path.join(OUTPUT_DIR, f"{out_name}.tif")
    
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(OUTPUT_DIR)
        extracted = [f for f in z.namelist() if f.endswith('.tif')][0]
        os.rename(os.path.join(OUTPUT_DIR, extracted), tif_path)
    os.remove(zip_path)
    
    # Reproject slightly to lock exact bounds (EE sometimes pads by 1 pixel)
    with rasterio.open(tif_path) as src:
        data = src.read()
        from rasterio.warp import reproject, Resampling
        dst = np.full((data.shape[0], master['height'], master['width']), np.nan, dtype=np.float32)
        reproject(
            source=data, destination=dst,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=master['transform'], dst_crs=master['crs'],
            resampling=Resampling.bilinear,
            src_nodata=np.nan, dst_nodata=np.nan
        )
    os.remove(tif_path)
    return dst

def write_netcdf(data, out_name, master, unit, long_name):
    print(f"    ✔ Converting to NetCDF CF-Compliant standard: {out_name}.nc")
    
    try:
        from pyproj import Transformer
        # Get lat/lon grids for CF standards
        t = master['transform']
        w, h = master['width'], master['height']
        cols, rows = np.meshgrid(np.arange(w), np.arange(h))
        x, y = t * (cols, rows)
        
        times = pd.date_range(start=START_DATE, end=END_DATE, inclusive="left")
        if data.shape[0] != len(times):
            times = pd.date_range(start=START_DATE, periods=data.shape[0], freq='D')

        # Convert mask
        mask = master['mask']
        for i in range(data.shape[0]):
            data[i][mask <= 0] = np.nan
            
        ds = xr.Dataset(
            {
                out_name: (["time", "y", "x"], data, {"units": unit, "long_name": long_name})
            },
            coords={
                "time": times,
                "x": (["x"], x[0, :], {"units": "m", "standard_name": "projection_x_coordinate"}),
                "y": (["y"], y[:, 0], {"units": "m", "standard_name": "projection_y_coordinate"})
            }
        )
        ds.to_netcdf(os.path.join(OUTPUT_DIR, f"{out_name}.nc"))
    except Exception as e:
        print(f"NetCDF failed: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    init_ee()
    master = get_master_grid()
    
    print("\n═" * 65 + "\n  LISVAP RAW INPUT GENERATOR (ERA5 > NETCDF)\n" + "═" * 65)
    
    # 1. Precipitation (m -> mm)
    pr = download_ee_stack('total_precipitation_sum', master, 'pr', "p * 1000", {'p': 'total_precipitation_sum'})
    write_netcdf(pr, 'pr', master, 'mm/day', 'Precipitation')

    # 2. Daily Tavg (K -> C)
    ta = download_ee_stack('temperature_2m', master, 'tavg', "t - 273.15", {'t': 'temperature_2m'})
    write_netcdf(ta, 'tavg', master, 'Celcius', 'Mean Air Temperature')

    # 3. Daily Tmax (K -> C)
    tmax = download_ee_stack('temperature_2m_max', master, 'tmax', "t - 273.15", {'t': 'temperature_2m_max'})
    write_netcdf(tmax, 'tmax', master, 'Celcius', 'Max Air Temperature')

    # 4. Daily Tmin (K -> C)
    tmin = download_ee_stack('temperature_2m_min', master, 'tmin', "t - 273.15", {'t': 'temperature_2m_min'})
    write_netcdf(tmin, 'tmin', master, 'Celcius', 'Min Air Temperature')

    # 5. Solar Radiation (J/m2 remains J/m2 for LISVAP)
    srad = download_ee_stack('surface_solar_radiation_downwards_sum', master, 'srad')
    write_netcdf(srad, 'srad', master, 'J m-2 day-1', 'Downward Solar Radiation')

    # 6. Wind Speed at 10m (magnitude from u and v)
    ws = download_ee_stack('ws', master, 'ws', "sqrt(u**2 + v**2)", {'u': 'u_component_of_wind_10m', 'v': 'v_component_of_wind_10m'})
    write_netcdf(ws, 'ws', master, 'm/s', 'Wind Speed 10m')

    # 7. Actual Vapor Pressure (kPa) from Dewpoint.
    # Formula: e_act = 0.6108 * exp((17.27 * T_dew) / (T_dew + 237.3))  where T_dew is in Celsius
    # ERA5 dewpoint is in K
    vpr_expr = "0.6108 * exp((17.27 * (td - 273.15)) / ((td - 273.15) + 237.3))"
    vpr = download_ee_stack('vpr', master, 'vpr', vpr_expr, {'td': 'dewpoint_temperature_2m'})
    write_netcdf(vpr, 'vpr', master, 'kPa', 'Actual Vapor Pressure')

    print("\n  ★  All LISVAP inputs strictly generated into NetCDF!\n")
