import re
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from rapidfuzz import fuzz

from core.cleaning_models import (
    init_central_db, DimBrand, BrandAlias, 
    DimUnifiedProduct, FactStoreVariant, FactStockSnapshot, AuditFuzzyMatch
)

# =====================================================================
# CONFIGURATION & CURRENCY CONVERSION RATES
# =====================================================================
FUZZY_THRESHOLD = 85.0  # Fuzzy matching threshold (85%+)

CURRENCY_TO_BHD = {
    "BHD": 1.0,
    "AED": 0.10238,
    "USD": 0.376
}

STORE_CURRENCIES = {
    "xbeauty": "AED",
    "main_company": "BHD",
    "soko": "BHD",
    "sokostore": "BHD"
}

TAG_BLOCKLIST = {
    'skincare', 'bodycare', 'haircare', 'makeup', 'cleanser', 'toner', 
    'serum', 'cream', 'sunscreen', 'mask', 'moisturizer', 'balm', 'ampoule',
    'sale', 'bestseller', 'best-seller', 'new', 'trending', 'featured', 
    'free-shipping', 'in-stock', 'out-of-stock', 'bundle', 'mini', 'travel-size',
    'soko', 'soko-store', 'all-products', 'frontpage', 'nan', 'none', 'unknown'
}

# =====================================================================
# UTILITY CLEANING FUNCTIONS
# =====================================================================
def get_core_product_key(title: str, brand_name: str = "") -> str:
    """
    Normalizes product titles by stripping Arabic/Non-ASCII characters, 
    measurement values, and canonical brand names to form a clean matching key.
    """
    if not title:
        return ""
    
    # 1. Strip Non-ASCII (Arabic characters, emojis, etc.)
    text = re.sub(r'[^\x00-\x7F]+', ' ', title)
    
    # 2. Strip Measurements (e.g., 100ml, 200g, 1.5fl oz, 10ea)
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s*(ml|g|oz|kg|l|fl\s*oz|ea)\b', ' ', text, flags=re.IGNORECASE)
    
    # 3. Strip Canonical Brand Name
    if brand_name and brand_name.lower() != "unknown brand":
        pattern = re.compile(re.escape(brand_name), re.IGNORECASE)
        text = pattern.sub(' ', text)
        
    # 4. Lowercase & strip non-alphanumeric characters
    clean_key = re.sub(r'[^a-z0-9]', '', text.lower())
    
    return clean_key

def clean_id_str(val) -> str:
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s

def clean_string(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r'[^a-z0-9]', '', text.lower())

def construct_full_title(prod_title: str, var_title: str) -> str:
    """Combines product title and variant title when variant title is meaningful."""
    p_title = str(prod_title).strip() if prod_title else ""
    v_title = str(var_title).strip() if var_title else ""
    
    if not v_title or v_title.lower() in ["default title", "default", "none", "nan"]:
        return p_title
    
    # Avoid duplicate appending if product title already ends with variant title
    if v_title.lower() in p_title.lower():
        return p_title
        
    return f"{p_title} {v_title}"

def extract_spec(title: str) -> str:
    """Extracts volume/weight specs (e.g., 50ml, 100g, 150ml) from titles."""
    if not title:
        return ""
    match = re.search(r'(\d+(?:\.\d+)?)\s*(ml|g|oz|kg|l|fl\s*oz)\b', title, re.IGNORECASE)
    if match:
        return f"{match.group(1)}{match.group(2).lower()}"
    return ""

def convert_to_bhd(price: float, currency: str) -> float:
    if price is None:
        return 0.0
    rate = CURRENCY_TO_BHD.get(currency.upper(), 1.0)
    return round(price * rate, 3)

# =====================================================================
# EXTRACTION & BRAND RESOLUTION
# =====================================================================
def extract_staging_variants(staging_db_dir: Path):
    """Loads all variants + product tags across competitor staging databases."""
    all_variants = []
    
    for db_file in staging_db_dir.glob("competitor_*.db"):
        company_name = db_file.stem.replace("competitor_", "")
        conn = sqlite3.connect(db_file)
        
        query = """
            SELECT 
                p.id as product_id,
                p.title as product_title,
                p.vendor,
                GROUP_CONCAT(t.name) as raw_tags,
                v.id as variant_id,
                v.title as variant_title,
                v.sku,
                v.barcode,
                v.price,
                v.compare_at_price
            FROM variants v
            JOIN products p ON v.product_id = p.id
            LEFT JOIN tags t ON p.id = t.product_id
            GROUP BY v.id
        """
        df = pd.read_sql_query(query, conn)
        df['origin_company'] = company_name
        df['currency'] = STORE_CURRENCIES.get(company_name, "BHD")
        all_variants.append(df)
        conn.close()

    bbk_db = staging_db_dir / "beautybykat_inventory.db"
    if bbk_db.exists():
        conn = sqlite3.connect(bbk_db)
        query = """
            SELECT 
                p.upk_id as product_id,
                p.product_title,
                p.vendor,
                '' as raw_tags,
                v.variant_id,
                v.variant_title,
                v.sku,
                v.variant_barcode as barcode,
                0.0 as price, 
                0.0 as compare_at_price
            FROM product_variants v
            JOIN products p ON v.upk_id = p.upk_id
        """
        df_bbk = pd.read_sql_query(query, conn)
        df_bbk['origin_company'] = 'beautybykat'
        df_bbk['currency'] = 'BHD' 
        all_variants.append(df_bbk)
        conn.close()

    return pd.concat(all_variants, ignore_index=True) if all_variants else pd.DataFrame()

