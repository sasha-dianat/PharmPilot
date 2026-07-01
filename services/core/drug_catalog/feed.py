"""Pluggable daily price feed.

Returns the day's price rows (announced and/or invoice) for the sync to diff
against the catalog. The real source is the NFI/IRC API (needs an authorized
'Web Information Services' credential) or an official price-list export; until
that's wired, this reads an optional JSON file at PRICE_FEED_PATH so the daily
job and the manual 'Run sync' button work end-to-end. Swap this one function for
the real client when credentialed — nothing else changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


async def fetch_daily_feed() -> list[dict]:
    """[{irc, name_fa?, announced_price?, last_invoice_price?}, ...] or []."""
    path = os.getenv("PRICE_FEED_PATH")
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []
