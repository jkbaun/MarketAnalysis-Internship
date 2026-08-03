import sys
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify
from datetime import datetime
from sqlalchemy.orm import joinedload # <--- 1. Import joinedload
from sqlalchemy import or_
# Assuming this script is inside Database_Builder/app/
CURRENT_DIR = Path(__file__).resolve().parent
DB_BUILDER_DIR = CURRENT_DIR.parent
if str(DB_BUILDER_DIR) not in sys.path:
    sys.path.append(str(DB_BUILDER_DIR))


from core.database import SessionLocal
from core.models import SupplierInvoice, InvoiceLineItem, ProductVariant, Product, Brand
app = Flask(__name__)

template_path = "invoice.html"
@app.route('/')
def index():
    session = SessionLocal()
    try:
        variants = session.query(ProductVariant).options(joinedload(ProductVariant.product)).all()
        # Fetch existing supplier invoices for the dropdown selection
        invoices = session.query(SupplierInvoice).order_by(SupplierInvoice.date_received.desc()).all()
        return render_template(template_path, variants=variants, invoices=invoices)
    finally:
        session.close()

@app.route('/api/invoice/<int:invoice_id>', methods=['GET'])
def get_invoice_details(invoice_id):
    """API endpoint to load existing invoice and line items for UI populating."""
    session = SessionLocal()
    try:
        invoice = session.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == invoice_id).first()
        if not invoice:
            return {"error": "Invoice not found"}, 404
        
        line_items = session.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice_id).all()
        
        return {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "date_received": invoice.date_received.strftime('%Y-%m-%d') if invoice.date_received else '',
            "total_amount": float(invoice.total_amount) if invoice.total_amount else 0.0,
            "line_items": [
                {
                    "item_id": item.item_id,
                    "variant_id": item.variant_id,
                    "barcode": item.barcode or '',
                    "raw_item_name": item.raw_item_name or '',
                    "uom": item.uom or '',
                    "qty_invoiced": item.qty_invoiced or 0,
                    "qty_counted": item.qty_counted or 0,
                    "unit_price": float(item.unit_price) if item.unit_price else 0.0,
                    "total_price": float(item.total_price) if item.total_price else 0.0
                } for item in line_items
            ]
        }
    finally:
        session.close()

@app.route('/api/search_invoices')
def search_invoices():
    query_string = request.args.get('q', '')
    
    if len(query_string) < 2:
        return jsonify([]) 

    search_term = f"%{query_string}%"

    session = SessionLocal()
    try:
        # Only query the invoice header table
        results = session.query(SupplierInvoice).filter(
            or_(
                SupplierInvoice.vendor_name.ilike(search_term),
                SupplierInvoice.invoice_number.ilike(search_term)
            )
        ).limit(10).all()

        output = []
        for invoice in results:
            output.append({
                'invoice_id': invoice.invoice_id,
                'invoice_number': invoice.invoice_number,
                'vendor_name': invoice.vendor_name,
                'date_received': invoice.date_received.strftime('%Y-%m-%d') if invoice.date_received else '',
            })

        return jsonify(output)
    finally:
        session.close()

@app.route('/submit-invoice', methods=['POST'])
def submit_invoice():
    existing_invoice_id = request.form.get('existing_invoice_id')
    invoice_number = request.form.get('invoice_number')
    vendor_name = request.form.get('vendor_name')
    date_received_str = request.form.get('date_received')
    total_amount = request.form.get('total_amount') or 0.0
    
    date_received = datetime.strptime(date_received_str, "%Y-%m-%d").date() if date_received_str else None
    
    session = SessionLocal()
    try:
        if existing_invoice_id:
            # Update existing invoice header
            invoice = session.query(SupplierInvoice).filter(SupplierInvoice.invoice_id == int(existing_invoice_id)).first()
            invoice.invoice_number = invoice_number
            invoice.vendor_name = vendor_name
            invoice.date_received = date_received
            invoice.total_amount = total_amount
            # Remove old line items to re-insert updated set
            session.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice.invoice_id).delete()
        else:
            # Create new invoice header
            invoice = SupplierInvoice(
                invoice_number=invoice_number,
                vendor_name=vendor_name,
                date_received=date_received,
                total_amount=total_amount
            )
            session.add(invoice)
            session.flush()
        
        # Grab dynamic array rows
        barcodes = request.form.getlist('barcode[]')
        variant_ids = request.form.getlist('variant_id[]')
        raw_names = request.form.getlist('raw_item_name[]')
        uoms = request.form.getlist('uom[]')
        qtys_invoiced = request.form.getlist('qty_invoiced[]')
        qtys_counted = request.form.getlist('qty_counted[]')
        unit_prices = request.form.getlist('unit_price[]')
        total_prices = request.form.getlist('total_price[]')
        
        for i in range(len(raw_names)):
            line_item = InvoiceLineItem(
                invoice_id=invoice.invoice_id,
                variant_id=int(variant_ids[i]) if variant_ids[i] else None,
                barcode=barcodes[i] if i < len(barcodes) else None,
                raw_item_name=raw_names[i],
                uom=uoms[i] if i < len(uoms) else None,
                qty_invoiced=int(qtys_invoiced[i]) if qtys_invoiced[i] else 0,
                qty_counted=int(qtys_counted[i]) if qtys_counted[i] else 0,
                unit_price=float(unit_prices[i]) if unit_prices[i] else 0.0,
                total_price=float(total_prices[i]) if total_prices[i] else 0.0
            )
            session.add(line_item)
            
        session.commit()
        print(f"✅ Invoice {invoice_number} saved successfully!")
    except Exception as e:
        session.rollback()
        print(f"❌ Error saving invoice: {e}")
    finally:
        session.close()
        
    return redirect(url_for('index'))

@app.route('/variants')
def variant_manager():
    """Renders the Variant Management scanner page."""
    session = SessionLocal()
    try:
        # Fetch all variants and join the parent product for the UI lookup
        variants = session.query(ProductVariant).options(joinedload(ProductVariant.product)).all()
        return render_template('variant-manager.html', variants=variants)
    finally:
        session.close()

@app.route('/update-variants', methods=['POST'])
def update_variants():
    """Handles bulk updates to variants scanned into the grid."""
    session = SessionLocal()
    try:
        variant_ids = request.form.getlist('variant_id[]')
        skus = request.form.getlist('sku[]')
        # You could also pull barcodes here if you want them editable
        
        for i in range(len(variant_ids)):
            var_id = int(variant_ids[i])
            variant = session.query(ProductVariant).filter_by(variant_id=var_id).first()
            if variant:
                variant.sku = skus[i] if skus[i] else variant.sku
                
        session.commit()
        print("✅ Variants updated successfully!")
    except Exception as e:
        session.rollback()
        print(f"❌ Error updating variants: {e}")
    finally:
        session.close()
        
    return redirect(url_for('variant_manager'))

if __name__ == '__main__':
    app.run(debug=True, port=5000)