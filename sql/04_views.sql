-- =====================================================================
-- Layer 4: reporting views. These are the definitions the UI and the
-- engine both read, so a metric has exactly one definition.
-- =====================================================================

-- Actual labour cost, revenue at bill rate, and hours per project
CREATE VIEW v_project_actuals AS
SELECT
  p.project_id,
  p.org_id,
  COALESCE(SUM(t.hours), 0)                                        AS actual_hours,
  COALESCE(SUM(CASE WHEN t.is_billable = 1 THEN t.hours END), 0)    AS billable_hours,
  COALESCE(SUM(t.hours * COALESCE(t.cost_rate, 0)), 0)              AS actual_labor_cost,
  COALESCE(SUM(CASE WHEN t.is_billable = 1
                    THEN t.hours * COALESCE(t.bill_rate, 0) END), 0) AS billable_value,
  COALESCE(SUM(CASE WHEN t.is_billable = 1 AND t.invoiced = 0
                    THEN t.hours * COALESCE(t.bill_rate, 0) END), 0) AS unbilled_value,
  MIN(t.entry_date)                                                 AS first_time_entry,
  MAX(t.entry_date)                                                 AS last_time_entry,
  COUNT(DISTINCT t.employee_id)                                     AS distinct_resources
FROM project p
LEFT JOIN time_entry t
       ON t.project_id = p.project_id AND t.approval_status = 'Approved'
GROUP BY p.project_id;

-- Change order rollup
CREATE VIEW v_project_change_orders AS
SELECT
  p.project_id,
  COUNT(co.change_order_id)                                             AS co_count,
  COALESCE(SUM(CASE WHEN co.status = 'Approved' THEN co.co_value END),0) AS co_value_approved,
  COALESCE(SUM(CASE WHEN co.status = 'Approved' THEN co.co_hours END),0) AS co_hours_approved
FROM project p
LEFT JOIN change_order co ON co.project_id = p.project_id
GROUP BY p.project_id;

-- Schedule position: milestone slip and critical-path slip
CREATE VIEW v_project_schedule AS
SELECT
  p.project_id,
  COUNT(CASE WHEN pt.is_milestone = 1 THEN 1 END)                        AS milestone_count,
  COUNT(CASE WHEN pt.is_milestone = 1 AND pt.status <> 'Complete'
              AND date(pt.planned_end) < date('now')            THEN 1 END) AS milestones_late,
  COUNT(CASE WHEN pt.is_milestone = 1 AND pt.is_critical = 1
              AND pt.status <> 'Complete'
              AND date(pt.planned_end) < date('now')            THEN 1 END) AS critical_milestones_late,
  COUNT(CASE WHEN pt.status = 'Blocked' THEN 1 END)                      AS tasks_blocked,
  COALESCE(SUM(pt.planned_hours), 0)                                     AS plan_hours,
  COALESCE(AVG(pt.percent_complete), 0)                                  AS avg_percent_complete,
  COALESCE(SUM(pt.planned_hours * pt.percent_complete / 100.0), 0)       AS earned_plan_hours
FROM project p
LEFT JOIN project_task pt
       ON pt.project_id = p.project_id
      AND (pt.plan_version_id IS NULL OR pt.plan_version_id IN
           (SELECT plan_version_id FROM project_plan_version
             WHERE project_id = p.project_id AND is_current = 1))
GROUP BY p.project_id;

-- Open risk and issue pressure
CREATE VIEW v_project_raid AS
SELECT
  p.project_id,
  COUNT(CASE WHEN ri.status IN ('Open','Mitigating') THEN 1 END)          AS open_items,
  COUNT(CASE WHEN ri.status IN ('Open','Mitigating')
              AND ri.priority IN ('High','Critical')          THEN 1 END) AS open_high_items,
  COUNT(CASE WHEN ri.status IN ('Open','Mitigating')
              AND ri.is_customer_raised = 1                   THEN 1 END) AS open_customer_items,
  COALESCE(MAX(ri.impact * ri.probability), 0)                            AS max_risk_exposure
FROM project p
LEFT JOIN risk_issue ri ON ri.project_id = p.project_id
GROUP BY p.project_id;

-- Recognized revenue and invoiced revenue to date, from the monthly ledger
CREATE VIEW v_project_revenue AS
SELECT
  p.project_id,
  COALESCE(SUM(f.recognized_revenue), 0) AS recognized_revenue_to_date,
  COALESCE(SUM(f.invoiced_revenue), 0)   AS invoiced_revenue_to_date,
  COALESCE(SUM(f.labor_cost + f.other_cost), 0) AS ledger_cost_to_date,
  COUNT(f.pfm_id)                        AS months_active
FROM project p
LEFT JOIN project_financial_month f ON f.project_id = p.project_id
GROUP BY p.project_id;

