"""
Requirement 39 (what-if simulator) and 40 (deal go / no-go).

Both models are deliberately small and arithmetic. The same formulas and the
same baseline parameters are used by the browser so an executive dragging a
slider gets the number this module would have given them; the baseline is
exported once and the arithmetic is repeated, rather than each side inventing
its own.

Stated assumptions, in one place:
  - Delivery cost is largely fixed in the short run. Losing revenue does not
    remove the consultants, so a customer loss hits margin harder than it hits
    cost. That is the whole reason these scenarios are worth running.
  - REPRICE_SHARE of the book can be repriced within a year (renewals and T&M);
    the rest is locked by signed fixed-price contracts.
  - A new hire reaches target utilisation after RAMP_MONTHS.
  - Delay costs hours: a stood-down team is partly idle and partly reworking.
"""
import json
import os
import sqlite3
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
AS_OF = date(2026, 8, 31)

REPRICE_SHARE = 0.65        # share of revenue that can be repriced within a year
RAMP_MONTHS = 3.0           # months for a new consultant to reach target utilisation
DELAY_IDLE_FACTOR = 0.12    # extra hours per month of delay, as a share of remaining work
ANNUAL_HOURS = 1950.0       # capacity hours per FTE per year


def pct(a, b):
    return (a / b * 100.0) if b else None


# ---------------------------------------------------------------------------
def baseline(con, org_id):
    """The current position every scenario is measured against."""
    cur = con.cursor()
    w12 = [f"{d.year}-{d.month:02d}" for d in
           [date(AS_OF.year - (1 if AS_OF.month - 1 - i <= 0 else 0),
                 (AS_OF.month - 1 - i - 1) % 12 + 1, 1) for i in range(12)]]
    lo, hi = min(w12), max(w12)
    fin = cur.execute(
        """SELECT SUM(f.recognized_revenue) rev, SUM(f.labor_cost) labor,
                  SUM(f.other_cost) other
             FROM project_financial_month f JOIN project p USING(project_id)
            WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?""",
        (org_id, lo, hi)).fetchone()
    hrs = cur.execute(
        """SELECT SUM(hours) total, SUM(CASE WHEN is_billable=1 THEN hours ELSE 0 END) billable
             FROM time_entry WHERE org_id=? AND approval_status='Approved'
              AND strftime('%Y-%m', entry_date) BETWEEN ? AND ?""",
        (org_id, lo, hi)).fetchone()
    hc, cap_month = cur.execute(
        """SELECT COUNT(*), SUM(weekly_capacity_hours)*52.0/12.0 FROM employee
            WHERE org_id=? AND is_active=1 AND is_billable=1""", (org_id,)).fetchone()
    avg_cost = cur.execute(
        """SELECT AVG(cost_rate) FROM employee WHERE org_id=? AND is_active=1
            AND is_billable=1 AND cost_rate IS NOT NULL""", (org_id,)).fetchone()[0]
    target_margin, target_util = cur.execute(
        """SELECT AVG(target_margin_pct), AVG(target_utilization_pct) FROM practice
            WHERE org_id=?""", (org_id,)).fetchone()

    revenue = fin["rev"] or 0.0 if isinstance(fin, sqlite3.Row) else (fin[0] or 0.0)
    labor = (fin["labor"] if isinstance(fin, sqlite3.Row) else fin[1]) or 0.0
    other = (fin["other"] if isinstance(fin, sqlite3.Row) else fin[2]) or 0.0
    total_hours = (hrs["total"] if isinstance(hrs, sqlite3.Row) else hrs[0]) or 0.0
    billable_hours = (hrs["billable"] if isinstance(hrs, sqlite3.Row) else hrs[1]) or 0.0
    cap_year = (cap_month or 0.0) * 12

    return {
        "period": f"{lo} to {hi}",
        "revenue": revenue, "cost": labor + other,
        "labor_cost": labor, "other_cost": other,
        "margin_pct": pct(revenue - labor - other, revenue),
        "hours": total_hours, "billable_hours": billable_hours,
        "capacity_hours": cap_year,
        "utilization_pct": pct(billable_hours, cap_year),
        "headcount": hc,
        "avg_cost_rate": avg_cost or 113.0,
        "realized_rate": (revenue / billable_hours) if billable_hours else 0.0,
        "target_margin_pct": target_margin, "target_utilization_pct": target_util,
        "reprice_share": REPRICE_SHARE, "ramp_months": RAMP_MONTHS,
        "delay_idle_factor": DELAY_IDLE_FACTOR, "annual_hours": ANNUAL_HOURS,
    }


