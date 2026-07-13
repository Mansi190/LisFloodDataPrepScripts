"""Render LISFLOOD run outputs to PNGs for lisflood_outputs_reference.html.

Scans outputs/cold (cold run) and outputs/warm (warm run) and the pipeline
inputs, and produces one PNG per file under display/inputs/<category>/ and
display/outputs/<category>/, plus a manifest.js (in display/) that the HTML
reads to decide which cold/warm/input images to display.

Generated file naming (deterministic, keyed by run + kind + variable stem):
    cold_end_<stem>.png   warm_end_<stem>.png    .end.nc snapshots
    cold_nc_<stem>.png    warm_nc_<stem>.png     per-timestep .nc stacks
    tss_<stem>.png                               .tss series, cold+warm overlaid
    sa_<stem>.png                                pipeline INPUT maps (study area)

Pipeline inputs are scanned from the data-prep output dirs (maps/, meteo/,
lai/, safe_init) — static 2D maps get a single panel, forcing stacks get the
two-panel last+mean treatment. Only files with these prefixes (and
manifest.js) are managed: stale ones are deleted when their source
disappears.

Usage:
    python generate_html_plots.py            # one pass
    python generate_html_plots.py --watch    # regenerate whenever out/ or
                                             # out_warm/ contents change
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import animation
import numpy as np
import pandas as pd
import xarray as xr

GIF_MAX_FRAMES = 60   # long stacks are sampled down to this many frames
GIF_FPS = 5

# this script lives in scripts/display/ ; repo root is two levels up
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
RUNS = {
    "cold": os.path.join(REPO_ROOT, "outputs", "cold"),
    "warm": os.path.join(REPO_ROOT, "outputs", "warm"),
}
DISPLAY_DIR = os.path.join(REPO_ROOT, "display")
MANIFEST_PATH = os.path.join(DISPLAY_DIR, "manifest.js")
MANAGED_PREFIXES = ("cold_", "warm_", "tss_", "sa_")

# ── image organisation: display/inputs/<cat>/ and display/outputs/<cat>/ ──
CHAN_STEMS = {"chan", "chanbw", "chanbnkf", "chanleng", "changrad", "chanman", "chans"}

def input_category(in_dir, stem):
    d = in_dir.replace("\\", "/")
    if d.endswith("/soilhyd"):
        return "soil"
    if d.endswith("/fraction") or d.endswith("/table2map"):
        return "landcover"
    if d.endswith("/safe_init"):
        return "initialisation"
    if "/meteo" in d:
        return "meteo"
    if "/lai/" in d or d.endswith("/lai"):
        return "lai"
    return "channels" if stem in CHAN_STEMS else "topography"

# ordered first-match; a variable is filed under the first category it appears in
OUTPUT_CAT = {
    "discharge": {"dis", "chanq", "chanqWin", "chcro", "wl"},
    "soil_moisture": {"tha", "thb", "thc", "thfa", "thfb", "thfc", "thia", "thib", "thic",
                      "th1", "th2", "th3", "th1AvUps", "th2AvUps", "th3AvUps",
                      "wdep", "wdepth", "wDepth", "wdepthUps", "pftop", "pfsub", "pFTop", "pFSub"},
    "groundwater": {"uz", "uzf", "uzi", "lz", "uz2lz", "loss", "gwl", "qUz", "qLz", "qUzUps",
                    "qLzUps", "quz", "qlz", "uzUps", "lzUps", "tws", "lossUps",
                    "percUZLZ", "percUZLZUps", "lzAvIn", "lzAvInUps", "lzavin", "lzavin_forest"},
    "et_interception": {"cum", "cumf", "cumi", "int", "ewint", "ldra", "esact", "tact", "etact",
                        "etUps", "ewUps", "esUps", "esAct", "tAct", "interception",
                        "interceptionUps", "ewIntAct", "ewIntActUps", "leafDrainage",
                        "leafDrainageUps", "cumInt", "cumInterceptionUps", "et", "es", "ew"},
    "snow_frost_runoff": {"scova", "scovb", "scovc", "scovUps", "snowCover", "frost", "frostUps",
                          "dslr", "dslf", "dsli", "dslrUps", "ofoth", "offor", "ofdir", "cseal",
                          "srun", "trun", "frun", "inf", "pflow", "sgw", "to2su", "rws", "smstress",
                          "surfaceRunoff", "surfaceRunoffUps", "smelt", "snow", "snowUps",
                          "snowMelt", "snowMeltUps", "dTopToSub", "dTopToSubUps", "dSubToUz",
                          "dSubToUzUps", "prefFlow", "prefFlowUps", "infiltration",
                          "infiltrationUps", "totalRunoff", "totalRunoffUps"},
    "meteo": {"pr", "ta", "tav", "rain", "rainUps", "precipUps", "tAvgUps"},
}

def output_category(stem):
    for cat, stems in OUTPUT_CAT.items():
        if stem in stems:
            return cat
    return "misc"

# Pipeline INPUT directories (scanned non-recursively for .nc files)
INPUT_DIRS = [os.path.join(REPO_ROOT, "inputs", d) for d in (
    "maps", "maps/fraction", "maps/soilhyd", "maps/table2map",
    "maps/safe_init", "meteo", "lai/forest", "lai/other",
)]

RUN_COLORS = {"cold": "#3b82f6", "warm": "#f59e0b"}

# stem -> (title, units, colormap)
VAR_META = {
    "dis":      ("Channel discharge", "m³/s", "Blues"),
    "chanq":    ("Channel discharge", "m³/s", "Blues"),
    "chanqWin": ("Discharge — last routing sub-step", "m³/s", "Blues"),
    "chcro":    ("Channel cross-section area", "m²", "Blues"),
    "cseal":    ("Depression storage — sealed fraction", "mm", "PuBu"),
    "cum":      ("Interception storage — other fraction", "mm", "YlGn"),
    "cumf":     ("Interception storage — forest fraction", "mm", "YlGn"),
    "cumi":     ("Interception storage — irrigated fraction", "mm", "YlGn"),
    "dslr":     ("Days since last rain — other fraction", "days", "YlOrBr"),
    "dslf":     ("Days since last rain — forest fraction", "days", "YlOrBr"),
    "dsli":     ("Days since last rain — irrigated fraction", "days", "YlOrBr"),
    "frost":    ("Frost index", "°C·days", "PuBu"),
    "lz":       ("Lower groundwater zone storage", "mm", "viridis"),
    "uz":       ("Upper groundwater zone — other fraction", "mm", "viridis"),
    "uzf":      ("Upper groundwater zone — forest fraction", "mm", "viridis"),
    "uzi":      ("Upper groundwater zone — irrigated fraction", "mm", "viridis"),
    "ofdir":    ("Overland flow storage — direct/sealed", "m³", "Blues"),
    "offor":    ("Overland flow storage — forest", "m³", "Blues"),
    "ofoth":    ("Overland flow storage — other", "m³", "Blues"),
    "scova":    ("Snow cover — elevation zone A", "mm", "PuBu"),
    "scovb":    ("Snow cover — elevation zone B", "mm", "PuBu"),
    "scovc":    ("Snow cover — elevation zone C", "mm", "PuBu"),
    "tha":      ("Soil moisture layer 1a — other fraction", "m³/m³", "YlGnBu"),
    "thb":      ("Soil moisture layer 1b — other fraction", "m³/m³", "YlGnBu"),
    "thc":      ("Soil moisture layer 2 — other fraction", "m³/m³", "YlGnBu"),
    "thfa":     ("Soil moisture layer 1a — forest fraction", "m³/m³", "YlGnBu"),
    "thfb":     ("Soil moisture layer 1b — forest fraction", "m³/m³", "YlGnBu"),
    "thfc":     ("Soil moisture layer 2 — forest fraction", "m³/m³", "YlGnBu"),
    "thia":     ("Soil moisture layer 1a — irrigated fraction", "m³/m³", "YlGnBu"),
    "thib":     ("Soil moisture layer 1b — irrigated fraction", "m³/m³", "YlGnBu"),
    "thic":     ("Soil moisture layer 2 — irrigated fraction", "m³/m³", "YlGnBu"),
    "wdepth":   ("Water depth on soil surface", "mm", "Blues"),
    "rainUps":  ("Rain — upstream basin average", "mm/day", None),
    "snowUps":  ("Snow — upstream basin average", "mm/day", None),
    "etUps":    ("Reference ET — upstream basin average", "mm/day", None),
    "ewUps":    ("Open-water evaporation — upstream basin average", "mm/day", None),
    "tAvgUps":  ("Average temperature — upstream basin average", "°C", None),
    "lossUps":  ("Groundwater loss — upstream basin average", "mm/day", None),
    "scovUps":  ("Snow cover — upstream basin average", "mm", None),
    "surfaceRunoffUps": ("Surface runoff — upstream basin average", "mm/day", None),
    # ── pipeline input maps ──
    "area":     ("Basin mask / pixel area", "", "Greys"),
    "dem_300m": ("Digital elevation model (300 m)", "m", "terrain"),
    "dem_30m":  ("Digital elevation model (30 m source)", "m", "terrain"),
    "elvstd":   ("Sub-grid elevation std. dev.", "m", "magma"),
    "gradient": ("Terrain gradient", "m/m", "viridis"),
    "ldd":      ("Local drain direction", "1–9", "tab10"),
    "lat":      ("Latitude grid", "°N", "viridis"),
    "chan":     ("Channel mask", "0/1", "Blues"),
    "chanbw":   ("Channel bottom width", "m", "Blues"),
    "chanbnkf": ("Channel bankfull depth", "m", "Blues"),
    "chanleng": ("Channel length per cell", "m", "Blues"),
    "changrad": ("Channel gradient", "m/m", "viridis"),
    "chanman":  ("Channel Manning roughness", "s/m^1/3", "YlOrBr"),
    "chans":    ("Channel side slope", "m/m", "viridis"),
    "lulc":     ("Land use / land cover classes", "class", "tab20"),
    "fracother":  ("Fraction — other (grass/crops)", "0–1", "YlGn"),
    "fracforest": ("Fraction — forest", "0–1", "Greens"),
    "fracsealed": ("Fraction — sealed/impervious", "0–1", "Greys"),
    "fracwater":  ("Fraction — open water", "0–1", "Blues"),
    "cropcoef_other":  ("Crop coefficient — other", "-", "YlGn"),
    "cropcoef_forest": ("Crop coefficient — forest", "-", "Greens"),
    "crgrnum_other":   ("Crop group number — other", "-", "YlGn"),
    "crgrnum_forest":  ("Crop group number — forest", "-", "Greens"),
    "mannings_other":  ("Surface Manning n — other", "s/m^1/3", "YlOrBr"),
    "mannings_forest": ("Surface Manning n — forest", "s/m^1/3", "YlOrBr"),
    "soildep1_other":  ("Soil depth layer 1 — other", "mm", "YlOrBr"),
    "soildep2_other":  ("Soil depth layer 2 — other", "mm", "YlOrBr"),
    "soildep1_forest": ("Soil depth layer 1 — forest", "mm", "YlOrBr"),
    "soildep2_forest": ("Soil depth layer 2 — forest", "mm", "YlOrBr"),
    "ksat1_other":  ("Sat. hydraulic conductivity L1 — other", "mm/day", "viridis"),
    "ksat1_forest": ("Sat. hydraulic conductivity L1 — forest", "mm/day", "viridis"),
    "ksat2":        ("Sat. hydraulic conductivity L2", "mm/day", "viridis"),
    "thetas1_other":  ("Saturated water content L1 — other", "m³/m³", "YlGnBu"),
    "thetas1_forest": ("Saturated water content L1 — forest", "m³/m³", "YlGnBu"),
    "thetas2":        ("Saturated water content L2", "m³/m³", "YlGnBu"),
    "thetar1_other":  ("Residual water content L1 — other", "m³/m³", "YlGnBu"),
    "thetar1_forest": ("Residual water content L1 — forest", "m³/m³", "YlGnBu"),
    "thetar2":        ("Residual water content L2", "m³/m³", "YlGnBu"),
    "alpha1_other":  ("Van Genuchten alpha L1 — other", "1/cm", "plasma"),
    "alpha1_forest": ("Van Genuchten alpha L1 — forest", "1/cm", "plasma"),
    "alpha2":        ("Van Genuchten alpha L2", "1/cm", "plasma"),
    "lambda1_other":  ("Pore-size index lambda L1 — other", "-", "plasma"),
    "lambda1_forest": ("Pore-size index lambda L1 — forest", "-", "plasma"),
    "lambda2":        ("Pore-size index lambda L2", "-", "plasma"),
    "laif":  ("Leaf area index — forest", "m²/m²", "Greens"),
    "laio":  ("Leaf area index — other", "m²/m²", "YlGn"),
    "pr":    ("Precipitation forcing", "mm/day", "Blues"),
    "ta":    ("Average temperature forcing", "°C", "RdYlBu_r"),
    "tn":    ("Minimum temperature forcing", "°C", "RdYlBu_r"),
    "tx":    ("Maximum temperature forcing", "°C", "RdYlBu_r"),
    "ws":    ("Wind speed forcing", "m/s", "viridis"),
    "rg":    ("Global radiation forcing", "J/m²/day", "inferno"),
    "e":     ("Actual vapour pressure forcing", "hPa", "viridis"),
    "pd":    ("Dew point temperature forcing", "°C", "RdYlBu_r"),
    "e0":    ("Potential open-water evaporation (LISVAP)", "mm/day", "YlOrBr"),
    "et":    ("Potential reference ET (LISVAP)", "mm/day", "YlOrBr"),
    "es":    ("Potential soil evaporation (LISVAP)", "mm/day", "YlOrBr"),
    "lzavin": ("Average inflow to lower zone (pre-run)", "mm/day", "viridis"),
}


def meta_for(stem):
    title, units, cmap = VAR_META.get(stem, (stem, "", None))
    return title, units, cmap or "viridis"


def style_axes(ax):
    ax.grid(True, linestyle="--", linewidth=0.5, color="#e2e8f0", alpha=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#cbd5e1")


def robust_limits(arr):
    vals = arr[np.isfinite(arr)]
    if vals.size == 0:
        return 0.0, 1.0
    vmin, vmax = np.percentile(vals, [2, 98])
    if vmin == vmax:
        vmin, vmax = vals.min(), vals.max()
    if vmin == vmax:
        # constant field (e.g. mask map): center it in the colormap
        vmin, vmax = vmin - 0.5, vmax + 0.5
    return float(vmin), float(vmax)


def draw_field(ax, da, stem, subtitle=""):
    title, units, cmap = meta_for(stem)
    data = da.values.astype(float)
    vmin, vmax = robust_limits(data)
    im = ax.pcolormesh(da["x"], da["y"], data, cmap=cmap, vmin=vmin, vmax=vmax, shading="auto")
    ax.set_aspect("equal")
    ax.set_title(subtitle or title, fontsize=10, fontweight="bold", color="#1e293b")
    ax.set_xticks([])
    ax.set_yticks([])
    cbar = plt.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label(units, fontsize=8, color="#475569")
    cbar.ax.tick_params(labelsize=7)


def plot_end_map(nc_path, out_png, stem, run):
    ds = xr.open_dataset(nc_path)
    da = ds[list(ds.data_vars)[0]]
    title, units, _ = meta_for(stem)
    fig, ax = plt.subplots(figsize=(6.5, 5.5), dpi=150)
    draw_field(ax, da, stem, subtitle=f"{title} — end state ({run} run)")
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    ds.close()


def nc_nframes(nc_path):
    ds = xr.open_dataset(nc_path)
    n = int(ds.sizes.get("time", 1))
    ds.close()
    return n


def plot_nc_single(nc_path, out_png, stem, label):
    """A per-timestep stack that has only ONE timestep → static map (nothing to
    animate). Happens when ReportSteps writes state maps at endtime only."""
    ds = xr.open_dataset(nc_path)
    da = ds[list(ds.data_vars)[0]]
    title, _units, _ = meta_for(stem)
    frame = da.isel(time=0) if "time" in da.sizes else da
    date = pd.Timestamp(da["time"].values[0]).strftime("%Y-%m-%d") if "time" in da.sizes else ""
    fig, ax = plt.subplots(figsize=(6.5, 5.6), dpi=150)
    draw_field(ax, frame, stem, subtitle=f"{title} ({label})" + (f" — {date}" if date else ""))
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    ds.close()


def plot_nc_gif(nc_path, out_gif, stem, label):
    """Animate a per-timestep spatial map stack to a GIF. Colour scale is fixed
    across all frames (robust 2–98 pct over the whole stack); long stacks are
    sampled evenly down to GIF_MAX_FRAMES."""
    ds = xr.open_dataset(nc_path)
    da = ds[list(ds.data_vars)[0]]
    title, units, cmap = meta_for(stem)
    nt = da.sizes["time"]
    idx = (np.linspace(0, nt - 1, GIF_MAX_FRAMES).round().astype(int)
           if nt > GIF_MAX_FRAMES else np.arange(nt))
    idx = np.unique(idx)
    data = da.values.astype(float)
    times = pd.to_datetime(da["time"].values)
    vmin, vmax = robust_limits(data)

    fig, ax = plt.subplots(figsize=(6.5, 5.6), dpi=120)
    im = ax.pcolormesh(da["x"], da["y"], data[idx[0]], cmap=cmap,
                       vmin=vmin, vmax=vmax, shading="auto")
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label(units, fontsize=8, color="#475569")
    cbar.ax.tick_params(labelsize=7)
    ttl = ax.set_title("", fontsize=11, fontweight="bold", color="#1e293b")

    def update(k):
        i = idx[k]
        im.set_array(data[i].ravel())
        ttl.set_text(f"{title} ({label})\n{times[i]:%Y-%m-%d}")
        return im, ttl

    anim = animation.FuncAnimation(fig, update, frames=len(idx), blit=False)
    anim.save(out_gif, writer=animation.PillowWriter(fps=GIF_FPS))
    plt.close(fig)
    ds.close()


def plot_input(nc_path, out_png, stem):
    """Pipeline input: static 2D → spatial map; time stack → basin-average
    time series (forcing is easier to check as a series than as map frames)."""
    ds = xr.open_dataset(nc_path)
    has_time = "time" in ds.sizes
    ds.close()
    if has_time:
        ds = xr.open_dataset(nc_path)
        da = ds[list(ds.data_vars)[0]]
        series = da.mean(dim=("y", "x"), skipna=True).values
        times = pd.to_datetime(da["time"].values)
        ds.close()
        title, units, _ = meta_for(stem)
        fig, ax = plt.subplots(figsize=(11, 4.6), dpi=150)
        ax.plot(times, series, color="#0f766e", linewidth=1.8, alpha=0.9)
        ax.fill_between(times, series, np.nanmin(series), color="#0f766e", alpha=0.08)
        ax.set_title(f"{title} — basin average (pipeline input)",
                     fontsize=12, fontweight="bold", pad=12, color="#1e293b")
        ax.set_ylabel(units, fontsize=10, color="#475569")
        style_axes(ax)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out_png, bbox_inches="tight")
        plt.close(fig)
        return
    ds = xr.open_dataset(nc_path)
    da = ds[list(ds.data_vars)[0]]
    title, _, _ = meta_for(stem)
    fig, ax = plt.subplots(figsize=(6.5, 5.5), dpi=150)
    draw_field(ax, da, stem, subtitle=f"{title} — pipeline input")
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    ds.close()


def plot_gauges(outlets_path, chan_path, out_png):
    """Reporting-station map: the gauge pixel(s) marked on the channel network
    (a bare single-pixel outlets map has no useful standalone plot)."""
    dso = xr.open_dataset(outlets_path)
    o = dso[list(dso.data_vars)[0]]
    ovals = np.nan_to_num(o.values)
    x, y = o["x"].values, o["y"].values
    fig, ax = plt.subplots(figsize=(6.5, 5.6), dpi=150)
    if os.path.exists(chan_path):
        dsc = xr.open_dataset(chan_path)
        c = dsc[list(dsc.data_vars)[0]]
        ax.pcolormesh(c["x"], c["y"], c.values, cmap="Blues",
                      vmin=0, vmax=1, shading="auto", alpha=0.55)
        dsc.close()
    rows, cols = np.where(ovals > 0)
    if len(rows):
        ax.scatter(x[cols], y[rows], marker="*", s=420, c="#dc2626",
                   edgecolors="white", linewidths=1.4, zorder=5,
                   label=f"reporting station ({len(rows)})")
        ax.legend(loc="lower left", fontsize=9, frameon=True,
                  facecolor="#ffffff", edgecolor="#e2e8f0")
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("Reporting station on channel network (pipeline input)",
                 fontsize=11, fontweight="bold", color="#1e293b")
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)
    dso.close()


def read_tss(path):
    """Parse a PCRaster .tss file -> (DataFrame indexed by timestep, header line)."""
    with open(path) as f:
        lines = f.readlines()
    header = lines[0].strip()
    num_cols = int(lines[1].strip())
    col_names = [lines[2 + i].strip() for i in range(num_cols)]
    data = []
    for line in lines[2 + num_cols:]:
        parts = line.split()
        if len(parts) == num_cols:
            data.append([float(x) for x in parts])
    df = pd.DataFrame(data, columns=col_names).set_index("timestep")
    return df, header


# discharge series get a rainfall (hyetograph) overlay from the pr.nc forcing
RAIN_OVERLAY_STEMS = {"dis", "chanqWin"}
PR_FORCING = os.path.join(REPO_ROOT, "inputs", "meteo", "pr.nc")


def plot_tss(paths_by_run, out_png, stem):
    """Overlay the same .tss variable from every run that produced it.
    Discharge series get an upright rainfall panel on top, sharing the x-axis."""
    title, units, _ = meta_for(stem)
    with_rain = stem in RAIN_OVERLAY_STEMS and os.path.exists(PR_FORCING)
    if with_rain:
        fig, (ax_rain, ax) = plt.subplots(
            2, 1, figsize=(11, 6.2), dpi=150, sharex=True,
            gridspec_kw={"height_ratios": [1, 3], "hspace": 0.08})
    else:
        fig, ax = plt.subplots(figsize=(11, 5.0), dpi=150)
    for run, path in paths_by_run.items():
        df, _ = read_tss(path)
        color = RUN_COLORS[run]
        for j, col in enumerate(df.columns):
            label = f"{run} run" + (f" — station {col}" if len(df.columns) > 1 else "")
            ax.plot(df.index, df[col], label=label, color=color,
                    linewidth=2.2, alpha=0.9, linestyle="-" if j == 0 else "--")
            if j == 0:
                ax.fill_between(df.index, df[col], color=color, alpha=0.07)
    if with_rain:
        ds = xr.open_dataset(PR_FORCING)
        rain = ds["pr"].mean(dim=("y", "x"), skipna=True).values
        ds.close()
        t = np.arange(1, len(rain) + 1)
        ax_rain.bar(t, rain, width=1.0, color="#dc2626", alpha=0.55)
        ax_rain.set_ylabel("Rainfall\n(mm/day)", fontsize=9, color="#b91c1c")
        ax_rain.tick_params(axis="y", labelsize=8, colors="#b91c1c")
        style_axes(ax_rain)
        ax_rain.set_title(f"{title} with basin-average rainfall",
                          fontsize=12, fontweight="bold", pad=12, color="#1e293b")
    else:
        ax.set_title(title, fontsize=12, fontweight="bold", pad=12, color="#1e293b")
    ax.set_xlabel("Timestep (days)", fontsize=10, color="#475569")
    ax.set_ylabel(units, fontsize=10, color="#475569")
    style_axes(ax)
    ax.legend(frameon=True, facecolor="#ffffff", edgecolor="#e2e8f0",
              framealpha=0.95, loc="upper left")
    fig.tight_layout()
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)


def classify(fname):
    """-> (kind, stem) where kind is 'end' | 'nc' | 'tss', else (None, None)."""
    if fname.endswith(".end.nc"):
        return "end", fname[:-len(".end.nc")]
    if fname.endswith(".tss"):
        return "tss", fname[:-len(".tss")]
    if fname.endswith(".nc"):
        return "nc", fname[:-len(".nc")]
    return None, None


def scan_runs():
    """-> {(kind, stem): {run: source_path}}"""
    found = {}
    for run, run_dir in RUNS.items():
        if not os.path.isdir(run_dir):
            continue
        for fname in sorted(os.listdir(run_dir)):
            kind, stem = classify(fname)
            if kind:
                found.setdefault((kind, stem), {})[run] = os.path.join(run_dir, fname)
    return found


def run_metadata(run_dir):
    """Pull settingsfile/date from any .nc (or .tss header) in the run dir."""
    if not os.path.isdir(run_dir):
        return None
    for fname in sorted(os.listdir(run_dir)):
        path = os.path.join(run_dir, fname)
        if fname.endswith(".nc"):
            try:
                ds = xr.open_dataset(path)
                info = {"settingsfile": ds.attrs.get("settingsfile", ""),
                        "date_created": ds.attrs.get("date_created", "")}
                ds.close()
                return info
            except Exception:
                continue
        if fname.endswith(".tss"):
            with open(path) as f:
                header = f.readline().strip()
            return {"settingsfile": header, "date_created": ""}
    return None


def needs_update(out_png, sources, force):
    if force or not os.path.exists(out_png):
        return True
    png_mtime = os.path.getmtime(out_png)
    return any(os.path.getmtime(src) > png_mtime for src in sources)


def _rel(top, cat, png):
    """Relative path (from display/) used both on disk and in the manifest."""
    return f"{top}/{cat}/{png}"


def generate(force=False):
    os.makedirs(DISPLAY_DIR, exist_ok=True)
    found = scan_runs()
    manifest_plots = {}
    expected = set()          # relative paths, e.g. "outputs/discharge/tss_dis.png"
    n_drawn = 0

    def target(rel):
        out_png = os.path.join(DISPLAY_DIR, rel)
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        return out_png

    for (kind, stem), by_run in sorted(found.items()):
        if kind == "tss":
            rel = _rel("outputs", output_category(stem), f"tss_{stem}.png")
            expected.add(rel)
            out_png = target(rel)
            sources = list(by_run.values())
            if stem in RAIN_OVERLAY_STEMS and os.path.exists(PR_FORCING):
                sources.append(PR_FORCING)
            if needs_update(out_png, sources, force):
                try:
                    plot_tss(by_run, out_png, stem)
                    n_drawn += 1
                    print(f"  drew {rel} ({'+'.join(sorted(by_run))})")
                except Exception as e:
                    print(f"  FAILED {rel}: {e}", file=sys.stderr)
                    continue
            manifest_plots[f"tss:{stem}"] = {"img": rel, "runs": sorted(by_run)}
        else:
            cat = output_category(stem)
            entry = {}
            for run, src in by_run.items():
                # end map → PNG; per-timestep stack → GIF if >1 frame, else static PNG
                if kind == "nc":
                    animate = nc_nframes(src) > 1
                    ext = "gif" if animate else "png"
                else:
                    animate, ext = False, "png"
                rel = _rel("outputs", cat, f"{run}_{kind}_{stem}.{ext}")
                expected.add(rel)
                out_path = target(rel)
                if needs_update(out_path, [src], force):
                    try:
                        if kind == "end":
                            plot_end_map(src, out_path, stem, run)
                        elif animate:
                            plot_nc_gif(src, out_path, stem, f"{run} run")
                        else:
                            plot_nc_single(src, out_path, stem, f"{run} run")
                        n_drawn += 1
                        print(f"  drew {rel}")
                    except Exception as e:
                        print(f"  FAILED {rel}: {e}", file=sys.stderr)
                        continue
                entry[run] = rel
            if entry:
                manifest_plots[f"{kind}:{stem}"] = entry

    # ── pipeline input maps ──
    for in_dir in INPUT_DIRS:
        if not os.path.isdir(in_dir):
            continue
        for fname in sorted(os.listdir(in_dir)):
            src = os.path.join(in_dir, fname)
            if not (fname.endswith(".nc") and os.path.isfile(src)):
                continue
            stem = fname[:-3]
            rel = _rel("inputs", input_category(in_dir, stem), f"sa_{stem}.png")
            expected.add(rel)
            out_png = target(rel)
            if needs_update(out_png, [src], force):
                try:
                    plot_input(src, out_png, stem)
                    n_drawn += 1
                    print(f"  drew {rel} (input)")
                except Exception as e:
                    print(f"  FAILED {rel}: {e}", file=sys.stderr)
                    continue
            manifest_plots[f"sa:{stem}"] = {"img": rel, "runs": ["input"]}

    # ── reporting station (gauge) overlaid on the channel network ──
    outlets_nc = os.path.join(REPO_ROOT, "inputs", "reportingStations", "outlets.nc")
    if os.path.exists(outlets_nc):
        chan_nc = os.path.join(REPO_ROOT, "inputs", "maps", "chan.nc")
        rel = _rel("inputs", "channels", "sa_outlets.png")
        expected.add(rel)
        out_path = target(rel)
        if needs_update(out_path, [outlets_nc, chan_nc], force):
            try:
                plot_gauges(outlets_nc, chan_nc, out_path)
                n_drawn += 1
                print(f"  drew {rel} (gauge)")
            except Exception as e:
                print(f"  FAILED {rel}: {e}", file=sys.stderr)
        manifest_plots["sa:outlets"] = {"img": rel, "runs": ["input"]}

    # Remove managed PNGs (under inputs/ & outputs/) whose source no longer exists
    removed = 0
    for top in ("inputs", "outputs"):
        base = os.path.join(DISPLAY_DIR, top)
        for root, _dirs, files in os.walk(base):
            for fname in files:
                if not (fname.endswith((".png", ".gif")) and fname.startswith(MANAGED_PREFIXES)):
                    continue
                rel = os.path.relpath(os.path.join(root, fname), DISPLAY_DIR).replace("\\", "/")
                if rel not in expected:
                    os.remove(os.path.join(root, fname))
                    removed += 1
                    print(f"  removed stale {rel}")
        # prune empty category dirs
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                dp = os.path.join(base, d)
                if os.path.isdir(dp) and not os.listdir(dp):
                    os.rmdir(dp)

    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "runs": {run: run_metadata(d) for run, d in RUNS.items()},
        "plots": manifest_plots,
    }
    with open(MANIFEST_PATH, "w") as f:
        f.write("window.LISFLOOD_PLOTS = ")
        json.dump(manifest, f, indent=1)
        f.write(";\n")
    print(f"Done: {n_drawn} plotted, {removed} stale removed, "
          f"{len(manifest_plots)} variables in manifest.")


def snapshot():
    """Fingerprint of run + input dirs, to detect changes in watch mode."""
    state = []
    for d in list(RUNS.values()) + INPUT_DIRS:
        if os.path.isdir(d):
            for fname in sorted(os.listdir(d)):
                path = os.path.join(d, fname)
                if os.path.isfile(path):
                    state.append((path, os.path.getmtime(path), os.path.getsize(path)))
    return tuple(state)


def watch(interval=10):
    print(f"Watching run outputs and pipeline inputs every {interval}s — Ctrl-C to stop.")
    last = None
    while True:
        current = snapshot()
        if current != last:
            if last is not None:
                # LISFLOOD may still be writing; wait for the dir to settle
                time.sleep(interval)
                settled = snapshot()
                if settled != current:
                    last = current
                    continue
            print(f"[{datetime.now():%H:%M:%S}] change detected, regenerating…")
            generate()
            last = snapshot()
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--watch", action="store_true",
                        help="keep running and regenerate when outputs change")
    parser.add_argument("--force", action="store_true",
                        help="redraw everything even if PNGs are up to date")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if args.watch:
        generate(force=args.force)
        watch()
    else:
        generate(force=args.force)
