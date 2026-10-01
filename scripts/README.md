# `scripts/` — LISFLOOD data-prep pipeline

This folder is a **data engineering system**. Its job: turn public remote-sensing archives
(SRTM, SoilGrids, CHIRPS, ERA5-Land, MODIS, a pan-India LULC asset) into the ~50 raster
files that the LISFLOOD hydrological model needs to simulate a river basin.

You do not need hydrology to read this code. You need to understand three things:

1. **A raster is a NumPy 2-D array plus a geo-header.** The header (an *affine transform*
   + a *CRS*) says where cell `[row, col]` sits on Earth.
2. **Every output file must sit on the exact same grid.** Same origin, same pixel size,
   same width/height, same CRS. LISFLOOD reads these as parallel arrays and indexes them
   by `[row, col]` — one pixel of misalignment silently corrupts the whole model.
3. **The heavy computation happens on Google's servers (Earth Engine), not your laptop.**
   Your laptop only downloads, aligns, masks, and writes.

---

## 1. Read this first: the four ideas the code is built on

### Idea 1 — `area.tif` is the "master grid"

One file, `inputs/maps/area.tif`, is generated first and *defines the coordinate system for
everything else*. Every later script opens it, reads its header, and forces its own output
onto that exact header. In `lisflood_utils.py` you'll see this parameter everywhere:

```python
save_aligned(array, out_path, "float32", -9999, like=AREA_TIF)
                                                 ^^^^^^^^^^^^
```

`like=` means "copy this file's geometry". This is the codebase's single most important
convention. Think of `area.tif` as a schema that every other table must conform to.

`area.tif` is a boolean mask: `1` inside the watershed, `0` outside. Its non-zero cells are
the model domain.

### Idea 2 — the affine transform

`rasterio` gives you `src.transform`, an `affine.Affine` object with six numbers. You only
ever use four:

| attribute | meaning |
|---|---|
| `t.c` | x-coordinate (easting) of the **top-left corner** |
| `t.f` | y-coordinate (northing) of the top-left corner |
| `t.a` | pixel width (`+300` m) |
| `t.e` | pixel height (`-300` m — **negative**, because rows go downward while northing goes up) |

So the pattern you'll see repeated in every script:

```python
x_coords = [t.c + (i + 0.5) * t.a for i in range(width)]   # +0.5 = pixel CENTRE
y_coords = [t.f + (i + 0.5) * t.e for i in range(height)]
```

and the reverse (world → pixel), in `make_outlets.py`:

```python
col = int(round((x_utm - t.c) / t.a))
row = int(round((y_utm - t.f) / t.e))
```

### Idea 3 — projected vs geographic CRS

The source data is in **EPSG:4326** (degrees, lat/lon). The pipeline works in **UTM**
(metres) because you cannot rasterise a 300-*metre* grid out of degrees. `pipeline_config.resolve_crs()`
derives the UTM zone from the ROI centroid:

```python
zone = int((lon + 180) // 6) + 1
return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"   # 326xx = north, 327xx = south
```

For this basin that resolves to EPSG:32644 (UTM 44N).

### Idea 4 — three file formats, three jobs

| format | extension | who reads it | why |
|---|---|---|---|
| GeoTIFF | `.tif` | the pipeline itself | easy to inspect in QGIS, `rasterio`-native, the working format |
| NetCDF | `.nc` | LISFLOOD & LISVAP | supports a `time` dimension → 8000-day forcing stacks in one file |
| PCRaster | `.map` | older LISFLOOD setups | legacy; `gdal_convert_pcraster()` exists but the pipeline now emits `.nc` |

Nearly every script ends with a `convert_to_netcdf()` step: write `.tif` → convert → `.nc`.

---

## 2. The flow

