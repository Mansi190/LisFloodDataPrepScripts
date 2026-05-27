#!/bin/bash
# Run this if gdal_translate was not found during script execution
# sudo apt install gdal-bin

gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lai/maps/lai_forest.tif ./lisflood_lai/maps/lai_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lai/maps/lai_other.tif ./lisflood_lai/maps/lai_other.map
