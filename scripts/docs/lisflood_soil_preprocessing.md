# `pipeline/lisflood_soil_preprocessing.py`

**315 lines. Step 4.** Turns SoilGrids texture/density measurements into the 15 soil hydraulic
parameter maps LISFLOOD needs, using **pedotransfer functions (PTFs)**.

Outputs (in `inputs/maps/soilhyd/`): `{thetas,thetar,alpha,lambda,ksat}` × `{1_forest, 1_other, 2}`.

---

## The core idea: a pedotransfer function

Nobody measures soil hydraulic conductivity over a whole basin — it takes a lab and a soil
core. But texture (% clay, % silt), bulk density, and organic carbon *are* mapped globally.

A **PTF is a regression that predicts the hard-to-measure property from the easy-to-measure
ones.** In CS terms: a small, fitted, closed-form model — features `(clay, silt, bd, soc)`,
targets the 5 hydraulic parameters. This script is the inference step.

The functions here follow HiHydroSoil V2.0 / Tóth et al. (2015).

---

## STEP 1 — `fetch_soilgrids_layer()`: thickness-weighted depth averaging

SoilGrids ships as separate bands per depth interval. LISFLOOD wants two layers, so the bands
must be collapsed:

```python
img = ee.Image(f"projects/soilgrids-isric/{var_name}_mean")
weighted = Σ  img.select(f"{var}_{depth}_mean").multiply(w)
composite = weighted.divide(sum(weights))
```

**Why not `ee.Reducer.mean()`?** Layer 1 is `["0-5cm","5-15cm","15-30cm","30-60cm"]`. A plain
mean gives the 5 cm band the same influence as the 30 cm band. Weighting by thickness
(`[5,10,15,30]`) gives a true depth-integrated average. The weights live in `pipeline_config`
so the same numbers define `SOIL_DEPTH_L1_MM = 600` — see
[lisflood_lulc_cover.md](lisflood_lulc_cover.md) for why that coupling matters.

Eight downloads total: `clay`, `silt`, `bdod`, `soc` × 2 layers. Each is cached by filename in
`inputs/raw/`, so a re-run is free.

`GEE_SCALE = 60` — extracted at 60 m even though SoilGrids is natively 250 m and the target is
300 m. Over-sampling gives the local bilinear reprojection more to work with at basin edges.

The region-construction comment records a real bug that was fixed: an earlier version built
the request rectangle by transforming two UTM corners into lat/lon. **A UTM box is not a
lat/lon box** — its north and south edges bow, so the corners fell outside the requested
rectangle and the data was silently short. The fix is to build the rectangle in `info.crs`
with a metre buffer, exactly as every other GEE script does.

---

## STEP 2 — unit conversion

SoilGrids stores integers to save space; you must divide by the documented scale factor:

```python
clay1 = align_and_scale(clay1_path, scale_factor=10.0)    # cg/kg → g/kg  (≈ %)
silt1 = align_and_scale(silt1_path, scale_factor=10.0)    # cg/kg → g/kg  (≈ %)
bd1   = align_and_scale(bd1_path,   scale_factor=100.0)   # cg/cm³ → g/cm³
soc1  = align_and_scale(soc1_path,  scale_factor=10.0)    # dg/kg → g/kg
```

Getting a scale factor wrong here is a 10× or 100× error that propagates through an exponential
in `make_ksat` and silently ruins the run. This is the kind of thing to check first when
results look strange.

`align_and_scale` also does nodata → NaN, `reproject_to_grid(bilinear)`, `snap_to_grid`.

---

## STEP 3 — the five PTFs

