# `pipeline/pipeline_config.py`

**205 lines. No I/O, no processing — a settings module + 3 helper functions.**
Every other pipeline script starts with `import pipeline_config as _cfg`.

## Why it exists

Without it, the ROI path, the CRS, the resolution and ~15 output directories would be
copy-pasted into a dozen scripts. Changing the study area would mean editing all of them and
missing one. This is the single-source-of-truth pattern: **one editable block at the top,
derived values below, nothing hardcoded downstream.**

## The odd first four lines

```python
import numpy as np
if not hasattr(np, 'in1d'):
    np.in1d = lambda ar1, ar2, ...: np.isin(ar1, ar2, ...)
```

A **monkeypatch**. `np.in1d` was removed in NumPy 2.0, but `pysheds` still calls it. Since
every script imports this module first, patching here fixes `pysheds` process-wide. It sits
*above* the module docstring, which is why the docstring is not actually `__doc__` — a
cosmetic side effect.

## Path anchoring

```python
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

Three `dirname` calls walk up from `scripts/pipeline/pipeline_config.py` → `scripts/pipeline`
→ `scripts` → repo root. Every path is built from `REPO_ROOT`, so scripts work no matter what
directory you launch them from. `__file__`-anchoring instead of `os.getcwd()` is the fix for
the classic "works when I run it from here, breaks from there" bug.

## The settings that matter

| name | value | what it controls |
|---|---|---|
| `ROI_SHAPEFILE` | `shapefiles/hydrobasins_roi_lev6_4060027940.shp` | the watershed boundary; **change this to change basins** |
| `RESOLUTION_M` | `300` | grid cell size in metres |
| `TARGET_CRS` | `None` | `None` → auto-detect UTM; or force e.g. `"EPSG:32644"` |
| `FORCING_START/END` | `2003-01-01` … `2024-12-31` | one continuous forcing span |
| `GEE_PROJECT` | `gssha-480613` | your Earth Engine cloud project |
| `GAUGE_SNAP_DIST_M` | `500` | search radius when snapping a gauge to a channel cell |

### Why one forcing span, not three

There are three LISFLOOD runs — prerun (2003–2015), cold (2016–2018), warm (2019). The code
downloads **one** continuous dataset; the settings XMLs slice it with `StepStart`/`StepEnd`.
Downloading three overlapping sets would triple the GEE traffic for no gain.

2003 is chosen so MODIS LAI (available from 2002-07-04) covers the whole period, and so the
13-year prerun has time to equilibrate the slow lower-groundwater store. CHIRPS itself goes
back to 1981 — 2003 is a modelling decision, not a data limit.

## Soil depth — a worked example of the single-source-of-truth idea

```python
SOIL_DEPTHS_L1         = ["0-5cm", "5-15cm", "15-30cm", "30-60cm"]
SOIL_DEPTHS_L1_WEIGHTS = [5, 10, 15, 30]              # thickness in cm
SOIL_DEPTH_L1_MM       = sum(SOIL_DEPTHS_L1_WEIGHTS) * 10   # 600 mm
```

Two different scripts need this number for different reasons:

- `lisflood_soil_preprocessing.py` averages SoilGrids properties **over** these bands
  (thickness-weighted, so a 30 cm band counts 6× a 5 cm band).
- `lisflood_lulc_cover.py` writes `soildep1_*.nc`, the depth LISFLOOD multiplies by porosity
  to get storage capacity: `w_s = ThetaSat × depth`.

If those two disagreed, the model would compute storage over a depth whose properties it
never measured. Deriving both from one list makes the bug structurally impossible.

## The three functions

### `resolve_crs()`
Returns `TARGET_CRS` if set, else derives the UTM zone from the ROI centroid:
`zone = int((lon + 180) // 6) + 1`, then `32600 + zone` (north) or `32700 + zone` (south).

The docstring flags the failure this prevents: if the ROI stayed in degrees and you asked for
300-unit pixels, 300 *degrees* would collapse the whole basin into one cell.

### `resolve_centroid()`
Reads the shapefile with geopandas, reprojects to EPSG:4326, returns `(lat, lon)` of the
union's centroid. Wrapped in try/except with a `(0.0, 0.0)` fallback — it is called during
module import paths where a missing shapefile shouldn't be fatal.

### `resolve_mean_elevation()`
Intended to give a basin-mean elevation for an atmospheric-pressure term.
**Currently dead code**: it opens `dem.tif`, but the topography script writes `dem_300m.tif`,
so it always hits the `return 0.0` fallback. Nothing calls it today (the evaporation maths
moved into the LISVAP Docker image).

## To point the pipeline at a new basin

1. Get a shapefile (see [get_hydrobasins_roi.md](get_hydrobasins_roi.md)).
2. Set `ROI_SHAPEFILE`.
3. Delete `inputs/raw/` (it caches downloads keyed by name, not by ROI).
4. `python pipeline.py`.

Leave `TARGET_CRS = None` unless the basin straddles two UTM zones.
