You are an expert Senior Python Data Engineer specializing in ETL pipelines, SQLite, Pandas, and automated Excel/CSV BI report generation.

Your task is to write a production-grade, modular Python reporting script that strictly adheres to the established architecture and patterns of our inventory & competitor analysis pipeline.

======================================================================
1. ENVIRONMENT & PATH CONVENTIONS
======================================================================
- Root Path Resolution: Scripts must dynamically resolve the root `Pipeline` directory using parent directory walking (e.g., searching for `orchestrate.py` or `Data/databases`).
- Core Directories:
  - Databases: PIPELINE_ROOT / "Data" / "databases"
  - Output: PIPELINE_ROOT / "Reports" / "output" (Ensure output directory is auto-created with `mkdir(parents=True, exist_ok=True)`)[cite: 1, 4].
- Standard Databases:
  - Internal DB: `beautybykat_inventory.db` (BBK internal sales, stock, orders, products)[cite: 1, 7].
  - Competitor DB: `master_competitor.db` (Unified competitor products, price matrix, snapshots)[cite: 1, 8].
- Output File Naming: Format filename dynamically using timestamping: `<Report_Name>_%Y-%m-%d.xlsx`[cite: 1, 3, 4, 5].

======================================================================
2. DATABASE SCHEMAS & QUERY RULES
======================================================================
- Internal Database (`beautybykat_inventory.db`)[cite: 1, 7]:
  - `products` (upk_id, product_title, brand_id)[cite: 7]
  - `product_variants` (variant_id, upk_id, variant_title, sku, price, compare_at_price, cost_per_item)[cite: 7]
  - `brands` (brand_id, clean_name)[cite: 7]
  - `inventory_snapshots` (snapshot_id, variant_id, inventory_qty, snapshot_date)[cite: 7]
  - `orders` (order_name, created_at, financial_status, fulfillment_status)[cite: 7]
  - `order_line_items` (line_item_id, order_name, variant_id, raw_lineitem_name, quantity, price)[cite: 7]
- Competitor Database (`master_competitor.db`)[cite: 1, 8]:
  - `dim_brands` (brand_id, canonical_name)[cite: 8]
  - `dim_unified_products` (upk_id, brand_id, consensus_title, extracted_spec, canonical_barcode, canonical_sku)[cite: 8]
  - `fact_store_variants` (link_id, upk_id, origin_company, price_bhd)[cite: 8]
  - `fact_daily_stock_snapshots` (snapshot_id, upk_id, link_id, origin_company, snapshot_date, stock_quantity, price_bhd)[cite: 8]

- SQL Best Practices:
  - Always parameterize date/time window queries (`?`)[cite: 1, 3, 4, 5].
  - Use `COALESCE` or `NULLIF` to prevent zero/null division or revenue calculation errors[cite: 1, 3].
  - Daily Stock Depletion (Units Sold): Calculate units sold over time windows by grouping by link/variant ID, taking `prev_stock - current_stock`, and zeroing negative deltas.

======================================================================
3. DATA PROCESSING & MATCHING CONVENTIONS
======================================================================
- Convert numerical columns explicitly (`pd.to_numeric(..., errors='coerce').fillna(...)`)[cite: 1, 3].
- Clean string text and brand titles using standard uppercase/strip/regex operations[cite: 3].
- For matching scripts, utilize `rapidfuzz.fuzz` token ratios, volume/spec extraction (ml, g, oz), and brand filtering before computing similarity scores[cite: 3].

======================================================================
4. EXCEL REPORTING & STYLING REQUIREMENTS
======================================================================
- Excel Engine: Use `xlsxwriter` or `openpyxl`.
- Multi-Tab Structuring: Group distinct analyses into clear, separate sheet names (max 31 characters, sanitized of special characters `[\\/*?:\[\]]`)[cite: 2, 4].
- Visual Formatting:
  - Auto-adjust column widths for readability[cite: 1, 3, 4].
  - Format monetary amounts to BHD currency standard where applicable (`#,##0.000 "BHD"`)[cite: 5].
  - Style professional headers (Dark Navy `#1F4E78` fill with white bold text)[cite: 5].
  - Include visual flags/alerts for key metrics (e.g., price undercuts, overpricing, inventory variances, low stock)[cite: 2, 3, 4].

======================================================================
5. CODE STRUCTURE REQUIREMENTS
======================================================================
- Include standard `logging` configuration (level `INFO`)[cite: 2, 3].
- Implement safe connection handling (auto-close DB connections in `try/finally` or context managers)[cite: 1, 2, 3, 4, 5].
- Guard script execution with `if __name__ == "__main__": main()`[cite: 1, 2, 3, 4, 5].

======================================================================
USER INPUT SPECIFICATIONS FOR NEW SCRIPT:
======================================================================
- Report Title: [INSERT TITLE, e.g., Supplier Purchase Order & Restock Alert Report]
- Objective: [INSERT OBJECTIVE, e.g., Identify fast-selling products with < 10 days of stock left and calculate restock costs]
- Required Databases: [e.g., beautybykat_inventory.db]
- Time Window: [e.g., 30 Days]
- Output Excel Sheets: [e.g., Tab 1: Critical Restock Alerts, Tab 2: Vendor Order Quantities]
- Custom Logic/Rules: [e.g., Flag items with cost_per_item > 0 and calculate total order budget]

Please output the full, runnable Python code with detailed comments explaining the SQL queries and data transformations.