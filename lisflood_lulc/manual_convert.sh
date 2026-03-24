#!/bin/bash
# sudo apt install gdal-bin

gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/fracwater.tif ./lisflood_lulc/maps/fracwater.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/fracsealed.tif ./lisflood_lulc/maps/fracsealed.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/fracforest.tif ./lisflood_lulc/maps/fracforest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/fracother.tif ./lisflood_lulc/maps/fracother.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/cropcoef_forest.tif ./lisflood_lulc/maps/cropcoef_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/cropcoef_other.tif ./lisflood_lulc/maps/cropcoef_other.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_NOMINAL ./lisflood_lulc/maps/crgrnum_forest.tif ./lisflood_lulc/maps/crgrnum_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_NOMINAL ./lisflood_lulc/maps/crgrnum_other.tif ./lisflood_lulc/maps/crgrnum_other.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/mannings_forest.tif ./lisflood_lulc/maps/mannings_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/mannings_other.tif ./lisflood_lulc/maps/mannings_other.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/soildep1_forest.tif ./lisflood_lulc/maps/soildep1_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/soildep1_other.tif ./lisflood_lulc/maps/soildep1_other.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/soildep2_forest.tif ./lisflood_lulc/maps/soildep2_forest.map
gdal_translate -of PCRaster -mo PCRASTER_VALUESCALE=VS_SCALAR ./lisflood_lulc/maps/soildep2_other.tif ./lisflood_lulc/maps/soildep2_other.map
