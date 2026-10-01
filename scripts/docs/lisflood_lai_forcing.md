# `pipeline/lisflood_lai_forcing.py`

**356 lines. Step 8.** Builds Leaf Area Index forcing for the forest and "other" fractions.

Outputs: `inputs/lai/forest/laif.nc` and `inputs/lai/other/laio.nc` — each `36 × 865 × 642`.

**LAI** = leaf area per unit ground area (m²/m²). It drives how much rain the canopy
intercepts and how much water the vegetation transpires.

Two ideas here you won't meet elsewhere in the pipeline: **temporal climatology folding** and
**spatial IDW interpolation**.

---

## Idea 1 — 22 years of data become 36 maps

LISFLOOD does not read a per-date LAI series. It reads a **sparse stack of one climatological
year, reused for every simulated year** (via `LAIOfDay` in the settings XML). The JRC standard
is 36 ten-day periods.

```python
N_LAI_SLOTS = 36
LAI_CLIM_YEAR = 2000
slots = np.minimum((obs_dates.dayofyear.values - 1) // 10, N_LAI_SLOTS - 1)
```

`(doy-1)//10` maps days 1–10 → slot 0, 11–20 → slot 1, …. `np.minimum(..., 35)` folds the
365th/366th day into the last slot (36×10 = 360, so days 361–366 overflow).

This also **solves the memory problem for free**. 2021 MODIS observations at 865×642 float32
is 4.5 GB; the previous version concatenated chunks into that array and held a second copy
alongside. Folding into 36 accumulators instead:

```python
acc = np.zeros((36, H, W), dtype=np.float64)     # 36×865×642×8 ≈ 160 MB
cnt = np.zeros((36, H, W), dtype=np.int32)       # ≈ 80 MB

for tif_path in tif_paths:
    chunk = rasterio.open(tif_path).read()
    for k in range(chunk.shape[0]):
        band = chunk[k]; band[band <= -9999] = np.nan
        final = np.where((base_mask > 0) & (lulc_mask > 0), snap_to_grid(band,...), np.nan)
        ok = ~np.isnan(final)
        acc[slots[b]][ok] += final[ok]     # sum and count per slot, per pixel
        cnt[slots[b]][ok] += 1
        b += 1
    del chunk

clim = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
```

This is a **streaming mean**: pixel-wise sum and count, divide at the end. Nothing bigger than
one chunk is ever held. `acc` is `float64` because summing ~56 values per slot in float32 would
accumulate rounding error; `np.maximum(cnt, 1)` avoids a divide-by-zero where the `np.where`
would discard the result anyway (NumPy evaluates both branches).

The masking is per-pixel: `ok = ~np.isnan(final)` means a cloudy pixel contributes to neither
sum nor count for that slot. Cloud gaps are handled by **just not counting them** — no
infilling needed.

Empty slots borrow from their temporal neighbour:

```python
for sl in range(N_LAI_SLOTS):
    if np.all(np.isnan(clim[sl])):
        clim[sl] = clim[(sl + 1) % N_LAI_SLOTS]
```

---

## The download

```python
lai_col = ee.ImageCollection("MODIS/061/MCD15A3H").filterBounds(region).filterDate(...)

def process_lai_bulk(img):
    lai = img.select('Lai')
    valid_mask = lai.lte(100)              # >100 = MODIS fill codes (cloud, water, snow)
    val = lai.updateMask(valid_mask).multiply(0.1)     # scale factor → real LAI units
    return val.reduceResolution(ee.Reducer.mean(), maxPixels=1024) \
              .reproject(crs=..., scale=300).unmask(-9999) \
              .toFloat().rename([date_str])
```

MCD15A3H is 4-daily at 500 m — so ~2000 observations for 2003–2024. Two source-specific steps:
the **quality filter** (`lte(100)`; MODIS stores fill flags in the same band as data) and the
**scale factor** (`× 0.1`; stored as integers).

`unmask(-9999)` before download, then `band[band <= -9999] = np.nan` after — masked pixels
survive the GeoTIFF round-trip as a sentinel and become NaN again locally.

`download_in_chunks()` is the same derived-chunk-size + 4-try-backoff pattern as
[lisflood_meteo_forcing.md](lisflood_meteo_forcing.md).

The fetch window is padded by ±8 days (`fetch_start = start - 8d`) so slot boundaries at the
ends of the period have neighbours.

---

## Idea 2 — one satellite signal, two land-cover series

MODIS gives one LAI value per pixel — a **mixture** of whatever grows there. LISFLOOD wants
LAI for the forest fraction and LAI for the other fraction *separately*, everywhere.

The method (from the LISFLOOD manual): find pixels that are almost purely one cover, take
their LAI as ground truth for that cover, and interpolate spatially to every basin cell.

```python
forest_mask = (mask > 0) & (fracforest >= 0.70)     # "pure" forest
other_mask  = (mask > 0) & (fracforest <= 0.20)     # "pure" non-forest
```

With fallbacks when a basin has no such pixels — this one has almost no dense forest:

```python
if not np.any(forest_mask):
    threshold = np.percentile(fracforest[mask > 0], 95)   # top 5% instead
    forest_mask = (mask > 0) & (fracforest >= threshold)
```

Sensible degradation, but **know when it fires**: in a basin with 3% max forest cover, "forest
LAI" is really "the leafiest cropland", and forest/other will be nearly identical.

### `idw_interpolate_t()` — inverse distance weighting

```python
tree = cKDTree(src_coords)                     # source = pure pixels for this cover
dist, idx = tree.query(tgt_coords, k=5)        # 5 nearest sources for every basin cell
dist = np.maximum(dist, 1e-12)
weights = 1.0 / (dist ** 2)                    # p=2
weights /= weights.sum(axis=1, keepdims=True)
return np.sum(weights * src_vals[idx], axis=1)
```

**IDW** predicts an unknown point as a weighted average of known neighbours, weighted by
`1/distance^p`. `p=2` makes influence fall off quadratically.

**`cKDTree`** is the reason this is fast. A brute-force nearest-5 search would be
`O(n_targets × n_sources)` — with ~500k basin cells and ~50k source pixels, 2.5×10¹⁰
comparisons *per timestep*. A k-d tree partitions space recursively so each query is
`O(log n)`, and `.query()` is vectorised over all targets at once. This is the standard tool
for nearest-neighbour work in scientific Python.

Distances are in **pixel units** (`np.where` gives row/col), not metres. For a square grid the
two are proportional, so relative weights are unaffected.

Edge cases handled: no sources → all nodata; one source → constant fill; exact hit → that
source gets weight 1.

The loop runs `idw_interpolate_t` twice per slot (forest, other) — 72 interpolations. Only
`mask > 0` cells are targets, so the outside-basin cells stay at `NODATA_VAL`.

---

## Output

```python
ds_forest = ds_bulk.copy(deep=True)
ds_forest["lai"].values = lai_forest_cube
ds_forest.to_netcdf(lai_forest_nc, encoding={"lai": {"_FillValue": -9999,
                                                     "zlib": True, "complevel": 4}})
```

Copying the bulk dataset preserves coordinates and attributes; only the values are swapped.
The attrs record provenance — `climatology_from`, `n_observations` — which is good practice:
the file says what it was built from.

The temp file `lai/lai_bulk_temp.nc` is deleted at the end.

## Gotcha

The written filenames are **`laif.nc` / `laio.nc`**, but `pipeline.py`'s `EXPECTED_OUTPUTS`
looks for `lai_forest.nc` / `lai_other.nc`. The step succeeds and is then reported as having
missing outputs. Pick one convention.
