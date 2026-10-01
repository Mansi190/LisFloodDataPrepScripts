"""How many gauges land in a HydroBASINS unit, as a function of level.

Answers "which level do I get study areas with >= N gauges at?" empirically.
For each level 6..12 every gauge is assigned to its containing basin, then we
report the gauges-per-basin distribution.

Note the counts are for a SINGLE basin polygon at that level. A LISFLOOD study
area built with EXTENT="catchment" merges everything upstream, so it holds at
least this many gauges -- read these as a lower bound / relative comparison
across levels, not as the final study-area count.

Outputs
  <stem>_per_hydrobasin_level.csv   one row per (level, basin) with a gauge
  <stem>_per_hydrobasin_level.png   bars of qualifying basins + gauges/basin CDF

Run (base conda env has ee/geopandas):
    /opt/homebrew/Caskroom/miniconda/base/bin/python \
        streamflow/google/gauges_per_hydrobasin_level.py
"""
import argparse
import os
from collections import Counter

import ee
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT = "gssha-480613"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IN = os.path.join(HERE, "floodhub_high_conf_gauges_india.csv")

INK = "#222222"
MUTED = "#777777"
# sequential ramp: level is ORDINAL, so one hue light -> dark, never categorical hues
RAMP = ["#cfe0ee", "#a8c6dd", "#7fabcb", "#5a90b9", "#3b76a3", "#2f6f9f", "#1d4f75"]


def parse_args():
    p = argparse.ArgumentParser(description="Gauges per HydroBASINS unit, by level.")
    p.add_argument("--input", default=DEFAULT_IN, help="CSV with latitude,longitude")
    p.add_argument("--levels", default="6,7,8,9,10,11,12",
                   help="comma-separated HydroBASINS levels (default 6..12)")
    p.add_argument("--min-gauges", type=int, default=2,
                   help="a basin 'qualifies' at >= this many gauges (default 2)")
    p.add_argument("--chunk", type=int, default=150, help="points per EE call")
    return p.parse_args()


def load_points(path):
    df = pd.read_csv(path).dropna(subset=["latitude", "longitude"])
    if "gauge_id" not in df.columns:
        for alt in ("id", "reach_id", "station_id", "site_no"):
            if alt in df.columns:
                df = df.rename(columns={alt: "gauge_id"})
                break
    if "gauge_id" not in df.columns:
        df["gauge_id"] = [f"pt{i}" for i in range(len(df))]
    return df.reset_index(drop=True)


def assign_level(rows, level, chunk):
    """Server-side point-in-polygon: gauge -> containing basin at `level`."""
    basins = ee.FeatureCollection(f"WWF/HydroATLAS/v1/Basins/level{level:02d}")
    out = []
    for i in range(0, len(rows), chunk):
        feats = [ee.Feature(ee.Geometry.Point([r["longitude"], r["latitude"]]),
                            {"gauge_id": r["gauge_id"]}) for r in rows[i:i + chunk]]

        def tag(f):
            b = ee.Feature(basins.filterBounds(f.geometry()).first())
            return f.set({"HYBAS_ID": b.get("HYBAS_ID"),
                          "SUB_AREA": b.get("SUB_AREA")})

        got = ee.FeatureCollection(feats).map(tag).getInfo()["features"]
        out.extend(f["properties"] for f in got)
        print(f"    level {level}: {min(i + chunk, len(rows))}/{len(rows)}")
    return out


def main():
    args = parse_args()
    levels = [int(x) for x in args.levels.split(",")]
    ee.Initialize(project=PROJECT)

    df = load_points(args.input)
    rows = df.to_dict("records")
    print(f"loaded {len(rows)} gauges from {os.path.basename(args.input)}")

    records, summary = [], []
    for lv in levels:
        props = assign_level(rows, lv, args.chunk)
        assigned = [p for p in props if p.get("HYBAS_ID") is not None]
        counts = Counter(p["HYBAS_ID"] for p in assigned)
        area = {p["HYBAS_ID"]: p.get("SUB_AREA") for p in assigned}

        for hid, c in counts.items():
            records.append({"level": lv, "HYBAS_ID": hid, "n_gauges": c,
                            "sub_area_km2": area.get(hid)})

        per_basin = np.array(sorted(counts.values()))
        areas = np.array([a for a in area.values() if a], dtype=float)
        summary.append({
            "level": lv,
            "gauges_assigned": len(assigned),
            "basins_with_gauge": len(counts),
            f"basins_ge_{args.min_gauges}": int((per_basin >= args.min_gauges).sum()),
            "max_gauges_in_basin": int(per_basin.max()) if len(per_basin) else 0,
            "median_area_km2": float(np.median(areas)) if len(areas) else np.nan,
        })
        print(f"  level {lv}: {len(counts)} basins hold gauges, "
              f"{int((per_basin >= args.min_gauges).sum())} have >= {args.min_gauges}")

    det = pd.DataFrame(records)
    summ = pd.DataFrame(summary)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    out_csv = os.path.join(HERE, f"{stem}_per_hydrobasin_level.csv")
    det.to_csv(out_csv, index=False)
    print(f"\nwrote {out_csv}")
    print("\n" + summ.to_string(index=False))

    plot(det, summ, stem, args.min_gauges)