```
                        shapefiles/*.shp        ← get_hydrobasins_roi.py / get_watershed.py
                              │                   (one-off: pick your basin)
                              ▼
        ┌──────────── topographyMapsScript.py ─────────────┐
        │  rasterise ROI → area.tif  ★ MASTER GRID ★       │
        │  SRTM 30 m → pysheds → LDD, gradient, elvstd     │
        └──────────────────────┬───────────────────────────┘
                               │  area.tif  (everything below aligns to it)
     ┌──────────┬──────────────┼───────────────┬────────────┬───────────┐
     ▼          ▼              ▼               ▼            ▼           ▼
  frac_lulc  channnels.py   soil_prepro   meteo_forcing  lai_forcing  LisVap/
  (fractions) (chan geom)   (15 maps)     (pr, ta)       (laif/laio)  generate_lat_nc
     │          │                                                        │
     ▼          ▼                                                        ▼
 lulc_cover  make_outlets.py                                    lisvap_inputs.py
 (10 maps)   (gauge pixel)                                      (tn,tx,rg,ws,pd)
                                                                         │
                                                                         ▼
                                                                  run_lisvap.py
                                                                  (Docker) → et,e,es
                               │
                               ▼
                    settings/*.xml  →  LISFLOOD (Docker)  →  outputs/cold, outputs/warm
                                                                  │
                                                                  ▼
                                                 scripts/display/generate_html_plots.py
```

`pipeline.py` is the orchestrator that runs those boxes in order and verifies the outputs.

---

## 3. Folder map

| path | what it is |
|---|---|
| `pipeline/` | the 12 scripts that build LISFLOOD inputs. **Start here.** |
| `pipeline/LisVap/` | 3 scripts + XML that produce evaporation forcing via the LISVAP Docker image |
| `calibration/` | gauge selection & GRRR discharge plotting (model-evaluation side) |
| `display/` | turns run outputs into PNGs/GIFs for the HTML report |
| `docs/` | **one explainer per script — the detailed walkthroughs** |

---

## 4. Per-script documentation

Read them in this order.

**Foundations (read before anything else)**
| doc | script |
|---|---|
| [pipeline_config.md](docs/pipeline_config.md) | `pipeline/pipeline_config.py` — every knob in the system |
| [lisflood_utils.md](docs/lisflood_utils.md) | `pipeline/lisflood_utils.py` — the shared toolbox |
| [pipeline.md](docs/pipeline.md) | `pipeline/pipeline.py` — the orchestrator |

**Choosing a study area**
| doc | script |
|---|---|
| [get_hydrobasins_roi.md](docs/get_hydrobasins_roi.md) | `pipeline/get_hydrobasins_roi.py` |
| [get_watershed.md](docs/get_watershed.md) | `pipeline/get_watershed.py` |

**Building the inputs (run order)**
| # | doc | script |
|---|---|---|
| 1 | [topographyMapsScript.md](docs/topographyMapsScript.md) | `pipeline/topographyMapsScript.py` |
| 2 | [lisflood_frac_lulc_preprocessing.md](docs/lisflood_frac_lulc_preprocessing.md) | `pipeline/lisflood_frac_lulc_preprocessing.py` |
| 3 | [lisflood_lulc_cover.md](docs/lisflood_lulc_cover.md) | `pipeline/lisflood_lulc_cover.py` |
| 4 | [lisflood_soil_preprocessing.md](docs/lisflood_soil_preprocessing.md) | `pipeline/lisflood_soil_preprocessing.py` |
| 5 | [channnels.md](docs/channnels.md) | `pipeline/channnels.py` |
| 6 | [make_outlets.md](docs/make_outlets.md) | `pipeline/make_outlets.py` |
| 7 | [lisflood_meteo_forcing.md](docs/lisflood_meteo_forcing.md) | `pipeline/lisflood_meteo_forcing.py` |
| 8 | [lisflood_lai_forcing.md](docs/lisflood_lai_forcing.md) | `pipeline/lisflood_lai_forcing.py` |
| 9 | [LisVap.md](docs/LisVap.md) | `pipeline/LisVap/` (all three scripts) |

**Downstream**
| doc | script |
|---|---|
| [calibration.md](docs/calibration.md) | `calibration/gauges_in_roi.py`, `calibration/plot_grrr_discharge.py` |
| [display.md](docs/display.md) | `display/generate_html_plots.py`, `display/animate_output.py` |

---

## 5. Libraries & APIs

### First: why does this need 14 libraries?

It looks like a lot. It isn't sprawl — it's the shape of the geospatial Python ecosystem, and
it comes from three facts:

**Fact 1 — most of these are thin Python skins over the same two C libraries.**
`rasterio`, `geopandas`, `pyproj`, `affine` and the `gdal_*` command-line tools are all fronts
on **GDAL** (data formats) and **PROJ** (coordinate maths). Nobody wrote five competing
libraries; one C stack got five Python front-ends, each specialised for a different *data
model*: raster, vector, a bare coordinate pair, a 6-number transform, a shell command. You
don't choose between them — you reach for whichever matches the shape of the thing in your
hand. Installing one usually installs the others.

