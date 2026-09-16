import os
import sys
import math
import time
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
                            load_grid, reproject_to_grid, init_ee)

# =============================================================================
#  CONFIGURATION
# =============================================================================
# Dates come from pipeline_config so all forcing scripts stay in lockstep; one
# continuous span covers prerun + cold + warm runs, which the settings XMLs slice.
START_DATE = _cfg.FORCING_START
END_DATE   = _cfg.FORCING_END

AREA_TIF   = _cfg.AREA_TIF
# Output goes inside a lisvap directory
OUTPUT_DIR = _cfg.DIR_METEO
NODATA_VAL = _cfg.NODATA_FLOAT

# ── Download strategy ────────────────────────────────────────────────────────
# ERA5-Land is a 0.1 degree (~11 km) product; the model grid is 300 m. The old
# code asked Earth Engine to reduceResolution+reproject to 300 m server-side,
# which shipped ~1285 identical copies of every real value: 2.22 MB per band
# instead of ~3 KB, i.e. ~3655 export requests instead of ~110 for the same
# information. Two problems with that, beyond the wall-clock cost:
#   * reduceResolution averages FINE pixels into COARSE ones. Here the source
#     (11 km) is already coarser than the target (300 m), so there is nothing to
#     average -- the reducer is a no-op and .reproject() does a nearest-neighbour
#     blow-up, stamping blocky 11 km squares onto the fine grid.
#   * every byte of that redundancy crosses the network and hits the disk.
# Now: export at the collection's NATIVE grid (crs + crs_transform read off the
# asset, so EE resamples nothing at all), and resample once, locally, in
# assemble_netcdf via lisflood_utils.reproject_to_grid.
# 'nearest' copies each 11 km value to the 300 m pixels under it (a staircase);
# 'bilinear' distance-weights the 4 surrounding cell centres (a ramp). Measured on
# 2003 tx over this basin the two differ by <0.08 C -- ERA5 fields are smooth at
# 11 km, so there is little to interpolate -- but the staircase compresses ~9x
# better (1.8 GB vs 16.2 GB for the 5 variables over 22 years) and matches how the
# pr.nc / ta.nc already on disk were built. Not worth 14 GB and an inconsistent
# forcing set to buy a difference smaller than ERA5's own error bar.
RESAMPLING = "nearest"
# Native pixels of margin kept around the model grid so the interpolator always
# has source cells on all sides of an edge target pixel. 3 * 0.1deg ~= 33 km.
EDGE_BUFFER_PIXELS = 3
# ImageCollection.toBands() caps at 5000 bands, so the full 22-year daily
# collection (8036 images) cannot be collapsed in one call -- and the
# bandNames().getInfo() round-trip on a graph that size blocks for minutes
# before a single byte downloads. Bind one year at a time, as
# lisflood_meteo_forcing.py already does for pr/ta.
MAX_BANDS_PER_REQUEST = 366

ERA5_COLLECTION = "ECMWF/ERA5_LAND/DAILY_AGGR"
# Band used to probe the collection's native projection.
ERA5_PROBE_BAND = "temperature_2m_min"

# =============================================================================


def _native_grid(col):
    """Read the collection's own crs + affine transform.

    Passing these straight to ee_export_image (crs + crs_transform, and NO
    scale) means the export lands on the asset's native pixels: Earth Engine
    performs no resampling whatsoever, so what arrives is exactly the numbers
    ERA5-Land holds. For ERA5-Land this returns
    ('EPSG:4326', [0.1, 0, -180.05, 0, -0.1, 90.05]).
    """
    proj = col.first().select(ERA5_PROBE_BAND).projection().getInfo()
    return proj["crs"], list(proj["transform"])


def _download_region(info, native_transform, ee):
    """Lat/lon rectangle covering the model grid plus a margin of native pixels.

    The model grid is in UTM metres; the export is in the asset's geographic
    CRS, so the corners are transformed and the bbox padded. The padding is not
    cosmetic: without source cells beyond the edge, bilinear resampling has
    nothing to interpolate from and the outermost model pixels come back empty.
    """
    from pyproj import Transformer

    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    tr = Transformer.from_crs(str(info.crs), "EPSG:4326", always_xy=True)
    lons, lats = tr.transform([xmin, xmax, xmin, xmax], [ymin, ymin, ymax, ymax])

    px, py = abs(native_transform[0]), abs(native_transform[4])
    buf_x, buf_y = EDGE_BUFFER_PIXELS * px, EDGE_BUFFER_PIXELS * py

    lon_min, lon_max = min(lons) - buf_x, max(lons) + buf_x
    lat_min, lat_max = min(lats) - buf_y, max(lats) + buf_y

    region = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max],
                                   proj="EPSG:4326", geodesic=False)

    # Estimated native pixel count, used only to size the request chunks.
    nx = int(math.ceil((lon_max - lon_min) / px)) + 1
    ny = int(math.ceil((lat_max - lat_min) / py)) + 1
    return region, nx, ny


