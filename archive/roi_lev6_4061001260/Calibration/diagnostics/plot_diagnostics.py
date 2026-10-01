#!/usr/bin/env python
"""Calibration diagnostic plots: is the model wrong in TIMING or in VOLUME?

Reads the simulated discharge of the current best-KGE run and the observations
CAL_1 filtered, then draws the hydrograph and the cumulative water volume.
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

CAL = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/Calibration"
OUT = os.path.join(CAL, "diagnostics")
os.makedirs(OUT, exist_ok=True)

# validated categorical slots 1 and 2 (see dataviz validator)
OBS_C, SIM_C = "#2a78d6", "#eb6834"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8b8a85"
SURFACE = "#fcfcfb"

FORCING_START = pd.Timestamp("2003-01-01")
SPLIT_DATE = pd.Timestamp("2012-07-02")   # station_data.csv -> Split_date
AREA_M2 = 4745.4 * 1e6                    # DrainingArea km2 -> m2


def read_tss(path):
    """LISFLOOD .tss -> Series indexed by date. Header is 4 lines; col 1 is the
    timestep counter, which is days since CalendarDayStart (t=1 -> 2003-01-01)."""
    ts, val = [], []
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i < 4:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            ts.append(int(parts[0]))
            val.append(float(parts[1]))
    idx = FORCING_START + pd.to_timedelta(np.array(ts) - 1, unit="D")
    return pd.Series(val, index=idx, name="Sim")


def fkge(s, o):
    """Exactly liscal/hydro_stats.py::fKGE, so the number is comparable."""
    m = np.isfinite(s) & np.isfinite(o)
    s, o = s[m], o[m]
    r = np.corrcoef(o, s)[0, 1]
    B = np.mean(s) / np.mean(o)
    y = (np.std(s) / np.mean(s)) / (np.std(o) / np.mean(o))
    return 1 - np.sqrt((r - 1) ** 2 + (B - 1) ** 2 + (y - 1) ** 2), r, B, y


def pick_best(params_csv):
    """Which run is 'best'? Report both notions liscal uses."""
    d = pd.read_csv(params_csv)
    d = d[pd.to_numeric(d["generation"], errors="coerce").notna()].copy()
    for c in ["Kling Gupta Efficiency", "Correlation", "sae"]:
        d[c] = pd.to_numeric(d[c])
    by_kge = d.loc[d["Kling Gupta Efficiency"].idxmax()]

    # liscal's end-of-run composite: top 10% by KGE, then corrRank*saeRank*KGERank
    top = d.sort_values("Kling Gupta Efficiency", ascending=False)
    top = top.head(int(max(2, round(len(d) * 0.1)))).copy()
    n, lo, hi = len(top), 0.1, 1.0
    def rank(frame, col, asc):
        f = frame.sort_values(col, ascending=asc)
        return pd.Series([lo + (i + 1) * (hi - lo) / n for i in range(n)], index=f.index)
    top["corrRank"] = rank(top, "Correlation", False)
    top["saeRank"] = rank(top, "sae", True)
    top["KGERank"] = rank(top, "Kling Gupta Efficiency", False)
    top["paretoRank"] = top["corrRank"] * top["saeRank"] * top["KGERank"]
    by_pareto = top.loc[top["paretoRank"].idxmin()]
    return by_kge, by_pareto, len(d)


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color="#e6e5e0", lw=0.7, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#d5d4ce")
    ax.tick_params(colors=INK2, labelsize=9, length=3, color="#d5d4ce")


best_kge, best_pareto, n_runs = pick_best(os.path.join(CAL, "catchments/1/paramsHistory.csv"))
run_id = best_kge["randId"]
print(f"runs so far                : {n_runs}")
print(f"best by KGE (DEAP fitness) : {run_id}  KGE={best_kge['Kling Gupta Efficiency']:.4f}")
print(f"best by liscal composite   : {best_pareto['randId']}  KGE={best_pareto['Kling Gupta Efficiency']:.4f}")

sim = read_tss(os.path.join(CAL, f"catchments/1/out/{run_id}/dis.tss"))
obs = pd.read_csv(os.path.join(CAL, "catchments/1/station/observations.csv"),
                  index_col=0, parse_dates=True, dayfirst=True).iloc[:, 0]
obs.name = "Obs"

df = pd.concat([sim, obs], axis=1).loc[sim.index.min():sim.index.max()]
scored = df.loc[SPLIT_DATE:]
kge, r, B, y = fkge(scored["Sim"].values, scored["Obs"].values)
print(f"\nscored window {scored.index.min():%Y-%m-%d} -> {scored.index.max():%Y-%m-%d}  n={len(scored)}")
print(f"recomputed KGE={kge:.4f}  r={r:.3f}  beta={B:.3f}  gamma={y:.3f}")
print(f"(paramsHistory says       KGE={best_kge['Kling Gupta Efficiency']:.4f})")

# ---------------------------------------------------------------- figure 1
fig, axes = plt.subplots(2, 1, figsize=(12, 7.6), facecolor=SURFACE,
                         gridspec_kw={"height_ratios": [1.35, 1], "hspace": 0.38})

ax = axes[0]
ax.axvspan(df.index.min(), SPLIT_DATE, color="#efeee8", zorder=0)
ax.plot(df.index, df["Obs"], color=OBS_C, lw=1.0, label="Observed", zorder=3)
ax.plot(df.index, df["Sim"], color=SIM_C, lw=1.0, label="Simulated", zorder=4)
style(ax)
# place after autoscale so the label sits inside the axes, not on the ticks
ax.text(df.index.min() + pd.Timedelta(days=30), ax.get_ylim()[1] * 0.94,
        "spin-up\nnot scored", va="top", ha="left", fontsize=8.5, color=MUTED, zorder=5)
ax.set_ylabel("Discharge  (m³/s)", color=INK2, fontsize=9.5)
ax.set_title("A · Hydrograph — peaks line up, but the model runs far too low",
             color=INK, fontsize=12, weight="bold", loc="left", pad=10)
ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK2)
ax.set_xlim(df.index.min(), df.index.max())

zoom = df.loc["2019-01-01":"2019-12-31"]
ax = axes[1]
ax.plot(zoom.index, zoom["Obs"], color=OBS_C, lw=1.6, label="Observed", zorder=3)
ax.plot(zoom.index, zoom["Sim"], color=SIM_C, lw=1.6, label="Simulated", zorder=4)
ax.fill_between(zoom.index, zoom["Sim"], zoom["Obs"], where=zoom["Obs"] >= zoom["Sim"],
                color=SIM_C, alpha=0.10, lw=0, zorder=2)
style(ax)
ax.set_ylabel("Discharge  (m³/s)", color=INK2, fontsize=9.5)
ax.set_title("B · One monsoon (2019) — same shape, same weeks, ~a third of the water",
             color=INK, fontsize=12, weight="bold", loc="left", pad=10)
ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK2)
ax.set_xlim(zoom.index.min(), zoom.index.max())

fig.text(0.008, 0.012,
         f"run {run_id} · scored {SPLIT_DATE:%Y-%m-%d}→{scored.index.max():%Y-%m-%d} · "
         f"KGE {kge:.3f}  =  r {r:.3f} · β {B:.3f} · γ {y:.3f}   "
         f"(β is the volume ratio: the model delivers {B*100:.0f}% of observed flow)",
         fontsize=8.5, color=MUTED)
fig.savefig(os.path.join(OUT, "fig1_hydrograph.png"), dpi=170,
            bbox_inches="tight", facecolor=SURFACE)
print("wrote fig1_hydrograph.png")

# ---------------------------------------------------------------- figure 2
cum = scored.copy()
for c in ("Obs", "Sim"):
    cum[c + "_mm"] = (cum[c] * 86400).cumsum() / AREA_M2 * 1000.0

fig, ax = plt.subplots(figsize=(12, 5.6), facecolor=SURFACE)
ax.plot(cum.index, cum["Obs_mm"], color=OBS_C, lw=2.0, label="Observed", zorder=3)
ax.plot(cum.index, cum["Sim_mm"], color=SIM_C, lw=2.0, label="Simulated", zorder=4)
ax.fill_between(cum.index, cum["Sim_mm"], cum["Obs_mm"], color=SIM_C, alpha=0.10, lw=0, zorder=2)
style(ax)

o_end, s_end = cum["Obs_mm"].iloc[-1], cum["Sim_mm"].iloc[-1]
yrs = (cum.index[-1] - cum.index[0]).days / 365.25
ax.annotate("", xy=(cum.index[-1], o_end), xytext=(cum.index[-1], s_end),
            arrowprops=dict(arrowstyle="<->", color=INK2, lw=1.3))
ax.text(cum.index[-1] - pd.Timedelta(days=60), (o_end + s_end) / 2,
        f"missing\n{o_end - s_end:,.0f} mm\n({(o_end - s_end)/yrs:,.0f} mm/yr)",
        ha="right", va="center", fontsize=10, color=INK, weight="bold")
for val, col, lab in ((o_end, OBS_C, "Observed"), (s_end, SIM_C, "Simulated")):
    ax.text(cum.index[-1] + pd.Timedelta(days=40), val, f"{lab}\n{val:,.0f} mm",
            va="center", fontsize=9, color=INK2)
ax.set_ylabel("Cumulative runoff depth  (mm)", color=INK2, fontsize=9.5)
ax.set_title("C · Cumulative water volume — the gap that no parameter can close",
             color=INK, fontsize=12, weight="bold", loc="left", pad=10)
ax.legend(frameon=False, loc="upper left", fontsize=9, labelcolor=INK2)
ax.set_xlim(cum.index.min(), cum.index.max() + pd.Timedelta(days=430))
ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:,.0f}"))
fig.text(0.008, 0.005,
         f"Observed {o_end/yrs:,.0f} mm/yr vs simulated {s_end/yrs:,.0f} mm/yr over {yrs:.1f} years. "
         f"Catchment 4745 km², mean precipitation ~1437 mm/yr → runoff coefficient "
         f"{o_end/yrs/1437:.2f} observed vs {s_end/yrs/1437:.2f} simulated.",
         fontsize=8.5, color=MUTED)
fig.savefig(os.path.join(OUT, "fig2_cumulative_volume.png"), dpi=170,
            bbox_inches="tight", facecolor=SURFACE)
print("wrote fig2_cumulative_volume.png")

print(f"\nobserved  {o_end/yrs:8,.1f} mm/yr")
print(f"simulated {s_end/yrs:8,.1f} mm/yr")
print(f"deficit   {(o_end-s_end)/yrs:8,.1f} mm/yr")
