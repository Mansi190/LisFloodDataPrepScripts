# `pipeline/LisVap/` — evaporation forcing

**Step 9, three scripts.** LISFLOOD needs three evaporation fields it cannot compute itself:

| variable | file | meaning |
|---|---|---|
| `ETRef` | `et.nc` | reference evapotranspiration (the standard grass-crop rate) |
| `E0` | `e.nc` | potential evaporation from open water |
| `ES0` | `es.nc` | potential evaporation from bare soil |

These come from **LISVAP**, JRC's companion pre-processor, which implements Penman-Monteith.
Rather than reimplement it, the pipeline feeds LISVAP its inputs and runs the official Docker
image.

```
generate_lat_nc.py  ──► inputs/maps/lat.nc
                                  │
lisflood_meteo_lisvap_inputs.py ──┼──► inputs/meteo/{tn,tx,rg,ws,pd}.nc
                                  │
                                  ▼
                       run_lisvap.py (docker run jrce1/lisvap)
                                  │
                                  ▼
                       inputs/meteo/{et,e,es}.nc
```

All three add the parent directory to `sys.path` so they can import the shared modules from a
subdirectory:

```python
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline_config as _cfg
```

---

## 1. `generate_lat_nc.py` (58 lines)

Writes a raster where **each cell's value is its own latitude in degrees**.

```python
transformer = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
x_coords = [transform.c + (i+0.5)*transform.a for i in range(width)]
y_coords = [transform.f + (i+0.5)*transform.e for i in range(height)]
xs, ys = np.meshgrid(x_coords, y_coords)
lons, lats = transformer.transform(xs, ys)
```

**Why latitude is needed:** Penman-Monteith requires *extraterrestrial radiation* Ra — the
solar energy arriving at the top of the atmosphere — which is a function of latitude and day
of year. So every cell needs to know where it is.

Two things to notice:

- `np.meshgrid` turns two 1-D coordinate axes into two 2-D arrays, so `lons[i,j]`/`lats[i,j]`
  give the full coordinate pair of every cell.
- `Transformer.transform` is **vectorised** — it accepts whole arrays. Looping per pixel would
  be thousands of times slower.
- The stored `y`/`x` coordinates stay in **UTM metres** (matching every other file); only the
  *values* are degrees. Keeping the grid identical is what makes LISVAP line the file up with
  the forcing.

---

## 2. `lisflood_meteo_lisvap_inputs.py` (267 lines)

Downloads the five ERA5-Land variables Penman-Monteith needs. Structurally this is
[lisflood_meteo_forcing.py](lisflood_meteo_forcing.md) with five variables instead of two —
same chunked download, same retry, same streaming NetCDF assembly. Read that doc first; only
the physics differs.

| var | ERA5 band(s) | transform | unit |
|---|---|---|---|
| `tn` | `temperature_2m_min` | `− 273.15` | °C |
| `tx` | `temperature_2m_max` | `− 273.15` | °C |
| `rg` | `surface_solar_radiation_downwards_sum` | none | J/m² |
| `ws` | `u_component_of_wind_10m`, `v_component_of_wind_10m` | `√(u² + v²)` | m/s |
| `pd` | `dewpoint_temperature_2m` | Magnus formula | hPa |

Two derived quantities are worth reading closely.

### Wind speed from vector components

```python
u = img.select('u_component_of_wind_10m')
v = img.select('v_component_of_wind_10m')
ws = u.pow(2).add(v.pow(2)).sqrt()
```

ERA5 stores wind as east–west and north–south **components** (signed, so direction is
recoverable). Evaporation only cares about magnitude, so take the vector norm. Averaging `u`
and `v` separately and then taking the norm is *not* the same thing — always convert per
timestep, then aggregate.

### Vapour pressure from dew point — the Magnus formula

```python
td = img.select('dewpoint_temperature_2m').subtract(273.15)
pd = td.multiply(17.27).divide(td.add(237.3)).exp().multiply(6.11)
```

i.e. `e = 6.11 · exp(17.27·Td / (Td + 237.3))` in hPa/mbar.

The **dew point** is the temperature at which the air would saturate. Saturation vapour
pressure at the dew point *is* the actual vapour pressure of the air. Evaporation is driven by
the vapour-pressure deficit (saturation at air temperature minus actual), so LISVAP needs this
term. Constants 17.27 and 237.3 are the standard Tetens/Magnus coefficients over water.

Note `assemble_netcdf` here also writes a `units` attribute per variable — LISVAP reads it.

⚠️ The raw tiles go to `inputs/meteo/raw/`, not `inputs/raw/` like every other script.
`lisflood_meteo_lisvap_inputs.py.bak` preserves the pre-streaming version for reference.

---

## 3. `run_lisvap.py` (34 lines)

```python
settings = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_lisvap.xml")
settings_in_container = "/input/" + os.path.relpath(settings, _cfg.REPO_ROOT)
cmd = ["docker", "run", "--rm",
       "-v", f"{_cfg.REPO_ROOT}:/input",
       "jrce1/lisvap",
       settings_in_container]
subprocess.run(cmd)
```

**Why Docker.** LISVAP is a PCRaster/Fortran-era scientific model with a dependency stack that
is painful to install. The official image pins all of it. `subprocess` + a bind mount is the
whole integration.

**The path translation is the subtle part.** `-v HOST:CONTAINER` maps the repo root to `/input`
inside the container. The settings file's path must therefore be rewritten from a host path to
a container path — which is exactly what `os.path.relpath(settings, REPO_ROOT)` prefixed with
`/input/` does.

The inline comment records a real regression: the mount used to be derived from `__file__`'s
parent. When the 2026-07 reorg moved `LisVap/` down into `scripts/pipeline/`, the mount
silently repointed at `scripts/pipeline`, and the container could no longer see
`/input/inputs/maps`. **Anchor mounts to a known root, never to a script's own location.**

`--rm` deletes the container on exit. A non-zero exit code propagates via `sys.exit(r.returncode)`;
the usual cause is Docker not running.

`settings_lisvap.xml` (in this folder) is the LISVAP configuration: `CalendarDayStart`,
`StepStart`/`StepEnd` covering 2003-01-01…2024-12-31, `DtSec=86400`, and the paths to the five
inputs plus `lat.nc`. Its dates must match `pipeline_config.FORCING_START/END`.
