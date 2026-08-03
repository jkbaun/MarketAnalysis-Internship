import os
import glob
import re
from collections import Counter
import pandas as pd
from rapidfuzz import fuzz

# =====================================================================
# PATH CONFIGURATION
# =====================================================================
INPUT_DIR = "/home/boredom-speaking/Desktop/JulyInternship/BBK_MainProject/Pipeline/Data/unified_database/phase1_cleaned"
BASE_OUTPUT_DIR = "/home/boredom-speaking/Desktop/JulyInternship/BBK_MainProject/Pipeline/Data/unified_database"

MAPPINGS_DIR = os.path.join(BASE_OUTPUT_DIR, "mappings")
FINAL_DIR = os.path.join(BASE_OUTPUT_DIR, "final_tables")

os.makedirs(MAPPINGS_DIR, exist_ok=True)
os.makedirs(FINAL_DIR, exist_ok=True)

OVERRIDE_FILE = os.path.join(MAPPINGS_DIR, "manual_upk_overrides.csv")
REVIEW_QUEUE_FILE = os.path.join(MAPPINGS_DIR, "unification_review_queue.csv")

AED_TO_BHD_RATE = 0.1028
MATCH_THRES = 80  # Threshold for blocked fuzzy matching

# =====================================================================
# STAGE 1: DATA LOADING & CURRENCY NORMALIZATION
# =====================================================================
def load_phase1_cleaned_data(input_path):
    """
    Loads all CSV files from the phase1_cleaned directory into a single DataFrame.
    """
    csv_files = glob.glob(os.path.join(input_path, "*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in input directory: {input_path}")

    print(f"[+] Found {len(csv_files)} CSV file(s) in phase1_cleaned directory.")
    all_dfs = []
    
    for file in csv_files:
        print(f"  [->] Loading: {os.path.basename(file)}")
        df = pd.read_csv(file)
        all_dfs.append(df)

    master_df = pd.concat(all_dfs, ignore_index=True)
    
    # Kill phantom duplicate rows and empty titles causing the VAR explosion
    master_df = master_df.dropna(subset=["title"]) 
    master_df = master_df.drop_duplicates()
    
    # Currency Normalization (AED -> BHD for XBeauty if applicable)
    if "origin_company" in master_df.columns and "price" in master_df.columns:
        xbeauty_mask = master_df["origin_company"].str.lower() == "xbeauty"
        # Only multiply if prices haven't already been converted (check average threshold)
        if xbeauty_mask.any() and master_df.loc[xbeauty_mask, "price"].mean() > 20:
            master_df.loc[xbeauty_mask, "price"] = master_df.loc[xbeauty_mask, "price"] * AED_TO_BHD_RATE

    return master_df

# =====================================================================
# STAGE 2: BRAND & TEXT PREPARATION
# =====================================================================
def calculate_jaccard_similarity(str1, str2):
    set1, set2 = set(str1.split()), set(str2.split())
    intersection = set1.intersection(set2)
    union = set1.union(set2)
    return (len(intersection) / len(union)) * 100.0 if union else 0.0

def load_manual_overrides():
    if not os.path.exists(OVERRIDE_FILE):
        df_template = pd.DataFrame(columns=["store_original_title", "origin_company", "target_parent_upk", "notes"])
        df_template.to_csv(OVERRIDE_FILE, index=False)
        return {}
    
    df_override = pd.read_csv(OVERRIDE_FILE)
    override_dict = {}
    for _, row in df_override.iterrows():
        title = str(row.get("store_original_title", "")).strip()
        company = str(row.get("origin_company", "")).strip().lower()
        upk = str(row.get("target_parent_upk", "")).strip()
        if title and upk:
            override_dict[(title, company)] = upk
    return override_dict

def resolve_canonical_brands(df):
    """
    Standardizes vendor names and handles retailer-specific brand tag resolution.
    """
    trusted_mask = ~df["vendor"].astype(str).str.lower().str.contains("soko", na=False)
    unique_vendors = df[trusted_mask]["vendor"].dropna().unique()
    brand_directory = {re.sub(r'[^a-z0-9]', '', str(v).lower()): v for v in unique_vendors if v}
    
    brand_directory.update({
        "dralthea": "Dr. Althea", 
        "cosrx": "Cosrx", 
        "medicube": "Medicube", 
        "skin1004": "SKIN1004", 
        "anua": "ANUA"
    })

    resolved_brands = []
    for _, row in df.iterrows():
        raw_vendor = str(row.get("vendor", ""))
        raw_tags = str(row.get("tags", ""))
        
        if "soko" in raw_vendor.lower() or raw_vendor.strip() in ["", "nan", "None", "Unknown"]:
            matched_brand = "Unknown Brand"
            tags_clean = re.sub(r'[^a-z0-9,]', '', raw_tags.lower()).split(',')
            for tag in tags_clean:
                if tag in brand_directory:
                    matched_brand = brand_directory[tag]
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
    return df

# =====================================================================
# STAGE 3: MULTI-TIER BLOCKED MATCHING ENGINE
# =====================================================================
def run_unification_engine(df):
    """
    Executes Tiered Matching (Tier 0: Override, Tier 1: SKU, Tier 2: Fingerprint, Tier 3: Blocked Fuzzy, Tier 4: Baseline).
    Prevents Cartesian row explosion.
    """
    print("\n[+] Executing Multi-Tier Unification Engine...")
    overrides = load_manual_overrides()

    df["parent_upk"] = ""
    df["child_upk"] = ""
    df["consensus_title"] = ""
    df["match_score"] = 0.0
    df["match_tier"] = ""

    blocked_pool = {}
    parent_counter = 1000

    for idx, row in df.iterrows():
        brand = row.get("canonical_brand", "Unknown Brand")
        orig_title = str(row.get("title", "")).strip()
        company = str(row.get("origin_company", "")).strip().lower()
        c_title = str(row.get("clean_match_str", row.get("title", "")))
        c_base = str(row.get("clean_base_title", c_title))
        sku = str(row.get("sku", "")).strip()
        spec = str(row.get("extracted_spec", "NO_SPEC"))
        fingerprint = str(row.get("composite_fingerprint", ""))
        v_id = str(row.get("variant_id", "")).strip()

        raw_vid = str(row.get("variant_id", "")).split('.')[0].strip()
        if raw_vid.lower() == "nan": 
            raw_vid = ""
            
        if len(raw_vid) >= 4:
            suffix = raw_vid[-4:]
        elif len(raw_vid) > 0:
            suffix = raw_vid.zfill(4) # Pads short IDs like "83" to "0083" instead of defaulting to VAR
        else:
            suffix = "VAR"
            
        df.at[idx, "child_upk"] = f"{matched_parent}-V_{clean_spec}_{suffix}"
        if sku in ["nan", "None"]: sku = ""

        # TIER 0: Manual Override Check
        if (orig_title, company) in overrides:
            matched_parent = overrides[(orig_title, company)]
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Tier 0: Manual Override"
            blocked_pool.setdefault(brand, {}).setdefault(spec, []).append((sku, fingerprint, c_title, matched_parent))
            continue

        if brand not in blocked_pool:
            blocked_pool[brand] = {}

        pool = blocked_pool[brand]
        match_found = False
        best_score = 0.0
        matched_parent = ""

        # TIER 1: SKU Match (within brand)
        if sku:
            for b_spec, items in blocked_pool[brand].items():
                for p_sku, p_fp, p_clean, p_upk in items:
                    if p_sku == sku:
                        matched_parent = p_upk
                        df.at[idx, "match_score"] = 100.0
                        df.at[idx, "match_tier"] = "Tier 1: SKU Match"
                        match_found = True
                        break
                if match_found: break

        # TIER 2: Fingerprint Match
        if not match_found and fingerprint and spec in pool:
            for p_sku, p_fp, p_clean, p_upk in pool[spec]:
                if p_fp == fingerprint:
                    matched_parent = p_upk
                    df.at[idx, "match_score"] = 100.0
                    df.at[idx, "match_tier"] = "Tier 2: Fingerprint Match"
                    match_found = True
                    break

        # TIER 3: Blocked Fuzzy & Jaccard Match (inside same Brand + Spec block)
        if not match_found and spec in pool:
            for p_sku, p_fp, p_clean, p_upk in pool[spec]:
                if c_title == p_clean:
                    matched_parent = p_upk
                    best_score = 100.0
                    df.at[idx, "match_tier"] = "Tier 3A: Exact Text Match"
                    match_found = True
                    break

                jaccard = calculate_jaccard_similarity(c_base, p_clean)
                token_fuzzy = fuzz.token_sort_ratio(c_base, p_clean)
                score = (jaccard * 0.4) + (token_fuzzy * 0.6)

                # Numeric Guardrail: If numbers differ, zero out score
                if set(re.findall(r'\d+', p_clean)) != set(re.findall(r'\d+', c_base)):
                    score = 0.0

                if score > best_score:
                    best_score = score
                    matched_parent = p_upk

            if not match_found and best_score >= MATCH_THRES:
                df.at[idx, "match_score"] = round(best_score, 1)
                df.at[idx, "match_tier"] = "Tier 3B: Blocked Fuzzy Match"
                match_found = True

        # TIER 4: Baseline Root Assignment
        if match_found:
            df.at[idx, "parent_upk"] = matched_parent
            if df.at[idx, "match_score"] == 0.0:
                df.at[idx, "match_score"] = round(best_score, 1)
            blocked_pool[brand].setdefault(spec, []).append((sku, fingerprint, c_title, matched_parent))
        else:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3]
            if not prefix: prefix = "UNK"
            matched_parent = f"UPK-{prefix}-{parent_counter}"
            parent_counter += 1
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Tier 4: Baseline Root"
            blocked_pool[brand].setdefault(spec, []).append((sku, fingerprint, c_title, matched_parent))

        # Assign Deterministic Variant Child UPK
        clean_spec = re.sub(r'[^A-Za-z0-9]', '', spec).upper()
        suffix = v_id[-4:] if len(v_id) >= 4 else "VAR"
        df.at[idx, "child_upk"] = f"{matched_parent}-V_{clean_spec}_{suffix}"

    # Calculate Consensus Titles across store titles
    for p_id in df["parent_upk"].unique():
        group_mask = df["parent_upk"] == p_id
        group_titles = df.loc[group_mask, "title"].dropna().tolist()
        latin_titles = [t for t in group_titles if re.match(r'^[\x00-\x7F\s]+$', str(t))]
        pool = latin_titles if latin_titles else group_titles
        
        if pool:
            base_titles = [re.sub(r'\s*-\s*.*$', '', str(t)).strip() for t in pool if str(t).strip()]
            consensus = Counter(base_titles).most_common(1)[0][0]
        else:
            consensus = ""
        df.loc[group_mask, "consensus_title"] = consensus

    return df

