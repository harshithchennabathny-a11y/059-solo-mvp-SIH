import cdsapi
import os
import logging
from config import START_DATE, END_DATE, BBOX_NORTH, BBOX_WEST, BBOX_SOUTH, BBOX_EAST, RAW_DATA_DIR_ERA5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def download_era5():
    os.makedirs(RAW_DATA_DIR_ERA5, exist_ok=True)
    output_file = os.path.join(RAW_DATA_DIR_ERA5, 'era5_wind.nc')
    
    logging.info("Connecting to CDS API...")
    try:
        c = cdsapi.Client()
        
        logging.info(f"Requesting ERA5 10m wind data (Weddell Sea, {START_DATE} to {END_DATE}, 6-hourly).")
        logging.info(f"NOTE: CDS API requests are queued on their servers and can take a long time to start.")
        
        c.retrieve(
            'reanalysis-era5-single-levels',
            {
                'product_type': 'reanalysis',
                'variable': [
                    '10m_u_component_of_wind',
                    '10m_v_component_of_wind',
                ],
                'year': ['2023', '2024'],
                'month': ['10', '11', '12', '01', '02'],
                'day': [f'{d:02d}' for d in range(1, 32)],
                'time': ['00:00', '06:00', '12:00', '18:00'],
                'area': [BBOX_NORTH, BBOX_WEST, BBOX_SOUTH, BBOX_EAST],  # North, West, South, East
                'format': 'netcdf',
            },
            output_file
        )
        logging.info(f"ERA5 download complete! File is at {output_file}")
    except Exception as e:
        logging.error(f"Error during ERA5 download: {e}")
        logging.error("Ensure your ~/.cdsapirc file is configured correctly.")

if __name__ == "__main__":
    download_era5()
