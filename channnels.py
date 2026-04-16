"""
=============================================================================
LISFLOOD CHANNEL GEOMETRY MAP GENERATOR
Derived from NASA SRTM 30m DEM — all outputs pixel-perfect aligned to
the reference DEM master grid (area.tif / area.map).

OUTPUTS  (LISFLOOD Table A12.1 — Channel Geometry):
  chan.map      — Boolean channel network  (0/1)
  changrad.map  — Channel gradient         [m m⁻¹]
  chanman.map   — Manning's roughness      [-]
  chanleng.map  — Channel length           [m]
  chanbw.map    — Channel bottom width     [m]
  chans.map     — Channel side slope       [m m⁻¹]  (dx/dy)
  chanbnkf.map  — Bankfull depth           [m]

ALIGNMENT STRATEGY (replicates topographyMapsScript.py exactly):
  • A MasterGrid object is loaded from area.tif / area.map
  • Every output is forced through master.reproject_array() or master.save()
  • This guarantees identical origin, pixel size, CRS, and dimensions

REQUIREMENTS:
  pip install rasterio numpy scipy pysheds requests geopandas shapely pyproj

RUN:
  python lisflood_channels.py
=============================================================================
"""

import os
import sys
import gzip
import math
import shutil
import subprocess
import numpy as np
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
#   ★  SETTINGS — edit pipeline_config.py to change ROI / CRS / paths
# ─────────────────────────────────────────────────────────────────────────────

import pipeline_config as _cfg

AREA_RASTER_PATH = _cfg.AREA_TIF          # area.tif produced by topographyMapsScript.py
OUTPUT_DIR       = "./lisflood_channels"
TARGET_CRS       = _cfg.resolve_crs()
RESOLUTION_M     = _cfg.RESOLUTION_M

# ── Channel network extraction thresholds ─────────────────────────────────────
# Flow-accumulation threshold: cells above this value become channel pixels.
# Lower  → more channels  |  Higher → fewer, larger channels
# 500–2000 works well for 30 m SRTM; tune to your catchment.
FLOW_ACC_THRESHOLD = 1000               # cells  (= 1000 × 900 m² ≈ 0.9 km²)

# ── Mean annual runoff coefficient for Q_proxy in hydraulic geometry ──────────
# Used to convert flow accumulation → approximate mean annual discharge Q.
# Q ≈ ACC_cells × pixel_area_km² × RUNOFF_COEFF
# Bihar/Araria district: monsoon-dominated (1000–1400 mm/yr rainfall, June–Sept).
# Weighted annual average: 12–20%. 5% is dry-season only and underestimates
# channel dimensions by ~1.7×, causing overflow in flood events.
# Calibrate to observed discharge if gauges are available.
RUNOFF_COEFF = 0.15                     # dimensionless (annual mean for Bihar)

# ── Default Manning's n table by Strahler order ───────────────────────────────
# Order 1 (headwater) → highest roughness; large rivers → lower roughness.
# Source: Chow (1959) + LISFLOOD documentation defaults.
MANNING_BY_ORDER = {
    1: 0.050,   # small headwater streams
    2: 0.045,
    3: 0.040,
    4: 0.035,
    5: 0.035,
    6: 0.030,
    7: 0.025,
    8: 0.025,   # major rivers
}
MANNING_DEFAULT = 0.035                 # fallback for all other orders

# ── Power-law hydraulic geometry coefficients ─────────────────────────────────
# Width  W = a_W × Q^b_W   where Q is mean annual discharge in m³/s.
# Depth  D = a_D × Q^b_D
# Discharge is approximated from flow accumulation:
#   Q_proxy = ACC × pixel_area_km² × 0.05   (simple runoff coefficient, ~5 %)
# Tune a_W, b_W, a_D, b_D for your region.  Defaults from Leopold & Maddock (1953).
HG_a_W = 2.0;  HG_b_W = 0.5   # width  coefficients
HG_a_D = 0.4;  HG_b_D = 0.4   # depth  coefficients

# Channel side slope (dx/dy — LISFLOOD convention: horizontal / vertical).
# Natural channels ~ 1–3; steep/trapezoidal ~ 0.5.
CHAN_SIDE_SLOPE = 1.0                   # [m m⁻¹]

# ─────────────────────────────────────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def log(msg, kind="INFO"):
    icons = {"INFO": "✔", "STEP": "▶", "WARN": "⚠", "ERROR": "✘", "DONE": "★"}
    print(f"  {icons.get(kind, '·')}  {msg}")


def check_imports():
    missing = []
    for pkg in ["rasterio", "numpy", "scipy", "pysheds",
                "requests", "shapely", "pyproj"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"\n  ✘  Missing: {', '.join(missing)}")
        print(f"     Run: pip install {' '.join(missing)}\n")
        sys.exit(1)


def make_dirs():
    for sub in ["", "/raw", "/maps"]:
        Path(OUTPUT_DIR + sub).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#   MASTER GRID  (replicated verbatim from topographyMapsScript.py)
#   One object that every raster in this script snaps to.
# ─────────────────────────────────────────────────────────────────────────────

