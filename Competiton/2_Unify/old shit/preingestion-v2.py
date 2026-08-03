import os
import glob
import re
from datetime import datetime
import pandas as pd

# =====================================================================
# PATH CONFIGURATION & CONSTANTS
# =====================================================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)

INPUT_DIR = os.path.join(BASE_DIR, "Data/scrapped_json")
OUTPUT_DIR = os.path.join(BASE_DIR, "Data/unified_database")

STAGING_DIR = os.path.join(OUTPUT_DIR, "company_clean_staging")
PHASE1_DIR = os.path.join(OUTPUT_DIR, "phase1_cleaned")

os.makedirs(STAGING_DIR, exist_ok=True)
os.makedirs(PHASE1_DIR, exist_ok=True)

AED_TO_BHD_RATE = 0.1028

# =====================================================================
# TEXT CLEANER, SPEC EXTRACTION & FINGERPRINTING
# =====================================================================
def clean_and_extract_specs(title, vendor):
    """
    1. Extracts volume/weight specifications (ml, g, oz, etc.).
    2. Removes vendor/brand noise safely.
    3. Purges non-ASCII characters.
    4. Generates a Deterministic Composite Fingerprint.
    """
    if pd.isna(title) or not str(title).strip():
        return "", "NO_SPEC", "UNK", "", "UNK_FINGERPRINT"

    raw_title = str(title)
    raw_vendor = str(vendor).strip() if pd.notna(vendor) else "Unknown"

    brand_prefix = re.sub(r'[^A-Za-z0-9]', '', raw_vendor).upper()[:3]
    if not brand_prefix:
        brand_prefix = "UNK"

    text_lower = raw_title.lower()

    # 1. Spec Extraction
    size_pattern = r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs|fl\s*oz|oz)\b'
    all_matches = re.findall(size_pattern, text_lower)

    extracted_specs = []
    if all_matches:
        for num, unit in all_matches:
            clean_unit = re.sub(r'\s+', '', unit)
            extracted_specs.append(f"{num}{clean_unit}")
        text_lower = re.sub(size_pattern, '', text_lower)

    extracted_spec_str = " + ".join(extracted_specs) if extracted_specs else "NO_SPEC"

    # 2. Vendor Cleaning
    if raw_vendor and raw_vendor.lower() != "unknown":
        vendor_clean = re.sub(r'[^a-z0-9]', '', raw_vendor.lower())
        pattern_vendor_raw = r'\b' + re.escape(raw_vendor.lower()) + r'\b'
        text_lower = re.sub(pattern_vendor_raw, '', text_lower)
        if vendor_clean and len(vendor_clean) > 2:
            pattern_vendor_clean = r'\b' + re.escape(vendor_clean) + r'\b'
            text_lower = re.sub(pattern_vendor_clean, '', text_lower)

    if "althea" in raw_vendor.lower():
        text_lower = re.sub(r'\b(dr\.?\s*althea|dralthea)\b', '', text_lower)

    # 3. Non-ASCII Purge
    text_clean = re.sub(r'[^a-z0-9\s]', ' ', text_lower)
    text_clean = re.sub(r'[^\x00-\x7F]+', ' ', text_clean)
    clean_base_title = " ".join(text_clean.split())
    clean_match_str = f"{clean_base_title} {extracted_spec_str}".strip() if extracted_spec_str != "NO_SPEC" else clean_base_title

    # 4. Deterministic Composite Fingerprint Generation
    # Extract unique core words and sort alphabetically to ignore word order
    words = sorted(list(set(clean_base_title.split())))
    core_tokens_str = "".join(words)
    clean_spec_tag = re.sub(r'[^A-Za-z0-9]', '', extracted_spec_str).upper()
    
    composite_fingerprint = f"{brand_prefix}_{core_tokens_str}_{clean_spec_tag}".upper()

    return clean_base_title, extracted_spec_str, brand_prefix, clean_match_str, composite_fingerprint


