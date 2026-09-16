import os
import sys
import subprocess
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline_config as _cfg
from lisflood_utils import log


def main():
    log("STEP 3 - Running LISVAP via Docker", "STEP")
    # Mount the REPO ROOT, not this script's parent, so the container sees the same
    # /input tree as the LISFLOOD runs (/input/inputs/maps, /input/inputs/meteo).
    # Deriving the mount from __file__ is what broke at the 2026-07 reorg: moving
    # LisVap/ down to scripts/pipeline/ silently repointed it at scripts/pipeline.
    settings = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "settings_lisvap.xml")
    settings_in_container = "/input/" + os.path.relpath(settings, _cfg.REPO_ROOT)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{_cfg.REPO_ROOT}:/input",
        "jrce1/lisvap",
        settings_in_container,
    ]
    log(f"Running: {' '.join(cmd)}")
    
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log("Docker run failed! Make sure Docker is running on your machine.", "ERROR")
        sys.exit(r.returncode)
    
    log("✔ LISVAP completed successfully.")

if __name__ == "__main__":
    main()
