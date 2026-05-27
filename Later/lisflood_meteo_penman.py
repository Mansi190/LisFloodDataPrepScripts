"""
=============================================================================
LISFLOOD METEOROLOGICAL PREPROCESSING — NATIVE PENMAN-MONTEITH (SCRIPT 2/2)

This script BPASSES the need for LISVAP completely. 
It downloads meteorological variables and natively evaluates the exact FAO-56 
Penman-Monteith thermodynamics to directly generate the required LISFLOOD:
  • pr.nc : Precipitation
  • ta.nc : Temperature
  • et.nc : Reference ET0
  • e.nc  : Open Water E0
  • es.nc : Bare Soil ES0
=============================================================================
"""

import os
import sys
import ee
import math
import urllib.request
import zipfile
import numpy as np
import rasterio
import pandas as pd
import xarray as xr
from rasterio.crs import CRS

# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS — edit pipeline_config.py to change ROI / CRS / paths
# ─────────────────────────────────────────────────────────────────────────────
import pipeline_config as _cfg

START_DATE  = "2024-01-01"
END_DATE    = "2025-01-01"
AREA_RASTER = _cfg.AREA_TIF
OUTPUT_DIR  = _cfg.OUTPUT_METEO + "/penman"

# Basin latitude (°N) — auto-derived from ROI shapefile centroid.
# Used for extraterrestrial radiation Ra in the Penman-Monteith formula.
_lat, _lon = _cfg.resolve_centroid()
LATITUDE    = _lat if _lat != 0.0 else 26.0   # fallback: Araria district ~26°N

# Mean basin elevation (m) — auto-derived from dem.tif after topo step.
# Used for atmospheric pressure P = 101.3 * ((293 - 0.0065*z)/293)^5.26
ELEVATION   = _cfg.resolve_mean_elevation() or 60.0   # fallback: ~Bihar plains