class MasterGrid:
    """
    Canonical spatial definition.  ALL rasters produced here must share it.

    Loaded from the reference area.tif so origin, pixel size, CRS, and
    dimensions are byte-identical to the DEM outputs.
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
        """Crop or zero-pad array to exactly (height, width)."""
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
        Reprojects src_array ONTO this master grid.
        Forces the output to have the exact same origin, pixel size,
        CRS, and dimensions — guaranteeing alignment.
        """
        from rasterio.warp import reproject, Resampling
        resamp = {"nearest":  Resampling.nearest,
                  "bilinear": Resampling.bilinear}[resampling_method]
        dst = np.full((self.height, self.width), dst_nodata, dtype=src_array.dtype)
        reproject(
            source=src_array,          destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=self.transform, dst_crs=self.crs,
            resampling=resamp,
            src_nodata=src_nodata,     dst_nodata=dst_nodata,
        )
        return dst

    def save(self, array, path, dtype, nodata):
        """Save array as GeoTIFF with master-grid spatial metadata."""
        import rasterio
        p = self.profile.copy()
        p.update({"dtype": dtype, "nodata": nodata})
        with rasterio.open(path, "w", **p) as dst:
            dst.write(self.snap(array, nodata).astype(dtype), 1)

    def __str__(self):
        return (f"MasterGrid | origin=({self.transform.c:.2f}, {self.transform.f:.2f}) | "
                f"{self.width} cols × {self.height} rows | {RESOLUTION_M}m/px | {self.crs}")


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 1 — LOAD MASTER GRID FROM REFERENCE AREA RASTER
# ─────────────────────────────────────────────────────────────────────────────

def load_master_grid(area_path: str) -> MasterGrid:
    """
    Reads the spatial metadata from the reference area.tif (or area.map)
    produced by topographyMapsScript.py and returns a MasterGrid object.

    This is the ONLY source of truth for CRS, transform, and grid dimensions.
    All channel outputs will be forced to match this exactly.
    """
    log(f"STEP 1 — Loading master grid from: {area_path}", "STEP")

    import rasterio
    from rasterio.crs import CRS

    if not os.path.exists(area_path):
        # Try .map extension as fallback
        alt = area_path.replace(".tif", ".map")
        if os.path.exists(alt):
            area_path = alt
        else:
            print(f"\n  ✘  Reference raster not found: {area_path}")
            print(f"     Run topographyMapsScript.py first, or set AREA_RASTER_PATH.\n")
            sys.exit(1)

    with rasterio.open(area_path) as src:
        t      = src.transform
        width  = src.width
        height = src.height
        crs    = src.crs if src.crs else CRS.from_epsg(int(TARGET_CRS.split(":")[1]))
        mask   = src.read(1)

    master = MasterGrid(t, width, height, crs)
    log(f"  {master}")
    log(f"  Mask cells (inside watershed): {int((mask > 0).sum()):,}")
    return master, mask


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 2 — DOWNLOAD NASA SRTM 30m AND SNAP TO MASTER GRID
#   (Same approach as topographyMapsScript.py — we need DEM for LDD/gradient)
# ─────────────────────────────────────────────────────────────────────────────

def get_dem_snapped(master: MasterGrid) -> tuple:
    """
    Returns the SRTM 30m DEM snapped to the master grid.

    Priority:
      1. Reuse dem_snapped.tif already produced by topographyMapsScript.py —
         avoids a redundant SRTM download and guarantees identical pixel values.
      2. Use a locally cached copy in this script's own raw/ folder.
      3. Download SRTM tiles fresh (fallback if neither exists).
    """
    import requests
    import rasterio
    from rasterio.merge import merge
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS

    log("STEP 2 — Loading / downloading SRTM DEM", "STEP")

    # ── Priority 1: reuse topo script output ──────────────────────────────────
    topo_dem = os.path.join(_cfg.OUTPUT_TOPO, "raw", "dem_snapped.tif")
    if os.path.exists(topo_dem):
        log(f"  Reusing DEM from topographyMapsScript: {topo_dem}")
        import rasterio as _rio
        with _rio.open(topo_dem) as src:
            return topo_dem, src.read(1)

    # ── Priority 2: local cached copy ────────────────────────────────────────
    raw_dir = os.path.join(OUTPUT_DIR, "raw")
    dem_out = os.path.join(raw_dir, "dem_snapped.tif")

    if os.path.exists(dem_out):
        log("  Snapped DEM already exists locally, loading.")
        with rasterio.open(dem_out) as src:
            return dem_out, src.read(1)

    # Derive WGS84 bounding box from master grid transform
    t        = master.transform
    west_m   = t.c
    north_m  = t.f
    east_m   = west_m  + master.width  * RESOLUTION_M
    south_m  = north_m - master.height * RESOLUTION_M

    # Reproject corners from UTM to WGS84
    import pyproj
    proj_inv = pyproj.Transformer.from_crs(
        TARGET_CRS, "EPSG:4326", always_xy=True
    )
    west_d, south_d = proj_inv.transform(west_m  - 5000, south_m - 5000)
    east_d, north_d = proj_inv.transform(east_m  + 5000, north_m + 5000)

    tile_lats = range(int(math.floor(south_d)), int(math.ceil(north_d)))
    tile_lons = range(int(math.floor(west_d)),  int(math.ceil(east_d)))

    tile_paths = []
    for lat in tile_lats:
        for lon in tile_lons:
            p = _download_srtm_tile(lat, lon, raw_dir)
            if p:
                tile_paths.append(p)

    if not tile_paths:
        log("  No tiles downloaded — using synthetic DEM", "WARN")
        return _synthetic_dem(master, dem_out)

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
    log(f"  DEM snapped: {master.width}×{master.height} | "
        f"elev {valid.min():.1f}–{valid.max():.1f} m → {dem_out}")
    return dem_out, dem_snapped


