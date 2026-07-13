The following files/folders are included:

[Item 1 'ghsa_metadata_wat.txt']       : complete station metadata, all 1702 stations, pipe(|) delimited

[Item 2 'ghsa_hydromet_wat_ann.txt']   : by station, hydrometeorological time series for stations in Groups 1, 2 and 3, annual, pipe(|) delimited
[Item 3 'ghsa_hydromet_wat_mon.txt']   : by station, hydrometeorological time series for stations in Groups 1, 2 and 3, monthly, pipe(|) delimited

[Item 4 'ghsa_hydromet_dist_ann.txt']  : by district, hydrometeorological time series, annual, pipe(|) delimited
[Item 5 'ghsa_hydromet_dist_mon.txt']  : by district, hydrometeorological time series, monthly, pipe(|) delimited

[Item 6 'ghsa_hydromet_basSt_ann.txt'] : by basinState, hydrometeorological time series, annual, pipe(|) delimited
[Item 7 'ghsa_hydromet_basSt_mon.txt'] : by basinState, hydrometeorological time series, monthly, pipe(|) delimited

[Item 8 'ghsa_composite_basins']       : one shapefile of GHSA composite basins
[Item 9 'ghsa_districts']              : one shapefile of GHSA districts
[Item 10 'ghsa_basinStates']           : one shapefile of GHSA basin states

[Item 11 folder 'by_station']          : shapefiles of delineated catchment boundaries for stations in Groups 1, 2 and 3 (1,224 stations)

[Item 12 folder 'pdfs']                : PDF files of station maps, annual time series charts for stations in Groups 1, 2 and 3 (one PDF per composite basin)
                                       : annual time series charts for districts and basin states

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 1 'ghsa_metadata_wat.txt']

ghsa_stn          : Unique ID used to identify a station, 13 characters long, GHSA
riv_basin         : Complete name of the River Basin
country           : Country
agency            : Source water agency or database
state             : State associated with the station (India stations only)
agency_id         : Station ID used by the source agency
site_name         : Name of the station
district          : District associated with the station (based on source agency info)
site_type1        : Purpose of monitoring station, a combination of HO (Hydrological Observatory) and/or FF (Flood Forecast site)
agency_area       : Catchment drainage area (sq km) from source agency
river             : River/tributary name from source agency
site_type2        : Type of monitoring station, a combination of G (Gauge), D (Discharge), S (Sediment), Q (Quality)
agency_lat        : Latitude of the station (decimal degrees), from source agency
agency_lon        : Longitude of the station (decimal degrees), from source agency
basinShort        : Composite river basin name (short form), GHSA
basinCode         : Three character ID used to identify composite river basins, GHSA
hydrosh_pf        : HydroSHEDS PF-12 watershed number
ghsa_locchk       : Location check performed by GHSA
ghsa_rivchk       : River name check performed by GHSA
ghsa_lmark        : Description of the landmark identified by GHSA
ghsa_lmark_lat    : Latitude of the GHSA landmark (decimal degrees)
ghsa_lmark_lon    : Longitude of the GHSA landmark (decimal degrees)
ghsa_lmark_dist   : Distance of GHSA landmark from original station (km)
ghsa_reloc        : Distance of GHSA relocated station from original station (km)
ghsa_lat          : Latitude of the GHSA relocated station (decimal degrees)
ghsa_lon          : Longitude of the GHSA relocated station (decimal degrees)
ghsa_area         : Catchment drainage area (sq km), GHSA
hydro_area        : Catchment drainage area (sq km), HydroSHEDS
merit_lat         : Latitude of the MERIT relocated station (decimal degrees)
merit_lon         : Longitude of the MERIT relocated station (decimal degrees)
merit_area        : Catchment drainage area (sq km), MERIT
disc_agency       : Discrepancy (fraction) of GHSA drainage area relative to source agency
disc_merit        : Discrepancy (fraction) of GHSA drainage area relative to MERIT
ghsa_netchk       : Adequacy of river network, HydroSHEDS
merit_netchk      : Adequacy of river network, MERIT
qc1               : Quality check 1 (P or F)
qc2               : Quality check 2 (P or F)
qc3               : Quality check 3 (P or F)
qc4               : Quality check 4 (P or F)
qc5               : Quality check 5 (P or F)
qc6               : Quality check 6 (P or F)
ghsa_group        : GHSA assigned group (G1 or G2 or G3 or G4)

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 2 'ghsa_hydromet_wat_ann.txt']

