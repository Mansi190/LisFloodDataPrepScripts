"""
Shared utilities for LISFLOOD preprocessing scripts.
Import from here rather than copy-pasting into each script.
"""

import os
import sys
import math
import gzip
import shutil
import subprocess
import numpy as np
import warnings
from collections import namedtuple
from pathlib import Path

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, kind="INFO"):
    icons = {"INFO": "✔", "STEP": "▶", "WARN": "⚠", "ERROR": "✘", "DONE": "★"}
    print(f"  {icons.get(kind, '·')}  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
#  DIRECTORY SETUP
# ─────────────────────────────────────────────────────────────────────────────

def make_dirs(output_dir):
    """Create output_dir (parents included). Callers that need a raw/ or
    maps/ subfolder create it explicitly by passing that exact path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  IMPORT CHECKER
# ─────────────────────────────────────────────────────────────────────────────

def check_imports(packages):
    """Verify all packages are importable; exit with install hint if any missing."""
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        log(f"Missing packages: {', '.join(missing)}", "ERROR")
        log(f"Run: pip install {' '.join(missing)}", "ERROR")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#  GOOGLE EARTH ENGINE INITIALISATION
# ─────────────────────────────────────────────────────────────────────────────

def init_ee(gee_project):
    """Initialise (and authenticate if needed) Google Earth Engine."""
    import ee
    try:
        ee.Initialize(project=gee_project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=gee_project)


# ─────────────────────────────────────────────────────────────────────────────
#  GRID HELPERS — area.tif is the canonical reference for every output.
#  Read its metadata once (cached) and use save_aligned / reproject_to_grid /
#  snap_to_grid to keep every output on the same pixel grid.
# ─────────────────────────────────────────────────────────────────────────────

GridInfo = namedtuple("GridInfo", "transform width height crs profile")

_GRID_CACHE = {}


def _read_info(path):
    """Open a reference raster and return its GridInfo. Internal helper."""
    import rasterio
    with rasterio.open(path) as src:
        return GridInfo(src.transform, src.width, src.height, src.crs,
                        src.profile.copy())


def _info(like):
    """Cached metadata lookup for `like` (path to a reference tif)."""
    if like not in _GRID_CACHE:
        _GRID_CACHE[like] = _read_info(like)
    return _GRID_CACHE[like]


def load_grid(area_path):
    """
    Read area.tif and return (GridInfo, mask_array).
    Falls back to area.map if .tif is absent.

    The returned GridInfo is also cached so subsequent save_aligned /
    reproject_to_grid calls referencing this path read no disk.
    """
    if not os.path.exists(area_path):
        alt = area_path.replace(".tif", ".map")
        if os.path.exists(alt):
            area_path = alt
        else:
            log(f"Reference raster not found: {area_path}", "ERROR")
            log("Run topographyMapsScript.py first, or check AREA_TIF.", "ERROR")
            sys.exit(1)

    import rasterio
    with rasterio.open(area_path) as src:
        info = GridInfo(src.transform, src.width, src.height, src.crs,
                        src.profile.copy())
        mask = src.read(1)
    _GRID_CACHE[area_path] = info

    res = abs(info.transform.a)
    log(f"  Grid : origin=({info.transform.c:.2f}, {info.transform.f:.2f}) | "
        f"{info.width}×{info.height} | {res:.0f}m/px | {info.crs}")
    log(f"  Mask : {int((mask > 0).sum()):,} cells inside")
    return info, mask


def snap_to_grid(array, like, nodata):
    """Crop or pad array to match `like`'s (height, width)."""
    info = _info(like)
    h, w = array.shape
    if h == info.height and w == info.width:
        return array
    cropped = array[:info.height, :info.width]
    if cropped.shape[0] < info.height or cropped.shape[1] < info.width:
        out = np.full((info.height, info.width), nodata, dtype=array.dtype)
        out[:cropped.shape[0], :cropped.shape[1]] = cropped
        return out
    return cropped


def save_aligned(array, out_path, dtype, nodata, like):
    """
    Write `array` as a GeoTIFF whose grid matches `like` (path to area.tif
    or any reference raster on the canonical pipeline grid).  Auto-snaps
    the array to `like`'s dimensions before writing.
    """
    import rasterio
    info = _info(like)
    profile = dict(info.profile)
    profile.update(driver="GTiff", dtype=dtype, nodata=nodata,
                   count=1, compress="lzw")
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(snap_to_grid(array, like, nodata).astype(dtype), 1)


def reproject_to_grid(src_array, src_transform, src_crs, like,
                     src_nodata, dst_nodata, resampling_method):
    """
    Reproject src_array onto `like`'s grid (same origin, pixel size, CRS,
    dimensions).  `like` is a path to area.tif (or any reference raster).
    """
    from rasterio.warp import reproject, Resampling
    info = _info(like)
    resamp = {
        "nearest":  Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "mode":     Resampling.mode,
        "average":  Resampling.average,
    }[resampling_method]
    dst = np.full((info.height, info.width), dst_nodata, dtype=src_array.dtype)
    reproject(
        source=src_array,            destination=dst,
        src_transform=src_transform, src_crs=src_crs,
        dst_transform=info.transform, dst_crs=info.crs,
        resampling=resamp,
        src_nodata=src_nodata,       dst_nodata=dst_nodata,
    )
    return dst


def condition_dem(dem_tif, dirmap=(64, 128, 1, 2, 4, 8, 16, 32)):
    """Hydrologically condition a DEM, then derive D8. Returns (fdir, acc, dem).

    The four pysheds steps in the order that matters: fill_pits (single-cell dips) ->
    fill_depressions (multi-cell sinks) -> resolve_flats (ties on flat ground) -> flowdir.
    Skip any and D8 leaves cells with no downstream neighbour, so accumulation stops short
    and a downstream trace dies there.

    Shared so the two jobs that need a flow network cannot drift apart:
    topographyMapsScript.py (the model's own LDD, ROI only) and make_inflow.py (a network
    spanning the ROI plus an upstream sub-basin). Returns pysheds Raster objects, not bare
    arrays, because callers rely on their nodata handling.
    """
    from pysheds.grid import Grid
    grid = Grid.from_raster(dem_tif)
    dem = grid.read_raster(dem_tif)
    dem = grid.fill_pits(dem)
    dem = grid.fill_depressions(dem)
    dem = grid.resolve_flats(dem)
    fdir = grid.flowdir(dem, dirmap=dirmap)
    acc = grid.accumulation(fdir, dirmap=dirmap)
    return fdir, acc, dem


# ─────────────────────────────────────────────────────────────────────────────
#  NATIVE-RESOLUTION EARTH ENGINE EXPORTS
# ─────────────────────────────────────────────────────────────────────────────
# Source archives (CHIRPS 0.05 deg, ERA5-Land 0.1 deg) are far coarser than the
# 300 m model grid. Asking Earth Engine to reproject to 300 m server-side ships
# hundreds of identical copies of every real value -- ~690x for ERA5-Land over a
# basin this size, ~190x for CHIRPS -- which is pure transfer and disk cost, and
# turns a job of ~110 export requests into one of ~3650.
#
# Instead: export on the collection's OWN grid (pass crs + crs_transform, and no
# scale, so EE resamples nothing at all), then resample once, locally, with
# reproject_to_grid. Note reduceResolution is the wrong tool in this direction --
# it averages FINE pixels into COARSE ones, and here the source is already the
# coarser of the two, so it does nothing while .reproject() does a plain
# nearest-neighbour blow-up.

def native_grid(collection, band):
    """Return (crs, transform) of an ImageCollection's own pixel grid.

    Feed these straight to geemap.ee_export_image as crs= and crs_transform=
    (with NO scale=) to get an export that Earth Engine has not resampled.
    e.g. ERA5-Land -> ('EPSG:4326', [0.1, 0, -180.05, 0, -0.1, 90.05])
         CHIRPS    -> ('EPSG:4326', [0.05, 0, -180, 0, -0.05, 50])
    """
    proj = collection.first().select(band).projection().getInfo()
    return proj["crs"], list(proj["transform"])


def native_region(info, native_transform, ee, buffer_pixels=3):
    """Lat/lon export window covering `info`'s grid plus a margin of native pixels.

    Returns (region, nx, ny) where nx/ny are the approximate native pixel counts,
    used to size download chunks.

    The model grid is projected (UTM metres) while the export is geographic, so
    the corners are transformed and the bbox padded. The padding is not cosmetic:
    resampling an edge target pixel needs source cells beyond the edge, or the
    outermost rows and columns of the model grid come back empty.
    """
    import math
    from pyproj import Transformer

    t = info.transform
    xmin, ymax = t.c, t.f
    xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height

    tr = Transformer.from_crs(str(info.crs), "EPSG:4326", always_xy=True)
    lons, lats = tr.transform([xmin, xmax, xmin, xmax], [ymin, ymin, ymax, ymax])

    px, py = abs(native_transform[0]), abs(native_transform[4])
    lon_min, lon_max = min(lons) - buffer_pixels * px, max(lons) + buffer_pixels * px
    lat_min, lat_max = min(lats) - buffer_pixels * py, max(lats) + buffer_pixels * py

    region = ee.Geometry.Rectangle([lon_min, lat_min, lon_max, lat_max],
                                   proj="EPSG:4326", geodesic=False)
    nx = int(math.ceil((lon_max - lon_min) / px)) + 1
    ny = int(math.ceil((lat_max - lat_min) / py)) + 1
    return region, nx, ny


# ─────────────────────────────────────────────────────────────────────────────
#  GDAL PCRaster CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def gdal_convert_pcraster(tif_path, map_path, pcraster_type="VS_SCALAR"):
    """
    Convert GeoTIFF → PCRaster .map via gdal_translate.

    pcraster_type : VS_SCALAR | VS_NOMINAL | VS_BOOLEAN | VS_LDD
    Returns True on success.
    """
    try:
        cmd = [
            "gdal_translate", "-of", "PCRaster",
            "-mo", f"PCRASTER_VALUESCALE={pcraster_type}",
            tif_path, map_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(map_path):
            return True
        log(f"gdal_translate stderr: {r.stderr.strip()}", "WARN")
        return False
    except Exception as e:
        log(f"gdal_translate failed: {e}", "WARN")
        return False

def gdal_convert_netcdf(tif_path, nc_path):
    """
    Convert GeoTIFF → NetCDF (.nc) using Xarray/Rasterio.
    This guarantees that the Y-axis coordinates are identical to our
    other generated files (descending, matching the TIFF transform), 
    preventing GDAL's default CF-1.5 bottom-up flip which breaks LISVAP.
    Returns True on success.
    """
    try:
        import xarray as xr
        import rasterio
        with rasterio.open(tif_path) as src:
            data = src.read(1)
            nodata = src.nodata if src.nodata is not None else -9999
            transform = src.transform
            crs = src.crs
            width = src.width
            height = src.height

        x_coords = [transform.c + (i + 0.5) * transform.a for i in range(width)]
        y_coords = [transform.f + (i + 0.5) * transform.e for i in range(height)]
        
        var_name = "Band1"
            
        ds = xr.Dataset(
            data_vars={
                var_name: (["y", "x"], data)
            },
            coords={
                "y": y_coords,
                "x": x_coords
            },
            attrs={
                "crs": str(crs)
            }
        )
        
        ds.to_netcdf(nc_path, encoding={var_name: {"_FillValue": nodata, "zlib": True, "complevel": 4}})
        return True
    except Exception as e:
        log(f"xarray convert_netcdf failed: {e}", "WARN")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  SRTM TILE DOWNLOAD HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def download_srtm_tile(lat, lon, raw_dir):
    """Download one SRTM 1-arcsec HGT tile and convert it to GeoTIFF."""
    import requests

    lat_s = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
    lon_s = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
    name  = f"{lat_s}{lon_s}"
    tif   = os.path.join(raw_dir, f"srtm_{name}.tif")
    if os.path.exists(tif):
        log(f"  {name} already downloaded.")
        return tif
    url = (f"https://s3.amazonaws.com/elevation-tiles-prod/skadi/"
           f"{lat_s}/{name}.hgt.gz")
    log(f"  Downloading SRTM tile {name} ...")
    try:
        resp = requests.get(url, timeout=120)
        if resp.status_code != 200:
            log(f"  HTTP {resp.status_code} for {name}", "WARN")
            return None
        gz  = os.path.join(raw_dir, f"{name}.hgt.gz")
        hgt = os.path.join(raw_dir, f"{name}.hgt")
        with open(gz, "wb") as f:
            f.write(resp.content)
        with gzip.open(gz, "rb") as fin, open(hgt, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        hgt_to_tif(hgt, tif, lat, lon)
        log(f"  ✔ {name}")
        return tif
    except Exception as e:
        log(f"  {name} failed: {e}", "WARN")
        return None


def hgt_to_tif(hgt_path, tif_path, lat, lon):
    """Convert a raw SRTM .hgt binary to GeoTIFF (WGS84, EPSG:4326)."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    data = np.fromfile(hgt_path, dtype=">i2").astype(np.float32)
    size = 3601
    data = data.reshape((size, size))
    data[data == -32768] = -9999
    t = from_bounds(lon, lat, lon + 1, lat + 1, size, size)
    with rasterio.open(tif_path, "w", driver="GTiff", height=size, width=size,
                       count=1, dtype="float32", crs=CRS.from_epsg(4326),
                       transform=t, nodata=-9999) as dst:
        dst.write(data, 1)


def merge_tiles(paths, out):
    """Merge multiple GeoTIFF tiles into one mosaic."""
    import rasterio
    from rasterio.merge import merge
    log(f"  Merging {len(paths)} tiles ...")
    datasets      = [rasterio.open(p) for p in paths]
    mosaic, trans = merge(datasets)
    profile       = datasets[0].profile.copy()
    profile.update({"height": mosaic.shape[1], "width": mosaic.shape[2],
                    "transform": trans})
    for d in datasets:
        d.close()
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(mosaic)
    return out


def ee_export_tiled(image, filename, scale, crs, region, target_mb=25.0,
                    max_attempts=5):
    """Download an Earth Engine image, tiling the request until it succeeds.

    TWO different server limits bite here, and they need different tile counts:

      * download size  -- getDownloadURL caps a response at ~32-50 MB.
      * reprojection   -- reduceResolution().reproject() from a fine native asset
                          (e.g. 10 m LULC) makes EE materialise the NATIVE grid over
                          the whole region; past ~5e7 px it returns "Reprojection
                          output too large", regardless of how small the output is.

    Worse, geemap.ee_export_image SWALLOWS both failures: it logs "An error occurred
    while downloading" and returns normally, leaving no file, so the caller only finds
    out when rasterio cannot open the result. We therefore treat "no file" as an error,
    seed the tile count from both limits, and DOUBLE it on failure rather than trying to
    predict every server-side rule.
    """
    import ee
    import geemap
    import rasterio
    from rasterio.merge import merge as rio_merge

    n_bands = int(image.bandNames().size().getInfo())
    area_m2 = float(region.area(1).getInfo())

    # seed 1: output payload
    out_mb = (area_m2 / (scale ** 2)) * 4 * n_bands / 1e6
    n = max(1, int(math.ceil(math.sqrt(out_mb / target_mb))))

    # seed 2: native grid the server must build to reproject
    try:
        native = float(image.projection().nominalScale().getInfo())
    except Exception:
        native = scale
    if 0 < native < scale:
        native_px = area_m2 / (native ** 2)
        n = max(n, int(math.ceil(math.sqrt(native_px / 5e7))))

    log(f"  export {out_mb:.0f} MB out / {n_bands} band(s) / native {native:.0f} m "
        f"-> starting at {n}x{n} tiles")

    def _export(img, path, geom):
        geemap.ee_export_image(img, filename=path, scale=scale, crs=str(crs),
                               region=geom, file_per_band=False)
        if not os.path.exists(path):
            raise RuntimeError(f"Earth Engine export produced no file: {path}")

    base, ext = os.path.splitext(filename)

    for attempt in range(max_attempts):
        tiles = []
        try:
            if n == 1:
                _export(image, filename, region)
                return filename

            coords = region.bounds(1, str(crs)).coordinates().getInfo()[0]
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            dx, dy = (x1 - x0) / n, (y1 - y0) / n

            for i in range(n):
                for j in range(n):
                    tile = f"{base}_t{i}_{j}{ext}"
                    if not os.path.exists(tile):
                        sub = ee.Geometry.Rectangle(
                            [x0 + i * dx, y0 + j * dy,
                             x0 + (i + 1) * dx, y0 + (j + 1) * dy],
                            proj=str(crs), geodesic=False)
                        _export(image, tile, sub)
                    tiles.append(tile)
                    log(f"    tile {len(tiles)}/{n * n}")

            srcs = [rasterio.open(t) for t in tiles]
            mosaic, transform = rio_merge(srcs)
            profile = srcs[0].profile
            profile.update(height=mosaic.shape[1], width=mosaic.shape[2],
                           transform=transform, count=mosaic.shape[0])
            with rasterio.open(filename, "w", **profile) as dst:
                dst.write(mosaic)
            for src in srcs:
                src.close()
            for t in tiles:
                os.remove(t)
            log(f"  mosaicked {len(tiles)} tiles -> {os.path.basename(filename)} "
                f"({mosaic.shape[2]}x{mosaic.shape[1]})")
            return filename

        except RuntimeError as e:
            for t in tiles:
                if os.path.exists(t):
                    os.remove(t)
            if attempt == max_attempts - 1:
                raise
            n *= 2
            log(f"  export failed ({e}); retrying at {n}x{n} tiles", "WARN")

    raise RuntimeError(f"tiled export failed after {max_attempts} attempts: {filename}")
