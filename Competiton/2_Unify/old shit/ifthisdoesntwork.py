import sqlite3
import pandas as pd
import re
import os
from rapidfuzz import fuzz

DB_FILE = "Data/unified_database/beauty_market_data.db"
OVERRIDE_FILE = "Data/unified_database/mappings/manual_upk_overrides.csv"
MATCH_THRES = 80 

def calculate_jaccard_similarity(str1, str2):
    set1, set2 = set(str1.split()), set(str2.split())
    union = set1.union(set2)
    return (len(set1.intersection(set2)) / len(union)) * 100.0 if union else 0.0

def load_manual_overrides():
    if not os.path.exists(OVERRIDE_FILE): return {}
    df_override = pd.read_csv(OVERRIDE_FILE)
    return {(str(r["store_original_title"]).strip(), str(r["origin_company"]).strip().lower()): str(r["target_parent_upk"]).strip() 
            for _, r in df_override.iterrows() if pd.notna(r["target_parent_upk"])}

def run_master_unification(staged_df, scrape_date):
    """
    Combines the SQLite persistence of Source 13 with the multi-tier 
    blocked matching of Source 16.
    """
    print("=========================================================")
    print(f"[+] PHASE 2: Stateful Unification & Ingestion for {scrape_date}")
    print("=========================================================")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Load Stateful Registry & Overrides
    registry_df = pd.read_sql("SELECT * FROM master_upk_registry", conn) 
    overrides = load_manual_overrides() 
    
    # 2. Determine next available UPK counter
    if not registry_df.empty:
        max_upk_num = registry_df['parent_upk'].str.extract(r'UPK-[A-Z]+-(\d+)')[0].astype(float).max()
        parent_counter = int(max_upk_num) + 1 if pd.notnull(max_upk_num) else 1000
    else:
        parent_counter = 1000

    # 3. Setup Tracking Columns
    staged_df["parent_upk"] = ""
    staged_df["child_upk"] = ""
    staged_df["match_score"] = 0.0
    staged_df["match_tier"] = ""

    # Structure for intra-batch matching: pool[brand][spec] = [(sku, fingerprint, clean_str, parent_upk)]
    blocked_pool = {} 
    processed_daily_rows = []
    new_registry_entries = []

    for idx, row in staged_df.iterrows():
        store = str(row.get("origin_company", "")).strip()
        handle = str(row.get("product_id", "")).strip()  # Treat ID/Handle as unique store key
        brand = row.get("canonical_brand", "Unknown Brand") 
        orig_title = str(row.get("title", "")).strip()
        c_title = str(row.get("clean_match_str", ""))
        c_base = str(row.get("clean_base_title", ""))
        sku = str(row.get("sku", "")).strip() if str(row.get("sku", "")) not in ["nan", "None"] else ""
        spec = str(row.get("extracted_spec", "NO_SPEC"))
        fingerprint = str(row.get("composite_fingerprint", ""))
        v_id = str(row.get("variant_id", "")).strip()
        
        matched_parent = None
        match_tier = ""
        match_score = 0.0

        # ==========================================
        # TIER 0: STATEFUL LOOKUPS & OVERRIDES
        # ==========================================
        # Check Overrides First
        if (orig_title, store.lower()) in overrides:
            matched_parent = overrides[(orig_title, store.lower())]
            match_tier = "Tier 0: Manual Override"
            match_score = 100.0 

        # Check SQLite Persistent State
        if not matched_parent and not registry_df.empty:
            existing = registry_df[(registry_df['store_name'] == store) & (registry_df['product_handle'] == handle)] 
            if not existing.empty:
                matched_parent = existing.iloc[0]['parent_upk']
                match_tier = "Tier 0: Historical Database Match"
                match_score = 100.0

        # ==========================================
        # TIER 1-3: DYNAMIC BLOCKED MATCHING (New Items)
        # ==========================================
        if not matched_parent:
            if brand not in blocked_pool: blocked_pool[brand] = {}
            pool = blocked_pool[brand]
            
            # Look inside the current brand + spec block
            for b_spec, items in pool.items():
                # Tier 1: SKU Match[cite: 12]
                if sku and not matched_parent:
                    for p_sku, p_fp, p_clean, p_upk in items:
                        if p_sku == sku:
                            matched_parent, match_tier, match_score = p_upk, "Tier 1: SKU Match", 100.0
                            break
                            
                # Tier 2: Exact Fingerprint Match[cite: 16]
                if fingerprint and not matched_parent:
                    for p_sku, p_fp, p_clean, p_upk in items:
                        if p_fp == fingerprint:
                            matched_parent, match_tier, match_score = p_upk, "Tier 2: Fingerprint Match", 100.0
                            break

            # Tier 3: Blocked Fuzzy Match (Guardrailed by numbers)[cite: 16, 12]
            if not matched_parent and spec in pool:
                best_score = 0.0
                for p_sku, p_fp, p_clean, p_upk in pool[spec]:
                    if c_title == p_clean:
                        matched_parent, match_tier, match_score = p_upk, "Tier 3A: Exact Text", 100.0
                        break
                    
                    j_score = calculate_jaccard_similarity(c_base, p_clean)
                    f_score = fuzz.token_sort_ratio(c_base, p_clean)
                    score = (j_score * 0.4) + (f_score * 0.6)
                    
                    # Numeric Guardrail: If numbers differ, kill the score[cite: 12]
                    if set(re.findall(r'\d+', p_clean)) != set(re.findall(r'\d+', c_title)):
                        score = 0.0
                        
                    if score > best_score:
                        best_score = score
                        if best_score >= MATCH_THRES:
                            matched_parent = p_upk
                            match_tier = "Tier 3B: Blocked Fuzzy Match"
                            match_score = round(best_score, 1)

        # ==========================================
        # TIER 4: BASELINE GENERATION
        # ==========================================
        if not matched_parent:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3] or "UNK" 
            matched_parent = f"UPK-{prefix}-{parent_counter}" 
            parent_counter += 1
            match_tier = "Tier 4: Baseline Root"
            match_score = 100.0

        # ==========================================
        # ASSIGNMENT & DATABASE COMMIT PREP
        # ==========================================
        # 1. Generate Child Variant UPK[cite: 14]
        clean_spec = re.sub(r'[^A-Za-z0-9]', '', spec).upper()
        suffix = v_id[-4:] if len(v_id) >= 4 else "VAR"
        child_upk = f"{matched_parent}-V_{clean_spec}_{suffix}"

        # 2. Update DataFrame
        staged_df.at[idx, "parent_upk"] = matched_parent
        staged_df.at[idx, "child_upk"] = child_upk
        staged_df.at[idx, "match_tier"] = match_tier
        staged_df.at[idx, "match_score"] = match_score

        # 3. Seed back into runtime pool for next rows[cite: 12, 16]
        blocked_pool.setdefault(brand, {}).setdefault(spec, []).append((sku, fingerprint, c_title, matched_parent))

        # 4. Prepare Database Inserts
        price = pd.to_numeric(row.get("price", 0.0), errors='coerce')
        stock = row.get("stock", 0)
        stock_status = "In Stock" if stock > 0 else "Out of Stock"
        
        processed_daily_rows.append((
            scrape_date, store, handle, matched_parent, child_upk, orig_title, brand, price, stock_status
        ))
        
        # Only add to registry if it's a newly discovered mapping
        if "Tier 0" not in match_tier:
            new_registry_entries.append((
                store, handle, brand, matched_parent, child_upk, orig_title, spec
            ))

    # ==========================================
    # COMMIT TO PERSISTENT STATE
    # ==========================================
    if new_registry_entries:
        cursor.executemany("""
        INSERT OR REPLACE INTO master_upk_registry 
        (store_name, product_handle, canonical_brand, parent_upk, child_upk, base_title, variant_spec)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, new_registry_entries)

    cursor.executemany("""
    INSERT OR REPLACE INTO daily_scraped_inventory 
    (scrape_date, store_name, product_handle, parent_upk, child_upk, store_title, canonical_brand, price_bhd, stock_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, processed_daily_rows)

    conn.commit()
    conn.close()

    print(f"  [✓] Processed and committed {len(staged_df)} items to SQLite Database.")
    return staged_df