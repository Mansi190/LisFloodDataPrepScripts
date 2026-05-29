"""
pipeline.py — Master runner for the LISFLOOD data-prep pipeline.

USAGE
-----
    python pipeline.py              # run all steps
    python pipeline.py --check      # check which outputs exist without running
    python pipeline.py --ini-only   # (re-)generate the LISFLOOD .ini template only
    python pipeline.py --step topo  # run one specific step

STEPS (in dependency order)
----------------------------
    topo    topographyMapsScript.py      → area, dem, ldd, gradient, elvstd
    lulc    lisflood_frac_lulc_preprocessing  → frac*, cropcoef, crgrnum, mannings, soildep
    soil    lisflood_soil_preprocessing  → thetas, thetar, alpha, lambda, ksat
    chan    channnels.py                 → chan, changrad, chanman, chanleng, chanbw,
                                           chans, chanbnkf
    meteo   lisflood_meteo_penman.py     → pr, ta, et, e, es  (NetCDF)
    lai     LAI_Landsat.py               → lai_forest/other × 12 months
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

import pipeline_config as cfg

# ─────────────────────────────────────────────────────────────────────────────
#  STEP DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

STEPS = [
    ("topo",       "topographyMapsScript.py"),
    ("lulc",       "lisflood_frac_lulc_preprocessing.py"),
    ("lulc_cover", "lisflood_lulc_cover.py"),
    ("soil",       "lisflood_soil_preprocessing.py"),
    ("chan",       "channnels.py"),
    ("gauges",     "lisflood_gauges_sites.py"),
    ("meteo",      "lisflood_meteo_forcing.py"),
    ("lai",        "lisflood_lai_forcing.py"),
]

# ─────────────────────────────────────────────────────────────────────────────
#  EXPECTED OUTPUT CHECKLIST
#  Every entry must exist before LISFLOOD can run.
# ─────────────────────────────────────────────────────────────────────────────

def _maps(base, *names):
    return [os.path.join(base, "maps", f"{n}.map") for n in names]

def _nc(base, *names):
    return [os.path.join(base, f"{n}.nc") for n in names]

EXPECTED_OUTPUTS = {
    "topo": _nc(cfg.OUTPUT_TOPO + "/maps", "area", "dem", "ldd", "gradient", "elvstd"),
    "lulc": _nc(cfg.OUTPUT_LULC + "/maps", "fracwater", "fracsealed", "fracforest", "fracother"),
    "lulc_cover": _nc(cfg.OUTPUT_LULC_COVER + "/maps",
                      "cropcoef_forest", "cropcoef_other",
                      "crgrnum_forest",  "crgrnum_other",
                      "mannings_forest", "mannings_other",
                      "soildep1_forest", "soildep1_other",
                      "soildep2_forest", "soildep2_other"),
    "soil": _nc(cfg.OUTPUT_SOIL + "/maps",
                  "thetas1_forest", "thetas1_other", "thetas2",
                  "thetar1_forest", "thetar1_other", "thetar2",
                  "alpha1_forest",  "alpha1_other",  "alpha2",
                  "lambda1_forest", "lambda1_other", "lambda2",
                  "ksat1_forest",   "ksat1_other",   "ksat2"),
    "chan": _nc(cfg.OUTPUT_CHANNELS + "/maps",
                 "chan", "changrad", "chanman", "chanleng",
                 "chanbw", "chans", "chanbnkf"),
    "gauges": _maps(cfg.OUTPUT_GAUGES, "gauges", "sites"),
    "meteo": _nc(cfg.OUTPUT_METEO + "/maps", "pr", "ta"),
    "lai": _nc(cfg.OUTPUT_LAI + "/maps", "lai_forest", "lai_other"),
}

# ─────────────────────────────────────────────────────────────────────────────
#  DEPENDENCY CHECK
# ─────────────────────────────────────────────────────────────────────────────

STEP_DEPS = {
    "topo":   [],
    "lulc":   ["topo"],
    "soil":   ["topo", "lulc"],
    "chan":   ["topo"],
    "gauges": ["topo", "chan"],
    "meteo":  ["topo"],
    "lai":    ["topo", "lulc"],
}


def check_deps(step_name):
    """Return list of dependency outputs that are missing."""
    missing = []
    for dep in STEP_DEPS.get(step_name, []):
        for path in EXPECTED_OUTPUTS.get(dep, []):
            if not os.path.exists(path):
                missing.append(path)
    return missing


def check_outputs(step_name):
    """Return (present, missing) lists for a step's expected outputs."""
    present, missing = [], []
    for path in EXPECTED_OUTPUTS.get(step_name, []):
        (present if os.path.exists(path) else missing).append(path)
    return present, missing


