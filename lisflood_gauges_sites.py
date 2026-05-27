"""
=============================================================================
LISFLOOD GAUGES AND SITES MAP GENERATOR

Produces two PCRaster nominal maps required in the LISFLOOD .ini file:

  gauges.map  — integer-coded map with non-zero IDs only at river
                cross-sections.  LISFLOOD writes discharge and water-level
                time series at these cells.
                Constraint: every gauge cell MUST lie on the channel
                network (chan.map == 1).

  sites.map   — integer-coded map with non-zero IDs at any cell.
                LISFLOOD writes extended variable output (soil moisture,
                percolation, water table, etc.) at these cells.
                Not restricted to the channel network.

─────────────────────────────────────────────────────────────────────────────
PLACEMENT LOGIC
─────────────────────────────────────────────────────────────────────────────

  1. OUTLET (ID = 1, always generated)
     ─────────────────────────────────
     Automatically detected as the channel cell with the HIGHEST FLOW
     ACCUMULATION inside the watershed mask.

     Why flow accumulation?
       Flow accumulation at a cell = number of upstream cells that drain
       through it.  The cell with the global maximum is the basin outlet —
       all runoff from every other cell must pass through it before leaving
       the catchment.  Placing gauge 1 there captures the full basin
       discharge, which is the most useful single time series for model
       calibration.

  2. CONFLUENCE GAUGES (IDs = 2 … K, auto-detected)
     ─────────────────────────────────────────────────
     Every channel cell where two or more upstream channel tributaries
     merge is automatically assigned a gauge.

     Detection method (D8 inflow count):
       For each channel cell, count how many of its 8 D8 neighbours have
       their flow-direction code pointing INTO that cell.  A cell with
       inflow count ≥ 2 is a confluence (stream junction).
       The outlet cell is excluded here (already placed as ID=1).

     Gauges are sorted by flow accumulation descending so the most
     significant junctions (main-stem confluences) get the lowest IDs.

  3. USER GAUGES (IDs = K+1 … N, optional)
     ─────────────────────────────────────────
     (lat, lon) WGS84 coordinates listed in GAUGE_LOCATIONS in
     pipeline_config.py (e.g. a real stream-gauge station).

     Snapping logic:
       • Convert (lat, lon) → UTM (x, y) using area.tif's CRS.
       • Convert UTM → grid (row, col) using the master-grid affine transform.
       • If that cell is already a channel cell → place the gauge there.
       • If not → expand a square search window pixel by pixel until a
         channel cell is found within GAUGE_SNAP_DIST_M metres.
       • If none found within the radius → skip with a warning.

     This is necessary because real-world station coordinates rarely land
     exactly on the modelled channel centreline.

  4. ADDITIONAL SITES (IDs = N+1 … M, optional)
     ─────────────────────────────────────────────
     (lat, lon) WGS84 coordinates in SITE_LOCATIONS in pipeline_config.py
     for non-channel monitoring points (e.g. rainfall gauges, lysimeters,
     soil-moisture sensors).

     Unlike gauges, these do NOT need to be on the channel.  They are
     snapped to the nearest INSIDE-MASK cell.

     All gauge cells are automatically also included in sites.map so
     LISFLOOD writes both channel-based and catchment-wide output.

─────────────────────────────────────────────────────────────────────────────
DEPENDENCIES (must run first)
─────────────────────────────────────────────────────────────────────────────
  topographyMapsScript.py  →  area.tif
  channnels.py             →  lisflood_channels/maps/chan.tif
                           →  lisflood_channels/raw/facc_snapped.tif
=============================================================================
"""

import os
import sys
import csv
import numpy as np
import warnings
warnings.filterwarnings("ignore")

import pipeline_config as _cfg
from lisflood_utils import (log, make_dirs, gdal_convert,
                            load_grid, save_aligned)


# =============================================================================
#  SETTINGS
# =============================================================================

AREA_TIF    = _cfg.AREA_TIF
CHAN_TIF    = os.path.join(_cfg.OUTPUT_CHANNELS, "maps", "chan.tif")
FACC_TIF    = os.path.join(_cfg.OUTPUT_CHANNELS, "raw",  "facc_snapped.tif")
LDD_TIF     = os.path.join(_cfg.OUTPUT_TOPO,     "maps", "ldd.tif")
OUTPUT_DIR  = _cfg.OUTPUT_GAUGES

MAX_SNAP_DIST_M = _cfg.GAUGE_SNAP_DIST_M


# =============================================================================
#  COORDINATE HELPERS
# =============================================================================

