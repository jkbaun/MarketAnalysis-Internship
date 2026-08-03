from pathlib import Path
from build_brand_master import main as run_brands
from scrape_to_stage import main as run_scrape
from build_product_master import main as run_products
# Optional: import your main functions directly if you prefer
# from Competiton.2_Unify.build_brand_master import main as run_brands
# from Competiton.2_Unify.build_product_master import main as run_products

def run_pipeline():
    print("=== STARTING E-COMMERCE PIPELINE ===")
    
    # Step 1: Scrape to Stage
    print("\n[Step 1/3] Running Staging Ingestion...")
    run_scrape()
    
    # Step 2: Build Brand Master
    print("\n[Step 2/3] Running Brand Master Integration...")
    run_brands()
    
    # Step 3: Build Product Master & Stock Snapshots
    print("\n[Step 3/3] Running Product Master Waterfall & Stock Migration...")
    run_products()
    
    print("\n=== PIPELINE EXECUTION COMPLETE ===")

if __name__ == "__main__":
    run_pipeline()