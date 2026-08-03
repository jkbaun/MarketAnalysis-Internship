import os
import re
import pandas as pd
from collections import Counter
from rapidfuzz import fuzz

# =====================================================================
# PATH & CONFIGURATION
# =====================================================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)

PHASE1_FILE = os.path.join(BASE_DIR, "Data/unified_database/phase1_cleaned/master_phase1_cleaned_joined.csv")
PHASE2_DIR = os.path.join(BASE_DIR, "Data/unified_database/mappings")
PHASE3_DIR = os.path.join(BASE_DIR, "Data/unified_database/final_tables")

os.makedirs(PHASE2_DIR, exist_ok=True)
os.makedirs(PHASE3_DIR, exist_ok=True)

# Increased threshold for stricter matching
MATCH_THRES = 84

# =====================================================================
# PHASE 2: BRAND CANONICALIZATION & PARENT/CHILD UPK RESOLUTION
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
        
        if "soko" in raw_vendor.lower() or raw_vendor.strip() in ["", "nan", "None", "Unknown"]:
            matched_brand = "Unknown Brand"
            
            # Tag fallback
            tags_clean = re.sub(r'[^a-z0-9,]', '', raw_tags.lower()).split(',')
            for tag in tags_clean:
                if tag in brand_directory:
                    matched_brand = brand_directory[tag]
                    break

            # Title fallback
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

    # 2. Multi-tier Parent UPK Resolution
    df["parent_upk"] = ""
    df["child_upk"] = ""
    df["consensus_title"] = ""
    df["match_score"] = 0.0
    df["match_tier"] = ""

    mapped_pool = {}  # { brand: [(sku, clean_str, parent_upk)] }
    parent_counter = 1000

    for idx, row in df.iterrows():
        brand = row.get("canonical_brand", "Unknown Brand")
        c_title = str(row.get("clean_match_str", ""))
        sku = str(row.get("sku", "")).strip()
        spec = str(row.get("extracted_spec", "NO_SPEC"))
        v_id = str(row.get("variant_id", "")).strip()
        
        if sku in ["nan", "None"]:
            sku = ""

        if brand not in mapped_pool:
            mapped_pool[brand] = []

        match_found = False
        best_score = 0.0
        matched_parent = ""

        # Tier 1: SKU Match
        if sku:
            for pool_sku, pool_clean, pool_parent in mapped_pool[brand]:
                if pool_sku == sku:
                    matched_parent = pool_parent
                    df.at[idx, "match_score"] = 100.0
                    df.at[idx, "match_tier"] = "Tier 1: SKU Match"
                    match_found = True
                    break

        # Tier 2 & Tier 3: Text & Fuzzy Match
        if not match_found:
            for pool_sku, pool_clean, pool_parent in mapped_pool[brand]:
                if c_title == pool_clean:
                    matched_parent = pool_parent
                    best_score = 100.0
                    df.at[idx, "match_tier"] = "Tier 2: Exact Match"
                    match_found = True
                    break

                # Switched to token_sort_ratio to heavily penalize subset mismatches
                score = fuzz.token_sort_ratio(c_title, pool_clean)
                
                # NUMERIC GUARDRAIL: Prevent variant digit collisions
                nums_pool = set(re.findall(r'\d+', pool_clean))
                nums_curr = set(re.findall(r'\d+', c_title))
                if nums_pool != nums_curr:
                    score = 0.0

                if score > best_score:
                    best_score = score
                    matched_parent = pool_parent

            if not match_found and best_score >= MATCH_THRES:
                df.at[idx, "match_score"] = round(best_score, 1)
                df.at[idx, "match_tier"] = "Tier 3: Fuzzy Match"
                match_found = True

        # Assign UPK and Update Pool
        if match_found:
            df.at[idx, "parent_upk"] = matched_parent
            if df.at[idx, "match_score"] == 0.0:
                df.at[idx, "match_score"] = round(best_score, 1)
            mapped_pool[brand].append((sku, c_title, matched_parent))
        else:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3]
            if not prefix: prefix = "UNK"
            matched_parent = f"UPK-{prefix}-{parent_counter}"
            parent_counter += 1
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Baseline Root"
            mapped_pool[brand].append((sku, c_title, matched_parent))

        # Assign Variant-Level Child UPK
        clean_spec = re.sub(r'[^A-Za-z0-9]', '', spec).upper()
        suffix = v_id[-4:] if len(v_id) >= 4 else "VAR"
        df.at[idx, "child_upk"] = f"{matched_parent}-V_{clean_spec}_{suffix}"

    # Calculate Consensus Title for each Parent UPK
    for p_id in df["parent_upk"].unique():
        group_mask = df["parent_upk"] == p_id
        group_titles = df.loc[group_mask, "title"].dropna().tolist()
        
        latin_titles = [t for t in group_titles if re.match(r'^[\x00-\x7F\s]+$', str(t))]
        pool = latin_titles if latin_titles else group_titles
        
        if pool:
            # If multiple variants exist, strip variant suffixes (e.g., "- Peach")
            if len(set(pool)) > 1:
                base_titles = [re.sub(r'\s*-\s*.*$', '', str(t)).strip() for t in pool if str(t).strip()]
            else:
                # If it's a standalone product, keep the formatting as-is
                base_titles = pool
            
            # Find the most common base title
            counts = Counter(base_titles)
            consensus = counts.most_common(1)[0][0]
        else:
            consensus = ""
            
        df.loc[group_mask, "consensus_title"] = consensus

    # Export Mapping Audit Sheet
    audit_cols = ["canonical_brand", "origin_company", "parent_upk", "child_upk", 
                  "title", "consensus_title", "extracted_spec", "match_score", "match_tier"]
    mapping_audit_df = df[audit_cols].rename(columns={"title": "store_original_title"}).drop_duplicates()
    
    phase2_out_path = os.path.join(PHASE2_DIR, "phase2_parent_upk_mappings.csv")
    mapping_audit_df.to_csv(phase2_out_path, index=False)
    print(f"  [✓] Phase 2 Parent UPK Audit Mapping Sheet Saved: {phase2_out_path}\n")

    return df