def fetch_gee_timeseries(info, start_date, end_date):
    log(f"STEP 1 - Fetching LisVap meteo data from GEE ({start_date} to {end_date})", "STEP")

    try:
        import ee
        import geemap
    except ImportError:
        log("Missing: earthengine-api geemap", "ERROR")
        sys.exit(1)

    init_ee(_cfg.GEE_PROJECT)

    probe = ee.ImageCollection(ERA5_COLLECTION).filterDate(start_date, end_date)
    native_crs, native_transform = _native_grid(probe)
    region, nx, ny = _download_region(info, native_transform, ee)

    log(f"  Native grid : {native_crs} | transform {native_transform}")
    log(f"  Export window: {nx}x{ny} native px "
        f"(model grid is {info.width}x{info.height} @ {_cfg.RESOLUTION_M} m; "
        f"resampled locally with '{RESAMPLING}')")

    # Bands are cast to float32 below, so 4 bytes/px. getDownloadURL caps one
    # request at ~48 MB; stay well under it. At native resolution a band is a
    # few KB, so this is normally capped by MAX_BANDS_PER_REQUEST (one year)
    # rather than by size -- which is the whole point of the change.
    mb_per_band = nx * ny * 4 / 1e6
    chunk_size = max(1, min(MAX_BANDS_PER_REQUEST, int(25 / mb_per_band)))
    log(f"  {mb_per_band*1000:.1f} KB/band -> {chunk_size} bands/request")

    def as_band(img, computed):
        # Cast to float32: ERA5-Land bands are double at source, so an uncast
        # request is 8 bytes/px and doubles every transfer for no added precision.
        return (computed.toFloat()
                .rename([img.date().format('YYYYMMdd')])
                .set('system:time_start', img.get('system:time_start')))

    def process_tn(img):
        return as_band(img, img.select('temperature_2m_min').subtract(273.15))

    def process_tx(img):
        return as_band(img, img.select('temperature_2m_max').subtract(273.15))

    def process_rg(img):
        return as_band(img, img.select('surface_solar_radiation_downwards_sum'))

    def process_ws(img):
        u = img.select('u_component_of_wind_10m')
        v = img.select('v_component_of_wind_10m')
        return as_band(img, u.pow(2).add(v.pow(2)).sqrt())

    def process_pd(img):
        # pd (mbar) = 6.11 * exp( (17.27 * td) / (td + 237.3) )
        td = img.select('dewpoint_temperature_2m').subtract(273.15)
        pd_band = td.multiply(17.27).divide(td.add(237.3)).exp().multiply(6.11)
        return as_band(img, pd_band)

    builders = {"tn": process_tn, "tx": process_tx, "rg": process_rg,
                "ws": process_ws, "pd": process_pd}

    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)

    years = pd.date_range(start=start_date, end=end_date, freq="YS").year.tolist()
    if not years or years[0] > pd.to_datetime(start_date).year:
        years = [pd.to_datetime(start_date).year] + years

    def export(chunk_img, path):
        # geemap SWALLOWS export failures -- it prints, returns normally, and
        # leaves no file -- so "did the file appear" is the only reliable signal.
        # At this request volume transient failures are expected, not exceptional.
        for attempt in range(4):
            geemap.ee_export_image(chunk_img, filename=path,
                                   crs=native_crs, crs_transform=native_transform,
                                   region=region, file_per_band=False)
            if os.path.exists(path):
                return
            if attempt < 3:
                wait = 5 * (3 ** attempt)      # 5s, 15s, 45s
                log(f"    {os.path.basename(path)} failed; retry "
                    f"{attempt + 1}/3 in {wait}s", "WARN")
                time.sleep(wait)
        raise RuntimeError(f"export produced no file after 4 tries: {path}")

    def block_tifs(var_name, build):
        """Download one variable across all year blocks; return tif paths in order."""
        out = []
        for yr in years:
            y0 = max(pd.to_datetime(start_date), pd.Timestamp(f"{yr}-01-01"))
            y1 = min(pd.to_datetime(end_date), pd.Timestamp(f"{yr}-12-31"))
            if y0 > y1:
                continue
            col = (ee.ImageCollection(ERA5_COLLECTION)
                     .filterBounds(region)
                     .filterDate(y0.strftime("%Y-%m-%d"),
                                 (y1 + pd.Timedelta(days=1)).strftime("%Y-%m-%d")))
            img = col.map(build).toBands()

            names = img.bandNames().getInfo()
            log(f"  {var_name} {yr}: {len(names)} day(s)")
            for i in range(0, len(names), chunk_size):
                f = os.path.join(raw_dir, f"{var_name}_raw_{yr}_{i}.tif")
                if not os.path.exists(f):
                    export(img.select(names[i:i + chunk_size]), f)
                out.append(f)
        return out

    tif_paths_dict = {}
    for var_name, build in builders.items():
        log(f"  Downloading {var_name.upper()} (native resolution, year blocks)...")
        tif_paths_dict[var_name] = block_tifs(var_name, build)

    return tif_paths_dict


