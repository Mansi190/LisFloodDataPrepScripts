# `pipeline/lisflood_lulc_cover.py`

**140 lines. Step 3. The simplest script in the pipeline** — a good one to read early, because
it shows the write-and-validate skeleton with none of the download complexity.

Outputs (in `inputs/maps/table2map/`): 10 maps × `.tif` + `.nc` —
`cropcoef_{forest,other}`, `crgrnum_{forest,other}`, `mannings_{forest,other}`,
`soildep1_{forest,other}`, `soildep2_{forest,other}`.

## What it does

Writes **spatially uniform** maps: one constant per land-cover class, painted across the
basin. No remote sensing, no GEE.

```python
def mask_surface(val, filter_mask, dtype=np.float32):
    arr = np.full((info.height, info.width), val, dtype=dtype)
    return np.where(filter_mask, arr, -9999).astype(dtype)
```

Fill with the constant, then stamp nodata outside the domain. The domain is the intersection
of two conditions:

```python
domain = (mask > 0) & (lulc_arr != _cfg.NODATA_INT)
```

Both the basin mask **and** valid land cover. A cell inside the basin where LULC is missing is
excluded — the fractions there would be meaningless.

## Why constants, and why that's fine

These are LISFLOOD's **vegetation and roughness lookup-table values**. In the reference
LISFLOOD setup they come from a class→value table joined onto the land-cover map
(hence the directory name `table2map`). Here the model already separates forest from other via
`fracforest`/`fracother`, so a per-class constant map is the equivalent formulation.

| map | forest | other | what it is |
|---|---|---|---|
| `cropcoef` | 1.15 | 1.10 | crop coefficient: multiplies reference ET to get this cover's potential ET |
| `crgrnum` | 3.5 | 1 | crop group number: how readily the plant closes stomata under water stress |
| `mannings` | 0.3 | 0.1 | Manning's n for **overland** flow — forest litter resists sheet flow far more than bare ground |
| `soildep1` | 600 mm | 600 mm | rooting-zone soil depth |
| `soildep2` | 1400 mm | 1400 mm | sub-rooting-zone depth |

Note the module-level constants `CRGRNUM_F = 3.5`, `CRGRNUM_O = 4.5`, `MANNINGS = 0.05` are
**dead** — the dict inlines its own values (`crgrnum_other` is written as `1`, not `4.5`).
Worth deleting so the file has one truth.

## The soil-depth cross-reference — the point of the script

```python
"soildep1_forest": mask_surface(_cfg.SOIL_DEPTH_L1_MM, domain),   # 600
"soildep2_forest": mask_surface(_cfg.SOIL_DEPTH_L2_MM, domain),   # 1400
```

Those are **not** literals — they come from `pipeline_config`, computed as
`sum(SOIL_DEPTHS_L1_WEIGHTS) * 10`, i.e. from the exact SoilGrids bands that
`lisflood_soil_preprocessing.py` averaged its hydraulic properties over.

Why it matters: LISFLOOD computes soil storage capacity as `w_s = ThetaSat × depth`. If this
depth said 1000 mm while ThetaSat was averaged over 0–60 cm, the model would compute storage
over a metre of soil using porosity measured over 60 cm. That's a silent physical
inconsistency — no crash, just wrong water. Forest and other share the same depth because
there is only one property-averaging depth structure.

## Type handling

```python
"crgrnum_forest": mask_surface(3.5, domain, np.int16),
...
dtype_str = "float32" if arr.dtype == np.float32 else "int16"
```

`crgrnum` is declared `int16`, so `3.5` truncates to `3` on write. If the fractional value is
intended, the dtype should be `float32`.

## Skeleton to reuse

```
generate_parameters()   → build arrays, save_aligned(...like=AREA_TIF), gdal_convert_netcdf
validate_alignment()    → assert every output matches area.tif, else sys.exit(1)
main()                  → banner, call both
```

This is the shape of every write path in the repo; the bigger scripts are this plus data
acquisition.
