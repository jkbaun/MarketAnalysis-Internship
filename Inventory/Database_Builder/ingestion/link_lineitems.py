from core.database import SessionLocal
from core.models import ProductVariant, OrderLineItem, InvoiceLineItem, AliasMapping

def resolve_unlinked_line_items():
    print("⚙️ Running Alias Resolver to link sales & invoices to product variants...")
    session = SessionLocal()
    
    try:
        # Load existing aliases into memory: {raw_name: variant_id}
        alias_map = {a.raw_name: a.variant_id for a in session.query(AliasMapping).all()}
        
        # Load all variants for direct title/SKU matching
        variants = session.query(ProductVariant).all()
        variant_by_title = {v.variant_title.lower(): v.variant_id for v in variants}
        variant_by_sku = {v.sku.lower(): v.variant_id for v in variants if v.sku}
        
        # 1. Resolve Order Line Items
        unlinked_orders = session.query(OrderLineItem).filter(OrderLineItem.variant_id == None).all()
        resolved_orders = 0
        
        for item in unlinked_orders:
            raw_name = item.raw_lineitem_name.strip()
            raw_lower = raw_name.lower()
            
            # Match 1: Alias Table
            if raw_name in alias_map:
                item.variant_id = alias_map[raw_name]
                resolved_orders += 1
            # Match 2: Direct Title Match
            elif raw_lower in variant_by_title:
                item.variant_id = variant_by_title[raw_lower]
                resolved_orders += 1
            # Match 3: SKU Match
            elif raw_lower in variant_by_sku:
                item.variant_id = variant_by_sku[raw_lower]
                resolved_orders += 1

        session.commit()
        print(f"✅ Successfully linked {resolved_orders} order line items to Product Variants.")
        
        # Flag remaining unlinked items for manual alias mapping
        remaining = session.query(OrderLineItem.raw_lineitem_name).filter(OrderLineItem.variant_id == None).distinct().all()
        if remaining:
            print(f"⚠️ {len(remaining)} distinct line items could not be automatically matched:")
            for r in remaining:
                print(f"   - '{r[0]}'")
                
    except Exception as e:
        session.rollback()
        print(f"❌ Error during alias resolution: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    resolve_unlinked_line_items()