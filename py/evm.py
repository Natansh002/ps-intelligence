"""
Earned value: cost and schedule performance per engagement.

CPI and SPI separate two questions that consumption alone conflates. An
engagement at 60% consumed and 60% complete is fine. One at 60% consumed and
40% complete is over cost. One at 40% consumed and 40% complete against a plan
that expected 60% by now is on cost and behind schedule. Only the pair
distinguishes them, and a portfolio review that reports consumption without
them cannot tell a cost problem from a schedule problem.

    PV   planned value      budget cost x planned complete at the as-of date
    EV   earned value       budget cost x earned complete
    AC   actual cost        cost incurred to date
    CPI  EV / AC            above 1 is under cost
    SPI  EV / PV            above 1 is ahead of schedule
    TCPI (BAC - EV) / (BAC - AC)   the efficiency the remainder now needs

One definitional choice decides whether any of this is worth reading. Percent
complete here is earned plan: the share of planned task hours whose tasks are
actually complete. It is not a typed-in number, and it is emphatically not
hours spent over estimate at completion, which is how at least one major PSA
product defines it. Under that definition EV = AC by construction, CPI is
identically 1.00, and the metric cannot report a cost problem no matter how
much money the engagement is losing. Publishing a CPI that cannot go below 1
is worse than publishing none.

Planned complete is derived from the plan's own dates rather than from elapsed
calendar time, so an engagement whose plan front-loads the work is measured
against that plan and not against a straight line.
"""
import datetime as dt
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

T = {
    "cpi_warn": 0.95,       # below this, cost efficiency is a finding
    "cpi_bad": 0.85,
    "spi_warn": 0.95,
    "spi_bad": 0.85,
    "tcpi_unreachable": 1.10,   # a remainder needing this much is not credible
    "band": 0.03,           # inside this of 1.00 counts as on plan
    # Materiality floors. Below these an index is arithmetic rather than
    # information, and including such a row in a roll-up moves the portfolio
    # figure by an amount that has nothing to do with delivery.
    "min_planned_pct": 10.0,    # too early for the schedule index to mean much
    "min_earned_pct": 5.0,
    "min_ac_share": 0.05,       # too little cost booked for CPI to be stable
}


def reportability(planned_pct, earned_pct, ac, bac):
    """
    Whether this engagement's indices belong in a roll-up.

    Kept as a flag on the row rather than a filter on the query, so the
    portfolio view can say how many engagements it set aside and why. A view
    that silently drops rows is one nobody can reconcile.
    """
    if planned_pct < T["min_planned_pct"]:
        return 0, (f"only {planned_pct:,.0f}% of the plan is due by now, so the "
                   f"schedule index has almost no denominator")
    if earned_pct < T["min_earned_pct"]:
        return 0, (f"{earned_pct:,.0f}% earned, which is inside the noise of a "
                   f"newly mobilised engagement")
    if bac > 0 and ac < bac * T["min_ac_share"]:
        return 0, (f"{ac / bac * 100:,.0f}% of budget cost booked, too little "
                   f"for a cost index to be stable")
    return 1, None


# Column positions in the project_evm insert tuple, named so the roll-up does
# not read as a sequence of magic numbers.
IX_PROJECT, IX_BAC, IX_PV, IX_EV, IX_AC = 2, 4, 7, 8, 9
IX_CPI, IX_SPI, IX_VAC = 12, 13, 17
IX_COST_BAND, IX_SCHED_BAND, IX_REPORTABLE = 18, 19, 21


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, args=(), default=0):
    r = con.execute(sql, args).fetchone()
    return default if r is None or r[0] is None else r[0]


def q2(x):
    return None if x is None else round(float(x), 2)


def q4(x):
    return None if x is None else round(float(x), 4)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


