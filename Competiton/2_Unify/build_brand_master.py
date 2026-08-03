import os
import re
import sqlite3
import pandas as pd
from pathlib import Path
from sqlalchemy.orm import sessionmaker

from core.cleaning_models import init_central_db, DimBrand, BrandAlias

# =====================================================================
# EXTRACTION & CLEANING LOGIC
# =====================================================================
TAG_BLOCKLIST = {
    'skincare', 'bodycare', 'haircare', 'makeup', 'cleanser', 'toner', 
    'serum', 'cream', 'sunscreen', 'mask', 'moisturizer', 'balm', 'ampoule',
    'sale', 'bestseller', 'best-seller', 'new', 'trending', 'featured', 
    'free-shipping', 'in-stock', 'out-of-stock', 'bundle', 'mini', 'travel-size',
    'soko', 'soko-store', 'all-products', 'frontpage', 'nan', 'none', 'unknown'
}

BRAND_OVERRIDES = {
    "Haggard": "Haggaard",
    "dr althea": "Dr. Althea",
    "dralthea": "Dr. Althea",
    "cos rx": "COSRX",
}

def clean_string(text):
    """Removes non-alphanumeric characters for standardizing comparisons."""
    if not isinstance(text, str):
        return ""
    return re.sub(r'[^a-z0-9]', '', text.lower())

def extract_raw_brand_data(staging_db_dir: Path):
    """Connects to staging DBs and extracts vendor, tags, and titles."""
    all_data = []
    
    for db_file in staging_db_dir.glob("competitor_*.db"):
        company_name = db_file.stem.replace("competitor_", "")
        
        conn = sqlite3.connect(db_file)
        query = """
            SELECT 
                p.id as product_id, 
                p.vendor, 
                p.title, 
                GROUP_CONCAT(t.name) as raw_tags
            FROM products p
            LEFT JOIN tags t ON p.id = t.product_id
            GROUP BY p.id
        """
        df = pd.read_sql_query(query, conn)
        df['origin_company'] = company_name
        all_data.append(df)
        conn.close()

        bbk_db = staging_db_dir / "beautybykat_inventory.db"
    if bbk_db.exists():
        conn = sqlite3.connect(bbk_db)
        # Map BBK's schema (upk_id, product_title) to the expected DataFrame columns
        query = """
            SELECT 
                upk_id as product_id, 
                vendor, 
                product_title as title, 
                '' as raw_tags 
            FROM products
        """
        df_bbk = pd.read_sql_query(query, conn)
        df_bbk['origin_company'] = 'beautybykat'
        all_data.append(df_bbk)
        conn.close()
        
        
    return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()

def build_brand_master(df):
    """Pass 1: Build the definitive list of trusted canonical brands."""
    trusted_mask = ~df["vendor"].astype(str).str.lower().str.contains("soko", na=False)
    unique_vendors = df[trusted_mask]["vendor"].dropna().unique()
    
    directory = {}
    for v in unique_vendors:
        raw_v = str(v).strip()
        cleaned_key = clean_string(raw_v)
        
        if cleaned_key in BRAND_OVERRIDES:
            directory[cleaned_key] = BRAND_OVERRIDES[cleaned_key]
        else:
            directory[cleaned_key] = raw_v

    # Apply remaining manual dict overrides
    for raw_key, canon_val in BRAND_OVERRIDES.items():
        directory[clean_string(raw_key)] = canon_val
        
    return directory

def resolve_row_brand(row, directory):
    """Pass 2: 3-Tier resolution to map every product to a canonical brand."""
    raw_vendor = str(row.get("vendor", "")).strip()
    raw_tags = str(row.get("raw_tags", ""))
    raw_title = str(row.get("title", ""))
    
    # Tier 1: Trusted Native Vendor (Ignore Soko's generic vendor name)
    if raw_vendor and "soko" not in raw_vendor.lower() and raw_vendor.lower() != "unknown":
        norm_v = clean_string(raw_vendor)
        if norm_v in directory:
            return directory[norm_v], "Tier 1: Native Vendor", raw_vendor

    # Tier 2: Filtered Tag Extraction (For Soko)
    if raw_tags and raw_tags.lower() not in ["nan", "none"]:
        tags = [t.strip().lower() for t in raw_tags.split(',')]
        for tag in tags:
            clean_tag = clean_string(tag)
            if clean_tag in TAG_BLOCKLIST or tag in TAG_BLOCKLIST:
                continue
            if clean_tag in directory:
                return directory[clean_tag], "Tier 2: Filtered Tag", tag

    # Tier 3: Title Regex Fallback
    title_norm = clean_string(raw_title)
    for norm_brand_key, canonical_name in directory.items():
        if len(norm_brand_key) > 3 and norm_brand_key in title_norm:
            return canonical_name, "Tier 3: Title Regex", raw_title[:50]

    return "Unknown Brand", "Tier 4: Unresolved", raw_vendor

