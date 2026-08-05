import json
import asyncio
import random
import os
from playwright.async_api import async_playwright
from datetime import datetime
import helper
import argparse

# Parse incoming arguments passed by master-v2.py or fallback to defaults
parser = argparse.ArgumentParser()
parser.add_argument("--tasks", type=int, default=5, help="Concurrent Playwright workers")
parser.add_argument("--retries", type=int, default=5, help="Max retry attempts per product")
args, _ = parser.parse_known_args()

CONCURRENT_TASKS = args.tasks
MAX_RETRIES = args.retries

base_url = "https://sokostore.com"  # Remove on refactor, pass thru config file (keep site-specific URL here)

stock_filename, products_filename = helper.generate_filenames(base_url)
PROGRESS_FILE = stock_filename
# Load products
with open(products_filename, 'r', encoding='utf-8') as f:
    products = json.load(f)

# Initialize or load existing progress checkpoint
scraped_data = {}
if os.path.exists(PROGRESS_FILE):
    try:
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            existing_list = json.load(f)
            scraped_data = {item['link']: item for item in existing_list}
        print(f"Loaded {len(scraped_data)} previously scraped items. Resuming...")
    except Exception as e:
        print(f"Error loading checkpoint, starting fresh: {e}")

def save_progress():
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
        "stock_history": {} # Initialize historical tracking
    })
    
    # Migration fallback if loading old checkpoint data
    if "stock_history" not in item_info:
        item_info["stock_history"] = {}
        
    # Always update dynamic fields
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
                            
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                # Keep your original quantity-component DOM logic, converted to async
                await page.wait_for_selector("quantity-component", timeout=5000)
                element = await page.query_selector("quantity-component")
                if element:
                    stock_level = await element.get_attribute("data-stock")
                    item_info["stock_history"][today_str] = stock_level
                else:
                    item_info["stock_history"][today_str] = "Component not found"
                
                break
        except Exception as e:
            print(f"Error on {url} (Attempt {attempt + 1}): {e}")
            
            if attempt == MAX_RETRIES - 1:
                # This was the final attempt. Log to JSON and mark as error.
                item_info["stock_history"][today_str] = f"Error: {str(e)[:30]}"
                helper.log_failed_pull(url, e) 
            else:
                # Wait briefly before retrying
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
    # --- CLEANUP ---
    # This runs exactly once after the loop finishes (either by breaking on success, or exhausting all retries)
    await asyncio.sleep(random.uniform(1.5, 4.5))

    return url, item_info

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=helper.random_context())
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Filter products needing work (Using the Error-revisit logic!)
        todo_products = []
        for p_item in products:
            product_url = f"{base_url}/products/{p_item['handle']}"
            history = scraped_data.get(product_url, {}).get("stock_history", {})
            if today_str not in history or str(history[today_str]).startswith("Error"):
                todo_products.append(p_item)
                
        print(f"Total remaining to scrape: {len(todo_products)}")
        
        # Turn our list into an async queue so workers can pull from it safely
        queue = asyncio.Queue()
        for p_item in todo_products:
            await queue.put(p_item)

        async def worker():
            while not queue.empty():
                page = await context.new_page()
                for _ in range(5):
                    try:
                        product = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    res = await scrape_product(page, product)
                    if res:
                        url, item_info = res
                        scraped_data[url] = item_info

                await page.close()
                save_progress()
                await asyncio.sleep(random.uniform(2.0, 4.0))
        # Launch exactly 5 permanent workers concurrently
        workers = [worker() for _ in range(CONCURRENT_TASKS)]
        await asyncio.gather(*workers)
        
        await browser.close()
        print("\nProcess fully complete!")

if __name__ == "__main__":
    asyncio.run(main())