# =====================================================================
# STAGE 4: EXPORT ANALYTICAL TABLES
# =====================================================================
def export_analytical_outputs(df):
    print("\n[+] Exporting Final Unified Database Tables...")

    # Clean non-ASCII characters from raw title output for readability
    df["title_clean"] = df["title"].apply(lambda x: re.sub(r'[^\x00-\x7F]+', '', str(x)).strip(" -Ø•"))
    if "change_notes" not in df.columns: 
        df["change_notes"] = ""

    # 1. Micro Time-Series Performance Table
    micro_db = df[[
        "snapshot_date", "parent_upk", "child_upk", "canonical_brand", 
        "origin_company", "consensus_title", "title_clean", "price", "stock", "change_notes"
    ]].copy()
    
    micro_db.columns = [
        "date", "parent_upk", "child_upk", "brand", 
        "company", "consensus_title", "store_original_title", "price", "stock", "system_notes"
    ]
    micro_db = micro_db.sort_values(by=["date", "brand", "parent_upk", "company"])
    micro_path = os.path.join(FINAL_DIR, "micro_perf_timeseries.csv")
    micro_db.to_csv(micro_path, index=False)

    # 2. Macro Market Overview Table (Cross-store catalog matrix)
    macro_sheet = df.groupby(["canonical_brand", "origin_company"])["parent_upk"].nunique().unstack(fill_value=0)
    macro_sheet["total_unique_market_products"] = df.groupby("canonical_brand")["parent_upk"].nunique()
    macro_sheet = macro_sheet.sort_values(by="total_unique_market_products", ascending=False).reset_index()
    macro_path = os.path.join(FINAL_DIR, "macro_market_overview.csv")
    macro_sheet.to_csv(macro_path, index=False)

    # 3. Mappings Audit File & Exception Queue
    audit_cols = ["canonical_brand", "origin_company", "parent_upk", "child_upk", 
                  "title_clean", "consensus_title", "extracted_spec", "match_score", "match_tier"]
    mapping_audit_df = df[[c for c in audit_cols if c in df.columns]].drop_duplicates()
    mapping_path = os.path.join(MAPPINGS_DIR, "phase2_parent_upk_mappings.csv")
    mapping_audit_df.to_csv(mapping_path, index=False)

    review_mask = ((df["match_tier"] == "Tier 3B: Blocked Fuzzy Match") & (df["match_score"] < 90.0)) | (df["match_tier"] == "Tier 4: Baseline Root")
    review_df = df[review_mask][["canonical_brand", "origin_company", "parent_upk", "title_clean", "extracted_spec", "match_score", "match_tier"]]
    review_df.to_csv(REVIEW_QUEUE_FILE, index=False)

    # Update ALL of your to_csv calls to include encoding='utf-8-sig'
    micro_db.to_csv(micro_path, index=False, encoding='utf-8-sig')
    macro_sheet.to_csv(macro_path, index=False, encoding='utf-8-sig')
    mapping_audit_df.to_csv(mapping_path, index=False, encoding='utf-8-sig')
    review_df.to_csv(REVIEW_QUEUE_FILE, index=False, encoding='utf-8-sig')

    print(f"  [✓] Micro Performance Table Saved -> {micro_path}")
    print(f"  [✓] Macro Market Overview Saved     -> {macro_path}")
    print(f"  [✓] Full Mappings Audit Saved      -> {mapping_path}")
    print(f"  [✓] Review Queue Generated ({len(review_df)} items) -> {REVIEW_QUEUE_FILE}")

# =====================================================================
# MAIN PIPELINE EXECUTION
# =====================================================================
def main():
    print("=========================================================")
    print("[+] STARTING UNIFIED DATABASE PIPELINE EXECUTION")
    print("=========================================================")
    
    # 1. Load data from phase1_cleaned
    df = load_phase1_cleaned_data(INPUT_DIR)
    
    # 2. Resolve Brands
    df = resolve_canonical_brands(df)
    
    # 3. Run Matching & Unification Engine
    df = run_unification_engine(df)
    
    # 4. Export Final Tables
    export_analytical_outputs(df)

    print("\n=========================================================")
    print("[SUCCESS] PIPELINE COMPLETED SUCCESSFULLY!")
    print("=========================================================")

if __name__ == "__main__":
    main()