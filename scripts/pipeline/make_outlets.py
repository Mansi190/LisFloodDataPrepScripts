import os
import sys
import numpy as np
import xarray as xr
from lisflood_utils import load_grid, save_aligned, gdal_convert_netcdf
import pyproj
import rasterio
import pipeline_config as cfg

REPORTING_DIR = os.path.join(cfg.BASE_DIR, "reportingStations")


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
    area_tif = os.path.join(cfg.DIR_MAPS, "area.tif")
    ldd_tif = os.path.join(cfg.DIR_MAPS, "ldd.tif")
    facc_tif = os.path.join(cfg.DIR_RAW, "facc_snapped.tif")
    
    info, mask = load_grid(area_tif)
    
    with rasterio.open(ldd_tif) as src:
        ldd = src.read(1)
    with rasterio.open(facc_tif) as src:
        facc = src.read(1)
        
    # Read stations.csv
    with open(os.path.join(REPORTING_DIR, "stations.csv"), "r") as f:
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
    os.makedirs(REPORTING_DIR, exist_ok=True)
    temp_tif = os.path.join(REPORTING_DIR, "outlets.tif")
    out_nc = os.path.join(REPORTING_DIR, "outlets.nc")
    
    save_aligned(outlets_arr, temp_tif, "uint8", 0, like=area_tif)
    gdal_convert_netcdf(temp_tif, out_nc)
    os.remove(temp_tif)
    
    print(f"Successfully generated {out_nc}!")
    

if __name__ == "__main__":
    main()
