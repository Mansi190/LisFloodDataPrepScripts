#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chan.tif ./lisflood_channels/maps/chan.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/changrad.tif ./lisflood_channels/maps/changrad.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chanman.tif ./lisflood_channels/maps/chanman.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chanleng.tif ./lisflood_channels/maps/chanleng.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chanbw.tif ./lisflood_channels/maps/chanbw.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chans.tif ./lisflood_channels/maps/chans.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./lisflood_channels/maps/chanbnkf.tif ./lisflood_channels/maps/chanbnkf.nc
