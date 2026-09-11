import os
import xarray as xr
import sys
import numpy as np

def main():
    print("Starting data loading and synchronization...")

    era5_path = "data/raw/era5/era5_wind.nc"
    copernicus_path = "data/raw/copernicus/ocean_currents.nc"
    output_dir = "data/processed"
    output_path = os.path.join(output_dir, "weddell_sea_combined.nc")
    
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(era5_path):
        print(f"Error: {era5_path} not found.")
        sys.exit(1)
        
    if not os.path.exists(copernicus_path):
        print(f"Error: {copernicus_path} not found.")
        sys.exit(1)

    print("Loading datasets...")
    ds_era5 = xr.open_dataset(era5_path).load()
    ds_cop = xr.open_dataset(copernicus_path).load()

    print("Cleaning coordinates...")
    # Rename ERA5 valid_time to time to match Copernicus
    if 'valid_time' in ds_era5.coords:
        ds_era5 = ds_era5.rename({'valid_time': 'time'})
        
    # Drop confusing ERA5 extra dimensions (sometimes present in CDS downloads)
    if 'expver' in ds_era5.coords:
        # Some ERA5 files have two expvers (1 and 5). We use combine_first to merge them.
        if ds_era5.sizes.get('expver', 0) > 1:
            ds_era5 = ds_era5.sel(expver=1).combine_first(ds_era5.sel(expver=5))
        ds_era5 = ds_era5.drop_vars('expver', errors='ignore')
        
    if 'number' in ds_era5.coords:
        ds_era5 = ds_era5.drop_vars('number', errors='ignore')

    # Drop Copernicus singleton depth dimension (we only downloaded surface, depth=1)
    if 'depth' in ds_cop.coords:
        ds_cop = ds_cop.squeeze('depth', drop=True)

    print("Interpolating Copernicus spatial grid (10km) down to ERA5 grid (25km)...")
    # Spatial interpolation: Copernicus -> ERA5 (to save disk space)
    ds_cop = ds_cop.interp(
        latitude=ds_era5.latitude,
        longitude=ds_era5.longitude,
        method="linear"
    )

    print("Interpolating Copernicus temporal grid (Daily) up to ERA5 grid (6-Hourly)...")
    # Temporal interpolation: Copernicus -> ERA5 (to preserve 6-hourly storm wind vectors)
    ds_cop = ds_cop.interp(
        time=ds_era5.time,
        method="linear"
    )

    print("Merging datasets...")
    # Now that spatial and temporal grids are exactly matched, we can safely merge
    ds_combined = xr.merge([ds_era5, ds_cop])

    print(f"Saving combined dataset to {output_path}...")
    ds_combined.to_netcdf(output_path)
    
    print("Success! Final Dataset Structure:")
    print(ds_combined)

if __name__ == "__main__":
    main()