def measures(b, revenue, cost, billable_hours, capacity_hours, headcount, notes=None):
    return {
        "revenue": revenue, "cost": cost,
        "margin_pct": pct(revenue - cost, revenue),
        "utilization_pct": pct(billable_hours, capacity_hours),
        "capacity_hours": capacity_hours, "billable_hours": billable_hours,
        "headcount": headcount, "notes": notes or {},
    }


# ---------------------------------------------------------------------------
def simulate(b, kind, params):
    """Returns (scenario_measures, commentary_by_measure)."""
    rev, cost = b["revenue"], b["cost"]
    bill, cap, hc = b["billable_hours"], b["capacity_hours"], b["headcount"]
    note = {}

    if kind == "UTILIZATION_CHANGE":
        u = params["utilization_pct"] / 100.0
        bill2 = cap * u
        rev2 = bill2 * b["realized_rate"]
        # Cost is people, and the people are already employed.
        cost2 = cost
        note["revenue"] = ("Billable hours move to the target utilisation and are "
                           "valued at the current realised rate. Cost is unchanged "
                           "because the consultants are already on the payroll.")
        return measures(b, rev2, cost2, bill2, cap, hc, note)

    if kind == "HEADCOUNT_CHANGE":
        n = params["headcount_delta"]
        cap2 = cap * (1 + n / hc) if hc else cap
        u = bill / cap if cap else 0
        # new joiners ramp, so they do not deliver a full year of billable hours
        ramp_loss = max(0.0, n) * (b["ramp_months"] / 12.0) * ANNUAL_HOURS * u
        bill2 = cap2 * u - ramp_loss
        rev2 = bill2 * b["realized_rate"]
        cost2 = cost + n * b["avg_cost_rate"] * ANNUAL_HOURS
        note["cost"] = (f"{abs(n)} consultants at ${b['avg_cost_rate']:,.0f}/hr fully "
                        f"loaded over {ANNUAL_HOURS:,.0f} hours.")
        note["revenue"] = (f"New joiners are assumed to reach current utilisation after "
                           f"{b['ramp_months']:,.0f} months, so first-year billable hours "
                           f"are reduced accordingly.")
        return measures(b, rev2, cost2, bill2, cap2, hc + n, note)

    if kind == "RATE_CHANGE":
        x = params["rate_change_pct"] / 100.0
        rev2 = rev * (1 + x * b["reprice_share"])
        note["revenue"] = (f"Only {b['reprice_share']*100:,.0f}% of the book can be "
                           f"repriced within a year. Signed fixed-price work is locked.")
        return measures(b, rev2, cost, bill, cap, hc, note)

    if kind == "CUSTOMER_LOSS":
        lost_rev = params["customer_revenue"]
        lost_hours = params.get("customer_hours", 0.0)
        rev2 = rev - lost_rev
        # the hours come back as capacity, not as a cost saving
        bill2 = max(0.0, bill - lost_hours)
        note["cost"] = ("Cost is unchanged. Losing the account frees consultants, it "
                        "does not remove them from the payroll, so the whole revenue "
                        "loss lands on margin until the capacity is resold or removed.")
        note["utilization_pct"] = (f"{lost_hours:,.0f} billable hours return to the "
                                   f"bench.")
        return measures(b, rev2, cost, bill2, cap, hc, note)

    if kind == "PROJECT_DELAY":
        days = params["delay_days"]
        remaining_hours = params.get("remaining_hours", 0.0)
        project_revenue = params.get("project_revenue", 0.0)
        extra_hours = remaining_hours * DELAY_IDLE_FACTOR * (days / 30.0)
        cost2 = cost + extra_hours * b["avg_cost_rate"]
        # revenue recognition slides out of the window
        slipped = project_revenue * min(1.0, days / 365.0)
        rev2 = rev - slipped
        note["cost"] = (f"{extra_hours:,.0f} additional hours at "
                        f"{DELAY_IDLE_FACTOR*100:,.0f}% of remaining work per month of "
                        f"delay, covering stand-down and re-testing.")
        note["revenue"] = (f"${slipped:,.0f} of recognition slides beyond the window.")
        return measures(b, rev2, cost2, bill, cap, hc, note)

    if kind == "NEW_DEAL":
        value = params["contract_value"]
        hours = params["hours"]
        cost2 = cost + hours * b["avg_cost_rate"] * (1 + params.get("overrun", 0.0))
        rev2 = rev + value
        bill2 = bill + hours
        note["cost"] = (f"{hours:,.0f} hours at ${b['avg_cost_rate']:,.0f}/hr"
                        + (f", uplifted {params.get('overrun',0)*100:,.0f}% for the "
                           f"overrun comparable projects show"
                           if params.get("overrun") else ""))
        return measures(b, rev2, cost2, bill2, cap, hc, note)

    if kind == "RESOURCE_MOVE":
        hours = params["hours"]
        note["revenue"] = ("Moving resource between projects does not change total "
                           "capacity or revenue at the org level. The effect is on the "
                           "two projects' margins and delivery dates, shown below.")
        return measures(b, rev, cost, bill, cap, hc, note)

    raise ValueError(f"unknown scenario type {kind}")


