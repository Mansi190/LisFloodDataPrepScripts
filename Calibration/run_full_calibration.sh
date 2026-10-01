#!/bin/bash
# Prepared for five stations; run only after reviewing FOLLOW_UP.md.
# CAL_6 then CAL_7 must complete upstream before downstream stations start.
set -euo pipefail
CAL="$(cd "$(dirname "$0")" && pwd)"
LISCAL="${LISCAL:-$(dirname "$(dirname "$CAL")")/lisflood-calibration}"
if [ -n "${LISFLOOD_PYTHON:-}" ]; then
    PYTHON="$LISFLOOD_PYTHON"
elif [ -x /opt/homebrew/Caskroom/miniconda/base/envs/lisflood/bin/python ]; then
    PYTHON=/opt/homebrew/Caskroom/miniconda/base/envs/lisflood/bin/python
else
    PYTHON="$(command -v python)"  # Activate the lisflood environment first on Ubuntu.
fi
export PATH="$(dirname "$PYTHON"):$PATH"
NCPUS="${NCPUS:-6}"
SETTINGS="$CAL/settings_calibration.txt"
LOG="$CAL/logs"
export MPLCONFIGDIR="$CAL/temp/matplotlib"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONPATH="$CAL/compat:$LISCAL${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$LOG" "$MPLCONFIGDIR"
cd "$LISCAL"
while IFS= read -r STATION; do
    [ -n "$STATION" ] || continue
    echo "=== START $(date) | station=$STATION ncpus=$NCPUS ==="
    "$PYTHON" -u bin/CAL_6_CALIBRATION.py "$SETTINGS" "$STATION" "$NCPUS" 2>&1 | tee "$LOG/cal6_station_${STATION}.log"
    "$PYTHON" -u bin/CAL_7_LONGTERM_RUN.py "$SETTINGS" "$STATION" 2>&1 | tee "$LOG/cal7_station_${STATION}.log"
done < "$CAL/CatchmentsToProcess.txt"
# Optional CAL_8 plotting is deferred until the plotting compatibility review.
echo "=== ALL DONE $(date) ==="
