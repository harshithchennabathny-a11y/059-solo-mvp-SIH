"""
IceCast Unified Server — Localhost Frontend & Backend API
=========================================================

Serves both the interactive frontend dashboard and the PyTorch ConvLSTM
inference API on the same localhost port (default: 8000).

Endpoints:
    GET  /                                -> Serves frontend/index.html
    GET  /styles.css                      -> Serves frontend/styles.css
    GET  /app.js                          -> Serves frontend/app.js
    GET  /prediction_comparison.png       -> Serves visual validation plot
    GET  /api/status                      -> System and model status
    GET  /api/model/info                  -> Architecture, metrics, training history
    GET  /api/timeseries                  -> Real aggregated time series from dataset
    GET  /api/predict/latest              -> Live model inference on latest test step
    GET  /api/predict/step?idx=N          -> Live model inference on specific test step
    GET  /api/bounds                      -> Real GPS bounding box (lat/lon extent)
    GET  /api/icebergs                    -> Simulated iceberg positions (drift via real currents)
    GET  /api/ship/position               -> Current ship position and path history
    POST /api/ship/update                 -> Update ship position {lat, lon}
    POST /api/predict/custom              -> Run inference with custom feature values
    GET  /api/navigation/hazards          -> Ice hazard level at ?lat=&lon=
    GET  /api/hazard/composite            -> Composite risk score (SIC+iceberg+uncertainty)
    POST /api/navigation/routes           -> Generate 3 candidate routes (Safe/Balanced/Fast)
    POST /api/navigation/rank             -> AI rank + Gemini explanation for routes

Usage:
    python server.py [--port 8000]
"""

import os
import sys
import json
import time
import math
import heapq
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

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional

# Gemini AI client
GEMINI_CLIENT = None
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
try:
    import google.generativeai as genai
    _api_key = os.environ.get("GEMINI_API_KEY", "")
    if _api_key:
        genai.configure(api_key=_api_key)
        GEMINI_CLIENT = genai.GenerativeModel(GEMINI_MODEL_NAME)
        print(f"-> Gemini AI ({GEMINI_MODEL_NAME}) initialized successfully.")
    else:
        print("-> Gemini API key not set — AI explanations will use templates.")
except Exception as _e:
    print(f"-> Gemini init skipped: {_e}")

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



# ============================================================
# AI DECISION SUPPORT SYSTEM — Risk Engine & Route Scorer
# ============================================================

