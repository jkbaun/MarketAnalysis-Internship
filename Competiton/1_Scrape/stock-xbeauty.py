import json
import asyncio
import random
import re
import os
from playwright.async_api import async_playwright
from datetime import datetime
import helper
import argparse

import time

# Parse incoming arguments passed by master-v2.py or fallback to defaults
parser = argparse.ArgumentParser()
parser.add_argument("--tasks", type=int, default=5, help="Concurrent Playwright workers")
parser.add_argument("--retries", type=int, default=5, help="Max retry attempts per product")
args, _ = parser.parse_known_args()

CONCURRENT_TASKS = args.tasks
MAX_RETRIES = args.retries

base_url = "https://xbeauty.me" # Remove on refactor, pass thru config file (keep site-specific URL here)
stock_filename, products_filename = helper.generate_filenames(base_url)
PROGRESS_FILE = stock_filename

# Load products
with open(products_filename, 'r', encoding='utf-8') as f:
    products = json.load(f)

# Initialize or load existing progress
scraped_data = {}
if os.path.exists(PROGRESS_FILE):
    try:
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            existing_list = json.load(f)
            # Use product link/handle as key to quickly check if already done
            scraped_data = {item['link']: item for item in existing_list}
        print(f"Loaded {len(scraped_data)} previously scraped items. Resuming...")
    except Exception as e:
        print(f"Error loading checkpoint, starting fresh: {e}")

# --- FIX 1: Async Lock for thread-safe saving ---
save_lock = asyncio.Lock()

async def save_progress():
    """Helper to dump current progress safely without blocking the event loop."""
    async with save_lock:
        await asyncio.to_thread(_write_json)

def _write_json():
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(scraped_data.values()), f, ensure_ascii=False, indent=4)

async def scrape_product(page, product, full_catalog=True):
    is_available = any(v.get("available") == True for v in product.get("variants", []))
    if not full_catalog and not is_available:
        return None

    handle = product['handle']
    url = f"{base_url}/products/{handle}"
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Load existing data from the master file, or initialize a new record
    item_info = scraped_data.get(url, {
        "id": product.get("id"),
        "title": product.get("title"),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "link": url,
        "stock_history": {} 
    })
    
    if "stock_history" not in item_info:
        item_info["stock_history"] = {}
        
    item_info["sale_price"] = product["variants"][0]["price"] if product.get("variants") else "N/A"
    
    if not is_available:
        item_info["stock_history"][today_str] = "0"
        return url, item_info

    for attempt in range(MAX_RETRIES): 
        try:
            print(f"Scraping: {url}")
            if item_info.get("barcode", "N/A") == "N/A":
                    json_response = await page.request.get(f"{url}.json")
                    if json_response.ok:
                        full_data = await json_response.json()
                        full_variants = full_data.get("product", {}).get("variants", [])
                        if full_variants:
                            item_info["barcode"] = full_variants[0].get("barcode", "N/A")
                            # We can also guarantee the SKU is captured perfectly here
                            item_info["sku"] = full_variants[0].get("sku", item_info.get("sku", "N/A"))
            
            # Container to capture our target API data
            intercepted_data = {}

            # Define the callback worker to watch background traffic
            async def capture_json(response):
                if "product-with-variants" in response.url and response.status == 200:
                    try:
                        intercepted_data["json"] = await response.json()
                    except Exception:
                        pass # Ignore parsing failures on non-JSON anomalies

            # Attach listener
            page.on("response", capture_json)

            # --- FIX 2: Try/Finally to prevent memory leaks ---
            try:
                # 1. Change to 'domcontentloaded' so the page doesn't hang on background trackers
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                # 2. Gracefully wait up to 3 seconds for the specific MoniCommerce API to fire.
                for _ in range(6):
                    if "json" in intercepted_data:
                        break
                    await asyncio.sleep(0.5)
            finally:
                # Remove listener immediately to prevent memory leaks across iterations
                page.remove_listener("response", capture_json)

            # --- METHOD 1: Clean API Interception ---
            if "json" in intercepted_data and intercepted_data["json"].get("variants"):
                api_variants = intercepted_data["json"]["variants"]
                
                # Check your Network tab's variant object to verify if they use 'inventory_quantity' or 'quantity'
                stock_level = api_variants[0].get("inventory_quantity") or api_variants[0].get("quantity")
                
                if stock_level is not None:
                    print(f"-> Successfully extracted stock via MoniCommerce API: {stock_level}")
                    item_info["stock_history"][today_str] = stock_level
                    break

            # --- METHOD 2: Fallback to global window variable ---
            stock_level = await page.evaluate("window._ReStockConfig?.product?.variants[0]?.quantity")
            if stock_level is not None:
                print(f"-> Fallback successful via window variable: {stock_level}")
                item_info["stock_history"][today_str] = stock_level
                break
            
            # --- METHOD 3: Fallback to HTML Regex extraction ---
            try:
                # Wait up to 3 seconds for the element to appear in the DOM
                script_element = await page.wait_for_selector('#ReStock-config', timeout=3000)
                script_text = await script_element.inner_text()
                match = re.search(r'quantity\s*:\s*(\d+)', script_text)
                item_info["stock_history"][today_str] = int(match.group(1)) if match else "Quantity missing"
            except Exception:
                # If 3 seconds pass and it still isn't there, it's genuinely missing
                item_info["stock_history"][today_str] = "Error: ReStock-config script not found"
            break 
        except Exception as e:
            print(f"Error on {url} (Attempt {attempt + 1}): {e}")
            
            if attempt == MAX_RETRIES - 1:
                item_info["stock_history"][today_str] = f"Error: {str(e)[:30]}"
                helper.log_failed_pull(url, e) 
            else:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
    await asyncio.sleep(random.uniform(1.5, 4.5))
    return url, item_info

async def main():
    start_time = time.perf_counter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=helper.random_context())
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Filter products needing work (Targeting specifically ReStock-config errors)
        todo_products = []
        for p_item in products:
            product_url = f"{base_url}/products/{p_item['handle']}"
            history = scraped_data.get(product_url, {}).get("stock_history", {})
            
            # Check if it hasn't been scraped today OR if it specifically hit the ReStock-config error
            status_today = str(history.get(today_str, ""))
            if not status_today or status_today.startswith("Error") or "ReStock" in status_today:
                todo_products.append(p_item)
                
        print(f"Total remaining to scrape: {len(todo_products)}")
        
        # Turn our list into an async queue so workers can pull from it safely
        queue = asyncio.Queue()
        for p_item in todo_products:
            await queue.put(p_item)

        async def worker():
            while True:
                try:
                    product = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break # Genuinely empty, exit worker cleanly

                page = await context.new_page()
                try:
                    res = await scrape_product(page, product)
                    if res:
                        url, item_info = res
                        scraped_data[url] = item_info
                        await save_progress() # Save immediately per item
                except Exception as e:
                    print(f"Worker execution error: {e}")
                finally:
                    await page.close()

                await asyncio.sleep(random.uniform(2.0, 4.0))        
        # Launch permanent workers concurrently based on argparse
        workers = [worker() for _ in range(CONCURRENT_TASKS)]
        await asyncio.gather(*workers)
        
        await browser.close()

        elapsed_seconds = time.perf_counter() - start_time
        minutes = int(elapsed_seconds // 60)
        seconds = int(elapsed_seconds % 60)
        print(f"\nProcess fully complete! Elapsed time: {minutes}m {seconds}s")

# Run the async loop
if __name__ == "__main__":
    asyncio.run(main())