def load_brand_directory(session):
    """Loads canonical brand lookup maps from central DB."""
    brands = session.query(DimBrand).all()
    brand_id_to_name = {b.brand_id: b.canonical_name for b in brands}
    name_to_brand_id = {clean_string(b.canonical_name): b.brand_id for b in brands}
    return brand_id_to_name, name_to_brand_id

def resolve_variant_brand(row, name_to_brand_id):
    """Resolves brand dynamically per variant using Vendor -> Tags -> Title."""
    raw_vendor = str(row.get("vendor", "")).strip()
    raw_tags = str(row.get("raw_tags", ""))
    raw_title = str(row.get("product_title", ""))

    # Tier 1: Native Vendor
    if raw_vendor and "soko" not in raw_vendor.lower() and raw_vendor.lower() != "unknown":
        clean_v = clean_string(raw_vendor)
        if clean_v in name_to_brand_id:
            return name_to_brand_id[clean_v]

    # Tier 2: Filtered Tags (Essential for Sokostore)
    if raw_tags and raw_tags.lower() not in ["nan", "none"]:
        tags = [t.strip().lower() for t in raw_tags.split(',')]
        for tag in tags:
            clean_tag = clean_string(tag)
            if clean_tag in TAG_BLOCKLIST or tag in TAG_BLOCKLIST:
                continue
            if clean_tag in name_to_brand_id:
                return name_to_brand_id[clean_tag]

    # Tier 3: Title Fallback
    title_norm = clean_string(raw_title)
    for norm_brand_key, b_id in name_to_brand_id.items():
        if len(norm_brand_key) > 3 and norm_brand_key in title_norm:
            return b_id

    return "BRD-UNK-0000"

