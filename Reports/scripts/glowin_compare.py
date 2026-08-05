import sqlite3
import pandas as pd
import numpy as np
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta
from rapidfuzz import fuzz

#RENAME ON REFACTOR

# =====================================================================
# LOGGING & DYNAMIC PATH RESOLUTION
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("GlowinCompare")

SCRIPT_DIR = Path(__file__).resolve().parent

def find_pipeline_root(start_dir: Path) -> Path:
    """Walks up parent directories to locate the pipeline root."""
    for parent in [start_dir] + list(start_dir.parents):
        if (parent / "orchestrate.py").exists() or (parent / "Data" / "databases").exists():
            return parent
    return start_dir.parents[1] if len(start_dir.parents) >= 2 else start_dir

PIPELINE_ROOT = find_pipeline_root(SCRIPT_DIR)

# Directories & Database Paths
DB_DIR = PIPELINE_ROOT / "Data" / "databases"
BBK_DB = DB_DIR / "beautybykat_inventory.db" # Remove on refactor: (keep site-specific URL here)
COMP_DB = DB_DIR / "master_competitor.db"
GLOWIN_CSV_FALLBACK = PIPELINE_ROOT / "Data" / "glowinbh_stock.csv" # Remove on refactor: (keep site-specific URL here)

OUTPUT_DIR = PIPELINE_ROOT / "Reports" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now()
OUTPUT_FILE = OUTPUT_DIR / f"Glowin_Comparison_Report_{TODAY.strftime('%Y-%m-%d')}.xlsx"

# Time Windows for Sales Analysis
ANALYSIS_DAYS = 30
START_DATE = (TODAY - timedelta(days=ANALYSIS_DAYS)).strftime('%Y-%m-%d')

# Brand Whitelist
GLOWIN_BRANDS = [
    "ABIB","AESTURA","ANUA","APLB","AXIS-Y","BANILA CO","BEAUTY OF JOSEON","BENTON",
    "BIODANCE","CELIMAX","COSRX","CRAZY HAIR","DERMA: B","DR.G","DR.ALTHEA","DR.CEURACLE",
    "DR.JART+","EQQUALBERRY","ETUDE","FRUDIA","GOODAL","HARUHARU WONDER","HEIMISH",
    "I'M FROM","ILLIYOON","INNISFREE","ISNTREE","IUNIK","JANEKE","KAHI","KAINE","KSECRET",
    "LANEIGE","MARY&MAY","MEDICUBE","MIELLE","MISSHA","MIXSOON","NUMBUZIN","PANOXYL",
    "PURITO","PYUNKANG YUL","ROUND LAB","ROM&ND","REAL BARRIER","SHEAMOISTURE","SHEGLAM",
    "SKIN1004","SKINFOOD","SOME BY MI","THEFACESHOP","TIRTIR","TOCOBO","TORRIDEN","VT COSMETICS"
]

# =====================================================================
# DATA CLEANING & MATCHING HELPERS
# =====================================================================
def clean_brand(x):
    x = str(x).upper().strip()
    fixes = {
        "DR ALTHEA": "DR.ALTHEA",
        "PURITO SEOUL": "PURITO",
        "IUNIK": "IUNIK",
    }
    return fixes.get(x, x)

def split_glowin_brand(title):
    title = str(title).strip()
    upper = title.upper()
    for brand in sorted(GLOWIN_BRANDS, key=len, reverse=True):
        if upper.startswith(brand.upper()):
            product = title[len(brand):].strip(" -:|")
            return clean_brand(brand), product
    return "UNKNOWN", title

def clean_product(x):
    x = str(x).lower()
    x = re.sub(r"[^a-z0-9\s\+\-\.]", " ", x)
    return re.sub(r"\s+", " ", x).strip()

def extract_volume(text):
    text = str(text).lower()
    match = re.search(r"(\d+)\s*(ml|g|gram|grams|pcs|sheets|sheet|ea|oz)", text)
    if not match:
        return None
    unit = match.group(2)
    unit = "g" if unit in ["gram", "grams"] else unit
    return match.group(1) + unit

