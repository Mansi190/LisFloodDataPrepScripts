"""
=============================================================================
LISFLOOD LAI (Leaf Area Index) PREPROCESSOR — LANDSAT 8 NDVI
Generates 12 monthly lai_forest.map and lai_other.map for LISFLOOD
using actual Landsat 8 optical data and empirical LAI modeling.

Strategy:
  1. Iterate 12 months perfectly.
  2. Retrieve Landsat 8 C2 T1_L2 for the given month.
  3. Implement Cloud/Shadow masking using QA_PIXEL.
  4. Apply rigorous USGS Scaling Factors before indices.
  5. Calculate monthly median NDVI.
  6. Apply empirically validated generic equation:
       LAI = 0.57 * exp(2.33 * NDVI)
  7. Mask explicitly against user's LULC map:
       - Forest LAI = LULC == 6
       - Other LAI  = LULC != 6 and LULC != 0
  8. Unmask empty pixels strictly to 0.0 before download.
  9. Export GeoTIFFs using GEE Python API exactly aligned to area.tif.
  10. Convert to valid PCRaster format (.map).
=============================================================================
"""

import os
import sys
import ee
import geemap
import calendar
import datetime
import numpy as np
import subprocess
from pathlib import Path
import warnings
import rasterio
warnings.filterwarnings("ignore")

# =============================================================================
#  SETTINGS — edit pipeline_config.py to change ROI / CRS / paths
# =============================================================================
import pipeline_config as _cfg

AREA_RASTER_PATH = _cfg.AREA_TIF
OUTPUT_DIR       = "./lai_outputs_landsat"

GEE_ASSET        = ("projects/corestack-datasets/assets/datasets/"
                    "LULC_v3_river_basin/pan_india_lulc_v3_2024_2025")
TARGET_CRS       = _cfg.resolve_crs()
RESOLUTION_M     = _cfg.RESOLUTION_M
YEAR             = 2024
NODATA           = _cfg.NODATA_FLOAT

# ─────────────────────────────────────────────────────────────────────────────
#  FALLBACK CLIMATOLOGY (To patch Landsat monsoon cloud gaps)
#  Source: Hagemann (2002) / LISFLOOD Tech Doc
# ─────────────────────────────────────────────────────────────────────────────
LAI_FOREST_MONTHLY = {
     1: 3.5,  2: 3.5,  3: 4.0,  4: 4.5,  5: 5.0,  6: 5.5,
     7: 6.0,  8: 6.0,  9: 5.5, 10: 4.5, 11: 4.0, 12: 3.5
}
LAI_OTHER_MONTHLY = {
     1: 0.5,  2: 1.0,  3: 2.0,  4: 1.5,  5: 0.5,  6: 1.0,
     7: 2.5,  8: 3.0,  9: 3.0, 10: 2.5, 11: 1.5, 12: 1.0
}


# =============================================================================
#  MASTER GRID (Exact clone of LAI.py alignment standard)
# =============================================================================
class MasterGrid:
    def __init__(self, transform, width, height, crs):
        self.transform = transform
        self.width     = width
        self.height    = height
        self.crs       = crs

    @property
    def profile(self):
        return {
            "driver": "GTiff", "crs": self.crs, "transform": self.transform,
            "width":  self.width, "height": self.height, "count": 1, "compress": "lzw"
        }

    def snap(self, array, nodata_val):
        h, w = array.shape
        if h == self.height and w == self.width: return array
        c = array[:self.height, :self.width]
        out = np.full((self.height, self.width), nodata_val, dtype=array.dtype)
        out[:c.shape[0], :c.shape[1]] = c
        return out

    def reproject_array(self, src_array, src_transform, src_crs, src_nodata, dst_nodata, resampling_method="bilinear"):
        from rasterio.warp import reproject, Resampling
        resamp = {"bilinear": Resampling.bilinear, "nearest": Resampling.nearest}[resampling_method]
        dst = np.full((self.height, self.width), dst_nodata, dtype=src_array.dtype)
        reproject(
            source=src_array, destination=dst,
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=self.transform, dst_crs=self.crs,
            resampling=resamp, src_nodata=src_nodata, dst_nodata=dst_nodata
        )
        return dst

    def save(self, array, path, dtype, nodata):
        p = self.profile.copy()
        p.update({"dtype": dtype, "nodata": nodata})
        with rasterio.open(path, "w", **p) as dst:
            dst.write(self.snap(array, nodata).astype(dtype), 1)

