import pandas as pd
import re
from rapidfuzz import fuzz

def robust_relational_unification(raw_df):
    """
    Prevents Cartesian explosion by separating Parent unification from Variant assignment.
    Expects 'raw_df' to have: date, company, title, vendor, sku, variants (list of dicts).
    """
    
    # ---------------------------------------------------------
    # STEP 1: SAFE UNNESTING (Avoid Time-Series Duplication)
    # ---------------------------------------------------------
    # Instead of a naive join, we ensure we only look at the most recent snapshot
    # to prevent historical dates from multiplying our rows.
    latest_date = raw_df['snapshot_date'].max()
    df_current = raw_df[raw_df['snapshot_date'] == latest_date].copy()
    
    expanded_rows = []
    
    for _, row in df_current.iterrows():
        base_title = str(row.get('title', '')).strip()
        vendor = str(row.get('vendor', 'Unknown'))
        company = str(row.get('origin_company', ''))
        variants = row.get('variants', [])
        
        # Normalize the base title to prevent variant specs from confusing the Parent matcher
        clean_parent_title = re.sub(r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs|oz)\b', '', base_title.lower())
        clean_parent_title = re.sub(r'[^a-z0-9\s]', '', clean_parent_title).strip()
        
        # ---------------------------------------------------------
        # STEP 2: PARENT-LEVEL EXTRACTION
        # ---------------------------------------------------------
        if isinstance(variants, list) and len(variants) > 0:
            for v in variants:
                # Extract specific variant details
                v_title = str(v.get('title', '')).lower()
                v_sku = str(v.get('sku', '')).strip()
                v_price = pd.to_numeric(v.get('price'), errors='coerce')
                
                # Extract the numeric/spec differentiator for this specific variant
                v_spec_match = re.search(r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs|oz)\b', v_title)
                v_spec = f"{v_spec_match.group(1)}{v_spec_match.group(2)}" if v_spec_match else "DEFAULT"
                
                expanded_rows.append({
                    "company": company,
                    "vendor": vendor,
                    "parent_match_key": clean_parent_title, # Used ONLY for parent matching
                    "original_title": base_title,
                    "variant_title": v_title,
                    "variant_sku": v_sku,
                    "variant_spec": v_spec.upper(),
                    "price": v_price
                })
        else:
            # Handle products with no explicit variants
            expanded_rows.append({
                "company": company,
                "vendor": vendor,
                "parent_match_key": clean_parent_title,
                "original_title": base_title,
                "variant_title": "Default",
                "variant_sku": str(row.get('sku', '')).strip(),
                "variant_spec": "DEFAULT",
                "price": pd.to_numeric(row.get('price'), errors='coerce')
            })

    flat_df = pd.DataFrame(expanded_rows)

    # ---------------------------------------------------------
    # STEP 3: PARENT UPK UNIFICATION (No Variant Tangling)
    # ---------------------------------------------------------
    parent_pool = {}
    parent_counter = 1000
    
    flat_df["parent_upk"] = ""
    
    # We group by the clean_parent_title so we only match the base product ONCE
    unique_parents = flat_df[['vendor', 'parent_match_key']].drop_duplicates()
    
    for _, p_row in unique_parents.iterrows():
        brand = p_row['vendor']
        p_key = p_row['parent_match_key']
        
        if brand not in parent_pool:
            parent_pool[brand] = []
            
        match_found = False
        matched_upk = ""
        
        # Fuzzy match strictly on the Parent Key (Variant specs are stripped!)
        for pool_key, pool_upk in parent_pool[brand]:
            if fuzz.token_sort_ratio(p_key, pool_key) > 85:
                matched_upk = pool_upk
                match_found = True
                break
                
        if not match_found:
            prefix = re.sub(r'[^A-Za-z0-9]', '', brand).upper()[:3] or "UNK"
            matched_upk = f"UPK-{prefix}-{parent_counter}"
            parent_counter += 1
            parent_pool[brand].append((p_key, matched_upk))
            
        # Assign Parent UPK back to all variants associated with this parent key
        flat_df.loc[(flat_df['vendor'] == brand) & (flat_df['parent_match_key'] == p_key), 'parent_upk'] = matched_upk

    # ---------------------------------------------------------
    # STEP 4: CHILD UPK GENERATION
    # ---------------------------------------------------------
    # Now that parents are safely unified, we generate deterministic Child UPKs
    # based strictly on the Parent UPK + the Variant Spec. 
    # This completely eliminates cross-company variant explosion.
    
    flat_df["child_upk"] = flat_df.apply(lambda x: f"{x['parent_upk']}-V_{x['variant_spec']}", axis=1)
    
    return flat_df