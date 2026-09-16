#!/bin/bash
# Full LISFLOOD calibration for ONE station (set STATION below). ROI hydrobasins_roi_lev6_4060028560:
#   1 = FloodHub hybas_4121140140 (753.9 km2, drains into 2 -> calibrate before 2)
#   2 = FloodHub hybas_4121142560 (2,244.9 km2)
#   3 = FloodHub hybas_4121593580 (762.7 km2, separate river)
# CAL_6 (DEAP calibration) -> CAL_7 (long-term run) -> CAL_8 (products).
set -o pipefail

source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh
# NOTE: the 'lisflood-pcraster' env has drifted to py3.14 / numpy 2.4.3 / pandas 3.0.1,
# which lisflood 4.3.1 does not support (it needs numpy<2 and pandas<2).
# The 'lisflood' env (py3.10, numpy 1.26.4, pandas 1.5.3, pcraster 4.4.2) is in spec.
conda activate lisflood

LISCAL=/Users/mansi/Documents/LisFlood/lisflood-calibration
CAL=/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/Calibration
SETTINGS=$CAL/settings_calibration.txt
STATION=1
NCPUS=6   # 10 cores, 16GB. 6 divides lambda_=36 into 6 clean waves. Peak/worker
          # dropped ~500MB->~45MB by NetCDFTimeChunks=30 in the settings template
          # (was "auto" = 328-day chunks x 4 forcings held decompressed).
LOG=$CAL/logs

cd "$LISCAL"
# Keep each worker single-threaded: numpy/BLAS otherwise spawns a thread pool per
# process, and N workers x M threads oversubscribes the 10 cores.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
       VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$CAL/compat:$LISCAL${PYTHONPATH:+:$PYTHONPATH}"

echo "=== START $(date) | station=$STATION ncpus=$NCPUS ==="

echo "=== CAL_6 CALIBRATION $(date) ==="
python -u bin/CAL_6_CALIBRATION.py "$SETTINGS" "$STATION" "$NCPUS" 2>&1 | tee "$LOG/cal6.log"
rc=${PIPESTATUS[0]}
if [ $rc -ne 0 ]; then echo "!!! CAL_6 FAILED rc=$rc $(date)"; exit $rc; fi

echo "=== CAL_7 LONGTERM RUN $(date) ==="
python -u bin/CAL_7_LONGTERM_RUN.py "$SETTINGS" "$STATION" 2>&1 | tee "$LOG/cal7.log"
rc=${PIPESTATUS[0]}
if [ $rc -ne 0 ]; then echo "!!! CAL_7 FAILED rc=$rc $(date)"; exit $rc; fi

echo "=== CAL_8 POSTPROCESSING $(date) ==="
python -u bin/CAL_8_POSTPROCESSING.py "$SETTINGS" "$STATION" 2>&1 | tee "$LOG/cal8.log"
rc=${PIPESTATUS[0]}
if [ $rc -ne 0 ]; then echo "!!! CAL_8 FAILED rc=$rc $(date)"; exit $rc; fi

echo "=== ALL DONE $(date) ==="
