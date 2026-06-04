import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import xarray as xr
from lisflood_utils import load_grid, save_aligned, gdal_convert_netcdf
import pyproj
import rasterio


def align_station(row, col, facc, ldd, mask, max_radius=2):
    """Finds the pixel with the HIGHEST flow accumulation (main river channel) within the search radius."""
    best_row, best_col = row, col
    max_acc = -1.0
    found = False
    
    for r in range(-max_radius, max_radius + 1):
        for c in range(-max_radius, max_radius + 1):
            nr, nc = row + r, col + c
            if 0 <= nr < facc.shape[0] and 0 <= nc < facc.shape[1]:
                # Must be inside mask and have valid LDD (PCRaster uses 1-9)
                if mask[nr, nc] > 0 and 1 <= ldd[nr, nc] <= 9:
                    val = facc[nr, nc]
                    if val > max_acc and val != -9999 and not np.isnan(val):
                        max_acc = val
                        best_row, best_col = nr, nc
                        found = True
                        
    return (best_row, best_col) if found else (None, None)

def main():
    # Load canonical grid and masks
    area_tif = "./lisflood_topography/maps/area.tif"
    dem_tif = "./lisflood_topography/maps/dem_300m.tif"
    ldd_tif = "./lisflood_topography/maps/ldd.tif"
    facc_tif = "./lisflood_channels/raw/facc_snapped.tif"
    
    info, mask = load_grid(area_tif)
    
    with rasterio.open(dem_tif) as src:
        dem = src.read(1)
    with rasterio.open(ldd_tif) as src:
        ldd = src.read(1)
    with rasterio.open(facc_tif) as src:
        facc = src.read(1)
        
    # Read stations.csv
    with open("stations.csv", "r") as f:
        line = f.read().strip()
    
    parts = line.split()
    if len(parts) < 3:
        print("Invalid stations.csv format")
        sys.exit(1)
        
    lon = float(parts[0]) / 10000.0 if not '.' in parts[0] else float(parts[0])
    lat = float(parts[1]) / 10000.0 if not '.' in parts[1] else float(parts[1])
    station_id = int(parts[2])
    
    print(f"Raw Station: Lon={lon}, Lat={lat}, ID={station_id}")
    
    # Map to Grid
    transformer = pyproj.Transformer.from_crs("EPSG:4326", info.crs, always_xy=True)
    x_utm, y_utm = transformer.transform(lon, lat)
    
    t = info.transform
    col_raw = int(round((x_utm - t.c) / t.a))
    row_raw = int(round((y_utm - t.f) / t.e))
    
    # Check if raw coordinate is valid
    is_valid_raw = (0 <= row_raw < info.height and 0 <= col_raw < info.width)
    
    row_snap, col_snap = row_raw, col_raw
    snapped = False
    
    if is_valid_raw and mask[row_raw, col_raw] > 0:
        print("Station lands inside the mask! Aligning with Flow Accumulation and LDD...")
        nr, nc = align_station(row_raw, col_raw, facc, ldd, mask, max_radius=2)
        if nr is not None:
            if nr != row_raw or nc != col_raw:
                row_snap, col_snap = nr, nc
                snapped = True
                print(f"Successfully aligned station to main river channel at Row={nr}, Col={nc} (Acc={facc[nr,nc]:.0f} pixels)")
            else:
                print("Station is already exactly on the main river channel.")
        else:
            print("Failed to align: No valid LDD pixels found nearby!")
    else:
        print("Warning: Station lands outside the basin mask!")
            
    # Generate outlets array
    outlets_arr = np.zeros((info.height, info.width), dtype=np.uint8)
    if 0 <= row_snap < info.height and 0 <= col_snap < info.width:
        outlets_arr[row_snap, col_snap] = station_id
        
    # Strict mask (just to be completely safe)
    outlets_arr = np.where(mask > 0, outlets_arr, 0)
    
    # Save to NetCDF
    os.makedirs("reportingStations", exist_ok=True)
    temp_tif = "reportingStations/outlets.tif"
    out_nc = "reportingStations/outlets.nc"
    
    save_aligned(outlets_arr, temp_tif, "uint8", 0, like=area_tif)
    gdal_convert_netcdf(temp_tif, out_nc)
    os.remove(temp_tif)
    
    print(f"Successfully generated {out_nc}!")
    
    # -------------------------------------------------------------
    # VISUALIZATION
    # -------------------------------------------------------------
    print("Generating visualization...")
    fig, axes = plt.subplots(1, 2, figsize=(20, 10))

    # Pre-process arrays for plotting
    dem_plot = dem.astype(np.float32).copy()
    dem_plot[mask == 0] = np.nan
    dem_plot[dem_plot == -9999] = np.nan

    ldd_plot = ldd.astype(np.float32).copy()
    ldd_plot[mask == 0] = np.nan
    ldd_plot[ldd_plot == 255] = np.nan

    # 1. DEM Subplot
    ax = axes[0]
    im1 = ax.imshow(dem_plot, cmap='terrain')
    plt.colorbar(im1, ax=ax, label='Elevation (m)', shrink=0.7)
    
    if is_valid_raw:
        ax.scatter(col_raw, row_raw, color='black', s=100, marker='x', label='Raw Coordinate')
    if 0 <= row_snap < info.height and 0 <= col_snap < info.width:
        ax.scatter(col_snap, row_snap, color='red', s=200, marker='*', edgecolor='black', zorder=5, label='Final Station')
        if snapped:
            ax.plot([col_raw, col_snap], [row_raw, row_snap], color='red', linestyle='--', alpha=0.7)
            
    ax.set_title("Outlet Mapping on DEM", fontsize=14, fontweight='bold')
    ax.axis('off')
    ax.legend(loc='lower right')

    # 2. LDD Subplot
    ax = axes[1]
    cmap_ldd = plt.cm.get_cmap('Set3', 9)
    im2 = ax.imshow(ldd_plot, cmap=cmap_ldd, vmin=0.5, vmax=9.5)
    plt.colorbar(im2, ax=ax, label='Flow Direction (1-9)', shrink=0.7, ticks=range(1, 10))
    
    if is_valid_raw:
        ax.scatter(col_raw, row_raw, color='black', s=100, marker='x', label='Raw Coordinate')
    if 0 <= row_snap < info.height and 0 <= col_snap < info.width:
        ax.scatter(col_snap, row_snap, color='red', s=200, marker='*', edgecolor='black', zorder=5, label='Final Station')
        if snapped:
            ax.plot([col_raw, col_snap], [row_raw, row_snap], color='red', linestyle='--', alpha=0.7)
            ax.text(col_snap + 2, row_snap - 2, "Snapped", color='red', fontweight='bold', fontsize=12)
            
    ax.set_title("Outlet Mapping on LDD (Local Drain Direction)", fontsize=14, fontweight='bold')
    ax.axis('off')
    ax.legend(loc='lower right')

    plt.tight_layout()
    out_png = "reportingStations/OUTLET_VISUAL_CHECK.png"
    plt.savefig(out_png, dpi=150, bbox_inches='tight')
    print(f"Saved visualization to {out_png}")

if __name__ == "__main__":
    main()
