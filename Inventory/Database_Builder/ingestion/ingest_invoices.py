import sys
import pandas as pd # (or re / datetime depending on the script)
from pathlib import Path
from datetime import datetime
import re
import pdfplumber
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
from core.models import SupplierInvoice, InvoiceLineItem

def ingest_invoice_text(txt_filepath, invoice_number="000000000", manual_date_str="2000-01-01", vendor_name="Update Vendor Name"):
    print(f"⚙️ Parsing Invoice {invoice_number} from {txt_filepath}...")
    
    date_received = datetime.strptime(manual_date_str, "%Y-%m-%d").date()
    session = SessionLocal()
    added_items = 0
    
    try:
        # --- Upsert Invoice Header ---
        invoice = session.query(SupplierInvoice).filter_by(invoice_number=invoice_number).first()
        if not invoice:
            invoice = SupplierInvoice(
                invoice_number=invoice_number,
                vendor_name=vendor_name,
                date_received=date_received
            )
            session.add(invoice)
            session.flush()
        else:
            # NEW: Wipe existing line items so a re-run doesn't duplicate them
            session.query(InvoiceLineItem).filter_by(invoice_id=invoice.invoice_id).delete()
            session.flush()
        
        # Read the raw text file
        content = ""
        with pdfplumber.open(txt_filepath) as pdf:
            for page in pdf.pages:
                # extract_text() usually preserves the visual layout 
                content += page.extract_text(layout=True, y_tolerance=1) + "\n"
                print(content)
        # Regex to find: 13-digit Barcode -> Item Name -> Quantity -> "PC"
        # Adapting to the specific format of Luxe Organix Invoice-2.pdf
        # Make the pipe optional (?:\|)? and handle variable spacing
        # Using \s+ forces a mandatory space before the quantity
# \b ensures we are grabbing a whole number, not a fragment
        # 1. Update the Regex to lock onto known UOMs (PC, SET, B1T1, CTN)
# This forces Group 2 to capture the entire Item Name + Broken Quantity
        pattern = re.compile(r'\[?(\d{12,})\]?\s+(.+)\s+(PC|PCS|SET|B1T1|CTN|BOX)\s+([\d\.,]+)\s+([\d\.,]+)', re.IGNORECASE)
        matches = pattern.findall(content)

        invoice_total_sum = 0.0

        for match in matches:
            barcode = match[0].strip()
            middle_chunk = match[1].strip()
            uom = match[2].strip()
            
            # STRIP COMMAS before converting to float to prevent ValueError
            unit_price = float(match[3].strip().replace(',', ''))
            total_price = float(match[4].strip().replace(',', ''))
            
            # --- THE MAGIC PARSER ---
            qty_match = re.search(r'([\d\s]+)$', middle_chunk)
            
            if qty_match:
                raw_qty_str = qty_match.group(1)
                qty = int(raw_qty_str.replace(" ", ""))
                item_name = middle_chunk[:-len(raw_qty_str)].strip()
            else:
                qty = 0
                item_name = middle_chunk
                
            invoice_total_sum += total_price
            
            # Create the inflow ledger item
            line_item = InvoiceLineItem(
                invoice_id=invoice.invoice_id,
                raw_item_name=item_name,
                barcode=barcode,
                qty_invoiced=qty,
                qty_counted=None,
                uom=uom,
                unit_price=unit_price,
                total_price=total_price
            )
            session.add(line_item)
            added_items += 1


        # After the loop, update the invoice header with the calculated sum
        invoice.total_amount = invoice_total_sum
        session.add(invoice)
        session.commit()
        print(f"✅ Success: Invoice {invoice_number} logged with {added_items} items received on {manual_date_str}.")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error during ingestion: {e}")
    finally:
        session.close()

