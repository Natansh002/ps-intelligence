-- =====================================================================
-- Delivery and implementation checks, and the resource requirement model.
--
-- The checks catalogue is a table rather than a hard-coded list for a
-- specific reason. Reviewing this console against Certinia and Rocketlane
-- makes one thing clear: both ship the *structure* for delivery governance
-- and leave the rules to the customer. Certinia gives you four Green/Yellow/
-- Red picklists on the project (Project, Financial, Schedule, Scope) with no
-- shipped scoring logic and the note that automation is "available via
-- Salesforce Flow Builder". Rocketlane gives you a free-text status and a
-- manual at-risk flag. Neither documents a resource over-allocation rule at
-- all, and Rocketlane documents the absence of one outright.
--
-- So the rules are the product here, and a rule that lives in code cannot be
-- reviewed by the person accountable for it. Each row records what it
-- asserts, why that matters, whether the source platform blocks it or merely
-- reports it, and where the semantics come from.
-- =====================================================================

DROP TABLE IF EXISTS check_run;
CREATE TABLE check_run (
  check_run_id      INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_at            TEXT    NOT NULL,
  as_of_date        TEXT    NOT NULL,
  engine_version    TEXT    NOT NULL,
  checks_run        INTEGER NOT NULL,
  findings_total    INTEGER NOT NULL,
  blocking_total    INTEGER NOT NULL,
  runtime_ms        INTEGER NOT NULL,
  readiness_pct     REAL,           -- weighted share of gates passed
  readiness_verdict TEXT            -- ready / conditional / not ready
);

DROP TABLE IF EXISTS delivery_check;
CREATE TABLE delivery_check (
  check_code     TEXT PRIMARY KEY,
  family         TEXT NOT NULL,     -- billing / time / actuals / resource /
                                    -- governance / close / migration / config
  family_label   TEXT NOT NULL,
  title          TEXT NOT NULL,
  assertion      TEXT NOT NULL,     -- what must be true
  why_it_matters TEXT NOT NULL,     -- the consequence of it not being true
  severity       TEXT NOT NULL,     -- critical / high / medium / low
  -- Whether the platform this rule is modelled on prevents the condition or
  -- merely lets you find it afterwards. The distinction is the whole point of
  -- a checks console: 'silent' is where the money leaks.
  enforcement    TEXT NOT NULL,     -- blocked / detected / silent
  is_gate        INTEGER NOT NULL DEFAULT 0,   -- counts toward go-live readiness
  gate_weight    REAL    NOT NULL DEFAULT 1,
  -- readiness   - configuration, controls and data integrity. Clearable
  --               before cutover, and 100% is a reachable number.
  -- operational - how delivery is actually going. Real money, real findings,
  --               but never zero in a live book and not a cutover blocker.
  -- Mixing the two is what makes a readiness score unreachable and therefore
  -- ignored, so they are scored separately and labelled here.
  check_class    TEXT NOT NULL DEFAULT 'readiness',
  scope          TEXT NOT NULL,     -- what population it runs over
  remediation    TEXT NOT NULL,
  vendor_basis   TEXT,              -- the documented semantics it mirrors
  vendor_source  TEXT,              -- where that documentation lives
  sort_order     INTEGER NOT NULL DEFAULT 0
);

DROP TABLE IF EXISTS check_result;
CREATE TABLE check_result (
  check_result_id INTEGER PRIMARY KEY,
  check_run_id    INTEGER NOT NULL REFERENCES check_run(check_run_id),
  check_code      TEXT    NOT NULL REFERENCES delivery_check(check_code),
  population      INTEGER NOT NULL,   -- records the check examined
  failing         INTEGER NOT NULL,   -- records that failed it
  value_at_stake  REAL,               -- money or hours the failures represent
  value_unit      TEXT,
  status          TEXT    NOT NULL,   -- pass / warn / fail / not_applicable
  statement       TEXT    NOT NULL,   -- the finding as a sentence
  UNIQUE (check_run_id, check_code)
);

DROP TABLE IF EXISTS check_finding;
CREATE TABLE check_finding (
  check_finding_id INTEGER PRIMARY KEY,
  check_run_id     INTEGER NOT NULL REFERENCES check_run(check_run_id),
  check_code       TEXT    NOT NULL REFERENCES delivery_check(check_code),
  entity_table     TEXT    NOT NULL,
  entity_pk        INTEGER,
  entity_label     TEXT    NOT NULL,
  observed         TEXT,
  expected         TEXT,
  message          TEXT    NOT NULL,
  severity         TEXT    NOT NULL,
  value_at_stake   REAL,
  value_unit       TEXT
);
CREATE INDEX ix_check_finding_run  ON check_finding(check_run_id, check_code);

