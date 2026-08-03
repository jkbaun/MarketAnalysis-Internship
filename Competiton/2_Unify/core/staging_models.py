from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, create_engine
)
from sqlalchemy.orm import declarative_base, relationship

# Dedicated Base for Staging DBs
StagingBase = declarative_base()


class CompetitorProduct(StagingBase):
    __tablename__ = 'products'

    id = Column(String, primary_key=True)  # Store/Shopify Product ID
    title = Column(String, nullable=False, index=True)
    handle = Column(String, index=True)
    body_html = Column(Text, nullable=True)
    vendor = Column(String, index=True)
    product_type = Column(String, index=True)
    status = Column(String, default="active")
    
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationships
    variants = relationship("CompetitorVariant", back_populates="product", cascade="all, delete-orphan")
    tags = relationship("CompetitorTag", back_populates="product", cascade="all, delete-orphan")
    images = relationship("CompetitorImage", back_populates="product", cascade="all, delete-orphan")
    change_logs = relationship("CompetitorChangeLog", back_populates="product", cascade="all, delete-orphan")


class CompetitorVariant(StagingBase):
    __tablename__ = 'variants'

    id = Column(String, primary_key=True)  # Store/Shopify Variant ID
    product_id = Column(String, ForeignKey('products.id'), nullable=False, index=True)
    title = Column(String, nullable=False)
    sku = Column(String, index=True, nullable=True)
    barcode = Column(String, index=True, nullable=True)  # Barcode / EAN / UPC
    
    price = Column(Float, nullable=False, default=0.0)
    compare_at_price = Column(Float, nullable=True)
    available = Column(Boolean, default=True)
    requires_shipping = Column(Boolean, default=True)
    taxable = Column(Boolean, default=True)
    
    position = Column(Integer, default=1)
    grams = Column(Float, default=0)

    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationships
    product = relationship("CompetitorProduct", back_populates="variants")
    stock_snapshots = relationship("CompetitorStockSnapshot", back_populates="variant", cascade="all, delete-orphan")


class CompetitorTag(StagingBase):
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey('products.id'), nullable=False, index=True)
    name = Column(String, nullable=False, index=True)

    product = relationship("CompetitorProduct", back_populates="tags")


class CompetitorImage(StagingBase):
    __tablename__ = 'images'

    id = Column(String, primary_key=True)
    product_id = Column(String, ForeignKey('products.id'), nullable=False, index=True)
    src = Column(Text, nullable=False)
    position = Column(Integer, default=1)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)

    product = relationship("CompetitorProduct", back_populates="images")


class CompetitorChangeLog(StagingBase):
    __tablename__ = 'change_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey('products.id'), nullable=False, index=True)
    variant_id = Column(String, nullable=True)
    
    log_date = Column(DateTime, nullable=False)
    field = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

    product = relationship("CompetitorProduct", back_populates="change_logs")


class CompetitorStockSnapshot(StagingBase):
    __tablename__ = 'stock_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    variant_id = Column(String, ForeignKey('variants.id'), nullable=False, index=True)
    snapshot_date = Column(DateTime, nullable=False, index=True)
    stock_quantity = Column(Integer, nullable=False)

    variant = relationship("CompetitorVariant", back_populates="stock_snapshots")

    __table_args__ = (
        UniqueConstraint('variant_id', 'snapshot_date', name='uix_variant_date'),
    )


def init_staging_db(db_uri: str):
    """Utility to initialize a store-specific staging database."""
    engine = create_engine(db_uri)
    StagingBase.metadata.create_all(engine)
    return engine