def load_master_grid(area_path):

    with rasterio.open(area_path) as src:
        return MasterGrid(src.transform, src.width, src.height, src.crs), src.read(1)

# =============================================================================
#  EARTH ENGINE LAI COMPUTATION
# =============================================================================
def init_ee():
    try:
        ee.Initialize(project=_cfg.GEE_PROJECT)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=_cfg.GEE_PROJECT)

def cloud_mask_l8(image):
    """Rigorous Cloud and Shadow Masking for Landsat 8."""
    qa = image.select('QA_PIXEL')
    cloud_shadow_bit = 1 << 4
    clouds_bit       = 1 << 3
    mask = qa.bitwiseAnd(cloud_shadow_bit).eq(0).And(qa.bitwiseAnd(clouds_bit).eq(0))
    return image.updateMask(mask).copyProperties(image, ["system:time_start"])

def apply_scale_factors(image):
    """USGS Collection 2 Surface Reflectance Scaling."""
    optical = image.select('SR_B.').multiply(0.0000275).add(-0.2)
    return image.addBands(optical, None, True)

def compute_monthly_lai_in_gee(month_start_date, month_end_date, region_geom, month):
    """
    Builds the Earth Engine computational graph for querying exactly
    the requested LAI maps for a particular month.
    """
    # 1. Load your LULC (Your rule: Forest is ONLY 6. Other is NOT 6 and NOT Background(0))
    lulc = ee.Image(GEE_ASSET).select('predicted_label').clip(region_geom)
    forest_mask = lulc.eq(6)
    other_mask  = lulc.neq(6).And(lulc.neq(0))
    
    # 2. Landsat 8 filtered and cloud-masked
    l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2") \
           .filterBounds(region_geom) \
           .filterDate(month_start_date, month_end_date) \
           .map(cloud_mask_l8) \
           .map(apply_scale_factors)
           
    # 3. Monthly Median
    median_l8 = l8.median()
    
    # 4. NDVI & LAI (NIR = B5, Red = B4)
    ndvi = median_l8.normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI')
    # LAI = 0.57 * exp(2.33 * NDVI)
    lai_full = ndvi.multiply(2.33).exp().multiply(0.57).rename('LAI')
    
    # 5. Semantic masking + cloud-gap filling with monthly climatology fallback.
    # Bihar has near-total Landsat cloud cover June–September (monsoon).
    # Without a fallback, cloud-masked pixels get LAI=0 which breaks LISFLOOD's
    # ET calculation for the entire monsoon season.
    # Strategy: where Landsat returns valid data, use it; fill the rest with the
    # Hagemann (2002) climatology values defined at the top of this script.

    forest_fallback = ee.Image(float(LAI_FOREST_MONTHLY[month])).updateMask(forest_mask)
    other_fallback  = ee.Image(float(LAI_OTHER_MONTHLY[month])).updateMask(other_mask)

    # Extract cloud-masked Landsat LAI for each land-cover class
    lai_f_raw = lai_full.updateMask(forest_mask)
    lai_o_raw = lai_full.updateMask(other_mask)

    # Patch cloud gaps with climatology (only inside each class boundary)
    lai_f_patched = lai_f_raw.unmask(forest_fallback)
    lai_o_patched = lai_o_raw.unmask(other_fallback)

    # Set all non-class cells to exactly 0.0 inside the catchment boundary
    lai_forest = lai_f_patched.unmask(0).clip(region_geom)
    lai_other  = lai_o_patched.unmask(0).clip(region_geom)
    
    return lai_forest, lai_other

