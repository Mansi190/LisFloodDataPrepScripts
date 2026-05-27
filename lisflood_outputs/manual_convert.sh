#!/bin/bash
# Install GDAL with PCRaster support:
#   macOS : brew install gdal
#   Ubuntu: sudo apt install gdal-bin

gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_BOOLEAN ./lisflood_outputs/maps/area.tif ./lisflood_outputs/maps/area.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_outputs/maps/dem.tif ./lisflood_outputs/maps/dem.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_LDD ./lisflood_outputs/maps/ldd.tif ./lisflood_outputs/maps/ldd.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_outputs/maps/gradient.tif ./lisflood_outputs/maps/gradient.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_outputs/maps/elvstd.tif ./lisflood_outputs/maps/elvstd.map
