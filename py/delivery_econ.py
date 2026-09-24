"""
Billing against revenue recognition, and the economics of each billing model.

Two questions, and both are usually answered badly.

**Where do delivery and cash diverge?** Recognition follows delivery, invoicing
follows the contract, and the gap between them has two signs that mean opposite
things. Recognised ahead of invoiced is work delivered and not billed: an
unbilled receivable, and the older it is the harder it is to defend. Invoiced
ahead of recognised is billing ahead of delivery: deferred revenue, a
commitment rather than an asset. A single netted "gap" figure hides which one
you are holding, and a netted zero can mean a large receivable on one
engagement cancelling a large deferral on another.

**Which billing model actually makes money?** The average margin is the least
useful number available. The models differ in who carries the overrun risk: on
time and materials the customer carries it, so the margin is bounded and no
engagement loses money; on fixed fee the delivery organisation carries it, so
outcomes fan out in both directions. A model with a higher mean and triple the
spread is not simply better, it is a different bet. So this reports the
distribution, the share of engagements that lost money, the effort overrun that
decides a fixed-fee outcome, and the mix of work each model is actually sold
on, because the two populations are not the same and a bare comparison of
averages is confounded by that.
"""
import datetime as dt
import math
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

# Thresholds, named and in one place.
T = {
    "material_gap": 1000.0,     # a gap worth counting as a position
    "aged_days": 60,            # an unbilled month past this is ageing
    "stale_days": 90,           # and past this it is a write-off risk
    "min_sample": 8,            # below this, report the sample not the statistic
    "wide_spread_pts": 12.0,    # a margin standard deviation worth calling wide
}

# Who carries the risk of the work taking longer than estimated. This is the
# single fact that explains most of the difference between the models, so it is
# stated on every row rather than left for the reader to infer.
RISK_CARRIER = {
    "Time and Materials": ("customer",
                           "The customer pays for the hours delivered, so an "
                           "overrun costs them money and costs the delivery "
                           "organisation only its reputation."),
    "Capped T&M": ("shared",
                   "The customer pays for hours until the cap, and every hour "
                   "past it is delivered free. The risk is the customer's up to "
                   "a point and entirely the delivery organisation's after it."),
    "Fixed Fee": ("delivery",
                  "The fee is the fee. Every hour beyond the estimate comes out "
                  "of margin, which is why a fixed-fee book lives or dies on "
                  "estimating and scope control."),
    "Milestone": ("delivery",
                  "Priced like fixed fee and invoiced on events, so the delivery "
                  "organisation carries the overrun and the cash timing depends "
                  "on acceptance it does not control."),
    "Retainer": ("shared",
                 "A fixed monthly fee against a broadly agreed scope. Overrun "
                 "shows up as scope creep rather than as an invoice, which "
                 "makes it the easiest model to lose money on quietly."),
}


# ---------------------------------------------------------------------------
def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, args=(), default=0):
    r = con.execute(sql, args).fetchone()
    return default if r is None or r[0] is None else r[0]


def q2(x):
    return None if x is None else round(float(x), 2)


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


def stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def months_between(a, b):
    """Whole months from period string a to period string b."""
    if not a or not b:
        return None
    ay, am = int(a[:4]), int(a[5:7])
    by, bm = int(b[:4]), int(b[5:7])
    return (by - ay) * 12 + (bm - am)