def _utm_to_rowcol(x_utm, y_utm, info):
    """
    Convert a UTM coordinate to the nearest grid (row, col).

    The affine transform maps:
      col = (x - origin_x) / pixel_width      (t.a = +pixel_width)
      row = (y - origin_y) / pixel_height      (t.e = -pixel_height, negative)
    """
    t   = info.transform
    col = (x_utm - t.c) / t.a
    row = (y_utm - t.f) / t.e
    return int(round(row)), int(round(col))


def _rowcol_to_utm(row, col, info):
    """Return the UTM centroid of grid cell (row, col)."""
    t = info.transform
    x = t.c + (col + 0.5) * t.a
    y = t.f + (row + 0.5) * t.e
    return x, y


def _run_crs():
    """Run CRS = area.tif's CRS (cached by load_grid)."""
    return load_grid(AREA_TIF)[0].crs


def _wgs84_to_utm(lat, lon):
    """Convert WGS84 (lat, lon) → projected (x, y) in the run CRS."""
    import pyproj
    tr = pyproj.Transformer.from_crs("EPSG:4326", _run_crs(), always_xy=True)
    return tr.transform(lon, lat)   # always_xy: input as (lon, lat)


def _utm_to_wgs84(x, y):
    """Convert projected (x, y) → WGS84 (lat, lon)."""
    import pyproj
    tr = pyproj.Transformer.from_crs(_run_crs(), "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(x, y)
    return lat, lon


def _snap_to_channel(x_utm, y_utm, chan, info):
    """
    Find the nearest channel cell to UTM coordinate (x_utm, y_utm).

    Algorithm:
      1. Convert the coordinate to (row, col).
      2. If that cell is already a channel cell, return it immediately.
      3. Otherwise search all cells within a ±max_px bounding box,
         keeping the one with the smallest Euclidean pixel distance
         that is also a channel cell.
      4. Return None if no channel cell is found within MAX_SNAP_DIST_M.

    Pixel-distance search is used (not geodesic) because the master grid
    is in a projected CRS (metres) where pixel distances are proportional
    to real-world distances.
    """
    res    = abs(info.transform.a)
    max_px = int(MAX_SNAP_DIST_M / res) + 1

    r0, c0 = _utm_to_rowcol(x_utm, y_utm, info)
    r0 = max(0, min(info.height - 1, r0))
    c0 = max(0, min(info.width  - 1, c0))

    if chan[r0, c0] == 1:
        return r0, c0

    best_r, best_c, best_d2 = None, None, float("inf")
    for dr in range(-max_px, max_px + 1):
        for dc in range(-max_px, max_px + 1):
            rr, cc = r0 + dr, c0 + dc
            if not (0 <= rr < info.height and 0 <= cc < info.width):
                continue
            if chan[rr, cc] != 1:
                continue
            d2 = dr * dr + dc * dc
            if d2 < best_d2:
                best_d2, best_r, best_c = d2, rr, cc

    if best_r is None:
        return None
    if np.sqrt(best_d2) * res > MAX_SNAP_DIST_M:
        return None
    return best_r, best_c


# =============================================================================
#  STEP 1 — LOAD INPUTS
# =============================================================================

def load_inputs():
    log("STEP 1 — Loading master grid, channel network, flow accumulation", "STEP")
    import rasterio

    info, mask = load_grid(AREA_TIF)

    for path, label, hint in [
        (CHAN_TIF, "chan.tif",          "Run channnels.py first."),
        (FACC_TIF, "facc_snapped.tif", "Run channnels.py first."),
        (LDD_TIF,  "ldd.tif",          "Run topographyMapsScript.py first."),
    ]:
        if not os.path.exists(path):
            log(f"{label} not found: {path}", "ERROR")
            log(hint, "ERROR")
            sys.exit(1)

    with rasterio.open(CHAN_TIF) as src:
        chan = src.read(1).astype(np.int8)

    with rasterio.open(FACC_TIF) as src:
        facc = src.read(1).astype(np.float32)
        facc[facc < 0] = 0.0

    with rasterio.open(LDD_TIF) as src:
        ldd = src.read(1).astype(np.int8)

    log(f"  Channel cells  : {int(chan.sum()):,}")
    log(f"  Max flow accum : {int(facc[chan == 1].max()):,} cells")
    return info, mask, chan, facc, ldd


# =============================================================================
#  STEP 2 — DETECT BASIN OUTLET
# =============================================================================

def detect_outlet(chan, facc, mask, info):
    """
    The outlet is the channel cell with the highest flow accumulation
    inside the watershed mask.

    Why this works:
      Flow accumulation counts how many upstream cells drain into each cell.
      The cell with the global maximum is where ALL basin runoff converges
      — the natural exit point of the catchment.  Gauge 1 placed here
      captures the integrated basin response, which is the standard
      reference point for calibration against observed streamflow.
    """
    log("STEP 2 — Detecting basin outlet (max flow accumulation on channel)", "STEP")

    on_channel_in_mask = (chan == 1) & (mask > 0)
    if not on_channel_in_mask.any():
        log("No channel cells found inside the watershed mask.", "ERROR")
        sys.exit(1)

    # Suppress non-channel cells so argmax finds only channel pixels
    facc_ch     = np.where(on_channel_in_mask, facc, -1.0)
    row, col    = np.unravel_index(np.argmax(facc_ch), facc_ch.shape)
    x, y        = _rowcol_to_utm(row, col, info)
    lat, lon    = _utm_to_wgs84(x, y)
    acc_cells   = int(facc[row, col])
    res         = abs(info.transform.a)
    area_km2    = acc_cells * res**2 / 1e6

    log(f"  Outlet — ID=1  row={row}  col={col}")
    log(f"           UTM  : ({x:.1f} E,  {y:.1f} N)")
    log(f"           WGS84: {lat:.5f}°N  {lon:.5f}°E")
    log(f"           Upstream area : {acc_cells:,} cells  ({area_km2:.2f} km²)")
    return row, col


# =============================================================================
#  STEP 2b — DETECT CHANNEL CONFLUENCES
# =============================================================================

def detect_confluences(chan, ldd, mask, facc, outlet_rc):
    """
    Find every channel cell where 2 or more upstream D8 channel cells drain in.

    Algorithm:
      For each of the 8 PCRaster LDD codes, identify channel cells that flow
      in that direction and add 1 to the inflow counter of their downstream
      neighbour.  Any channel cell whose inflow counter reaches 2 or more is
      a confluence (stream junction).

    PCRaster LDD numpad convention (matches ldd.tif from topographyMapsScript.py):
      7=NW  8=N   9=NE
      4=W   5=pit  6=E
      1=SW  2=S   3=SE

      Code → downstream offset (dr, dc):
        1 (SW) → (+1, -1)    6 (E)  → ( 0, +1)
        2 (S)  → (+1,  0)    7 (NW) → (-1, -1)
        3 (SE) → (+1, +1)    8 (N)  → (-1,  0)
        4 (W)  → ( 0, -1)    9 (NE) → (-1, +1)
        5 (pit) — no movement, skipped

    The outlet cell is excluded (already placed as gauge ID=1).
    Results are sorted by flow accumulation descending so main-stem
    confluences (highest discharge) get the lowest IDs.
    """
    log("STEP 2b — Detecting channel confluences (stream junctions)", "STEP")

    # PCRaster LDD code -> (dr, dc) offset to the downstream cell
    outflow_offsets = {
        1: (+1, -1),   # SW
        2: (+1,  0),   # S
        3: (+1, +1),   # SE
        4: ( 0, -1),   # W
        6: ( 0, +1),   # E
        7: (-1, -1),   # NW
        8: (-1,  0),   # N
        9: (-1, +1),   # NE
    }

    h, w = chan.shape
    inflow_count = np.zeros((h, w), dtype=np.int8)

    for code, (dr, dc) in outflow_offsets.items():
        # Cells that are on the channel and flow in this direction
        source = (chan == 1) & (ldd == code)

        # Slice ranges that avoid going out of bounds when shifting by (dr, dc)
        r_s = slice(max(0, -dr), h - max(0, dr))
        c_s = slice(max(0, -dc), w - max(0, dc))
        r_d = slice(max(0,  dr), h - max(0, -dr))
        c_d = slice(max(0,  dc), w - max(0, -dc))

        inflow_count[r_d, c_d] += source[r_s, c_s].astype(np.int8)

    # Confluences: channel cells inside the mask with ≥2 upstream channel cells
    confluence_mask = (inflow_count >= 2) & (chan == 1) & (mask > 0)
    # Outlet is already gauge ID=1 — exclude it
    confluence_mask[outlet_rc[0], outlet_rc[1]] = False

    rows, cols = np.where(confluence_mask)
    if rows.size == 0:
        log("  No confluences found — check FLOW_ACC_THRESHOLD in channnels.py", "WARN")
        return []

    # Sort by flow accumulation descending (most significant junctions first)
    order      = np.argsort(-facc[rows, cols])
    confluences = [(int(rows[i]), int(cols[i])) for i in order]

    log(f"  Found {len(confluences)} confluences")
    return confluences


# =============================================================================
#  STEP 3 — SNAP USER GAUGES TO CHANNEL
# =============================================================================

def place_user_gauges(chan, info):
    """
    For each (name, lat, lon) in GAUGE_LOCATIONS, find the nearest channel
    cell within GAUGE_SNAP_DIST_M and record its (row, col).

    Skips points with no channel cell in range (with a warning) so a single
    bad coordinate does not abort the whole run.
    """
    log("STEP 3 — Placing user-specified gauges on channel network", "STEP")

    placed = []
    if not _cfg.GAUGE_LOCATIONS:
        log("  GAUGE_LOCATIONS is empty — only the outlet gauge will be generated")
        return placed

    for name, lat, lon in _cfg.GAUGE_LOCATIONS:
        x_utm, y_utm = _wgs84_to_utm(lat, lon)
        result       = _snap_to_channel(x_utm, y_utm, chan, info)
        if result is None:
            log(f"  {name} ({lat:.4f}°N {lon:.4f}°E) — no channel cell within "
                f"{MAX_SNAP_DIST_M} m  → SKIPPED", "WARN")
            continue
        r, c        = result
        xs, ys      = _rowcol_to_utm(r, c, info)
        snap_dist_m = np.sqrt((xs - x_utm)**2 + (ys - y_utm)**2)
        log(f"  {name} ({lat:.4f}°N {lon:.4f}°E) → row={r} col={c}  "
            f"[snapped {snap_dist_m:.1f} m]")
        placed.append((name, r, c))

    return placed


# =============================================================================
#  STEP 4 — PLACE ADDITIONAL MONITORING SITES
# =============================================================================

def place_sites(mask, info):
    """
    For each (name, lat, lon) in SITE_LOCATIONS, find the nearest
    inside-mask cell.

    Unlike gauges, sites are NOT required to be on the channel.  They
    represent any point where LISFLOOD state variables are needed —
    soil-moisture sensors, groundwater wells, met stations, etc.
    """
    log("STEP 4 — Placing additional monitoring sites", "STEP")

    placed = []
    if not _cfg.SITE_LOCATIONS:
        log("  SITE_LOCATIONS is empty — sites map will mirror gauges map")
        return placed

    rows_in, cols_in = np.where(mask > 0)   # pre-compute for nearest-cell search

    for name, lat, lon in _cfg.SITE_LOCATIONS:
        x_utm, y_utm = _wgs84_to_utm(lat, lon)
        r0, c0       = _utm_to_rowcol(x_utm, y_utm, info)
        r0 = max(0, min(info.height - 1, r0))
        c0 = max(0, min(info.width  - 1, c0))

        if mask[r0, c0] == 0:
            # Outside mask — snap to nearest inside-mask cell
            dists    = (rows_in - r0)**2 + (cols_in - c0)**2
            idx      = int(np.argmin(dists))
            r0, c0   = int(rows_in[idx]), int(cols_in[idx])
            log(f"  {name}: coordinate outside mask — snapped to nearest inside cell")

        xs, ys      = _rowcol_to_utm(r0, c0, info)
        snap_dist_m = np.sqrt((xs - x_utm)**2 + (ys - y_utm)**2)
        log(f"  {name} ({lat:.4f}°N {lon:.4f}°E) → row={r0} col={c0}  "
            f"[{snap_dist_m:.1f} m from target]")
        placed.append((name, r0, c0))

    return placed


# =============================================================================
#  STEP 5 — BUILD NUMPY ARRAYS
# =============================================================================

def build_maps(outlet_rc, confluences, user_gauges, add_sites, info):
    """
    Assign integer IDs and fill the gauges and sites arrays.

    ID scheme
    ─────────
      1         → outlet (always ID 1)
      2 … K     → confluence gauges (auto-detected junctions, sorted by
                  flow accumulation descending — main-stem first)
      K+1 … N   → user gauges  (GAUGE_LOCATIONS, in list order)
      N+1 … M   → extra sites that are NOT already a gauge cell
                  (SITE_LOCATIONS)

    Both gauges and sites start as all-zero arrays.  LISFLOOD reads zero
    as "no output here".  Non-zero IDs trigger time-series output.

    gauges_arr   — only channel cells ever get a non-zero ID
    sites_arr    — includes all gauge cells PLUS any extra site cells
    """
    log("STEP 5 — Assigning IDs and building gauges / sites arrays", "STEP")

    gauges_arr = np.zeros((info.height, info.width), dtype=np.int16)
    sites_arr  = np.zeros((info.height, info.width), dtype=np.int16)
    records    = []

    def _place(arr, row, col, gid, name, kind):
        arr[row, col] = gid
        x, y      = _rowcol_to_utm(row, col, info)
        lat, lon  = _utm_to_wgs84(x, y)
        records.append({
            "id": gid, "name": name, "kind": kind,
            "row": row, "col": col,
            "utm_e": round(x, 1), "utm_n": round(y, 1),
            "lat": round(lat, 6), "lon": round(lon, 6),
        })
        log(f"  ID {gid:>3}  [{kind:<10}]  {name:<25}  row={row} col={col}  "
            f"{lat:.4f}°N {lon:.4f}°E")

    gid = 1
    _place(gauges_arr, outlet_rc[0], outlet_rc[1], gid, "outlet", "gauge")
    _place(sites_arr,  outlet_rc[0], outlet_rc[1], gid, "outlet", "gauge+site")

    for i, (row, col) in enumerate(confluences):
        gid += 1
        name = f"confluence_{i+1}"
        _place(gauges_arr, row, col, gid, name, "gauge")
        _place(sites_arr,  row, col, gid, name, "gauge+site")

    for name, row, col in user_gauges:
        gid += 1
        _place(gauges_arr, row, col, gid, name, "gauge")
        _place(sites_arr,  row, col, gid, name, "gauge+site")

    for name, row, col in add_sites:
        if sites_arr[row, col] == 0:          # don't overwrite a gauge cell
            gid += 1
            _place(sites_arr, row, col, gid, name, "site")
        else:
            log(f"  {name}: overlaps existing gauge cell — skipped from sites", "WARN")

    log(f"\n  Gauge cells : {int((gauges_arr > 0).sum())}  "
        f"(1 outlet + {len(confluences)} confluences + {len(user_gauges)} user)")
    log(f"  Site  cells : {int((sites_arr  > 0).sum())}")
    return gauges_arr, sites_arr, records


# =============================================================================
#  STEP 6 — SAVE GEOTIFFS + CONVERT TO PCRaster
# =============================================================================

def save_maps(gauges_arr, sites_arr, info):
    log("STEP 6 — Saving GeoTIFFs and converting to PCRaster .map", "STEP")

    maps_dir = os.path.join(OUTPUT_DIR, "maps")

    for name, arr in [("gauges", gauges_arr), ("sites", sites_arr)]:
        tif = os.path.join(maps_dir, f"{name}.tif")
        mp  = os.path.join(maps_dir, f"{name}.map")
        save_aligned(arr, tif, "int16", 0, like=AREA_TIF)
        ok = gdal_convert(tif, mp, "VS_NOMINAL")
        log(f"  {name}.map  {'✔' if ok else '⚠  run gdal_translate manually'}")


# =============================================================================
#  STEP 7 — WRITE CSV REPORT
# =============================================================================

def write_report(records):
    log("STEP 7 — Writing location report CSV", "STEP")

    csv_path = os.path.join(OUTPUT_DIR, "gauge_site_locations.csv")
    fields   = ["id", "name", "kind", "row", "col",
                "utm_e", "utm_n", "lat", "lon"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)
    log(f"  {csv_path}")


# =============================================================================
#  MAIN
# =============================================================================

def main():
    print("\n" + "═" * 62)
    print("  LISFLOOD GAUGES & SITES MAP GENERATOR")
    print(f"  CRS         : {info.crs}")
    print(f"  Snap radius : {MAX_SNAP_DIST_M} m")
    print(f"  User gauges : {len(_cfg.GAUGE_LOCATIONS)}")
    print(f"  Extra sites : {len(_cfg.SITE_LOCATIONS)}")
    print("═" * 62 + "\n")

    make_dirs(OUTPUT_DIR)

    info, mask, chan, facc, ldd      = load_inputs()
    outlet_rc                       = detect_outlet(chan, facc, mask, info)
    confluences                     = detect_confluences(chan, ldd, mask, facc, outlet_rc)
    user_gauges                     = place_user_gauges(chan, info)
    add_sites                       = place_sites(mask, info)
    gauges_arr, sites_arr, records  = build_maps(outlet_rc, confluences, user_gauges, add_sites, info)
    save_maps(gauges_arr, sites_arr, info)
    write_report(records)

    gauges_map = os.path.join(OUTPUT_DIR, "maps", "gauges.map")
    sites_map  = os.path.join(OUTPUT_DIR, "maps", "sites.map")

    print("\n" + "═" * 62)
    print("  ★  DONE")
    print(f"\n  Output dir : {OUTPUT_DIR}/maps/")
    print(f"  Report     : {OUTPUT_DIR}/gauge_site_locations.csv")
    print("\n  Add to LISFLOOD .ini:")
    print(f"    GaugesMap = {gauges_map}")
    print(f"    SitesMap  = {sites_map}")
    print("═" * 62 + "\n")


if __name__ == "__main__":
    main()
