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
                            load_grid, reproject_to_grid, init_ee,
                            native_grid, native_region)

# =============================================================================
#  CONFIGURATION
# =============================================================================
# Dates come from pipeline_config so all forcing scripts stay in lockstep; one
# continuous span covers prerun + cold + warm runs, which the settings XMLs slice.
START_DATE = _cfg.FORCING_START
END_DATE   = _cfg.FORCING_END

AREA_TIF   = _cfg.AREA_TIF
OUTPUT_DIR = _cfg.DIR_METEO
NODATA_VAL = _cfg.NODATA_FLOAT

# Local resampling from the source archive's native grid onto the 300 m model
# grid. 'nearest' copies each source value to the model pixels under it; matches
# what the old server-side reproject produced, and compresses ~9x better than
# 'bilinear' because the result is a staircase rather than a ramp. See
# lisflood_utils.native_grid for why the export is no longer done at 300 m.
RESAMPLING = "nearest"
EDGE_BUFFER_PIXELS = 3
MAX_BANDS_PER_REQUEST = 366   # one year; ImageCollection.toBands() caps at 5000

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

    raw_dir = _cfg.DIR_RAW
    make_dirs(raw_dir)

    years = pd.date_range(start=start_date, end=end_date, freq="YS").year.tolist()
    if not years or years[0] > pd.to_datetime(start_date).year:
        years = [pd.to_datetime(start_date).year] + years

    def block_tifs(collection_id, band, prefix, offset=0.0, scale_factor=1.0):
        """Download one variable across all year blocks; return the tif paths in order.

        Each collection is exported on ITS OWN grid, so CHIRPS (0.05 deg) and
        ERA5-Land (0.1 deg) get different export windows and chunk sizes. The
        alternative -- forcing both to 300 m server-side -- is what made this
        step ~1500 requests instead of ~44.
        """
        probe = ee.ImageCollection(collection_id).filterDate(start_date, end_date)
        crs_n, transform_n = native_grid(probe, band)
        region, nx, ny = native_region(info, transform_n, ee, EDGE_BUFFER_PIXELS)

        mb_per_band = nx * ny * 4 / 1e6
        chunk_size = max(1, min(MAX_BANDS_PER_REQUEST, int(25 / mb_per_band)))
        log(f"  {prefix}: native {crs_n} px {transform_n[0]} deg -> window {nx}x{ny}, "
            f"{mb_per_band*1000:.1f} KB/band, {chunk_size} bands/request")

        def process_image(img):
            val = img.select(band).multiply(scale_factor).add(offset)
            # Cast to float32: ERA5-Land bands are double at source, so an uncast
            # request is 8 bytes/px for no added precision. assemble_netcdf stores
            # float32 regardless.
            return (val.toFloat().rename([img.date().format('YYYYMMdd')])
                    .set('system:time_start', img.get('system:time_start')))

        out = []
        for yr in years:
            y0 = max(pd.to_datetime(start_date), pd.Timestamp(f"{yr}-01-01"))
            y1 = min(pd.to_datetime(end_date), pd.Timestamp(f"{yr}-12-31"))
            if y0 > y1:
                continue
            col = (ee.ImageCollection(collection_id)
                     .filterBounds(region)
                     .filterDate(y0.strftime("%Y-%m-%d"),
                                 (y1 + pd.Timedelta(days=1)).strftime("%Y-%m-%d")))
            img = col.map(process_image).toBands()

            names = img.bandNames().getInfo()
            log(f"  {prefix} {yr}: {len(names)} day(s)")
            for i in range(0, len(names), chunk_size):
                # "_native_" so these never collide with the pre-existing
                # {prefix}_raw_{yr}_{i}.tif files from the 300 m-export era.
                f = os.path.join(raw_dir, f"{prefix}_native_{yr}_{i}.tif")
                if not os.path.exists(f):
                    # geemap swallows export errors -- it returns normally leaving no
                    # file -- so "did the file appear" is the only reliable signal.
                    for attempt in range(4):
                        geemap.ee_export_image(img.select(names[i:i + chunk_size]),
                                               filename=f, crs=crs_n,
                                               crs_transform=transform_n,
                                               region=region, file_per_band=False)
                        if os.path.exists(f):
                            break
                        if attempt < 3:
                            wait = 5 * (3 ** attempt)      # 5s, 15s, 45s
                            log(f"    {os.path.basename(f)} failed; retry "
                                f"{attempt + 1}/3 in {wait}s", "WARN")
                            time.sleep(wait)
                    else:
                        raise RuntimeError(f"export produced no file after 4 tries: {f}")
                out.append(f)
        return out

    log("  Preparing CHIRPS Precipitation (pr)...")
    pr_tifs = block_tifs("UCSB-CHG/CHIRPS/DAILY", "precipitation", "pr")

    log("  Preparing ERA5-Land Mean Air Temperature (ta)...")
    ta_tifs = block_tifs("ECMWF/ERA5_LAND/DAILY_AGGR", "temperature_2m", "ta",
                         offset=-273.15)

    return pr_tifs, ta_tifs

