"""
Intake Precompute Service.
==========================
Runs the specialist council + DUR-aware triage the MOMENT an Rx enters the queue,
caching the result on the prescription. When the pharmacist opens the review
screen the analysis is already there — zero wait (your point 1b / Priority 2.1).

Flow (fire-and-forget at intake):
  build patient context  →  convene council (rule-based specialists, incl.
  family-aware hereditary)  →  classify triage lane (green/amber/red with
  immovable hard gates)  →  cache council_cache + triage_lane on the Rx.

Safe: rule-based specialists need no LLM key; failures degrade to status='failed'
and the normal review path still works. Never blocks dispensing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.clinical_brain.council.specialist_council import SpecialistCouncil
from services.ai.clinical_decision_support.interaction.precompute import recompute_for_patient_id
from services.core.pharmacy_workflow.patient_context import load_active_medications_and_diagnoses
from services.core.pharmacy_workflow.triage import ReviewTriageEngine, TriageInput

logger = logging.getLogger(__name__)

# High-alert set mirrors the triage engine; kept local to avoid import cycles.
_CONTROLLED_HINT = ("oxycodone", "hydrocodone", "morphine", "fentanyl", "tramadol",
                    "alprazolam", "diazepam", "clonazepam", "lorazepam", "zolpidem",
                    "methadone", "buprenorphine", "codeine")


class IntakePrecomputeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def run(self, prescription_id: UUID) -> dict:
        """Compute + cache council and triage for one prescription."""
        try:
            await self._set_status(prescription_id, "computing")

            rx = await self._load_rx(prescription_id)
            if not rx:
                await self._set_status(prescription_id, "failed")
                return {"status": "failed", "reason": "rx_not_found"}

            patient_ctx = await self._build_patient_context(rx["patient_id"])

            # ── Council (rule-based specialists + family-aware hereditary) ────
            council = SpecialistCouncil(db=self.db)
            report = await council.convene(
                prescription_id=prescription_id,
                patient_id=UUID(rx["patient_id"]),
                prescription=rx["rx_dict"],
                patient_profile=patient_ctx,
            )
            council_cache = self._report_to_cache(report)

            # ── Triage (DUR + council aware, immovable hard gates) ────────────
            dur_alerts = await self._load_dur_alerts(prescription_id)
            triage = ReviewTriageEngine().classify(self._triage_input(rx, patient_ctx,
                                                                      council_cache, dur_alerts))
            triage_result = {
                "lane": triage.lane.value,
                "reasons": triage.reasons,
                "hard_gates": triage.hard_gates,
                "one_tap_confirm": triage.one_tap_confirm,
                "requires_visual_verification": triage.requires_visual_verification,
                "suggested_priority": triage.suggested_priority,
            }

            await self.db.execute(
                text("""
                    UPDATE prescriptions SET
                      council_cache = CAST(:cc AS JSONB),
                      triage_lane = :lane,
                      triage_result = CAST(:tr AS JSONB),
                      council_computed_at = now(),
                      intake_analysis_status = 'ready',
                      updated_at = now()
                    WHERE id = :id
                """),
                {"cc": json.dumps(council_cache), "lane": triage.lane.value,
                 "tr": json.dumps(triage_result), "id": str(prescription_id)},
            )
            # ── Interaction report (deterministic engine) — precomputed + cached
            # per-patient so the pharmacist's interaction panel renders instantly.
            # Self-isolating: never raises, never blocks the intake analysis.
            await recompute_for_patient_id(
                db=self.db, patient_id=UUID(rx["patient_id"]),
                pharmacy_id=UUID(rx["pharmacy_id"]))

            logger.info("Precompute ready for Rx %s: lane=%s, %d council findings",
                        prescription_id, triage.lane.value, council_cache["total_findings"])
            return {"status": "ready", "lane": triage.lane.value, "council": council_cache}

        except Exception as exc:
            logger.error("Intake precompute failed for %s: %s", prescription_id, exc)
            try:
                await self._set_status(prescription_id, "failed")
            except Exception:
                pass
            return {"status": "failed", "reason": str(exc)}

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _set_status(self, rx_id: UUID, status: str) -> None:
        await self.db.execute(
            text("UPDATE prescriptions SET intake_analysis_status=:s, updated_at=now() WHERE id=:id"),
            {"s": status, "id": str(rx_id)},
        )

    async def _load_rx(self, rx_id: UUID) -> dict | None:
        row = (await self.db.execute(
            text("""SELECT id, patient_id, pharmacy_id, drug_name, drug_strength, sig_text,
                           quantity_prescribed, days_supply, refills_authorized,
                           refills_remaining, dea_schedule, is_controlled, ndc
                    FROM prescriptions WHERE id=:id"""),
            {"id": str(rx_id)})).mappings().first()
        if not row:
            return None
        is_controlled = bool(row["is_controlled"]) or any(
            h in (row["drug_name"] or "").lower() for h in _CONTROLLED_HINT)
        return {
            "patient_id": str(row["patient_id"]),
            "pharmacy_id": str(row["pharmacy_id"]),
            "is_controlled": is_controlled,
            "dea_schedule": row["dea_schedule"],
            "is_first_fill": (row["refills_authorized"] or 0) == (row["refills_remaining"] or 0),
            "rx_dict": {
                "drug_name": row["drug_name"],
                "drug_strength": row["drug_strength"],
                "sig_text": row["sig_text"],
                "quantity": float(row["quantity_prescribed"] or 0),
                "days_supply": row["days_supply"],
                "ndc": row["ndc"],
            },
        }

    async def _build_patient_context(self, patient_id: str) -> dict:
        """Assemble the full patient picture the council reasons over."""
        ctx: dict = {
            "patient_id": patient_id,
            "active_medications": [],
            "diagnoses": [],
            "inherited_conditions": [],
            "allergies": [],
            "labs": {},
            "pharmacogenomics": None,
        }
        ctx.update(await load_active_medications_and_diagnoses(self.db, patient_id))
        # allergies
        try:
            rows = (await self.db.execute(
                text("SELECT allergen_name FROM patient_allergies WHERE patient_id=:id"),
                {"id": patient_id})).mappings().all()
            ctx["allergies"] = [r["allergen_name"] for r in rows]
        except Exception:
            pass
        # labs (eGFR, G6PD, etc.)
        try:
            rows = (await self.db.execute(
                text("""SELECT test_name, value FROM lab_results
                        WHERE patient_id=:id ORDER BY created_at DESC LIMIT 30"""),
                {"id": patient_id})).mappings().all()
            for r in rows:
                key = (r["test_name"] or "").lower()
                if key and key not in ctx["labs"]:
                    ctx["labs"][key] = r["value"]
                    # surface known inherited markers as conditions for the engine
                    if "g6pd" in key and r["value"] and "defic" in str(r["value"]).lower():
                        ctx["inherited_conditions"].append("G6PD deficiency")
        except Exception:
            pass
        return ctx

    async def _load_dur_alerts(self, rx_id: UUID) -> list[dict]:
        try:
            rows = (await self.db.execute(
                text("""SELECT severity, is_hard_stop, was_overridden
                        FROM dur_alerts WHERE prescription_id=:id"""),
                {"id": str(rx_id)})).mappings().all()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _triage_input(self, rx: dict, patient_ctx: dict,
                      council_cache: dict, dur_alerts: list[dict]) -> TriageInput:
        # A stable chronic refill = not first fill + drug already in active meds.
        drug = (rx["rx_dict"].get("drug_name") or "").lower()
        on_profile = any(drug.split()[0] in (m.get("drug_name") or "").lower()
                         for m in patient_ctx["active_medications"]) if drug else False
        return TriageInput(
            drug_name=rx["rx_dict"].get("drug_name", ""),
            is_controlled=rx["is_controlled"],
            dea_schedule=rx["dea_schedule"],
            is_first_fill=rx["is_first_fill"],
            is_refill=not rx["is_first_fill"],
            is_stable_chronic=(not rx["is_first_fill"]) and on_profile,
            known_patient=True,
            dose_in_range=True,
            dur_alerts=dur_alerts,
            council_findings=council_cache.get("all_findings", []),
            pdmp_risk=None,
            claim_status=None,
        )

    def _report_to_cache(self, report) -> dict:
        def ser(findings):
            return [{
                "specialist": f.specialist, "severity": f.severity,
                "message": f.message, "drug_name": f.drug_name,
                "evidence_source": f.evidence_source, "evidence_grade": f.evidence_grade,
            } for f in findings]
        all_findings = (ser(report.blockers) + ser(report.cautions) +
                        ser(report.counseling_points) + ser(report.monitoring_parameters) +
                        ser(report.clarification_prompts) + ser(report.hereditary_flags))
        return {
            "generated_at": report.generated_at,
            "specialists_consulted": report.specialists_consulted,
            "summary": report.council_summary,
            "blockers": ser(report.blockers),
            "cautions": ser(report.cautions),
            "counseling": ser(report.counseling_points),
            "monitoring": ser(report.monitoring_parameters),
            "clarification": ser(report.clarification_prompts),
            "hereditary": ser(report.hereditary_flags),
            "all_findings": all_findings,
            "total_findings": report.total_findings,
            "has_blockers": report.has_blockers,
        }


async def precompute_in_background(prescription_id: str) -> None:
    """
    Background entrypoint — opens its OWN DB session (the request session is gone)
    and runs the precompute. Safe to fire-and-forget from the intake endpoint.
    """
    from services.platform.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            svc = IntakePrecomputeService(db)
            await svc.run(UUID(prescription_id))
            await db.commit()
        except Exception as exc:  # never let a background failure surface
            logger.error("background precompute error %s: %s", prescription_id, exc)
            await db.rollback()