-- =====================================================================
-- Metric definitions, published rather than implied.
--
-- Reviewing the vendor documentation, the single most useful thing either
-- product publishes is its arithmetic, and the most damaging thing is where
-- it does not. Rocketlane publishes utilisation and margin as one-line
-- formulas. Certinia publishes three period-scoped utilisation formulas with
-- credited and excluded adjustments in both numerator and denominator, and
-- publishes no margin formula at all. Neither publishes an EAC derivation.
--
-- This table holds the formula this engine actually uses, beside the vendor
-- formula it corresponds to, so a reviewer can see where the two differ and
-- decide which they want.
-- =====================================================================
DROP TABLE IF EXISTS metric_definition;
CREATE TABLE metric_definition (
  metric_code   TEXT PRIMARY KEY,
  metric_label  TEXT NOT NULL,
  formula       TEXT NOT NULL,
  numerator     TEXT,
  denominator   TEXT,
  notes         TEXT NOT NULL,
  vendor        TEXT,        -- whose published formula this matches
  vendor_formula TEXT,
  vendor_source TEXT,
  divergence    TEXT,        -- where this engine deliberately differs
  sort_order    INTEGER NOT NULL DEFAULT 0
);

-- =====================================================================
-- Resource requirement by product line.
--
-- The question a services leader actually asks is not "what is my
-- utilisation" but "how many consultants of which kind do I need in which
-- product line, by when, to deliver the backlog and the pipeline without
-- putting the bench under water". That needs the inputs to be explicit and
-- editable, so they are stored rather than assumed.
-- =====================================================================
DROP TABLE IF EXISTS resource_plan;
CREATE TABLE resource_plan (
  plan_id           INTEGER PRIMARY KEY,
  org_id            INTEGER NOT NULL REFERENCES org(org_id),
  run_id            INTEGER REFERENCES intelligence_run(run_id),
  plan_name         TEXT    NOT NULL,
  as_of_date        TEXT    NOT NULL,
  horizon_months    INTEGER NOT NULL,
  created_at        TEXT    NOT NULL,
  -- org-wide assumptions
  recruit_lead_weeks       REAL NOT NULL,
  attrition_pct_annual     REAL NOT NULL,
  standard_hours_per_week  REAL NOT NULL,
  working_weeks_per_year   REAL NOT NULL
);

