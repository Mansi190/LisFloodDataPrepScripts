# `pipeline/lisflood_utils.py`

**422 lines. The shared toolbox.** Every pipeline script imports from here. If you read one
file to understand the codebase's mechanics, read this one.

Six groups of helpers:

---

## 1. Logging & setup

```python
def log(msg, kind="INFO"):
    icons = {"INFO":"✔", "STEP":"▶", "WARN":"⚠", "ERROR":"✘", "DONE":"★"}
    print(f"  {icons.get(kind,'·')}  {msg}")
```

Deliberately not the `logging` module — these are long interactive runs and the icons let you
scan hundreds of lines of console output.

`check_imports(packages)` tries `__import__` on each name and, if any fail, prints
`pip install <missing>` and `sys.exit(1)`. It **fails fast**: better to die in second 1 than
after a 40-minute download.

`make_dirs(path)` is `Path(path).mkdir(parents=True, exist_ok=True)`.

---

## 2. `init_ee(project)` — Earth Engine auth

```python
try:
    ee.Initialize(project=gee_project)
except Exception:
    ee.Authenticate()          # opens a browser, once
    ee.Initialize(project=gee_project)
```

Try the fast path; fall back to interactive auth. Credentials cache in `~/.config/earthengine`,
so the browser opens only the first time.

---

## 3. Grid helpers — **the heart of the file**

```python
GridInfo = namedtuple("GridInfo", "transform width height crs profile")
_GRID_CACHE = {}
```

A `namedtuple` because it's an immutable value object with named fields — `info.transform` reads
better than `info[0]`, and you can't accidentally mutate the reference grid.

`_GRID_CACHE` is a module-level dict keyed by file path. `save_aligned` is called ~50 times
across a run; without the cache each call would re-open `area.tif`. Standard memoisation.

### `load_grid(area_path)`

The entry point every script calls first. Returns `(GridInfo, mask_array)`, seeds the cache,
and prints a summary. If `area.tif` is missing it tries `area.map`, then exits with
"Run topographyMapsScript.py first". A **guard clause** — the alternative is a confusing
crash 200 lines later.

### `snap_to_grid(array, like, nodata)`

Crops or zero-pads an array to the reference's exact `(height, width)`.

```python
if h == info.height and w == info.width:
    return array                     # fast path, no copy
cropped = array[:info.height, :info.width]
if cropped is too small:
    out = np.full((H, W), nodata); out[:ch, :cw] = cropped; return out
return cropped
```

Why needed: GEE returns tiles sized by *its* internal snapping, often off by a pixel or two.
This is a last-resort geometric fixup — pure array indexing, no resampling.

### `save_aligned(array, out_path, dtype, nodata, like)`

The universal writer. Copies `like`'s profile, overrides dtype/nodata/compression, snaps, writes.

```python
profile.update(driver="GTiff", dtype=dtype, nodata=nodata, count=1, compress="lzw")
dst.write(snap_to_grid(array, like, nodata).astype(dtype), 1)
```

Because every write goes through here with `like=AREA_TIF`, **alignment is enforced by
construction** rather than checked afterwards. (The scripts *also* check afterwards, in
`validate_alignment` — belt and braces.)

### `reproject_to_grid(src_array, src_transform, src_crs, like, src_nodata, dst_nodata, method)`

Wraps `rasterio.warp.reproject`. The source can be any CRS/resolution; the destination is
always the canonical grid.

The `resampling_method` string maps to a `Resampling` enum, and **choosing it correctly is a
real modelling decision**:

| method | use for | why |
|---|---|---|
| `nearest` | categorical data (LULC classes, channel mask, LDD codes) | averaging class 3 and class 7 into 5 is meaningless |
| `bilinear` | smooth continuous fields (clay %, temperature) | smooth interpolation |
| `average` | downsampling continuous data (30 m DEM → 300 m) | conserves the mean |
| `mode` | downsampling categorical data | most common class wins |

---

## 4. Format conversion

