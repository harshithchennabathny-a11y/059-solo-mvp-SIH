import subprocess
import sys

def test_copernicus():
    print("Testing Copernicus Marine CLI and Dataset ID...")
    dataset_id = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
    try:
        import os
        exe_path = os.path.join(sys.prefix, "Scripts", "copernicusmarine.exe")
        result = subprocess.run(
            [exe_path, "describe", "--dataset-id", dataset_id],
            capture_output=True, text=True, check=True
        )
        print(f"Success! Found dataset ID: {dataset_id}")
        
        lines = result.stdout.split('\n')
        print("Metadata preview:")
        print('\n'.join(lines[:15]))
        print("...")
        
    except subprocess.CalledProcessError as e:
        print(f"\nError: Could not describe dataset '{dataset_id}'.")
        print("1. Did you run 'copernicusmarine login' in the terminal?")
        print("2. The dataset ID might have been renamed or deprecated.")
        print("\nError output:")
        print(e.stderr)

if __name__ == "__main__":
    test_copernicus()