def _download_srtm_tile(lat: int, lon: int, raw_dir: str):
    import requests
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS

    lat_s = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
    lon_s = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
    name  = f"{lat_s}{lon_s}"
    tif   = os.path.join(raw_dir, f"srtm_{name}.tif")
    if os.path.exists(tif):
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
    t = from_bounds(lon, lat, lon + 1, lat + 1, size, size)
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


def _synthetic_dem(master: MasterGrid, out_path: str):
    """Fallback synthetic DEM for offline testing."""
    h, w = master.height, master.width
    np.random.seed(42)
    row_idx = np.arange(h).reshape(-1, 1)
    dem = (65 + 5 * (h - row_idx) / h
           + np.random.normal(0, 0.5, (h, w))).astype(np.float32)
    master.save(dem, out_path, "float32", -9999)
    log("  ★ Synthetic DEM generated (offline fallback)", "WARN")
    return out_path, dem


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 3 — COMPUTE FLOW DIRECTION + FLOW ACCUMULATION (pysheds)
# ─────────────────────────────────────────────────────────────────────────────

def compute_flow_products(dem_path: str, master: MasterGrid) -> tuple:
    """
    Uses pysheds to:
      1. Fill pits and depressions
      2. Compute flow direction (D8)
      3. Compute flow accumulation

    All outputs are reprojected onto the master grid (nearest-neighbor).
    Returns:
      fdir_snap  — D8 flow direction array (pysheds dirmap codes)
      facc_snap  — flow accumulation array (cells)
      order_snap — Strahler order array
    """
    log("STEP 3 — Computing flow direction and accumulation (pysheds)", "STEP")

    import rasterio
    try:
        from pysheds.grid import Grid
    except ImportError:
        log("Install pysheds: pip install pysheds", "ERROR")
        sys.exit(1)

    fdir_out  = os.path.join(OUTPUT_DIR, "raw", "fdir_snapped.tif")
    facc_out  = os.path.join(OUTPUT_DIR, "raw", "facc_snapped.tif")
    order_out = os.path.join(OUTPUT_DIR, "raw", "order_snapped.tif")

    if all(os.path.exists(p) for p in [fdir_out, facc_out, order_out]):
        log("  Flow products already exist, loading.")
        with rasterio.open(fdir_out)  as f: fdir  = f.read(1)
        with rasterio.open(facc_out)  as f: facc  = f.read(1)
        with rasterio.open(order_out) as f: order = f.read(1)
        return fdir, facc, order

    grid   = Grid.from_raster(dem_path)
    dem    = grid.read_raster(dem_path)
    filled = grid.fill_pits(dem)
    log("  Pits filled")
    filled = grid.fill_depressions(filled)
    log("  Depressions filled")
    filled = grid.resolve_flats(filled)
    log("  Flat areas resolved")

    dirmap = (64, 128, 1, 2, 4, 8, 16, 32)   # pysheds convention
    fdir   = grid.flowdir(filled, dirmap=dirmap)
    facc   = grid.accumulation(fdir, dirmap=dirmap)
    log(f"  Flow accumulation max: {np.array(facc).max():,.0f} cells")

    # ── Strahler order (approximate, from pysheds if available) ───────────────
    try:
        order = grid.stream_order(fdir, facc > FLOW_ACC_THRESHOLD, dirmap=dirmap)
        log("  Strahler order computed")
    except Exception:
        # Older pysheds versions may lack stream_order — compute manually
        order = _strahler_from_acc(np.array(facc))
        log("  Strahler order (from accumulation, fallback)")

    fdir_arr  = np.array(fdir).astype(np.int16)
    facc_arr  = np.array(facc).astype(np.float32)
    order_arr = np.array(order).astype(np.int8)

    # Snap all three to master grid
    with rasterio.open(dem_path) as src:
        t, crs = src.transform, src.crs
        fdir_snap  = master.reproject_array(
            fdir_arr.astype(np.float32), t, crs, -1, -1, "nearest").astype(np.int16)
        facc_snap  = master.reproject_array(
            facc_arr, t, crs, -1, -1, "nearest").astype(np.float32)
        order_snap = master.reproject_array(
            order_arr.astype(np.float32), t, crs, 0, 0, "nearest").astype(np.int8)

    master.save(fdir_snap.astype(np.float32),  fdir_out,  "float32", -1)
    master.save(facc_snap,                      facc_out,  "float32", -1)
    master.save(order_snap.astype(np.float32),  order_out, "float32",  0)

    log(f"  Flow products snapped → {OUTPUT_DIR}/raw/")
    return fdir_snap, facc_snap, order_snap


def _strahler_from_acc(facc_arr: np.ndarray) -> np.ndarray:
    """Approximate Strahler order from flow accumulation using log2 bins."""
    order = np.zeros_like(facc_arr, dtype=np.int8)
    bins  = [FLOW_ACC_THRESHOLD * 2**i for i in range(8)]
    for i, threshold in enumerate(bins):
        order[facc_arr >= threshold] = i + 1
    return order


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4a — chan.map  (Boolean channel network)
# ─────────────────────────────────────────────────────────────────────────────