# =====================================================================
# PHASE 3: FINAL TABLES & MARKET ANALYTICS EXPORT
# =====================================================================
def execute_phase_3(df):
    print("=========================================================")
    print("[+] PHASE 3: Generating Final Database Tables...")
    print("=========================================================")

    df["title"] = df["title"].apply(lambda x: re.sub(r'[^\x00-\x7F]+', '', str(x)).strip(" -Ø•"))

    if "change_notes" not in df.columns:
        df["change_notes"] = ""

    # 1. Micro Time-Series Performance Table
    micro_db = df[[
        "snapshot_date", "parent_upk", "child_upk", "canonical_brand", 
        "origin_company", "consensus_title", "title", "price", "stock", "change_notes"
    ]].copy()
    
    micro_db.columns = [
        "date", "parent_upk", "child_upk", "brand", 
        "company", "consensus_title", "store_original_title", "price", "stock", "system_notes"
    ]
    micro_db = micro_db.sort_values(by=["date", "brand", "parent_upk", "company"])
    micro_path = os.path.join(PHASE3_DIR, "micro_perf_timeseries.csv")
    micro_db.to_csv(micro_path, index=False)
    print(f"  [✓] Final Micro Performance Table Saved: {micro_path}")

    # 2. Macro Market Overview Table
    macro_sheet = df.groupby(["canonical_brand", "origin_company"])["parent_upk"].nunique().unstack(fill_value=0)
    macro_sheet["total_unique_market_products"] = df.groupby("canonical_brand")["parent_upk"].nunique()
    macro_sheet = macro_sheet.sort_values(by="total_unique_market_products", ascending=False).reset_index()
    macro_path = os.path.join(PHASE3_DIR, "macro_market_overview.csv")
    macro_sheet.to_csv(macro_path, index=False)
    print(f"  [✓] Final Macro Market Overview Table Saved: {macro_path}\n")

# =====================================================================
# PIPELINE EXECUTION
# =====================================================================
if __name__ == "__main__":
    if not os.path.exists(PHASE1_FILE):
        raise FileNotFoundError(f"Missing pre-ingestion file at {PHASE1_FILE}. Please run preingestion.py first.")

    print(f"[+] Loading Phase 1 Cleaned Data: {PHASE1_FILE}")
    p1_df = pd.read_csv(PHASE1_FILE)
    
    p2_df = execute_phase_2(p1_df)
    execute_phase_3(p2_df)

    print("=========================================================")
    print("[SUCCESS] Complete Unification Pipeline Executed Successfully!")
    print("=========================================================")