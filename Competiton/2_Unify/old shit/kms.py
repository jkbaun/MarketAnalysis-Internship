import sqlite3
import pandas as pd
from datetime import date
from rapidfuzz import fuzz, process

DB_FILE = "beauty_market_data.db"

# ==========================================
# 1. DATABASE INITIALIZATION (SCHEMA)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Persistent Master Registry: Stores known parent/child UPK mappings
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS master_upk_registry (
        store_name TEXT NOT NULL,
        product_handle TEXT NOT NULL,
        canonical_brand TEXT NOT NULL,
        parent_upk TEXT NOT NULL,
        child_upk TEXT NOT NULL,
        base_title TEXT NOT NULL,
        variant_spec TEXT,
        is_manual_override INTEGER DEFAULT 0,
        updated_at DATE DEFAULT CURRENT_DATE,
        PRIMARY KEY (store_name, product_handle)
    )
    """)
    
    # Daily Ledger: Time-series log of store prices and stock levels
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_scraped_inventory (
        scrape_date DATE NOT NULL,
        store_name TEXT NOT NULL,
        product_handle TEXT NOT NULL,
        parent_upk TEXT,
        child_upk TEXT,
        store_title TEXT NOT NULL,
        canonical_brand TEXT NOT NULL,
        price_bhd REAL NOT NULL,
        stock_status TEXT NOT NULL,
        PRIMARY KEY (scrape_date, store_name, product_handle)
    )
    """)
    
    conn.commit()
    conn.close()
    print("✓ Database initialized successfully.")

