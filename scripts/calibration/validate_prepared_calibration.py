"""Check prepared data and write settings previews, without running a model."""
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta
import json
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import pcraster as pcr
import xarray as xr
from liscal import config, templates

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT/'Calibration'


def main():
    cfg = config.ConfigCalibration(str(CAL/'settings_calibration.txt'))
    stations = pd.read_csv(CAL/'stations_data.csv').set_index('ObsID')
    links = pd.read_csv(CAL/'stations_links.csv',index_col=0)
    order = [int(x) for x in (CAL/'CatchmentsToProcess.txt').read_text().split()]
    assert set(order) == set(stations.index)
    results = []
    for sid, station in stations.iterrows():
        folder = CAL/'catchments'/str(sid)
        upstream = [int(v) for v in links.loc[sid].dropna()]
        assert all(order.index(uid) < order.index(sid) for uid in upstream)
        pcr.setclone(str(folder/'maps/masksmall.map'))
        mask = pcr.pcr2numpy(pcr.readmap(str(folder/'maps/masksmall.map')),0).astype(bool)
        outlet = pcr.pcr2numpy(pcr.readmap(str(folder/'maps/outletsmall.map')),0)
        assert np.count_nonzero(outlet)==1 and mask[outlet>0].all()
        inlets = pcr.pcr2numpy(pcr.readmap(str(folder/'inflow/inflow_cut.map')),0)
        assert set(np.unique(inlets))-{0} == set(upstream)
        assert mask[inlets>0].all()
        # Every local active cell should route to the selected station.
        ldd = pcr.readmap(str(folder/'maps/ldd.map'))
        catch = pcr.pcr2numpy(pcr.catchment(ldd,pcr.nominal(str(folder/'maps/outletsmall.map'))),0)
        assert (catch[mask]>0).all(), f'Station {sid}: disconnected local drainage'
        with xr.open_dataset(folder/'maps/area.nc') as ds:
            x, y = ds.x.values, ds.y.values
        with xr.open_dataset(folder/'maps/chan.nc') as ds:
            channel = next(v.values for v in ds.data_vars.values() if v.ndim == 2)>0
        assert mask.shape == (len(y),len(x))
        for path in (folder/'maps').glob('*.nc'):
            with xr.open_dataset(path) as ds:
                assert np.array_equal(ds.x,x) and np.array_equal(ds.y,y), str(path)
                for var in ds.data_vars.values():
                    if var.ndim < 2:
                        continue
                    if 'time' in var.dims:
                        sample=var.isel(time=[0,len(var.time)//2,len(var.time)-1]).values
                        assert np.isfinite(sample[:,mask]).all(), str(path)
                    else:
                        valid = mask & channel if path.stem in ['chan','chanbw','chanbnkf','changrad','chanleng','chanman','chans'] else mask
                        assert np.isfinite(var.values[valid]).all(),str(path)
                if path.stem in ['pr','ta','et','es','e0']:
                    expected=pd.date_range('2003-01-01','2020-12-31',freq='D')
                    assert np.array_equal(ds.time.values,expected.values)
        records=pd.read_csv(folder/'station/observations.csv',index_col=0)
        assert len(records)==6210 and records.notna().all().all()
        meta=pd.read_csv(folder/'station/station_data.csv',index_col=0)[str(sid)]
        sub=SimpleNamespace(obsid=sid,path=str(folder),
            gaugeloc=f'{station.LisfloodX} {station.LisfloodY}',inflowflag=str(int(bool(upstream))))
        tpl=templates.LisfloodSettingsTemplate(cfg,sub)
        start=datetime.strptime(meta.Split_date,'%d/%m/%Y %H:%M')-timedelta(days=int(meta.Spinup_days))
        files=tpl.write_template('PREVIEW_ONLY',cfg.prerun_start.strftime('%d/%m/%Y %H:%M'),
            cfg.prerun_end.strftime('%d/%m/%Y %H:%M'),start.strftime('%d/%m/%Y %H:%M'),
            meta.Obs_end,cfg.param_ranges,cfg.param_ranges.DefaultValue.values)
        for file in files:
            tree=ET.parse(file)
            assert not any(re.search(r'%[A-Za-z_]',e.attrib.get('value','')) for e in tree.iter('textvar'))
            flags=[e.attrib['choice'] for e in tree.iter('setoption') if e.attrib.get('name')=='inflow']
            assert flags==[str(int(bool(upstream)))],flags
        assert not list((folder/'out').iterdir()), 'Unexpected run outputs'
        results.append(dict(ObsID=int(sid),inflow_enabled=bool(upstream),upstream=upstream,
                            daily_discharge_values=len(records),checks='PASS'))
    report={'status':'DATA_CHECKS_PASSED_NO_MODEL_RUN','run_order':order,
            'checks':'grid alignment; representative finite map/forcing values; full forcing dates; station data; routing; inlet IDs; XML previews; empty outputs',
            'remaining_review':'gauge placement, calibration settings, optional plotting, Ubuntu environment/paths',
            'stations':results}
    (CAL/'validation_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
