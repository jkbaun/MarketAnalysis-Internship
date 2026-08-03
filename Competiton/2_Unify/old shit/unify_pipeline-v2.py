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

OVERRIDE_FILE = os.path.join(PHASE2_DIR, "manual_upk_overrides.csv")
REVIEW_QUEUE_FILE = os.path.join(PHASE2_DIR, "unification_review_queue.csv")

MATCH_THRES = 80  # Lower threshold since blocking guarantees product type safety

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================
def calculate_jaccard_similarity(str1, str2):
    set1, set2 = set(str1.split()), set(str2.split())
    intersection = set1.intersection(set2)
    union = set1.union(set2)
    return (len(intersection) / len(union)) * 100.0 if union else 0.0

def load_manual_overrides():
    if not os.path.exists(OVERRIDE_FILE):
        # Create empty template if missing
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

# =====================================================================
# PHASE 2: MATCHING ENGINE & REVIEW QUEUE GENERATION
# =====================================================================
def execute_phase_2(df):
    print("=========================================================")
    print("[+] PHASE 2: Executing Blocked Matching & Unification...")
    print("=========================================================")

    # 1. Load Manual Overrides
    overrides = load_manual_overrides()

    # 2. Canonical Brand Resolution
    trusted_mask = ~df["vendor"].astype(str).str.lower().str.contains("soko", na=False)
    unique_vendors = df[trusted_mask]["vendor"].dropna().unique()
    brand_directory = {re.sub(r'[^a-z0-9]', '', str(v).lower()): v for v in unique_vendors if v}
    brand_directory.update({"dralthea": "Dr. Althea", "cosrx": "Cosrx", "medicube": "Medicube", "skin1004": "SKIN1004", "anua": "ANUA"})

    resolved_brands = []
    for idx, row in df.iterrows():
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

    # 3. Blocked Multi-Tier Matching Engine
    df["parent_upk"] = ""
    df["child_upk"] = ""
    df["consensus_title"] = ""
    df["match_score"] = 0.0
    df["match_tier"] = ""

    # Structure: mapped_pool[brand][spec] = [ (sku, fingerprint, clean_str, parent_upk) ]
    blocked_pool = {}
    parent_counter = 1000

    for idx, row in df.iterrows():
        brand = row.get("canonical_brand", "Unknown Brand")
        orig_title = str(row.get("title", "")).strip()
        company = str(row.get("origin_company", "")).strip().lower()
        c_title = str(row.get("clean_match_str", ""))
        sku = str(row.get("sku", "")).strip()
        spec = str(row.get("extracted_spec", "NO_SPEC"))
        fingerprint = str(row.get("composite_fingerprint", ""))
        v_id = str(row.get("variant_id", "")).strip()

        if sku in ["nan", "None"]: sku = ""

        # Tier 0: Manual Override Check
        if (orig_title, company) in overrides:
            matched_parent = overrides[(orig_title, company)]
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Tier 0: Manual Override"
            
            # Seed overridden item into blocked pool
            blocked_pool.setdefault(brand, {}).setdefault(spec, []).append((sku, fingerprint, c_title, matched_parent))
            continue

        if brand not in blocked_pool:
            blocked_pool[brand] = {}

        pool = blocked_pool[brand]
        match_found = False
        best_score = 0.0
        matched_parent = ""

        # Tier 1: SKU Match (within brand)
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

        # Tier 2: Deterministic Fingerprint Match (O(1) exact signature match)
        if not match_found and fingerprint:
            for p_sku, p_fp, p_clean, p_upk in pool:
                if p_fp == fingerprint:
                    matched_parent = p_upk
                    df.at[idx, "match_score"] = 100.0
                    df.at[idx, "match_tier"] = "Tier 2: Fingerprint Match"
                    match_found = True
                    break

        # Tier 3: Blocked Jaccard & Fuzzy Match (strictly inside same Brand + Spec block)
        if not match_found:
            for p_sku, p_fp, p_clean, p_upk in pool:
                if c_title == p_clean:
                    matched_parent = p_upk
                    best_score = 100.0
                    df.at[idx, "match_tier"] = "Tier 3A: Exact Text Match"
                    match_found = True
                    break

                c_base = str(row.get("clean_base_title", ""))

