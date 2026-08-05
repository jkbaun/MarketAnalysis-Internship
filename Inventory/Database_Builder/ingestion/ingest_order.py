import sys
import pandas as pd # (or re / datetime depending on the script)
from pathlib import Path
from datetime import datetime
import re

# ==========================================
# DYNAMIC PATH RESOLUTION
# ==========================================
# 1. Find where this script lives (Inventory/Database_Builder/ingestion)
CURRENT_DIR = Path(__file__).resolve().parent

# 2. Define the Database_Builder folder and add it to sys.path so Python finds 'core'
DB_BUILDER_DIR = CURRENT_DIR.parent
if str(DB_BUILDER_DIR) not in sys.path:
    sys.path.append(str(DB_BUILDER_DIR))

# 3. Define the Data folder for CSVs/TXTs
DATA_DIR = DB_BUILDER_DIR.parent / "Data"

# Now you can safely import from core!
from core.database import SessionLocal
from core.models import Order, OrderLineItem

def ingest_orders(csv_filepath, date_str=None):
    print(f"⚙️ Ingesting Orders from {csv_filepath}...")
    
    df = pd.read_csv(csv_filepath, low_memory=False)
    
    # 1. Forward-fill Shopify's multi-item row hierarchy
    status_cols = ['Created at', 'Financial Status', 'Fulfillment Status']
    for col in status_cols:
        if col in df.columns:
            df[col] = df.groupby('Name')[col].ffill().bfill()
            
    session = SessionLocal()
    processed_orders, added_line_items = 0, 0
    
    try:
        for order_name, group in df.groupby('Name'):
            first_row = group.iloc[0]
            
            # Parse datetime safely
            raw_date = str(first_row.get("Created at", ""))
            try:
                # Shopify usually uses: "2026-07-12 14:32:00 +0300"
                order_date = datetime.strptime(raw_date[:19], "%Y-%m-%d %H:%M:%S") 
            except ValueError:
                order_date = datetime.now() # Fallback if parsing fails
                
            fin_status = str(first_row.get("Financial Status", "")).lower()
            ful_status = str(first_row.get("Fulfillment Status", "")).lower()
            
            # --- Upsert Order Header ---
            order = session.query(Order).filter_by(order_name=order_name).first()
            if not order:
                order = Order(
                    order_name=order_name, 
                    created_at=order_date, 
                    financial_status=fin_status, 
                    fulfillment_status=ful_status
                )
                session.add(order)
                processed_orders += 1
            else:
                # Update status in case an older CSV had it as 'pending' but new one says 'paid'
                order.financial_status = fin_status
                order.fulfillment_status = ful_status
                
            session.flush()
            
            # --- Insert Line Items ---
            # We wipe existing line items for this order and rewrite them to prevent duplicates
            session.query(OrderLineItem).filter_by(order_name=order_name).delete()
            
            for _, item_row in group.iterrows():
                lineitem_name = str(item_row.get("Lineitem name", "")).strip()
                qty = int(pd.to_numeric(item_row.get("Lineitem quantity", 1), errors="coerce"))
                price = pd.to_numeric(item_row.get("Lineitem price", 0.0), errors="coerce")
                
                if lineitem_name and lineitem_name.lower() != "nan":
                    new_item = OrderLineItem(
                        order_name=order_name,
                        raw_lineitem_name=lineitem_name,
                        quantity=qty,
                        price=price
                        # variant_id remains NULL until mapped via the Alias system later
                    )
                    session.add(new_item)
                    added_line_items += 1
                    
        session.commit()
        print(f"✅ Success: {processed_orders} Orders processed, {added_line_items} Line Items recorded.")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error during ingestion: {e}")
    finally:
        session.close()
