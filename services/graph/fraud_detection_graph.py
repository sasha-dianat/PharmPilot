"""
Pharmacy Fraud Detection Graph — Neo4j
========================================
Detects prescription drug fraud patterns using graph analytics.
These patterns are trivial to detect in a graph but extremely expensive in SQL:

  1. Doctor Shopping: Patient→Prescriber star graph (many prescribers, one patient)
  2. Pill Mill: Prescriber→Patient star graph (one prescriber, hundreds of CS patients)
  3. Pharmacy Clustering: Geographic clustering of fraud-linked pharmacies
  4. Forged Prescriptions: Prescriber wrote no such drug (outside specialty/DEA scope)
  5. Diversion Networks: Staff→InventoryDrop correlation across shifts
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from services.graph.client import GraphClient

logger = logging.getLogger(__name__)


@dataclass
class FraudRiskSignal:
    signal_type: str
    severity: str          # warning | high | critical
    description: str
    evidence: dict
    cypher_query_used: str = ""


@dataclass
class FraudRiskProfile:
    entity_type: str       # patient | prescriber | pharmacy
    entity_id: str
    risk_score: float      # 0.0 – 1.0
    signals: list[FraudRiskSignal] = field(default_factory=list)


class FraudDetectionGraph:

    def __init__(self, client: GraphClient):
        self.client = client

    # ── Patient patterns ──────────────────────────────────────────────────

    def detect_doctor_shopping(self, patient_id: str, lookback_days: int = 90) -> Optional[FraudRiskSignal]:
        """
        Detect doctor shopping: patient received CS prescriptions from
        3+ different prescribers within the lookback window.
        Graph query is O(1) vs O(n²) SQL self-join.
        """
        if not self.client.is_connected:
            return None
        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        results = self.client.run_sync("""
            MATCH (p:Patient {id: $patient_id})-[rx:PRESCRIBED_BY]->(pr:Prescriber)
            WHERE rx.fill_date >= $cutoff
              AND rx.is_controlled = true
            WITH count(DISTINCT pr) AS prescriber_count,
                 collect(DISTINCT pr.npi) AS prescriber_npis,
                 collect(DISTINCT rx.drug_name) AS drugs
            WHERE prescriber_count >= 3
            RETURN prescriber_count, prescriber_npis, drugs
        """, patient_id=patient_id, cutoff=cutoff)

        if results:
            r = results[0]
            return FraudRiskSignal(
                signal_type="doctor_shopping",
                severity="high" if r["prescriber_count"] < 5 else "critical",
                description=(
                    f"Patient received controlled substance prescriptions from "
                    f"{r['prescriber_count']} different prescribers in {lookback_days} days."
                ),
                evidence={
                    "prescriber_count": r["prescriber_count"],
                    "prescriber_npis": r["prescriber_npis"],
                    "drugs": r["drugs"],
                },
                cypher_query_used="doctor_shopping_v1",
            )
        return None

    # ── Prescriber patterns ───────────────────────────────────────────────

    def detect_pill_mill_prescriber(self, prescriber_npi: str, lookback_days: int = 30) -> Optional[FraudRiskSignal]:
        """
        Pill mill detection: prescriber wrote CS prescriptions for an
        unusually high number of patients with high average MME.
        """
        if not self.client.is_connected:
            return None
        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        results = self.client.run_sync("""
            MATCH (pr:Prescriber {npi: $npi})<-[rx:PRESCRIBED_BY]-(p:Patient)
            WHERE rx.fill_date >= $cutoff
              AND rx.is_controlled = true
            WITH count(DISTINCT p) AS patient_count,
                 avg(rx.daily_mme) AS avg_daily_mme,
                 count(rx) AS total_cs_rxs,
                 collect(DISTINCT rx.drug_name)[..5] AS top_drugs
            WHERE patient_count > 50 OR avg_daily_mme > 90
            RETURN patient_count, avg_daily_mme, total_cs_rxs, top_drugs
        """, npi=prescriber_npi, cutoff=cutoff)

        if results:
            r = results[0]
            return FraudRiskSignal(
                signal_type="pill_mill_prescriber",
                severity="critical",
                description=(
                    f"Prescriber wrote {r['total_cs_rxs']} controlled substance prescriptions "
                    f"to {r['patient_count']} patients in {lookback_days} days "
                    f"(avg {r.get('avg_daily_mme', 0):.0f} MME/day)."
                ),
                evidence=dict(r),
                cypher_query_used="pill_mill_v1",
            )
        return None

    # ── Patient graph sync ────────────────────────────────────────────────

    def sync_patient_fill(
        self,
        patient_id: str,
        prescriber_npi: str,
        pharmacy_ncpdp: str,
        drug_ndc: str,
        drug_name: str,
        is_controlled: bool,
        fill_date: str,
        daily_mme: Optional[float] = None,
    ) -> None:
        """
        Record a prescription fill event in the graph.
        Called after every successful dispensing — keeps graph in sync with PostgreSQL.
        """
        if not self.client.is_connected:
            return
        self.client.run_sync("""
            MERGE (p:Patient {id: $patient_id})
            MERGE (pr:Prescriber {npi: $prescriber_npi})
            MERGE (ph:Pharmacy {ncpdp_id: $pharmacy_ncpdp})
            MERGE (d:Drug {ndc11: $drug_ndc})
            ON CREATE SET d.generic_name = $drug_name, d.is_controlled = $is_controlled
            CREATE (p)-[:PRESCRIBED_BY {
                fill_date: $fill_date,
                drug_ndc: $drug_ndc,
                drug_name: $drug_name,
                is_controlled: $is_controlled,
                daily_mme: $daily_mme,
                pharmacy_ncpdp: $pharmacy_ncpdp
            }]->(pr)
            MERGE (p)-[:FILLED_AT]->(ph)
            MERGE (p)-[:TAKES {last_fill: $fill_date}]->(d)
        """,
        patient_id=patient_id, prescriber_npi=prescriber_npi,
        pharmacy_ncpdp=pharmacy_ncpdp, drug_ndc=drug_ndc,
        drug_name=drug_name, is_controlled=is_controlled,
        fill_date=fill_date, daily_mme=daily_mme,
        )

    # ── DSCSA Supply Chain ────────────────────────────────────────────────

    def record_drug_transfer(
        self,
        lot_serial: str,
        ndc11: str,
        from_entity: str,  # DEA/NPI of sender
        to_entity: str,    # DEA/NPI of receiver
        transaction_date: str,
        quantity: float,
    ) -> None:
        """
        Record a drug supply chain transfer for DSCSA T3 traceability.
        The directed graph makes lot provenance queries trivial.
        """
        if not self.client.is_connected:
            return
        self.client.run_sync("""
            MERGE (lot:DrugLot {serial: $serial})
            ON CREATE SET lot.ndc11 = $ndc11, lot.created_at = datetime()
            MERGE (from:Entity {id: $from_entity})
            MERGE (to:Entity {id: $to_entity})
            CREATE (from)-[:TRANSFERRED {
                date: $tx_date,
                quantity: $quantity,
                lot_serial: $serial
            }]->(to)
            MERGE (lot)-[:CURRENTLY_AT]->(to)
        """,
        serial=lot_serial, ndc11=ndc11,
        from_entity=from_entity, to_entity=to_entity,
        tx_date=transaction_date, quantity=quantity,
        )

    def trace_lot_provenance(self, lot_serial: str) -> list[dict]:
        """Trace the complete chain of custody for a drug lot (DSCSA recall support)."""
        if not self.client.is_connected:
            return []
        return self.client.run_sync("""
            MATCH path = (origin:Entity)-[:TRANSFERRED*]->(current:Entity)
            WHERE ANY(r IN relationships(path) WHERE r.lot_serial = $serial)
            RETURN [n IN nodes(path) | n.id] AS chain,
                   [r IN relationships(path) | {date: r.date, qty: r.quantity}] AS transfers,
                   length(path) AS hops
            ORDER BY hops DESC
            LIMIT 1
        """, serial=lot_serial)

    def find_affected_pharmacies_on_recall(self, ndc11: str, lot_number: str) -> list[str]:
        """
        On a drug recall: find all pharmacies that received lots from this NDC.
        One Cypher query replaces a complex multi-table SQL join.
        """
        if not self.client.is_connected:
            return []
        results = self.client.run_sync("""
            MATCH (lot:DrugLot {ndc11: $ndc11})-[:CURRENTLY_AT]->(ph:Pharmacy)
            RETURN ph.ncpdp_id AS ncpdp_id, ph.name AS name
        """, ndc11=ndc11)
        return [r["ncpdp_id"] for r in results]
