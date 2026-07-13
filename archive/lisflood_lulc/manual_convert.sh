#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_lulc/maps/lulc.tif ./lisflood_lulc/maps/lulc.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_lulc/maps/fracsealed.tif ./lisflood_lulc/maps/fracsealed.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_lulc/maps/fracwater.tif ./lisflood_lulc/maps/fracwater.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_lulc/maps/fracforest.tif ./lisflood_lulc/maps/fracforest.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_lulc/maps/fracother.tif ./lisflood_lulc/maps/fracother.nc
