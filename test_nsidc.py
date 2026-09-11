import earthaccess

def test_nsidc_search():
    print("Authenticating with NASA Earthdata...")
    # This will prompt for credentials the first time it runs if ~/.netrc isn't set up.
    earthaccess.login()
    
    print("\nSearching for NSIDC Sea Ice Concentration CDR (G02202 v6)...")
    results = earthaccess.search_data(
        short_name="G02202",
        version="6",
        temporal=("2023-10-01", "2024-02-28"),
        bounding_box=(-60, -78, -20, -60)  # (west, south, east, north)
    )
    
    print(f"\nFound {len(results)} files matching the criteria.")
    if len(results) > 0:
        print("First few results:")
        for r in results[:3]:
            print(f"- {r.size()} MB | {r.urllinks()[0] if r.urllinks() else 'No URL'}")

if __name__ == "__main__":
    test_nsidc_search()
