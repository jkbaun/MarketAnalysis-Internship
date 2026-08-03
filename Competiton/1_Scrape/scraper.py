import asyncio
import aiohttp
import json
import random
import time
from datetime import datetime
import os
import helper  # <-- 1. IMPORT YOUR UPDATED HELPER MODULE

# =====================================================================
# CONFIGURATION & CONSTANTS
# =====================================================================
COMPETITORS_URLS = [
    "https://sokostore.com/products.json", 
    "https://xbeauty.me/products.json",
    "https://glowinbh.com/products.json" 
]

import argparse

# Attempt to load scrapper_config.json if running directly
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "scrapper_config.json")
cfg = {}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full_cfg = json.load(f)
            cfg_perf = full_cfg.get("scraping_performance", {})
            cfg_bot = full_cfg.get("anti_bot_and_delays", {})
    except Exception:
        cfg_perf, cfg_bot = {}, {}
else:
    cfg_perf, cfg_bot = {}, {}

BATCH_SIZE = cfg_perf.get("batch_size", 5)
MIN_JITTER = cfg_bot.get("min_jitter", 1.5)
MAX_JITTER = cfg_bot.get("max_jitter", 3.8)
RETRY_COOLDOWN = cfg_bot.get("retry_cooldown", 5.0)

# Global log array to be saved to log.json
log_entries = []

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================
def add_log(url, page, status_code, status_type, message=""):
    """Records an event with a timestamp for log.json."""
    log_entries.append({
        "timestamp": datetime.now().isoformat(),
        "url": url,
        "page": page,
        "status_code": status_code,
        "status_type": status_type,
        "details": message
    })

