#!/usr/bin/env python
"""Where does the missing water go?

Calibration is bias-limited: beta = 0.33, i.e. the model routes only a third of
the observed volume past the gauge. The 11 calibrated parameters re-partition and
route water, they cannot create it, so the deficit has to sit in the water
balance itself. Over a period long enough for storage change to be negligible,

    P = ET + Q  (+ dS ~ 0, and calibrated GwLoss = 0)

so any mm that is not discharge left as evaporation. This reads the model's OWN
reported fluxes upstream of the gauge -- no inference -- and puts the simulated
balance next to the one the observations imply.

CAL_9_DIAGNOSTICS.py would have drawn something like this, but it wants 18
timeseries of which this run reports 4, plus three (theta1-3total) that do not
exist in the LISFLOOD 4.3.1 template at all. The rate series it needs are gated
on repRateUpsGauges, which is off because enabling it crashes the run (see the
comment in templates/settings_lisflood.xml). The four series used here are the
ones already reported, and they carry the argument on their own.
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CAL = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/Calibration"
OUT = os.path.join(CAL, "diagnostics")
RUN = os.path.join(CAL, "catchments", "1", "out", "long_term_run")
os.makedirs(OUT, exist_ok=True)

# validated categorical slots (see dataviz validator), matching plot_diagnostics.py
OBS_C, SIM_C = "#2a78d6", "#eb6834"
P_C, ET_C = "#6b8f3a", "#b5432f"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#8b8a85"
SURFACE = "#fcfcfb"

FORCING_START = pd.Timestamp("2003-01-01")
AREA_M2 = 4745.4 * 1e6                    # DrainingArea km2 -> m2
EVAL_START, EVAL_END = pd.Timestamp("2011-07-03"), pd.Timestamp("2020-12-31")


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
    return pd.Series(val, index=idx)


def cumecs_to_mm_day(s):
    """m3/s at the outlet -> catchment-average mm/day."""
    return s * 86400.0 / AREA_M2 * 1000.0


# ---------------------------------------------------------------- load
rain = read_tss(os.path.join(RUN, "rainUps.tss"))    # mm/day
snow = read_tss(os.path.join(RUN, "snowUps.tss"))    # mm/day
et = read_tss(os.path.join(RUN, "etUps.tss"))        # mm/day, ACTUAL ET

sim = pd.read_csv(os.path.join(CAL, "catchments", "1", "out",
                               "streamflow_simulated_best.csv"), index_col=0)
sim.index = pd.to_datetime(sim.index, format="%d/%m/%Y %H:%M")
qsim = cumecs_to_mm_day(sim.iloc[:, 0])

obs = pd.read_csv(os.path.join(CAL, "catchments", "1", "station",
                               "observations.csv"), index_col=0)
obs.index = pd.to_datetime(obs.index, dayfirst=True, errors="coerce")
qobs = cumecs_to_mm_day(pd.to_numeric(obs.iloc[:, 0], errors="coerce"))

sl = slice(EVAL_START, EVAL_END)
years = (EVAL_END - EVAL_START).days / 365.25

P = (rain[sl] + snow[sl]).sum() / years
ET = et[sl].sum() / years
QS = qsim[sl].sum() / years
QO = qobs[sl].dropna().sum() / len(qobs[sl].dropna()) * 365.25
ET_IMPLIED = P - QO                       # what the observed balance leaves for ET

print(f"P            {P:8.1f} mm/yr")
print(f"ET (model)   {ET:8.1f} mm/yr   -> runoff coeff {QS / P:.3f}")
print(f"Q simulated  {QS:8.1f} mm/yr")
print(f"Q observed   {QO:8.1f} mm/yr   -> runoff coeff {QO / P:.3f}")
print(f"ET implied   {ET_IMPLIED:8.1f} mm/yr  (P - Qobs)")
print(f"ET excess    {ET - ET_IMPLIED:8.1f} mm/yr  ({ET / ET_IMPLIED - 1:+.0%})")

# ---------------------------------------------------------------- figure
fig, (axA, axB) = plt.subplots(1, 2, figsize=(13.4, 5.4), facecolor=SURFACE,
                               gridspec_kw={"width_ratios": [1, 1.25]})

# A -- the two balances, stacked to the same rainfall
labels = ["Model", "Implied by\nobservations"]
ets = [ET, ET_IMPLIED]
qs = [QS, QO]
x = np.arange(2)
axA.bar(x, ets, 0.55, color=ET_C, label="Evapotranspiration")
axA.bar(x, qs, 0.55, bottom=ets, color=OBS_C, label="Discharge")
axA.axhline(P, color=P_C, lw=2, ls="--", zorder=5)
axA.text(1.46, P, f"  Precipitation\n  {P:,.0f} mm/yr", color=P_C, fontsize=9,
         va="center", ha="left", fontweight="bold")

for i, (e, q) in enumerate(zip(ets, qs)):
    axA.text(i, e / 2, f"ET\n{e:,.0f}", ha="center", va="center",
             color="white", fontsize=10.5, fontweight="bold")
    axA.text(i, e + q / 2, f"Q  {q:,.0f}", ha="center", va="center",
             color="white", fontsize=10.5, fontweight="bold")

axA.annotate("", xy=(0.34, ET_IMPLIED), xytext=(0.34, ET),
             arrowprops=dict(arrowstyle="<->", color=INK, lw=1.6))
axA.text(0.40, (ET + ET_IMPLIED) / 2, f"{ET - ET_IMPLIED:,.0f} mm/yr\nof extra ET",
         fontsize=9.5, color=INK, va="center", fontweight="bold")

axA.set_xticks(x)
axA.set_xticklabels(labels, fontsize=10, color=INK2)
axA.set_ylabel("Water depth  (mm/yr)", color=INK2, fontsize=9.5)
axA.set_ylim(0, P * 1.12)
axA.set_xlim(-0.6, 2.1)
axA.set_title("A · The same rainfall, split two ways",
              fontsize=11.5, color=INK, fontweight="bold", loc="left", pad=12)
axA.legend(frameon=False, fontsize=9, loc="lower left", bbox_to_anchor=(0, -0.22),
           ncol=2)

# B -- monthly climatology: when is the ET too high?
mon = pd.DataFrame({
    "P": (rain[sl] + snow[sl]).groupby((rain[sl].index.month)).mean() * 30.4,
    "ET": et[sl].groupby(et[sl].index.month).mean() * 30.4,
    "Qsim": qsim[sl].groupby(qsim[sl].index.month).mean() * 30.4,
    "Qobs": qobs[sl].dropna().groupby(qobs[sl].dropna().index.month).mean() * 30.4,
})
m = np.arange(1, 13)
axB.fill_between(m, 0, mon["P"], color=P_C, alpha=0.16, label="Precipitation")
axB.plot(m, mon["P"], color=P_C, lw=2)
axB.plot(m, mon["ET"], color=ET_C, lw=2.4, label="ET (model)")
axB.plot(m, mon["Qobs"], color=OBS_C, lw=2.4, label="Q observed")
axB.plot(m, mon["Qsim"], color=SIM_C, lw=2.4, ls="--", label="Q simulated")

axB.set_xticks(m)
axB.set_xticklabels(list("JFMAMJJASOND"), fontsize=9.5, color=INK2)
axB.set_ylabel("Monthly depth  (mm)", color=INK2, fontsize=9.5)
axB.set_title("B · ET keeps drawing all year, including the dry season",
              fontsize=11.5, color=INK, fontweight="bold", loc="left", pad=12)
axB.legend(frameon=False, fontsize=9, ncol=2)

for ax in (axA, axB):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=9)

fig.suptitle(
    f"Water balance, {EVAL_START:%b %Y}–{EVAL_END:%b %Y}   ·   "
    f"the model evaporates {ET / ET_IMPLIED - 1:+.0%} more than the balance allows",
    fontsize=13, color=INK, fontweight="bold", x=0.012, ha="left", y=0.995)
fig.tight_layout(rect=[0, 0, 1, 0.94])
fig.savefig(os.path.join(OUT, "fig4_water_balance.png"), dpi=170,
            facecolor=SURFACE)
print("\nwrote", os.path.join(OUT, "fig4_water_balance.png"))
