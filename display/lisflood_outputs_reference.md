# LISFLOOD Complete Output Reference

> Sources: [Official Docs](https://ec-jrc.github.io/lisflood-model/) | [Code Guide](https://ec-jrc.github.io/lisflood-code/) | [Output Files Annex](https://ec-jrc.github.io/lisflood-model/4_2_annex_output-files/)

LISFLOOD produces three categories of output:
1. **Default outputs** — always generated every run
2. **Time series (.tss)** — at user-defined gauge/site locations
3. **Spatial maps (.nc)** — NetCDF grids over the full domain

Everything is controlled by `<setoption>` flags in the settings XML file.

---

## 1. Default Outputs (Always Generated)

No flag needed — these are produced every run.

| Variable | File | Units | Notes |
|----------|------|-------|-------|
| Channel discharge | `dis.tss` | m³/s | At all gauge locations |
| Cumulative mass balance error | `mbError.tss` | m³ | Water balance check |
| Mass balance error (basin avg) | `mbErrorMm.tss` | mm | Catchment-averaged |
| Channel routing sub-steps | `NoSubStepsChannel.tss` | — | Numerical diagnostics |
| Soil moisture routine sub-steps | `steps.tss` | — | Numerical diagnostics |

---

## 2. State Variable Maps — End of Simulation

**Flag:** `repEndMaps=1`

Snapshot of model state at the **last timestep**. Used to warm-start the next run.
File names below are as written by LISFLOOD OS v4.x (verified against actual run outputs).

| Variable | File | Units | Land Cover Domain |
|----------|------|-------|-------------------|
| Channel discharge | `chanq.end.nc` | m³/s | Channel only |
| Channel cross-section area | `chcro.end.nc` | m² | Channel only |
| Overland flow — other / forest / direct | `ofoth.end.nc` / `offor.end.nc` / `ofdir.end.nc` | m³ | Per fraction |
| Days since last rain | `dslr.end.nc` / `dslf.end.nc` / `dsli.end.nc` | days | Other / Forest / Irrigated |
| Snow cover zones A / B / C | `scova.end.nc` / `scovb.end.nc` / `scovc.end.nc` | mm | Elevation zones (1/3 pixel each) |
| Frost index | `frost.end.nc` | °C·days | Whole pixel |
| Cumulative interception | `cum.end.nc` / `cumf.end.nc` / `cumi.end.nc` | mm | Other / Forest / Irrigated |
| Soil moisture layer 1a (superficial) | `tha.end.nc` / `thfa.end.nc` / `thia.end.nc` | m³/m³ | Other / Forest / Irrigated |
| Soil moisture layer 1b (upper root zone) | `thb.end.nc` / `thfb.end.nc` / `thib.end.nc` | m³/m³ | Other / Forest / Irrigated |
| Soil moisture layer 2 (lower) | `thc.end.nc` / `thfc.end.nc` / `thic.end.nc` | m³/m³ | Other / Forest / Irrigated |
| Upper groundwater zone storage | `uz.end.nc` / `uzf.end.nc` / `uzi.end.nc` | mm | Other / Forest / Irrigated |
| Lower groundwater zone storage | `lz.end.nc` | mm | Whole pixel (single shared store) |
| Depression storage — sealed | `cseal.end.nc` | mm | Impervious fraction |

---

## 3. Time Series at Gauge / Site Locations

### 3a. Discharge & Channel

| Variable | File | Units | Flag |
|----------|------|-------|------|
| Discharge (avg over sub-steps) | `dis.tss` | m³/s | `repDischargeTs=1` |
| Discharge (last routing sub-step) | `chanqWin.tss` | m³/s | `repDischargeTs=1` |
| Water level in channel | `waterLevel.tss` | m above channel bottom | `repWaterLevelTs=1` |

### 3b. State Variables at Sites

**Flag:** `repStateSites=1`

| Variable | File | Units |
|----------|------|-------|
| Water depth on soil surface | `wDepth.tss` | mm |
| Snow cover | `snowCover.tss` | mm |
| Interception storage (cumulative) | `cumInt.tss` | mm |
| Soil moisture — layer 1a | `th1.tss` | m³/m³ |
| Soil moisture — layer 1b | `th2.tss` | m³/m³ |
| Soil moisture — layer 2 | `th3.tss` | m³/m³ |
| Upper groundwater zone storage | `uz.tss` | mm |
| Lower groundwater zone storage | `lz.tss` | mm |
| Days since last rain | `dslr.tss` | days |
| Frost index | `frost.tss` | °C·days |

### 3c. Rate (Flux) Variables at Sites

**Flag:** `repRateSites=1`

| Variable | File | Units |
|----------|------|-------|
| Rain (excluding snow) | `rain.tss` | mm/timestep |
| Snow (excluding rain) | `snow.tss` | mm/timestep |
| Snowmelt | `snowMelt.tss` | mm/timestep |
| Actual soil evaporation | `esAct.tss` | mm/timestep |
| Actual plant transpiration | `tAct.tss` | mm/timestep |
| Rainfall interception by canopy | `interception.tss` | mm/timestep |
| Evaporation of intercepted water | `ewIntAct.tss` | mm/timestep |
| Leaf drainage from canopy | `leafDrainage.tss` | mm/timestep |
| Infiltration into soil | `infiltration.tss` | mm/timestep |
| Preferential bypass flow | `prefFlow.tss` | mm/timestep |
| Percolation upper→lower soil layer | `dTopToSub.tss` | mm/timestep |
| Seepage lower soil→groundwater | `dSubToUz.tss` | mm/timestep |
| Surface runoff | `surfaceRunoff.tss` | mm/timestep |
| Upper zone outflow | `qUz.tss` | mm/timestep |
| Lower zone outflow (baseflow) | `qLz.tss` | mm/timestep |
| Total runoff | `totalRunoff.tss` | mm/timestep |
| Percolation upper→lower GW zone | `percUZLZ.tss` | mm/timestep |
| Groundwater loss (deep percolation) | `loss.tss` | mm/timestep |
| Average LZ inflow at sites | `lzAvIn.tss` | mm/day | *(requires `repLZAvInflowSites=1`)* |

### 3d. Meteorological Variables — Upstream Basin Average

**Flag:** `repMeteoUpsGauges=1`

Spatial average of meteorological forcing over the upstream catchment of each gauge.

| Variable | File | Units |
|----------|------|-------|
| Precipitation | `precipUps.tss` | mm/timestep |
| Potential reference ET (Penman-Monteith crop) | `etUps.tss` | mm/timestep |
| Potential soil evaporation | `esUps.tss` | mm/timestep |
| Potential open water evaporation | `ewUps.tss` | mm/timestep |
| Average daily temperature | `tAvgUps.tss` | °C |

### 3e. State Variables — Upstream Basin Average

**Flag:** `repStateUpsGauges=1`

Same 9 state variables as §3b, averaged over upstream catchment area. Files have `Ups` suffix (e.g., `thTopUps.tss`, `uzUps.tss`).

| Variable | File | Units |
|----------|------|-------|
| Water depth | `wdepthUps.tss` | mm |
| Snow cover | `snowCoverUps.tss` | mm |
| Interception storage | `cumInterceptionUps.tss` | mm |
| Soil moisture — layer 1a | `th1AvUps.tss` | m³/m³ |
| Soil moisture — layer 1b | `th2AvUps.tss` | m³/m³ |
| Soil moisture — layer 2 | `th3AvUps.tss` | m³/m³ |
| Upper groundwater zone | `uzUps.tss` | mm |
| Lower groundwater zone | `lzUps.tss` | mm |
| Days since last rain | `dslrUps.tss` | days |
| Frost index | `frostUps.tss` | °C·days |
| Average LZ inflow | `lzAvInUps.tss` | mm/day | *(requires `repLZAvInflowUpsGauges=1`)* |

### 3f. Rate Variables — Upstream Basin Average

**Flag:** `repRateUpsGauges=1`

Same 19 flux variables as §3c, averaged over upstream catchment. Files have `Ups` suffix (e.g., `rainUps.tss`, `surfaceRunoffUps.tss`, `lossUps.tss`).

> **Performance note:** `repStateUpsGauges`, `repRateUpsGauges`, and `repDischargeMaps` can significantly slow down model execution. Enable only when needed.

---

## 4. Spatial Map Outputs (NetCDF grids, per timestep)

### 4a. Channel & Water Level

| Variable | File Prefix | Units | Flag |
|----------|-------------|-------|------|
| Discharge | `dis` | m³/s | `repDischargeMaps=1` |
| Water level in channel | `wl` | m above channel bottom | `repWaterLevelMaps=1` + `simulateWaterLevels=1` |

### 4b. Meteorological Pass-through Maps

| Variable | File Prefix | Units | Flag |
|----------|-------------|-------|------|
| Precipitation | `pr` | mm | `repPrecipitationMaps=1` |
| Reference ET (crop, Penman-Monteith) | `et` | mm | `repETRefMaps=1` |
| Reference soil evaporation | `es` | mm | `repESRefMaps=1` |
| Reference open water evaporation | `ew` | mm | `repEWRefMaps=1` |
| Average daily temperature | `tav` | °C | `repTavgMaps=1` |

### 4c. State Variable Maps (per timestep)

**Flag:** `repStateMaps=1` — written at timesteps defined by `ReportSteps` in settings.

| Variable | Prefix (Other / Forest / Irrigated) | Units | Flag |
|----------|--------------------------------------|-------|------|
| Water depth on soil | `wdep` | mm | `repWaterDepthMaps=1` |
| Snow cover (elevation zones A/B/C) | `scova` / `scovb` / `scovc` | mm | `repSnowCoverMaps=1` |
| Interception storage | `cum` / `cumf` / `cumi` | mm | `repCumInterceptionMaps=1` |
| Soil moisture layer 1a (superficial) | `tha` / `thfa` / `thia` | m³/m³ | `repThetaMaps=1` |
| Soil moisture layer 1b (upper root zone) | `thb` / `thfb` / `thib` | m³/m³ | `repThetaMaps=1` |
| Soil moisture layer 2 (lower) | `thc` / `thfc` / `thic` | m³/m³ | `repThetaMaps=1` |
| Upper groundwater zone | `uz` / `uzf` / `uzi` | mm | `repUZMaps=1` |
| Lower groundwater zone (single shared store) | `lz` | mm | `repLZMaps=1` |
| Days since last rain | `dslr` / `dslf` / `dsli` | days | `repDSLRMaps=1` |
| Frost index | `frost` | °C·days | `repFrostIndexMaps=1` |
| Instantaneous channel discharge | `chanq` | m³/s | `repStateMaps=1` |
| Channel cross-section area | `chcro` | m² | `repChanCrossSectionMaps=1` |
| Overland flow storage | `ofoth` / `offor` / `ofdir` | m³ | `repStateMaps=1` |

### 4d. Rate / Flux Maps (per timestep)

| Variable | File Prefix | Units | Flag |
|----------|-------------|-------|------|
| Rain (excluding snow) | `rain` | mm | `repRainMaps=1` |
| Snow (excluding rain) | `snow` | mm | `repSnowMaps=1` |
| Snowmelt | `smelt` | mm | `repSnowMeltMaps=1` |
| Actual soil evaporation | `esact` | mm | `repESActMaps=1` |
| Actual plant transpiration | `tact` | mm | `repTaMaps=1` |
| Actual total evapotranspiration | `etact` | mm | `repETActMaps=1` |
| Rainfall interception by canopy | `int` | mm | `repInterceptionMaps=1` |
| Evaporation of intercepted water | `ewint` | mm | `repEWIntMaps=1` |
| Leaf drainage from canopy | `ldra` | mm | `repLeafDrainageMaps=1` |
| Infiltration into soil | `inf` | mm | `repInfiltrationMaps=1` |
| Preferential bypass flow | `pflow` | mm | `repPrefFlowMaps=1` |
| Percolation upper→lower soil | `to2su` | mm | `repPercolationMaps=1` |
| Seepage lower soil→groundwater | `sgw` | mm | `repSeepSubToGWMaps=1` |
| Surface runoff | `srun` | mm | `repSurfaceRunoffMaps=1` |
| Upper zone outflow | `quz` | mm | `repUZOutflowMaps=1` |
| Lower zone outflow (baseflow) | `qlz` | mm | `repLZOutflowMaps=1` |
| Fast runoff (surface + upper zone) | `frun` | mm | `repFastRunoffMaps=1` |
| Total runoff | `trun` | mm | `repTotalRunoffMaps=1` |
| Percolation UZ→LZ (GW recharge) | `uz2lz` | mm | `repGwPercUZLZMaps=1` |
| Groundwater loss (deep percolation) | `gwl` | mm | `repGwLossMaps=1` |
| Soil transpiration reduction factor | `rws` | 0–1 | `repRWS=1` |
| Soil moisture stress days count | `smstress` | days | `repStressDays=1` |

---

## 5. Optional Module Outputs

### 5a. Lakes

**Flag:** `simulateLakes=1` + `repsimulateLakes=1`

Only works with kinematic wave routing (not dynamic wave).

| Variable | File | Units | Description |
|----------|------|-------|-------------|
| Lake inflow | `qLakeIn.tss` | m³/s | Flow entering the lake |
| Lake outflow | `qLakeOut.tss` | m³/s | Flow leaving the lake |
| Lake evaporation | `EWLake.tss` | mm | Open water evaporation loss |
| Lake water level | `hLake.tss` | m | Lake surface elevation |
| Lake level (end map) | `lakhxxxx.nc` | m | Final timestep state map |

### 5b. Reservoirs

**Flag:** `simulateReservoirs=1` + `repsimulateReservoirs=1`

Only works with kinematic wave routing.

| Variable | File | Units | Description |
|----------|------|-------|-------------|
| Reservoir inflow | `qresin.tss` | m³/s | Inflow to reservoir |
| Reservoir outflow | `qresout.tss` | m³/s | Controlled release + spillway |
| Reservoir fill fraction | `resfill.tss` | 0–1 | Fraction of total capacity filled |
| Reservoir fill (end map) | `rsfilxxx.nc` | fraction | Final timestep state map |

### 5c. Polders

**Flag:** `simulatePolders=1` + `repsimulatePolders=1`

Only works with dynamic wave routing.

| Variable | File | Units | Description |
|----------|------|-------|-------------|
| Polder water level | `hPolder.tss` | m | Water level inside polder |
| Polder flux | `qPolder.tss` | m³/s | Exchange (+ve = channel→polder, −ve = polder→channel) |
| Polder level (end map) | `hpolxxxx.nc` | m | Final timestep state map |

---

## 6. Soil Moisture Tension (pF)

**Flag:** `simulatePF=1` (must be on first)

pF = log₁₀(capillary suction head in cm). Range ~1 (waterlogged) to ~5 (air-dry). Calculated from Van Genuchten equations. Does **not** affect model results — reporting only.

| Variable | File | Units | Flag |
|----------|------|-------|------|
| pF upper soil — at sites | `pFTop.tss` | dimensionless | `repPFSites=1` |
| pF lower soil — at sites | `pFSub.tss` | dimensionless | `repPFSites=1` |
| pF upper soil — upstream avg | `pFTopUps.tss` | dimensionless | `repPFUpsGauges=1` |
| pF lower soil — upstream avg | `pFSubUps.tss` | dimensionless | `repPFUpsGauges=1` |
| pF upper soil — spatial map | prefix `pftop` | dimensionless | `repPF1Maps=1` |
| pF lower soil — spatial map | prefix `pfsub` | dimensionless | `repPF2Maps=1` |

---

## 7. Water Use & Stress Indicators

**Flag:** `wateruse=1` (base module)

### Consumptive Use Maps — `repTotalWUse=1`

| Variable | File | Units | Sector |
|----------|------|-------|--------|
| Domestic consumptive use | `domCo.nc` | mm | Households |
| Industrial consumptive use | `indCo.nc` | mm | Manufacturing |
| Energy consumptive use | `eneCo.nc` | mm | Power generation |
| Livestock consumptive use | `livCo.nc` | mm | Agriculture/livestock |
| Total surface water abstraction | `TotalWUse.nc` | mm | `repTotalAbs=1` |
| Regional total abstraction | `TotalWUseRegion.nc` | mm | `repTotalAbs=1` |

### Water Stress Indices — `repWIndex=1`

Reported monthly, per water region.

| Index | File | Units | Description |
|-------|------|-------|-------------|
| Falkenmark Index 1 | `Fk1.nc` | m³/capita/month | Local water availability per person |
| Falkenmark Index 3 | `Fk3.nc` | m³/capita/month | Water availability including upstream inflow |
| Water Exploitation Index (Abstraction) | `WeiA.nc` | fraction | Total abstraction / available water |
| Water Exploitation Index (Consumption) | `WeiC.nc` | fraction | Total consumption / available water |
| Water Exploitation Index (Demand) | `WeiD.nc` | fraction | Total demand / available water |
| Water Dependency Index | `WDI.nc` | fraction | Fraction of demand unmet by local supply |
| Irrigation shortage | `IrSh.nc` | m³ | Irrigation demand not met due to water availability |
| E-flow breach indicator | `Eflow.nc` | 0 or 1 | Days when environmental flow threshold is breached |

---

## 8. Warm-Start / Pre-run Outputs

**Flag:** `InitLisflood=1`

Run LISFLOOD in pre-run mode (multi-year spin-up) to generate stable initial conditions. These outputs feed back as inputs to the actual simulation.

| Variable | File | Units | Description |
|----------|------|-------|-------------|
| Average LZ inflow — other fraction | `lzavin.nc` | mm/timestep | Steady-state GW recharge for other land cover |
| Average LZ inflow — forest fraction | `lzavin_forest.nc` | mm/timestep | Steady-state GW recharge for forest |
| Average discharge | `avgdis.nc` | m³/s | Long-term mean Q, needed for split routing |

---

## 9. Quick Reference: All `setoption` Reporting Flags

```xml
<!-- ── ALWAYS ON ────────────────────────────────────────── -->
<setoption name="repDischargeTs"          choice="1"/>  <!-- dis.tss, chanqWin.tss -->
<setoption name="repEndMaps"              choice="1"/>  <!-- .end.nc state snapshots -->
<setoption name="repStateMaps"            choice="1"/>  <!-- state maps at ReportSteps -->

<!-- ── TIME SERIES AT SITES ──────────────────────────────── -->
<setoption name="repStateSites"           choice="1"/>  <!-- SM, GW, snow at sites -->
<setoption name="repRateSites"            choice="1"/>  <!-- ET, runoff, infiltration at sites -->
<setoption name="repLZAvInflowSites"      choice="1"/>  <!-- GW recharge at sites -->
<setoption name="repPFSites"              choice="1"/>  <!-- soil pF at sites -->
<setoption name="repWaterLevelTs"         choice="1"/>  <!-- channel water level at gauges -->

<!-- ── TIME SERIES UPSTREAM OF GAUGES ───────────────────── -->
<setoption name="repMeteoUpsGauges"       choice="1"/>  <!-- P, ET0, T upstream averages -->
<setoption name="repStateUpsGauges"       choice="1"/>  <!-- SM, GW upstream averages -->
<setoption name="repRateUpsGauges"        choice="1"/>  <!-- fluxes upstream averages -->
<setoption name="repLZAvInflowUpsGauges"  choice="1"/>  <!-- GW recharge upstream averages -->
<setoption name="repPFUpsGauges"          choice="1"/>  <!-- soil pF upstream averages -->

<!-- ── SPATIAL MAP STACKS ────────────────────────────────── -->
<setoption name="repDischargeMaps"        choice="1"/>  <!-- dis.nc -->
<setoption name="repWaterLevelMaps"       choice="1"/>  <!-- wl.nc -->
<setoption name="repPrecipitationMaps"    choice="1"/>  <!-- pr.nc -->
<setoption name="repETRefMaps"            choice="1"/>  <!-- et.nc -->
<setoption name="repESRefMaps"            choice="1"/>  <!-- es.nc -->
<setoption name="repEWRefMaps"            choice="1"/>  <!-- ew.nc -->
<setoption name="repTavgMaps"             choice="1"/>  <!-- tav.nc -->
<setoption name="repThetaMaps"            choice="1"/>  <!-- soil moisture maps -->
<setoption name="repThetaForestMaps"      choice="1"/>  <!-- soil moisture forest maps -->
<setoption name="repUZMaps"               choice="1"/>  <!-- upper GW zone maps -->
<setoption name="repLZMaps"               choice="1"/>  <!-- lower GW zone maps -->
<setoption name="repSnowMaps"             choice="1"/>  <!-- snow maps -->
<setoption name="repSnowCoverMaps"        choice="1"/>  <!-- snow cover maps -->
<setoption name="repSnowMeltMaps"         choice="1"/>  <!-- snowmelt maps -->
<setoption name="repRainMaps"             choice="1"/>  <!-- rain maps -->
<setoption name="repESActMaps"            choice="1"/>  <!-- actual soil evaporation maps -->
<setoption name="repTaMaps"               choice="1"/>  <!-- transpiration maps -->
<setoption name="repETActMaps"            choice="1"/>  <!-- actual ET maps -->
<setoption name="repInterceptionMaps"     choice="1"/>  <!-- interception maps -->
<setoption name="repEWIntMaps"            choice="1"/>  <!-- intercepted water evap maps -->
<setoption name="repLeafDrainageMaps"     choice="1"/>  <!-- leaf drainage maps -->
<setoption name="repInfiltrationMaps"     choice="1"/>  <!-- infiltration maps -->
<setoption name="repPrefFlowMaps"         choice="1"/>  <!-- preferential flow maps -->
<setoption name="repPercolationMaps"      choice="1"/>  <!-- soil percolation maps -->
<setoption name="repSeepSubToGWMaps"      choice="1"/>  <!-- seepage to GW maps -->
<setoption name="repSurfaceRunoffMaps"    choice="1"/>  <!-- surface runoff maps -->
<setoption name="repUZOutflowMaps"        choice="1"/>  <!-- upper zone outflow maps -->
<setoption name="repLZOutflowMaps"        choice="1"/>  <!-- lower zone outflow maps -->
<setoption name="repFastRunoffMaps"       choice="1"/>  <!-- fast runoff maps -->
<setoption name="repTotalRunoffMaps"      choice="1"/>  <!-- total runoff maps -->
<setoption name="repGwPercUZLZMaps"       choice="1"/>  <!-- GW recharge maps -->
<setoption name="repGwLossMaps"           choice="1"/>  <!-- GW loss maps -->
<setoption name="repWaterDepthMaps"       choice="1"/>  <!-- overland flow depth maps -->
<setoption name="repFrostIndexMaps"       choice="1"/>  <!-- frost index maps -->
<setoption name="repDSLRMaps"             choice="1"/>  <!-- days since last rain maps -->
<setoption name="repRWS"                  choice="1"/>  <!-- transpiration reduction factor -->
<setoption name="repStressDays"           choice="1"/>  <!-- SM stress days count -->
<setoption name="repPF1Maps"              choice="1"/>  <!-- pF upper soil maps -->
<setoption name="repPF2Maps"              choice="1"/>  <!-- pF lower soil maps -->

<!-- ── WATER USE & INDICATORS ────────────────────────────── -->
<setoption name="repTotalAbs"             choice="1"/>  <!-- total abstraction maps -->
<setoption name="repTotalWUse"            choice="1"/>  <!-- sector consumptive use maps -->
<setoption name="repWIndex"               choice="1"/>  <!-- water stress indices -->

<!-- ── OPTIONAL MODULES ──────────────────────────────────── -->
<setoption name="simulateLakes"           choice="1"/>  <!-- lake TSS outputs -->
<setoption name="simulateReservoirs"      choice="1"/>  <!-- reservoir TSS outputs -->
<setoption name="simulatePolders"         choice="1"/>  <!-- polder TSS outputs -->
<setoption name="simulatePF"              choice="1"/>  <!-- enable pF computation -->
<setoption name="simulateWaterLevels"     choice="1"/>  <!-- enable water level computation -->
<setoption name="wateruse"                choice="1"/>  <!-- enable water abstraction module -->
```

---

## 10. Land Cover Fractions Explained

LISFLOOD tracks hydrological processes separately for **5 land cover types**. Many output variables therefore have per-fraction variants:

| Fraction | Suffix convention | Description |
|----------|------------------|-------------|
| Other (grassland/crops) | no suffix | Default land cover |
| Forest | `F` or `Forest` | Higher interception, deeper roots |
| Irrigated land | `i` or `Irrigation` | Receives additional water demand |
| Impervious/sealed | `Sealed` | No infiltration, direct runoff only |
| Open water | — | Evaporation only, no soil processes |

State maps like soil moisture (`tha`, `thfa`, `thia`) and interception (`cum`, `cumF`, `cumi`) are reported separately per fraction because each fraction has independent soil and storage behaviour.

---

*Sources: [LISFLOOD Model Docs](https://ec-jrc.github.io/lisflood-model/) | [LISFLOOD Code Guide](https://ec-jrc.github.io/lisflood-code/) | [Output Files Annex](https://ec-jrc.github.io/lisflood-model/4_2_annex_output-files/) | [Lakes](https://ec-jrc.github.io/lisflood-model/3_02_optLISFLOOD_lakes/) | [Reservoirs](https://ec-jrc.github.io/lisflood-model/3_03_optLISFLOOD_reservoirs/) | [Polders](https://ec-jrc.github.io/lisflood-model/3_04_optLISFLOOD_polder/) | [Water Use](https://ec-jrc.github.io/lisflood-model/2_18_stdLISFLOOD_water-use/) | [Soil pF](https://ec-jrc.github.io/lisflood-model/3_07_optLISFLOOD_soil-moisture/) | [Water Levels](https://ec-jrc.github.io/lisflood-model/3_10_optLISFLOOD_water-levels/)*
