#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chan.tif ./output_dataset/maps/chan.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/changrad.tif ./output_dataset/maps/changrad.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chanman.tif ./output_dataset/maps/chanman.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chanleng.tif ./output_dataset/maps/chanleng.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chanbw.tif ./output_dataset/maps/chanbw.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chans.tif ./output_dataset/maps/chans.nc
gdal_translate -of netCDF -co FORMAT=NC4 -co COMPRESS=DEFLATE ./output_dataset/maps/chanbnkf.tif ./output_dataset/maps/chanbnkf.nc