async def fetch_page(session, base_url, page, is_retry=False):
    """
    Fetches a single page asynchronously with randomized jitter and dynamic User-Agents.
    Returns (page_num, products_list, success_bool).
    """
    # 1. Implement Jitter (apply a slightly longer delay if it's a retry pass)
    jitter_delay = random.uniform(MIN_JITTER, MAX_JITTER) * (1.5 if is_retry else 1.0)
    await asyncio.sleep(jitter_delay)
    
    # 2. DYNAMIC USER-AGENT INJECTION
    # If this is a retry pass (we failed earlier), force an immediate UA rotation!
    headers = {
        "User-Agent": helper.random_context(force_rotate=is_retry),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    
    target_url = f"{base_url}?limit=250&page={page}"
    
    try:
        async with session.get(target_url, headers=headers, timeout=15) as response:
            status = response.status
            
            # Handle Success
            if status == 200:
                data = await response.json()
                products = data.get('products', [])
                add_log(base_url, page, status, "SUCCESS", f"Retrieved {len(products)} items.")
                print(f"  [+] Page {page}: Success ({len(products)} products)")
                return page, products, True
            
            # Handle Rate Limiting / Server Errors (503, 429, etc.)
            else:
                helper.log_error(
                    url=target_url,
                    error_msg=f"HTTP Error {status}",
                    stage="CATALOG_PULL",
                    status_code=status
                )
                print(f"  [-] Page {page}: Failed with status {status}")
                return page, [], False
                
    except Exception as e:
        helper.log_error(
            url=target_url,
            error_msg=str(e),
            stage="CATALOG_PULL",
            status_code=0
        )
        print(f"  [-] Page {page}: Exception -> {str(e)}")
        return page, [], False

# =====================================================================
# CORE SCRAPER LOGIC
# =====================================================================
async def scrape_store(session, base_url):
    print(f"\n==========================================")
    print(f"Starting Scrape: {base_url}")
    print(f"==========================================")
    
    all_products = []
    retry_queue = []
    current_page = 1
    has_more_pages = True
    
    # --- PHASE 1: Asynchronous Batch Scraping ---
    while has_more_pages:
        print(f"\n--- Fetching Batch: Pages {current_page} to {current_page + BATCH_SIZE - 1} ---")
        
        # Create tasks for the current batch of pages
        tasks = [
            fetch_page(session, base_url, page) 
            for page in range(current_page, current_page + BATCH_SIZE)
        ]
        
        # Execute batch concurrently
        results = await asyncio.gather(*tasks)
        
        for page_num, products, success in results:
            if success:
                if products:
                    all_products.extend(products)
                    # If a page returns fewer than the 250 limit, we hit the catalog end
                    if len(products) < 250:
                        has_more_pages = False
                else:
                    # 200 OK but empty products list -> end of catalog
                    has_more_pages = False
            else:
                # Add failed page to retry queue to be skipped for now
                retry_queue.append(page_num)
        
        current_page += BATCH_SIZE
        
        # If we detected the end of the catalog, stop spawning new batches
        if not has_more_pages:
            print("Reached end of catalog pagination.")
            break

    # --- PHASE 2: Revisit Skipped Pages (The Retry Queue) ---
    if retry_queue:
        print(f"\n--- Cooling down for {RETRY_COOLDOWN}s before retrying {len(retry_queue)} skipped page(s) ---")
        await asyncio.sleep(RETRY_COOLDOWN)
        
        print(f"Revisiting Pages: {retry_queue}")
        retry_tasks = [fetch_page(session, base_url, page, is_retry=True) for page in retry_queue]
        retry_results = await asyncio.gather(*retry_tasks)
        
        for page_num, products, success in retry_results:
            if success and products:
                all_products.extend(products)
                print(f"  [!] Successfully recovered Page {page_num} on retry!")
            else:
                print(f"  [X] Page {page_num} permanently failed.")

    # --- PHASE 3: Deduplication, Change Tracking & Missing Items ---
    if all_products:
        filename = helper.generate_filenames(base_url, options="products")
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 1. Load existing master catalog
        master_catalog = {}
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
                master_catalog = {str(p['id']): p for p in existing_data}
                
        new_count = 0
        updated_count = 0
        
        # 2. Track all IDs fetched during TODAY'S scrape
        scraped_ids = set()
        
        # -----------------------------------------------------------
        # LOOP 1: Process active items returned by the store
        # -----------------------------------------------------------
        # LOOP 1: Process active items returned by the store
        for p in all_products:
            pid = str(p['id'])
            scraped_ids.add(pid)
            
            if pid not in master_catalog:
                # Brand new product added to the store today
                master_catalog[pid] = p
                master_catalog[pid]['status'] = 'active'
                master_catalog[pid]['change_log'] = []
                new_count += 1
            else:
                existing = master_catalog[pid]
                if 'change_log' not in existing:
                    existing['change_log'] = []

                # If product was unlisted but came back today
                if existing.get('status') == 'unlisted':
                    existing['status'] = 'active'
                    existing['change_log'].append({
                        "date": today_str,
                        "field": "status",
                        "old": "unlisted",
                        "new": "active"
                    })

                # --- COMPREHENSIVE CHANGE LOG TRACKING ---
                helper.track_product_changes(existing, p, today_str)

        # -----------------------------------------------------------
        # LOOP 2: Catch items in master_catalog NOT present today
        # (This runs OUTSIDE Loop 1, right here!)
        # -----------------------------------------------------------
        unlisted_count = 0
        for pid, item in master_catalog.items():
            if pid not in scraped_ids and item.get("status") != "unlisted":
                item["status"] = "unlisted"
                item.setdefault("change_log", []).append({
                    "date": today_str,
                    "field": "status",
                    "old": "active",
                    "new": "unlisted"
                })
                unlisted_count += 1

        # 3. Save everything back to disk
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(list(master_catalog.values()), f, indent=4, ensure_ascii=False)

        print(f"\n[DONE] Summary:")
        print(f"  - New products added: {new_count}")
        print(f"  - Metadata updates: {updated_count}")
        print(f"  - Products marked unlisted: {unlisted_count}")
# =====================================================================
# MAIN ENTRY POINT
# =====================================================================
async def main():
    start_time = time.time()
    
    # Use a single TCP connector session across all requests for efficiency
    connector = aiohttp.TCPConnector(limit_per_host=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        for url in COMPETITORS_URLS:
            await scrape_store(session, url)
            # Brief rest between different target competitors
            await asyncio.sleep(random.uniform(2.0, 4.0))
            
        
    elapsed = round(time.time() - start_time, 2)
    print(f"\n==========================================")
    print(f"All operations completed in {elapsed} seconds.")
    print(f"Execution logs saved to log.json.")
    print(f"==========================================")

if __name__ == "__main__":
    # In VS Code / standard terminal, run the async loop
    asyncio.run(main())