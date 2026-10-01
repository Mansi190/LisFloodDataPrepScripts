# `pipeline/channnels.py`

**353 lines. Step 5.** (Yes, three n's. It's the filename.) Builds the river-channel geometry
LISFLOOD routes water through.

Outputs (in `inputs/maps/`): `chan`, `changrad`, `chanman`, `chanleng`, `chanbw`, `chans`,
`chanbnkf` — plus `inputs/raw/facc_snapped.tif`, which `make_outlets.py` depends on.

## Two-track design

| computed **in GEE** | computed **locally** |
|---|---|
| `changrad` — channel gradient | `chan` — channel mask (from flow accumulation) |
| `chanman` — Manning's n from land cover | `chanbw` — bottom width |
| `chanleng` — meander length *(overridden)* | `chanbnkf` — bankfull depth |
| `chans` — side slope (constant 1.0) | flow accumulation on the LDD graph |

Things needing the drainage-line vector or the SRTM percentile machinery go to GEE; things
needing the pipeline's own LDD stay local.

---

## The GEE half — `compute_and_download_gee_channels()`

### Manning's n from land cover

```python
chanMan = pred_label.where(pred_label.eq(1), 0.080) \
                    .where(pred_label.gte(2).And(pred_label.lte(4)), 0.035) \
                    .where(pred_label.eq(6), 0.100) \
                    .where(pred_label.eq(5), 0.040) \
                    .where(pred_label.gte(7), 0.045)
```

`.where(condition, value)` is EE's vectorised conditional — chained, it's a `case` statement.
Manning's *n* is a roughness coefficient: higher = more friction = slower flow. Open water is
smoothest (0.035); forested channels, full of debris, are roughest (0.100).

### Channel gradient — the 10–90 percentile method

```python
channelMask = ee.Image(0).byte().paint(drainage_fc, 1)      # rasterise the vector network
dem_masked  = srtm_dem.updateMask(channelMask.eq(1))        # elevation ONLY on channel pixels
dem_p = dem_masked.reduceResolution(ee.Reducer.percentile([10, 90]), maxPixels=4096) \
                  .reproject(crs=str(info.crs), scale=300)
chanGrad = p90.subtract(p10).divide(RESOLUTION_M * 1.1).unmask(0.0001).clamp(0.0001, 0.05)
```

Three ideas stacked:

1. **`.paint()`** burns a `FeatureCollection` (the CoRE Stack pan-India drainage lines) into a
   raster — vector → raster, server-side.
2. **`updateMask`** restricts the DEM to channel pixels only. You want the slope *of the
   river*, not of the hillsides beside it.
3. **Percentiles instead of min/max.** `max − min` over a 300 m cell is dominated by a single
   noisy SRTM pixel. The 10th–90th percentile spread is the same measure with the outliers cut
   — a **robust statistic**. Same reasoning as the `robust_limits` 2–98 percentile in the
   plotting scripts.

`unmask(0.0001)` fills cells with no channel; `clamp(0.0001, 0.05)` bounds it. A zero gradient
would divide by zero in the routing.

### Channel length from line density

```python
channelMask_30m = ee.Image(0).paint(drainage_fc, 1).reproject(crs=..., scale=30)
chanLength = channelMask_30m.reduceResolution(ee.Reducer.mean(), maxPixels=4096)
                            .reproject(crs=..., scale=300)
                            .multiply((300 * 300) / 30.0)
                            .max(ee.Image.constant(300))
```

The algebra: mean of the 0/1 mask = fraction of the cell covered by river. Multiply by cell
area → river area in m². The painted line is 30 m wide, so `area / 30` = length. Floor it at
one cell width.

**This result is then thrown away** — `process_local_channels` overwrites `chanleng` with
`chanleng_300m.tif`, the physically traced meander length from `topographyMapsScript.py`.
The GEE version survives only as a fallback if that file is missing. Tracing the actual path
beats inferring it from coverage.

### Side slope

```python
chanSdXdY = ee.Image.constant(1.0)     # a 45° trapezoid bank
```

A placeholder constant — no data source gives channel bank geometry at this scale.

---

## The local half — `process_local_channels()`

### Re-deriving flow accumulation on the LDD graph

```python
pcr_to_pysheds = {8:64, 9:128, 6:1, 3:2, 2:4, 1:8, 4:16, 7:32, 5:0}
fdir_pysheds = np.zeros_like(ldd, dtype=np.int16)
for pcr, pysh in pcr_to_pysheds.items():
    fdir_pysheds[ldd == pcr] = pysh
acc = grid.accumulation(fdir_pysheds)
```

`topographyMapsScript.py` already computed accumulation at 30 m. Why redo it?

Because that was the *fine* network. What matters now is the network **LISFLOOD will actually
route on** — the coarse LDD produced by the Yamazaki upscaling. Any small discrepancy between
the two would put channels and drainage areas on a graph the model doesn't use. Recomputing on
the exported LDD makes accumulation consistent with the model's own topology, by construction.

The dict translation is needed because the two libraries use different flow encodings —
PCRaster's numpad codes (1–9) vs pysheds' powers of two. See
[topographyMapsScript.md](topographyMapsScript.md#pcraster-ldd-encoding).

`facc_snapped.tif` is saved here because `make_outlets.py` snaps gauges to the
highest-accumulation nearby cell — and it must use the *same* graph.

### Defining the channel network

```python
area_km2 = acc_arr * (300**2 / 1_000_000)          # cells upstream → km²
UPSTREAM_AREA_THRESHOLD_KM2 = 5.0
chan = np.where((area_km2 >= 5.0) & (mask_arr > 0), 1, 0)
```

**The single most consequential parameter in this file.** A cell is a river if enough land
drains through it. Lower the threshold → a denser network, more cells routed, slower runs,
possibly spurious streams. Raise it → a sparse skeleton that may disconnect tributaries.
5 km² at 300 m ≈ 55 upstream cells.

(The log message says "10 km2" while the constant is 5.0 — the message is stale.)

### Hydraulic geometry — width and depth from drainage area

```python
chanbnkf = 0.27 * (area_km2 ** 0.33)      # bankfull depth, m
chanbw   = area_km2 * 0.0032              # bottom width, m
```

These are **downstream hydraulic geometry** relations: empirically, channel dimensions scale
as a power law of upstream drainage area (bigger catchment → more discharge → bigger channel).
The depth exponent ~0.33 is the classic literature value. The width relation here is linear,
which is unusual — the standard form is also a power law (≈ area^0.5). Worth flagging: a
linear width will over-predict width in large catchments and under-predict in small ones.

`chanbnkf` is the depth at which the channel overflows — LISFLOOD's flood threshold.

### Final masking — note it's stricter than elsewhere

```python
final = np.where((chan == 1) & (mask_arr > 0), arr, -9999)
```

Channel properties exist **only on channel cells**. Everywhere else is nodata. That's different
from the soil/LULC maps, which are valid across the whole basin.

Floors are applied first: the five flow-related maps get `1e-5` where they'd be ≤0 or NaN
(they end up in denominators); `chans` gets `0.0`.

---

## Gotchas

- `validate_alignment` here adds a pixel-size check the other copies lack.
- `_write_convert_script` overwrites `inputs/maps/manual_convert.sh` — the topography script
  writes to the same path, so only the last writer's lines survive.
- The GEE-computed `chanleng` band is downloaded and then discarded every run; skipping it
  would save a band's worth of download.
