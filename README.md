Here is the raw Markdown format so you can easily copy and paste it directly into your project.

```markdown
# 🛒 E-Commerce Intelligence & Inventory Orchestrator

An end-to-end automated pipeline designed for e-commerce data scraping, inventory reconciliation, entity resolution, and executive reporting. This system coordinates multi-competitor price/stock tracking, parses internal sales orders and supplier invoice PDFs, unifies databases using fuzzy matching, and generates automated financial and inventory audit reports.

---

## 📌 Critical System Requirements

> [!CAUTION]
> **STRICT CONFIGURATION RULES:**
> 1. **DO NOT** change `scrapper_config.json` or `reports_config.json` filenames. The orchestrator relies on these exact filenames.
> 2. **DO NOT** execute scripts outside the root working directory. All relative paths are mapped from the orchestrator directory.

---

## 🚀 Usage

Run the master pipeline orchestrator directly from the root directory:

```bash
python orchestrate.py

```

Upload Orders and Products export data to Data/products_orders
Add export filenames + data to reports_config.json (Invoices if Applicable)

If focused on external competitor data, toggle `audit_inventory` in `reports_config.json` to false
Adjust Scrapping speed in `scrapper_config.json`

---
## 🔄 Execution Flow & Pipeline Phases

The orchestrator executes a 5-phase sequential data processing pipeline:

```text
[Phase 1: Scraping] ──► [Internal DB Audit] ──► [Phase 2: Staging] ──► [Phase 3: Brand Match] ──► [Phase 4: Product Waterfall] ──► [Phase 5: Reporting]

```

1. **Phase 1: Competitor Web Scraping (`master-v2.py`)**
* Automatically executes if it hasn't run today or if `rerun` is forced to `true` in `scrapper_config.json`.
* Leverages `aiohttp` and Playwright headless Chromium instances to extract target product catalogs across competitors (`sokostore`, `xbeauty`, `glowin`).


2. **Internal Inventory Audit (`run_pipeline`)**
* Parses customer order histories (CSV) and supplier invoice PDFs (`pdfplumber`).
* Builds and updates the primary SQLite database (`beautybykat_inventory.db`).


3. **Phase 2: Staging Database Ingestion (`scrape_to_stage-v1.py`)**
* Normalizes raw competitor scraped JSON data into structured staging tables.


4. **Phase 3: Brand Master Resolution (`build_brand_master.py`)**
* Resolves entity naming discrepancies across disparate market datasets using `RapidFuzz`.


5. **Phase 4: Product Master Waterfall & Stock Migration (`build_product_master.py`)**
* Performs product-level matching, stock level updates, and historical inventory migrations.


6. **Phase 5: Automated Final Report Generation (`generate_reports.py`)**
* Generates formatted Excel (`.xlsx`) and CSV reports including Financial Summaries, Stock Migration Audits, Low Stock Alerts, and Executive Summaries.



---

## ⚙️ Setup & Installation

### 1. Prerequisites

* **Python 3.10+**
* **Chromium Browser** (managed via Playwright)

### 2. Environment Setup

Clone the repository and set up a virtual environment:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

```

### 3. Install Dependencies & Playwright

```bash
pip install -r requirements.txt
playwright install chromium

```

---

## 🛠️ Configuration Guide

### `scrapper_config.json`

Controls scraper execution, target endpoints, rate limits, and run history.

* **`rerun`**: Set to `"true"` to force re-scraping on the same day. Set to `"false"` for automated daily deduplication.
* **`targets.competitor_urls`**: List of target endpoint URLs to extract products from.
* **`anti_bot_and_delays`**: Adjust request jitter, cooldown timers, and User-Agent rotation thresholds.

### `reports_config.json`

Manages file ingestion inputs and audit parameters.

* **`audit_inventory`**: Toggle inventory pipeline execution (`true`/`false`).
* **`order_files`**: Specify raw CSV order files located in `Data/exports/products_orders/`.
* **`invoice_files`**: Define invoice PDFs in `Data/exports/invoice/` with metadata (`invoice_number`, `date`).
* **`product_files`**: Historical snapshot product files mapped with dates.

---

### Fault Tolerance & Safety

* **Database Corruption Prevention**: If any pipeline phase fails, the orchestrator triggers a `CRITICAL ERROR` alert and gracefully terminates process execution (`sys.exit(1)`), preventing downstream data corruption.
* **Automated Logging**: Tracks execution timestamps in `scrapper_config.json` to prevent unnecessary repeated scraping requests.

---

## 🧰 Built With

* **[Pandas](https://pandas.pydata.org/)** – Data manipulation & ETL transformations
* **[SQLAlchemy](https://www.sqlalchemy.org/)** – ORM database interaction with SQLite
* **[RapidFuzz](https://www.google.com/search?q=https://github.com/maxbachmann/RapidFuzz)** – High-performance fuzzy matching for brand/product entity resolution
* **[Playwright](https://playwright.dev/python/) & [aiohttp**](https://docs.aiohttp.org/) – Headless browser automation and asynchronous HTTP requests
* **[pdfplumber](https://github.com/jsvine/pdfplumber)** – Automated PDF text & table extraction for supplier invoices
* **[OpenPyXL](https://openpyxl.readthedocs.io/) & [XlsxWriter**](https://xlsxwriter.readthedocs.io/) – Styled Excel report creation

```

```