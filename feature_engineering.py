"""
Feature Engineering Pipeline for Weddell Sea Ice Prediction.

Takes the combined NetCDF dataset and produces ML-ready features:
- Derived physical quantities (wind speed, wind direction, current speed)
- Temporal features (day of year, ice trend over past N days)
- NaN handling (land mask)
- Normalization (StandardScaler per variable)
- Sliding window sequences for time-series prediction

Output: NumPy arrays saved to data/processed/features/ ready for model training.
"""

import xarray as xr
import numpy as np
import os
import logging
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
INPUT_FILE = "data/processed/weddell_sea_combined.nc"
OUTPUT_DIR = "data/processed/features"
LOOKBACK_DAYS = 7       # Use the past 7 time steps as input sequence
FORECAST_HORIZON = 1    # Predict 1 time step ahead
TRAIN_SPLIT_DATE = "2024-10-01"  # Train on everything before this, test on the rest


def load_dataset(path):
    """Load the combined NetCDF dataset."""
    logging.info(f"Loading dataset from {path}...")
    ds = xr.open_dataset(path)
    logging.info(f"Dataset loaded: {ds.sizes['time']} time steps, "
                 f"{ds.sizes['latitude']}x{ds.sizes['longitude']} grid, "
                 f"Variables: {list(ds.data_vars)}")
    return ds


def add_derived_features(ds):
    """
    Compute physically meaningful derived variables from raw data.
    
    - wind_speed: magnitude of 10m wind vector
    - wind_dir: direction of wind (radians)
    - current_speed: magnitude of ocean current vector
    - current_dir: direction of ocean current (radians)
    """
    logging.info("Computing derived features...")

    # Wind speed and direction
    ds['wind_speed'] = np.sqrt(ds['u10']**2 + ds['v10']**2)
    ds['wind_dir'] = np.arctan2(ds['v10'], ds['u10'])

    # Ocean current speed and direction
    ds['current_speed'] = np.sqrt(ds['uo']**2 + ds['vo']**2)
    ds['current_dir'] = np.arctan2(ds['vo'], ds['uo'])

    logging.info(f"Added 4 derived features. Total variables: {list(ds.data_vars)}")
    return ds


def add_temporal_features(ds):
    """
    Add time-based features that capture seasonal cycles.
    
    - day_of_year_sin / day_of_year_cos: cyclical encoding so Dec 31 is close to Jan 1
    """
    logging.info("Computing temporal features...")

    day_of_year = ds.time.dt.dayofyear.values
    # Cyclical encoding so the model understands that day 365 ≈ day 1
    sin_vals = np.sin(2 * np.pi * day_of_year / 365.25)
    cos_vals = np.cos(2 * np.pi * day_of_year / 365.25)

    # Broadcast to full grid shape (time, lat, lon)
    nlat = ds.sizes['latitude']
    nlon = ds.sizes['longitude']

    ds['day_sin'] = xr.DataArray(
        np.broadcast_to(sin_vals[:, None, None], (len(day_of_year), nlat, nlon)),
        dims=['time', 'latitude', 'longitude']
    )
    ds['day_cos'] = xr.DataArray(
        np.broadcast_to(cos_vals[:, None, None], (len(day_of_year), nlat, nlon)),
        dims=['time', 'latitude', 'longitude']
    )

    logging.info("Added day_sin, day_cos temporal features.")
    return ds


def handle_nans(data_array):
    """
    Replace NaN values with 0. 
    NaNs appear over land (no ocean data) and at grid edges.
    For a production model we'd use a proper land mask; 
    for the MVP, filling with 0 is sufficient.
    """
    nan_count = int(np.isnan(data_array).sum())
    if nan_count > 0:
        logging.info(f"  Filling {nan_count:,} NaN values with 0")
    return np.nan_to_num(data_array, nan=0.0)


def normalize_features(feature_stack, feature_names):
    """
    Normalize each feature channel independently using StandardScaler logic.
    Returns normalized data and the scaler stats (mean, std) for inverse transform later.
    
    feature_stack shape: (time, lat, lon, channels)
    """
    logging.info("Normalizing features...")
    n_features = feature_stack.shape[-1]
    stats = {}

    normalized = np.zeros_like(feature_stack, dtype=np.float32)
    for i in range(n_features):
        channel = feature_stack[:, :, :, i]
        mean = float(np.nanmean(channel))
        std = float(np.nanstd(channel))
        std = std if std > 1e-8 else 1.0  # avoid division by zero for constant channels

        normalized[:, :, :, i] = (channel - mean) / std
        stats[feature_names[i]] = {"mean": mean, "std": std}
        logging.info(f"  {feature_names[i]:>15s}: mean={mean:+.4f}, std={std:.4f}")

    return normalized, stats


