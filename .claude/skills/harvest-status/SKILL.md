---
name: harvest-status
description: One-shot status of the national drug-data pipeline — NFI crawl progress, catalog size/pricing/coverage counts, pending price proposals, and the concrete next data step.
---

# Harvest Status

Report the live state of PharmPilot's Iranian drug-data pipeline:

1. **Crawl**: `wc -l nfi_pages.jsonl` (harvested products) and whether
   `ps aux | grep "[h]arvest_nfi.py crawl"` shows a running crawler; last nfi_id
   in the file tail vs the 60000 target → % complete.
2. **GUI harvest**: GET /api/v1/pricing/catalog/nfi/status (admin login
   PharmPilot2024!) — running/scanned/products/ingested/ETA.
3. **Catalog**: GET /api/v1/pricing/catalog/stats — total / priced /
   ingredient_groups / last_updated. Cross-check with
   `PGPASSWORD=change_in_production psql -h 127.0.0.1 -p 5433 -U pharmpilot -d pharmpilot -t -c "SELECT count(*), count(coverage) FROM drug_catalog;"`
4. **Coverage**: per-insurer counts —
   `SELECT key, count(*) FROM drug_catalog, jsonb_object_keys(coverage) key GROUP BY key;`
5. **Price proposals**: pending count via GET /api/v1/pricing/proposals.
6. **Gap analysis**: jsonl lines vs DB rows → un-ingested backlog; crawl ID gap
   → remaining crawl.

End with exactly one recommended next action (e.g. "ingest the backlog:
DATABASE_URL=… python scripts/harvest_nfi.py ingest --feed nfi_pages.jsonl",
or "resume crawl", or "upload دارونامه for salamat").
