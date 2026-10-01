# `scripts/pipeline/`

The 12 scripts that build every LISFLOOD input from public remote-sensing data.

**Read [`../README.md`](../README.md) first** — it explains the master-grid convention, the
Earth Engine constraints, and the libraries. Then use the walkthroughs below.

## Run order

```bash
python pipeline.py --check     # status: what exists, what's missing
python pipeline.py             # run everything in dependency order
python pipeline.py --step topo # one step
```

| # | script | produces | doc |
|---|---|---|---|
| — | `pipeline_config.py` | *(settings, no output)* | [doc](../docs/pipeline_config.md) |
| — | `lisflood_utils.py` | *(shared helpers)* | [doc](../docs/lisflood_utils.md) |
| — | `pipeline.py` | orchestration + `settings/lisflood_settings.xml` | [doc](../docs/pipeline.md) |
| 1 | `topographyMapsScript.py` | **`area.tif`** ★, dem, ldd, gradient, elvstd, chanleng | [doc](../docs/topographyMapsScript.md) |
| 2 | `lisflood_frac_lulc_preprocessing.py` | lulc, fracsealed/water/forest/other | [doc](../docs/lisflood_frac_lulc_preprocessing.md) |
| 3 | `lisflood_lulc_cover.py` | cropcoef, crgrnum, mannings, soildep1/2 | [doc](../docs/lisflood_lulc_cover.md) |
| 4 | `lisflood_soil_preprocessing.py` | 15 soil hydraulic maps | [doc](../docs/lisflood_soil_preprocessing.md) |
| 5 | `channnels.py` | chan, changrad, chanman, chanleng, chanbw, chans, chanbnkf | [doc](../docs/channnels.md) |
| 6 | `make_outlets.py` | outlets.nc (gauge pixels) | [doc](../docs/make_outlets.md) |
| 7 | `lisflood_meteo_forcing.py` | pr.nc, ta.nc | [doc](../docs/lisflood_meteo_forcing.md) |
| 8 | `lisflood_lai_forcing.py` | laif.nc, laio.nc | [doc](../docs/lisflood_lai_forcing.md) |
| 9 | `LisVap/*` | lat.nc, tn/tx/rg/ws/pd.nc → et/e/es.nc | [doc](../docs/LisVap.md) |

## Choosing a study area (run once, before step 1)

| script | doc |
|---|---|
| `get_hydrobasins_roi.py` — HydroBASINS, any level, optional full upstream catchment | [doc](../docs/get_hydrobasins_roi.md) |
| `get_watershed.py` — 20-line CoRE Stack version | [doc](../docs/get_watershed.md) |

## The one rule

Every raster output goes through `save_aligned(..., like=AREA_TIF)`. That is what keeps ~50
files on one identical grid. If you add a script, use it.