def make_chan(facc_snap: np.ndarray, mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chan.map: Boolean 1 for channel pixels, 0 elsewhere.
    Channel pixels = those with flow accumulation >= FLOW_ACC_THRESHOLD.
    Restricted to MaskMap domain.

    LISFLOOD requirement: U.[-]  R.: 0 or 1  (Boolean PCRaster)
    """
    log("STEP 4a — Building channel network (chan.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chan.tif")

    chan = np.where(
        (facc_snap >= FLOW_ACC_THRESHOLD) & (mask > 0),
        np.int8(1), np.int8(0)
    )
    master.save(chan, out_tif, "int8", 0)
    n_chan = int(chan.sum())
    area_km2 = n_chan * RESOLUTION_M**2 / 1e6
    log(f"  Channel pixels: {n_chan:,}  ({area_km2:.2f} km²) → {out_tif}")
    return chan


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4b — changrad.map  (Channel gradient [m m⁻¹])
# ─────────────────────────────────────────────────────────────────────────────

def make_changrad(dem_snapped: np.ndarray, fdir_snap: np.ndarray, chan: np.ndarray,
                  mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    changrad.map: True downstream slope of channel cells bed.
    Calculated explicitly by dropping elevation to the D8 downstream connected cell.
    
    LISFLOOD requirement: U.[m m⁻¹]  R.: map > 0 !!!
    Zero or negative values must NOT appear — minimum enforced at 1e-5.
    """
    log("STEP 4b — Computing channel gradient (changrad.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "changrad.tif")

    dem = dem_snapped.copy().astype(np.float32)
    dem[dem <= -9000] = np.nan
    grad = np.zeros_like(dem)

    # pysheds dirmap = (64, 128, 1, 2, 4, 8, 16, 32) maps to (E, NE, N, NW, W, SW, S, SE).
    # Confirmed from topographyMapsScript.py code_map: 64→PCR6(E), 1→PCR8(N), 4→PCR4(W), 16→PCR2(S),
    #   128→PCR9(NE), 2→PCR7(NW), 8→PCR1(SW), 32→PCR3(SE).
    #
    # padded has a 1-cell border, so the current cell at (row, col) in dem
    # is at (row+1, col+1) in padded. Its downstream neighbour is:
    #   E  (64):  same row, col+1 → padded[row+1, col+2] → slice [1:r+1, 2:c+2]
    #   N  ( 1):  row-1,  col    → padded[row,   col+1] → slice [0:r,   1:c+1]
    #   W  ( 4):  same row, col-1 → padded[row+1, col  ] → slice [1:r+1, 0:c  ]
    #   S  (16):  row+1,  col    → padded[row+2, col+1] → slice [2:r+2, 1:c+1]
    #   NE (128): row-1,  col+1  → padded[row,   col+2] → slice [0:r,   2:c+2]
    #   NW (  2): row-1,  col-1  → padded[row,   col  ] → slice [0:r,   0:c  ]
    #   SW (  8): row+1,  col-1  → padded[row+2, col  ] → slice [2:r+2, 0:c  ]
    #   SE ( 32): row+1,  col+1  → padded[row+2, col+2] → slice [2:r+2, 2:c+2]
    padded = np.pad(dem, 1, mode='constant', constant_values=np.nan)
    r, c = dem.shape

    c_len = float(RESOLUTION_M)
    d_len = c_len * math.sqrt(2.0)

    dirs = {
        64:  (padded[1:r+1, 2:c+2], c_len),   # E
        1:   (padded[0:r,   1:c+1], c_len),   # N
        4:   (padded[1:r+1, 0:c],   c_len),   # W
        16:  (padded[2:r+2, 1:c+1], c_len),   # S
        128: (padded[0:r,   2:c+2], d_len),   # NE
        2:   (padded[0:r,   0:c],   d_len),   # NW
        8:   (padded[2:r+2, 0:c],   d_len),   # SW
        32:  (padded[2:r+2, 2:c+2], d_len),   # SE
    }
    
    for code, (down_dem, length) in dirs.items():
        mask_dir = (fdir_snap == code)
        # Gradient = (Z_current - Z_downstream) / length
        grad[mask_dir] = (dem[mask_dir] - down_dem[mask_dir]) / length

    # LISFLOOD requires > 0 everywhere in channels. 
    # If pits were overfilled or cell drops into NaN (e.g. edge), clamp to 1e-5
    grad = np.where(
        (grad <= 0) | np.isnan(grad) | (dem_snapped <= -9000),
        1e-5, grad
    ).astype(np.float32)

    # Apply only to channel + mask cells; nodata elsewhere
    changrad = np.where(
        (chan == 1) & (mask > 0),
        grad,
        np.float32(-9999)
    )
    master.save(changrad, out_tif, "float32", -9999)
    valid = changrad[changrad > -9000]
    log(f"  ChanGrad range: {valid.min():.6f} – {valid.max():.5f} m/m → {out_tif}")
    return changrad


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4c — chanman.map  (Manning's roughness coefficient [-])
# ─────────────────────────────────────────────────────────────────────────────

def make_chanman(order_snap: np.ndarray, chan: np.ndarray,
                 mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chanman.map: Manning's n assigned by Strahler order.
    Headwater channels get higher roughness; large rivers get lower.
    MANNING_BY_ORDER table at top of script controls this assignment.

    LISFLOOD requirement: U.[-]  R.: map > 0
    """
    log("STEP 4c — Assigning Manning's roughness (chanman.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chanman.tif")

    chanman = np.full_like(order_snap, MANNING_DEFAULT, dtype=np.float32)
    for order_val, n_val in MANNING_BY_ORDER.items():
        chanman[order_snap == order_val] = n_val

    # Apply only to channel + mask cells
    chanman = np.where(
        (chan == 1) & (mask > 0),
        chanman,
        np.float32(-9999)
    )
    master.save(chanman, out_tif, "float32", -9999)
    valid = chanman[chanman > -9000]
    log(f"  ChanMan range: {valid.min():.4f} – {valid.max():.4f} → {out_tif}")
    return chanman


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4d — chanleng.map  (Channel length [m])
# ─────────────────────────────────────────────────────────────────────────────

def make_chanleng(fdir_snap: np.ndarray, chan: np.ndarray,
                  mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chanleng.map: Length of channel within each pixel [m].

    For straight (cardinal) flow: length = RESOLUTION_M (30 m).
    For diagonal flow:            length = RESOLUTION_M × √2 ≈ 42.43 m.
    This accounts for sinuosity within the pixel and satisfies LISFLOOD's
    note that channel length may exceed grid size.

    pysheds D8 dirmap = (64, 128, 1, 2, 4, 8, 16, 32)
    Cardinal codes:   1, 4, 16, 64  (E, W, S, N respectively? depends on grid)
    Diagonal codes:   2, 8, 32, 128
    Actually in pysheds (64=NW, 128=N, 1=NE, 2=E, 4=SE, 8=S, 16=SW, 32=W)
    Cardinal:  128(N), 8(S), 2(E), 32(W)   → 4 directions
    Diagonal:  64(NW), 1(NE), 4(SE), 16(SW) → 4 directions

    LISFLOOD requirement: U.[m]  R.: map > 0
    """
    log("STEP 4d — Computing channel length (chanleng.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chanleng.tif")

    CARDINAL = {64, 1, 4, 16}         # N, E, S, W
    DIAGONAL = {128, 2, 8, 32}        # NE, SE, SW, NW

    diag_len     = float(RESOLUTION_M) * math.sqrt(2.0)
    chanleng     = np.full((master.height, master.width), np.float32(RESOLUTION_M), dtype=np.float32)
    fdir_codes   = fdir_snap.astype(np.int32)
    diagonal_mask = np.isin(fdir_codes, list(DIAGONAL))
    chanleng[diagonal_mask] = diag_len

    # Apply only to channel pixels
    chanleng = np.where(
        (chan == 1) & (mask > 0),
        chanleng,
        np.float32(-9999)
    )
    master.save(chanleng, out_tif, "float32", -9999)
    valid = chanleng[chanleng > 0]
    log(f"  ChanLength range: {valid.min():.2f} – {valid.max():.2f} m → {out_tif}")
    return chanleng


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4e — chanbw.map  (Channel bottom width [m])
# ─────────────────────────────────────────────────────────────────────────────

def make_chanbw(facc_snap: np.ndarray, chan: np.ndarray,
                mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chanbw.map: Channel bottom width [m] derived via hydraulic geometry.

    Method:
      1. Approximate mean annual discharge Q from flow accumulation:
           Q ≈ ACC_cells × pixel_area_km² × 0.05  (5% runoff coefficient)
      2. Apply power law:  W = a_W × Q^b_W
      3. Clip to minimum 1.0 m (LISFLOOD requires > 0)

    HG_a_W, HG_b_W are tunable at top of script.

    LISFLOOD requirement: U.[m]  R.: map > 0
    """
    log("STEP 4e — Estimating channel bottom width (chanbw.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chanbw.tif")

    pixel_area_km2 = (RESOLUTION_M ** 2) / 1e6
    Q_proxy = np.clip(facc_snap.astype(np.float64) * pixel_area_km2 * RUNOFF_COEFF, 0.01, None)
    width   = (HG_a_W * Q_proxy ** HG_b_W).astype(np.float32)
    width   = np.clip(width, 1.0, None)   # must be > 0

    chanbw = np.where(
        (chan == 1) & (mask > 0),
        width,
        np.float32(-9999)
    )
    master.save(chanbw, out_tif, "float32", -9999)
    valid = chanbw[chanbw > 0]
    log(f"  ChanBW range: {valid.min():.2f} – {valid.max():.2f} m → {out_tif}")
    return chanbw


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4f — chans.map  (Channel side slope [m m⁻¹] — dx/dy convention)
# ─────────────────────────────────────────────────────────────────────────────

def make_chans(chan: np.ndarray, mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chans.map: Channel side slope in LISFLOOD's dx/dy convention
    (horizontal distance / vertical distance).

    ⚠  Note: LISFLOOD defines this as dx/dy (horizontal/vertical),
    which is the RECIPROCAL of the usual dy/dx definition.
    A rectangular channel has chans = 0.

    CHAN_SIDE_SLOPE at top of script controls this value (default 1.0).

    LISFLOOD requirement: U.[m m⁻¹]  R.: map ≥ 0
    """
    log("STEP 4f — Assigning channel side slope (chans.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chans.tif")

    chans = np.where(
        (chan == 1) & (mask > 0),
        np.float32(CHAN_SIDE_SLOPE),
        np.float32(-9999)
    )
    master.save(chans, out_tif, "float32", -9999)
    log(f"  ChanSdXdY: {CHAN_SIDE_SLOPE} m/m (uniform) → {out_tif}")
    return chans


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 4g — chanbnkf.map  (Bankfull depth [m])
# ─────────────────────────────────────────────────────────────────────────────

def make_chanbnkf(facc_snap: np.ndarray, chan: np.ndarray,
                  mask: np.ndarray, master: MasterGrid) -> np.ndarray:
    """
    chanbnkf.map: Bankfull channel depth [m] derived via hydraulic geometry.

    Method:
      1. Same Q proxy as chanbw.map
      2. Apply power law:  D = a_D × Q^b_D
      3. Clip to minimum 0.1 m (LISFLOOD requires > 0)

    HG_a_D, HG_b_D are tunable at top of script.

    LISFLOOD requirement: U.[m]  R.: map > 0
    """
    log("STEP 4g — Estimating bankfull depth (chanbnkf.map)", "STEP")

    out_tif = os.path.join(OUTPUT_DIR, "maps", "chanbnkf.tif")

    pixel_area_km2 = (RESOLUTION_M ** 2) / 1e6
    Q_proxy = np.clip(facc_snap.astype(np.float64) * pixel_area_km2 * RUNOFF_COEFF, 0.01, None)
    depth   = (HG_a_D * Q_proxy ** HG_b_D).astype(np.float32)
    depth   = np.clip(depth, 0.1, None)   # must be > 0

    chanbnkf = np.where(
        (chan == 1) & (mask > 0),
        depth,
        np.float32(-9999)
    )
    master.save(chanbnkf, out_tif, "float32", -9999)
    valid = chanbnkf[chanbnkf > 0]
    log(f"  ChanBnkf range: {valid.min():.3f} – {valid.max():.3f} m → {out_tif}")
    return chanbnkf


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 5 — CONVERT ALL GEOTIFFS TO PCRASTER .map
# ─────────────────────────────────────────────────────────────────────────────

def convert_to_pcraster(tif_paths: dict) -> dict:
    """
    Converts each output GeoTIFF to PCRaster .map format using
    gdal_translate -of PCRaster — identical approach as topographyMapsScript.py.

    PCRaster data-type mapping:
      chan      → Boolean  (PCRASTER_VALUESCALE=VS_BOOLEAN)
      changrad  → Scalar   (VS_SCALAR)
      chanman   → Scalar
      chanleng  → Scalar
      chanbw    → Scalar
      chans     → Scalar
      chanbnkf  → Scalar

    If gdal_translate is not on PATH, writes a manual_convert.sh helper.
    """
    log("STEP 5 — Converting GeoTIFFs → PCRaster .map", "STEP")

    map_paths = {}

    # PCRaster value-scale metadata for each layer
    VSTYPE = {
        "chan":     "VS_BOOLEAN",
        "changrad": "VS_SCALAR",
        "chanman":  "VS_SCALAR",
        "chanleng": "VS_SCALAR",
        "chanbw":   "VS_SCALAR",
        "chans":    "VS_SCALAR",
        "chanbnkf": "VS_SCALAR",
    }

    for name, tif_path in tif_paths.items():
        map_path = tif_path.replace(".tif", ".map")
        vs       = VSTYPE.get(name, "VS_SCALAR")
        if _gdal_convert(tif_path, map_path, vs):
            map_paths[name] = map_path
            log(f"  {name}.map  ✔")
        else:
            log(f"  {name}.tif  ✔   (.map conversion failed — see manual_convert.sh)", "WARN")

    _write_convert_script(tif_paths, VSTYPE)
    return map_paths


def _gdal_convert(tif: str, mapfile: str, vs: str = "VS_SCALAR") -> bool:
    """
    Calls gdal_translate with PCRaster output format and value-scale metadata.
    Replicates the _gdal_convert helper from topographyMapsScript.py,
    with the addition of -mo PCRASTER_VALUESCALE=<vs> for correct PCRaster typing.
    """
    try:
        cmd = [
            "gdal_translate",
            "-of", "PCRaster",
            "-mo", f"PCRASTER_VALUESCALE={vs}",
            tif, mapfile
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0 and os.path.exists(mapfile)
    except Exception:
        return False


def _write_convert_script(tif_paths: dict, vstype: dict):
    sh_path = os.path.join(OUTPUT_DIR, "manual_convert.sh")
    with open(sh_path, "w") as f:
        f.write("#!/bin/bash\n# Run this if gdal_translate was not found during script execution\n")
        f.write("# sudo apt install gdal-bin\n\n")
        for name, tif in tif_paths.items():
            vs  = vstype.get(name, "VS_SCALAR")
            out = tif.replace(".tif", ".map")
            f.write(f"gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE={vs} {tif} {out}\n")
    log(f"  manual_convert.sh written → {sh_path}")


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 6 — VALIDATION  (MANDATORY)
# ─────────────────────────────────────────────────────────────────────────────

def validate_alignment(tif_paths: dict, master: MasterGrid):
    """
    Opens every output GeoTIFF and verifies:
      ✔ CRS     matches master
      ✔ Width   matches master
      ✔ Height  matches master
      ✔ Origin  matches master  (transform.c, transform.f)
      ✔ Pixel   matches master  (transform.a == RESOLUTION_M)
      ✔ No zero or negative values in channel cells (where applicable)

    Prints an alignment proof table.  Exits with error if any mismatch found.
    """
    log("STEP 6 — Validating alignment of all outputs", "STEP")

    import rasterio

    TOLERANCE = 0.01   # metres — acceptable rounding difference in origin

    print("\n  ALIGNMENT PROOF — every file must show identical values:")
    print(f"  {'File':<20} {'Origin (E, N)':<32} {'Size':<14} {'Pixel':<8} {'CRS'}")
    print(f"  {'-'*20} {'-'*32} {'-'*14} {'-'*8} {'-'*20}")

    errors = []

    for name, path in tif_paths.items():
        if not os.path.exists(path):
            errors.append(f"    {name}: file missing")
            continue
        with rasterio.open(path) as src:
            t     = src.transform
            crs   = str(src.crs)
            w, h  = src.width, src.height

            print(f"  {name+'.tif':<20} "
                  f"({t.c:.2f}, {t.f:.2f}){'':<14} "
                  f"{w}×{h}{'':<6} "
                  f"{t.a:.1f}m    "
                  f"{crs}")

            # CRS check
            if src.crs and src.crs.to_epsg() != int(TARGET_CRS.split(":")[1]):
                errors.append(f"    {name}: CRS {src.crs} ≠ {TARGET_CRS}")

            # Dimension check
            if w != master.width or h != master.height:
                errors.append(f"    {name}: size {w}×{h} ≠ {master.width}×{master.height}")

            # Origin check (within tolerance)
            if abs(t.c - master.transform.c) > TOLERANCE:
                errors.append(f"    {name}: origin_x {t.c:.4f} ≠ {master.transform.c:.4f}")
            if abs(t.f - master.transform.f) > TOLERANCE:
                errors.append(f"    {name}: origin_y {t.f:.4f} ≠ {master.transform.f:.4f}")

            # Pixel size check
            if abs(abs(t.a) - RESOLUTION_M) > 0.01:
                errors.append(f"    {name}: pixel_x {abs(t.a):.2f} ≠ {RESOLUTION_M}")

    print()
    if errors:
        print("  ✘  ALIGNMENT FAILURES:")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        log("  All outputs pass alignment check  ✔  ★", "DONE")

    # ── Range / LISFLOOD constraint checks ────────────────────────────────────
    print("\n  LISFLOOD CONSTRAINT CHECKS:")
    constraints = {
        "chan.tif":     ("== 0 or 1 (Boolean)",  lambda d: np.all(np.isin(d[(d != 0)], [1]))),
        "changrad.tif": ("> 0 everywhere",        lambda d: np.all(d[d > -9000] > 0)),
        "chanman.tif":  ("> 0 everywhere",        lambda d: np.all(d[d > -9000] > 0)),
        "chanleng.tif": ("> 0 everywhere",        lambda d: np.all(d[d > -9000] > 0)),
        "chanbw.tif":   ("> 0 everywhere",        lambda d: np.all(d[d > -9000] > 0)),
        "chans.tif":    (">= 0 everywhere",       lambda d: np.all(d[d > -9000] >= 0)),
        "chanbnkf.tif": ("> 0 everywhere",        lambda d: np.all(d[d > -9000] > 0)),
    }
    all_ok = True
    for fname, (label, check_fn) in constraints.items():
        fpath = os.path.join(OUTPUT_DIR, "maps", fname)
        if not os.path.exists(fpath):
            print(f"  ⚠  {fname}: not found (skipping)")
            continue
        import rasterio
        with rasterio.open(fpath) as src:
            data = src.read(1)
        ok = check_fn(data)
        status = "✔" if ok else "✘"
        print(f"  {status}  {fname:<20} {label}")
        if not ok:
            all_ok = False

    if not all_ok:
        print("\n  ✘  Some LISFLOOD constraints violated — review outputs above.")
        sys.exit(1)
    else:
        print("\n  ★  All LISFLOOD constraints satisfied.")


# ─────────────────────────────────────────────────────────────────────────────
#   STEP 7 — VISUALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def visualize(chan, changrad, chanbw, chanbnkf, chanman, mask, master):
    """Creates a visual summary PNG of all channel outputs."""
    log("STEP 7 — Creating CHANNEL_VISUAL_CHECK.png", "STEP")
    try:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 3, figsize=(18, 11))
        fig.suptitle(
            f"LISFLOOD Channel Maps — {RESOLUTION_M}m  |  {TARGET_CRS}\n"
            f"Grid: origin=({master.transform.c:.1f}, {master.transform.f:.1f})  "
            f"{master.width}×{master.height} cells  |  "
            f"Flow-acc threshold: {FLOW_ACC_THRESHOLD:,} cells",
            fontsize=10, fontweight="bold"
        )

        def panel(ax, data, title, cmap, label, nodata=-9000):
            d = data.astype(np.float32).copy()
            d[d <= nodata] = np.nan
            im = ax.imshow(d, cmap=cmap, interpolation="nearest")
            plt.colorbar(im, ax=ax, label=label, shrink=0.85)
            ax.set_title(title, fontsize=9, fontweight="bold")
            ax.axis("off")

        panel(axes[0, 0], chan,     "chan.map\n(Boolean)", "Blues",   "0/1",  nodata=-0.5)
        panel(axes[0, 1], changrad, "changrad.map\n[m/m]", "YlOrRd", "m/m")
        panel(axes[0, 2], chanbw,   "chanbw.map\n[m]",    "GnBu",   "m")
        panel(axes[1, 0], chanbnkf, "chanbnkf.map\n[m]",  "PuBu",   "m")
        panel(axes[1, 1], chanman,  "chanman.map\n[-]",   "RdYlGn", "n")

        ax = axes[1, 2]
        ax.axis("off")
        proof = (
            "ALIGNMENT PROOF\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "All 7 files share:\n\n"
            f"  Rows   : {master.height}\n"
            f"  Cols   : {master.width}\n"
            f"  Pixel  : {RESOLUTION_M}m × {RESOLUTION_M}m\n"
            f"  CRS    : {TARGET_CRS}\n"
            f"  Origin :\n"
            f"    E = {master.transform.c:.2f} m\n"
            f"    N = {master.transform.f:.2f} m\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "LISFLOOD .ini settings:\n\n"
            "  Channels      = chan.map\n"
            "  ChanGrad      = changrad.map\n"
            "  ChanMan       = chanman.map\n"
            "  ChanLength    = chanleng.map\n"
            "  ChanBottomWidth = chanbw.map\n"
            "  ChanSdXdY     = chans.map\n"
            "  ChanDepthThreshold = chanbnkf.map"
        )
        ax.text(0.05, 0.97, proof, transform=ax.transAxes,
                fontsize=9, va="top", fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor="#e8f4f8", alpha=0.9))

        plt.tight_layout()
        out = os.path.join(OUTPUT_DIR, "CHANNEL_VISUAL_CHECK.png")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close()
        log(f"  ★ {out}")

    except ImportError:
        log("pip install matplotlib to enable visualization", "WARN")


# ─────────────────────────────────────────────────────────────────────────────
#   SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(tif_paths: dict, map_paths: dict, master: MasterGrid):
    print("\n" + "═" * 62)
    print("  ★  DONE — All LISFLOOD Channel Maps perfectly aligned")
    print("═" * 62)
    print(f"\n  Grid    : {master.width} cols × {master.height} rows @ {RESOLUTION_M}m")
    print(f"  CRS     : {TARGET_CRS}")
    print(f"  Origin  : ({master.transform.c:.2f} m E, {master.transform.f:.2f} m N)\n")

    ini_vars = {
        "chan":     "Channels          ",
        "changrad": "ChanGrad          ",
        "chanman":  "ChanMan           ",
        "chanleng": "ChanLength        ",
        "chanbw":   "ChanBottomWidth   ",
        "chans":    "ChanSdXdY         ",
        "chanbnkf": "ChanDepthThreshold",
    }
    print("  LISFLOOD .ini (channel section):")
    for key, ini_name in ini_vars.items():
        val = map_paths.get(key, f"⚠  run manual_convert.sh for {key}.map")
        print(f"    {ini_name} = {val}")

    print(f"\n  Output : {OUTPUT_DIR}/maps/")
    print(f"  Check  : {OUTPUT_DIR}/CHANNEL_VISUAL_CHECK.png")
    print("═" * 62 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
#   MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 62)
    print("  LISFLOOD CHANNEL MAP GENERATOR")
    print(f"  Reference raster : {AREA_RASTER_PATH}")
    print(f"  NASA SRTM 30m  |  {RESOLUTION_M}m  |  {TARGET_CRS}")
    print(f"  Flow-acc threshold: {FLOW_ACC_THRESHOLD:,} cells")
    print("═" * 62 + "\n")

    check_imports()
    make_dirs()

    # ── Step 1 — Load master grid (locks all alignment to area.tif) ──────────
    master, mask = load_master_grid(AREA_RASTER_PATH)

    # ── Step 2 — Get DEM snapped to master grid ──────────────────────────────
    dem_path, dem_snapped = get_dem_snapped(master)

    # ── Step 3 — Flow direction + accumulation + Strahler order ─────────────
    fdir_snap, facc_snap, order_snap = compute_flow_products(dem_path, master)

    # ── Step 4 — Compute all 7 channel layers ────────────────────────────────
    chan     = make_chan    (facc_snap, mask, master)
    changrad = make_changrad(dem_snapped, fdir_snap, chan, mask, master)
    chanman  = make_chanman (order_snap, chan, mask, master)
    chanleng = make_chanleng(fdir_snap, chan, mask, master)
    chanbw   = make_chanbw  (facc_snap, chan, mask, master)
    chans    = make_chans   (chan, mask, master)
    chanbnkf = make_chanbnkf(facc_snap, chan, mask, master)

    # Collect all TIF paths for conversion + validation
    tif_paths = {
        "chan":     os.path.join(OUTPUT_DIR, "maps", "chan.tif"),
        "changrad": os.path.join(OUTPUT_DIR, "maps", "changrad.tif"),
        "chanman":  os.path.join(OUTPUT_DIR, "maps", "chanman.tif"),
        "chanleng": os.path.join(OUTPUT_DIR, "maps", "chanleng.tif"),
        "chanbw":   os.path.join(OUTPUT_DIR, "maps", "chanbw.tif"),
        "chans":    os.path.join(OUTPUT_DIR, "maps", "chans.tif"),
        "chanbnkf": os.path.join(OUTPUT_DIR, "maps", "chanbnkf.tif"),
    }

    # ── Step 5 — Convert to PCRaster .map ────────────────────────────────────
    map_paths = convert_to_pcraster(tif_paths)

    # ── Step 6 — Validate alignment and LISFLOOD constraints ─────────────────
    validate_alignment(tif_paths, master)

    # ── Step 7 — Visual summary ───────────────────────────────────────────────
    visualize(chan, changrad, chanbw, chanbnkf, chanman, mask, master)

    print_summary(tif_paths, map_paths, master)


if __name__ == "__main__":
    main()