# Inside your Tier 3 matching loop, compare against the base title
                jaccard = calculate_jaccard_similarity(c_base, p_clean)
                token_fuzzy = fuzz.token_sort_ratio(c_base, p_clean)
                score = (jaccard * 0.4) + (token_fuzzy * 0.6)

                # Numeric Guardrail
                if set(re.findall(r'\d+', p_clean)) != set(re.findall(r'\d+', c_title)):
                    score = 0.0

                if score > best_score:
                    best_score = score
                    matched_parent = p_upk

            if not match_found and best_score >= MATCH_THRES:
                df.at[idx, "match_score"] = round(best_score, 1)
                df.at[idx, "match_tier"] = "Tier 3B: Blocked Fuzzy Match"
                match_found = True

        # Tier 4: Baseline Root Assignment
        if match_found:
            df.at[idx, "parent_upk"] = matched_parent
            if df.at[idx, "match_score"] == 0.0:
                df.at[idx, "match_score"] = round(best_score, 1)
            blocked_pool[brand][spec].append((sku, fingerprint, c_title, matched_parent))
        else:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3]
            if not prefix: prefix = "UNK"
            matched_parent = f"UPK-{prefix}-{parent_counter}"
            parent_counter += 1
            df.at[idx, "parent_upk"] = matched_parent
            df.at[idx, "match_score"] = 100.0
            df.at[idx, "match_tier"] = "Tier 4: Baseline Root"
            blocked_pool[brand][spec].append((sku, fingerprint, c_title, matched_parent))

        # Assign Variant-Level Child UPK
        clean_spec = re.sub(r'[^A-Za-z0-9]', '', spec).upper()
        suffix = v_id[-4:] if len(v_id) >= 4 else "VAR"
        df.at[idx, "child_upk"] = f"{matched_parent}-V_{clean_spec}_{suffix}"

    # Calculate Consensus Titles
    for p_id in df["parent_upk"].unique():
        group_mask = df["parent_upk"] == p_id
        group_titles = df.loc[group_mask, "title"].dropna().tolist()
        latin_titles = [t for t in group_titles if re.match(r'^[\x00-\x7F\s]+$', str(t))]
        pool = latin_titles if latin_titles else group_titles
        
        if pool:
            base_titles = [re.sub(r'\s*-\s*.*$', '', str(t)).strip() for t in pool if str(t).strip()] if len(set(pool)) > 1 else pool
            consensus = Counter(base_titles).most_common(1)[0][0]
        else:
            consensus = ""
        df.loc[group_mask, "consensus_title"] = consensus

    # Export Full Audit Sheet
    audit_cols = ["canonical_brand", "origin_company", "parent_upk", "child_upk", 
                  "title", "consensus_title", "extracted_spec", "match_score", "match_tier"]
    mapping_audit_df = df[audit_cols].rename(columns={"title": "store_original_title"}).drop_duplicates()
    phase2_out_path = os.path.join(PHASE2_DIR, "phase2_parent_upk_mappings.csv")
    mapping_audit_df.to_csv(phase2_out_path, index=False)

    # Export Exception / Review Queue
    # Flag items with low fuzzy scores OR newly generated roots for user verification
    review_mask = (
        (df["match_tier"] == "Tier 3B: Blocked Fuzzy Match") & (df["match_score"] < 90.0)
    ) | (df["match_tier"] == "Tier 4: Baseline Root")
    
    review_df = df[review_mask][["canonical_brand", "origin_company", "parent_upk", "title", "extracted_spec", "match_score", "match_tier"]]
    review_df.to_csv(REVIEW_QUEUE_FILE, index=False)

    print(f"  [✓] Phase 2 Mappings Saved: {phase2_out_path}")
    print(f"  [✓] Exception Review Queue Saved ({len(review_df)} items flagged): {REVIEW_QUEUE_FILE}\n")

    return df

# =====================================================================
# PHASE 3: FINAL TABLES EXPORT
# =====================================================================
def execute_phase_3(df):
    print("=========================================================")
    print("[+] PHASE 3: Exporting Unified Analytical Tables...")
    print("=========================================================")

    df["title"] = df["title"].apply(lambda x: re.sub(r'[^\x00-\x7F]+', '', str(x)).strip(" -Ø•"))
    if "change_notes" not in df.columns: df["change_notes"] = ""

    # Micro Time-Series Table
    micro_db = df[[
        "snapshot_date", "parent_upk", "child_upk", "canonical_brand", 
        "origin_company", "consensus_title", "title", "price", "stock", "change_notes"
    ]].copy()
    micro_db.columns = ["date", "parent_upk", "child_upk", "brand", "company", "consensus_title", "store_original_title", "price", "stock", "system_notes"]
    micro_db = micro_db.sort_values(by=["date", "brand", "parent_upk", "company"])
    micro_path = os.path.join(PHASE3_DIR, "micro_perf_timeseries.csv")
    micro_db.to_csv(micro_path, index=False)

    # Macro Market Overview
    macro_sheet = df.groupby(["canonical_brand", "origin_company"])["parent_upk"].nunique().unstack(fill_value=0)
    macro_sheet["total_unique_market_products"] = df.groupby("canonical_brand")["parent_upk"].nunique()
    macro_sheet = macro_sheet.sort_values(by="total_unique_market_products", ascending=False).reset_index()
    macro_path = os.path.join(PHASE3_DIR, "macro_market_overview.csv")
    macro_sheet.to_csv(macro_path, index=False)

    print(f"  [✓] Micro Performance Table Saved: {micro_path}")
    print(f"  [✓] Macro Market Overview Saved: {macro_path}\n")

if __name__ == "__main__":
    if not os.path.exists(PHASE1_FILE):
        raise FileNotFoundError(f"Missing pre-ingestion file at {PHASE1_FILE}. Run preingestion_2.py first.")

    p1_df = pd.read_csv(PHASE1_FILE)
    p2_df = execute_phase_2(p1_df)
    execute_phase_3(p2_df)

    print("=========================================================")
    print("[SUCCESS] Complete Pipeline Executed Successfully!")
    print("=========================================================")