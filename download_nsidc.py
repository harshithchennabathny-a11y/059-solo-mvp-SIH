import earthaccess
import os
import logging
from config import START_DATE, END_DATE, BBOX, RAW_DATA_DIR_NSIDC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def download_nsidc():
    os.makedirs(RAW_DATA_DIR_NSIDC, exist_ok=True)
    logging.info("Authenticating with NASA Earthdata...")
    earthaccess.login()
    
    logging.info(f"Searching for G02202 v6 for {START_DATE} to {END_DATE} (Weddell Sea)...")
    results = earthaccess.search_data(
        short_name="G02202",
        version="6",
        temporal=(START_DATE, END_DATE),
        bounding_box=tuple(BBOX)  # (west, south, east, north)
    )
    
    if not results:
        logging.warning("No files found!")
        return

    logging.info(f"Found {len(results)} files. Starting download...")
    earthaccess.download(results, RAW_DATA_DIR_NSIDC)
    logging.info(f"NSIDC download complete! Files are in {RAW_DATA_DIR_NSIDC}")

if __name__ == "__main__":
    download_nsidc()
