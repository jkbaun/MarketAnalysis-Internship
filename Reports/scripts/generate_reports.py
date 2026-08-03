"""
generate_reports.py
===================
Master orchestration runner for Phase 5 reporting modules:
  1. internal_inventory.py
  2. market_analytics.py
  3. market_compare.py (Dynamic execution per competitor)
  4. executive_master.py
"""

import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ReportOrchestrator")

SCRIPT_DIR = Path(__file__).resolve().parent  # Pipeline/Reports/scripts

def find_pipeline_root(start_dir: Path) -> Path:
    """Walks up parent directories to locate the root 'Pipeline' directory."""
    for parent in [start_dir] + list(start_dir.parents):
        if (parent / "orchestrate.py").exists() or (parent / "Data" / "databases").exists():
            return parent
    return start_dir.parents[1] if len(start_dir.parents) >= 2 else start_dir

PIPELINE_ROOT = find_pipeline_root(SCRIPT_DIR)
DEFAULT_CONFIG_PATH = PIPELINE_ROOT / "reports_config.json"


def load_config(config_path: Path) -> dict:
    """Loads reports_config.json settings."""
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse config at {config_path}: {e}")

    # Default fallback configuration
    return {
        "competitors": ["glowin", "sokostore", "xbeauty"],
        "reports": {
            "internal_inventory": True,
            "market_analytics": True,
            "competitor_comparison": True,
            "executive_summary": True
        }
    }


def run_sub_report(script_name: str, args: list = None, description: str = "") -> bool:
    """Helper to run a sub-report script via subprocess."""
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        logger.warning(f"[SKIP] Module script missing: {script_path}")
        return False

    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    print(f"\n----------------------------------------------")
    logger.info(f"Executing Module: {description}")
    logger.info(f"Command: {' '.join(cmd)}")
    print(f"----------------------------------------------")

    try:
        subprocess.run(cmd, check=True, cwd=str(SCRIPT_DIR))
        logger.info(f"[SUCCESS] {description} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[ERROR] {description} failed with exit code {e.returncode}")
        return False


def run_reports_pipeline(config_path: Path):
    """Executes all Phase 5 report generation modules."""
    print("\n==============================================")
    print("[PIPELINE] Phase 5: Automated Final Report Generation")
    print("Running Master Runner: generate_reports.py")
    print("==============================================")

    config = load_config(config_path)
    reports_cfg = config.get("reports", {})
    competitor_list = config.get("competitors", ["glowin", "sokostore", "xbeauty"])

    completed = 0
    total = 0

    # 1. Internal Inventory Analysis
    if reports_cfg.get("internal_inventory", True) or reports_cfg.get("inventory_audit", True):
        total += 1
        if run_sub_report("internal_inventory.py", description="Internal Inventory Audit & Stock Valuation"):
            completed += 1

    # 2. Market Analytics & Category Trends
    if reports_cfg.get("market_analytics", True):
        total += 1
        if run_sub_report("market_analytics.py", description="Macro Market Analytics"):
            completed += 1

    # 3. Dynamic Competitor Comparisons (Loop over Glowin, Sokostore, XBeauty, etc.)
    if reports_cfg.get("competitor_comparison", True) or reports_cfg.get("competitor_price_stock", True):
        for company in competitor_list:
            total += 1
            desc = f"Market Comparison vs. {company.title()}"
            if run_sub_report("market_compare.py", args=["--company", company], description=desc):
                completed += 1

    # 4. Executive Summary KPI Report
    if reports_cfg.get("executive_summary", True):
        total += 1
        if run_sub_report("executive_master.py", description="Executive Master Summary Report"):
            completed += 1

    print("\n==============================================")
    print(f"[COMPLETED] Phase 5 Report Suite Execution Finished ({completed}/{total} Modules Successful)")
    print("==============================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BBK Master Report Suite Orchestrator")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to reports_config.json")
    args = parser.parse_args()

    run_reports_pipeline(config_path=args.config)