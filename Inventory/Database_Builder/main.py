import os
from pathlib import Path
from core.database import init_db
from ingestion.ingest_products import ingest_products
from ingestion.ingest_order import ingest_orders
from ingestion.ingest_invoices import ingest_invoice_text
from ingestion.link_lineitems import resolve_unlinked_line_items
# Anchor to the folder where main.py actually lives (Database_Builder)
BASE_DIR = Path(__file__).resolve().parent

# Navigate up one level to 'Inventory', then down into 'Data/exports'
EXPORTS_DIR = (BASE_DIR.parent).parent / "Data" / "exports"

def run_pipeline(product_path, order_path, invoice_data, ):
    print("🚀 STARTING E-COMMERCE DATA PIPELINE...\n")
    
    # Step 1: Build Database Schema
    init_db()
    
    # Step 2: Product Catalog Ingestion
    print("\n--- PHASE 1: INGESTING PRODUCT CATALOG & SNAPSHOTS ---")
    
    for filepath, date_str in product_path:
        if filepath.exists():
            ingest_products(str(filepath), date_str)
        else:
            print(f"⚠️ File not found: {filepath}")
            
    # Step 3: Order Exports Ingestion
    print("\n--- PHASE 2: INGESTING ORDERS ---")

    for filepath in order_path:
        if filepath.exists():
            ingest_orders(str(filepath), date_str)
        else:
            print(f"⚠️ File not found: {filepath}")
            
    # Step 4: Invoice Ingestion
    print("\n--- PHASE 3: INGESTING SUPPLIER INVOICES ---")
    
    # Unpack the tuple right here in the loop
    for filepath, invoice_num, invoice_date in invoice_data:
        if filepath.exists():
            # Pass the dynamically extracted variables!
            ingest_invoice_text(str(filepath), invoice_num, invoice_date)
        else:
            print(f"⚠️ File not found: {filepath}")

    # Step 5: Alias Resolution
    print("\n--- PHASE 4: RESOLVING ALIASES & LINKING DATA ---")
    resolve_unlinked_line_items()

    print("\n🎉 PIPELINE EXECUTION COMPLETE!")
