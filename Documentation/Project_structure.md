Pipeline/
│
├── Inventory/
│   ├── Data/
│   │   ├── exports/
│   │   │       ├── products_{date}.csv
│   │   │       ├── exports_{date}.csv
│   │   │       └── invoice_{date}.csv
│   │   │
│   │   └── master_database.db
│   │   
│   ├── Database_Builder/
│   │   ├── app/
│   │   │       ├── static/
│   │   │       │       ├── script.js
│   │   │       │       └── styles.css
│   │   │       │
│   │   │       ├── templates/
│   │   │       │        ├── invoice.html
│   │   │       │        └── variant_manager.html
│   │   │       │
│   │   │       ├── AI_CONTEXT.md
│   │   │       └── app.py
│   │   │
│   │   ├── core/
│   │   │   ├── database.py
│   │   │   └── models.py
│   │   │   
│   │   ├── ingestion/
│   │   │        ├── AI_CONTEXT.MD
│   │   │        ├── ingest_products.py
│   │   │        ├── ingest_orders.py
│   │   │        ├── ingest_products.py
│   │   │        └── link_lineitems.py
│   │   │
│   │   └── main.py
│   │
│   └── Database_Report/
│           ├── AI_CONTEXT.md
│           └── Custom_Reports.py
│
├── Competition/
│   ├── 1_SCRAPE/
│   │   ├── AI_CONTEXT.md
│   │   ├── helper.py
│   │   ├── master.py
│   │   ├── scraper.py
│   │   └── stock_{CompetitorName}.py
│   │   
│   ├── 2_UNIFY/
│   │   ├── AI_CONTEXT.md
│   │   ├── scrape_to_stage.py
│   │   ├── build_brand_master.py
│   │   └── build_product_master.py
│   │  
│   │   
│   ├── 3_ANALYZE/
│   │   ├── AI_CONTEXT.md
│   │   └── Custom_marketanalysis.py
│   │
│   └── Data/
│   │   ├── databases/
│   │   ├── scrapped_json/
│   │   └── central_master.db 
│
├── Documentation/
│
└── requirements.txt