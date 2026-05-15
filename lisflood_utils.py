"""
Shared utilities for LISFLOOD preprocessing scripts.
Import from here rather than copy-pasting into each script.
"""

import os
import sys
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
    """Create output_dir and its raw/ and maps/ subdirectories."""
    for sub in ["", "/raw", "/maps"]:
        Path(output_dir + sub).mkdir(parents=True, exist_ok=True)


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
    Convert GeoTIFF → NetCDF (.nc) via gdal_translate.
    Returns True on success.
    """
    try:
        cmd = [
            "gdal_translate", "-of", "netCDF",
            "-co", "FORMAT=NC4",
            "-co", "COMPRESS=DEFLATE",
            tif_path, nc_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(nc_path):
            return True
        log(f"gdal_translate stderr: {r.stderr.strip()}", "WARN")
        return False
    except Exception as e:
        log(f"gdal_translate failed: {e}", "WARN")
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
