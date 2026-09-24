-- =====================================================================
-- Layer 3: intelligence outputs
-- Every engine writes its result here with the drivers that produced it,
-- so a number shown in the UI can always be explained and re-audited.
-- Requirements 30-42, 45.
-- =====================================================================

-- Req 30: one run of the intelligence engine over the whole book
CREATE TABLE intelligence_run (
  run_id            INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_at            TEXT NOT NULL DEFAULT (datetime('now')),
  as_of_date        TEXT NOT NULL,
  engine_version    TEXT NOT NULL,
  projects_scored   INTEGER NOT NULL DEFAULT 0,
  runtime_ms        INTEGER,
  -- Observed outturn rates the probability model was calibrated against, and
  -- the intercepts that calibration produced. Stored per run so a score can be
  -- re-read later against the base rate it was judged on.
  base_rates_json   TEXT,
  intercepts_json   TEXT
);

-- Req 31: predictive project risk
CREATE TABLE project_risk_score (
  risk_score_id     INTEGER PRIMARY KEY,
  run_id            INTEGER NOT NULL REFERENCES intelligence_run(run_id),
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  current_health    TEXT NOT NULL,
  predicted_health  TEXT NOT NULL,
  failure_probability_pct REAL NOT NULL,
  p_budget_overrun_pct REAL NOT NULL,
  p_schedule_delay_pct REAL NOT NULL,
  p_margin_below_target_pct REAL NOT NULL,
  confidence        TEXT CHECK (confidence IN ('Low','Medium','High')),
  peer_sample_size  INTEGER,
  UNIQUE (run_id, project_id)
);

-- The "explain WHY" rows. One row per contributing driver, with the
-- weight it carried, so the UI never shows a score without its reasons.
CREATE TABLE risk_driver (
  driver_id         INTEGER PRIMARY KEY,
  risk_score_id     INTEGER NOT NULL REFERENCES project_risk_score(risk_score_id),
  driver_code       TEXT NOT NULL,          -- BURN_RATE, MILESTONE_SLIP, UAT_LATE, PM_CAPACITY,
                                            -- PEER_HISTORY, OPEN_ISSUES, CAPACITY_SHORTFALL,
                                            -- CHANGE_ORDER_DEPENDENCY, RATE_DILUTION, KEY_PERSON
  affects           TEXT NOT NULL CHECK (affects IN ('budget','schedule','margin','delivery','multiple')),
  statement         TEXT NOT NULL,          -- human sentence shown in the UI
  metric_value      REAL,
  metric_unit       TEXT,
  weight            REAL NOT NULL DEFAULT 0,
  direction         TEXT NOT NULL DEFAULT 'adverse' CHECK (direction IN ('adverse','favourable'))
);
CREATE INDEX ix_driver_score ON risk_driver(risk_score_id);

-- Req 32: margin prediction
CREATE TABLE project_margin_prediction (
  prediction_id     INTEGER PRIMARY KEY,
  run_id            INTEGER NOT NULL REFERENCES intelligence_run(run_id),
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  revenue_total     REAL NOT NULL,
  actual_cost       REAL NOT NULL,
  forecast_cost     REAL NOT NULL,
  predicted_cost    REAL NOT NULL,
  -- A project with no recognised revenue yet has no margin to date. That is a
  -- real state, not missing data, so these are nullable.
  current_margin_pct REAL,
  forecast_margin_pct REAL,
  predicted_margin_pct REAL NOT NULL,
  target_margin_pct REAL NOT NULL,
  margin_risk       TEXT NOT NULL CHECK (margin_risk IN ('Low','Medium','High','Critical')),
  predicted_hours_to_complete REAL,
  hours_available   REAL,
  -- The plan basis the driver decomposition starts from. Held so the waterfall
  -- can be re-checked: the drivers must sum exactly to
  -- predicted_margin_pct - plan_margin_pct.
  plan_cost         REAL,
  plan_margin_pct   REAL,
  UNIQUE (run_id, project_id)
);

CREATE TABLE margin_driver (
  margin_driver_id  INTEGER PRIMARY KEY,
  prediction_id     INTEGER NOT NULL REFERENCES project_margin_prediction(prediction_id),
  driver_code       TEXT NOT NULL,
  statement         TEXT NOT NULL,
  margin_impact_pts REAL NOT NULL,          -- percentage points of margin attributed
  metric_value      REAL,
  metric_unit       TEXT
);
CREATE INDEX ix_mdriver_pred ON margin_driver(prediction_id);

