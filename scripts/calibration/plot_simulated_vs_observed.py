"""Compare final LISFLOOD discharge with the GRRR calibration reference.

Run with the lisflood environment's Python from the repository root.
"""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "Calibration/summary/discharge_comparison"
OUT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "Calibration/temp/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def read_series(path):
    frame = pd.read_csv(path)
    frame.index = pd.to_datetime(frame.pop("Timestamp"), format="%d/%m/%Y %H:%M")
    return frame.apply(pd.to_numeric, errors="coerce")


def main():
    observed = read_series(ROOT / "Calibration/observed_discharges.csv")
    pairs = {}
    for station in range(1, 6):
        simulated = read_series(ROOT / f"Calibration/catchments/{station}/out/streamflow_simulated_best.csv")
        pair = pd.concat([observed[str(station)].rename("Observed reference (GRRR)"),
                          simulated[str(station)].rename("Simulated (LISFLOOD)")], axis=1)
        pair = pair.loc["2004-01-01":"2020-12-31"].replace([1e31, -9999], float("nan"))
        if pair.dropna().empty:
            raise ValueError(f"No overlapping discharge values for station {station}")
        pair.to_csv(OUT / f"station_{station}_daily.csv", index_label="date")
        pairs[station] = pair

    for zoom in (False, True):
        fig, axes = plt.subplots(5, 1, figsize=(15, 15), sharex=True, layout="constrained")
        for ax, (station, daily) in zip(axes, pairs.items()):
            data = daily.loc["2018":"2020"] if zoom else daily.resample("MS").mean()
            ax.plot(data.index, data.iloc[:, 0], color="#2166ac", lw=1.1, label=data.columns[0])
            ax.plot(data.index, data.iloc[:, 1], color="#d66022", lw=1.0, alpha=.9, label=data.columns[1])
            if not zoom:
                ax.axvline(pd.Timestamp("2012-07-02"), color="0.4", ls="--", lw=.8,
                           label="Calibration / validation split")
            ax.set_title(f"Station {station}", loc="left", fontsize=11)
            ax.set_ylabel("Discharge (m³/s)")
            ax.set_ylim(bottom=0)
            ax.grid(alpha=.2)
            ax.spines[["top", "right"]].set_visible(False)
        axes[0].legend(loc="upper right", ncol=3, fontsize=9)
        axes[-1].set_xlabel("Date")
        period = "Daily discharge · 2018–2020" if zoom else "Monthly mean discharge · 2004–2020 · 2003 spin-up excluded"
        fig.suptitle("LISFLOOD simulated discharge vs observed reference\n" + period +
                     "\nReference: GRRR reanalysis used for calibration; each station has its own y-axis", fontsize=13)
        name = "daily_2018_2020" if zoom else "monthly_2004_2020"
        for ext in ("png", "pdf"):
            fig.savefig(OUT / f"{name}.{ext}", dpi=180)
        plt.close(fig)
    print(f"Saved comparisons and aligned daily CSVs to {OUT}")


if __name__ == "__main__":
    main()
