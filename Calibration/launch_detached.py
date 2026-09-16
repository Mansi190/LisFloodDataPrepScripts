#!/usr/bin/env python3
"""Start run_full_calibration.sh fully detached from the calling session.

Double-fork + setsid so the job gets its own session and process group. A plain
background job stays in the caller's process group and dies when that group is
signalled -- which is how the previous run was lost after ~5 evaluations.
macOS ships no setsid(1), hence doing it here.
"""
import os, sys

CAL = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/Calibration"
SCRIPT = os.path.join(CAL, "run_full_calibration.sh")
LOG = os.path.join(CAL, "logs", "full_calibration.log")
PIDFILE = os.path.join(CAL, "logs", "calibration.pid")

if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    os._exit(0)

os.chdir(CAL)
fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.close(os.open("/dev/null", os.O_RDONLY))
with open(PIDFILE, "w") as f:
    f.write(str(os.getpid()))
os.execv("/bin/bash", ["bash", SCRIPT])
