import os
import re
import glob
import ast
import pandas as pd
from rapidfuzz import fuzz

# =====================================================================
# PATH & DIRECTORY CONFIGURATION
# =====================================================================
BASE_DIR = "/home/boredom-speaking/Desktop/JulyInternship/BBK_MainProject/Pipeline"
INPUT_DIR = os.path.join(BASE_DIR, "Data/scrapped_json")
OUTPUT_DIR = os.path.join(BASE_DIR, "Data/unified_database")

PHASE1_DIR = os.path.join(OUTPUT_DIR, "phase1_cleaned")
PHASE2_DIR = os.path.join(OUTPUT_DIR, "mappings")
PHASE3_DIR = os.path.join(OUTPUT_DIR, "final_tables")

os.makedirs(PHASE1_DIR, exist_ok=True)
os.makedirs(PHASE2_DIR, exist_ok=True)
os.makedirs(PHASE3_DIR, exist_ok=True)

# Exchange Rate Peg
AED_TO_BHD_RATE = 0.1028
MATCH_THRES = 70

# =====================================================================
# PHASE 1: INGESTION, JOINING, CURRENCY NORMALIZATION & TEXT CLEANING
# =====================================================================
def clean_and_extract_specs(title, vendor):
    """Cleans raw titles: strips Arabic/non-ASCII, extracts specs (ml, g, etc.), and drops brand noise."""
    if pd.isna(title) or not title:
        return "", "NO_SPEC", "UNK"

    raw_title = str(title)
    raw_vendor = str(vendor) if pd.notna(vendor) else "Unknown"

    # 1. Brand prefix
    brand_prefix = re.sub(r'[^A-Za-z0-9]', '', raw_vendor).upper()[:3]
    if not brand_prefix:
        brand_prefix = "UNK"

    # 2. Extract unit spec (e.g., 50ml, 100g, 60pads)
    size_pattern = r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs)\b'
    text_lower = raw_title.lower()
    size_match = re.search(size_pattern, text_lower)
    
    extracted_spec = "NO_SPEC"
    if size_match:
        extracted_spec = f"{size_match.group(1)}{size_match.group(2)}"
        text_lower = text_lower.replace(size_match.group(0), "")

    # 3. Strip Vendor/Brand noise
    vendor_clean = re.sub(r'[^a-z0-9]', '', raw_vendor.lower())
    text_lower = text_lower.replace(raw_vendor.lower(), "").replace(vendor_clean, "")
    if "althea" in vendor_clean:
        text_lower = text_lower.replace("dr.althea", "").replace("dralthea", "").replace("dr althea", "")

    # 4. Strip non-ASCII (Arabic scripts, symbols, emojis)
    text_clean = re.sub(r'[^a-z0-9\s]', ' ', text_lower)
    text_clean = re.sub(r'[^\x00-\x7F]+', ' ', text_clean)
    
    clean_base_title = " ".join(text_clean.split())
    clean_match_str = f"{clean_base_title} {extracted_spec}".strip() if extracted_spec != "NO_SPEC" else clean_base_title

    return clean_base_title, extracted_spec, brand_prefix, clean_match_str


