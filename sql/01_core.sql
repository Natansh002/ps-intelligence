-- =====================================================================
-- PSA + PS Intelligence platform
-- Layer 1: core operating data model
-- SQLite dialect. Requirements coverage: sections 27-45.
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Tenancy / org so an acquired book of business can live beside the
-- operating company for comparison before and after migration (req 43).
-- ---------------------------------------------------------------------
CREATE TABLE org (
  org_id            INTEGER PRIMARY KEY,
  org_code          TEXT NOT NULL UNIQUE,
  org_name          TEXT NOT NULL,
  org_role          TEXT NOT NULL CHECK (org_role IN ('operating','acquisition_target')),
  currency          TEXT NOT NULL DEFAULT 'CAD',
  fiscal_year_start_month INTEGER NOT NULL DEFAULT 7,   -- Fiscal year: July-June (example org)
  acquired_on       TEXT,
  migration_state   TEXT NOT NULL DEFAULT 'n/a'
                      CHECK (migration_state IN ('n/a','assessing','approved','migrating','migrated')),
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE practice (
  practice_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  practice_code     TEXT NOT NULL,
  practice_name     TEXT NOT NULL,
  segment           TEXT,            -- e.g. K12 / Non-for-Profit
  target_margin_pct REAL NOT NULL DEFAULT 30.0,
  target_utilization_pct REAL NOT NULL DEFAULT 70.0,
  UNIQUE (org_id, practice_code)
);

-- ---------------------------------------------------------------------
-- Customers (req 27.1)
-- ---------------------------------------------------------------------
CREATE TABLE customer (
  customer_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  legacy_customer_id TEXT,           -- traceability back to the acquired system (req 43)
  customer_code     TEXT NOT NULL,
  customer_name     TEXT NOT NULL,
  customer_type     TEXT,            -- Public sector / Nonprofit / Commercial
  industry          TEXT,
  region            TEXT,
  geography         TEXT,
  account_owner     TEXT,
  customer_status   TEXT NOT NULL DEFAULT 'Active'
                      CHECK (customer_status IN ('Prospect','Active','At Risk','Churned','Dormant')),
  contract_start    TEXT,
  contract_end      TEXT,
  annual_revenue    REAL,
  lifetime_revenue  REAL,
  csat_score        REAL,
  UNIQUE (org_id, customer_code)
);
CREATE INDEX ix_customer_org ON customer(org_id);

-- ---------------------------------------------------------------------
-- Resources (req 27.3)
-- ---------------------------------------------------------------------
CREATE TABLE employee (
  employee_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  legacy_employee_id TEXT,
  employee_code     TEXT NOT NULL,
  employee_name     TEXT NOT NULL,
  role              TEXT,
  department        TEXT,
  practice_id       INTEGER REFERENCES practice(practice_id),
  location          TEXT,
  geography         TEXT,
  employment_type   TEXT CHECK (employment_type IN ('Full Time','Part Time','Contractor','Intern')),
  cost_rate         REAL,            -- fully loaded cost per hour
  billing_rate      REAL,            -- standard list rate per hour
  weekly_capacity_hours REAL NOT NULL DEFAULT 37.5,
  utilization_target_pct REAL NOT NULL DEFAULT 70.0,
  manager_employee_id INTEGER REFERENCES employee(employee_id),
  start_date        TEXT,
  end_date          TEXT,
  is_billable       INTEGER NOT NULL DEFAULT 1,
  is_active         INTEGER NOT NULL DEFAULT 1,
  UNIQUE (org_id, employee_code)
);
CREATE INDEX ix_employee_org ON employee(org_id);

CREATE TABLE skill (
  skill_id          INTEGER PRIMARY KEY,
  skill_name        TEXT NOT NULL UNIQUE,
  skill_family      TEXT
);

CREATE TABLE employee_skill (
  employee_id       INTEGER NOT NULL REFERENCES employee(employee_id),
  skill_id          INTEGER NOT NULL REFERENCES skill(skill_id),
  proficiency       INTEGER NOT NULL DEFAULT 3 CHECK (proficiency BETWEEN 1 AND 5),
  PRIMARY KEY (employee_id, skill_id)
);

-- ---------------------------------------------------------------------
-- Contracts / SOWs / change orders (req 27.8)
-- ---------------------------------------------------------------------
CREATE TABLE contract (
  contract_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  legacy_contract_id TEXT,
  contract_code     TEXT NOT NULL,
  customer_id       INTEGER NOT NULL REFERENCES customer(customer_id),
  contract_type     TEXT CHECK (contract_type IN ('MSA','SOW','Subscription','Retainer','Support')),
  billing_model     TEXT CHECK (billing_model IN ('Fixed Fee','Time and Materials','Milestone','Capped T&M','Retainer')),
  contract_value    REAL NOT NULL DEFAULT 0,
  currency          TEXT NOT NULL DEFAULT 'CAD',
  sold_hours        REAL,
  start_date        TEXT,
  end_date          TEXT,
  payment_terms     TEXT,
  remaining_value   REAL,
  UNIQUE (org_id, contract_code)
);

CREATE TABLE sow (
  sow_id            INTEGER PRIMARY KEY,
  contract_id       INTEGER NOT NULL REFERENCES contract(contract_id),
  legacy_sow_id     TEXT,
  sow_code          TEXT NOT NULL UNIQUE,
  sow_value         REAL NOT NULL DEFAULT 0,
  sold_hours        REAL,
  signed_date       TEXT,
  version           INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- Projects (req 27.2)
-- ---------------------------------------------------------------------
CREATE TABLE project (
  project_id        INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  legacy_project_id TEXT,
  project_code      TEXT NOT NULL,
  project_name      TEXT NOT NULL,
  customer_id       INTEGER NOT NULL REFERENCES customer(customer_id),
  contract_id       INTEGER REFERENCES contract(contract_id),
  sow_id            INTEGER REFERENCES sow(sow_id),
  practice_id       INTEGER REFERENCES practice(practice_id),
  project_type      TEXT,            -- Implementation / Upgrade / Migration / Advisory / Managed Service
  product           TEXT,            -- e.g. Finance / HR-Payroll / Portal
  methodology       TEXT,            -- named so delivery risk can be attributed to it (req 36)
  project_manager_id INTEGER REFERENCES employee(employee_id),
  billing_model     TEXT CHECK (billing_model IN ('Fixed Fee','Time and Materials','Milestone','Capped T&M','Retainer')),
  start_date        TEXT,
  planned_end_date  TEXT,
  actual_end_date   TEXT,
  go_live_date      TEXT,
  planned_go_live_date TEXT,
  contract_value    REAL NOT NULL DEFAULT 0,
  sow_value         REAL NOT NULL DEFAULT 0,
  budget_hours      REAL NOT NULL DEFAULT 0,
  budget_cost       REAL NOT NULL DEFAULT 0,
  revenue_recognized REAL NOT NULL DEFAULT 0,
  project_status    TEXT NOT NULL DEFAULT 'In Flight'
                      CHECK (project_status IN ('Not Started','In Flight','On Hold','Complete','Cancelled')),
  project_health    TEXT NOT NULL DEFAULT 'Green'
                      CHECK (project_health IN ('Green','Yellow','Red')),
  health_set_by     TEXT,
  health_set_on     TEXT,
  target_margin_pct REAL NOT NULL DEFAULT 30.0,
  UNIQUE (org_id, project_code)
);
CREATE INDEX ix_project_org      ON project(org_id);
CREATE INDEX ix_project_customer ON project(customer_id);
CREATE INDEX ix_project_status   ON project(project_status);

-- Change orders (req 32, 36)
CREATE TABLE change_order (
  change_order_id   INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  legacy_change_order_id TEXT,
  co_code           TEXT NOT NULL,
  description       TEXT,
  co_value          REAL NOT NULL DEFAULT 0,
  co_hours          REAL NOT NULL DEFAULT 0,
  raised_date       TEXT,
  approved_date     TEXT,
  status            TEXT NOT NULL DEFAULT 'Draft'
                      CHECK (status IN ('Draft','Submitted','Approved','Rejected')),
  UNIQUE (project_id, co_code)
);

-- Tasks and milestones (req 27.6). Versioned plan baselines so plan
-- changes are auditable rather than overwritten.
CREATE TABLE project_plan_version (
  plan_version_id   INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  version_no        INTEGER NOT NULL,
  is_baseline       INTEGER NOT NULL DEFAULT 0,
  is_current        INTEGER NOT NULL DEFAULT 0,
  created_on        TEXT NOT NULL,
  created_by        TEXT,
  change_reason     TEXT,
  UNIQUE (project_id, version_no)
);

CREATE TABLE project_task (
  task_id           INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  plan_version_id   INTEGER REFERENCES project_plan_version(plan_version_id),
  legacy_task_id    TEXT,
  task_code         TEXT NOT NULL,
  task_name         TEXT NOT NULL,
  phase             TEXT,
  is_milestone      INTEGER NOT NULL DEFAULT 0,
  is_critical       INTEGER NOT NULL DEFAULT 0,
  milestone_type    TEXT CHECK (milestone_type IN ('Delivery','Billing','Gate',NULL)),
  planned_start     TEXT,
  planned_end       TEXT,
  actual_start      TEXT,
  actual_end        TEXT,
  planned_hours     REAL NOT NULL DEFAULT 0,
  actual_hours      REAL NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'Not Started'
                      CHECK (status IN ('Not Started','In Progress','Complete','Blocked','Cancelled')),
  percent_complete  REAL NOT NULL DEFAULT 0,
  billing_amount    REAL NOT NULL DEFAULT 0,
  billing_status    TEXT CHECK (billing_status IN ('Not Ready','Ready','Approved','Invoiced',NULL))
);
CREATE INDEX ix_task_project ON project_task(project_id);

-- Time entries (req 27.4)
CREATE TABLE time_entry (
  time_entry_id     INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  legacy_time_entry_id TEXT,
  employee_id       INTEGER NOT NULL REFERENCES employee(employee_id),
  project_id        INTEGER REFERENCES project(project_id),
  task_id           INTEGER REFERENCES project_task(task_id),
  entry_date        TEXT NOT NULL,
  hours             REAL NOT NULL,
  is_billable       INTEGER NOT NULL DEFAULT 1,
  time_category     TEXT,            -- Consulting / PM / Travel / Internal / Rework / PTO
  approval_status   TEXT NOT NULL DEFAULT 'Approved'
                      CHECK (approval_status IN ('Draft','Submitted','Approved','Rejected')),
  cost_rate         REAL,            -- snapshot at entry time
  bill_rate         REAL,
  invoiced          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_time_project ON time_entry(project_id);
CREATE INDEX ix_time_emp_dt  ON time_entry(employee_id, entry_date);
CREATE INDEX ix_time_date    ON time_entry(entry_date);

-- Monthly project financials (req 27.5)
CREATE TABLE project_financial_month (
  pfm_id            INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  period_month      TEXT NOT NULL,   -- YYYY-MM
  contract_value    REAL NOT NULL DEFAULT 0,
  invoiced_revenue  REAL NOT NULL DEFAULT 0,
  recognized_revenue REAL NOT NULL DEFAULT 0,
  labor_cost        REAL NOT NULL DEFAULT 0,
  other_cost        REAL NOT NULL DEFAULT 0,
  budget_amount     REAL NOT NULL DEFAULT 0,
  forecast_amount   REAL NOT NULL DEFAULT 0,
  actual_amount     REAL NOT NULL DEFAULT 0,
  UNIQUE (project_id, period_month)
);
CREATE INDEX ix_pfm_month ON project_financial_month(period_month);

-- Risks and issues (req 27.7)
CREATE TABLE risk_issue (
  risk_issue_id     INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  legacy_risk_id    TEXT,
  ref_code          TEXT NOT NULL,
  entry_type        TEXT NOT NULL CHECK (entry_type IN ('Risk','Issue','Assumption','Dependency')),
  description       TEXT,
  date_identified   TEXT,
  owner             TEXT,
  priority          TEXT CHECK (priority IN ('Low','Medium','High','Critical')),
  impact            INTEGER CHECK (impact BETWEEN 1 AND 5),
  probability       INTEGER CHECK (probability BETWEEN 1 AND 5),
  status            TEXT NOT NULL DEFAULT 'Open'
                      CHECK (status IN ('Open','Mitigating','Closed','Accepted')),
  resolution        TEXT,
  due_date          TEXT,
  is_customer_raised INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_risk_project ON risk_issue(project_id);

-- Resource allocation / assignments
CREATE TABLE assignment (
  assignment_id     INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  employee_id       INTEGER NOT NULL REFERENCES employee(employee_id),
  role_on_project   TEXT,
  start_date        TEXT,
  end_date          TEXT,
  planned_hours     REAL NOT NULL DEFAULT 0,
  allocation_pct    REAL NOT NULL DEFAULT 100,
  cost_rate         REAL,
  bill_rate         REAL
);
CREATE INDEX ix_assignment_emp ON assignment(employee_id);
CREATE INDEX ix_assignment_prj ON assignment(project_id);

-- Pipeline (req 37, 40)
CREATE TABLE pipeline_opportunity (
  opportunity_id    INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  opp_code          TEXT NOT NULL UNIQUE,
  customer_id       INTEGER REFERENCES customer(customer_id),
  customer_name_raw TEXT,
  opp_name          TEXT NOT NULL,
  practice_id       INTEGER REFERENCES practice(practice_id),
  product           TEXT,
  project_type      TEXT,
  billing_model     TEXT,
  services_value    REAL NOT NULL DEFAULT 0,
  estimated_hours   REAL NOT NULL DEFAULT 0,
  probability_pct   REAL NOT NULL DEFAULT 50,
  expected_close    TEXT,
  expected_start    TEXT,
  expected_duration_months REAL,
  stage             TEXT CHECK (stage IN ('Qualify','Discover','Propose','Negotiate','Closed Won','Closed Lost'))
);

-- Forecast snapshots, so forecast accuracy can be measured (req 38)
CREATE TABLE project_forecast_snapshot (
  snapshot_id       INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  snapshot_date     TEXT NOT NULL,
  snapshot_type     TEXT NOT NULL CHECK (snapshot_type IN ('Original','Revised','Final')),
  forecast_hours    REAL,
  forecast_cost     REAL,
  forecast_revenue  REAL,
  forecast_margin_pct REAL,
  forecast_end_date TEXT,
  submitted_by_employee_id INTEGER REFERENCES employee(employee_id),
  UNIQUE (project_id, snapshot_date, snapshot_type)
);

-- Revenue recognition schedule + audit trail (project brief: billing,
-- rev rec, versioning, auditing)
CREATE TABLE revenue_recognition (
  revrec_id         INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  period_month      TEXT NOT NULL,
  method            TEXT CHECK (method IN ('Percent Complete','Milestone','As Incurred','Straight Line')),
  recognized_amount REAL NOT NULL DEFAULT 0,
  deferred_amount   REAL NOT NULL DEFAULT 0,
  posted            INTEGER NOT NULL DEFAULT 0,
  posted_on         TEXT,
  UNIQUE (project_id, period_month, method)
);

CREATE TABLE audit_log (
  audit_id          INTEGER PRIMARY KEY,
  entity_table      TEXT NOT NULL,
  entity_pk         INTEGER NOT NULL,
  field_name        TEXT,
  old_value         TEXT,
  new_value         TEXT,
  changed_by        TEXT,
  changed_at        TEXT NOT NULL DEFAULT (datetime('now')),
  change_source     TEXT,            -- ui / import / engine / api
  -- An override is a human decision that departs from the computed value.
  -- It is the one kind of change where the note matters more than the value,
  -- because without it the override is indistinguishable from a mistake.
  is_override       INTEGER NOT NULL DEFAULT 0,
  change_reason     TEXT,            -- mandatory on an override
  approved_by       TEXT             -- second pair of eyes, where required
);
CREATE INDEX ix_audit_entity ON audit_log(entity_table, entity_pk);
CREATE INDEX ix_audit_override ON audit_log(is_override, field_name);
