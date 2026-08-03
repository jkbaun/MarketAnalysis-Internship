from sqlalchemy import Column, Integer, String, Date, Numeric, ForeignKey, DateTime, UniqueConstraint, Boolean, DateTime 
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

# ==========================================
# PRODUCT CATALOG (PARENTS & VARIANTS)
# ==========================================

class Product(Base):
    __tablename__ = 'products'
    
    upk_id = Column(Integer, primary_key=True, autoincrement=True)
    product_title = Column(String, unique=True, nullable=False)
    vendor = Column(String, nullable=True) # Keep raw string for auditing
    
    # Link to the cleaned Brand Dimension
    brand_id = Column(Integer, ForeignKey('brands.brand_id'), nullable=True)
    
    # Relationships
    brand = relationship("Brand", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

class ProductVariant(Base):
    """
    Child Variant Table.
    Holds specific SKUs and options (e.g., "50ml", "100ml", or "Standard").
    """
    __tablename__ = 'product_variants'
    
    variant_id = Column(Integer, primary_key=True, autoincrement=True)
    upk_id = Column(Integer, ForeignKey('products.upk_id'), nullable=False)
    variant_title = Column(String, nullable=False)  # e.g., "50ml" or "Standard"
    sku = Column(String, unique=True, nullable=True) # SKUs can be NULL if store setup was incomplete

    # FOR WHEN INTEGRATING THE SHOPIFY API
    # UNIQUE SHOPIFY PRIMARY ID 
    shopify_variant_id = Column(String, unique=True, nullable=True, index=True)
    variant_barcode = Column(String, nullable=True)

    price = Column(Numeric(10, 2), nullable=True)
    compare_at_price = Column(Numeric(10, 2), nullable=True)
    cost_per_item = Column(Numeric(10, 2), nullable=True)

    first_seen_date = Column(DateTime, default=datetime.utcnow)
    # updated_date = Column(DateTime, default=datetime)
    last_seen_date = Column(DateTime, default=datetime)
    is_active = Column(Boolean, default=True)
    # Relationships
    product = relationship("Product", back_populates="variants")
    snapshots = relationship("InventorySnapshot", back_populates="variant", cascade="all, delete-orphan")
    order_items = relationship("OrderLineItem", back_populates="variant")
    aliases = relationship("AliasMapping", back_populates="variant")

    def __repr__(self):
        return f"<ProductVariant(variant_id={self.variant_id}, title='{self.variant_title}', sku='{self.sku}')>"

class Brand(Base):
    __tablename__ = 'brands'
    
    brand_id = Column(Integer, primary_key=True, autoincrement=True)
    clean_name = Column(String, unique=True, nullable=False)
    
    # Future-proofing: Room for macro analysis metadata
    parent_company = Column(String, nullable=True) 
    country_of_origin = Column(String, nullable=True)
    
    # Relationships
    products = relationship("Product", back_populates="brand")




# ==========================================
# 2. INVENTORY HISTORY (THE LEDGER)
# ==========================================

class InventorySnapshot(Base):
    """
    Historical Stock Ledger.
    Tracks variant inventory levels captured at specific export dates.
    """
    __tablename__ = 'inventory_snapshots'
    
    snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    variant_id = Column(Integer, ForeignKey('product_variants.variant_id'), nullable=False)
    inventory_qty = Column(Integer, nullable=False)
    snapshot_date = Column(Date, nullable=False)
    
    # Relationships
    variant = relationship("ProductVariant", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint('variant_id', 'snapshot_date', name='_variant_snapshot_uc'),
    )
    def __repr__(self):
        return f"<InventorySnapshot(variant_id={self.variant_id}, qty={self.inventory_qty}, date={self.snapshot_date})>"

# ==========================================
# INVOICES (INFLOWS)
# ==========================================


class WholeSaleInvoice(Base):
    __tablename__ = "wholesale_invoices"

    invoice_id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String, unique=True, nullable=False)
    retailer_name =Column(String, unique=True, nullable=False)

    date_sent = Column(Date, nullable=False)
    inventory_moved = Column(Numeric(10, 2), nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False) 

