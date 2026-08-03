import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

# =====================================================================
# CONFIGURATION & DYNAMIC PATHS
# =====================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_ROOT = SCRIPT_DIR.parents[1] if len(SCRIPT_DIR.parents) >= 2 else SCRIPT_DIR

DB_DIR = PIPELINE_ROOT / "Data" / "databases"
BBK_DB = DB_DIR / "beautybykat_inventory.db"
COMP_DB = DB_DIR / "master_competitor.db"

OUTPUT_DIR = PIPELINE_ROOT / "Reports" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now()
OUTPUT_FILE = OUTPUT_DIR / f"Executive_Master_Report_{TODAY.strftime('%Y-%m-%d')}.xlsx"

# Time Windows
ANALYSIS_DAYS_BBK = 30
ANALYSIS_DAYS_COMP = 7

START_DATE_BBK = (TODAY - timedelta(days=ANALYSIS_DAYS_BBK)).strftime('%Y-%m-%d')
END_DATE_BBK = TODAY.strftime('%Y-%m-%d')


# =====================================================================
# 1. BEAUTYBYKAT FINANCIALS & NON-MOVERS WITH STOCK SNAPSHOTS
# =====================================================================
def get_bbk_data(conn):
    """
    Queries BBK DB using products, product_variants, inventory_snapshots, 
    orders, and order_line_items.
    """
    start_datetime = f"{START_DATE_BBK} 00:00:00"
    end_datetime = f"{END_DATE_BBK} 23:59:59"
    
    # 1. Executive Summary Totals
    query_totals = """
        SELECT 
            COUNT(DISTINCT o.order_name) AS Total_Orders, 
            COALESCE(SUM(oli.quantity), 0) AS Total_Units_Sold, 
            COALESCE(ROUND(SUM(oli.quantity * oli.price), 2), 0) AS Total_Revenue 
        FROM order_line_items oli 
        JOIN orders o ON oli.order_name = o.order_name 
        WHERE o.created_at >= ? AND o.created_at <= ?
    """
    df_totals = pd.read_sql_query(query_totals, conn, params=(start_datetime, end_datetime))
    
    # 2. Sales Performance by Product
    query_sales = """
        SELECT 
            p.upk_id,
            b.clean_name AS Brand, 
            p.product_title AS Product, 
            COUNT(DISTINCT valid_orders.order_name) AS total_orders, 
            COALESCE(SUM(valid_orders.quantity), 0) AS total_units_sold, 
            COALESCE(ROUND(SUM(valid_orders.quantity * valid_orders.price), 2), 0) AS total_revenue
        FROM products p 
        LEFT JOIN brands b ON p.brand_id = b.brand_id
        LEFT JOIN product_variants v ON p.upk_id = v.upk_id
        LEFT JOIN (
            SELECT oli.variant_id, oli.raw_lineitem_name, oli.order_name, oli.quantity, oli.price
            FROM order_line_items oli 
            JOIN orders o ON oli.order_name = o.order_name
            WHERE o.created_at >= ? AND o.created_at <= ?
        ) valid_orders ON (valid_orders.variant_id = v.variant_id OR valid_orders.raw_lineitem_name LIKE (p.product_title || '%'))
        GROUP BY p.upk_id, b.clean_name, p.product_title
    """
    df_products = pd.read_sql_query(query_sales, conn, params=(start_datetime, end_datetime))
    
    # 3. Stock Level History from inventory_snapshots
    query_stock_history = """
        SELECT 
            p.upk_id,
            s.snapshot_date,
            SUM(s.inventory_qty) AS aggregate_stock
        FROM products p
        JOIN product_variants v ON p.upk_id = v.upk_id
        JOIN inventory_snapshots s ON v.variant_id = s.variant_id
        WHERE DATE(s.snapshot_date) >= ? AND DATE(s.snapshot_date) <= ?
        GROUP BY p.upk_id, s.snapshot_date
        ORDER BY s.snapshot_date ASC
    """
    df_stock = pd.read_sql_query(query_stock_history, conn, params=(START_DATE_BBK, END_DATE_BBK))
    
    # Process Oldest vs Latest stock levels per product
    if not df_stock.empty:
        stock_summary = df_stock.groupby('upk_id').agg(
            oldest_stock=('aggregate_stock', 'first'),
            latest_stock=('aggregate_stock', 'last')
        ).reset_index()
        stock_summary['stock_change'] = stock_summary['latest_stock'] - stock_summary['oldest_stock']
        
        # Merge stock history with product sales data
        df_full_catalog = pd.merge(df_products, stock_summary, on='upk_id', how='left')
    else:
        df_full_catalog = df_products.copy()
        df_full_catalog['oldest_stock'] = 0
        df_full_catalog['latest_stock'] = 0
        df_full_catalog['stock_change'] = 0

    # Fill NaN values for missing snapshot entries
    df_full_catalog[['oldest_stock', 'latest_stock', 'stock_change']] = df_full_catalog[['oldest_stock', 'latest_stock', 'stock_change']].fillna(0).astype(int)

    # Clean columns for final reports
    display_cols = ['Brand', 'Product', 'oldest_stock', 'latest_stock', 'stock_change', 'total_units_sold', 'total_revenue']
    
    # Top Earnings & Sellers
    df_earn = df_full_catalog.sort_values(by='total_revenue', ascending=False).drop(columns=['upk_id'])
    df_sell = df_full_catalog.sort_values(by='total_units_sold', ascending=False).drop(columns=['upk_id'])
    
    # Non-Movers (0 Units Sold during period)
    df_nomove = df_full_catalog[df_full_catalog['total_units_sold'] == 0][display_cols]
    df_nomove = df_nomove.sort_values(by=['latest_stock', 'oldest_stock'], ascending=[False, False])
    
    return df_totals, df_earn, df_sell, df_nomove


