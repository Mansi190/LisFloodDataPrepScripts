#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/fraction/lulc.tif ./output_dataset/maps/fraction/lulc.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/fraction/fracsealed.tif ./output_dataset/maps/fraction/fracsealed.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/fraction/fracwater.tif ./output_dataset/maps/fraction/fracwater.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/fraction/fracforest.tif ./output_dataset/maps/fraction/fracforest.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/fraction/fracother.tif ./output_dataset/maps/fraction/fracother.nc
