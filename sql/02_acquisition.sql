-- =====================================================================
-- Layer 2: acquisition intake, validation, mapping, migration lineage
-- Requirements 27 (templates), 28 (import wizard), 29 (mapping),
-- 43 (migration + traceability), 44 (data quality)
-- =====================================================================

-- The eight standard templates are defined as data, not hard-coded, so
-- the download and the validator stay in step with one definition.
CREATE TABLE import_template (
  template_id       INTEGER PRIMARY KEY,
  template_code     TEXT NOT NULL UNIQUE,   -- CUSTOMERS, PROJECTS, EMPLOYEES, TIME_ENTRIES,
                                            -- PROJECT_FINANCIALS, TASKS_MILESTONES, RISKS_ISSUES, CONTRACTS_SOWS
  template_name     TEXT NOT NULL,
  target_table      TEXT NOT NULL,
  sort_order        INTEGER NOT NULL DEFAULT 0,
  min_history_months INTEGER NOT NULL DEFAULT 0,
  description       TEXT
);

CREATE TABLE import_template_field (
  field_id          INTEGER PRIMARY KEY,
  template_id       INTEGER NOT NULL REFERENCES import_template(template_id),
  column_order      INTEGER NOT NULL,
  column_header     TEXT NOT NULL,          -- exactly as it appears in the Excel/CSV template
  target_column     TEXT,                   -- column in the target table
  data_type         TEXT NOT NULL CHECK (data_type IN ('text','integer','decimal','date','boolean','enum')),
  is_mandatory      INTEGER NOT NULL DEFAULT 0,
  is_key            INTEGER NOT NULL DEFAULT 0,
  enum_values       TEXT,                   -- pipe separated
  fk_template_code  TEXT,                   -- referential check target, e.g. PROJECTS
  min_value         REAL,
  max_value         REAL,
  notes             TEXT,
  UNIQUE (template_id, column_header)
);

-- One import run per upload attempt (req 28)
CREATE TABLE import_batch (
  batch_id          INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  template_id       INTEGER NOT NULL REFERENCES import_template(template_id),
  file_name         TEXT,
  uploaded_by       TEXT,
  uploaded_at       TEXT NOT NULL DEFAULT (datetime('now')),
  row_count         INTEGER NOT NULL DEFAULT 0,
  valid_count       INTEGER NOT NULL DEFAULT 0,
  warning_count     INTEGER NOT NULL DEFAULT 0,
  error_count       INTEGER NOT NULL DEFAULT 0,
  attempt_no        INTEGER NOT NULL DEFAULT 1,
  status            TEXT NOT NULL DEFAULT 'Uploaded'
                      CHECK (status IN ('Uploaded','Validated','Failed Validation','Mapped','Committed','Rolled Back'))
);

-- Staging holds the acquired company's rows exactly as supplied. Nothing
-- reaches the core tables until validated, mapped and committed.
CREATE TABLE import_staging_row (
  staging_row_id    INTEGER PRIMARY KEY,
  batch_id          INTEGER NOT NULL REFERENCES import_batch(batch_id),
  source_row_no     INTEGER NOT NULL,
  legacy_key        TEXT,
  payload_json      TEXT NOT NULL,          -- the raw row as supplied
  row_status        TEXT NOT NULL DEFAULT 'Pending'
                      CHECK (row_status IN ('Pending','Valid','Warning','Error','Corrected','Committed','Skipped')),
  committed_table   TEXT,
  committed_pk      INTEGER
);
CREATE INDEX ix_staging_batch ON import_staging_row(batch_id, row_status);

-- Validation findings (req 28 step 3, req 44)
CREATE TABLE validation_finding (
  finding_id        INTEGER PRIMARY KEY,
  batch_id          INTEGER NOT NULL REFERENCES import_batch(batch_id),
  staging_row_id    INTEGER REFERENCES import_staging_row(staging_row_id),
  severity          TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
  rule_code         TEXT NOT NULL,          -- MISSING_MANDATORY, DUPLICATE_RECORD, INVALID_DATE,
                                            -- ORPHAN_FK, NEGATIVE_VALUE, MISSING_COST_RATE,
                                            -- MISSING_BILL_RATE, MISSING_PM, RATE_INCONSISTENT,
                                            -- CURRENCY_INCONSISTENT, ANOMALY
  column_header     TEXT,
  observed_value    TEXT,
  message           TEXT NOT NULL,
  recommendation    TEXT,                   -- req 44: tell them how to fix it
  resolved          INTEGER NOT NULL DEFAULT 0,
  resolved_at       TEXT
);
CREATE INDEX ix_finding_batch ON validation_finding(batch_id, severity);

-- Terminology mapping (req 29)
CREATE TABLE mapping_set (
  mapping_set_id    INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  set_name          TEXT NOT NULL,
  dimension         TEXT NOT NULL,          -- time_category / project_type / billing_model /
                                            -- project_status / health / role / practice / currency
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (org_id, dimension, set_name)
);

CREATE TABLE mapping_rule (
  mapping_rule_id   INTEGER PRIMARY KEY,
  mapping_set_id    INTEGER NOT NULL REFERENCES mapping_set(mapping_set_id),
  legacy_value      TEXT NOT NULL,
  target_value      TEXT,
  confidence_pct    REAL,                   -- suggested match confidence
  match_source      TEXT CHECK (match_source IN ('exact','suggested','manual','unmapped')),
  occurrence_count  INTEGER NOT NULL DEFAULT 0,
  approved          INTEGER NOT NULL DEFAULT 0,
  UNIQUE (mapping_set_id, legacy_value)
);

-- Migration lineage + scoring (req 43)
CREATE TABLE migration_run (
  migration_run_id  INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  approved_by       TEXT,
  approved_at       TEXT,
  stage             TEXT NOT NULL DEFAULT 'Standardized'
                      CHECK (stage IN ('Legacy','Standardized','Validated','Mapped','Clean','Loaded')),
  data_quality_score REAL,
  financial_completeness_pct REAL,
  resource_completeness_pct REAL,
  project_completeness_pct REAL,
  notes             TEXT
);

CREATE TABLE migration_lineage (
  lineage_id        INTEGER PRIMARY KEY,
  migration_run_id  INTEGER NOT NULL REFERENCES migration_run(migration_run_id),
  entity_table      TEXT NOT NULL,
  entity_pk         INTEGER NOT NULL,
  legacy_system     TEXT,
  legacy_id         TEXT NOT NULL,
  legacy_payload_json TEXT,
  loaded_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_lineage_legacy ON migration_lineage(legacy_system, legacy_id);
CREATE INDEX ix_lineage_entity ON migration_lineage(entity_table, entity_pk);

-- Data quality findings against already-loaded data (req 44), separate
-- from per-batch validation so quality can be re-run at any time.
CREATE TABLE data_quality_finding (
  dq_id             INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_at            TEXT NOT NULL DEFAULT (datetime('now')),
  category          TEXT NOT NULL,          -- Missing data / Duplicates / Invalid financial /
                                            -- Missing rates / Invalid dates / Currency / Anomaly
  rule_code         TEXT NOT NULL,
  entity_table      TEXT,
  entity_pk         INTEGER,
  entity_label      TEXT,
  severity          TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
  message           TEXT NOT NULL,
  recommendation    TEXT,
  resolved          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_dq_org ON data_quality_finding(org_id, severity);
