# `calibration/` — model evaluation

Two independent scripts. Neither is part of `pipeline.py`; both support the question **"is the
model right?"** rather than "what does the model need?".

The pipeline can produce every input LISFLOOD needs, but not its ~9 calibration parameters
(`b_Xinanjiang`, `UpperZoneTimeConstant`, `GwLoss`, …). Those are **fitted by optimising
simulated discharge against observations** — which first requires knowing which observations
fall inside your basin. That's step one below.

---

# 1. `gauges_in_roi.py` (106 lines)

Subsets a gauge CSV to the points inside an ROI polygon. Pure local geopandas — no Earth
Engine, so it runs in a second and you can re-run it freely while trying different ROIs.

```bash
python scripts/calibration/gauges_in_roi.py \
    --roi shapefiles/hydrobasins_roi_lev6_4060027940.shp \
    --gauges streamflow/google/floodhub_high_conf_gauges_india.csv --plot
```

## The core: a point-in-polygon join

```python
roi = gpd.read_file(roi_path)
if roi.crs.to_epsg() != 4326:
    roi = roi.to_crs(4326)
roi_geom = roi.geometry.union_all()

df  = pd.read_csv(args.gauges).dropna(subset=[lat, lon])
pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs=4326)

inside = pts[pts.within(roi_geom)].drop(columns="geometry")
```

Three deliberate choices:

1. **`union_all()` before the test.** A multi-polygon ROI would otherwise match a gauge once
   per polygon, and a gauge on a shared edge would appear twice. Dissolving to one geometry
   makes duplication impossible — a structural fix rather than a `drop_duplicates()` afterwards.
2. **Reproject the ROI to 4326, not the points.** The gauges are already lat/lon; moving one
   polygon is cheaper than moving thousands of points, and avoids a needless round-trip.
3. **`points_from_xy(lon, lat)`** — x first. The same axis-order trap as everywhere else.

`if roi.crs is None: raise SystemExit(...)` — a shapefile with no `.prj` cannot be located on
Earth, and silently assuming 4326 would produce a plausible-looking wrong answer.

## `resolve_roi()` — importing the config from a sibling package

```python
sys.path.insert(0, PIPELINE_DIR)
import pipeline_config as cfg
return cfg.ROI_SHAPEFILE
```

`calibration/` isn't a package alongside `pipeline/`, so the path is injected at call time —
and only when `--roi` was omitted, so the common case doesn't pay for it.

## Output

`<gauges stem>_in_<roi stem>.csv`, written **beside the ROI**, with a printed summary. `--plot`
adds a PNG: all gauges in grey, kept ones in red, ROI outlined, axes padded 15% beyond the ROI
bounds so context is visible.

---

# 2. `plot_grrr_discharge.py` (111 lines)

Plots the discharge series produced by `grrr_discharge_for_gauges.py` (not in this folder — see
the GRRR notes). GRRR = Google's Runoff Reanalysis & Reforecast. **These are modelled values,
not gauge observations** — the figure says so in its own title, which is the right instinct
when a plot could be mistaken for ground truth.

```bash
python scripts/calibration/plot_grrr_discharge.py \
    --input shapefiles/grrr_discharge_roi_lev6_4060027940.csv --roll 30
```

## Small multiples, not a dual axis

```python
fig, axes = plt.subplots(n, 1, figsize=(12, 3.1*n + 1.0), sharex=True)
```

The module docstring argues the case explicitly, and it's worth internalising:

> the reaches differ several-fold in magnitude, so a shared y would flatten the small one and a
> second y-scale would invite false comparison. Y-axes are therefore INDEPENDENT and the panel
> titles carry the numbers.

**One panel per series, shared x, independent y, magnitudes in the titles.** A twin-axis chart
lets a reader "see" a correlation that is an artefact of two arbitrary scalings.

## Signal and noise in one panel

```python
roll = s.rolling(args.roll, center=True, min_periods=1).mean()
ax.plot(s.index, s.values, color=DAILY, linewidth=0.35, alpha=0.9)   # the data
ax.plot(roll.index, roll.values, color=MEAN, linewidth=1.4)          # the seasonal signal
```

- `center=True` — the window is centred on each point, so the smoothed curve isn't lagged
  half a window to the right (the default `center=False` is a *trailing* mean).
- `min_periods=1` — the first and last 15 days still get a value instead of NaN.
- `linewidth=0.35` — a **hairline** for 16,000 daily points. At 2px the panel fills solid black
  and you see nothing. Line weight is a real parameter at this density.

## Direct-labelling one point

```python
i = s.idxmax()
late = i > s.index[0] + (s.index[-1] - s.index[0]) * 0.66
ax.annotate(f"{s.max():,.0f}  ({i:%Y-%m-%d})", xy=(i, s.max()),
            xytext=(-8 if late else 8, -2), textcoords="offset points",
            ha="right" if late else "left")
```

Only the record peak is labelled. The `late` test flips the label inboard when the peak sits in
the right third, so it doesn't run off the figure — a small touch that makes the chart robust
to whatever data arrives.

## Colour discipline

```python
DAILY = "#5a90b9"   # light
MEAN  = "#1d4f75"   # dark — same hue
```

One hue, light→dark: the rolling mean reads as *emphasis on* the daily series rather than a
competing series. The comment records that both pass ≥3:1 contrast on a light background.

Also: top and right spines removed, remaining spines at `0.7` grey, y-grid only at `alpha=0.35`
with `set_axisbelow(True)` so gridlines sit behind the data. Standard chart hygiene — the
frame should never compete with the line.
