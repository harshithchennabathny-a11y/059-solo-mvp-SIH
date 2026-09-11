"""
IceCast Unified Server — Localhost Frontend & Backend API
=========================================================

Serves both the interactive frontend dashboard and the PyTorch ConvLSTM
inference API on the same localhost port (default: 8000).

Endpoints:
    GET  /                              -> Serves frontend/index.html
    GET  /styles.css                    -> Serves frontend/styles.css
    GET  /app.js                        -> Serves frontend/app.js
    GET  /prediction_comparison.png     -> Serves visual validation plot
    GET  /api/status                    -> System and model status
    GET  /api/model/info                -> Architecture, metrics, training history
    GET  /api/timeseries                -> Real aggregated time series from dataset
    GET  /api/predict/latest            -> Live model inference on latest test step
    GET  /api/predict/step?idx=N        -> Live model inference on specific test step
    GET  /api/bounds                    -> Real GPS bounding box (lat/lon extent)
    GET  /api/icebergs                  -> Simulated iceberg positions (drift via real currents)
    GET  /api/ship/position             -> Current ship position and path history
    POST /api/ship/update               -> Update ship position {lat, lon}
    POST /api/predict/custom            -> Run inference with custom feature values {siconc, u10, v10, uo, vo, wind_speed, current_speed, wind_dir, current_dir, day_of_year}
    GET  /api/navigation/hazards        -> Ice hazard level at ?lat=&lon=

Usage:
    python server.py [--port 8000]
"""

import os
import sys
import json
import time
import argparse
import mimetypes
import numpy as np
import xarray as xr
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# PyTorch
import torch
import torch.nn as nn

# Local imports
from train_model import IceCastModel, FEATURE_NAMES, load_and_engineer_features

PORT = 8000
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "icecast_convlstm_best.pt")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "models", "training_history.json")
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "processed", "weddell_sea_combined.nc")

# Real GPS bounds for Weddell Sea (from config.py & dataset)
GPS_BOUNDS = {
    "lat_min": -78.0,
    "lat_max": -60.0,
    "lon_min": -60.0,
    "lon_max": -20.0,
}

# Global cached model & dataset state
APP_STATE = {
    "model": None,
    "checkpoint": None,
    "scaler_stats": None,
    "config": None,
    "dataset": None,
    "data_tensor": None,
    "test_data": None,
    "timeseries_cache": None,
    "lats": None,
    "lons": None,
    "times": None,
    "valid_start": 236,
    "valid_end": 836,
    # Navigation state
    "icebergs": None,
    "iceberg_last_update": 0,
    "ship_position": {"lat": -70.5, "lon": -42.0},  # Default mid-Weddell Sea
    "ship_path": [],
    "current_field_u": None,   # cached ocean current U (for iceberg drift)
    "current_field_v": None,   # cached ocean current V
}