```python
def make_thetas(clay, silt, oc, bd):     # saturated water content (porosity), cm³/cm³
    v = 0.7919 + clay*0.001691 - bd*0.29619 + oc*0.000182
    return np.clip(v, 0.05, 0.95)

def make_thetar(clay, silt, oc):         # residual water content — water the soil won't release
    v = 0.015 + clay*0.0003 + silt*0.0001 + oc*0.001
    return np.clip(v, 0.001, 0.25)

def make_alpha(clay, silt):              # van Genuchten α (1/cm) — inverse air-entry suction
    v = np.exp(-1.97 + clay*0.0068 - silt*0.0030)
    return np.clip(v, 0.001, 10.0)

def make_lambda(clay, silt):             # van Genuchten λ = n−1 — pore-size distribution
    v = 0.1 + clay*0.005 - silt*0.001
    return np.clip(v, 0.001, 3.0)

def make_ksat(clay, silt, bd):           # saturated conductivity, mm/day
    v = 10.0 ** (1.2 - clay*0.01 - bd*0.5 + silt*0.001) * 10.0
    return np.clip(v, 0.01, 10000.0)
```

Read the signs — they encode the physics, and they're your sanity check:

- `thetas` **decreases** with bulk density. Denser soil = less pore space. Correct.
- `ksat` **decreases** with both clay and bulk density. Clay blocks flow; compaction blocks
  flow. Correct.
- `ksat` is `10^(...)`, so conductivity varies over orders of magnitude — which is why unit
  errors in the inputs are so destructive here.
- The trailing `* 10.0` in `make_ksat` converts cm/day → **mm/day**, LISFLOOD's unit
  (manual Table A12.1). A missing ×10 would make every soil ten times less permeable.

**Every PTF ends in `np.clip`.** These are regressions extrapolating outside their fitting
range at extreme pixels; the clips are physical bounds that keep a bad pixel from producing a
NaN or an infinity that poisons the whole run.

### `_check_physical()` — enforcing a cross-parameter invariant

```python
violated = (thetar >= thetas)
thetar = np.where(violated, thetas * 0.1, thetar)
```

Residual water content must be **less** than saturated water content — it's the water left
after drainage, so it's a subset. Two independent regressions have no idea about each other,
so they can cross. If they did, LISFLOOD would compute negative available water. The fix
(`thetar = 0.1 × thetas`) is a heuristic; the count is logged as a WARN so you can see whether
it's 3 pixels or 30,000.

The comments record that this file has been corrected: `thetar` and `alpha` originally used
clay-only forms which gave unrealistically low residual water (~0.01–0.02) and no
silt-dependence in air-entry suction.

---

## STEP 4 — masking and export

```python
domain = (area_mask > 0) & (lulc_arr != NODATA_INT)

def mask_surface(arr, filter_mask):
    masked = np.where(filter_mask, arr, NODATA_FLOAT)
    if any NaN inside the domain:
        masked[NaN & filter_mask] = np.nanmean(masked[filter_mask])   # gap-fill with basin mean
    return masked
```

The gap-fill is the notable part: NaN holes **inside** the basin (SoilGrids gaps, or edge
pixels the reprojection couldn't reach) are filled with the basin mean rather than left as
nodata. LISFLOOD cannot run on a domain with holes — a nodata cell in the middle of the
network breaks the water balance. Filling with the mean is a defensible default; the
alternative would be a spatial interpolation.

## `_forest` and `_other` currently hold identical data

```python
"thetas1_forest": mask_surface(props["thetas1"], domain),
"thetas1_other":  mask_surface(props["thetas1"], domain),
```

Both come from the same array. The docstring says layer 1 is "cleanly split into _forest and
_other using the LULC mask", and `CLASS_TREES = 6` is defined — but no split is applied. The
files exist because LISFLOOD's settings XML requires both `ThetaSat1a` and `ThetaSat1b`.

Whether they *should* differ is a real modelling question: soil under forest genuinely tends to
have higher organic carbon and lower bulk density. But SoilGrids doesn't resolve that at 250 m,
so writing the same values is the honest choice. Worth documenting as a limitation rather than
"fixing" by inventing a multiplier.

## Guards in `main()`

```python
if not exists(AREA_TIF) or not exists(LULC_ALIGNED): "Run Topo and LULC first"; exit(1)
if lulc_arr.shape != area_mask.shape:                "Re-run the LULC script";  exit(1)
```

The second is the sharper one: a shape mismatch means `area.tif` was regenerated (new ROI or
resolution) without regenerating `lulc.tif`. Catching that at startup beats producing 15
silently misaligned maps.
