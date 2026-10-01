"""Plot GRRR discharge time series from the wide CSV written by grrr_discharge_for_gauges.py.

One panel per gauge, stacked, sharing the time axis -- small multiples, never a dual-axis
chart: the reaches differ several-fold in magnitude, so a shared y would flatten the small
one and a second y-scale would invite false comparison. Y-axes are therefore INDEPENDENT
and the panel titles carry the numbers, so magnitude is read from the labels, not the shape.

Each panel draws the raw daily series (the data) plus a centred 30-day mean (the seasonal
signal riding under the spikes), and direct-labels the single record peak.

Run from the repo root:
    python scripts/calibration/plot_grrr_discharge.py \
        --input shapefiles/grrr_discharge_roi_lev6_4060027940.csv
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# one hue, light -> dark: daily is the data, the rolling mean is emphasis on top of it.
# Both pass contrast >= 3:1 on a light surface (dataviz validator, light mode).
DAILY = "#5a90b9"
MEAN = "#1d4f75"
INK = "#222222"
MUTED = "#777777"


def parse_args():
    p = argparse.ArgumentParser(description="Plot GRRR discharge time series.")
    p.add_argument("--input", required=True, help="wide CSV: date + one column per gauge")
    p.add_argument("--roll", type=int, default=30, help="rolling-mean window in days (default 30)")
    p.add_argument("--start", default=None, help="YYYY-MM-DD zoom start")
    p.add_argument("--end", default=None, help="YYYY-MM-DD zoom end")
    p.add_argument("--out", default=None, help="output PNG (default: <input stem>.png)")
    return p.parse_args()


def main():
    args = parse_args()
    df = pd.read_csv(args.input, parse_dates=["date"], index_col="date")
    if args.start or args.end:
        df = df.loc[args.start:args.end]
    cols = list(df.columns)
    n = len(cols)
    if not n:
        raise SystemExit("no gauge columns in the input CSV")

    fig, axes = plt.subplots(n, 1, figsize=(12, 3.1 * n + 1.0), sharex=True)
    axes = [axes] if n == 1 else list(axes)

    for ax, c in zip(axes, cols):
        s = df[c].dropna()
        roll = s.rolling(args.roll, center=True, min_periods=1).mean()
        # hairline for 16k daily points -- a 2px line would fill the panel solid
        ax.plot(s.index, s.values, color=DAILY, linewidth=0.35, alpha=0.9,
                zorder=2, label="daily")
        ax.plot(roll.index, roll.values, color=MEAN, linewidth=1.4,
                zorder=3, label=f"{args.roll}-day mean")

        # direct-label the record peak only -- never a number on every point
        i = s.idxmax()
        ax.scatter([i], [s.max()], s=26, facecolor="white", edgecolor=MEAN,
                   linewidth=1.3, zorder=4)
        # flip the label inboard when the peak sits in the right third, else it overflows
        late = i > s.index[0] + (s.index[-1] - s.index[0]) * 0.66
        ax.annotate(f"{s.max():,.0f}  ({i:%Y-%m-%d})",
                    xy=(i, s.max()), xytext=(-8 if late else 8, -2),
                    textcoords="offset points", fontsize=8, color=INK, va="top",
                    ha="right" if late else "left",
                    bbox=dict(facecolor="white", edgecolor="none", pad=1.0, alpha=0.85))

        ax.set_title(f"{c}      mean {s.mean():,.1f}   median {s.median():,.1f}   "
                     f"peak {s.max():,.0f}",
                     fontsize=10, color=INK, loc="left", pad=6)
        ax.set_ylabel("discharge (m³/s)", fontsize=9)
        ax.set_ylim(0, s.max() * 1.18)
        ax.margins(x=0.005)
        ax.grid(axis="y", linewidth=0.4, alpha=0.35, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("0.7")

    axes[0].legend(fontsize=8, frameon=False, loc="upper left", ncol=2)
    axes[-1].set_xlabel("date", fontsize=9)

    stem = os.path.splitext(os.path.basename(args.input))[0]
    span = f"{df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}"
    fig.suptitle("GRRR reanalysis discharge — modelled, not observed",
                 fontsize=13, color=INK, x=0.062, ha="left", y=0.985)
    fig.text(0.062, 0.958,
             f"{span}   ·   {len(df):,} daily steps   ·   independent y-axes per reach   "
             f"·   units not declared in the GRRR store (m³/s inferred)",
             fontsize=8.5, color=MUTED, ha="left", va="top")

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.input)), f"{stem}.png")
    fig.subplots_adjust(left=0.075, right=0.98, top=0.90, bottom=0.075, hspace=0.30)
    fig.savefig(out, dpi=200)
    print(f"wrote {out}")
    for c in cols:
        s = df[c].dropna()
        print(f"  {c:20} n={len(s):,}  mean={s.mean():8.2f}  peak={s.max():9.2f} "
              f"on {s.idxmax():%Y-%m-%d}")


if __name__ == "__main__":
    main()
