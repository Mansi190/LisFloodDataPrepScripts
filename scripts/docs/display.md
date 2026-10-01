# `display/` — visualising results

Two scripts. Neither touches the pipeline; both read what LISFLOOD produced and render it.

---

# 1. `generate_html_plots.py` (692 lines)

The reporting layer. Scans `outputs/cold`, `outputs/warm`, and the pipeline inputs; writes one
PNG (or GIF) per file into `display/inputs/<category>/` and `display/outputs/<category>/`;
writes `display/manifest.js`, which `lisflood_outputs_reference.html` reads to build the page.

```bash
python scripts/display/generate_html_plots.py            # one pass
python scripts/display/generate_html_plots.py --force    # redraw everything
python scripts/display/generate_html_plots.py --watch    # regenerate on change
```

## Architecture: a make-like incremental build

```python
def needs_update(out_png, sources, force):
    if force or not os.path.exists(out_png):
        return True
    png_mtime = os.path.getmtime(out_png)
    return any(os.path.getmtime(src) > png_mtime for src in sources)
```

Classic **mtime-based dependency checking** — exactly what `make` does. Re-running after one
new output redraws one PNG, not two hundred.

The reverse direction is garbage collection:

```python
MANAGED_PREFIXES = ("cold_", "warm_", "tss_", "sa_")
...
if rel not in expected and fname.startswith(MANAGED_PREFIXES):
    os.remove(...)          # source disappeared → delete the PNG
```

The `expected` set is built during generation; anything managed but not expected is stale.
The prefix guard means the script **only ever deletes files it created** — a hand-added image
in `display/` is safe. That's the right way to write a destructive cleanup: constrain by
naming convention, not by "everything in this folder".

## Deterministic naming

```
cold_end_<stem>.png / warm_end_<stem>.png    .end.nc final-state snapshots
cold_nc_<stem>.png  / warm_nc_<stem>.png     per-timestep .nc stacks
tss_<stem>.png                               .tss series, cold+warm overlaid
sa_<stem>.png                                pipeline INPUT maps (study area)
```

Names are a pure function of `(run, kind, stem)`, which is what makes both the incremental
build and the stale-file detection possible: the same source always maps to the same target.

## `classify()` and `scan_runs()` — dispatch by extension

```python
def classify(fname):
    if fname.endswith(".end.nc"): return "end", fname[:-len(".end.nc")]
    if fname.endswith(".tss"):    return "tss", fname[:-len(".tss")]
    if fname.endswith(".nc"):     return "nc",  fname[:-len(".nc")]
    return None, None
```

Order matters — `.end.nc` must be tested before `.nc`. `scan_runs()` inverts the directory
listing into `{(kind, stem): {run: path}}`, so a variable present in both runs arrives as one
entry with two paths, ready to overlay.

Categories come from two lookup tables: `input_category()` (by directory) and
`output_category()` (by stem, first match in an ordered dict) — `discharge`,
`soil_moisture`, `groundwater`, `et_interception`, `snow_frost_runoff`, `meteo`, `misc`.
`VAR_META` maps ~60 stems to `(title, units, colormap)`, which is also the best glossary in
the repo for what LISFLOOD's cryptic output names mean.

## `read_tss()` — parsing PCRaster time-series

```python
header    = lines[0]
num_cols  = int(lines[1])
col_names = [lines[2+i] for i in range(num_cols)]
data      = lines[2+num_cols:]
```

A `.tss` file is a tiny self-describing format: title, column count, column names, then rows.
This is where the discharge series at your gauge lives.

## Plotting decisions worth copying

**Robust colour limits** — the same idea as the channel-gradient percentiles:

```python
vmin, vmax = np.percentile(vals[np.isfinite(vals)], [2, 98])
```

One extreme cell would otherwise compress the entire colour scale into nothing. And in
animations the limits are computed **once over the whole stack**, so brightness changes across
frames mean real magnitude changes rather than per-frame rescaling.

**Frame subsampling** for GIFs:

```python
GIF_MAX_FRAMES = 60
idx = np.linspace(0, nt-1, 60).round().astype(int) if nt > 60 else np.arange(nt)
```

**Forcing inputs are drawn as time series, not map frames** (`plot_input`): a basin-average
line lets you spot a unit error or a data gap instantly; 8000 map frames do not.

**The rainfall hyetograph**:

```python
RAIN_OVERLAY_STEMS = {"dis", "chanqWin"}
fig, (ax_rain, ax) = plt.subplots(2, 1, sharex=True,
                                  gridspec_kw={"height_ratios": [1, 3]})
```

Discharge gets a rainfall panel above it sharing the x-axis. This is *the* standard hydrograph
figure — you read whether a discharge peak follows a rainfall event, and with what lag. Note it
uses two stacked panels rather than a twin y-axis: separate panels with a shared time axis let
you compare timing without inviting a false comparison of magnitudes.

**`plot_gauges()`** draws the channel network in pale blue with a red star on each reporting
station. **Check this image after `make_outlets.py`** — a mis-snapped gauge invalidates every
calibration number downstream.

## `manifest.js`

```python
f.write("window.LISFLOOD_PLOTS = ")
json.dump(manifest, f, indent=1)
f.write(";\n")
```

JSON wrapped in a JS assignment so the HTML can `<script src="manifest.js">` it with no fetch
and no server — the page works from `file://`. The manifest carries the generation timestamp,
per-run metadata (`settingsfile`, `date_created`, pulled from NetCDF attributes), and the
plot index.

## `watch()` — the settle check

```python
current = snapshot()                  # (path, mtime, size) for every file
if current != last:
    time.sleep(interval)
    settled = snapshot()
    if settled != current:            # still changing — LISFLOOD is mid-write
        last = current; continue
    generate()
```

Polling alone would catch a half-written NetCDF. Requiring **two identical consecutive
snapshots** before acting is a debounce: only regenerate once the directory has stopped moving.
Including file *size* in the fingerprint is what makes a growing file detectable when its mtime
resolution is coarse.

---

# 2. `animate_output.py` (114 lines)

Turns one per-timestep `.nc` stack into an MP4 or GIF.

```bash
python scripts/display/animate_output.py outputs/warm/dis.nc
python scripts/display/animate_output.py outputs/warm/srun.nc --fps 4 --out runoff.gif
```

```python
im = ax.pcolormesh(da["x"], da["y"], data[0], cmap=cmap, vmin=vmin, vmax=vmax)
def update(i):
    im.set_array(data[i].ravel())
    ttl.set_text(f"{title}\n{times[i]:%Y-%m-%d}  (frame {i+1}/{nframes})")
    return im, ttl
anim = animation.FuncAnimation(fig, update, frames=nframes, blit=False)
```

The matplotlib animation pattern: draw **once**, then mutate the artist's data per frame with
`set_array`. Redrawing the whole axes each frame would be an order of magnitude slower.
`.ravel()` is required because `pcolormesh` stores its values flattened.

Writer selection degrades gracefully:

```python
ext = ".mp4" if shutil.which("ffmpeg") else ".gif"
```

`shutil.which` is the clean way to test for an external binary. MP4 (via `FFMpegWriter`) is far
smaller for long stacks; `PillowWriter` needs no external tool.

Its error messages are unusually good — they diagnose the *configuration* problem, not just the
symptom:

```
"...has only 1 frame — ReportSteps is likely set to 'endtime'.
 Change it to write every timestep and rerun."
```

If you want a movie of a variable, the settings XML needs `repSurfaceRunoffMaps=1` (or the
equivalent switch) **and** `ReportSteps` writing every step rather than only at the end.
