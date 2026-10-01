# Five-station preparation — basin 4061001260

Status: **inputs prepared; calibration and LISFLOOD simulations NOT started**.
Gauge placement remains provisional. Review [FOLLOW_UP.md](FOLLOW_UP.md) before running.

## Station IDs

| Calibration ObsID | Shortlist map label | Gauge | Direct upstream calibration stations |
|---|---|---|---|
| 1 | 4 | hybas_4120998630 | 2, 3, 4 |
| 2 | 1 | hybas_4120978780 | none |
| 3 | 2 | hybas_4120983650 | none |
| 4 | 3 | hybas_4120992110 | none |
| 5 | 5 | hybas_4121001150 | 1 |

The existing station retains ID 1. `CatchmentsToProcess.txt` orders the runs
4, 3, 2, 1, 5. Stations 2/3/4 are independent of each other on the prepared LDD.
The network comes from the current model grid, not a claim that gauge snapping is resolved.

## Prepared files

- `stations.csv`, `stations_data.csv`, `stations_links.csv`: metadata and dependencies.
- `gauge_placement_audit.csv`: original and model coordinates, actual displacement,
  catalogue area, cached accumulation and repaired-LDD accumulation.
- `grrr_selected_2003_2020.csv`: 6,575 daily values per gauge, in m³/s, by original ID.
- `observed_discharges.csv`: the same data formatted for liscal with columns ObsID 1–5.
  Despite the required filename, these are **modelled GRRR values, not observations**.
- `catchments/<ObsID>/maps/`: aligned static maps, meteorological forcing and LAI,
  clipped to each interstation region's bounding box; masks define active cells.
- `catchments/<ObsID>/station/`: 6,210 daily calibration/validation reference values
  after the existing station spin-up exclusion, and extracted station metadata.
- `catchments/<ObsID>/settings/*PREVIEW_ONLY.xml`: resolved previews, never executed.
- `preparation_manifest.json`, `validation_report.json`, `logs/prep*`: preparation evidence.

GRRR source: `gs://flood-forecasting/hydrologic_predictions/model_id_8583a5c2_v0/reanalysis/streamflow.zarr`.
Fetched using the unchanged catalogue gauge IDs; snapping does not change which
GRRR series is downloaded. Only station 1 has `qualityVerified=True` in the catalogue.

## Inflow is conditional

Only stations **1 and 5** require inflow with these interstation masks. Their upstream
areas are excluded from their local computation; discharge must arrive from the upstream
stations. Stations **2, 3 and 4 have inflow disabled**.

The shared template points to `inflow/inflow_cut.map` and `inflow/chanq.tss`.
These bindings are unused when `%inflowflag` resolves to 0. Inlet maps retain upstream
ObsIDs, matching the TSS headers; LISFLOOD converts IDs to column indices internally.
No placeholder TSS files were created. Actual `chanq.tss` files will be assembled by
liscal from upstream CAL_7 results at execution time. They cannot exist yet without
running the upstream simulations, which the user has explicitly deferred.

## Existing settings retained for review

- Forcing: 2003-01-01 through 2020-12-31, daily.
- Prerun: 2003-01-01 through 2008-12-31.
- Station data: 2004-01-01 through 2020-12-31; split: 2012-07-02.
- Calibration run begins 365 days before the split; objective uses the later period.
- DEAP: population 72, mu 18, lambda 36, generation range 6–16.
- Default workers remain 6; `NCPUS` can override the launcher after hardware review.

`run_full_calibration.sh` is prepared to execute CAL_6 then CAL_7 per station in
dependency order. It has **not been invoked**. Optional CAL_8 plots are deferred:
the external plotting code assumes legacy filenames and coordinate names and needs
the review recorded in FOLLOW_UP.md. The added diagnostic maps are not simulation inputs.

The input grids, complete forcing dates, representative finite values, station coverage,
local drainage connectivity, inlet IDs and XML previews passed file-level checks.
No numerical model execution was used to validate them.

## Preparation code and portability

`scripts/calibration/prepare_selected_station_locations.py` uses the existing
`make_outlets.py` rules and records the result without hiding coordinate changes.
CAL_1, CAL_2 and CAL_3 from the sibling `lisflood-calibration` prepare station filtering,
links and masks. Then `prepare_catchment_inputs.py` streams aligned NetCDF crops and
invokes CAL_5 for station extraction. `validate_prepared_calibration.py` checks files
and generates XML previews only. None of these preparation scripts calls CAL_6/7.

