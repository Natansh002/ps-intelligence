"""
The diligence engine.

Seven questions, each of which a PSA product will show you a chart for and
none of which it will answer:

    What is the CSAT history, and what did customers actually say?
    Which engagements are red and amber, and why?
    Which phase runs long, and what causes it?
    How long does hypercare really take, and who pays for it?
    What is the average bill rate, list against booked against realised?
    What do the last three performance cycles say about the team?
    What does the product defect backlog cost delivery?

Two rules run through all of it.

First, every derived number carries the evidence it came from in the same
row. A CSAT average with no verbatim behind it cannot tell you why the
number moved, and a red project list with the reasons on a different screen
drifts apart from its own explanation within a week. So the register holds
the reasons, the phase analysis holds the attributed causes, and each one
states what in the data says so.

Second, nothing is asserted that cannot be checked. "Configure runs long
because of customer-raised issues" is only worth printing if the issues are
countable and counted, and the incidence is reported beside the claim so a
reader can see how often it actually holds rather than taking the headline
on trust. Where the evidence is thin, the row says the evidence is thin.
"""
import datetime as dt
import math
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

# Thresholds, in one place and named, so a reviewer can argue with them
# rather than having to find them.
T = {
    "promoter_score": 4.5,        # CSAT counted as a promoter
    "detractor_score": 3.0,       # CSAT counted as a detractor
    "csat_move_pts": 0.25,        # a movement worth commenting on
    "slip_material_days": 10,     # a phase slip worth attributing
    "cause_incidence_pct": 30,    # incidence below which a cause is anecdote
    "hypercare_clean_pct": 15,    # overrun within which an exit is clean
    "backlog_growing_net": 5,     # net ticket flow that counts as growing
    "rag_amber_prob": 35,         # failure probability for amber
    "rag_red_prob": 55,           # failure probability for red
    "margin_gap_pts": 5,          # margin below target worth flagging
    "min_sample": 5,              # below this, report the sample not the stat
}


