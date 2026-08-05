import sys
import pandas as pd # (or re / datetime depending on the script)
from pathlib import Path
from datetime import datetime
import re
# ==========================================
# DYNAMIC PATH RESOLUTION
# ==========================================
# 1. Find where this script lives (Inventory/Database_Builder/ingestion)
CURRENT_DIR = Path(__file__).resolve().parent

# 2. Define the Database_Builder folder and add it to sys.path so Python finds 'core'
DB_BUILDER_DIR = CURRENT_DIR.parent
if str(DB_BUILDER_DIR) not in sys.path:
    sys.path.append(str(DB_BUILDER_DIR))

# 3. Define the Data folder for CSVs/TXTs
DATA_DIR = DB_BUILDER_DIR.parent / "Data"

# Now you can safely import from core!
from core.database import SessionLocal
from core.models import Product, ProductVariant, InventorySnapshot, Brand


# Normalizes brand names
def clean_brand_name(raw_vendor: str) -> str:
    if not raw_vendor or pd.isna(raw_vendor) or str(raw_vendor).strip().lower() in ["nan", "none", ""]:
        return "Unbranded / Unknown"
    
    brand = str(raw_vendor).strip()
    
    # 1. Remove text inside parentheses (e.g., "(KOREAN VERSION)" -> "")
    brand = re.sub(r'\(.*?\)', '', brand)
    
    # 2. Normalize whitespace (collapses multiple spaces)
    brand = " ".join(brand.split())
    
    # 3. Standardize Casing (e.g., "Purito SEOUL" -> "Purito Seoul")
    brand = brand.title()
    
    # 4. Direct Overrides for descriptive noise or specific brand cleanups
    BRAND_OVERRIDES = {
        "Pure Beauty Collagen Made In Japan": "Pure Beauty",
        "Precious Skin Thailand": "Precious Skin",
        "The Ordinary": "The Ordinary",
        "Purito Seoul": "Purito",  # Optional: strip city suffixes if desired
        "Jennie Moon Skincare": "Jennie Moon",
        "Beauty&U": "Beauty & U"
    }
    
    return BRAND_OVERRIDES.get(brand, brand)

# Flattens strings for product titles
def clean_str(val):
    """Safely convert any cell (float, NaN, int, None) to a trimmed string."""
    if pd.isna(val) or val is None:
        return ""
    return str(val).strip()