# =====================================================================
# MAIN PIPELINE
# =====================================================================
def main():
    data_dir = Path("/home/boredom-speaking/Desktop/JulyInternship/Pipeline/Data")
    staging_db_dir = data_dir / "databases"
    central_db_path = data_dir / "databases/master_competitor.db"
    
    engine = init_central_db(f"sqlite:///{central_db_path}")
    Session = sessionmaker(bind=engine)
    session = Session()

    print("[+] Loading Brand Directory from Central DB...")
    brand_id_to_name, name_to_brand_id = load_brand_directory(session)

    print("[+] Extracting raw variants and tags from staging databases...")
    variants_df = extract_staging_variants(staging_db_dir)
    if variants_df.empty:
        print("[-] No variants found in staging DBs. Exiting.")
        return

    # Sort so main_company products act as Waterfall anchors
    variants_df['is_main'] = variants_df['origin_company'] == 'main_company'
    variants_df = variants_df.sort_values(by='is_main', ascending=False)

    upk_by_barcode = {}
    upk_by_core_spec_brand = {}
    core_to_counter_map = {}
    upk_fuzzy_registry = {}  # Scoped registry: {(b_id, spec): [(core_key, upk_id), ...]}
    
    upk_counter = 1000
    store_variant_records = []
    
    print("[+] Executing Waterfall Matching Algorithm...")
    
    for _, row in variants_df.iterrows():
        company = str(row['origin_company'])
        b_id = resolve_variant_brand(row, name_to_brand_id)
        brand_canonical_name = brand_id_to_name.get(b_id, "")

        barcode = str(row['barcode']).strip() if row['barcode'] and str(row['barcode']).strip() not in ['None', 'nan', ''] else None
        sku = str(row['sku']).strip() if row['sku'] and str(row['sku']).strip() not in ['None', 'nan', ''] else None
        
        full_title = construct_full_title(row['product_title'], row['variant_title'])
        
        # Core Upgrades: Extract Measurement Spec & Alphanumeric Core Key
        spec = extract_spec(full_title)
        core_key = get_core_product_key(full_title, brand_canonical_name)
        
        matched_upk = None
        match_tier = None
        match_key = None
        
        # --- WATERFALL MATCHING ---

        # Tier 1: Valid Store Barcode (>= 8 chars to avoid proprietary short codes)
        if barcode and len(barcode) >= 8 and barcode in upk_by_barcode:
            matched_upk = upk_by_barcode[barcode]
            match_tier = "Tier 1: Barcode"
            match_key = barcode

        # Tier 2: Exact Core Key + Spec + Brand
        elif (core_key, spec, b_id) in upk_by_core_spec_brand:
            matched_upk = upk_by_core_spec_brand[(core_key, spec, b_id)]
            match_tier = "Tier 2: Exact Core + Spec + Brand"
            match_key = f"{core_key[:30]}_{spec}_{b_id}"

        # Tier 2.5: Fuzzy Matching within Same Brand + Spec Group
        else:
            best_score = 0.0
            best_candidate_upk = None
            best_candidate_key = None

            candidate_list = upk_fuzzy_registry.get((b_id, spec), [])
            
            for cand_key, cand_upk in candidate_list:
                score = fuzz.token_sort_ratio(core_key, cand_key)
                if score > best_score:
                    best_score = score
                    best_candidate_upk = cand_upk
                    best_candidate_key = cand_key

            if best_score >= FUZZY_THRESHOLD and best_candidate_upk:
                matched_upk = best_candidate_upk
                match_tier = f"Tier 2.5: Fuzzy ({best_score:.1f}%)"
                match_key = f"{core_key[:20]}_vs_{best_candidate_key[:20]}"

                # Log Accepted Audit Record
                session.add(AuditFuzzyMatch(
                    origin_company=company,
                    store_variant_id=str(row['variant_id']),
                    candidate_upk_id=best_candidate_upk,
                    incoming_core_key=core_key,
                    matched_core_key=best_candidate_key,
                    similarity_score=best_score,
                    match_status="ACCEPTED"
                ))
            else:
                # Log Near-Misses for Tuning (Scores between 65% and 84%)
                if best_score >= 65.0 and best_candidate_upk:
                    session.add(AuditFuzzyMatch(
                        origin_company=company,
                        store_variant_id=str(row['variant_id']),
                        candidate_upk_id=best_candidate_upk,
                        incoming_core_key=core_key,
                        matched_core_key=best_candidate_key,
                        similarity_score=best_score,
                        match_status="REJECTED"
                    ))

        # Tier 3: New UPK Creation (Fallback if Tiers 1, 2, and 2.5 fail)
        if not matched_upk:
            upk_prefix = b_id.split('-')[1] if '-' in b_id else "GEN"
            
            if core_key not in core_to_counter_map:
                core_to_counter_map[core_key] = upk_counter
                upk_counter += 1
                
            base_counter = core_to_counter_map[core_key]
            spec_suffix = spec.upper() if spec else "STD"
            
            matched_upk = f"UPK-{upk_prefix}-{base_counter}-{spec_suffix}"
            match_tier = "Tier 3: Structured UPK Generation"
            match_key = matched_upk
            
            new_upk = DimUnifiedProduct(
                upk_id=matched_upk,
                brand_id=b_id,
                consensus_title=full_title,
                extracted_spec=spec,
                clean_match_key=core_key,
                canonical_barcode=barcode,
                canonical_sku=sku
            )
            session.add(new_upk)

        # Update Lookup Registries
        if barcode and len(barcode) >= 8:
            upk_by_barcode[barcode] = matched_upk
        if core_key:
            upk_by_core_spec_brand[(core_key, spec, b_id)] = matched_upk
            
            if (b_id, spec) not in upk_fuzzy_registry:
                upk_fuzzy_registry[(b_id, spec)] = []
            upk_fuzzy_registry[(b_id, spec)].append((core_key, matched_upk))

        raw_price = float(row['price']) if row['price'] is not None else 0.0
        currency = row['currency']
        price_in_bhd = convert_to_bhd(raw_price, currency)

        store_variant_records.append({
            "upk_id": matched_upk,
            "origin_company": company,
            "store_product_id": str(row['product_id']),
            "store_variant_id": str(row['variant_id']),
            "raw_product_title": str(row['product_title']),
            "raw_variant_title": str(row['variant_title']),
            "cleaned_product_title": core_key,
            "raw_sku": sku,
            "raw_barcode": barcode,
            "price_raw": raw_price,
            "currency_raw": currency,
            "price_bhd": price_in_bhd,
            "match_tier": match_tier,
            "match_key_used": match_key
        })

    session.commit()
    print(f"[+] Created {len(core_to_counter_map)} Core Products and {len(set([r['upk_id'] for r in store_variant_records]))} Total UPKs.")

    print("[+] Inserting Store Variant lineage links...")
    link_id_map = {}
    
    for rec in store_variant_records:
        fsv = FactStoreVariant(
            upk_id=rec['upk_id'],
            origin_company=rec['origin_company'],
            store_product_id=rec['store_product_id'],
            store_variant_id=rec['store_variant_id'],
            raw_product_title=rec['raw_product_title'],
            raw_variant_title=rec['raw_variant_title'],
            cleaned_product_title=rec['cleaned_product_title'],
            raw_sku=rec['raw_sku'],
            raw_barcode=rec['raw_barcode'],
            price_raw=rec['price_raw'],
            currency_raw=rec['currency_raw'],
            price_bhd=rec['price_bhd'],
            match_tier=rec['match_tier'],
            match_key_used=rec['match_key_used']
        )
        session.add(fsv)
        session.flush()
        
        clean_var_id = clean_id_str(rec['store_variant_id'])
        link_id_map[(rec['origin_company'], clean_var_id)] = (
            fsv.link_id, 
            rec['upk_id'], 
            rec['currency_raw']
        )
        
        clean_prod_id = clean_id_str(rec['store_product_id'])
        link_id_map[(rec['origin_company'], clean_prod_id)] = (
            fsv.link_id, 
            rec['upk_id'], 
            rec['currency_raw']
        )

    session.commit()

    print("[+] Migrating Historical Daily Stock Snapshots into Central Warehouse...")
    snapshot_count = 0
    
    # 1. LOAD EXISTING SNAPSHOTS TO PREVENT DUPLICATES
    existing_snapshots = set(
        session.query(FactStockSnapshot.link_id, FactStockSnapshot.snapshot_date).all()
    )
    
    for db_file in staging_db_dir.glob("competitor_*.db"):
        company_name = db_file.stem.replace("competitor_", "")
        conn = sqlite3.connect(db_file)
        
        query = """
            SELECT s.variant_id, s.snapshot_date, s.stock_quantity, v.price 
            FROM stock_snapshots s
            JOIN variants v ON s.variant_id = v.id
        """
        stock_df = pd.read_sql_query(query, conn)
        conn.close()
        
        for _, srow in stock_df.iterrows():
            v_id = clean_id_str(srow['variant_id'])
            lookup_key = (company_name, v_id)
            
            if lookup_key in link_id_map:
                link_id, upk_id, currency = link_id_map[lookup_key]
                dt = datetime.fromisoformat(str(srow['snapshot_date'])) if isinstance(srow['snapshot_date'], str) else srow['snapshot_date']
                
                # 2. THE TIME-CHECK: ONLY ADD IF NOT IN THE DATABASE
                if (link_id, dt) not in existing_snapshots:
                    
                    raw_p = float(srow['price']) if srow['price'] is not None else 0.0
                    p_bhd = convert_to_bhd(raw_p, currency)
                    qty = int(srow['stock_quantity'])
                    
                    snapshot = FactStockSnapshot(
                        upk_id=upk_id,
                        link_id=link_id,
                        origin_company=company_name,
                        snapshot_date=dt,
                        price_raw=raw_p,
                        currency_raw=currency,
                        price_bhd=p_bhd,
                        stock_quantity=qty,
                        is_in_stock=(qty > 0)
                    )
                    session.add(snapshot)
                    snapshot_count += 1

    bbk_db = staging_db_dir / "beautybykat_inventory.db"
    if bbk_db.exists():
        conn = sqlite3.connect(bbk_db)
        query = """
            SELECT variant_id, snapshot_date, inventory_qty as stock_quantity, 0.0 as price 
            FROM inventory_snapshots
        """
        stock_df = pd.read_sql_query(query, conn)
        conn.close()
        
        company_name = "beautybykat"
        for _, srow in stock_df.iterrows():
            v_id = clean_id_str(srow['variant_id'])
            lookup_key = (company_name, v_id)
            
            if lookup_key in link_id_map:
                link_id, upk_id, currency = link_id_map[lookup_key]
                dt = datetime.fromisoformat(str(srow['snapshot_date'])) if isinstance(srow['snapshot_date'], str) else srow['snapshot_date']
                
                if (link_id, dt) not in existing_snapshots:
                    qty = int(srow['stock_quantity'])
                    
                    snapshot = FactStockSnapshot(
                        upk_id=upk_id,
                        link_id=link_id,
                        origin_company=company_name,
                        snapshot_date=dt,
                        price_raw=0.0,
                        currency_raw=currency,
                        price_bhd=0.0,
                        stock_quantity=qty,
                        is_in_stock=(qty > 0)
                    )
                    session.add(snapshot)
                    snapshot_count += 1
                    
    session.commit()
    print(f"[SUCCESS] Product Master Built!")
    print(f"          Total Core Products Created: {len(core_to_counter_map)}")
    print(f"          Total Variant Mappings: {len(store_variant_records)}")
    print(f"          Total Daily Snapshots Migrated: {snapshot_count}")

if __name__ == "__main__":
    main()