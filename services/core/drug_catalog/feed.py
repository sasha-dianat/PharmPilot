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
    """[{irc, name_fa?, announced_price?, last_invoice_price?}, ...] or [].

    Sources, in order: a JSON HTTP endpoint (PRICE_FEED_URL — this is where the
    real NFI/IRC or distributor client plugs in) then a local JSON file
    (PRICE_FEED_PATH — offline/testing). Returns [] when neither is configured or
    reachable, so the sync simply produces no proposals.
    """
    url = os.getenv("PRICE_FEED_URL")
    if url:
        rows = await _fetch_url(url)
        if rows:
            return rows

    path = os.getenv("PRICE_FEED_PATH")
    if path and Path(path).exists():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


async def _fetch_url(url: str) -> list[dict]:
    """GET a JSON list of price rows. Auth headers for the real NFI client go via
    PRICE_FEED_AUTH (sent as Authorization) when present."""
    try:
        import httpx
    except Exception:
        return []
    headers = {}
    token = os.getenv("PRICE_FEED_AUTH")
    if token:
        headers["Authorization"] = token
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        return []
