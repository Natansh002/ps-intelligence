"""
Resource requirement by product line.

The question a services leader is actually asked in a board meeting is not
"what is our utilisation". It is "how many consultants of which kind do we
need in which product line, by when, and what happens to the pipeline if we
do not have them". Answering that needs six things to be visible at once:

    time to go live          how long an implementation takes, measured
    existing consultants     who is available, counted as FTE not heads
    billable utilisation     the target the plan is priced at
    productive utilisation   billable plus the non-billable work that has to
                             happen anyway, which is the real ceiling
    training targets         hours removed from capacity before it is sold
    sales pipeline           weighted, phased, and converted into hours

Every one of those is stored as an input on the plan line rather than buried
in this file, and the arithmetic below reads them back out of the database.
That is deliberate: a capacity plan nobody can audit is a capacity plan
nobody acts on, and the first question anyone senior asks is "what did you
assume". The answer has to be on the screen.

Two things this deliberately does not do. It does not assume the hires it
recommends: supply is the workforce as it stands, less attrition, so the gap
is the size of the decision rather than the size of the residual after an
imaginary recruitment plan. And it does not present a single number. The
sensitivity table shows which lever actually moves the answer, because in
practice a two-point utilisation improvement and a four-week faster
implementation are not the same size of ask, and the plan is worth less if it
cannot say which one to pull.
"""
import datetime as dt
import math
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

PLAN_HORIZON_MONTHS = 12

# Organisation-wide assumptions. These are written onto the plan row and read
# back from it, so the model runs off stored data and the console can show
# what it assumed. They are defaults, not constants.
ORG_ASSUMPTIONS = {
    "recruit_lead_weeks": 10.0,      # requisition to start date
    "attrition_pct_annual": 12.0,
    "standard_hours_per_week": 37.5,
    # Fifty-two, not forty-six.
    #
    # Deducting statutory holidays and leave here and then applying a
    # utilisation target on top double-counts absence, because the
    # utilisation target is itself defined against the full contracted year:
    # 70% means 70% of 37.5 hours a week for 52 weeks, with leave already
    # inside its denominator. Netting the weeks off first made the model
    # report 97 sellable hours per FTE per month against 117 the
    # organisation actually delivers, a 17% understatement, and that
    # understatement then presented itself as a permanent capacity gap in
    # every product line. The target does the absorbing; the weeks stay at
    # 52.
    "working_weeks_per_year": 52.0,
}

# Per-line assumptions that cannot be measured from the data held here.
LINE_ASSUMPTIONS = {
    "ramp_weeks": 12.0,              # new hire to fully productive
    "ramp_util_pct": 45.0,           # utilisation during ramp
    "training_hours_per_year": 60.0, # certification and enablement
    "productive_uplift_pts": 14.0,   # presales, enablement, internal delivery
}


# ---------------------------------------------------------------------------
def month_key(d):
    return d.strftime("%Y-%m")


def add_months(d, n):
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def months_from(start, count):
    return [month_key(add_months(start, i)) for i in range(count)]


def q2(x):
    return None if x is None else round(float(x), 2)


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, args=(), default=0):
    r = con.execute(sql, args).fetchone()
    return default if r is None or r[0] is None else r[0]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def time_to_go_live(con, org_id, product):
    """
    Median weeks from start to go live, measured on completed work.

    Median rather than mean, because one 60-week recovery project would
    otherwise set the staffing plan for every future implementation. Where
    there are too few observations to trust, this says so rather than
    reporting a number with the same confidence as a measured one.
    """
    obs = [r["wks"] for r in rows(con, """
        SELECT (julianday(go_live_date) - julianday(start_date)) / 7.0 AS wks
        FROM project
        WHERE org_id=? AND product=? AND go_live_date IS NOT NULL
          AND start_date IS NOT NULL
          AND project_type IN ('Implementation','Upgrade','Migration')""",
        (org_id, product))]
    obs = [o for o in obs if o and 1 < o < 120]
    if len(obs) >= 5:
        return round(median(obs), 1), f"measured, {len(obs)} completed"
    org = [r["wks"] for r in rows(con, """
        SELECT (julianday(go_live_date) - julianday(start_date)) / 7.0 AS wks
        FROM project WHERE org_id=? AND go_live_date IS NOT NULL
          AND start_date IS NOT NULL""", (org_id,))]
    org = [o for o in org if o and 1 < o < 120]
    return (round(median(org) or 16.0, 1),
            f"assumed, only {len(obs)} completed on this line, "
            f"organisation median used")