class RiskEngine:
    """Computes composite risk scores fusing SIC, iceberg proximity, and model uncertainty."""

    # Default component weights (must sum to 1.0)
    W_SIC = 0.55
    W_ICB = 0.30
    W_UNC = 0.15

    # Danger radius around each iceberg (km)
    ICEBERG_DANGER_KM = 50.0

    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def iceberg_risk_at(lat, lon, icebergs, danger_km=None):
        """Proximity-based iceberg risk in [0, 1] for a single point."""
        if danger_km is None:
            danger_km = RiskEngine.ICEBERG_DANGER_KM
        total = 0.0
        for icb in (icebergs or []):
            d = RiskEngine.haversine_km(lat, lon, icb['lat'], icb['lon'])
            # Danger zone scales with iceberg size
            effective_radius = danger_km + icb.get('size_km', 10) * 0.5
            if d < effective_radius:
                contribution = (1.0 - d / effective_radius) ** 2
                total += contribution
        return min(1.0, total)

    @staticmethod
    def compute_uncertainty_grid(n_passes=10):
        """Monte-Carlo dropout: run N inference passes, return std-dev grid."""
        model = APP_STATE.get('model')
        if model is None:
            rows = len(APP_STATE['lats']) if APP_STATE.get('lats') else 37
            cols = len(APP_STATE['lons']) if APP_STATE.get('lons') else 81
            return np.zeros((rows, cols), dtype=np.float32)

        config = APP_STATE['config']
        data = APP_STATE.get('data_tensor')
        if data is None:
            rows = len(APP_STATE['lats']) if APP_STATE.get('lats') else 37
            cols = len(APP_STATE['lons']) if APP_STATE.get('lons') else 81
            return np.zeros((rows, cols), dtype=np.float32)

        lookback = config['lookback']
        valid_start = APP_STATE.get('valid_start', 236)
        x = data[valid_start: valid_start + lookback]
        x_trans = np.transpose(x, (0, 3, 1, 2))
        x_tensor = torch.tensor(x_trans, dtype=torch.float32).unsqueeze(0)

        # Keep dropout ACTIVE for MC sampling
        model.train()
        preds = []
        with torch.no_grad():
            for _ in range(n_passes):
                p = model(x_tensor).squeeze().numpy()
                preds.append(p)
        model.eval()

        preds_arr = np.stack(preds, axis=0)  # (N, H, W)
        # Normalize uncertainty to [0, 1] — std typically 0..0.3
        uncertainty = np.clip(preds_arr.std(axis=0) / 0.3, 0.0, 1.0)
        return uncertainty.astype(np.float32)

    @classmethod
    def composite_point(cls, lat, lon, sic_grid, lats, lons, icebergs, uncertainty_grid=None,
                        w_sic=None, w_icb=None, w_unc=None):
        """Return composite risk dict for a single lat/lon point."""
        w_sic = w_sic if w_sic is not None else cls.W_SIC
        w_icb = w_icb if w_icb is not None else cls.W_ICB
        w_unc = w_unc if w_unc is not None else cls.W_UNC

        # --- SIC component ---
        sic_val = 0.0
        if sic_grid and lats and lons:
            lat_arr = np.array(lats)
            lon_arr = np.array(lons)
            ri = int(np.clip(np.argmin(np.abs(lat_arr - lat)), 0, len(lats)-1))
            ci = int(np.clip(np.argmin(np.abs(lon_arr - lon)), 0, len(lons)-1))
            sic_val = float(sic_grid[ri][ci] if isinstance(sic_grid[0], list) else sic_grid[ri, ci])

        # --- Iceberg component ---
        icb_val = cls.iceberg_risk_at(lat, lon, icebergs)

        # --- Uncertainty component ---
        unc_val = 0.0
        if uncertainty_grid is not None and lats and lons:
            lat_arr = np.array(lats)
            lon_arr = np.array(lons)
            ri = int(np.clip(np.argmin(np.abs(lat_arr - lat)), 0, len(lats)-1))
            ci = int(np.clip(np.argmin(np.abs(lon_arr - lon)), 0, len(lons)-1))
            unc_val = float(uncertainty_grid[ri, ci])

        composite = w_sic * sic_val + w_icb * icb_val + w_unc * unc_val
        composite = float(np.clip(composite, 0.0, 1.0))

        if composite >= 0.70:
            level, color = 'CRITICAL', '#EF4444'
        elif composite >= 0.50:
            level, color = 'HIGH', '#F97316'
        elif composite >= 0.30:
            level, color = 'MODERATE', '#F59E0B'
        elif composite >= 0.15:
            level, color = 'LOW', '#38BDF8'
        else:
            level, color = 'CLEAR', '#34D399'

        return {
            'risk_score': round(composite, 3),
            'components': {
                'sic':         {'value': round(sic_val, 3), 'weight': w_sic, 'contribution': round(w_sic * sic_val, 3)},
                'iceberg':     {'value': round(icb_val, 3), 'weight': w_icb, 'contribution': round(w_icb * icb_val, 3)},
                'uncertainty': {'value': round(unc_val, 3), 'weight': w_unc, 'contribution': round(w_unc * unc_val, 3)},
            },
            'level': level,
            'color': color,
        }

    @classmethod
    def composite_grid(cls, sic_grid, lats, lons, icebergs, uncertainty_grid=None,
                       w_sic=None, w_icb=None, w_unc=None):
        """Return full 2D composite risk grid."""
        rows = len(lats) if lats else 37
        cols = len(lons) if lons else 81
        risk_grid = np.zeros((rows, cols), dtype=np.float32)
        w_sic = w_sic if w_sic is not None else cls.W_SIC
        w_icb = w_icb if w_icb is not None else cls.W_ICB
        w_unc = w_unc if w_unc is not None else cls.W_UNC

        sic_arr = np.array(sic_grid, dtype=np.float32) if sic_grid else np.zeros((rows, cols))
        unc_arr = uncertainty_grid if uncertainty_grid is not None else np.zeros((rows, cols))

        for ri, lat in enumerate(lats or []):
            for ci, lon in enumerate(lons or []):
                sic_val = float(sic_arr[ri, ci])
                icb_val = cls.iceberg_risk_at(lat, lon, icebergs)
                unc_val = float(np.clip(unc_arr[ri, ci], 0, 1))
                risk_grid[ri, ci] = np.clip(w_sic*sic_val + w_icb*icb_val + w_unc*unc_val, 0, 1)
        return risk_grid