def plot(det, summ, stem, min_gauges):
    levels = summ["level"].tolist()
    qual_col = f"basins_ge_{min_gauges}"

    fig, (ax_b, ax_c) = plt.subplots(1, 2, figsize=(12, 5.2))

    # ---- left: how many usable study areas per level -----------------------
    x = np.arange(len(levels))
    w = 0.38
    ax_b.bar(x - w / 2, summ["basins_with_gauge"], width=w,
             color="#a8c6dd", label="basins with >= 1 gauge", zorder=3)
    ax_b.bar(x + w / 2, summ[qual_col], width=w,
             color="#2f6f9f", label=f"basins with >= {min_gauges} gauges", zorder=3)
    for xi, v in zip(x, summ[qual_col]):
        ax_b.text(xi + w / 2, v + 0.6, str(int(v)), ha="center", va="bottom",
                  fontsize=8, color=MUTED)
    ax_b.set_xticks(x)
    ax_b.set_xticklabels([f"L{l}\n{a:,.0f} km²" if np.isfinite(a) else f"L{l}"
                          for l, a in zip(levels, summ["median_area_km2"])],
                         fontsize=8)
    ax_b.set_xlabel("HydroBASINS level  (median area of gauged basins)")
    ax_b.set_ylabel("number of basins")
    ax_b.set_title("Usable study areas by level", fontsize=11, color=INK, loc="left")
    ax_b.legend(fontsize=8, frameon=False)

    # ---- right: CDF of gauges per basin, one curve per level ---------------
    for i, lv in enumerate(levels):
        v = np.sort(det.loc[det["level"] == lv, "n_gauges"].values)
        if not len(v):
            continue
        xs = np.arange(1, v.max() + 1)
        cdf = np.array([(v <= k).mean() for k in xs])
        colour = RAMP[i % len(RAMP)]
        ax_c.step(np.concatenate(([0.5], xs)), np.concatenate(([0.0], cdf)),
                  where="post", color=colour, linewidth=2, zorder=3, label=f"L{lv}")
        ax_c.plot(xs, cdf, "o", color=colour, markersize=4,
                  markeredgecolor="white", markeredgewidth=0.9, zorder=4)

    ax_c.axvline(min_gauges - 0.5, color=MUTED, linestyle="--", linewidth=0.9, zorder=1)
    # the informative range is the first few gauges; L6's long tail is noted instead of drawn
    xmax = 8
    tail = int(det["n_gauges"].max())
    ax_c.set_xlim(0.4, xmax + 0.4)
    ax_c.set_xticks(range(1, xmax + 1))
    if tail > xmax:
        ax_c.text(xmax + 0.3, 0.02, f"tail to {tail} →", fontsize=7.5,
                  color=MUTED, ha="right", va="bottom")
    ax_c.set_ylim(0, 1.06)
    ax_c.set_yticks(np.arange(0, 1.01, 0.25))
    ax_c.set_yticklabels([f"{int(100*t)}%" for t in np.arange(0, 1.01, 0.25)])
    ax_c.set_xlabel("gauges in the basin")
    ax_c.set_ylabel("cumulative share of gauged basins")
    ax_c.set_title("Gauges per basin (CDF, basins holding >= 1 gauge)",
                   fontsize=11, color=INK, loc="left")
    ax_c.legend(fontsize=8, frameon=False, title="level", title_fontsize=8,
                loc="lower right", ncol=2)

    for ax in (ax_b, ax_c):
        ax.grid(axis="y", linewidth=0.4, alpha=0.35, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("0.7")

    out_png = os.path.join(HERE, f"{stem}_per_hydrobasin_level.png")
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
