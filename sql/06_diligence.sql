-- =====================================================================
-- The diligence pack.
--
-- These are the tables behind the questions a buyer, or a new PS leader,
-- actually asks on day one and that a PSA product will not answer:
--
--   What is the customer satisfaction history, and what did they actually say?
--   Which projects are red and amber, and why?
--   Which milestone runs long, and what causes it?
--   How long does hypercare really take?
--   What is the average bill rate, list against realised?
--   Show me the last three performance cycles for the delivery team.
--   Show me the product bug and enhancement backlog.
--
-- Every one of those is a question about evidence rather than about a
-- dashboard. So each table here holds the evidence, including the verbatim
-- text, and the engine derives the summary from it rather than the other way
-- round. A CSAT average with no comments behind it tells you nothing about
-- why the number moved.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Customer satisfaction, with what they said
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS csat_response;
CREATE TABLE csat_response (
  csat_id          INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  customer_id      INTEGER NOT NULL REFERENCES customer(customer_id),
  project_id       INTEGER REFERENCES project(project_id),
  survey_point     TEXT    NOT NULL,  -- kickoff / design / go_live / hypercare_exit /
                                      -- annual
  responded_on     TEXT    NOT NULL,
  respondent_role  TEXT    NOT NULL,  -- sponsor / project_lead / end_user / finance
  score            REAL    NOT NULL,  -- 1 to 5
  nps              INTEGER,           -- -100 to 100 where asked
  would_reference  INTEGER,           -- 0 or 1, null where not asked
  comment          TEXT,              -- verbatim
  theme            TEXT,              -- derived: what the comment is about
  sentiment        TEXT,              -- positive / mixed / negative
  is_escalation    INTEGER NOT NULL DEFAULT 0,
  responded_by_name TEXT
);
CREATE INDEX ix_csat_project  ON csat_response(project_id);
CREATE INDEX ix_csat_customer ON csat_response(org_id, customer_id, responded_on);

