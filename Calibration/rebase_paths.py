#!/usr/bin/env python3
"""Rebase the copied calibration settings to this checkout and Python environment."""
from pathlib import Path
import sys

cal = Path(__file__).resolve().parent
repo = cal.parent
settings = cal / "settings_calibration.txt"
old_root = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts"
text = settings.read_text()
if old_root in text:
    text = text.replace(old_root, str(repo))
text = text.replace(
    "/opt/homebrew/Caskroom/miniconda/base/envs/lisflood/bin/python", sys.executable)
settings.write_text(text)
print(f"Updated {settings} for {repo} and {sys.executable}")
