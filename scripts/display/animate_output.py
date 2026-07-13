"""Animate a LISFLOOD per-timestep map stack (.nc) into a movie.

Usage:
    python animate_output.py outputs/warm/dis.nc
    python animate_output.py outputs/warm/srun.nc --fps 4 --out runoff.gif

Writes an MP4 if ffmpeg is available, otherwise a GIF. The colour scale is
fixed across all frames (robust 2–98 percentile over the whole stack) so
brightness changes reflect real magnitude changes, not per-frame rescaling.

NOTE for surface runoff: srun.nc is only written if repSurfaceRunoffMaps=1
AND ReportSteps writes every timestep (not "endtime"). See generate note below.
"""

import argparse
import os
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import animation
import numpy as np
import pandas as pd
import xarray as xr

# stem -> (title, units, colormap) — falls back to the variable name
META = {
    "srun":  ("Surface runoff generated", "mm/timestep", "YlGnBu"),
    "dis":   ("Channel discharge", "m³/s", "Blues"),
    "ofoth": ("Overland flow storage — other", "m³", "Blues"),
    "offor": ("Overland flow storage — forest", "m³", "Blues"),
    "ofdir": ("Overland flow storage — direct", "m³", "Blues"),
    "trun":  ("Total runoff", "mm/timestep", "YlGnBu"),
    "inf":   ("Infiltration", "mm/timestep", "Greens"),
    "pr":    ("Precipitation", "mm/day", "Blues"),
}


def robust_limits(arr):
    vals = arr[np.isfinite(arr)]
    if vals.size == 0:
        return 0.0, 1.0
    vmin, vmax = np.percentile(vals, [2, 98])
    if vmin == vmax:
        vmin, vmax = float(np.nanmin(vals)), float(np.nanmax(vals))
    if vmin == vmax:
        vmax = vmin + 1e-9
    return float(vmin), float(vmax)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ncfile", help="path to a per-timestep .nc map stack")
    ap.add_argument("--out", help="output movie path (.mp4 or .gif)")
    ap.add_argument("--fps", type=int, default=5, help="frames per second")
    ap.add_argument("--dpi", type=int, default=130)
    args = ap.parse_args()

    ds = xr.open_dataset(args.ncfile)
    da = ds[list(ds.data_vars)[0]]
    if "time" not in da.sizes:
        raise SystemExit(
            f"{args.ncfile} has no time dimension — nothing to animate. "
            "For a movie the map must be written every timestep "
            "(repSurfaceRunoffMaps=1 and ReportSteps writing each step, then rerun).")
    nframes = da.sizes["time"]
    if nframes < 2:
        raise SystemExit(
            f"{args.ncfile} has only {nframes} frame — ReportSteps is likely "
            "set to 'endtime'. Change it to write every timestep and rerun.")

    stem = os.path.basename(args.ncfile).split(".")[0]
    title, units, cmap = META.get(stem, (stem, "", "viridis"))
    times = pd.to_datetime(da["time"].values)
    data = da.values.astype(float)
    vmin, vmax = robust_limits(data)

    fig, ax = plt.subplots(figsize=(7, 6), dpi=args.dpi)
    im = ax.pcolormesh(da["x"], da["y"], data[0], cmap=cmap,
                       vmin=vmin, vmax=vmax, shading="auto")
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label(units, fontsize=9, color="#475569")
    ttl = ax.set_title("", fontsize=12, fontweight="bold", color="#1e293b")

    def update(i):
        im.set_array(data[i].ravel())
        ttl.set_text(f"{title}\n{times[i]:%Y-%m-%d}  (frame {i+1}/{nframes})")
        return im, ttl

    anim = animation.FuncAnimation(fig, update, frames=nframes, blit=False)

    out = args.out
    if not out:
        base = os.path.splitext(os.path.basename(args.ncfile))[0]
        ext = ".mp4" if shutil.which("ffmpeg") else ".gif"
        out = os.path.join(os.path.dirname(args.ncfile), base + "_movie" + ext)

    if out.endswith(".mp4") and shutil.which("ffmpeg"):
        anim.save(out, writer=animation.FFMpegWriter(fps=args.fps, bitrate=2400))
    else:
        if out.endswith(".mp4"):
            out = out[:-4] + ".gif"
            print("ffmpeg not found → writing GIF instead")
        anim.save(out, writer=animation.PillowWriter(fps=args.fps))
    plt.close(fig)
    ds.close()
    print(f"saved {out}  ({nframes} frames @ {args.fps} fps)")


if __name__ == "__main__":
    main()
