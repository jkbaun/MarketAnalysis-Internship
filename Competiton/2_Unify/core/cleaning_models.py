from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, create_engine
)
from sqlalchemy.orm import declarative_base, relationship

CentralBase = declarative_base()


class AuditFuzzyMatch(CentralBase):
    __tablename__ = 'audit_fuzzy_matches'

    audit_id = Column(Integer, primary_key=True, autoincrement=True)
    origin_company = Column(String)
    store_variant_id = Column(String)
    candidate_upk_id = Column(String)
    incoming_core_key = Column(String)
    matched_core_key = Column(String)
    similarity_score = Column(Float)
    match_status = Column(String)  # 'ACCEPTED' or 'REJECTED'
    timestamp = Column(DateTime, default=datetime.utcnow)

class DimBrand(CentralBase):
    __tablename__ = 'dim_brands'
    
    brand_id = Column(String(50), primary_key=True)       # e.g., 'BRD-COS-1000'
    canonical_name = Column(String(250), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    aliases = relationship("BrandAlias", back_populates="brand", cascade="all, delete-orphan")
    unified_products = relationship("DimUnifiedProduct", back_populates="brand")


class BrandAlias(CentralBase):
    __tablename__ = 'map_store_brand_aliases'
    
    alias_id = Column(Integer, primary_key=True, autoincrement=True)
    raw_input_string = Column(String(250), nullable=False)
    origin_company = Column(String(100), nullable=False)
    brand_id = Column(String(50), ForeignKey('dim_brands.brand_id'), nullable=False)
    match_type = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("DimBrand", back_populates="aliases")


class DimUnifiedProduct(CentralBase):
    """DIMENSION: The single canonical product entity (The UPK)."""
    __tablename__ = 'dim_unified_products'
    
    upk_id = Column(String(50), primary_key=True)         # e.g., 'UPK-COS-1001'
    brand_id = Column(String(50), ForeignKey('dim_brands.brand_id'), nullable=False)
    consensus_title = Column(String(500), nullable=False)
    extracted_spec = Column(String(100), nullable=True)    # e.g., '100ml', '50g'
    clean_match_key = Column(String(500), index=True)
    
    canonical_barcode = Column(String(100), index=True, nullable=True)
    canonical_sku = Column(String(100), index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("DimBrand", back_populates="unified_products")
    store_variants = relationship("FactStoreVariant", back_populates="unified_product")
    stock_snapshots = relationship("FactStockSnapshot", back_populates="unified_product")

class DimTag(CentralBase):
    """DIMENSION: Standardized tags used across all unified products."""
    __tablename__ = 'dim_tags'
    
    tag_id = Column(String(50), primary_key=True)      # e.g., 'TAG-SKIN-001'
    canonical_name = Column(String(100), nullable=False, unique=True)
    category = Column(String(50), nullable=False)      # e.g., 'Skincare', 'Makeup', 'Haircare'

class MapProductTag(CentralBase):
    """BRIDGE: Many-to-Many mapping between UPKs and Canonical Tags."""
    __tablename__ = 'map_product_tags'
    
    map_id = Column(Integer, primary_key=True, autoincrement=True)
    upk_id = Column(String(50), ForeignKey('dim_unified_products.upk_id'), nullable=False)
    tag_id = Column(String(50), ForeignKey('dim_tags.tag_id'), nullable=False)

class FactStoreVariant(CentralBase):
    """LINEAGE / BRIDGE: Connects raw competitor variants to a central UPK with full traceback."""
    __tablename__ = 'fact_store_variants'
    
    link_id = Column(Integer, primary_key=True, autoincrement=True)
    upk_id = Column(String(50), ForeignKey('dim_unified_products.upk_id'), nullable=False)
    
    # Store Origin Identifiers
    origin_company = Column(String(100), nullable=False, index=True)
    store_product_id = Column(String(100), nullable=False)
    store_variant_id = Column(String(100), nullable=False, index=True)
    
    # Raw Metadata (Traceback)
    raw_product_title = Column(String(500))
    raw_variant_title = Column(String(250))
    cleaned_product_title = Column(String, nullable=True)
    raw_sku = Column(String(100))
    raw_barcode = Column(String(100))


    
    price_raw = Column(Float, default=0.0)
    currency_raw = Column(String(10), default="BHD")
    price_bhd = Column(Float, default=0.0)

    # Audit Lineage
    match_tier = Column(String(100))        # e.g., 'Tier 1: Barcode', 'Tier 2: SKU', 'Tier 3: Title Fuzzy'
    match_score = Column(Float, default=1.0) # e.g., 1.0 for exact, 0.88 for fuzzy
    match_key_used = Column(String(250))    # The string/barcode that triggered the match
    
    created_at = Column(DateTime, default=datetime.utcnow)

    unified_product = relationship("DimUnifiedProduct", back_populates="store_variants")
    snapshots = relationship("FactStockSnapshot", back_populates="store_variant")


class FactStockSnapshot(CentralBase):
    """FACT TABLE: Time-series historical stock and pricing for analysis."""
    __tablename__ = 'fact_daily_stock_snapshots'
    
    snapshot_id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Foreign Keys linking to Master Models
    upk_id = Column(String(50), ForeignKey('dim_unified_products.upk_id'), nullable=False, index=True)
    link_id = Column(Integer, ForeignKey('fact_store_variants.link_id'), nullable=False, index=True)
    
    origin_company = Column(String(100), nullable=False, index=True)
    snapshot_date = Column(DateTime, nullable=False, index=True)
    
    # Quantitative Metrics
    price = Column(Float, default=0.0)
    compare_at_price = Column(Float, nullable=True)
    stock_quantity = Column(Integer, nullable=False, default=0)
    is_in_stock = Column(Boolean, default=True)

    price_raw = Column(Float, default=0.0)
    currency_raw = Column(String(10), default="BHD")
    price_bhd = Column(Float, default=0.0)

    
    unified_product = relationship("DimUnifiedProduct", back_populates="stock_snapshots")
    store_variant = relationship("FactStoreVariant", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint('link_id', 'snapshot_date', name='uix_link_snapshot_date'),
    )


def init_central_db(db_uri: str):
    engine = create_engine(db_uri)
    CentralBase.metadata.create_all(engine)
    return engine