def assemble_netcdf(tif_paths, var_name, info, mask, dates, nc_path):
    """Stream the downloaded tifs into a NetCDF, one chunk at a time.

    The previous version read every tif into a list, concatenated it, and then
    allocated a second full-size cube -- for a 22-year daily run on this grid that is
    8036 x 865 x 642 float32 = 17.9 GB, twice over, on a 17 GB machine. The process was
    SIGKILLed by the OOM killer after every download had already succeeded.

    Here the NetCDF is created empty with its full time dimension, and each band is
    written to its own time slice as it is read. Peak memory is one chunk (~24 MB),
    independent of run length, so the period can grow without the assembly changing.
    """
    import netCDF4

    log(f"  Processing NetCDF for {var_name} (streaming)...")

    x_coords = [info.transform.c + (i + 0.5) * info.transform.a for i in range(info.width)]
    y_coords = [info.transform.f + (i + 0.5) * info.transform.e for i in range(info.height)]

    # count bands without reading pixels, so we can size the time dimension up front
    total_bands = 0
    for tif_path in tif_paths:
        with rasterio.open(tif_path) as src:
            total_bands += src.count

    num_days = len(dates)
    if total_bands < num_days:
        log(f"Warning: Fetched {total_bands} days from GEE, expected {num_days}.", "WARN")
        num_days = total_bands
    dates = dates[:num_days]

    if os.path.exists(nc_path):
        os.remove(nc_path)

    with netCDF4.Dataset(nc_path, "w", format="NETCDF4") as nc:
        nc.createDimension("time", num_days)
        nc.createDimension("y", info.height)
        nc.createDimension("x", info.width)

        tv = nc.createVariable("time", "f8", ("time",))
        tv.units = "days since 1970-01-01 00:00:00"
        tv.calendar = "standard"
        tv[:] = netCDF4.date2num(dates.to_pydatetime(), tv.units, tv.calendar)

        yv = nc.createVariable("y", "f8", ("y",)); yv[:] = y_coords
        xv = nc.createVariable("x", "f8", ("x",)); xv[:] = x_coords

        v = nc.createVariable(var_name, "f4", ("time", "y", "x"),
                              zlib=True, complevel=4, fill_value=NODATA_VAL,
                              chunksizes=(1, info.height, info.width))
        nc.description = f"LISFLOOD Meteorological Forcing: {var_name}"
        nc.crs = str(info.crs)
        nc.source = f"GEE (CHIRPS / ERA5-Land), native grid, resampled '{RESAMPLING}'"

        t = 0
        for n, tif_path in enumerate(tif_paths, start=1):
            if t >= num_days:
                break
            with rasterio.open(tif_path) as src:
                arr = src.read()
                src_t, src_crs = src.transform, src.crs

            for b in range(arr.shape[0]):
                if t >= num_days:
                    break
                band_data = arr[b].astype(np.float32)
                # CHIRPS/ERA5-Land mask water; geemap writes those cells as -inf.
                # Test for non-finite rather than equality with src.nodata: that
                # also catches +/-inf and NaN if an export ever labels them
                # differently, and reproject_to_grid is then told src_nodata=NaN
                # so the interpolator drops them instead of propagating an infinity.
                band_data[~np.isfinite(band_data)] = np.nan

                # Bands arrive on the source archive's native grid, not the model
                # grid, because the export no longer resamples server-side. This is
                # the single resampling step; it returns an array already matching
                # AREA_TIF, so the old snap_to_grid (crop/pad only) is not used.
                aligned = reproject_to_grid(band_data, src_t, src_crs, AREA_TIF,
                                            np.nan, np.nan, RESAMPLING)
                final = np.where((mask > 0), aligned, NODATA_VAL)
                final[np.isnan(final)] = NODATA_VAL
                v[t, :, :] = final
                t += 1

            del arr
            if n % 50 == 0 or n == len(tif_paths):
                log(f"    {var_name}: {t}/{num_days} days written "
                    f"({n}/{len(tif_paths)} files)")

    log(f"  Saved {nc_path}")
    return xr.open_dataset(nc_path)

def process_and_save(pr_tifs, ta_tifs, info, mask):
    log("STEP 2 - Assembling NetCDF time-series", "STEP")
    maps_dir = OUTPUT_DIR
    make_dirs(maps_dir)
    
    dates = pd.date_range(start=START_DATE, end=END_DATE, freq='D')
    
    pr_nc = os.path.join(maps_dir, "pr.nc")
    ta_nc = os.path.join(maps_dir, "ta.nc")
    
    if not REBUILD and os.path.exists(pr_nc) and os.path.exists(ta_nc):
        log("  Existing NetCDF files found. Loading them...")
        ds_pr = xr.open_dataset(pr_nc)
        ds_ta = xr.open_dataset(ta_nc)
    else:
        ds_pr = assemble_netcdf(pr_tifs, "pr", info, mask, dates, pr_nc)
        ds_ta = assemble_netcdf(ta_tifs, "ta", info, mask, dates, ta_nc)
    
    return ds_pr, ds_ta


REBUILD = "--rebuild" in sys.argv   # ignore existing pr.nc/ta.nc and regenerate


def main():
    print("\n" + "=" * 65)
    print("  LISFLOOD METEOROLOGICAL FORCING GENERATOR")
    print("  Reference raster : " + AREA_TIF)
    print("=" * 65 + "\n")
    
    check_imports(["ee", "geemap", "rasterio", "xarray", "pandas"])
    
    info, mask = load_grid(AREA_TIF)
    
    pr_tifs, ta_tifs = fetch_gee_timeseries(info, START_DATE, END_DATE)
    
    ds_pr, ds_ta = process_and_save(pr_tifs, ta_tifs, info, mask)
    
    
    print("\n" + "=" * 65)
    print("  ★ DONE - Meteorological data perfectly aligned and stacked")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