# ---------------------------------------------------------------------------
def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, args=(), default=0):
    r = con.execute(sql, args).fetchone()
    return default if r is None or r[0] is None else r[0]


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def pctile(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
    return xs[k]


def pearson(xs, ys):
    """
    Correlation, with the sample size reported alongside it everywhere it is
    used. A coefficient without an n is a decoration.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < T["min_sample"]:
        return None, n
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return round(sxy / math.sqrt(sxx * syy), 3), n


def q2(x):
    return None if x is None else round(float(x), 2)


def add_months(d, n):
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------
def build_csat(con, org_id, run_id, as_of):
    con.execute("DELETE FROM csat_summary WHERE org_id=?", (org_id,))
    today = dt.date.fromisoformat(as_of)
    window_from = add_months(today, -12).isoformat()
    prior_from = add_months(today, -24).isoformat()

    scopes = [("org", None, "Whole organisation", "1=1", ())]
    for r in rows(con, """SELECT DISTINCT p.product FROM csat_response c
                            JOIN project p USING(project_id)
                           WHERE c.org_id=? AND p.product IS NOT NULL""",
                  (org_id,)):
        scopes.append(("product", r["product"], r["product"],
                       "p.product=?", (r["product"],)))
    for r in rows(con, """SELECT DISTINCT pr.practice_id, pr.practice_name
                            FROM csat_response c JOIN project p USING(project_id)
                            JOIN practice pr ON pr.practice_id=p.practice_id
                           WHERE c.org_id=?""", (org_id,)):
        scopes.append(("practice", str(r["practice_id"]), r["practice_name"],
                       "p.practice_id=?", (r["practice_id"],)))
    for r in rows(con, """SELECT c.customer_id, cu.customer_name, COUNT(*) n
                            FROM csat_response c
                            JOIN customer cu ON cu.customer_id=c.customer_id
                           WHERE c.org_id=? GROUP BY 1 HAVING n>=?
                           ORDER BY n DESC""", (org_id, T["min_sample"])):
        scopes.append(("customer", str(r["customer_id"]), r["customer_name"],
                       "c.customer_id=?", (r["customer_id"],)))
    for r in rows(con, "SELECT DISTINCT survey_point FROM csat_response "
                       "WHERE org_id=?", (org_id,)):
        scopes.append(("survey_point", r["survey_point"],
                       r["survey_point"].replace("_", " ").title(),
                       "c.survey_point=?", (r["survey_point"],)))

    out = []
    for scope, key, label, clause, args in scopes:
        base = f"""FROM csat_response c
                   LEFT JOIN project p ON p.project_id=c.project_id
                   WHERE c.org_id=? AND {clause}"""
        cur_rows = rows(con, f"""
            SELECT c.score, c.nps, c.would_reference, c.theme, c.sentiment,
                   c.comment
            {base} AND c.responded_on>=?""",
            (org_id,) + args + (window_from,))
        if not cur_rows:
            continue
        prior = [r["score"] for r in rows(con, f"""
            SELECT c.score {base} AND c.responded_on>=? AND c.responded_on<?""",
            (org_id,) + args + (prior_from, window_from))]

        scores = [r["score"] for r in cur_rows]
        avg = sum(scores) / len(scores)
        prior_avg = (sum(prior) / len(prior)) if prior else None
        delta = (avg - prior_avg) if prior_avg is not None else None
        promoters = sum(1 for s in scores if s >= T["promoter_score"])
        detractors = sum(1 for s in scores if s <= T["detractor_score"])
        nps_vals = [r["nps"] for r in cur_rows if r["nps"] is not None]
        refs = [r["would_reference"] for r in cur_rows
                if r["would_reference"] is not None]
        themes = {}
        for r in cur_rows:
            if r["theme"] and r["sentiment"] in ("negative", "mixed"):
                themes[r["theme"]] = themes.get(r["theme"], 0) + 1
        top_theme, top_count = (max(themes.items(), key=lambda kv: kv[1])
                                if themes else (None, 0))

        # The statement leads with the movement, because the level is on the
        # screen already and the movement is what a reader is looking for.
        if len(scores) < T["min_sample"]:
            statement = (f"{label}: {len(scores)} responses, too few to read "
                         f"as a trend. The individual comments are the "
                         f"evidence here, not the average.")
        else:
            statement = f"{label}: {avg:,.2f} from {len(scores)} responses"
            if delta is not None and abs(delta) >= T["csat_move_pts"]:
                statement += (f", {'up' if delta > 0 else 'down'} "
                              f"{abs(delta):,.2f} on the prior year")
            elif delta is not None:
                statement += ", flat on the prior year"
            statement += "."
            if detractors:
                statement += (f" {detractors} of {len(scores)} scored "
                              f"{T['detractor_score']:,.0f} or below")
                if top_theme:
                    statement += (f", and the most common theme in the "
                                  f"critical comments is "
                                  f"{top_theme.replace('_', ' ')} "
                                  f"({top_count} of them)")
                statement += "."
            if refs and len(refs) >= T["min_sample"]:
                statement += (f" {sum(refs) / len(refs) * 100:,.0f}% would act "
                              f"as a reference.")

        out.append((org_id, run_id, scope, key, label, window_from, as_of,
                    len(scores), q2(avg), q2(prior_avg), q2(delta),
                    q2(promoters / len(scores) * 100),
                    q2(detractors / len(scores) * 100),
                    q2(sum(nps_vals) / len(nps_vals)) if nps_vals else None,
                    q2(sum(refs) / len(refs) * 100) if refs else None,
                    top_theme, top_count, statement))

    con.executemany("""
        INSERT INTO csat_summary
          (org_id, run_id, scope, scope_key, scope_label, period_from,
           period_to, responses, avg_score, prior_avg_score, delta,
           pct_promoters, pct_detractors, nps, reference_rate, top_theme,
           top_theme_count, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


# ---------------------------------------------------------------------------
# Phase duration and why it slips
# ---------------------------------------------------------------------------
PHASE_ORDER = {"Initiate": 1, "Analyse": 2, "Configure": 3, "Test": 4,
               "Deploy": 5, "Stabilise": 6}


def build_phases(con, org_id, run_id, as_of):
    con.execute("""DELETE FROM phase_slip_cause WHERE phase_analysis_id IN
                   (SELECT phase_analysis_id FROM phase_duration_analysis
                     WHERE org_id=?)""", (org_id,))
    con.execute("DELETE FROM phase_duration_analysis WHERE org_id=?", (org_id,))

    scopes = [("org", None, "Whole organisation", "1=1", ())]
    for r in rows(con, """SELECT product, COUNT(*) n FROM project
                           WHERE org_id=? AND product IS NOT NULL
                             AND actual_end_date IS NOT NULL
                           GROUP BY 1 HAVING n>=? ORDER BY n DESC""",
                  (org_id, T["min_sample"])):
        scopes.append(("product", r["product"], r["product"],
                       "p.product=?", (r["product"],)))

    made = 0
    for scope, key, label, clause, args in scopes:
        phase_rows = rows(con, f"""
            SELECT tk.phase,
                   tk.project_id,
                   MIN(tk.planned_start) AS p_start,
                   MAX(tk.planned_end)   AS p_end,
                   MIN(tk.actual_start)  AS a_start,
                   MAX(tk.actual_end)    AS a_end,
                   SUM(tk.planned_hours) AS p_hours,
                   SUM(tk.actual_hours)  AS a_hours
            FROM project_task tk JOIN project p ON p.project_id=tk.project_id
            WHERE p.org_id=? AND {clause} AND p.actual_end_date IS NOT NULL
              AND tk.phase IS NOT NULL AND tk.actual_end IS NOT NULL
            GROUP BY tk.phase, tk.project_id""", (org_id,) + args)
        if not phase_rows:
            continue

        by_phase = {}
        for r in phase_rows:
            if not (r["p_start"] and r["p_end"] and r["a_start"] and r["a_end"]):
                continue
            planned = (dt.date.fromisoformat(r["p_end"])
                       - dt.date.fromisoformat(r["p_start"])).days + 1
            actual = (dt.date.fromisoformat(r["a_end"])
                      - dt.date.fromisoformat(r["a_start"])).days + 1
            if planned <= 0 or actual <= 0:
                continue
            by_phase.setdefault(r["phase"], []).append({
                "project_id": r["project_id"], "planned": planned,
                "actual": actual, "slip": actual - planned,
                "p_hours": r["p_hours"] or 0, "a_hours": r["a_hours"] or 0})
        if not by_phase:
            continue

        total_slip = sum(max(0, x["slip"]) for xs in by_phase.values()
                         for x in xs)
        summaries = []
        for phase, xs in by_phase.items():
            slips = [x["slip"] for x in xs]
            med_slip = median(slips) or 0
            med_planned = median([x["planned"] for x in xs]) or 0
            worst = max(xs, key=lambda x: x["slip"])
            p_h = median([x["p_hours"] for x in xs]) or 0
            a_h = median([x["a_hours"] for x in xs]) or 0
            slipped = [s for s in slips if s > 0]
            summaries.append({
                "phase": phase, "n": len(xs),
                "planned": med_planned,
                "actual": median([x["actual"] for x in xs]) or 0,
                "slip": med_slip,
                "slip_pct": (med_slip / med_planned * 100) if med_planned else 0,
                "n_slipped": len(slipped),
                "pct_slipped": len(slipped) / len(xs) * 100,
                "slip_when": (sum(slipped) / len(slipped)) if slipped else 0,
                "slip_p90": pctile(slips, 0.9) or 0,
                "p_hours": p_h, "a_hours": a_h,
                "effort_pct": ((a_h / p_h - 1) * 100) if p_h else None,
                "share": (sum(max(0, s) for s in slips) / total_slip * 100
                          if total_slip else 0),
                "worst_id": worst["project_id"], "worst_slip": worst["slip"],
                "projects": [x["project_id"] for x in xs
                             if x["slip"] >= T["slip_material_days"]],
            })
        summaries.sort(key=lambda s: -s["share"])

        for rank, s in enumerate(summaries, 1):
            # Lead with the shape of the distribution, not its middle. Most
            # engagements run most phases close to plan; the interesting
            # number is how often a phase goes long and by how much when it
            # does, and a median of one day on top of a three-week tail is
            # technically true and completely misleading.
            statement = (
                f"{s['phase']} is planned at {s['planned']:,.0f} days across "
                f"{s['n']} completed engagements. "
                f"{s['pct_slipped']:,.0f}% of them ran it long, by "
                f"{s['slip_when']:,.0f} days on average and "
                f"{s['slip_p90']:,.0f} at the ninetieth percentile. ")
            if rank == 1 and s["share"] >= 25:
                statement += (f"It carries {s['share']:,.0f}% of all schedule "
                              f"slip in scope, which makes it the phase worth "
                              f"fixing first. ")
            else:
                statement += f"It accounts for {s['share']:,.0f}% of total slip. "
            if s["effort_pct"] is not None:
                if s["effort_pct"] > 12:
                    statement += (f"Effort overruns by {s['effort_pct']:,.0f}% "
                                  f"as well, so this is more work than "
                                  f"planned rather than the same work taking "
                                  f"longer.")
                elif s["effort_pct"] < -8:
                    statement += (f"Effort is {abs(s['effort_pct']):,.0f}% "
                                  f"under plan, so the team is waiting rather "
                                  f"than working. That points at a dependency, "
                                  f"not at capacity.")
                else:
                    statement += ("Effort is close to plan, so the same work "
                                  "is taking longer rather than more work "
                                  "appearing.")

            cur = con.execute("""
                INSERT INTO phase_duration_analysis
                  (org_id, run_id, scope, scope_key, scope_label, phase,
                   phase_order, projects, planned_days_med, actual_days_med,
                   slip_days_med, slip_pct_med, projects_slipped,
                   pct_slipped, slip_days_when_slipped, slip_days_p90,
                   planned_hours_med,
                   actual_hours_med, effort_overrun_pct, share_of_slip_pct,
                   worst_project_id, worst_project_slip, rank_by_slip,
                   statement)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (org_id, run_id, scope, key, label, s["phase"],
                 PHASE_ORDER.get(s["phase"], 9), s["n"], q2(s["planned"]),
                 q2(s["actual"]), q2(s["slip"]), q2(s["slip_pct"]),
                 s["n_slipped"], q2(s["pct_slipped"]), q2(s["slip_when"]),
                 q2(s["slip_p90"]),
                 q2(s["p_hours"]), q2(s["a_hours"]), q2(s["effort_pct"]),
                 q2(s["share"]), s["worst_id"], q2(s["worst_slip"]), rank,
                 statement))
            made += 1
            attribute_causes(con, cur.lastrowid, s)
    return made


def attribute_causes(con, analysis_id, s):
    """
    Why the phase ran long, from evidence rather than assertion.

    Each candidate cause is a countable thing in the database, measured over
    the engagements that actually slipped in this phase. The incidence is
    stored beside the claim: a cause present in 62% of slipped engagements is
    a finding, and one present in 14% is an anecdote that happens to be
    memorable. Both get recorded, and the incidence is what separates them,
    because the alternative is a plausible sentence with nothing behind it.
    """
    pids = s["projects"]
    if not pids:
        return
    marks = ",".join("?" * len(pids))
    n = len(pids)
    causes = []

    cust_issues = one(con, f"""
        SELECT COUNT(DISTINCT project_id) FROM risk_issue
         WHERE project_id IN ({marks}) AND is_customer_raised=1
           AND entry_type='Issue'""", pids)
    if cust_issues:
        total = one(con, f"""SELECT COUNT(*) FROM risk_issue
                             WHERE project_id IN ({marks})
                               AND is_customer_raised=1
                               AND entry_type='Issue'""", pids)
        causes.append((
            "customer_dependency", "Customer-side dependency or availability",
            f"{total} customer-raised issues logged on {cust_issues} of the "
            f"{n} engagements that slipped in this phase",
            cust_issues / n * 100, total, "issues"))

    rework = rows(con, f"""
        SELECT project_id,
               SUM(CASE WHEN time_category IN ('Rework','Over-service')
                        THEN hours ELSE 0 END) AS rw,
               SUM(hours) AS tot
        FROM time_entry WHERE project_id IN ({marks}) GROUP BY project_id""",
        pids)
    heavy = [r for r in rework if r["tot"] and r["rw"] / r["tot"] > 0.08]
    if heavy:
        share = sum(r["rw"] for r in heavy) / sum(r["tot"] for r in heavy) * 100
        causes.append((
            "rework", "Rework and over-service",
            f"{len(heavy)} of {n} engagements booked more than 8% of their "
            f"hours to rework or over-service, averaging {share:,.0f}%",
            len(heavy) / n * 100, q2(share), "percent"))

    cos = one(con, f"""SELECT COUNT(DISTINCT project_id) FROM change_order
                        WHERE project_id IN ({marks}) AND status='Approved'""",
              pids)
    if cos:
        co_hours = one(con, f"""SELECT SUM(co_hours) FROM change_order
                                 WHERE project_id IN ({marks})
                                   AND status='Approved'""", pids)
        causes.append((
            "scope_change", "Scope added mid-phase",
            f"{cos} of {n} engagements took an approved change order, adding "
            f"{co_hours or 0:,.0f} hours of scope",
            cos / n * 100, q2(co_hours), "hours"))

    churn = rows(con, f"""
        SELECT project_id, COUNT(DISTINCT employee_id) AS people
        FROM time_entry WHERE project_id IN ({marks}) GROUP BY project_id""",
        pids)
    churned = [c for c in churn if c["people"] > 9]
    if churned:
        causes.append((
            "resource_churn", "Delivery team churn",
            f"{len(churned)} of {n} engagements had more than nine different "
            f"people booking time, against a typical team of four to six",
            len(churned) / n * 100,
            q2(median([c["people"] for c in churned])), "people"))

    defects = one(con, f"""SELECT COUNT(*) FROM product_ticket
                            WHERE project_id IN ({marks}) AND ticket_type='bug'
                              AND severity IN ('blocker','high')""", pids)
    if defects:
        affected = one(con, f"""SELECT COUNT(DISTINCT project_id)
                                  FROM product_ticket
                                 WHERE project_id IN ({marks})
                                   AND ticket_type='bug'
                                   AND severity IN ('blocker','high')""", pids)
        causes.append((
            "product_defect", "Product defects in the delivery window",
            f"{defects} blocker or high severity product defects raised "
            f"against {affected} of the {n} slipped engagements",
            affected / n * 100, defects, "defects"))

    if not causes:
        return
    causes.sort(key=lambda c: -c[3])
    # Attribute the median slip across the causes in proportion to their
    # incidence. It is an apportionment rather than a measurement, and it is
    # labelled as one in the UI, because the honest alternative is to leave
    # the reader with five percentages and no sense of scale.
    weight = sum(c[3] for c in causes) or 1
    con.executemany("""
        INSERT INTO phase_slip_cause
          (phase_analysis_id, cause_code, cause_label, evidence,
           incidence_pct, attributed_days, metric_value, metric_unit, rank)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        [(analysis_id, c[0], c[1], c[2], q2(c[3]),
          q2(s["slip"] * c[3] / weight), c[4], c[5], i + 1)
         for i, c in enumerate(causes)])


# ---------------------------------------------------------------------------
# Hypercare
# ---------------------------------------------------------------------------
def build_hypercare(con, org_id, run_id, as_of):
    con.execute("DELETE FROM hypercare_summary WHERE org_id=?", (org_id,))
    scopes = [("org", None, "Whole organisation", "1=1", ())]
    for r in rows(con, """SELECT p.product, COUNT(*) n FROM hypercare_period h
                            JOIN project p USING(project_id)
                           WHERE h.org_id=? GROUP BY 1 HAVING n>=?
                           ORDER BY n DESC""", (org_id, T["min_sample"])):
        scopes.append(("product", r["product"], r["product"],
                       "p.product=?", (r["product"],)))

    out = []
    for scope, key, label, clause, args in scopes:
        hs = rows(con, f"""
            SELECT h.*, p.product, p.billing_model, e.cost_rate
            FROM hypercare_period h JOIN project p USING(project_id)
            LEFT JOIN employee e ON e.employee_id=p.project_manager_id
            WHERE h.org_id=? AND {clause}""", (org_id,) + args)
        if not hs:
            continue
        closed = [h for h in hs if h["actual_days"] is not None]
        planned = median([h["planned_days"] for h in hs])
        actual = median([h["actual_days"] for h in closed])
        p90 = pctile([h["actual_days"] for h in closed], 0.9)
        overrun = median([h["overrun_days"] for h in closed])
        effort = median([h["effort_hours"] for h in hs])
        eff_pct = median([h["effort_pct_of_project"] for h in hs])
        tot_effort = sum(h["effort_hours"] or 0 for h in hs)
        tot_billable = sum(h["billable_hours"] or 0 for h in hs)
        billable_share = (tot_billable / tot_effort * 100) if tot_effort else None
        # The cost of the part nobody charges for, at the organisation's own
        # average cost rate. This is the number that makes hypercare a
        # commercial conversation rather than a support one.
        cost_rate = one(con, """SELECT SUM(hours*cost_rate)/SUM(hours)
                                  FROM time_entry WHERE org_id=? AND hours>0""",
                        (org_id,), default=0) or 0
        unbilled_cost = (tot_effort - tot_billable) * cost_rate
        clean = sum(1 for h in hs if h["exit_verdict"] == "clean")

        statement = (
            f"{label}: hypercare planned at {planned:,.0f} days runs "
            f"{actual:,.0f} at the median and {p90:,.0f} at the ninetieth "
            f"percentile, across {len(hs)} go-lives. "
            if actual and p90 else
            f"{label}: {len(hs)} go-lives, too few closed periods to read a "
            f"distribution. ")
        if overrun and planned:
            statement += (f"That is {overrun:,.0f} days beyond plan at the "
                          f"median, {overrun / planned * 100:,.0f}% over. ")
        if billable_share is not None:
            statement += (
                f"Only {billable_share:,.0f}% of the "
                f"{tot_effort:,.0f} hours spent in hypercare is billable, so "
                f"roughly {unbilled_cost:,.0f} of delivery cost is absorbed "
                f"rather than charged. ")
        statement += (f"{clean / len(hs) * 100:,.0f}% of exits were clean; the "
                      f"rest were extended or are still open.")

        out.append((org_id, run_id, scope, key, label, len(hs),
                    q2(planned), q2(actual), q2(p90), q2(overrun),
                    q2(effort), q2(eff_pct), q2(billable_share),
                    q2(unbilled_cost),
                    q2(median([h["tickets_raised"] for h in hs])),
                    q2(clean / len(hs) * 100), statement))

    con.executemany("""
        INSERT INTO hypercare_summary
          (org_id, run_id, scope, scope_key, scope_label, projects,
           planned_days_med, actual_days_med, actual_days_p90,
           overrun_days_med, effort_hours_med, effort_pct_med,
           billable_share_pct, unbilled_cost, tickets_med, clean_exit_pct,
           statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------
def build_rates(con, org_id, run_id, as_of):
    """
    List against booked against realised, on one row.

    Three different numbers get called "the bill rate" and they answer three
    different questions. The list rate is what the rate card says. The booked
    rate is what the engagement was actually sold at, so the gap between them
    is discount, which is a sales conversation. The realised rate is revenue
    actually recognised divided by billable hours actually delivered, so the
    gap between booked and realised is leakage: hours written off, fixed fees
    that ran over, work never invoiced. That is a delivery conversation, and
    it is invisible if only one of the three is reported.

    The net rate divides the same revenue by *all* hours delivered on the
    engagement, billable or not. It is the only one of the four that tells you
    what an hour of delivery time on that work is worth.

    All four are computed over hours booked to a project. Bench, leave and
    internal time are excluded, not because they are free but because a
    revenue rate per hour of annual leave is not a number: absorbing that
    time is what the utilisation figures are for, and mixing the two produces
    an organisation-wide net rate that cannot be compared with any of the
    rows beneath it.
    """
    con.execute("DELETE FROM rate_analysis WHERE org_id=?", (org_id,))
    window = add_months(dt.date.fromisoformat(as_of), -12).isoformat()

    dims = [("org", None, "Whole organisation", "1=1", ())]
    for r in rows(con, "SELECT DISTINCT role FROM employee WHERE org_id=?",
                  (org_id,)):
        dims.append(("role", r["role"], r["role"], "e.role=?", (r["role"],)))
    for r in rows(con, """SELECT DISTINCT product FROM project WHERE org_id=?
                            AND product IS NOT NULL""", (org_id,)):
        dims.append(("product", r["product"], r["product"],
                     "p.product=?", (r["product"],)))
    for r in rows(con, """SELECT practice_id, practice_name FROM practice
                           WHERE org_id=?""", (org_id,)):
        dims.append(("practice", str(r["practice_id"]), r["practice_name"],
                     "p.practice_id=?", (r["practice_id"],)))
    for r in rows(con, "SELECT DISTINCT billing_model FROM project WHERE org_id=?",
                  (org_id,)):
        dims.append(("billing_model", r["billing_model"], r["billing_model"],
                     "p.billing_model=?", (r["billing_model"],)))
    for r in rows(con, """SELECT c.customer_id, c.customer_name,
                                 SUM(p.contract_value) v
                            FROM customer c JOIN project p USING(customer_id)
                           WHERE c.org_id=? GROUP BY 1 ORDER BY v DESC LIMIT 12""",
                  (org_id,)):
        dims.append(("customer", str(r["customer_id"]), r["customer_name"],
                     "p.customer_id=?", (r["customer_id"],)))

    out = []
    for dim, key, label, clause, args in dims:
        agg = rows(con, f"""
            SELECT SUM(CASE WHEN t.is_billable=1 THEN t.hours ELSE 0 END) AS bill_h,
                   SUM(t.hours) AS tot_h,
                   SUM(CASE WHEN t.is_billable=1
                            THEN t.hours * COALESCE(e.billing_rate,0) END) AS list_v,
                   SUM(CASE WHEN t.is_billable=1
                            THEN t.hours * COALESCE(t.bill_rate,0) END) AS booked_v,
                   SUM(t.hours * COALESCE(t.cost_rate,0)) AS cost_v,
                   COUNT(DISTINCT t.employee_id) AS heads,
                   COUNT(DISTINCT t.project_id) AS projects
            FROM time_entry t
                 JOIN employee e ON e.employee_id=t.employee_id
                 LEFT JOIN project p ON p.project_id=t.project_id
            WHERE t.org_id=? AND {clause} AND t.approval_status='Approved'
              AND t.project_id IS NOT NULL
              AND t.entry_date>=?""", (org_id,) + args + (window,))[0]
        if not agg["bill_h"]:
            continue

        # Revenue over the same population and window, so realised is
        # comparable with booked rather than merely adjacent to it.
        revenue = one(con, f"""
            SELECT SUM(fm.recognized_revenue)
            FROM project_financial_month fm JOIN project p USING(project_id)
            WHERE p.org_id=? AND {clause.replace('e.role=?', '1=1')}
              AND fm.period_month>=?""",
            (org_id,) + (args if "e.role=?" not in clause else ())
            + (window[:7],), default=None) if dim != "role" else None

        list_rate = agg["list_v"] / agg["bill_h"] if agg["list_v"] else None
        booked = agg["booked_v"] / agg["bill_h"] if agg["booked_v"] else None
        realised = (revenue / agg["bill_h"]) if revenue else None
        net = (revenue / agg["tot_h"]) if revenue and agg["tot_h"] else None
        cost_rate = agg["cost_v"] / agg["tot_h"] if agg["tot_h"] else None
        discount = ((1 - booked / list_rate) * 100
                    if list_rate and booked else None)
        leakage = ((1 - realised / booked) * 100
                   if booked and realised else None)
        margin = ((revenue - agg["cost_v"]) / revenue * 100
                  if revenue else None)

        absorption = ((1 - net / booked) * 100
                      if booked and net else None)
        bill_share = (agg["bill_h"] / agg["tot_h"] * 100
                      if agg["tot_h"] else None)

        statement = f"{label}: "
        if list_rate and booked:
            statement += (f"the rate card says {list_rate:,.0f} and the work "
                          f"was sold at {booked:,.0f}, a discount of "
                          f"{discount:,.1f}%. ")
        if realised and net and absorption is not None:
            # Two figures, and the difference between them is the whole
            # point. Revenue per *billable* hour can sit above the booked
            # rate on fixed-price work and usually does, because the fee is
            # fixed and the non-billable hours delivered alongside it are
            # invisible to that division. Revenue per hour *delivered* is not
            # flattered by anything: it is what the engagement actually
            # returned for the time it actually consumed. Reporting only the
            # first is how a book with a seven-point absorption problem reads
            # as recovering above its own rate card.
            statement += (
                f"Revenue recognised comes to {realised:,.0f} per billable "
                f"hour, but only {bill_share:,.0f}% of the hours delivered "
                f"were billable, so across every hour actually spent on this "
                f"work an hour returned {net:,.0f} — "
                f"{abs(absorption):,.1f}% "
                f"{'below' if absorption > 0 else 'above'} the rate it was "
                f"sold at. ")
            if leakage is not None and leakage > 1:
                statement += (f"{leakage:,.1f}% of that is lost between the "
                              f"billable hour and the recognised revenue: "
                              f"hours written off, fixed fees that ran over, "
                              f"or work never invoiced. ")
        elif realised:
            statement += f"Revenue realised {realised:,.0f} per billable hour. "
        if margin is not None:
            # Named precisely, because "margin" on its own gets read as the
            # bottom line and this is revenue less the cost of the hours. It
            # carries no overhead, no bench and no sales cost.
            statement += (f"Gross delivery margin on the same population, "
                          f"revenue less the cost of the hours delivered, is "
                          f"{margin:,.1f}%.")
        if dim == "role":
            statement += (" Revenue cannot be attributed to a role, so the "
                          "discount is the only comparable figure here.")

        out.append((org_id, run_id, dim, key, label, q2(agg["bill_h"]),
                    q2(agg["tot_h"]), q2(list_rate), q2(booked), q2(realised),
                    q2(net), q2(cost_rate), q2(discount), q2(leakage),
                    q2(absorption),
                    q2(margin), q2(revenue), q2(agg["cost_v"]), agg["heads"],
                    statement))

    con.executemany("""
        INSERT INTO rate_analysis
          (org_id, run_id, dimension, dimension_key, dimension_label,
           billable_hours, total_hours, list_rate_avg, booked_rate_avg,
           realised_rate, net_rate, cost_rate_avg, discount_pct, leakage_pct,
           absorption_pct, margin_pct, revenue, cost, headcount, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
def build_performance(con, org_id, run_id, as_of):
    con.execute("DELETE FROM performance_summary WHERE org_id=?", (org_id,))
    cycles = rows(con, """SELECT * FROM performance_cycle WHERE org_id=?
                          ORDER BY sort_order""", (org_id,))
    if not cycles:
        return 0

    scopes = [("org", None, "Whole organisation", "1=1", ())]
    for c in cycles:
        scopes.append(("cycle", c["cycle_code"], c["cycle_label"],
                       "pc.cycle_id=?", (c["cycle_id"],)))
    for r in rows(con, """SELECT practice_id, practice_name FROM practice
                           WHERE org_id=?""", (org_id,)):
        scopes.append(("practice", str(r["practice_id"]), r["practice_name"],
                       "e.practice_id=?", (r["practice_id"],)))
    for r in rows(con, "SELECT DISTINCT role FROM employee WHERE org_id=?",
                  (org_id,)):
        scopes.append(("role", r["role"], r["role"], "e.role=?", (r["role"],)))

    latest = cycles[-1]["cycle_id"]
    prior = cycles[-2]["cycle_id"] if len(cycles) > 1 else None

    out = []
    for scope, key, label, clause, args in scopes:
        base = f"""FROM performance_review pr
                   JOIN performance_cycle pc ON pc.cycle_id=pr.cycle_id
                   JOIN employee e ON e.employee_id=pr.employee_id
                   WHERE pc.org_id=? AND {clause}"""
        # For anything other than a cycle scope, the current picture is the
        # latest cycle. Averaging three cycles together and calling it the
        # rating would hide the trend, which is the only thing worth knowing.
        cycle_filter = "" if scope == "cycle" else " AND pr.cycle_id=?"
        cargs = () if scope == "cycle" else (latest,)
        rs = rows(con, f"""SELECT pr.* {base}{cycle_filter}""",
                  (org_id,) + args + cargs)
        if not rs:
            continue
        prior_rs = []
        if scope != "cycle" and prior:
            prior_rs = rows(con, f"""SELECT pr.overall_rating {base}
                                     AND pr.cycle_id=?""",
                            (org_id,) + args + (prior,))

        ratings = [r["overall_rating"] for r in rs]
        avg = sum(ratings) / len(ratings)
        prior_avg = (sum(r["overall_rating"] for r in prior_rs) / len(prior_rs)
                     if prior_rs else None)
        r_coef, n_pairs = pearson([r["utilization_pct"] for r in rs], ratings)
        training = [r["training_hours"] for r in rs if r["training_hours"]]
        target_training = one(con, """SELECT training_hours_per_year FROM
                                      resource_plan_line l JOIN resource_plan p
                                      USING(plan_id) WHERE p.org_id=? LIMIT 1""",
                              (org_id,), default=None)

        cycle_note = ("" if scope == "cycle" else
                      f" in {cycles[-1]['cycle_label']}")
        statement = (
            f"{label}: {len(rs)} reviews{cycle_note} averaging {avg:,.2f}")
        if prior_avg is not None:
            d = avg - prior_avg
            statement += (f", {'up' if d > 0 else 'down' if d < 0 else 'flat'}"
                          + (f" {abs(d):,.2f} on the prior cycle" if d else
                             " on the prior cycle"))
        statement += ". "
        top = sum(1 for x in ratings if x >= 4.5)
        bottom = sum(1 for x in ratings if x <= 2.5)
        statement += (f"{top} at 4.5 or above, {bottom} at 2.5 or below. ")
        if r_coef is not None:
            if abs(r_coef) >= 0.4:
                statement += (
                    f"Ratings correlate with billable utilisation at "
                    f"r={r_coef:+.2f} over {n_pairs} reviews, which means the "
                    f"cycle is largely rewarding chargeable hours. That is "
                    f"defensible if it is deliberate and a problem if it is "
                    f"not, because the people carrying presales and "
                    f"enablement are the ones it penalises. ")
            elif abs(r_coef) <= 0.15:
                statement += (
                    f"Ratings show almost no relationship to billable "
                    f"utilisation (r={r_coef:+.2f} over {n_pairs} reviews). "
                    f"Either the cycle is measuring something else "
                    f"deliberately, or it is not measuring anything "
                    f"consistently. ")
            else:
                statement += (f"Ratings correlate with utilisation at "
                              f"r={r_coef:+.2f} over {n_pairs} reviews. ")
        flight = sum(1 for r in rs if r["flight_risk"] == "high")
        if flight:
            top_flight = sum(1 for r in rs if r["flight_risk"] == "high"
                             and r["overall_rating"] >= 4.0)
            statement += (f"{flight} flagged as a high retention risk"
                          + (f", {top_flight} of them rated 4.0 or above"
                             if top_flight else "")
                          + ". ")
        if training and target_training:
            avg_tr = sum(training) / len(training)
            if avg_tr < target_training * 0.85:
                statement += (
                    f"Training averages {avg_tr:,.0f} hours against a target "
                    f"of {target_training:,.0f}, and the capacity plan has "
                    f"already reserved the target. Under-delivering it does "
                    f"not free capacity, it defers a certification.")

        out.append((org_id, run_id, scope, key, label, len(rs), q2(avg),
                    q2(prior_avg),
                    q2(avg - prior_avg) if prior_avg is not None else None,
                    q2(top / len(rs) * 100), q2(bottom / len(rs) * 100),
                    flight, sum(1 for r in rs if r["promotion_ready"]),
                    q2(sum(training) / len(training)) if training else None,
                    q2(target_training),
                    q2(sum(1 for r in rs if r["is_calibrated"]) / len(rs) * 100),
                    r_coef, statement))

    con.executemany("""
        INSERT INTO performance_summary
          (org_id, run_id, scope, scope_key, scope_label, reviews, avg_rating,
           prior_avg_rating, rating_delta, pct_top, pct_bottom,
           high_flight_risk, promotion_ready, avg_training_hours,
           training_target_hours, calibration_pct, rating_vs_utilization_r,
           statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


# ---------------------------------------------------------------------------
# Product tickets
# ---------------------------------------------------------------------------
def build_tickets(con, org_id, run_id, as_of):
    con.execute("DELETE FROM product_ticket_summary WHERE org_id=?", (org_id,))
    today = dt.date.fromisoformat(as_of)
    cut90 = (today - dt.timedelta(days=90)).isoformat()
    cost_rate = one(con, """SELECT SUM(hours*cost_rate)/SUM(hours)
                              FROM time_entry WHERE org_id=? AND hours>0""",
                    (org_id,), default=0) or 0

    out = []
    for p in rows(con, """SELECT DISTINCT product FROM product_ticket
                           WHERE org_id=? ORDER BY product""", (org_id,)):
        product = p["product"]
        ts = rows(con, """SELECT * FROM product_ticket
                           WHERE org_id=? AND product=?""", (org_id, product))
        open_states = ("open", "in_progress")
        open_bugs = [t for t in ts if t["ticket_type"] == "bug"
                     and t["status"] in open_states]
        open_enh = [t for t in ts if t["ticket_type"] == "enhancement"
                    and t["status"] in open_states]
        blockers = [t for t in open_bugs if t["severity"] == "blocker"]
        raised90 = [t for t in ts if t["raised_on"] >= cut90]
        resolved90 = [t for t in ts if t["resolved_on"]
                      and t["resolved_on"] >= cut90]
        ttrs = [t["time_to_resolve_days"] for t in ts
                if t["time_to_resolve_days"]]
        oldest = max((t["age_days"] for t in open_bugs + open_enh),
                     default=None)
        regressions = sum(1 for t in ts if t["is_regression"])
        cust = sum(1 for t in ts if t["raised_by"] == "customer")
        # Delivery time spent on product defects: this is the number that
        # turns a product backlog into a services margin question.
        effort = sum(t["effort_hours"] or 0 for t in ts
                     if t["ticket_type"] == "bug")
        affected = len({t["project_id"] for t in ts if t["project_id"]})
        blocking = sum(1 for t in ts if t["blocks_go_live"])
        net = len(raised90) - len(resolved90)
        verdict = ("growing" if net > T["backlog_growing_net"] else
                   "shrinking" if net < -T["backlog_growing_net"] else "stable")

        statement = (
            f"{product}: {len(open_bugs)} open defects"
            + (f", {len(blockers)} of them blockers" if blockers else "")
            + f", and {len(open_enh)} open enhancement requests. ")
        statement += (
            f"Over the last 90 days {len(raised90)} were raised against "
            f"{len(resolved90)} resolved, so the backlog is {verdict}")
        if verdict == "growing":
            statement += (f" by {net} tickets a quarter. At that rate the "
                          f"backlog does not get worked down by adding "
                          f"capacity to delivery; it needs product "
                          f"engineering.")
        else:
            statement += "."
        if ttrs:
            statement += (f" Median time to resolve is "
                          f"{median(ttrs):,.0f} days, ninetieth percentile "
                          f"{pctile(ttrs, 0.9):,.0f}.")
        if effort > 0:
            statement += (f" Delivery has absorbed {effort:,.0f} hours on "
                          f"product defects, roughly "
                          f"{effort * cost_rate:,.0f} of cost, across "
                          f"{affected} engagements. That cost sits in "
                          f"services margin and originates in the product.")
        if cust and ts:
            statement += (f" {cust / len(ts) * 100:,.0f}% were raised by "
                          f"customers rather than found internally, which is "
                          f"the share that reaches CSAT.")

        out.append((org_id, run_id, product, len(open_bugs), len(blockers),
                    len(open_enh), len(raised90), len(resolved90), net,
                    q2(median(ttrs)), q2(pctile(ttrs, 0.9)), q2(oldest),
                    q2(regressions / len(ts) * 100) if ts else None,
                    q2(cust / len(ts) * 100) if ts else None,
                    q2(effort), q2(effort * cost_rate), affected, blocking,
                    verdict, statement))

    con.executemany("""
        INSERT INTO product_ticket_summary
          (org_id, run_id, product, open_bugs, open_blockers,
           open_enhancements, raised_last_90, resolved_last_90, net_flow_90,
           median_ttr_days, p90_ttr_days, oldest_open_days, regression_pct,
           customer_raised_pct, delivery_effort_hours, delivery_effort_cost,
           projects_affected, blocking_go_live, backlog_verdict, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


# ---------------------------------------------------------------------------
# The red and amber register
# ---------------------------------------------------------------------------
def build_rag(con, org_id, run_id, as_of):
    """
    One row per at-risk engagement, with the reasons attached to it.

    The reasons live in a child table keyed to the register row rather than
    being recomputed wherever they are displayed. That is the whole design
    decision: a portfolio review where the list of red projects and the
    explanation of why they are red are generated by two different queries
    will, sooner or later, show a project as red with no reason and a reason
    with no project. Attaching them means the list cannot drift from its own
    explanation.

    Both the reported colour and the computed one are stored, and where they
    disagree by two bands the register says so and shows the override note,
    or records that there wasn't one. Neither Certinia nor Rocketlane
    computes a health score at all, so on those products the reported colour
    is the only colour and this comparison cannot be made.
    """
    con.execute("""DELETE FROM rag_reason WHERE rag_id IN
                   (SELECT rag_id FROM rag_register WHERE org_id=?)""",
                (org_id,))
    con.execute("DELETE FROM rag_register WHERE org_id=?", (org_id,))
    order = {"Green": 0, "Yellow": 1, "Red": 2}
    today = dt.date.fromisoformat(as_of)

    candidates = rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.product,
               p.project_health, p.project_status, p.contract_value,
               p.budget_hours, p.planned_end_date, p.target_margin_pct,
               c.customer_name,
               e.employee_name AS pm_name,
               rs.predicted_health, rs.failure_probability_pct,
               mp.forecast_margin_pct, mp.predicted_margin_pct,
               mp.predicted_hours_to_complete,
               COALESCE(te.hours,0) AS booked,
               COALESCE(esc.n,0) AS escalations
        FROM project p
        LEFT JOIN customer c ON c.customer_id=p.customer_id
        LEFT JOIN employee e ON e.employee_id=p.project_manager_id
        LEFT JOIN (SELECT project_id, MAX(risk_score_id) id
                     FROM project_risk_score GROUP BY project_id) lr
               ON lr.project_id=p.project_id
        LEFT JOIN project_risk_score rs ON rs.risk_score_id=lr.id
        LEFT JOIN (SELECT project_id, MAX(prediction_id) id
                     FROM project_margin_prediction GROUP BY project_id) lm
               ON lm.project_id=p.project_id
        LEFT JOIN project_margin_prediction mp ON mp.prediction_id=lm.id
        LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                    WHERE approval_status='Approved' GROUP BY project_id) te
               ON te.project_id=p.project_id
        LEFT JOIN (SELECT project_id, COUNT(*) n FROM risk_issue
                    WHERE entry_type='Issue' AND status<>'Closed'
                      AND is_customer_raised=1 GROUP BY project_id) esc
               ON esc.project_id=p.project_id
        WHERE p.org_id=? AND p.project_status IN ('In Flight','On Hold')""",
        (org_id,))

    n = 0
    for p in candidates:
        reported = p["project_health"]
        computed = p["predicted_health"]
        prob = p["failure_probability_pct"] or 0
        # An engagement belongs in the register if either the PM says so or
        # the model does. Taking only the PM's word makes the register a
        # restatement of the PM's opinion; taking only the model's makes it
        # something the PM will not recognise.
        in_register = (reported in ("Red", "Yellow")
                       or (computed in ("Red", "Yellow"))
                       or prob >= T["rag_amber_prob"])
        if not in_register:
            continue

        override = rows(con, """
            SELECT change_reason, changed_by, changed_at, old_value, new_value
            FROM audit_log WHERE entity_table='project' AND entity_pk=?
              AND field_name='project_health' AND is_override=1
            ORDER BY changed_at DESC LIMIT 1""", (p["project_id"],))
        override = override[0] if override else None
        divergence = (abs(order.get(reported, 0) - order.get(computed, 0))
                      if computed else 0)

        consumed = (p["booked"] / p["budget_hours"] * 100
                    if p["budget_hours"] else 0)
        slip = 0.0
        if p["planned_end_date"]:
            pe = dt.date.fromisoformat(p["planned_end_date"])
            if pe < today:
                slip = (today - pe).days
        margin_gap = None
        if p["forecast_margin_pct"] is not None and p["target_margin_pct"]:
            margin_gap = p["target_margin_pct"] - p["forecast_margin_pct"]
        hours_over = max(0.0, p["booked"] - (p["budget_hours"] or 0))
        # The lowest score on the engagement, not the latest one.
        #
        # Taking the most recent response put a 4.5 and the words "support
        # tickets were answered by people who knew our configuration" on a red
        # engagement, because the last survey happened to be a positive one at
        # a point before the trouble. That is a true fact and a useless piece
        # of evidence, and beside a red flag it reads as a broken screen. What
        # a reviewer needs is the worst thing the customer has said, with the
        # date so they can see when it was said.
        csat = rows(con, """SELECT score, comment, responded_on, survey_point
                             FROM csat_response
                             WHERE project_id=? AND comment IS NOT NULL
                             ORDER BY score ASC, responded_on DESC
                             LIMIT 1""", (p["project_id"],))
        csat = csat[0] if csat else None

        # ---- the reasons, in order of what a reviewer should act on ----
        reasons = []
        # A margin percentage on an engagement that has barely started is
        # arithmetically valid and worthless: divide a full-project cost
        # forecast by a fortnight of revenue and any number is available. A
        # figure past the bound is reported as what it actually is, which is
        # cost incurred ahead of revenue, not a margin.
        early = (p["forecast_margin_pct"] is not None
                 and p["forecast_margin_pct"] < -100)
        if early:
            reasons.append((
                "early_burn", "Cost incurred ahead of revenue",
                f"{p['booked']:,.0f} hours booked against a contract of "
                f"{p['contract_value'] or 0:,.0f} with recognition barely "
                f"started. No margin figure is meaningful this early, so "
                f"none is shown; what is worth watching is whether the "
                f"revenue follows the cost within the next period.",
                q2(p["booked"]), "hours", None, "medium"))
        elif margin_gap is not None and margin_gap >= T["margin_gap_pts"]:
            reasons.append((
                "margin", "Forecast margin below target",
                f"Forecast margin is {p['forecast_margin_pct']:,.1f}% against "
                f"a target of {p['target_margin_pct']:,.1f}%, a gap of "
                f"{margin_gap:,.1f} points. On this contract value that is "
                f"about {(p['contract_value'] or 0) * margin_gap / 100:,.0f} "
                f"of margin.",
                q2(margin_gap), "points", p["target_margin_pct"],
                "critical" if margin_gap >= 15 else "high"))
        if consumed > 100:
            reasons.append((
                "budget", "Budget hours exhausted",
                f"{consumed:,.0f}% of budget hours consumed, "
                f"{hours_over:,.0f} hours beyond plan, with "
                f"{p['predicted_hours_to_complete'] or 0:,.0f} still "
                f"forecast to complete.",
                q2(consumed), "percent", 100,
                "critical" if consumed > 120 else "high"))
        elif consumed > 85:
            reasons.append((
                "budget", "Budget hours nearly exhausted",
                f"{consumed:,.0f}% of budget hours consumed with "
                f"{p['predicted_hours_to_complete'] or 0:,.0f} hours still "
                f"forecast to complete.",
                q2(consumed), "percent", 85, "medium"))
        if slip > 0:
            reasons.append((
                "schedule", "Past the planned end date",
                f"{slip:,.0f} days past a planned end of "
                f"{p['planned_end_date']} with no revised date recorded on "
                f"the plan.",
                q2(slip), "days", 0,
                "high" if slip > 30 else "medium"))
        if p["escalations"]:
            n_esc = p["escalations"]
            reasons.append((
                "escalation", "Open customer-raised issues",
                f"{n_esc} customer-raised "
                f"{'issue is' if n_esc == 1 else 'issues are'} open. These "
                f"are the ones that reach the sponsor, whatever the status "
                f"report says.",
                n_esc, "issues", 0, "high" if n_esc > 2 else "medium"))
        if csat and csat["score"] <= T["detractor_score"]:
            reasons.append((
                "csat", "Customer satisfaction at detractor level",
                f"Scored {csat['score']:,.1f} of 5 at "
                f"{csat['survey_point'].replace('_', ' ')} on "
                f"{csat['responded_on']}."
                + (f" Verbatim: “{csat['comment']}”"
                   if csat["comment"] else ""),
                q2(csat["score"]), "score", T["detractor_score"], "high"))
        defects = one(con, """SELECT COUNT(*) FROM product_ticket
                               WHERE project_id=? AND ticket_type='bug'
                                 AND status IN ('open','in_progress')
                                 AND severity IN ('blocker','high')""",
                      (p["project_id"],))
        if defects:
            reasons.append((
                "product", "Open high-severity product defects",
                f"{defects} blocker or high severity defects open against "
                f"this engagement. The delivery team carries the workaround "
                f"effort and the customer carries the impression.",
                defects, "defects", 0,
                "high" if defects > 2 else "medium"))
        if divergence >= 2:
            # Three different situations, and conflating them is unfair to
            # the project manager. The colour may have been overridden with a
            # reason, overridden without one, or never revisited at all. Only
            # the middle case is a governance failure; the last is usually
            # just a colour nobody has looked at since the model moved.
            if override and override["change_reason"]:
                note = (f" The override was recorded by "
                        f"{override['changed_by']} with the note: “"
                        f"{override['change_reason']}”")
            elif override:
                note = (f" {override['changed_by']} overrode the colour on "
                        f"{override['changed_at'][:10]} without recording a "
                        f"reason, so there is nothing to weigh the model "
                        f"against.")
            else:
                note = (" No override was recorded, so the reported colour "
                        "has simply not been revisited since the model moved. "
                        "That is a review cadence problem rather than a "
                        "judgement one.")
            reasons.append((
                "governance", "Reported health two bands from the model",
                f"Reported {reported} against a computed {computed} at a "
                f"{prob:,.0f}% failure probability." + note,
                divergence, "bands", 1,
                "critical" if override else "high"))
        if not reasons:
            reasons.append((
                "model", "Elevated failure probability with no single driver",
                f"The model puts failure probability at {prob:,.0f}% without "
                f"any one measure breaching its threshold. That pattern is "
                f"usually several small things at once, and it is worth a "
                f"conversation rather than an intervention.",
                q2(prob), "percent", T["rag_amber_prob"], "medium"))

        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        reasons.sort(key=lambda r: sev_rank[r[6]])
        primary = reasons[0]

        revenue_at_risk = None
        if margin_gap is not None and margin_gap > 0:
            revenue_at_risk = q2((p["contract_value"] or 0) * margin_gap / 100)

        action, owner, by = recovery_for(primary[0], p, slip, consumed, today)
        statement = (
            f"{p['project_code']} {p['project_name']} is reported "
            f"{reported}"
            + (f" against a computed {computed}" if computed
               and computed != reported else "")
            + f". {primary[2]} "
            + (f"There {'are' if len(reasons) > 2 else 'is'} "
               f"{len(reasons) - 1} further "
               f"{'reasons' if len(reasons) > 2 else 'reason'} on the record. "
               if len(reasons) > 1 else "")
            + f"{action}")

        cur = con.execute("""
            INSERT INTO rag_register
              (org_id, run_id, project_id, reported_health, computed_health,
               health_score, failure_probability_pct, is_overridden,
               override_note, divergence_bands, first_amber_on, first_red_on,
               weeks_in_state, primary_reason, reason_detail,
               revenue_at_risk, margin_gap_pts, slip_days, hours_over,
               open_escalations, latest_csat, latest_csat_comment,
               recovery_action, recovery_owner, recovery_by, statement)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (org_id, run_id, p["project_id"], reported, computed, None, prob,
             1 if override else 0,
             override["change_reason"] if override else None,
             divergence, None, None, None, primary[1], primary[2],
             revenue_at_risk, q2(margin_gap), q2(slip), q2(hours_over),
             p["escalations"], q2(csat["score"]) if csat else None,
             csat["comment"] if csat else None,
             action, owner, by, statement))
        rag_id = cur.lastrowid
        con.executemany("""
            INSERT INTO rag_reason
              (rag_id, rank, reason_code, reason_label, statement,
               metric_value, metric_unit, threshold_value, severity)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            [(rag_id, i + 1, r[0], r[1], r[2], r[3], r[4], r[5], r[6])
             for i, r in enumerate(reasons)])
        n += 1
    return n


def recovery_for(code, p, slip, consumed, today):
    """
    What to do, who owns it, and by when.

    A register entry that stops at the diagnosis gets read and not acted on.
    The owner matters as much as the action: "commercial conversation with
    the sponsor" belongs to an account owner, not to the project manager who
    is already inside the problem.
    """
    by = (today + dt.timedelta(days=14)).isoformat()
    if code == "margin":
        return ("Price the remaining scope before any more of it is "
                "delivered. If the gap is change orders that were absorbed, "
                "it is a commercial conversation with the sponsor, not a "
                "delivery one.", "Account owner with practice lead", by)
    if code == "budget":
        return ("Re-forecast to completion and take the revised number to "
                "the sponsor. Continuing to burn against an exhausted budget "
                "converts a difficult conversation into an unwinnable one.",
                "Project manager with practice lead", by)
    if code == "schedule":
        return (f"Set a revised end date and baseline it. The plan has been "
                f"wrong for {slip:,.0f} days, and every report issued against "
                f"it since has been wrong too.", "Project manager",
                (today + dt.timedelta(days=7)).isoformat())
    if code == "escalation":
        return ("Close out the customer-raised issues or agree in writing "
                "that they are out of scope. An open escalation with no owner "
                "becomes the reference conversation.",
                "Project manager with account owner", by)
    if code == "csat":
        return ("Sponsor call, led by someone who is not the project "
                "manager. A detractor score is recoverable; a detractor score "
                "nobody acknowledged is not.", "Practice lead",
                (today + dt.timedelta(days=7)).isoformat())
    if code == "product":
        return ("Get a fix commitment with a date from product, and tell the "
                "customer what the workaround is until then. Delivery cannot "
                "absorb a product defect indefinitely and should not try.",
                "Practice lead with product owner", by)
    if code == "governance":
        return ("Reconcile the reported colour with the model at the next "
                "portfolio review, and record the reason either way. The "
                "disagreement is the useful part; leaving it unexplained is "
                "not.", "Practice lead", by)
    return ("Review at the next portfolio meeting. Nothing here needs an "
            "intervention this week.", "Practice lead",
            (today + dt.timedelta(days=28)).isoformat())


# ---------------------------------------------------------------------------
def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    as_of = one(con, "SELECT MAX(as_of_date) FROM intelligence_run", (),
                default=dt.date.today().isoformat())
    for org_id, code in con.execute(
            "SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        run_id = one(con, "SELECT MAX(run_id) FROM intelligence_run "
                          "WHERE org_id=?", (org_id,), default=None)
        n_csat = build_csat(con, org_id, run_id, as_of)
        n_phase = build_phases(con, org_id, run_id, as_of)
        n_hc = build_hypercare(con, org_id, run_id, as_of)
        n_rate = build_rates(con, org_id, run_id, as_of)
        n_perf = build_performance(con, org_id, run_id, as_of)
        n_tick = build_tickets(con, org_id, run_id, as_of)
        n_rag = build_rag(con, org_id, run_id, as_of)
        con.commit()
        print(f"{code}: csat {n_csat}, phases {n_phase}, hypercare {n_hc}, "
              f"rates {n_rate}, performance {n_perf}, tickets {n_tick}, "
              f"rag {n_rag}")
    con.close()


if __name__ == "__main__":
    main()
