import os
import sys
import numpy as np
import xarray as xr
from lisflood_utils import load_grid, save_aligned, gdal_convert_netcdf
import pyproj
import rasterio
import pipeline_config as cfg

REPORTING_DIR = os.path.join(cfg.BASE_DIR, "reportingStations")


def align_station(row, col, facc, ldd, mask, max_radius=2, target_km2=None,
                  px_km2=None, tol=0.20):
    """Move a gauge onto the model's own channel.

    A gauge's published coordinates almost never land on a channel cell of the
    MODEL's drainage network: that network is D8 flow accumulation over a 300 m
    upscaled SRTM DEM, and it sits a few hundred metres to a few km away from the
    real river. Measured on this basin, all three FloodHub gauges landed on
    hillslope with 0.3-3.5 km2 of accumulation against true upstream areas of
    2,171-10,902 km2. Reporting there would give local hillslope runoff where the
    observations are river discharge, so some snap is mandatory.

    Two modes:

    target_km2 given -> AREA MATCHING (preferred). Search `max_radius` and take
        the cell whose accumulation is closest, in relative terms, to the gauge's
        known upstream area, using distance only to break ties. Returns None if
        nothing is within `tol`, so a gauge that cannot be placed fails loudly
        instead of silently reporting the wrong river.

    target_km2 None -> MAX ACCUMULATION (legacy). Take the largest accumulation
        in the window. Only safe at a small radius: widen it and this will happily
        jump to a bigger neighbouring river, which is exactly the failure the area
        match exists to prevent. The caller therefore keeps the radius at 2 px
        for this mode.

    Returns (row, col, acc_km2, rel_err) or (None, None, None, None).
    """
    best = None
    for r in range(-max_radius, max_radius + 1):
        for c in range(-max_radius, max_radius + 1):
            nr, nc = row + r, col + c
            if not (0 <= nr < facc.shape[0] and 0 <= nc < facc.shape[1]):
                continue
            if mask[nr, nc] <= 0 or not (1 <= ldd[nr, nc] <= 9):
                continue
            val = facc[nr, nc]
            if val == -9999 or np.isnan(val):
                continue
            dist2 = r * r + c * c
            if target_km2 is None:
                key = (-val, dist2)                       # biggest acc, then nearest
                rel = None
            else:
                rel = abs(val * px_km2 - target_km2) / target_km2
                key = (rel, dist2)                        # closest area, then nearest
            if best is None or key < best[0]:
                best = (key, nr, nc, val, rel)

    if best is None:
        return None, None, None, None
    _, nr, nc, val, rel = best
    if target_km2 is not None and rel > tol:
        return None, None, None, rel
    return nr, nc, val * px_km2 if px_km2 else None, rel