ghsa_stn          : Unique ID used to identify a station, 13 characters long, GHSA
wyr               : Water Year
prec_era          : Total precpitation from ERA5-Land, MCM/year
runo_era          : Total runoff from ERA5-Land, MCM/year
evap_era          : Total Evap from ERA5-Land, MCM/year
evapC_era         : Total Evap for Cropland, from ERA5-Land, MCM/year
evapI_era         : Total Evap for Irrigated Cropland, from ERA5-Land, MCM/year
swe_era           : SWE from ERA5-Land, MCM/year
snowc_era         : Snow cover fraction from ERA5-Land, fraction
soilm3L_era       : Root zone soil moisture (0 - 100 cm) from ERA5-Land, fraction
soilm1L_era       : Surface soil moisture (0 - 10 cm) from ERA5-Land, fraction
prec_fldas        : Total precpitation from FLDAS, MCM/year
runo_fldas        : Total runoff from FLDAS, MCM/year
evap_fldas        : Total Evap from FLDAS, MCM/year
evapC_fldas       : Total Evap for Cropland, from FLDAS, MCM/year
evapI_fldas       : Total Evap for Irrigated Cropland, from FLDAS, MCM/year
swe_fldas         : SWE from FLDAS, MCM/year
snowc_fldas       : Snow cover fraction from FLDAS, fraction
soilm3L_fldas     : Root zone soil moisture (0 - 100 cm) from FLDAS, fraction
soilm1L_fldas     : Surface soil moisture (0 - 10 cm) from FLDAS, fraction
prec_imd          : Total precpitation from IMD-CRU, MCM/year
evap_gleam        : Total Evap from GLEAM, MCM/year
evapC_gleam       : Total Evap for Cropland, from GLEAM, MCM/year
evapI_gleam       : Total Evap for Irrigated Cropland, from GLEAM, MCM/year
snowc_modis       : Snow cover fraction from MODIS, fraction
swe_utrecht       : SWE from Utrecht, MCM/year
dtws_gldas        : Delta TWS from GLDAS, MCM/year
dtws_csr          : Delta TWS from GRACE/CSR, MCM/year
ndvi_gimms        : NDVI from GIMMS (unitless)
ndviC_gimms       : NDVI for Cropland, from GIMMS (unitless)
ndviC_gimms_area  : Area contributing to ndviC_gimms (sq km), used for spatial aggregation
sm_esacci         : Surface soil moisture from ESACCI
days_tot          : total number of days
days_avail        : Number of daily observations of available streamflow
flow_mcm_obs      : Total streamflow (raw or unadjusted), MCM/year
flow_mcm_tot      : Total streamflow (adjusted for missing days), MCM/year
lulc_urbanF       : HILDA land use and land cover, fraction urban, 0-1
lulc_cropF        : HILDA land use and land cover, fraction crop, 0-1
lulc_forestF      : HILDA land use and land cover, fraction forest, 0-1
lulc_pastureF     : HILDA land use and land cover, fraction pasture, grass/shrubland, 0-1
lulc_otherF       : HILDA land use and land cover, fraction other, 0-1

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 3 'ghsa_hydromet_wat_mon.txt']

