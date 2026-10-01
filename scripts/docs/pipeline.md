# `pipeline/pipeline.py`

**450 lines. The orchestrator.** It runs no science of its own — it sequences the other
scripts, checks dependencies, verifies outputs, and generates a LISFLOOD settings XML.

## The model: a hardcoded DAG

Three data structures do all the work.

### 1. `STEPS` — the ordered list

```python
STEPS = [
    ("topo",       "topographyMapsScript.py"),
    ("lulc",       "lisflood_frac_lulc_preprocessing.py"),
    ("lulc_cover", "lisflood_lulc_cover.py"),
    ("soil",       "lisflood_soil_preprocessing.py"),
    ("chan",       "channnels.py"),
    ("outlets",    "make_outlets.py"),
    ("meteo",      "lisflood_meteo_forcing.py"),
    ("lai",        "lisflood_lai_forcing.py"),
    ("lisvap_lat", "LisVap/generate_lat_nc.py"),
    ("lisvap_in",  "LisVap/lisflood_meteo_lisvap_inputs.py"),
    ("lisvap_run", "LisVap/run_lisvap.py"),
]
```

### 2. `EXPECTED_OUTPUTS` — the contract

A dict `step → [file paths]`. This is what turns "the script exited 0" into "the step actually
produced what it promised":

```python
"soil": _nc(cfg.DIR_SOILHYD, "thetas1_forest", "thetas1_other", "thetas2", ... )  # 15 files
```

### 3. `STEP_DEPS` — the edges

```python
STEP_DEPS = {
    "topo":    [],
    "lulc":    ["topo"],
    "soil":    ["topo", "lulc"],
    "chan":    ["topo"],
    "outlets": ["topo", "chan"],
    "meteo":   ["topo"],
    "lai":     ["topo", "lulc"],
}
```

Note this is a dependency on **outputs, not on execution**. `check_deps` doesn't ask "did topo
run?", it asks "do topo's files exist?" — which is what actually matters, and what makes
resuming after a crash work.

## `run_step()` — the execution unit

```python
missing_deps = check_deps(step_name)
if missing_deps: print them; return False          # 1. pre-check

result = subprocess.run([sys.executable, script])  # 2. run
if result.returncode != 0: return False

present, missing = check_outputs(step_name)        # 3. post-check
return len(missing) == 0
```

Each step runs as a **separate process** (`subprocess.run`), not an imported function. That's
deliberate: these scripts are memory-hungry, and a subprocess returns all its RAM to the OS on
exit. It also means a segfault or OOM-kill in one step can't take down the runner — you get an
exit code instead. `sys.executable` (not `"python"`) guarantees the same interpreter/venv.

The main loop **stops on first failure** rather than continuing:

```python
for step_name, script_file in STEPS:
    if not run_step(step_name, script_file):
        failed.append(step_name); break
```

Correct here, because everything downstream depends on `area.tif`. Continuing past a topo
failure would just produce nine more confusing failures.

## The four CLI modes

```bash
python pipeline.py --check          # status report only, runs nothing
python pipeline.py --ini-only       # regenerate the settings XML only
python pipeline.py --step topo      # one step (+ regenerate the XML)
python pipeline.py                  # everything
```

`--check` calls `print_status()`, which walks every step and prints `✔` (complete),
`⚠` (partial), or `✘` (nothing), plus the missing paths. **This is the first thing to run when
something looks wrong.**

## `generate_ini()`

Writes `settings/lisflood_settings.xml` by f-string-interpolating the config's directory
constants into a large XML template. It maps every generated file to its LISFLOOD variable
name — this is the clearest place to see *what the whole pipeline is for*:

```xml
<textvar name="MaskMap"   value="{topo}/area.nc"/>
<textvar name="Ldd"       value="{topo}/ldd.nc"/>
<textvar name="ThetaSat1a" value="{soil}/thetas1_forest.nc"/>
<textvar name="PrecipitationMaps" value="{meteo}/pr.nc"/>
```

The `a`/`b` suffixes in LISFLOOD names are the forest/other split: `ThetaSat1a` = layer 1
forest, `ThetaSat1b` = layer 1 other.

Calibration parameters (`b_Xinanjiang`, `UpperZoneTimeConstant`, `GwLoss`, …) are written with
placeholder defaults. **These cannot be derived from remote sensing** — they're fitted by
optimising against observed discharge. That's what `Calibration/` and `scripts/calibration/`
are for.

## Caveats

- `EXPECTED_OUTPUTS["lai"]` expects `lai_forest.nc`/`lai_other.nc`; the LAI script writes
  `laif.nc`/`laio.nc`. The step succeeds and is then reported as missing its outputs.
- `STEP_DEPS` has no entry for `lulc_cover`, `lisvap_lat`, `lisvap_in`, `lisvap_run` —
  `.get(step, [])` returns `[]`, so those steps skip the dependency check entirely.
- `generate_ini()` counts `[FILL IN]`/`[CALIBRATE]` markers that the template no longer
  contains, so it always reports 0 items needing attention.
- The generated XML is a **template/reference**. The runs actually use the hand-maintained
  `settings/prerun.xml`, `cold_start.xml`, `warm_start.xml`.
