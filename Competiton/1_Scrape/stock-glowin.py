import json
import asyncio
import random
import os
import re
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

base_url = "https://glowinbh.com"
stock_filename, products_filename = helper.generate_filenames(base_url)
PROGRESS_FILE = stock_filename

# Load products catalog
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
    
    # Target variant ID as string for key matching
    target_variant_id = str(product["variants"][0]["id"]) if product.get("variants") else None
    
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
    
    # Out of stock catalog items handled instantly
    if not is_available:
        item_info["stock_history"][today_str] = "0"
        return url, item_info

    for attempt in range(MAX_RETRIES): 
        try:
            print(f"Scraping (Attempt {attempt + 1}/{MAX_RETRIES}): {url}")

            # Grab Barcode/SKU via lightweight JSON endpoint if missing
            if item_info.get("barcode", "N/A") == "N/A":
                try:
                    json_response = await page.request.get(f"{url}.json")
                    if json_response.ok:
                        full_data = await json_response.json()
                        full_variants = full_data.get("product", {}).get("variants", [])
                        if full_variants:
                            item_info["barcode"] = full_variants[0].get("barcode", "N/A")
                            item_info["sku"] = full_variants[0].get("sku", item_info.get("sku", "N/A"))
                except Exception:
                    pass

            # Load page DOM
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            
            stock_level = None

            # --- METHOD 1: Direct HTML Text Content & Regex Extraction ---
            try:
                raw_json = None
                script_element = await page.query_selector('script[data-product-inventory-json]')
                
                if script_element:
                    # FIX: text_content() works on hidden <script> elements, inner_text() returns ""
                    raw_json = await script_element.text_content()
                else:
                    # Fallback Regex directly on full page HTML source
                    html_content = await page.content()
                    match = re.search(r'<script[^>]*data-product-inventory-json[^>]*>(.*?)</script>', html_content, re.DOTALL)
                    if match:
                        raw_json = match.group(1)

                if raw_json and raw_json.strip():
                    data = json.loads(raw_json)
                    inventory_data = data.get("inventory", {})
                    
                    if inventory_data:
                        if target_variant_id and target_variant_id in inventory_data:
                            stock_level = inventory_data[target_variant_id].get("inventory_quantity")
                        else:
                            first_key = next(iter(inventory_data))
                            stock_level = inventory_data[first_key].get("inventory_quantity")
            except Exception:
                pass # Non-blocking

            # --- METHOD 2: window.REZ_DATA JS Evaluation (with non-blocking wait) ---
            if stock_level is None:
                try:
                    # Wait up to 2.5s specifically for window.REZ_DATA to populate without throwing outer errors
                    try:
                        await page.wait_for_function("() => window.REZ_DATA && window.REZ_DATA.inventoryQuantityByVariantId", timeout=2500)
                    except Exception:
                        pass

                    rez_data = await page.evaluate("window.REZ_DATA")
                    if rez_data and "inventoryQuantityByVariantId" in rez_data:
                        inv_map = rez_data["inventoryQuantityByVariantId"]
                        
                        if target_variant_id in inv_map:
                            stock_level = inv_map[target_variant_id]
                        elif target_variant_id and target_variant_id.isdigit() and int(target_variant_id) in inv_map:
                            stock_level = inv_map[int(target_variant_id)]
                        elif inv_map:
                            stock_level = next(iter(inv_map.values()))
                except Exception:
                    pass

            # --- VERIFY AND SAVE ---
            if stock_level is not None:
                item_info["stock_history"][today_str] = stock_level
                break # Success! Exit retry loop.
            else:
                raise ValueError("Stock data absent from DOM script tag and window.REZ_DATA")

        except Exception as e:
            print(f"Error on {url} (Attempt {attempt + 1}): {e}")
            
            if attempt == MAX_RETRIES - 1:
                item_info["stock_history"][today_str] = f"Error: {str(e)[:30]}"
                helper.log_failed_pull(url, e) 
            else:
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
    await asyncio.sleep(random.uniform(1.0, 3.0))
    return url, item_info

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=helper.random_context())
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Filter products needing work
        todo_products = []
        for p_item in products:
            product_url = f"{base_url}/products/{p_item['handle']}"
            history = scraped_data.get(product_url, {}).get("stock_history", {})
            
            status_today = str(history.get(today_str, ""))
            
            if not status_today or status_today.startswith("Error") or "not found" in status_today or "empty" in status_today:
                todo_products.append(p_item)

        print(f"Total remaining to scrape: {len(todo_products)}")
        
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
                await asyncio.sleep(random.uniform(1.5, 3.5))

        workers = [worker() for _ in range(CONCURRENT_TASKS)]
        await asyncio.gather(*workers)
        
        await browser.close()
        print("\nProcess fully complete!")

if __name__ == "__main__":
    asyncio.run(main())