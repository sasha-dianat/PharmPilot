"""
Self-service clinical-knowledge ingestion CLI.

Pulls FDA SPL sections (interactions, contraindications, precautions,
pharmacokinetics — defined in config/kb_ingest.json) into the Second Brain's
Qdrant store. The owner customizes WHAT is ingested by editing the JSON config;
no code changes required.

Examples:
    # ingest the standard top-drug set from the config
    python scripts/ingest_spl.py

    # ingest only what the pharmacy stocks/dispenses (reads the DB)
    DATABASE_URL=... python scripts/ingest_spl.py --scope formulary

    # ingest a specific list, capped
    python scripts/ingest_spl.py --scope list --drugs warfarin,metformin,apixaban

    # use a custom config and skip the curated DDI pairs
    python scripts/ingest_spl.py --config config/kb_ingest.json --no-ddi --limit 20
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])


def main() -> None:
    from services.ai.knowledge_engine import spl_ingestor as S

    ap = argparse.ArgumentParser(description="Ingest FDA SPL clinical sections into the RAG store")
    ap.add_argument("--config", default=None, help="path to kb_ingest.json (default: config/kb_ingest.json)")
    ap.add_argument("--scope", choices=["top", "list", "formulary"], default=None,
                    help="override scope.mode in the config")
    ap.add_argument("--drugs", default=None, help="comma-separated generic names (with --scope list)")
    ap.add_argument("--limit", type=int, default=None, help="cap number of drugs")
    ap.add_argument("--no-ddi", action="store_true", help="skip curated drug-interaction pairs")
    ap.add_argument("--bulk-dir", default=None,
                    help="ingest the openFDA/DailyMed BULK dataset from this dir of "
                         "*.json/*.json.zip files (deduped by generic). Heavy — prefer a GPU/Colab.")
    args = ap.parse_args()

    cfg = S.load_config(args.config)
    if args.scope:
        cfg["scope"]["mode"] = args.scope
    if args.drugs:
        cfg["scope"]["drugs"] = [d.strip() for d in args.drugs.split(",") if d.strip()]
    if args.limit is not None:
        cfg["scope"]["limit"] = args.limit
    if args.no_ddi:
        cfg["include_curated_ddi"] = False

    print("Sections to ingest:", ", ".join(f"{s['domain']}" for s in cfg["sections"]))

    if args.bulk_dir:
        # Optional formulary restriction when --scope formulary/list is also given.
        only = None
        if args.scope in ("formulary", "list"):
            only = {g.lower() for g in S.resolve_scope(cfg, db_url=os.environ.get("DATABASE_URL"))}
            print(f"Bulk restricted to {len(only)} formulary generics")
        summary = S.ingest_bulk_dir(cfg, args.bulk_dir, limit=args.limit, only_generics=only)
    else:
        summary = S.ingest(cfg, db_url=os.environ.get("DATABASE_URL"))
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        if k == "skipped":
            print(f"  skipped ({len(v)}): {', '.join(v) if v else 'none'}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
