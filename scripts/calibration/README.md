# `scripts/calibration/`

Model-evaluation helpers — "is the model right?", as opposed to `pipeline/`'s "what does the
model need?".

| script | what it does |
|---|---|
| `gauges_in_roi.py` | point-in-polygon subset of a gauge CSV to your ROI (local geopandas, instant) |
| `plot_grrr_discharge.py` | small-multiples plot of GRRR reanalysis discharge series |

Full walkthrough: [`../docs/calibration.md`](../docs/calibration.md).
Repo overview: [`../README.md`](../README.md).
