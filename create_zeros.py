import xarray as xr
import os

area_path = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/lisflood_topography/maps/area.nc"
out_path = "/Users/mansi/Documents/LisFlood/LisFloodDataPrepScripts/lisflood_topography/maps/zeros.nc"

ds = xr.open_dataset(area_path)
var_name = list(ds.data_vars.keys())[0]

ds_zeros = xr.Dataset(
    data_vars={var_name: (ds[var_name].dims, xr.zeros_like(ds[var_name]).values)},
    coords=ds.coords,
    attrs=ds.attrs
)

ds_zeros[var_name] = xr.where(ds[var_name].notnull(), 0.0, ds[var_name])
ds_zeros.to_netcdf(out_path)
print("Created zeros.nc")
