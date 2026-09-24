"""
Export everything the front end needs as one JSON payload.

The UI is a single self-contained page, so it cannot query SQLite. This module
is the boundary: it runs the queries once and hands over the answers. Anything
the page shows has to appear here first, which keeps the presentation layer
free of business logic.
"""
import json
import os
import sqlite3
from collections import defaultdict

import scenarios

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
OUT = os.path.join(ROOT, "out", "psa_data.json")


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def run_meta(con, run_id):
    r = one(con, "SELECT * FROM intelligence_run WHERE run_id=?", (run_id,))
    for k in ("base_rates_json", "intercepts_json"):
        r[k.replace("_json", "")] = json.loads(r.pop(k) or "{}")
    return r


def one(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None


def billing_payload(con, oid):
    """Billing against recognition: the positions, the month series, the
    exceptions. Exceptions carry the customer name because a billing
    conversation is with a customer, not with a project code."""
    pos = rows(con, """SELECT * FROM billing_position WHERE org_id=?
                        ORDER BY scope, recognised DESC""", (oid,))
    if not pos:
        return None
    return {
        "positions": pos,
        "months": rows(con, """SELECT * FROM billing_month WHERE org_id=?
                                ORDER BY period_month""", (oid,)),
        "exceptions": rows(con, """
            SELECT e.*, p.project_code, p.project_name, c.customer_name
              FROM billing_exception e
              JOIN project p USING(project_id)
              LEFT JOIN customer c ON c.customer_id=p.customer_id
             WHERE e.org_id=? ORDER BY e.direction, e.amount DESC""", (oid,)),
    }


def models_payload(con, oid):
    econ = rows(con, """SELECT * FROM model_economics WHERE org_id=?
                         ORDER BY sort_order""", (oid,))
    if not econ:
        return None
    return {
        "economics": econ,
        "bands": rows(con, """SELECT * FROM model_margin_band WHERE org_id=?
                               ORDER BY billing_model, sort_order""", (oid,)),
        "comparison": rows(con, """SELECT * FROM model_comparison WHERE org_id=?
                                    ORDER BY sort_order""", (oid,)),
    }


def automation_payload(con, oid):
    """
    The automation and AI analysis: the inputs, the opportunities with their
    evidence, the roll-ups and the sensitivity.

    Evidence is attached to its opportunity rather than exported flat, because
    the page never shows a modelled saving without the measured figure it was
    derived from.
    """
    opps = rows(con, """SELECT * FROM automation_opportunity WHERE org_id=?
                         ORDER BY rank""", (oid,))
    if not opps:
        return None
    ev = defaultdict(list)
    for e in rows(con, """SELECT e.* FROM automation_evidence e
                            JOIN automation_opportunity o USING(opportunity_id)
                           WHERE o.org_id=? ORDER BY e.sort_order""", (oid,)):
        ev[e["opportunity_id"]].append(e)
    for o in opps:
        o["evidence"] = ev.get(o["opportunity_id"], [])
    return {
        "inputs": rows(con, """SELECT * FROM automation_input WHERE org_id=?
                                ORDER BY sort_order""", (oid,)),
        "opportunities": opps,
        "summary": rows(con, """SELECT * FROM automation_summary WHERE org_id=?
                                 ORDER BY sort_order""", (oid,)),
        "sensitivity": rows(con, """SELECT * FROM automation_sensitivity
                                     WHERE org_id=? ORDER BY sort_order""",
                            (oid,)),
    }


def evm_payload(con, oid):
    """
    Cost and schedule performance per live engagement, plus the roll-ups.

    Every row is exported, including the ones held back from the roll-up as too
    early to carry an index, because the page shows those with their reason
    rather than dropping them. A portfolio view that quietly excludes rows is
    one nobody can reconcile against the live book.
    """
    summary = rows(con, """SELECT * FROM evm_summary WHERE org_id=?
                            ORDER BY CASE scope WHEN 'org' THEN 0 WHEN 'practice'
                                     THEN 1 WHEN 'product' THEN 2 ELSE 3 END,
                                     scope_label""", (oid,))
    if not summary:
        return None
    return {
        "summary": summary,
        "projects": rows(con, """
            SELECT e.*, p.project_code, p.project_name, p.product,
                   p.billing_model, p.project_health, p.planned_end_date,
                   c.customer_name, pr.practice_name,
                   emp.employee_name AS pm_name
              FROM project_evm e
              JOIN project p USING(project_id)
              LEFT JOIN customer c ON c.customer_id=p.customer_id
              LEFT JOIN practice pr ON pr.practice_id=p.practice_id
              LEFT JOIN employee emp ON emp.employee_id=p.project_manager_id
             WHERE e.org_id=?
             ORDER BY e.is_reportable DESC, e.csi ASC, e.cpi ASC""", (oid,)),
    }


def checks_payload(con, oid):
    """
    The check run, its results and a bounded slice of the findings.

    Findings are attached to their check rather than exported as a flat list,
    because the page never shows a finding without the assertion it failed.
    """
    run = one(con, """SELECT * FROM check_run WHERE org_id=?
                       ORDER BY check_run_id DESC LIMIT 1""", (oid,))
    if not run:
        return None
    rid = run["check_run_id"]
    results = rows(con, """
        SELECT r.*, c.family, c.family_label, c.title, c.assertion,
               c.why_it_matters, c.severity, c.enforcement, c.is_gate,
               c.gate_weight, c.check_class, c.scope, c.remediation,
               c.vendor_basis, c.vendor_source, c.sort_order
          FROM check_result r JOIN delivery_check c USING(check_code)
         WHERE r.check_run_id=? ORDER BY c.sort_order""", (rid,))
    by_code = defaultdict(list)
    for f in rows(con, """SELECT * FROM check_finding WHERE check_run_id=?
                           ORDER BY check_code,
                                    COALESCE(value_at_stake,0) DESC""", (rid,)):
        if len(by_code[f["check_code"]]) < 12:
            by_code[f["check_code"]].append(f)
    for r in results:
        r["findings"] = by_code.get(r["check_code"], [])
    return {
        "run": run,
        "results": results,
        "metrics": rows(con, "SELECT * FROM metric_definition ORDER BY sort_order"),
    }


def resourcing_payload(con, oid):
    plan = one(con, """SELECT * FROM resource_plan WHERE org_id=?
                        ORDER BY plan_id DESC LIMIT 1""", (oid,))
    if not plan:
        return None
    lines = rows(con, """SELECT * FROM resource_plan_line WHERE plan_id=?
                          ORDER BY peak_gap_fte DESC""", (plan["plan_id"],))
    for l in lines:
        lid = l["plan_line_id"]
        l["months"] = rows(con, """SELECT * FROM resource_plan_month
                                    WHERE plan_line_id=? ORDER BY period_month""",
                           (lid,))
        l["actions"] = rows(con, """SELECT * FROM resource_plan_action
                                     WHERE plan_line_id=? ORDER BY rank""", (lid,))
        l["sensitivity"] = rows(con, """SELECT * FROM resource_plan_sensitivity
                                         WHERE plan_line_id=?
                                         ORDER BY ABS(fte_delta) DESC""", (lid,))
    return {"plan": plan, "lines": lines}


def diligence_payload(con, oid):
    """
    The diligence pack, with the evidence attached to each derived row.

    Verbatim comments, phase causes and RAG reasons all travel with the thing
    they explain. Exporting them as parallel lists would let the page pair a
    summary with the wrong evidence, which is the failure the whole design of
    these tables is meant to prevent.
    """
    phases = rows(con, """SELECT * FROM phase_duration_analysis WHERE org_id=?
                           ORDER BY scope, scope_label, rank_by_slip""", (oid,))
    causes = defaultdict(list)
    for c in rows(con, """SELECT c.* FROM phase_slip_cause c
                            JOIN phase_duration_analysis p USING(phase_analysis_id)
                           WHERE p.org_id=? ORDER BY c.rank""", (oid,)):
        causes[c["phase_analysis_id"]].append(c)
    for p in phases:
        p["causes"] = causes.get(p["phase_analysis_id"], [])

    rag = rows(con, """
        SELECT g.*, p.project_code, p.project_name, p.product, p.billing_model,
               p.contract_value, p.planned_end_date, c.customer_name,
               e.employee_name AS pm_name,
               (SELECT MIN(responded_on) FROM csat_response cr
                 WHERE cr.project_id=g.project_id
                   AND cr.score = g.latest_csat) AS csat_on
          FROM rag_register g
          JOIN project p USING(project_id)
          LEFT JOIN customer c ON c.customer_id=p.customer_id
          LEFT JOIN employee e ON e.employee_id=p.project_manager_id
         WHERE g.org_id=?
         ORDER BY CASE g.reported_health WHEN 'Red' THEN 0 WHEN 'Yellow' THEN 1
                  ELSE 2 END, COALESCE(g.revenue_at_risk,0) DESC""", (oid,))
    reasons = defaultdict(list)
    for r in rows(con, """SELECT r.* FROM rag_reason r JOIN rag_register g USING(rag_id)
                           WHERE g.org_id=? ORDER BY r.rank""", (oid,)):
        reasons[r["rag_id"]].append(r)
    for g in rag:
        g["reasons"] = reasons.get(g["rag_id"], [])

    return {
        "csat": rows(con, """SELECT * FROM csat_summary WHERE org_id=?
                              ORDER BY scope, avg_score""", (oid,)),
        # The verbatims a reader will actually want: the critical ones first,
        # then the strongest, because both are evidence and a page of neutral
        # threes is not.
        "comments": rows(con, """
            SELECT c.csat_id, c.score, c.comment, c.theme, c.sentiment,
                   c.survey_point, c.responded_on, c.respondent_role,
                   c.would_reference, cu.customer_name, p.project_code,
                   p.project_name, p.product
              FROM csat_response c
              JOIN customer cu ON cu.customer_id=c.customer_id
              LEFT JOIN project p ON p.project_id=c.project_id
             WHERE c.org_id=? AND c.comment IS NOT NULL
             ORDER BY CASE WHEN c.score<=3 THEN 0 WHEN c.score>=4.5 THEN 1
                           ELSE 2 END, c.responded_on DESC
             LIMIT 90""", (oid,)),
        "phases": phases,
        "hypercare": rows(con, """SELECT * FROM hypercare_summary WHERE org_id=?
                                   ORDER BY scope, projects DESC""", (oid,)),
        "hypercare_worst": rows(con, """
            SELECT h.*, p.project_code, p.project_name, p.product,
                   c.customer_name
              FROM hypercare_period h JOIN project p USING(project_id)
              LEFT JOIN customer c ON c.customer_id=p.customer_id
             WHERE h.org_id=? AND h.overrun_days IS NOT NULL
             ORDER BY h.overrun_days DESC LIMIT 15""", (oid,)),
        "rates": rows(con, """SELECT * FROM rate_analysis WHERE org_id=?
                               ORDER BY dimension, billable_hours DESC""", (oid,)),
        "performance": rows(con, """SELECT * FROM performance_summary WHERE org_id=?
                                     ORDER BY scope, scope_label""", (oid,)),
        "cycles": rows(con, """SELECT * FROM performance_cycle WHERE org_id=?
                                ORDER BY sort_order""", (oid,)),
        # Individual review forms, for the three most recent cycles, for the
        # people a new leader would ask about first: the highest and lowest
        # rated, and anyone flagged as a retention risk.
        "reviews": rows(con, """
            SELECT r.*, e.employee_name, e.role, e.practice_id,
                   pr.practice_name, cy.cycle_code, cy.cycle_label,
                   cy.sort_order,
                   m.employee_name AS reviewer_name
              FROM performance_review r
              JOIN performance_cycle cy USING(cycle_id)
              JOIN employee e ON e.employee_id=r.employee_id
              LEFT JOIN practice pr ON pr.practice_id=e.practice_id
              LEFT JOIN employee m ON m.employee_id=r.reviewer_employee_id
             WHERE cy.org_id=?
               AND r.employee_id IN (
                   SELECT employee_id FROM performance_review pr2
                     JOIN performance_cycle cy2 USING(cycle_id)
                    WHERE cy2.org_id=? AND cy2.sort_order=(
                          SELECT MAX(sort_order) FROM performance_cycle
                           WHERE org_id=?)
                    ORDER BY (pr2.flight_risk='high') DESC,
                             ABS(pr2.overall_rating-3.5) DESC
                    LIMIT 14)
             ORDER BY e.employee_name, cy.sort_order""", (oid, oid, oid)),
        "tickets": rows(con, """SELECT * FROM product_ticket_summary WHERE org_id=?
                                 ORDER BY open_bugs DESC""", (oid,)),
        "ticket_detail": rows(con, """
            SELECT t.*, c.customer_name, p.project_code
              FROM product_ticket t
              LEFT JOIN customer c ON c.customer_id=t.customer_id
              LEFT JOIN project p ON p.project_id=t.project_id
             WHERE t.org_id=? AND t.status IN ('open','in_progress')
             ORDER BY CASE t.severity WHEN 'blocker' THEN 0 WHEN 'high' THEN 1
                           WHEN 'medium' THEN 2 ELSE 3 END,
                      t.age_days DESC
             LIMIT 70""", (oid,)),
        "rag": rag,
    }


def build_org(con, org):
    oid = org["org_id"]
    run_id = one(con, "SELECT MAX(run_id) r FROM intelligence_run WHERE org_id=?", (oid,))["r"]

    projects = rows(con, """
        SELECT v.*, rs.failure_probability_pct, rs.p_budget_overrun_pct,
               rs.p_schedule_delay_pct, rs.p_margin_below_target_pct,
               rs.predicted_health, rs.confidence, rs.peer_sample_size, rs.risk_score_id,
               mp.prediction_id, mp.revenue_total, mp.actual_cost, mp.forecast_cost,
               mp.predicted_cost, mp.forecast_margin_pct, mp.predicted_margin_pct,
               mp.margin_risk, mp.predicted_hours_to_complete, mp.hours_available,
               mp.plan_cost, mp.plan_margin_pct,
               b.benchmark_id, b.peer_count, b.match_basis, b.peer_avg_duration_days,
               b.peer_avg_hours, b.peer_avg_revenue, b.peer_avg_margin_pct,
               b.peer_avg_delay_days, b.peer_avg_change_orders,
               b.peer_avg_change_order_value, b.peer_avg_team_size,
               b.peer_budget_overrun_pct, b.tracking_variance_pct, b.tracking_verdict
          FROM v_project_360 v
          LEFT JOIN project_risk_score rs
                 ON rs.project_id=v.project_id AND rs.run_id=?
          LEFT JOIN project_margin_prediction mp
                 ON mp.project_id=v.project_id AND mp.run_id=?
          LEFT JOIN project_benchmark b
                 ON b.project_id=v.project_id AND b.run_id=?
         WHERE v.org_id=?""", (run_id, run_id, run_id, oid))

    drivers = defaultdict(list)
    for d in rows(con, """SELECT rd.*, rs.project_id FROM risk_driver rd
                            JOIN project_risk_score rs USING(risk_score_id)
                           WHERE rs.run_id=? ORDER BY rd.weight DESC""", (run_id,)):
        drivers[d["project_id"]].append(d)
    mdrivers = defaultdict(list)
    for d in rows(con, """SELECT md.*, mp.project_id FROM margin_driver md
                            JOIN project_margin_prediction mp USING(prediction_id)
                           WHERE mp.run_id=? ORDER BY ABS(md.margin_impact_pts) DESC""",
                  (run_id,)):
        mdrivers[d["project_id"]].append(d)
    peers = defaultdict(list)
    for p in rows(con, """SELECT bp.*, b.project_id, pr.project_code, pr.project_name
                            FROM benchmark_peer bp
                            JOIN project_benchmark b USING(benchmark_id)
                            JOIN project pr ON pr.project_id=bp.peer_project_id
                           WHERE b.run_id=? ORDER BY bp.similarity_pct DESC""", (run_id,)):
        peers[p["project_id"]].append(p)

    slim_keys = ("project_id", "project_code", "project_name", "customer_name",
                 "practice_code", "practice_name", "product", "project_type",
                 "methodology", "billing_model", "project_manager_name",
                 "project_status", "project_health", "start_date", "planned_end_date",
                 "actual_end_date", "total_revenue", "total_budget_hours",
                 "actual_hours", "billable_hours", "actual_labor_cost",
                 "recognized_revenue_to_date", "current_margin_pct", "target_margin_pct",
                 "hours_consumed_pct", "co_count", "co_value_approved",
                 "milestones_late", "critical_milestones_late", "open_items",
                 "open_high_items", "open_customer_items", "avg_percent_complete",
                 "distinct_resources", "end_slip_days", "net_hourly_rate",
                 "unbilled_value", "region", "industry", "geography", "segment")
    live_keys = slim_keys + (
        "failure_probability_pct", "p_budget_overrun_pct", "p_schedule_delay_pct",
        "p_margin_below_target_pct", "predicted_health", "confidence",
        "peer_sample_size", "revenue_total", "actual_cost", "forecast_cost",
        "predicted_cost", "forecast_margin_pct", "predicted_margin_pct", "margin_risk",
        "predicted_hours_to_complete", "hours_available", "plan_cost",
        "plan_margin_pct", "peer_count", "match_basis",
        "peer_avg_duration_days", "peer_avg_hours", "peer_avg_revenue",
        "peer_avg_margin_pct", "peer_avg_delay_days", "peer_avg_change_orders",
        "peer_avg_change_order_value", "peer_avg_team_size", "peer_budget_overrun_pct",
        "tracking_variance_pct", "tracking_verdict")

    live, portfolio = [], []
    for p in projects:
        if p["project_status"] in ("In Flight", "Not Started") and p["risk_score_id"]:
            rec = {k: p.get(k) for k in live_keys}
            rec["risk_drivers"] = [
                {kk: d[kk] for kk in ("driver_code", "affects", "statement",
                                      "metric_value", "metric_unit", "weight", "direction")}
                for d in drivers.get(p["project_id"], [])]
            rec["margin_drivers"] = [
                {kk: d[kk] for kk in ("driver_code", "statement", "margin_impact_pts",
                                      "metric_value", "metric_unit")}
                for d in mdrivers.get(p["project_id"], [])]
            rec["peers"] = [{"project_code": q["project_code"],
                             "project_name": q["project_name"],
                             "similarity_pct": q["similarity_pct"]}
                            for q in peers.get(p["project_id"], [])[:12]]
            live.append(rec)
        portfolio.append({k: p.get(k) for k in slim_keys})

    monthly = rows(con, """
        SELECT f.period_month,
               SUM(f.recognized_revenue) revenue,
               SUM(f.invoiced_revenue) invoiced,
               SUM(f.labor_cost + f.other_cost) cost,
               SUM(f.budget_amount) budget
          FROM project_financial_month f JOIN project p USING(project_id)
         WHERE p.org_id=? GROUP BY f.period_month ORDER BY f.period_month""", (oid,))
    hours_month = {r["m"]: r for r in rows(con, """
        SELECT strftime('%Y-%m', entry_date) m, SUM(hours) hours,
               SUM(CASE WHEN is_billable=1 THEN hours ELSE 0 END) billable
          FROM time_entry WHERE org_id=? AND approval_status='Approved'
         GROUP BY m""", (oid,))}
    cap_month = one(con, """SELECT SUM(weekly_capacity_hours)*52.0/12.0 c FROM employee
                             WHERE org_id=? AND is_active=1 AND is_billable=1""",
                    (oid,))["c"] or 1
    for m in monthly:
        h = hours_month.get(m["period_month"], {})
        m["hours"] = h.get("hours")
        m["billable_hours"] = h.get("billable")
        m["utilization_pct"] = (h.get("billable") or 0) / cap_month * 100
        m["margin_pct"] = ((m["revenue"] - m["cost"]) / m["revenue"] * 100
                           if m["revenue"] else None)

    cuts = defaultdict(list)
    for r in rows(con, """SELECT * FROM profitability_cut WHERE org_id=?
                           ORDER BY dimension, gross_profit DESC""", (oid,)):
        cuts[r["dimension"]].append(r)
    trimmed = {}
    for dim, rs in cuts.items():
        trimmed[dim] = rs if len(rs) <= 40 else rs[:20] + rs[-20:]

    people = rows(con, """
        SELECT e.employee_id, e.employee_name, e.role, e.location, e.employment_type,
               pr.practice_code, pr.practice_name, e.cost_rate, e.billing_rate,
               e.utilization_target_pct,
               COALESCE(SUM(CASE WHEN t.is_billable=1 THEN t.hours END),0) billable_hours,
               COALESCE(SUM(t.hours),0) total_hours,
               COUNT(DISTINCT t.project_id) projects,
               e.weekly_capacity_hours*52.0/12.0*24 capacity_hours
          FROM employee e
          LEFT JOIN practice pr ON pr.practice_id=e.practice_id
          LEFT JOIN time_entry t ON t.employee_id=e.employee_id
               AND t.approval_status='Approved' AND t.entry_date >= date('2024-09-01')
         WHERE e.org_id=? AND e.is_billable=1
         GROUP BY e.employee_id""", (oid,))
    for p in people:
        p["utilization_pct"] = (p["billable_hours"] / p["capacity_hours"] * 100
                                if p["capacity_hours"] else None)

    dq = rows(con, """SELECT category, rule_code, severity, COUNT(*) n,
                             MIN(recommendation) recommendation
                        FROM data_quality_finding WHERE org_id=?
                       GROUP BY category, rule_code, severity ORDER BY n DESC""", (oid,))
    dq_examples = rows(con, """SELECT category, rule_code, severity, entity_table,
                                      entity_label, message, recommendation
                                 FROM data_quality_finding WHERE org_id=?
                                ORDER BY severity, rule_code LIMIT 120""", (oid,))

    batches = rows(con, """SELECT b.*, t.template_code, t.template_name, t.target_table,
                                  t.min_history_months
                             FROM import_batch b JOIN import_template t USING(template_id)
                            WHERE b.org_id=? ORDER BY t.sort_order""", (oid,))
    findings = defaultdict(list)
    for f in rows(con, """SELECT f.*, t.template_code FROM validation_finding f
                            JOIN import_batch b USING(batch_id)
                            JOIN import_template t ON t.template_id=b.template_id
                           WHERE b.org_id=? ORDER BY f.severity, f.rule_code""", (oid,)):
        findings[f["template_code"]].append(f)

    mappings = defaultdict(list)
    for r in rows(con, """SELECT ms.dimension, ms.set_name, mr.* FROM mapping_rule mr
                            JOIN mapping_set ms USING(mapping_set_id)
                           WHERE ms.org_id=? ORDER BY ms.dimension, mr.legacy_value""",
                  (oid,)):
        mappings[r["dimension"]].append(r)

    scen = []
    for s in rows(con, "SELECT * FROM scenario WHERE org_id=? ORDER BY scenario_id", (oid,)):
        s["parameters"] = json.loads(s.pop("parameters_json"))
        s["results"] = rows(con, "SELECT * FROM scenario_result WHERE scenario_id=?",
                            (s["scenario_id"],))
        scen.append(s)

    deals = []
    for d in rows(con, """SELECT d.*, c.customer_name FROM deal_assessment d
                            LEFT JOIN customer c ON c.customer_id=d.customer_id
                           WHERE d.org_id=? ORDER BY d.contract_value DESC""", (oid,)):
        d["factors"] = rows(con, """SELECT * FROM deal_assessment_factor
                                     WHERE deal_assessment_id=?""",
                            (d["deal_assessment_id"],))
        d["recommendations"] = rows(con, """SELECT * FROM deal_recommendation
                                             WHERE deal_assessment_id=?""",
                                    (d["deal_assessment_id"],))
        deals.append(d)

    flags = rows(con, """SELECT * FROM red_flag WHERE org_id=? ORDER BY
                          CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1
                                        WHEN 'Medium' THEN 2 ELSE 3 END""", (oid,))
    for f in flags:
        f["evidence"] = json.loads(f.pop("evidence_json") or "[]")

    briefing = one(con, """SELECT * FROM executive_briefing WHERE org_id=?
                            ORDER BY briefing_date DESC LIMIT 1""", (oid,))
    if briefing:
        briefing["items"] = rows(con, """SELECT * FROM briefing_item WHERE briefing_id=?
                                          ORDER BY section, rank""",
                                 (briefing["briefing_id"],))

    return {
        "org": org,
        "run": run_meta(con, run_id),
        "practices": rows(con, "SELECT * FROM practice WHERE org_id=?", (oid,)),
        "psos": one(con, """SELECT * FROM ps_os_snapshot WHERE org_id=? AND run_id=?""",
                    (oid, run_id)),
        "revenue_below_target": one(con, """SELECT SUM(mp.revenue_total) v
              FROM project_margin_prediction mp JOIN project p USING(project_id)
             WHERE p.org_id=? AND mp.run_id=? AND p.project_status='In Flight'
               AND mp.predicted_margin_pct < mp.target_margin_pct""", (oid, run_id))["v"],
        "actions": rows(con, """SELECT * FROM leadership_action WHERE org_id=? AND run_id=?
                                 ORDER BY rank""", (oid, run_id)),
        "briefing": briefing,
        "live_projects": live,
        "portfolio": portfolio,
        "monthly": monthly,
        "profitability": trimmed,
        "people": people,
        "capacity": rows(con, """SELECT c.*, pr.practice_code, pr.practice_name
                                   FROM capacity_month c
                                   LEFT JOIN practice pr USING(practice_id)
                                  WHERE c.org_id=? AND c.run_id=?
                                  ORDER BY c.period_month, pr.practice_code""",
                         (oid, run_id)),
        "future_risks": rows(con, """SELECT * FROM future_risk_prediction
                                      WHERE org_id=? AND run_id=?""", (oid, run_id)),
        "forecast_accuracy": rows(con, """SELECT * FROM forecast_accuracy
                                           WHERE org_id=? AND run_id=?
                                           ORDER BY scope, hours_variance_pct""",
                                  (oid, run_id)),
        "red_flags": flags,
        "assessment": one(con, """SELECT * FROM acquisition_assessment WHERE org_id=?
                                   ORDER BY assessment_id DESC LIMIT 1""", (oid,)),
        "metrics": rows(con, """SELECT m.* FROM acquisition_metric m
                                  JOIN acquisition_assessment a USING(assessment_id)
                                 WHERE a.org_id=? ORDER BY m.metric_id""", (oid,)),
        "data_quality": {"summary": dq, "examples": dq_examples},
        "import_batches": batches,
        "validation_findings": findings,
        "mappings": mappings,
        "migration": one(con, """SELECT * FROM migration_run WHERE org_id=?
                                  ORDER BY migration_run_id DESC LIMIT 1""", (oid,)),
        "lineage_count": one(con, """SELECT COUNT(*) n FROM migration_lineage ml
                                       JOIN migration_run mr USING(migration_run_id)
                                      WHERE mr.org_id=?""", (oid,))["n"],
        "scenarios": scen,
        "deals": deals,
        "baseline": scenarios.baseline(con, oid),
        "cohort_stats": scenarios.cohort_stats(con, oid),
        "billing": billing_payload(con, oid),
        "models": models_payload(con, oid),
        "checks": checks_payload(con, oid),
        "evm": evm_payload(con, oid),
        "automation": automation_payload(con, oid),
        "resourcing": resourcing_payload(con, oid),
        "diligence": diligence_payload(con, oid),
        "counts": {
            t: one(con, f"SELECT COUNT(*) n FROM {t} WHERE org_id=?", (oid,))["n"]
            for t in ("customer", "employee", "project", "contract", "time_entry")
        },
    }


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    payload = {
        "generated_at": one(con, "SELECT datetime('now') t")["t"],
        "as_of": "2026-08-31",
        "engine_version": one(con, """SELECT engine_version v FROM intelligence_run
                                       ORDER BY run_id DESC LIMIT 1""")["v"],
        "templates": [
            {**t, "fields": rows(con, """SELECT * FROM import_template_field
                                          WHERE template_id=? ORDER BY column_order""",
                                 (t["template_id"],))}
            for t in rows(con, "SELECT * FROM import_template ORDER BY sort_order")],
        "assumptions": {
            "overhead_rate": 0.18,
            "assessment_months": 24,
            "horizon_months": 6,
            "reprice_share": scenarios.REPRICE_SHARE,
            "ramp_months": scenarios.RAMP_MONTHS,
            "delay_idle_factor": scenarios.DELAY_IDLE_FACTOR,
            "annual_hours": scenarios.ANNUAL_HOURS,
        },
        "orgs": {},
    }
    # Only the acquisition target is exported.
    #
    # The operating company stays in the database because the assessment has no
    # meaning without it: every metric on the target is set beside the same
    # metric measured the same way on the acquirer's own book, and the peer
    # cohorts and the fitted base rate come from the same place. But nobody
    # needs to browse the acquirer's screens to read the target, so it is
    # reduced to the benchmark values already stored on acquisition_metric plus
    # the name and code needed to label the column. That roughly halves the
    # payload and removes an org switcher that only ever led somewhere nobody
    # was going.
    targets = rows(con, """SELECT * FROM org WHERE org_role='acquisition_target'
                            ORDER BY org_id""")
    if not targets:
        raise SystemExit("no acquisition target in the database")
    for org in targets:
        payload["orgs"][org["org_code"]] = build_org(con, org)

    bench = one(con, "SELECT * FROM org WHERE org_role='operating'")
    payload["benchmark"] = {
        "org_code": bench["org_code"],
        "org_name": bench["org_name"],
        "projects": one(con, "SELECT COUNT(*) n FROM project WHERE org_id=?",
                        (bench["org_id"],))["n"],
        "consultants": one(con, """SELECT COUNT(*) n FROM employee
                                    WHERE org_id=? AND is_active=1
                                      AND is_billable=1""",
                           (bench["org_id"],))["n"],
        "note": ("Retained as the comparator only. Every benchmark figure in "
                 "the assessment is this organisation's own book measured on "
                 "the same definitions, over the same window."),
    }
    con.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=float)
    print(f"wrote {OUT}  {os.path.getsize(OUT)/1e6:.2f} MB")


if __name__ == "__main__":
    main()
