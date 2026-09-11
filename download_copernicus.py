import subprocess
import sys
import os
import logging
from config import START_DATE, END_DATE, BBOX_NORTH, BBOX_WEST, BBOX_SOUTH, BBOX_EAST, RAW_DATA_DIR_COPERNICUS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def download_copernicus():
    os.makedirs(RAW_DATA_DIR_COPERNICUS, exist_ok=True)
    exe_path = os.path.join(sys.prefix, "Scripts", "copernicusmarine.exe")
    dataset_id = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
    output_file = os.path.join(RAW_DATA_DIR_COPERNICUS, "ocean_currents.nc")
    
    logging.info(f"Starting Copernicus Marine download for Ocean Currents (Weddell Sea, {START_DATE} to {END_DATE})...")
    cmd = [
        exe_path, "subset",
        "--dataset-id", dataset_id,
        "--variable", "uo",
        "--variable", "vo",
        "--variable", "siconc",
        "--start-datetime", START_DATE,
        "--end-datetime", END_DATE,
        "--minimum-longitude", str(BBOX_WEST),
        "--maximum-longitude", str(BBOX_EAST),
        "--minimum-latitude", str(BBOX_SOUTH),
        "--maximum-latitude", str(BBOX_NORTH),
        "--minimum-depth", "0",
        "--maximum-depth", "1",
        "--output-filename", output_file,
        "--force-download"
    ]
    
    try:
        subprocess.run(cmd, check=True)
        logging.info(f"Copernicus Marine download complete! File is at {output_file}")
    except subprocess.CalledProcessError as e:
        logging.error("Error during download.")
        logging.error("Did you authenticate? Run 'copernicusmarine login' in the terminal.")
        logging.error(e)

if __name__ == "__main__":
    download_copernicus()