# ─────────────────────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_step(step_name, script_file, dry_run=False):
    script = os.path.join(os.path.dirname(__file__), script_file)
    if not os.path.exists(script):
        print(f"  ✘  Script not found: {script}")
        return False

    missing_deps = check_deps(step_name)
    if missing_deps:
        print(f"  ✘  {step_name}: missing dependency outputs:")
        for p in missing_deps:
            print(f"       {p}")
        return False

    if dry_run:
        print(f"  (dry-run) would run: {script_file}")
        return True

    print(f"\n{'═'*62}")
    print(f"  ▶  Running step: {step_name}  ({script_file})")
    print(f"{'═'*62}\n")

    result = subprocess.run([sys.executable, script], check=False)
    if result.returncode != 0:
        print(f"\n  ✘  {step_name} FAILED (exit code {result.returncode})")
        return False

    # Post-step output check
    present, missing = check_outputs(step_name)
    if missing:
        print(f"\n  ⚠  {step_name} completed but {len(missing)} output(s) missing:")
        for p in missing:
            print(f"       {p}")
    else:
        print(f"\n  ✔  {step_name}: all {len(present)} outputs verified")
    return len(missing) == 0


# ─────────────────────────────────────────────────────────────────────────────
#  OUTPUT STATUS REPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_status():
    crs = cfg.TARGET_CRS or "Auto-detected"
    print(f"\n{'═'*62}")
    print(f"  LISFLOOD Pipeline Status")
    print(f"  ROI  : {cfg.ROI_SHAPEFILE}")
    print(f"  CRS  : {crs}  |  {cfg.RESOLUTION_M}m")
    print(f"{'═'*62}")

    total_present = total_missing = 0
    for step_name, _ in STEPS:
        present, missing = check_outputs(step_name)
        status = "✔" if not missing else ("⚠" if present else "✘")
        label  = (f"{len(present)}/{len(present)+len(missing)} outputs"
                  if present or missing else "no outputs tracked")
        print(f"  {status}  {step_name:<6}  {label}")
        for p in missing:
            print(f"           missing: {os.path.relpath(p)}")
        total_present += len(present)
        total_missing += len(missing)

    print(f"\n  Total: {total_present} present, {total_missing} missing")
    print(f"{'═'*62}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  LISFLOOD .INI TEMPLATE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_ini():
    """
    Writes a LISFLOOD settings XML template populated with all generated map paths.
    Calibration parameters are left as TODO placeholders — they require a spin-up
    run or field measurements and cannot be auto-generated from remote-sensing data.

    Missing inputs that must be filled in manually:
      - Simulation period (CalendarDayStart, StepStart, StepEnd)
      - Initial conditions (initLisflood / warm-start state files)
      - Groundwater parameters (GwLoss, UpperZoneTimeConstant, etc.)
      - Reservoir / lake data (if applicable)
      - Calibration multipliers (SnowMeltCoef, b_Xinanjiang, etc.)
    """
    topo   = cfg.OUTPUT_TOPO + "/maps"
    lulc   = cfg.OUTPUT_LULC + "/maps"
    lulc_c = cfg.OUTPUT_LULC_COVER + "/maps"
    soil   = cfg.OUTPUT_SOIL + "/maps"
    chan   = cfg.OUTPUT_CHANNELS + "/maps"
    gauges = cfg.OUTPUT_GAUGES + "/maps"
    meteo  = cfg.OUTPUT_METEO + "/maps"
    lai    = cfg.OUTPUT_LAI + "/maps"
    crs   = cfg.TARGET_CRS or "Auto-detected"

    ini_path = "./lisflood_settings.xml"

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  LISFLOOD Settings — auto-generated by pipeline.py
  ROI : {cfg.ROI_SHAPEFILE}
  CRS : {crs}  |  {cfg.RESOLUTION_M} m

  TODO items are marked with [FILL IN] and must be set before running LISFLOOD.
  Calibration parameters marked [CALIBRATE] require a spin-up or parameter
  estimation procedure (e.g. PEST, DREAM, manual tuning against discharge gauges).
-->

<lfsettings>

  <!-- ══════════════════════════════════════════════════════════════
       SIMULATION CONTROL
  ═══════════════════════════════════════════════════════════════ -->
  <lfuser>

    <textvar name="CalendarDayStart" value="[FILL IN: e.g. 01/01/2024 06:00]"/>
    <textvar name="StepStart"        value="[FILL IN: e.g. 1]"/>
    <textvar name="StepEnd"          value="[FILL IN: e.g. 365]"/>
    <textvar name="DtSec"            value="86400"/>   <!-- daily timestep -->
    <textvar name="DtSecChannel"     value="3600"/>    <!-- sub-daily channel routing -->

    <textvar name="PathRoot"         value="."/>
    <textvar name="PathMaps"         value="{topo}"/>
    <textvar name="PathMeteo"        value="{meteo}"/>
    <textvar name="PathOut"          value="./lisflood_run_output"/>

    <!-- ── Model switches ──────────────────────────────────────────── -->
    <textvar name="GroundwaterSmooth"        value="1"/>
    <textvar name="TransientGroundwaterFlow" value="1"/>
    <textvar name="SplitRouting"             value="1"/>
    <textvar name="ReportSteps"              value="1..365"/>

  </lfuser>

  <!-- ══════════════════════════════════════════════════════════════
       SPATIAL MAPS — TOPOGRAPHY
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="MaskMap"    value="{topo}/area.nc"/>
  <textvar name="Dem"        value="{topo}/dem.nc"/>
  <textvar name="Ldd"        value="{topo}/ldd.nc"/>
  <textvar name="Grad"       value="{topo}/gradient.nc"/>
  <textvar name="ElevStdev"  value="{topo}/elvstd.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       SPATIAL MAPS — LAND USE / LAND COVER
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="FracWater"  value="{lulc}/fracwater.nc"/>
  <textvar name="FracSealed" value="{lulc}/fracsealed.nc"/>
  <textvar name="FracForest" value="{lulc}/fracforest.nc"/>
  <textvar name="FracOther"  value="{lulc}/fracother.nc"/>

  <textvar name="CropCoefForest"  value="{lulc_c}/cropcoef_forest.nc"/>
  <textvar name="CropCoefOther"   value="{lulc_c}/cropcoef_other.nc"/>
  <textvar name="CropGroupForest" value="{lulc_c}/crgrnum_forest.nc"/>
  <textvar name="CropGroupOther"  value="{lulc_c}/crgrnum_other.nc"/>

  <textvar name="Manning"        value="{lulc_c}/mannings_forest.nc"/>  <!-- forest -->
  <textvar name="ManningSoil"    value="{lulc_c}/mannings_other.nc"/>   <!-- other  -->

  <textvar name="SoilDepth1a"    value="{lulc_c}/soildep1_forest.nc"/>
  <textvar name="SoilDepth1b"    value="{lulc_c}/soildep1_other.nc"/>
  <textvar name="SoilDepth2a"    value="{lulc_c}/soildep2_forest.nc"/>
  <textvar name="SoilDepth2b"    value="{lulc_c}/soildep2_other.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       SPATIAL MAPS — SOIL HYDRAULIC PROPERTIES
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="ThetaSat1a"  value="{soil}/thetas1_forest.nc"/>
  <textvar name="ThetaSat1b"  value="{soil}/thetas1_other.nc"/>
  <textvar name="ThetaSat2"   value="{soil}/thetas2.nc"/>

  <textvar name="ThetaRes1a"  value="{soil}/thetar1_forest.nc"/>
  <textvar name="ThetaRes1b"  value="{soil}/thetar1_other.nc"/>
  <textvar name="ThetaRes2"   value="{soil}/thetar2.nc"/>

  <textvar name="GenuAlpha1a" value="{soil}/alpha1_forest.nc"/>
  <textvar name="GenuAlpha1b" value="{soil}/alpha1_other.nc"/>
  <textvar name="GenuAlpha2"  value="{soil}/alpha2.nc"/>

  <textvar name="GenuLambda1a" value="{soil}/lambda1_forest.nc"/>
  <textvar name="GenuLambda1b" value="{soil}/lambda1_other.nc"/>
  <textvar name="GenuLambda2"  value="{soil}/lambda2.nc"/>

  <textvar name="KSat1a"  value="{soil}/ksat1_forest.nc"/>
  <textvar name="KSat1b"  value="{soil}/ksat1_other.nc"/>
  <textvar name="KSat2"   value="{soil}/ksat2.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       SPATIAL MAPS — CHANNEL GEOMETRY
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="Channels"           value="{chan}/chan.nc"/>
  <textvar name="ChanGrad"           value="{chan}/changrad.nc"/>
  <textvar name="ChanMan"            value="{chan}/chanman.nc"/>
  <textvar name="ChanLength"         value="{chan}/chanleng.nc"/>
  <textvar name="ChanBottomWidth"    value="{chan}/chanbw.nc"/>
  <textvar name="ChanSdXdY"          value="{chan}/chans.nc"/>
  <textvar name="ChanDepthThreshold" value="{chan}/chanbnkf.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       SPATIAL MAPS — LAI
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="LAIForest" value="{lai}/lai_forest.nc"/>
  <textvar name="LAIOther"  value="{lai}/lai_other.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       GAUGES AND MONITORING SITES
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="GaugesMap"  value="{gauges}/gauges.map"/>
  <textvar name="SitesMap"   value="{gauges}/sites.map"/>

  <!-- ══════════════════════════════════════════════════════════════
       METEOROLOGICAL FORCING (NetCDF stacks)
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="PrecipitationMaps"  value="{meteo}/pr.nc"/>
  <textvar name="TavgMaps"           value="{meteo}/ta.nc"/>
  <textvar name="ETRefMaps"          value="{meteo}/et.nc"/>
  <textvar name="E0Maps"             value="{meteo}/e.nc"/>
  <textvar name="ES0Maps"            value="{meteo}/es.nc"/>

  <!-- ══════════════════════════════════════════════════════════════
       INITIAL CONDITIONS  [FILL IN or use warm-start state files]
       These CANNOT be auto-generated — they require either:
         a) A spin-up simulation (run LISFLOOD for 1-5 years with
            recycled meteorology, then save the end-state)
         b) Observed data (soil moisture from satellite, GW levels
            from wells, stream discharge from gauges)
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="init_luzMaps"   value="[FILL IN: initial upper zone storage (mm)]"/>
  <textvar name="init_lzMaps"    value="[FILL IN: initial lower zone storage (mm)]"/>
  <textvar name="init_dslrMaps"  value="[FILL IN: initial days-since-last-rain]"/>
  <textvar name="init_wMaps"     value="[FILL IN: initial soil moisture layer 1]"/>
  <textvar name="init_w2Maps"    value="[FILL IN: initial soil moisture layer 2]"/>
  <textvar name="init_TotalChanQMaps" value="[FILL IN: initial channel discharge]"/>

  <!-- ══════════════════════════════════════════════════════════════
       CALIBRATION PARAMETERS  [CALIBRATE against observed discharge]
       These require parameter estimation (PEST / DREAM / manual).
       Typical starting values given for Bihar/monsoon climate.
  ═══════════════════════════════════════════════════════════════ -->
  <textvar name="SnowMeltCoef"        value="[CALIBRATE: start 4.0 mm/°C/day]"/>
  <textvar name="b_Xinanjiang"        value="[CALIBRATE: start 0.2, range 0.01–2.0]"/>
  <textvar name="PowerPrefFlow"       value="[CALIBRATE: start 3.0]"/>
  <textvar name="UpperZoneTimeConstant" value="[CALIBRATE: start 10 days]"/>
  <textvar name="LowerZoneTimeConstant" value="[CALIBRATE: start 1000 days]"/>
  <textvar name="GwPercValue"         value="[CALIBRATE: deep percolation, mm/day]"/>
  <textvar name="GwLoss"              value="[CALIBRATE: 0 = no GW loss]"/>
  <textvar name="CalChanMan"          value="[CALIBRATE: Manning multiplier, start 1.0]"/>
  <textvar name="CalEvaporation"      value="[CALIBRATE: ET multiplier, start 1.0]"/>

  <!-- ══════════════════════════════════════════════════════════════
       MISSING SPATIAL INPUTS (not auto-generated — require data)
       Comment out sections for processes not present in your basin.
  ═══════════════════════════════════════════════════════════════ -->
  <!--
  <textvar name="LakeSites"       value="[OPTIONAL: lake/reservoir locations]"/>
  <textvar name="ReservoirSites"  value="[OPTIONAL: reservoir sites]"/>
  <textvar name="WaterUseMap"     value="[OPTIONAL: irrigation/withdrawal]"/>
  <textvar name="PumpingMap"      value="[OPTIONAL: groundwater pumping]"/>
  -->

