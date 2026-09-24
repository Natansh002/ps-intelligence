-- =====================================================================
-- Billing against revenue recognition, and the economics of each billing
-- model.
--
-- Two questions that a PSA product answers only in fragments.
--
-- The first is where delivery and cash diverge. Revenue recognition follows
-- delivery; invoicing follows the contract. The gap between them is not an
-- error, it is a position, and it has two signs. Where recognition runs
-- ahead the organisation has delivered work it has not billed, which is an
-- unbilled receivable and the older it gets the harder it is to defend.
-- Where invoicing runs ahead it has billed work it has not delivered, which
-- is deferred revenue and a commitment rather than an asset. Reporting one
-- number for "the gap" hides which of the two you have.
--
-- The second is which commercial model actually makes money. The honest
-- answer needs the spread and not just the average, because the models
-- differ in who carries the overrun risk: on time and materials the customer
-- carries it, on fixed fee the delivery organisation does. A model with a
-- higher mean and a much wider distribution is not simply better, it is a
-- different bet, and the tables below are shaped to make that visible rather
-- than to rank two averages.
-- =====================================================================

DROP TABLE IF EXISTS billing_position;
CREATE TABLE billing_position (
  billing_position_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,   -- org / billing_model / status /
                                       -- practice / product
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  projects         INTEGER NOT NULL,
  recognised       REAL    NOT NULL,
  invoiced         REAL    NOT NULL,
  -- The two signs, kept apart. A netted figure of zero can mean everything
  -- is billed on time, or that a large receivable and a large deferral are
  -- cancelling each other out on different engagements.
  unbilled         REAL    NOT NULL,   -- recognised ahead of invoiced
  deferred         REAL    NOT NULL,   -- invoiced ahead of recognised
  net_position     REAL    NOT NULL,
  projects_unbilled INTEGER NOT NULL,
  projects_deferred INTEGER NOT NULL,
  billed_pct       REAL,               -- invoiced as a share of recognised
  -- How long the unbilled side has been sitting there. Age is what turns a
  -- normal billing lag into a write-off.
  unbilled_over_60 REAL,
  unbilled_over_90 REAL,
  oldest_unbilled_days REAL,
  statement        TEXT    NOT NULL
);

-- The month-by-month picture, so the lag is visible as a shape rather than
-- asserted as a number.
DROP TABLE IF EXISTS billing_month;
CREATE TABLE billing_month (
  billing_month_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  period_month     TEXT    NOT NULL,
  recognised       REAL    NOT NULL,
  invoiced         REAL    NOT NULL,
  cum_recognised   REAL    NOT NULL,
  cum_invoiced     REAL    NOT NULL,
  cum_gap          REAL    NOT NULL,
  billed_pct       REAL,
  UNIQUE (org_id, period_month)
);

-- The engagements furthest out of line, either way, with enough context to
-- act on them rather than merely notice them.
DROP TABLE IF EXISTS billing_exception;
CREATE TABLE billing_exception (
  billing_exception_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  project_id       INTEGER NOT NULL REFERENCES project(project_id),
  direction        TEXT    NOT NULL,   -- unbilled / deferred
  recognised       REAL    NOT NULL,
  invoiced         REAL    NOT NULL,
  amount           REAL    NOT NULL,
  pct_of_contract  REAL,
  last_invoice_month TEXT,
  months_since_invoice REAL,
  project_status   TEXT    NOT NULL,
  billing_model    TEXT    NOT NULL,
  cause            TEXT    NOT NULL,   -- what the data says is behind it
  action           TEXT    NOT NULL,
  statement        TEXT    NOT NULL
);
CREATE INDEX ix_billing_exc ON billing_exception(org_id, direction, amount);

-- =====================================================================
-- Billing model economics
-- =====================================================================
DROP TABLE IF EXISTS model_economics;
CREATE TABLE model_economics (
  model_econ_id    INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  billing_model    TEXT    NOT NULL,
  risk_carried_by  TEXT    NOT NULL,   -- customer / delivery / shared
  projects         INTEGER NOT NULL,
  revenue          REAL    NOT NULL,
  cost             REAL    NOT NULL,
  share_of_revenue_pct REAL,
  -- Central tendency, and then the part that actually matters.
  margin_pct       REAL,               -- revenue-weighted, the portfolio view
  margin_mean_pct  REAL,               -- per project, unweighted
  margin_median_pct REAL,
  margin_p25_pct   REAL,
  margin_p75_pct   REAL,
  margin_min_pct   REAL,
  margin_max_pct   REAL,
  margin_stdev_pts REAL,               -- the spread: who carries the risk
  projects_negative INTEGER NOT NULL,
  pct_negative     REAL,
  -- Effort against plan, which is where a fixed-fee margin is won or lost.
  overrun_median   REAL,
  overrun_p90      REAL,
  pct_over_budget  REAL,
  -- Rate realisation, from the same basis the rate analysis uses.
  discount_pct     REAL,
  realised_rate    REAL,
  -- The mix behind the number, because the models are not sold on the same
  -- work and a raw comparison of two averages is confounded by that.
  median_size_hours REAL,
  top_project_type TEXT,
  top_project_type_pct REAL,
  pct_partner_led  REAL,
  median_duration_months REAL,
  verdict          TEXT    NOT NULL,
  statement        TEXT    NOT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0
);

