import re
import ast
import pandas as pd

AED_TO_BHD_RATE = 0.1028 

def normalize_currency(df):
    """
    Normalizes specific company prices (like Xbeauty in AED) to BHD.
    """
    if "origin_company" in df.columns and "price" in df.columns:
        xbeauty_mask = df["origin_company"].str.lower() == "xbeauty"
        df.loc[xbeauty_mask, "price"] = pd.to_numeric(df.loc[xbeauty_mask, "price"], errors='coerce') * AED_TO_BHD_RATE
    return df

def resolve_brands_and_tags(df):
    """
    Resolves market brand mappings and processes tag route exceptions.
    """
    trusted_mask = ~df["vendor"].str.lower().str.contains("soko", na=False)
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
    for idx, row in df.iterrows():
        raw_vendor = str(row.get("vendor", ""))
        raw_tags = str(row.get("tags", ""))
        
        if "soko" in raw_vendor.lower() or raw_vendor.strip() == "":
            matched_brand = "Unknown Brand"
            
            # Tag Extraction Route
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
            
            # Title Fallback Route
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