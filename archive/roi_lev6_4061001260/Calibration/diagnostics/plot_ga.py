#!/usr/bin/env python
"""What the genetic algorithm actually did, drawn from paramsHistory.csv.

Panel A: every individual ever evaluated, by generation -> selection pressure.
Panel B: how the population's spread in each parameter collapsed -> convergence,
         and which parameters got pinned against their bounds.
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CAL = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/Calibration"
OUT = os.path.join(CAL, "diagnostics")
os.makedirs(OUT, exist_ok=True)

BLUE, INK, INK2, MUTED = "#2a78d6", "#0b0b0b", "#52514e", "#8b8a85"
CLOUD, BAND = "#b9c9df", "#dbe4ef"
SURFACE = "#fcfcfb"
KGE = "Kling Gupta Efficiency"

ranges = pd.read_csv(os.path.join(CAL, "param_ranges.csv"), index_col=0)
d = pd.read_csv(os.path.join(CAL, "catchments/1/paramsHistory.csv"))
d = d[pd.to_numeric(d["generation"], errors="coerce").notna()].copy()
d["generation"] = d["generation"].astype(int)
d[KGE] = pd.to_numeric(d[KGE])
for p in ranges.index:
    d[p] = pd.to_numeric(d[p])

gens = sorted(d["generation"].unique())
print(f"{len(d)} individuals across generations {gens[0]}-{gens[-1]}")
for g in gens:
    s = d[d["generation"] == g][KGE]
    print(f"  gen {g}: n={len(s):3d}  best={s.max():.4f}  median={s.median():+.4f}  worst={s.min():+.4f}")


def style(ax, small=False):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color="#e6e5e0", lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#d5d4ce")
    ax.tick_params(colors=INK2, labelsize=8 if small else 9, length=3, color="#d5d4ce")


fig = plt.figure(figsize=(12.5, 11.0), facecolor=SURFACE)
gs = fig.add_gridspec(3, 3, height_ratios=[1.5, 1, 1], hspace=0.75, wspace=0.28,
                      top=0.93, bottom=0.06)

# ---------------------------------------------------------------- panel A
ax = fig.add_subplot(gs[0, :])
rng = np.random.default_rng(0)
ax.scatter(d["generation"] + rng.uniform(-0.16, 0.16, len(d)), d[KGE],
           s=16, color=CLOUD, edgecolor="none", zorder=2, label="every individual evaluated")
best = d.groupby("generation")[KGE].max().cummax()
ax.plot(best.index, best.values, color=BLUE, lw=2.2, marker="o", ms=6,
        zorder=4, label="best so far (what DEAP keeps)")
for g, v in best.items():
    ax.annotate(f"{v:.3f}", (g, v), textcoords="offset points", xytext=(0, 9),
                ha="center", fontsize=8, color=INK2)
style(ax)
ax.set_xticks(gens)
ax.set_xlabel("Generation", color=INK2, fontsize=9.5)
ax.set_ylabel("KGE", color=INK2, fontsize=9.5)
# headroom so the per-generation value labels clear the title
lo_y, hi_y = d[KGE].min(), d[KGE].max()
ax.set_ylim(lo_y - 0.12, hi_y + 0.30)
ax.set_title("A · Selection pressure — the cloud of candidates rises and tightens",
             color=INK, fontsize=12.5, weight="bold", loc="left", pad=26)
ax.text(0, 1.03, "gen 0 = 72 random guesses; every later generation = 36 children",
        transform=ax.transAxes, fontsize=8.5, color=MUTED, va="bottom")
ax.legend(frameon=False, loc="lower right", fontsize=9, labelcolor=INK2)

# ---------------------------------------------------------------- panel B
show = ["UpperZoneTimeConstant", "GwPercValue", "adjust_Normal_Flood",
        "LowerZoneTimeConstant", "b_Xinanjiang", "CalChanMan2"]
best_row = d.loc[d[KGE].idxmax()]

for i, p in enumerate(show):
    ax = fig.add_subplot(gs[1 + i // 3, i % 3])
    lo, hi = ranges.loc[p, "MinValue"], ranges.loc[p, "MaxValue"]
    norm = (d[p] - lo) / (hi - lo)
    g = d["generation"]
    band = pd.DataFrame({"g": g, "v": norm}).groupby("g")["v"]
    ax.fill_between(band.min().index, band.min().values, band.max().values,
                    color=BAND, lw=0, zorder=2, label="population range")
    ax.scatter(g + rng.uniform(-0.14, 0.14, len(d)), norm, s=7,
               color=CLOUD, edgecolor="none", zorder=3)
    bl = d.loc[d.groupby("generation")[KGE].idxmax()]
    ax.plot(bl["generation"], (bl[p] - lo) / (hi - lo), color=BLUE, lw=1.8, zorder=4,
            label="best of generation")
    ax.axhline(0, color="#c9463c", lw=1.1, ls=(0, (4, 3)), zorder=5)
    ax.axhline(1, color="#c9463c", lw=1.1, ls=(0, (4, 3)), zorder=5)
    style(ax, small=True)
    ax.set_ylim(-0.09, 1.09)
    ax.set_xticks(gens)
    ax.set_yticks([0, 0.5, 1])
    ax.set_yticklabels(["min", "", "max"])
    v = best_row[p]
    ax.set_title(f"{p}\nbest = {v:,.3g}   (range {lo:g} – {hi:g})",
                 color=INK, fontsize=9.5, weight="bold", loc="left", pad=6)
    if i == 0:
        first_ax = ax

# section title placed above the FIRST small-multiple row, not between the rows
p0 = first_ax.get_position()
fig.text(0.008, p0.y1 + 0.055,
         "B · Convergence — the search narrowing, and pinning itself to the bounds (red)",
         fontsize=12.5, color=INK, weight="bold", ha="left")
handles = [plt.Line2D([], [], color=BLUE, lw=1.8, label="best of generation"),
           plt.Rectangle((0, 0), 1, 1, color=BAND, label="population range"),
           plt.Line2D([], [], color="#c9463c", lw=1.1, ls=(0, (4, 3)), label="allowed bounds")]
# sits on the same line as the section title, in the empty space to its right
fig.legend(handles=handles, frameon=False, fontsize=8.5, labelcolor=INK2,
           loc="lower right", bbox_to_anchor=(0.99, p0.y1 + 0.050), ncol=3)
fig.text(0.008, 0.004,
         "Each dot is one 30-minute LISFLOOD evaluation. Values normalised to each "
         "parameter's allowed range from param_ranges.csv, so 'max' means the optimiser "
         "has pushed that parameter as far as it is permitted to go.",
         fontsize=8.5, color=MUTED)
fig.savefig(os.path.join(OUT, "fig3_ga_evolution.png"), dpi=170,
            bbox_inches="tight", facecolor=SURFACE)
print("wrote fig3_ga_evolution.png")

print("\nbest individual, position within each parameter's allowed range:")
for p in ranges.index:
    lo, hi = ranges.loc[p, "MinValue"], ranges.loc[p, "MaxValue"]
    f = (best_row[p] - lo) / (hi - lo)
    flag = "  <-- PINNED" if (f < 0.02 or f > 0.98) else ""
    print(f"  {p:24s} {best_row[p]:>10,.3f}   {f*100:5.1f}% of range{flag}")
