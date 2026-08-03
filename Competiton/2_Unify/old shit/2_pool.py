import os
import glob
import pandas as pd

# =====================================================================
# PATH CONFIGURATION
# =====================================================================
INPUT_DIR = "Data/scrapped_json"
OUTPUT_DIR = "Data/unified_database"
MAPPINGS_DIR = os.path.join(OUTPUT_DIR, "mappings")
FINAL_DIR = os.path.join(OUTPUT_DIR, "final_tables")

os.makedirs(MAPPINGS_DIR, exist_ok=True)
os.makedirs(FINAL_DIR, exist_ok=True)

def stage_1_clean_raw_files(products_df, stocks_df):
    """
    Standardizes raw data types, coerces stocks to integer (defaulting to 0),
    and coerces prices to float.
    """
    stocks_df['stock'] = pd.to_numeric(stocks_df['inventory_quantity'], errors='coerce').fillna(0).astype(int)
    stocks_df['price'] = pd.to_numeric(stocks_df['price'], errors='coerce')
    merged_df = pd.merge(stocks_df, products_df, on="product_id", how="left")
    return merged_df

def load_master_stocks_csv():
    files = glob.glob(os.path.join(INPUT_DIR, "master_stocks_*.csv"))
    all_dfs = []
    for f in files:
        company = os.path.basename(f).replace("master_stocks_", "").replace(".csv", "")
        df = pd.read_csv(f)
        df["origin_company"] = company
        all_dfs.append(df)
    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

def load_master_stocks_json():
    files = glob.glob(os.path.join(INPUT_DIR, "master_stock_*.json"))
    all_dfs = []
    for f in files:
        company = os.path.basename(f).replace("master_stock_", "").replace(".json", "")
        df = pd.read_json(f)
        df["origin_company"] = company
        
        if "variants" in df.columns:
            df["sku"] = df["variants"].apply(lambda v: v[0].get("sku", "") if isinstance(v, list) and len(v) > 0 else "")
            df["price"] = df["variants"].apply(lambda v: v[0].get("price", "") if isinstance(v, list) and len(v) > 0 else "")
        else:
            df["sku"] = ""
            
        all_dfs.append(df)
    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()