-- Req 33: benchmarking against comparable completed projects
CREATE TABLE project_benchmark (
  benchmark_id      INTEGER PRIMARY KEY,
  run_id            INTEGER NOT NULL REFERENCES intelligence_run(run_id),
  project_id        INTEGER NOT NULL REFERENCES project(project_id),
  peer_count        INTEGER NOT NULL,
  match_basis       TEXT,                   -- which attributes defined the cohort
  peer_avg_duration_days REAL,
  peer_avg_hours    REAL,
  peer_avg_revenue  REAL,
  peer_avg_margin_pct REAL,
  peer_avg_delay_days REAL,
  peer_avg_change_orders REAL,
  peer_avg_change_order_value REAL,
  peer_avg_team_size REAL,
  peer_budget_overrun_pct REAL,             -- how much peers overran on average
  tracking_variance_pct REAL,               -- "tracking 23% worse than comparable projects"
  tracking_verdict  TEXT CHECK (tracking_verdict IN ('Better','In line','Worse','Materially worse')),
  UNIQUE (run_id, project_id)
);

CREATE TABLE benchmark_peer (
  benchmark_peer_id INTEGER PRIMARY KEY,
  benchmark_id      INTEGER NOT NULL REFERENCES project_benchmark(benchmark_id),
  peer_project_id   INTEGER NOT NULL REFERENCES project(project_id),
  similarity_pct    REAL NOT NULL
);

