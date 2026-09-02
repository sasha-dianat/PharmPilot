"""Seed one pharmacy's zones from design doc 3.1.

Idempotent — ON CONFLICT on the business key does nothing — so it is safe in a
provisioning path. The failure mode it prevents is SILENCE: a pharmacy with no
registered zones refuses every zoned observation and reports that nowhere,
which is indistinguishable from a shop where nothing happened.

Every policy column is seeded NULL. Nothing in this repo purges and no schedule
evaluator exists, so a retention_days or armed_schedule value here would be a
number nobody measured sitting on a compliance-facing column.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Design doc 3.1. Codes the doc suffixes with a range (Z-COUNTER-1..n,
# Z-AISLE-A..n, Z-EGRESS-1..n) are seeded with their first instance only; the
# rest are registered per site when the coverage survey names them.
DOC_ZONES: list[dict] = [
    {"site": "pharmacy", "code": "Z-ENT",        "name_fa": "ورودی",                "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-WAIT",       "name_fa": "سالن انتظار",           "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-COUNTER-1",  "name_fa": "پیشخوان ۱",            "zone_class": "service"},
    {"site": "pharmacy", "code": "Z-OTC",        "name_fa": "قفسه‌های OTC",          "zone_class": "public"},
    {"site": "pharmacy", "code": "Z-CDS",        "name_fa": "کابینت داروهای کنترل‌شده", "zone_class": "restricted"},
    {"site": "pharmacy", "code": "Z-COMPOUND",   "name_fa": "اتاق ترکیب",            "zone_class": "restricted"},
    {"site": "pharmacy", "code": "Z-BACKOFFICE", "name_fa": "دفتر پشتی",             "zone_class": "restricted"},
    # "No camera" in the doc. Registered anyway, so an observation naming it is
    # a loud constraint violation rather than a silently unzoned row.
    {"site": "pharmacy", "code": "Z-CONSULT",    "name_fa": "اتاق مشاوره",           "zone_class": "prohibited"},
    {"site": "pharmacy", "code": "Z-STAFFDOOR",  "name_fa": "درب کارکنان",           "zone_class": "credentialed"},
    {"site": "pharmacy", "code": "Z-EGRESS-1",   "name_fa": "مسیر خروج ۱",           "zone_class": "safety"},
    {"site": "depot",    "code": "Z-DOCK",       "name_fa": "بارانداز",              "zone_class": "restricted"},
    {"site": "depot",    "code": "Z-AISLE-A",    "name_fa": "راهرو A",               "zone_class": "restricted"},
    # high_risk drives WH-03's real-time routing in phase 5b. Misclassed, a
    # controlled-substance discrepancy falls into the daily batch instead.
    {"site": "depot",    "code": "Z-CAGE",       "name_fa": "قفس مخدر",              "zone_class": "high_risk"},
    {"site": "depot",    "code": "Z-COLD",       "name_fa": "سردخانه",               "zone_class": "high_risk"},
    {"site": "depot",    "code": "Z-QUARANTINE", "name_fa": "قرنطینه",               "zone_class": "restricted"},
    {"site": "depot",    "code": "Z-STAFFDOOR",  "name_fa": "درب کارکنان انبار",     "zone_class": "credentialed"},
    {"site": "depot",    "code": "Z-EGRESS-1",   "name_fa": "مسیر خروج انبار ۱",     "zone_class": "safety"},
]


async def seed_zones(db: AsyncSession, pharmacy_id) -> int:
    """Register every design-doc zone for one pharmacy. Returns how many."""
    for z in DOC_ZONES:
        await db.execute(text("""
            INSERT INTO vision_zone
                (pharmacy_id, site, code, name_fa, zone_class,
                 retention_days, armed_schedule)
            VALUES (:pid, :site, :code, :fa, :klass, NULL, NULL)
            ON CONFLICT (pharmacy_id, site, code) DO NOTHING"""),
            {"pid": pharmacy_id, "site": z["site"], "code": z["code"],
             "fa": z["name_fa"], "klass": z["zone_class"]})
    return len(DOC_ZONES)
