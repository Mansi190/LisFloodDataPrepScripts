"""
=============================================================================
LISFLOOD INPUT MAP GENERATOR — Araria District, Bihar
Perfect grid alignment guaranteed — all files share identical origin,
pixel size, and dimensions.
=============================================================================

WORKFLOW:
  Step 1 — Download watershed polygon from HydroSHEDS (~1000 ha in Araria)
  Step 2 — Rasterize the watershed .shp → this becomes the MASTER GRID
            (every other raster will be forced to match this exactly)
  Step 3 — Download NASA SRTM 30m DEM, clip + snap to master grid
  Step 4 — Fill sinks, compute LDD — snapped to master grid
  Step 5 — Compute gradient and elvstd — already on master grid
  Step 6 — Apply mask, verify alignment, save .tif + .map + visual PNG

WHY RASTERIZE FIRST?
  The watershed shapefile defines the exact study domain.
  By rasterizing it first, we get a pixel-perfect grid definition.
  All subsequent rasters are forced to match that grid exactly —
  same top-left corner, same pixel size, same row/column count.
  LISFLOOD will reject inputs that differ by even a single pixel.

INSTALL:
  pip install rasterio numpy scipy pysheds matplotlib requests
  pip install geopandas shapely fiona pyproj

RUN:
  python lisflood_araria.py
=============================================================================
"""

import os
import sys
import gzip
import math
import shutil
import zipfile
import subprocess
import numpy as np
import requests
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
#   ★  SETTINGS — only edit this section
# ─────────────────────────────────────────────────────────────────────────────

# ── WATERSHED SOURCE ──────────────────────────────────────────────────────────
# Choose ONE of three options:
#
#   "HYDROSHEDS"  → script downloads a pre-made watershed from HydroSHEDS
#                   (~1000 ha basin inside Araria district)
#
#   "OWN_FILE"    → you provide your own shapefile or GeoPackage
#                   set OWN_WATERSHED_PATH to your file path below
#
#   "SYNTHETIC"   → uses a built-in test polygon (no download needed)
#                   useful for testing the pipeline without any data

WATERSHED_SOURCE = "OWN_FILE"   # ← change this to switch mode

# ── If WATERSHED_SOURCE = "OWN_FILE" ─────────────────────────────────────────
# Set the path to your shapefile (.shp) or GeoPackage (.gpkg) or GeoJSON (.geojson)
# Rules:
#   • The file must contain exactly ONE polygon (your watershed boundary)
#   • It can be in ANY coordinate system — the script reprojects it automatically
#   • If the file has multiple features, only the FIRST one is used
#     (dissolve them in QGIS first if you want to merge multiple polygons)
OWN_WATERSHED_PATH = "./ArariaShapefile.shp"

# ── HydroSHEDS options (only used when WATERSHED_SOURCE = "HYDROSHEDS") ──────
ARARIA_BBOX     = [87.0, 25.8, 88.0, 26.6]   # [west, south, east, north]
TARGET_AREA_HA  = 1000                         # target watershed size in hectares
MAX_CANDIDATES  = 5                            # how many HydroSHEDS options to show

# ── Common settings ───────────────────────────────────────────────────────────
OUTPUT_DIR      = "./lisflood_outputs"
TARGET_CRS      = "EPSG:32645"                 # UTM Zone 45N — correct for Bihar
RESOLUTION_M    = 30                           # 30m matches native SRTM resolution

# ─────────────────────────────────────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, kind="INFO"):
    icons = {"INFO": "✔", "STEP": "▶", "WARN": "⚠", "ERROR": "✘", "DONE": "★"}
    print(f"  {icons.get(kind, '·')}  {msg}")

def make_dirs():
    for sub in ["", "/raw", "/maps"]:
        Path(OUTPUT_DIR + sub).mkdir(parents=True, exist_ok=True)

