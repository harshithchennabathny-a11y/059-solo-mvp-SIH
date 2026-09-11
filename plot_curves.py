"""
IceCast — Generate Training & Testing Curves Plot
=================================================
Plots Train Loss vs Test Loss and MAE over epochs.
"""

import json
import matplotlib.pyplot as plt
import numpy as np

def main():
    history_file = "models/training_history.json"
    output_file = "training_curves.png"

    with open(history_file, "r") as f:
        data = json.load(f)

    history = data["history"]
    train_loss = history["train_loss"]
    test_loss = history["test_loss"]
    test_mae = history["test_mae"]
    epochs = list(range(1, len(train_loss) + 1))

    siconc_std = data["scaler_stats"]["siconc"]["std"]
    mae_orig = [m * siconc_std * 100 for m in test_mae]  # in percentage
    rmse_orig = [np.sqrt(l) * siconc_std * 100 for l in test_loss]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # 1. Loss Curve
    ax1.plot(epochs, train_loss, 'o-', color='#3B82F6', linewidth=2.2, label='Train Loss (MSE)')
    ax1.plot(epochs, test_loss, 's--', color='#10B981', linewidth=2.2, label='Test Loss (MSE)')
    ax1.set_xlabel('Epoch', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Mean Squared Error (MSE)', fontsize=12, fontweight='bold')
    ax1.set_title('Training vs Testing Loss Convergence', fontsize=13, fontweight='bold')
    ax1.grid(True, linestyle=':', alpha=0.6)
    ax1.set_yscale('log')
    ax1.legend(fontsize=11)

    # 2. Original Scale Error (% Ice Concentration)
    ax2.plot(epochs, rmse_orig, 'o-', color='#8B5CF6', linewidth=2.2, label='Test RMSE (% Conc)')
    ax2.plot(epochs, mae_orig, '^-', color='#F59E0B', linewidth=2.2, label='Test MAE (% Conc)')
    ax2.set_xlabel('Epoch', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Error Percentage (%)', fontsize=12, fontweight='bold')
    ax2.set_title('Test Error on Original Physical Scale', fontsize=13, fontweight='bold')
    ax2.grid(True, linestyle=':', alpha=0.6)
    ax2.legend(fontsize=11)

    plt.suptitle(f"IceCast ConvLSTM — Training & Evaluation Telemetry (Early Stopped at Epoch {len(epochs)})", fontsize=14, y=1.02)
    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.savefig("frontend/training_curves.png", dpi=150, bbox_inches='tight')
    print(f"Saved training curves to {output_file}")

if __name__ == "__main__":
    main()
