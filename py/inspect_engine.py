import os
import sqlite3
import textwrap

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "psa.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def q(sql, params=()):
    return con.execute(sql, params).fetchall()


print("=== PS OPERATING SYSTEM ===")
for r in q("""SELECT o.org_code, s.* FROM ps_os_snapshot s JOIN org o USING(org_id)"""):
    print(f"\n{r['org_code']}  health={r['overall_health']}")
    print(f"  revenue T12 ${r['revenue_ytd']:,.0f} | forecast incl backlog ${r['revenue_forecast_fy']:,.0f}")
    print(f"  margin {r['margin_pct']:.1f}% vs target {r['margin_target_pct']:.0f}%")
    print(f"  utilisation {r['utilization_pct']:.1f}% vs target {r['utilization_target_pct']:.0f}%")
    print(f"  revenue at risk ${r['revenue_at_risk']:,.0f}")
    print(f"  live projects {r['projects_total']} (G{r['projects_green']}/Y{r['projects_yellow']}/R{r['projects_red']})"
          f" budget-risk {r['projects_budget_risk']} sched-risk {r['projects_schedule_risk']} margin-risk {r['projects_margin_risk']}")
    print(f"  bench {r['bench_hours']:,.0f}h | overallocated {r['overallocated_headcount']}")
    print(f"  pipeline ${r['pipeline_value']:,.0f} (weighted ${r['weighted_pipeline_value']:,.0f})")
    print(f"  demand {r['resource_demand_hours']:,.0f}h | capacity gap {r['capacity_gap_hours']:,.0f}h"
          f" | hiring {r['hiring_requirement_fte']:.1f} FTE")

print("\n\n=== WORST PREDICTED PROJECT (OPCO) ===")
r = q("""SELECT p.project_code, p.project_name, p.project_health, p.billing_model,
                p.methodology, rs.*, mp.*, b.peer_count, b.tracking_variance_pct,
                b.tracking_verdict, b.peer_budget_overrun_pct, b.peer_avg_margin_pct
           FROM project_risk_score rs
           JOIN project p USING(project_id)
           JOIN project_margin_prediction mp ON mp.project_id=p.project_id AND mp.run_id=rs.run_id
           LEFT JOIN project_benchmark b ON b.project_id=p.project_id AND b.run_id=rs.run_id
          WHERE p.org_id=1 AND p.project_status='In Flight'
          ORDER BY rs.failure_probability_pct DESC LIMIT 1""")[0]
print(f"{r['project_code']}  {r['project_name']}")
print(f"  PM health: {r['project_health']}   predicted: {r['predicted_health']}   confidence: {r['confidence']}")
print(f"  Failure probability            {r['failure_probability_pct']:.0f}%")
print(f"  Probability of budget overrun  {r['p_budget_overrun_pct']:.0f}%")
print(f"  Probability of schedule delay  {r['p_schedule_delay_pct']:.0f}%")
print(f"  Probability margin < target    {r['p_margin_below_target_pct']:.0f}%")
print(f"  Current margin {r['current_margin_pct'] or 0:.1f}% | forecast {r['forecast_margin_pct'] or 0:.1f}%"
      f" | predicted {r['predicted_margin_pct']:.1f}% | target {r['target_margin_pct']:.0f}% | risk {r['margin_risk']}")
print(f"  Peers {r['peer_count']}, tracking {r['tracking_variance_pct']}% ({r['tracking_verdict']}),"
      f" peer overrun {r['peer_budget_overrun_pct']:.1f}%, peer margin {r['peer_avg_margin_pct']:.1f}%")
print("  WHY:")
for d in q("""SELECT * FROM risk_driver WHERE risk_score_id=? ORDER BY weight DESC""",
           (r["risk_score_id"],)):
    print(f"    [{d['affects']:8s}] {d['statement']}  (weight {d['weight']})")
