import subprocess
import sys
from pathlib import Path
import json
from datetime import datetime

ORCHESTRATOR_DIR = Path(__file__).resolve().parent
DB_BUILDER_DIR = ORCHESTRATOR_DIR / "Inventory" / "Database_Builder"

EXPORTS_DIR = ORCHESTRATOR_DIR / "Data" / "exports"
sys.path.append(str(DB_BUILDER_DIR))

from main import run_pipeline #ignore the error lol



SCRAPPER_CONFIG_FILE = ORCHESTRATOR_DIR / "scrapper_config.json"
REPORTS_CONFIG_FILE = ORCHESTRATOR_DIR / "reports_config.json"

def load_config(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as file:
        return json.load(file)
    
def update_last_run_timestamp(config_file_path):
    """Updates the 'last_run' field in the configuration JSON with the current timestamp."""
    config_path = Path(config_file_path)
    
    if config_path.exists():
        with open(config_path, 'r') as file:
            config_data = json.load(file)
        
        # Set the timestamp to the current ISO format string
        config_data["scrapper"]["last_run"] = datetime.now().isoformat()
        
        with open(config_path, 'w') as file:
            json.dump(config_data, file, indent=4)
        print(f"[+] Updated '{config_file_path}' with execution timestamp: {config_data['scrapper']['last_run']}")

def run_step(script_name, script_dir, description):
    """Helper function to run a pipeline step via subprocess and handle errors."""
    script_path = script_dir / script_name
    print(f"\n==============================================")
    print(f"[PIPELINE] {description}")
    print(f"Running: {script_name}...")
    print(f"==============================================")
    
    try:
        # subprocess.run waits until the script completely finishes
        subprocess.run(
            [sys.executable, str(script_path)], 
            check=True, 
            cwd=str(script_dir)
        )
        print(f"[SUCCESS] {description} completed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"[CRITICAL ERROR] {description} failed! Process exited with code {e.returncode}")
        sys.exit(1) # Kill the orchestrator to prevent downstream database corruption

if __name__ == "__main__":
    scrapper_config = load_config(SCRAPPER_CONFIG_FILE) #[cite: 22]
    reports_config = load_config(REPORTS_CONFIG_FILE) #[cite: 22]

    # ---------------------------------------------------------
    # 1. TRIGGER THE SCRAPING PHASE
    # ---------------------------------------------------------
    SCRAPE_DIR = ORCHESTRATOR_DIR / "Competiton" / "1_Scrape"
    MASTER_SCRIPT = SCRAPE_DIR / "master-v2.py"

    UNIFY_DIR = ORCHESTRATOR_DIR / "Competiton" / "2_Unify"


    PRODUCTS_DIR = EXPORTS_DIR / 'products_orders' #[cite: 22]
    INVOICE_DIR = EXPORTS_DIR / 'invoice' #[cite: 22]
    # Add these underneath your existing path variables
    REPORTS_DIR = ORCHESTRATOR_DIR / "Reports" / "scripts"
# BUILDING BBK DB
    order_files = [PRODUCTS_DIR / f for f in reports_config["order_files"]]
    product_files = [
            (PRODUCTS_DIR / item["filename"], item["date"]) 
            for item in reports_config["product_files"]
        ]

    invoice_files = [
        (INVOICE_DIR / item["name"], item["invoice_number"], item["date"]) 
        for item in reports_config["invoice_files"]
    ]

    if reports_config["audit_inventory"]:
        run_pipeline(product_files, order_files, invoice_files)


    
   
    rerun_setting = scrapper_config["scrapper"].get("rerun", False)
    should_rerun = rerun_setting is True or str(rerun_setting).lower() == "true"

    # 2. Check if last_run date matches today's date
    last_run_raw = scrapper_config["scrapper"].get("last_run", "")
    ran_today = False

    if last_run_raw:
        try:
            last_run_date = datetime.fromisoformat(last_run_raw).date()
            ran_today = (last_run_date == datetime.now().date())
        except (ValueError, TypeError):
            ran_today = False  # If date parsing fails, assume it didn't run today

    # 3. Run phase if rerun is forced OR it hasn't run yet today
    if should_rerun or not ran_today:
        try:
            print("Launching master-v2.py... (Scraping in progress)")
            run_step("master-v2.py", SCRAPE_DIR, "Phase 1: Competitor Web Scraping")
            update_last_run_timestamp(SCRAPPER_CONFIG_FILE)
            print("Scraping completed successfully! Moving to next phase...")
        except subprocess.CalledProcessError as e:
            print(f"CRITICAL ERROR: Scraping failed! {e}")
            sys.exit(1)
        
    else:
        print("[INFO] Skipping scraping phase: already ran today and rerun is false.")
        
    # ---------------------------------------------------------
    # Unify Databases
    # ---------------------------------------------------------

    print("=== STARTING MASTER E-COMMERCE PIPELINE ===")
    # ---------------------------------------------------------
    # Scraping Competitors and Preparing them for data analysis
    # ---------------------------------------------------------
    # 1. Parse rerun flag safely (handles boolean True/False as well as strings like "true"/"false")
    


    # STEP 2: Build the database
    run_step("scrape_to_stage-v1.py", UNIFY_DIR, "Phase 2: Staging Database Ingestion")
    run_step("build_brand_master.py", UNIFY_DIR, "Phase 3: Brand Master Resolution")
    run_step("build_product_master.py", UNIFY_DIR, "Phase 4: Product Master Waterfall & Stock Migration")
    print("\n=== PIPELINE EXECUTION COMPLETE: ALL DATABASES UPDATED ===")

    # ---------------------------------------------------------
    # CONTINUE WITH DIRECTORIES & DB BUILDING FOR BBK
    # ---------------------------------------------------------

    DB_PATH = DB_BUILDER_DIR / "beautybykat_inventory.db"

    run_step("generate_reports.py", REPORTS_DIR, "Phase 5: Automated Final Report Generation")
    
    print("\n==============================================")
    print("      ALL PIPELINE OPERATIONS COMPLETE        ")
    print("==============================================")