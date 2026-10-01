# `pipeline/get_hydrobasins_roi.py`

**156 lines. A one-off utility, run before the pipeline.** Produces the watershed shapefile
that `pipeline_config.ROI_SHAPEFILE` points at.

```bash
python scripts/pipeline/get_hydrobasins_roi.py
```

## HydroBASINS in one paragraph

A global hierarchical decomposition of land into nested drainage basins, levels 1 (continental)
to 12 (~130 km² units). Each polygon carries topology fields — crucially `HYBAS_ID` (its own id)
and `NEXT_DOWN` (the id of the basin it drains into). **It's a forest of trees encoded as an
edge list**, and this script is a graph traversal over it.

## Configuration (edit the CONFIG block)

```python
LEVEL        = 6            # 1 = coarse, 12 = fine
EXTENT       = "single"     # or "catchment"
POINT_LONLAT = [80.450, 17.192]    # pick by point ...
HYBAS_ID     = None                # ... or by exact id (set exactly one)
```

## `find_target()` — resolving the selection

```python
if HYBAS_ID is not None:
    feat = basins.filter(ee.Filter.eq("HYBAS_ID", HYBAS_ID)).first()
elif POINT_LONLAT is not None:
    feat = basins.filterBounds(ee.Geometry.Point(POINT_LONLAT)).first()
info = ee.Feature(feat).toDictionary(["HYBAS_ID", "MAIN_BAS"]).getInfo()
```

`filterBounds` is a server-side spatial join — "which polygon contains this point". `.getInfo()`
is the one place data crosses back to Python.

## `upstream_ids()` — the graph traversal

**This is the interesting function.** "Give me everything that drains into basin X."

```python
same  = basins.filter(ee.Filter.eq("MAIN_BAS", main_bas))     # one river system only
ids   = same.aggregate_array("HYBAS_ID").getInfo()
nexts = same.aggregate_array("NEXT_DOWN").getInfo()

children = {}                          # invert the edge list
for b, nd in zip(ids, nexts):
    children.setdefault(nd, []).append(b)

keep, stack = set(), [target_id]       # iterative DFS
while stack:
    cur = stack.pop()
    if cur in keep: continue
    keep.add(cur)
    stack.extend(children.get(cur, []))
return sorted(keep)
```

Three things to note:

1. **Edge inversion.** The data gives `child → parent` (`NEXT_DOWN`). You need `parent →
   children` to walk upstream, so build the reverse adjacency once, `O(n)`, instead of scanning
   the whole list per node.
2. **Iterative DFS with an explicit stack**, not recursion. A deep river tree would blow
   Python's ~1000-frame recursion limit.
3. **`MAIN_BAS` pre-filter.** Every HydroBASINS polygon records its top-level river system.
   Filtering server-side means you download one river's edge list, not the continent's — the
   difference between thousands of rows and millions.

The `if cur in keep: continue` guard makes it robust to duplicate paths or a cycle in the data.

## The two extents

```python
if EXTENT == "single":
    fc = basins.filter(ee.Filter.eq("HYBAS_ID", target_id)).select(KEEP_FIELDS)
elif EXTENT == "catchment":
    ids = upstream_ids(basins, target_id, main_bas)
    fc = basins.filter(ee.Filter.inList("HYBAS_ID", ids)).union(ee.ErrorMargin(1))
```

- **`single`** — one polygon. Fast, but hydrologically incomplete: water enters across the
  upstream boundary from land you aren't modelling.
- **`catchment`** — every upstream basin, `.union()`ed into one polygon. **This is what a valid
  LISFLOOD study area is**: closed at the top, so all water in the domain originates as rainfall
  inside it. `ee.ErrorMargin(1)` allows 1 m of geometric slop when dissolving shared edges.

## The 255-field shapefile limit

```python
KEEP_FIELDS = ["HYBAS_ID","NEXT_DOWN","NEXT_SINK","MAIN_BAS",
               "DIST_SINK","DIST_MAIN","SUB_AREA","UP_AREA","PFAF_ID"]
```

HydroATLAS carries ~295 attributes per basin. The **ESRI Shapefile format (dBase) caps at 255
fields** — a 1990s format constraint that will fail the export otherwise. `.select()` trims
server-side. (`union()` drops attributes anyway, so this only applies to the `single` path.)

## `visualize()`

Reads the output back with geopandas, computes area via the local UTM zone (the same
`(lon+180)//6 + 1` formula as `pipeline_config.resolve_crs()` — area in degrees is meaningless),
and saves a PNG next to the shapefile. `matplotlib.use("Agg")` before importing pyplot selects
the non-interactive backend — required for headless/server use, and used consistently across
the repo.

## Practical notes

- Output CRS is EPSG:4326; `topographyMapsScript.py` reprojects to UTM itself.
- Filenames are built from the **resolved** id (`hydrobasins_roi_lev6_4060027940.shp`), so
  point-selection and id-selection land on the same file.
- Large rivers at level 12 can time out server-side in `union()`. Use a coarser level.
- After generating: set `ROI_SHAPEFILE`, delete `inputs/raw/`, re-run the pipeline.
