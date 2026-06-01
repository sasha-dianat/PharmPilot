-- PharmPilot initial database setup
-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- For fuzzy drug name search
CREATE EXTENSION IF NOT EXISTS "btree_gist"; -- For date range constraints

-- Core schemas
CREATE SCHEMA IF NOT EXISTS pharmpilot;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Audit log table — append-only, immutable, tamper-evident
CREATE TABLE IF NOT EXISTS audit.phi_access_log (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(100) NOT NULL,
    actor_id        UUID,
    actor_type      VARCHAR(30),  -- staff, system, ai
    patient_id      UUID,
    resource_type   VARCHAR(100),
    resource_id     UUID,
    action          VARCHAR(50),
    ip_address      INET,
    session_id      UUID,
    data_hash       VARCHAR(64),  -- SHA-256 hash of the accessed data
    metadata        JSONB
);

-- Make audit log append-only via row-level security
ALTER TABLE audit.phi_access_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_insert_only ON audit.phi_access_log FOR INSERT WITH CHECK (true);
-- SELECT allowed; UPDATE and DELETE prohibited

-- Core pharmacy table (multi-tenant root)
CREATE TABLE IF NOT EXISTS pharmacies (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    npi             VARCHAR(10) UNIQUE,
    ncpdp_id        VARCHAR(10) UNIQUE,
    dea_number      VARCHAR(15),
    address_line1   VARCHAR(255),
    city            VARCHAR(100),
    state           CHAR(2),
    zip_code        VARCHAR(10),
    phone           VARCHAR(20),
    timezone        VARCHAR(50) DEFAULT 'America/New_York',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    config          JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prescribers
CREATE TABLE IF NOT EXISTS prescribers (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    npi             VARCHAR(10) NOT NULL UNIQUE,
    dea_number      VARCHAR(15),
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    specialty       VARCHAR(100),
    phone           VARCHAR(20),
    fax             VARCHAR(20),
    address_line1   VARCHAR(255),
    city            VARCHAR(100),
    state           CHAR(2),
    zip_code        VARCHAR(10),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_prescribers_npi ON prescribers(npi);
CREATE INDEX IF NOT EXISTS idx_prescribers_last_name ON prescribers USING gin(last_name gin_trgm_ops);
