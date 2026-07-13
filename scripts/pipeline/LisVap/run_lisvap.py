import os
import sys
import subprocess
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline_config as _cfg
from lisflood_utils import log

def visualize_lisvap_outputs():
    try:
        import xarray as xr
        import matplotlib.pyplot as plt
        import numpy as np
        log("Generating LISVAP visual checks...")
    except ImportError as e:
        log(f"Skipping visualization: missing required libraries ({e}). Use 'pcraster_env' environment.", "WARN")
        return

    meteo_dir = _cfg.DIR_METEO
    area_path = os.path.join(os.path.dirname(meteo_dir), "maps/area.nc")
    
    # Load mask
    try:
        with xr.open_dataset(area_path) as ds_area:
            mask = ds_area['Band1'].values
    except Exception as e:
        log(f"Failed to load mask file area.nc: {e}", "WARN")
        return

    variables = {
        "et": ("et.nc", "Potential Reference Evapotranspiration (ET0)", "#3b82f6"),
        "e": ("e.nc", "Potential Open Water Evaporation (E0)", "#10b981"),
        "es": ("es.nc", "Potential Soil Evaporation (ES0)", "#f59e0b")
    }

    # Set up subplots (3 rows, 2 columns)
    fig, axes = plt.subplots(3, 2, figsize=(14, 15), dpi=150)
    
    for idx, (key, (file_name, title, color)) in enumerate(variables.items()):
        path = os.path.join(meteo_dir, file_name)
        if not os.path.exists(path):
            log(f"LISVAP output file not found for visualization: {path}", "WARN")
            continue
        
        try:
            with xr.open_dataset(path) as ds:
                var_name = list(ds.data_vars)[0]
                data = ds[var_name].values  # shape: (time, y, x)
        except Exception as e:
            log(f"Failed to load {file_name}: {e}", "WARN")
            continue
        
        # Calculate daily mean inside mask
        daily_mean = []
        for t in range(data.shape[0]):
            daily_mean.append(np.nanmean(data[t][mask == 1]))
            
        # Select a mid-year day to show spatial map (day 180)
        spatial_day = min(180, data.shape[0] - 1)
        spatial_map = data[spatial_day].copy()
        spatial_map[mask != 1] = np.nan
        
        # Plot 1: Spatial Map
        ax_map = axes[idx, 0]
        im = ax_map.imshow(spatial_map, cmap='YlOrRd', origin='upper')
        fig.colorbar(im, ax=ax_map, label='mm/day', shrink=0.8)
        ax_map.set_title(f"{title} - Spatial Map (Day {spatial_day + 1})", fontsize=11, fontweight='bold', color='#1e293b')
        ax_map.grid(False)
        ax_map.axis('off')
        
        # Plot 2: Time Series of Basin Mean
        ax_ts = axes[idx, 1]
        ax_ts.plot(range(len(daily_mean)), daily_mean, color=color, linewidth=2, label='Basin Mean')
        ax_ts.fill_between(range(len(daily_mean)), daily_mean, color=color, alpha=0.1)
        ax_ts.set_title(f"{title} - Seasonal Cycle", fontsize=11, fontweight='bold', color='#1e293b')
        ax_ts.set_xlabel("Time (Days)", fontsize=9, color='#475569')
        ax_ts.set_ylabel("Evaporation (mm/day)", fontsize=9, color='#475569')
        ax_ts.grid(True, linestyle="--", linewidth=0.5, color='#e2e8f0')
        ax_ts.spines['top'].set_visible(False)
        ax_ts.spines['right'].set_visible(False)
        ax_ts.spines['left'].set_color('#cbd5e1')
        ax_ts.spines['bottom'].set_color('#cbd5e1')
        ax_ts.legend(loc='upper right')

    plt.suptitle("LISVAP Output Visualizations", fontsize=16, fontweight='bold', y=0.98, color='#1e293b')
    plt.tight_layout()
    output_png = os.path.join(meteo_dir, "LISVAP_VISUAL_CHECK.png")
    plt.savefig(output_png, bbox_inches='tight')
    plt.close()
    log(f"✔ LISVAP visual check saved successfully → {output_png}")

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
    visualize_lisvap_outputs()

if __name__ == "__main__":
    main()
