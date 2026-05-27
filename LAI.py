"""
=============================================================================
LISFLOOD LAI MAP GENERATOR (GEE Version)
=============================================================================
"""

import os
import sys
import numpy as np
import warnings
warnings.filterwarnings("ignore")

import pipeline_config as _cfg
from lisflood_utils import (GridInfo, log, check_imports, make_dirs,
                            gdal_convert_pcraster, gdal_convert_netcdf, load_grid, save_aligned, reproject_to_grid,
                            init_ee)

AREA_TIF        = _cfg.AREA_TIF
OUTPUT_DIR      = "./lisflood_lai"
RESOLUTION_M    = _cfg.RESOLUTION_M
YEAR            = 2024


def visualize(lai_forest, lai_other, mask, info):
    log("STEP 5 - Creating LAI_VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(14, 6))
        fig.suptitle(
            f"LISFLOOD LAI Maps (GEE Logic) - {RESOLUTION_M}m  |  {info.crs}\n"
            f"Grid: origin=({info.transform.c:.1f}, {info.transform.f:.1f})  "
            f"{info.width}x{info.height} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, vmin, vmax, nodata=-9000):
            bg = np.zeros_like(mask, dtype=np.float32)
            bg[mask == 0] = np.nan
            ax.imshow(bg, cmap="Greys", vmin=-1, vmax=1, alpha=0.15, interpolation="nearest")

            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan

            im = ax.imshow(d, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        panel(axes[0], lai_forest, "lai_forest (day 1)\n[m2/m2]", "Greens", "LAI", 0, 6)
        panel(axes[1], lai_other,  "lai_other (day 1)\n[m2/m2]",  "Oranges", "LAI", 0, 4)

        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "LAI_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  * {out}")
    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")


def print_summary(out_paths: dict, info: GridInfo):
    print("\n" + "=" * 62)
    print("  *  DONE - LISFLOOD LAI NetCDFs written")
    print("=" * 62)
    print(f"\n  Grid    : {info.width} cols x {info.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS     : {info.crs}")
    print(f"  Origin  : ({info.transform.c:.2f} m E, {info.transform.f:.2f} m N)\n")
    print("  Outputs :")
    for name, path in out_paths.items():
        print(f"    {name:<12} = {path}")
    print(f"\n  Check   : {OUTPUT_DIR}/LAI_VISUAL_CHECK.png")
    print("=" * 62 + "\n")


def compute_and_download_gee_lai(info):
    log("STEP 2 - Computing LAI parameters in GEE...", "STEP")

    try:
        import ee
        import wxee
    except ImportError:
        log("Missing: earthengine-api wxee", "ERROR")
        sys.exit(1)

    init_ee(_cfg.GEE_PROJECT)
    wxee.Initialize(project=_cfg.GEE_PROJECT)

    # Bounding box for export
    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    buf = 2000  # 2km buffer
    region = ee.Geometry.Rectangle(
        [xmin - buf, ymin - buf, xmax + buf, ymax + buf],
        proj=str(info.crs),
        geodesic=False
    )

    lulc10m = ee.Image("projects/corestack-datasets/assets/datasets/LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
    lulc_label = lulc10m.select('predicted_label')
    forestMask = lulc_label.eq(6)
    otherMask = lulc_label.eq(1).Or(lulc_label.eq(5)).Or(lulc_label.gte(7).And(lulc_label.lte(12)))

    def process_sentinel(image):
        qa = image.select('QA60')
        cloudBitMask = 1 << 10
        cirrusBitMask = 1 << 11
        mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))

        ndvi = image.normalizedDifference(['B8', 'B4'])
        lai = ndvi.multiply(2.33).exp().multiply(0.57)

        laiForest = lai.updateMask(forestMask).rename('lai_forest')
        laiOther = lai.updateMask(otherMask).rename('lai_other')

        return image.addBands([laiForest, laiOther]).select(['lai_forest', 'lai_other']).updateMask(mask)

    collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                  .filterBounds(region)
                  .filterDate(f'{YEAR}-01-01', f'{YEAR}-12-31')
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                  .map(process_sentinel))

    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    make_dirs(raw_dir)
    make_dirs(os.path.join(OUTPUT_DIR, "maps"))

    raw_nc = os.path.join(raw_dir, "lai_raw_gee.nc")
    if not os.path.exists(raw_nc):
        log(f"Downloading GEE LAI time-series to {raw_nc} via wxee...")
        ds = collection.wx.to_xarray(region=region, scale=RESOLUTION_M, crs=str(info.crs))
        ds.to_netcdf(raw_nc)
    else:
        log(f"Raw GEE data already exists: {raw_nc}")

    return raw_nc


