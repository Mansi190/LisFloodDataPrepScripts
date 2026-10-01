#!/usr/bin/env python3
"""Start run_full_calibration.sh fully detached on POSIX systems.

Double-fork + setsid so the job gets its own session and process group. A plain
background job stays in the caller's process group and dies when that group is
signalled.
"""
import os, sys
from pathlib import Path

CAL = str(Path(__file__).resolve().parent)
SCRIPT = os.path.join(CAL, "run_full_calibration.sh")
LOGDIR = os.path.join(CAL, "logs")
LOG = os.path.join(LOGDIR, "full_calibration.log")
PIDFILE = os.path.join(LOGDIR, "calibration.pid")

if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    os._exit(0)

os.chdir(CAL)
os.makedirs(LOGDIR, exist_ok=True)
os.environ.setdefault("LISFLOOD_PYTHON", sys.executable)
fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(fd, 1); os.dup2(fd, 2)
os.close(os.open("/dev/null", os.O_RDONLY))
with open(PIDFILE, "w") as f:
    f.write(str(os.getpid()))
os.execv("/bin/bash", ["bash", SCRIPT])
