#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/lulc.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/lulc.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracsealed.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracsealed.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracwater.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracwater.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracforest.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracforest.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracother.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/fraction/fracother.nc