-- Margin outcomes bucketed, so the distribution can be drawn rather than
-- described. A histogram is the only honest way to show two models whose
-- means are close and whose spreads are not.
DROP TABLE IF EXISTS model_margin_band;
CREATE TABLE model_margin_band (
  band_id          INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  billing_model    TEXT    NOT NULL,
  band_from        REAL    NOT NULL,
  band_to          REAL    NOT NULL,
  band_label       TEXT    NOT NULL,
  projects         INTEGER NOT NULL,
  share_pct        REAL    NOT NULL,
  revenue          REAL    NOT NULL,
  sort_order       INTEGER NOT NULL
);

-- The comparison the question actually asks for, stated once, with the
-- confounds named rather than buried.
DROP TABLE IF EXISTS model_comparison;
CREATE TABLE model_comparison (
  comparison_id    INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  model_a          TEXT    NOT NULL,
  model_b          TEXT    NOT NULL,
  metric           TEXT    NOT NULL,
  metric_label     TEXT    NOT NULL,
  value_a          REAL,
  value_b          REAL,
  delta            REAL,
  unit             TEXT,
  favours          TEXT,               -- a / b / neither
  reading          TEXT    NOT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0
);

-- =====================================================================
-- Earned value: cost and schedule performance
--
-- The console had consumption, progress and margin but no CPI or SPI, which
-- is a real gap. Those two are the standard way a delivery organisation
-- separates two questions that consumption alone conflates: are we spending
-- more than the work is worth, and are we behind. A project at 60% consumed
-- and 60% complete is fine; one at 60% consumed and 40% complete is over
-- cost; one at 40% consumed and 40% complete against a plan that expected
-- 60% is on cost and behind schedule. Only the pair distinguishes them.
--
-- Definitions, stated because every organisation claims to use the standard
-- ones and few use the same ones:
--   PV  planned value      budget cost x planned complete at the as-of date
--   EV  earned value       budget cost x actual complete
--   AC  actual cost        cost incurred to date
--   CPI EV / AC            above 1 is under cost
--   SPI EV / PV            above 1 is ahead of schedule
--   TCPI (BAC - EV) / (BAC - AC)   the efficiency the remainder now needs
--
-- Percent complete is the earned-plan figure this engine already derives from
-- the task plan, not a typed-in number and not hours spent over estimate. That
-- choice matters more here than anywhere else: CPI computed from an
-- hours-spent percent complete is algebraically incapable of being below 1,
-- which is why a system that defines progress that way can report a healthy
-- CPI on a project losing money.
-- =====================================================================
DROP TABLE IF EXISTS project_evm;
CREATE TABLE project_evm (
  evm_id           INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  project_id       INTEGER NOT NULL REFERENCES project(project_id),
  as_of_date       TEXT    NOT NULL,
  bac              REAL    NOT NULL,   -- budget at completion, cost
  planned_pct      REAL,               -- where the plan says it should be
  earned_pct       REAL,               -- where the plan says it is
  pv               REAL    NOT NULL,
  ev               REAL    NOT NULL,
  ac               REAL    NOT NULL,
  cv               REAL    NOT NULL,   -- EV - AC, currency
  sv               REAL    NOT NULL,   -- EV - PV, currency
  cpi              REAL,
  spi              REAL,
  csi              REAL,               -- CPI x SPI, the combined index
  tcpi             REAL,
  eac_cpi          REAL,               -- BAC / CPI, the independent estimate
  vac              REAL,               -- BAC - EAC
  cost_band        TEXT    NOT NULL,   -- under / on / over
  schedule_band    TEXT    NOT NULL,   -- ahead / on / behind
  quadrant         TEXT    NOT NULL,   -- the pair, named
  -- Materiality. An index is a ratio, and a ratio computed on a few days of
  -- a six-month plan is arithmetic rather than information: an engagement two
  -- weeks in reads SPI 0.25 because the plan expected 8% and 2% is done, and
  -- one with a fortnight of cost against a 200k budget reads whatever the
  -- rounding says. Those rows are kept and shown, because hiding rows is how
  -- a portfolio view becomes untrustworthy, but they are excluded from the
  -- roll-ups and labelled with the reason.
  is_reportable    INTEGER NOT NULL DEFAULT 1,
  exclusion_reason TEXT,
  statement        TEXT    NOT NULL,
  UNIQUE (org_id, project_id, run_id)
);
CREATE INDEX ix_evm_org ON project_evm(org_id, cpi);

-- Portfolio roll-up, weighted by budget rather than averaged, because an
-- unweighted mean CPI lets a 40-hour engagement offset a 4,000-hour one.
DROP TABLE IF EXISTS evm_summary;
CREATE TABLE evm_summary (
  evm_summary_id   INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,   -- org / practice / product /
                                       -- billing_model / project_manager
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  projects         INTEGER NOT NULL,
  projects_excluded INTEGER NOT NULL DEFAULT 0,
  bac              REAL    NOT NULL,
  pv               REAL    NOT NULL,
  ev               REAL    NOT NULL,
  ac               REAL    NOT NULL,
  cpi              REAL,               -- budget weighted
  spi              REAL,
  cpi_median       REAL,               -- and unweighted, for the comparison
  spi_median       REAL,
  projects_over_cost INTEGER NOT NULL,
  projects_behind  INTEGER NOT NULL,
  projects_both    INTEGER NOT NULL,
  vac_total        REAL,
  statement        TEXT    NOT NULL
);