### `gdal_convert_pcraster(tif, map, type)`
Shells out to `gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=<type>`. Types:
`VS_SCALAR` (continuous), `VS_NOMINAL` (classes), `VS_BOOLEAN`, `VS_LDD` (flow directions).
Returns `True`/`False` rather than raising, and logs stderr — legacy path, mostly unused now.

### `gdal_convert_netcdf(tif, nc)` — **read the docstring, it's the interesting one**

This does *not* shell out to GDAL despite the name. It builds the NetCDF by hand with xarray:

```python
y_coords = [transform.f + (i + 0.5) * transform.e for i in range(height)]   # DESCENDING
ds = xr.Dataset({"Band1": (["y","x"], data)}, coords={"y": y_coords, "x": x_coords})
ds.to_netcdf(nc_path, encoding={"Band1": {"_FillValue": nodata, "zlib": True, "complevel": 4}})
```

**Why hand-rolled:** `gdal_translate -of netCDF` follows the CF-1.5 convention and writes the
y-axis **bottom-up** (ascending), flipping the array relative to the GeoTIFF. LISVAP reads
these files assuming top-down. A vertically mirrored basin is the kind of bug that produces
plausible-looking but completely wrong output. Writing the coordinates explicitly keeps them
descending, matching the TIFF transform.

The variable is always named `Band1` — that's what LISFLOOD's settings XML expects for static
maps.

---

## 5. SRTM tile helpers (currently unused fallback)

`download_srtm_tile(lat, lon, raw_dir)` fetches `N26E087.hgt.gz` from the AWS
`elevation-tiles-prod` S3 bucket, gunzips it, and calls `hgt_to_tif`.

`hgt_to_tif` is a nice small lesson in binary formats:

```python
data = np.fromfile(hgt_path, dtype=">i2").astype(np.float32)   # big-endian int16
data = data.reshape((3601, 3601))                              # 1-arcsec tile
data[data == -32768] = -9999                                   # SRTM void → our nodata
t = from_bounds(lon, lat, lon+1, lat+1, 3601, 3601)            # 1°×1° tile
```

`merge_tiles` mosaics with `rasterio.merge.merge`.

These exist as an offline path; the pipeline currently pulls SRTM through Earth Engine instead.

---

## 6. `ee_export_tiled(image, filename, scale, crs, region, ...)` — the download engine

Used by the LULC, soil, and channel scripts. Its docstring is the best explanation of the
GEE constraints in the repo. Two *different* server limits, needing different tile counts:

- **Download size** — one request caps at ~32–50 MB.
- **Reprojection size** — `reduceResolution().reproject()` from a fine native asset (10 m LULC)
  makes EE materialise the **native** grid over the whole region. Past ~5×10⁷ pixels it fails
  with "Reprojection output too large", *no matter how small the output is*.

So it seeds the tile count from **both** limits and takes the max:

```python
out_mb = (area_m2 / scale**2) * 4 * n_bands / 1e6
n = ceil(sqrt(out_mb / 25))                       # seed 1: payload

native = image.projection().nominalScale().getInfo()
if 0 < native < scale:
    n = max(n, ceil(sqrt((area_m2 / native**2) / 5e7)))   # seed 2: native grid
```

Then the retry loop:

```python
for attempt in range(max_attempts):
    try:
        ... export n×n tiles, mosaic with rio_merge, delete tiles, return
    except RuntimeError:
        clean up partial tiles
        n *= 2                    # exponential backoff in SPACE, not time
        log("retrying at {n}x{n} tiles")
```

**Doubling instead of predicting** is the key design call. The server has several undocumented
limits; rather than model them all, guess low and adapt.

And the failure detection:

```python
def _export(img, path, geom):
    geemap.ee_export_image(...)
    if not os.path.exists(path):
        raise RuntimeError(f"Earth Engine export produced no file: {path}")
```

`geemap.ee_export_image` **returns normally on failure**, printing a message and leaving no
file. Wrapping it so "no file" becomes an exception is what makes the retry loop possible at
all. This idiom recurs in every download path in the repo.

Note the resume behaviour: `if not os.path.exists(tile)` before each tile, so an interrupted
run re-uses the tiles it already got.
