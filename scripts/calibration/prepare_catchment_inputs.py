"""Crop inputs after CAL_1–3 and extract station records (no model execution).

Run in the lisflood environment, with the sibling lisflood-calibration on PYTHONPATH
and its PCRaster executables on PATH. NetCDF forcing is copied in 30-day chunks.
"""
from pathlib import Path
import json
import os
import subprocess
import sys

import netCDF4
import numpy as np
import pandas as pd
import pcraster as pcr
import xarray as xr

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "Calibration"


def crop_nc(source, target, ys, xs, full_x, full_y):
    with netCDF4.Dataset(source) as src:
        if not (np.array_equal(src['x'][:], full_x) and np.array_equal(src['y'][:], full_y)):
            raise ValueError(f"Source not aligned to master grid: {source}")
        with netCDF4.Dataset(target, 'w') as dst:
            dst.setncatts({a: src.getncattr(a) for a in src.ncattrs()})
            for name, dim in src.dimensions.items():
                length = ys.stop-ys.start if name == 'y' else xs.stop-xs.start if name == 'x' else len(dim)
                dst.createDimension(name, length)
            for name, var in src.variables.items():
                opts = {}
                if '_FillValue' in var.ncattrs():
                    opts['fill_value'] = var.getncattr('_FillValue')
                if var.ndim >= 2:
                    opts.update(zlib=True, complevel=1)
                    opts['chunksizes'] = tuple(1 if d == 'time' else len(dst.dimensions[d]) for d in var.dimensions)
                out = dst.createVariable(name, var.dtype, var.dimensions, **opts)
                out.setncatts({a: var.getncattr(a) for a in var.ncattrs() if a != '_FillValue'})
                # Coordinate values are authoritative; update GDAL metadata if supplied.
                if 'GeoTransform' in out.ncattrs():
                    out.GeoTransform = f'{full_x[xs.start]-150} 300 0 {full_y[ys.start]+150} 0 -300'
                index = [ys if d == 'y' else xs if d == 'x' else slice(None) for d in var.dimensions]
                if 'time' in var.dimensions:
                    axis = var.dimensions.index('time')
                    for start in range(0, len(src.dimensions['time']), 30):
                        block = slice(start, start+30)
                        index[axis] = block
                        dest = [slice(None)] * var.ndim
                        dest[axis] = block
                        out[tuple(dest)] = var[tuple(index)]
                else:
                    out[:] = var[tuple(index)] if var.ndim else var[...]


def main():
    stations = pd.read_csv(CAL/'stations_data.csv').set_index('ObsID')
    links = pd.read_csv(CAL/'stations_links.csv', index_col=0)
    with xr.open_dataset(ROOT/'inputs/maps/area.nc') as ds:
        full_x, full_y = ds.x.values, ds.y.values
    pcr.setclone(str(ROOT/'inputs/maps/ldd.map'))
    ldd = pcr.readmap(str(ROOT/'inputs/maps/ldd.map'))
    ldd_arr = pcr.pcr2numpy(ldd, 255)
    uparea = pcr.pcr2numpy(pcr.accuflux(ldd, pcr.scalar(90000)), np.nan)
    files = [p for p in (ROOT/'inputs/maps').rglob('*.nc') if p.name not in ('dem_30m.nc', 'ldd.nc')]
    files += list((ROOT/'inputs/lai').rglob('*.nc'))
    files += [ROOT/'inputs/meteo'/f'{v}.nc' for v in ['pr', 'ta', 'e0', 'es', 'et']]
    manifest = []
    for sid in stations.index:
        folder = CAL/'catchments'/str(sid)
        maps = folder/'maps'
        pcr.setclone(str(ROOT/'inputs/maps/ldd.map'))
        mask = pcr.pcr2numpy(pcr.readmap(str(maps/'mask.map')), 0).astype(bool)
        rr, cc = np.where(mask)
        ys, xs = slice(rr.min(), rr.max()+1), slice(cc.min(), cc.max()+1)
        print(f'Station {sid}: crop {ys.stop-ys.start} x {xs.stop-xs.start}; {mask.sum()*0.09:.2f} km² interstation area', flush=True)
        for source in files:
            crop_nc(source, maps/source.name, ys, xs, full_x, full_y)
        pcr.setclone(str(maps/'masksmall.map'))
        cut_ldd = np.where(mask[ys,xs], ldd_arr[ys,xs], 255).astype('uint8')
        repaired = pcr.lddrepair(pcr.numpy2pcr(pcr.Ldd, cut_ldd, 255))
        pcr.report(repaired, str(maps/'ldd.map'))
        coords = {'y': full_y[ys], 'x': full_x[xs]}
        def write_map(name, array, units):
            xr.DataArray(array, dims=('y','x'), coords=coords, name='Band1',
                         attrs={'units': units}).to_dataset().to_netcdf(maps/name)
        write_map('ldd.nc', pcr.pcr2numpy(repaired, np.uint8(255)), '1')
        write_map('pixarea.nc', np.where(mask[ys,xs],90000.,np.nan), 'm2')
        write_map('upArea.nc', uparea[ys,xs], 'm2')
        with xr.open_dataset(maps/'dem_300m.nc') as ds:
            ds.load().to_netcdf(maps/'elv.nc')
        # Preserve upstream ObsIDs: LISFLOOD reads those IDs from the TSS header
        # and maps them to column positions internally (inflow.py).
        upstream = [int(v) for v in links.loc[sid].dropna()]
        pcr.setclone(str(ROOT/'inputs/maps/ldd.map'))
        original = pcr.pcr2numpy(pcr.readmap(str(CAL/'catchments/inlets.map')), 0)
        original = np.where(mask, original, 0)
        remapped = np.zeros(original.shape, dtype=np.int32)
        for uid in upstream:
            if np.count_nonzero(original == uid) != 1:
                raise ValueError(f'Station {sid}: expected one inlet for upstream {uid}')
            remapped[original == uid] = uid
        pcr.report(pcr.numpy2pcr(pcr.Nominal, remapped, -9999), str(folder/'inflow/inflow.map'))
        pcr.setclone(str(maps/'masksmall.map'))
        pcr.report(pcr.numpy2pcr(pcr.Nominal, np.ascontiguousarray(remapped[ys,xs]), -9999), str(folder/'inflow/inflow_cut.map'))
        (folder/'settings').mkdir(exist_ok=True)
        with (CAL/'logs'/f'prep_CAL_5_station_{sid}.log').open('w') as log:
            subprocess.run([sys.executable, str(ROOT.parent/'lisflood-calibration/bin/CAL_5_EXTRACT_STATION.py'),
                            str(CAL/'settings_calibration.txt'),str(sid)],stdout=log,stderr=subprocess.STDOUT,check=True)
        manifest.append(dict(ObsID=int(sid), gauge_id=stations.loc[sid,'GaugeID'],
                             upstream_stations=upstream, interstation_area_km2=round(mask.sum()*.09,2),
                             rows=int(ys.stop-ys.start), columns=int(xs.stop-xs.start)))
    (CAL/'preparation_manifest.json').write_text(json.dumps({'status':'PREPARED_NOT_STARTED',
        'placement':'PROVISIONAL; see FOLLOW_UP.md','stations':manifest},indent=2)+'\n')
    print('Input cropping and station extraction complete. No calibration or model run executed.',flush=True)


if __name__ == '__main__':
    main()
