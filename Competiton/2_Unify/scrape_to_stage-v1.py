import json
from pathlib import Path
from datetime import datetime
from sqlalchemy.orm import Session
from core.staging_models import (
    init_staging_db, CompetitorProduct, CompetitorVariant, 
    CompetitorTag, CompetitorImage, CompetitorChangeLog, CompetitorStockSnapshot
)

def parse_iso_datetime(dt_str):
    """Safely converts ISO datetime strings (e.g., '2026-07-17T10:03:40+04:00')."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return None

def parse_date(date_str):
    """Safely converts YYYY-MM-DD date strings."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def ingest_products(filepath: Path, session: Session):
    """Pass 1: Ingest complete product details, variants, tags, images, and change logs."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for item in data:
        prod_id = str(item.get("id"))

        # 1. Map Main Product Record
        product = CompetitorProduct(
            id=prod_id,
            title=item.get("title"),
            handle=item.get("handle"),
            body_html=item.get("body_html"),
            vendor=item.get("vendor"),
            product_type=item.get("product_type"),
            status=item.get("status", "active"),
            published_at=parse_iso_datetime(item.get("published_at")),
            created_at=parse_iso_datetime(item.get("created_at")),
            updated_at=parse_iso_datetime(item.get("updated_at"))
        )

        # 2. Map Product Variants (including price, SKU, barcode, and weight)
        for v in item.get("variants", []):
            variant = CompetitorVariant(
                id=str(v.get("id")),
                product_id=prod_id,
                title=v.get("title"),
                sku=v.get("sku"),
                barcode=str(v.get("barcode")).strip() if v.get("barcode") else None,
                price=float(v.get("price", 0.0)) if v.get("price") is not None else 0.0,
                compare_at_price=float(v.get("compare_at_price")) if v.get("compare_at_price") else None,
                available=v.get("available", True),
                requires_shipping=v.get("requires_shipping", True),
                taxable=v.get("taxable", True),
                position=v.get("position", 1),
                grams=float(v.get("grams", 0.0)) if v.get("grams") is not None else 0.0,
                created_at=parse_iso_datetime(v.get("created_at")),
                updated_at=parse_iso_datetime(v.get("updated_at"))
            )
            product.variants.append(variant)

        # 3. Map Tags
        for tag_name in item.get("tags", []):
            product.tags.append(CompetitorTag(name=tag_name))

        # 4. Map Images
        for img in item.get("images", []):
            image = CompetitorImage(
                id=str(img.get("id")),
                product_id=prod_id,
                src=img.get("src"),
                position=img.get("position", 1),
                width=img.get("width"),
                height=img.get("height")
            )
            product.images.append(image)

        # 5. Map Change Logs
        for log in item.get("change_log", []):
            change = CompetitorChangeLog(
                product_id=prod_id,
                variant_id=str(log.get("variant_id")) if log.get("variant_id") else None,
                log_date=parse_date(log.get("date")),
                field=log.get("field"),
                old_value=str(log.get("old")) if log.get("old") is not None else None,
                new_value=str(log.get("new")) if log.get("new") is not None else None
            )
            product.change_logs.append(change)

        session.merge(product)

def sanitize_stock_value(val):
    """
    Safely converts strings/integers to an int.
    Returns None if the value is an error message or non-numeric.
    """
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        cleaned = val.strip()
        # Allow negative/positive integers
        if cleaned.lstrip('-').isdigit():
            return int(cleaned)
    # Log or ignore non-numeric strings like "ReStock-config script not found"
    return None


def ingest_stock(stock_file: Path, session: Session):
    if not stock_file.exists():
        return
    
    with open(stock_file, 'r', encoding='utf-8') as f:
        stock_data = json.load(f)

    existing_snapshots = set(
        session.query(CompetitorStockSnapshot.variant_id, CompetitorStockSnapshot.snapshot_date).all()
    )

    inserted_count = 0
    skipped_dirty_count = 0

    for entry in stock_data:
        # Fallback logic to get variant ID cleanly
        v_id_raw = entry.get('variant_id') or entry.get('id')
        if not v_id_raw or str(v_id_raw).strip() in ["None", "nan", ""]:
            continue
            
        v_id = str(v_id_raw).strip()
        raw_history = entry.get('stock_history')

        if isinstance(raw_history, dict):
            for date_str, raw_qty in raw_history.items():
                qty = sanitize_stock_value(raw_qty)
                if qty is None:
                    skipped_dirty_count += 1
                    continue
                try:
                    dt = datetime.fromisoformat(str(date_str))
                except (ValueError, TypeError):
                    continue

                if (v_id, dt) not in existing_snapshots:
                    snapshot = CompetitorStockSnapshot(
                        variant_id=v_id,
                        snapshot_date=dt,
                        stock_quantity=qty
                    )
                    session.add(snapshot)
                    existing_snapshots.add((v_id, dt))
                    inserted_count += 1

        else:
            raw_date = entry.get('stock_history')
            raw_qty = entry.get('stock_quantity', 0)
            qty = sanitize_stock_value(raw_qty)
            
            if not raw_date or qty is None:
                if qty is None:
                    skipped_dirty_count += 1
                continue
                
            try:
                dt = datetime.fromisoformat(str(raw_date))
            except (ValueError, TypeError):
                continue

            if (v_id, dt) not in existing_snapshots:
                snapshot = CompetitorStockSnapshot(
                    variant_id=v_id,
                    snapshot_date=dt,
                    stock_quantity=qty
                )
                session.add(snapshot)
                existing_snapshots.add((v_id, dt))
                inserted_count += 1

    session.commit()
    print(f"[+] Ingested {inserted_count} new stock snapshots ({skipped_dirty_count} dirty entries skipped).")
    
def main():
    data_dir = Path("/home/boredom-speaking/Desktop/JulyInternship/Pipeline/Data")
    json_dir = data_dir / "scraped"
    db_out_dir = data_dir / "databases"
    db_out_dir.mkdir(parents=True, exist_ok=True)

    competitors = set()
    for filepath in json_dir.glob("master_products_*.json"):
        competitor_name = filepath.stem.replace("master_products_", "")
        competitors.add(competitor_name)

    for comp in competitors:
        print(f"Processing competitor: {comp}...")
        
        db_uri = f"sqlite:///{db_out_dir}/competitor_{comp}.db"
        engine = init_staging_db(db_uri)
        
        with Session(engine) as session:
            products_file = json_dir / f"master_products_{comp}.json"
            stock_file = json_dir / f"master_stock_{comp}.json"

            if products_file.exists():
                ingest_products(products_file, session)
            
            if stock_file.exists():
                ingest_stock(stock_file, session)
            
            session.commit()
            print(f"Successfully populated {comp} database at {db_uri}")

if __name__ == "__main__":
        main()