# ---------------------------------------------------------------------------
def cohort_stats(con, org_id):
    """
    Historical outturn by (project type, methodology) and by product, used both
    for go/no-go and for the client-side simulator. Cohorts smaller than five
    completed projects are not reported, because they are not evidence.
    """
    out = {"by_type_method": {}, "by_product": {}, "by_customer": {}, "org": {}}
    rows = con.execute(
        """SELECT project_type, methodology, product, customer_id,
                  actual_hours, total_budget_hours, current_margin_pct,
                  hours_consumed_pct, end_slip_days, co_count
             FROM v_project_360
            WHERE org_id=? AND project_status='Complete' AND total_budget_hours > 0""",
        (org_id,)).fetchall()

    def summarise(bucket):
        n = len(bucket)
        overruns = [r["actual_hours"] / r["total_budget_hours"] - 1 for r in bucket]
        over_budget = sum(1 for r in bucket if (r["hours_consumed_pct"] or 0) > 100)
        late = sum(1 for r in bucket if (r["end_slip_days"] or 0) > 10)
        margins = [r["current_margin_pct"] for r in bucket
                   if r["current_margin_pct"] is not None]
        return {
            "n": n,
            "avg_overrun": sum(overruns) / n,
            "over_budget_rate": over_budget / n,
            "late_rate": late / n,
            "avg_margin_pct": (sum(margins) / len(margins)) if margins else None,
            "avg_change_orders": sum(r["co_count"] or 0 for r in bucket) / n,
        }

    buckets = {}
    for r in rows:
        buckets.setdefault((r["project_type"], r["methodology"]), []).append(r)
    for k, v in buckets.items():
        if len(v) >= 5:
            out["by_type_method"][f"{k[0]}|{k[1]}"] = summarise(v)
    buckets = {}
    for r in rows:
        buckets.setdefault(r["product"], []).append(r)
    for k, v in buckets.items():
        if len(v) >= 5:
            out["by_product"][k] = summarise(v)
    buckets = {}
    for r in rows:
        buckets.setdefault(r["customer_id"], []).append(r)
    for k, v in buckets.items():
        if len(v) >= 3:
            out["by_customer"][str(k)] = summarise(v)
    if rows:
        out["org"] = summarise(list(rows))
    return out


