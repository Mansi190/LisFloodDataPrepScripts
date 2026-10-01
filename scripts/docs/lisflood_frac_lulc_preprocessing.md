# `pipeline/lisflood_frac_lulc_preprocessing.py`

**271 lines. Step 2.** Converts a 10 m land-cover classification into the four land-cover
*fractions* LISFLOOD partitions each cell by.

Outputs (in `inputs/maps/fraction/`): `lulc`, `fracsealed`, `fracwater`, `fracforest`,
`fracother` — `.tif` + `.nc`.

## The concept: fractional land cover

LISFLOOD does not model a 300 m cell as "forest". It models it as *e.g.* 20% sealed,
5% water, 45% forest, 30% other, and runs the water balance separately on each fraction, then
sums. So the pipeline's job is: for each 300 m cell, what proportion of it is each class?

**The invariant: the four fractions must sum to exactly 1.0 in every cell.** The script
verifies this at the end.

## The trick: a boolean mask averaged is a fraction

```python
builtUpMask = lulc_label.eq(1)                        # 1 where built-up, 0 elsewhere
fImpermeable = builtUpMask.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                          .reproject(crs=str(info.crs), scale=300)
```

Mean of a 0/1 mask over 900 sub-pixels = the fraction that are 1. That's the whole method, and
it's exact — no interpolation, no assumptions.

`maxPixels=1024` caps how many native pixels EE will aggregate per output pixel. 300 m / 10 m
= 30, so 30×30 = 900 ≤ 1024. If you raised `RESOLUTION_M` you'd have to raise this too.

## Source and class mapping

```python
lulc10m = ee.Image("projects/corestack-datasets/assets/datasets/"
                   "LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
lulc_label = lulc10m.select('predicted_label')
```

A CoRE Stack pan-India LULC asset at 10 m. Class → LISFLOOD fraction:

| classes | mask | LISFLOOD fraction | meaning |
|---|---|---|---|
| `1` | `builtUpMask` | `fracsealed` | impervious — rain runs off immediately |
| `2–4` | `waterMask` | `fracwater` | open water — evaporates at potential rate |
| `6` | `forestMask` | `fracforest` | trees — deep roots, high interception |
| `5`, `7`, `8–12` | `otherMask` | `fracother` | crops, grass, bare — the default bucket |

```python
otherMask = lulc_label.eq(5).Or(lulc_label.eq(7)) \
                      .Or(lulc_label.gte(8).And(lulc_label.lte(12)))
```

The four masks are **mutually exclusive and exhaustive** over classes 1–12, which is exactly
what makes the fractions sum to 1.

## Categorical vs continuous downsampling — side by side

```python
lulc30m = lulc_label.reduceResolution(reducer=ee.Reducer.mode(), ...)   # categorical → MODE
fWater  = waterMask.reduceResolution(reducer=ee.Reducer.mean(), ...)    # 0/1 → MEAN
```

`mode` for the class map (most common class wins — averaging class IDs is meaningless);
`mean` for the fractions. The same distinction reappears at the local resampling step:

```python
resamp = "nearest" if name == 'lulc' else "bilinear"
```

The 5 bands are stacked into one `ee.Image` and downloaded through
`ee_export_tiled(combined, raw_tif, 300, info.crs, region)` — this is the call that triggers
the "Reprojection output too large" tile seeding, since 10 m native under a large ROI blows
past 5×10⁷ native pixels.

## Local processing — `process_local_lulc()`

For each band: nodata → NaN, reproject onto the canonical grid, then

```python
if name != 'lulc':
    arr = np.clip(arr, 0.0, 1.0)                    # bilinear can overshoot slightly
    arr = np.where(np.isnan(arr), 0.0, arr)
final = np.where(mask_arr > 0, arr, -9999)          # outside basin = nodata
```

Note the ordering: NaN → 0 happens **before** masking. Inside the basin a missing fraction
becomes 0 (a real value); outside it becomes −9999 (nodata). Getting that order wrong would
put −9999 into the basin.

## `validate_fractions()` — the invariant check

```python
total = fsealed + fwater + fforest + fother
sum_is_1 = np.abs(total[mask > 0] - 1.0) < 1e-4
```

`1e-4` rather than `==` because float32 arithmetic through GEE aggregation and bilinear
resampling won't give exact 1.0. This only **warns** — it doesn't exit. If it warns, suspect
either an unmapped class or bilinear leaking across the basin edge.

## `_write_convert_script()`

Emits a `manual_convert.sh` with `gdal_translate` lines for every output, as an escape hatch
if the in-process NetCDF conversion failed (usually a missing GDAL). Note the shell script
uses the real `gdal_translate -of netCDF`, which has the y-axis flip issue described in
[lisflood_utils.md](lisflood_utils.md) — use it only as a last resort.

## `lulc.tif` is a dependency, not just an output

`pipeline_config.LULC_ALIGNED` points at `fraction/lulc.tif`. Both
`lisflood_soil_preprocessing.py` and `lisflood_lulc_cover.py` read it to build their domain
mask (`inside & valid_lulc`). `lisflood_lai_forcing.py` reads `fracforest.tif`. So this script
must run before soil, lulc_cover, and lai.
