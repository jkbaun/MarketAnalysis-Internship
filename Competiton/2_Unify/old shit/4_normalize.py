import os
import re
import pandas as pd
from rapidfuzz import fuzz

def generate_consensus_title(titles_list):
    """
    Selects the cleanest, most representative title from a group of store titles.
    """
    latin_titles = [t for t in titles_list if re.match(r'^[\x00-\x7F\s]+$', str(t))]
    pool = latin_titles if latin_titles else titles_list
    sorted_by_len = sorted(pool, key=len)
    return sorted_by_len[len(sorted_by_len) // 2] if sorted_by_len else ""

def multi_tier_unification(df, match_threshold=85):
    """
    Resolves product identities (Tier 1: SKU, Tier 2: Exact, Tier 3: Fuzzy).
    Expects 'clean_match_str' and 'canonical_brand' to be present.
    """
    df["upk_id"] = ""
    df["match_confidence"] = 0.0
    df["match_tier"] = ""
    
    mapped_pool = {} 
    upk_counter = 1000
    
    for idx, row in df.iterrows():
        brand = row.get("canonical_brand", "Unknown")
        c_title = row.get("clean_match_str", "")
        sku = str(row.get("sku", "")).strip()
        if sku == "nan" or sku == "None": 
            sku = ""
        
        if brand not in mapped_pool:
            mapped_pool[brand] = []
            
        match_found = False
        best_score = 0
        best_upk = ""
        
        # TIER 1: SKU MATCH
        if sku:
            for pool_sku, pool_clean_str, pool_upk in mapped_pool[brand]:
                if pool_sku == sku:
                    best_upk = pool_upk
                    df.at[idx, "match_confidence"] = 100.0
                    df.at[idx, "match_tier"] = "Tier 1: SKU"
                    match_found = True
                    break
        
        # TIER 2 & 3: TEXT MATCHING
        # TIER 2 & 3: TEXT MATCHING
        if not match_found:
            for pool_sku, pool_clean_str, pool_upk in mapped_pool[brand]:
                if c_title == pool_clean_str:
                    best_upk = pool_upk
                    best_score = 100.0
                    df.at[idx, "match_tier"] = "Tier 2: Exact Clean"
                    match_found = True
                    break
                    
                score = fuzz.token_set_ratio(c_title, pool_clean_str)
                
                # [CRITICAL FIX]: Block fuzzy matches if variant numbers/shades differ
                nums_pool = set(re.findall(r'\d+', pool_clean_str))
                nums_curr = set(re.findall(r'\d+', c_title))
                if nums_pool != nums_curr:
                    score = 0  # Zero out score so different variants never collide
                
                if score > best_score:
                    best_score = score
                    best_upk = pool_upk
            
            if not match_found and best_score >= match_threshold:
                df.at[idx, "match_confidence"] = round(best_score, 1)
                df.at[idx, "match_tier"] = "Tier 3: Fuzzy"
                match_found = True
        
        # ASSIGN OR CREATE UPK
        if match_found:
            df.at[idx, "upk_id"] = best_upk
            if df.at[idx, "match_confidence"] == 0.0:
                df.at[idx, "match_confidence"] = round(best_score, 1)
                
            # [CRITICAL FIX]: Add matched aliases back into the pool!
            # This guarantees subsequent identical strings get a Tier 2 Exact Match.
            mapped_pool[brand].append((sku, c_title, best_upk))
            
        else:
            # [FIX]: Corrected regex typo (A-Za-z)
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3]
            new_upk = f"UPK-{prefix}-{upk_counter}"
            upk_counter += 1
            df.at[idx, "upk_id"] = new_upk
            df.at[idx, "match_confidence"] = 100.0
            df.at[idx, "match_tier"] = "Baseline Original"
            
            mapped_pool[brand].append((sku, c_title, new_upk))
    
    return df

def build_outputs(df, final_dir="Data/unified_database/final_tables"):
    req_cols = ["snapshot_date", "upk_id", "canonical_brand", "origin_company", 
                "title", "price", "inventory_quantity", "change_notes"]
    missing_cols = [c for c in req_cols if c not in df.columns]
    for c in missing_cols:
        df[c] = ""
        
    micro_db = df[req_cols].copy()
    micro_db.columns = ["date", "upk_id", "brand", "company", "store_original_title", "price", "stock", "system_notes"]
    micro_db = micro_db.sort_values(by=["date", "brand", "upk_id", "company"])
    micro_db.to_csv(os.path.join(final_dir, "micro_perf_timeseries.csv"), index=False)
    
    macro_sheet = df.groupby(["canonical_brand", "origin_company"])["upk_id"].nunique().unstack(fill_value=0)
    macro_sheet["total_unique_market_products"] = df.groupby("canonical_brand")["upk_id"].nunique()
    macro_sheet = macro_sheet.sort_values(by="total_unique_market_products", ascending=False).reset_index()
    macro_sheet.to_csv(os.path.join(final_dir, "macro_market_overview.csv"), index=False)