# Review before starting calibration

Calibration is intentionally NOT started. Five non-CWC HYBAS locations in basin
4061001260 are selected. Preparation uses the existing placement rules provisionally;
it does not resolve the placement questions below.

## Gauge coordinates and placement

- Trace catalogue longitude/latitude through CRS conversion, pixel indexing,
  snapping, reporting maps, calibration station coordinates and plotted markers.
- Review `scripts/pipeline/make_outlets.py`: `round` versus pixel-centre indexing,
  square search window versus a true distance radius, upstream-area matching,
  distance tie-breaking, 20% area tolerance, channel membership and duplicate cells.
- Compare cached `inputs/raw/facc_snapped.tif`, `ldd.tif`, `ldd.nc` and the repaired
  `ldd.map` actually used by calibration. Check accumulation and network consistency.
- Review `scripts/pipeline/make_inflow.py`, `align_station`, and all other places
  that move coordinates. Catalogue coordinates must remain recorded unchanged.
- Review CAL_2 inlet placement: it moves upstream outlets one cell downstream,
  and may move them farther to resolve inlet collisions. These are inflow boundary
  cells, not new physical gauge positions.
- Plot original and model positions with connecting lines and displacement distances.
  Inspect river identity and upstream topology, not just matching drainage area.
- Current candidate placements are kilometres from catalogue locations. Resolve
  this before accepting the station placement for scientific use.

## Calibration settings

- Review forcing, prerun, calibration/validation split and station spin-up dates.
- Review parameter ranges, objective (KGE), DEAP population/generations and stopping.
- Review upstream/downstream order, inflow files and interstation masks.
  Prepared order: 2/3/4 before 1, then 5. Inflows are required only for 1 and 5;
  stations 2/3/4 use no inflow. Check the TSS-header ObsID to raster-ID mapping.
- Check the gap between the current prerun end (2008-12-31) and the calibration
  run start (2011-07-03); understand how stored states are transferred across it.
- Choose worker count after a RAM/CPU benchmark on the intended Ubuntu host.
- Review portability of paths and dependencies before transferring the workspace.
- GRRR discharge is modelled reanalysis, not measured gauge discharge. Four selected
  locations are not quality-verified in the local catalogue; assess suitability.
- Revisit plotting compatibility and required optional spatial diagnostics.

## Start condition

Wait for the user's explicit instruction to start. Preparation and validation of
files do not authorise CAL_6, CAL_7, or a LISFLOOD simulation.