-- Req 34-36: acquisition assessment
CREATE TABLE acquisition_assessment (
  assessment_id     INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  period_from       TEXT NOT NULL,
  period_to         TEXT NOT NULL,
  months_of_history INTEGER NOT NULL,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE acquisition_metric (
  metric_id         INTEGER PRIMARY KEY,
  assessment_id     INTEGER NOT NULL REFERENCES acquisition_assessment(assessment_id),
  metric_group      TEXT NOT NULL,          -- Financial / Utilization / Concentration /
                                            -- Delivery / Leakage / Forecast
  metric_code       TEXT NOT NULL,
  metric_label      TEXT NOT NULL,
  metric_value      REAL,
  metric_unit       TEXT NOT NULL CHECK (metric_unit IN ('currency','percent','hours','count','ratio','days')),
  benchmark_value   REAL,                   -- operating company equivalent, for comparison
  variance_vs_benchmark REAL,
  direction_good    TEXT CHECK (direction_good IN ('higher','lower','neutral')),
  UNIQUE (assessment_id, metric_code)
);

-- Req 35: historical profitability by dimension
CREATE TABLE profitability_cut (
  cut_id            INTEGER PRIMARY KEY,
  assessment_id     INTEGER REFERENCES acquisition_assessment(assessment_id),
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  dimension         TEXT NOT NULL,          -- customer / project / product / consultant /
                                            -- practice / geography / project_type /
                                            -- billing_model / project_manager / industry
  dimension_key     TEXT NOT NULL,
  dimension_label   TEXT NOT NULL,
  revenue           REAL NOT NULL DEFAULT 0,
  cost              REAL NOT NULL DEFAULT 0,
  gross_profit      REAL NOT NULL DEFAULT 0,
  margin_pct        REAL,
  hours             REAL NOT NULL DEFAULT 0,
  billable_hours    REAL NOT NULL DEFAULT 0,
  net_hourly_rate   REAL,                   -- recognized revenue / hours spent
  revenue_cost_ratio REAL,
  project_count     INTEGER NOT NULL DEFAULT 0,
  over_budget_count INTEGER NOT NULL DEFAULT 0,
  flag              TEXT                    -- top10 / bottom10 / margin_destroyer /
                                            -- chronic_over_budget / high_rev_low_margin
);
CREATE INDEX ix_cut_dim ON profitability_cut(org_id, dimension);

-- Req 36: acquisition red flags
CREATE TABLE red_flag (
  red_flag_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  assessment_id     INTEGER REFERENCES acquisition_assessment(assessment_id),
  flag_category     TEXT NOT NULL,          -- Customer Risk / Margin Risk / Resource Risk /
                                            -- Delivery Risk / Revenue Risk / Knowledge Risk /
                                            -- Customer Concentration / Data Risk
  severity          TEXT NOT NULL CHECK (severity IN ('Low','Medium','High','Critical')),
  headline          TEXT NOT NULL,
  detail            TEXT,
  metric_value      REAL,
  metric_unit       TEXT,
  threshold_value   REAL,
  evidence_json     TEXT,                   -- the rows behind the claim
  recommended_action TEXT
);
CREATE INDEX ix_flag_org ON red_flag(org_id, severity);

-- Req 37: forward-looking risk predictions
CREATE TABLE future_risk_prediction (
  prediction_id     INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  horizon_month     TEXT,                   -- YYYY-MM the prediction lands in
  prediction_type   TEXT NOT NULL,          -- CAPACITY_SURPLUS / CAPACITY_SHORTFALL /
                                            -- HIRING_REQUIREMENT / MARGIN_SHIFT /
                                            -- UTILIZATION_MISS / CUSTOMER_DEMAND
  statement         TEXT NOT NULL,
  metric_value      REAL,
  metric_unit       TEXT,
  confidence        TEXT CHECK (confidence IN ('Low','Medium','High')),
  basis             TEXT                    -- what data supports it
);

-- Capacity model feeding req 37 and 41
CREATE TABLE capacity_month (
  capacity_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  period_month      TEXT NOT NULL,
  practice_id       INTEGER REFERENCES practice(practice_id),
  available_hours   REAL NOT NULL DEFAULT 0,
  committed_hours   REAL NOT NULL DEFAULT 0,   -- signed work
  pipeline_hours    REAL NOT NULL DEFAULT 0,   -- weighted pipeline
  demand_hours      REAL NOT NULL DEFAULT 0,
  gap_hours         REAL NOT NULL DEFAULT 0,   -- positive = surplus, negative = shortfall
  implied_headcount_gap REAL,
  projected_utilization_pct REAL,
  UNIQUE (org_id, period_month, practice_id, run_id)
);

-- Req 38: forecast accuracy
CREATE TABLE forecast_accuracy (
  accuracy_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  scope             TEXT NOT NULL CHECK (scope IN ('project','project_manager','practice','customer','org')),
  scope_key         TEXT NOT NULL,
  scope_label       TEXT NOT NULL,
  sample_size       INTEGER NOT NULL DEFAULT 0,
  original_forecast_hours REAL,
  revised_forecast_hours REAL,
  actual_hours      REAL,
  hours_variance_pct REAL,                  -- negative = underestimated
  original_forecast_margin_pct REAL,
  actual_margin_pct REAL,
  margin_variance_pts REAL,
  accuracy_pct      REAL,
  bias              TEXT CHECK (bias IN ('Underestimates','Overestimates','Accurate')),
  statement         TEXT,
  UNIQUE (org_id, scope, scope_key, run_id)
);

-- Req 39: what-if simulator
CREATE TABLE scenario (
  scenario_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  scenario_name     TEXT NOT NULL,
  scenario_type     TEXT NOT NULL,          -- UTILIZATION_CHANGE / HEADCOUNT_CHANGE /
                                            -- PROJECT_DELAY / RATE_CHANGE / CUSTOMER_LOSS /
                                            -- RESOURCE_MOVE / NEW_DEAL
  question          TEXT NOT NULL,          -- as the executive phrased it
  parameters_json   TEXT NOT NULL,
  created_by        TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  is_saved          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE scenario_result (
  result_id         INTEGER PRIMARY KEY,
  scenario_id       INTEGER NOT NULL REFERENCES scenario(scenario_id),
  measure           TEXT NOT NULL,          -- revenue / cost / margin_pct / utilization_pct /
                                            -- capacity_hours / delivery_risk / headcount_required
  baseline_value    REAL,
  scenario_value    REAL,
  delta_value       REAL,
  delta_pct         REAL,
  unit              TEXT,
  commentary        TEXT,
  UNIQUE (scenario_id, measure)
);

-- Req 40: deal go / no-go
CREATE TABLE deal_assessment (
  deal_assessment_id INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  opportunity_id    INTEGER REFERENCES pipeline_opportunity(opportunity_id),
  deal_name         TEXT NOT NULL,
  customer_id       INTEGER REFERENCES customer(customer_id),
  contract_value    REAL NOT NULL,
  proposed_hours    REAL NOT NULL,
  timeline_months   REAL,
  billing_model     TEXT,
  required_skills   TEXT,
  recommendation    TEXT NOT NULL CHECK (recommendation IN ('GO','REVIEW','NO-GO')),
  expected_margin_pct REAL,
  target_margin_pct REAL,
  expected_revenue  REAL,
  expected_profit   REAL,
  resource_availability_pct REAL,
  delivery_risk_pct REAL,
  schedule_risk_pct REAL,
  customer_risk_pct REAL,
  peer_overrun_pct  REAL,
  peer_count        INTEGER,
  assessed_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE deal_assessment_factor (
  factor_id         INTEGER PRIMARY KEY,
  deal_assessment_id INTEGER NOT NULL REFERENCES deal_assessment(deal_assessment_id),
  factor_code       TEXT NOT NULL,
  factor_label      TEXT NOT NULL,
  verdict           TEXT NOT NULL CHECK (verdict IN ('pass','caution','fail')),
  statement         TEXT NOT NULL,
  metric_value      REAL,
  metric_unit       TEXT
);

CREATE TABLE deal_recommendation (
  deal_rec_id       INTEGER PRIMARY KEY,
  deal_assessment_id INTEGER NOT NULL REFERENCES deal_assessment(deal_assessment_id),
  action            TEXT NOT NULL,          -- e.g. increase SOW value / reduce scope
  statement         TEXT NOT NULL,
  quantified_value  REAL,
  unit              TEXT
);

-- Req 41: PS Operating System rollup, five questions
CREATE TABLE ps_os_snapshot (
  snapshot_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  as_of_date        TEXT NOT NULL,
  -- 1. are we making money
  revenue_ytd       REAL, revenue_forecast_fy REAL,
  margin_pct        REAL, margin_target_pct REAL,
  revenue_at_risk   REAL,
  -- 2. are projects healthy
  projects_total    INTEGER, projects_green INTEGER, projects_yellow INTEGER, projects_red INTEGER,
  projects_budget_risk INTEGER, projects_schedule_risk INTEGER, projects_margin_risk INTEGER,
  -- 3. are people utilized
  utilization_pct   REAL, utilization_target_pct REAL,
  capacity_hours    REAL, bench_hours REAL, overallocated_headcount INTEGER,
  -- 4. can we deliver future demand
  pipeline_value    REAL, weighted_pipeline_value REAL,
  resource_demand_hours REAL, capacity_gap_hours REAL, hiring_requirement_fte REAL,
  overall_health    TEXT CHECK (overall_health IN ('Healthy','Watch','At Risk')),
  UNIQUE (org_id, as_of_date, run_id)
);

-- 5. what should leadership do
CREATE TABLE leadership_action (
  action_id         INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  rank              INTEGER NOT NULL,
  action_type       TEXT NOT NULL,          -- REVIEW_PROJECT / REALLOCATE / ESCALATE /
                                            -- REVIEW_REVENUE_AT_RISK / HIRE / REPRICE
  headline          TEXT NOT NULL,
  rationale         TEXT,
  entity_table      TEXT,
  entity_pk         INTEGER,
  value_at_stake    REAL,
  urgency           TEXT CHECK (urgency IN ('This week','This month','This quarter')),
  status            TEXT NOT NULL DEFAULT 'Open'
                      CHECK (status IN ('Open','In Progress','Done','Dismissed'))
);

-- Req 42: weekly executive briefing, generated from live data
CREATE TABLE executive_briefing (
  briefing_id       INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  briefing_date     TEXT NOT NULL,
  period_label      TEXT,
  overall_health    TEXT,
  revenue_actual    REAL, revenue_forecast REAL,
  margin_pct        REAL, margin_target_pct REAL,
  utilization_pct   REAL, utilization_target_pct REAL,
  revenue_at_risk   REAL,
  projects_at_risk  INTEGER,
  narrative_md      TEXT,
  generated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (org_id, briefing_date)
);

CREATE TABLE briefing_item (
  briefing_item_id  INTEGER PRIMARY KEY,
  briefing_id       INTEGER NOT NULL REFERENCES executive_briefing(briefing_id),
  section           TEXT NOT NULL CHECK (section IN ('key_issue','recommended_action','win','watch_item')),
  rank              INTEGER NOT NULL,
  statement         TEXT NOT NULL,
  entity_table      TEXT,
  entity_pk         INTEGER,
  value_at_stake    REAL
);