ghsa_stn          : Unique ID used to identify a station, 13 characters long, GHSA
wyr               : Water Year
cmon              : Calendar month
prec_era          : Total precpitation from ERA5-Land, MCM/month
runo_era          : Total runoff from ERA5-Land, MCM/month
evap_era          : Total Evap from ERA5-Land, MCM/month
evapC_era         : Total Evap for Cropland, from ERA5-Land, MCM/month
evapI_era         : Total Evap for Irrigated Cropland, from ERA5-Land, MCM/month
swe_era           : SWE from ERA5-Land, MCM/month
snowc_era         : Snow cover fraction from ERA5-Land, fraction
soilm3L_era       : Root zone soil moisture (0 - 100 cm) from ERA5-Land, fraction
soilm1L_era       : Surface soil moisture (0 - 10 cm) from ERA5-Land, fraction
prec_fldas        : Total precpitation from FLDAS, MCM/month
runo_fldas        : Total runoff from FLDAS, MCM/month
evap_fldas        : Total Evap from FLDAS, MCM/month
evapC_fldas       : Total Evap for Cropland, from FLDAS, MCM/month
evapI_fldas       : Total Evap for Irrigated Cropland, from FLDAS, MCM/month
swe_fldas         : SWE from FLDAS, MCM/month
snowc_fldas       : Snow cover fraction from FLDAS, fraction
soilm3L_fldas     : Root zone soil moisture (0 - 100 cm) from FLDAS, fraction
soilm1L_fldas     : Surface soil moisture (0 - 10 cm) from FLDAS, fraction
prec_imd          : Total precpitation from IMD-CRU, MCM/month
evap_gleam        : Total Evap from GLEAM, MCM/month
evapC_gleam       : Total Evap for Cropland, from GLEAM, MCM/month
evapI_gleam       : Total Evap for Irrigated Cropland, from GLEAM, MCM/month
snowc_modis       : Snow cover fraction from MODIS, fraction
swe_utrecht       : SWE from Utrecht, MCM/month
dtws_gldas        : Delta TWS from GLDAS, MCM/month
dtws_csr          : Delta TWS from GRACE/CSR, MCM/month
ndvi_gimms        : NDVI from GIMMS (unitless)
ndviC_gimms       : NDVI for Cropland, from GIMMS (unitless)
ndviC_gimms_area  : Area contributing to ndviC_gimms (sq km), used for spatial aggregation
sm_esacci         : Surface soil moisture from ESACCI
days_tot          : total number of days
days_avail        : Number of daily observations of available streamflow
flow_mcm_obs      : Total streamflow (raw or unadjusted), MCM/month
flow_mcm_tot      : Total streamflow (adjusted for missing days), MCM/month

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 4 'ghsa_hydromet_dist_ann.txt']

ghsa_dist  : distict code used within GHSA
Rest of the fields are the same as 'ghsa_hydromet_wat_ann.txt'

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 5 'ghsa_hydromet_dist_mon.txt']

ghsa_dist  : distict code used within GHSA
Rest of the fields are the same as 'ghsa_hydromet_wat_mon.txt'

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 6 'ghsa_hydromet_basSt_ann.txt']

ghsa_basSt : basin state code used within GHSA
Rest of the fields are the same as 'ghsa_hydromet_wat_ann.txt'

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 7 'ghsa_hydromet_basSt_mon.txt']

ghsa_basSt : basin state code used within GHSA
Rest of the fields are the same as 'ghsa_hydromet_wat_mon.txt'

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 8 'ghsa_composite_basins']

basinCode  : three character ID used to identify composite river basins, GHSA
basinLong  : Composite river basin name (long form), GHSA
basinShort : Composite river basin name (short form), GHSA

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 9 'ghsa_districts']

Cntry      : three character country code
stName     : state name (short form)
stCode     : state code 
ghsa_dist  : distict code used within GHSA
distName   : district name (revised)          
distOrig   : district name (original)
stNameLong : state name (long form)
distArea   : area of the district in sq. km

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 10 'ghsa_basinStates']

Cntry      : three character country code
stCode     : state code
basinCode  : three character ID used to identify composite river basins, GHSA
basinShort : Composite river basin name (short form), GHSA
ghsa_basSt : basin state code used within GHSA
basStArea  : area of the basin state in sq. km


#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 11 folder 'by_station']

catchment boundary : "hybas_as_lev12_v1c_xx-yyyy_zzzzz"
shapefiles of delineated catchment boundaries for stations in Groups 1, 2 and 3 (1,224 stations)

where 'xx-yyyy_zzzzz' is the 13 character GHSA station ID ('ghsa_stn')

#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_#_
[Item 12 folder 'pdfs']

station maps               : "ghsa_wat_map_BasinName.pdf"
    where 'BasinName' is the name of the GHSA composite river basin name ('basinLong' in 'ghsa_composite_basins')
	
annual time series charts  : by station, "ghsa_wat_ts_ann_BasinName.pdf"
    where 'BasinName' is the name of the GHSA composite river basin name ('basinLong' in 'ghsa_composite_basins')

annual time series charts  : by district, "ghsa_dist_ts_ann_Cntry.pdf"
    where 'Cntry' is the three letter country code
	
annual time series charts  : by basinState, "ghsa_basSt_ts_ann.pdf"