# ---------------------------------------------------------------------------
# Billing against recognition
# ---------------------------------------------------------------------------
def project_positions(con, org_id, as_of):
    """
    One row per engagement: what it recognised, what it invoiced, and how long
    the unbilled part has been sitting there.

    Ageing is measured from the last month that actually carried an invoice,
    not from the start of the engagement. An engagement invoicing steadily has
    a small young gap by design; one that stopped invoicing six months ago has
    the same gap and a completely different meaning.
    """
    return rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.billing_model,
               p.project_status, p.contract_value, p.planned_end_date,
               c.customer_name,
               SUM(f.recognized_revenue) AS recognised,
               SUM(f.invoiced_revenue)   AS invoiced,
               MAX(CASE WHEN f.invoiced_revenue > 0 THEN f.period_month END)
                 AS last_invoice_month,
               MAX(f.period_month) AS last_month
          FROM project p
          JOIN project_financial_month f USING(project_id)
          LEFT JOIN customer c ON c.customer_id = p.customer_id
         WHERE p.org_id = ?
         GROUP BY p.project_id""", (org_id,))


def build_billing(con, org_id, run_id, as_of):
    con.execute("DELETE FROM billing_exception WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM billing_month WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM billing_position WHERE org_id=?", (org_id,))

    projs = project_positions(con, org_id, as_of)
    as_of_month = as_of[:7]

    for p in projs:
        p["gap"] = (p["recognised"] or 0) - (p["invoiced"] or 0)
        p["age_months"] = months_between(p["last_invoice_month"], as_of_month)

    # ---- the month series -------------------------------------------------
    monthly = rows(con, """
        SELECT f.period_month,
               SUM(f.recognized_revenue) AS recognised,
               SUM(f.invoiced_revenue)   AS invoiced
          FROM project_financial_month f JOIN project p USING(project_id)
         WHERE p.org_id=? GROUP BY f.period_month ORDER BY f.period_month""",
        (org_id,))
    cum_r = cum_i = 0.0
    month_rows = []
    for m in monthly:
        cum_r += m["recognised"] or 0
        cum_i += m["invoiced"] or 0
        month_rows.append((
            org_id, run_id, m["period_month"], q2(m["recognised"]),
            q2(m["invoiced"]), q2(cum_r), q2(cum_i), q2(cum_r - cum_i),
            q2(cum_i / cum_r * 100) if cum_r else None))
    con.executemany("""
        INSERT INTO billing_month
          (org_id, run_id, period_month, recognised, invoiced, cum_recognised,
           cum_invoiced, cum_gap, billed_pct)
        VALUES (?,?,?,?,?,?,?,?,?)""", month_rows)

    # ---- the positions ----------------------------------------------------
    # A blank billing model or status is a real state on imported data, and it
    # gets its own labelled scope rather than a null one. Dropping those
    # engagements would make the positions stop summing to the book, which is
    # the one thing a billing view has to do.
    UNSET = "Not recorded"
    scopes = [("org", None, "Whole organisation", lambda p: True)]
    for bm in sorted({p["billing_model"] or UNSET for p in projs}):
        scopes.append(("billing_model", bm, bm,
                       lambda p, bm=bm: (p["billing_model"] or UNSET) == bm))
    for st in sorted({p["project_status"] or UNSET for p in projs}):
        scopes.append(("status", st, st,
                       lambda p, st=st: (p["project_status"] or UNSET) == st))

    out = []
    for scope, key, label, keep in scopes:
        sel = [p for p in projs if keep(p)]
        if not sel:
            continue
        rec = sum(p["recognised"] or 0 for p in sel)
        inv = sum(p["invoiced"] or 0 for p in sel)
        unbilled = sum(p["gap"] for p in sel if p["gap"] > T["material_gap"])
        deferred = sum(-p["gap"] for p in sel if p["gap"] < -T["material_gap"])
        n_unb = sum(1 for p in sel if p["gap"] > T["material_gap"])
        n_def = sum(1 for p in sel if p["gap"] < -T["material_gap"])
        aged60 = sum(p["gap"] for p in sel
                     if p["gap"] > T["material_gap"]
                     and (p["age_months"] or 0) >= 2)
        aged90 = sum(p["gap"] for p in sel
                     if p["gap"] > T["material_gap"]
                     and (p["age_months"] or 0) >= 3)
        oldest = max((p["age_months"] or 0) for p in sel
                     if p["gap"] > T["material_gap"]) if n_unb else None

        statement = _billing_statement(label, scope, sel, rec, inv, unbilled,
                                       deferred, n_unb, n_def, aged90, oldest)
        out.append((org_id, run_id, scope, key, label, len(sel), q2(rec),
                    q2(inv), q2(unbilled), q2(deferred), q2(unbilled - deferred),
                    n_unb, n_def, q2(inv / rec * 100) if rec else None,
                    q2(aged60), q2(aged90),
                    q2(oldest * 30.4) if oldest is not None else None,
                    statement))
    con.executemany("""
        INSERT INTO billing_position
          (org_id, run_id, scope, scope_key, scope_label, projects, recognised,
           invoiced, unbilled, deferred, net_position, projects_unbilled,
           projects_deferred, billed_pct, unbilled_over_60, unbilled_over_90,
           oldest_unbilled_days, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)

    # ---- the exceptions ---------------------------------------------------
    exc = []
    for direction, sign in (("unbilled", 1), ("deferred", -1)):
        cands = [p for p in projs if p["gap"] * sign > T["material_gap"]]
        cands.sort(key=lambda p: -p["gap"] * sign)
        for p in cands[:12]:
            amount = p["gap"] * sign
            cause, action = _billing_cause(direction, p)
            statement = (
                f"{p['project_code']} has recognised "
                f"{p['recognised'] or 0:,.0f} against "
                f"{p['invoiced'] or 0:,.0f} invoiced, "
                + (f"leaving {amount:,.0f} delivered and unbilled"
                   if direction == "unbilled" else
                   f"so {amount:,.0f} is billed ahead of delivery")
                + (f". Nothing has been invoiced for "
                   f"{p['age_months']} months." if direction == "unbilled"
                   and (p["age_months"] or 0) >= 2 else ".")
                + f" {cause}")
            exc.append((
                org_id, run_id, p["project_id"], direction,
                q2(p["recognised"]), q2(p["invoiced"]), q2(amount),
                q2(amount / p["contract_value"] * 100)
                if p["contract_value"] else None,
                p["last_invoice_month"],
                p["age_months"], p["project_status"], p["billing_model"],
                cause, action, statement))
    con.executemany("""
        INSERT INTO billing_exception
          (org_id, run_id, project_id, direction, recognised, invoiced, amount,
           pct_of_contract, last_invoice_month, months_since_invoice,
           project_status, billing_model, cause, action, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", exc)
    return len(out), len(month_rows), len(exc)


def _billing_cause(direction, p):
    """
    What the data says is behind the gap, and what to do about it.

    Named per billing model because the same gap means different things: a
    month of unbilled time and materials is the billing cycle working as
    designed, and a month of unbilled fixed fee means an invoice nobody
    raised.
    """
    bm = p["billing_model"]
    age = p["age_months"] or 0
    if direction == "unbilled":
        if p["project_status"] in ("Complete", "Cancelled"):
            return ("The engagement is closed, so this is either an invoice "
                    "nobody raised or a write-off nobody recorded.",
                    "Decide which it is and record it. An unbilled balance on "
                    "a closed engagement is not a receivable, it is an "
                    "unrecognised loss sitting in the ledger.")
        if bm in ("Time and Materials", "Capped T&M"):
            if age >= 2:
                return (f"Time and materials bills in arrears, so one cycle "
                        f"unbilled is normal and {age} months is not.",
                        "Run the billing cycle on this engagement and find out "
                        "why it was skipped. On T&M the invoice is the only "
                        "thing converting delivered hours into cash.")
            return ("Time and materials billed in arrears, within the normal "
                    "cycle.",
                    "No action. This is the billing lag working as designed.")
        if bm == "Milestone":
            return ("Milestone billing waits on acceptance, so the gap is "
                    "either a milestone not yet signed off or one signed off "
                    "and not released to billing.",
                    "Check which. Where acceptance is the blocker, chase "
                    "acceptance rather than the invoice.")
        return ("Fixed-fee work bills to a schedule, so a gap this size means "
                "a scheduled invoice was not raised.",
                "Raise it. The schedule exists precisely so that billing does "
                "not depend on anyone remembering.")
    # deferred
    if bm in ("Milestone", "Fixed Fee", "Retainer"):
        return ("Billed ahead of delivery, which the schedule allows and the "
                "revenue standard does not let you recognise.",
                "No action beyond keeping it visible. This is deferred "
                "revenue: a commitment to deliver, not an asset.")
    return ("Invoiced ahead of the hours delivered on a time-and-materials "
            "engagement, which should not normally happen.",
            "Reconcile the invoice to the approved timesheets. Either hours "
            "are missing or the customer has been over-billed.")


def _billing_statement(label, scope, sel, rec, inv, unbilled, deferred,
                       n_unb, n_def, aged90, oldest):
    s = (f"{label}: {rec:,.0f} recognised against {inv:,.0f} invoiced across "
         f"{len(sel)} engagements. ")
    if unbilled <= 0 and deferred <= 0:
        return s + "Billing and delivery are in line, engagement by engagement."
    parts = []
    if unbilled > 0:
        parts.append(f"{unbilled:,.0f} delivered and unbilled across "
                     f"{n_unb} engagement{'s' if n_unb != 1 else ''}")
    if deferred > 0:
        parts.append(f"{deferred:,.0f} billed ahead of delivery across "
                     f"{n_def} engagement{'s' if n_def != 1 else ''}")
    s += " and ".join(parts) + ". "
    # The netted figure last, and only with the warning that goes with it.
    net = unbilled - deferred
    if unbilled > 0 and deferred > 0:
        s += (f"Netting those gives {abs(net):,.0f}, which is the number a "
              f"cash forecast would use and the wrong number for a billing "
              f"conversation: the two sit on different engagements and only "
              f"one of them is anybody's to collect. ")
    if aged90 > 0:
        s += (f"{aged90:,.0f} of the unbilled balance is on engagements that "
              f"have not invoiced for a quarter or more")
        if oldest:
            s += f", the oldest {oldest} months"
        s += ". That is the part at risk of never being billed at all."
    return s.strip()


# ---------------------------------------------------------------------------
# Billing model economics
# ---------------------------------------------------------------------------
def model_projects(con, org_id):
    """
    Completed engagements only, with margin and effort against plan.

    Completed only, because a margin on a live engagement is a forecast and
    mixing forecasts with outturns to compare commercial models would let the
    optimism of a PM decide which model looks better.
    """
    return rows(con, """
        SELECT p.project_id, p.project_code, p.billing_model, p.project_type,
               p.methodology, p.budget_hours, p.budget_cost, p.contract_value,
               p.start_date, p.actual_end_date,
               f.revenue, f.cost,
               CASE WHEN f.revenue > 0
                    THEN (f.revenue - f.cost) / f.revenue * 100 END AS margin_pct,
               CASE WHEN p.budget_cost > 0
                    THEN f.labor_cost / p.budget_cost END AS overrun,
               te.hours
          FROM project p
          JOIN (SELECT project_id,
                       SUM(recognized_revenue) revenue,
                       SUM(labor_cost + other_cost) cost,
                       SUM(labor_cost) labor_cost
                  FROM project_financial_month GROUP BY project_id) f
               USING(project_id)
          LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                      WHERE approval_status='Approved' GROUP BY project_id) te
               USING(project_id)
         WHERE p.org_id=? AND p.project_status='Complete'
           AND f.revenue > 0""", (org_id,))


MARGIN_BANDS = [
    (-1e9, 0, "Loss"),
    (0, 10, "0 to 10%"),
    (10, 20, "10 to 20%"),
    (20, 30, "20 to 30%"),
    (30, 40, "30 to 40%"),
    (40, 1e9, "Over 40%"),
]


def build_models(con, org_id, run_id, as_of):
    con.execute("DELETE FROM model_comparison WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM model_margin_band WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM model_economics WHERE org_id=?", (org_id,))

    projs = model_projects(con, org_id)
    if not projs:
        return 0, 0, 0
    total_rev = sum(p["revenue"] for p in projs)

    rates = {r["dimension_label"]: r for r in rows(con, """
        SELECT dimension_label, discount_pct, net_rate FROM rate_analysis
         WHERE org_id=? AND dimension='billing_model'""", (org_id,))}

    order = ["Fixed Fee", "Milestone", "Capped T&M", "Time and Materials",
             "Retainer"]
    econ = []
    bands = []
    for bm in sorted({p["billing_model"] for p in projs},
                     key=lambda x: order.index(x) if x in order else 99):
        sel = [p for p in projs if p["billing_model"] == bm]
        margins = [p["margin_pct"] for p in sel if p["margin_pct"] is not None]
        overruns = [p["overrun"] for p in sel if p["overrun"]]
        rev = sum(p["revenue"] for p in sel)
        cost = sum(p["cost"] for p in sel)
        sd = stdev(margins)
        neg = sum(1 for m in margins if m < 0)

        types = {}
        for p in sel:
            types[p["project_type"]] = types.get(p["project_type"], 0) + 1
        top_type, top_n = (max(types.items(), key=lambda kv: kv[1])
                           if types else (None, 0))
        durations = []
        for p in sel:
            if p["start_date"] and p["actual_end_date"]:
                durations.append(
                    (dt.date.fromisoformat(p["actual_end_date"])
                     - dt.date.fromisoformat(p["start_date"])).days / 30.4)
        partner = sum(1 for p in sel if p["methodology"] == "Partner-Led")
        carrier, carrier_note = RISK_CARRIER.get(bm, ("shared", ""))

        verdict, statement = _model_statement(
            bm, sel, margins, overruns, rev, cost, sd, neg, carrier,
            carrier_note, top_type, top_n, total_rev)

        econ.append((
            org_id, run_id, bm, carrier, len(sel), q2(rev), q2(cost),
            q2(rev / total_rev * 100) if total_rev else None,
            q2((rev - cost) / rev * 100) if rev else None,
            q2(sum(margins) / len(margins)) if margins else None,
            q2(median(margins)), q2(pctile(margins, 0.25)),
            q2(pctile(margins, 0.75)), q2(min(margins)) if margins else None,
            q2(max(margins)) if margins else None, q2(sd), neg,
            q2(neg / len(margins) * 100) if margins else None,
            q2(median(overruns)), q2(pctile(overruns, 0.9)),
            q2(sum(1 for o in overruns if o > 1.0) / len(overruns) * 100)
            if overruns else None,
            q2((rates.get(bm) or {}).get("discount_pct")),
            q2((rates.get(bm) or {}).get("net_rate")),
            q2(median([p["budget_hours"] for p in sel])),
            top_type, q2(top_n / len(sel) * 100) if sel else None,
            q2(partner / len(sel) * 100) if sel else None,
            q2(median(durations)), verdict, statement,
            order.index(bm) if bm in order else 99))

        for i, (lo, hi, blabel) in enumerate(MARGIN_BANDS):
            inb = [p for p in sel
                   if p["margin_pct"] is not None
                   and lo <= p["margin_pct"] < hi]
            bands.append((org_id, run_id, bm, lo if lo > -1e8 else -100,
                          hi if hi < 1e8 else 100, blabel, len(inb),
                          q2(len(inb) / len(sel) * 100) if sel else 0,
                          q2(sum(p["revenue"] for p in inb)), i))

    con.executemany("""
        INSERT INTO model_economics
          (org_id, run_id, billing_model, risk_carried_by, projects, revenue,
           cost, share_of_revenue_pct, margin_pct, margin_mean_pct,
           margin_median_pct, margin_p25_pct, margin_p75_pct, margin_min_pct,
           margin_max_pct, margin_stdev_pts, projects_negative, pct_negative,
           overrun_median, overrun_p90, pct_over_budget, discount_pct,
           realised_rate, median_size_hours, top_project_type,
           top_project_type_pct, pct_partner_led, median_duration_months,
           verdict, statement, sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        econ)
    con.executemany("""
        INSERT INTO model_margin_band
          (org_id, run_id, billing_model, band_from, band_to, band_label,
           projects, share_pct, revenue, sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", bands)

    n_cmp = _build_comparison(con, org_id, run_id, projs)
    return len(econ), len(bands), n_cmp


def _model_statement(bm, sel, margins, overruns, rev, cost, sd, neg, carrier,
                     carrier_note, top_type, top_n, total_rev):
    if len(sel) < T["min_sample"]:
        return ("too few", (
            f"{bm}: {len(sel)} completed engagements, too few to read as a "
            f"pattern. The individual outcomes are the evidence here, not the "
            f"average."))
    mean = sum(margins) / len(margins)
    med = median(margins)
    s = (f"{bm}: {len(sel)} completed engagements, {rev:,.0f} of recognised "
         f"revenue. Margin averages {mean:,.1f}% and the median is "
         f"{med:,.1f}%")
    if sd is not None:
        s += (f", with a standard deviation of {sd:,.1f} points and outcomes "
              f"from {min(margins):,.1f}% to {max(margins):,.1f}%. ")
    else:
        s += ". "
    s += carrier_note + " "
    if neg:
        s += (f"{neg} of {len(margins)} lost money "
              f"({neg / len(margins) * 100:,.0f}%). ")
    else:
        s += "None of them lost money. "
    if overruns:
        over = sum(1 for o in overruns if o > 1.0) / len(overruns) * 100
        s += (f"Half ran at {median(overruns):,.2f} times planned cost or "
              f"better; {over:,.0f}% went over. ")

    if sd is not None and sd >= T["wide_spread_pts"]:
        verdict = "high variance"
        s += ("The spread is what matters here rather than the average: this "
              "is a model that rewards good delivery and punishes bad "
              "delivery, and the portfolio only earns the higher mean if the "
              "estimating holds.")
    elif sd is not None:
        verdict = "predictable"
        s += ("The spread is narrow, so the average is close to what any one "
              "engagement returns. That predictability is worth something in "
              "itself and it is the reason to accept a lower mean.")
    else:
        verdict = "predictable"
    if top_type and top_n / len(sel) > 0.4:
        s += (f" Note the mix before comparing: {top_n / len(sel) * 100:,.0f}% "
              f"of this model's work is {top_type}, so part of the difference "
              f"is what it gets sold on rather than the model itself.")
    return verdict, s


def _build_comparison(con, org_id, run_id, projs):
    """
    The head-to-head the question asks for: time and materials against fixed
    fee.

    Each row is one measure with both values, which side it favours, and a
    reading. The last rows are deliberately about the confounds, because a
    comparison that reports only the measures where one model wins is an
    argument rather than an analysis.
    """
    a, b = "Time and Materials", "Fixed Fee"
    A = [p for p in projs if p["billing_model"] == a]
    B = [p for p in projs if p["billing_model"] == b]
    if len(A) < T["min_sample"] or len(B) < T["min_sample"]:
        return 0

    def m(sel):
        return [p["margin_pct"] for p in sel if p["margin_pct"] is not None]

    def o(sel):
        return [p["overrun"] for p in sel if p["overrun"]]

    ma, mb = m(A), m(B)
    rev_a = sum(p["revenue"] for p in A)
    rev_b = sum(p["revenue"] for p in B)
    cost_a = sum(p["cost"] for p in A)
    cost_b = sum(p["cost"] for p in B)

    rows_out = [
        ("margin_weighted", "Margin, revenue weighted",
         (rev_a - cost_a) / rev_a * 100, (rev_b - cost_b) / rev_b * 100,
         "percent",
         "What the portfolio actually earned on each model. This is the "
         "number a P&L sees."),
        ("margin_mean", "Margin, average per engagement",
         sum(ma) / len(ma), sum(mb) / len(mb), "percent",
         "Unweighted, so a small engagement counts as much as a large one. "
         "Worth seeing beside the weighted figure: where the two disagree, "
         "the model's result is being carried by a few large engagements."),
        ("margin_median", "Margin, median engagement",
         median(ma), median(mb), "percent",
         "The typical outcome, unmoved by the best and worst engagement on "
         "each side."),
        ("margin_spread", "Spread of outcomes, standard deviation",
         stdev(ma), stdev(mb), "points",
         "The most important row here. A wider spread means the outcome "
         "depends on delivery rather than on the contract, which is exactly "
         "what it means to carry the risk."),
        ("margin_worst", "Worst engagement",
         min(ma), min(mb), "percent",
         "The downside each model actually produced. On time and materials "
         "the customer absorbs an overrun, so the floor is high; on fixed "
         "fee the floor is wherever the estimating stopped being right."),
        ("pct_negative", "Engagements that lost money",
         sum(1 for x in ma if x < 0) / len(ma) * 100,
         sum(1 for x in mb if x < 0) / len(mb) * 100, "percent",
         "A loss-making engagement on time and materials takes deliberate "
         "effort. On fixed fee it takes an estimate that was wrong."),
        ("overrun", "Cost against plan, median",
         median(o(A)), median(o(B)), "ratio",
         "Effort discipline, which is where a fixed-fee margin is won. If "
         "this is similar on both, the margin difference is pricing rather "
         "than delivery."),
        ("size", "Median engagement size, budget hours",
         median([p["budget_hours"] for p in A]),
         median([p["budget_hours"] for p in B]), "hours",
         "A confound, not a result. Larger engagements carry more estimating "
         "risk, so a size difference between the two populations partly "
         "explains any spread difference."),
        ("partner_led",
         "Share delivered on the partner-led method",
         sum(1 for p in A if p["methodology"] == "Partner-Led") / len(A) * 100,
         sum(1 for p in B if p["methodology"] == "Partner-Led") / len(B) * 100,
         "percent",
         "The second confound. Partner-led delivery overruns more than any "
         "other method in this book, so whichever model carries more of it "
         "starts at a disadvantage that has nothing to do with the contract."),
    ]

    # Which side each measure favours. Lower is better for spread, worst-case
    # is better higher, loss share and overrun lower. The two confound rows
    # favour neither by construction, and saying so is the point of including
    # them.
    higher_better = {"margin_weighted", "margin_mean", "margin_median",
                     "margin_worst"}
    lower_better = {"margin_spread", "pct_negative", "overrun"}
    out = []
    for i, (code, label, va, vb, unit, reading) in enumerate(rows_out):
        if va is None or vb is None:
            continue
        if code in higher_better:
            favours = "a" if va > vb else "b" if vb > va else "neither"
        elif code in lower_better:
            favours = "a" if va < vb else "b" if vb < va else "neither"
        else:
            favours = "neither"
        out.append((org_id, run_id, a, b, code, label, q2(va), q2(vb),
                    q2(va - vb), unit, favours, reading, i))
    con.executemany("""
        INSERT INTO model_comparison
          (org_id, run_id, model_a, model_b, metric, metric_label, value_a,
           value_b, delta, unit, favours, reading, sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    return len(out)


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
        n_pos, n_mon, n_exc = build_billing(con, org_id, run_id, as_of)
        n_econ, n_band, n_cmp = build_models(con, org_id, run_id, as_of)
        con.commit()
        print(f"{code}: billing {n_pos} positions / {n_mon} months / "
              f"{n_exc} exceptions, models {n_econ} / {n_band} bands / "
              f"{n_cmp} comparisons")
    con.close()


if __name__ == "__main__":
    main()