def assemble_netcdf(tif_paths, var_name, info, mask, dates, nc_path):
    """Reproject each native-resolution band onto the model grid, streaming to netCDF.

    WAS: every chunk read into a list, np.concatenate'd into one (8036, 865, 642)
    float32 array (17.9 GB), then a second array of the same size for `cube` --
    35.7 GB per variable, on a 16 GB machine. The full-array version is kept in
    lisflood_meteo_lisvap_inputs.py.bak.

    NOW: the file is created up front with its full time dimension and each band is
    written as it is read, so peak memory is one chunk plus one band.

    The bands arriving here are on ERA5-Land's ~11 km lat/lon grid, not the model
    grid, because the export no longer resamples server-side. reproject_to_grid
    does that step here -- one interpolation, on values that were never inflated
    in transit. It returns an array already matching AREA_TIF's dimensions, so
    the old snap_to_grid (crop/pad only) is no longer needed or correct.
    """
    import netCDF4
    log(f"  Processing NetCDF for {var_name}...")

    x_coords = np.array([info.transform.c + (i + 0.5) * info.transform.a
                         for i in range(info.width)], dtype=np.float64)
    y_coords = np.array([info.transform.f + (i + 0.5) * info.transform.e
                         for i in range(info.height)], dtype=np.float64)

    unit_map = {"tn": "degree_celsius", "tx": "degree_celsius",
                "ws": "m/s", "pd": "hpa", "rg": "j/m2"}
    num_days = len(dates)

    if os.path.exists(nc_path):
        os.remove(nc_path)
    ds = netCDF4.Dataset(nc_path, "w", format="NETCDF4")
    ds.createDimension("time", num_days)
    ds.createDimension("y", info.height)
    ds.createDimension("x", info.width)

    v_t = ds.createVariable("time", "f8", ("time",))
    v_t.units = "days since 1970-01-01"
    v_t.calendar = "proleptic_gregorian"
    v_t[:] = netCDF4.date2num(dates.to_pydatetime(), v_t.units, v_t.calendar)
    ds.createVariable("y", "f8", ("y",))[:] = y_coords
    ds.createVariable("x", "f8", ("x",))[:] = x_coords

    v = ds.createVariable(var_name, "f4", ("time", "y", "x"),
                          zlib=True, complevel=4, fill_value=NODATA_VAL,
                          chunksizes=(1, info.height, info.width))
    v.units = unit_map.get(var_name, "")
    ds.description = f"LISVAP Meteorological Input: {var_name}"
    ds.crs = str(info.crs)
    ds.source = f"GEE ({ERA5_COLLECTION}), native grid, resampled '{RESAMPLING}'"

    b = 0
    for tif_path in tif_paths:
        with rasterio.open(tif_path) as src:
            chunk = src.read()
            src_t = src.transform
            src_crs = src.crs
            src_nd = src.nodata
        for k in range(chunk.shape[0]):
            if b >= num_days:
                break
            band_data = chunk[k].astype(np.float32)
            # ERA5-Land masks water bodies; geemap writes those cells as -inf
            # (the GeoTIFF's declared nodata). Test for non-finite rather than
            # equality with src.nodata: that also catches +/-inf and NaN if the
            # export ever labels them differently, and reproject_to_grid is then
            # told src_nodata=NaN so the interpolator excludes them instead of
            # smearing an infinity across neighbouring cells.
            band_data[~np.isfinite(band_data)] = np.nan
            aligned = reproject_to_grid(band_data, src_t, src_crs, AREA_TIF,
                                        np.nan, np.nan, RESAMPLING)
            final = np.where(mask > 0, aligned, NODATA_VAL)
            final[np.isnan(final)] = NODATA_VAL
            v[b, :, :] = final
            b += 1
        del chunk
    ds.close()

    if b < num_days:
        log(f"Warning: wrote {b} days, expected {num_days}.", "WARN")
    log(f"  ✔ Saved {nc_path}  ({b} steps)")
    return nc_path


def process_and_save(tif_paths, info, mask):
    log("STEP 2 - Assembling NetCDF time-series", "STEP")
    maps_dir = OUTPUT_DIR
    make_dirs(maps_dir)

    dates = pd.date_range(start=START_DATE, end=END_DATE, freq='D')

    ds_dict = {}   # paths, not in-memory datasets
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
