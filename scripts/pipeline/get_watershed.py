import ee
import geemap

# 1. Connect to Google Earth Engine using your project
ee.Initialize(project='gssha-480613')

# 2. Load the giant cloud dataset of all watersheds
all_watersheds = ee.FeatureCollection('projects/corestack-datasets/assets/datasets/hydrological_boundaries/watersheds')

# 3. Tell Earth Engine which one you want by giving it a point inside your area.
# FORMAT: [Longitude, Latitude]
my_point = ee.Geometry.Point([74.60222, 16.68444])  # <-- [Longitude, Latitude] (Notice 87 comes first!)

# 4. Filter the giant dataset to only keep the watershed that touches your point
my_watershed = all_watersheds.filterBounds(my_point)
print(my_watershed.getInfo())

# 5. Download it directly to your laptop as a Shapefile!
print("Downloading...")
geemap.ee_export_vector(my_watershed, filename="./ShapeFile/Watershed.shp")
print("Done! You can now use this in your pipeline_config.py")