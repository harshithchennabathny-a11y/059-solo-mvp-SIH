import cdsapi

def test_era5():
    print("Testing ERA5 CDS API connection...")
    try:
        # Initialize the client. This will fail immediately if ~/.cdsapirc is missing or malformed.
        c = cdsapi.Client()
        print("Success! cdsapi.Client initialized.")
        print("Credentials were successfully found in ~/.cdsapirc.")
        print("We are ready to write the full download script.")
    except Exception as e:
        print(f"\nError connecting to CDS API: {e}")
        print("Make sure you have created the ~/.cdsapirc file in your home directory with your url and key.")

if __name__ == "__main__":
    test_era5()
