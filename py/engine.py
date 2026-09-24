"""
PS Business Intelligence Engine.

One pass over the whole book of business per org. Everything it produces is
written back to the database with the drivers that produced it, so any number
the UI shows can be traced to the rows behind it.

Design rules followed throughout:
  - No number is asserted without the evidence rows that support it.
  - Every model is arithmetic that can be read and argued with. The margin
    prediction is an additive cost decomposition, so the drivers sum exactly
    to the gap they explain, rather than being narrated after the fact.
  - Where a coefficient is a judgement call it is named as one, in one place
    (see WEIGHTS), so it can be recalibrated against outturns later.
  - The current month is treated as incomplete and excluded from trend
    measures.

Covers requirements 30-38 and 41-45. Scenario simulation (39) and deal
go/no-go (40) are in scenarios.py, which the UI also runs client side.
"""
import json
import math
import os
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import dq_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
ENGINE_VERSION = "1.0.0"
AS_OF = date(2026, 8, 31)

# Assumptions stated once, in the open.
OVERHEAD_RATE = 0.18        # non-delivery overhead as a share of revenue, for the EBITDA proxy
ASSESSMENT_MONTHS = 24      # history window for the acquisition assessment
HORIZON_MONTHS = 6          # forward capacity horizon

# Logistic coefficients. These are priors, not fitted values - they encode
# which signals matter and how much, and should be recalibrated against
# realised outcomes once the platform has its own outturn history.
WEIGHTS = {
    "budget":   {"burn": 3.20, "peer": 1.40, "capacity": 1.10,
                 "milestone": 0.80, "issues": 0.50, "leakage": 0.60, "pm_load": 0.40},
    "schedule": {"spi": 2.40, "milestone": 1.90, "uat": 1.30,
                 "issues": 0.90, "pm_load": 0.70, "capacity": 0.60},
    "margin":   {"burn": 2.80, "peer": 1.20, "leakage": 1.60,
                 "co_dependency": 0.70, "capacity": 0.90, "margin_gap": 1.50},
}
# Health bands are set relative to the organisation's own base rate rather than
# at fixed absolute probabilities. In a business where 60% of completed projects
# historically exceeded their hours budget, a 55% probability of overrun is the
# norm, not an alarm, and a fixed threshold would paint the whole portfolio red.
BAND_RED = 0.18      # points of probability above the base rate
BAND_YELLOW = 0.07
# Failure probability is a stated blend of the three, not a hidden one.
FAILURE_BLEND = {"margin": 0.45, "budget": 0.35, "schedule": 0.20}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def logistic(x):
    return 1.0 / (1.0 + math.exp(-clamp(x, -20, 20)))


def month_key(d):
    return d.strftime("%Y-%m")


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def months_back(d, n):
    return [month_key(add_months(date(d.year, d.month, 1), -i)) for i in range(n, 0, -1)]


def pct(num, den):
    return (num / den * 100.0) if den else None