-- One row per project with everything the engine needs
CREATE VIEW v_project_360 AS
SELECT
  p.project_id, p.org_id, p.project_code, p.project_name,
  c.customer_id, c.customer_name, c.industry, c.region, c.geography, c.customer_status,
  pr.practice_id, pr.practice_code, pr.practice_name, pr.segment,
  p.project_type, p.product, p.methodology, p.billing_model,
  p.project_manager_id, pm.employee_name AS project_manager_name,
  p.start_date, p.planned_end_date, p.actual_end_date,
  p.planned_go_live_date, p.go_live_date,
  p.project_status, p.project_health,
  p.contract_value, p.sow_value, p.budget_hours, p.budget_cost,
  p.target_margin_pct,
  p.budget_hours   + co.co_hours_approved                     AS total_budget_hours,
  a.actual_hours, a.billable_hours, a.actual_labor_cost,
  a.billable_value, a.unbilled_value, a.distinct_resources,
  co.co_count, co.co_value_approved, co.co_hours_approved,
  s.milestone_count, s.milestones_late, s.critical_milestones_late,
  s.tasks_blocked, s.avg_percent_complete, s.plan_hours, s.earned_plan_hours,
  r.open_items, r.open_high_items, r.open_customer_items,
  rv.recognized_revenue_to_date, rv.invoiced_revenue_to_date, rv.months_active,
  -- Contract-value revenue is billing-model aware: T&M earns on hours,
  -- capped T&M is bounded by the ceiling, fixed fee earns the contract.
  CASE
    WHEN p.billing_model = 'Time and Materials'
      THEN a.billable_value
    WHEN p.billing_model = 'Capped T&M'
      THEN MIN(a.billable_value, p.contract_value + co.co_value_approved)
    ELSE p.contract_value + co.co_value_approved
  END                                                          AS total_revenue,
  CASE WHEN (p.budget_hours + co.co_hours_approved) > 0
       THEN a.actual_hours / (p.budget_hours + co.co_hours_approved) * 100 END AS hours_consumed_pct,
  -- Margin to date compares revenue actually recognized with cost actually
  -- incurred, so an in-flight project is not credited with revenue it has
  -- not earned.
  CASE WHEN rv.recognized_revenue_to_date > 0
       THEN (rv.recognized_revenue_to_date - a.actual_labor_cost)
            / rv.recognized_revenue_to_date * 100 END                          AS current_margin_pct,
  CASE WHEN a.actual_hours > 0
       THEN a.actual_labor_cost / a.actual_hours END                           AS avg_cost_rate,
  CASE WHEN a.billable_hours > 0
       THEN rv.recognized_revenue_to_date / a.billable_hours END               AS net_hourly_rate,
  julianday(COALESCE(p.actual_end_date, date('now'))) - julianday(p.start_date) AS elapsed_days,
  julianday(p.planned_end_date) - julianday(p.start_date)                      AS planned_days,
  CASE WHEN p.actual_end_date IS NOT NULL
       THEN julianday(p.actual_end_date) - julianday(p.planned_end_date)
       ELSE NULL END                                                           AS end_slip_days
FROM project p
JOIN customer c            ON c.customer_id = p.customer_id
LEFT JOIN practice pr      ON pr.practice_id = p.practice_id
LEFT JOIN employee pm      ON pm.employee_id = p.project_manager_id
LEFT JOIN v_project_actuals a       ON a.project_id = p.project_id
LEFT JOIN v_project_change_orders co ON co.project_id = p.project_id
LEFT JOIN v_project_schedule s      ON s.project_id = p.project_id
LEFT JOIN v_project_raid r          ON r.project_id = p.project_id
LEFT JOIN v_project_revenue rv      ON rv.project_id = p.project_id;

-- Monthly utilization by consultant. Denominator is working-day capacity.
CREATE VIEW v_employee_month_utilization AS
SELECT
  e.employee_id, e.org_id, e.employee_name, e.practice_id, e.role,
  strftime('%Y-%m', t.entry_date)                                     AS period_month,
  SUM(t.hours)                                                        AS total_hours,
  SUM(CASE WHEN t.is_billable = 1 THEN t.hours ELSE 0 END)            AS billable_hours,
  SUM(t.hours * COALESCE(t.cost_rate, 0))                             AS cost,
  SUM(CASE WHEN t.is_billable = 1 THEN t.hours * COALESCE(t.bill_rate,0) ELSE 0 END) AS billable_value,
  e.weekly_capacity_hours * 52.0 / 12.0                               AS capacity_hours,
  CASE WHEN e.weekly_capacity_hours > 0
       THEN SUM(CASE WHEN t.is_billable = 1 THEN t.hours ELSE 0 END)
            / (e.weekly_capacity_hours * 52.0 / 12.0) * 100 END        AS utilization_pct
FROM employee e
JOIN time_entry t ON t.employee_id = e.employee_id AND t.approval_status = 'Approved'
GROUP BY e.employee_id, strftime('%Y-%m', t.entry_date);

-- Customer level profitability over the whole history
CREATE VIEW v_customer_profitability AS
SELECT
  c.customer_id, c.org_id, c.customer_name, c.industry, c.region,
  COUNT(DISTINCT p.project_id)                    AS project_count,
  SUM(v.total_revenue)                            AS revenue,
  SUM(v.actual_labor_cost)                        AS cost,
  SUM(v.total_revenue) - SUM(v.actual_labor_cost) AS gross_profit,
  CASE WHEN SUM(v.total_revenue) > 0
       THEN (SUM(v.total_revenue) - SUM(v.actual_labor_cost)) / SUM(v.total_revenue) * 100 END AS margin_pct,
  SUM(v.actual_hours)                             AS hours
FROM customer c
LEFT JOIN project p     ON p.customer_id = c.customer_id
LEFT JOIN v_project_360 v ON v.project_id = p.project_id
GROUP BY c.customer_id;

-- Latest intelligence run per org, so the UI never has to guess
CREATE VIEW v_latest_run AS
SELECT org_id, MAX(run_id) AS run_id FROM intelligence_run GROUP BY org_id;