def stage_single_company(p_file, s_file, company_name):
    print(f"  [->] Fusing & Staging: {company_name.upper()}")

    products_raw = pd.read_json(p_file) if os.path.exists(p_file) else pd.DataFrame()
    stocks_raw = pd.read_json(s_file) if (s_file and os.path.exists(s_file)) else pd.DataFrame()

    if products_raw.empty:
        print(f"      [!] Skipping {company_name}: Empty products file.")
        return pd.DataFrame()

    for df in [products_raw, stocks_raw]:
        if not df.empty and "id" in df.columns and "product_id" not in df.columns:
            df.rename(columns={"id": "product_id"}, inplace=True)

    stock_dict = {}
    snapshot_date = datetime.now().strftime("%Y-%m-%d")

    if not stocks_raw.empty and "product_id" in stocks_raw.columns:
        if "snapshot_date" in stocks_raw.columns:
            valid_dates = stocks_raw["snapshot_date"].dropna().astype(str)
            if not valid_dates.empty:
                snapshot_date = valid_dates.iloc[-1]
            stocks_raw = stocks_raw.sort_values(by="snapshot_date", ascending=True)

        for _, s_row in stocks_raw.iterrows():
            p_id = str(s_row.get("product_id", ""))
            qty = pd.to_numeric(s_row.get("inventory_quantity") or s_row.get("stock"), errors='coerce')
            stock_dict[p_id] = int(qty) if pd.notna(qty) else 0

    expanded_rows = []

    for _, p_row in products_raw.iterrows():
        p_id = str(p_row.get("product_id", ""))
        base_title = str(p_row.get("title", ""))
        vendor = str(p_row.get("vendor", ""))
        variants = p_row.get("variants", [])
        fallback_stock = stock_dict.get(p_id, 0)

        if isinstance(variants, list) and len(variants) > 0:
            for v in variants:
                v_title = str(v.get("title", "")).strip()
                full_title = f"{base_title} - {v_title}" if v_title and v_title.lower() not in ["default title", "default"] else base_title
                v_stock = pd.to_numeric(v.get("inventory_quantity"), errors='coerce')
                final_stock = int(v_stock) if pd.notna(v_stock) else fallback_stock

                expanded_rows.append({
                    "snapshot_date": snapshot_date, "product_id": p_id, "variant_id": str(v.get("id", "")),
                    "title": full_title, "vendor": vendor, "sku": str(v.get("sku", "")).strip(),
                    "price": pd.to_numeric(v.get("price"), errors='coerce'), "stock": final_stock,
                    "origin_company": company_name
                })
        else:
            expanded_rows.append({
                "snapshot_date": snapshot_date, "product_id": p_id, "variant_id": "",
                "title": base_title, "vendor": vendor, "sku": str(p_row.get("sku", "")).strip(),
                "price": pd.to_numeric(p_row.get("price"), errors='coerce'), "stock": fallback_stock,
                "origin_company": company_name
            })

    staged_df = pd.DataFrame(expanded_rows)

    if company_name.lower() == "xbeauty":
        staged_df["price"] = staged_df["price"] * AED_TO_BHD_RATE

    cleaned_bases, extracted_specs, brand_prefixes, clean_match_strs, fingerprints = [], [], [], [], []
    for _, row in staged_df.iterrows():
        base, spec, prefix, match_str, fp = clean_and_extract_specs(row["title"], row["vendor"])
        cleaned_bases.append(base)
        extracted_specs.append(spec)
        brand_prefixes.append(prefix)
        clean_match_strs.append(match_str)
        fingerprints.append(fp)

    staged_df["clean_base_title"] = cleaned_bases
    staged_df["extracted_spec"] = extracted_specs
    staged_df["brand_prefix"] = brand_prefixes
    staged_df["clean_match_str"] = clean_match_strs
    staged_df["composite_fingerprint"] = fingerprints

    out_path = os.path.join(STAGING_DIR, f"{company_name}_clean_staging.csv")
    staged_df.to_csv(out_path, index=False)
    print(f"      [✓] Staged {len(staged_df)} variant rows -> {out_path}")

    return staged_df


def execute_preingestion():
    print("=========================================================")
    print("[+] PRE-INGESTION: Fusing Products & Stocks across Stores...")
    print("=========================================================")

    product_files = glob.glob(os.path.join(INPUT_DIR, "master_products_*.json"))
    staged_dfs = []

    for p_file in product_files:
        company = os.path.basename(p_file).replace("master_products_", "").replace(".json", "")
        s_file = os.path.join(INPUT_DIR, f"master_stock_{company}.json")
        df_staged = stage_single_company(p_file, s_file, company)
        if not df_staged.empty:
            staged_dfs.append(df_staged)

    if not staged_dfs:
        raise FileNotFoundError("No product JSON files found in input directory.")

    master_phase1_df = pd.concat(staged_dfs, ignore_index=True)
    master_p1_path = os.path.join(PHASE1_DIR, "master_phase1_cleaned_joined.csv")
    master_phase1_df.to_csv(master_p1_path, index=False)

    print(f"\n  [✓] Master Pre-Ingestion Joined Dataset Saved ({len(master_phase1_df)} rows): {master_p1_path}\n")
    return master_phase1_df


if __name__ == "__main__":
    execute_preingestion()