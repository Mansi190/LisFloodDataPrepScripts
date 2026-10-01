# `pipeline/topographyMapsScript.py`

**487 lines. Step 1 — the most important script in the repo.** It creates `area.tif`, the
master grid every other script aligns to, and derives the terrain maps that define how water
flows.

Outputs (in `inputs/maps/`): `area`, `dem_30m`, `dem_300m`, `gradient`, `elvstd`, `ldd`,
`chanleng_300m` — each as `.tif` **and** `.nc`.

---

## STEP 1 — `rasterize_watershed()`: creating the master grid

Vector polygon → raster mask, in three passes.

```python
gdal_rasterize -burn 1 -init 0 -tr 30 30 -tap -ot Byte  shp  area_30m.tif
gdalwarp -r average -tr 300 300 -tap                    area_30m.tif  area_fraction.tif
mask_arr = np.where(fraction_arr > 0, 1, 0)          →  area.tif
```

**Why not rasterise directly at 300 m?** A direct burn is all-or-nothing per cell: a boundary
cell 40% inside the basin becomes either fully in or fully out. Going via 30 m and averaging
gives each 300 m cell the *exact fraction* of its area inside the polygon (100 sub-pixels →
0.00–1.00). Cheap and exact.

**`-tap` is the flag that makes everything else work.** "Target Aligned Pixels" forces the
output grid origin onto a multiple of the pixel size — so `area.tif`'s corner lands on a
round number of metres. Every subsequent raster inherits this. Without `-tap` the origin would
depend on the shapefile's arbitrary bounding box.

The final rule is `> 0`, i.e. **any-touch**: a cell 1% inside the basin is included. This
slightly over-estimates the domain, which is the safe direction — a channel that clips a
boundary cell stays connected.

`reproject_shapefile()` runs first, converting the ROI to the target UTM CRS (see
`pipeline_config.resolve_crs()`) and caching the result as `inputs/raw/watershed_projected.shp`.

---

## STEP 2 — `compute_and_download_gee_topo()`: fetching SRTM

Builds the request rectangle from the grid's own transform, with a 2 km buffer:

```python
xmin, ymax = t.c, t.f
xmax, ymin = xmin + t.a * info.width, ymax + t.e * info.height
region = ee.Geometry.Rectangle([xmin-buf, ymin-buf, xmax+buf, ymax+buf],
                               proj=str(info.crs), geodesic=False)
```

Two details worth internalising:
- `proj=str(info.crs)` — the rectangle is expressed in **UTM metres**, not degrees. This is the
  same geometry path all the GEE scripts use.
- `geodesic=False` — treat the edges as straight lines in the projected plane, not great
  circles. For a projected rectangle that's what you want.

The buffer exists because resampling near an edge needs neighbours; without it the outermost
row/column would be interpolated from nothing.

Then `dem = ee.Image("USGS/SRTMGL1_003")` (SRTM 1-arcsec ≈ 30 m) is downloaded, **tiled**. This
script has its own inline tiling rather than calling `ee_export_tiled` — it computes the tile
count from raw payload size only, since SRTM's native resolution equals the requested scale so
the reprojection limit doesn't apply:

```python
total_mb = (width_m/30) * (height_m/30) * 4 / 1e6     # this ROI: 6549×8780 px = 230 MB
n = ceil(sqrt(total_mb / 25))                         # ~25 MB per tile
```

The result is cached as `inputs/raw/topo_raw_gee_30m.tif` — delete it to force a re-download.

---

## STEP 3 — `process_local_topo()`: pysheds hydrological conditioning

This is where a raw DEM becomes a *hydrologically valid* DEM.

```python
grid    = Grid.from_raster(raw_tif)
dem_30m = grid.read_raster(raw_tif)
dem_30m = grid.fill_pits(dem_30m)          # single cells lower than all 8 neighbours
dem_30m = grid.fill_depressions(dem_30m)   # multi-cell basins with no outlet
dem_30m = grid.resolve_flats(dem_30m)      # areas with zero gradient
fdir_30m = grid.flowdir(dem_30m, dirmap=(64,128,1,2,4,8,16,32))
acc_30m  = grid.accumulation(fdir_30m)
```

**Why all three fixes are mandatory:** flow routing assumes every cell drains somewhere. A pit
(sensor noise) or a depression (a real lake, or an artefact) is a cell with nowhere to go —
routing terminates there and the downstream network is severed. `resolve_flats` handles the
opposite problem: a perfectly flat cell has no steepest neighbour, so direction is undefined.

**D8 flow direction** = each cell drains entirely into one of its 8 neighbours: the steepest
downhill one. `dirmap` gives the codes, going clockwise from north:

```
 32  64 128        NW  N  NE
 16   ●   1   =    W   ●   E
  8   4   2        SW  S  SE
```

**Flow accumulation** = for each cell, how many upstream cells drain through it. Follow the
directions upstream and count. High accumulation = a river. This single array later becomes
the channel network, the channel widths, and the gauge-snapping target.

### Elevation standard deviation (`elvstd`)

Sub-grid roughness — how variable the terrain is *inside* one 300 m cell. Used by LISFLOOD's
snow/elevation-zone logic.

