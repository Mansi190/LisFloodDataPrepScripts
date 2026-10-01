# `pipeline/lisflood_meteo_forcing.py`

**258 lines. Step 7.** Downloads 22 years of daily precipitation and temperature and writes
them as NetCDF time stacks.

Outputs: `inputs/meteo/pr.nc` (CHIRPS precipitation, mm/day) and `inputs/meteo/ta.nc`
(ERA5-Land mean air temperature, °C). Each is `8036 × 865 × 642` float32.

This script is where the pipeline stops being a raster-alignment problem and becomes a
**scale problem**. Both of its main functions exist because of a limit that was hit.

---

## The two constraints

| constraint | magnitude | the response |
|---|---|---|
| GEE band cap | `toBands()` maxes at 5000 bands; 22 years = 8036 days | split by **year** |
| GEE request cap | ~32–50 MB per download | split each year into **band chunks** |
| RAM | the full cube is 17.9 GB, ×2 to concatenate, on a 17 GB machine | **stream** to disk |

---

## `fetch_gee_timeseries()` — the download

### Per-image processing

```python
def process_image(img, band_name, var_name, scale_factor=1.0, offset=0.0):
    val = img.select(band_name).multiply(scale_factor).add(offset)
    upscaled = val.reduceResolution(ee.Reducer.mean(), maxPixels=1024) \
                  .reproject(crs=str(info.crs), scale=300)
    date_str = img.date().format('YYYYMMdd')
    return upscaled.toFloat().rename([date_str]) \
                   .set('system:time_start', img.get('system:time_start'))
```

Three things happen per day:
- unit fix via `offset` (ERA5 gives kelvin; `offset=-273.15` → °C),
- resample to the canonical grid,
- **rename the band to its date**. That's how identity survives `toBands()`, which
  concatenates a collection into one multi-band image and would otherwise lose the ordering.

`.toFloat()` is a bandwidth optimisation with a comment worth reading: ERA5-Land bands are
`double` at source (8 bytes/px). Uncast, the same band count that fits for CHIRPS (float,
4 bytes) blows the request cap for ERA5. Since the NetCDF stores float32 anyway, casting loses
nothing.

### Chunk sizing — derived, not guessed

```python
mb_per_band = info.width * info.height * 4 / 1e6      # 865×642×4 ≈ 2.22 MB
chunk_size  = max(1, int(25 / mb_per_band))           # → 11 bands per request
```

The number adapts to the grid. Hardcoding 60 bands (an earlier version) works on a small basin
and fails on a large one.

### The year-block loop

```python
for yr in years:
    col = ee.ImageCollection(collection_id).filterBounds(region).filterDate(y0, y1+1day)
    img = col.map(lambda im: process_image(...)).toBands()
    names = img.bandNames().getInfo()
    for i in range(0, len(names), chunk_size):
        f = f"{prefix}_raw_{yr}_{i}.tif"
        if not os.path.exists(f):
            ... download with retry ...
```

`filterDate` is half-open — hence `+ pd.Timedelta(days=1)` so 31 December is included.

The `if not os.path.exists(f)` is the resume mechanism: ~1500 files per run, and a run takes
hours. A crash at file 1400 costs you file 1400, not 1400 files. This is why
`inputs/raw/` is full of `pr_raw_2011_319.tif`-style names.

### Retry with exponential backoff

```python
for attempt in range(4):
    geemap.ee_export_image(...)
    if os.path.exists(f): break
    if attempt < 3:
        wait = 5 * (3 ** attempt)        # 5s, 15s, 45s
        time.sleep(wait)
else:
    raise RuntimeError(f"export produced no file after 4 tries: {f}")
```

Note the `for...else`: the `else` runs only if the loop finished **without** `break`. A neat
Python idiom for "all retries exhausted".

The comment states the reasoning plainly: at ~1500 requests per run, transient failures are
*expected*, not exceptional. And since `geemap` swallows errors and returns normally, "no file"
is the only signal available.

---

## `assemble_netcdf()` — streaming, the part to actually study

The docstring documents a real OOM kill. The old version built a list of arrays, concatenated,
then allocated a second cube: 17.9 GB × 2 on a 17 GB machine. Every download had succeeded;
the process died at the last step.

The fix — **create the file with its full shape first, fill it incrementally**:

```python
# 1. size the time dimension WITHOUT reading any pixels
total_bands = sum(rasterio.open(p).count for p in tif_paths)

# 2. create an empty NetCDF with the full time dimension
with netCDF4.Dataset(nc_path, "w", format="NETCDF4") as nc:
    nc.createDimension("time", num_days)
    nc.createDimension("y", info.height)
    nc.createDimension("x", info.width)
    v = nc.createVariable(var_name, "f4", ("time","y","x"),
                          zlib=True, complevel=4, fill_value=NODATA_VAL,
                          chunksizes=(1, info.height, info.width))

    # 3. write one day at a time
    for tif_path in tif_paths:
        arr = rasterio.open(tif_path).read()
        for b in range(arr.shape[0]):
            band = arr[b].astype(np.float32)
            band[band == src_nd] = np.nan
            aligned = snap_to_grid(band, AREA_TIF, np.nan)
            final = np.where(mask > 0, aligned, NODATA_VAL)
            final[np.isnan(final)] = NODATA_VAL
            v[t, :, :] = final
            t += 1
        del arr
```

**Peak memory ≈ one chunk (~25 MB), independent of run length.** You could extend to 50 years
without touching the assembly code. This is the generic answer to "the data doesn't fit in
RAM": don't materialise it — create the container at full size and stream through it.

Two details that make it work:

- **`chunksizes=(1, H, W)`** — NetCDF4 stores data in chunks, and a write touches whole chunks.
  Making one timestep the physical unit means `v[t,:,:] = final` writes exactly one chunk with
  no read-modify-write.
- **`zlib=True, complevel=4`** — level 4 is the usual compression/CPU sweet spot. Meteorological
  fields are spatially smooth and compress well.

The time axis is written as CF-compliant numbers:

```python
tv.units = "days since 1970-01-01 00:00:00"; tv.calendar = "standard"
tv[:] = netCDF4.date2num(dates.to_pydatetime(), tv.units, tv.calendar)
```

If GEE returned fewer days than expected the code warns and truncates rather than failing —
`num_days = total_bands`.

## Caching at the NetCDF level too

```python
if os.path.exists(pr_nc) and os.path.exists(ta_nc):
    log("Existing NetCDF files found. Loading them...")
```

So a re-run skips assembly entirely. **Delete `pr.nc`/`ta.nc` if you change the mask or the
date range** — otherwise you silently keep the old stack.

## Data sources

| variable | collection | resolution | note |
|---|---|---|---|
| `pr` | `UCSB-CHG/CHIRPS/DAILY` | ~5.5 km | satellite + gauge blend; from 1981 |
| `ta` | `ECMWF/ERA5_LAND/DAILY_AGGR` | ~11 km | reanalysis; band `temperature_2m`, K → °C |

Both are **coarser than 300 m**, so `reduceResolution` is really smooth interpolation, not
aggregation. That's an honest limitation: the model runs at 300 m but its rainfall forcing has
5.5 km detail.
