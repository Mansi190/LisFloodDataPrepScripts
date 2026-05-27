import numpy as np
import xarray as xr
import rasterio
from pyproj import Transformer
import os

# Create a latitude NetCDF file which is required by LISVAP as a BaseMap.
# It reads the geospatial bounds from the canonical mask (area.tif)

area_tif = '../lisflood_topography/maps/area.tif'
lat_nc = '../lisflood_topography/maps/lat.nc'

def generate_lat_nc():
    print(f"Reading bounds from {area_tif}...")
    with rasterio.open(area_tif) as src:
        width, height = src.width, src.height
        transform = src.transform
        crs = src.crs

    # We need to transform the projected coordinates back to WGS84 (Lat/Lon)
    transformer = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)

    # Generate 2D arrays for x and y
    x_coords = np.array([transform.c + (i + 0.5) * transform.a for i in range(width)])
    y_coords = np.array([transform.f + (i + 0.5) * transform.e for i in range(height)])

    # Create 2D meshgrid
    xs, ys = np.meshgrid(x_coords, y_coords)

    # Transform to WGS84
    lons, lats = transformer.transform(xs, ys)

    # Save to NetCDF
    ds = xr.Dataset(
        data_vars={
            'lat': (['y', 'x'], lats)
        },
        coords={
            'y': y_coords,
            'x': x_coords
        },
        attrs={
            "description": "Latitude map for LISVAP BaseMap",
            "source": "Generated from canonical grid transform"
        }
    )
    
    ds.to_netcdf(lat_nc)
    print(f'Successfully created {lat_nc}!')

if __name__ == "__main__":
    generate_lat_nc()
