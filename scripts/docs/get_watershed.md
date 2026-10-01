# `pipeline/get_watershed.py`

**20 lines.** The minimal alternative to
[`get_hydrobasins_roi.py`](get_hydrobasins_roi.md): grab one watershed polygon from the
CoRE Stack asset by clicking a point.

```python
import ee, geemap
ee.Initialize(project='gssha-480613')

all_watersheds = ee.FeatureCollection(
    'projects/corestack-datasets/assets/datasets/hydrological_boundaries/watersheds')

my_point = ee.Geometry.Point([74.60222, 16.68444])     # [LONGITUDE, LATITUDE]
my_watershed = all_watersheds.filterBounds(my_point)
print(my_watershed.getInfo())

geemap.ee_export_vector(my_watershed, filename="./shapefiles/Watershed.shp")
```

## Worth reading anyway

It's the smallest complete example of the Earth Engine pattern the whole repo uses:

1. `ee.Initialize(project=...)` — authenticate against your cloud project.
2. `ee.FeatureCollection(asset_id)` — reference a server-side dataset. **Nothing downloads.**
3. `.filterBounds(geometry)` — a server-side spatial query. Still nothing downloads.
4. `.getInfo()` / `ee_export_vector(...)` — the terminal call that actually executes and
   transfers.

Steps 2–3 build a computation graph; only step 4 costs anything. That laziness is why the
bigger scripts can chain `.reduceResolution().reproject().rename()` freely.

## The coordinate-order trap

```python
ee.Geometry.Point([74.60222, 16.68444])   # [lon, lat] — longitude FIRST
```

The inline comment ("Notice 87 comes first!") is there because this is the most common GeoJSON
mistake. GeoJSON and Earth Engine use **(x, y) = (longitude, latitude)**, while everyday speech
and EPSG:4326's formal axis order are (latitude, longitude). Swapping them puts your basin in
the wrong hemisphere with no error message.

The same trap appears in `make_outlets.py`, handled there with
`pyproj.Transformer.from_crs(..., always_xy=True)`.

## `get_watershed.py` vs `get_hydrobasins_roi.py`

| | `get_watershed.py` | `get_hydrobasins_roi.py` |
|---|---|---|
| source | CoRE Stack watersheds | WWF HydroATLAS/HydroBASINS |
| granularity | fixed | levels 1–12 |
| upstream catchment | no | yes (`EXTENT="catchment"`) |
| output name | always `Watershed.shp` | encodes level + basin id |
| visual check | no | writes a PNG |

Use this one for a quick look; use `get_hydrobasins_roi.py` for anything you'll actually model,
because a hydrologically *closed* catchment is what makes the water balance meaningful.

## Hardcoded values to change

`ee.Initialize(project='gssha-480613')` and the point coordinates are inline, not read from
`pipeline_config`. If you fork this script, import the config instead.
