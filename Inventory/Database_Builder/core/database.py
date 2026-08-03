import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base


# ==========================================
# 1. DATABASE CONFIGURATION
# ==========================================
# ==========================================
# 1. DATABASE CONFIGURATION
# ==========================================
CORE_DIR = Path(__file__).resolve().parent # /Pipeline/Inventory/Database_Builder/core

# Traverse up 3 levels to get to the root 'Pipeline' directory
PIPELINE_DIR = CORE_DIR.parent.parent.parent 

# Navigate down into Data/databases
DB_DIR = PIPELINE_DIR / "Data" / "databases"

# Ensure both the 'Data' and 'databases' folders exist before creating the DB
# (parents=True ensures it builds the whole folder tree if it's missing)
DB_DIR.mkdir(parents=True, exist_ok=True)

# Build the final SQLite URL
DB_PATH = DB_DIR / "beautybykat_inventory.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

# ==========================================
# 2. ENGINE CREATION
# ==========================================
# The engine is the core interface to the database.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    # check_same_thread is specific to SQLite to prevent thread-locking errors
    connect_args={"check_same_thread": False} 
)

# ==========================================
# 3. SESSION FACTORY
# ==========================================
# This creates temporary "conversations" (sessions) with the database.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ==========================================
# 4. DATABASE INITIALIZATION & MANAGEMENT
# ==========================================

def init_db():
    """
    Constructs the entire database.
    Reads models.py and generates all the physical tables if they don't already exist.
    """
    print("🛠️ Constructing database tables from models.py...")
    # This command looks at Base and builds the schema (Products, Variants, Orders, etc.)
    Base.metadata.create_all(bind=engine)
    print("✅ Database construction complete.")

def get_session():
    """
    A context manager tool for your ingestion scripts.
    It yields a database session and ensures it closes safely afterward, 
    even if your Pandas script throws an error midway.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

if __name__ == "__main__":
    # Running this file directly from the terminal will build the database structure.
    init_db()