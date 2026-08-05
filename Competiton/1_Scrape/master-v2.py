import subprocess
import sys
import os
import re
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MAX_ATTEMPTS = 15


def run_script(script_name):
    print(f"\n[{script_name}] Starting execution...")
    
    # 2. Join the base directory with the script name so Python always finds it
    script_path = os.path.join(BASE_DIR, script_name)
    try:
        # Run the script using the absolute path
        result = subprocess.run([sys.executable, script_path], check=True)
        print(f"[{script_name}] Completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[{script_name}] FAILED with exit code {e.returncode}.")
        return False


def run_scraping_pipeline(config=None, script_dir=None, data_dir=None):
    """
    Master function to run the scraper and concurrent stock updates.
    
    :param config: Dict containing configuration options (e.g. max_pipeline_attempts, stock_scripts)
    :param script_dir: Directory where scraper scripts live
    :param data_dir: Directory where output data should be stored
    :return: bool (True if all scripts finished successfully, False otherwise)
    """
    script_dir = Path(script_dir) if script_dir else BASE_DIR
    config = config or {}

    # Extract inputs with sensible fallbacks
    max_attempts = config.get("max_pipeline_attempts", 15)
    pending_scripts = list(config.get("stock_scripts", [
        "stock-soko.py", 
        "stock-xbeauty.py", 
        "stock-glowin.py"
    ]))

    # Ensure target output directory exists
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)

    print("=== MASTER SCRAPING PIPELINE INITIATED ===")

    # 1. Run main product scraper
    scraper_success = run_script("scraper.py")
    if not scraper_success:
        print("\n[!] Pipeline aborted due to scraper failure.")
        return False

    # 2. Run concurrent stock updates
    print("\n=== STARTING CONCURRENT STOCK UPDATES ===")
    attempt = 1

    while pending_scripts and attempt <= max_attempts:
        print(f"\n--- Batch Run Attempt {attempt}/{max_attempts} ---")
        print(f"Launching {len(pending_scripts)} scripts simultaneously...")

        processes = {}

        # Launch pending scripts concurrently
        # Pull performance configs from scrapper_config.json with safe defaults
        perf = config.get("scraping_performance", {})
        concurrent_tasks = str(perf.get("concurrent_tasks", 5))
        max_retries = str(perf.get("max_retries", 5))

        # Launch pending scripts concurrently with dynamic flags
        for script in pending_scripts:
            script_path = script_dir / script
            p = subprocess.Popen(
                [
                    sys.executable, 
                    str(script_path), 
                    "--tasks", concurrent_tasks, 
                    "--retries", max_retries
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(script_dir)
            )
            processes[script] = p

        still_pending = []

        # Process terminal outputs
        for script, p in processes.items():
            stdout, _ = p.communicate()
            print(f"\n[{script}] Finished. Analyzing output...")
            print("\n".join(stdout.splitlines()[-15:]))

            match = re.search(r"Total remaining to scrape:\s*(\d+)", stdout)

            if p.returncode != 0:
                print(f"[!] {script} crashed. Queuing for retry.")
                still_pending.append(script)
            elif match:
                remaining = int(match.group(1))
                if remaining > 0:
                    print(f"[*] {script} still has {remaining} items left. Queuing for retry.")
                    still_pending.append(script)
                else:
                    print(f"[+] {script} fully complete (0 items left)!")
            else:
                print(f"[?] {script} didn't report remaining items. Assuming complete.")

        pending_scripts = still_pending
        attempt += 1

        if pending_scripts and attempt <= max_attempts:
            print(f"\nWaiting 5 seconds before retrying {len(pending_scripts)} remaining script(s)...")
            time.sleep(5)

    if not pending_scripts:
        print("\n=== SCRAPING PHASE FULLY COMPLETE ===")
        return True
    else:
        print(f"\n[!] Reached max attempts ({max_attempts}). Scripts left incomplete: {pending_scripts}")
        return False


if __name__ == "__main__":
    import sys
    # Allows you to still test this file directly from terminal
    success = run_scraping_pipeline()
    sys.exit(0 if success else 1)