```python
factor = int(300 / 30)                                        # 10
hr_transform = info.transform * affine.Affine.scale(1/10, 1/10)   # a 10× finer grid,
                                                                   # exactly nested
reproject(dem_30m_arr → dem_hr_exact, dst_transform=hr_transform)
blocks  = dem_hr_exact.reshape(H, 10, W, 10)
elvstd  = np.nanstd(blocks, axis=(1, 3))
```

The `reshape(H, factor, W, factor)` + `axis=(1,3)` trick is worth learning: it turns an
`(H*10, W*10)` array into per-cell 10×10 blocks and reduces over both block axes at once —
a vectorised block-reduce with no Python loop. The DEM is first reprojected onto an *exactly
nested* fine grid so the blocks line up perfectly with the 300 m cells.

### Gradient — `calc_d8_gradient()`

Steepest downhill slope at 300 m, computed with shifted arrays rather than loops:

```python
padded = np.pad(dem, 1, constant_values=np.nan)
for dy, dx, dist in neighbours:                 # 4 orthogonal (d=300), 4 diagonal (d=300√2)
    shifted = padded[1+dy : -1+dy, 1+dx : -1+dx]
    grad = np.fmax(grad, (dem - shifted) / dist)
```

`np.fmax` (not `np.maximum`) ignores NaN, so edge cells don't poison the result. Diagonals use
`res*√2` because that's the actual distance between diagonal cell centres.

Post-processing clamps: gradient gets a floor of `1e-5` (LISFLOOD divides by it — zero would
be a division by zero), elvstd gets a floor of `0`.

---

## STEP 4 — `compute_ldd_snapped()`: upscaling the flow network

**This is the hardest function in the repo and the one most worth understanding.**

The problem: you have 30 m flow directions, you need 300 m flow directions. Naive
downsampling destroys the network — a river meandering within a 300 m cell has many local
directions, and picking the mode or the centre cell's direction gives a broken graph.

The solution (a Yamazaki-style upscaling): **for each coarse cell, find the dominant river
inside it and trace it until it leaves the cell. Whichever coarse neighbour it enters is the
coarse flow direction.**

```python
factor = H_30m // H_300m                                 # 10

for Y in range(H_300m):
  for X in range(W_300m):
    block_acc = acc_arr[Y*10:(Y+1)*10, X*10:(X+1)*10]    # the 10×10 fine block
    max_idx   = np.unravel_index(np.nanargmax(block_acc), block_acc.shape)
    curr_y, curr_x = Y*10 + max_idx[0], X*10 + max_idx[1]   # the main channel pixel

    while True:                                          # walk downstream at 30 m
        dy, dx = dir_map_30m[fdir_arr[curr_y, curr_x]]
        curr_y += dy; curr_x += dx
        new_Y, new_X = curr_y // 10, curr_x // 10
        if (new_Y, new_X) != (Y, X):                     # left the coarse cell
            ldd_300m[Y, X] = convert_delta_to_pcraster(new_Y-Y, new_X-X)
            break
        if steps > 400: ldd_300m[Y,X] = 5; break         # loop guard
```

The highest-accumulation pixel is, by definition, the main channel — the cell's flow is
dominated by it.

### PCRaster LDD encoding

The output uses PCRaster's numpad convention, which is **not** the D8 powers-of-two scheme:

```
 7  8  9        NW  N  NE
 4  5  6   =    W  pit E
 1  2  3        SW  S  SE
```

`5` means "pit" — flow stops here (the basin outlet, or an unresolvable cell).
`convert_delta_to_pcraster(dy, dx)` is a straight `{(dy,dx): code}` lookup.

So the codebase juggles **two flow-direction encodings**. `channnels.py` has to translate back
(`pcr_to_pysheds`) to feed pysheds again. Worth noting when you read that file.

### The meander-length side product

Inside the same loop, a second walk goes *upstream* from the main-channel pixel, accumulating
`30 m` per orthogonal step and `30√2 m` per diagonal step. That total is the **true meandering
channel length inside the cell** — typically much more than 300 m, because a river doesn't
cross a cell in a straight line.

The upstream search inverts the direction table: neighbour `(dy,dx)` flows *into* me only if
its direction code points back at me.

```python
required_dir = {(-1,0):4, (-1,1):8, (0,1):16, (1,1):32,
                (1,0):64, (1,-1):128, (0,-1):1, (-1,-1):2}[(dy,dx)]
if n_dir == required_dir:  # this neighbour drains into me
```

Saved as `chanleng_300m.tif`; `channnels.py` picks it up as `chanleng`. Channel length sets
travel time, so getting it right matters for flood-peak timing.

**Performance note:** this is a pure-Python double loop over every coarse cell with an inner
trace — the slowest part of the pipeline. Vectorising it would be a genuine contribution.

---

## STEPS 5 & 7 — conversion and the alignment proof

`convert_to_netcdf()` runs every `.tif` through `gdal_convert_netcdf` (see
[lisflood_utils.md](lisflood_utils.md) for why that isn't actually GDAL).

`validate_alignment()` prints a table of every output's origin/size/pixel/CRS and hard-exits
if any differs from `area.tif` beyond a 0.01 m tolerance. A near-copy of this function appears
in three other scripts — a reasonable refactor target.

The `_30m` outputs are excluded from the check (they're at a different resolution by design):

```python
val_paths = {k: v for k, v in tif_paths.items() if not k.endswith('_30m')}
```
