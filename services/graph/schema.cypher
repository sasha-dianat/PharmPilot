// ============================================================
//  PharmPilot Neo4j Graph Schema
//  Run once at database initialisation.
//  Constraints enforce uniqueness; indexes enable fast lookup.
// ============================================================

// ── Constraints (unique IDs) ──────────────────────────────
CREATE CONSTRAINT drug_ndc       IF NOT EXISTS FOR (d:Drug)        REQUIRE d.ndc11    IS UNIQUE;
CREATE CONSTRAINT drug_rxcui     IF NOT EXISTS FOR (d:Drug)        REQUIRE d.rxcui    IS UNIQUE;
CREATE CONSTRAINT patient_id     IF NOT EXISTS FOR (p:Patient)     REQUIRE p.id       IS UNIQUE;
CREATE CONSTRAINT prescriber_npi IF NOT EXISTS FOR (r:Prescriber)  REQUIRE r.npi      IS UNIQUE;
CREATE CONSTRAINT pharmacy_ncpdp IF NOT EXISTS FOR (ph:Pharmacy)   REQUIRE ph.ncpdp_id IS UNIQUE;
CREATE CONSTRAINT condition_icd  IF NOT EXISTS FOR (c:Condition)   REQUIRE c.icd10    IS UNIQUE;
CREATE CONSTRAINT ingredient_id  IF NOT EXISTS FOR (i:Ingredient)  REQUIRE i.name     IS UNIQUE;
CREATE CONSTRAINT cyp_name       IF NOT EXISTS FOR (e:CYPEnzyme)   REQUIRE e.name     IS UNIQUE;
CREATE CONSTRAINT lot_serial     IF NOT EXISTS FOR (l:DrugLot)     REQUIRE l.serial   IS UNIQUE;

// ── Indexes (fast lookup) ─────────────────────────────────
CREATE INDEX drug_name          IF NOT EXISTS FOR (d:Drug)        ON (d.generic_name);
CREATE INDEX drug_schedule      IF NOT EXISTS FOR (d:Drug)        ON (d.dea_schedule);
CREATE INDEX interaction_sev    IF NOT EXISTS FOR ()-[r:INTERACTS_WITH]-() ON (r.severity);
CREATE INDEX patient_dob        IF NOT EXISTS FOR (p:Patient)     ON (p.date_of_birth);
CREATE INDEX prescriber_state   IF NOT EXISTS FOR (r:Prescriber)  ON (r.state);

// ── Full-text search on drug names ────────────────────────
CREATE FULLTEXT INDEX drug_name_fulltext IF NOT EXISTS FOR (d:Drug) ON EACH [d.generic_name, d.brand_name];
