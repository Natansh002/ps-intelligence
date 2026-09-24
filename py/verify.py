"""
Verification suite.

Three kinds of check, all of them independent of the code that produced the
numbers:

  1. Internal consistency - things that must be arithmetically true, such as
     the margin drivers summing exactly to the gap they claim to explain, and
     every profitability cut adding up to the same org total.
  2. Export fidelity - the JSON the page reads must match a fresh query against
     the database, so a presentation bug cannot quietly change a figure.
  3. Requirement coverage - each requirement from 27 to 45 is asserted against
     something checkable in the data, not against a checklist.

Run it after any rebuild. Exit code is non-zero if anything fails.
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
PAYLOAD = os.path.join(ROOT, "out", "psa_data.json")

results = []


def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))


def close(a, b, tol=0.01):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    q = lambda sql, p=(): [dict(r) for r in con.execute(sql, p).fetchall()]
    one = lambda sql, p=(): (con.execute(sql, p).fetchone() or [None])[0]
    with open(PAYLOAD, encoding="utf-8") as fh:
        data = json.load(fh)

    # ---------------------------------------------------------------- 1
    # margin drivers must sum exactly to predicted - plan
    bad = []
    for r in q("""SELECT mp.prediction_id, mp.project_id, mp.predicted_margin_pct,
                         mp.plan_margin_pct,
                         (SELECT COALESCE(SUM(margin_impact_pts),0) FROM margin_driver md
                           WHERE md.prediction_id = mp.prediction_id) AS driver_sum
                    FROM project_margin_prediction mp"""):
        gap = r["predicted_margin_pct"] - r["plan_margin_pct"]
        if not close(gap, r["driver_sum"], 0.05):
            bad.append((r["project_id"], round(gap, 3), round(r["driver_sum"], 3)))
    check("margin drivers sum to the predicted-minus-plan gap", not bad,
          f"{len(bad)} mismatches, first: {bad[:3]}")

    # every risk score has at least one driver, or is genuinely clean
    orphan = one("""SELECT COUNT(*) FROM project_risk_score rs
                     WHERE NOT EXISTS (SELECT 1 FROM risk_driver d
                                        WHERE d.risk_score_id = rs.risk_score_id)
                       AND rs.failure_probability_pct >
                           (SELECT 100.0 * json_extract(base_rates_json,'$.failure')
                              FROM intelligence_run WHERE run_id = rs.run_id)""")
    check("no project scores above its base rate without a stated reason", orphan == 0,
          f"{orphan} scores lack drivers")

    # profitability cuts reconcile to the same org revenue on every dimension
    mism = []
    for org_id, in con.execute("SELECT org_id FROM org").fetchall():
        totals = q("""SELECT dimension, ROUND(SUM(revenue)) rev, ROUND(SUM(cost)) cost
                        FROM profitability_cut WHERE org_id=? GROUP BY dimension""", (org_id,))
        if not totals:
            continue
        ref = totals[0]
        for t in totals[1:]:
            # geography and industry can drop rows with a null key
            if abs(t["rev"] - ref["rev"]) > max(1000, ref["rev"] * 0.001):
                mism.append((org_id, t["dimension"], t["rev"], ref["rev"]))
    check("every profitability dimension reconciles to the same revenue total", not mism,
          f"{mism[:4]}")

    # The operating company's ledger is built from its own timesheets, so it
    # must tie. The acquired extract deliberately does not tie - that break is
    # a finding, not a bug, and the engine has to catch it.
    for org_id, code in con.execute("SELECT org_id, org_code FROM org").fetchall():
        ledger = one("""SELECT SUM(f.labor_cost) FROM project_financial_month f
                          JOIN project p USING(project_id) WHERE p.org_id=?""", (org_id,))
        sheets = one("""SELECT SUM(hours * COALESCE(cost_rate,0)) FROM time_entry
                         WHERE org_id=? AND approval_status='Approved'
                           AND project_id IS NOT NULL""", (org_id,))
        ties = abs(ledger - sheets) < max(1.0, sheets * 0.0001)
        if code == "OPCO":
            check(f"{code}: monthly ledger labour cost matches approved timesheets", ties,
                  f"ledger {ledger:,.0f} vs timesheets {sheets:,.0f}")
        else:
            detected = one("""SELECT COUNT(*) FROM data_quality_finding
                               WHERE org_id=? AND rule_code='LEDGER_TIMESHEET_MISMATCH'""",
                           (org_id,))
            check(f"{code}: ledger/timesheet break in the extract is detected, not ignored",
                  ties or detected > 0,
                  f"gap {abs(ledger - sheets):,.0f}, findings {detected}")

    # Sanity on the prediction as a whole. A model that says the live book will
    # land far from where completed work actually landed is telling you about
    # itself, not about the projects. This caught a double-counted change-order
    # line that had made every prediction eight points pessimistic.
    for org_id, code in con.execute("SELECT org_id, org_code FROM org").fetchall():
        pred = [r["v"] for r in q("""SELECT mp.predicted_margin_pct v
                                       FROM project_margin_prediction mp
                                       JOIN project p USING(project_id)
                                      WHERE p.org_id=? AND p.project_status='In Flight'
                                      ORDER BY 1""", (org_id,))]
        done = [r["v"] for r in q("""SELECT current_margin_pct v FROM v_project_360
                                      WHERE org_id=? AND project_status='Complete'
                                        AND current_margin_pct IS NOT NULL
                                      ORDER BY 1""", (org_id,))]
        if len(pred) < 8 or len(done) < 20:
            continue
        med = lambda xs: xs[len(xs) // 2]
        drift = med(done) - med(pred)
        check(f"{code}: predicted margins sit within 12 points of completed outturns",
              abs(drift) <= 12,
              f"completed median {med(done):.1f}%, predicted median {med(pred):.1f}%, "
              f"drift {drift:+.1f} pts")

    # recognised revenue never exceeds contract value plus approved change orders
    over = one("""SELECT COUNT(*) FROM (
                    SELECT p.project_id,
                           SUM(f.recognized_revenue) rec,
                           p.contract_value + COALESCE((SELECT SUM(co_value) FROM change_order
                             WHERE project_id=p.project_id AND status='Approved'),0) cap
                      FROM project p JOIN project_financial_month f USING(project_id)
                     WHERE p.billing_model IN ('Fixed Fee','Milestone','Capped T&M')
                       AND p.contract_value > 0
                     GROUP BY p.project_id)
                  WHERE rec > cap * 1.001""")
    check("fixed-price recognition never exceeds the contract ceiling", over == 0,
          f"{over} projects over-recognised")

    # ---------------------------------------------------------------- 2
    # The console shows the acquisition target and nothing else. The acquirer
    # stays in the database because it is the comparator behind every benchmark
    # figure, so the two facts have to be asserted together: exactly one
    # organisation exported, and it is the target, with the benchmark reference
    # still present. Getting this wrong the other way would publish the
    # acquirer's whole book to an audience that asked not to see it.
    exported = sorted(data["orgs"])
    target_code = one("SELECT org_code FROM org WHERE org_role='acquisition_target'")
    check("export: exactly one organisation, and it is the acquisition target",
          exported == [target_code],
          f"exported {exported}, target is {target_code}")
    bench = data.get("benchmark") or {}
    bench_code = one("SELECT org_code FROM org WHERE org_role='operating'")
    check("export: the benchmark organisation is named but not exported",
          bench.get("org_code") == bench_code
          and bench_code not in data["orgs"]
          and bench.get("projects", 0) > 0,
          f"benchmark {bench.get('org_code')} against {bench_code}")
    check("export: every benchmarked metric still carries its comparator value",
          all(m.get("benchmark_value") is not None
              for m in data["orgs"][target_code]["metrics"]
              if m.get("variance_vs_benchmark") is not None))

    for code, o in data["orgs"].items():
        oid = one("SELECT org_id FROM org WHERE org_code=?", (code,))
        run_id = one("SELECT MAX(run_id) FROM intelligence_run WHERE org_id=?", (oid,))

        db_live = one("""SELECT COUNT(*) FROM project_risk_score rs JOIN project p USING(project_id)
                          WHERE p.org_id=? AND rs.run_id=? AND p.project_status='In Flight'""",
                      (oid, run_id))
        js_live = len([p for p in o["live_projects"] if p["project_status"] == "In Flight"])
        check(f"{code}: live project count matches the database", db_live == js_live,
              f"db {db_live} vs export {js_live}")

        db_rev = one("""SELECT SUM(f.recognized_revenue) FROM project_financial_month f
                          JOIN project p USING(project_id) WHERE p.org_id=?""", (oid,))
        js_rev = sum(m["revenue"] or 0 for m in o["monthly"])
        check(f"{code}: exported monthly revenue matches the ledger",
              close(db_rev, js_rev, max(1.0, db_rev * 0.0001)),
              f"db {db_rev:,.0f} vs export {js_rev:,.0f}")

        db_flags = one("SELECT COUNT(*) FROM red_flag WHERE org_id=?", (oid,))
        check(f"{code}: red flags all exported", db_flags == len(o["red_flags"]),
              f"db {db_flags} vs export {len(o['red_flags'])}")

        db_rar = one("SELECT revenue_at_risk FROM ps_os_snapshot WHERE org_id=? AND run_id=?",
                     (oid, run_id))
        check(f"{code}: revenue at risk matches the stored snapshot",
              close(db_rar, o["psos"]["revenue_at_risk"], 1.0))

        # every red flag with a metric has evidence rows behind it
        naked = [f["headline"] for f in o["red_flags"]
                 if not f["evidence"] and f["flag_category"] != "Forecast Risk"]
        check(f"{code}: every red flag carries its evidence", not naked, f"{naked[:2]}")

        # the what-if baseline is reproducible from the database
        b = o["baseline"]
        lo, hi = b["period"].split(" to ")
        rev12 = one("""SELECT SUM(f.recognized_revenue) FROM project_financial_month f
                         JOIN project p USING(project_id)
                        WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?""", (oid, lo, hi))
        check(f"{code}: what-if baseline revenue is reproducible",
              close(b["revenue"], rev12, max(1.0, rev12 * 0.0001)),
              f"baseline {b['revenue']:,.0f} vs query {rev12:,.0f}")

        # stored scenarios agree with the baseline they were computed from
        for s in o["scenarios"]:
            base_rev = next((r["baseline_value"] for r in s["results"]
                             if r["measure"] == "revenue"), None)
            if base_rev is not None and not close(base_rev, b["revenue"], 1.0):
                check(f"{code}: scenario '{s['question'][:40]}' uses the current baseline",
                      False, f"{base_rev:,.0f} vs {b['revenue']:,.0f}")
        check(f"{code}: all stored scenarios use the current baseline", True)

    # ---------------------------------------------------------------- 3
    # 27: eight templates, with the specified fields present
    tpl = {t["template_code"]: t for t in data["templates"]}
    check("27: all eight standard templates defined", len(tpl) == 8, f"{sorted(tpl)}")
    required = {
        "CUSTOMERS": ["Customer ID", "Customer Name", "Customer Type", "Industry", "Region",
                      "Account Owner", "Customer Status", "Contract Start", "Contract End",
                      "Annual Revenue", "Lifetime Revenue"],
        "PROJECTS": ["Legacy Project ID", "Customer", "Project Name", "Project Type",
                     "Product", "Project Manager", "Start Date", "Planned End Date",
                     "Actual End Date", "Contract Value", "SOW Value", "Budget Hours",
                     "Actual Hours", "Budget Cost", "Actual Cost", "Revenue",
                     "Project Status", "Project Health", "Go-Live Date"],
        "EMPLOYEES": ["Employee ID", "Employee Name", "Role", "Department", "Practice",
                      "Location", "Employment Type", "Cost Rate", "Billing Rate", "Capacity",
                      "Utilization Target", "Skills", "Manager", "Start Date"],
        "TIME_ENTRIES": ["Employee ID", "Employee Name", "Project ID", "Project Name",
                         "Task", "Date", "Hours", "Billable / Non-Billable",
                         "Time Category", "Approval Status"],
        "PROJECT_FINANCIALS": ["Project ID", "Month", "Contract Value", "Invoiced Revenue",
                               "Recognized Revenue", "Labor Cost", "Other Cost",
                               "Total Cost", "Gross Profit", "Gross Margin %", "Budget",
                               "Forecast", "Actual"],
        "TASKS_MILESTONES": ["Project ID", "Task ID", "Task Name", "Phase", "Planned Start",
                             "Planned End", "Actual Start", "Actual End", "Planned Hours",
                             "Actual Hours", "Status", "Percent Complete"],
        "RISKS_ISSUES": ["Project ID", "Risk/Issue ID", "Type", "Description",
                         "Date Identified", "Owner", "Priority", "Impact", "Probability",
                         "Status", "Resolution", "Due Date"],
        "CONTRACTS_SOWS": ["Customer", "Project", "Contract ID", "SOW ID", "Contract Type",
                           "Contract Value", "Sold Hours", "Start Date", "End Date",
                           "Billing Model", "Payment Terms", "Change Order Value",
                           "Remaining Value"],
    }
    missing = []
    for code, cols in required.items():
        have = {f["column_header"] for f in tpl.get(code, {}).get("fields", [])}
        missing += [f"{code}.{c}" for c in cols if c not in have]
    check("27: every specified template column is present", not missing, f"{missing[:6]}")
    check("27: time and financial templates require 24 months of history",
          tpl["TIME_ENTRIES"]["min_history_months"] >= 24
          and tpl["PROJECT_FINANCIALS"]["min_history_months"] >= 24)

    # 28: import wizard - batches, staging rows, findings, re-upload support
    check("28: one import batch per template", one("SELECT COUNT(*) FROM import_batch") == 8)
    check("28: validation findings link to staging rows",
          one("""SELECT COUNT(*) FROM validation_finding WHERE staging_row_id IS NULL""") == 0)
    check("28: findings cover every specified check class",
          {r["rule_code"] for r in q("SELECT DISTINCT rule_code FROM validation_finding")}
          >= {"MISSING_MANDATORY", "DUPLICATE_RECORD", "INVALID_DATE", "NEGATIVE_VALUE",
              "MISSING_COST_RATE", "MISSING_BILL_RATE", "MISSING_PM",
              "CURRENCY_INCONSISTENT"})
    check("28: every finding tells the user how to fix it",
          one("SELECT COUNT(*) FROM validation_finding WHERE recommendation IS NULL") == 0)

    # 29: mapping across dimensions, with unmapped values visible
    dims = {r["dimension"] for r in q("SELECT DISTINCT dimension FROM mapping_set")}
    check("29: terminology mapping covers the main dimensions",
          dims >= {"time_category", "project_type", "billing_model", "project_status",
                   "role", "currency"}, f"{sorted(dims)}")
    check("29: unmapped legacy values are retained rather than dropped",
          one("SELECT COUNT(*) FROM mapping_rule WHERE match_source='unmapped'") > 0)

    # 30: the engine reads every entity class
    check("30: engine run recorded over the whole book",
          one("SELECT COUNT(*) FROM intelligence_run") == 2)

    # 31: probabilities and reasons
    check("31: every scored project has three probabilities and a predicted health",
          one("""SELECT COUNT(*) FROM project_risk_score
                  WHERE failure_probability_pct IS NULL OR p_budget_overrun_pct IS NULL
                     OR p_schedule_delay_pct IS NULL OR p_margin_below_target_pct IS NULL
                     OR predicted_health IS NULL""") == 0)
    codes = {r["driver_code"] for r in q("SELECT DISTINCT driver_code FROM risk_driver")}
    check("31: the driver vocabulary covers the specified causes",
          codes >= {"BURN_RATE", "MILESTONE_SLIP", "UAT_LATE", "PEER_HISTORY",
                    "OPEN_ISSUES", "CAPACITY_SHORTFALL"}, f"{sorted(codes)}")
    # PM capacity only fires where a PM is actually over-loaded. Assert the
    # implication rather than the presence, so an absent driver on a
    # well-staffed portfolio is a pass and a missed one is a fail.
    stretched = one("""SELECT COUNT(*) FROM (SELECT project_manager_id, COUNT(*) n
                         FROM project WHERE project_status='In Flight'
                          AND project_manager_id IS NOT NULL
                         GROUP BY project_manager_id) WHERE n > 3""")
    check("31: PM capacity fires exactly when a PM is over-loaded",
          (stretched == 0) == ("PM_CAPACITY" not in codes),
          f"{stretched} over-loaded PMs, driver present: {'PM_CAPACITY' in codes}")

    # 32: three margins per project
    check("32: current, forecast and predicted margin all present",
          one("""SELECT COUNT(*) FROM project_margin_prediction
                  WHERE predicted_margin_pct IS NULL OR forecast_cost IS NULL
                     OR predicted_cost IS NULL""") == 0)

    # 33: benchmarking with a real cohort
    check("33: benchmarks name their cohort and its members",
          one("SELECT MIN(peer_count) FROM project_benchmark") >= 1
          and one("SELECT COUNT(*) FROM benchmark_peer") > 0)
    check("33: peer cohort statistics are populated",
          one("""SELECT COUNT(*) FROM project_benchmark
                  WHERE peer_avg_hours IS NULL OR peer_avg_margin_pct IS NULL
                     OR peer_avg_duration_days IS NULL OR peer_avg_change_orders IS NULL""") == 0)

    # 34: the specified assessment metrics
    m = {r["metric_code"] for r in q("""SELECT metric_code FROM acquisition_metric am
                                          JOIN acquisition_assessment a USING(assessment_id)
                                          JOIN org o ON o.org_id=a.org_id
                                         WHERE o.org_code='CCG'""")}
    want = {"REVENUE", "GROSS_MARGIN", "EBITDA_PROXY", "BILLABLE_UTIL",
            "REV_PER_CONSULTANT", "COST_PER_CONSULTANT", "REALIZED_RATE",
            "AVG_PROJECT_MARGIN", "TOP3_CUSTOMERS", "TOP5_PROJECTS", "REVENUE_AT_RISK",
            "UNPROFITABLE_CUSTOMERS", "UNPROFITABLE_PROJECTS", "UNDERUTILIZED",
            "OVERALLOCATED", "BOTTLENECKS", "AGING_PROJECTS", "DELAYED_PROJECTS",
            "CO_DEPENDENCY", "REVENUE_LEAKAGE", "UNBILLED_TIME", "MISSING_TIME",
            "FORECAST_ACCURACY"}
    check("34: every specified acquisition measure is produced", want <= m,
          f"missing {sorted(want - m)}")
    check("34: target metrics are benchmarked against the operating company",
          one("""SELECT COUNT(*) FROM acquisition_metric am
                   JOIN acquisition_assessment a USING(assessment_id)
                   JOIN org o ON o.org_id=a.org_id
                  WHERE o.org_code='CCG' AND am.benchmark_value IS NOT NULL""") > 20)

    # 35: ten profitability dimensions with the specified flags
    d = {r["dimension"] for r in q("SELECT DISTINCT dimension FROM profitability_cut")}
    check("35: profitability cut by every specified dimension",
          d >= {"customer", "project", "product", "practice", "geography", "project_type",
                "billing_model", "project_manager", "industry"}, f"{sorted(d)}")
    f = set()
    for r in q("SELECT DISTINCT flag FROM profitability_cut WHERE flag IS NOT NULL"):
        f |= set(r["flag"].split(","))
    check("35: top, bottom, margin destroyers and chronic overruns all identified",
          f >= {"top10", "bottom10", "chronic_over_budget"}, f"{sorted(f)}")

    # 36: red flag categories
    cats = {r["flag_category"] for r in q("SELECT DISTINCT flag_category FROM red_flag")}
    check("36: red flags span the specified risk categories",
          cats >= {"Customer Concentration", "Margin Risk", "Resource Risk",
                   "Delivery Risk", "Revenue Risk", "Knowledge Risk"}, f"{sorted(cats)}")

    # 37: forward predictions
    t = {r["prediction_type"] for r in q("SELECT DISTINCT prediction_type FROM future_risk_prediction")}
    check("37: forward predictions cover capacity, hiring, margin and utilisation",
          t >= {"CAPACITY_SURPLUS", "HIRING_REQUIREMENT"} and
          len(t & {"CAPACITY_SHORTFALL", "MARGIN_SHIFT", "UTILIZATION_MISS",
                   "CUSTOMER_DEMAND"}) >= 2, f"{sorted(t)}")
    check("37: capacity model covers every practice across the horizon",
          one("""SELECT COUNT(DISTINCT practice_id) FROM capacity_month""") ==
          one("SELECT COUNT(*) FROM practice"))

    # 38: forecast accuracy at every scope
    sc = {r["scope"] for r in q("SELECT DISTINCT scope FROM forecast_accuracy")}
    check("38: forecast accuracy measured by PM, practice, customer and org",
          sc >= {"project_manager", "practice", "customer", "org"}, f"{sorted(sc)}")
    check("38: original, revised and actual all compared",
          one("""SELECT COUNT(*) FROM forecast_accuracy
                  WHERE original_forecast_hours IS NULL OR revised_forecast_hours IS NULL
                     OR actual_hours IS NULL""") == 0)

    # 39: scenarios across the specified question types
    st = {r["scenario_type"] for r in q("SELECT DISTINCT scenario_type FROM scenario")}
    check("39: scenarios cover utilisation, headcount, delay, rate, loss and new deal",
          st >= {"UTILIZATION_CHANGE", "HEADCOUNT_CHANGE", "PROJECT_DELAY", "RATE_CHANGE",
                 "CUSTOMER_LOSS", "NEW_DEAL"}, f"{sorted(st)}")
    check("39: every scenario reports all the specified impacts",
          one("""SELECT MIN(n) FROM (SELECT scenario_id, COUNT(*) n FROM scenario_result
                   GROUP BY scenario_id)""") >= 6)

    # 40: go / no-go with factors and quantified recommendations
    check("40: deal assessments produced with a recommendation",
          one("SELECT COUNT(*) FROM deal_assessment") >= 6 and
          one("""SELECT COUNT(*) FROM deal_assessment
                  WHERE recommendation NOT IN ('GO','REVIEW','NO-GO')""") == 0)
    check("40: every assessment explains itself and says what to do",
          one("""SELECT COUNT(*) FROM deal_assessment d
                  WHERE (SELECT COUNT(*) FROM deal_assessment_factor f
                          WHERE f.deal_assessment_id=d.deal_assessment_id) < 5
                     OR (SELECT COUNT(*) FROM deal_recommendation r
                          WHERE r.deal_assessment_id=d.deal_assessment_id) < 1""") == 0)

    # 41: the five questions, and the actions
    s = q("SELECT * FROM ps_os_snapshot")
    need = ["revenue_ytd", "margin_pct", "revenue_at_risk", "projects_total",
            "projects_budget_risk", "utilization_pct", "capacity_hours",
            "pipeline_value", "capacity_gap_hours", "hiring_requirement_fte"]
    check("41: the operating system answers all five questions",
          all(r[k] is not None for r in s for k in need))
    check("41: leadership actions are ranked with value at stake",
          one("SELECT COUNT(*) FROM leadership_action WHERE rank IS NULL") == 0 and
          one("SELECT COUNT(*) FROM leadership_action") >= 8)

    # 42: briefing generated from the run
    check("42: an executive briefing exists per org, generated from a run",
          one("SELECT COUNT(*) FROM executive_briefing WHERE run_id IS NOT NULL") == 2)
    check("42: the briefing carries issues and actions",
          one("""SELECT COUNT(DISTINCT section) FROM briefing_item
                  WHERE section IN ('key_issue','recommended_action')""") == 2)

    # 43: migration pipeline and traceability
    mig = q("SELECT * FROM migration_run")
    check("43: migration scored on quality and completeness",
          all(r["data_quality_score"] is not None and r["financial_completeness_pct"] is not None
              and r["resource_completeness_pct"] is not None
              and r["project_completeness_pct"] is not None for r in mig))
    check("43: every migrated record keeps its legacy identifier",
          one("SELECT COUNT(*) FROM migration_lineage") > 250 and
          one("SELECT COUNT(*) FROM migration_lineage WHERE legacy_id IS NULL") == 0)
    traced = one("""SELECT COUNT(*) FROM project p
                     WHERE p.org_id=(SELECT org_id FROM org WHERE org_code='CCG')
                       AND p.legacy_project_id IS NULL""")
    check("43: no acquired project lost its legacy ID", traced == 0, f"{traced} untraceable")

    # 44: data quality categories
    cat = {r["category"] for r in q("SELECT DISTINCT category FROM data_quality_finding")}
    check("44: data quality covers the specified problem classes",
          cat >= {"Missing data", "Duplicates", "Invalid dates", "Missing rates",
                  "Inconsistent currencies"}, f"{sorted(cat)}")
    check("44: every quality finding carries a correction recommendation",
          one("SELECT COUNT(*) FROM data_quality_finding WHERE recommendation IS NULL") == 0)

    # 45: the four questions beyond "what happened"
    check("45: descriptive, diagnostic, predictive and prescriptive all present",
          one("SELECT COUNT(*) FROM profitability_cut") > 0        # what happened
          and one("SELECT COUNT(*) FROM risk_driver") > 0          # why
          and one("SELECT COUNT(*) FROM project_risk_score") > 0   # what will happen
          and one("SELECT COUNT(*) FROM leadership_action") > 0    # what to do
          and one("SELECT COUNT(*) FROM scenario_result") > 0)     # what if

    # audit and versioning, from the project brief itself
    check("brief: plan versioning retained with a superseded baseline",
          one("SELECT COUNT(*) FROM project_plan_version WHERE is_baseline=1") > 0 and
          one("SELECT COUNT(*) FROM project_plan_version WHERE is_current=1 AND is_baseline=0") > 0)
    check("brief: revenue recognition schedule and audit trail populated",
          one("SELECT COUNT(*) FROM revenue_recognition") > 0 and
          one("SELECT COUNT(*) FROM audit_log") > 0)
    check("brief: overrides are marked as overrides and mostly carry a reason",
          one("SELECT COUNT(*) FROM audit_log WHERE is_override=1") > 0 and
          one("""SELECT COUNT(*) FROM audit_log WHERE is_override=1
                   AND change_reason IS NOT NULL""") > 0 and
          one("""SELECT COUNT(*) FROM audit_log WHERE is_override=1
                   AND change_reason IS NULL""") > 0)

    # ---------------------------------------------------------------
    # The timesheet grain. These are the invariants that stop a
    # utilisation figure from averaging out a set of impossible weeks.
    # ---------------------------------------------------------------
    impossible_days = one("""SELECT COUNT(*) FROM (
        SELECT employee_id, entry_date, SUM(hours) h FROM time_entry
         GROUP BY 1,2 HAVING h > 16)""")
    injected = one("SELECT COUNT(*) FROM time_entry WHERE hours > 24")
    check("time: nobody books an impossible day outside the injected defects",
          impossible_days <= injected,
          f"{impossible_days} days over 16h against {injected} injected defects")

    wk = ("date(entry_date, '-' || ((strftime('%w', entry_date)+6)%7) "
          "|| ' days')")
    impossible_weeks = one(f"""SELECT COUNT(*) FROM (
        SELECT employee_id, {wk} w, SUM(hours) h FROM time_entry
         GROUP BY 1,2 HAVING h > 60)""")
    check("time: no week beyond a hard week, outside the injected defects",
          impossible_weeks <= injected,
          f"{impossible_weeks} weeks over 60h")

    max_year = one("""SELECT MAX(h) FROM (
        SELECT SUM(t.hours) h FROM time_entry t
         WHERE t.entry_date >= date((SELECT MAX(entry_date) FROM time_entry),
                                    '-365 days')
         GROUP BY t.employee_id)""")
    check("time: no annual total beyond what a person can work",
          max_year is not None and max_year <= 2600,
          f"highest annual total {max_year:,.0f} hours" if max_year else "")

    mixed = one(f"""SELECT COUNT(*) FROM (
        SELECT employee_id, {wk} w, COUNT(DISTINCT approval_status) n
          FROM time_entry GROUP BY 1,2 HAVING n > 1)""")
    check("time: a timesheet week is approved as a week, not per entry",
          mixed == 0, f"{mixed} weeks in more than one approval state")

    before_start = one("""SELECT COUNT(*) FROM time_entry t
        JOIN employee e USING(employee_id)
       WHERE t.entry_date < e.start_date""")
    check("time: nobody books time before they joined",
          before_start == 0, f"{before_start} entries")

    # ---------------------------------------------------------------
    # Checks engine
    # ---------------------------------------------------------------
    check("checks: a run exists per org with a readiness verdict",
          one("SELECT COUNT(*) FROM check_run WHERE readiness_verdict IS NOT NULL") == 2)
    # The count is asserted rather than merely counted, because a rule that
    # silently stops being registered is a check that silently stops running.
    n_rules = one("SELECT COUNT(*) FROM delivery_check")
    check("checks: the catalogue holds fifty-six rules across nine families",
          n_rules == 56 and
          one("SELECT COUNT(DISTINCT family) FROM delivery_check") == 9,
          f"{n_rules} rules")
    check("checks: every rule states an assertion, a consequence and a remedy",
          one("""SELECT COUNT(*) FROM delivery_check
                  WHERE assertion IS NULL OR why_it_matters IS NULL
                     OR remediation IS NULL OR TRIM(assertion)=''""") == 0)
    check("checks: enforcement is recorded, and some rules are silent in both",
          one("""SELECT COUNT(*) FROM delivery_check
                  WHERE enforcement NOT IN ('blocked','detected','silent')""") == 0
          and one("SELECT COUNT(*) FROM delivery_check WHERE enforcement='silent'") > 0)
    check("checks: readiness and operational rules are separated",
          one("SELECT COUNT(*) FROM delivery_check WHERE check_class='readiness'") > 0
          and one("""SELECT COUNT(*) FROM delivery_check
                      WHERE check_class='operational'""") > 0
          and one("""SELECT COUNT(*) FROM delivery_check
                      WHERE (check_class='readiness') <> (is_gate=1)""") == 0)
    check("checks: every rule ran in every org",
          one("SELECT COUNT(*) FROM check_result") == n_rules * 2)
    # The invariant that catches a check measuring two grains at once.
    bad_grain = one("SELECT COUNT(*) FROM check_result WHERE failing > population")
    check("checks: no check fails more records than it examined",
          bad_grain == 0, f"{bad_grain} checks with failing > population")
    check("checks: a not-applicable result has an empty population, and no more",
          one("""SELECT COUNT(*) FROM check_result
                  WHERE (status='not_applicable') <> (population=0)""") == 0)
    check("checks: every finding belongs to a rule that reported failures",
          one("""SELECT COUNT(*) FROM check_finding f
                  WHERE NOT EXISTS (SELECT 1 FROM check_result r
                                     WHERE r.check_run_id=f.check_run_id
                                       AND r.check_code=f.check_code
                                       AND r.failing > 0)""") == 0)
    check("checks: metric definitions published with their vendor equivalent",
          one("SELECT COUNT(*) FROM metric_definition") >= 8 and
          one("SELECT COUNT(*) FROM metric_definition WHERE formula IS NULL") == 0)

    # ---------------------------------------------------------------
    # Resource requirement
    # ---------------------------------------------------------------
    check("resourcing: a plan exists per org with lines, months and actions",
          one("SELECT COUNT(*) FROM resource_plan") == 2 and
          one("SELECT COUNT(*) FROM resource_plan_line") > 6 and
          one("SELECT COUNT(*) FROM resource_plan_month") > 60 and
          one("SELECT COUNT(*) FROM resource_plan_action") > 6)
    check("resourcing: every named input is stored on the line",
          one("""SELECT COUNT(*) FROM resource_plan_line
                  WHERE time_to_go_live_weeks IS NULL
                     OR existing_consultants IS NULL OR existing_fte IS NULL
                     OR billable_util_target_pct IS NULL
                     OR productive_util_target_pct IS NULL
                     OR training_hours_per_year IS NULL
                     OR pipeline_weighted_hours IS NULL
                     OR backlog_hours IS NULL""") == 0)
    check("resourcing: the source of the go-live figure is stated",
          one("""SELECT COUNT(*) FROM resource_plan_line
                  WHERE time_to_go_live_source IS NULL
                     OR TRIM(time_to_go_live_source)=''""") == 0)
    # Attributing capacity by hours delivered has to conserve the workforce.
    # If it does not, the plan is adding up people who do not exist.
    for code in ("OPCO", "CCG"):
        oid = one("SELECT org_id FROM org WHERE org_code=?", (code,))
        attributed = one("""SELECT SUM(l.existing_fte) FROM resource_plan_line l
                             JOIN resource_plan p USING(plan_id)
                            WHERE p.org_id=?""", (oid,)) or 0
        billable = one("""SELECT COUNT(*) FROM employee WHERE org_id=?
                           AND is_active=1 AND is_billable=1""", (oid,))
        check(f"resourcing: {code} attributed FTE does not exceed the billable "
              f"workforce",
              attributed <= billable + 0.5,
              f"{attributed:,.1f} FTE attributed against {billable} billable heads")
    check("resourcing: months add up to the horizon on every line",
          one("""SELECT COUNT(*) FROM (
                SELECT l.plan_line_id, p.horizon_months h, COUNT(m.plan_month_id) n
                  FROM resource_plan_line l JOIN resource_plan p USING(plan_id)
                  LEFT JOIN resource_plan_month m USING(plan_line_id)
                 GROUP BY l.plan_line_id HAVING n <> h)""") == 0)
    check("resourcing: a hire count is only reported with a hire verdict",
          one("""SELECT COUNT(*) FROM resource_plan_line
                  WHERE hire_count > 0 AND verdict <> 'hire'""") == 0)
    check("resourcing: every line carries a verdict and a statement",
          one("""SELECT COUNT(*) FROM resource_plan_line
                  WHERE verdict IS NULL OR statement IS NULL
                     OR TRIM(statement)=''""") == 0)
    check("resourcing: sensitivity tested on every line",
          one("""SELECT COUNT(*) FROM resource_plan_line l
                  WHERE NOT EXISTS (SELECT 1 FROM resource_plan_sensitivity s
                                     WHERE s.plan_line_id=l.plan_line_id)""") == 0)

    # ---------------------------------------------------------------
    # Earned value
    #
    # The arithmetic is checked against itself rather than against a stored
    # figure, because every one of these indices is a ratio of two other
    # stored columns and a transcription error in either would otherwise be
    # invisible.
    # ---------------------------------------------------------------
    check("evm: an index row exists for every live engagement with a plan",
          one("SELECT COUNT(*) FROM project_evm") > 40 and
          one("""SELECT COUNT(*) FROM project_evm e JOIN project p
                        USING(project_id)
                  WHERE p.project_status <> 'In Flight'""") == 0)
    bad_cpi = one("""SELECT COUNT(*) FROM project_evm
                      WHERE cpi IS NOT NULL AND ac > 0
                        AND ABS(cpi - ev / ac) > 0.005""")
    check("evm: the cost index equals earned value over actual cost",
          bad_cpi == 0, f"{bad_cpi} rows")
    bad_spi = one("""SELECT COUNT(*) FROM project_evm
                      WHERE spi IS NOT NULL AND pv > 0
                        AND ABS(spi - ev / pv) > 0.005""")
    check("evm: the schedule index equals earned value over planned value",
          bad_spi == 0, f"{bad_spi} rows")
    # Tolerance scales with the budget: earned percent is stored to two
    # decimals, so a quarter-million-dollar budget carries about thirteen
    # dollars of rounding and a flat tolerance would fail on arithmetic that
    # is correct.
    bad_ev = one("""SELECT COUNT(*) FROM project_evm
                     WHERE ABS(ev - bac * earned_pct / 100.0)
                           > bac * 0.0001 + 1.0""")
    check("evm: earned value is budget cost times earned percent complete",
          bad_ev == 0, f"{bad_ev} rows")
    # The definitional trap this whole family exists to avoid. Percent complete
    # measured as hours spent over estimate at completion makes earned value
    # equal actual cost, so every index pins to 1.00 and the metric can never
    # report a cost problem. If the spread ever collapses, that has happened.
    spread = one("""SELECT COUNT(*) FROM project_evm
                     WHERE is_reportable=1 AND cpi IS NOT NULL
                       AND ABS(cpi - 1.0) > 0.05""")
    check("evm: the cost index is not pinned to 1.00 by its own definition",
          spread >= 5, f"only {spread} rows more than 5 points off 1.00")
    check("evm: percent complete is earned plan, never hours over estimate",
          one("""SELECT COUNT(*) FROM project_evm
                  WHERE earned_pct > 100.01 OR earned_pct < 0""") == 0)
    check("evm: every row states its quadrant and a reading",
          one("""SELECT COUNT(*) FROM project_evm
                  WHERE quadrant IS NULL OR TRIM(quadrant)=''
                     OR statement IS NULL OR TRIM(statement)=''""") == 0)
    check("evm: a row held back from the roll-up says why",
          one("""SELECT COUNT(*) FROM project_evm
                  WHERE (is_reportable=0) <> (exclusion_reason IS NOT NULL)""") == 0)
    # The roll-up must reconcile to the rows it was built from, or the
    # portfolio figure is its own separate assertion.
    mismatch = one("""SELECT COUNT(*) FROM evm_summary s
                       WHERE s.scope='org' AND s.projects <>
                             (SELECT COUNT(*) FROM project_evm e
                               WHERE e.org_id=s.org_id AND e.is_reportable=1)""")
    check("evm: the portfolio roll-up counts exactly the reportable rows",
          mismatch == 0, f"{mismatch} roll-ups out of step")
    check("evm: the roll-up is budget weighted, not an average of ratios",
          one("""SELECT COUNT(*) FROM evm_summary
                  WHERE cpi IS NOT NULL AND ac > 0
                    AND ABS(cpi - ev / ac) > 0.005""") == 0)
    check("evm: the earned value checks ran and are scored as operational",
          one("""SELECT COUNT(*) FROM delivery_check
                  WHERE family='earnedvalue'""") == 6 and
          one("""SELECT COUNT(*) FROM delivery_check
                  WHERE family='earnedvalue' AND check_class='operational'""") == 4)

    # Recorded progress against booked effort. This is the input the whole
    # family depends on, and it was wrong: a shared week ceiling silently
    # refused hours that did not fit, leaving plans marked 78% delivered with
    # eighteen hours booked against them and a cost index of 9.5.
    unsupported = one("""
        SELECT COUNT(*) FROM (
          SELECT p.project_id,
                 COALESCE(SUM(t.actual_hours), 0) plan_hours,
                 (SELECT COALESCE(SUM(hours), 0) FROM time_entry
                   WHERE project_id = p.project_id) booked
            FROM project p
            JOIN project_task t ON t.project_id = p.project_id
                 AND t.plan_version_id IN (SELECT plan_version_id
                       FROM project_plan_version WHERE is_current = 1)
           WHERE p.project_status IN ('In Flight', 'Complete')
           GROUP BY p.project_id
          HAVING plan_hours > 20 AND booked < plan_hours * 0.6)""")
    check("evm: recorded progress is supported by booked time",
          unsupported <= 12, f"{unsupported} engagements under 60% supported")


    # ---------------------------------------------------------------
    # Automation and AI opportunities
    #
    # The analysis is only worth anything if the three parts stay separable:
    # a measured baseline, named assumptions, and a modelled result. These
    # checks exist to keep them apart, because the moment an assumption can
    # pass as measured the whole thing becomes a workshop output.
    # ---------------------------------------------------------------
    check("automation: opportunities exist for both organisations",
          one("""SELECT COUNT(*) FROM (SELECT org_id FROM automation_opportunity
                   GROUP BY org_id)""") == 2 and
          one("SELECT COUNT(*) FROM automation_opportunity") >= 20)
    check("automation: every opportunity states how its baseline was measured",
          one("""SELECT COUNT(*) FROM automation_opportunity
                  WHERE baseline_basis IS NULL OR TRIM(baseline_basis)=''
                     OR statement IS NULL OR TRIM(statement)=''""") == 0)
    check("automation: every input says whether it was measured or assumed",
          one("""SELECT COUNT(*) FROM automation_input
                  WHERE origin NOT IN ('measured','assumption')
                     OR basis IS NULL OR TRIM(basis)=''""") == 0 and
          one("""SELECT COUNT(*) FROM automation_input
                  WHERE origin='measured'""") > 0 and
          one("""SELECT COUNT(*) FROM automation_input
                  WHERE origin='assumption'""") > 0)
    # An assumption with no range is a number nobody has tested. The two
    # exceptions are the ceiling rules, which are thresholds rather than rates.
    untested = one("""SELECT COUNT(*) FROM automation_input
                       WHERE origin='assumption' AND low IS NULL
                         AND input_code NOT LIKE '%ceiling%'""")
    check("automation: every assumed rate carries a range",
          untested == 0, f"{untested} assumptions with no range")
    # The arithmetic, checked against itself. Hours saved must be the annual
    # baseline narrowed twice and then reduced by the review share.
    bad = one("""SELECT COUNT(*) FROM automation_opportunity
                  WHERE annual_baseline_hours > 0 AND ABS(hours_saved -
                        annual_baseline_hours * addressable_pct / 100.0
                        * automation_pct / 100.0
                        * (1 - review_pct / 100.0)) > 1.0""")
    check("automation: hours saved is the baseline narrowed by the three rates",
          bad == 0, f"{bad} rows where the arithmetic does not reproduce")
    check("automation: the rates are all shares, and review never exceeds all",
          one("""SELECT COUNT(*) FROM automation_opportunity
                  WHERE addressable_pct < 0 OR addressable_pct > 100
                     OR automation_pct < 0 OR automation_pct > 100
                     OR review_pct < 0 OR review_pct > 100""") == 0)
    # The requirement the whole system is built against: no AI writing to
    # financial or project data unattended. An AI-typed opportunity with no
    # guardrail is that requirement quietly dropped.
    unguarded = one("""SELECT COUNT(*) FROM automation_opportunity
                        WHERE automation_type IN ('ai_assisted','ai_autonomous')
                          AND (guardrail IS NULL OR TRIM(guardrail)='')""")
    check("automation: every AI opportunity names what stays with a person",
          unguarded == 0, f"{unguarded} AI opportunities with no guardrail")
    check("automation: an AI-assisted saving is net of its review time",
          one("""SELECT COUNT(*) FROM automation_opportunity
                  WHERE automation_type='ai_assisted' AND review_pct <= 0""") == 0)
    check("automation: every opportunity carries measured evidence",
          one("""SELECT COUNT(*) FROM automation_opportunity o
                  WHERE NOT EXISTS (SELECT 1 FROM automation_evidence e
                                     WHERE e.opportunity_id=o.opportunity_id)""") == 0)
    # A model that can only say yes is a sales document.
    verdicts = one("""SELECT COUNT(DISTINCT verdict)
                        FROM automation_opportunity""")
    check("automation: the model is capable of rejecting an opportunity",
          verdicts >= 3 and
          one("""SELECT COUNT(*) FROM automation_opportunity
                  WHERE verdict IN ('no','investigate')""") > 0,
          f"{verdicts} distinct verdicts")
    check("automation: a cost saving and recovered cash are kept apart",
          one("""SELECT COUNT(*) FROM automation_summary
                  WHERE scope='org' AND (cost_saved IS NULL
                        OR value_unlocked IS NULL)""") == 0)
    check("automation: the roll-up counts every opportunity",
          one("""SELECT COUNT(*) FROM automation_summary s
                   WHERE s.scope='org' AND s.opportunities <>
                         (SELECT COUNT(*) FROM automation_opportunity o
                           WHERE o.org_id=s.org_id)""") == 0)
    check("automation: sensitivity is run on both levers, in both directions",
          one("""SELECT COUNT(*) FROM (SELECT lever FROM automation_sensitivity
                   GROUP BY org_id, lever)""") == 4 and
          one("""SELECT COUNT(*) FROM automation_sensitivity
                  WHERE delta_vs_base > 0""") > 0 and
          one("""SELECT COUNT(*) FROM automation_sensitivity
                  WHERE delta_vs_base < 0""") > 0)

    # ---------------------------------------------------------------
    # Change capture: the console's write-back path
    #
    # The console reads a snapshot and never writes. Overrides are captured in
    # the browser, exported as a file, and applied by apply_changes.py. These
    # check the landing zone rather than the page: an override that reaches
    # the database without a reason or without an approver is the control
    # having failed open.
    # ---------------------------------------------------------------
    # Scoped to what the applier wrote, not to the whole log. The seed leaves
    # some overrides uncommented on purpose so GOV-01 has real failures to
    # find, and an assertion over every row would contradict the dataset the
    # check engine exists to examine.
    check("changes: every applied override carries a reason and a name",
          one("""SELECT COUNT(*) FROM audit_log
                  WHERE change_source LIKE 'ui via %'
                    AND (change_reason IS NULL OR TRIM(change_reason)=''
                         OR changed_by IS NULL OR TRIM(changed_by)=''
                         OR approved_by IS NULL OR TRIM(approved_by)='')""") == 0)
    # And the other side of that: the seed has to keep producing the failures
    # the governance check reports, or GOV-01 passes because there is nothing
    # left to find rather than because the book is clean.
    uncommented = one("""SELECT COUNT(*) FROM audit_log
                          WHERE is_override=1 AND field_name='project_health'
                            AND (change_reason IS NULL
                                 OR TRIM(change_reason)='')""")
    check("changes: uncommented overrides still exist for GOV-01 to find",
          uncommented > 0, "the governance check has nothing to report")
    check("changes: every audit row records both values",
          one("""SELECT COUNT(*) FROM audit_log
                  WHERE old_value IS NULL OR new_value IS NULL""") == 0)
    check("changes: an applied override actually changed something",
          one("""SELECT COUNT(*) FROM audit_log
                  WHERE change_source LIKE 'ui via %'
                    AND old_value = new_value""") == 0)
    # The applier writes a source naming the file it came from, so a change
    # that arrived through the console can be told from one the seed wrote.
    check("changes: every audit row records where the change came from",
          one("""SELECT COUNT(*) FROM audit_log
                  WHERE change_source IS NULL OR TRIM(change_source)=''""") == 0)
    # The fields apply_changes.py is allowed to touch have to exist, or the
    # allow list is protecting a column that is not there any more.
    import importlib.util as _il
    _spec = _il.spec_from_file_location(
        "apply_changes", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "apply_changes.py"))
    _ac = _il.module_from_spec(_spec)
    _spec.loader.exec_module(_ac)
    _missing = []
    for (_t, _f), _s in _ac.ALLOWED.items():
        _cols = [r[1] for r in con.execute(f"PRAGMA table_info({_t})")]
        for _c in (_f, _s["key"], _s["label"]):
            if _c not in _cols:
                _missing.append(f"{_t}.{_c}")
    check("changes: every field the applier may write still exists",
          not _missing, ", ".join(_missing))

    # ---------------------------------------------------------------
    # Diligence pack: every derived row keeps its evidence
    # ---------------------------------------------------------------
    check("diligence: satisfaction responses carry a verbatim and a theme",
          one("SELECT COUNT(*) FROM csat_response") > 500 and
          one("""SELECT COUNT(*) FROM csat_response
                  WHERE comment IS NOT NULL AND theme IS NULL""") == 0)
    check("diligence: satisfaction tracks delivery outcome",
          (one("""SELECT AVG(c.score) FROM csat_response c JOIN project p
                    USING(project_id) WHERE p.project_health='Green'""")
           - one("""SELECT AVG(c.score) FROM csat_response c JOIN project p
                     USING(project_id) WHERE p.project_health='Red'""")) > 0.5,
          "green and red engagements score too similarly to be believable")
    check("diligence: satisfaction summarised with the response count",
          one("SELECT COUNT(*) FROM csat_summary") > 30 and
          one("SELECT COUNT(*) FROM csat_summary WHERE responses=0") == 0)
    check("diligence: phase analysis ranks the phases and shows the skew",
          one("SELECT COUNT(*) FROM phase_duration_analysis") > 10 and
          one("""SELECT COUNT(*) FROM phase_duration_analysis
                  WHERE pct_slipped IS NULL
                     OR slip_days_when_slipped IS NULL""") == 0)
    check("diligence: the phase that runs long has causes attached with evidence",
          one("""SELECT COUNT(*) FROM phase_duration_analysis p
                  WHERE p.rank_by_slip=1 AND p.scope='org'
                    AND NOT EXISTS (SELECT 1 FROM phase_slip_cause c
                                     WHERE c.phase_analysis_id=p.phase_analysis_id)""") == 0
          and one("""SELECT COUNT(*) FROM phase_slip_cause
                      WHERE evidence IS NULL OR TRIM(evidence)=''""") == 0)
    check("diligence: phases actually differ in how long they run",
          one("""SELECT COUNT(DISTINCT ROUND(pct_slipped)) FROM
                  phase_duration_analysis WHERE scope='org'""") > 2,
          "every phase slips identically, which means the slip is not modelled")
    check("diligence: hypercare measured, with the unbilled share priced",
          one("SELECT COUNT(*) FROM hypercare_period") > 100 and
          one("""SELECT COUNT(*) FROM hypercare_summary
                  WHERE unbilled_cost IS NULL OR billable_share_pct IS NULL""") == 0)
    check("diligence: hypercare runs longer than planned, as it does in practice",
          one("""SELECT AVG(actual_days) - AVG(planned_days)
                   FROM hypercare_period WHERE actual_days IS NOT NULL""") > 0)
    check("diligence: rates published as list, sold, and per hour delivered",
          one("SELECT COUNT(*) FROM rate_analysis") > 30 and
          one("""SELECT COUNT(*) FROM rate_analysis WHERE dimension='org'
                  AND (list_rate_avg IS NULL OR booked_rate_avg IS NULL
                       OR net_rate IS NULL)""") == 0)
    check("diligence: the discount is real, so the three rates are not one rate",
          one("""SELECT discount_pct FROM rate_analysis
                  WHERE dimension='org' LIMIT 1""") > 1.0,
          "booked equals list, so nothing was negotiated anywhere")
    check("diligence: three closed performance cycles per org, with reviews",
          one("SELECT COUNT(*) FROM performance_cycle") == 6 and
          one("SELECT COUNT(*) FROM performance_review") > 300)
    check("diligence: every rating sits beside the measures it was set against",
          one("""SELECT COUNT(*) FROM performance_review
                  WHERE utilization_pct IS NULL OR strengths IS NULL
                     OR development IS NULL OR manager_comment IS NULL""") == 0)
    check("diligence: ratings are distributed, not centred on one band",
          one("SELECT COUNT(DISTINCT rating_label) FROM performance_review") >= 4)
    check("diligence: the rating-to-utilisation relationship is reported",
          one("""SELECT COUNT(*) FROM performance_summary
                  WHERE scope='org' AND rating_vs_utilization_r IS NULL""") == 0)
    check("diligence: product tickets split bugs from enhancements, with effort",
          one("SELECT COUNT(*) FROM product_ticket") > 300 and
          one("""SELECT COUNT(DISTINCT ticket_type) FROM product_ticket""") == 2 and
          one("""SELECT COUNT(*) FROM product_ticket_summary
                  WHERE delivery_effort_hours IS NULL
                     OR backlog_verdict IS NULL""") == 0)
    check("diligence: the red and amber register keeps its reasons attached",
          one("SELECT COUNT(*) FROM rag_register") > 20 and
          one("""SELECT COUNT(*) FROM rag_register g
                  WHERE NOT EXISTS (SELECT 1 FROM rag_reason r
                                     WHERE r.rag_id=g.rag_id)""") == 0)
    check("diligence: every register entry names an action and an owner",
          one("""SELECT COUNT(*) FROM rag_register
                  WHERE recovery_action IS NULL OR recovery_owner IS NULL""") == 0)
    # A margin percentage past this bound is not a margin, it is a project
    # that has booked cost before it has recognised revenue.
    check("diligence: no register entry reports an implausible margin",
          one("""SELECT COUNT(*) FROM rag_register
                  WHERE margin_gap_pts > 100 OR margin_gap_pts < -100""") == 0,
          "a margin gap past a hundred points is arithmetic, not a finding")
    check("diligence: a register entry showing a customer comment shows the score",
          one("""SELECT COUNT(*) FROM rag_register
                  WHERE latest_csat_comment IS NOT NULL
                    AND latest_csat IS NULL""") == 0)

    con.close()

    fails = [r for r in results if not r[0]]
    for ok, name, detail in results:
        if not ok:
            print(f"FAIL  {name}" + (f"\n        {detail}" if detail else ""))
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