DROP TABLE IF EXISTS resource_plan_line;
CREATE TABLE resource_plan_line (
  plan_line_id     INTEGER PRIMARY KEY,
  plan_id          INTEGER NOT NULL REFERENCES resource_plan(plan_id),
  product_line      TEXT    NOT NULL,   -- the product, e.g. Finance, Payroll
  practice_id       INTEGER REFERENCES practice(practice_id),
  practice_label    TEXT,
  -- ---- inputs, every one of them stated ----
  time_to_go_live_weeks     REAL NOT NULL,  -- median implementation duration
  time_to_go_live_source    TEXT NOT NULL,  -- measured or assumed
  existing_consultants      REAL NOT NULL,  -- billable headcount today
  existing_fte              REAL NOT NULL,  -- capacity-weighted, part time counted properly
  billable_util_target_pct  REAL NOT NULL,
  productive_util_target_pct REAL NOT NULL, -- billable plus productive non-billable
  ramp_weeks                REAL NOT NULL,  -- new hire to fully productive
  ramp_util_pct             REAL NOT NULL,  -- utilisation during ramp
  training_hours_per_year   REAL NOT NULL,  -- certification and enablement
  pipeline_value            REAL NOT NULL,
  pipeline_hours            REAL NOT NULL,
  pipeline_weighted_hours   REAL NOT NULL,  -- probability weighted
  backlog_hours             REAL NOT NULL,  -- committed, remaining on live work
  avg_deal_hours            REAL NOT NULL,
  realised_rate             REAL,           -- revenue per billable hour delivered
  -- Trailing delivered hours per month. Beyond the pipeline window there is
  -- no demand signal, and treating that as zero demand invents a bench that
  -- does not exist. The run rate is what the far months revert to, and the
  -- coverage figure says how much of the plan rests on it.
  run_rate_hours_month      REAL,
  -- How far above the utilisation it was priced at this line has actually
  -- been running. Reported on its own rather than folded into the gap,
  -- because a team working above target is not a capacity shortfall and
  -- treating it as one recommends hiring against something a utilisation
  -- conversation would fix.
  over_target_pct           REAL,
  -- Months of committed work at the rate the line actually delivers, beside
  -- the months remaining before the dates that work is promised against.
  -- Where the first materially exceeds the second the line has a schedule
  -- problem, and no recruitment lands inside the gap.
  backlog_months_at_run_rate REAL,
  backlog_window_months      REAL,
  months_short               INTEGER,   -- months with a gap above 0.25 FTE
  avg_gap_when_short         REAL,
  coverage_committed_pct    REAL,           -- demand that is contracted
  coverage_pipeline_pct     REAL,           -- demand from weighted pipeline
  coverage_runrate_pct      REAL,           -- demand from the run rate
  -- ---- outputs ----
  demand_hours_horizon      REAL NOT NULL,
  supply_hours_horizon      REAL NOT NULL,
  gap_hours_horizon         REAL NOT NULL,
  productive_hours_per_fte  REAL NOT NULL,  -- per month, after training and absence
  required_fte              REAL NOT NULL,
  fte_gap                   REAL NOT NULL,
  -- Hires needed only to stand still. Reported apart from the gap because
  -- "you need three consultants" means something very different when two of
  -- them are replacing leavers, and a plan that merges the two is arguing
  -- for growth using the cost of standing still.
  attrition_backfill_fte    REAL,
  hire_count                INTEGER NOT NULL,   -- net new, beyond backfill
  first_gap_month           TEXT,
  hire_by_date              TEXT,
  peak_gap_fte              REAL,
  peak_gap_month            TEXT,
  bench_risk_fte            REAL,           -- surplus, if the gap is negative
  revenue_at_risk           REAL,           -- unservable pipeline value
  verdict                   TEXT NOT NULL,  -- hire / subcontract / cross-train /
                                            -- sell more / hold
  statement                 TEXT NOT NULL
);

DROP TABLE IF EXISTS resource_plan_month;
CREATE TABLE resource_plan_month (
  plan_month_id    INTEGER PRIMARY KEY,
  plan_line_id     INTEGER NOT NULL REFERENCES resource_plan_line(plan_line_id),
  period_month     TEXT    NOT NULL,
  backlog_hours    REAL    NOT NULL,   -- contracted, remaining
  pipeline_hours   REAL    NOT NULL,   -- weighted pipeline
  unsold_hours     REAL    NOT NULL DEFAULT 0,  -- run-rate expectation
  demand_hours     REAL    NOT NULL,
  visibility_pct   REAL,               -- share of demand that is not run rate
  headcount_fte    REAL    NOT NULL,
  ramping_fte      REAL    NOT NULL,
  capacity_hours   REAL    NOT NULL,   -- raw available
  productive_hours REAL    NOT NULL,   -- after training, absence and ramp
  billable_hours   REAL    NOT NULL,   -- at the billable target
  gap_hours        REAL    NOT NULL,
  gap_fte          REAL    NOT NULL,
  projected_util_pct REAL,
  band             TEXT    NOT NULL,   -- short / balanced / bench
  UNIQUE (plan_line_id, period_month)
);

DROP TABLE IF EXISTS resource_plan_action;
CREATE TABLE resource_plan_action (
  plan_action_id  INTEGER PRIMARY KEY,
  plan_line_id    INTEGER NOT NULL REFERENCES resource_plan_line(plan_line_id),
  rank            INTEGER NOT NULL,
  action_type     TEXT    NOT NULL,   -- hire / subcontract / cross_train /
                                      -- rephase / sell / release
  headline        TEXT    NOT NULL,
  rationale       TEXT    NOT NULL,
  quantity        REAL,
  unit            TEXT,
  by_date         TEXT,
  value_at_stake  REAL
);

DROP TABLE IF EXISTS resource_plan_sensitivity;
CREATE TABLE resource_plan_sensitivity (
  sensitivity_id  INTEGER PRIMARY KEY,
  plan_line_id    INTEGER NOT NULL REFERENCES resource_plan_line(plan_line_id),
  lever           TEXT    NOT NULL,   -- which input was moved
  lever_label     TEXT    NOT NULL,
  shift           TEXT    NOT NULL,   -- how far it was moved
  fte_gap_before  REAL    NOT NULL,
  fte_gap_after   REAL    NOT NULL,
  fte_delta       REAL    NOT NULL,
  statement       TEXT    NOT NULL
);
