"""
IceCast Detailed Test Phase Evaluation
=======================================
Evaluates the trained ConvLSTM across all 357 test sequences
and breaks down performance by Sea Ice Regime.
"""

import os
import torch
import numpy as np
from train_model import IceCastModel, load_and_engineer_features, SeaIceDataset

def main():
    model_path = "models/icecast_convlstm_best.pt"
    data_path = "data/processed/weddell_sea_combined.nc"

    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    config = checkpoint["config"]
    scaler_stats = checkpoint["scaler_stats"]

    data, _ = load_and_engineer_features(data_path, downsample=config["downsample_factor"])

    n_time = data.shape[0]
    split_idx = int(n_time * config["train_ratio"])
    test_data = data[split_idx:]

    dataset = SeaIceDataset(test_data, config["lookback"], config["forecast_horizon"])
    print(f"\n=======================================================")
    print(f"       ICECAST TEST EVALUATION PHASE REPORT")
    print(f"=======================================================")
    print(f"Total Test Sequences:    {len(dataset)}")
    print(f"Test Temporal Coverage:  Dec 2023 - Feb 2024 (Holdout Period)")
    print(f"Spatial Grid Resolution: {data.shape[1]}x{data.shape[2]} ({config['downsample_factor']}x downsampled)")
    print(f"Input Features:          {len(checkpoint['feature_names'])} channels")

    model = IceCastModel(
        n_features=data.shape[-1],
        hidden_channels=config["convlstm_filters"],
        kernel_size=config["kernel_size"],
        dropout=0.0
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    siconc_mean = scaler_stats['siconc']['mean']
    siconc_std = scaler_stats['siconc']['std']

    all_actual = []
    all_pred = []

    print("\nRunning inference over all test sequences...")
    with torch.no_grad():
        for i in range(len(dataset)):
            x, y = dataset[i]
            x_t = x.unsqueeze(0)
            pred_norm = model(x_t).squeeze().numpy()
            
            # Denormalize to [0, 1] original sea ice concentration scale
            pred = np.clip(pred_norm * siconc_std + siconc_mean, 0.0, 1.0)
            act = np.clip(y.squeeze().numpy() * siconc_std + siconc_mean, 0.0, 1.0)
            
            all_actual.append(act)
            all_pred.append(pred)

    actuals = np.array(all_actual)
    preds = np.array(all_pred)
    errors = np.abs(actuals - preds)
    sq_errors = (actuals - preds)**2

    overall_mae = float(np.mean(errors))
    overall_rmse = float(np.sqrt(np.mean(sq_errors)))
    max_error = float(np.max(errors))

    # Breakdown by Ice Concentration Regime:
    # 1. Open Water (< 15%)
    # 2. Marginal Ice Zone (15% - 80%) - Shipping corridor
    # 3. Dense Pack Ice (> 80%)
    mask_open = (actuals < 0.15)
    mask_miz = (actuals >= 0.15) & (actuals <= 0.80)
    mask_pack = (actuals > 0.80)

    print("\n-------------------------------------------------------")
    print(" 1. OVERALL TEST METRICS (Original Scale: 0.0 to 1.0)")
    print("-------------------------------------------------------")
    print(f" Mean Absolute Error (MAE):     {overall_mae:.5f} ({overall_mae*100:.3f}% ice conc)")
    print(f" Root Mean Square Error (RMSE): {overall_rmse:.5f} ({overall_rmse*100:.3f}% ice conc)")
    print(f" Maximum Spatial Point Error:   {max_error:.5f}")
    print(f" Correlation Coefficient (R):   {float(np.corrcoef(actuals.flatten(), preds.flatten())[0, 1]):.4f}")

    print("\n-------------------------------------------------------")
    print(" 2. BREAKDOWN BY MARITIME ICE REGIME")
    print("-------------------------------------------------------")
    print(f" {'Regime':<25} | {'Grid Points':<12} | {'MAE':<10} | {'RMSE':<10}")
    print(f" {'-'*25} | {'-'*12} | {'-'*10} | {'-'*10}")

    for name, mask in [("Open Ocean (<15%)", mask_open),
                       ("Marginal Ice Zone (15-80%)", mask_miz),
                       ("Dense Pack Ice (>80%)", mask_pack)]:
        count = int(np.sum(mask))
        if count > 0:
            reg_mae = float(np.mean(errors[mask]))
            reg_rmse = float(np.sqrt(np.mean(sq_errors[mask])))
            print(f" {name:<25} | {count:<12,d} | {reg_mae:.5f}   | {reg_rmse:.5f}")

    print("=======================================================\n")

if __name__ == "__main__":
    main()
