"""
IceCast — Lightweight ConvLSTM for Weddell Sea Ice Prediction
=============================================================

CPU-optimized model that loads directly from weddell_sea_combined.nc.
No pre-saved feature arrays needed — computes everything on-the-fly.

Architecture:
    Input: (batch, 7 time steps, 37×81 grid, 11 channels)
    → ConvLSTM(16 filters) → ConvLSTM(32 filters)
    → Conv2D head → (batch, 37×81) predicted ice concentration

Usage:
    python train_model.py              # Train from scratch
    python train_model.py --epochs 30  # Custom epochs
    python train_model.py --eval-only  # Evaluate saved model
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import numpy as np
import xarray as xr
import os
import json
import logging
import argparse
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

CONFIG = {
    "input_file": "data/processed/weddell_sea_combined.nc",
    "model_save_dir": "models",
    "lookback": 7,
    "forecast_horizon": 1,
    "train_ratio": 0.7,
    "downsample_factor": 2,       # 73×161 → 37×81
    "batch_size": 4,
    "learning_rate": 1e-3,
    "epochs": 20,
    "patience": 5,                # Early stopping patience
    "convlstm_filters": [16, 32],
    "kernel_size": 3,
    "dropout": 0.1,
}

# Feature definitions
RAW_VARS = ['u10', 'v10', 'uo', 'vo', 'siconc']
TARGET_VAR = 'siconc'

FEATURE_NAMES = [
    'siconc', 'u10', 'v10', 'uo', 'vo',
    'wind_speed', 'current_speed', 'wind_dir', 'current_dir',
    'day_sin', 'day_cos',
]


# =============================================================================
# Data Loading & Feature Engineering (On-the-fly)
# =============================================================================

def load_and_engineer_features(filepath, downsample=2):
    """
    Load NetCDF, compute derived features, downsample spatially.
    Returns numpy array of shape (time, lat, lon, channels) and metadata.
    """
    logger.info(f"Loading {filepath}...")
    ds = xr.open_dataset(filepath)
    logger.info(f"Loaded: {ds.sizes['time']} time steps, "
                f"{ds.sizes['latitude']}×{ds.sizes['longitude']} grid")

    # Compute derived features
    logger.info("Computing derived features...")
    ds['wind_speed'] = np.sqrt(ds['u10']**2 + ds['v10']**2)
    ds['wind_dir'] = np.arctan2(ds['v10'], ds['u10'])
    ds['current_speed'] = np.sqrt(ds['uo']**2 + ds['vo']**2)
    ds['current_dir'] = np.arctan2(ds['vo'], ds['uo'])

    # Temporal features
    doy = ds.time.dt.dayofyear.values
    sin_vals = np.sin(2 * np.pi * doy / 365.25)
    cos_vals = np.cos(2 * np.pi * doy / 365.25)
    nlat, nlon = ds.sizes['latitude'], ds.sizes['longitude']

    ds['day_sin'] = xr.DataArray(
        np.broadcast_to(sin_vals[:, None, None], (len(doy), nlat, nlon)).copy(),
        dims=['time', 'latitude', 'longitude']
    )
    ds['day_cos'] = xr.DataArray(
        np.broadcast_to(cos_vals[:, None, None], (len(doy), nlat, nlon)).copy(),
        dims=['time', 'latitude', 'longitude']
    )

    # Stack into numpy array
    logger.info("Stacking features...")
    arrays = []
    for name in FEATURE_NAMES:
        arr = ds[name].values.astype(np.float32)
        arr = np.nan_to_num(arr, nan=0.0)
        arrays.append(arr)

    # Shape: (time, lat, lon, channels)
    data = np.stack(arrays, axis=-1)
    logger.info(f"Full resolution: {data.shape}")

    # Spatial downsampling
    if downsample > 1:
        data = data[:, ::downsample, ::downsample, :]
        logger.info(f"Downsampled (×{downsample}): {data.shape}")

    # Normalize each channel
    scaler_stats = {}
    for i, name in enumerate(FEATURE_NAMES):
        mean = float(np.mean(data[:, :, :, i]))
        std = float(np.std(data[:, :, :, i]))
        std = std if std > 1e-8 else 1.0
        data[:, :, :, i] = (data[:, :, :, i] - mean) / std
        scaler_stats[name] = {"mean": mean, "std": std}
        logger.info(f"  {name:>15s}: mean={mean:+.4f}, std={std:.4f}")

    ds.close()
    return data, scaler_stats


class SeaIceDataset(Dataset):
    """PyTorch dataset that creates sliding-window sequences on-the-fly."""

    def __init__(self, data, lookback=7, horizon=1, target_idx=0):
        """
        Args:
            data: numpy array (time, lat, lon, channels)
            lookback: number of input time steps
            horizon: forecast steps ahead
            target_idx: channel index of target variable (siconc = 0)
        """
        self.data = data
        self.lookback = lookback
        self.horizon = horizon
        self.target_idx = target_idx
        self.n_samples = data.shape[0] - lookback - horizon + 1

        if self.n_samples <= 0:
            raise ValueError(f"Not enough time steps ({data.shape[0]}) "
                             f"for lookback={lookback} + horizon={horizon}")

    def __len__(self):
        return self.n_samples

    def __getitem__(self, idx):
        # Input: (lookback, lat, lon, channels) → permute to (lookback, channels, lat, lon)
        x = self.data[idx: idx + self.lookback].copy()
        x = np.transpose(x, (0, 3, 1, 2))  # (T, C, H, W)

        # Target: (lat, lon) — ice concentration at t+lookback+horizon-1
        y = self.data[idx + self.lookback + self.horizon - 1, :, :, self.target_idx].copy()

        return torch.FloatTensor(x), torch.FloatTensor(y)


# =============================================================================
# ConvLSTM Cell & Model
# =============================================================================

class ConvLSTMCell(nn.Module):
    """Single ConvLSTM cell — combines Conv2D with LSTM gating."""

    def __init__(self, input_channels, hidden_channels, kernel_size):
        super().__init__()
        self.hidden_channels = hidden_channels
        padding = kernel_size // 2

        # Combined gates: input, forget, cell, output
        self.gates = nn.Conv2d(
            input_channels + hidden_channels,
            4 * hidden_channels,
            kernel_size=kernel_size,
            padding=padding,
            bias=True
        )

    def forward(self, x, hidden_state=None):
        """
        Args:
            x: (batch, channels, H, W)
            hidden_state: tuple (h, c) each (batch, hidden_channels, H, W)
        Returns:
            h_next, c_next
        """
        batch, _, h, w = x.size()

        if hidden_state is None:
            device = x.device
            h_prev = torch.zeros(batch, self.hidden_channels, h, w, device=device)
            c_prev = torch.zeros(batch, self.hidden_channels, h, w, device=device)
        else:
            h_prev, c_prev = hidden_state

        combined = torch.cat([x, h_prev], dim=1)
        gates = self.gates(combined)

        i, f, g, o = gates.chunk(4, dim=1)
        i = torch.sigmoid(i)
        f = torch.sigmoid(f)
        g = torch.tanh(g)
        o = torch.sigmoid(o)

        c_next = f * c_prev + i * g
        h_next = o * torch.tanh(c_next)

        return h_next, c_next


class ConvLSTM(nn.Module):
    """Multi-layer ConvLSTM that processes a sequence of spatial frames."""

    def __init__(self, input_channels, hidden_channels_list, kernel_size):
        super().__init__()
        self.num_layers = len(hidden_channels_list)
        self.cells = nn.ModuleList()

        for i, hidden_ch in enumerate(hidden_channels_list):
            in_ch = input_channels if i == 0 else hidden_channels_list[i - 1]
            self.cells.append(ConvLSTMCell(in_ch, hidden_ch, kernel_size))

    def forward(self, x):
        """
        Args:
            x: (batch, seq_len, channels, H, W)
        Returns:
            Last hidden state of final layer: (batch, hidden[-1], H, W)
        """
        batch, seq_len, _, h, w = x.size()

        # Initialize hidden states
        hidden_states = [None] * self.num_layers

        # Process each time step
        for t in range(seq_len):
            input_t = x[:, t]  # (batch, channels, H, W)

            for layer_idx, cell in enumerate(self.cells):
                h_next, c_next = cell(input_t, hidden_states[layer_idx])
                hidden_states[layer_idx] = (h_next, c_next)
                input_t = h_next  # Output of this layer is input to the next

        # Return the last hidden state of the last layer
        return hidden_states[-1][0]


class IceCastModel(nn.Module):
    """
    Full IceCast model:
        ConvLSTM encoder → Conv2D prediction head → single-channel ice map
    """

    def __init__(self, n_features=11, hidden_channels=[16, 32],
                 kernel_size=3, dropout=0.1):
        super().__init__()

        self.encoder = ConvLSTM(n_features, hidden_channels, kernel_size)

        # Prediction head: Conv2D layers to map hidden state → ice concentration
        last_hidden = hidden_channels[-1]
        self.head = nn.Sequential(
            nn.Conv2d(last_hidden, 16, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Dropout2d(dropout),
            nn.Conv2d(16, 8, kernel_size=3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(8, 1, kernel_size=1),  # → (batch, 1, H, W)
        )

    def forward(self, x):
        """
        Args:
            x: (batch, seq_len, channels, H, W)
        Returns:
            prediction: (batch, H, W) — predicted normalized siconc
        """
        hidden = self.encoder(x)       # (batch, hidden[-1], H, W)
        out = self.head(hidden)         # (batch, 1, H, W)
        return out.squeeze(1)           # (batch, H, W)

    def count_params(self):
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


# =============================================================================
# Training & Evaluation
# =============================================================================

def train_one_epoch(model, loader, criterion, optimizer, epoch):
    model.train()
    total_loss = 0
    n_batches = 0

    for batch_idx, (x, y) in enumerate(loader):
        optimizer.zero_grad()
        pred = model(x)
        loss = criterion(pred, y)
        loss.backward()

        # Gradient clipping to prevent exploding gradients
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        optimizer.step()
        total_loss += loss.item()
        n_batches += 1

        if (batch_idx + 1) % 25 == 0:
            logger.info(f"  Epoch {epoch} | Batch {batch_idx+1}/{len(loader)} | "
                        f"Loss: {loss.item():.6f}")

    return total_loss / max(n_batches, 1)


def evaluate(model, loader, criterion):
    model.eval()
    total_loss = 0
    total_mae = 0
    n_batches = 0

    with torch.no_grad():
        for x, y in loader:
            pred = model(x)
            loss = criterion(pred, y)
            mae = torch.mean(torch.abs(pred - y))
            total_loss += loss.item()
            total_mae += mae.item()
            n_batches += 1

    avg_loss = total_loss / max(n_batches, 1)
    avg_mae = total_mae / max(n_batches, 1)
    return avg_loss, avg_mae


def train(config):
    """Main training loop."""
    logger.info("=" * 60)
    logger.info("IceCast ConvLSTM — Training")
    logger.info("=" * 60)

    # Load and prepare data
    data, scaler_stats = load_and_engineer_features(
        config["input_file"],
        downsample=config["downsample_factor"]
    )

    n_time, n_lat, n_lon, n_features = data.shape
    logger.info(f"Data: {n_time} time steps, {n_lat}×{n_lon} grid, {n_features} features")

    # Train/test split by time
    split_idx = int(n_time * config["train_ratio"])
    train_data = data[:split_idx]
    test_data = data[split_idx:]
    logger.info(f"Split: {split_idx} train / {n_time - split_idx} test time steps")

    # Create datasets and loaders
    train_dataset = SeaIceDataset(train_data, config["lookback"], config["forecast_horizon"])
    test_dataset = SeaIceDataset(test_data, config["lookback"], config["forecast_horizon"])

    logger.info(f"Train sequences: {len(train_dataset)}, Test sequences: {len(test_dataset)}")

    train_loader = DataLoader(train_dataset, batch_size=config["batch_size"],
                              shuffle=True, num_workers=0, pin_memory=False)
    test_loader = DataLoader(test_dataset, batch_size=config["batch_size"],
                             shuffle=False, num_workers=0, pin_memory=False)

    # Create model
    model = IceCastModel(
        n_features=n_features,
        hidden_channels=config["convlstm_filters"],
        kernel_size=config["kernel_size"],
        dropout=config["dropout"],
    )
    logger.info(f"Model parameters: {model.count_params():,}")

    # Loss, optimizer, scheduler
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=config["learning_rate"], weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5,
                                                      patience=3)

    # Training loop with early stopping
    best_test_loss = float('inf')
    patience_counter = 0
    history = {"train_loss": [], "test_loss": [], "test_mae": []}

    os.makedirs(config["model_save_dir"], exist_ok=True)

    logger.info(f"\nStarting training for {config['epochs']} epochs...")
    logger.info(f"Batch size: {config['batch_size']}, LR: {config['learning_rate']}")
    start_time = time.time()

    for epoch in range(1, config["epochs"] + 1):
        epoch_start = time.time()

        # Train
        train_loss = train_one_epoch(model, train_loader, criterion, optimizer, epoch)

        # Evaluate
        test_loss, test_mae = evaluate(model, test_loader, criterion)

        # Learning rate scheduling
        scheduler.step(test_loss)

        epoch_time = time.time() - epoch_start
        elapsed = time.time() - start_time

        history["train_loss"].append(train_loss)
        history["test_loss"].append(test_loss)
        history["test_mae"].append(test_mae)

        # Convert MAE back to original scale for interpretability
        siconc_std = scaler_stats['siconc']['std']
        siconc_mean = scaler_stats['siconc']['mean']
        mae_original = test_mae * siconc_std  # approximate original-scale MAE
        rmse_original = np.sqrt(test_loss) * siconc_std

        logger.info(f"Epoch {epoch:3d}/{config['epochs']} | "
                    f"Train: {train_loss:.6f} | Test: {test_loss:.6f} | "
                    f"MAE(norm): {test_mae:.4f} | MAE(orig): {mae_original:.4f} | "
                    f"RMSE(orig): {rmse_original:.4f} | "
                    f"Time: {epoch_time:.1f}s | Total: {elapsed:.0f}s")

        # Early stopping check
        if test_loss < best_test_loss:
            best_test_loss = test_loss
            patience_counter = 0

            # Save best model
            save_path = os.path.join(config["model_save_dir"], "icecast_convlstm_best.pt")
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "test_loss": test_loss,
                "test_mae": test_mae,
                "config": config,
                "scaler_stats": scaler_stats,
                "grid_shape": (n_lat, n_lon),
                "feature_names": FEATURE_NAMES,
            }, save_path)
            logger.info(f"  ✓ Best model saved (test_loss={test_loss:.6f})")
        else:
            patience_counter += 1
            if patience_counter >= config["patience"]:
                logger.info(f"  Early stopping triggered (patience={config['patience']})")
                break

    total_time = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"Training complete in {total_time/60:.1f} minutes")
    logger.info(f"Best test loss: {best_test_loss:.6f}")
    logger.info(f"Best RMSE (original scale): {np.sqrt(best_test_loss) * siconc_std:.4f}")
    logger.info(f"Model saved to: {config['model_save_dir']}/icecast_convlstm_best.pt")
    logger.info("=" * 60)

    # Save training history
    history_path = os.path.join(config["model_save_dir"], "training_history.json")
    with open(history_path, "w") as f:
        json.dump({
            "history": history,
            "best_test_loss": best_test_loss,
            "total_time_seconds": total_time,
            "config": config,
            "scaler_stats": scaler_stats,
            "completed_at": datetime.now().isoformat(),
        }, f, indent=2)

    return model, history


def evaluate_model(config):
    """Load saved model and run evaluation."""
    logger.info("Loading saved model for evaluation...")

    model_path = os.path.join(config["model_save_dir"], "icecast_convlstm_best.pt")
    if not os.path.exists(model_path):
        logger.error(f"No saved model found at {model_path}")
        return

    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    saved_config = checkpoint["config"]
    scaler_stats = checkpoint["scaler_stats"]

    # Load data
    data, _ = load_and_engineer_features(
        config["input_file"],
        downsample=saved_config["downsample_factor"]
    )

    n_time = data.shape[0]
    split_idx = int(n_time * saved_config["train_ratio"])
    test_data = data[split_idx:]

    test_dataset = SeaIceDataset(test_data, saved_config["lookback"],
                                  saved_config["forecast_horizon"])
    test_loader = DataLoader(test_dataset, batch_size=saved_config["batch_size"],
                             shuffle=False, num_workers=0)

    # Load model
    model = IceCastModel(
        n_features=data.shape[-1],
        hidden_channels=saved_config["convlstm_filters"],
        kernel_size=saved_config["kernel_size"],
        dropout=0.0,  # No dropout during eval
    )
    model.load_state_dict(checkpoint["model_state_dict"])

    criterion = nn.MSELoss()
    test_loss, test_mae = evaluate(model, test_loader, criterion)

    siconc_std = scaler_stats['siconc']['std']
    logger.info(f"Test MSE: {test_loss:.6f}")
    logger.info(f"Test MAE (normalized): {test_mae:.4f}")
    logger.info(f"Test MAE (original scale): {test_mae * siconc_std:.4f}")
    logger.info(f"Test RMSE (original scale): {np.sqrt(test_loss) * siconc_std:.4f}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IceCast ConvLSTM Training")
    parser.add_argument("--epochs", type=int, default=CONFIG["epochs"],
                        help=f"Number of epochs (default: {CONFIG['epochs']})")
    parser.add_argument("--batch-size", type=int, default=CONFIG["batch_size"],
                        help=f"Batch size (default: {CONFIG['batch_size']})")
    parser.add_argument("--lr", type=float, default=CONFIG["learning_rate"],
                        help=f"Learning rate (default: {CONFIG['learning_rate']})")
    parser.add_argument("--eval-only", action="store_true",
                        help="Only evaluate saved model, don't train")
    parser.add_argument("--downsample", type=int, default=CONFIG["downsample_factor"],
                        help=f"Spatial downsample factor (default: {CONFIG['downsample_factor']})")
    args = parser.parse_args()

    config = CONFIG.copy()
    config["epochs"] = args.epochs
    config["batch_size"] = args.batch_size
    config["learning_rate"] = args.lr
    config["downsample_factor"] = args.downsample

    if args.eval_only:
        evaluate_model(config)
    else:
        train(config)