def process_local_lai(raw_nc, mask_arr, info):
    """Smooth, gap-fill, reproject. Returns dict of aligned (time, y, x) stacks."""
    log("STEP 3 - Interpolating and aligning outputs to area.tif...", "STEP")
    import xarray as xr
    import pandas as pd

    ds = xr.open_dataset(raw_nc)

    log("  Smoothing and interpolating to daily timescale...", "STEP")
    time_coords = pd.date_range(f'{YEAR}-01-01', f'{YEAR}-12-31')
    ds_daily = ds.resample(time='1D').interpolate('linear')
    ds_daily = ds_daily.reindex(time=time_coords).bfill(dim='time').ffill(dim='time')

    band_names = ['lai_forest', 'lai_other']
    results = {}

    from rasterio.transform import from_bounds

    for name in band_names:
        da = ds_daily[name]
        times, h, w = da.shape

        # xarray coordinates represent pixel centers. We need outer edges for from_bounds.
        half_res = RESOLUTION_M / 2.0
        minx = float(da.x.min()) - half_res
        maxx = float(da.x.max()) + half_res
        miny = float(da.y.min()) - half_res
        maxy = float(da.y.max()) + half_res
        src_transform = from_bounds(minx, miny, maxx, maxy, w, h)

        aligned_stack = np.zeros((times, info.height, info.width), dtype=np.float32)

        for t_idx in range(times):
            arr = da.values[t_idx]
            aligned = reproject_to_grid(
                src_array=arr,
                src_transform=src_transform,
                src_crs=info.crs,
                like=AREA_TIF,
                src_nodata=np.nan,
                dst_nodata=np.nan,
                resampling_method="bilinear"
            ).astype(np.float32)

            # Clamp negatives to 0; keep NaN outside AOI mask
            aligned = np.where(aligned < 0, 0.0, aligned)
            aligned = np.where(mask_arr > 0, aligned, np.nan).astype(np.float32)

            aligned_stack[t_idx] = aligned

        results[name] = aligned_stack

    return results, time_coords


