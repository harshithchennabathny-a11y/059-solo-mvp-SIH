# config.py

# Time Window
START_DATE = "2023-10-01"
END_DATE = "2024-02-28"

# Bounding Box (Weddell Sea)
# [west, south, east, north]
BBOX = [-60, -78, -20, -60]

# Individual bounding box components for APIs that need them separate
BBOX_WEST = BBOX[0]
BBOX_SOUTH = BBOX[1]
BBOX_EAST = BBOX[2]
BBOX_NORTH = BBOX[3]

# Directories
RAW_DATA_DIR_ERA5 = "./data/raw/era5"
RAW_DATA_DIR_COPERNICUS = "./data/raw/copernicus"
RAW_DATA_DIR_NSIDC = "./data/raw/nsidc"
PROCESSED_DATA_DIR = "./data/processed"