def ingest_products(csv_filepath, snapshot_date_str):
    print(f"⚙️ Ingesting Products from {csv_filepath}...")
    
    df = pd.read_csv(csv_filepath, low_memory=False)
    snapshot_date = datetime.strptime(snapshot_date_str, "%Y-%m-%d").date()
    session = SessionLocal()
    
    added_products, added_variants, added_snapshots = 0, 0, 0
    seen_snapshots_in_run = set()  # Tracks variant_ids snapshotted during this CSV run

    try:
        for index, row in df.iterrows():
            parent_title = clean_str(row.get("Title"))
            if not parent_title or parent_title.lower() in ["nan", "none"]:
                continue # Skip empty rows safely
                
            raw_variant = clean_str(row.get("Option1 Value"))
            raw_sku = clean_str(row.get("Variant SKU"))
            
            # Sanitize SKU & Quantity
            # --- Sanitize SKU, Barcode & Quantity ---
            sku = raw_sku if raw_sku and raw_sku.lower() not in ["nan", "none"] else None
            
            # Extract and clean barcode (strip leading single quotes ' and trailing float .0)
            raw_barcode = clean_str(row.get("Variant Barcode")).lstrip("'")
            if raw_barcode.endswith(".0"):
                raw_barcode = raw_barcode[:-2]
            barcode = raw_barcode if raw_barcode and raw_barcode.lower() not in ["nan", "none"] else None

            raw_qty = pd.to_numeric(row.get("Variant Inventory Qty"), errors="coerce")
            qty = int(raw_qty) if pd.notna(raw_qty) else 0
            
            # NEW: Extract Pricing Data
            raw_price = row.get("Price / Bahrain")
            # If the column exists but the cell is empty (NaN), fallback to Variant Price
            if pd.isna(raw_price) or str(raw_price).strip() == "":
                raw_price = row.get("Variant Price")

            raw_price = pd.to_numeric(raw_price, errors="coerce")
            price = float(raw_price) if pd.notna(raw_price) else None

            # 2. Do the exact same for compare_at_price
            raw_compare = row.get("Compare At Price / Bahrain")
            if pd.isna(raw_compare) or str(raw_compare).strip() == "":
                raw_compare = row.get("Variant Compare At Price")
                
            raw_compare = pd.to_numeric(raw_compare, errors="coerce")
            compare_at_price = float(raw_compare) if pd.notna(raw_compare) else None

            # 3. Cost per item (unchanged as it has no fallback)
            raw_cost = pd.to_numeric(row.get("Cost per item"), errors="coerce")
            cost_per_item = float(raw_cost) if pd.notna(raw_cost) else None

            # Handle default/lazy Shopify variants
            if not raw_variant or raw_variant.lower() in ["default title", "nan", "none"]:
                variant_title = "Standard"
            else:
                variant_title = raw_variant
            
            vendor_val = clean_str(row.get("Vendor"))
            vendor = vendor_val if vendor_val else None

            cleaned_brand_name = clean_brand_name(vendor)
            brand = session.query(Brand).filter_by(clean_name=cleaned_brand_name).first()
            if not brand:
                brand = Brand(clean_name=cleaned_brand_name)
                session.add(brand)
                session.flush() # Secure the brand_id

            # --- Upsert Parent Product ---
            product = session.query(Product).filter_by(product_title=parent_title).first()
            if not product:
                product = Product(
                    product_title=parent_title, 
                    vendor=vendor,         # Preserved raw string for auditing
                    brand_id=brand.brand_id # Foreign key reference to normalized table
                )
                session.add(product)
                session.flush()
                added_products += 1
            else:
                # Retroactively link existing products if brand_id was missing
                if not product.brand_id:
                    product.brand_id = brand.brand_id
                    session.flush()
                
            # --- Upsert Product Variant ---
            # Match strictly on parent product and title to prevent duplicate variants when barcodes change
            variant = session.query(ProductVariant).filter_by(
                upk_id=product.upk_id, 
                variant_title=variant_title
            ).first()

            if not variant:
                variant = ProductVariant(
                    upk_id=product.upk_id, 
                    variant_title=variant_title, 
                    sku=sku,
                    variant_barcode=barcode, # Comma added here
                    price=price,                       # NEW
                    compare_at_price=compare_at_price, # NEW
                    cost_per_item=cost_per_item,       # NEW
                    first_seen_date=snapshot_date,
                    last_seen_date=snapshot_date,  
                    is_active=True                 
                )
                session.add(variant)
                session.flush()
                added_variants += 1
            else:
                # Update existing variant attributes retroactively
                if sku and not variant.sku:
                    variant.sku = sku
                if barcode and not variant.variant_barcode:
                    variant.variant_barcode = barcode
                    
                # NEW: Keep pricing up to date for existing variants
                variant.price = price
                variant.compare_at_price = compare_at_price
                variant.cost_per_item = cost_per_item
                
                # Update auditing fields for existing items
                variant.last_seen_date = snapshot_date
                variant.is_active = True
                session.flush()

            # --- Upsert Inventory Snapshot ---
            if variant.variant_id not in seen_snapshots_in_run:
                existing_snap = session.query(InventorySnapshot).filter_by(
                    variant_id=variant.variant_id, 
                    snapshot_date=snapshot_date
                ).first()
                
                if not existing_snap:
                    new_snap = InventorySnapshot(
                        variant_id=variant.variant_id, 
                        inventory_qty=qty, 
                        snapshot_date=snapshot_date
                    )
                    session.add(new_snap)
                    added_snapshots += 1
                
                seen_snapshots_in_run.add(variant.variant_id)

        missing_variants = session.query(ProductVariant).filter(
            ProductVariant.last_seen_date < snapshot_date,
            ProductVariant.is_active == True
        ).all()
        
        deactivated_count = 0
        for missing_var in missing_variants:
            missing_var.is_active = False
            deactivated_count += 1

        session.commit()
        print(f"✅ Success: {added_products} new Products, {added_variants} new Variants, {added_snapshots} Snapshots.")
        if deactivated_count > 0:
            print(f"⚠️ Flagged {deactivated_count} missing variants as inactive.")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error during ingestion: {e}")
    finally:
        session.close()