# =====================================================================
# 2. COMPETITOR MARKET LEADERS (WITH REVENUE FIX)
# =====================================================================
def get_competitor_leaders(conn):
    """
    Calculates competitor sales and revenue over a rolling snapshot window,
    fixing zero-revenue bugs by falling back to store variant prices.
    """
    cutoff = (TODAY - timedelta(days=ANALYSIS_DAYS_COMP)).strftime('%Y-%m-%d')
    
    # Fallback SQL logic for missing/zero prices in snapshot table
    query = """
        SELECT 
            s.link_id, 
            s.origin_company, 
            s.stock_quantity, 
            COALESCE(NULLIF(s.price_bhd, 0), NULLIF(v.price_bhd, 0), 0.0) AS price_bhd, 
            b.canonical_name AS brand_name, 
            s.snapshot_date, 
            s.upk_id
        FROM fact_daily_stock_snapshots s 
        JOIN fact_store_variants v ON s.link_id = v.link_id
        JOIN dim_unified_products p ON s.upk_id = p.upk_id 
        JOIN dim_brands b ON p.brand_id = b.brand_id
        WHERE DATE(s.snapshot_date) >= ? 
        ORDER BY s.link_id, s.snapshot_date ASC
    """
    df = pd.read_sql_query(query, conn, params=(cutoff,))
    
    if df.empty:
        return pd.DataFrame()
    
    # Explicit numerical conversions
    df['price_bhd'] = pd.to_numeric(df['price_bhd'], errors='coerce').fillna(0.0)
    df['stock_quantity'] = pd.to_numeric(df['stock_quantity'], errors='coerce').fillna(0)
    df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
    
    # Calculate daily stock depletion (units sold)
    df['prev_stock'] = df.groupby('link_id')['stock_quantity'].shift(1)
    df['units_sold'] = (df['prev_stock'] - df['stock_quantity']).apply(lambda x: int(x) if pd.notnull(x) and x > 0 else 0)
    df['revenue_bhd'] = df['units_sold'] * df['price_bhd']
    
    # Aggregate by Brand and Company
    brand_comp = df.groupby(['brand_name', 'origin_company']).agg(
        company_units_sold=('units_sold', 'sum'),
        company_revenue_bhd=('revenue_bhd', 'sum')
    ).reset_index()
    
    # Top company per brand
    top_company = brand_comp.sort_values(
        by=['brand_name', 'company_units_sold', 'company_revenue_bhd'], 
        ascending=[True, False, False]
    ).groupby('brand_name').first().reset_index()
    
    # Grand totals per brand
    brand_totals = df.groupby('brand_name').agg(
        total_units_sold=('units_sold', 'sum'),
        total_revenue_bhd=('revenue_bhd', 'sum')
    ).reset_index()
    
    report = pd.merge(brand_totals, top_company, on='brand_name', how='left').sort_values(by='total_units_sold', ascending=False)
    report.rename(columns={'origin_company': 'Top Selling Company'}, inplace=True)
    report['total_revenue_bhd'] = report['total_revenue_bhd'].round(3)
    report['company_revenue_bhd'] = report['company_revenue_bhd'].round(3)
    
    return report


# =====================================================================
# 3. MAIN ORCHESTRATOR & EXCEL GENERATOR
# =====================================================================
def main():
    if not BBK_DB.exists():
        print(f"[-] BBK DB not found at {BBK_DB}!")
        return
        
    print(f"[+] Connecting to BBK Database: {BBK_DB.name}")
    bbk_conn = sqlite3.connect(BBK_DB)
    df_totals, df_earn, df_sell, df_nomove = get_bbk_data(bbk_conn)
    bbk_conn.close()
    
    df_market = pd.DataFrame()
    if COMP_DB.exists():
        print(f"[+] Connecting to Competitor DB: {COMP_DB.name}")
        comp_conn = sqlite3.connect(COMP_DB)
        df_market = get_competitor_leaders(comp_conn)
        comp_conn.close()

    print(f"[+] Exporting Executive Master Report to {OUTPUT_FILE}...")
    with pd.ExcelWriter(OUTPUT_FILE, engine='xlsxwriter') as writer:
        df_totals.to_excel(writer, index=False, sheet_name="Executive Summary")
        df_earn.to_excel(writer, index=False, sheet_name="Top Earnings (BBK)")
        df_sell.to_excel(writer, index=False, sheet_name="Top Sellers (BBK)")
        df_nomove.to_excel(writer, index=False, sheet_name="Non-Movers (BBK)")
        
        if not df_market.empty:
            df_market.to_excel(writer, index=False, sheet_name="Market Leaders (Comp)")
            
        # Format Worksheets
        for sheet in writer.sheets:
            ws = writer.sheets[sheet]
            ws.set_column('A:B', 32)
            ws.set_column('C:H', 18)
            
    print(f"[SUCCESS] Executive Master Report generated successfully at {OUTPUT_FILE}")

if __name__ == "__main__":
    main()