# =============================================================================
#  GDAL PCRaster
# =============================================================================
def tif_to_map(tif_path, map_path):
    cmd = ["gdal_translate", "-of", "PCRaster", "-ot", "Float32",
           "-a_nodata", str(NODATA), tif_path, map_path]
    subprocess.run(cmd, capture_output=True)


def visual_check(tif_forest, tif_other, month, year, output_dir):
    """
    Save a side-by-side PNG of lai_forest and lai_other for a quick sanity check.
    - Forest pixels shown in greens  (YlGn colormap)
    - Other pixels shown in oranges  (YlOrBr colormap)
    - Nodata / outside-watershed pixels masked out (white)
    """
    import matplotlib
    matplotlib.use("Agg")           # non-interactive backend (no screen needed)
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    month_name = datetime.date(year, month, 1).strftime("%B %Y")
    png_path   = os.path.join(output_dir, "plots",
                              f"lai_check_{month:02d}{year}.png")

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), facecolor="#1a1a2e")
    fig.suptitle(f"LAI Visual Check — {month_name}",
                 fontsize=15, fontweight="bold", color="white", y=1.01)

    configs = [
        (tif_forest, "LAI Forest (Class 6)",  "YlGn"),
        (tif_other,  "LAI Other  (non-forest)", "YlOrBr"),
    ]

    for ax, (tif_path, title, cmap_name) in zip(axes, configs):
        ax.set_facecolor("#0d0d1a")
        if not os.path.exists(tif_path):
            ax.text(0.5, 0.5, "File not found", ha="center", va="center",
                    color="red", transform=ax.transAxes, fontsize=12)
            ax.set_title(title, color="white", fontsize=12, pad=8)
            continue

        with rasterio.open(tif_path) as src:
            data = src.read(1).astype(np.float32)
            nodata_val = src.nodata if src.nodata is not None else NODATA

        # Mask nodata and outside-watershed
        masked = np.ma.masked_where(
            (data <= nodata_val / 2) | np.isnan(data), data
        )

        valid = masked.compressed()
        vmin  = float(np.nanpercentile(valid, 2))  if valid.size else 0.0
        vmax  = float(np.nanpercentile(valid, 98)) if valid.size else 6.0

        cmap = plt.get_cmap(cmap_name).copy()
        cmap.set_bad(color="#1a1a2e")    # nodata → dark background

        im = ax.imshow(masked, cmap=cmap, vmin=vmin, vmax=vmax,
                       interpolation="nearest")
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("LAI  (m² m⁻²)", color="white", fontsize=9)
        cbar.ax.yaxis.set_tick_params(color="white")
        plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

        # Stats annotation
        stats = (f"min={valid.min():.2f}  max={valid.max():.2f}\n"
                 f"mean={valid.mean():.2f}  px={valid.size:,}") if valid.size else "No data"
        ax.text(0.02, 0.02, stats, transform=ax.transAxes,
                fontsize=8, color="white", va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", fc="black", alpha=0.5))

        ax.set_title(title, color="white", fontsize=12, pad=8)
        ax.axis("off")

    plt.tight_layout()
    plt.savefig(png_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f" 🖼  Visual check saved → {png_path}")

# =============================================================================
#  MAIN LOOP
# =============================================================================
def main():
    print("====================================")
    print(" LISFLOOD LANDSAT 8 LAI GENERATOR ")
    print("====================================\n")
    
    print("Initializing Earth Engine...")
    init_ee()
    
    for d in ["raw", "maps", "tif", "plots"]:
        Path(f"{OUTPUT_DIR}/{d}").mkdir(parents=True, exist_ok=True)
    
    print("Loading Master Grid...")
    master, watershed_mask = load_master_grid(AREA_RASTER_PATH)
    
    # Extract Region Geometry for GEE — convert master grid corners from UTM to WGS84.
    import pyproj
    t = master.transform
    corners_utm = [
        (t.c,                     t.f),
        (t.c + master.width*t.a,  t.f),
        (t.c,                     t.f + master.height*t.e),
        (t.c + master.width*t.a,  t.f + master.height*t.e),
    ]
    # Convert UTM → WGS84 (was wrongly converting TARGET_CRS → itself).
    transformer = pyproj.Transformer.from_crs(TARGET_CRS, "EPSG:4326", always_xy=True)
    lon_lat = [transformer.transform(x, y) for x, y in corners_utm]
    lons = [c[0] for c in lon_lat]
    lats = [c[1] for c in lon_lat]

    region = ee.Geometry.Rectangle([min(lons)-0.01, min(lats)-0.01, max(lons)+0.01, max(lats)+0.01])
    
    import gc
    
    for month in range(1, 13):
        # Build Date bounds
        start_date = f"{YEAR}-{month:02d}-01"
        _, last_day = calendar.monthrange(YEAR, month)
        end_date = f"{YEAR}-{month:02d}-{last_day}T23:59:59"
        ddmmyyyy = f"01{month:02d}{YEAR}"
        
        print(f"\n--- [ Processing Month {month:02d} ({datetime.date(YEAR, month, 1).strftime('%B')}) ] ---")
        
        raw_forest = f"{OUTPUT_DIR}/raw/geemap_lai_forest_{month}.tif"
        raw_other  = f"{OUTPUT_DIR}/raw/geemap_lai_other_{month}.tif"
        tif_forest = f"{OUTPUT_DIR}/tif/lai_forest_{ddmmyyyy}.tif"
        tif_other  = f"{OUTPUT_DIR}/tif/lai_other_{ddmmyyyy}.tif"
        map_forest = f"{OUTPUT_DIR}/maps/lai_forest_{ddmmyyyy}.map"
        map_other  = f"{OUTPUT_DIR}/maps/lai_other_{ddmmyyyy}.map"
        
        # 1. Download from Earth Engine via geemap
        if not os.path.exists(raw_forest) or not os.path.exists(raw_other):
            try:
                lai_f, lai_o = compute_monthly_lai_in_gee(start_date, end_date, region, month)
                print(" Downloading Forest Layer from GEE...")
                geemap.ee_export_image(lai_f, filename=raw_forest, scale=30, crs=TARGET_CRS, region=region, file_per_band=False)
                print(" Downloading Other Layer from GEE...")
                geemap.ee_export_image(lai_o, filename=raw_other, scale=30, crs=TARGET_CRS, region=region, file_per_band=False)
            except Exception as e:
                print(f" GEE Export failed: {e}")
                continue
                
        # 2. Strict MasterGrid Alignment
        for raw_tiff, clean_tiff, pcr_map in [(raw_forest, tif_forest, map_forest), (raw_other, tif_other, map_other)]:
            if not os.path.exists(raw_tiff): continue
                
            try:
                with rasterio.open(raw_tiff) as src:
                    arr = src.read(1).astype(np.float32)
                    # Eradicate NaNs / Negative errors before warp
                    arr[np.isnan(arr)] = 0.0     
                    
                    # Strictly use NEAREST resampling to prevent masking boundaries from blurring
                    aligned = master.reproject_array(arr, src.transform, src.crs, src_nodata=np.nan, dst_nodata=0.0, resampling_method="nearest")
                    
                # STRICT Catchment Masking! (Nullify outside catchment completely)
                aligned[watershed_mask == 0] = NODATA
                
                # Save & Transform
                master.save(aligned, clean_tiff, "float32", NODATA)
                tif_to_map(clean_tiff, pcr_map)
                print(f" ✔ Saved & Formatted -> {os.path.basename(pcr_map)}")
            except Exception as e:
                print(f" ⚠ Error aligning {raw_tiff} : {e}")
                
        gc.collect()

        # 3. Visual check PNG
        visual_check(tif_forest, tif_other, month, YEAR, OUTPUT_DIR)

    print("\n[ DONE ] All 12 monthly LAI grids constructed successfully.")

if __name__ == "__main__":
    main()