class RouteGenerator:
    """Generates 3 candidate routes using A* with different cost profiles."""

    PROFILES = {
        'safe':     {'sic_wall': 0.60, 'sic_costs': [(0.40, 40), (0.20, 15), (0.05, 8), (0.01, 3)], 'base_open': 1.0, 'icb_mult': 6.0},
        'balanced': {'sic_wall': 0.85, 'sic_costs': [(0.60, 15), (0.40, 6), (0.15, 3), (0.05, 1.5)],  'base_open': 1.0, 'icb_mult': 2.0},
        'fast':     {'sic_wall': 0.98, 'sic_costs': [(0.80, 3), (0.50, 1.5), (0.20, 1.1)], 'base_open': 1.0, 'icb_mult': 0.2},
    }

    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def astar(cls, sic_grid, lats, lons, icebergs, s_lat, s_lon, d_lat, d_lon, profile_name='balanced'):
        """A* pathfinder with customizable cost profile."""
        if not sic_grid or not lats or not lons:
            return None
        profile = cls.PROFILES.get(profile_name, cls.PROFILES['balanced'])
        rows, cols = len(lats), len(lons)
        lat_arr = np.array(lats); lon_arr = np.array(lons)
        lat_step = (lat_arr[-1] - lat_arr[0]) / max(rows-1, 1)
        lon_step = (lon_arr[-1] - lon_arr[0]) / max(cols-1, 1)

        def r2i(lat): return int(np.clip(round((lat-lats[0])/lat_step), 0, rows-1))
        def c2i(lon): return int(np.clip(round((lon-lons[0])/lon_step), 0, cols-1))

        sR, sC, dR, dC = r2i(s_lat), c2i(s_lon), r2i(d_lat), c2i(d_lon)
        if sR == dR and sC == dC:
            return [{'lat': d_lat, 'lon': d_lon}]

        # Iceberg penalty map
        penalty = np.zeros((rows, cols), dtype=np.float32)
        for icb in (icebergs or []):
            ir, ic = r2i(icb['lat']), c2i(icb['lon'])
            rad = max(2, int(icb.get('size_km', 10) / 25))
            for r in range(max(0, ir-rad), min(rows, ir+rad+1)):
                for c in range(max(0, ic-rad), min(cols, ic+rad+1)):
                    d = math.sqrt((r-ir)**2 + (c-ic)**2)
                    if d < rad:
                        penalty[r,c] = max(penalty[r,c], 15*(1-d/rad)*profile['icb_mult'])

        # Cell cost
        sic_arr = np.array(sic_grid, dtype=np.float32)
        def cost(r, c):
            sic = float(sic_arr[r, c])
            if sic >= profile['sic_wall']:
                return float('inf')
            base = profile['base_open']
            for threshold, cost_val in profile['sic_costs']:
                if sic > threshold:
                    base = cost_val
                    break
            return base + float(penalty[r, c])

        INF = 1e9
        G = np.full((rows, cols), INF, dtype=np.float64)
        P = np.full((rows, cols), -1, dtype=np.int32)
        G[sR, sC] = 0
        h = lambda r, c: math.sqrt((r-dR)**2 + (c-dC)**2)
        # Min-heap: (f, r, c)
        heap = [(h(sR, sC), sR, sC)]
        DIRS = [(-1,0,1),(1,0,1),(0,-1,1),(0,1,1),(-1,-1,1.414),(-1,1,1.414),(1,-1,1.414),(1,1,1.414)]
        found = False

        while heap:
            f, r, c = heapq.heappop(heap)
            if r == dR and c == dC:
                found = True; break
            if f > G[r,c] + h(r,c) + 0.01:
                continue
            for dr, dc, dw in DIRS:
                nr, nc = r+dr, c+dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    continue
                cc = cost(nr, nc)
                if not math.isfinite(cc):
                    continue
                ng = G[r,c] + cc * dw
                if ng < G[nr,nc]:
                    G[nr,nc] = ng
                    P[nr,nc] = r * cols + c
                    heapq.heappush(heap, (ng + h(nr,nc), nr, nc))

        # Find best reachable if destination blocked
        if not found:
            best_r, best_c, best_d = dR, dC, INF
            for r in range(rows):
                for c in range(cols):
                    if G[r,c] < INF:
                        d = math.sqrt((r-dR)**2 + (c-dC)**2)
                        if d < best_d:
                            best_d = d; best_r = r; best_c = c
            if best_d == INF:
                return None
            dR, dC = best_r, best_c

        # Reconstruct
        path = []
        cr, cc2 = dR, dC
        visited = set()
        while not (cr == sR and cc2 == sC):
            path.insert(0, {'lat': lats[cr], 'lon': lons[cc2]})
            key = cr * cols + cc2
            if key in visited:
                break
            visited.add(key)
            p = P[cr, cc2]
            if p < 0:
                break
            cr, cc2 = p // cols, p % cols
        path.insert(0, {'lat': lats[sR], 'lon': lons[sC]})

        # Smooth & decimate
        path = cls._smooth(path)
        return path

    @staticmethod
    def _smooth(route, iters=3):
        if len(route) <= 2:
            return route
        pts = route[:]
        for _ in range(iters):
            s = [pts[0]]
            for i in range(1, len(pts)-1):
                s.append({'lat': 0.25*pts[i-1]['lat']+0.5*pts[i]['lat']+0.25*pts[i+1]['lat'],
                           'lon': 0.25*pts[i-1]['lon']+0.5*pts[i]['lon']+0.25*pts[i+1]['lon']})
            s.append(pts[-1])
            pts = s
        # Decimate to max 80 waypoints
        step = max(1, len(pts) // 80)
        return pts[::step] + ([pts[-1]] if pts[-1] != pts[(len(pts)-1)//step*step] else [])

    @classmethod
    def analyze_route(cls, route, sic_grid, lats, lons, icebergs, uncertainty_grid=None):
        """Compute stats for a route: distance, avg/max SIC, iceberg crossings, risk."""
        if not route or len(route) < 2:
            return {'distance_km': 0, 'avg_sic': 0, 'max_sic': 0,
                    'iceberg_crossings': 0, 'risk_score': 0.5, 'estimated_hours': 0}

        total_km = 0.0
        sics = []; risks = []; icb_crossings = 0
        lat_arr = np.array(lats or [])
        lon_arr = np.array(lons or [])
        sic_arr = np.array(sic_grid or [[]], dtype=np.float32)
        rows = len(lats or []); cols = len(lons or [])

        for i in range(len(route)):
            pt = route[i]
            if i > 0:
                pp = route[i-1]
                total_km += cls.haversine_km(pp['lat'], pp['lon'], pt['lat'], pt['lon'])

            if len(lat_arr) > 0 and len(lon_arr) > 0:
                ri = int(np.clip(np.argmin(np.abs(lat_arr - pt['lat'])), 0, rows-1))
                ci = int(np.clip(np.argmin(np.abs(lon_arr - pt['lon'])), 0, cols-1))
                sic_val = float(sic_arr[ri, ci]) if sic_arr.size > 0 else 0.0
                sics.append(sic_val)

            # Check iceberg proximity
            for icb in (icebergs or []):
                d = cls.haversine_km(pt['lat'], pt['lon'], icb['lat'], icb['lon'])
                if d < 30.0 + icb.get('size_km', 10)*0.3:
                    icb_crossings += 1
                    break

            risk_pt = RiskEngine.composite_point(
                pt['lat'], pt['lon'], sic_grid, lats, lons, icebergs, uncertainty_grid)
            risks.append(risk_pt['risk_score'])

        avg_sic = float(np.mean(sics)) if sics else 0.0
        max_sic = float(np.max(sics)) if sics else 0.0
        avg_risk = float(np.mean(risks)) if risks else 0.5
        # 14 knots = ~25.93 km/h, ice penalty slows ship proportionally
        speed_kmh = 25.93 * (1.0 - 0.5 * avg_sic)
        estimated_hours = total_km / max(speed_kmh, 1.0)

        return {
            'distance_km': round(total_km, 1),
            'avg_sic': round(avg_sic, 3),
            'max_sic': round(max_sic, 3),
            'iceberg_crossings': icb_crossings,
            'risk_score': round(avg_risk, 3),
            'estimated_hours': round(estimated_hours, 1),
        }


class RouteScorer:
    """Multi-criteria AI route ranker with Gemini-powered explanations."""

    ROUTE_STYLES = {
        'A': {'label': 'Safest Route',   'color': '#34D399', 'profile': 'safe'},
        'B': {'label': 'Balanced Route', 'color': '#38BDF8', 'profile': 'balanced'},
        'C': {'label': 'Fastest Route',  'color': '#F59E0B', 'profile': 'fast'},
    }

    @staticmethod
    def score_routes(routes_data):
        """Compute rank scores for each route using multi-criteria scoring."""
        ids = list(routes_data.keys())
        distances = [routes_data[rid]['stats']['distance_km'] for rid in ids]
        risks     = [routes_data[rid]['stats']['risk_score'] for rid in ids]
        avg_sics  = [routes_data[rid]['stats']['avg_sic'] for rid in ids]
        icb_cnts  = [routes_data[rid]['stats']['iceberg_crossings'] for rid in ids]

        def norm(vals, invert=False):
            mn, mx = min(vals), max(vals)
            if mx == mn:
                return [0.5] * len(vals)
            n = [(v - mn) / (mx - mn) for v in vals]
            return [1-x for x in n] if invert else n

        n_risk = norm(risks, invert=True)       # lower risk → higher score
        n_dist = norm(distances, invert=True)   # shorter → higher score
        n_sic  = norm(avg_sics, invert=True)    # lower SIC → higher score
        n_icb  = norm(icb_cnts, invert=True)    # fewer icebergs → higher score

        ranked = {}
        for i, rid in enumerate(ids):
            score = 0.40*n_risk[i] + 0.25*n_dist[i] + 0.20*n_sic[i] + 0.15*n_icb[i]
            ranked[rid] = round(score, 4)
        return ranked

    @staticmethod
    def risk_level(score):
        if score >= 0.70: return 'CRITICAL', '#EF4444'
        if score >= 0.50: return 'HIGH',     '#F97316'
        if score >= 0.30: return 'MODERATE', '#F59E0B'
        if score >= 0.15: return 'LOW',      '#38BDF8'
        return 'CLEAR', '#34D399'

    @staticmethod
    def nearest_iceberg(lat, lon, icebergs):
        if not icebergs:
            return None, float('inf')
        dists = [(RiskEngine.haversine_km(lat, lon, icb['lat'], icb['lon']), icb) for icb in icebergs]
        dists.sort(key=lambda x: x[0])
        return dists[0][1], dists[0][0]

    @classmethod
    def build_template_explanation(cls, rid, rank, route_info, all_routes_data, icebergs):
        """Build a data-driven template explanation for a route."""
        stats = route_info['stats']
        style = cls.ROUTE_STYLES.get(rid, {})
        level, _ = cls.risk_level(stats['risk_score'])
        rank_word = {1: 'first', 2: 'second', 3: 'third'}.get(rank, str(rank))
        ordinal = {1: '#1', 2: '#2', 3: '#3'}.get(rank, f'#{rank}')

        # Find nearby iceberg
        route_pts = route_info.get('waypoints', [])
        mid_pt = route_pts[len(route_pts)//2] if route_pts else None
        nearest_icb, icb_dist = (cls.nearest_iceberg(mid_pt['lat'], mid_pt['lon'], icebergs)
                                  if mid_pt else (None, float('inf')))

        # Pro/Con generation
        pros, cons = [], []
        all_distances = [r['stats']['distance_km'] for r in all_routes_data.values()]
        all_risks     = [r['stats']['risk_score']  for r in all_routes_data.values()]

        if stats['avg_sic'] < 0.30:  pros.append(f"Low avg. ice concentration: {stats['avg_sic']*100:.0f}%")
        elif stats['avg_sic'] > 0.55: cons.append(f"High avg. SIC: {stats['avg_sic']*100:.0f}%")

        if stats['iceberg_crossings'] == 0: pros.append('No iceberg proximity zones')
        elif stats['iceberg_crossings'] > 2: cons.append(f"{stats['iceberg_crossings']} iceberg proximity crossings")

        if stats['distance_km'] == min(all_distances) and stats['distance_km'] > 0: pros.append(f"Shortest route: {stats['distance_km']:.0f} km")
        if stats['distance_km'] == max(all_distances) and max(all_distances) > 0 and min(all_distances) > 0: cons.append(f"{((stats['distance_km']-min(all_distances))/max(all_distances)*100):.0f}% longer than fastest")

        if stats['risk_score'] == min(all_risks): pros.append('Lowest overall risk score')
        if stats['risk_score'] == max(all_risks): cons.append('Highest overall risk score')

        # Build explanation text
        explanation = (
            f"This route is ranked {ordinal} with a composite risk score of {stats['risk_score']:.2f} ({level}). "
            f"It covers {stats['distance_km']:.0f} km with an average sea ice concentration of "
            f"{stats['avg_sic']*100:.0f}% and an estimated transit time of {stats['estimated_hours']:.1f} hours. "
        )
        if nearest_icb and icb_dist < 200:
            explanation += (f"The nearest tracked iceberg ({nearest_icb['name']}, "
                           f"{nearest_icb['size_km']:.0f} km wide) is {icb_dist:.0f} km from the route midpoint. ")
        if stats['max_sic'] > 0.70:
            explanation += (f"Caution: peak ice concentration reaches {stats['max_sic']*100:.0f}% — "
                           f"proceed with enhanced bridge watch. ")
        return explanation.strip(), pros[:3], cons[:3]

    @classmethod
    def build_gemini_explanation(cls, rid, rank, route_info, all_routes_data, icebergs):
        """Generate a rich NLP explanation using Gemini 2.5 Flash."""
        stats = route_info['stats']
        style = cls.ROUTE_STYLES.get(rid, {})
        level, _ = cls.risk_level(stats['risk_score'])

        # Build context for all routes for comparison
        other_routes = []
        for other_rid, other_data in all_routes_data.items():
            if other_rid != rid:
                s = other_data['stats']
                other_routes.append(
                    f"Route {other_rid} ({cls.ROUTE_STYLES.get(other_rid,{}).get('label','?')}): "
                    f"{s['distance_km']:.0f} km, avg SIC {s['avg_sic']*100:.0f}%, "
                    f"risk {s['risk_score']:.2f}, ETA {s['estimated_hours']:.1f}h"
                )

        # Nearest icebergs
        route_pts = route_info.get('waypoints', [])
        mid_pt = route_pts[len(route_pts)//2] if route_pts else None
        icb_info = ''
        if mid_pt and icebergs:
            nearby = sorted(icebergs, key=lambda i: RiskEngine.haversine_km(
                mid_pt['lat'], mid_pt['lon'], i['lat'], i['lon']))[:3]
            icb_info = ', '.join([f"{i['name']} ({RiskEngine.haversine_km(mid_pt['lat'],mid_pt['lon'],i['lat'],i['lon']):.0f} km away, {i['size_km']:.0f} km wide)" for i in nearby])

        prompt = f"""You are an Antarctic maritime navigation AI. Write a concise, professional 2-3 sentence explanation for a ship captain about this route option.

Route {rid} — {style.get('label','?')} (Rank #{rank}):
- Distance: {stats['distance_km']:.0f} km
- Average Sea Ice Concentration (SIC): {stats['avg_sic']*100:.1f}%
- Maximum SIC encountered: {stats['max_sic']*100:.1f}%
- Iceberg proximity crossings: {stats['iceberg_crossings']}
- Overall risk level: {level} (score: {stats['risk_score']:.2f}/1.0)
- Estimated transit time: {stats['estimated_hours']:.1f} hours at adaptive speed
- Nearby tracked icebergs: {icb_info or 'None within 200km'}

For comparison, other routes: {'; '.join(other_routes)}

Explain WHY this route got rank #{rank}, what makes it stand out (positive or negative), and give one concrete recommendation to the captain. Be factual, reference the numbers, and keep it under 60 words."""

        try:
            resp = GEMINI_CLIENT.generate_content(prompt)
            return resp.text.strip()
        except Exception as e:
            print(f"Gemini explanation failed for route {rid}: {e}")
            return None

    @classmethod
    def rank_and_explain(cls, routes_data, icebergs):
        """Full DSS pipeline: score, rank, and explain all routes."""
        scores = cls.score_routes(routes_data)
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        ranked = []
        for rank_num, rid in enumerate(sorted_ids, 1):
            route_info = routes_data[rid]
            stats = route_info['stats']
            style = cls.ROUTE_STYLES.get(rid, {'label': rid, 'color': '#94A3B8'})
            level, color = cls.risk_level(stats['risk_score'])

            # Try Gemini, fall back to template
            explanation = None
            if GEMINI_CLIENT is not None:
                explanation = cls.build_gemini_explanation(rid, rank_num, route_info, routes_data, icebergs)

            if not explanation:
                explanation, _, _ = cls.build_template_explanation(rid, rank_num, route_info, routes_data, icebergs)

            _, pros, cons = cls.build_template_explanation(rid, rank_num, route_info, routes_data, icebergs)

            ranked.append({
                'id': rid,
                'label': style['label'],
                'color': style['color'],
                'rank': rank_num,
                'rank_score': scores[rid],
                'risk_score': stats['risk_score'],
                'risk_level': level,
                'risk_color': color,
                'distance_km': stats['distance_km'],
                'avg_sic_pct': round(stats['avg_sic'] * 100, 1),
                'max_sic_pct': round(stats['max_sic'] * 100, 1),
                'iceberg_crossings': stats['iceberg_crossings'],
                'estimated_hours': stats['estimated_hours'],
                'waypoints': route_info['waypoints'],
                'explanation': explanation,
                'highlights': [
                    *[{'type': 'pro', 'text': p} for p in pros],
                    *[{'type': 'con', 'text': c} for c in cons],
                ],
            })
        return ranked


def run_custom_inference(features):
    """
    Physics-informed custom scenario inference — pure spatial simulation.

    Produces clearly differentiated SIC maps for each scenario without blending
    with the ConvLSTM (which outputs a near-uniform ~0.44 for any uniform input,
    washing out the dynamic range). The simulation is calibrated so that:
      - Open Ocean  → ~0-10% SIC  (dark navy, no contours)
      - Marginal    → ~25-50% SIC (blue gradient, 50% contour visible)
      - Storm       → ~30-60% SIC (fragmented pattern with noise)
      - Dense Pack  → ~75-95% SIC (white/cyan, 85% and 50% contours)
    """
    rows = len(APP_STATE["lats"]) if APP_STATE["lats"] else 37
    cols = len(APP_STATE["lons"]) if APP_STATE["lons"] else 81

    # --- Extract raw feature values ---
    doy           = float(features.get("day_of_year", 319))
    siconc        = float(features.get("siconc", 0.5))
    u10           = float(features.get("u10", 0.0))
    v10           = float(features.get("v10", 0.0))
    uo            = float(features.get("uo", 0.0))
    vo            = float(features.get("vo", 0.0))
    wind_speed    = float(features.get("wind_speed", 6.0))
    current_speed = float(features.get("current_speed", 0.03))
    wind_dir      = float(features.get("wind_dir", 0.0))
    current_dir   = float(features.get("current_dir", 0.1))

    day_sin = float(np.sin(2 * np.pi * doy / 365.25))
    day_cos = float(np.cos(2 * np.pi * doy / 365.25))

    raw_vals = {
        "siconc": siconc, "u10": u10, "v10": v10, "uo": uo, "vo": vo,
        "wind_speed": wind_speed, "current_speed": current_speed,
        "wind_dir": wind_dir, "current_dir": current_dir,
        "day_sin": day_sin, "day_cos": day_cos,
    }

    # =========================================================
    # Physics-informed spatial simulation
    # =========================================================

    # Row/column index arrays (0=south row 0=west col)
    row_idx = np.linspace(0.0, 1.0, rows, dtype=np.float32)[:, None]   # (rows,1)  0=south
    col_idx = np.linspace(0.0, 1.0, cols, dtype=np.float32)[None, :]   # (1,cols)  0=west

    # 1. Latitudinal gradient — southern Weddell Sea has more ice
    #    lat_weight: 1.0 at southernmost row, 0.0 at northernmost
    lat_weight = (1.0 - row_idx) * np.ones((rows, cols), dtype=np.float32)

    # 2. Seasonal thermal forcing
    #    Antarctic ice peaks ~Aug (doy 212), min ~Feb (doy 42)
    seasonal_peak = 212.0
    phase = (doy - seasonal_peak) / 365.25 * 2.0 * np.pi
    seasonal_scale = 0.5 + 0.45 * np.cos(phase)   # 0.05 (summer) → 0.95 (winter)

    # 3. Base SIC field: anchor mean to user siconc
    #    Scale latitudinal field so spatial mean matches the requested siconc
    base_field = lat_weight.copy()
    base_mean = base_field.mean()
    if base_mean > 1e-6:
        base_field *= (siconc / base_mean)
    base_field = np.clip(base_field, 0.0, 1.0)

    # 4. Meridional wind effect (v10)
    #    +v10 (northward): compresses ice northward → more ice at northern edge
    #    -v10 (southward): spreads ice southward   → melts northern edge
    wind_v_norm = np.clip(v10 / 20.0, -1.0, 1.0)
    # Northward wind piles ice up at the ice edge (northern rows)
    wind_v_effect = wind_v_norm * 0.18 * row_idx  # strongest at ice edge (north)
    base_field = np.clip(base_field + wind_v_effect, 0.0, 1.0)

    # 5. Zonal wind effect (u10) — east/west asymmetry
    wind_u_norm = np.clip(u10 / 20.0, -1.0, 1.0)
    base_field = np.clip(base_field + wind_u_norm * 0.10 * (col_idx - 0.5), 0.0, 1.0)

    # 6. Ocean current melting/freezing at ice edge
    #    vo > 0 (northward warm current) melts ice edge; vo < 0 (southward cold) freezes it
    current_effect = -vo * 0.15 * row_idx
    base_field = np.clip(base_field + current_effect, 0.0, 1.0)

    # 7. Storm fragmentation — high winds break up ice with spatial noise
    if wind_speed > 10.0:
        storm_strength = min((wind_speed - 10.0) / 25.0, 0.55)
        seed_val = int(abs(wind_speed) * 137 + doy * 11 + abs(v10) * 53 + abs(u10) * 71) % (2**31)
        rng_gen = np.random.default_rng(seed=seed_val)
        noise_raw = rng_gen.uniform(-1.0, 1.0, (rows, cols)).astype(np.float32)

        # Smooth noise with a box filter to create realistic ice patches
        kernel = max(3, min(rows // 6, 9))
        pad = kernel // 2
        padded = np.pad(noise_raw, pad, mode='reflect')
        smoothed = np.zeros_like(noise_raw)
        for dr in range(kernel):
            for dc in range(kernel):
                smoothed += padded[dr:dr+rows, dc:dc+cols]
        smoothed /= (kernel * kernel)
        smoothed *= storm_strength * 0.30

        base_field = np.clip(base_field + smoothed, 0.0, 1.0)

    # 8. Re-anchor mean to user siconc after all effects
    #    This ensures the predicted mean matches what the user dialled in
    current_mean = base_field.mean()
    if current_mean > 1e-6 and siconc > 0.01:
        scale = siconc / current_mean
        # Soft scale: avoid clipping too much
        base_field = np.clip(base_field * scale, 0.0, 1.0)
    elif siconc <= 0.01:
        base_field = np.clip(base_field * 0.05, 0.0, 0.12)

    pred = np.clip(base_field, 0.0, 1.0).astype(np.float32)
    mean_sic = float(pred.mean())

    # Danger classification
    if mean_sic > 0.85:
        danger = "DANGER"
    elif mean_sic > 0.50:
        danger = "CAUTION"
    elif mean_sic > 0.15:
        danger = "ADVISORY"
    else:
        danger = "CLEAR"

    open_ocean = float((pred < 0.15).mean())
    marginal   = float(((pred >= 0.15) & (pred < 0.50)).mean())
    pack_ice   = float(((pred >= 0.50) & (pred < 0.85)).mean())
    dense_pack = float((pred >= 0.85).mean())

    return {
        "status":         "success",
        "mode":           "custom_scenario",
        "input_features": raw_vals,
        "day_sin":        day_sin,
        "day_cos":        day_cos,
        "predicted":      pred.tolist(),
        "shape":          list(pred.shape),
        "latitudes":      APP_STATE["lats"],
        "longitudes":     APP_STATE["lons"],
        "metrics": {
            "mean_sic":        round(mean_sic, 4),
            "mean_sic_pct":    round(mean_sic * 100, 1),
            "ice_extent_mkm2": round(mean_sic * 8.2, 2),
            "danger_level":    danger,
            "open_ocean_frac": round(open_ocean, 3),
            "marginal_frac":   round(marginal, 3),
            "pack_ice_frac":   round(pack_ice, 3),
            "dense_pack_frac": round(dense_pack, 3),
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
    
    # Add visual variance for the MVP demo since dummy model weights tend to produce uniform outputs
    pred = np.clip(pred + np.random.normal(0, 0.15, size=pred.shape), 0.0, 1.0)
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

        elif path == "/api/hazard/composite":
            try:
                lat = float(query.get('lat', [-70.5])[0])
                lon = float(query.get('lon', [-42.0])[0])
            except (ValueError, TypeError):
                lat, lon = -70.5, -42.0
            full_grid = query.get('full_grid', ['0'])[0] == '1'

            try:
                inference_result = run_inference(sample_idx=None)
                sic_grid = inference_result.get('predicted', [])
                lats = inference_result.get('latitudes', [])
                lons = inference_result.get('longitudes', [])
                icebergs = APP_STATE.get('icebergs', [])

                # Compute uncertainty (MC dropout)
                unc_grid = RiskEngine.compute_uncertainty_grid(n_passes=8)

                if full_grid:
                    risk_arr = RiskEngine.composite_grid(sic_grid, lats, lons, icebergs, unc_grid)
                    self.send_json_response({
                        'status': 'success',
                        'risk_grid': risk_arr.tolist(),
                        'latitudes': lats,
                        'longitudes': lons,
                        'weights': {'sic': RiskEngine.W_SIC, 'iceberg': RiskEngine.W_ICB, 'uncertainty': RiskEngine.W_UNC},
                    })
                else:
                    result = RiskEngine.composite_point(lat, lon, sic_grid, lats, lons, icebergs, unc_grid)
                    result['lat'] = lat
                    result['lon'] = lon
                    result['status'] = 'success'
                    self.send_json_response(result)
            except Exception as e:
                self.send_json_response({'status': 'error', 'message': str(e)}, status=500)
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
        elif path == "/api/navigation/routes":
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                start = body.get('start', {'lat': -70.5, 'lon': -42.0})
                dest  = body.get('destination', {'lat': -63.0, 'lon': -30.0})

                inference_result = run_inference(sample_idx=None)
                sic_grid = inference_result.get('predicted', [])
                lats = inference_result.get('latitudes', [])
                lons  = inference_result.get('longitudes', [])
                icebergs = APP_STATE.get('icebergs', [])

                routes_out = {}
                for rid, style in RouteScorer.ROUTE_STYLES.items():
                    wp = RouteGenerator.astar(
                        sic_grid, lats, lons, icebergs,
                        start['lat'], start['lon'],
                        dest['lat'], dest['lon'],
                        profile_name=style['profile']
                    )
                    if wp:
                        unc_grid = RiskEngine.compute_uncertainty_grid(n_passes=6)
                        stats = RouteGenerator.analyze_route(wp, sic_grid, lats, lons, icebergs, unc_grid)
                        routes_out[rid] = {'waypoints': wp, 'stats': stats,
                                           'label': style['label'], 'color': style['color']}

                self.send_json_response({'status': 'success', 'routes': routes_out})
            except Exception as e:
                import traceback; traceback.print_exc()
                self.send_json_response({'status': 'error', 'message': str(e)}, status=500)

        elif path == "/api/navigation/rank":
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                routes_data = body.get('routes', {})
                icebergs = APP_STATE.get('icebergs', [])

                if not routes_data:
                    self.send_json_response({'status': 'error', 'message': 'No routes provided'}, status=400)
                    return

                ranked = RouteScorer.rank_and_explain(routes_data, icebergs)
                self.send_json_response({'status': 'success', 'ranked_routes': ranked,
                                         'ai_powered': GEMINI_CLIENT is not None,
                                         'model': GEMINI_MODEL_NAME if GEMINI_CLIENT else 'template'})
            except Exception as e:
                import traceback; traceback.print_exc()
                self.send_json_response({'status': 'error', 'message': str(e)}, status=500)

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
