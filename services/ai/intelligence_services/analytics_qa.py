"""
#3 — Natural Language Analytics ("Ask Your Data")  (offline-first)
==================================================================
Plain-language questions → SQL → narrative answer over a CURATED, read-only,
NON-PHI schema view of the analytics tables.

LOCAL BRAIN (always available — Ollama text-to-SQL):
  • The local LLM is handed ONLY a compact, curated schema (no patient names, DOB,
    national IDs, addresses — those columns are never exposed).
  • It returns a single SELECT. A hardened SQLGuard then validates it before any
    execution.
  • Rows are aggregated server-side; the local LLM writes a 1–2 sentence narrative.

CLOUD BRAIN (when online — richer reasoning):
  • Cloud LLM (Claude/GPT) handles complex multi-join / ambiguous questions and
    writes a fuller narrative. Offline → local model answers common questions with
    degraded=true.

SECURITY (non-negotiable):
  • SELECT-only. Single statement. Whitelisted tables/columns only. No comments,
    no DDL/DML keywords, no multiple statements. A LIMIT is always enforced.
  • The LLM sees the SCHEMA, never the ROWS. SQL executes on the server; only
    aggregated results are returned.
  • Queries are scoped to the caller's pharmacy via a bound :pharmacy_id param.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, generate,
)

logger = logging.getLogger(__name__)

SERVICE = "analytics_qa"

DEFAULT_LIMIT = 100
MAX_LIMIT     = 1000

# ─── Curated, NON-PHI schema exposed to the LLM ───────────────────────────────
# Only aggregate-friendly, non-identifying columns. Patient/prescriber direct
# identifiers and demographics are deliberately excluded.

CURATED_SCHEMA: dict[str, list[str]] = {
    "prescriptions": [
        "ndc", "drug_name", "drug_strength", "is_controlled", "dea_schedule",
        "status", "source", "days_supply", "quantity_dispensed",
        "refills_remaining", "written_date", "fill_date", "created_at", "pharmacy_id",
    ],
    "claim_transactions": [
        "ndc", "bin_number", "status", "total_amount_paid", "patient_pay_amount",
        "ingredient_cost_paid", "dispensing_fee_paid", "quantity", "days_supply",
        "date_of_service", "created_at", "pharmacy_id",
    ],
    "payment_events": [
        "tender_type", "patient_pay", "amount_tendered", "change_due",
        "waiver_reason", "created_at",
    ],
    "inventory_lots": [
        "ndc11", "quantity_on_hand", "unit_cost", "expiry_date",
        "is_recalled", "is_quarantined", "received_at", "pharmacy_id",
    ],
    "drug_products": [
        "ndc11", "generic_name", "brand_name", "is_generic", "is_controlled",
        "dea_schedule",
    ],
    "dur_override_events": [
        "alert_type", "reason_code", "overridden_by", "created_at",
    ],
}

ALLOWED_TABLES = set(CURATED_SCHEMA.keys())

# Hard-forbidden tokens anywhere in the generated SQL.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"attach|copy|merge|replace|pragma|vacuum|exec|execute|call|"
    r"into|set)\b",
    re.IGNORECASE,
)
# PHI / identifier columns that must never be selected even if a table exposes them.
_PHI_COLUMNS = re.compile(
    r"\b(patient_id|prescriber_id|member_id|national_id|ssn|ssn_last4|"
    r"first_name|last_name|father_name|date_of_birth|dob|email|phone|"
    r"phone_primary|phone_secondary|address|address_line1|address_line2|zip_code)\b",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    ok:      bool
    sql:     str
    reason:  str = ""


class SQLGuard:
    """Validate + harden an LLM-generated SQL string before execution."""

    @staticmethod
    def sanitize(raw_sql: str) -> GuardResult:
        if not raw_sql or not raw_sql.strip():
            return GuardResult(False, "", "Empty SQL.")

        sql = raw_sql.strip()

        # Strip code fences / markdown the LLM may add.
        sql = re.sub(r"^```(?:sql)?", "", sql, flags=re.IGNORECASE).strip()
        sql = re.sub(r"```$", "", sql).strip()

        # Take only the first statement; reject if multiple non-trailing semicolons.
        parts = [p for p in sql.split(";") if p.strip()]
        if len(parts) > 1:
            return GuardResult(False, "", "Multiple statements are not allowed.")
        sql = parts[0].strip() if parts else sql.rstrip(";").strip()

        # No SQL comments.
        if "--" in sql or "/*" in sql:
            return GuardResult(False, "", "SQL comments are not allowed.")

        # Must start with SELECT or WITH (CTE) … SELECT.
        if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
            return GuardResult(False, "", "Only SELECT queries are allowed.")

        # Forbidden keywords.
        if _FORBIDDEN.search(sql):
            return GuardResult(False, "", "Query contains a forbidden keyword.")

        # PHI columns.
        if _PHI_COLUMNS.search(sql):
            return GuardResult(False, "", "Query references a protected/identifying column.")

        # Every referenced table (after FROM / JOIN) must be whitelisted.
        referenced = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        for tbl in referenced:
            if tbl.lower() not in ALLOWED_TABLES:
                return GuardResult(False, "", f"Table '{tbl}' is not permitted.")

        # Enforce a LIMIT by wrapping (guarantees a cap even on aggregates).
        if not re.search(r"\blimit\b", sql, re.IGNORECASE):
            sql = f"SELECT * FROM (\n{sql}\n) AS _capped LIMIT {DEFAULT_LIMIT}"
        else:
            # Clamp an existing LIMIT to MAX_LIMIT.
            def _clamp(m):
                n = min(int(m.group(1)), MAX_LIMIT)
                return f"LIMIT {n}"
            sql = re.sub(r"\blimit\s+(\d+)", _clamp, sql, flags=re.IGNORECASE)

        return GuardResult(True, sql)


def _schema_prompt() -> str:
    lines = ["You may query ONLY these tables and columns (PostgreSQL):"]
    for tbl, cols in CURATED_SCHEMA.items():
        lines.append(f"  {tbl}({', '.join(cols)})")
    lines.append("")
    lines.append("Rules: output ONE SELECT statement only. No comments. "
                 "Always filter by pharmacy_id = :pharmacy_id when the table has it. "
                 "Prefer aggregates (COUNT, SUM, AVG) and GROUP BY. "
                 "Return ONLY the SQL, nothing else.")
    return "\n".join(lines)


async def _generate_sql(question: str, prefer_local: bool) -> tuple[str, str, bool]:
    """Ask the LLM for SQL. Returns (sql, provider_tier, degraded)."""
    system = ("You are a careful analytics assistant for a pharmacy. "
              "Translate the user's question into a single safe PostgreSQL SELECT. "
              + _schema_prompt())
    res = await generate(question, system=system, max_tokens=300, temperature=0.0,
                         prefer_local=prefer_local, phi=False, task="general")
    return res.text.strip(), ("local" if res.tier.value == "local" else "cloud"), res.degraded


async def _narrate(question: str, rows: list[dict], prefer_local: bool) -> str:
    """Have the LLM write a 1–2 sentence answer from the aggregated rows."""
    if not rows:
        return "No matching records were found for that question."
    # Cap rows passed to the narrator (these are aggregates, non-PHI).
    sample = rows[:20]
    system = ("Answer the user's question in 1-2 concise sentences using ONLY the data rows. "
              "Include the key numbers. Do not invent values.")
    prompt = f"Question: {question}\n\nData rows (JSON): {sample}\n\nAnswer:"
    res = await generate(prompt, system=system, max_tokens=160, temperature=0.1,
                         prefer_local=prefer_local, phi=False, task="summarize")
    return res.text.strip() or "Here are the results."


def _chartable(rows: list[dict]) -> Optional[dict]:
    """If rows look like (label, number), return chart-ready data."""
    if not rows or len(rows) < 2:
        return None
    cols = list(rows[0].keys())
    if len(cols) != 2:
        return None
    label_col, value_col = cols
    try:
        data = [{"label": str(r[label_col]), "value": float(r[value_col])} for r in rows[:20]]
    except (TypeError, ValueError):
        return None
    return {"label_key": label_col, "value_key": value_col, "data": data}


async def ask(
    db: AsyncSession,
    pharmacy_id: str,
    question: str,
    *,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Main entry — §1.2 envelope with {answer, sql, rows, chart}."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    prefer_local = (tier == Tier.LOCAL)

    options_active  = ["local_text_to_sql"]
    options_offline = ["cloud_reasoning"] if tier == Tier.LOCAL else []
    degraded = (tier == Tier.LOCAL)

    # 1. Generate SQL.
    try:
        raw_sql, sql_tier, gen_degraded = await _generate_sql(question, prefer_local)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[analytics_qa] sql generation failed (%s)", exc)
        return build_envelope(
            {"answer": "I couldn't reach the language model to interpret that question.",
             "sql": None, "rows": [], "chart": None},
            tier_used=Tier.LOCAL, confidence=0.0, degraded=True,
            options_active=[], options_offline=["local_text_to_sql", "cloud_reasoning"],
            model_version="analytics_qa_v1",
        )
    if gen_degraded:
        degraded = True
        if "cloud_reasoning" not in options_offline:
            options_offline.append("cloud_reasoning")

    # 2. Guard the SQL.
    guard = SQLGuard.sanitize(raw_sql)
    if not guard.ok:
        return build_envelope(
            {"answer": f"I could not run that safely: {guard.reason} "
                       "Try rephrasing, e.g. 'total revenue by drug last month'.",
             "sql": raw_sql, "rows": [], "chart": None, "blocked": True, "block_reason": guard.reason},
            tier_used=Tier(sql_tier), confidence=0.3, degraded=degraded,
            options_active=options_active, options_offline=options_offline,
            model_version="analytics_qa_v1",
        )

    # 3. Execute (read-only), scoped by pharmacy.
    try:
        result = await db.execute(text(guard.sql), {"pharmacy_id": pharmacy_id, "limit": DEFAULT_LIMIT})
        rows = [dict(r) for r in result.mappings().all()]
        # Coerce non-JSON types to strings for safe transport.
        rows = [{k: (float(v) if hasattr(v, "is_integer") else
                     str(v) if not isinstance(v, (int, float, bool, type(None))) else v)
                 for k, v in row.items()} for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[analytics_qa] execution failed (%s)", exc)
        return build_envelope(
            {"answer": "That query couldn't be executed against the database. Try rephrasing.",
             "sql": guard.sql, "rows": [], "chart": None, "error": str(exc)[:200]},
            tier_used=Tier(sql_tier), confidence=0.2, degraded=degraded,
            options_active=options_active, options_offline=options_offline,
            model_version="analytics_qa_v1",
        )

    # 4. Narrate + chart.
    try:
        answer = await _narrate(question, rows, prefer_local)
    except Exception:  # noqa: BLE001
        answer = f"Returned {len(rows)} row(s)."
    chart = _chartable(rows)

    if sql_tier == "cloud" and not degraded:
        options_active.append("cloud_reasoning")
        tier = Tier.HYBRID

    confidence = 0.78 if not degraded else 0.6
    return build_envelope(
        {"answer": answer, "sql": guard.sql, "rows": rows[:DEFAULT_LIMIT], "chart": chart,
         "row_count": len(rows)},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="analytics_qa_v1",
    )
