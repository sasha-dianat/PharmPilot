"""
Clinical Knowledge Graph — Neo4j
===================================
Semantic network of clinical concepts that augments the AI Clinical Brain.
The ACB retrieves graph context alongside RAG chunks — providing structured
reasoning paths rather than just text similarity.

Graph enables queries like:
  "What drugs treat hypertension but are contraindicated in CKD stage 4?"
  → Traverse: (Hypertension)<-[:TREATS]-(Drug)-[:CONTRAINDICATED_IN]->(CKD)

  "If patient can't take metformin (eGFR 28) and is diabetic — what next?"
  → Traverse: same class → safer alternatives with renal dosing data

  "Find all conditions this patient has that interact with their new drug"
  → (Drug)-[:INTERACTS_WITH_CONDITION]->(Condition) for all patient conditions
"""
import logging
from typing import Optional
from services.graph.client import GraphClient

logger = logging.getLogger(__name__)


class ClinicalKnowledgeGraph:

    def __init__(self, client: GraphClient):
        self.client = client

    # ── Seed core knowledge ───────────────────────────────────────────────

    def seed_core_ontology(self) -> None:
        """
        Populate the graph with foundational clinical knowledge.
        Run once at deployment; updated when guidelines change.
        """
        if not self.client.is_connected:
            logger.warning("Neo4j unavailable — clinical ontology not seeded")
            return

        # Drug classes and their CYP metabolism
        self.client.run_sync("""
            MERGE (cyp3a4:CYPEnzyme {name: 'CYP3A4'})
            MERGE (cyp2d6:CYPEnzyme {name: 'CYP2D6'})
            MERGE (cyp2c9:CYPEnzyme {name: 'CYP2C9'})
            MERGE (cyp2c19:CYPEnzyme {name: 'CYP2C19'})
        """)

        # Condition hierarchy
        conditions = [
            ("CKD_STAGE_3",  "Chronic Kidney Disease Stage 3",  "N18.3"),
            ("CKD_STAGE_4",  "Chronic Kidney Disease Stage 4",  "N18.4"),
            ("CKD_STAGE_5",  "Chronic Kidney Disease Stage 5",  "N18.5"),
            ("HF",           "Heart Failure",                   "I50.9"),
            ("T2DM",         "Type 2 Diabetes Mellitus",        "E11.9"),
            ("HTN",          "Hypertension",                    "I10"),
            ("AFIB",         "Atrial Fibrillation",             "I48.91"),
            ("COPD",         "COPD",                            "J44.9"),
            ("LIVER_CIRR",   "Liver Cirrhosis",                 "K74.60"),
            ("PREGNANCY",    "Pregnancy",                       "Z34.90"),
        ]
        for code, name, icd10 in conditions:
            self.client.run_sync("""
                MERGE (c:Condition {icd10: $icd10})
                SET c.name = $name, c.code = $code
            """, icd10=icd10, name=name, code=code)

        # Drug contraindications (high-value clinical rules)
        contraindications = [
            # Drug generic name → Condition code
            ("metformin",          "CKD_STAGE_5",  "Contraindicated: lactic acidosis risk at eGFR <30"),
            ("metformin",          "CKD_STAGE_4",  "Dose reduction required: eGFR 30-45"),
            ("nitrofurantoin",     "CKD_STAGE_3",  "Contraindicated: ineffective + peripheral neuropathy risk"),
            ("spironolactone",     "CKD_STAGE_5",  "Contraindicated: hyperkalemia risk"),
            ("warfarin",           "PREGNANCY",    "Contraindicated: fetal warfarin syndrome"),
            ("methotrexate",       "PREGNANCY",    "Contraindicated: teratogen category X"),
            ("isotretinoin",       "PREGNANCY",    "Contraindicated: severe teratogen"),
            ("NSAIDs",             "HF",           "Avoid: fluid retention, cardiac decompensation"),
            ("NSAIDs",             "CKD_STAGE_3",  "Caution: nephrotoxicity, use lowest dose shortest duration"),
            ("meperidine",         "CKD_STAGE_3",  "Avoid: neurotoxic metabolite accumulation"),
            ("digoxin",            "CKD_STAGE_3",  "Dose reduction required: renally cleared"),
        ]
        for drug_name, condition_code, reason in contraindications:
            self.client.run_sync("""
                MERGE (d:Drug {generic_name: $drug_name})
                WITH d
                MATCH (c:Condition {code: $condition_code})
                MERGE (d)-[r:CONTRAINDICATED_IN]->(c)
                SET r.reason = $reason, r.updated_at = datetime()
            """, drug_name=drug_name, condition_code=condition_code, reason=reason)

        logger.info("Core clinical ontology seeded into Neo4j")

    # ── Context retrieval for ACB ─────────────────────────────────────────

    def get_drug_condition_context(
        self,
        drug_generic_name: str,
        patient_condition_icds: list[str],
    ) -> list[dict]:
        """
        Get all clinically relevant relationships between a drug and
        a patient's active conditions. Used to enrich ACB prompts.
        """
        if not self.client.is_connected or not patient_condition_icds:
            return []
        return self.client.run_sync("""
            MATCH (d:Drug)-[r]->(c:Condition)
            WHERE toLower(d.generic_name) CONTAINS toLower($drug_name)
              AND c.icd10 IN $condition_icds
              AND type(r) IN ['CONTRAINDICATED_IN', 'CAUTION_IN', 'DOSE_ADJUST_IN']
            RETURN d.generic_name AS drug,
                   c.name         AS condition,
                   c.icd10        AS icd10,
                   type(r)        AS relationship_type,
                   r.reason       AS reason
            ORDER BY
              CASE type(r)
                WHEN 'CONTRAINDICATED_IN' THEN 0
                WHEN 'CAUTION_IN'         THEN 1
                ELSE 2
              END
        """, drug_name=drug_generic_name, condition_icds=patient_condition_icds)

    def find_safer_alternatives(
        self,
        drug_generic_name: str,
        patient_condition_icds: list[str],
    ) -> list[dict]:
        """
        Find drugs in the same therapeutic class that are NOT contraindicated
        for the patient's current conditions.
        """
        if not self.client.is_connected:
            return []
        return self.client.run_sync("""
            MATCH (original:Drug)-[:IN_CLASS]->(cls:DrugClass)<-[:IN_CLASS]-(alt:Drug)
            WHERE toLower(original.generic_name) CONTAINS toLower($drug_name)
              AND alt.generic_name <> original.generic_name
              AND NOT EXISTS {
                MATCH (alt)-[:CONTRAINDICATED_IN]->(c:Condition)
                WHERE c.icd10 IN $condition_icds
              }
            RETURN alt.generic_name AS alternative,
                   alt.ndc11        AS ndc11,
                   cls.name         AS drug_class
            LIMIT 5
        """, drug_name=drug_generic_name, condition_icds=patient_condition_icds)

    def get_pharmacogenomic_alerts(
        self,
        drug_generic_name: str,
        pgx_genes: dict,  # {"CYP2D6": "poor_metabolizer", "CYP2C19": "rapid_metabolizer"}
    ) -> list[dict]:
        """
        Retrieve pharmacogenomic interaction alerts for a drug
        given the patient's genotype profile.
        """
        if not self.client.is_connected or not pgx_genes:
            return []
        alerts = []
        for gene, phenotype in pgx_genes.items():
            results = self.client.run_sync("""
                MATCH (d:Drug)-[r:PGX_INTERACTION]->(e:CYPEnzyme {name: $gene})
                WHERE toLower(d.generic_name) CONTAINS toLower($drug_name)
                  AND r.phenotype = $phenotype
                RETURN d.generic_name AS drug,
                       e.name         AS gene,
                       r.phenotype    AS phenotype,
                       r.effect       AS effect,
                       r.recommendation AS recommendation
            """, drug_name=drug_generic_name, gene=gene, phenotype=phenotype)
            alerts.extend(results)
        return alerts