# ---------------------------------------------------------------------------
def project_evm(con, org_id, as_of):
    """
    One row per live engagement.

    Live only. On a completed engagement CPI and SPI are history: the cost
    variance is already in the margin and the schedule variance is already in
    the delivery date, both of which are reported elsewhere and more directly.
    The value of an index is that it is a leading indicator, and there is
    nothing left to lead.
    """
    today = dt.date.fromisoformat(as_of)
    projs = rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.practice_id,
               p.product, p.billing_model, p.budget_cost, p.budget_hours,
               p.start_date, p.planned_end_date,
               pr.practice_name,
               e.employee_name AS pm_name,
               COALESCE(ac.cost, 0)  AS ac,
               COALESCE(tk.planned, 0) AS planned_hours,
               COALESCE(tk.earned, 0)  AS earned_hours
          FROM project p
          LEFT JOIN practice pr ON pr.practice_id = p.practice_id
          LEFT JOIN employee e ON e.employee_id = p.project_manager_id
          -- Labour only, deliberately. Budget at completion here is
          -- budget hours times a planned cost rate, which is a labour
          -- budget with no expense allowance in it. Putting expenses into
          -- actual cost and not into the budget makes every index read
          -- about three points worse than the delivery it is measuring,
          -- which is the sort of definitional mismatch that gets a metric
          -- argued about instead of acted on. Expenses are reported against
          -- margin, where the revenue side carries them too.
          LEFT JOIN (SELECT project_id, SUM(labor_cost) cost
                       FROM project_financial_month GROUP BY project_id) ac
                 ON ac.project_id = p.project_id
          LEFT JOIN (SELECT project_id,
                            SUM(planned_hours) planned,
                            SUM(planned_hours * COALESCE(percent_complete,0)
                                / 100.0) earned
                       FROM project_task
                      WHERE plan_version_id IN
                            (SELECT plan_version_id FROM project_plan_version
                              WHERE is_current = 1)
                      GROUP BY project_id) tk
                 ON tk.project_id = p.project_id
         WHERE p.org_id = ? AND p.project_status = 'In Flight'
           AND p.budget_cost > 0""", (org_id,))

    out = []
    for p in projs:
        if not p["planned_hours"]:
            continue
        bac = p["budget_cost"]
        earned_pct = p["earned_hours"] / p["planned_hours"] * 100

        # Where the plan says it should be by now: the share of planned task
        # hours whose planned end date has passed. Derived from the plan's own
        # dates so a front-loaded plan is measured against itself.
        planned_pct = one(con, """
            SELECT SUM(CASE WHEN planned_end <= ? THEN planned_hours ELSE 0 END)
                   * 100.0 / NULLIF(SUM(planned_hours), 0)
              FROM project_task
             WHERE project_id = ? AND plan_version_id IN
                   (SELECT plan_version_id FROM project_plan_version
                     WHERE is_current = 1)""",
            (as_of, p["project_id"]), default=None)
        if planned_pct is None:
            continue

        pv = bac * planned_pct / 100.0
        ev = bac * earned_pct / 100.0
        ac = p["ac"]
        cpi = (ev / ac) if ac > 0 else None
        spi = (ev / pv) if pv > 0 else None
        tcpi = ((bac - ev) / (bac - ac)) if (bac - ac) > 0 else None
        eac = (bac / cpi) if cpi else None

        band = T["band"]
        cost_band = ("on" if cpi is None or abs(cpi - 1) <= band
                     else "under" if cpi > 1 else "over")
        sched_band = ("on" if spi is None or abs(spi - 1) <= band
                      else "ahead" if spi > 1 else "behind")
        quadrant, statement = _read(p, cpi, spi, tcpi, bac, ev, ac, pv, eac,
                                    cost_band, sched_band)
        reportable, why = reportability(planned_pct, earned_pct, ac, bac)
        if not reportable:
            statement = (f"{p['project_code']}: indices not published — {why}. "
                         f"The engagement is still measured on consumption and "
                         f"margin like any other.")

        out.append((
            org_id, None, p["project_id"], as_of, q2(bac), q2(planned_pct),
            q2(earned_pct), q2(pv), q2(ev), q2(ac), q2(ev - ac), q2(ev - pv),
            q4(cpi), q4(spi), q4(cpi * spi) if cpi and spi else None,
            q4(tcpi), q2(eac), q2(bac - eac) if eac else None,
            cost_band, sched_band, quadrant, reportable, why, statement))
    return out


def _read(p, cpi, spi, tcpi, bac, ev, ac, pv, eac, cost_band, sched_band):
    """
    The pair, named and explained.

    Naming the quadrant matters because CPI and SPI are read together or not
    at all, and a table of two decimals invites people to read them one at a
    time. The four combinations imply genuinely different actions.
    """
    code = f"{cost_band}/{sched_band}"
    names = {
        "over/behind": ("over cost and behind schedule",
                        "The worst pair, and the only one where both levers "
                        "are already spent: the work done so far cost more "
                        "than it was worth and there is less of it than the "
                        "plan expected."),
        "over/on": ("over cost, on schedule",
                    "Delivering to the plan and paying too much for it. This "
                    "is usually seniority mix or rework rather than lateness."),
        "over/ahead": ("over cost, ahead of schedule",
                       "Buying the schedule with effort. Fine if that was the "
                       "decision and expensive if it was not."),
        "on/behind": ("on cost, behind schedule",
                      "Spending at the right rate on less work than planned. "
                      "Usually a dependency or an availability problem rather "
                      "than a delivery one."),
        "under/behind": ("under cost, behind schedule",
                         "Cheap and late, which normally means the team is "
                         "not on it: under-resourced rather than efficient."),
        "on/on": ("on cost and on schedule", "Nothing to act on."),
        "on/ahead": ("on cost, ahead of schedule", "Nothing to act on."),
        "under/on": ("under cost, on schedule",
                     "Ahead on efficiency. Worth understanding why, because "
                     "it is either a good team or an over-generous estimate."),
        "under/ahead": ("under cost and ahead of schedule",
                        "Worth understanding why. If the estimate was "
                        "generous, that is a pricing finding rather than a "
                        "delivery one."),
    }
    label, note = names.get(code, (code, ""))
    s = (f"{p['project_code']}: CPI {cpi:,.2f} and SPI {spi:,.2f} — "
         f"{label}. " if cpi and spi else
         f"{p['project_code']}: indices unavailable. ")
    s += note + " "
    if cpi and cpi < 1:
        s += (f"{ev:,.0f} of value earned for {ac:,.0f} spent, a cost variance "
              f"of {ev - ac:,.0f}. ")
    if spi and spi < 1:
        s += (f"The plan expected {pv:,.0f} of value by now and "
              f"{ev:,.0f} has been earned. ")
    if tcpi and cpi and tcpi > T["tcpi_unreachable"] and tcpi > cpi:
        s += (f"Finishing on budget now needs the remaining work delivered at "
              f"{tcpi:,.2f} efficiency against the {cpi:,.2f} achieved so "
              f"far, which is not a plan so much as a hope. ")
    if eac and eac > bac * 1.05:
        s += (f"At the efficiency achieved to date the engagement lands at "
              f"{eac:,.0f} against a budget of {bac:,.0f}.")
    return label, s.strip()


def build_summary(con, org_id, run_id, as_of, evm):
    """Budget-weighted roll-ups, with the unweighted median beside them."""
    by_pid = {r[2]: r for r in evm}
    meta = {p["project_id"]: p for p in rows(con, """
        SELECT p.project_id, p.practice_id, pr.practice_name, p.product,
               p.billing_model, e.employee_name AS pm_name
          FROM project p
          LEFT JOIN practice pr ON pr.practice_id=p.practice_id
          LEFT JOIN employee e ON e.employee_id=p.project_manager_id
         WHERE p.org_id=?""", (org_id,))}

    scopes = [("org", None, "Whole portfolio", lambda m: True)]
    for key, label in sorted({(m["practice_id"], m["practice_name"])
                              for m in meta.values() if m["practice_id"]}):
        scopes.append(("practice", str(key), label,
                       lambda m, k=key: m["practice_id"] == k))
    for prod in sorted({m["product"] for m in meta.values() if m["product"]}):
        scopes.append(("product", prod, prod,
                       lambda m, p=prod: m["product"] == p))
    for bm in sorted({m["billing_model"] for m in meta.values()
                      if m["billing_model"]}):
        scopes.append(("billing_model", bm, bm,
                       lambda m, b=bm: m["billing_model"] == b))

    out = []
    for scope, key, label, keep in scopes:
        inscope = [r for pid, r in by_pid.items()
                   if pid in meta and keep(meta[pid])]
        # Roll up only the engagements far enough along to carry an index. The
        # count set aside is reported beside the figure so the view reconciles
        # back to the live book.
        sel = [r for r in inscope if r[IX_REPORTABLE]]
        excluded = len(inscope) - len(sel)
        if not sel:
            continue
        bac = sum(r[IX_BAC] for r in sel)
        pv = sum(r[IX_PV] for r in sel)
        ev = sum(r[IX_EV] for r in sel)
        ac = sum(r[IX_AC] for r in sel)
        cpis = [r[IX_CPI] for r in sel if r[IX_CPI]]
        spis = [r[IX_SPI] for r in sel if r[IX_SPI]]
        over = sum(1 for r in sel if r[IX_COST_BAND] == "over")
        behind = sum(1 for r in sel if r[IX_SCHED_BAND] == "behind")
        both = sum(1 for r in sel if r[IX_COST_BAND] == "over"
                   and r[IX_SCHED_BAND] == "behind")
        vac = sum((r[IX_VAC] or 0) for r in sel)
        w_cpi = (ev / ac) if ac else None
        w_spi = (ev / pv) if pv else None

        s = (f"{label}: {len(sel)} live engagements, {bac:,.0f} of budget. ")
        if excluded:
            s += (f"{excluded} more {'is' if excluded == 1 else 'are'} too "
                  f"early to carry an index and {'is' if excluded == 1 else 'are'} "
                  f"set aside. ")
        if w_cpi and w_spi:
            s += (f"Budget weighted CPI {w_cpi:,.2f} and SPI {w_spi:,.2f}. ")
            m_cpi, m_spi = median(cpis), median(spis)
            if m_cpi and abs(m_cpi - w_cpi) >= 0.05:
                s += (f"The median engagement sits at {m_cpi:,.2f}, so the "
                      f"weighted figure is being "
                      f"{'dragged down' if w_cpi < m_cpi else 'lifted'} by the "
                      f"larger engagements rather than describing the typical "
                      f"one. ")
        if both:
            s += (f"{both} are over cost and behind schedule at the same time, "
                  f"which is where the recoverable and the unrecoverable part "
                  f"company. ")
        elif over or behind:
            s += (f"{over} over cost, {behind} behind schedule. ")
        else:
            s += "Nothing outside the on-plan band. "
        if vac < -1000:
            s += (f"At the efficiency achieved to date the portfolio lands "
                  f"{abs(vac):,.0f} over budget.")
        out.append((org_id, run_id, scope, key, label, len(sel), excluded,
                    q2(bac), q2(pv), q2(ev), q2(ac), q4(w_cpi), q4(w_spi),
                    q4(median(cpis)), q4(median(spis)), over, behind, both,
                    q2(vac), s.strip()))
    return out


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    as_of = one(con, "SELECT MAX(as_of_date) FROM intelligence_run", (),
                default=dt.date.today().isoformat())
    for org_id, code in con.execute(
            "SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        run_id = one(con, "SELECT MAX(run_id) FROM intelligence_run "
                          "WHERE org_id=?", (org_id,), default=None)
        con.execute("DELETE FROM evm_summary WHERE org_id=?", (org_id,))
        con.execute("DELETE FROM project_evm WHERE org_id=?", (org_id,))
        evm = [(r[0], run_id) + r[2:] for r in project_evm(con, org_id, as_of)]
        con.executemany("""
            INSERT INTO project_evm
              (org_id, run_id, project_id, as_of_date, bac, planned_pct,
               earned_pct, pv, ev, ac, cv, sv, cpi, spi, csi, tcpi, eac_cpi,
               vac, cost_band, schedule_band, quadrant, is_reportable,
               exclusion_reason, statement)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", evm)
        summ = build_summary(con, org_id, run_id, as_of, evm)
        con.executemany("""
            INSERT INTO evm_summary
              (org_id, run_id, scope, scope_key, scope_label, projects,
               projects_excluded, bac, pv, ev, ac, cpi, spi, cpi_median,
               spi_median, projects_over_cost, projects_behind, projects_both,
               vac_total, statement)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", summ)
        con.commit()
        held = sum(1 for r in evm if not r[IX_REPORTABLE])
        print(f"{code}: {len(evm)} engagements scored "
              f"({held} too early to publish), {len(summ)} roll-ups")
    con.close()


if __name__ == "__main__":
    main()