def write_netcdf(stacks, time_coords, info, out_dir):
    """Write two NetCDFs (lai_forest.nc, lai_other.nc) matching the laif.nc reference format.

    Reference structure (from sample file):
      - data variable named 'laif' (single LAI variable per file)
      - dims (time, y, x), float32 data
      - _FillValue: NaN on data AND every coordinate
      - grid_mapping points to 'lambert_azimuthal_equal_area' (string attr only;
        no actual scalar variable — CRS is packed into esri_pe_string)
      - global attrs use lowercase 'conventions'
    """
    log("STEP 4 - Writing NetCDFs in laif.nc reference format...", "STEP")
    import netCDF4 as nc4
    import pandas as pd

    t = info.transform
    x_coords = (t.c + t.a * (np.arange(info.width)  + 0.5)).astype(np.float64)
    y_coords = (t.f + t.e * (np.arange(info.height) + 0.5)).astype(np.float64)

    # time as float64 "days since YEAR-01-01" (CF standard)
    time_units = f"days since {YEAR}-01-01"
    time_vals  = nc4.date2num(time_coords.to_pydatetime(),
                              units=time_units, calendar="standard").astype(np.float64)

    # Use the WKT from the canonical grid for esri_pe_string
    esri_pe_string = info.crs.to_wkt()

    out_paths = {}
    file_specs = [
        ("lai_forest", "Forest", "laiforest", "leaf_area_index_for_forest"),
        ("lai_other",  "Other",  "laiother",  "leaf_area_index_for_other"),
    ]

    for fname, cover_type, std_name, long_name in file_specs:
        arr = stacks[fname].astype(np.float32)
        out_path = os.path.join(out_dir, f"{fname}.nc")

        with nc4.Dataset(out_path, "w", format="NETCDF4") as ds:
            # --- Dimensions (match reference order: x, y, time) ---
            ds.createDimension("x",    info.width)
            ds.createDimension("y",    info.height)
            ds.createDimension("time", len(time_coords))

            # --- Coordinate variables (float64, contiguous, _FillValue=NaN) ---
            x_var = ds.createVariable("x", "f8", ("x",), fill_value=np.float64(np.nan))
            x_var.standard_name = "projection_x_coordinate"
            x_var.long_name     = "x coordinate of projection"
            x_var.units         = "Meter"
            x_var[:] = x_coords

            y_var = ds.createVariable("y", "f8", ("y",), fill_value=np.float64(np.nan))
            y_var.standard_name = "projection_y_coordinate"
            y_var.long_name     = "y coordinate of projection"
            y_var.units         = "Meter"
            y_var[:] = y_coords

            t_var = ds.createVariable("time", "f8", ("time",), fill_value=np.float64(np.nan))
            t_var.standard_name = "time"
            t_var.units         = time_units
            t_var[:] = time_vals

            # --- Data variable 'laif' (always this name in reference format) ---
            d_var = ds.createVariable("laif", "f4", ("time", "y", "x"),
                                      fill_value=np.float32(np.nan))
            d_var.standard_name  = std_name
            d_var.long_name      = long_name
            d_var.units          = "m2/m2"
            d_var.grid_mapping   = "lambert_azimuthal_equal_area"
            d_var.esri_pe_string = esri_pe_string
            d_var[:] = arr

            # --- Global attributes (order + lowercase 'conventions' match reference) ---
            ds.history         = f"Created {pd.Timestamp.now().strftime('%a %b %d %H:%M:%S %Y')}"
            ds.conventions     = "CF-1.6"
            ds.source_software = "lisflood_lai pipeline"
            ds.title           = f"Lisflood maps for AOI - {cover_type} ({YEAR})"
            ds.keywords        = "Lisflood, LAI"
            ds.source          = f"Sentinel-2 SR Harmonized via GEE - {cover_type}"
            ds.institition     = "JRC H01"   # typo preserved to match reference
            ds.institution     = "JRC E1"

        size_mb = os.path.getsize(out_path) / 1e6
        log(f"  wrote {out_path}  ({size_mb:.1f} MB)")
        out_paths[fname] = out_path

    return out_paths


def main():
    print("\n" + "=" * 62)
    print("  LISFLOOD LAI MAP GENERATOR (GEE Version)")
    print(f"  Reference raster : {AREA_TIF}")
    print("=" * 62 + "\n")

    check_imports(["rasterio", "numpy", "geemap", "xarray", "netCDF4", "pandas"])
    make_dirs(OUTPUT_DIR)

    log("STEP 1 - Load canonical grid (locks all alignment to area.tif)", "STEP")
    info, mask = load_grid(AREA_TIF)

    raw_nc = compute_and_download_gee_lai(info)
    stacks, time_coords = process_local_lai(raw_nc, mask, info)

    out_dir = os.path.join(OUTPUT_DIR, "maps")
    out_paths = write_netcdf(stacks, time_coords, info, out_dir)

    # Visualise day 1 of each variable
    visualize(stacks['lai_forest'][0], stacks['lai_other'][0], mask, info)

    print_summary(out_paths, info)


if __name__ == "__main__":
    main()