import os
import sys
import subprocess
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline_config as _cfg
from lisflood_utils import log

def main():
    log("STEP 3 - Running LISVAP via Docker", "STEP")
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [
        "docker", "run", "--rm", 
        "-v", f"{parent_dir}:/input", 
        "jrce1/lisvap", 
        "/input/LisVap/settings_lisvap.xml"
    ]
    log(f"Running: {' '.join(cmd)}")
    
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log("Docker run failed! Make sure Docker is running on your machine.", "ERROR")
        sys.exit(r.returncode)
    
    log("✔ LISVAP completed successfully.")

if __name__ == "__main__":
    main()