class SupplierInvoice(Base):
    __tablename__ = 'supplier_invoices'
    
    invoice_id = Column(Integer, primary_key=True, autoincrement=True) #[cite: 3]
    invoice_number = Column(String, unique=True, nullable=False) #[cite: 3]
    vendor_name = Column(String, nullable=True) #[cite: 3]
    date_received = Column(Date, nullable=False) #[cite: 3]
    
    # NEW: Store the sum of the invoice
    total_amount = Column(Numeric(10, 2), nullable=True) 

class InvoiceLineItem(Base):
    __tablename__ = 'invoice_line_items'
    
    item_id = Column(Integer, primary_key=True, autoincrement=True) #[cite: 3]
    invoice_id = Column(Integer, ForeignKey('supplier_invoices.invoice_id'), nullable=False) #[cite: 3]
    variant_id = Column(Integer, ForeignKey('product_variants.variant_id'), nullable=True) #[cite: 3]
    
    raw_item_name = Column(String, nullable=False) #[cite: 3]
    qty_invoiced = Column(Integer, nullable=False) #[cite: 3]
    qty_counted = Column(Integer, nullable=True) #[cite: 3]
    
    # NEW: Ingestion fields
    barcode = Column(String, nullable=True)
    uom = Column(String, nullable=True)
    unit_price = Column(Numeric(10, 2), nullable=True)
    total_price = Column(Numeric(10, 2), nullable=True)
 


# ==========================================
# SALES & ORDERS (OUTFLOWS)
# ==========================================

class Order(Base):
    """
    Order Header Table.
    Uses Shopify's order 'Name' (e.g., "#38129") as the primary key.
    """
    __tablename__ = 'orders'
    
    order_name = Column(String, primary_key=True) # e.g., "#38129"
    created_at = Column(DateTime, nullable=False)
    financial_status = Column(String, nullable=True) # e.g., "paid", "partially_refunded"
    fulfillment_status = Column(String, nullable=True) # e.g., "fulfilled", "unfulfilled"
    
    # Relationships
    line_items = relationship("OrderLineItem", back_populates="order", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Order(name='{self.order_name}', status='{self.financial_status}')>"


class OrderLineItem(Base):
    __tablename__ = 'order_line_items'
    
    line_item_id = Column(Integer, primary_key=True, autoincrement=True)
    order_name = Column(String, ForeignKey('orders.order_name'), nullable=False)
    
    # Foreign key to ProductVariant (nullable until mapped via Alias system)
    variant_id = Column(Integer, ForeignKey('product_variants.variant_id'), nullable=True)
    
    raw_lineitem_name = Column(String, nullable=False) # e.g. "Ultrafresh Sunscreen - 50ml"
    quantity = Column(Integer, nullable=False, default=1)
    price = Column(Numeric(10, 2), nullable=True)
    
    # Relationships
    order = relationship("Order", back_populates="line_items")
    variant = relationship("ProductVariant", back_populates="order_items")

    def __repr__(self):
        return f"<OrderLineItem(order='{self.order_name}', item='{self.raw_lineitem_name}', qty={self.quantity})>"


# ==========================================
# 4. BRIDGING & ALIAS MAPPING
# ==========================================

class AliasMapping(Base):
    """
    Bridging table to resolve messy or changing line item names from CSV exports
    to their true ProductVariant ID.
    """
    __tablename__ = 'alias_mappings'
    
    mapping_id = Column(Integer, primary_key=True, autoincrement=True)
    raw_name = Column(String, unique=True, nullable=False) # The raw string seen in order/invoice CSVs
    variant_id = Column(Integer, ForeignKey('product_variants.variant_id'), nullable=False)
    
    # Relationships
    variant = relationship("ProductVariant", back_populates="aliases")

    def __repr__(self):
        return f"<AliasMapping(raw_name='{self.raw_name}' -> variant_id={self.variant_id})>"