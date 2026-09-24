-- =====================================================================
-- Automation and AI opportunities, measured rather than asserted
--
-- The usual version of this analysis is a workshop output: a list of things
-- somebody thinks could be automated, each with a percentage saving beside it
-- and nothing underneath. It reads well and it cannot be defended, because the
-- baseline is a guess and the saving is a multiplication of two guesses.
--
-- So this is built the other way round, in three separable parts:
--
--   1. The baseline is measured. Every opportunity's current effort comes from
--      a query over this database, and the row carries the query in words so
--      the figure can be challenged.
--   2. The rates are inputs. How much of that effort a tool can touch, how
--      much of the touched part actually goes away, and how much still needs a
--      person to review it are assumptions, they are held as editable rows,
--      and every one states where it came from.
--   3. The saving is the product of the two, and it is labelled as modelled.
--
-- Keeping those apart is the whole point. It means a reader can accept the
-- baseline and argue with the rate, which is a conversation that converges,
-- rather than accepting or rejecting a single number, which is one that does
-- not.
--
-- One more distinction the row carries: what kind of automation this is.
-- Deterministic rules, an integration, an AI-assisted step with a person
-- confirming, and an AI-autonomous step are four different risk profiles, and
-- the last two need a guardrail written down. The requirement this system is
-- built against is explicit that AI must not modify financial or project data
-- without confirmation, so no opportunity here is allowed to imply it.
-- =====================================================================

DROP TABLE IF EXISTS automation_input;
CREATE TABLE automation_input (
  automation_input_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  input_code       TEXT    NOT NULL,
  input_label      TEXT    NOT NULL,
  input_group      TEXT    NOT NULL,   -- economics / effort / adoption
  value            REAL    NOT NULL,
  unit             TEXT    NOT NULL,   -- pct / currency / hours / days / ratio
  -- Where the number comes from. "measured" means this database computed it;
  -- "assumption" means somebody chose it and it is theirs to defend.
  origin           TEXT    NOT NULL CHECK (origin IN ('measured','assumption')),
  basis            TEXT    NOT NULL,
  low              REAL,               -- the range worth testing against
  high             REAL,
  sort_order       INTEGER NOT NULL,
  UNIQUE (org_id, input_code, run_id)
);

DROP TABLE IF EXISTS automation_opportunity;
CREATE TABLE automation_opportunity (
  opportunity_id   INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  opp_code         TEXT    NOT NULL,
  process          TEXT    NOT NULL,   -- the work as the team would name it
  area             TEXT    NOT NULL,   -- migration / delivery / commercial /
                                       -- support / people
  -- Four risk profiles, not one word. The last two carry a guardrail.
  automation_type  TEXT    NOT NULL CHECK (automation_type IN
                     ('rules','integration','ai_assisted','ai_autonomous')),

  -- 1. the measured baseline
  baseline_hours   REAL,
  baseline_cost    REAL,
  baseline_count   REAL,               -- the unit count, where hours are not it
  baseline_unit    TEXT,
  -- The baseline is a 24-month total, because that is the history the console
  -- holds. The annual figure is stored beside it rather than left to the
  -- reader, since quoting a two-year total next to a per-year saving is the
  -- easiest way to overstate a case by exactly twice.
  annual_baseline_hours REAL,
  baseline_basis   TEXT    NOT NULL,   -- the query, in words

  -- 2. the rates applied, each an input
  addressable_pct  REAL    NOT NULL,   -- of the baseline a tool can touch
  automation_pct   REAL    NOT NULL,   -- of that, what goes away
  review_pct       REAL    NOT NULL,   -- of that, what a person still checks

  -- 3. the modelled outcome
  hours_saved      REAL,
  cost_saved       REAL,
  value_unlocked   REAL,               -- recovered revenue or avoided leakage,
                                       -- kept apart from a cost saving
  implementation_days REAL,
  implementation_cost REAL,
  run_cost_year    REAL,
  net_year_one     REAL,
  payback_months   REAL,

  -- what would have to be true
  feasibility      TEXT    NOT NULL,   -- high / medium / low
  data_readiness   TEXT    NOT NULL,   -- ready / partial / not held
  confidence       TEXT    NOT NULL,
  guardrail        TEXT,               -- mandatory on the two AI types
  prerequisite     TEXT,
  verdict          TEXT    NOT NULL,   -- do now / next / investigate / no
  statement        TEXT    NOT NULL,
  rank             INTEGER NOT NULL
);
CREATE INDEX ix_auto_opp ON automation_opportunity(org_id, rank);

-- The evidence behind one opportunity, so the baseline is inspectable rather
-- than merely cited. A figure with no way to reach the rows underneath it is
-- the thing this table exists to avoid.
DROP TABLE IF EXISTS automation_evidence;
CREATE TABLE automation_evidence (
  evidence_id      INTEGER PRIMARY KEY,
  opportunity_id   INTEGER NOT NULL REFERENCES automation_opportunity(opportunity_id),
  measure          TEXT    NOT NULL,
  value            REAL,
  unit             TEXT,
  source_view      TEXT,               -- where in this console to see it
  detail           TEXT    NOT NULL,
  sort_order       INTEGER NOT NULL
);
CREATE INDEX ix_auto_ev ON automation_evidence(opportunity_id, sort_order);

DROP TABLE IF EXISTS automation_summary;
CREATE TABLE automation_summary (
  automation_summary_id INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  scope            TEXT    NOT NULL,   -- org / area / automation_type
  scope_key        TEXT,
  scope_label      TEXT    NOT NULL,
  opportunities    INTEGER NOT NULL,
  baseline_hours   REAL,
  baseline_cost    REAL,
  hours_saved      REAL,
  cost_saved       REAL,
  value_unlocked   REAL,
  implementation_cost REAL,
  net_year_one     REAL,
  payback_months   REAL,
  -- The split that decides how much of this needs governance rather than
  -- procurement: an AI-assisted step with a person confirming is a different
  -- approval conversation from a scheduled rule.
  ai_share_pct     REAL,
  statement        TEXT    NOT NULL,
  sort_order       INTEGER NOT NULL
);

-- Sensitivity, because the automation rate is the assumption the whole model
-- rests on and a single-point answer invites the reader to accept or dismiss
-- it whole. Kept per scenario rather than per opportunity: the question being
-- asked is what the programme is worth if the rates are wrong.
DROP TABLE IF EXISTS automation_sensitivity;
CREATE TABLE automation_sensitivity (
  sensitivity_id   INTEGER PRIMARY KEY,
  org_id           INTEGER NOT NULL REFERENCES org(org_id),
  run_id           INTEGER REFERENCES intelligence_run(run_id),
  lever            TEXT    NOT NULL,
  lever_label      TEXT    NOT NULL,
  shift            REAL    NOT NULL,
  shift_label      TEXT    NOT NULL,
  hours_saved      REAL,
  net_year_one     REAL,
  delta_vs_base    REAL,
  reading          TEXT    NOT NULL,
  sort_order       INTEGER NOT NULL
);