def execute_phase_1():
    print("=========================================================")
    print("[+] PHASE 1: Processing Product & Stock JSONs...")
    print("=========================================================")

    product_files = glob.glob(os.path.join(INPUT_DIR, "master_products_*.json"))
    phase1_dfs = []

    for p_file in product_files:
        company = os.path.basename(p_file).replace("master_products_", "").replace(".json", "")
        s_file = os.path.join(INPUT_DIR, f"master_stock_{company}.json")

        if not os.path.exists(s_file):
            print(f"  [!] Skipping {company}: Stock JSON matching file not found.")
            continue

        print(f"  [->] Joining and cleaning data for company: {company.upper()}")
        
        # Load JSONs
        products_df = pd.read_json(p_file)
        stocks_df = pd.read_json(s_file)

        # Ensure uniform join keys
        if "id" in products_df.columns and "product_id" not in products_df.columns:
            products_df.rename(columns={"id": "product_id"}, inplace=True)
            
        if "id" in stocks_df.columns and "product_id" not in stocks_df.columns:
            stocks_df.rename(columns={"id": "product_id"}, inplace=True)

        # Merge Products and Stock tables on product_id
        merged = pd.merge(stocks_df, products_df, on="product_id", how="left", suffixes=("", "_prod"))
        merged["origin_company"] = company

        # Extract nested variants details (SKU & Price) if present
        if "variants" in merged.columns:
            merged["sku"] = merged["variants"].apply(lambda v: v[0].get("sku", "") if isinstance(v, list) and len(v) > 0 else "")
            merged["price"] = merged["variants"].apply(lambda v: v[0].get("price", "") if isinstance(v, list) and len(v) > 0 else "")

        # Coerce inventory stock & price numeric types
        if "inventory_quantity" in merged.columns:
            merged["stock"] = pd.to_numeric(merged["inventory_quantity"], errors='coerce').fillna(0).astype(int)
        else:
            merged["stock"] = 0

        merged["price"] = pd.to_numeric(merged["price"], errors='coerce')

        # Currency Normalization (Convert AED to BHD for XBeauty)
        if company.lower() == "xbeauty":
            merged["price"] = merged["price"] * AED_TO_BHD_RATE

        # Clean text & extract specs row-by-row
        cleaned_bases, extracted_specs, brand_prefixes, clean_match_strs = [], [], [], []
        for _, row in merged.iterrows():
            title = row.get("title", "")
            vendor = row.get("vendor", "")
            base, spec, prefix, match_str = clean_and_extract_specs(title, vendor)
            cleaned_bases.append(base)
            extracted_specs.append(spec)
            brand_prefixes.append(prefix)
            clean_match_strs.append(match_str)

        merged["clean_base_title"] = cleaned_bases
        merged["extracted_spec"] = extracted_specs
        merged["brand_prefix"] = brand_prefixes
        merged["clean_match_str"] = clean_match_strs

        # Export individual phase 1 company sheet
        company_out_path = os.path.join(PHASE1_DIR, f"{company}_cleaned_inventory.csv")
        merged.to_csv(company_out_path, index=False)
        print(f"      [✓] Saved Phase 1 company sheet: {company_out_path}")

        phase1_dfs.append(merged)

    if not phase1_dfs:
        raise FileNotFoundError("No valid product & stock files were merged.")

    master_phase1_df = pd.concat(phase1_dfs, ignore_index=True)
    master_p1_path = os.path.join(PHASE1_DIR, "master_phase1_cleaned_joined.csv")
    master_phase1_df.to_csv(master_p1_path, index=False)
    print(f"  [✓] Master Phase 1 Joined Table Saved: {master_p1_path}\n")

    return master_phase1_df


