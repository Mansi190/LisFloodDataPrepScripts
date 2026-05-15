import sys
import ee
from lisflood_utils import init_ee

# Initialize Earth Engine
init_ee('ee-mansi-corestack') # assuming default, but let's just let init_ee handle it if project config is there

asset_id = "projects/corestack-datasets/assets/datasets/drainage-line/pan_india_drainage_lines"
try:
    info = ee.data.getAsset(asset_id)
    print("Asset Type:", info['type'])
    if info['type'] == 'IMAGE':
        img = ee.Image(asset_id)
        print("Band names:", img.bandNames().getInfo())
except Exception as e:
    print("Error:", e)
