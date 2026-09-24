import os
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "psa.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def show(title, sql, params=()):
    print("\n== " + title)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        print("  (none)")
        return
    keys = rows[0].keys()
    print("  " + " | ".join(k[:26] for k in keys))
    for r in rows:
        print("  " + " | ".join(
            (f"{r[k]:,.1f}" if isinstance(r[k], float) else str(r[k]))[:26] for k in keys))


show("row counts", """
SELECT 'time_entry' t, COUNT(*) n FROM time_entry
UNION ALL SELECT 'project', COUNT(*) FROM project
UNION ALL SELECT 'project_task', COUNT(*) FROM project_task
UNION ALL SELECT 'pfm', COUNT(*) FROM project_financial_month
UNION ALL SELECT 'risk_issue', COUNT(*) FROM risk_issue
UNION ALL SELECT 'change_order', COUNT(*) FROM change_order
UNION ALL SELECT 'assignment', COUNT(*) FROM assignment
UNION ALL SELECT 'forecast_snapshot', COUNT(*) FROM project_forecast_snapshot
UNION ALL SELECT 'pipeline', COUNT(*) FROM pipeline_opportunity
""")

show("org level economics", """
SELECT o.org_code,
  COUNT(*) projects,
  ROUND(SUM(v.total_revenue)/1e6,2) rev_m,
  ROUND(SUM(v.recognized_revenue_to_date)/1e6,2) recog_m,
  ROUND(SUM(v.actual_labor_cost)/1e6,2) cost_m,
  ROUND((SUM(v.recognized_revenue_to_date)-SUM(v.actual_labor_cost))
        /NULLIF(SUM(v.recognized_revenue_to_date),0)*100,1) margin_pct,
  ROUND(SUM(v.actual_hours)) hours
FROM v_project_360 v JOIN org o ON o.org_id=v.org_id
GROUP BY o.org_code
""")

show("status mix", """
SELECT o.org_code, p.project_status, p.project_health, COUNT(*) n
FROM project p JOIN org o ON o.org_id=p.org_id
GROUP BY o.org_code, p.project_status, p.project_health ORDER BY 1,2,3
""")

show("completed project margin distribution (OPCO)", """
SELECT ROUND(current_margin_pct/10)*10 band, COUNT(*) n
FROM v_project_360 WHERE org_id=1 AND project_status='Complete'
GROUP BY band ORDER BY band
""")

show("methodology vs overrun (completed, both orgs)", """
SELECT methodology, COUNT(*) n,
  ROUND(AVG(hours_consumed_pct),1) avg_hours_consumed_pct,
  ROUND(AVG(current_margin_pct),1) avg_margin,
  ROUND(100.0*SUM(CASE WHEN hours_consumed_pct>100 THEN 1 ELSE 0 END)/COUNT(*),1) pct_over_budget
FROM v_project_360 WHERE project_status='Complete'
GROUP BY methodology ORDER BY pct_over_budget DESC
""")

show("customer concentration (CCG)", """
SELECT customer_name, ROUND(SUM(total_revenue)/1e6,2) rev_m,
  ROUND(100.0*SUM(total_revenue)/(SELECT SUM(total_revenue) FROM v_project_360 WHERE org_id=2),1) pct
FROM v_project_360 WHERE org_id=2 GROUP BY customer_name ORDER BY rev_m DESC LIMIT 6
""")

show("utilization last 6 full months", """
SELECT o.org_code, u.period_month,
  ROUND(AVG(u.utilization_pct),1) avg_util,
  ROUND(SUM(u.billable_hours)) billable_hours
FROM v_employee_month_utilization u JOIN org o ON o.org_id=u.org_id
WHERE u.period_month >= '2026-02' AND u.period_month < '2026-08'
GROUP BY o.org_code, u.period_month ORDER BY 1,2
""")

show("forecast bias check (completed w/ original+final)", """
SELECT o.org_code, COUNT(*) n,
  ROUND(AVG((f.forecast_hours - fin.forecast_hours)/NULLIF(fin.forecast_hours,0)*100),1)
    AS orig_vs_actual_pct
FROM project p
JOIN org o ON o.org_id=p.org_id
JOIN project_forecast_snapshot f   ON f.project_id=p.project_id AND f.snapshot_type='Original'
JOIN project_forecast_snapshot fin ON fin.project_id=p.project_id AND fin.snapshot_type='Final'
GROUP BY o.org_code
""")

show("unbilled + missing time signals", """
SELECT o.org_code,
  ROUND(SUM(v.unbilled_value)/1e6,2) unbilled_m,
  (SELECT COUNT(*) FROM time_entry t WHERE t.org_id=o.org_id AND t.approval_status<>'Approved') unapproved_entries
FROM v_project_360 v JOIN org o ON o.org_id=v.org_id GROUP BY o.org_code
""")
con.close()
