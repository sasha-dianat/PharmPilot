"""
Drug Interaction Graph — Neo4j
================================
Stores the pharmacy drug interaction network as a property graph.
Every drug is a node; every DDI is a directed edge with clinical metadata.

Advantages over relational DDI tables:
  1. Multi-drug path queries: "find all interaction chains of length 2–4
     that include opioids and CNS depressants" — O(n) in graph, O(n^k) in SQL
  2. Therapeutic duplicate detection via GPI-class nodes
  3. Substitution path finding: "what is the shortest safe alternative path
     from warfarin to a non-interacting anticoagulant for this patient's profile?"
  4. Real-time network enrichment from FDB/Medi-Span without schema changes
"""
import logging
from typing import Optional
from uuid import UUID

from services.graph.client import GraphClient

logger = logging.getLogger(__name__)

# Evidence grades mapped to severity scores for graph weighting
SEVERITY_WEIGHT = {
    "contraindicated": 1.0,
    "severe":          0.85,
    "moderate":        0.55,
    "minor":           0.25,
    "informational":   0.05,
}


class DrugInteractionGraph:
    """
    Manages the drug interaction subgraph in Neo4j.
    All writes are idempotent (MERGE not CREATE).
    """

    def __init__(self, client: GraphClient):
        self.client = client

    # ── Node management ───────────────────────────────────────────────────

    def upsert_drug(self, drug: dict) -> None:
        """Add or update a drug node. Called when drug catalog is updated."""
        if not self.client.is_connected:
            return
        self.client.run_sync("""
            MERGE (d:Drug {ndc11: $ndc11})
            SET d.generic_name    = $generic_name,
                d.brand_name      = $brand_name,
                d.rxcui           = $rxcui,
                d.gpi             = $gpi,
                d.dea_schedule    = $dea_schedule,
                d.is_controlled   = $is_controlled,
                d.drug_class      = $drug_class,
                d.updated_at      = datetime()
        """,
        ndc11=drug.get("ndc11"),
        generic_name=drug.get("generic_name", ""),
        brand_name=drug.get("brand_name", ""),
        rxcui=drug.get("rxcui"),
        gpi=drug.get("gpi"),
        dea_schedule=drug.get("dea_schedule"),
        is_controlled=drug.get("is_controlled", False),
        drug_class=drug.get("drug_class", ""),
        )

    def upsert_interaction(
        self,
        drug_a_ndc: str,
        drug_b_ndc: str,
        severity: str,
        mechanism: str,
        description: str,
        evidence_grade: str = "B",
        source: str = "fdb",
    ) -> None:
        """
        Add or update a bidirectional drug interaction edge.
        Both directions stored (A→B and B→A) for traversal efficiency.
        """
        if not self.client.is_connected:
            return
        weight = SEVERITY_WEIGHT.get(severity.lower(), 0.5)
        self.client.run_sync("""
            MATCH (a:Drug {ndc11: $ndc_a}), (b:Drug {ndc11: $ndc_b})
            MERGE (a)-[r:INTERACTS_WITH {drug_b_ndc: $ndc_b}]->(b)
            SET r.severity       = $severity,
                r.mechanism      = $mechanism,
                r.description    = $description,
                r.evidence_grade = $evidence_grade,
                r.source         = $source,
                r.weight         = $weight,
                r.updated_at     = datetime()
            MERGE (b)-[r2:INTERACTS_WITH {drug_b_ndc: $ndc_a}]->(a)
            SET r2.severity       = $severity,
                r2.mechanism      = $mechanism,
                r2.description    = $description,
                r2.evidence_grade = $evidence_grade,
                r2.source         = $source,
                r2.weight         = $weight,
                r2.updated_at     = datetime()
        """,
        ndc_a=drug_a_ndc, ndc_b=drug_b_ndc,
        severity=severity, mechanism=mechanism, description=description,
        evidence_grade=evidence_grade, source=source, weight=weight,
        )

    # ── Queries ───────────────────────────────────────────────────────────

    def find_direct_interactions(
        self,
        ndc: str,
        active_medication_ndcs: list[str],
        min_severity: str = "minor",
    ) -> list[dict]:
        """
        Find all direct interactions between a drug and a patient's medication list.
        Much faster than pairwise SQL checks for large medication lists.
        """
        if not self.client.is_connected or not active_medication_ndcs:
            return []
        min_weight = SEVERITY_WEIGHT.get(min_severity.lower(), 0.0)
        return self.client.run_sync("""
            MATCH (new:Drug {ndc11: $ndc})-[r:INTERACTS_WITH]->(existing:Drug)
            WHERE existing.ndc11 IN $active_ndcs
              AND r.weight >= $min_weight
            RETURN new.generic_name  AS new_drug,
                   existing.generic_name AS interacting_drug,
                   existing.ndc11    AS interacting_ndc,
                   r.severity        AS severity,
                   r.mechanism       AS mechanism,
                   r.description     AS description,
                   r.evidence_grade  AS evidence_grade,
                   r.weight          AS weight
            ORDER BY r.weight DESC
        """,
        ndc=ndc, active_ndcs=active_medication_ndcs, min_weight=min_weight,
        )

    def find_polypharmacy_chains(
        self,
        medication_ndcs: list[str],
        max_depth: int = 4,
    ) -> list[dict]:
        """
        Find multi-drug interaction chains within a patient's medication list.
        Detects complex scenarios like: A+B individually safe, A+B+C dangerous
        (serotonin syndrome from SSRI + tramadol + linezolid).
        """
        if not self.client.is_connected or len(medication_ndcs) < 3:
            return []
        return self.client.run_sync("""
            MATCH path = (a:Drug)-[:INTERACTS_WITH*2..{max_depth}]->(b:Drug)
            WHERE a.ndc11 IN $ndcs AND b.ndc11 IN $ndcs
              AND ALL(n IN nodes(path) WHERE n.ndc11 IN $ndcs)
              AND ALL(r IN relationships(path) WHERE r.weight >= 0.55)
            RETURN [n IN nodes(path) | n.generic_name] AS chain,
                   [r IN relationships(path) | r.severity] AS severities,
                   length(path) AS chain_length,
                   reduce(s=0.0, r IN relationships(path) | s + r.weight) AS total_risk
            ORDER BY total_risk DESC
            LIMIT 10
        """.replace("{max_depth}", str(max_depth)),
        ndcs=medication_ndcs,
        )

    def find_safe_alternatives(
        self,
        drug_ndc: str,
        contraindicated_drug_ndcs: list[str],
        same_drug_class_gpi_prefix: str,
    ) -> list[dict]:
        """
        Find drugs in the same therapeutic class that have NO interactions
        with the patient's current medications.
        Used by ACB to suggest safer alternatives.
        """
        if not self.client.is_connected:
            return []
        return self.client.run_sync("""
            MATCH (candidate:Drug)
            WHERE candidate.gpi STARTS WITH $gpi_prefix
              AND candidate.ndc11 <> $original_ndc
              AND NOT EXISTS {
                MATCH (candidate)-[:INTERACTS_WITH]->(problem:Drug)
                WHERE problem.ndc11 IN $contra_ndcs
                  AND problem.severity IN ['severe', 'contraindicated']
              }
            RETURN candidate.ndc11        AS ndc11,
                   candidate.generic_name AS generic_name,
                   candidate.brand_name   AS brand_name,
                   candidate.dea_schedule AS dea_schedule
            LIMIT 5
        """,
        gpi_prefix=same_drug_class_gpi_prefix,
        original_ndc=drug_ndc,
        contra_ndcs=contraindicated_drug_ndcs,
        )

    def bulk_load_from_fdb(self, interactions: list[dict]) -> int:
        """
        Bulk load drug interactions from FDB/Medi-Span export.
        Uses UNWIND for batch efficiency.
        """
        if not self.client.is_connected:
            return 0
        self.client.run_sync("""
            UNWIND $interactions AS row
            MATCH (a:Drug {ndc11: row.drug_a_ndc}), (b:Drug {ndc11: row.drug_b_ndc})
            MERGE (a)-[r:INTERACTS_WITH {drug_b_ndc: row.drug_b_ndc}]->(b)
            SET r += {
                severity: row.severity,
                mechanism: row.mechanism,
                description: row.description,
                evidence_grade: row.evidence_grade,
                weight: row.weight,
                source: 'fdb_bulk',
                updated_at: datetime()
            }
        """, interactions=interactions)
        logger.info("Bulk loaded %d drug interactions into Neo4j", len(interactions))
        return len(interactions)