def dominant_practice(con, org_id, product):
    r = con.execute("""
        SELECT p.practice_id, pr.practice_name, COUNT(*) n
        FROM project p LEFT JOIN practice pr ON pr.practice_id=p.practice_id
        WHERE p.org_id=? AND p.product=? AND p.practice_id IS NOT NULL
        GROUP BY 1 ORDER BY n DESC LIMIT 1""", (org_id, product)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def supply_inputs(con, org_id, product, weeks_per_year, std_week):
    """
    Consultants attributable to the product line, counted as FTE.

    Consultants are not owned by a product, they are shared, so a line's
    supply has to be attributed rather than looked up. The first version of
    this split each practice across the products that practice worked on,
    which produced one line apparently delivering 279 billable hours per FTE
    per month against a theoretical ceiling of 138. The cause was that demand
    was measured on the product and supply on the practice, and the two
    populations were not the same people.

    So attribution follows the hours each individual actually booked to the
    line, against their own capacity at their own utilisation target. One
    consultant delivering exactly at target on one line counts as one FTE,
    somebody splitting their year across two lines counts as roughly half in
    each, and the total across lines cannot exceed the billable workforce.
    The useful property is that supply and demand are now measured on the
    same population, so multiplying the FTE by the hours per FTE reproduces
    what the line actually delivered last year. A capacity model that cannot
    reproduce last year is not going to be believed about next year.

    Heads are reported beside FTE because they are a different question. Nine
    people spending a fifth of their time on a line is three FTE of capacity
    and nine people's worth of context switching.
    """
    people = rows(con, """
        SELECT e.employee_id, e.employee_name, e.weekly_capacity_hours,
               e.utilization_target_pct,
               SUM(CASE WHEN p.product=? THEN t.hours ELSE 0 END) AS line_hours,
               SUM(t.hours) AS all_hours
        FROM time_entry t
             JOIN project p ON p.project_id = t.project_id
             JOIN employee e ON e.employee_id = t.employee_id
        WHERE p.org_id=? AND t.is_billable=1
          AND t.approval_status='Approved'
          AND t.entry_date >= date((SELECT MAX(entry_date) FROM time_entry),
                                   '-365 days')
          AND e.is_active=1 AND e.is_billable=1
        GROUP BY e.employee_id
        HAVING line_hours > 0""", (product, org_id))
    if not people:
        return 0.0, 0.0, 72.0, []

    fte = 0.0
    heads = 0
    targets = []
    detail = []
    for p in people:
        cap_week = p["weekly_capacity_hours"] or std_week
        # A person's FTE is their capacity, and their contribution to a line
        # is the share of their own billable year that went to it. Dividing
        # line hours by a target-based capacity instead made anyone running
        # above target count as more than one FTE, so the attributed capacity
        # across seven lines came to 126 FTE against a billable head count of
        # 108. Sharing out each person's own FTE conserves the total by
        # construction, which is the property that makes the number safe to
        # add up on a screen.
        if not p["all_hours"]:
            continue
        own_fte = cap_week / std_week
        contribution = own_fte * p["line_hours"] / p["all_hours"]
        fte += contribution
        targets.append(p["utilization_target_pct"] or 72.0)
        if contribution >= 0.05:
            heads += 1
        detail.append((p["employee_name"], round(contribution, 2)))
    detail.sort(key=lambda d: -d[1])
    return round(heads, 1), round(fte, 2), round(median(targets) or 72.0, 1), detail


def backlog_by_month(con, org_id, product, horizon):
    """
    Committed hours still to deliver on live work, phased over each
    engagement's remaining duration.

    Remaining hours come from the engine's estimate to complete where it has
    one, so the capacity plan and the margin forecast are built on the same
    number. A plan that disagrees with the forecast on the same project is
    two plans.
    """
    per_month = {m: 0.0 for m in horizon}
    total = 0.0
    window = 0          # months to the latest planned end date on live work
    live = rows(con, """
        SELECT p.project_id, p.budget_hours, p.planned_end_date,
               COALESCE(mp.predicted_hours_to_complete, 0) AS eac_remaining,
               COALESCE(te.hours, 0) AS booked
        FROM project p
        LEFT JOIN (SELECT project_id, MAX(prediction_id) pid
                     FROM project_margin_prediction GROUP BY project_id) last
               ON last.project_id = p.project_id
        LEFT JOIN project_margin_prediction mp
               ON mp.prediction_id = last.pid
        LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                    WHERE approval_status='Approved' GROUP BY project_id) te
               ON te.project_id = p.project_id
        WHERE p.org_id=? AND p.product=? AND p.project_status='In Flight'""",
        (org_id, product))
    today = dt.date.fromisoformat(
        one(con, "SELECT MAX(entry_date) FROM time_entry", (),
            default=dt.date.today().isoformat()))
    for p in live:
        remaining = p["eac_remaining"] or max(
            0.0, (p["budget_hours"] or 0) - p["booked"])
        if remaining <= 0:
            continue
        total += remaining
        end = (dt.date.fromisoformat(p["planned_end_date"])
               if p["planned_end_date"] else add_months(today, 4))
        span = (end.year - today.year) * 12 + end.month - today.month
        # An engagement already past its planned end date has a span of zero
        # or less, and dropping its remaining hours into the current month
        # produces a spike no team could ever absorb and no reader believes.
        # Overdue work still takes time to finish, so it is spread over the
        # months the remaining hours actually imply at a plausible team size.
        if span < 1:
            span = max(1, int(math.ceil(remaining / 320.0)))   # ~2 FTE
        span = min(span, len(horizon))
        for i in range(span):
            m = month_key(add_months(today, i))
            if m in per_month:
                per_month[m] += remaining / span
        if p["planned_end_date"]:
            end = dt.date.fromisoformat(p["planned_end_date"])
            window = max(window, (end.year - today.year) * 12
                         + end.month - today.month)
    return per_month, total, max(1, min(window, len(horizon)))


def run_rate_hours(con, org_id, product):
    """
    Billable hours delivered per month on this line over the trailing year.

    This is the number the far months of the plan revert to. Beyond the
    pipeline window there is no demand signal, and a plan that reads that as
    zero demand reports a bench that exists only in the spreadsheet. A
    services line that billed 900 hours a month for a year will not bill zero
    next August because nobody has sold it yet.
    """
    return one(con, """
        SELECT SUM(t.hours) / 12.0
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
        WHERE p.org_id=? AND p.product=? AND t.is_billable=1
          AND t.approval_status='Approved'
          AND t.entry_date >= date((SELECT MAX(entry_date) FROM time_entry),
                                   '-365 days')""",
        (org_id, product), default=0.0) or 0.0


def expected_demand(visible, run_rate):
    """
    A month's demand is the greater of what can be seen and what the line
    normally does.

    An earlier version ramped the run-rate floor up over the horizon, on the
    reasoning that near months are visible and far months are not. That
    confused two different things: how much of next August's demand is
    *visible* today, and how much of it will *exist*. Modelling the
    invisible half as absent made the middle of every horizon look like a
    bench and pushed every line to a "sell more" verdict, which is the
    mirror image of the front-loaded spike it replaced.

    The run rate applies in full, every month. Where committed work and
    weighted pipeline exceed it, they win, because that is real demand on
    top of the baseline. Where they do not, the baseline stands, because a
    line that has billed nine hundred hours a month for a year will not bill
    zero next August merely because nobody has signed for it yet.
    """
    return max(visible, run_rate)


def pipeline_by_month(con, org_id, product, horizon):
    """
    Pipeline hours, probability weighted and phased from expected start.

    Unweighted pipeline is a marketing number. Weighted pipeline is a
    staffing number, because the plan has to survive losing the deals that
    were always unlikely.
    """
    raw = {m: 0.0 for m in horizon}
    weighted = {m: 0.0 for m in horizon}
    tot_hours = tot_weighted = tot_value = weighted_value = 0.0
    opps = rows(con, """
        SELECT estimated_hours, probability_pct, services_value,
               expected_start, expected_duration_months, stage
        FROM pipeline_opportunity
        WHERE org_id=? AND product=?
          AND stage NOT IN ('Closed Won','Closed Lost')""", (org_id, product))
    for o in opps:
        hrs = o["estimated_hours"] or 0.0
        prob = (o["probability_pct"] or 0) / 100.0
        tot_hours += hrs
        tot_weighted += hrs * prob
        tot_value += o["services_value"] or 0.0
        weighted_value += (o["services_value"] or 0.0) * prob
        if not o["expected_start"]:
            continue
        start = dt.date.fromisoformat(o["expected_start"])
        span = max(1, int(o["expected_duration_months"] or 4))
        for i in range(span):
            m = month_key(add_months(start, i))
            if m in raw:
                raw[m] += hrs / span
                weighted[m] += hrs * prob / span
    return (raw, weighted, tot_hours, tot_weighted, tot_value, weighted_value,
            len(opps))


def realised_rate(con, org_id, product):
    """
    Revenue recognised per hour delivered on the line, over the last year.

    Every hour, not just the billable ones. Dividing by billable hours only
    gives a number that reads far above the rate card on fixed-price work,
    because the fee is fixed and the non-billable hours delivered beside it
    are invisible to that division. Since this figure is used to value
    unservable pipeline, the flattered version would overstate the revenue
    at risk by the same margin. It is also the figure the rate analysis calls
    "per hour delivered", so the two views agree.
    """
    return one(con, """
        WITH h AS (
          SELECT SUM(t.hours) hrs FROM time_entry t
                 JOIN project p ON p.project_id=t.project_id
           WHERE p.org_id=? AND p.product=?
             AND t.approval_status='Approved'
             AND t.entry_date >= date((SELECT MAX(entry_date) FROM time_entry),
                                      '-365 days')),
             r AS (
          SELECT SUM(fm.recognized_revenue) rev FROM project_financial_month fm
                 JOIN project p ON p.project_id=fm.project_id
           WHERE p.org_id=? AND p.product=?
             AND fm.period_month >= strftime('%Y-%m',
                   date((SELECT MAX(entry_date) FROM time_entry), '-365 days')))
        SELECT CASE WHEN (SELECT hrs FROM h) > 0
                    THEN (SELECT rev FROM r) / (SELECT hrs FROM h) END""",
        (org_id, product, org_id, product), default=None)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
def productive_hours_per_fte_month(plan, line):
    """
    Sellable hours one FTE delivers in a month.

    Capacity is not hours in the month. It is contracted hours, less the
    weeks nobody works, less the training the business has committed to,
    multiplied by the utilisation the work was priced at. Skipping any of
    those three is how a capacity plan ends up 20% optimistic, and it is
    always the same 20%.
    """
    annual = plan["standard_hours_per_week"] * plan["working_weeks_per_year"]
    annual -= line["training_hours_per_year"]
    return annual / 12.0 * (line["billable_util_target_pct"] / 100.0)


def run_line(plan, line, demand_by_month, horizon, attrition_monthly):
    """
    Month by month: demand against the workforce as it stands.

    Attrition is assumed to be backfilled, so supply holds flat and the gap
    that comes out is net new capacity. Charging attrition against supply
    without backfilling it made every line in the portfolio read "hire",
    because at 12% a year a line needs recruitment just to stand still. That
    is true, and it is a different decision from growth, so it is reported
    as its own number rather than folded into this one.
    """
    out = []
    fte = line["existing_fte"]
    per_fte = productive_hours_per_fte_month(plan, line)
    raw_per_fte = (plan["standard_hours_per_week"]
                   * plan["working_weeks_per_year"] / 12.0)
    for i, m in enumerate(horizon):
        d = demand_by_month[m]
        capacity = fte * raw_per_fte
        productive = fte * (raw_per_fte - line["training_hours_per_year"] / 12.0)
        billable = fte * per_fte
        gap_hours = d - billable
        out.append({
            "period_month": m,
            "demand_hours": round(d, 1),
            "headcount_fte": round(fte, 2),
            "ramping_fte": 0.0,
            "capacity_hours": round(capacity, 1),
            "productive_hours": round(productive, 1),
            "billable_hours": round(billable, 1),
            "gap_hours": round(gap_hours, 1),
            "gap_fte": round(gap_hours / per_fte, 2) if per_fte else 0.0,
            "projected_util_pct": round(d / capacity * 100, 1) if capacity else None,
            "band": ("short" if gap_hours > per_fte * 0.25 else
                     "bench" if gap_hours < -per_fte * 0.5 else "balanced"),
        })
        # Supply holds flat. attrition_monthly is deliberately not applied
        # here: it drives the backfill requirement instead, which is reported
        # on its own line.
        pass
    return out, per_fte


def shortfall_index(months):
    """
    Average FTE short across the whole horizon, counting a surplus month as
    zero rather than as a credit.

    This exists because the sensitivity table needs a measure that moves in
    one direction when a lever improves things, and neither of the two
    numbers the verdict uses does. The peak ignores everything but one
    month. The average across the months that are short is worse than
    useless: improve a line and its easiest short months stop being short,
    so the average over what remains goes up while the actual shortfall goes
    down. That produced a table saying five extra points of utilisation made
    the requirement worse, which is the kind of number that ends a meeting.

    Surplus months are floored at zero because spare capacity in March does
    not staff a project in September. Consultants are not inventory.
    """
    if not months:
        return 0.0
    return round(sum(max(0.0, r["gap_fte"]) for r in months) / len(months), 2)


def sensitivity(plan, line, demand_by_month, horizon, attrition_monthly,
                base_gap):
    """
    One lever at a time, so the answer to "what would have to change" is a
    number rather than a conversation. The levers are the inputs a services
    leader can actually move within two quarters.
    """
    levers = [
        ("billable_util", "Billable utilisation target",
         "+5 points", {"billable_util_target_pct":
                       line["billable_util_target_pct"] + 5}),
        ("billable_util_down", "Billable utilisation target",
         "-5 points", {"billable_util_target_pct":
                       max(30.0, line["billable_util_target_pct"] - 5)}),
        ("training", "Training and certification",
         "+40 hours a year", {"training_hours_per_year":
                              line["training_hours_per_year"] + 40}),
        ("attrition", "Attrition", "+4 points a year", {}),
        ("pipeline", "Pipeline conversion", "-10 points of win rate", {}),
        ("go_live", "Time to go live", "-2 weeks per implementation", {}),
    ]
    out = []
    for code, label, shift, patch in levers:
        alt = dict(line)
        alt.update(patch)
        alt_demand = dict(demand_by_month)
        alt_attr = attrition_monthly
        if code == "attrition":
            alt_attr = (plan["attrition_pct_annual"] + 4) / 100.0 / 12.0
        if code == "pipeline":
            # Ten points off the win rate removes a tenth of the pipeline
            # hours in every month it was expected to land. The requirement
            # does not fall one for one, because the run-rate floor reasserts
            # itself in the months the lost work would have covered. That
            # offset is the point of showing the lever at all.
            share = (line["pipeline_weighted_hours"] /
                     line["pipeline_hours"]) if line["pipeline_hours"] else 0
            factor = max(0.0, (share - 0.10) / share) if share > 0 else 1.0
            for m in horizon:
                back = line["_backlog_month"].get(m, 0.0)
                pipe = line["_pipeline_month"].get(m, 0.0) * factor
                alt_demand[m] = expected_demand(back + pipe,
                                                line["_run_rate"])
        if code == "go_live":
            # A shorter implementation does not reduce the hours, it moves
            # them earlier and frees the team sooner. Within the horizon that
            # shows up as the same work needing fewer consultant-months.
            weeks = max(4.0, line["time_to_go_live_weeks"] - 2)
            factor = weeks / line["time_to_go_live_weeks"]
            for m in alt_demand:
                alt_demand[m] *= factor
        months, per_fte = run_line(plan, alt, alt_demand, horizon, alt_attr)
        after = shortfall_index(months)
        out.append({
            "lever": code, "lever_label": label, "shift": shift,
            "fte_gap_before": round(base_gap, 2),
            "fte_gap_after": after,
            "fte_delta": round(after - base_gap, 2),
        })
    for s in out:
        d = s["fte_delta"]
        if s["lever"] == "pipeline" and abs(d) < 0.15:
            s["statement"] = (
                "Losing ten points of win rate barely changes the "
                "requirement, because the run-rate baseline reasserts itself "
                "in the months the lost work would have filled. The pipeline "
                "shapes which months are busy, not how many consultants the "
                "line needs.")
        elif s["lever"] == "attrition" and abs(d) < 0.15:
            s["statement"] = (
                "Attrition does not move the growth requirement, because "
                "leavers are assumed to be backfilled. It moves the "
                "recruitment bill rather than the capacity gap, and it is "
                "reported as the backfill figure instead.")
        elif abs(d) < 0.15:
            s["statement"] = (f"{s['lever_label']} {s['shift']} barely moves "
                              f"the requirement. It is not the constraint "
                              f"here.")
        elif d < 0:
            s["statement"] = (f"{s['lever_label']} {s['shift']} removes "
                              f"{abs(d):,.1f} FTE from the requirement.")
        else:
            s["statement"] = (f"{s['lever_label']} {s['shift']} adds "
                              f"{d:,.1f} FTE to the requirement.")
    out.sort(key=lambda s: abs(s["fte_delta"]), reverse=True)
    return out


def verdict_for(months, backlog_months, backlog_window, bench,
                over_target_pct=None):
    """
    The recommendation, on two axes: how big the shortfall is and how long it
    lasts. Either axis alone gives the wrong answer.

    Summarising the horizon with a peak recommended hiring in every line in
    every quarter, because backlog is front loaded by nature. Summarising it
    with a median recommended holding on a line running at three and a half
    times its capacity for five months, because half the horizon was quiet.
    The decision a resourcing meeting actually makes needs both: a large gap
    for two months is a subcontract or a rephase, a moderate gap for nine
    months is a hire, and the same number of FTE-months can be either.

    Before any of that, one arithmetic check. If the committed backlog holds
    more months of work than there are months before the dates it is
    promised against, the line has a schedule problem and no amount of
    recruitment fixes it inside the lead time. That case is called first,
    because recommending eight hires against a date that was never
    achievable is worse than saying nothing.
    """
    short = [r for r in months if r["gap_fte"] > 0.25]
    n_short = len(short)
    depth = (sum(r["gap_fte"] for r in short) / n_short) if n_short else 0.0
    peak = max((r["gap_fte"] for r in months), default=0.0)

    # A shortfall this large cannot be recruited into and cannot be
    # subcontracted either: nobody onboards five consultants into a live
    # engagement inside the month the gap opens. At that scale the only
    # lever that works inside the lead time is the schedule.
    if peak >= 5.0 and n_short <= 4:
        return "rephase", (
            f"The shortfall reaches {peak:,.1f} FTE but lasts only "
            f"{n_short} month{'s' if n_short != 1 else ''}. That is too "
            f"large to subcontract at short notice and far too short to "
            f"recruit against. Staggering the starts that collide in the "
            f"peak month is the only lever that acts inside the lead time.")

    if backlog_window >= 1 and backlog_months > backlog_window * 1.4:
        return "rephase", (
            f"The committed backlog holds {backlog_months:,.1f} months of "
            f"work at the rate this line actually delivers, against planned "
            f"end dates {backlog_window:,.0f} months out. That is a schedule "
            f"problem before it is a resourcing one: recruitment cannot land "
            f"inside the gap, so the dates have to move or the scope has to.")

    # A line running well above the target it was priced at will show a
    # shortfall in every month by construction. The honest first move there is
    # to settle whether the target or the hours are wrong, because hiring
    # against a target nobody believes just moves the same argument to a
    # bigger team.
    if n_short >= 6 and over_target_pct is not None and over_target_pct >= 20:
        return "reset target", (
            f"The team is delivering {over_target_pct:,.0f}% above the "
            f"utilisation this work was priced at, in {n_short} of "
            f"{len(months)} months. Closing that with headcount would take "
            f"{depth:,.1f} FTE, but the prior question is whether "
            f"{100 + over_target_pct:,.0f}% of target is the real operating "
            f"level for this line or a year of quiet overtime. Answer that "
            f"first; the hiring number changes either way.")

    if n_short >= 6 and depth >= 1.0:
        return "hire", (
            f"The shortfall averages {depth:,.1f} FTE across {n_short} of "
            f"{len(months)} months. That is structural rather than a peak, "
            f"and subcontracting it for a year costs more than the salary.")
    if n_short >= 6 and depth >= 0.4:
        return "hire", (
            f"A persistent {depth:,.1f} FTE short across {n_short} months. "
            f"One consultant closes it. Started now it lands before the gap "
            f"widens; started when somebody complains it lands a quarter "
            f"late.")
    if peak >= 1.0 and n_short <= 4:
        return "subcontract", (
            f"The shortfall reaches {peak:,.1f} FTE but only in {n_short} "
            f"month{'s' if n_short != 1 else ''}. Subcontract cover costs "
            f"margin on those hours; a permanent hire costs a bench for the "
            f"rest of the year.")
    if n_short >= 3:
        return "subcontract", (
            f"{n_short} months short by an average of {depth:,.1f} FTE. Too "
            f"long to absorb and too small to recruit against without "
            f"building a bench.")
    if bench and bench >= 1.0:
        return "sell more", (
            f"Around {bench:,.1f} FTE of capacity in this line that the "
            f"pipeline is not filling. The constraint here is demand, not "
            f"supply, and it is the most expensive kind of spare capacity "
            f"because it is fully loaded and not chargeable.")
    if bench and bench >= 0.4:
        return "cross-train", (
            f"About {bench:,.1f} FTE of slack here against pressure "
            f"elsewhere. Cross-training moves capacity without changing "
            f"headcount, and it is the only lever that works inside a "
            f"quarter.")
    return "hold", ("Demand and supply are within a rounding error of each "
                    "other across the horizon. Nothing to do beyond keeping "
                    "the forecast current.")


# ---------------------------------------------------------------------------
def build(con, org_id, as_of, run_id=None):
    horizon_start = dt.date.fromisoformat(as_of).replace(day=1)
    horizon = months_from(horizon_start, PLAN_HORIZON_MONTHS)

    con.execute("""DELETE FROM resource_plan_sensitivity WHERE plan_line_id IN
                   (SELECT plan_line_id FROM resource_plan_line WHERE plan_id IN
                    (SELECT plan_id FROM resource_plan WHERE org_id=?))""",
                (org_id,))
    con.execute("""DELETE FROM resource_plan_action WHERE plan_line_id IN
                   (SELECT plan_line_id FROM resource_plan_line WHERE plan_id IN
                    (SELECT plan_id FROM resource_plan WHERE org_id=?))""",
                (org_id,))
    con.execute("""DELETE FROM resource_plan_month WHERE plan_line_id IN
                   (SELECT plan_line_id FROM resource_plan_line WHERE plan_id IN
                    (SELECT plan_id FROM resource_plan WHERE org_id=?))""",
                (org_id,))
    con.execute("""DELETE FROM resource_plan_line WHERE plan_id IN
                   (SELECT plan_id FROM resource_plan WHERE org_id=?)""",
                (org_id,))
    con.execute("DELETE FROM resource_plan WHERE org_id=?", (org_id,))

    cur = con.execute("""
        INSERT INTO resource_plan
          (org_id, run_id, plan_name, as_of_date, horizon_months, created_at,
           recruit_lead_weeks, attrition_pct_annual, standard_hours_per_week,
           working_weeks_per_year)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (org_id, run_id, f"Resource requirement, {PLAN_HORIZON_MONTHS} months",
         as_of, PLAN_HORIZON_MONTHS,
         dt.datetime.now().isoformat(timespec="seconds"),
         ORG_ASSUMPTIONS["recruit_lead_weeks"],
         ORG_ASSUMPTIONS["attrition_pct_annual"],
         ORG_ASSUMPTIONS["standard_hours_per_week"],
         ORG_ASSUMPTIONS["working_weeks_per_year"]))
    plan_id = cur.lastrowid
    # Read the assumptions back out of the row that was just written, so the
    # arithmetic below demonstrably runs on stored inputs rather than on the
    # module constants.
    plan = rows(con, "SELECT * FROM resource_plan WHERE plan_id=?",
                (plan_id,))[0]
    attrition_monthly = plan["attrition_pct_annual"] / 100.0 / 12.0

    products = [r["product"] for r in rows(con, """
        SELECT product, COUNT(*) n FROM project
        WHERE org_id=? AND product IS NOT NULL
        GROUP BY 1 ORDER BY n DESC""", (org_id,))]

    made = 0
    for product in products:
        practice_id, practice_label = dominant_practice(con, org_id, product)
        if practice_id is None:
            continue
        ttgl, ttgl_src = time_to_go_live(con, org_id, product)
        heads, fte, util, _who = supply_inputs(
            con, org_id, product, plan["working_weeks_per_year"],
            plan["standard_hours_per_week"])
        if fte <= 0.05:
            continue
        back_month, backlog_total, backlog_window = backlog_by_month(
            con, org_id, product, horizon)
        (pipe_raw, pipe_w, pipe_hours, pipe_weighted, pipe_value,
         pipe_weighted_value, n_opps) = pipeline_by_month(
            con, org_id, product, horizon)

        line = {
            "time_to_go_live_weeks": ttgl,
            "existing_consultants": heads,
            "existing_fte": fte,
            "billable_util_target_pct": util,
            "productive_util_target_pct": min(
                95.0, util + LINE_ASSUMPTIONS["productive_uplift_pts"]),
            "ramp_weeks": LINE_ASSUMPTIONS["ramp_weeks"],
            "ramp_util_pct": LINE_ASSUMPTIONS["ramp_util_pct"],
            "training_hours_per_year": LINE_ASSUMPTIONS["training_hours_per_year"],
            "pipeline_value": pipe_value,
            "pipeline_hours": pipe_hours,
            "pipeline_weighted_hours": pipe_weighted,
            "backlog_hours": backlog_total,
            "avg_deal_hours": round(pipe_hours / n_opps, 1) if n_opps else 0.0,
            "realised_rate": realised_rate(con, org_id, product),
            "_pipeline_month": pipe_w,
        }
        rr = run_rate_hours(con, org_id, product)
        # What this team can bill at the utilisation the work was priced at.
        # The run rate is capped at it for the purpose of the demand floor,
        # and the gap between the two is reported as its own figure.
        #
        # Without the cap, a line delivering slightly above its own target
        # produced a small positive gap in all twelve months, so every line
        # read "short in 12 of 12 months" by an identical fraction of an FTE.
        # That is not a capacity shortfall, it is a team working above the
        # utilisation it was priced at, and the two want completely different
        # conversations. Smearing it across a year as a phantom hiring
        # requirement hides the real month-to-month shape underneath it.
        per_fte_preview = productive_hours_per_fte_month(plan, line)
        sustainable = fte * per_fte_preview
        over_target_pct = ((rr / sustainable - 1) * 100
                           if sustainable > 0 else None)
        floor = rr
        unsold = {}
        demand = {}
        for m in horizon:
            visible = back_month[m] + pipe_w[m]
            demand[m] = expected_demand(visible, floor)
            unsold[m] = demand[m] - visible
        line["_unsold_month"] = unsold
        line["_backlog_month"] = back_month
        line["_run_rate"] = rr

        months, per_fte = run_line(plan, line, demand, horizon,
                                   attrition_monthly)
        demand_total = sum(demand.values())
        supply_total = sum(r["billable_hours"] for r in months)
        gap_total = demand_total - supply_total
        peak = max(months, key=lambda r: r["gap_fte"])
        short = [r for r in months if r["band"] == "short"]
        bench_months = [r for r in months if r["band"] == "bench"]
        bench_fte = (round(-median([r["gap_fte"] for r in bench_months]), 2)
                     if bench_months else None)

        first_gap = short[0]["period_month"] if short else None
        # The date a requisition has to open to land before the gap does.
        # Where that date has already passed, saying so is the finding: the
        # decision window closed, and every week from here is a week of the
        # shortfall that cannot now be avoided by recruiting.
        hire_by, hire_by_passed = None, False
        if first_gap:
            y, m = int(first_gap[:4]), int(first_gap[5:7])
            lead = dt.timedelta(weeks=plan["recruit_lead_weeks"]
                                + line["ramp_weeks"])
            by = dt.date(y, m, 1) - lead
            hire_by = by.isoformat()
            hire_by_passed = by < dt.date.fromisoformat(as_of)

        # How long the shortfall lasts, and how deep it is while it lasts.
        # Both are needed: the same number of FTE-months is a subcontract if
        # it arrives in two months and a hire if it spreads over nine.
        short_months = [r for r in months if r["gap_fte"] > 0.25]
        months_short = len(short_months)
        depth = (round(sum(r["gap_fte"] for r in short_months) / months_short, 2)
                 if months_short else 0.0)
        backlog_months = round(backlog_total / rr, 1) if rr else None
        backfill = round(fte * plan["attrition_pct_annual"] / 100.0
                         * PLAN_HORIZON_MONTHS / 12.0, 2)

        unservable = max(0.0, gap_total)
        rate = line["realised_rate"] or 0.0
        revenue_at_risk = round(min(unservable * rate, pipe_weighted_value), 2)

        verdict, why = verdict_for(months, backlog_months or 0,
                                   backlog_window, bench_fte, over_target_pct)
        # Only a hire verdict carries a hire count, and it is sized on the
        # depth of the sustained shortfall rather than the peak. Reporting a
        # requisition beside a recommendation to sell more capacity, or
        # sizing a permanent hire off a single spiky month, is the kind of
        # contradiction that costs a report its authority.
        hire_count = max(1, int(math.ceil(depth))) if verdict == "hire" else 0

        # Where a line has been running well away from the target it was
        # priced at, that is the explanation for most of what follows and it
        # belongs in the first sentence. A persistent shortfall on a line
        # delivering thirty per cent above its own target is not a headcount
        # problem, it is a target that no longer describes the work, and
        # answering it with a requisition answers the wrong question.
        over_note = ""
        if over_target_pct is not None and over_target_pct >= 12:
            over_note = (
                f"This line has been delivering {over_target_pct:,.0f}% more "
                f"than {fte:,.1f} FTE can bill at a {util:,.0f}% target, so "
                f"most of the shortfall below is the gap between that target "
                f"and what the team is actually doing. Either the target is "
                f"wrong for this line or the hours are not sustainable; "
                f"recruiting against it settles neither question. ")
        elif over_target_pct is not None and over_target_pct <= -20:
            over_note = (
                f"This line has been delivering {abs(over_target_pct):,.0f}% "
                f"less than {fte:,.1f} FTE could bill at a {util:,.0f}% "
                f"target, which is the spare capacity the numbers below "
                f"describe. ")

        statement = (
            over_note
            + f"{product} needs {demand_total:,.0f} hours over "
            f"{PLAN_HORIZON_MONTHS} months against {supply_total:,.0f} the "
            f"current {fte:,.1f} FTE can bill at a {util:,.0f}% target. "
            + (f"It is short in {months_short} of {len(months)} months by an "
               f"average of {depth:,.1f} FTE, peaking at "
               f"{peak['gap_fte']:,.1f} in {peak['period_month']}"
               + ((f". A requisition would have had to open by {hire_by} to "
                   f"land before the gap, so recruitment can no longer "
                   f"prevent it; it can only shorten it."
                   if hire_by_passed else
                   f", so a requisition opened by {hire_by} lands before the "
                   f"gap does.")
                  if hire_by and verdict == "hire" else ".")
               if months_short else
               f"There is no shortfall in the horizon; the line runs "
               f"{abs(gap_total):,.0f} hours under its billable capacity.")
            + (f" {revenue_at_risk:,.0f} of weighted pipeline is at risk if "
               f"nothing changes." if revenue_at_risk > 1000 else ""))

        cur = con.execute("""
            INSERT INTO resource_plan_line
              (plan_id, product_line, practice_id, practice_label,
               time_to_go_live_weeks, time_to_go_live_source,
               existing_consultants, existing_fte, billable_util_target_pct,
               productive_util_target_pct, ramp_weeks, ramp_util_pct,
               training_hours_per_year, pipeline_value, pipeline_hours,
               pipeline_weighted_hours, backlog_hours, avg_deal_hours,
               realised_rate, run_rate_hours_month, over_target_pct,
               backlog_months_at_run_rate, backlog_window_months,
               months_short, avg_gap_when_short, coverage_committed_pct,
               coverage_pipeline_pct, coverage_runrate_pct,
               demand_hours_horizon, supply_hours_horizon,
               gap_hours_horizon, productive_hours_per_fte, required_fte,
               fte_gap, attrition_backfill_fte, hire_count, first_gap_month,
               hire_by_date,
               peak_gap_fte, peak_gap_month, bench_risk_fte, revenue_at_risk,
               verdict, statement)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (plan_id, product, practice_id, practice_label,
             ttgl, ttgl_src, heads, fte, util,
             line["productive_util_target_pct"], line["ramp_weeks"],
             line["ramp_util_pct"], line["training_hours_per_year"],
             round(pipe_value, 2), round(pipe_hours, 1),
             round(pipe_weighted, 1), round(backlog_total, 1),
             line["avg_deal_hours"],
             round(rate, 2) if rate else None,
             round(rr, 1), q2(over_target_pct), backlog_months,
             backlog_window,
             months_short, depth,
             round(sum(back_month.values()) / demand_total * 100, 1)
             if demand_total else None,
             round(sum(pipe_w[m] for m in horizon) / demand_total * 100, 1)
             if demand_total else None,
             round(sum(unsold.values()) / demand_total * 100, 1)
             if demand_total else None,
             round(demand_total, 1), round(supply_total, 1),
             round(gap_total, 1), round(per_fte, 1),
             round(demand_total / (per_fte * PLAN_HORIZON_MONTHS), 2)
             if per_fte else 0.0,
             round(gap_total / (per_fte * PLAN_HORIZON_MONTHS), 2)
             if per_fte else 0.0,
             backfill, hire_count, first_gap, hire_by,
             round(peak["gap_fte"], 2), peak["period_month"], bench_fte,
             revenue_at_risk, verdict, statement))
        line_id = cur.lastrowid
        made += 1

        con.executemany("""
            INSERT INTO resource_plan_month
              (plan_line_id, period_month, backlog_hours, pipeline_hours,
               unsold_hours, demand_hours, visibility_pct, headcount_fte,
               ramping_fte, capacity_hours, productive_hours, billable_hours,
               gap_hours, gap_fte, projected_util_pct, band)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(line_id, r["period_month"], round(back_month[r["period_month"]], 1),
              round(pipe_w[r["period_month"]], 1),
              round(unsold[r["period_month"]], 1), r["demand_hours"],
              round((back_month[r["period_month"]] + pipe_w[r["period_month"]])
                    / r["demand_hours"] * 100, 1) if r["demand_hours"] else None,
              r["headcount_fte"], r["ramping_fte"], r["capacity_hours"],
              r["productive_hours"], r["billable_hours"], r["gap_hours"],
              r["gap_fte"], r["projected_util_pct"], r["band"])
             for r in months])

        con.executemany("""
            INSERT INTO resource_plan_action
              (plan_line_id, rank, action_type, headline, rationale,
               quantity, unit, by_date, value_at_stake)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            [(line_id, i + 1, a["type"], a["headline"], a["rationale"],
              a.get("qty"), a.get("unit"), a.get("by"), a.get("value"))
             for i, a in enumerate(actions_for(
                 product, verdict, why, hire_count, peak, hire_by,
                 hire_by_passed, plan, line, bench_fte, revenue_at_risk,
                 rate))])

        con.executemany("""
            INSERT INTO resource_plan_sensitivity
              (plan_line_id, lever, lever_label, shift, fte_gap_before,
               fte_gap_after, fte_delta, statement)
            VALUES (?,?,?,?,?,?,?,?)""",
            [(line_id, s["lever"], s["lever_label"], s["shift"],
              s["fte_gap_before"], s["fte_gap_after"], s["fte_delta"],
              s["statement"])
             for s in sensitivity(plan, line, demand, horizon,
                                  attrition_monthly,
                                  shortfall_index(months))])
    return plan_id, made


def actions_for(product, verdict, why, hire_count, peak, hire_by,
                hire_by_passed, plan, line, bench_fte, revenue_at_risk, rate):
    """
    What to do, ranked, with the date the decision stops being available.

    A capacity report that ends at the gap leaves the reader to work out the
    lead time themselves, and the lead time is the only part that has a
    deadline attached.
    """
    acts = []
    if verdict == "hire" and hire_count > 0:
        acts.append({
            "type": "hire",
            "headline": f"Open {hire_count} requisition"
                        f"{'s' if hire_count > 1 else ''} in {product}",
            "rationale": (
                f"{why} At {plan['recruit_lead_weeks']:,.0f} weeks to fill and "
                f"{line['ramp_weeks']:,.0f} weeks to full productivity, a "
                f"start date is {plan['recruit_lead_weeks'] + line['ramp_weeks']:,.0f} "
                f"weeks behind the requisition."
                + (" That date has already passed, so this requisition "
                   "shortens the shortfall rather than preventing it, and "
                   "cover is needed in the meantime."
                   if hire_by_passed else "")),
            "qty": hire_count, "unit": "FTE", "by": hire_by,
            "value": revenue_at_risk or None})
    if peak["gap_fte"] > 0.4:
        acts.append({
            "type": "subcontract",
            "headline": f"Hold {min(2, max(1, round(peak['gap_fte']))):,.0f} "
                        f"subcontract FTE against {peak['period_month']}",
            "rationale": (
                "Subcontract cover is the bridge between the gap opening and "
                "a hire becoming productive. It costs margin on those hours "
                "and it protects the delivery date, which is usually the "
                "cheaper of the two."),
            "qty": min(2, max(1, round(peak["gap_fte"]))), "unit": "FTE",
            "by": peak["period_month"] + "-01"})
        acts.append({
            "type": "rephase",
            "headline": f"Rephase or stagger the {peak['period_month']} starts",
            "rationale": (
                "Two implementations starting in the same month against a "
                "single team is a choice, not a constraint. Staggering by "
                "four weeks is free and it removes the peak."),
            "by": peak["period_month"] + "-01"})
    if bench_fte and bench_fte >= 0.4:
        acts.append({
            "type": "cross_train",
            "headline": f"Cross-train {bench_fte:,.1f} FTE out of {product}",
            "rationale": (
                "Capacity sitting in a line the pipeline is not filling is "
                "the most expensive kind of capacity, because it is fully "
                "loaded and not chargeable. Moving it needs a plan, not a "
                "reallocation in a spreadsheet."),
            "qty": bench_fte, "unit": "FTE"})
        acts.append({
            "type": "sell",
            "headline": f"Aim demand generation at {product}",
            "rationale": (
                f"There is billable capacity here at "
                f"{(rate or 0):,.0f} per hour realised. The constraint on "
                f"this line is demand."),
            "value": round((bench_fte or 0) * (rate or 0) * 1500, 2)
            if rate else None})
    if not acts:
        acts.append({
            "type": "hold",
            "headline": f"No resourcing action needed in {product}",
            "rationale": why})
    return acts


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    as_of = one(con, "SELECT MAX(as_of_date) FROM intelligence_run", (),
                default=dt.date.today().isoformat())
    for org_id, code in con.execute(
            "SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        run_id = one(con, "SELECT MAX(run_id) FROM intelligence_run "
                          "WHERE org_id=?", (org_id,), default=None)
        plan_id, made = build(con, org_id, as_of, run_id)
        lines = rows(con, """
            SELECT product_line, verdict, ROUND(peak_gap_fte,1) g, hire_count
            FROM resource_plan_line WHERE plan_id=? ORDER BY peak_gap_fte DESC""",
            (plan_id,))
        print(f"{code}: {made} product lines")
        for l in lines:
            print(f"    {l['product_line']:22s} peak gap {l['g']:>5} FTE  "
                  f"{l['verdict']}  hire {l['hire_count']}")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