# ==========================================
# 2. STATEFUL MATCHING & INGESTION PIPELINE
# ==========================================
def run_stateful_ingestion(raw_scraped_df, scrape_date=None):
    if scrape_date is None:
        scrape_date = str(date.today())
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Fetch existing Master Registry for Tier 0 Lookups
    registry_df = pd.read_sql("SELECT * FROM master_upk_registry", conn)
    
    # Get highest current UPK number for new dynamic generation
    if not registry_df.empty:
        max_upk_num = registry_df['parent_upk'].str.extract(r'UPK-(\d+)')[0].astype(float).max()
        upk_counter = int(max_upk_num) + 1 if pd.notnull(max_upk_num) else 1000
    else:
        upk_counter = 1000

    processed_rows = []
    
    for _, row in raw_scraped_df.iterrows():
        store = row['store_name']
        handle = row['product_handle']
        brand = row['canonical_brand']
        title = row['store_title']
        spec = row.get('variant_spec', 'STD')
        
        # Tier 0: Direct Lookup in Master Registry
        existing = registry_df[(registry_df['store_name'] == store) & (registry_df['product_handle'] == handle)]
        
        if not existing.empty:
            # Match found in persistent state! Lock down UPK
            parent_upk = existing.iloc[0]['parent_upk']
            child_upk = existing.iloc[0]['child_upk']
        else:
            # Tier 1: Fuzzy Match within same canonical brand in registry
            brand_matches = registry_df[registry_df['canonical_brand'] == brand]
            matched_upk = None
            
            if not brand_matches.empty:
                candidates = brand_matches['base_title'].tolist()
                best_match = process.extractOne(title, candidates, scorer=fuzz.token_sort_ratio)
                
                if best_match and best_match[1] >= 88:  # 88% Confidence Threshold
                    match_row = brand_matches[brand_matches['base_title'] == best_match[0]].iloc[0]
                    matched_upk = match_row['parent_upk']
            
            if matched_upk:
                parent_upk = matched_upk
            else:
                # Tier 2: Create new UPK
                parent_upk = f"UPK-{upk_counter}"
                upk_counter += 1
            
            child_upk = f"{parent_upk}-V_{spec.upper().replace(' ', '')}"
            
            # Save new mapping back to Master Registry (Self-Correcting Persistent State)
            cursor.execute("""
            INSERT OR REPLACE INTO master_upk_registry 
            (store_name, product_handle, canonical_brand, parent_upk, child_upk, base_title, variant_spec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (store, handle, brand, parent_upk, child_upk, title, spec))
            
        # Append to daily batch
        processed_rows.append((scrape_date, store, handle, parent_upk, child_upk, title, brand, row['price_bhd'], row['stock_status']))
        
    # Write daily inventory batch
    cursor.executemany("""
    INSERT OR REPLACE INTO daily_scraped_inventory 
    (scrape_date, store_name, product_handle, parent_upk, child_upk, store_title, canonical_brand, price_bhd, stock_status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, processed_rows)
    
    conn.commit()
    conn.close()
    print(f"✓ Processed {len(processed_rows)} items for date: {scrape_date}")

# ==========================================
# 3. ANALYTICAL TABLE GENERATION (SQL VIEWS)
# ==========================================
def generate_analysis_tables():
    conn = sqlite3.connect(DB_FILE)
    
    # 1. Micro Performance Time-Series Query
    micro_sql = """
    SELECT 
        scrape_date,
        parent_upk,
        child_upk,
        canonical_brand,
        store_title,
        store_name,
        price_bhd,
        stock_status
    FROM daily_scraped_inventory
    ORDER BY scrape_date DESC, parent_upk, store_name;
    """
    micro_df = pd.read_sql(micro_sql, conn)
    
    # 2. Macro Market Overview Query (Cross-store catalog coverage)
    macro_sql = """
    SELECT 
        canonical_brand,
        COUNT(DISTINCT parent_upk) AS total_market_products,
        COUNT(DISTINCT CASE WHEN store_name = 'GlowIn' THEN parent_upk END) AS glowin_catalog_count,
        COUNT(DISTINCT CASE WHEN store_name = 'XBeauty' THEN parent_upk END) AS xbeauty_catalog_count,
        COUNT(DISTINCT CASE WHEN store_name = 'SokoStore' THEN parent_upk END) AS sokostore_catalog_count
    FROM daily_scraped_inventory
    GROUP BY canonical_brand
    HAVING total_market_products > 0
    ORDER BY total_market_products DESC;
    """
    macro_df = pd.read_sql(macro_sql, conn)
    conn.close()
    
    return micro_df, macro_df

# ==========================================
# 4. EXAMPLE RUNTIMETEST
# ==========================================
if __name__ == "__main__":
    init_db()
    
    # Mock Scraped Batch 1 (Day 1)
    day1_data = pd.DataFrame([
        {"store_name": "GlowIn", "product_handle": "torriden-dive-in-50ml", "canonical_brand": "Torriden", "store_title": "Torriden Dive-In Low Molecule Hyaluronic Acid Serum 50ml", "price_bhd": 6.500, "stock_status": "In Stock", "variant_spec": "50ml"},
        {"store_name": "XBeauty", "product_handle": "xb-torriden-serum", "canonical_brand": "Torriden", "store_title": "Torriden Dive In Hyaluronic Serum 50ml", "price_bhd": 5.900, "stock_status": "In Stock", "variant_spec": "50ml"}
    ])
    
    run_stateful_ingestion(day1_data, scrape_date="2026-07-22")
    
    # Mock Scraped Batch 2 (Day 2 - New Item + Repeat Item)
    day2_data = pd.DataFrame([
        # Existing item (Will hit Tier-0 instant match)
        {"store_name": "GlowIn", "product_handle": "torriden-dive-in-50ml", "canonical_brand": "Torriden", "store_title": "Torriden Dive-In Low Molecule Hyaluronic Acid Serum 50ml", "price_bhd": 6.200, "stock_status": "In Stock", "variant_spec": "50ml"},
        # New product (Will trigger fuzzy engine / dynamic assignment)
        {"store_name": "GlowIn", "product_handle": "anua-77-toner", "canonical_brand": "Anua", "store_title": "Anua Heartleaf 77% Soothing Toner 250ml", "price_bhd": 7.100, "stock_status": "In Stock", "variant_spec": "250ml"}
    ])
    
    run_stateful_ingestion(day2_data, scrape_date="2026-07-23")
    
    # Retrieve final analytical outputs
    micro_table, macro_table = generate_analysis_tables()
    
    print("\n--- MICRO PERFORMANCE TIMESERIES ---")
    print(micro_table.to_string(index=False))
    
    print("\n--- MACRO MARKET OVERVIEW ---")
    print(macro_table.to_string(index=False))