print("  MARGIN DRIVERS (points of margin):")
for d in q("""SELECT * FROM margin_driver WHERE prediction_id=?
              ORDER BY ABS(margin_impact_pts) DESC""", (r["prediction_id"],)):
    print(f"    {d['margin_impact_pts']:+7.2f}  {d['statement']}")

print("\n\n=== RED FLAGS (CCG) ===")
for r in q("""SELECT * FROM red_flag WHERE org_id=2 ORDER BY
              CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END"""):
    print(f"\n[{r['severity']}] {r['flag_category']}")
    print(f"  {r['headline']}")
    print(f"  {textwrap.shorten(r['detail'] or '', 150)}")
    print(f"  -> {r['recommended_action']}")

print("\n\n=== ACQUISITION ASSESSMENT (CCG vs OPCO) ===")
for r in q("""SELECT metric_group, metric_label, metric_value, metric_unit,
                     benchmark_value, variance_vs_benchmark, direction_good
                FROM acquisition_metric m JOIN acquisition_assessment a USING(assessment_id)
               WHERE a.org_id=2 ORDER BY metric_group, metric_id"""):
    def fmt(v, u):
        if v is None:
            return "-"
        if u == "currency":
            return f"${v:,.0f}"
        if u == "percent":
            return f"{v:,.1f}%"
        if u == "count":
            return f"{v:,.0f}"
        return f"{v:,.1f}"
    print(f"  {r['metric_group']:14s} {r['metric_label'][:52]:52s} "
          f"{fmt(r['metric_value'], r['metric_unit']):>14s}  vs OPCO "
          f"{fmt(r['benchmark_value'], r['metric_unit']):>14s}")

print("\n\n=== FUTURE RISK (OPCO) ===")
for r in q("SELECT * FROM future_risk_prediction WHERE org_id=1 ORDER BY prediction_type"):
    print(f"  [{r['prediction_type']}] {r['statement']}")

print("\n\n=== FORECAST ACCURACY (OPCO, by practice) ===")
for r in q("""SELECT scope_label, sample_size, hours_variance_pct, accuracy_pct, bias, statement
                FROM forecast_accuracy WHERE org_id=1 AND scope='practice'
               ORDER BY hours_variance_pct"""):
    print(f"  {r['statement']}")

print("\n\n=== TOP / BOTTOM CUSTOMERS (CCG) ===")
for flag in ("top10", "margin_destroyer", "high_rev_low_margin"):
    print(f"\n {flag}:")
    for r in q("""SELECT dimension_label, revenue, gross_profit, margin_pct, project_count
                    FROM profitability_cut WHERE org_id=2 AND dimension='customer'
                     AND flag LIKE ? ORDER BY gross_profit DESC LIMIT 6""", (f"%{flag}%",)):
        print(f"   {r['dimension_label'][:38]:38s} rev ${r['revenue']:>12,.0f}  gp ${r['gross_profit']:>11,.0f}"
              f"  {(r['margin_pct'] or 0):6.1f}%  ({r['project_count']} projects)")

print("\n\n=== LEADERSHIP ACTIONS (OPCO) ===")
for r in q("SELECT * FROM leadership_action WHERE org_id=1 ORDER BY rank"):
    print(f"  {r['rank']}. [{r['urgency']}] {r['headline']}")
    print(f"      ${r['value_at_stake']:,.0f} at stake. {textwrap.shorten(r['rationale'], 130)}")

print("\n\n=== MIGRATION SCORE (CCG) ===")
for r in q("SELECT * FROM migration_run WHERE org_id=2"):
    print(f"  stage={r['stage']}  quality={r['data_quality_score']}%  "
          f"financial={r['financial_completeness_pct']}%  resource={r['resource_completeness_pct']}%  "
          f"project={r['project_completeness_pct']}%")

print("\n\n=== BRIEFING (OPCO) ===")
print(q("SELECT narrative_md FROM executive_briefing WHERE org_id=1")[0][0])
con.close()