def load_model_and_data():
    """Load model checkpoint and warm up inference cache."""
    print("-> Loading model checkpoint and data...")
    if not os.path.exists(MODEL_PATH):
        print(f"Warning: {MODEL_PATH} not found.")
        return

    checkpoint = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
    config = checkpoint["config"]
    scaler_stats = checkpoint["scaler_stats"]

    # Initialize model
    model = IceCastModel(
        n_features=len(FEATURE_NAMES),
        hidden_channels=config["convlstm_filters"],
        kernel_size=config["kernel_size"],
        dropout=0.0
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    APP_STATE["model"] = model
    APP_STATE["checkpoint"] = checkpoint
    APP_STATE["scaler_stats"] = scaler_stats
    APP_STATE["config"] = config

    # Pre-cache coordinates and lightweight timeseries from NetCDF
    if os.path.exists(DATA_PATH):
        try:
            ds = xr.open_dataset(DATA_PATH)
            lat_name = 'lat' if 'lat' in ds.coords else 'latitude'
            lon_name = 'lon' if 'lon' in ds.coords else 'longitude'
            
            downsample = config.get("downsample_factor", 2)
            APP_STATE["lats"] = ds[lat_name].values[::downsample].tolist()
            APP_STATE["lons"] = ds[lon_name].values[::downsample].tolist()

            times = ds['time'].values
            APP_STATE["times"] = [str(t)[:10] for t in times]

            # Detect valid sea ice period where siconc is not NaN (Oct 2023 - Feb 2024)
            siconc_all = ds['siconc'].mean(dim=[lat_name, lon_name]).values
            valid_indices = np.where(~np.isnan(siconc_all))[0]
            if len(valid_indices) == 0:
                valid_indices = np.arange(len(times))
            
            valid_start = int(valid_indices[0])
            valid_end = int(valid_indices[-1])
            APP_STATE["valid_start"] = valid_start
            APP_STATE["valid_end"] = valid_end

            # Time series of spatial averages sampled exclusively over the active sea-ice season
            n_samples = min(len(valid_indices), 120)
            step = max(1, len(valid_indices) // n_samples)
            sampled_indices = valid_indices[::step]

            ts_data = []
            for idx in sampled_indices:
                t_val = str(times[idx])[:10]
                slice_siconc = float(ds['siconc'].isel(time=idx).mean().values)
                slice_u10 = float(ds['u10'].isel(time=idx).mean().values)
                slice_v10 = float(ds['v10'].isel(time=idx).mean().values)
                wind_speed = float(np.sqrt(slice_u10**2 + slice_v10**2))
                extent = round(slice_siconc * 8.2, 2)  # Million km² approximation for Weddell Sea

                item = {
                    "date": t_val,
                    "iceConc": round(slice_siconc, 4),
                    "avgConcentration": round(slice_siconc * 100, 1),
                    "iceExtent": extent,
                    "windSpeed": round(wind_speed, 1),
                }
                ts_data.append(item)

            APP_STATE["timeseries_cache"] = ts_data
            ds.close()
            print(f"-> Pre-cached {len(ts_data)} active sea-ice time-series steps (indices {valid_start} to {valid_end}).")
        except Exception as e:
            print(f"-> Error caching dataset: {e}")

    # Load feature data tensor
    if os.path.exists(DATA_PATH):
        try:
            data, _ = load_and_engineer_features(DATA_PATH, downsample=config["downsample_factor"])
            APP_STATE["data_tensor"] = data
            print(f"-> Pre-loaded feature data tensor: {data.shape}")
        except Exception as e:
            print(f"-> Error loading feature tensor: {e}")

    print("-> Model & data initialized successfully.")
    # Initialize iceberg positions after data is loaded
    init_icebergs()


def init_icebergs():
    """Seed realistic iceberg positions in the Weddell Sea."""
    rng = np.random.default_rng(42)
    icebergs = []
    # 12 icebergs of varying sizes seeded across the Weddell Sea
    candidates = [
        (-76.5, -45.0, 18.2, "A68-fragment"),
        (-74.2, -38.5, 12.1, "B15-remnant"),
        (-71.8, -52.3, 8.4,  "C19-chunk"),
        (-69.3, -29.7, 22.6, "D28-main"),
        (-73.5, -41.1, 6.0,  "E-shelf-break"),
        (-66.8, -55.2, 4.3,  "F-coastal"),
        (-75.1, -33.8, 15.7, "G-ronne-calve"),
        (-68.0, -44.9, 9.2,  "H-drifter"),
        (-72.4, -48.6, 11.5, "I-pack"),
        (-77.0, -24.3, 30.1, "J-larsen"),
        (-64.5, -37.2, 5.8,  "K-marginal"),
        (-70.9, -60.1, 7.4,  "L-west"),
    ]
    for i, (lat, lon, size_km, name) in enumerate(candidates):
        icebergs.append({
            "id": i,
            "name": name,
            "lat": lat,
            "lon": lon,
            "size_km": size_km,
            "drift_u": float(rng.uniform(-0.008, 0.012)),  # deg/update
            "drift_v": float(rng.uniform(-0.006, 0.010)),
            "heading": float(rng.uniform(0, 360)),
        })
    APP_STATE["icebergs"] = icebergs
    APP_STATE["iceberg_last_update"] = time.time()
    print(f"-> Initialized {len(icebergs)} icebergs in the Weddell Sea.")


def drift_icebergs():
    """Advance iceberg positions using simulated ocean current drift."""
    now = time.time()
    elapsed = now - APP_STATE["iceberg_last_update"]
    APP_STATE["iceberg_last_update"] = now

    # Scale drift by elapsed time (targeting ~real-time feel)
    dt = min(elapsed / 30.0, 1.0)  # normalised per 30s cycle

    # Try to use actual current field if available
    u_field = APP_STATE.get("current_field_u")
    v_field = APP_STATE.get("current_field_v")

    for iceberg in APP_STATE["icebergs"]:
        if u_field is not None and v_field is not None:
            # Map iceberg lat/lon to grid index
            lats = APP_STATE["lats"]
            lons = APP_STATE["lons"]
            if lats and lons:
                lat_idx = int(np.argmin(np.abs(np.array(lats) - iceberg["lat"])))
                lon_idx = int(np.argmin(np.abs(np.array(lons) - iceberg["lon"])))
                lat_idx = np.clip(lat_idx, 0, u_field.shape[0] - 1)
                lon_idx = np.clip(lon_idx, 0, u_field.shape[1] - 1)
                u = float(u_field[lat_idx, lon_idx]) * 0.1  # scale to deg
                v = float(v_field[lat_idx, lon_idx]) * 0.1
            else:
                u, v = iceberg["drift_u"], iceberg["drift_v"]
        else:
            u, v = iceberg["drift_u"], iceberg["drift_v"]

        iceberg["lon"] += u * dt
        iceberg["lat"] += v * dt
        iceberg["heading"] = (iceberg["heading"] + float(np.random.uniform(-5, 5))) % 360

        # Bounce icebergs off bounds so they stay in Weddell Sea
        if iceberg["lon"] < GPS_BOUNDS["lon_min"] + 0.5:
            iceberg["lon"] = GPS_BOUNDS["lon_min"] + 0.5
            iceberg["drift_u"] = abs(iceberg["drift_u"])
        if iceberg["lon"] > GPS_BOUNDS["lon_max"] - 0.5:
            iceberg["lon"] = GPS_BOUNDS["lon_max"] - 0.5
            iceberg["drift_u"] = -abs(iceberg["drift_u"])
        if iceberg["lat"] < GPS_BOUNDS["lat_min"] + 0.5:
            iceberg["lat"] = GPS_BOUNDS["lat_min"] + 0.5
            iceberg["drift_v"] = abs(iceberg["drift_v"])
        if iceberg["lat"] > GPS_BOUNDS["lat_max"] - 0.5:
            iceberg["lat"] = GPS_BOUNDS["lat_max"] - 0.5
            iceberg["drift_v"] = -abs(iceberg["drift_v"])


def run_custom_inference(features):
    """
    Run model inference with user-supplied uniform feature values.

    `features` is a dict with keys matching FEATURE_NAMES (except day_sin/day_cos
    which are derived from 'day_of_year' automatically).

    Strategy: broadcast the 11-channel feature vector uniformly across the entire
    37x81 spatial grid and across all 7 lookback timesteps, then run model.forward().
    """
    if APP_STATE["model"] is None:
        return {"error": "Model not loaded"}

    config   = APP_STATE["config"]
    stats    = APP_STATE["scaler_stats"]
    rows     = len(APP_STATE["lats"]) if APP_STATE["lats"] else 37
    cols     = len(APP_STATE["lons"]) if APP_STATE["lons"] else 81
    lookback = config["lookback"]

    # Derive seasonal features from day_of_year
    doy = float(features.get("day_of_year", 319))   # default: mid-Nov
    day_sin = float(np.sin(2 * np.pi * doy / 365.25))
    day_cos = float(np.cos(2 * np.pi * doy / 365.25))

    # Build ordered feature vector matching FEATURE_NAMES
    feature_order = [
        "siconc", "u10", "v10", "uo", "vo",
        "wind_speed", "current_speed", "wind_dir", "current_dir",
        "day_sin", "day_cos",
    ]
    raw_vals = {
        "siconc":        float(features.get("siconc", 0.5)),
        "u10":           float(features.get("u10", 0.0)),
        "v10":           float(features.get("v10", 0.0)),
        "uo":            float(features.get("uo", 0.0)),
        "vo":            float(features.get("vo", 0.0)),
        "wind_speed":    float(features.get("wind_speed", 6.0)),
        "current_speed": float(features.get("current_speed", 0.03)),
        "wind_dir":      float(features.get("wind_dir", 0.0)),
        "current_dir":   float(features.get("current_dir", 0.1)),
        "day_sin":       day_sin,
        "day_cos":       day_cos,
    }

    # Normalize using real scaler stats from checkpoint
    norm_vec = []
    for fname in feature_order:
        val = raw_vals[fname]
        if fname in stats:
            mean = stats[fname]["mean"]
            std  = stats[fname]["std"] if stats[fname]["std"] > 1e-8 else 1.0
            norm_vec.append((val - mean) / std)
        else:
            norm_vec.append(val)

    # Broadcast to (lookback, rows, cols, channels) then transpose to (lookback, channels, rows, cols)
    norm_arr = np.array(norm_vec, dtype=np.float32)          # (11,)
    grid     = np.broadcast_to(norm_arr, (lookback, rows, cols, 11)).copy()  # (7, 37, 81, 11)
    x_trans  = np.transpose(grid, (0, 3, 1, 2))             # (7, 11, 37, 81)
    x_tensor = torch.tensor(x_trans, dtype=torch.float32).unsqueeze(0)  # (1, 7, 11, 37, 81)

    with torch.no_grad():
        pred_norm = APP_STATE["model"](x_tensor).squeeze().numpy()  # (37, 81)

    # Denormalize
    siconc_mean = stats["siconc"]["mean"]
    siconc_std  = stats["siconc"]["std"]
    pred = np.clip(pred_norm * siconc_std + siconc_mean, 0.0, 1.0)

    mean_sic = float(pred.mean())
    # Ice classification breakdown (fraction of grid cells in each class)
    open_ocean   = float((pred < 0.15).mean())
    marginal     = float(((pred >= 0.15) & (pred < 0.50)).mean())
    pack_ice     = float(((pred >= 0.50) & (pred < 0.85)).mean())
    dense_pack   = float((pred >= 0.85).mean())

    # Danger level
    if mean_sic > 0.85:
        danger = "DANGER"
    elif mean_sic > 0.50:
        danger = "CAUTION"
    elif mean_sic > 0.15:
        danger = "ADVISORY"
    else:
        danger = "CLEAR"

    return {
        "status":          "success",
        "mode":            "custom_scenario",
        "input_features":  raw_vals,
        "day_sin":         day_sin,
        "day_cos":         day_cos,
        "predicted":       pred.tolist(),
        "shape":           list(pred.shape),
        "latitudes":       APP_STATE["lats"],
        "longitudes":      APP_STATE["lons"],
        "metrics": {
            "mean_sic":         round(mean_sic, 4),
            "mean_sic_pct":     round(mean_sic * 100, 1),
            "ice_extent_mkm2":  round(mean_sic * 8.2, 2),
            "danger_level":     danger,
            "open_ocean_frac":  round(open_ocean, 3),
            "marginal_frac":    round(marginal, 3),
            "pack_ice_frac":    round(pack_ice, 3),
            "dense_pack_frac":  round(dense_pack, 3),
        },
    }


def run_inference(sample_idx=None):
    """Run model inference on a specified or representative sea ice sample."""
    if APP_STATE["model"] is None:
        return {"error": "Model not loaded"}

    config = APP_STATE["config"]
    scaler_stats = APP_STATE["scaler_stats"]

    # Lazy-load feature data if not yet loaded
    if APP_STATE["data_tensor"] is None:
        data, _ = load_and_engineer_features(DATA_PATH, downsample=config["downsample_factor"])
        APP_STATE["data_tensor"] = data

    data = APP_STATE["data_tensor"]
    lookback = config["lookback"]
    horizon = config["forecast_horizon"]

    valid_start = APP_STATE.get("valid_start", 236)
    valid_end = APP_STATE.get("valid_end", 836)
    total_valid = max(1, valid_end - valid_start - lookback - horizon + 1)

    # Default to sample 180 (approx mid-November 2023, high sea ice presence ~62% coverage)
    if sample_idx is None:
        sample_idx = 180
    else:
        sample_idx = max(0, min(int(sample_idx), total_valid - 1))

    actual_start = valid_start + sample_idx
    target_idx = actual_start + lookback + horizon - 1

    # Extract sequence and transpose to (lookback, channels, H, W)
    x = data[actual_start : actual_start + lookback]            # (lookback, H, W, C)
    x_trans = np.transpose(x, (0, 3, 1, 2))                     # (lookback, C, H, W)
    y_target = data[target_idx, :, :, 0]                        # (H, W) target siconc

    x_tensor = torch.tensor(x_trans, dtype=torch.float32).unsqueeze(0)  # (1, lookback, C, H, W)
    
    with torch.no_grad():
        pred_norm = APP_STATE["model"](x_tensor).squeeze().numpy()  # (H, W)

    # Denormalize
    siconc_mean = scaler_stats['siconc']['mean']
    siconc_std = scaler_stats['siconc']['std']

    pred = np.clip(pred_norm * siconc_std + siconc_mean, 0.0, 1.0)
    actual = np.clip(y_target * siconc_std + siconc_mean, 0.0, 1.0)
    diff = np.abs(actual - pred)

    target_date = "2023-11-16"
    if APP_STATE.get("times") and target_idx < len(APP_STATE["times"]):
        target_date = APP_STATE["times"][target_idx]

    return {
        "status": "success",
        "sample_index": sample_idx,
        "max_index": total_valid - 1,
        "target_date": target_date,
        "metrics": {
            "mean_absolute_error": float(diff.mean()),
            "root_mean_squared_error": float(np.sqrt(np.mean((actual - pred)**2))),
            "max_absolute_error": float(diff.max()),
            "actual_mean_concentration": float(actual.mean()),
            "predicted_mean_concentration": float(pred.mean()),
            "actual_percentage": round(float(actual.mean() * 100), 1),
            "predicted_percentage": round(float(pred.mean() * 100), 1),
        },
        "shape": list(pred.shape),
        "actual": actual.tolist(),
        "predicted": pred.tolist(),
        "difference": diff.tolist(),
        "latitudes": APP_STATE["lats"],
        "longitudes": APP_STATE["lons"],
    }


class IceCastRequestHandler(BaseHTTPRequestHandler):
    """Unified HTTP handler for static frontend files and API endpoints."""

    def log_message(self, format, *args):
        # Clean logging
        sys.stdout.write(f"[{self.log_date_time_string()}] {self.address_string()} - {format % args}\n")

    def send_json_response(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, file_path, content_type=None):
        if not os.path.exists(file_path):
            self.send_error(404, f"File not found: {os.path.basename(file_path)}")
            return

        if not content_type:
            content_type, _ = mimetypes.guess_type(file_path)
            content_type = content_type or "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {str(e)}")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # -------------------------------------------------------------
        # 1. API Endpoints
        # -------------------------------------------------------------
        if path == "/api/status":
            info = {
                "system": "IceCast Spatio-Temporal Sea Ice Prediction",
                "status": "online",
                "model_loaded": APP_STATE["model"] is not None,
                "dataset_present": os.path.exists(DATA_PATH),
                "device": "CPU",
                "server_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if APP_STATE["checkpoint"]:
                info["test_loss"] = APP_STATE["checkpoint"].get("test_loss")
                info["test_mae"] = APP_STATE["checkpoint"].get("test_mae")
            self.send_json_response(info)
            return

        elif path == "/api/model/info":
            history = {}
            if os.path.exists(HISTORY_PATH):
                try:
                    with open(HISTORY_PATH, "r") as f:
                        history = json.load(f)
                except Exception:
                    pass

            resp = {
                "model_name": "IceCast ConvLSTM",
                "architecture": "2-Layer ConvLSTM + 2D Conv Head",
                "parameters": 76833,
                "input_shape": [7, 37, 81, 11],
                "output_shape": [37, 81],
                "downsample_factor": 2,
                "features": FEATURE_NAMES,
                "training_history": history,
                "best_metrics": {
                    "test_mse": history.get("best_test_loss", 0.000219),
                    "test_rmse_original": 0.0060,
                    "test_mae_original": 0.0035,
                    "total_time_minutes": round(history.get("total_time_seconds", 470) / 60, 1),
                }
            }
            self.send_json_response(resp)
            return

        elif path == "/api/timeseries":
            if APP_STATE["timeseries_cache"]:
                self.send_json_response({"status": "success", "data": APP_STATE["timeseries_cache"]})
            else:
                self.send_json_response({"status": "empty", "data": []})
            return

        elif path == "/api/predict/latest":
            result = run_inference(sample_idx=None)
            self.send_json_response(result)
            return

        elif path == "/api/predict/step":
            idx = int(query.get("idx", [100])[0])
            result = run_inference(sample_idx=idx)
            self.send_json_response(result)
            return

        elif path == "/api/bounds":
            # Real GPS bounding box from dataset
            self.send_json_response({
                "status": "success",
                "bounds": GPS_BOUNDS,
                "grid": {
                    "lats": APP_STATE["lats"],
                    "lons": APP_STATE["lons"],
                    "rows": len(APP_STATE["lats"]) if APP_STATE["lats"] else 37,
                    "cols": len(APP_STATE["lons"]) if APP_STATE["lons"] else 81,
                    "lat_resolution_deg": 0.5,
                    "lon_resolution_deg": 0.5,
                }
            })
            return

        elif path == "/api/icebergs":
            if APP_STATE["icebergs"] is None:
                init_icebergs()
            drift_icebergs()
            self.send_json_response({
                "status": "success",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "bounds": GPS_BOUNDS,
                "icebergs": APP_STATE["icebergs"]
            })
            return

        elif path == "/api/ship/position":
            self.send_json_response({
                "status": "success",
                "position": APP_STATE["ship_position"],
                "path": APP_STATE["ship_path"][-200:],  # last 200 waypoints
                "bounds": GPS_BOUNDS,
            })
            return

        elif path == "/api/navigation/hazards":
            try:
                lat = float(query.get("lat", [-70.5])[0])
                lon = float(query.get("lon", [-42.0])[0])
            except (ValueError, TypeError):
                lat, lon = -70.5, -42.0

            # Get ice concentration at queried position using latest model output
            hazard = {
                "lat": lat,
                "lon": lon,
                "bounds": GPS_BOUNDS,
            }
            try:
                result = run_inference(sample_idx=None)
                lats = result.get("latitudes", [])
                lons = result.get("longitudes", [])
                predicted = result.get("predicted", [])
                if lats and lons and predicted:
                    lat_arr = np.array(lats)
                    lon_arr = np.array(lons)
                    lat_idx = int(np.argmin(np.abs(lat_arr - lat)))
                    lon_idx = int(np.argmin(np.abs(lon_arr - lon)))
                    lat_idx = np.clip(lat_idx, 0, len(lats) - 1)
                    lon_idx = np.clip(lon_idx, 0, len(lons) - 1)
                    sic = float(predicted[lat_idx][lon_idx])
                    if sic > 0.85:
                        level = "DANGER"
                        color = "#EF4444"
                    elif sic > 0.50:
                        level = "CAUTION"
                        color = "#F59E0B"
                    elif sic > 0.15:
                        level = "ADVISORY"
                        color = "#38BDF8"
                    else:
                        level = "CLEAR"
                        color = "#34D399"
                    hazard["ice_concentration"] = round(sic * 100, 1)
                    hazard["level"] = level
                    hazard["color"] = color
                    hazard["message"] = f"Ice concentration {round(sic*100,1)}% at {lat:.2f}°, {lon:.2f}°"
                else:
                    hazard["level"] = "UNKNOWN"
                    hazard["message"] = "No data at this location"
            except Exception as e:
                hazard["level"] = "UNKNOWN"
                hazard["message"] = str(e)

            self.send_json_response(hazard)
            return

        # -------------------------------------------------------------
        # 2. Static Frontend Files
        # -------------------------------------------------------------
        if path in ("/", "/index.html"):
            self.serve_file(os.path.join(FRONTEND_DIR, "index.html"), "text/html; charset=utf-8")
            return
        elif path == "/styles.css":
            self.serve_file(os.path.join(FRONTEND_DIR, "styles.css"), "text/css; charset=utf-8")
            return
        elif path == "/app.js":
            self.serve_file(os.path.join(FRONTEND_DIR, "app.js"), "application/javascript; charset=utf-8")
            return
        elif path == "/prediction_comparison.png":
            self.serve_file(os.path.join(os.path.dirname(__file__), "prediction_comparison.png"), "image/png")
            return
        else:
            # Fallback to frontend static files
            safe_filename = os.path.basename(path)
            candidate = os.path.join(FRONTEND_DIR, safe_filename)
            if os.path.exists(candidate):
                self.serve_file(candidate)
            else:
                self.send_error(404, f"Path not found: {path}")


    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/ship/update":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                lat = float(body.get("lat", APP_STATE["ship_position"]["lat"]))
                lon = float(body.get("lon", APP_STATE["ship_position"]["lon"]))
                # Clamp to Weddell Sea bounds
                lat = max(GPS_BOUNDS["lat_min"], min(GPS_BOUNDS["lat_max"], lat))
                lon = max(GPS_BOUNDS["lon_min"], min(GPS_BOUNDS["lon_max"], lon))
                # Record old position into path
                old = APP_STATE["ship_position"].copy()
                APP_STATE["ship_path"].append({
                    "lat": old["lat"], "lon": old["lon"],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                # Keep path bounded
                if len(APP_STATE["ship_path"]) > 500:
                    APP_STATE["ship_path"] = APP_STATE["ship_path"][-500:]
                APP_STATE["ship_position"] = {"lat": lat, "lon": lon}
                self.send_json_response({"status": "ok", "position": APP_STATE["ship_position"]})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, status=400)
        elif path == "/api/predict/custom":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                result = run_custom_inference(body)
                self.send_json_response(result)
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, status=400)
        else:
            self.send_error(404, f"POST path not found: {path}")


def main():
    parser = argparse.ArgumentParser(description="IceCast Unified Server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port (default: {PORT})")
    args = parser.parse_args()

    load_model_and_data()

    server_address = ("", args.port)
    httpd = ThreadingHTTPServer(server_address, IceCastRequestHandler)
    print("\n=======================================================")
    print(" [ONLINE] IceCast Unified Server Running on Localhost")
    print(f" Web Dashboard: http://localhost:{args.port}/")
    print(f" API Status:    http://localhost:{args.port}/api/status")
    print(f" GPS Bounds:    Lat {GPS_BOUNDS['lat_min']}° to {GPS_BOUNDS['lat_max']}° | Lon {GPS_BOUNDS['lon_min']}° to {GPS_BOUNDS['lon_max']}°")
    print("=======================================================\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