def assess_deal(con, org_id, deal, stats, base):
    """
    Requirement 40. Every factor gets a verdict and a sentence; the
    recommendation is a rule over those factors, not a score nobody can unpick.
    """
    cur = con.cursor()
    value = deal["contract_value"]
    hours = deal["proposed_hours"]
    months = deal.get("timeline_months") or 6
    practice_id = deal.get("practice_id")

    econ = cur.execute(
        """SELECT practice_code, practice_name, target_margin_pct, target_utilization_pct
             FROM practice WHERE practice_id=?""", (practice_id,)).fetchone()
    target = econ["target_margin_pct"] if econ else base["target_margin_pct"]
    cost_rate = cur.execute(
        """SELECT AVG(cost_rate) FROM employee WHERE org_id=? AND practice_id=?
            AND cost_rate IS NOT NULL""", (org_id, practice_id)).fetchone()[0] \
        or base["avg_cost_rate"]

    key = f"{deal.get('project_type')}|{deal.get('methodology')}"
    cohort = stats["by_type_method"].get(key) or \
        stats["by_product"].get(deal.get("product")) or stats["org"] or {}
    peer_overrun = cohort.get("avg_overrun", 0.0) or 0.0
    peer_n = cohort.get("n", 0)
    over_budget_rate = cohort.get("over_budget_rate", 0.0) or 0.0
    late_rate = cohort.get("late_rate", 0.0) or 0.0

    expected_hours = hours * (1 + max(0.0, peer_overrun))
    expected_cost = expected_hours * cost_rate * 1.03      # 3% non-labour
    expected_margin = pct(value - expected_cost, value)
    expected_profit = value - expected_cost

    # resource availability over the delivery window, from the capacity model
    cap = cur.execute(
        """SELECT SUM(gap_hours) gap, SUM(available_hours*0.72) billable
             FROM capacity_month WHERE org_id=? AND practice_id=?""",
        (org_id, practice_id)).fetchone()
    free = max(0.0, cap["gap"] or 0.0)
    # The capacity model runs six months forward. Compare the free hours in
    # that horizon against the share of the deal that lands inside it, not
    # against the whole engagement.
    horizon_months = 6.0
    hours_in_horizon = hours * min(horizon_months, months) / max(months, 1e-9)
    availability = min(100.0, pct(free, hours_in_horizon) or 0.0)

    cust = stats["by_customer"].get(str(deal.get("customer_id"))) or {}
    customer_risk = (1 - (cust.get("avg_margin_pct") or target) / max(target, 1)) * 100 \
        if cust else 25.0
    customer_risk = max(0.0, min(95.0, customer_risk))

    factors = []

    def f(code, label, verdict, statement, value_, unit):
        factors.append((code, label, verdict, statement, value_, unit))

    margin_verdict = ("pass" if expected_margin >= target - 2 else
                      "caution" if expected_margin >= target - 12 else "fail")
    f("MARGIN", "Expected margin", margin_verdict,
      f"Expected margin {expected_margin:,.1f}% against a {target:,.0f}% target, after "
      f"applying the {peer_overrun*100:,.1f}% overrun that {peer_n} comparable completed "
      f"projects actually incurred", expected_margin, "percent")

    avail_verdict = ("pass" if availability >= 85 else
                     "caution" if availability >= 55 else "fail")
    f("RESOURCE", "Resource availability", avail_verdict,
      f"{availability:,.0f}% of the {hours_in_horizon:,.0f} hours falling in the "
      f"next {horizon_months:,.0f} months is uncommitted in "
      f"{econ['practice_name'] if econ else 'this practice'} "
      f"({free:,.0f} free hours against a {hours:,.0f} hour engagement)",
      availability, "percent")

    delivery_verdict = ("pass" if over_budget_rate < 0.4 else
                        "caution" if over_budget_rate < 0.65 else "fail")
    f("DELIVERY", "Delivery risk", delivery_verdict,
      f"{over_budget_rate*100:,.0f}% of {peer_n} comparable projects exceeded their "
      f"hours budget", over_budget_rate * 100, "percent")

    sched_verdict = ("pass" if late_rate < 0.35 else
                     "caution" if late_rate < 0.6 else "fail")
    f("SCHEDULE", "Schedule risk", sched_verdict,
      f"{late_rate*100:,.0f}% of comparable projects delivered more than 10 days late",
      late_rate * 100, "percent")

    if cust:
        cv = ("pass" if (cust.get("avg_margin_pct") or 0) >= target - 3 else
              "caution" if (cust.get("avg_margin_pct") or 0) >= target - 12 else "fail")
        f("CUSTOMER", "Customer history", cv,
          f"{cust['n']} completed projects for this customer averaged "
          f"{cust['avg_margin_pct']:,.1f}% margin and "
          f"{cust['avg_change_orders']:,.1f} change orders",
          cust["avg_margin_pct"], "percent")
    else:
        f("CUSTOMER", "Customer history", "caution",
          "No completed project history for this customer, so no basis for a "
          "customer-specific adjustment", None, "count")

    rate_implied = value / hours if hours else 0
    rate_verdict = ("pass" if rate_implied >= base["realized_rate"] * 0.97 else
                    "caution" if rate_implied >= base["realized_rate"] * 0.88 else "fail")
    f("RATE", "Implied rate", rate_verdict,
      f"Implied rate ${rate_implied:,.0f}/hr against a portfolio realised rate of "
      f"${base['realized_rate']:,.0f}/hr", rate_implied, "currency")

    fails = sum(1 for x in factors if x[2] == "fail")
    cautions = sum(1 for x in factors if x[2] == "caution")
    if fails >= 2 or expected_margin < target - 20 or availability < 40:
        rec = "NO-GO"
    elif fails or cautions >= 2:
        rec = "REVIEW"
    else:
        rec = "GO"

    recs = []
    if expected_margin is not None and expected_margin < target:
        needed_value = expected_cost / (1 - target / 100.0)
        recs.append(("Increase SOW value",
                     f"Increase the SOW by ${needed_value - value:,.0f} to "
                     f"${needed_value:,.0f} to reach the {target:,.0f}% target margin",
                     needed_value - value, "currency"))
        reduce_hours = hours - (value * (1 - target / 100.0) /
                                (cost_rate * 1.03 * (1 + max(0.0, peer_overrun))))
        if reduce_hours > 0:
            recs.append(("Reduce scope",
                         f"Or take {reduce_hours:,.0f} hours out of scope at the same "
                         f"price", reduce_hours, "hours"))
    if availability < 85:
        short = hours_in_horizon * (1 - availability / 100.0)
        recs.append(("Secure capacity",
                     f"Secure {short:,.0f} hours of additional capacity, about "
                     f"{short/(ANNUAL_HOURS*horizon_months/12):,.1f} FTE across the next "
                     f"{horizon_months:,.0f} months, before signing",
                     short, "hours"))
    if over_budget_rate >= 0.5:
        recs.append(("Price the overrun",
                     f"Build the observed {peer_overrun*100:,.1f}% overrun into the "
                     f"estimate rather than the risk register", peer_overrun * 100,
                     "percent"))
    if not recs:
        recs.append(("Proceed", "No material adjustment indicated on current evidence",
                     None, None))

    cur.execute(
        """INSERT INTO deal_assessment(org_id, opportunity_id, deal_name, customer_id,
                contract_value, proposed_hours, timeline_months, billing_model,
                required_skills, recommendation, expected_margin_pct, target_margin_pct,
                expected_revenue, expected_profit, resource_availability_pct,
                delivery_risk_pct, schedule_risk_pct, customer_risk_pct, peer_overrun_pct,
                peer_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (org_id, deal.get("opportunity_id"), deal["deal_name"], deal.get("customer_id"),
         value, hours, months, deal.get("billing_model"), deal.get("required_skills"),
         rec, expected_margin, target, value, expected_profit, availability,
         over_budget_rate * 100, late_rate * 100, customer_risk, peer_overrun * 100,
         peer_n))
    aid = cur.lastrowid
    cur.executemany(
        """INSERT INTO deal_assessment_factor(deal_assessment_id, factor_code, factor_label,
                verdict, statement, metric_value, metric_unit)
           VALUES (?,?,?,?,?,?,?)""",
        [(aid, c, l, v, s, mv, mu) for c, l, v, s, mv, mu in factors])
    cur.executemany(
        """INSERT INTO deal_recommendation(deal_assessment_id, action, statement,
                quantified_value, unit) VALUES (?,?,?,?,?)""",
        [(aid, a, s, v, u) for a, s, v, u in recs])
    return aid


# ---------------------------------------------------------------------------
def build_examples(con, org_id):
    """
    Worked scenarios and deal assessments, stored so the UI opens with real
    output rather than an empty form. The questions are the ones in the
    requirement, asked against this org's actual position.
    """
    cur = con.cursor()
    b = baseline(con, org_id)
    stats = cohort_stats(con, org_id)
    # Idempotent: a re-run replaces this org's worked examples rather than
    # appending a second set, so rebuilding does not silently double them.
    cur.execute("DELETE FROM scenario_result WHERE scenario_id IN "
                "(SELECT scenario_id FROM scenario WHERE org_id=?)", (org_id,))
    cur.execute("DELETE FROM scenario WHERE org_id=?", (org_id,))
    cur.execute("DELETE FROM deal_assessment_factor WHERE deal_assessment_id IN "
                "(SELECT deal_assessment_id FROM deal_assessment WHERE org_id=?)", (org_id,))
    cur.execute("DELETE FROM deal_recommendation WHERE deal_assessment_id IN "
                "(SELECT deal_assessment_id FROM deal_assessment WHERE org_id=?)", (org_id,))
    cur.execute("DELETE FROM deal_assessment WHERE org_id=?", (org_id,))

    # biggest customer, for the loss scenario
    # Customer revenue on the same trailing-12-month basis as the baseline.
    # Taking it from the 24-month profitability cut would double the apparent
    # size of the account against a 12-month org total.
    lo, hi = b["period"].split(" to ")
    top = cur.execute(
        """SELECT c.customer_id AS dimension_key, c.customer_name AS dimension_label,
                  SUM(f.recognized_revenue) AS revenue,
                  (SELECT COALESCE(SUM(t.hours),0) FROM time_entry t
                     JOIN project p2 ON p2.project_id=t.project_id
                    WHERE p2.customer_id=c.customer_id AND t.is_billable=1
                      AND t.approval_status='Approved'
                      AND strftime('%Y-%m', t.entry_date) BETWEEN ? AND ?) AS hours
             FROM project_financial_month f
             JOIN project p  ON p.project_id=f.project_id
             JOIN customer c ON c.customer_id=p.customer_id
            WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?
            GROUP BY c.customer_id ORDER BY revenue DESC LIMIT 1""",
        (lo, hi, org_id, lo, hi)).fetchone()
    # a live project at risk, for the delay scenario
    worst = cur.execute(
        """SELECT p.project_id, p.project_code, p.project_name, mp.revenue_total,
                  mp.predicted_hours_to_complete
             FROM project_margin_prediction mp JOIN project p USING(project_id)
             JOIN project_risk_score rs ON rs.project_id=p.project_id AND rs.run_id=mp.run_id
            WHERE p.org_id=? AND p.project_status='In Flight'
            ORDER BY rs.failure_probability_pct DESC LIMIT 1""", (org_id,)).fetchone()

    defs = [
        ("What happens if utilisation falls to 65%?", "UTILIZATION_CHANGE",
         {"utilization_pct": 65}),
        (f"What happens if utilisation settles at the "
         f"{b['target_utilization_pct']:,.0f}% target?", "UTILIZATION_CHANGE",
         {"utilization_pct": round(b["target_utilization_pct"], 1)}),
        ("What happens if we hire 10 consultants?", "HEADCOUNT_CHANGE",
         {"headcount_delta": 10}),
        ("What happens if billing rates increase by 5%?", "RATE_CHANGE",
         {"rate_change_pct": 5}),
    ]
    if top:
        defs.append((f"What happens if {top['dimension_label']} leaves?", "CUSTOMER_LOSS",
                     {"customer_revenue": top["revenue"], "customer_hours": top["hours"],
                      "customer": top["dimension_label"]}))
    if worst:
        defs.append((f"What happens if {worst['project_code']} is delayed by 30 days?",
                     "PROJECT_DELAY",
                     {"delay_days": 30,
                      "remaining_hours": worst["predicted_hours_to_complete"] or 0,
                      "project_revenue": worst["revenue_total"] or 0,
                      "project": worst["project_code"]}))
    defs.append(("What happens if we take a $2M project?", "NEW_DEAL",
                 {"contract_value": 2_000_000, "hours": 11_000,
                  "overrun": max(0.0, stats.get("org", {}).get("avg_overrun", 0.0))}))

    saved = []
    for question, kind, params in defs:
        res = simulate(b, kind, params)
        cur.execute(
            """INSERT INTO scenario(org_id, scenario_name, scenario_type, question,
                    parameters_json, created_by, is_saved)
               VALUES (?,?,?,?,?,?,1)""",
            (org_id, question, kind, question, json.dumps(params), "Engine (worked example)"))
        sid = cur.lastrowid
        rows = []
        for measure in ("revenue", "cost", "margin_pct", "utilization_pct",
                        "capacity_hours", "billable_hours", "headcount"):
            base_v = b.get({"billable_hours": "billable_hours"}.get(measure, measure))
            if measure == "capacity_hours":
                base_v = b["capacity_hours"]
            new_v = res[measure]
            unit = {"revenue": "currency", "cost": "currency", "margin_pct": "percent",
                    "utilization_pct": "percent", "capacity_hours": "hours",
                    "billable_hours": "hours", "headcount": "count"}[measure]
            rows.append((sid, measure, base_v, new_v,
                         (new_v - base_v) if (base_v is not None and new_v is not None) else None,
                         pct((new_v - base_v), base_v)
                         if (base_v not in (None, 0) and new_v is not None) else None,
                         unit, res["notes"].get(measure)))
        cur.executemany(
            """INSERT INTO scenario_result(scenario_id, measure, baseline_value,
                    scenario_value, delta_value, delta_pct, unit, commentary)
               VALUES (?,?,?,?,?,?,?,?)""", rows)
        saved.append(sid)

    # go / no-go on the largest open opportunities
    opps = cur.execute(
        """SELECT o.*, c.customer_name FROM pipeline_opportunity o
             LEFT JOIN customer c ON c.customer_id=o.customer_id
            WHERE o.org_id=? AND o.stage IN ('Propose','Negotiate','Discover')
            ORDER BY o.services_value DESC LIMIT 6""", (org_id,)).fetchall()
    deals = []
    for o in opps:
        deals.append(assess_deal(con, org_id, {
            "opportunity_id": o["opportunity_id"],
            "deal_name": o["opp_name"],
            "customer_id": o["customer_id"],
            "contract_value": o["services_value"],
            "proposed_hours": o["estimated_hours"],
            "timeline_months": o["expected_duration_months"],
            "billing_model": o["billing_model"],
            "project_type": o["project_type"],
            "product": o["product"],
            "practice_id": o["practice_id"],
            "methodology": "Standard Waterfall",
            "required_skills": None,
        }, stats, b))
    con.commit()
    return {"baseline": b, "cohort_stats": stats, "scenarios": saved, "deals": deals}


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    for org in con.execute("SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        out = build_examples(con, org["org_id"])
        print(f"{org['org_code']}: {len(out['scenarios'])} scenarios, "
              f"{len(out['deals'])} deal assessments, "
              f"{len(out['cohort_stats']['by_type_method'])} type/method cohorts")
    con.close()


if __name__ == "__main__":
    main()
