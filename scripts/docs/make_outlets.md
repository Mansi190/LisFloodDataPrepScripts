# `pipeline/make_outlets.py`

**133 lines. Step 6.** Converts a list of gauge coordinates into `outlets.nc` — the raster that
tells LISFLOOD where to report discharge.

Input: `inputs/reportingStations/stations.csv`. Output: `inputs/reportingStations/outlets.nc`.

## Why a raster and not a coordinate list

LISFLOOD reports discharge at **every non-zero cell** of the gauges map, using the cell's value
as the station ID. So the job is: for each `(lon, lat)`, find the right pixel, and write the ID
there.

`uint8` storage is why IDs must be 1–255 and 0 means "no station".

## `stations.csv` parsing

Format: `lon lat id` per line, `#` starts a comment.

```python
line = raw.split("#")[0].strip()
lon = float(parts[0]) / 10000.0 if '.' not in parts[0] else float(parts[0])
```

That conditional supports a legacy fixed-point convention (`746022` → `74.6022`). If a
value has a decimal point it's taken as degrees; otherwise it's divided by 10,000. Slightly
surprising, but harmless as long as you always write decimals.

Validation before any work:

```python
if len(set(ids)) != len(ids):        exit("duplicate ids")
if max(ids) > 255 or min(ids) < 1:   exit("ids must be 1..255 (uint8)")
```

## World coordinates → pixel

```python
transformer = pyproj.Transformer.from_crs("EPSG:4326", info.crs, always_xy=True)
x_utm, y_utm = transformer.transform(lon, lat)
col = int(round((x_utm - t.c) / t.a))
row = int(round((y_utm - t.f) / t.e))
```

`always_xy=True` is not optional. Without it, pyproj follows the EPSG axis order, and
EPSG:4326 is officially **(lat, lon)** — so `transform(lon, lat)` would silently swap your
coordinates and put the gauge in the wrong hemisphere. This flag is one of the most common
sources of geospatial bugs; always pass it.

The two divisions are the inverse of the affine transform (see the README's "affine transform"
section). `t.e` is negative, which is what makes northing decrease as row increases.

## `align_station()` — the interesting bit

```python
def align_station(row, col, facc, ldd, mask, max_radius=2):
    max_acc = -1
    for r in range(-2, 3):
        for c in range(-2, 3):
            if mask[nr,nc] > 0 and 1 <= ldd[nr,nc] <= 9:
                if facc[nr,nc] > max_acc:
                    max_acc = facc[nr,nc]; best = (nr,nc)
    return best
```

**Why snapping is necessary.** A gauge's published coordinate has maybe 100 m of error, and the
modelled river network — derived from a 30 m DEM upscaled to 300 m — doesn't land exactly on
the real river either. Drop the coordinate straight onto the grid and you can easily land on a
hillslope cell one pixel off the channel. LISFLOOD would then report the discharge of a cell
with almost no upstream area: near zero, every day, and no error message.

**Why maximum flow accumulation is the right target.** Accumulation *is* upstream area. Within
a 5×5 neighbourhood, the cell with the most upstream area is the main channel. Searching for
"nearest channel cell" would be worse — it could snap to a small tributary that happens to be
closer.

The `1 <= ldd <= 9` test rejects cells with no valid flow direction (255 = outside the LDD).

Note `max_radius=2` (±600 m) is hardcoded here, while `pipeline_config.GAUGE_SNAP_DIST_M = 500`
exists and is unused — a config value that isn't wired up.

## Failure handling

```python
if not (valid_raw and mask[row_raw, col_raw] > 0):
    print("WARNING: station lands outside the basin mask — SKIPPED"); continue
...
if placed == 0:
    print("No station landed inside the mask"); sys.exit(1)
```

Individual stations degrade gracefully (warn and skip); **zero** placed stations is fatal,
because an empty `outlets.nc` means LISFLOOD would run for hours and report nothing. A
collision (two stations snapping to the same cell) warns and overwrites.

## Output

```python
save_aligned(outlets_arr, temp_tif, "uint8", 0, like=area_tif)
gdal_convert_netcdf(temp_tif, out_nc)
os.remove(temp_tif)
```

TIF as an intermediate, converted, then deleted — the same `save_aligned(like=…)` path as every
other output, so the gauges map is guaranteed to sit on the model grid.

## Dependencies

Needs `maps/area.tif`, `maps/ldd.tif` (topo step) and `raw/facc_snapped.tif` (**channels**
step). Running this before `channnels.py` fails on the missing accumulation file.

Verify the result visually: `display/generate_html_plots.py` renders the gauge as a star on the
channel network (`plot_gauges`). Do check it — a mis-snapped gauge invalidates every
calibration statistic downstream.