def check_imports():
    missing = []
    for pkg in ["rasterio", "numpy", "scipy", "pysheds",
                "matplotlib", "requests", "geopandas", "shapely", "pyproj"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n  ✘  Missing: {', '.join(missing)}")
        print(f"     Run: pip install {' '.join(missing)}\n")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
#   THE MASTER GRID
#   One object. Created once from the rasterized watershed.
#   Every raster in this script uses it to guarantee alignment.
# ─────────────────────────────────────────────────────────────────────────────

class MasterGrid:
    """
    Holds the canonical spatial definition that ALL rasters must share.

    Fields:
      transform — affine transform (defines top-left origin + pixel size)
      width     — number of columns
      height    — number of rows
      crs       — coordinate reference system

    Usage:
      master.save(array, path, dtype, nodata)
          → saves the array as a GeoTIFF with exactly this grid's metadata
      master.reproject_array(...)
          → reprojects any array ONTO this exact grid
      master.snap(array, nodata)
          → crops/pads array to exactly (height, width) if rounding differs
    """

    def __init__(self, transform, width, height, crs):
        self.transform = transform
        self.width     = width
        self.height    = height
        self.crs       = crs

    @property
    def profile(self):
        return {
            "driver":    "GTiff",
            "crs":       self.crs,
            "transform": self.transform,
            "width":     self.width,
            "height":    self.height,
            "count":     1,
            "compress":  "lzw",
        }

    def snap(self, array, nodata_val):
        """Crop or pad array to exactly (height, width)."""
        h, w = array.shape
        if h == self.height and w == self.width:
            return array
        cropped = array[:self.height, :self.width]
        if cropped.shape[0] < self.height or cropped.shape[1] < self.width:
            out = np.full((self.height, self.width), nodata_val, dtype=array.dtype)
            out[:cropped.shape[0], :cropped.shape[1]] = cropped
            return out
        return cropped

    def reproject_array(self, src_array, src_transform, src_crs,
                        src_nodata, dst_nodata, resampling_method):
        """
        Reprojects src_array onto the master grid.
        This forces the output to have the exact same origin, pixel size,
        CRS, and dimensions as the master — guaranteeing alignment.
        """
        from rasterio.warp import reproject, Resampling
        resamp = {"nearest": Resampling.nearest,
                  "bilinear": Resampling.bilinear}[resampling_method]
        dst = np.full((self.height, self.width), dst_nodata, dtype=src_array.dtype)
        reproject(
            source=src_array, destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=self.transform, dst_crs=self.crs,
            resampling=resamp,
            src_nodata=src_nodata, dst_nodata=dst_nodata,
        )
        return dst

    def save(self, array, path, dtype, nodata):
        """Save array as GeoTIFF with master grid spatial metadata."""
        import rasterio
        p = self.profile.copy()
        p.update({"dtype": dtype, "nodata": nodata})
        with rasterio.open(path, "w", **p) as dst:
            dst.write(self.snap(array, nodata).astype(dtype), 1)

    def __str__(self):
        return (f"MasterGrid | origin=({self.transform.c:.2f}, {self.transform.f:.2f}) | "
                f"{self.width} cols × {self.height} rows | {RESOLUTION_M}m/px")


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 1 — GET WATERSHED FROM HYDROSHEDS
# ─────────────────────────────────────────────────────────────────────────────

def get_hydrosheds_watershed():
    """
    Downloads HydroSHEDS Level 10 basin polygons for Asia,
    filters to Araria district, picks the basin closest to TARGET_AREA_HA.
    Returns a GeoDataFrame in WGS84.
    """
    log("STEP 1 — Getting HydroSHEDS watershed", "STEP")

    raw_dir  = os.path.join(OUTPUT_DIR, "raw")
    gpkg_out = os.path.join(OUTPUT_DIR, "watershed.gpkg")
    shp_out  = os.path.join(OUTPUT_DIR, "watershed.shp")

    if os.path.exists(gpkg_out):
        log("Watershed already exists, loading.")
        import geopandas as gpd
        gdf  = gpd.read_file(gpkg_out)
        geom = gdf.iloc[0].geometry
        area = gdf.iloc[0]["area_ha"]
        log(f"  Loaded: {area:.0f} ha")
        return geom, area, gdf

    import geopandas as gpd
    from shapely.geometry import box

    west, south, east, north = ARARIA_BBOX
    basin_gdf = _download_hydrosheds(raw_dir, west, south, east, north)

    if basin_gdf is None or len(basin_gdf) == 0:
        log("HydroSHEDS failed — using synthetic watershed", "WARN")
        _print_hydrosheds_instructions(raw_dir)
        return _make_synthetic_watershed(gpkg_out, shp_out)

    araria_box = box(west, south, east, north)
    in_araria  = basin_gdf[basin_gdf.intersects(araria_box)].copy()

    if len(in_araria) == 0:
        log("No basins in Araria bbox — using synthetic", "WARN")
        return _make_synthetic_watershed(gpkg_out, shp_out)

    in_araria_utm      = in_araria.to_crs(TARGET_CRS)
    in_araria["area_ha"]   = in_araria_utm.geometry.area / 10_000
    in_araria["area_diff"] = abs(in_araria["area_ha"] - TARGET_AREA_HA)
    candidates = in_araria.nsmallest(MAX_CANDIDATES, "area_diff")

    log(f"  {len(in_araria)} basins found · top {len(candidates)} candidates:")
    for i, (_, row) in enumerate(candidates.iterrows()):
        tag = "★ SELECTED" if i == 0 else f"  option {i+1}"
        log(f"    {tag}  area={row['area_ha']:.0f} ha  diff={row['area_diff']:.0f} ha")

    best = candidates.iloc[[0]]
    best.to_file(gpkg_out, driver="GPKG")
    best.to_file(shp_out)
    log(f"  Saved → {gpkg_out}")
    return best.iloc[0].geometry, best.iloc[0]["area_ha"], best


def _download_hydrosheds(raw_dir, west, south, east, north):
    import geopandas as gpd
    zip_path = os.path.join(raw_dir, "hybas_as_lev10.zip")
    shp_glob = list(Path(raw_dir).glob("hybas_as_lev10*.shp"))
    if shp_glob:
        log("  HydroSHEDS already extracted.")
        return gpd.read_file(shp_glob[0], bbox=(west, south, east, north))
    url = ("https://data.hydrosheds.org/file/HydroBASINS/standard/"
           "hybas_as_lev10_v1c.zip")
    log(f"  Downloading HydroSHEDS Level 10 Asia (~50 MB) ...")
    try:
        resp = requests.get(url, timeout=300, stream=True)
        if resp.status_code != 200:
            log(f"  HTTP {resp.status_code}", "WARN")
            return None
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  {100*downloaded/total:.0f}%", end="", flush=True)
        print()
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(raw_dir)
        shp = list(Path(raw_dir).glob("hybas_as_lev10*.shp"))
        if not shp:
            return None
        return gpd.read_file(shp[0], bbox=(west, south, east, north))
    except Exception as e:
        log(f"  Error: {e}", "WARN")
        return None


def _print_hydrosheds_instructions(raw_dir):
    print("\n  ┌──────────────────────────────────────────────────────┐")
    print("  │  Manual HydroSHEDS download:                         │")
    print("  │  1. https://www.hydrosheds.org/page/hydrobasins       │")
    print("  │  2. HydroBASINS → Asia → Level 10                    │")
    print(f"  │  3. Extract into: {raw_dir}/   │")
    print("  │  4. Re-run script                                     │")
    print("  └──────────────────────────────────────────────────────┘\n")


def _make_synthetic_watershed(gpkg_out, shp_out):
    import geopandas as gpd
    from shapely.geometry import Polygon
    cx, cy = 87.47, 26.15
    d = 0.048
    poly = Polygon([
        (cx-d, cy-d*0.8), (cx-d*0.7, cy-d), (cx+d*0.3, cy-d),
        (cx+d, cy-d*0.5), (cx+d, cy+d*0.6), (cx+d*0.4, cy+d),
        (cx-d*0.5, cy+d), (cx-d, cy+d*0.4),
    ])
    gdf = gpd.GeoDataFrame({"area_ha": [1000], "source": ["synthetic"]},
                           geometry=[poly], crs="EPSG:4326")
    gdf.to_file(gpkg_out, driver="GPKG")
    gdf.to_file(shp_out)
    area = gdf.to_crs(TARGET_CRS).geometry.area.iloc[0] / 10_000
    log(f"  Synthetic watershed: {area:.0f} ha")
    return gdf.iloc[0].geometry, area, gdf


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 2 — RASTERIZE WATERSHED → CREATES MASTER GRID
# ─────────────────────────────────────────────────────────────────────────────

def rasterize_watershed(watershed_geom):
    """
    Converts the watershed polygon → raster and creates the MasterGrid.

    The rasterization process:
      1. Reproject watershed polygon to UTM metres
      2. Get bounding box in metres
      3. SNAP origin to nearest clean 30m multiple
         (prevents floating-point drift when two rasters are independently
          reprojected — snapping to integer multiples of 30 guarantees
          both land on the same grid)
      4. Burn the polygon: inside=1, outside=0
      5. Build MasterGrid from this raster's exact metadata

    All subsequent rasters use master.reproject_array() or master.save()
    which both use the same transform, width, height, and CRS.
    """
    log("STEP 2 — Rasterizing watershed → creating master grid", "STEP")

    import rasterio
    from rasterio.transform import from_origin
    from rasterio.features import rasterize
    from shapely.ops import transform as shapely_transform
    import pyproj

    out_path = os.path.join(OUTPUT_DIR, "maps", "area.tif")

    # Reproject watershed polygon to UTM
    proj_fwd       = pyproj.Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    watershed_utm  = shapely_transform(proj_fwd.transform, watershed_geom)
    bounds         = watershed_utm.bounds   # (minx, miny, maxx, maxy) in metres

    log(f"  Watershed UTM bounds: W={bounds[0]:.1f} S={bounds[1]:.1f} "
        f"E={bounds[2]:.1f} N={bounds[3]:.1f}")

    # Snap origin to clean 30m grid
    # floor → expand outward so we never clip the watershed
    snap     = RESOLUTION_M
    origin_x = math.floor(bounds[0] / snap) * snap   # left  edge
    origin_y = math.ceil (bounds[3] / snap) * snap   # top   edge
    end_x    = math.ceil (bounds[2] / snap) * snap   # right edge
    end_y    = math.floor(bounds[1] / snap) * snap   # bottom edge

    width  = int(round((end_x   - origin_x) / snap))
    height = int(round((origin_y - end_y)   / snap))

    transform = from_origin(origin_x, origin_y, snap, snap)

    log(f"\n  Master grid (snapped to {snap}m):")
    log(f"    Top-left origin : ({origin_x:.1f} m E,  {origin_y:.1f} m N)")
    log(f"    Pixel size      : {snap}m × {snap}m")
    log(f"    Dimensions      : {width} cols × {height} rows")
    log(f"    Total cells     : {width * height:,}")
    log(f"    Grid area       : {width * height * snap**2 / 1e6:.2f} km²")

    # Burn watershed polygon into grid
    mask_arr = rasterize(
        shapes    = [(watershed_utm, 1)],
        out_shape = (height, width),
        transform = transform,
        fill      = 0,
        dtype     = np.int8
    )

    inside_ha = mask_arr.sum() * snap**2 / 10_000
    log(f"\n  Rasterized: {mask_arr.sum():,} cells inside ({inside_ha:.1f} ha)")

    # Build and return MasterGrid
    master = MasterGrid(transform, width, height, TARGET_CRS)
    log(f"  {master}")

    # Save area.tif
    master.save(mask_arr, out_path, "int8", 0)
    log(f"  area.tif saved → {out_path}")
    return master, mask_arr


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 3 — DOWNLOAD SRTM DEM AND SNAP TO MASTER GRID
# ─────────────────────────────────────────────────────────────────────────────

def get_dem_snapped(watershed_geom, master):
    """
    Downloads NASA SRTM 30m tiles and reprojects them EXACTLY onto
    the master grid using master.reproject_array().

    The key: instead of letting rasterio choose the output grid
    (which gives slightly different origins each time), we force
    it to use master.transform, master.width, master.height.
    """
    log("STEP 3 — Downloading NASA SRTM 30m and snapping to master grid", "STEP")

    import rasterio
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    dem_out = os.path.join(OUTPUT_DIR, "raw", "dem_snapped.tif")

    if os.path.exists(dem_out):
        log("Snapped DEM already exists, loading.")
        with rasterio.open(dem_out) as src:
            return dem_out, src.read(1)

    bounds = watershed_geom.bounds
    buf    = 0.05
    west, south = bounds[0]-buf, bounds[1]-buf
    east, north = bounds[2]+buf, bounds[3]+buf

    tile_lats  = range(int(math.floor(south)), int(math.ceil(north)))
    tile_lons  = range(int(math.floor(west)),  int(math.ceil(east)))
    tile_paths = [p for lat in tile_lats for lon in tile_lons
                  for p in [_download_srtm_tile(lat, lon, raw_dir)] if p]

    if not tile_paths:
        log("No tiles downloaded — using synthetic DEM", "WARN")
        return _synthetic_dem_snapped(master, dem_out)

    merged = (tile_paths[0] if len(tile_paths) == 1
              else _merge_tiles(tile_paths, os.path.join(raw_dir, "srtm_merged.tif")))

    with rasterio.open(merged) as src:
        raw = src.read(1).astype(np.float32)
        raw[raw == src.nodata] = np.nan
        dem_snapped = master.reproject_array(
            src_array=raw, src_transform=src.transform, src_crs=src.crs,
            src_nodata=np.nan, dst_nodata=-9999, resampling_method="bilinear"
        )

    master.save(dem_snapped, dem_out, "float32", -9999)
    valid = dem_snapped[dem_snapped > -9000]
    log(f"  DEM snapped: {master.width}×{master.height} cells | "
        f"elev {valid.min():.1f}–{valid.max():.1f} m")
    log(f"  → {dem_out}")
    return dem_out, dem_snapped


def _download_srtm_tile(lat, lon, raw_dir):
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
        _hgt_to_tif(hgt, tif, lat, lon)
        log(f"  ✔ {name}")
        return tif
    except Exception as e:
        log(f"  {name} failed: {e}", "WARN")
        return None


def _hgt_to_tif(hgt_path, tif_path, lat, lon):
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    data = np.fromfile(hgt_path, dtype=">i2").astype(np.float32)
    size = 3601
    data = data.reshape((size, size))
    data[data == -32768] = -9999
    t = from_bounds(lon, lat, lon+1, lat+1, size, size)
    with rasterio.open(tif_path, "w", driver="GTiff", height=size, width=size,
                       count=1, dtype="float32", crs=CRS.from_epsg(4326),
                       transform=t, nodata=-9999) as dst:
        dst.write(data, 1)


def _merge_tiles(paths, out):
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


def _synthetic_dem_snapped(master, out_path):
    h, w = master.height, master.width
    np.random.seed(42)
    row_idx = np.arange(h).reshape(-1, 1)
    dem = (65 + 5*(h-row_idx)/h
           + np.random.normal(0, 0.5, (h, w))).astype(np.float32)
    master.save(dem, out_path, "float32", -9999)
    return out_path, dem


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4 — FILL SINKS + LDD (snapped to master grid)
# ─────────────────────────────────────────────────────────────────────────────

def compute_ldd_snapped(dem_path, master):
    """
    Fills DEM sinks and computes LDD.
    Snaps result to master grid using NEAREST NEIGHBOR resampling.

    Why nearest neighbor (not bilinear)?
      LDD values are direction codes: 1, 2, 3 ... 9.
      Bilinear interpolation would give 4.7 or 6.3 — meaningless.
      Nearest neighbor keeps them as whole integers.
    """
    log("STEP 4 — Filling sinks and computing LDD", "STEP")

    import rasterio
    try:
        from pysheds.grid import Grid
    except ImportError:
        log("Install pysheds: pip install pysheds", "ERROR")
        sys.exit(1)

    out_path = os.path.join(OUTPUT_DIR, "maps", "ldd.tif")

    grid   = Grid.from_raster(dem_path)
    dem    = grid.read_raster(dem_path)
    filled = grid.fill_pits(dem)
    log("  Pits filled")
    filled = grid.fill_depressions(filled)
    log("  Depressions filled")
    filled = grid.resolve_flats(filled)
    log("  Flat areas resolved")

    dirmap   = (64, 128, 1, 2, 4, 8, 16, 32)
    fdir     = grid.flowdir(filled, dirmap=dirmap)
    code_map = {64:6, 128:9, 1:8, 2:7, 4:4, 8:1, 16:2, 32:3, 0:5}
    fdir_arr = np.array(fdir).astype(np.int16)
    ldd_raw  = np.full_like(fdir_arr, 5, dtype=np.int8)
    for ps, pcr in code_map.items():
        ldd_raw[fdir_arr == ps] = pcr

    # Snap to master grid
    with rasterio.open(dem_path) as src:
        ldd_snapped = master.reproject_array(
            src_array=ldd_raw.astype(np.float32),
            src_transform=src.transform, src_crs=src.crs,
            src_nodata=-1, dst_nodata=-1,
            resampling_method="nearest"
        ).astype(np.int8)

    master.save(ldd_snapped, out_path, "int8", -1)
    log(f"  LDD snapped | pit cells: {int((ldd_snapped==5).sum())}")
    log(f"  → {out_path}")
    return ldd_snapped


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 5a — GRADIENT
# ─────────────────────────────────────────────────────────────────────────────

def compute_gradient_snapped(dem_snapped, master):
    """
    Slope in m/m computed from the snapped DEM.
    Since dem_snapped is already on the master grid, the output
    is automatically aligned — no reprojection needed.
    """
    log("STEP 5 — Computing gradient", "STEP")
    out_path = os.path.join(OUTPUT_DIR, "maps", "gradient.tif")
    dem_work = dem_snapped.copy().astype(np.float32)
    dem_work[dem_work <= -9000] = np.nan
    safe     = np.where(np.isnan(dem_work), 0, dem_work)
    dz_dx    = np.gradient(safe, RESOLUTION_M, axis=1)
    dz_dy    = np.gradient(safe, RESOLUTION_M, axis=0)
    gradient = np.sqrt(dz_dx**2 + dz_dy**2).astype(np.float32)
    gradient = np.where(
        (gradient <= 0) | np.isnan(gradient) | (dem_snapped <= -9000),
        1e-5, gradient
    ).astype(np.float32)
    master.save(gradient, out_path, "float32", -9999)
    valid = gradient[dem_snapped > -9000]
    log(f"  Gradient range: {valid.min():.6f} – {valid.max():.5f} m/m")
    log(f"  → {out_path}")
    return gradient


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 5b — ELEVATION STD DEV
# ─────────────────────────────────────────────────────────────────────────────

def compute_elvstd_snapped(dem_snapped, master):
    """
    Terrain roughness (std dev of elevation in 3×3 window).
    Already on master grid — no reprojection needed.
    """
    log("STEP 6 — Computing elevation std deviation", "STEP")
    from scipy.ndimage import generic_filter
    out_path = os.path.join(OUTPUT_DIR, "maps", "elvstd.tif")
    dem_work = dem_snapped.copy().astype(np.float32)
    dem_work[dem_work <= -9000] = np.nan
    elvstd   = generic_filter(
        np.where(np.isnan(dem_work), 0, dem_work),
        np.std, size=3, mode="nearest"
    ).astype(np.float32)
    elvstd = np.where(
        (elvstd < 0) | (dem_snapped <= -9000), -9999, elvstd
    ).astype(np.float32)
    master.save(elvstd, out_path, "float32", -9999)
    valid = elvstd[elvstd > -9000]
    log(f"  Elvstd range: {valid.min():.3f} – {valid.max():.2f} m")
    log(f"  → {out_path}")
    return elvstd


# ─────────────────────────────────────────────────────────────────────────────
#   APPLY MASK + VERIFY ALIGNMENT + SAVE FINAL OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

def apply_mask_and_save(mask_arr, ldd, gradient, elvstd, master):
    """
    1. Verifies all arrays are exactly the same shape (alignment check)
    2. Sets cells outside the watershed to nodata
    3. Saves final .tif files (all using master.profile — identical metadata)
    4. Converts to PCRaster .map format
    5. Prints alignment proof (each file's origin, size, pixel size)
    """
    log("Applying mask, verifying alignment, saving final outputs", "STEP")
    import rasterio

    # ── ALIGNMENT CHECK ────────────────────────────────────────────────────────
    # All arrays must be exactly (master.height × master.width).
    # If any differ → something went wrong in reprojection → crash loudly here
    # rather than silently produce wrong LISFLOOD output.
    expected = (master.height, master.width)
    errors   = []
    for name, arr in [("mask", mask_arr), ("ldd", ldd),
                      ("gradient", gradient), ("elvstd", elvstd)]:
        if arr.shape != expected:
            errors.append(f"    {name}: {arr.shape} ≠ {expected}")
    if errors:
        print("\n  ✘  ALIGNMENT FAILURE — shapes do not match master grid:")
        for e in errors:
            print(e)
        sys.exit(1)
    log("  ✔  All arrays match master grid shape")

    # ── Apply mask ─────────────────────────────────────────────────────────────
    ldd_out  = np.where(mask_arr == 1, ldd,      np.int8(-1))
    grad_out = np.where(mask_arr == 1, gradient, np.float32(-9999))
    elvs_out = np.where(mask_arr == 1, elvstd,   np.float32(-9999))

    configs = {
        "area":     (mask_arr, "int8",    0),
        "ldd":      (ldd_out,  "int8",    -1),
        "gradient": (grad_out, "float32", -9999),
        "elvstd":   (elvs_out, "float32", -9999),
    }

    tif_paths = {}
    map_paths = {}

    for name, (arr, dtype, nodata) in configs.items():
        tif_path = os.path.join(OUTPUT_DIR, "maps", f"{name}.tif")
        map_path = os.path.join(OUTPUT_DIR, "maps", f"{name}.map")
        master.save(arr, tif_path, dtype, nodata)
        tif_paths[name] = tif_path
        if _gdal_convert(tif_path, map_path):
            map_paths[name] = map_path
            log(f"  {name}.tif + {name}.map  ✔")
        else:
            log(f"  {name}.tif  ✔   (run manual_convert.sh for .map)", "WARN")

    _write_convert_script(tif_paths)

    # ── ALIGNMENT PROOF ────────────────────────────────────────────────────────
    # Open each saved file and print its metadata so you can confirm
    # every file has byte-identical spatial properties.
    print("\n  ALIGNMENT PROOF — every file must show identical values:")
    print(f"  {'File':<16} {'Origin (E, N)':<30} {'Size':<16} {'Pixel'}")
    print(f"  {'-'*16} {'-'*30} {'-'*16} {'-'*8}")
    for name, path in tif_paths.items():
        with rasterio.open(path) as src:
            t = src.transform
            print(f"  {name+'.tif':<16} "
                  f"({t.c:.2f}, {t.f:.2f}){'':<12} "
                  f"{src.width}×{src.height}{'':<6} "
                  f"{t.a:.1f}m")

    return tif_paths, map_paths, ldd_out, grad_out, elvs_out


def _gdal_convert(tif, mapfile):
    try:
        r = subprocess.run(
            ["gdal_translate", "-of", "PCRaster", tif, mapfile],
            capture_output=True, text=True, timeout=60
        )
        return r.returncode == 0 and os.path.exists(mapfile)
    except Exception:
        return False


def _write_convert_script(tif_paths):
    path = os.path.join(OUTPUT_DIR, "manual_convert.sh")
    with open(path, "w") as f:
        f.write("#!/bin/bash\n# sudo apt install gdal-bin\n\n")
        for name, tif in tif_paths.items():
            f.write(f"gdal_translate -of PCRaster {tif} "
                    f"{tif.replace('.tif','.map')}\n")


# ─────────────────────────────────────────────────────────────────────────────
#   VISUAL CHECK
# ─────────────────────────────────────────────────────────────────────────────

def visualize(mask, dem, ldd, gradient, elvstd, area_ha, master):
    log("Creating VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 3, figsize=(18, 11))
        fig.suptitle(
            f"LISFLOOD Maps — Araria District, Bihar\n"
            f"Watershed: {area_ha:.0f} ha  |  {RESOLUTION_M}m  |  {TARGET_CRS}\n"
            f"All files: origin=({master.transform.c:.1f}, {master.transform.f:.1f})  "
            f"{master.width}×{master.height} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, nodata=-9000):
            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan
            im = ax.imshow(d, cmap=cmap, interpolation="nearest")
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        dem_m = dem.copy(); dem_m[mask == 0] = np.nan
        panel(axes[0,0], dem_m,  "DEM\n(metres)",           "terrain",  "m")
        axes[0,1].imshow(mask, cmap="RdYlGn", vmin=0, vmax=1)
        axes[0,1].set_title("area.map\n1=inside  0=outside", fontsize=9, fontweight="bold")
        axes[0,1].axis("off")
        ldd_p = ldd.astype(np.float32); ldd_p[ldd_p < 1] = np.nan
        panel(axes[0,2], ldd_p, "ldd.map\n(1-9 numpad)",    "hsv",      "code", nodata=0)
        panel(axes[1,0], gradient, "gradient.map\n(m/m)",   "YlOrRd",   "m/m")
        elvs_m = elvstd.copy(); elvs_m[mask == 0] = np.nan
        panel(axes[1,1], elvs_m, "elvstd.map\n(roughness)", "magma",    "m")

        ax = axes[1,2]
        ax.axis("off")
        proof = (
            "ALIGNMENT PROOF\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "All 4 files are identical in:\n\n"
            f"  Rows   : {master.height}\n"
            f"  Cols   : {master.width}\n"
            f"  Pixel  : {RESOLUTION_M}m × {RESOLUTION_M}m\n"
            f"  CRS    : {TARGET_CRS}\n"
            f"  Origin :\n"
            f"    E = {master.transform.c:.2f} m\n"
            f"    N = {master.transform.f:.2f} m\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "LISFLOOD .ini settings:\n\n"
            "  MaskMap   = area.map\n"
            "  Ldd       = ldd.map\n"
            "  Grad      = gradient.map\n"
            "  ElevStdev = elvstd.map"
        )
        ax.text(0.05, 0.97, proof, transform=ax.transAxes,
                fontsize=10, va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor="#fffde7", alpha=0.9))

        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  ★ {out}")

    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")


# ─────────────────────────────────────────────────────────────────────────────
#   SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(tif_paths, map_paths, master, area_ha):
    print("\n" + "═" * 62)
    print("  ★  DONE — All LISFLOOD maps perfectly aligned")
    print("═" * 62)
    print(f"\n  Watershed : {area_ha:.0f} ha")
    print(f"  Grid      : {master.width} cols × {master.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS       : {TARGET_CRS}")
    print(f"  Origin    : ({master.transform.c:.2f} m E, {master.transform.f:.2f} m N)\n")
    vars_ = {"area":"MaskMap  ","ldd":"Ldd      ","gradient":"Grad     ","elvstd":"ElevStdev"}
    print("  LISFLOOD .ini:")
    for n, v in vars_.items():
        val = map_paths.get(n, f"⚠ run manual_convert.sh")
        print(f"    {v} = {val}")
    print(f"\n  Output : {OUTPUT_DIR}/maps/")
    print(f"  Check  : {OUTPUT_DIR}/VISUAL_CHECK.png")
    print("═" * 62 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
#   LOAD OWN WATERSHED FILE
# ─────────────────────────────────────────────────────────────────────────────

def load_own_watershed():
    """
    Loads your own shapefile / GeoPackage / GeoJSON as the watershed boundary.

    Handles all common issues automatically:
      • Any CRS          → reprojects to WGS84 for consistency with rest of script
      • MultiPolygon     → dissolves into a single Polygon
      • Multiple features→ takes the first one (or dissolves all, see below)
      • Missing CRS      → warns you and assumes WGS84

    Supported file formats:
      .shp   — ESRI Shapefile (needs .shx, .dbf, .prj files alongside it)
      .gpkg  — GeoPackage    (single file, preferred modern format)
      .geojson — GeoJSON     (single file, works from QGIS exports)
    """
    log("WATERSHED SOURCE: own file", "STEP")

    import geopandas as gpd
    from shapely.ops import unary_union

    path = OWN_WATERSHED_PATH

    # ── Check file exists ──────────────────────────────────────────────────────
    if not os.path.exists(path):
        print(f"\n  ✘  File not found: {path}")
        print(f"     Set OWN_WATERSHED_PATH to your file's full path.\n")
        sys.exit(1)

    log(f"  Loading: {path}")

    # ── Read file ──────────────────────────────────────────────────────────────
    try:
        gdf = gpd.read_file(path)
    except Exception as e:
        print(f"\n  ✘  Could not read file: {e}")
        print(f"     Supported formats: .shp, .gpkg, .geojson\n")
        sys.exit(1)

    log(f"  Features found : {len(gdf)}")
    log(f"  CRS in file    : {gdf.crs}")
    log(f"  Geometry types : {gdf.geom_type.unique().tolist()}")

    # ── Handle missing CRS ─────────────────────────────────────────────────────
    if gdf.crs is None:
        log("  No CRS found in file — assuming WGS84 (EPSG:4326)", "WARN")
        log("  If your file uses a different CRS, set it in QGIS first", "WARN")
        gdf = gdf.set_crs("EPSG:4326")

    # ── Reproject to WGS84 if needed ──────────────────────────────────────────
    if gdf.crs.to_epsg() != 4326:
        log(f"  Reprojecting from {gdf.crs.to_epsg()} → WGS84 ...")
        gdf = gdf.to_crs("EPSG:4326")

    # ── Handle multiple features ───────────────────────────────────────────────
    if len(gdf) > 1:
        log(f"  Multiple features ({len(gdf)}) — dissolving into one polygon ...", "WARN")
        log(f"  (If you only want one specific feature, dissolve in QGIS first)", "WARN")
        dissolved = unary_union(gdf.geometry.values)
        gdf = gpd.GeoDataFrame(geometry=[dissolved], crs="EPSG:4326")

    # ── Handle MultiPolygon ────────────────────────────────────────────────────
    geom = gdf.iloc[0].geometry
    if geom.geom_type == "MultiPolygon":
        log("  MultiPolygon detected — taking convex hull to get single polygon", "WARN")
        geom = geom.convex_hull

    # ── Compute area ───────────────────────────────────────────────────────────
    gdf_utm  = gdf.to_crs(TARGET_CRS)
    area_ha  = gdf_utm.geometry.iloc[0].area / 10_000

    log(f"  ✔  Watershed loaded successfully")
    log(f"     Area     : {area_ha:.1f} ha")
    log(f"     Bounds   : W={geom.bounds[0]:.4f}° E={geom.bounds[2]:.4f}° "
        f"S={geom.bounds[1]:.4f}° N={geom.bounds[3]:.4f}°")

    # ── Save a copy to output folder for reference ─────────────────────────────
    out_gpkg = os.path.join(OUTPUT_DIR, "watershed.gpkg")
    out_shp  = os.path.join(OUTPUT_DIR, "watershed.shp")
    result   = gpd.GeoDataFrame(
        {"area_ha": [area_ha], "source": [f"own_file:{os.path.basename(path)}"]},
        geometry=[geom], crs="EPSG:4326"
    )
    result.to_file(out_gpkg, driver="GPKG")
    result.to_file(out_shp)
    log(f"  Saved copy → {out_gpkg}")

    return geom, area_ha, result


# ─────────────────────────────────────────────────────────────────────────────
#   MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 62)
    print("  LISFLOOD MAP GENERATOR — Araria District, Bihar")
    print(f"  Watershed source : {WATERSHED_SOURCE}")
    print(f"  NASA SRTM 30m  |  {RESOLUTION_M}m  |  {TARGET_CRS}")
    print("═" * 62 + "\n")

    check_imports()
    make_dirs()

    # ── Step 1 — Get watershed polygon ────────────────────────────────────────
    # Three modes: HydroSHEDS auto-download, your own file, or synthetic test
    if WATERSHED_SOURCE == "OWN_FILE":
        watershed_geom, area_ha, watershed_gdf = load_own_watershed()

    elif WATERSHED_SOURCE == "HYDROSHEDS":
        watershed_geom, area_ha, watershed_gdf = get_hydrosheds_watershed()

    elif WATERSHED_SOURCE == "SYNTHETIC":
        log("WATERSHED SOURCE: synthetic test polygon", "STEP")
        gpkg = os.path.join(OUTPUT_DIR, "watershed.gpkg")
        shp  = os.path.join(OUTPUT_DIR, "watershed.shp")
        watershed_geom, area_ha, watershed_gdf = _make_synthetic_watershed(gpkg, shp)

    else:
        print(f"\n  ✘  Unknown WATERSHED_SOURCE: '{WATERSHED_SOURCE}'")
        print("     Must be one of: 'HYDROSHEDS', 'OWN_FILE', 'SYNTHETIC'\n")
        sys.exit(1)

    # 2 — Rasterize → MASTER GRID (all subsequent rasters must match this)
    master, mask_arr = rasterize_watershed(watershed_geom)

    # 3 — SRTM DEM snapped to master grid
    dem_path, dem_snapped = get_dem_snapped(watershed_geom, master)

    # 4 — LDD snapped to master grid (nearest-neighbor — integer codes)
    ldd_arr = compute_ldd_snapped(dem_path, master)

    # 5a — Gradient (dem_snapped already on master grid → no reprojection)
    gradient_arr = compute_gradient_snapped(dem_snapped, master)

    # 5b — Elevation std dev (same — already aligned)
    elvstd_arr = compute_elvstd_snapped(dem_snapped, master)

    # 6 — Verify alignment, apply mask, save .tif + .map
    tif_paths, map_paths, ldd_out, grad_out, elvs_out = apply_mask_and_save(
        mask_arr, ldd_arr, gradient_arr, elvstd_arr, master
    )

    # Visual check
    visualize(mask_arr, dem_snapped, ldd_out, grad_out, elvs_out, area_ha, master)

    print_summary(tif_paths, map_paths, master, area_ha)


if __name__ == "__main__":
    main()