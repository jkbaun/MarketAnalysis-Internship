import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import re

# =====================================================================
# CONFIGURATION & DYNAMIC PATHS
# =====================================================================
# Dynamically locate the script's directory and root pipeline directory
SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_ROOT = SCRIPT_DIR.parents[1] if len(SCRIPT_DIR.parents) >= 2 else SCRIPT_DIR

# Define relative data and database paths
DB_DIR = PIPELINE_ROOT / "Data" / "databases"
BBK_DB = DB_DIR / "beautybykat_inventory.db"

# Define output directory and file path
OUTPUT_DIR = PIPELINE_ROOT / "Reports" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now()
OUTPUT_FILE = OUTPUT_DIR / f"Internal_Inventory_Report_{TODAY.strftime('%Y-%m-%d')}.xlsx"

# Time Windows
START_DATE = (TODAY - timedelta(days=30)).strftime('%Y-%m-%d')
END_DATE = TODAY.strftime('%Y-%m-%d')


# =====================================================================
# REPORT DATA QUERIES
# =====================================================================
def get_reconciliation(conn):
    start_datetime = f"{START_DATE} 00:00:00"
    end_datetime = f"{END_DATE} 23:59:59"
    
    df_products = pd.read_sql_query("SELECT pv.variant_id, p.product_title, pv.variant_title, pv.sku FROM product_variants pv JOIN products p ON pv.upk_id = p.upk_id", conn)
    df_start = pd.read_sql_query("SELECT variant_id, inventory_qty AS start_qty FROM inventory_snapshots main WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots sub WHERE sub.variant_id = main.variant_id AND snapshot_date <= ?)", conn, params=(START_DATE,))
    
    df_sold = pd.read_sql_query("""
        SELECT pv.variant_id, SUM(oli.quantity) AS sold_qty
        FROM order_line_items oli
        JOIN orders o ON oli.order_name = o.order_name
        JOIN products p ON oli.raw_lineitem_name LIKE (p.product_title || '%')
        JOIN product_variants pv ON p.upk_id = pv.upk_id
        WHERE (oli.raw_lineitem_name = p.product_title || ' - ' || pv.variant_title OR oli.raw_lineitem_name = p.product_title)
        AND o.created_at >= ? AND o.created_at <= ?
        GROUP BY pv.variant_id
    """, conn, params=(start_datetime, end_datetime))
    
    df_end = pd.read_sql_query("SELECT variant_id, inventory_qty AS actual_end_qty FROM inventory_snapshots main WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM inventory_snapshots sub WHERE sub.variant_id = main.variant_id AND snapshot_date <= ?)", conn, params=(END_DATE,))

    for df in [df_products, df_start, df_sold, df_end]:
        df['variant_id'] = pd.to_numeric(df['variant_id'], errors='coerce').astype('Int64')

    report = df_products.merge(df_start, on='variant_id', how='left').merge(df_sold, on='variant_id', how='left').merge(df_end, on='variant_id', how='left')
    report[['start_qty', 'sold_qty', 'actual_end_qty']] = report[['start_qty', 'sold_qty', 'actual_end_qty']].fillna(0)
    
    report['expected_qty'] = report['start_qty'] - report['sold_qty']
    report['variance'] = report['actual_end_qty'] - report['expected_qty']
    report = report[(report['start_qty'] != 0) | (report['sold_qty'] != 0) | (report['actual_end_qty'] != 0)]
    
    return report.sort_values(by=['product_title', 'variant_title'])[['sku', 'product_title', 'variant_title', 'start_qty', 'sold_qty', 'expected_qty', 'actual_end_qty', 'variance']]

def get_brand_performance(conn):
    query = """
        SELECT b.clean_name AS Brand, COUNT(DISTINCT o.order_name) AS total_orders, SUM(oli.quantity) AS total_units_sold, ROUND(SUM(oli.quantity * oli.price), 2) AS total_revenue
        FROM order_line_items oli
        JOIN orders o ON oli.order_name = o.order_name
        JOIN products p ON oli.raw_lineitem_name LIKE (p.product_title || '%')
        JOIN brands b ON p.brand_id = b.brand_id
        WHERE o.created_at >= ? AND o.created_at <= ?
        GROUP BY b.clean_name ORDER BY total_revenue DESC
    """
    return pd.read_sql_query(query, conn, params=(f"{START_DATE} 00:00:00", f"{END_DATE} 23:59:59"))

def get_detailed_movement(conn):
    query = """
        SELECT b.clean_name AS Brand, p.product_title AS Product, SUM(oli.quantity) AS units_sold, ROUND(SUM(oli.quantity * oli.price), 2) AS total_revenue
        FROM order_line_items oli
        JOIN orders o ON oli.order_name = o.order_name
        JOIN products p ON oli.raw_lineitem_name LIKE (p.product_title || '%')
        JOIN brands b ON p.brand_id = b.brand_id
        WHERE o.created_at >= ? AND o.created_at <= ?
        GROUP BY b.clean_name, p.product_title
        ORDER BY b.clean_name ASC, total_revenue DESC
    """
    return pd.read_sql_query(query, conn, params=(f"{START_DATE} 00:00:00", f"{END_DATE} 23:59:59"))


# =====================================================================
# MAIN EXECUTOR & EXCEL EXPORT
# =====================================================================
def main():
    print(f"[+] Connecting to {BBK_DB}...")
    if not BBK_DB.exists():
        print(f"[-] Database not found at {BBK_DB}!")
        return
        
    conn = sqlite3.connect(BBK_DB)
    df_recon = get_reconciliation(conn)
    df_brand = get_brand_performance(conn)
    df_detailed = get_detailed_movement(conn)
    conn.close()

    print(f"[+] Writing Internal Inventory Report to {OUTPUT_FILE}...")
    with pd.ExcelWriter(OUTPUT_FILE, engine='xlsxwriter') as writer:
        df_recon.to_excel(writer, index=False, sheet_name="Reconciliation")
        df_brand.to_excel(writer, index=False, sheet_name="Brand Performance")
        
        unique_brands = df_detailed['Brand'].dropna().unique()
        for brand in unique_brands:
            brand_df = df_detailed[df_detailed['Brand'] == brand].drop(columns=['Brand'])
            safe_sheet = re.sub(r'[\\/*?:\[\]]', '', str(brand))[:31]
            brand_df.to_excel(writer, index=False, sheet_name=safe_sheet)
            worksheet = writer.sheets[safe_sheet]
            worksheet.set_column('A:A', 50)
            worksheet.set_column('B:C', 15)
            
        recon_ws = writer.sheets["Reconciliation"]
        recon_ws.set_column('A:C', 30)
        brand_ws = writer.sheets["Brand Performance"]
        brand_ws.set_column('A:A', 25)

    print(f"[SUCCESS] Internal Inventory Report generated at {OUTPUT_FILE}")

if __name__ == "__main__":
    main()