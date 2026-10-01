"""Pull GRRR reanalysis discharge for a set of gauges into one wide CSV.

GRRR = Google's Runoff Reanalysis & Reforecast: daily river discharge, 1980-2023, for
~1.03M global HydroBASINS reaches. **Model output, not gauge observations** -- a series
here is what Google's model produced for that reach, so anything calibrated against it is
being fitted to another model.

Gauge ids are HydroBASINS ids (`hybas_<HYBAS_ID>`), which is exactly what the Flood Hub
gauge CSVs carry, so no snapping or matching is needed.

    python scripts/calibration/grrr_discharge_for_gauges.py \
        --gauges shapefiles/floodhub_high_conf_noncwc_gauges_india_in_<roi>.csv \
        --out shapefiles/grrr_discharge_<roi>.csv

Plot the result with plot_grrr_discharge.py.

DEPENDENCIES: the zarr store needs `zarr<3`, `gcsfs` and `numcodecs`, which neither the
base conda env nor the `lisflood` env has. Make a venv for it:

    python -m venv grrr-venv && grrr-venv/bin/pip install "zarr<3" gcsfs numcodecs xarray pandas

Importable as a function too -- make_inflow.py calls `fetch_discharge()` directly.
"""
import argparse
import sys

import numpy as np
import pandas as pd

STORE = ("gs://flood-forecasting/hydrologic_predictions/model_id_8583a5c2_v0"
         "/reanalysis/streamflow.zarr")
DEPS = ("zarr", "gcsfs", "numcodecs")


def _require_deps():
    from importlib.util import find_spec
    missing = [m for m in DEPS if find_spec(m) is None]
    if missing:
        sys.exit(f"Reading the GRRR zarr store needs {', '.join(missing)}, which this "
                 "interpreter lacks. See the module docstring for the venv recipe, or pass "
                 "an already-fetched CSV to whatever called this.")


def fetch_discharge(gauge_ids, start=None, end=None):
    """Daily discharge [m3/s] for `gauge_ids` -> DataFrame indexed by date.

    Every id must resolve to exactly one reach; a missing one is an error rather than a
    silently absent column, because a dropped inflow point would quietly remove water from
    the model. Raises if the requested window has gaps for the same reason.
    """
    _require_deps()
    import xarray as xr

    ds = xr.open_zarr(STORE, storage_options={"token": "anon"}, consolidated=True)
    ids = ds["gauge_id"].values.astype(str)
    cols = {}
    for g in gauge_ids:
        hit = np.flatnonzero(ids == g)
        if len(hit) != 1:
            raise SystemExit(f"{g}: {'not found' if not len(hit) else 'ambiguous'} in GRRR")
        cols[g] = ds["streamflow"].isel(gauge_id=int(hit[0])).values

    df = pd.DataFrame(cols, index=pd.to_datetime(ds["time"].values))
    df.index.name = "date"

    if start or end:
        want = pd.date_range(start or df.index[0], end or df.index[-1], freq="D")
        df = df.reindex(want)
        df.index.name = "date"
        if df.isna().any().any():
            raise SystemExit(f"{int(df.isna().sum().sum())} missing daily value(s) over "
                             f"{start}..{end} — refusing to pad silently")
    return df


def parse_args():
    p = argparse.ArgumentParser(description="Fetch GRRR discharge for a set of gauges.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--gauges", help="CSV with a gauge_id column")
    src.add_argument("--ids", help="comma-separated gauge ids (hybas_...)")
    p.add_argument("--out", required=True, help="output wide CSV")
    p.add_argument("--start", default=None, help="YYYY-MM-DD")
    p.add_argument("--end", default=None, help="YYYY-MM-DD")
    return p.parse_args()


def main():
    args = parse_args()
    if args.gauges:
        ids = list(pd.read_csv(args.gauges, dtype={"gauge_id": str})["gauge_id"])
    else:
        ids = [s.strip() for s in args.ids.split(",")]
    print(f"fetching {len(ids)} reach(es) from GRRR...")

    df = fetch_discharge(ids, args.start, args.end)
    df.to_csv(args.out, float_format="%.4f")
    print(f"wrote {args.out}  ({len(df):,} daily steps x {len(df.columns)} reach(es))")
    for c in df.columns:
        s = df[c]
        print(f"  {c:20} mean={s.mean():8.2f}  peak={s.max():9.2f} on {s.idxmax():%Y-%m-%d}")


if __name__ == "__main__":
    main()