print(f"  [meteo/penman] LATITUDE={LATITUDE:.3f}°  ELEVATION={ELEVATION:.1f} m")

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
        return {
            "width": src.width, "height": src.height,
            "crs": str(src.crs), "transform": src.transform,
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

def get_ee_var(var_name, master, expr=None, vdict=None):
    col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(START_DATE, END_DATE)
    def prod(img):
        return img.expression(expr, {k: img.select(v) for k,v in vdict.items()}).rename('x') if expr else img.select(var_name).rename('x')
    stack = col.map(prod).toBands()

    # master['bbox'] is in UTM metres — convert to WGS84 degrees for GEE.
    geom = ee.Geometry.Rectangle(_utm_bbox_to_wgs84(master))
    url = stack.getDownloadURL({'dimensions': f"{master['width']}x{master['height']}", 'crs': master['crs'], 'region': geom, 'format': 'GEO_TIFF'})
    
    zp = os.path.join(OUTPUT_DIR, f"temp_{var_name}.zip")
    tp = os.path.join(OUTPUT_DIR, f"temp_{var_name}.tif")
    
    urllib.request.urlretrieve(url, zp)
    with zipfile.ZipFile(zp, 'r') as z:
        z.extractall(OUTPUT_DIR)
        os.rename(os.path.join(OUTPUT_DIR, [f for f in z.namelist() if f.endswith('.tif')][0]), tp)
    os.remove(zp)
    
    with rasterio.open(tp) as src:
        from rasterio.warp import reproject, Resampling
        dst = np.full((src.count, master['height'], master['width']), np.nan, dtype=np.float32)
        reproject(
            source=src.read(), destination=dst,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=master['transform'], dst_crs=master['crs'],
            resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan
        )
    os.remove(tp)
    return dst

def write_netcdf(data, out_name, master, unit, description):
    print(f"    ✔ Writing NetCDF: {out_name}.nc")
    t, w, h = master['transform'], master['width'], master['height']
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    x, y = t * (cols, rows)
    times = pd.date_range(start=START_DATE, periods=data.shape[0], freq='D')
    
    for i in range(data.shape[0]):
        data[i][master['mask'] <= 0] = np.nan
        
    ds = xr.Dataset(
        {out_name: (["time", "y", "x"], data, {"units": unit, "long_name": description})},
        coords={
            "time": times,
            "x": (["x"], x[0, :], {"units": "m"}),
            "y": (["y"], y[:, 0], {"units": "m"})
        }
    )
    ds.to_netcdf(os.path.join(OUTPUT_DIR, f"{out_name}.nc"))

# ─────────────────────────────────────────────────────────────────────────────
# FAO56 PENMAN-MONTEITH NATIVE ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def compute_et0_e0_es0(tavg, tmax, tmin, srad_j, ws_10m, vpr_kpa, days):
    """
    Computes three evaporation outputs using FAO-56 Penman-Monteith / Penman:

      ET0 — FAO-56 Penman-Monteith for reference grass (albedo=0.23, rs=70 s/m)
      E0  — Penman open-water evaporation        (albedo=0.05, rs=0)
      ES0 — Penman bare-soil potential evaporation (albedo=0.30, rs=0)

    E0 and ES0 use the classic Penman (1948) formula with no stomatal resistance:
        E = [0.408·Δ·Rn + γ·0.26·(1 + u2/100)·(es − ea)] / (Δ + γ)
    Albedo changes Rns and therefore Rn, which is the primary driver of the
    difference between the three surfaces.
    """
    print("\n  ▶ Executing FAO-56 Penman-Monteith / Penman Physical Solver...")

    z   = ELEVATION
    phi = LATITUDE * math.pi / 180.0
    P   = 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26   # atmospheric pressure (kPa)
    gamma = 0.000665 * P                                  # psychrometric constant (kPa/°C)

    et0 = np.zeros_like(tavg)
    e0  = np.zeros_like(tavg)
    es0 = np.zeros_like(tavg)

    for i, J in enumerate(range(1, days + 1)):
        T  = tavg[i];  Tx = tmax[i];  Tn = tmin[i]
        ea = vpr_kpa[i]
        Rs = srad_j[i] * 1e-6      # J m⁻² → MJ m⁻²
        u2 = ws_10m[i] * 0.748     # 10 m → 2 m wind speed (FAO-56 Eq. 47)

        # Saturation vapour pressure and VPD
        es_Tx = 0.6108 * np.exp((17.27 * Tx) / (Tx + 237.3))
        es_Tn = 0.6108 * np.exp((17.27 * Tn) / (Tn + 237.3))
        es_   = (es_Tx + es_Tn) / 2.0
        vpd   = np.clip(es_ - ea, 0, None)

        # Slope of saturation vapour pressure curve (kPa/°C)
        delta = (4098 * (0.6108 * np.exp((17.27 * T) / (T + 237.3)))) / ((T + 237.3) ** 2)

        # Extraterrestrial radiation (MJ m⁻² day⁻¹)
        dr        = 1 + 0.033 * math.cos(2 * math.pi / 365 * J)
        delta_sun = 0.409 * math.sin((2 * math.pi / 365 * J) - 1.39)
        ws_angle  = math.acos(np.clip(-math.tan(phi) * math.tan(delta_sun), -1, 1))
        Ra = (24 * 60 / math.pi) * 0.0820 * dr * (
            ws_angle * math.sin(phi) * math.sin(delta_sun)
            + math.cos(phi) * math.cos(delta_sun) * math.sin(ws_angle)
        )

        # Net long-wave radiation (shared across all surfaces)
        Rso    = (0.75 + 2e-5 * z) * Ra
        Rs_Rso = np.clip(Rs / np.clip(Rso, 0.1, None), 0.3, 1.0)
        Rnl    = (4.903e-9) * (((Tx + 273.16) ** 4 + (Tn + 273.16) ** 4) / 2) * \
                 (0.34 - 0.14 * np.sqrt(np.clip(ea, 0.01, None))) * \
                 (1.35 * Rs_Rso - 0.35)

        # ── ET0: FAO-56 PM — reference grass (albedo=0.23, rs=70 s/m) ───────
        Rn_et0  = (1 - 0.23) * Rs - Rnl
        num_et0 = (0.408 * delta * Rn_et0) + (gamma * (900 / (T + 273)) * u2 * vpd)
        den_et0 = delta + gamma * (1 + 0.34 * u2)
        et0[i]  = np.clip(num_et0 / den_et0, 0, None)

        # ── E0: Penman open water (albedo=0.05, rs=0) ─────────────────────────
        # Open water absorbs more solar radiation (low albedo) → higher Rn than grass.
        Rn_e0  = (1 - 0.05) * Rs - Rnl
        num_e0 = (0.408 * delta * Rn_e0) + (gamma * 0.26 * (1 + u2 / 100) * vpd)
        den_e0 = delta + gamma
        e0[i]  = np.clip(num_e0 / den_e0, 0, None)

        # ── ES0: Penman bare soil (albedo=0.30, rs=0) ─────────────────────────
        # Bare soil reflects more solar radiation than grass → lower Rn → lower ES0.
        # Note: ES0 represents potential (supply-unlimited) bare-soil evaporation.
        # LISFLOOD scales it by actual soil moisture internally.
        Rn_es0  = (1 - 0.30) * Rs - Rnl
        num_es0 = (0.408 * delta * Rn_es0) + (gamma * 0.26 * (1 + u2 / 100) * vpd)
        den_es0 = delta + gamma
        es0[i]  = np.clip(num_es0 / den_es0, 0, None)

    return et0, e0, es0

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    init_ee()
    master = get_master_grid()
    print("\n═" * 65 + "\n  NATIVE PENMAN-MONTEITH ET CALCULATOR\n" + "═" * 65)
    
    # A. Direct LISFLOOD variables (pr, ta)
    pr = get_ee_var('pr', master, "p*1000", {'p': 'total_precipitation_sum'})
    write_netcdf(pr, 'pr', master, 'mm/day', 'Precipitation')
    
    ta = get_ee_var('ta', master, "t - 273.15", {'t': 'temperature_2m'})
    write_netcdf(ta, 'ta', master, 'Celcius', 'Mean Air Temperature')
    
    # B. Variables needed for ET0
    tmax = get_ee_var('tmax', master, "t - 273.15", {'t': 'temperature_2m_max'})
    tmin = get_ee_var('tmin', master, "t - 273.15", {'t': 'temperature_2m_min'})
    ws = get_ee_var('ws', master, "sqrt(u**2 + v**2)", {'u': 'u_component_of_wind_10m', 'v': 'v_component_of_wind_10m'})
    srad = get_ee_var('surface_solar_radiation_downwards_sum', master)
    vpr = get_ee_var('vpr', master, "0.6108 * exp((17.27 * (td - 273.15)) / ((td - 273.15) + 237.3))", {'td': 'dewpoint_temperature_2m'})
    
    # C. Calculate Physics — ET0 (grass), E0 (open water), ES0 (bare soil)
    et0, e0, es0 = compute_et0_e0_es0(ta, tmax, tmin, srad, ws, vpr, days=pr.shape[0])

    write_netcdf(et0, 'et', master, 'mm/day', 'Reference Evapotranspiration ET0 (FAO-56 PM, albedo=0.23)')
    write_netcdf(e0,  'e',  master, 'mm/day', 'Open Water Evaporation E0 (Penman, albedo=0.05)')
    write_netcdf(es0, 'es', master, 'mm/day', 'Bare Soil Evaporation ES0 (Penman, albedo=0.30)')

    print("\n  ★  All files instantly calculated and cleanly saved to NetCDF!\n")