Before moving to Ubuntu, update absolute paths in `settings_calibration.txt` and
regenerate previews; activate a compatible LISFLOOD/PCRaster environment. The launcher
supports `LISCAL`, `LISFLOOD_PYTHON` and `NCPUS` overrides. The sibling calibration
checkout is also required. Existing parameter choices are awaiting review, not endorsed
by the preparation checks.

## Move this prepared run to Ubuntu

The quickest reliable route is a resumable `rsync` copy of this prepared workspace;
there is no need to regenerate the 2003–2020 forcing or repeat CAL_1–5. Copy the
repository tree but omit Git history, the old archive, raw satellite downloads and the
unclipped full-domain forcing. Each station already has its clipped forcing and maps.
The required bundle is about 0.9 GB on this machine, plus the LISFLOOD calibration code.

On the current machine, from the repository's parent directory, copy to the new host
(replace `user@ubuntu-host` and the destination):

```bash
rsync -a --progress --partial \
  --exclude='.git/' --exclude='.claude/' --exclude='archive/' \
  --include='inputs/raw/facc_snapped.tif' --exclude='inputs/raw/*' \
  --exclude='inputs/meteo/' --exclude='inputs/lai/' \
  LisFloodDataPrepScripts/ user@ubuntu-host:~/LisFlood/LisFloodDataPrepScripts/
```

The source files stay here; `rsync` can be rerun to resume or refresh the transfer.
Keep the sibling checkout layout on Ubuntu:

```text
~/LisFlood/LisFloodDataPrepScripts/
~/LisFlood/lisflood-calibration/
```

Clone the calibration repository at the exact source revision used here, then create a
compatible Linux environment. The local run used Python 3.10, NumPy 1.26.4, pandas 1.5.3,
PCRaster 4.4.2, liscal 1.1.0 (`141e1767d9b3d8b04a5d74804e1b71b2c4775b6c`), and the
LISFLOOD 4.3.1 model. Use the matching LISFLOOD source/install and verify the PCRaster
command line tools work inside the environment; the Mac environment itself cannot be
copied to Ubuntu.

```bash
cd ~/LisFlood
git clone https://github.com/ec-jrc/lisflood-calibration.git
git -C lisflood-calibration checkout 141e1767d9b3d8b04a5d74804e1b71b2c4775b6c
```

The LISFLOOD project's current Linux installation guide uses conda-forge PCRaster/GDAL
and the `lisflood-model` package. For this prepared calibration, keep the versions used
here compatible with liscal:

```bash
conda create -n lisflood -c conda-forge python=3.10 numpy=1.26.4 pandas=1.5.3 pcraster=4.4.2 gdal xarray netcdf4 scipy matplotlib deap
conda activate lisflood
python -m pip install lisflood-model==4.3.1
python -m pip install -e ~/LisFlood/lisflood-calibration
```

On Ubuntu, after installing the environment and cloning lisflood-calibration:

```bash
cd ~/LisFlood/LisFloodDataPrepScripts
conda activate lisflood
python Calibration/rebase_paths.py
bash -n Calibration/run_full_calibration.sh
python scripts/calibration/validate_prepared_calibration.py
```

The validator checks files and creates XML previews; it does not run LISFLOOD.

`rebase_paths.py` updates the absolute paths in `settings_calibration.txt` to the new
checkout and Python executable. The run script discovers the sibling lisflood-calibration
checkout and uses `CatchmentsToProcess.txt` order (4, 3, 2, 1, 5), so upstream stations
finish before dependent downstream stations. It runs CAL_6 and CAL_7; optional CAL_8
plots remain deferred. Before an actual run, review FOLLOW_UP.md, verify the discharge
source is suitable, and confirm station placement/settings. Starting calibration is a
separate step after those reviews.

The transfer deliberately excludes `.git`; it preserves the latest working files and
prepared data but does not carry commit history. The current branch has three local
commits ahead of GitHub because earlier commit history contains files GitHub rejects.
Use this workspace handover as the source of truth on the new machine; decide separately
whether the code should be pushed after generated data is excluded or moved to Git LFS.