**Fact 2 — a raster with a time axis is a different problem from a raster without one.**
GeoTIFF/`rasterio` has no concept of time; its model is "N bands of a 2-D grid". That is
perfect for `area.tif` and useless for 8,036 days of rainfall. So the pipeline carries a second
raster stack — NetCDF, via `xarray`/`netCDF4` — for anything with a `time` dimension. Two
formats because there are genuinely two kinds of data here, and because LISFLOOD itself
demands NetCDF for forcing.

**Fact 3 — three things happen somewhere other than your laptop.** Earth Engine (`ee`) runs on
Google's servers, LISVAP and LISFLOOD run in Docker containers, and some GDAL operations only
exist as CLI tools. Each needs its own client: an API binding, `subprocess`, `subprocess`.

Group them by job and the list stops looking arbitrary:

| layer | libraries | the question it answers |
|---|---|---|
| **compute** | `numpy` | how do I do arithmetic on a grid? |
| **raster I/O (2-D)** | `rasterio` | how do I read/write a GeoTIFF *with its geo-header*? |
| **raster I/O (3-D + time)** | `xarray`, `netCDF4` | how do I read/write an 18 GB time-series cube? |
| **vector I/O** | `geopandas` | how do I handle the ROI polygon? |
| **coordinates** | `pyproj`, `affine` | where on Earth is pixel `[row, col]`? |
| **remote data** | `ee`, `geemap` | how do I get 22 years of satellite data without downloading a petabyte? |
| **domain algorithms** | `pysheds`, `scipy` | flow routing; nearest-neighbour search |
| **time** | `pandas` | date ranges, day-of-year, rolling means |
| **output** | `matplotlib` | figures |
| **external processes** | GDAL CLI, Docker (via `subprocess`) | things with no Python equivalent |

### Each one, and why it's here