def main():
    # Load canonical grid and masks
    area_tif = os.path.join(cfg.DIR_MAPS, "area.tif")
    ldd_tif = os.path.join(cfg.DIR_MAPS, "ldd.tif")
    facc_tif = os.path.join(cfg.DIR_RAW, "facc_snapped.tif")
    
    info, mask = load_grid(area_tif)
    
    with rasterio.open(ldd_tif) as src:
        ldd = src.read(1)
    with rasterio.open(facc_tif) as src:
        facc = src.read(1)
        
    # Read stations.csv — one station per line: "lon lat id", '#' starts a comment.
    # Multiple reporting stations are the normal case (LISFLOOD reports discharge at
    # every non-zero cell of outlets.map), so parse line-by-line rather than treating
    # the whole file as a single record.
    stations = []
    with open(os.path.join(REPORTING_DIR, "stations.csv"), "r") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                print(f"stations.csv line {lineno}: need 'lon lat id [up_area_km2]', got {raw!r}")
                sys.exit(1)
            lon = float(parts[0]) / 10000.0 if '.' not in parts[0] else float(parts[0])
            lat = float(parts[1]) / 10000.0 if '.' not in parts[1] else float(parts[1])
            # optional 4th column: the gauge's known upstream area in km2, used
            # to snap by area match rather than by maximum accumulation.
            up_area = float(parts[3]) if len(parts) > 3 else None
            stations.append((lon, lat, int(parts[2]), up_area))

    if not stations:
        print("stations.csv contains no stations")
        sys.exit(1)

    ids = [s_[2] for s_ in stations]
    if len(set(ids)) != len(ids):
        print(f"stations.csv has duplicate ids: {ids}")
        sys.exit(1)
    if max(ids) > 255 or min(ids) < 1:
        print(f"station ids must be 1..255 (outlets.map is uint8), got {ids}")
        sys.exit(1)

    print(f"Read {len(stations)} reporting station(s) from stations.csv")

    transformer = pyproj.Transformer.from_crs("EPSG:4326", info.crs, always_xy=True)
    t = info.transform
    outlets_arr = np.zeros((info.height, info.width), dtype=np.uint8)
    placed = 0

    px_km2 = abs(t.a) * abs(t.e) / 1e6
    snap_px = max(1, int(round(cfg.GAUGE_SNAP_DIST_M / abs(t.a))))
    skipped = []

    for lon, lat, station_id, up_area in stations:
        print(f"\n-- station {station_id}: Lon={lon}, Lat={lat}"
              + (f"  (expected upstream area {up_area:,.1f} km2)" if up_area else ""))
        x_utm, y_utm = transformer.transform(lon, lat)
        col_raw = int(round((x_utm - t.c) / t.a))
        row_raw = int(round((y_utm - t.f) / t.e))

        is_valid_raw = (0 <= row_raw < info.height and 0 <= col_raw < info.width)
        row_snap, col_snap = row_raw, col_raw

        if is_valid_raw and mask[row_raw, col_raw] > 0:
            # Area matching needs a wide search (the channel can be km away) and is
            # safe there because it is anchored to the expected area. Max-accumulation
            # must stay narrow or it jumps to the neighbouring river.
            radius = snap_px if up_area else 2
            nr, nc, acc_km2, rel = align_station(
                row_raw, col_raw, facc, ldd, mask,
                max_radius=radius, target_km2=up_area, px_km2=px_km2)
            if nr is None:
                if up_area:
                    print(f"   ERROR: no cell within {cfg.GAUGE_SNAP_DIST_M/1000:.1f} km has an "
                          f"upstream area near {up_area:,.1f} km2 "
                          f"(closest was {rel*100:.0f}% off) — SKIPPED")
                else:
                    print("   WARNING: no valid LDD pixel nearby — SKIPPED")
                skipped.append(station_id)
                continue
            moved_km = ((nr - row_raw) ** 2 + (nc - col_raw) ** 2) ** 0.5 * abs(t.a) / 1000
            row_snap, col_snap = nr, nc
            if up_area:
                print(f"   snapped to Row={nr}, Col={nc}  moved {moved_km:.2f} km  "
                      f"acc={acc_km2:,.1f} km2 vs expected {up_area:,.1f} km2 "
                      f"({rel*100:.1f}% off)")
            else:
                print(f"   aligned to channel at Row={nr}, Col={nc} "
                      f"(Acc={facc[nr, nc]:.0f} px, moved {moved_km:.2f} km) "
                      f"[max-accumulation mode: no expected area given]")
        else:
            print("   WARNING: station lands outside the basin mask — SKIPPED")
            skipped.append(station_id)
            continue

        if outlets_arr[row_snap, col_snap] != 0:
            print(f"   WARNING: cell already holds station "
                  f"{outlets_arr[row_snap, col_snap]}; overwriting with {station_id}")
        outlets_arr[row_snap, col_snap] = station_id
        placed += 1

    # Strict mask (just to be completely safe)
    outlets_arr = np.where(mask > 0, outlets_arr, 0)
    print(f"\nPlaced {placed}/{len(stations)} station(s) inside the basin")
    if skipped:
        print(f"SKIPPED station(s): {skipped} — these will NOT appear in outlets.nc")
    if placed == 0:
        print("No station landed inside the mask — outlets.nc would be empty")
        sys.exit(1)

    # Save to NetCDF
    os.makedirs(REPORTING_DIR, exist_ok=True)
    temp_tif = os.path.join(REPORTING_DIR, "outlets.tif")
    out_nc = os.path.join(REPORTING_DIR, "outlets.nc")
    
    save_aligned(outlets_arr, temp_tif, "uint8", 0, like=area_tif)
    gdal_convert_netcdf(temp_tif, out_nc)
    os.remove(temp_tif)
    
    print(f"Successfully generated {out_nc}!")
    

if __name__ == "__main__":
    main()