# =====================================================================
# PHASE 2: BRAND CANONICALIZATION & ROOT/PARENT UPK RESOLUTION
# =====================================================================
def execute_phase_2(df):
    print("=========================================================")
    print("[+] PHASE 2: Brand Canonization & UPK Parent/Root Resolution...")
    print("=========================================================")

    # 1. Resolve Canonical Brands
    trusted_mask = ~df["vendor"].astype(str).str.lower().str.contains("soko", na=False)
    unique_vendors = df[trusted_mask]["vendor"].dropna().unique()

    brand_directory = {re.sub(r'[^a-z0-9]', '', str(v).lower()): v for v in unique_vendors if v}
    brand_directory.update({
        "dralthea": "Dr. Althea", "cosrx": "Cosrx", 
        "medicube": "Medicube", "skin1004": "SKIN1004", "anua": "ANUA"
    })

    resolved_brands = []
    for idx, row in df.iterrows():
        raw_vendor = str(row.get("vendor", ""))
        raw_tags = str(row.get("tags", ""))
        
        if "soko" in raw_vendor.lower() or raw_vendor.strip() == "":
            matched_brand = "Unknown Brand"
            try:
                parsed_tags = ast.literal_eval(raw_tags) if '[' in raw_tags else raw_tags.split(',')
                parsed_tags = [str(t).lower().strip() for t in parsed_tags]
            except (ValueError, SyntaxError):
                parsed_tags = [t.strip() for t in raw_tags.lower().split(',')]

            for tag in parsed_tags:
                clean_tag = re.sub(r'[^a-z0-9]', '', tag)
                if clean_tag in brand_directory:
                    matched_brand = brand_directory[clean_tag]
                    break

            if matched_brand == "Unknown Brand":
                raw_title = str(row.get("title", ""))
                combined_text = re.sub(r'[^a-z0-9]', '', raw_title.lower())
                for norm_key, true_brand in brand_directory.items():
                    if norm_key in combined_text:
                        matched_brand = true_brand
                        break

            resolved_brands.append(matched_brand)
        else:
            norm_v = re.sub(r'[^a-z0-9]', '', raw_vendor.lower())
            resolved_brands.append(brand_directory.get(norm_v, raw_vendor))

    df["canonical_brand"] = resolved_brands

    # 2. Multi-tier Parent UPK Resolution & Fuzzy Matching Audit
    df["parent_upk"] = ""
    df["child_upk"] = ""
    df["consensus_title"] = ""
    df["match_score"] = 0.0
    df["match_tier"] = ""

    mapped_pool = {}  # { brand: [(sku, clean_str, parent_upk, orig_title)] }
    parent_counter = 1000
    match_threshold = MATCH_THRES

    for idx, row in df.iterrows():
        brand = row.get("canonical_brand", "Unknown Brand")
        c_title = row.get("clean_match_str", "")
        sku = str(row.get("sku", "")).strip()
        orig_title = str(row.get("title", ""))
        spec = row.get("extracted_spec", "NO_SPEC")
        if sku in ["nan", "None"]:
            sku = ""

        if brand not in mapped_pool:
            mapped_pool[brand] = []

        match_found = False
        best_score = 0.0
        matched_parent = ""

        # Tier 1: SKU Match
        if sku:
            for pool_sku, pool_clean, pool_parent, _ in mapped_pool[brand]:
                if pool_sku == sku:
                    matched_parent = pool_parent
                    df.at[idx, "match_score"] = 100.0
                    df.at[idx, "match_tier"] = "Tier 1: SKU Match"
                    match_found = True
                    break

        # Tier 2 & Tier 3: String Matching
        if not match_found:
            for pool_sku, pool_clean, pool_parent, _ in mapped_pool[brand]:
                if c_title == pool_clean:
                    matched_parent = pool_parent
                    best_score = 100.0
                    df.at[idx, "match_tier"] = "Tier 2: Exact Match"
                    match_found = True
                    break

                score = fuzz.token_set_ratio(c_title, pool_clean)
                if score > best_score:
                    best_score = score
                    matched_parent = pool_parent

            if not match_found and best_score >= match_threshold:
                df.at[idx, "match_score"] = round(best_score, 1)
                df.at[idx, "match_tier"] = "Tier 3: Fuzzy Match"
                match_found = True

        if match_found:
            df.at[idx, "parent_upk"] = matched_parent
            if df.at[idx, "match_score"] == 0.0:
                df.at[idx, "match_score"] = round(best_score, 1)
        else:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3]
            matched_parent = f"UPK-{prefix}-{parent_counter}"
            parent_counter += 1
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Baseline Root"
            mapped_pool[brand].append((sku, c_title, matched_parent, orig_title))

        # Assign Variant Child UPK
        clean_spec = re.sub(r'[^A-Za-z0-9]', '', spec).upper()
        df.at[idx, "child_upk"] = f"{matched_parent}-V_{clean_spec}"

    # Calculate Consensus Title for each Parent UPK Group
    for p_id in df["parent_upk"].unique():
        group_mask = df["parent_upk"] == p_id
        group_titles = df.loc[group_mask, "title"].tolist()
        latin_titles = [t for t in group_titles if re.match(r'^[\x00-\x7F\s]+$', str(t))]
        pool = latin_titles if latin_titles else group_titles
        consensus = sorted(pool, key=len)[len(pool) // 2] if pool else ""
        df.loc[group_mask, "consensus_title"] = consensus

    # Generate Audit Mapping Table for Phase 2
    mapping_audit_df = df[[
        "canonical_brand", "origin_company", "parent_upk", "child_upk", 
        "title", "consensus_title", "extracted_spec", "match_score", "match_tier"
    ]].rename(columns={"title": "store_original_title"}).drop_duplicates()

    phase2_out_path = os.path.join(PHASE2_DIR, "phase2_parent_upk_mappings.csv")
    mapping_audit_df.to_csv(phase2_out_path, index=False)
    print(f"  [✓] Phase 2 Parent UPK Audit Mapping Sheet Saved: {phase2_out_path}\n")

    return df


# =====================================================================
# PHASE 3: FINAL TABLES & ANALYTICS EXPORT
# =====================================================================
# =====================================================================
# PHASE 3: FINAL TABLES & ANALYTICS EXPORT
# =====================================================================
def execute_phase_3(df):
    print("=========================================================")
    print("[+] PHASE 3: Generating Final Database Tables...")
    print("=========================================================")

    # [FIX 1] Scrub corrupted Arabic encoding bytes from the raw title
    df["title"] = df["title"].apply(lambda x: re.sub(r'[^\x00-\x7F]+', '', str(x)).strip(" -Ø•"))

    # [FIX 2] Add 'consensus_title' to the required columns list
    req_cols = ["snapshot_date", "parent_upk", "child_upk", "canonical_brand", 
                "origin_company", "consensus_title", "title", "price", "stock", "change_notes"]
    for col in req_cols:
        if col not in df.columns:
            df[col] = ""

    # 1. Micro Time-Series Performance Output
    # Grab the newly added consensus_title alongside the scrubbed original title
    micro_db = df[[
        "snapshot_date", "parent_upk", "child_upk", "canonical_brand", 
        "origin_company", "consensus_title", "title", "price", "stock", "change_notes"
    ]].copy()
    
    # Rename columns for the final output sheet
    micro_db.columns = [
        "date", "parent_upk", "child_upk", "brand", 
        "company", "consensus_title", "store_original_title", "price", "stock", "system_notes"
    ]
    micro_db = micro_db.sort_values(by=["date", "brand", "parent_upk", "company"])
    micro_path = os.path.join(PHASE3_DIR, "micro_perf_timeseries.csv")
    micro_db.to_csv(micro_path, index=False)
    print(f"  [✓] Final Micro Performance Table Saved: {micro_path}")

    # 2. Macro Market Overview Output
    macro_sheet = df.groupby(["canonical_brand", "origin_company"])["parent_upk"].nunique().unstack(fill_value=0)
    macro_sheet["total_unique_market_products"] = df.groupby("canonical_brand")["parent_upk"].nunique()
    macro_sheet = macro_sheet.sort_values(by="total_unique_market_products", ascending=False).reset_index()
    macro_path = os.path.join(PHASE3_DIR, "macro_market_overview.csv")
    macro_sheet.to_csv(macro_path, index=False)
    print(f"  [✓] Final Macro Market Overview Table Saved: {macro_path}\n")

# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================
if __name__ == "__main__":
    p1_df = execute_phase_1()
    p2_df = execute_phase_2(p1_df)
    execute_phase_3(p2_df)
    print("=========================================================")
    print("[SUCCESS] Complete Pipeline Processed and Exported Successfully!")
    print("=========================================================")