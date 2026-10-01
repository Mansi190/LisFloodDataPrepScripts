#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chan.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chan.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/changrad.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/changrad.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanman.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanman.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanleng.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanleng.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanbw.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanbw.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chans.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chans.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanbnkf.tif /Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/inputs/maps/chanbnkf.nc