| library | what it is | why this pipeline needs it |
|---|---|---|
| **numpy** | N-dimensional arrays + vectorised maths | The substrate. Every raster in this codebase *is* an `ndarray`; every other library hands you one or takes one. Masking, clipping, the PTF equations, the block-reduce for `elvstd` — all numpy. Without it you'd loop in Python over 500,000 cells. |
| **rasterio** | Pythonic GDAL for rasters | Reads/writes GeoTIFF **together with its geo-header** (`.transform`, `.crs`, `.profile`, `.nodata`). That header is the whole point — a bare PNG/array loses where the data is. This is the pipeline's working format because it's inspectable in QGIS and cheap to re-open. |
| **rasterio.warp** | GDAL's resampling engine | `reproject()` moves an array from one grid to another — different CRS, different pixel size, different origin. It is *the* function that enforces the master-grid rule; `lisflood_utils.reproject_to_grid` is a thin wrapper on it. |
| **xarray** | labelled N-D arrays; friendly NetCDF | Lets you write `da.mean(dim=("y","x"))` instead of remembering that y is axis 1. Used for building small/medium NetCDFs (`lat.nc`, LAI, the static-map conversion) and for reading outputs in the plotting scripts. |
| **netCDF4** | the low-level NetCDF C binding | Used **only** where xarray can't cope: the meteo and LISVAP forcing cubes are 18 GB, and xarray wants the array in memory. netCDF4 lets you create an empty file at full size and write one timestep at a time. The trade is convenience for control — see §6B. |
| **geopandas** | pandas + geometry | The ROI is a polygon, not a grid. `gpd.read_file` → `.to_crs()` → `.union_all()` → `.within()` covers reprojecting the watershed and testing which gauges fall inside it. Only three scripts touch vectors, but nothing else does this job. |
| **pyproj** | the PROJ coordinate-transform engine | Converts a *single coordinate pair* between CRSs — a gauge's lat/lon into UTM metres (`make_outlets.py`), or the whole grid back to degrees (`generate_lat_nc.py`). geopandas uses it internally; these scripts call it directly because they have loose coordinates, not a GeoDataFrame. **Always with `always_xy=True`.** |
| **affine** | 6-number affine transform objects | Imported explicitly once, to *build* a transform rather than read one: `info.transform * Affine.scale(1/10, 1/10)` creates a grid exactly 10× finer and perfectly nested inside the 300 m grid, for the sub-grid elevation std-dev. rasterio hands you `Affine` objects everywhere else. |
| **earthengine-api (`ee`)** | client for Google Earth Engine | The reason this pipeline is possible on a laptop. SRTM, SoilGrids, CHIRPS, ERA5-Land and MODIS are petabyte archives; `ee` lets you send a *recipe* ("mask to class 6, average to 300 m, clip to this box") and get back only the answer. Lazy and declarative — nothing runs until a terminal call. |
| **geemap** | download helpers for `ee` | `ee` can build an image but exporting it to your disk is fiddly (signed URLs, zip handling). `geemap.ee_export_image` / `ee_export_vector` are one-liners for that. **Caveat:** it swallows failures and returns normally, which is why every download in this repo checks `os.path.exists()` afterwards. |
| **pysheds** | terrain hydrology in pure Python/numpy | Pit filling, depression filling, flat resolution, D8 flow direction, flow accumulation. These are non-trivial graph algorithms over a DEM, and this is the standard Python implementation. GEE cannot do them the way the pipeline needs (it must run on the *upscaled* LDD), so this runs locally. |
| **scipy** (`spatial.cKDTree`) | scientific algorithms; here, a k-d tree | Only one use: nearest-5-neighbour search for the LAI IDW interpolation. Brute force would be ~500k targets × ~50k sources per timestep; a k-d tree makes each query `O(log n)` and vectorises over all targets. Importing scipy for one class is normal — it's what it's for. |
| **pandas** | tables + the time axis | Not used for tables much; used for **dates**. `pd.date_range(start, end, freq="D")` builds the 8,036-day index, `DatetimeIndex.dayofyear` drives the LAI ten-day slots, `.rolling(center=True)` does the smoothing in the plots. Date arithmetic is easy to get wrong by hand. |
| **matplotlib** | plotting | Every PNG and GIF. Always `matplotlib.use("Agg")` *before* importing pyplot — the non-interactive backend, required for headless/scripted use. |
| **requests** | HTTP | One use: the offline SRTM fallback in `lisflood_utils.download_srtm_tile`, fetching `.hgt.gz` tiles from an S3 bucket. Not on the main path. |
| **GDAL CLI** (`subprocess`) | `gdal_rasterize`, `gdalwarp`, `gdal_translate` | Shelled out to for the polygon→raster burn and the 30 m→300 m warp. Why not rasterio? Because `gdal_rasterize -tap` and `gdalwarp -r average -tap` have no clean Python equivalent, and `-tap` (target-aligned pixels) is exactly what pins the master grid to round coordinates. Pragmatism over purity. |
| **Docker** (`subprocess`) | container runtime | LISVAP and LISFLOOD are PCRaster/Fortran-era models with painful dependency stacks. The official images pin all of it; `docker run -v REPO:/input` is the whole integration. |

### The one Earth Engine idea you need

`ee` is **lazy and declarative**. `ee.Image("USGS/SRTMGL1_003").multiply(2)` computes nothing —
it builds a computation graph. Work happens only when you call a *terminal* method:

- `.getInfo()` — pull a small result (a number, a list of band names) back to Python
- `geemap.ee_export_image(...)` — render the image to a GeoTIFF and download it

That laziness is why the scripts chain `.reduceResolution().reproject().rename()` freely: it
costs nothing until the last line. Two server limits then dominate the design of this codebase:

- **~32–50 MB per download request.** Hence all the tiling/chunking code.
- **~5×10⁷ pixels for a `reproject()`.** Aggregating a 10 m asset to 300 m makes EE materialise
  the *native* 10 m grid over the whole region first — so the limit bites on input size, not
  output size.

Worse, `geemap.ee_export_image` **swallows failures**: it prints a message, returns normally,
and leaves no file. So the codebase's universal idiom is:

```python
if not os.path.exists(path):
    raise RuntimeError("Earth Engine export produced no file")
```

"Did the file appear?" is the only reliable success signal. You'll see this in
`ee_export_tiled()`, and again in every forcing script's `download_in_chunks()`.

### Pairs that look redundant but aren't

These are the ones that make the list feel bloated. Each pair is a real distinction:

| pair | why both |
|---|---|
| `rasterio` vs GDAL CLI | Same C library. rasterio for reading pixels into numpy; the CLI for `-tap`-aligned rasterise/warp, which the Python API doesn't expose cleanly. |
| `xarray` vs `netCDF4` | xarray is comfortable but in-memory. netCDF4 is verbose but streams. The 18 GB cubes forced the split — see the OOM story in §6B. |
| `geopandas` vs `pyproj` | geopandas when you have a *table of geometry*; pyproj when you have *two floats*. Using geopandas for one gauge coordinate would be absurd overhead. |
| `ee` vs `geemap` | `ee` builds the computation; `geemap` downloads the result. Separate concerns, separate packages. |
| `pysheds` vs `ee` terrain ops | GEE has terrain functions, but the pipeline needs accumulation computed on **its own upscaled LDD**, which only exists locally. |

### Could it be smaller?

Honestly, a bit. `geemap` is replaceable with `ee.Image.getDownloadURL()` + `requests`;
`requests` itself is only the unused SRTM fallback; `affine` is reachable through
`rasterio.transform`. That gets you to about eleven, and none of the removals make the code
clearer. Everything else is load-bearing: drop `rasterio` and you can't read a GeoTIFF, drop
`pysheds` and you're implementing D8 flow accumulation yourself.

The one genuine redundancy is **five near-identical copies of `validate_alignment()`** across
the scripts — that's duplication in *this* codebase, not in its dependencies, and it's the
obvious refactor.

## 6. Two recurring engineering patterns

### A. Tile / chunk to stay under the request cap

Spatial data (topo, LULC, soil, channels) is split **spatially** into an `n × n` grid of tiles,
downloaded, then mosaicked with `rasterio.merge`. On failure the tile count *doubles* and it
retries — see `lisflood_utils.ee_export_tiled()`. This is deliberately adaptive rather than
predictive, because the server has several undocumented limits.

Time-series data (meteo, LAI, LISVAP) is split **temporally** into band chunks, sized from the
grid so each request is ~25 MB:

```python
mb_per_band = info.width * info.height * 4 / 1e6
chunk_size  = max(1, int(25 / mb_per_band))
```

### B. Stream to disk instead of holding the cube in RAM

22 years of daily data on an 865×642 grid is `8036 × 865 × 642 × 4 bytes ≈ 17.9 GB`. Building it
with `np.concatenate` needs that twice → the OOM killer. Both `lisflood_meteo_forcing.py` and
`LisVap/lisflood_meteo_lisvap_inputs.py` instead create the NetCDF **empty with its full time
dimension**, then write one day at a time:

```python
v = nc.createVariable(var, "f4", ("time","y","x"), chunksizes=(1, H, W))
...
v[t, :, :] = final     # peak memory = one chunk (~25 MB), independent of run length
```

`chunksizes=(1, H, W)` matters: it makes "one timestep" the physical unit on disk, so a
single-day write touches exactly one chunk.

---

## 7. Running it

```bash
cd scripts/pipeline

python pipeline.py --check          # what exists / what's missing — start here
python pipeline.py                  # everything, in dependency order
python pipeline.py --step topo      # one step
python pipeline.py --ini-only       # regenerate settings/lisflood_settings.xml
```

Prerequisites: a Google Earth Engine account (`ee.Authenticate()` runs on first use),
GDAL command-line tools on `PATH`, and Docker running (for the LISVAP step).

Everything is **resumable**: each script checks `os.path.exists()` before downloading, so a
re-run after a crash picks up where it stopped. To force a re-download, delete the relevant
file from `inputs/raw/`.

---

## 8. Known wrinkles

- `pipeline.py`'s `EXPECTED_OUTPUTS["lai"]` looks for `lai_forest.nc` / `lai_other.nc`, but
  `lisflood_lai_forcing.py` writes `laif.nc` / `laio.nc`. The step runs fine and then reports
  its outputs as missing.
- `pipeline.py`'s `STEP_DEPS` has no entries for `lulc_cover`, `lisvap_*` — those steps skip
  the dependency check.
- `pipeline_config.resolve_mean_elevation()` looks for `dem.tif`, but the topography script
  writes `dem_300m.tif`, so it always returns the 0.0 fallback. Nothing currently calls it.
- `channnels.py` is spelled with three n's. It's the filename; leave it or rename everywhere.
- `generate_ini()` writes a settings XML mentioning `[FILL IN]`/`[CALIBRATE]` but the template
  no longer contains those markers, so it always reports "0 items need manual attention".
  The hand-maintained runs live in `settings/prerun.xml`, `cold_start.xml`, `warm_start.xml`.
