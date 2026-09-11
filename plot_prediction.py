"""
IceCast — Visualize Model Predictions vs Ground Truth
=====================================================

Loads trained ConvLSTM and generates a side-by-side comparison:
- Ground Truth Sea Ice Concentration
- Predicted Sea Ice Concentration
- Absolute Error Map
"""

import os
import torch
import numpy as np
import xarray as xr
import matplotlib.pyplot as plt
from train_model import IceCastModel, load_and_engineer_features, SeaIceDataset

def main():
    model_path = "models/icecast_convlstm_best.pt"
    data_path = "data/processed/weddell_sea_combined.nc"
    output_img = "prediction_comparison.png"

    if not os.path.exists(model_path):
        print(f"Error: {model_path} not found.")
        return

    print("Loading model checkpoint...")
    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    config = checkpoint["config"]
    scaler_stats = checkpoint["scaler_stats"]

    # Load data with matching downsample
    data, scaler_stats = load_and_engineer_features(data_path, downsample=config["downsample_factor"])
    
    # Open dataset for coordinates
    with xr.open_dataset(data_path) as ds:
        # Determine lat/lon coordinate names
        lat_name = 'lat' if 'lat' in ds.coords else 'latitude'
        lon_name = 'lon' if 'lon' in ds.coords else 'longitude'
        lat = ds[lat_name].values[::config["downsample_factor"]]
        lon = ds[lon_name].values[::config["downsample_factor"]]

    # Test split
    n_time = data.shape[0]
    split_idx = int(n_time * config["train_ratio"])
    test_data = data[split_idx:]

    dataset = SeaIceDataset(test_data, config["lookback"], config["forecast_horizon"])
    
    # Pick a sample from the test set with significant sea ice presence
    sample_idx = min(100, len(dataset) - 1)
    x_input, y_target = dataset[sample_idx]

    # Model inference
    model = IceCastModel(
        n_features=data.shape[-1],
        hidden_channels=config["convlstm_filters"],
        kernel_size=config["kernel_size"],
        dropout=0.0
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    with torch.no_grad():
        x_tensor = x_input.unsqueeze(0)  # (1, lookback, H, W, C)
        pred_norm = model(x_tensor).squeeze().numpy()  # (H, W)

    # Denormalize target and prediction
    siconc_mean = scaler_stats['siconc']['mean']
    siconc_std = scaler_stats['siconc']['std']

    pred = np.clip(pred_norm * siconc_std + siconc_mean, 0.0, 1.0)
    actual = np.clip(y_target.squeeze().numpy() * siconc_std + siconc_mean, 0.0, 1.0)
    diff = np.abs(actual - pred)

    # Plot 3-panel comparison
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    # Actual
    im0 = axes[0].pcolormesh(lon, lat, actual, cmap='Blues_r', vmin=0, vmax=1, shading='auto')
    axes[0].set_title("Ground Truth (Actual Sea Ice)", fontsize=13, fontweight='bold')
    axes[0].set_xlabel("Longitude")
    axes[0].set_ylabel("Latitude")
    fig.colorbar(im0, ax=axes[0], label="Ice Concentration (0-1)")

    # Prediction
    im1 = axes[1].pcolormesh(lon, lat, pred, cmap='Blues_r', vmin=0, vmax=1, shading='auto')
    axes[1].set_title("IceCast ConvLSTM Prediction", fontsize=13, fontweight='bold')
    axes[1].set_xlabel("Longitude")
    fig.colorbar(im1, ax=axes[1], label="Ice Concentration (0-1)")

    # Absolute Error
    im2 = axes[2].pcolormesh(lon, lat, diff, cmap='Reds', vmin=0, vmax=0.1, shading='auto')
    axes[2].set_title(f"Absolute Error (Mean: {diff.mean():.4f})", fontsize=13, fontweight='bold')
    axes[2].set_xlabel("Longitude")
    fig.colorbar(im2, ax=axes[2], label="Absolute Difference")

    plt.suptitle("IceCast Model Validation — Weddell Sea Forecast", fontsize=15, y=1.02)
    plt.tight_layout()
    plt.savefig(output_img, dpi=150, bbox_inches='tight')
    print(f"Saved prediction visual to {output_img}")

if __name__ == "__main__":
    main()