def get_table_columns(conn, table_name):
    """Utility to safely check column existence in SQLite tables."""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]

# =====================================================================
# DATA FETCHING MODULES
# =====================================================================
def load_bbk_data(conn):
    """Fetches BBK pricing, stock, and historical sales using actual BBK schema."""
    variant_cols = set(get_table_columns(conn, "product_variants"))
    price_expr = "v.price" if "price" in variant_cols else "0.0"
    compare_expr = "v.compare_at_price" if "compare_at_price" in variant_cols else "0.0"

    query = f"""
        SELECT 
            p.upk_id,
            b.clean_name AS Brand, 
            p.product_title AS Product_Title, 
            COALESCE(sales.avg_price, NULLIF({price_expr}, 0.0), 0.0) AS BBK_Price,
            COALESCE({compare_expr}, 0.0) AS BBK_Compare_Price,
            COALESCE(stock.current_stock, 0) AS BBK_Stock,
            COALESCE(sales.units_sold, 0) AS BBK_Units_Sold,
            COALESCE(sales.revenue, 0.0) AS BBK_Revenue
        FROM products p
        LEFT JOIN brands b ON p.brand_id = b.brand_id
        LEFT JOIN product_variants v ON p.upk_id = v.upk_id
        LEFT JOIN (
            SELECT 
                v_sub.upk_id,
                SUM(s.inventory_qty) AS current_stock
            FROM inventory_snapshots s
            JOIN product_variants v_sub ON s.variant_id = v_sub.variant_id
            WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots)
            GROUP BY v_sub.upk_id
        ) stock ON p.upk_id = stock.upk_id
        LEFT JOIN (
            SELECT 
                p_sub.upk_id,
                SUM(oli.quantity) AS units_sold,
                ROUND(SUM(oli.quantity * oli.price), 2) AS revenue,
                ROUND(AVG(oli.price), 3) AS avg_price
            FROM order_line_items oli
            JOIN orders o ON oli.order_name = o.order_name
            JOIN products p_sub ON oli.raw_lineitem_name LIKE (p_sub.product_title || '%')
            WHERE o.created_at >= ?
            GROUP BY p_sub.upk_id
        ) sales ON p.upk_id = sales.upk_id
        GROUP BY p.upk_id, b.clean_name, p.product_title
    """
    return pd.read_sql_query(query, conn, params=(f"{START_DATE} 00:00:00",))