class Engine:
    def __init__(self, con, org_id, as_of=AS_OF):
        self.con = con
        self.cur = con.cursor()
        self.org_id = org_id
        self.as_of = as_of
        self.run_id = None
        self.projects = {}
        self.peers_by_project = {}
        self.margin = {}
        self.risk = {}
        self.bench = {}
        # complete months only: the current month is still being booked
        self.window = months_back(as_of, ASSESSMENT_MONTHS)
        self.org = dict(con.execute(
            "SELECT * FROM org WHERE org_id=?", (org_id,)).fetchone())

    # -- helpers -----------------------------------------------------------
    def rows(self, sql, params=()):
        return [dict(r) for r in self.con.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        r = self.con.execute(sql, params).fetchone()
        return dict(r) if r else None

    def scalar(self, sql, params=()):
        r = self.con.execute(sql, params).fetchone()
        return r[0] if r else None

    # =====================================================================
    # Tables the engine and everything downstream of it own, children before
    # parents. Nothing here is source data: these are all derived, so a rerun
    # is free once they are cleared.
    DERIVED = [
        # engine
        "briefing_item", "executive_briefing", "risk_driver", "margin_driver",
        "benchmark_peer", "project_risk_score", "project_margin_prediction",
        "project_benchmark", "acquisition_metric", "profitability_cut",
        "red_flag", "acquisition_assessment", "capacity_month",
        "data_quality_finding", "forecast_accuracy", "future_risk_prediction",
        "leadership_action", "ps_os_snapshot",
        # scenarios
        "scenario_result", "deal_recommendation", "deal_assessment_factor",
        "deal_assessment", "scenario",
        # diligence
        "phase_slip_cause", "phase_duration_analysis", "rag_reason",
        "rag_register", "csat_summary", "hypercare_summary", "rate_analysis",
        "performance_summary", "product_ticket_summary",
        # checks, resourcing, economics, earned value
        "check_finding", "check_result", "check_run",
        "resource_plan_sensitivity", "resource_plan_action",
        "resource_plan_month", "resource_plan_line", "resource_plan",
        "billing_position", "billing_month", "billing_exception",
        "model_economics", "model_margin_band", "model_comparison",
        "project_evm", "evm_summary", "works_practice", "works_metric",
        "works_cadence", "works_component",
        "automation_evidence", "automation_sensitivity", "automation_summary",
        "automation_opportunity", "automation_input",
        # last, because everything above points at it
        "intelligence_run",
    ]

    def reset_org(self):
        """
        Clear this organisation's derived rows before writing new ones.

        The engine was written to run once over a freshly built schema, which
        is true of the rebuild script and of nothing else. Loading a new target
        and re-running the engine over the existing database hit that
        assumption: the executive briefing is an INSERT OR REPLACE, the replace
        deleted the previous briefing, and the briefing items still pointing at
        it took the whole run down with a foreign key error.

        Scoping fifty tables to one organisation is the awkward part. Six of
        them carry no org of their own and are reached through a parent, three
        more through a grandparent, and getting the order wrong fails on a
        foreign key rather than on anything visible. So rather than encode the
        graph by hand and maintain it, the deletes run with foreign keys off
        and the result is then checked: PRAGMA foreign_key_check has to come
        back empty before the run proceeds. That turns an ordering problem
        into an assertion, and the assertion is the thing worth keeping.
        """
        self.con.commit()                 # a pragma is a no-op mid transaction
        self.con.execute("PRAGMA foreign_keys = OFF")
        try:
            for t in self.DERIVED:
                cols = [r[1] for r in
                        self.con.execute(f"PRAGMA table_info({t})")]
                if not cols:
                    continue              # a table this build does not have
                if "org_id" in cols:
                    self.con.execute(f"DELETE FROM {t} WHERE org_id=?",
                                     (self.org_id,))
                    continue
                # No org of its own: reached through whichever project or
                # parent row it hangs off.
                if "project_id" in cols:
                    self.con.execute(
                        f"""DELETE FROM {t} WHERE project_id IN
                            (SELECT project_id FROM project WHERE org_id=?)""",
                        (self.org_id,))
                    continue
                pred = self.ORPHAN_PARENT.get(t)
                if pred is None:
                    raise SystemExit(
                        f"reset_org: {t} has no org_id, no project_id and no "
                        f"parent predicate. Add one rather than leaving the "
                        f"table behind on a rerun.")
                self.con.execute(f"DELETE FROM {t} WHERE {pred}",
                                 (self.org_id,))
            self.con.commit()
        finally:
            self.con.execute("PRAGMA foreign_keys = ON")
        bad = self.con.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise SystemExit(
                f"reset_org left {len(bad)} orphaned rows, first in "
                f"{bad[0][0]}. The delete list is incomplete: add the table "
                f"rather than turning the check off.")

    # The tables with no org and no project of their own. Each carries the
    # predicate that scopes it, because some are two levels from anything that
    # knows which organisation it belongs to and a single parent name cannot
    # express that.
    ORPHAN_PARENT = {
        "briefing_item":
            "briefing_id IN (SELECT briefing_id FROM executive_briefing "
            "WHERE org_id=?)",
        "risk_driver":
            "risk_score_id IN (SELECT risk_score_id FROM project_risk_score "
            "WHERE project_id IN (SELECT project_id FROM project "
            "WHERE org_id=?))",
        "margin_driver":
            "prediction_id IN (SELECT prediction_id FROM "
            "project_margin_prediction WHERE project_id IN "
            "(SELECT project_id FROM project WHERE org_id=?))",
        "benchmark_peer":
            "benchmark_id IN (SELECT benchmark_id FROM project_benchmark "
            "WHERE project_id IN (SELECT project_id FROM project "
            "WHERE org_id=?))",
        "acquisition_metric":
            "assessment_id IN (SELECT assessment_id FROM "
            "acquisition_assessment WHERE org_id=?)",
        "scenario_result":
            "scenario_id IN (SELECT scenario_id FROM scenario WHERE org_id=?)",
        "deal_recommendation":
            "deal_assessment_id IN (SELECT deal_assessment_id FROM "
            "deal_assessment WHERE org_id=?)",
        "deal_assessment_factor":
            "deal_assessment_id IN (SELECT deal_assessment_id FROM "
            "deal_assessment WHERE org_id=?)",
        "phase_slip_cause":
            "phase_analysis_id IN (SELECT phase_analysis_id FROM "
            "phase_duration_analysis WHERE org_id=?)",
        "rag_reason":
            "rag_id IN (SELECT rag_id FROM rag_register WHERE org_id=?)",
        "resource_plan_line":
            "plan_id IN (SELECT plan_id FROM resource_plan WHERE org_id=?)",
        "resource_plan_month":
            "plan_line_id IN (SELECT plan_line_id FROM resource_plan_line WHERE plan_id IN (SELECT plan_id FROM resource_plan WHERE org_id=?))",
        "resource_plan_action":
            "plan_line_id IN (SELECT plan_line_id FROM resource_plan_line WHERE plan_id IN (SELECT plan_id FROM resource_plan WHERE org_id=?))",
        "resource_plan_sensitivity":
            "plan_line_id IN (SELECT plan_line_id FROM resource_plan_line "
            "WHERE plan_id IN (SELECT plan_id FROM resource_plan "
            "WHERE org_id=?))",
        "check_finding":
            "check_run_id IN (SELECT check_run_id FROM check_run "
            "WHERE org_id=?)",
        "check_result":
            "check_run_id IN (SELECT check_run_id FROM check_run "
            "WHERE org_id=?)",
        "automation_evidence":
            "opportunity_id IN (SELECT opportunity_id FROM "
            "automation_opportunity WHERE org_id=?)",
    }

    def run(self):
        t0 = time.time()
        self.reset_org()
        self.cur.execute(
            """INSERT INTO intelligence_run(org_id, as_of_date, engine_version)
               VALUES (?,?,?)""", (self.org_id, self.as_of.isoformat(), ENGINE_VERSION))
        self.run_id = self.cur.lastrowid

        self.load_portfolio()
        self.rates = self.base_rates()
        self.build_peer_cohorts()
        self.predict_margins()
        self.score_risk()
        self.write_benchmarks()
        self.profitability_cuts()
        self.capacity_model()
        self.forecast_accuracy()
        self.future_risks()
        self.data_quality()
        self.acquisition_assessment()
        self.red_flags()
        self.ps_operating_system()
        self.leadership_actions()
        self.executive_briefing()
        self.migration_score()

        self.cur.execute(
            """UPDATE intelligence_run SET projects_scored=?, runtime_ms=?,
                    base_rates_json=?, intercepts_json=? WHERE run_id=?""",
            (len(self.risk), int((time.time() - t0) * 1000),
             json.dumps(self.rates), json.dumps(self.intercepts), self.run_id))
        self.con.commit()
        return self.run_id

    # =====================================================================
    def load_portfolio(self):
        """Everything the models need about each project, in one read."""
        for p in self.rows("SELECT * FROM v_project_360 WHERE org_id=?", (self.org_id,)):
            pid = p["project_id"]
            p["actual_hours"] = p["actual_hours"] or 0.0
            p["billable_hours"] = p["billable_hours"] or 0.0
            p["actual_labor_cost"] = p["actual_labor_cost"] or 0.0
            p["total_budget_hours"] = p["total_budget_hours"] or 0.0
            p["recognized_revenue_to_date"] = p["recognized_revenue_to_date"] or 0.0
            p["total_revenue"] = p["total_revenue"] or 0.0
            p["co_count"] = p["co_count"] or 0
            # Physical progress is earned planned hours over total planned
            # hours. A flat average of task percentages weights a two-hour
            # sign-off the same as a 400-hour build.
            if (p["plan_hours"] or 0) > 0:
                p["pc_frac"] = clamp((p["earned_plan_hours"] or 0) / p["plan_hours"],
                                     0.0, 1.0)
            else:
                p["pc_frac"] = clamp((p["avg_percent_complete"] or 0) / 100.0, 0.0, 1.0)
            self.projects[pid] = p

        # expenses and unapproved change orders, per project
        for r in self.rows("""SELECT f.project_id, SUM(f.other_cost) other_cost
                                FROM project_financial_month f JOIN project p USING(project_id)
                               WHERE p.org_id=? GROUP BY f.project_id""", (self.org_id,)):
            if r["project_id"] in self.projects:
                self.projects[r["project_id"]]["other_cost"] = r["other_cost"] or 0.0
        for r in self.rows("""SELECT co.project_id,
                                     SUM(CASE WHEN co.status IN ('Submitted','Draft')
                                              THEN co.co_value ELSE 0 END) pending_co_value,
                                     SUM(CASE WHEN co.status IN ('Submitted','Draft')
                                              THEN co.co_hours ELSE 0 END) pending_co_hours
                                FROM change_order co JOIN project p USING(project_id)
                               WHERE p.org_id=? GROUP BY co.project_id""", (self.org_id,)):
            if r["project_id"] in self.projects:
                self.projects[r["project_id"]].update(r)
        # non-billable share and top-resource concentration
        for r in self.rows("""SELECT t.project_id,
                                     SUM(CASE WHEN t.is_billable=0 THEN t.hours ELSE 0 END) nb_hours,
                                     SUM(t.hours) all_hours
                                FROM time_entry t WHERE t.org_id=? AND t.project_id IS NOT NULL
                                  AND t.approval_status='Approved'
                               GROUP BY t.project_id""", (self.org_id,)):
            p = self.projects.get(r["project_id"])
            if p:
                p["nb_share"] = (r["nb_hours"] / r["all_hours"]) if r["all_hours"] else 0.0
        for r in self.rows("""SELECT project_id, MAX(h) top_hours, SUM(h) tot_hours
                                FROM (SELECT project_id, employee_id, SUM(hours) h
                                        FROM time_entry WHERE org_id=? AND project_id IS NOT NULL
                                         AND approval_status='Approved'
                                       GROUP BY project_id, employee_id)
                               GROUP BY project_id""", (self.org_id,)):
            p = self.projects.get(r["project_id"])
            if p:
                p["top_resource_share"] = (r["top_hours"] / r["tot_hours"]) if r["tot_hours"] else 0.0
        # UAT / Test phase start slippage
        for r in self.rows("""SELECT t.project_id,
                                     MIN(julianday(t.actual_start) - julianday(t.planned_start)) AS d
                                FROM project_task t JOIN project p USING(project_id)
                               WHERE p.org_id=? AND t.phase='Test' AND t.actual_start IS NOT NULL
                               GROUP BY t.project_id""", (self.org_id,)):
            p = self.projects.get(r["project_id"])
            if p:
                p["uat_days_late"] = max(0.0, r["d"] or 0.0)
        # PM concurrent load
        pm_load = dict(self.con.execute(
            """SELECT project_manager_id, COUNT(*) FROM project
                WHERE org_id=? AND project_status='In Flight' AND project_manager_id IS NOT NULL
                GROUP BY 1""", (self.org_id,)).fetchall())
        # latest PM forecast per project
        fc = {}
        for r in self.rows("""SELECT s.project_id, s.snapshot_type, s.forecast_hours,
                                     s.forecast_cost, s.forecast_margin_pct, s.snapshot_date
                                FROM project_forecast_snapshot s JOIN project p USING(project_id)
                               WHERE p.org_id=? ORDER BY s.project_id, s.snapshot_date""",
                           (self.org_id,)):
            fc[r["project_id"]] = r
        for pid, p in self.projects.items():
            p.setdefault("other_cost", 0.0)
            p.setdefault("pending_co_value", 0.0)
            p.setdefault("pending_co_hours", 0.0)
            p.setdefault("nb_share", 0.0)
            p.setdefault("top_resource_share", 0.0)
            p.setdefault("uat_days_late", 0.0)
            p["pm_projects"] = pm_load.get(p["project_manager_id"], 0)
            p["latest_forecast"] = fc.get(pid)
            p["total_cost_to_date"] = p["actual_labor_cost"] + p["other_cost"]
            p["expense_ratio"] = (p["other_cost"] / p["actual_labor_cost"]
                                  if p["actual_labor_cost"] else 0.03)
            p["cost_rate_actual"] = (p["actual_labor_cost"] / p["actual_hours"]
                                     if p["actual_hours"] else None)
            p["cost_rate_plan"] = (p["budget_cost"] / p["budget_hours"]
                                   if p["budget_hours"] else None)
            # elapsed fraction of the planned schedule
            try:
                sd = datetime.fromisoformat(p["start_date"]).date()
                ed = datetime.fromisoformat(p["planned_end_date"]).date()
                span = max(1, (ed - sd).days)
                p["elapsed_frac"] = clamp((self.as_of - sd).days / span, 0.0, 3.0)
            except (TypeError, ValueError):
                p["elapsed_frac"] = 0.0

    # =====================================================================
    def build_peer_cohorts(self):
        """
        Comparable completed projects. Similarity is an explicit attribute
        score so the cohort can be explained to whoever questions it, and the
        threshold relaxes rather than returning a cohort of two.
        """
        completed = [p for p in self.projects.values()
                     if p["project_status"] == "Complete" and (p["total_budget_hours"] or 0) > 0]
        for p in self.projects.values():
            if p["project_status"] not in ("In Flight", "Not Started"):
                continue
            scored = []
            for q in completed:
                if q["project_id"] == p["project_id"]:
                    continue
                sc = 0
                if q["product"] == p["product"]:
                    sc += 25
                if q["project_type"] == p["project_type"]:
                    sc += 20
                if q["methodology"] == p["methodology"]:
                    sc += 15
                if q["billing_model"] == p["billing_model"]:
                    sc += 10
                if q["practice_id"] == p["practice_id"]:
                    sc += 10
                bh_p = p["total_budget_hours"] or 0
                bh_q = q["total_budget_hours"] or 0
                if bh_p > 0 and bh_q > 0:
                    ratio = bh_q / bh_p
                    if 0.6 <= ratio <= 1.67:
                        sc += 20
                    elif 0.4 <= ratio <= 2.5:
                        sc += 10
                scored.append((sc, q))
            cohort = []
            for threshold in (55, 45, 35, 25):
                cohort = [(sc, q) for sc, q in scored if sc >= threshold]
                if len(cohort) >= 8:
                    break
            cohort.sort(key=lambda x: -x[0])
            self.peers_by_project[p["project_id"]] = {
                "threshold": threshold,
                "peers": cohort[:60],
            }

    def peer_stats(self, pid):
        info = self.peers_by_project.get(pid)
        if not info or not info["peers"]:
            return None
        peers = [q for _, q in info["peers"]]
        n = len(peers)

        def avg(fn):
            vals = [fn(q) for q in peers if fn(q) is not None]
            return sum(vals) / len(vals) if vals else None

        overruns = [q["actual_hours"] / q["total_budget_hours"] - 1.0
                    for q in peers if q["total_budget_hours"]]
        return {
            "peer_count": n,
            "match_basis": "product, project type, methodology, billing model, "
                           f"practice and size band (similarity >= {info['threshold']}%)",
            "avg_duration_days": avg(lambda q: q["elapsed_days"]),
            "avg_hours": avg(lambda q: q["actual_hours"]),
            "avg_revenue": avg(lambda q: q["total_revenue"]),
            "avg_margin_pct": avg(lambda q: q["current_margin_pct"]),
            "avg_delay_days": avg(lambda q: q["end_slip_days"]),
            "avg_change_orders": avg(lambda q: q["co_count"]),
            "avg_change_order_value": avg(lambda q: q["co_value_approved"]),
            "avg_team_size": avg(lambda q: q["distinct_resources"]),
            "budget_overrun": (sum(overruns) / len(overruns)) if overruns else 0.0,
            "peers": [{"project_id": q["project_id"], "similarity": sc}
                      for sc, q in info["peers"][:25]],
        }

    # =====================================================================
    def predict_margins(self):
        """
        Requirement 32. Predicted cost is built additively from the plan, so
        the drivers sum exactly to the gap between the forecast margin and the
        predicted margin. Nothing is attributed after the fact.
        """
        for pid, p in self.projects.items():
            if p["project_status"] not in ("In Flight", "Not Started"):
                continue
            ps = self.peer_stats(pid) or {}
            peer_overrun = ps.get("budget_overrun", 0.0) or 0.0
            budget_hours = p["total_budget_hours"] or 0.0
            pc = max(p["pc_frac"], 0.0)
            rate_actual = p["cost_rate_actual"] or p["cost_rate_plan"] or 0.0
            rate_plan = p["cost_rate_plan"] or rate_actual
            exp_ratio = clamp(p["expense_ratio"], 0.0, 0.15)

            # How much weight to give this project's own velocity depends on
            # how far through it is. Early on, peer history is the better guide.
            w_actual = clamp(pc * 1.4, 0.15, 0.85)
            w_peer = (1.0 - w_actual) * 0.75

            # Estimate to complete, built additively from the plan so the
            # decomposition below is exact rather than narrated afterwards.
            # budget_hours here is total_budget_hours from the view, which
            # already includes approved change-order hours. Adding them again
            # inflates the plan and makes every prediction look pessimistic.
            d_scope = p["co_hours_approved"] or 0.0            # for the wording only
            plan_hours = budget_hours
            eac_velocity = (p["actual_hours"] / pc) if pc >= 0.05 else plan_hours
            d_burn = max(0.0, eac_velocity - plan_hours) * w_actual
            d_peer = plan_hours * max(0.0, peer_overrun) * w_peer
            slip = clamp(p["elapsed_frac"] - pc, 0.0, 0.8)
            d_slip = plan_hours * slip * 0.15

            raw_eac = plan_hours + d_burn + d_peer + d_slip
            # Two bounds. Hours already booked are a floor: a project cannot
            # finish for less than it has spent. Three times the plan is a
            # ceiling, because extrapolating velocity from a project that is 5%
            # complete produces arithmetically valid numbers nobody should act
            # on. Whichever bound binds is reported as its own line, so the
            # decomposition still adds up.
            eac_hours = max(p["actual_hours"], min(raw_eac, plan_hours * 3.0))
            d_bound = eac_hours - raw_eac
            eac_capped = raw_eac > plan_hours * 3.0

            predicted_labor = eac_hours * rate_actual
            predicted_cost = predicted_labor * (1 + exp_ratio)
            plan_cost = plan_hours * rate_plan * (1 + exp_ratio)

            # Revenue at completion, by billing model. Time and materials earns
            # on hours delivered, so its revenue at completion is the expected
            # billable hours at the realised rate. Using the value billed so
            # far as the denominator of a completion margin makes every live
            # T&M project look catastrophic.
            approved_revenue = (p["contract_value"] or 0.0) + (p["co_value_approved"] or 0.0)
            bm = p["billing_model"]
            if bm in ("Time and Materials", "Capped T&M") and p["billable_hours"] > 0:
                realised_bill_rate = (p["billable_value"] or 0.0) / p["billable_hours"]
                # The project's own billable share, but only once it has
                # delivered enough hours for that share to mean anything. An
                # engagement nine days old with fourteen hours booked, one of
                # them billable, is not telling you that 5% of its remaining
                # three hundred hours will be billable. Projecting completion
                # revenue off that produced a forecast margin of minus nine
                # hundred percent on a perfectly ordinary new engagement, and
                # one number like that on a portfolio screen costs the whole
                # screen its credibility. Below the threshold the
                # organisation's own trailing share is the better estimate.
                min_hours = max(40.0, plan_hours * 0.15)
                if p["actual_hours"] >= min_hours:
                    billable_share = p["billable_hours"] / p["actual_hours"]
                    share_basis = "this engagement"
                else:
                    billable_share = self.org_billable_share()
                    share_basis = "organisation trailing average"
                tm_revenue = eac_hours * billable_share * realised_bill_rate
                revenue = min(tm_revenue, approved_revenue) if bm == "Capped T&M" else tm_revenue
            else:
                revenue = approved_revenue
                share_basis = None
            if revenue <= 0:
                continue

            fc = p["latest_forecast"] or {}
            forecast_cost = fc.get("forecast_cost") or plan_cost
            forecast_margin = pct(revenue - forecast_cost, revenue)
            plan_margin = pct(revenue - plan_cost, revenue)
            current_margin = p["current_margin_pct"]
            predicted_margin = pct(revenue - predicted_cost, revenue)
            target = p["target_margin_pct"]

            if predicted_margin is None:
                continue
            # Thresholds are set so that "High" means genuinely troubled work,
            # not simply below an ambitious target. An organisation that targets
            # 36% and delivers 30% would otherwise show most of its book in
            # alarm, and the alarm would be ignored.
            if predicted_margin < 0:
                risk = "Critical"
            elif predicted_margin < target - 15 or predicted_margin < 15:
                risk = "High"
            elif predicted_margin < target - 5:
                risk = "Medium"
            else:
                risk = "Low"

            hours_available = self.remaining_team_capacity(p)

            self.cur.execute(
                """INSERT INTO project_margin_prediction(run_id, project_id, revenue_total,
                        actual_cost, forecast_cost, predicted_cost, current_margin_pct,
                        forecast_margin_pct, predicted_margin_pct, target_margin_pct,
                        margin_risk, predicted_hours_to_complete, hours_available,
                        plan_cost, plan_margin_pct)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.run_id, pid, revenue, p["total_cost_to_date"], forecast_cost,
                 predicted_cost, current_margin, forecast_margin, predicted_margin,
                 target, risk, max(0.0, eac_hours - p["actual_hours"]), hours_available,
                 plan_cost, plan_margin))
            prediction_id = self.cur.lastrowid

            # Exact decomposition of predicted_cost - plan_cost. Expenses sit in
            # both terms, so they are not a separate line; the hour deltas carry
            # the expense uplift with them.
            #   predicted - plan = [(d_burn + d_peer + d_slip + d_bound) * rate_actual
            #                       + plan_hours * (rate_actual - rate_plan)] * (1 + exp)
            drivers = []
            uplift = (1 + exp_ratio)

            def add(code, statement, cost_delta, value, unit):
                if abs(cost_delta) < 1:
                    return
                drivers.append((prediction_id, code, statement,
                                round(-cost_delta / revenue * 100, 4), value, unit))

            add("BURN_RATE",
                f"Burn to date implies {eac_velocity:,.0f} hours at completion against a "
                f"{plan_hours:,.0f} hour plan, weighted {w_actual*100:,.0f}% at "
                f"{pc*100:,.0f}% complete",
                d_burn * rate_actual * uplift, round(d_burn, 1), "hours")
            add("PEER_HISTORY",
                f"Comparable completed projects overran their budget by "
                f"{peer_overrun*100:,.1f}% on average",
                d_peer * rate_actual * uplift, round(d_peer, 1), "hours")
            add("SCHEDULE_SLIP",
                f"Elapsed schedule is {(p['elapsed_frac']-pc)*100:,.0f} points ahead of "
                f"physical progress, which historically carries rework",
                d_slip * rate_actual * uplift, round(d_slip, 1), "hours")
            add("RATE_MIX",
                f"Delivered blended cost rate is ${rate_actual:,.0f}/hr against "
                f"${rate_plan:,.0f}/hr planned",
                plan_hours * (rate_actual - rate_plan) * uplift,
                round(rate_actual - (rate_plan or 0), 2), "currency")
            if d_bound > 0:
                add("HOURS_ALREADY_SPENT",
                    f"{p['actual_hours']:,.0f} hours are already booked, above the modelled "
                    f"estimate to complete of {raw_eac:,.0f}",
                    d_bound * rate_actual * uplift, round(d_bound, 1), "hours")
            elif d_bound < 0:
                add("ESTIMATE_BOUNDED",
                    f"Modelled estimate of {raw_eac:,.0f} hours was capped at three times "
                    f"the {plan_hours:,.0f} hour plan",
                    d_bound * rate_actual * uplift, round(d_bound, 1), "hours")
            self.cur.executemany(
                """INSERT INTO margin_driver(prediction_id, driver_code, statement,
                        margin_impact_pts, metric_value, metric_unit)
                   VALUES (?,?,?,?,?,?)""", drivers)

            self.margin[pid] = {
                "prediction_id": prediction_id, "revenue": revenue,
                "predicted_margin": predicted_margin, "forecast_margin": forecast_margin,
                "plan_margin": plan_margin,
                "current_margin": current_margin, "target": target, "risk": risk,
                "eac_hours": eac_hours, "peer_overrun": peer_overrun,
                "hours_to_complete": max(0.0, eac_hours - p["actual_hours"]),
                "hours_available": hours_available,
                "eac_capped": eac_capped, "pc": pc,
            }

    def org_billable_share(self):
        """
        The organisation's trailing billable share of project hours.

        Used as the prior for an engagement too young to have one of its own.
        Cached because it is the same answer for every project in the run.
        """
        if getattr(self, "_org_billable_share", None) is None:
            row = self.cur.execute(
                """SELECT SUM(CASE WHEN t.is_billable=1 THEN t.hours END),
                          SUM(t.hours)
                     FROM time_entry t
                    WHERE t.org_id=? AND t.project_id IS NOT NULL
                      AND t.approval_status='Approved'
                      AND t.entry_date >= date((SELECT MAX(entry_date)
                                                  FROM time_entry),
                                               '-365 days')""",
                (self.org_id,)).fetchone()
            self._org_billable_share = (
                (row[0] / row[1]) if row and row[1] else 0.9)
        return self._org_billable_share

    def remaining_team_capacity(self, p):
        """Assigned team's uncommitted hours between now and the planned end."""
        try:
            end = datetime.fromisoformat(p["planned_end_date"]).date()
        except (TypeError, ValueError):
            return 0.0
        # A project already at or past its planned end still has a recovery
        # window. Flooring at four weeks stops the shortfall percentage from
        # dividing by something close to zero and reporting 1,700% short.
        weeks = max(4.0, (end - self.as_of).days / 7.0)
        rows = self.rows(
            """SELECT a.employee_id, a.allocation_pct, e.weekly_capacity_hours
                 FROM assignment a JOIN employee e USING(employee_id)
                WHERE a.project_id=?""", (p["project_id"],))
        total = 0.0
        for r in rows:
            total += (r["weekly_capacity_hours"] or 37.5) * weeks * \
                clamp((r["allocation_pct"] or 100) / 100.0, 0.05, 1.0)
        return total

    # =====================================================================
    def base_rates(self):
        """
        Observed outturn rates on completed projects. These are what the
        probability model is calibrated against, so a predicted 60% means
        "worse than a business where 60% of projects overran" rather than an
        arbitrary number from a hand-set intercept.
        """
        done = [p for p in self.projects.values()
                if p["project_status"] == "Complete" and (p["total_budget_hours"] or 0) > 0]
        n = len(done) or 1
        over = sum(1 for p in done if p["actual_hours"] > p["total_budget_hours"])
        late = sum(1 for p in done if (p["end_slip_days"] or 0) > 10)
        below = sum(1 for p in done
                    if p["current_margin_pct"] is not None
                    and p["current_margin_pct"] < p["target_margin_pct"])
        rates = {"budget": over / n, "schedule": late / n, "margin": below / n,
                 "sample": len(done)}
        rates["failure"] = (FAILURE_BLEND["margin"] * rates["margin"]
                            + FAILURE_BLEND["budget"] * rates["budget"]
                            + FAILURE_BLEND["schedule"] * rates["schedule"])
        return rates

    @staticmethod
    def fit_intercept(zs, target_rate):
        """
        Shift the intercept until the mean predicted probability matches the
        observed rate. Bisection on a monotone function - no optimiser needed.
        """
        if not zs:
            return 0.0
        target_rate = clamp(target_rate, 0.02, 0.95)
        lo, hi = -12.0, 12.0
        for _ in range(60):
            mid = (lo + hi) / 2
            mean = sum(logistic(mid + z) for z in zs) / len(zs)
            if mean < target_rate:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def score_risk(self):
        """
        Requirement 31. Two passes: build the features, calibrate the three
        intercepts against observed outturn rates, then write the scores with
        the drivers that produced them.
        """
        feats = {}
        for pid, p in self.projects.items():
            if p["project_status"] not in ("In Flight", "Not Started"):
                continue
            m = self.margin.get(pid)
            ps = self.peer_stats(pid) or {}
            peer_overrun = ps.get("budget_overrun", 0.0) or 0.0
            pc = p["pc_frac"]
            budget_hours = p["total_budget_hours"] or 0.0

            burn_index = ((p["actual_hours"] / budget_hours) / pc
                          if (budget_hours and pc >= 0.05) else 1.0)
            # Below about 15% complete, burn per unit of progress is dividing
            # by a small, lumpy number. Damp it rather than pretending an
            # early mobilisation spike is a trend.
            early_damp = clamp(pc / 0.15, 0.25, 1.0)
            f = {
                "burn": clamp(burn_index - 1.0, -0.5, 1.2) * early_damp,
                "spi": clamp(p["elapsed_frac"] - pc, -0.5, 0.8),
                "milestone": clamp((p["critical_milestones_late"] or 0) / 3.0, 0, 1.5),
                "uat": clamp((p["uat_days_late"] or 0) / 30.0, 0, 2.0),
                "pm_load": clamp(((p["pm_projects"] or 0) - 3) / 5.0, 0, 1.2),
                "peer": clamp(peer_overrun, -0.2, 0.8),
                "issues": clamp((p["open_high_items"] or 0) / 4.0, 0, 1.5),
            }
            cap_gap = 0.0
            if m and m["hours_available"] > 0:
                cap_gap = m["hours_to_complete"] / m["hours_available"] - 1.0
            f["capacity"] = clamp(cap_gap, -0.3, 1.0)
            co_dep = ((p["pending_co_value"] or 0) / p["total_revenue"]
                      if p["total_revenue"] else 0.0)
            f["co_dependency"] = clamp(co_dep, 0, 0.6)
            f["leakage"] = clamp((p["nb_share"] or 0) - 0.06, 0, 0.30) * 3
            gap = ((p["target_margin_pct"] - m["predicted_margin"]) / 100.0
                   if m and m["predicted_margin"] is not None else 0.0)
            f["margin_gap"] = clamp(gap, -0.3, 0.6)

            z = {}
            for outcome, w in WEIGHTS.items():
                z[outcome] = sum(w[k] * f[k] for k in w)
            feats[pid] = {"f": f, "z": z, "burn_index": burn_index, "pc": pc,
                          "cap_gap": cap_gap, "co_dep": co_dep,
                          "peer_overrun": peer_overrun, "n_peers": ps.get("peer_count", 0),
                          "gap": gap, "m": m}

        self.intercepts = {
            outcome: self.fit_intercept([v["z"][outcome] for v in feats.values()],
                                        self.rates[outcome])
            for outcome in WEIGHTS
        }

        for pid, v in feats.items():
            p = self.projects[pid]
            m, f = v["m"], v["f"]
            p_budget = logistic(self.intercepts["budget"] + v["z"]["budget"])
            p_sched = logistic(self.intercepts["schedule"] + v["z"]["schedule"])
            p_margin = logistic(self.intercepts["margin"] + v["z"]["margin"])
            failure = (FAILURE_BLEND["margin"] * p_margin
                       + FAILURE_BLEND["budget"] * p_budget
                       + FAILURE_BLEND["schedule"] * p_sched)

            base = self.rates["failure"]
            if m and m["predicted_margin"] is not None and m["predicted_margin"] < 0:
                predicted_health = "Red"
            elif failure >= base + BAND_RED:
                predicted_health = "Red"
            elif failure >= base + BAND_YELLOW:
                predicted_health = "Yellow"
            else:
                predicted_health = "Green"

            n_peers = v["n_peers"]
            pc = v["pc"]
            if pc < 0.10 or (m and m.get("eac_capped")):
                conf = "Low"
            elif n_peers >= 25 and 0.15 <= pc <= 0.92:
                conf = "High"
            elif n_peers >= 10:
                conf = "Medium"
            else:
                conf = "Low"

            self.cur.execute(
                """INSERT INTO project_risk_score(run_id, project_id, current_health,
                        predicted_health, failure_probability_pct, p_budget_overrun_pct,
                        p_schedule_delay_pct, p_margin_below_target_pct, confidence,
                        peer_sample_size)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.run_id, pid, p["project_health"], predicted_health,
                 round(failure * 100, 1), round(p_budget * 100, 1),
                 round(p_sched * 100, 1), round(p_margin * 100, 1), conf, n_peers))
            score_id = self.cur.lastrowid

            drivers = []

            def add(code, affects, statement, weight, value, unit, direction="adverse"):
                if weight <= 0.005:
                    return
                drivers.append((score_id, code, affects, statement,
                                value, unit, round(weight, 3), direction))

            wb, ws, wm = WEIGHTS["budget"], WEIGHTS["schedule"], WEIGHTS["margin"]
            if f["burn"] > 0.01:
                add("BURN_RATE", "budget",
                    f"Actual hours are {(v['burn_index']-1)*100:,.0f}% above the planned "
                    f"burn rate at {pc*100:,.0f}% physical completion"
                    + (" (early stage, weighted down)" if pc < 0.15 else ""),
                    f["burn"] * wb["burn"], round(f["burn"] * 100, 1), "percent")
            elif f["burn"] < -0.02:
                add("BURN_RATE", "budget",
                    f"Actual hours are {abs(f['burn'])*100:,.0f}% below the planned burn rate",
                    abs(f["burn"]) * 0.5, round(f["burn"] * 100, 1), "percent", "favourable")
            if (p["critical_milestones_late"] or 0) > 0:
                add("MILESTONE_SLIP", "schedule",
                    f"{p['critical_milestones_late']} critical milestone(s) are behind schedule",
                    f["milestone"] * ws["milestone"], p["critical_milestones_late"], "count")
            if (p["uat_days_late"] or 0) > 0:
                add("UAT_LATE", "schedule",
                    f"Test phase started {p['uat_days_late']:,.0f} days late",
                    f["uat"] * ws["uat"], round(p["uat_days_late"], 0), "days")
            if f["pm_load"] > 0:
                add("PM_CAPACITY", "delivery",
                    f"Project manager is carrying {p['pm_projects']} concurrent in-flight projects",
                    f["pm_load"] * ws["pm_load"], p["pm_projects"], "count")
            if v["peer_overrun"] > 0.01:
                add("PEER_HISTORY", "multiple",
                    f"{n_peers} comparable completed projects exceeded budget by "
                    f"{v['peer_overrun']*100:,.0f}% on average",
                    f["peer"] * wb["peer"], round(v["peer_overrun"] * 100, 1), "percent")
            if (p["open_high_items"] or 0) > 0:
                add("OPEN_ISSUES", "delivery",
                    f"{p['open_high_items']} open high or critical issue(s), "
                    f"{p['open_customer_items'] or 0} raised by the customer",
                    f["issues"] * ws["issues"], p["open_high_items"], "count")
            if v["cap_gap"] > 0.02 and m:
                add("CAPACITY_SHORTFALL", "budget",
                    f"Remaining work needs {m['hours_to_complete']:,.0f} hours against "
                    f"{m['hours_available']:,.0f} hours of assigned capacity, "
                    f"{v['cap_gap']*100:,.0f}% short",
                    f["capacity"] * wb["capacity"], round(v["cap_gap"] * 100, 1), "percent")
            if f["co_dependency"] > 0.01:
                add("CHANGE_ORDER_DEPENDENCY", "margin",
                    f"{v['co_dep']*100:,.1f}% of expected revenue depends on change orders "
                    f"not yet approved", f["co_dependency"] * wm["co_dependency"],
                    round(v["co_dep"] * 100, 1), "percent")
            if f["leakage"] > 0:
                add("NONBILL_LEAKAGE", "margin",
                    f"{(p['nb_share'] or 0)*100:,.1f}% of booked hours are non-billable",
                    f["leakage"] * wm["leakage"], round((p["nb_share"] or 0) * 100, 1),
                    "percent")
            if (p["top_resource_share"] or 0) > 0.45:
                add("KEY_PERSON", "delivery",
                    f"One consultant has delivered {(p['top_resource_share'])*100:,.0f}% "
                    f"of the hours on this project",
                    clamp(p["top_resource_share"] - 0.45, 0, 0.4) * 2,
                    round(p["top_resource_share"] * 100, 1), "percent")
            if m and m["predicted_margin"] is not None and f["margin_gap"] > 0.01:
                add("MARGIN_GAP", "margin",
                    f"Predicted final margin of {m['predicted_margin']:,.1f}% is "
                    f"{(p['target_margin_pct']-m['predicted_margin']):,.1f} points below "
                    f"the {p['target_margin_pct']:,.0f}% target",
                    f["margin_gap"] * wm["margin_gap"], round(v["gap"] * 100, 1), "percent")

            self.cur.executemany(
                """INSERT INTO risk_driver(risk_score_id, driver_code, affects, statement,
                        metric_value, metric_unit, weight, direction)
                   VALUES (?,?,?,?,?,?,?,?)""", drivers)
            self.risk[pid] = {
                "score_id": score_id, "failure": failure * 100,
                "p_budget": p_budget * 100, "p_sched": p_sched * 100,
                "p_margin": p_margin * 100, "predicted_health": predicted_health,
                "confidence": conf, "peer_count": n_peers,
            }

    # =====================================================================
    def write_benchmarks(self):
        """Requirement 33."""
        for pid, p in self.projects.items():
            ps = self.peer_stats(pid)
            if not ps:
                continue
            pc = max(p["pc_frac"], 0.02)
            hpp_project = p["actual_hours"] / (pc * 100)
            hpp_peer = (ps["avg_hours"] or 0) / 100.0
            variance = pct(hpp_project - hpp_peer, hpp_peer) if hpp_peer else None
            if variance is None:
                verdict = None
            elif variance <= -10:
                verdict = "Better"
            elif variance <= 10:
                verdict = "In line"
            elif variance <= 25:
                verdict = "Worse"
            else:
                verdict = "Materially worse"
            self.cur.execute(
                """INSERT INTO project_benchmark(run_id, project_id, peer_count, match_basis,
                        peer_avg_duration_days, peer_avg_hours, peer_avg_revenue,
                        peer_avg_margin_pct, peer_avg_delay_days, peer_avg_change_orders,
                        peer_avg_change_order_value, peer_avg_team_size,
                        peer_budget_overrun_pct, tracking_variance_pct, tracking_verdict)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.run_id, pid, ps["peer_count"], ps["match_basis"],
                 ps["avg_duration_days"], ps["avg_hours"], ps["avg_revenue"],
                 ps["avg_margin_pct"], ps["avg_delay_days"], ps["avg_change_orders"],
                 ps["avg_change_order_value"], ps["avg_team_size"],
                 round((ps["budget_overrun"] or 0) * 100, 2),
                 None if variance is None else round(variance, 1), verdict))
            bid = self.cur.lastrowid
            self.cur.executemany(
                """INSERT INTO benchmark_peer(benchmark_id, peer_project_id, similarity_pct)
                   VALUES (?,?,?)""",
                [(bid, q["project_id"], q["similarity"]) for q in ps["peers"]])
            self.bench[pid] = {"benchmark_id": bid, "variance": variance,
                               "verdict": verdict, **ps}

    # =====================================================================
    def profitability_cuts(self):
        """
        Requirement 35. One query per dimension over the assessment window,
        then the flags. Revenue and cost both come from the monthly ledger so
        the cuts add up to the org total.
        """
        lo, hi = self.window[0], self.window[-1]
        # Keys are coalesced so unassigned or unclassified work lands in a
        # visible bucket. Dropping null keys silently loses revenue from the cut
        # and the dimension stops reconciling to the org total, which is exactly
        # the kind of quiet error that discredits a report.
        dims = {
            "customer": ("c.customer_id", "c.customer_name"),
            "project": ("p.project_id", "p.project_code || '  ' || p.project_name"),
            "product": ("COALESCE(p.product,'(unclassified)')", "COALESCE(p.product,'(unclassified)')"),
            "practice": ("COALESCE(pr.practice_code,'(none)')", "COALESCE(pr.practice_name,'(no practice)')"),
            "geography": ("COALESCE(c.geography,'(unknown)')", "COALESCE(c.geography,'(unknown)')"),
            "project_type": ("COALESCE(p.project_type,'(unclassified)')", "COALESCE(p.project_type,'(unclassified)')"),
            "billing_model": ("COALESCE(p.billing_model,'(unset)')", "COALESCE(p.billing_model,'(unset)')"),
            "project_manager": ("COALESCE(pm.employee_id, -1)", "COALESCE(pm.employee_name,'(unassigned)')"),
            "industry": ("COALESCE(c.industry,'(unknown)')", "COALESCE(c.industry,'(unknown)')"),
            "methodology": ("COALESCE(p.methodology,'(unset)')", "COALESCE(p.methodology,'(unset)')"),
        }
        for dim, (key, label) in dims.items():
            sql = f"""
                SELECT {key} AS k, {label} AS lbl,
                       SUM(f.recognized_revenue) revenue,
                       SUM(f.labor_cost + f.other_cost) cost,
                       COUNT(DISTINCT p.project_id) project_count
                  FROM project_financial_month f
                  JOIN project p  ON p.project_id = f.project_id
                  JOIN customer c ON c.customer_id = p.customer_id
                  LEFT JOIN practice pr ON pr.practice_id = p.practice_id
                  LEFT JOIN employee pm ON pm.employee_id = p.project_manager_id
                 WHERE p.org_id = ? AND f.period_month BETWEEN ? AND ?
                 GROUP BY {key}"""
            rows = self.rows(sql, (self.org_id, lo, hi))
            # hours in the same window, for net hourly rate
            hsql = f"""
                SELECT {key} AS k, SUM(t.hours) hours,
                       SUM(CASE WHEN t.is_billable=1 THEN t.hours ELSE 0 END) billable
                  FROM time_entry t
                  JOIN project p  ON p.project_id = t.project_id
                  JOIN customer c ON c.customer_id = p.customer_id
                  LEFT JOIN practice pr ON pr.practice_id = p.practice_id
                  LEFT JOIN employee pm ON pm.employee_id = p.project_manager_id
                 WHERE p.org_id = ? AND t.approval_status='Approved'
                   AND strftime('%Y-%m', t.entry_date) BETWEEN ? AND ?
                 GROUP BY {key}"""
            hours = {r["k"]: r for r in self.rows(hsql, (self.org_id, lo, hi))}
            over = {}
            if dim in ("customer", "project", "practice", "project_manager", "methodology"):
                osql = f"""
                    SELECT {key} AS k, SUM(CASE WHEN v.hours_consumed_pct > 100 THEN 1 ELSE 0 END) n
                      FROM v_project_360 v
                      JOIN project p  ON p.project_id = v.project_id
                      JOIN customer c ON c.customer_id = p.customer_id
                      LEFT JOIN practice pr ON pr.practice_id = p.practice_id
                      LEFT JOIN employee pm ON pm.employee_id = p.project_manager_id
                     WHERE p.org_id=? AND p.project_status='Complete'
                     GROUP BY {key}"""
                over = {r["k"]: r["n"] for r in self.rows(osql, (self.org_id,))}

            recs = []
            for r in rows:
                if r["k"] is None:
                    continue
                rev = r["revenue"] or 0.0
                cost = r["cost"] or 0.0
                h = hours.get(r["k"], {})
                recs.append({
                    "dimension_key": str(r["k"]), "dimension_label": r["lbl"] or str(r["k"]),
                    "revenue": rev, "cost": cost, "gross_profit": rev - cost,
                    "margin_pct": pct(rev - cost, rev),
                    "hours": h.get("hours") or 0.0, "billable_hours": h.get("billable") or 0.0,
                    "net_hourly_rate": (rev / h["hours"]) if h.get("hours") else None,
                    "revenue_cost_ratio": (rev / cost) if cost else None,
                    "project_count": r["project_count"] or 0,
                    "over_budget_count": over.get(r["k"], 0),
                })
            self.flag_profitability(dim, recs)
            self.cur.executemany(
                """INSERT INTO profitability_cut(org_id, dimension, dimension_key,
                        dimension_label, revenue, cost, gross_profit, margin_pct, hours,
                        billable_hours, net_hourly_rate, revenue_cost_ratio, project_count,
                        over_budget_count, flag)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(self.org_id, dim, r["dimension_key"], r["dimension_label"], r["revenue"],
                  r["cost"], r["gross_profit"], r["margin_pct"], r["hours"],
                  r["billable_hours"], r["net_hourly_rate"], r["revenue_cost_ratio"],
                  r["project_count"], r["over_budget_count"], r.get("flag"))
                 for r in recs])

    def flag_profitability(self, dim, recs):
        """
        Top 10, bottom 10, margin destroyers, thin-margin revenue, and chronic
        over-budget delivery.

        "Chronic over budget" is judged against this organisation's own base
        rate, not an absolute share. Where 60% of all projects historically
        exceeded their hours budget, a 60% rate for one customer is normal and
        flagging it teaches people to ignore the flag.
        """
        material = [r for r in recs if r["revenue"] > 0]
        if not material:
            return
        base_over = (self.rates or {}).get("budget", 0.5)
        by_profit = sorted(material, key=lambda r: -r["gross_profit"])
        flags = defaultdict(list)
        top_ids = {id(r) for r in by_profit[:10]}
        for r in by_profit[:10]:
            flags[id(r)].append("top10")
        for r in by_profit[-10:]:
            if id(r) not in top_ids:
                flags[id(r)].append("bottom10")
        rev_total = sum(r["revenue"] for r in material)
        for r in material:
            if r["gross_profit"] < 0:
                flags[id(r)].append("margin_destroyer")
            if (r["margin_pct"] is not None and r["margin_pct"] < 12
                    and r["revenue"] > rev_total * 0.03):
                flags[id(r)].append("high_rev_low_margin")
            if r["project_count"] >= 5:
                rate = r["over_budget_count"] / r["project_count"]
                if rate >= min(0.95, base_over + 0.20):
                    flags[id(r)].append("chronic_over_budget")
        for r in recs:
            f = flags.get(id(r))
            r["flag"] = ",".join(sorted(set(f))) if f else None

    # =====================================================================
    def capacity_model(self):
        """
        Requirements 37 and 41. Demand is signed work plus probability-weighted
        pipeline; supply is billable capacity at the practice utilisation
        target. Remaining hours on live projects are the engine's own EAC, not
        the plan, because the plan is what is already wrong.
        """
        practices = self.rows("SELECT * FROM practice WHERE org_id=?", (self.org_id,))
        headcount = defaultdict(float)
        for r in self.rows(
                """SELECT practice_id, SUM(weekly_capacity_hours) cap, COUNT(*) n
                     FROM employee WHERE org_id=? AND is_active=1 AND is_billable=1
                     GROUP BY practice_id""", (self.org_id,)):
            headcount[r["practice_id"]] = (r["cap"] or 0) * 52.0 / 12.0

        # remaining demand from live projects, spread over the remaining months
        demand = defaultdict(lambda: defaultdict(float))
        for pid, p in self.projects.items():
            if p["project_status"] not in ("In Flight", "Not Started"):
                continue
            m = self.margin.get(pid)
            remaining = (m["hours_to_complete"] if m else
                         max(0.0, (p["total_budget_hours"] or 0) - p["actual_hours"]))
            if remaining <= 0:
                continue
            try:
                end = datetime.fromisoformat(p["planned_end_date"]).date()
                start = max(self.as_of, datetime.fromisoformat(p["start_date"]).date())
            except (TypeError, ValueError):
                continue
            n_months = max(1, min(HORIZON_MONTHS,
                                  (end.year - start.year) * 12 + end.month - start.month + 1))
            for i in range(n_months):
                demand[p["practice_id"]][month_key(add_months(start, i))] += remaining / n_months

        pipeline = defaultdict(lambda: defaultdict(float))
        for o in self.rows(
                """SELECT * FROM pipeline_opportunity
                    WHERE org_id=? AND stage NOT IN ('Closed Won','Closed Lost')""",
                (self.org_id,)):
            try:
                start = datetime.fromisoformat(o["expected_start"]).date()
            except (TypeError, ValueError):
                continue
            n = max(1, int(round(o["expected_duration_months"] or 3)))
            weighted = (o["estimated_hours"] or 0) * (o["probability_pct"] or 0) / 100.0
            for i in range(n):
                mk = month_key(add_months(start, i))
                pipeline[o["practice_id"]][mk] += weighted / n

        months = [month_key(add_months(self.as_of, i + 1)) for i in range(HORIZON_MONTHS)]
        self.capacity = []
        for pr in practices:
            pid = pr["practice_id"]
            for mk in months:
                avail = headcount.get(pid, 0.0)
                committed = demand[pid].get(mk, 0.0)
                pipe = pipeline[pid].get(mk, 0.0)
                total_demand = committed + pipe
                billable_capacity = avail * pr["target_utilization_pct"] / 100.0
                gap = billable_capacity - total_demand
                row = {
                    "period_month": mk, "practice_id": pid,
                    "practice_code": pr["practice_code"], "practice_name": pr["practice_name"],
                    "available_hours": avail, "committed_hours": committed,
                    "pipeline_hours": pipe, "demand_hours": total_demand,
                    "gap_hours": gap,
                    "implied_headcount_gap": (-gap / (162.5 * pr["target_utilization_pct"] / 100.0)
                                              if gap < 0 else 0.0),
                    "projected_utilization_pct": pct(total_demand, avail),
                }
                self.capacity.append(row)
                self.cur.execute(
                    """INSERT INTO capacity_month(org_id, run_id, period_month, practice_id,
                            available_hours, committed_hours, pipeline_hours, demand_hours,
                            gap_hours, implied_headcount_gap, projected_utilization_pct)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (self.org_id, self.run_id, mk, pid, avail, committed, pipe,
                     total_demand, gap, row["implied_headcount_gap"],
                     row["projected_utilization_pct"]))

    # =====================================================================
    def forecast_accuracy(self):
        """
        Requirement 38. Original vs revised vs actual, for completed projects
        that have all three. Negative variance means hours were underestimated.
        """
        rows = self.rows(
            """SELECT p.project_id, p.project_code, p.project_manager_id,
                      COALESCE(pm.employee_name,'(unassigned)') pm_name,
                      pr.practice_code, pr.practice_name, c.customer_id, c.customer_name,
                      o.forecast_hours orig_hours, o.forecast_margin_pct orig_margin,
                      r.forecast_hours rev_hours,
                      fin.forecast_hours act_hours, fin.forecast_margin_pct act_margin
                 FROM project p
                 JOIN project_forecast_snapshot o
                      ON o.project_id=p.project_id AND o.snapshot_type='Original'
                 JOIN project_forecast_snapshot fin
                      ON fin.project_id=p.project_id AND fin.snapshot_type='Final'
                 LEFT JOIN project_forecast_snapshot r
                      ON r.project_id=p.project_id AND r.snapshot_type='Revised'
                 LEFT JOIN employee pm ON pm.employee_id=p.project_manager_id
                 LEFT JOIN practice pr ON pr.practice_id=p.practice_id
                 JOIN customer c ON c.customer_id=p.customer_id
                WHERE p.org_id=? AND p.project_status='Complete'""", (self.org_id,))

        scopes = {
            "project_manager": lambda r: (str(r["project_manager_id"]), r["pm_name"]),
            "practice": lambda r: (r["practice_code"], r["practice_name"]),
            "customer": lambda r: (str(r["customer_id"]), r["customer_name"]),
            "org": lambda r: ("ALL", self.org["org_name"]),
        }
        self.forecast = {}
        for scope, keyfn in scopes.items():
            buckets = defaultdict(list)
            for r in rows:
                k, lbl = keyfn(r)
                if k is None:
                    continue
                buckets[(k, lbl)].append(r)
            for (k, lbl), rs in buckets.items():
                oh = sum(r["orig_hours"] or 0 for r in rs)
                rh = sum(r["rev_hours"] or r["orig_hours"] or 0 for r in rs)
                ah = sum(r["act_hours"] or 0 for r in rs)
                if ah <= 0:
                    continue
                var = pct(oh - ah, ah)
                om = [r["orig_margin"] for r in rs if r["orig_margin"] is not None]
                am = [r["act_margin"] for r in rs if r["act_margin"] is not None]
                om_avg = sum(om) / len(om) if om else None
                am_avg = sum(am) / len(am) if am else None
                accuracy = 100 - abs(var) if var is not None else None
                if var is None:
                    bias = None
                elif var < -5:
                    bias = "Underestimates"
                elif var > 5:
                    bias = "Overestimates"
                else:
                    bias = "Accurate"
                noun = {"project_manager": "Projects managed by this PM",
                        "practice": "Projects in this practice",
                        "customer": "Projects for this customer",
                        "org": "Projects across the organisation"}[scope]
                statement = None
                if bias == "Underestimates":
                    statement = (f"{noun} historically underestimate hours by "
                                 f"{abs(var):,.1f}% ({len(rs)} completed projects)")
                elif bias == "Overestimates":
                    statement = (f"{noun} historically overestimate hours by "
                                 f"{var:,.1f}% ({len(rs)} completed projects)")
                else:
                    statement = (f"{noun} forecast hours within "
                                 f"{abs(var):,.1f}% of actual ({len(rs)} projects)")
                rec = {
                    "scope": scope, "scope_key": k, "scope_label": lbl,
                    "sample_size": len(rs), "original_forecast_hours": oh,
                    "revised_forecast_hours": rh, "actual_hours": ah,
                    "hours_variance_pct": var,
                    "original_forecast_margin_pct": om_avg, "actual_margin_pct": am_avg,
                    "margin_variance_pts": (am_avg - om_avg
                                            if (om_avg is not None and am_avg is not None)
                                            else None),
                    "accuracy_pct": accuracy, "bias": bias, "statement": statement,
                }
                self.forecast[(scope, k)] = rec
                self.cur.execute(
                    """INSERT INTO forecast_accuracy(org_id, run_id, scope, scope_key,
                            scope_label, sample_size, original_forecast_hours,
                            revised_forecast_hours, actual_hours, hours_variance_pct,
                            original_forecast_margin_pct, actual_margin_pct,
                            margin_variance_pts, accuracy_pct, bias, statement)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (self.org_id, self.run_id, scope, k, lbl, len(rs), oh, rh, ah, var,
                     om_avg, am_avg, rec["margin_variance_pts"], accuracy, bias, statement))

    # =====================================================================
    def future_risks(self):
        """Requirement 37, stated as sentences an executive can act on."""
        preds = []
        by_month = defaultdict(lambda: {"gap": 0.0, "demand": 0.0, "avail": 0.0})
        for r in self.capacity:
            b = by_month[r["period_month"]]
            b["gap"] += r["gap_hours"]
            b["demand"] += r["demand_hours"]
            b["avail"] += r["available_hours"]

        for mk in sorted(by_month):
            b = by_month[mk]
            label = datetime.strptime(mk, "%Y-%m").strftime("%B %Y")
            if b["gap"] > 400:
                preds.append(("CAPACITY_SURPLUS", mk,
                              f"On current signed work and weighted pipeline, the "
                              f"organisation will have approximately {b['gap']:,.0f} hours "
                              f"of unsold capacity in {label}, net across practices",
                              b["gap"], "hours", "Medium",
                              "Signed project EAC plus probability-weighted pipeline "
                              "against billable capacity at practice utilisation targets"))
            elif b["gap"] < -400:
                fte = -b["gap"] / (162.5 * 0.72)
                preds.append(("CAPACITY_SHORTFALL", mk,
                              f"Demand exceeds billable capacity by "
                              f"{abs(b['gap']):,.0f} hours in {label}, about "
                              f"{fte:,.1f} FTE, net across practices",
                              abs(b["gap"]), "hours", "Medium",
                              "Signed project EAC plus weighted pipeline against capacity"))
        # hiring requirement over the horizon, by practice
        by_practice = defaultdict(float)
        for r in self.capacity:
            if r["gap_hours"] < 0:
                by_practice[r["practice_name"]] += -r["gap_hours"]
        for name, short in sorted(by_practice.items(), key=lambda x: -x[1])[:4]:
            fte = short / (162.5 * 0.72) / HORIZON_MONTHS
            if fte >= 0.5:
                preds.append(("HIRING_REQUIREMENT", None,
                              f"On current pipeline, {name} needs about {fte:,.1f} "
                              f"additional FTE to cover demand over the next "
                              f"{HORIZON_MONTHS} months",
                              fte, "count", "Medium",
                              "Aggregate monthly shortfall divided by billable capacity "
                              "per FTE at target utilisation"))
        # utilisation outlook next month
        nxt = sorted(by_month)[0] if by_month else None
        if nxt:
            b = by_month[nxt]
            proj = pct(b["demand"], b["avail"])
            target = self.scalar(
                "SELECT AVG(target_utilization_pct) FROM practice WHERE org_id=?",
                (self.org_id,)) or 70.0
            if proj is not None and proj < target - 3:
                preds.append(("UTILIZATION_MISS", nxt,
                              f"Projected utilisation for "
                              f"{datetime.strptime(nxt,'%Y-%m').strftime('%B %Y')} is "
                              f"{proj:,.1f}% against a {target:,.0f}% target",
                              proj, "percent", "Medium",
                              "Demand hours over available capacity hours"))
        # margin shift from the live mix
        live = [self.margin[p] for p in self.margin
                if self.projects[p]["project_status"] == "In Flight"]
        if live:
            rev = sum(m["revenue"] for m in live)
            pred = sum(m["revenue"] * (m["predicted_margin"] or 0) for m in live) / rev
            fcst = sum(m["revenue"] * (m["forecast_margin"] or 0) for m in live) / rev
            if abs(pred - fcst) > 0.5:
                preds.append(("MARGIN_SHIFT", None,
                              f"The current live project mix is expected to deliver "
                              f"{pred:,.1f}% margin against {fcst:,.1f}% forecast, a "
                              f"{pred-fcst:+,.1f} point shift",
                              pred - fcst, "percent", "Medium",
                              "Revenue-weighted predicted margin across in-flight projects"))
        # customers likely to need more capacity, from repeat history
        for r in self.rows(
                """SELECT c.customer_name, COUNT(*) n,
                          MAX(p.actual_end_date) last_end,
                          AVG(p.contract_value) avg_value
                     FROM project p JOIN customer c ON c.customer_id=p.customer_id
                    WHERE p.org_id=? AND p.project_status='Complete'
                      AND c.customer_status='Active'
                    GROUP BY c.customer_id HAVING COUNT(*) >= 4
                    ORDER BY COUNT(*) DESC LIMIT 3""", (self.org_id,)):
            preds.append(("CUSTOMER_DEMAND", None,
                          f"{r['customer_name']} has commissioned {r['n']} projects "
                          f"averaging ${r['avg_value']:,.0f}; on that pattern further "
                          f"services demand is likely",
                          r["avg_value"], "currency", "Low",
                          f"{r['n']} completed projects, most recent ending {r['last_end']}"))

        self.cur.executemany(
            """INSERT INTO future_risk_prediction(org_id, run_id, horizon_month,
                    prediction_type, statement, metric_value, metric_unit, confidence, basis)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(self.org_id, self.run_id, mk, t, st, v, u, c, b)
             for t, mk, st, v, u, c, b in preds])
        self.future = preds

    # =====================================================================
    def data_quality(self):
        """Requirement 44, against loaded data rather than a single upload."""
        self.cur.execute("DELETE FROM data_quality_finding WHERE org_id=?", (self.org_id,))
        rows = []
        for rule in dq_rules.RULES:
            for r in self.con.execute(rule["sql"], (self.org_id,)).fetchall():
                rows.append((self.org_id, rule["category"], rule["rule_code"],
                             rule["entity_table"], r[2] if len(r) > 2 else None,
                             str(r[0]), rule["severity"],
                             rule["message"].format(label=r[0]), rule["recommendation"]))
        self.cur.executemany(
            """INSERT INTO data_quality_finding(org_id, category, rule_code, entity_table,
                    entity_pk, entity_label, severity, message, recommendation)
               VALUES (?,?,?,?,?,?,?,?,?)""", rows)
        self.dq_count = len(rows)

    # =====================================================================
    def window_totals(self):
        lo, hi = self.window[0], self.window[-1]
        fin = self.one(
            """SELECT SUM(f.recognized_revenue) revenue, SUM(f.invoiced_revenue) invoiced,
                      SUM(f.labor_cost) labor, SUM(f.other_cost) other
                 FROM project_financial_month f JOIN project p USING(project_id)
                WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?""",
            (self.org_id, lo, hi))
        hrs = self.one(
            """SELECT SUM(hours) total, SUM(CASE WHEN is_billable=1 THEN hours ELSE 0 END) billable
                 FROM time_entry WHERE org_id=? AND approval_status='Approved'
                  AND strftime('%Y-%m', entry_date) BETWEEN ? AND ?""",
            (self.org_id, lo, hi))
        cap = self.scalar(
            """SELECT SUM(weekly_capacity_hours)*52.0/12.0 FROM employee
                WHERE org_id=? AND is_active=1 AND is_billable=1""", (self.org_id,)) or 0.0
        heads = self.scalar(
            """SELECT COUNT(*) FROM employee WHERE org_id=? AND is_active=1
                AND is_billable=1""", (self.org_id,)) or 0
        rev = fin["revenue"] or 0.0
        cost = (fin["labor"] or 0.0) + (fin["other"] or 0.0)
        return {
            "months": len(self.window), "period_from": lo, "period_to": hi,
            "revenue": rev, "invoiced": fin["invoiced"] or 0.0,
            "labor_cost": fin["labor"] or 0.0, "other_cost": fin["other"] or 0.0,
            "total_cost": cost, "gross_profit": rev - cost,
            "gross_margin_pct": pct(rev - cost, rev),
            "hours": hrs["total"] or 0.0, "billable_hours": hrs["billable"] or 0.0,
            "capacity_hours_month": cap, "billable_headcount": heads,
            "utilization_pct": pct(hrs["billable"] or 0.0, cap * len(self.window)),
            "revenue_per_consultant_year": (rev / len(self.window) * 12 / heads) if heads else None,
            "cost_per_consultant_year": (cost / len(self.window) * 12 / heads) if heads else None,
            "realized_rate": ((rev / hrs["billable"]) if hrs["billable"] else None),
        }

    def acquisition_assessment(self):
        """Requirement 34. Benchmarked against the operating company."""
        t = self.window_totals()
        self.totals = t
        self.cur.execute(
            """INSERT INTO acquisition_assessment(org_id, run_id, period_from, period_to,
                    months_of_history) VALUES (?,?,?,?,?)""",
            (self.org_id, self.run_id, t["period_from"], t["period_to"], t["months"]))
        aid = self.cur.lastrowid
        self.assessment_id = aid

        # concentration
        cust = self.rows(
            """SELECT c.customer_name, SUM(f.recognized_revenue) rev
                 FROM project_financial_month f
                 JOIN project p ON p.project_id=f.project_id
                 JOIN customer c ON c.customer_id=p.customer_id
                WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?
                GROUP BY c.customer_id ORDER BY rev DESC""",
            (self.org_id, t["period_from"], t["period_to"]))
        total_rev = sum(r["rev"] or 0 for r in cust) or 1.0
        top1 = pct(cust[0]["rev"], total_rev) if cust else None
        top3 = pct(sum(r["rev"] or 0 for r in cust[:3]), total_rev)
        top5 = pct(sum(r["rev"] or 0 for r in cust[:5]), total_rev)
        hhi = sum(((r["rev"] or 0) / total_rev * 100) ** 2 for r in cust)
        self.concentration = {"customers": cust, "top1": top1, "top3": top3,
                              "top5": top5, "hhi": hhi, "total": total_rev}

        prj = self.rows(
            """SELECT p.project_code, p.project_name, SUM(f.recognized_revenue) rev
                 FROM project_financial_month f JOIN project p USING(project_id)
                WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?
                GROUP BY p.project_id ORDER BY rev DESC LIMIT 5""",
            (self.org_id, t["period_from"], t["period_to"]))
        top5_projects = pct(sum(r["rev"] or 0 for r in prj), total_rev)

        # utilisation and allocation
        util = self.rows(
            """SELECT e.employee_id, e.employee_name, e.role, e.utilization_target_pct,
                      SUM(CASE WHEN t.is_billable=1 THEN t.hours ELSE 0 END) billable,
                      e.weekly_capacity_hours*52.0/12.0 * ? AS capacity
                 FROM employee e
                 LEFT JOIN time_entry t ON t.employee_id=e.employee_id
                      AND t.approval_status='Approved'
                      AND strftime('%Y-%m', t.entry_date) BETWEEN ? AND ?
                WHERE e.org_id=? AND e.is_active=1 AND e.is_billable=1
                GROUP BY e.employee_id""",
            (len(self.window), t["period_from"], t["period_to"], self.org_id))
        for u in util:
            u["utilization_pct"] = pct(u["billable"] or 0, u["capacity"] or 0)
        under = [u for u in util if (u["utilization_pct"] or 0) < (u["utilization_target_pct"] or 70)]
        over = [u for u in util if (u["utilization_pct"] or 0) > 100]
        self.utilization_detail = util

        # delivery position
        aging = [p for p in self.projects.values()
                 if p["project_status"] == "In Flight" and p["planned_end_date"]
                 and p["planned_end_date"] < self.as_of.isoformat()]
        delayed = [p for p in self.projects.values()
                   if (p["end_slip_days"] or 0) > 10]
        unprofitable_projects = [p for p in self.projects.values()
                                 if (p["current_margin_pct"] is not None
                                     and p["current_margin_pct"] < 0
                                     and (p["total_revenue"] or 0) > 0)]
        cust_prof = self.rows(
            """SELECT dimension_label, revenue, gross_profit, margin_pct
                 FROM profitability_cut WHERE org_id=? AND dimension='customer'""",
            (self.org_id,))
        unprofitable_customers = [c for c in cust_prof if (c["gross_profit"] or 0) < 0]

        # Revenue at risk is deliberately narrow: work the engine expects to
        # land in the red, or to be predicted red overall. A wider definition
        # that swept in everything below an ambitious target would put a
        # quarter of the book on the number and make it useless.
        revenue_at_risk = sum(
            m["revenue"] for pid, m in self.margin.items()
            if self.projects[pid]["project_status"] == "In Flight"
            and (m["risk"] == "Critical"
                 or self.risk.get(pid, {}).get("predicted_health") == "Red"))
        revenue_below_target = sum(
            m["revenue"] for pid, m in self.margin.items()
            if self.projects[pid]["project_status"] == "In Flight"
            and m["predicted_margin"] is not None
            and m["predicted_margin"] < m["target"])

        co_dependency = pct(
            self.scalar("""SELECT SUM(co.co_value) FROM change_order co
                             JOIN project p USING(project_id)
                            WHERE p.org_id=? AND co.status='Approved'""", (self.org_id,)) or 0,
            self.scalar("""SELECT SUM(contract_value) FROM project WHERE org_id=?
                            AND contract_value > 0""", (self.org_id,)) or 1)

        unbilled = self.scalar(
            """SELECT SUM(CASE WHEN t.is_billable=1 AND t.invoiced=0
                               THEN t.hours*COALESCE(t.bill_rate,0) ELSE 0 END)
                 FROM time_entry t WHERE t.org_id=? AND t.project_id IS NOT NULL
                  AND t.approval_status='Approved'""", (self.org_id,)) or 0.0
        leakage = self.scalar(
            """SELECT SUM(t.hours*COALESCE(t.bill_rate,0))
                 FROM time_entry t JOIN project p ON p.project_id=t.project_id
                WHERE t.org_id=? AND t.is_billable=0 AND t.approval_status='Approved'
                  AND p.billing_model IN ('Fixed Fee','Milestone')""", (self.org_id,)) or 0.0
        missing_time = self.scalar(
            """SELECT COUNT(*) FROM data_quality_finding
                WHERE org_id=? AND rule_code='MISSING_TIME'""", (self.org_id,)) or 0
        unapproved = self.scalar(
            """SELECT COUNT(*) FROM time_entry WHERE org_id=? AND approval_status<>'Approved'""",
            (self.org_id,)) or 0
        fa = self.forecast.get(("org", "ALL"), {})

        ebitda_proxy = t["gross_profit"] - t["revenue"] * OVERHEAD_RATE

        bottlenecks = sorted(
            {r["practice_name"] for r in self.capacity if r["gap_hours"] < -200})

        metrics = [
            ("Financial", "REVENUE", f"Recognised revenue ({t['months']}m)",
             t["revenue"], "currency", "higher"),
            ("Financial", "GROSS_MARGIN", "Gross margin", t["gross_margin_pct"],
             "percent", "higher"),
            ("Financial", "EBITDA_PROXY",
             f"EBITDA proxy (gross profit less {OVERHEAD_RATE*100:,.0f}% overhead allowance)",
             ebitda_proxy, "currency", "higher"),
            ("Financial", "EBITDA_PROXY_PCT", "EBITDA proxy margin",
             pct(ebitda_proxy, t["revenue"]), "percent", "higher"),
            ("Utilization", "BILLABLE_UTIL", "Billable utilisation",
             t["utilization_pct"], "percent", "higher"),
            ("Utilization", "REV_PER_CONSULTANT", "Revenue per consultant (annualised)",
             t["revenue_per_consultant_year"], "currency", "higher"),
            ("Utilization", "COST_PER_CONSULTANT", "Cost per consultant (annualised)",
             t["cost_per_consultant_year"], "currency", "lower"),
            ("Utilization", "REALIZED_RATE", "Average realised billing rate",
             t["realized_rate"], "currency", "higher"),
            ("Utilization", "UNDERUTILIZED", "Consultants below utilisation target",
             len(under), "count", "lower"),
            ("Utilization", "UNDERUTILIZED_PCT", "Share of consultants below target",
             pct(len(under), len(util)), "percent", "lower"),
            ("Utilization", "OVERALLOCATED", "Consultants above 100% utilisation",
             len(over), "count", "lower"),
            ("Delivery", "AVG_PROJECT_MARGIN", "Average completed project margin",
             self.scalar("""SELECT AVG(current_margin_pct) FROM v_project_360
                             WHERE org_id=? AND project_status='Complete'""",
                         (self.org_id,)), "percent", "higher"),
            ("Delivery", "AGING_PROJECTS", "In-flight projects past planned end date",
             len(aging), "count", "lower"),
            ("Delivery", "DELAYED_PROJECTS", "Projects delivered more than 10 days late",
             len(delayed), "count", "lower"),
            ("Delivery", "BOTTLENECKS", "Practices with a capacity shortfall in the horizon",
             len(bottlenecks), "count", "lower"),
            ("Concentration", "TOP1_CUSTOMER", "Largest customer share of revenue",
             top1, "percent", "lower"),
            ("Concentration", "TOP3_CUSTOMERS", "Top 3 customer share of revenue",
             top3, "percent", "lower"),
            ("Concentration", "TOP5_CUSTOMERS", "Top 5 customer share of revenue",
             top5, "percent", "lower"),
            ("Concentration", "HHI", "Customer revenue concentration (HHI)",
             hhi, "ratio", "lower"),
            ("Concentration", "TOP5_PROJECTS", "Top 5 project share of revenue",
             top5_projects, "percent", "lower"),
            ("Leakage", "REVENUE_AT_RISK",
             "Revenue at risk (predicted red or negative margin)",
             revenue_at_risk, "currency", "lower"),
            ("Leakage", "REVENUE_BELOW_TARGET",
             "Revenue on live projects predicted below target margin",
             revenue_below_target, "currency", "lower"),
            ("Leakage", "UNPROFITABLE_CUSTOMERS", "Customers with negative gross profit",
             len(unprofitable_customers), "count", "lower"),
            ("Leakage", "UNPROFITABLE_PROJECTS", "Projects with negative margin",
             len(unprofitable_projects), "count", "lower"),
            ("Leakage", "CO_DEPENDENCY", "Approved change orders as a share of contract value",
             co_dependency, "percent", "lower"),
            ("Leakage", "REVENUE_LEAKAGE",
             "Non-billable hours on fixed-price work, at list rate",
             leakage, "currency", "lower"),
            ("Leakage", "UNBILLED_TIME", "Approved billable time not yet invoiced",
             unbilled, "currency", "lower"),
            ("Leakage", "MISSING_TIME", "Assignments with no time booked",
             missing_time, "count", "lower"),
            ("Leakage", "UNAPPROVED_TIME", "Time entries not approved",
             unapproved, "count", "lower"),
            ("Forecast", "FORECAST_ACCURACY", "Forecast accuracy (hours, completed projects)",
             fa.get("accuracy_pct"), "percent", "higher"),
            ("Forecast", "FORECAST_VARIANCE", "Original forecast variance vs actual hours",
             fa.get("hours_variance_pct"), "percent", "neutral"),
        ]
        self.cur.executemany(
            """INSERT INTO acquisition_metric(assessment_id, metric_group, metric_code,
                    metric_label, metric_value, metric_unit, direction_good)
               VALUES (?,?,?,?,?,?,?)""",
            [(aid, g, c, l, v, u, d) for g, c, l, v, u, d in metrics])
        self.metrics = {c: dict(group=g, label=l, value=v, unit=u, direction=d)
                        for g, c, l, v, u, d in metrics}
        self.assessment_context = {
            "aging": aging, "delayed": delayed, "under": under, "over": over,
            "unprofitable_projects": unprofitable_projects,
            "unprofitable_customers": unprofitable_customers,
            "revenue_at_risk": revenue_at_risk,
            "revenue_below_target": revenue_below_target, "bottlenecks": bottlenecks,
            "leakage": leakage, "unbilled": unbilled, "ebitda_proxy": ebitda_proxy,
        }

    def benchmark_against(self, other):
        """Fill benchmark_value on this org's metrics from another org's run."""
        for code, m in self.metrics.items():
            o = other.metrics.get(code)
            if not o or o["value"] is None or m["value"] is None:
                continue
            var = m["value"] - o["value"]
            self.cur.execute(
                """UPDATE acquisition_metric SET benchmark_value=?, variance_vs_benchmark=?
                    WHERE assessment_id=? AND metric_code=?""",
                (o["value"], var, self.assessment_id, code))
        self.con.commit()

    # =====================================================================
    def red_flags(self):
        """Requirement 36. Threshold breaches, each with the rows behind it."""
        flags = []
        c = self.concentration
        ctx = self.assessment_context
        M = self.metrics

        def val(code):
            return M.get(code, {}).get("value")

        if c["top3"] and c["top3"] > 30:
            flags.append(("Customer Concentration",
                          "Critical" if c["top3"] > 50 else "High",
                          f"{c['top3']:,.1f}% of professional services revenue comes from "
                          f"the top 3 customers",
                          "Loss of any one of these accounts would leave delivery capacity "
                          "stranded. Check contract terms, notice periods and renewal dates.",
                          c["top3"], "percent", 30.0,
                          json.dumps([{"customer": r["customer_name"],
                                       "revenue": round(r["rev"] or 0, 2),
                                       "share_pct": round(pct(r["rev"], c["total"]) or 0, 1)}
                                      for r in c["customers"][:3]]),
                          "Model the margin and utilisation impact of losing each top-3 "
                          "account before completing the transaction"))
        if c["top1"] and c["top1"] > 15:
            flags.append(("Customer Risk", "High" if c["top1"] > 25 else "Medium",
                          f"{c['customers'][0]['customer_name']} represents "
                          f"{c['top1']:,.1f}% of total PS revenue",
                          "Single-account dependency at this level is a valuation issue, "
                          "not just a delivery one.",
                          c["top1"], "percent", 15.0,
                          json.dumps([{"customer": c["customers"][0]["customer_name"],
                                       "revenue": round(c["customers"][0]["rev"] or 0, 2)}]),
                          "Confirm contracted backlog and renewal position for this account"))

        thin = [(pid, m) for pid, m in self.margin.items()
                if self.projects[pid]["project_status"] == "In Flight"
                and m["predicted_margin"] is not None and m["predicted_margin"] < 10]
        live = [pid for pid, p in self.projects.items() if p["project_status"] == "In Flight"]
        if thin and live:
            share = pct(len(thin), len(live))
            flags.append(("Margin Risk", "High" if share > 15 else "Medium",
                          f"{share:,.1f}% of live projects have a predicted final margin "
                          f"below 10%",
                          f"{len(thin)} of {len(live)} in-flight projects, carrying "
                          f"${sum(m['revenue'] for _, m in thin):,.0f} of revenue.",
                          share, "percent", 15.0,
                          json.dumps([{"project": self.projects[pid]["project_code"],
                                       "name": self.projects[pid]["project_name"],
                                       "predicted_margin_pct": round(m["predicted_margin"], 1),
                                       "revenue": round(m["revenue"], 2)}
                                      for pid, m in sorted(thin,
                                                           key=lambda x: x[1]["predicted_margin"])[:10]]),
                          "Review scope, rate and change-order position on each before "
                          "the next billing cycle"))

        under_pct = val("UNDERUTILIZED_PCT")
        if under_pct and under_pct > 15:
            flags.append(("Resource Risk", "High" if under_pct > 30 else "Medium",
                          f"{under_pct:,.1f}% of consultants are below their utilisation "
                          f"target",
                          f"{len(ctx['under'])} of {len(self.utilization_detail)} billable "
                          f"consultants over the {self.totals['months']}-month window.",
                          under_pct, "percent", 15.0,
                          json.dumps([{"name": u["employee_name"], "role": u["role"],
                                       "utilization_pct": round(u["utilization_pct"] or 0, 1),
                                       "target_pct": u["utilization_target_pct"]}
                                      for u in sorted(ctx["under"],
                                                      key=lambda u: u["utilization_pct"] or 0)[:12]]),
                          "Decide per person: redeploy, reskill, or take the cost out"))

        # methodology delivery risk, stated as a relative likelihood
        base = self.rows(
            """SELECT methodology, COUNT(*) n,
                      SUM(CASE WHEN hours_consumed_pct > 100 THEN 1 ELSE 0 END) over_budget
                 FROM v_project_360 WHERE org_id=? AND project_status='Complete'
                   AND total_budget_hours > 0
                 GROUP BY methodology HAVING COUNT(*) >= 8""", (self.org_id,))
        if len(base) >= 2:
            rates = [(r["methodology"], r["over_budget"] / r["n"], r["n"]) for r in base]
            rates.sort(key=lambda x: -x[1])
            worst, worst_rate, worst_n = rates[0]
            others_n = sum(n for _, _, n in rates[1:])
            others_over = sum(rate * n for _, rate, n in rates[1:])
            others_rate = others_over / others_n if others_n else 0
            if others_rate > 0 and worst_rate / others_rate >= 1.2:
                flags.append(("Delivery Risk",
                              "High" if worst_rate / others_rate >= 1.5 else "Medium",
                              f"Projects using the {worst} methodology are "
                              f"{worst_rate/others_rate:,.1f}x more likely to exceed budget",
                              f"{worst_rate*100:,.0f}% of {worst_n} completed {worst} "
                              f"projects exceeded their hours budget, against "
                              f"{others_rate*100:,.0f}% across all other methodologies.",
                              worst_rate / others_rate, "ratio", 1.2,
                              json.dumps([{"methodology": m, "over_budget_rate_pct": round(r*100, 1),
                                           "completed_projects": n} for m, r, n in rates]),
                              f"Stop selling {worst} engagements at standard margin, or "
                              f"price the observed overrun into the estimate"))

        behind = [(pid, self.margin[pid]["revenue"]) for pid in live
                  if (self.projects[pid]["critical_milestones_late"] or 0) > 0
                  and pid in self.margin]
        if behind:
            at_risk = sum(v for _, v in behind)
            flags.append(("Revenue Risk", "High" if at_risk > self.totals["revenue"] * 0.05
                          else "Medium",
                          f"${at_risk:,.0f} of contracted revenue sits on projects that are "
                          f"currently behind schedule",
                          f"{len(behind)} live projects have at least one critical milestone "
                          f"past its planned date.",
                          at_risk, "currency", None,
                          json.dumps([{"project": self.projects[pid]["project_code"],
                                       "revenue": round(v, 2),
                                       "critical_late":
                                           self.projects[pid]["critical_milestones_late"]}
                                      for pid, v in sorted(behind, key=lambda x: -x[1])[:10]]),
                          "Re-baseline or escalate; delayed milestones are delayed billing"))

        # knowledge risk: how much of the live book depends on a few people
        rows = self.rows(
            """SELECT e.employee_id, e.employee_name,
                      COUNT(DISTINCT t.project_id) projects, SUM(t.hours) hours
                 FROM time_entry t
                 JOIN employee e ON e.employee_id=t.employee_id
                 JOIN project p  ON p.project_id=t.project_id
                WHERE t.org_id=? AND p.project_status='In Flight'
                  AND t.approval_status='Approved'
                GROUP BY e.employee_id ORDER BY projects DESC""", (self.org_id,))
        if rows and live:
            top5 = rows[:5]
            covered = set()
            for r in self.rows(
                    """SELECT DISTINCT t.project_id, t.employee_id FROM time_entry t
                         JOIN project p ON p.project_id=t.project_id
                        WHERE t.org_id=? AND p.project_status='In Flight'""", (self.org_id,)):
                if r["employee_id"] in {x["employee_id"] for x in top5}:
                    covered.add(r["project_id"])
            share = pct(len(covered), len(live))
            if share and share > 25:
                flags.append(("Knowledge Risk", "High" if share > 40 else "Medium",
                              f"{share:,.1f}% of live projects depend on just 5 consultants",
                              f"{len(covered)} of {len(live)} in-flight projects have one of "
                              f"these five booking time to them.",
                              share, "percent", 25.0,
                              json.dumps([{"name": r["employee_name"],
                                           "live_projects": r["projects"],
                                           "hours": round(r["hours"], 1)} for r in top5]),
                              "Pair a second consultant onto each engagement these five "
                              "carry alone, and document configuration decisions"))

        dq_errors = self.scalar(
            """SELECT COUNT(*) FROM data_quality_finding WHERE org_id=? AND severity='error'""",
            (self.org_id,)) or 0
        if dq_errors:
            flags.append(("Data Risk", "High" if dq_errors > 30 else "Medium",
                          f"{dq_errors} data errors block a clean migration",
                          "Records with missing rates, invalid dates or impossible values "
                          "cannot be costed or scheduled as supplied.",
                          dq_errors, "count", 0.0,
                          json.dumps([{"rule": r["rule_code"], "count": r["n"]}
                                      for r in self.rows(
                                          """SELECT rule_code, COUNT(*) n
                                               FROM data_quality_finding
                                              WHERE org_id=? AND severity='error'
                                              GROUP BY rule_code ORDER BY n DESC""",
                                          (self.org_id,))]),
                          "Clear the error queue in the import wizard before approving "
                          "migration"))

        fa = self.forecast.get(("org", "ALL"))
        if fa and fa["hours_variance_pct"] is not None and fa["hours_variance_pct"] < -5:
            flags.append(("Forecast Risk", "Medium",
                          f"Original hour forecasts run {abs(fa['hours_variance_pct']):,.1f}% "
                          f"below actual across {fa['sample_size']} completed projects",
                          "Estimates are systematically light, which flows straight into "
                          "sold margin.",
                          fa["hours_variance_pct"], "percent", -5.0,
                          json.dumps([{"scope": v["scope_label"],
                                       "variance_pct": round(v["hours_variance_pct"], 1),
                                       "projects": v["sample_size"]}
                                      for k, v in self.forecast.items()
                                      if k[0] == "practice" and v["hours_variance_pct"] is not None]),
                          "Apply a practice-level contingency factor at estimate stage "
                          "until forecast accuracy improves"))

        self.cur.executemany(
            """INSERT INTO red_flag(org_id, run_id, assessment_id, flag_category, severity,
                    headline, detail, metric_value, metric_unit, threshold_value,
                    evidence_json, recommended_action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(self.org_id, self.run_id, self.assessment_id, cat, sev, head, det, v, u, th,
              ev, act) for cat, sev, head, det, v, u, th, ev, act in flags])
        self.flags = flags

    # =====================================================================
    def ps_operating_system(self):
        """Requirement 41, the five questions."""
        t = self.totals
        ctx = self.assessment_context
        live = [p for p in self.projects.values() if p["project_status"] == "In Flight"]
        target_margin = self.scalar(
            "SELECT AVG(target_margin_pct) FROM practice WHERE org_id=?", (self.org_id,)) or 30.0
        target_util = self.scalar(
            "SELECT AVG(target_utilization_pct) FROM practice WHERE org_id=?",
            (self.org_id,)) or 70.0

        # trailing 12 months, complete months only
        w12 = months_back(self.as_of, 12)
        rev12 = self.scalar(
            """SELECT SUM(f.recognized_revenue) FROM project_financial_month f
                 JOIN project p USING(project_id)
                WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?""",
            (self.org_id, w12[0], w12[-1])) or 0.0
        cost12 = self.scalar(
            """SELECT SUM(f.labor_cost + f.other_cost) FROM project_financial_month f
                 JOIN project p USING(project_id)
                WHERE p.org_id=? AND f.period_month BETWEEN ? AND ?""",
            (self.org_id, w12[0], w12[-1])) or 0.0
        bill12 = self.scalar(
            """SELECT SUM(CASE WHEN is_billable=1 THEN hours ELSE 0 END) FROM time_entry
                WHERE org_id=? AND approval_status='Approved'
                  AND strftime('%Y-%m', entry_date) BETWEEN ? AND ?""",
            (self.org_id, w12[0], w12[-1])) or 0.0
        cap12 = t["capacity_hours_month"] * 12

        # forward revenue from the live book plus not-started backlog
        backlog = sum((p["total_revenue"] or 0) - (p["recognized_revenue_to_date"] or 0)
                      for p in self.projects.values()
                      if p["project_status"] in ("In Flight", "Not Started"))

        counts = defaultdict(int)
        for p in live:
            counts[p["project_health"]] += 1
        budget_risk = sum(1 for pid in self.risk
                          if self.risk[pid]["p_budget"] >= 50
                          and self.projects[pid]["project_status"] == "In Flight")
        sched_risk = sum(1 for pid in self.risk
                         if self.risk[pid]["p_sched"] >= 50
                         and self.projects[pid]["project_status"] == "In Flight")
        margin_risk = sum(1 for pid in self.margin
                          if self.margin[pid]["risk"] in ("High", "Critical")
                          and self.projects[pid]["project_status"] == "In Flight")

        # Bench is capacity that was not sold, not the shortfall against
        # target. Reporting the latter shows zero bench the moment the target
        # is met, which is exactly when leadership stops looking.
        bench_hours = max(0.0, cap12 - bill12)
        last_month = self.window[-1]
        bench_heads = self.scalar(
            """SELECT COUNT(*) FROM (
                    SELECT e.employee_id,
                           COALESCE(SUM(CASE WHEN t.is_billable=1 THEN t.hours END),0) b,
                           e.weekly_capacity_hours*52.0/12.0 cap
                      FROM employee e
                      LEFT JOIN time_entry t ON t.employee_id=e.employee_id
                           AND t.approval_status='Approved'
                           AND strftime('%Y-%m', t.entry_date)=?
                     WHERE e.org_id=? AND e.is_active=1 AND e.is_billable=1
                     GROUP BY e.employee_id)
                WHERE cap > 0 AND b/cap < 0.40""", (last_month, self.org_id)) or 0
        pipeline = self.one(
            """SELECT SUM(services_value) v,
                      SUM(services_value*probability_pct/100.0) wv,
                      SUM(estimated_hours*probability_pct/100.0) wh
                 FROM pipeline_opportunity WHERE org_id=?
                  AND stage NOT IN ('Closed Won','Closed Lost')""", (self.org_id,))
        gap = sum(r["gap_hours"] for r in self.capacity)
        # Two different numbers, both true. The gross figure sums shortfalls
        # practice by practice, because a Finance consultant cannot cover an
        # HRP gap. The net figure assumes full fungibility and is the floor.
        hiring_gross = sum(r["implied_headcount_gap"]
                           for r in self.capacity) / HORIZON_MONTHS
        hiring_net = max(0.0, -gap / (162.5 * target_util / 100.0) / HORIZON_MONTHS)
        hiring = hiring_gross

        margin12 = pct(rev12 - cost12, rev12)
        util12 = pct(bill12, cap12)
        score = 0
        score += 1 if (margin12 or 0) >= target_margin else 0
        score += 1 if (util12 or 0) >= target_util - 3 else 0
        score += 1 if counts["Red"] <= max(1, len(live) * 0.08) else 0
        score += 1 if ctx["revenue_at_risk"] <= rev12 * 0.06 else 0
        overall = "Healthy" if score >= 4 else ("Watch" if score >= 2 else "At Risk")

        self.psos = {
            "as_of": self.as_of.isoformat(),
            "revenue_ytd": rev12, "revenue_forecast_fy": rev12 + backlog,
            "margin_pct": margin12, "margin_target_pct": target_margin,
            "revenue_at_risk": ctx["revenue_at_risk"],
            "projects_total": len(live), "projects_green": counts["Green"],
            "projects_yellow": counts["Yellow"], "projects_red": counts["Red"],
            "projects_budget_risk": budget_risk, "projects_schedule_risk": sched_risk,
            "projects_margin_risk": margin_risk,
            "utilization_pct": util12, "utilization_target_pct": target_util,
            "capacity_hours": t["capacity_hours_month"], "bench_hours": bench_hours,
            "overallocated_headcount": len(ctx["over"]),
            "pipeline_value": pipeline["v"] or 0.0,
            "weighted_pipeline_value": pipeline["wv"] or 0.0,
            "resource_demand_hours": sum(r["demand_hours"] for r in self.capacity),
            "capacity_gap_hours": gap,
            "hiring_requirement_fte": hiring,
            "hiring_requirement_fte_net": hiring_net,
            "bench_headcount": bench_heads,
            "backlog": backlog,
            "revenue_below_target": ctx.get("revenue_below_target"),
            "overall_health": overall,
        }
        self.cur.execute(
            """INSERT INTO ps_os_snapshot(org_id, run_id, as_of_date, revenue_ytd,
                    revenue_forecast_fy, margin_pct, margin_target_pct, revenue_at_risk,
                    projects_total, projects_green, projects_yellow, projects_red,
                    projects_budget_risk, projects_schedule_risk, projects_margin_risk,
                    utilization_pct, utilization_target_pct, capacity_hours, bench_hours,
                    overallocated_headcount, pipeline_value, weighted_pipeline_value,
                    resource_demand_hours, capacity_gap_hours, hiring_requirement_fte,
                    overall_health)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.org_id, self.run_id, self.as_of.isoformat(), rev12, rev12 + backlog,
             margin12, target_margin, ctx["revenue_at_risk"], len(live), counts["Green"],
             counts["Yellow"], counts["Red"], budget_risk, sched_risk, margin_risk,
             util12, target_util, t["capacity_hours_month"], bench_hours, len(ctx["over"]),
             pipeline["v"] or 0.0, pipeline["wv"] or 0.0,
             self.psos["resource_demand_hours"], gap, hiring, overall))

    # =====================================================================
    def leadership_actions(self):
        """
        Requirement 41 question 5. Ranked by value at stake, so the list is an
        order of work rather than a list of observations.
        """
        cands = []
        for pid, m in self.margin.items():
            p = self.projects[pid]
            if p["project_status"] != "In Flight":
                continue
            r = self.risk.get(pid, {})
            if r.get("failure", 0) < 55 and m["risk"] not in ("High", "Critical"):
                continue
            exposure = m["revenue"] * max(0.0, (m["target"] - (m["predicted_margin"] or 0))) / 100.0
            cands.append({
                "action_type": "REVIEW_PROJECT",
                "headline": f"Review {p['project_code']} {p['project_name']} - "
                            f"{r.get('failure', 0):,.0f}% probability of adverse outcome",
                "rationale": f"Predicted final margin {m['predicted_margin']:,.1f}% against a "
                             f"{m['target']:,.0f}% target on ${m['revenue']:,.0f} of revenue. "
                             f"Margin risk {m['risk']}, current health {p['project_health']}, "
                             f"predicted {r.get('predicted_health')}.",
                "entity_table": "project", "entity_pk": pid,
                "value_at_stake": exposure, "urgency": "This week",
            })
        # over-allocated and under-used people
        over = sorted(self.assessment_context["over"],
                      key=lambda u: -(u["utilization_pct"] or 0))[:2]
        for u in over:
            cands.append({
                "action_type": "REALLOCATE",
                "headline": f"Reallocate {u['employee_name']} - running at "
                            f"{u['utilization_pct']:,.0f}% utilisation",
                "rationale": f"Sustained allocation above capacity. Delivery quality and "
                             f"retention risk on every engagement this person carries.",
                "entity_table": "employee", "entity_pk": u["employee_id"],
                "value_at_stake": 0.0, "urgency": "This week",
            })
        # escalate the worst customer relationship
        worst = self.rows(
            """SELECT dimension_key, dimension_label, revenue, gross_profit, margin_pct
                 FROM profitability_cut
                WHERE org_id=? AND dimension='customer' AND revenue > 0
                ORDER BY gross_profit ASC LIMIT 1""", (self.org_id,))
        if worst and (worst[0]["gross_profit"] or 0) < 0:
            w = worst[0]
            cands.append({
                "action_type": "ESCALATE",
                "headline": f"Escalate {w['dimension_label']} - "
                            f"${abs(w['gross_profit']):,.0f} gross loss over "
                            f"{self.totals['months']} months",
                "rationale": f"Revenue ${w['revenue']:,.0f} at {w['margin_pct']:,.1f}% margin. "
                             f"Either reprice, restructure the delivery model, or exit.",
                "entity_table": "customer", "entity_pk": int(w["dimension_key"]),
                "value_at_stake": abs(w["gross_profit"] or 0), "urgency": "This month",
            })
        # revenue at risk
        rar = self.assessment_context["revenue_at_risk"]
        if rar > 0:
            cands.append({
                "action_type": "REVIEW_REVENUE_AT_RISK",
                "headline": f"Review ${rar:,.0f} of revenue at risk across live projects",
                "rationale": "Revenue sitting on projects the engine predicts will land "
                             "below target margin or are trending red.",
                "entity_table": None, "entity_pk": None,
                "value_at_stake": rar * 0.15, "urgency": "This month",
            })
        # hiring or contracting
        short = defaultdict(float)
        for r in self.capacity:
            if r["gap_hours"] < 0:
                short[r["practice_name"]] += -r["gap_hours"]
        if short:
            name, hours = max(short.items(), key=lambda x: x[1])
            fte = hours / (162.5 * 0.72) / HORIZON_MONTHS
            if fte >= 0.4:
                cands.append({
                    "action_type": "HIRE",
                    "headline": f"Add about {fte:,.1f} FTE of {name} capacity for the next "
                                f"{HORIZON_MONTHS} months",
                    "rationale": f"{hours:,.0f} hours of demand above billable capacity "
                                 f"across the horizon, from signed work and weighted pipeline.",
                    "entity_table": None, "entity_pk": None,
                    "value_at_stake": hours * (self.totals["realized_rate"] or 180) * 0.3,
                    "urgency": "This quarter",
                })
        # surplus capacity
        surplus = sum(r["gap_hours"] for r in self.capacity if r["gap_hours"] > 0)
        if surplus > 1500:
            cands.append({
                "action_type": "REPRICE",
                "headline": f"Fill or reduce {surplus:,.0f} hours of unsold capacity in the "
                            f"next {HORIZON_MONTHS} months",
                "rationale": "Capacity above weighted demand. Options are pipeline "
                             "acceleration, discounted fill work, or taking cost out.",
                "entity_table": None, "entity_pk": None,
                "value_at_stake": surplus * (self.totals["realized_rate"] or 180) * 0.25,
                "urgency": "This quarter",
            })

        cands.sort(key=lambda a: -a["value_at_stake"])
        self.actions = cands[:8]
        self.cur.executemany(
            """INSERT INTO leadership_action(org_id, run_id, rank, action_type, headline,
                    rationale, entity_table, entity_pk, value_at_stake, urgency)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(self.org_id, self.run_id, i + 1, a["action_type"], a["headline"],
              a["rationale"], a["entity_table"], a["entity_pk"], a["value_at_stake"],
              a["urgency"]) for i, a in enumerate(self.actions)])

    # =====================================================================
    def executive_briefing(self):
        """Requirement 42, generated from the run above rather than written."""
        s = self.psos
        t = self.totals
        # most recent complete month against forecast
        last = self.window[-1]
        mrow = self.one(
            """SELECT SUM(f.recognized_revenue) actual, SUM(f.forecast_amount) forecast
                 FROM project_financial_month f JOIN project p USING(project_id)
                WHERE p.org_id=? AND f.period_month=?""", (self.org_id, last))
        at_risk_projects = sum(
            1 for pid in self.risk
            if self.risk[pid]["predicted_health"] in ("Red", "Yellow")
            and self.projects[pid]["project_status"] == "In Flight")

        icon = {"Healthy": "Healthy", "Watch": "Watch", "At Risk": "At Risk"}[s["overall_health"]]
        lines = [
            f"# Professional Services Weekly Brief",
            f"**{self.org['org_name']}** - week ending {self.as_of.strftime('%d %B %Y')}",
            "",
            f"**Overall health: {icon}**",
            "",
            f"- Revenue (trailing 12 months): ${s['revenue_ytd']:,.0f}",
            f"- Margin: {s['margin_pct']:,.1f}% against a {s['margin_target_pct']:,.0f}% target",
            f"- Utilisation: {s['utilization_pct']:,.1f}% against a "
            f"{s['utilization_target_pct']:,.0f}% target",
            f"- Revenue at risk: ${s['revenue_at_risk']:,.0f}",
            f"- Projects at risk: {at_risk_projects} of {s['projects_total']} live",
            f"- Contracted backlog not yet recognised: ${s['backlog']:,.0f}",
            "",
            "## Key issues",
        ]
        issues = []
        for cat, sev, head, det, v, u, th, ev, act in self.flags[:5]:
            issues.append(f"{head}")
            lines.append(f"1. {head}")
        if not issues:
            lines.append("No threshold breaches this week.")
        lines += ["", "## Recommended actions"]
        for a in self.actions[:5]:
            lines.append(f"1. {a['headline']}")
        lines += ["", "## Basis",
                  f"Generated from the intelligence run of "
                  f"{datetime.now().strftime('%Y-%m-%d %H:%M')} over "
                  f"{len(self.projects)} projects, "
                  f"{self.scalar('SELECT COUNT(*) FROM time_entry WHERE org_id=?', (self.org_id,)):,} "
                  f"time entries and {t['months']} complete months of financials. "
                  f"Utilisation and margin exclude the current partial month."]

        self.cur.execute(
            """INSERT OR REPLACE INTO executive_briefing(org_id, run_id, briefing_date,
                    period_label, overall_health, revenue_actual, revenue_forecast,
                    margin_pct, margin_target_pct, utilization_pct, utilization_target_pct,
                    revenue_at_risk, projects_at_risk, narrative_md)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self.org_id, self.run_id, self.as_of.isoformat(),
             f"Week ending {self.as_of.isoformat()}", s["overall_health"],
             mrow["actual"] or 0.0, mrow["forecast"] or 0.0, s["margin_pct"],
             s["margin_target_pct"], s["utilization_pct"], s["utilization_target_pct"],
             s["revenue_at_risk"], at_risk_projects, "\n".join(lines)))
        bid = self.cur.lastrowid
        items = []
        for i, (cat, sev, head, det, v, u, th, ev, act) in enumerate(self.flags[:6], 1):
            items.append((bid, "key_issue", i, head, None, None, v if u == "currency" else None))
        for i, a in enumerate(self.actions[:6], 1):
            items.append((bid, "recommended_action", i, a["headline"], a["entity_table"],
                          a["entity_pk"], a["value_at_stake"]))
        for i, r in enumerate(
                sorted([b for b in self.bench.values() if b["verdict"] == "Better"],
                       key=lambda b: b["variance"] or 0)[:3], 1):
            items.append((bid, "win", i,
                          f"Tracking {abs(r['variance']):,.0f}% better than "
                          f"{r['peer_count']} comparable projects", None, None, None))
        self.cur.executemany(
            """INSERT INTO briefing_item(briefing_id, section, rank, statement,
                    entity_table, entity_pk, value_at_stake)
               VALUES (?,?,?,?,?,?,?)""", items)
        self.briefing_md = "\n".join(lines)

    # =====================================================================
    def migration_score(self):
        """
        Requirement 43. Scores are completeness measured against the mandatory
        fields in the templates, and a quality score penalised by open findings.
        """
        run = self.one("SELECT * FROM migration_run WHERE org_id=? ORDER BY migration_run_id DESC",
                       (self.org_id,))
        if not run:
            return

        def completeness(sql):
            r = self.one(sql, (self.org_id,))
            return pct(r["ok"], r["total"]) if r and r["total"] else 100.0

        financial = completeness(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN contract_value > 0 AND budget_hours > 0
                                AND budget_cost > 0 THEN 1 ELSE 0 END) ok
                 FROM project WHERE org_id=?""")
        resource = completeness(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN cost_rate IS NOT NULL AND billing_rate IS NOT NULL
                                AND start_date IS NOT NULL THEN 1 ELSE 0 END) ok
                 FROM employee WHERE org_id=?""")
        project = completeness(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN project_manager_id IS NOT NULL
                                AND date(planned_end_date) >= date(start_date)
                                AND legacy_project_id NOT LIKE '%-DUP' THEN 1 ELSE 0 END) ok
                 FROM project WHERE org_id=?""")
        errors = self.scalar(
            "SELECT COUNT(*) FROM data_quality_finding WHERE org_id=? AND severity='error'",
            (self.org_id,)) or 0
        warnings = self.scalar(
            "SELECT COUNT(*) FROM data_quality_finding WHERE org_id=? AND severity='warning'",
            (self.org_id,)) or 0
        rows_total = sum(self.scalar(f"SELECT COUNT(*) FROM {tbl} WHERE org_id=?",
                                     (self.org_id,)) or 0
                         for tbl in ("customer", "employee", "project", "contract",
                                     "time_entry"))
        penalty = (errors * 3 + warnings) / max(1, rows_total) * 100 * 12
        quality = clamp(100 - penalty, 0, 100)
        self.cur.execute(
            """UPDATE migration_run SET data_quality_score=?, financial_completeness_pct=?,
                    resource_completeness_pct=?, project_completeness_pct=?
                WHERE migration_run_id=?""",
            (round(quality, 1), round(financial, 1), round(resource, 1), round(project, 1),
             run["migration_run_id"]))
        self.migration = {
            "migration_run_id": run["migration_run_id"], "stage": run["stage"],
            "data_quality_score": round(quality, 1),
            "financial_completeness_pct": round(financial, 1),
            "resource_completeness_pct": round(resource, 1),
            "project_completeness_pct": round(project, 1),
            "errors": errors, "warnings": warnings,
        }


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    engines = {}
    for org in con.execute("SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        e = Engine(con, org["org_id"])
        rid = e.run()
        engines[org["org_code"]] = e
        print(f"{org['org_code']}: run {rid}, {len(e.risk)} projects scored, "
              f"{len(e.flags)} red flags, {e.dq_count} data quality findings")
    if "CCG" in engines and "OPCO" in engines:
        engines["CCG"].benchmark_against(engines["OPCO"])
        print("CCG metrics benchmarked against OPCO")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