</lfsettings>
"""

    with open(ini_path, "w") as f:
        f.write(content)

    print(f"\n  ✔  LISFLOOD settings template written → {ini_path}")
    print(f"     Search for [FILL IN] and [CALIBRATE] to complete the configuration.")
    items = content.count("[FILL IN]") + content.count("[CALIBRATE]")
    print(f"     {items} items need manual attention.\n")


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LISFLOOD data-prep pipeline runner")
    parser.add_argument("--check",    action="store_true",
                        help="Show output status without running anything")
    parser.add_argument("--ini-only", action="store_true",
                        help="(Re-)generate the LISFLOOD .ini template only")
    parser.add_argument("--step",     metavar="NAME",
                        help="Run a single step by name: topo lulc soil chan meteo lai")
    args = parser.parse_args()

    # Validate config
    crs = cfg.TARGET_CRS or "Auto-detected"

    if args.check:
        print_status()
        return

    if args.ini_only:
        generate_ini()
        return

    if args.step:
        step_map = {name: script for name, script in STEPS}
        if args.step not in step_map:
            print(f"Unknown step '{args.step}'. Choose from: {', '.join(step_map)}")
            sys.exit(1)
        success = run_step(args.step, step_map[args.step])
        if success:
            generate_ini()
        sys.exit(0 if success else 1)

    # Run all steps
    print(f"\n{'═'*62}")
    print(f"  LISFLOOD Pipeline  |  ROI: {os.path.basename(cfg.ROI_SHAPEFILE)}")
    print(f"  CRS: {crs}  |  {cfg.RESOLUTION_M}m")
    print(f"{'═'*62}")

    failed = []
    for step_name, script_file in STEPS:
        ok = run_step(step_name, script_file)
        if not ok:
            failed.append(step_name)
            print(f"\n  Stopping pipeline — {step_name} failed.")
            break

    print_status()

    if not failed:
        generate_ini()
        print("  ★  Pipeline complete. Edit lisflood_settings.xml before running LISFLOOD.\n")
    else:
        print(f"  ✘  Pipeline stopped at step: {failed[0]}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
