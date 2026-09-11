import xarray as xr
import matplotlib.pyplot as plt
import os
import numpy as np

def main():
    file_path = "data/processed/weddell_sea_combined.nc"
    output_path = "sample_sea_ice_combined.png"
    
    print(f"Loading {file_path}...")
    ds = xr.open_dataset(file_path)
    
    # Pick a specific timestamp inside our valid 5-month window
    sample_slice = ds.sel(time="2023-10-02T12:00:00", method="nearest")
    
    plt.figure(figsize=(12, 8))
    
    # Plot Sea Ice Concentration as background
    plot = sample_slice['siconc'].plot(cmap='Blues_r', add_colorbar=True, alpha=0.8)
    plot.colorbar.set_label("Sea Ice Concentration")
    
    # Extract coordinates
    lon = sample_slice['longitude'].values
    lat = sample_slice['latitude'].values
    
    # We need a meshgrid to plot vectors
    X, Y = np.meshgrid(lon, lat)
    
    # Quiver plots can get very dense, so we stride the data (plot every Nth point)
    stride = 10
    
    # 1. Plot Ocean Currents (uo, vo) in Red
    plt.quiver(X[::stride, ::stride], Y[::stride, ::stride], 
               sample_slice['uo'].values[::stride, ::stride], 
               sample_slice['vo'].values[::stride, ::stride], 
               color='red', scale=5, width=0.002, headwidth=3, label="Ocean Currents")
               
    # 2. Plot 10m Winds (u10, v10) in Black (scaled differently because winds are much faster than currents)
    plt.quiver(X[::stride, ::stride], Y[::stride, ::stride], 
               sample_slice['u10'].values[::stride, ::stride], 
               sample_slice['v10'].values[::stride, ::stride], 
               color='black', scale=200, width=0.002, headwidth=3, label="10m Wind")
    
    plt.title("Weddell Sea - Sea Ice, Currents, and Wind\nDate: " + str(sample_slice.time.values)[:10])
    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.legend(loc="upper right")
    
    plt.tight_layout()
    plt.savefig(output_path)
    print(f"Saved visual plot to {output_path}")

if __name__ == "__main__":
    main()
