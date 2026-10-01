# `scripts/display/`

Turns LISFLOOD run outputs into figures.

| script | what it does |
|---|---|
| `generate_html_plots.py` | scans `outputs/cold`, `outputs/warm` and the pipeline inputs → PNGs/GIFs under `display/` + `manifest.js` for the HTML report. Incremental (mtime-based) with `--watch` and `--force`. |
| `animate_output.py` | one per-timestep `.nc` stack → MP4 (ffmpeg) or GIF |

Full walkthrough: [`../docs/display.md`](../docs/display.md).
Repo overview: [`../README.md`](../README.md).