def load_glowin_data():
    """Fetches Glowin dataset explicitly from master_competitor.db snapshots."""
    if not COMP_DB.exists():
        logger.warning(f"master_competitor.db not found at {COMP_DB}. Checking fallback CSV.")
        if GLOWIN_CSV_FALLBACK.exists():
            glowin = pd.read_csv(GLOWIN_CSV_FALLBACK)
            glowin[["Brand", "Glowin_Product"]] = glowin["title"].apply(lambda x: pd.Series(split_glowin_brand(x)))
            glowin["Glowin_Price"] = pd.to_numeric(glowin["price"], errors='coerce').fillna(0.0)
            glowin["Glowin_Original_Price"] = pd.to_numeric(glowin.get("original_price", glowin["price"]), errors='coerce').fillna(0.0)
            glowin["Glowin_Stock"] = pd.to_numeric(glowin["stock_quantity"], errors='coerce').fillna(0)
            glowin["Glowin_Units_Sold"] = 0
            glowin["Glowin_Revenue"] = 0.0
            return glowin
        return pd.DataFrame()

    logger.info(f"Connecting to Competitor DB: {COMP_DB.name}")
    conn = sqlite3.connect(COMP_DB)
    
    # Inspect column names in dim_unified_products safely
    prod_cols = set(get_table_columns(conn, "dim_unified_products"))
    
    # Modified block to correctly target 'consensus_title'
    if "consensus_title" in prod_cols:
        prod_title_col = "p.consensus_title"
    elif "product_title" in prod_cols:
        prod_title_col = "p.product_title"
    elif "product_name" in prod_cols:
        prod_title_col = "p.product_name"
    else:
        prod_title_col = "p.clean_title"

    query = f"""
        SELECT 
            s.link_id,
            b.canonical_name AS Brand,
            {prod_title_col} AS Glowin_Product,
            s.stock_quantity,
            COALESCE(NULLIF(s.price_bhd, 0), NULLIF(v.price_bhd, 0), 0.0) AS Glowin_Price,
            s.snapshot_date
        FROM fact_daily_stock_snapshots s
        JOIN fact_store_variants v ON s.link_id = v.link_id
        JOIN dim_unified_products p ON s.upk_id = p.upk_id
        JOIN dim_brands b ON p.brand_id = b.brand_id
        WHERE UPPER(s.origin_company) LIKE '%GLOWIN%' AND DATE(s.snapshot_date) >= ?
        ORDER BY s.link_id, s.snapshot_date ASC
    """
    df_snap = pd.read_sql_query(query, conn, params=(START_DATE,))
    conn.close()

    if df_snap.empty:
        logger.warning("No Glowin snapshot records found in master_competitor.db!")
        return pd.DataFrame()

    df_snap['stock_quantity'] = pd.to_numeric(df_snap['stock_quantity'], errors='coerce').fillna(0)
    df_snap['Glowin_Price'] = pd.to_numeric(df_snap['Glowin_Price'], errors='coerce').fillna(0.0)
    
    # Calculate daily stock depletion for sales estimation
    df_snap['prev_stock'] = df_snap.groupby('link_id')['stock_quantity'].shift(1)
    df_snap['units_sold'] = (df_snap['prev_stock'] - df_snap['stock_quantity']).apply(lambda x: int(x) if pd.notnull(x) and x > 0 else 0)
    df_snap['revenue'] = df_snap['units_sold'] * df_snap['Glowin_Price']

    # Aggregate per competitor listing link
    glowin_df = df_snap.groupby(['link_id', 'Brand', 'Glowin_Product']).agg(
        Glowin_Price=('Glowin_Price', 'last'),
        Glowin_Stock=('stock_quantity', 'last'),
        Glowin_Units_Sold=('units_sold', 'sum'),
        Glowin_Revenue=('revenue', 'sum')
    ).reset_index()

    glowin_df['Glowin_Original_Price'] = glowin_df['Glowin_Price']
    return glowin_df

