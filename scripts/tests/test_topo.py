import xarray as xr
import numpy as np
import glob

maps = glob.glob('inputs/maps/*.nc')
for m in maps:
    ds = xr.open_dataset(m)
    v = ds['Band1'].values
    v = v[~np.isnan(v)]
    if len(v) > 0:
        print(f"{m.split('/')[-1]}: min={np.min(v)}, max={np.max(v)}, zeros={np.sum(v==0)}")