# =====================================================================
# EXECUTION
# =====================================================================
# =====================================================================
# EXECUTION
# =====================================================================
def main():
    data_dir = Path("/home/boredom-speaking/Desktop/JulyInternship/Pipeline/Data")
    staging_db_dir = data_dir / "databases"
    central_db_path = data_dir / "databases/master_competitor.db"
    
    # 1. Initialize Central Database (Safe to run on existing DB)
    engine = init_central_db(f"sqlite:///{central_db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    # --- NEW: MEMORY LOADING FOR APPEND-ONLY BEHAVIOR ---
    print("[+] Loading existing brands and aliases into memory...")
    
    existing_brands = session.query(DimBrand).all()
    brand_id_map = {b.canonical_name: b.brand_id for b in existing_brands}
    
    # Find the highest existing brand ID counter to prevent overlaps
    brand_counter = 1000
    if existing_brands:
        existing_counters = []
        for b in existing_brands:
            try:
                # Extracts the number from "BRD-XXX-1005"
                num_part = int(b.brand_id.split('-')[-1])
                existing_counters.append(num_part)
            except ValueError:
                pass
        if existing_counters:
            brand_counter = max(existing_counters) + 1

    existing_aliases = session.query(BrandAlias).all()
    # Store tuples of (raw_input, company, brand_id) to check against
    seen_aliases = {(a.raw_input_string, a.origin_company, a.brand_id) for a in existing_aliases}
    # ----------------------------------------------------

    # 2. Extract Data from Staging
    print("[+] Extracting data from local competitor staging databases...")
    df = extract_raw_brand_data(staging_db_dir)
    if df.empty:
        print("[-] No data found in staging databases. Exiting.")
        return

    # 3. Build Canonical Directory
    print("[+] Building Reference Brand Directory...")
    brand_directory = build_brand_master(df)
    
    # 4. Generate Brand IDs (Only add new ones)
    new_brand_count = 0
    for norm_key, canon_name in brand_directory.items():
        if canon_name not in brand_id_map:
            prefix = clean_string(canon_name).upper()[:3]
            b_id = f"BRD-{prefix}-{brand_counter}"
            brand_id_map[canon_name] = b_id
            brand_counter += 1
            
            session.add(DimBrand(brand_id=b_id, canonical_name=canon_name))
            new_brand_count += 1
            
    # Add the Unknown bucket safely
    if "Unknown Brand" not in brand_id_map:
        brand_id_map["Unknown Brand"] = "BRD-UNK-0000"
        session.add(DimBrand(brand_id="BRD-UNK-0000", canonical_name="Unknown Brand"))
        
    session.commit()

    # 5. Resolve Every Product and create mapping aliases (Only add new ones)
    print("[+] Resolving Store Mappings (Handling Soko Tags)...")
    new_alias_count = 0
    
    for _, row in df.iterrows():
        company = str(row.get("origin_company", ""))
        canon_brand, match_type, raw_trigger = resolve_row_brand(row, brand_directory)
        b_id = brand_id_map[canon_brand]
        
        # Ensure the string is truncated to match the DB limit like the original script
        raw_trigger_clean = raw_trigger[:240] 
        alias_key = (raw_trigger_clean, company, b_id)
        
        if alias_key not in seen_aliases:
            seen_aliases.add(alias_key)
            session.add(BrandAlias(
                raw_input_string=alias_key[0], 
                origin_company=alias_key[1],
                brand_id=alias_key[2],
                match_type=match_type
            ))
            new_alias_count += 1

    session.commit()
    print(f"[SUCCESS] Brand Master Database updated at: {central_db_path}")
    print(f"          Total Canonical Brands: {len(brand_id_map)} ({new_brand_count} freshly inserted)")
    print(f"          Total Alias Mappings: {len(seen_aliases)} ({new_alias_count} freshly inserted)")

if __name__ == "__main__":
    main()