# =====================================================================
# CORE MATCHING & COMPARISON PIPELINE
# =====================================================================
def run_comparison(bbk_df, glowin_df):
    """Executes fuzzy matching, price delta calculations, and sales analysis."""
    bbk_df["Brand_Clean"] = bbk_df["Brand"].apply(clean_brand)
    bbk_df["Clean_Product"] = bbk_df["Product_Title"].apply(clean_product)
    bbk_df["Volume"] = bbk_df["Product_Title"].apply(extract_volume)

    glowin_df["Brand_Clean"] = glowin_df["Brand"].apply(clean_brand)
    glowin_df["Clean_Product"] = glowin_df["Glowin_Product"].apply(clean_product)
    glowin_df["Volume"] = glowin_df["Glowin_Product"].apply(extract_volume)

    matches = []
    for _, b in bbk_df.iterrows():
        candidates = glowin_df[glowin_df["Brand_Clean"] == b["Brand_Clean"]].copy()
        if candidates.empty:
            continue

        best = None
        for _, g in candidates.iterrows():
            if b["Volume"] and g["Volume"] and b["Volume"] != g["Volume"]:
                continue

            score = fuzz.token_set_ratio(b["Clean_Product"], g["Clean_Product"])
            if best is None or score > best["Similarity"]:
                best = {
                    "Brand": b["Brand"],
                    "BBK Product": b["Product_Title"],
                    "Glowin Product": g["Glowin_Product"],
                    "Similarity": score,
                    "BBK Price": b["BBK_Price"],
                    "Glowin Price": g["Glowin_Price"],
                    "BBK Compare Price": b["BBK_Compare_Price"],
                    "Glowin Original Price": g["Glowin_Original_Price"],
                    "BBK Stock": b["BBK_Stock"],
                    "Glowin Stock": g["Glowin_Stock"],
                    "BBK Units Sold": b["BBK_Units_Sold"],
                    "Glowin Units Sold (Est)": g["Glowin_Units_Sold"],
                    "BBK Revenue": b["BBK_Revenue"],
                    "Glowin Revenue (Est)": g["Glowin_Revenue"],
                }

        if best and best["Similarity"] >= 75:
            matches.append(best)

    df = pd.DataFrame(matches)
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Pricing calculations
    df["BBK Price"] = pd.to_numeric(df["BBK Price"], errors='coerce').fillna(0.0)
    df["Glowin Price"] = pd.to_numeric(df["Glowin Price"], errors='coerce').fillna(0.0)
    
    df["Price Difference"] = (df["BBK Price"] - df["Glowin Price"]).round(3)
    df["Price Diff %"] = np.where(
        df["Glowin Price"] > 0, 
        ((df["BBK Price"] - df["Glowin Price"]) / df["Glowin Price"] * 100).round(2), 
        0.0
    )
    
    df["Who Is Cheaper"] = df["Price Difference"].apply(
        lambda x: "BeautyByKat cheaper" if x < 0 else ("Glowin cheaper" if x > 0 else "Same price")
    )

    def price_alert(row):
        if row["Glowin Price"] <= 0:
            return "Normal"
        diff_ratio = row["Price Difference"] / row["Glowin Price"]
        if diff_ratio > 0.10:
            return "⚠️ HIGH ALERT: BBK Overpriced"
        elif diff_ratio < -0.10:
            return "🔥 HIGH MARGIN: BBK Undercutting"
        return "Normal"

    df["Price Alert"] = df.apply(price_alert, axis=1)

    # Velocity / Sales comparison
    df["Sales Leader"] = np.where(
        df["BBK Units Sold"] > df["Glowin Units Sold (Est)"], 
        "BBK Lead", 
        np.where(df["BBK Units Sold"] < df["Glowin Units Sold (Est)"], "Glowin Lead", "Tied / Low Volume")
    )

    df_price_analysis = df.sort_values(by="Price Difference", ascending=False)
    df_sales_analysis = df.sort_values(by=["BBK Units Sold", "Glowin Units Sold (Est)"], ascending=[False, False])

    return df_price_analysis, df_sales_analysis

# =====================================================================
# MAIN EXECUTOR & REPORT GENERATOR
# =====================================================================
def main():
    if not BBK_DB.exists():
        logger.error(f"BBK Database missing at {BBK_DB}")
        return

    logger.info(f"Connecting to BBK DB: {BBK_DB.name}")
    bbk_conn = sqlite3.connect(BBK_DB)
    bbk_df = load_bbk_data(bbk_conn)
    bbk_conn.close()

    logger.info("Loading Glowin dataset from master_competitor.db snapshots...")
    glowin_df = load_glowin_data()
    if glowin_df.empty:
        logger.error("Aborting comparison: Glowin data empty or unreadable.")
        return

    logger.info("Executing fuzzy matching & market analysis...")
    df_price, df_sales = run_comparison(bbk_df, glowin_df)

    if df_price.empty:
        logger.warning("No catalog matches found between BBK and Glowin.")
        return

    logger.info(f"Exporting market analysis to {OUTPUT_FILE}...")
    with pd.ExcelWriter(OUTPUT_FILE, engine='xlsxwriter') as writer:
        df_price.to_excel(writer, index=False, sheet_name="Price Comparison Analysis")
        df_sales.to_excel(writer, index=False, sheet_name="Sales & Velocity Comparison")

        for sheet in writer.sheets:
            ws = writer.sheets[sheet]
            ws.set_column('A:A', 15)
            ws.set_column('B:C', 35)
            ws.set_column('D:P', 16)

    logger.info(f"[SUCCESS] Report successfully generated at: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()