import re
import pandas as pd

def advanced_clean_title(title, brand):
    """
    Transforms messy titles into pristine, high-signal English token keys.
    Extracts measurements, purges non-ASCII characters, and drops brand noise.
    """
    if pd.isna(title) or title is None:
        return ""
    
    text = str(title).lower().strip()
    
    # 1. Extract and standardize capacity measurements
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs)\b', text)
    size_str = ""
    if size_match:
        size_str = f"{size_match.group(1)}{size_match.group(2)}"
        text = text.replace(size_match.group(0), "")
        
    # 2. Drop brand keywords to avoid skewing similarity counts
    norm_brand = re.sub(r'[^a-z0-9]', '', str(brand).lower())
    text = text.replace(norm_brand, "").replace(str(brand).lower(), "")
    
    if "althea" in norm_brand:
        text = text.replace("dr.althea", "").replace("dralthea", "").replace("dr althea", "")
        
    # 3. Purge non-ASCII characters
    text = re.sub(r'[^a-z0-9\s]', ' ', text)  
    text = re.sub(r'[^\x00-\x7F]+', ' ', text) 
    
    # 4. Clean up structural whitespace sequences
    words = [w for w in text.split() if w]
    clean_base = " ".join(words)
    
    if size_str:
        return f"{clean_base} {size_str}".strip()
    return clean_base.strip()

def extract_variant_spec(row):
    """
    1. Checks native Shopify 'grams' or 'variant_grams' fields first.
    2. Falls back to regex extraction from the title string if missing/zero.
    """
    native_grams = row.get("grams") or row.get("variant_grams")
    if pd.notna(native_grams):
        try:
            val = float(native_grams)
            if val > 0:
                return f"{int(val)}g"
        except (ValueError, TypeError):
            pass

    raw_title = str(row.get("title", "")).lower()
    size_pattern = r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs)\b'
    size_match = re.search(size_pattern, raw_title)
    
    if size_match:
        return f"{size_match.group(1)}{size_match.group(2)}"
        
    return "NO_SPEC"

def stage_2_clean_text_and_extract_specs(df):
    """
    1. Removes Arabic/non-ASCII characters.
    2. Strips brand names from product titles.
    3. Extracts measurements (ml, g, pads, pcs) into a strict isolated key.
    """
    clean_titles = []
    extracted_sizes = []
    sanitized_brands = []

    for _, row in df.iterrows():
        raw_title = str(row.get('title', ''))
        raw_vendor = str(row.get('vendor', 'Unknown'))

        clean_brand_prefix = re.sub(r'[^A-ZA-z0-9]', '', raw_vendor).upper()[:3]
        if not clean_brand_prefix:
            clean_brand_prefix = "UNK"
        sanitized_brands.append(clean_brand_prefix)

        text = re.sub(r'[^\x00-\x7F]+', ' ', raw_title).lower()
        size_pattern = r'(\d+(?:\.\d+)?)\s*(ml|l|g|mg|ea|pads|sheets|capsules|pcs)\b'
        size_match = re.search(size_pattern, text)
        
        size_key = "NO_SIZE"
        if size_match:
            size_key = f"{size_match.group(1)}{size_match.group(2)}"
            text = text.replace(size_match.group(0), "")

        vendor_clean = re.sub(r'[^a-z0-9]', '', raw_vendor.lower())
        text = text.replace(raw_vendor.lower(), "").replace(vendor_clean, "")
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        clean_base = " ".join(text.split())

        clean_titles.append(clean_base)
        extracted_sizes.append(size_key)

    df['clean_base_title'] = clean_titles
    df['extracted_size'] = extracted_sizes
    df['brand_prefix'] = sanitized_brands
    df['strict_match_key'] = df['clean_base_title'] + " | " + df['extracted_size']
    return df