def create_sequences(data, target_channel_idx, lookback, horizon):
    """
    Create sliding window sequences for time-series prediction.
    
    Input data shape: (time, lat, lon, channels)
    
    Returns:
        X: (n_samples, lookback, lat, lon, channels)  — input sequences
        y: (n_samples, lat, lon)                       — target (siconc at t+horizon)
    """
    logging.info(f"Creating sequences: lookback={lookback}, horizon={horizon}...")
    n_time = data.shape[0]
    n_samples = n_time - lookback - horizon + 1

    if n_samples <= 0:
        raise ValueError(f"Not enough time steps ({n_time}) for lookback={lookback} + horizon={horizon}")

    X = np.zeros((n_samples, lookback, data.shape[1], data.shape[2], data.shape[3]), dtype=np.float32)
    y = np.zeros((n_samples, data.shape[1], data.shape[2]), dtype=np.float32)

    for i in range(n_samples):
        X[i] = data[i : i + lookback]
        y[i] = data[i + lookback + horizon - 1, :, :, target_channel_idx]

    logging.info(f"Created {n_samples} sequences. X shape: {X.shape}, y shape: {y.shape}")
    return X, y


def main():
    # 1. Load
    ds = load_dataset(INPUT_FILE)

    # 2. Derived features
    ds = add_derived_features(ds)
    ds = add_temporal_features(ds)

    # 3. Select the feature channels we want for the model
    feature_names = [
        'siconc',          # target variable (also used as input)
        'u10', 'v10',      # raw wind components
        'uo', 'vo',        # raw ocean current components
        'wind_speed',      # derived
        'current_speed',   # derived
        'wind_dir',        # derived
        'current_dir',     # derived
        'day_sin',         # temporal
        'day_cos',         # temporal
    ]
    TARGET_CHANNEL_IDX = 0  # siconc is the first feature

    # 4. Stack into a single numpy array (time, lat, lon, channels)
    logging.info("Stacking features into numpy array...")
    arrays = []
    for name in feature_names:
        arr = ds[name].values.astype(np.float32)
        arr = handle_nans(arr)
        arrays.append(arr)

    # shape: (time, lat, lon, n_features)
    feature_stack = np.stack(arrays, axis=-1)
    logging.info(f"Feature stack shape: {feature_stack.shape} "
                 f"({feature_stack.shape[0]} time steps, "
                 f"{feature_stack.shape[1]}x{feature_stack.shape[2]} grid, "
                 f"{feature_stack.shape[3]} features)")

    # 5. Normalize
    feature_stack, scaler_stats = normalize_features(feature_stack, feature_names)

    # 6. Train/Test split by time
    times = ds.time.values
    split_idx = int(np.searchsorted(times, np.datetime64(TRAIN_SPLIT_DATE)))
    logging.info(f"Train/test split at index {split_idx} "
                 f"(train: {split_idx} steps, test: {len(times) - split_idx} steps)")

    train_data = feature_stack[:split_idx]
    test_data = feature_stack[split_idx:]

    # 7. Create sequences
    X_train, y_train = create_sequences(train_data, TARGET_CHANNEL_IDX, LOOKBACK_DAYS, FORECAST_HORIZON)
    X_test, y_test = create_sequences(test_data, TARGET_CHANNEL_IDX, LOOKBACK_DAYS, FORECAST_HORIZON)

    # 8. Save everything
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logging.info(f"Saving to {OUTPUT_DIR}/...")
    np.save(os.path.join(OUTPUT_DIR, "X_train.npy"), X_train)
    np.save(os.path.join(OUTPUT_DIR, "y_train.npy"), y_train)
    np.save(os.path.join(OUTPUT_DIR, "X_test.npy"), X_test)
    np.save(os.path.join(OUTPUT_DIR, "y_test.npy"), y_test)

    # Save metadata so we know how to interpret the arrays later
    metadata = {
        "feature_names": feature_names,
        "target_variable": "siconc",
        "target_channel_idx": TARGET_CHANNEL_IDX,
        "lookback": LOOKBACK_DAYS,
        "forecast_horizon": FORECAST_HORIZON,
        "train_split_date": TRAIN_SPLIT_DATE,
        "X_train_shape": list(X_train.shape),
        "y_train_shape": list(y_train.shape),
        "X_test_shape": list(X_test.shape),
        "y_test_shape": list(y_test.shape),
        "scaler_stats": scaler_stats,
        "created_at": datetime.now().isoformat(),
    }
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    logging.info("=" * 60)
    logging.info("Feature engineering complete!")
    logging.info(f"  X_train: {X_train.shape}  ({X_train.nbytes / 1e6:.1f} MB)")
    logging.info(f"  y_train: {y_train.shape}")
    logging.info(f"  X_test:  {X_test.shape}   ({X_test.nbytes / 1e6:.1f} MB)")
    logging.info(f"  y_test:  {y_test.shape}")
    logging.info(f"  Files saved to: {OUTPUT_DIR}/")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()
