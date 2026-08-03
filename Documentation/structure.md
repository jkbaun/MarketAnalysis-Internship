Pipeline/
│
├── data/                             
│   ├── raw/                          
│   │   ├── inventory/
│   │   │   └── exports_{date}.csv
│   │   └── competition/
│   │       └── raw_{CompetitorName}_{date}.json
│   │
│   ├── processed/                    
│   │   └── unified_products.db
│   │
│   ├── exports/                     
│   │   ├── invoices/
│   │   └── reports/
│   │
│   └── db/                           
│       └── master_database.db
│
├── src/                             
│   ├── inventory/
│   │   ├── app/                      
│   │   │   ├── static/
│   │   │   ├── templates/
│   │   │   └── app.py
│   │   │
│   │   ├── core/                     
│   │   │   ├── database.py
│   │   │   └── models.py
│   │   │
│   │   ├── ingestion/                
│   │   │   ├── ingest_products.py
│   │   │   ├── ingest_orders.py
│   │   │   └── link_lineitems.py
│   │   │
│   │   └── reporting/
│   │       └── custom_reports.py
│   │
│   └── competition/
│       ├── 1_scrape/
│       ├── 2_unify/
│       └── 3_analyze/
│
├── config/                           
│   └── settings.py
│
├── docs/
├── .gitignore                        
└── requirements.txt