import random
from urllib.parse import urlparse
import pandas as pd
import json
import os
from datetime import datetime
from pathlib import Path

import random
import os

LOG_DIR = Path(__file__).parent
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0"
]

_STATE = {
    "call_count": 0,
    "current_ua": random.choice(_USER_AGENTS),
    "default_rotate_after": 5  # Matches your default batch / concurrency sizes
}


def generate_filenames(base_url, options=None):
    # 1. Parse the site name from the URL (your existing logic)
    domain = urlparse(base_url).netloc
    site_name = domain.replace("www.", "").split('.')[0]
    
    # 2. Get the absolute path of the directory helper.py is in (1_Scrape)
    current_dir = Path(__file__).parent
    
    # 3. Navigate up two levels to "Pipeline", then down to "Data/scraped"
    target_dir = current_dir.parent.parent / "Data" / "scraped"
    
    # 4. Ensure the directory exists (prevents crashing if the folder is missing)
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 5. Construct the final absolute paths
    stock_filename = target_dir / f"master_stock_{site_name}.json"
    products_filename = target_dir / f"master_products_{site_name}.json"

    if options == "products":
        return str(products_filename)
    elif options == "stock":
        return str(stock_filename)
    return str(stock_filename), str(products_filename)

def process_stock_json(file_path):
    # 1. Extract the company name from the filename
    # Example: 'stock_apple.json' -> 'apple'
    base_name = os.path.basename(file_path)  # Gets 'stock_apple.json'
    file_name_no_ext = os.path.splitext(base_name)[0]  # Gets 'stock_apple'
    
    # Remove 'stock_' prefix
    company_name = file_name_no_ext.replace('stock_', '')
    
    # 2. Process the JSON data
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    # Flatten the JSON (converts nested objects into columns)
    df = pd.json_normalize(data)
    
    # 3. Export to a new CSV file named after the company
    output_filename = f"{company_name}.csv"
    df.to_csv(output_filename, index=False)
    
    # 4. Return the processed name
    return company_name

# --- Example Usage ---
# file = 'stock_apple.json'
# name = process_stock_json(file)
# print(f"Successfully processed: {name}")


def random_context(force_rotate=False, rotate_after=None):
    """
    Returns a User-Agent string. Automatically rotates after X calls.
    
    Optional Arguments:
        force_rotate (bool): If True, forces an immediate rotation.
        rotate_after (int): Override the default rotation threshold.
    """
    threshold = rotate_after if rotate_after is not None else _STATE["default_rotate_after"]
    _STATE["call_count"] += 1
    
    # Check if we hit the threshold OR if forced
    if force_rotate or (_STATE["call_count"] % threshold == 0):
        # Pick a new UA distinct from the current one
        available = [ua for ua in _USER_AGENTS if ua != _STATE["current_ua"]]
        _STATE["current_ua"] = random.choice(available if available else _USER_AGENTS)
        print(f"  [~] [helper.py] Rotated User-Agent after {_STATE['call_count']} calls.")
        
    return _STATE["current_ua"]

def log_error(url, error_msg, stage="STOCK_SCRAPE", status_code=None):
    """
    Appends a standardized error log entry to Data/logs.json for both
    catalog pulls (products.json) and individual stock scraping failures.
    """
    # Define absolute path to Data/logs.json
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "Data"))
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(data_dir, "logs.json")
    
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "stage": stage,
        "url": url,
        "status_code": status_code,
        "error": str(error_msg)
    }
    
    logs = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except json.JSONDecodeError:
            pass  # Start fresh if file is empty or corrupted
            
    logs.append(log_entry)
    
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4)

# Keep alias for backward compatibility with existing stock-*.py scripts
def log_failed_pull(url, error_msg):
    log_error(url, error_msg, stage="STOCK_SCRAPE")


def track_product_changes(existing, incoming, today_str):
    """
    Compares an existing master product record with an incoming raw product JSON object.
    Logs changes to root fields, tags, and variant-level attributes into 'change_log'.
    """
    if "change_log" not in existing:
        existing["change_log"] = []

    # --- 1. ROOT LEVEL METADATA FIELDS ---
    root_fields = [
        "title", "vendor", "product_type", "handle", 
        "published_at", "created_at", "updated_at"
    ]

    for field in root_fields:
        old_val = existing.get(field)
        new_val = incoming.get(field)

        # Log change if value shifted and wasn't previously None
        if old_val != new_val and old_val is not None:
            existing["change_log"].append({
                "date": today_str,
                "field": field,
                "old": old_val,
                "new": new_val
            })
            existing[field] = new_val

    # --- 2. TAGS TRACKING ---
    old_tags = existing.get("tags", [])
    new_tags = incoming.get("tags", [])

    # Standardize string vs list tag inputs
    if isinstance(old_tags, str):
        old_tags = [t.strip() for t in old_tags.split(",") if t.strip()]
    if isinstance(new_tags, str):
        new_tags = [t.strip() for t in new_tags.split(",") if t.strip()]

    if sorted(old_tags) != sorted(new_tags) and old_tags:
        existing["change_log"].append({
            "date": today_str,
            "field": "tags",
            "old": old_tags,
            "new": new_tags
        })
        existing["tags"] = new_tags

    # --- 3. VARIANT LEVEL TRACKING ---
    old_variants = {
        str(v.get("id")): v 
        for v in existing.get("variants", []) 
        if isinstance(v, dict) and v.get("id")
    }
    new_variants = incoming.get("variants", [])
    variant_fields = ["price", "compare_at_price", "available", "grams", "sku"]

    for n_var in new_variants:
        v_id = str(n_var.get("id"))
        v_title = n_var.get("title", "Default Title")

        if v_id in old_variants:
            o_var = old_variants[v_id]
            for v_field in variant_fields:
                o_val = o_var.get(v_field)
                n_val = n_var.get(v_field)

                if o_val != n_val and o_val is not None:
                    existing["change_log"].append({
                        "date": today_str,
                        "field": f"variant_{v_field}",
                        "variant_id": n_var.get("id"),
                        "variant_title": v_title,
                        "old": o_val,
                        "new": n_val
                    })
        else:
            # Log new variant added to an existing product
            existing["change_log"].append({
                "date": today_str,
                "field": "variant_added",
                "variant_id": n_var.get("id"),
                "variant_title": v_title,
                "old": None,
                "new": f"Price: {n_var.get('price')}"
            })

    # Always update the full variants array to latest state
    existing["variants"] = new_variants