-- Derived: CSAT rolled up with the drivers behind the movement
DROP TABLE IF EXISTS csat_summary;
CREATE TABLE csat_summary (
  csat_summary_id  INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,  -- org / product / practice / customer /
                                      -- survey_point / period
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  period_from      TEXT,
  period_to        TEXT,
  responses        INTEGER NOT NULL,
  avg_score        REAL,
  prior_avg_score  REAL,
  delta            REAL,
  pct_promoters    REAL,             -- score 4.5 and above
  pct_detractors   REAL,             -- score 3 and below
  nps              REAL,
  reference_rate   REAL,
  top_theme        TEXT,
  top_theme_count  INTEGER,
  statement        TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- Milestone and phase duration: which one runs long, and why
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS phase_duration_analysis;
CREATE TABLE phase_duration_analysis (
  phase_analysis_id INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  scope             TEXT    NOT NULL,   -- org / product / practice
  scope_key         TEXT,
  scope_label       TEXT    NOT NULL,
  phase             TEXT    NOT NULL,
  phase_order       INTEGER NOT NULL,
  projects          INTEGER NOT NULL,
  planned_days_med  REAL    NOT NULL,
  actual_days_med   REAL    NOT NULL,
  slip_days_med     REAL    NOT NULL,
  slip_pct_med      REAL    NOT NULL,
  -- Phase slip is heavily skewed: most engagements run a phase close to plan
  -- and a minority run it very long. The median therefore hides the entire
  -- phenomenon, so the share that slipped and the size of the slip among
  -- those that did are recorded beside it. Reporting only the median is how
  -- a one-day headline sits on top of a three-week problem.
  projects_slipped  INTEGER,
  pct_slipped       REAL,
  slip_days_when_slipped REAL,
  slip_days_p90     REAL,
  planned_hours_med REAL,
  actual_hours_med  REAL,
  effort_overrun_pct REAL,
  share_of_slip_pct REAL    NOT NULL,   -- this phase's share of total slip
  worst_project_id  INTEGER REFERENCES project(project_id),
  worst_project_slip REAL,
  rank_by_slip      INTEGER NOT NULL,
  statement         TEXT    NOT NULL
);

-- The attributed cause. This is the "and why" half of the question, and it is
-- derived from evidence in the data rather than asserted: dependency waits,
-- customer-raised issues in the window, rework hours, resource churn,
-- change orders landing mid-phase, and defect volume against the product.
DROP TABLE IF EXISTS phase_slip_cause;
CREATE TABLE phase_slip_cause (
  cause_id          INTEGER PRIMARY KEY,
  phase_analysis_id INTEGER NOT NULL REFERENCES phase_duration_analysis(phase_analysis_id),
  cause_code        TEXT    NOT NULL,
  cause_label       TEXT    NOT NULL,
  evidence          TEXT    NOT NULL,   -- what in the data says so
  incidence_pct     REAL    NOT NULL,   -- share of slipped projects showing it
  attributed_days   REAL,               -- days of the median slip it explains
  metric_value      REAL,
  metric_unit       TEXT,
  rank              INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Hypercare: go-live to closure, and what happens in between
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS hypercare_period;
CREATE TABLE hypercare_period (
  hypercare_id      INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  go_live_date      TEXT    NOT NULL,
  planned_exit_date TEXT,
  actual_exit_date  TEXT,
  planned_days      REAL,
  actual_days       REAL,
  overrun_days      REAL,
  effort_hours      REAL    NOT NULL,
  billable_hours    REAL    NOT NULL,
  effort_pct_of_project REAL,
  tickets_raised    INTEGER NOT NULL,
  tickets_blocking  INTEGER NOT NULL,
  exit_csat         REAL,
  exit_verdict      TEXT,              -- clean / extended / unresolved
  extension_reason  TEXT,
  statement         TEXT
);

DROP TABLE IF EXISTS hypercare_summary;
CREATE TABLE hypercare_summary (
  hypercare_summary_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,   -- org / product / practice
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  projects         INTEGER NOT NULL,
  planned_days_med REAL,
  actual_days_med  REAL,
  actual_days_p90  REAL,
  overrun_days_med REAL,
  effort_hours_med REAL,
  effort_pct_med   REAL,
  billable_share_pct REAL,
  unbilled_cost    REAL,               -- the cost of hypercare nobody charges for
  tickets_med      REAL,
  clean_exit_pct   REAL,
  statement        TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- Rates: list against realised
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS rate_analysis;
CREATE TABLE rate_analysis (
  rate_analysis_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  dimension        TEXT    NOT NULL,   -- org / role / product / practice /
                                       -- customer / billing_model / period
  dimension_key    TEXT,
  dimension_label  TEXT    NOT NULL,
  billable_hours   REAL    NOT NULL,
  total_hours      REAL    NOT NULL,
  list_rate_avg    REAL,               -- the rate card
  booked_rate_avg  REAL,               -- rate on the entries, hour weighted
  realised_rate    REAL,               -- revenue actually earned per billable hour
  net_rate         REAL,               -- revenue per total hour delivered
  cost_rate_avg    REAL,
  -- Three gaps, three different conversations. Reporting one of them and
  -- calling it "the rate" is how a services book looks healthy on a screen
  -- and thin in the accounts.
  discount_pct     REAL,               -- booked against list: given away at sale
  leakage_pct      REAL,               -- realised against booked, per billable hour
  absorption_pct   REAL,               -- net against booked, per hour delivered
  margin_pct       REAL,
  revenue          REAL,
  cost             REAL,
  headcount        INTEGER,
  statement        TEXT
);

-- ---------------------------------------------------------------------
-- Team: the last three performance cycles
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS performance_cycle;
CREATE TABLE performance_cycle (
  cycle_id        INTEGER PRIMARY KEY,
  org_id          INTEGER NOT NULL REFERENCES org(org_id),
  cycle_code      TEXT    NOT NULL,
  cycle_label     TEXT    NOT NULL,
  period_from     TEXT    NOT NULL,
  period_to       TEXT    NOT NULL,
  closed_on       TEXT,
  calibrated      INTEGER NOT NULL DEFAULT 0,
  participation_pct REAL,
  sort_order      INTEGER NOT NULL
);

DROP TABLE IF EXISTS performance_review;
CREATE TABLE performance_review (
  review_id        INTEGER PRIMARY KEY,
  cycle_id         INTEGER NOT NULL REFERENCES performance_cycle(cycle_id),
  employee_id      INTEGER NOT NULL REFERENCES employee(employee_id),
  reviewer_employee_id INTEGER REFERENCES employee(employee_id),
  overall_rating   REAL    NOT NULL,   -- 1 to 5
  rating_label     TEXT    NOT NULL,
  -- the measures the rating was set against, so the rating is checkable
  utilization_pct  REAL,
  utilization_target_pct REAL,
  realised_rate    REAL,
  csat_avg         REAL,
  projects_delivered INTEGER,
  on_time_pct      REAL,
  timesheet_compliance_pct REAL,
  certifications   INTEGER,
  training_hours   REAL,
  -- the narrative
  strengths        TEXT,
  development      TEXT,
  goals            TEXT,
  manager_comment  TEXT,
  flight_risk      TEXT,               -- low / medium / high
  promotion_ready  INTEGER NOT NULL DEFAULT 0,
  is_calibrated    INTEGER NOT NULL DEFAULT 0,
  submitted_on     TEXT,
  UNIQUE (cycle_id, employee_id)
);
CREATE INDEX ix_review_employee ON performance_review(employee_id);

DROP TABLE IF EXISTS performance_summary;
CREATE TABLE performance_summary (
  perf_summary_id  INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,   -- org / practice / role / cycle
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  reviews          INTEGER NOT NULL,
  avg_rating       REAL,
  prior_avg_rating REAL,
  rating_delta     REAL,
  pct_top          REAL,               -- 4.5 and above
  pct_bottom       REAL,               -- 2.5 and below
  high_flight_risk INTEGER,
  promotion_ready  INTEGER,
  avg_training_hours REAL,
  training_target_hours REAL,
  calibration_pct  REAL,
  -- the correlation a services business should care about
  rating_vs_utilization_r REAL,
  statement        TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- Product bugs and enhancements
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS product_ticket;
CREATE TABLE product_ticket (
  ticket_id        INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  ticket_ref       TEXT    NOT NULL,
  product          TEXT    NOT NULL,
  module           TEXT,
  ticket_type      TEXT    NOT NULL,   -- bug / enhancement
  severity         TEXT,               -- blocker / high / medium / low
  priority         TEXT,
  title            TEXT    NOT NULL,
  detail           TEXT,
  raised_on        TEXT    NOT NULL,
  raised_by        TEXT,               -- customer / delivery / internal_qa
  customer_id      INTEGER REFERENCES customer(customer_id),
  project_id       INTEGER REFERENCES project(project_id),
  status           TEXT    NOT NULL,   -- open / in_progress / fixed / released /
                                       -- deferred / declined
  resolved_on      TEXT,
  released_in      TEXT,               -- release identifier
  age_days         REAL,
  time_to_resolve_days REAL,
  effort_hours     REAL,
  blocks_go_live   INTEGER NOT NULL DEFAULT 0,
  is_regression    INTEGER NOT NULL DEFAULT 0,
  workaround       TEXT,
  UNIQUE (org_id, ticket_ref)
);
CREATE INDEX ix_ticket_product ON product_ticket(org_id, product, status);
CREATE INDEX ix_ticket_project ON product_ticket(project_id);

DROP TABLE IF EXISTS product_ticket_summary;
CREATE TABLE product_ticket_summary (
  ticket_summary_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  product          TEXT    NOT NULL,
  open_bugs        INTEGER NOT NULL,
  open_blockers    INTEGER NOT NULL,
  open_enhancements INTEGER NOT NULL,
  raised_last_90   INTEGER NOT NULL,
  resolved_last_90 INTEGER NOT NULL,
  net_flow_90      INTEGER NOT NULL,   -- raised minus resolved: is it growing
  median_ttr_days  REAL,
  p90_ttr_days     REAL,
  oldest_open_days REAL,
  regression_pct   REAL,
  customer_raised_pct REAL,
  delivery_effort_hours REAL,          -- delivery time spent on product defects
  delivery_effort_cost REAL,
  projects_affected INTEGER NOT NULL,
  blocking_go_live INTEGER NOT NULL,
  backlog_verdict  TEXT    NOT NULL,   -- shrinking / stable / growing
  statement        TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- The red and amber register: one row per at-risk engagement with the
-- reasons attached, so the list and the explanation cannot drift apart.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS rag_register;
CREATE TABLE rag_register (
  rag_id           INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  project_id       INTEGER NOT NULL REFERENCES project(project_id),
  reported_health  TEXT    NOT NULL,
  computed_health  TEXT,
  health_score     REAL,
  failure_probability_pct REAL,
  is_overridden    INTEGER NOT NULL DEFAULT 0,
  override_note    TEXT,
  divergence_bands INTEGER NOT NULL DEFAULT 0,
  first_amber_on   TEXT,
  first_red_on     TEXT,
  weeks_in_state   REAL,
  -- what is actually wrong, in order
  primary_reason   TEXT    NOT NULL,
  reason_detail    TEXT    NOT NULL,
  -- the exposure
  revenue_at_risk  REAL,
  margin_gap_pts   REAL,
  slip_days        REAL,
  hours_over       REAL,
  open_escalations INTEGER NOT NULL DEFAULT 0,
  latest_csat      REAL,
  latest_csat_comment TEXT,
  -- what to do
  recovery_action  TEXT,
  recovery_owner   TEXT,
  recovery_by      TEXT,
  statement        TEXT    NOT NULL
);
CREATE INDEX ix_rag_org ON rag_register(org_id, reported_health);

DROP TABLE IF EXISTS rag_reason;
CREATE TABLE rag_reason (
  rag_reason_id   INTEGER PRIMARY KEY,
  rag_id          INTEGER NOT NULL REFERENCES rag_register(rag_id),
  rank            INTEGER NOT NULL,
  reason_code     TEXT    NOT NULL,
  reason_label    TEXT    NOT NULL,
  statement       TEXT    NOT NULL,
  metric_value    REAL,
  metric_unit     TEXT,
  threshold_value REAL,
  severity        TEXT    NOT NULL
);
