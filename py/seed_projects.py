"""
Project, plan, time, financial and pipeline generation.

Called by seed_core.py. All values are synthetic demonstration data.

The generator is deliberately opinionated about *why* projects go wrong,
because the intelligence engine has to be able to find those causes:
  - methodology carries a base overrun multiplier (Partner-Led is the worst)
  - a handful of consultants are over-subscribed, creating key-person risk
  - the acquisition target has revenue concentrated in a few customers
  - PMs revise forecasts late and by less than the eventual overrun
"""
import json
import random
from datetime import date, timedelta

PHASES = ["Initiate", "Analyse", "Configure", "Test", "Deploy", "Stabilise"]
PHASE_WEIGHTS = [0.08, 0.20, 0.30, 0.22, 0.12, 0.08]

# Where the schedule slip actually lands, and where the extra effort does.
#
# Implementations do not slip evenly. Requirements and configuration absorb
# most of it, testing inherits whatever they leave unresolved, and cutover
# gets compressed rather than extended because the date is usually the one
# thing the customer will not move. Shifting every phase by the same amount
# leaves each phase running exactly its planned duration, which makes the
# question "which phase runs long" unanswerable: the honest answer from that
# data is "all of them equally", and that is never true.
PHASE_SLIP_SHARE = {
    "Initiate": 0.04, "Analyse": 0.24, "Configure": 0.38,
    "Test": 0.25, "Deploy": 0.06, "Stabilise": 0.03,
}
# When the cause is a customer-side dependency, the wait lands earlier and
# the team is stood down rather than working, so duration stretches and
# effort does not.
PHASE_SLIP_SHARE_EXTERNAL = {
    "Initiate": 0.05, "Analyse": 0.46, "Configure": 0.24,
    "Test": 0.16, "Deploy": 0.06, "Stabilise": 0.03,
}
# Effort overrun is not spread evenly either: rework concentrates where
# defects are found, which is test, and where they are caused, which is
# configuration.
PHASE_EFFORT_BIAS = {
    "Initiate": 0.88, "Analyse": 1.02, "Configure": 1.16,
    "Test": 1.28, "Deploy": 0.96, "Stabilise": 1.08,
}
BILLABLE_CATS = ["Consulting", "Configuration", "Testing", "Training",
                 "Project Management", "Data Migration"]
NONBILL_CATS = ["Rework", "Travel", "Over-service", "Internal"]

METHOD_TABLE = {
    "Standard Waterfall": (1.06, 0.30),
    "Agile Hybrid":       (1.04, 0.26),
    "Rapid Deploy":       (1.12, 0.34),
    "Partner-Led":        (1.28, 0.52),
}
BILLING_MODELS = [("Fixed Fee", 0.34), ("Milestone", 0.24),
                  ("Time and Materials", 0.26), ("Capped T&M", 0.12),
                  ("Retainer", 0.04)]

RISK_TEXT = [
    "Customer subject matter experts not available for configuration workshops",
    "Legacy data quality worse than assumed at estimate",
    "Third party integration scope not confirmed",
    "Payroll parallel run variances unresolved",
    "Key customer stakeholder on extended leave",
    "Test environment refresh delayed by hosting provider",
    "Statutory reporting requirement clarified late",
    "Customer requested additional training beyond SOW",
    "Interface specification changed after sign-off",
    "Resource turnover on the delivery team",
    "Benefit plan rules more complex than scoped",
    "UAT defect backlog not burning down",
]
CO_TEXT = [
    "Additional interface to bank file format", "Extra training cohort",
    "Additional legal entity in scope", "Custom report package",
    "Extended hypercare period", "Additional data migration cycle",
    "Scope added for absence management", "Second parallel payroll run",
]


def weighted(rng, pairs):
    r, acc = rng.random(), 0.0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def month_key(d):
    return d.strftime("%Y-%m")


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def bell(i, n):
    """Burn profile: slow start, heavy middle, tail off."""
    if n <= 1:
        return 1.0
    x = (i + 0.5) / n
    return 0.35 + 1.9 * (x ** 0.9) * ((1 - x) ** 0.7) * 3.0


def build(con, s, rng, today, history_start, opco_cfg, ccg_cfg):
    cur = con.cursor()
    for cfg in (opco_cfg, ccg_cfg):
        _build_org(con, cur, s, rng, today, history_start, cfg)
    _seed_audit_samples(cur, rng)
    con.commit()


# ---------------------------------------------------------------------------
def _build_org(con, cur, s, _rng, today, history_start, cfg):
    org_id = cfg["org_id"]
    # Two independent streams. `rp` fixes the shape of the portfolio (which
    # customer, how big, when, how badly it runs); `rng` covers staffing and
    # detail. Keeping them apart means the workforce can be resized to hit a
    # utilisation target without reshuffling every project.
    rp = random.Random(917000 + org_id)
    rng = random.Random(918000 + org_id)
    practices = cfg["practices"]
    emps = cfg["emps"]
    customers = cfg["customers"]
    products = cfg["products"]
    n_projects = cfg["n_projects"]
    rate_pressure = cfg["rate_pressure"]
    legacy = cfg["legacy"]

    by_practice = {}
    for e in emps:
        by_practice.setdefault(e["practice"], []).append(e)
    pms = [e for e in emps if e["role"] in ("Project Manager", "Practice Director",
                                            "Solution Architect")]
    if not pms:
        pms = emps[:4]

    # Key-person concentration: a small set of consultants pulled onto a
    # disproportionate share of work. This is what creates knowledge risk.
    key_people = rng.sample([e for e in emps if e["role"] != "Practice Director"],
                            5 if len(emps) > 20 else 3)

    # Customer revenue concentration for the acquisition target.
    conc = cfg["concentration"]
    if conc > 0:
        anchors = customers[:3]
        cust_weights = []
        for c in customers:
            cust_weights.append(6.0 if c in anchors else 1.0)
    else:
        cust_weights = [1.0 + rp.random() * 1.4 for _ in customers]

    # PM behaviour: a persistent optimism bias per PM, which is what the
    # forecast accuracy engine later detects.
    pm_bias = {e["id"]: rng.uniform(0.86, 1.10) for e in pms}

    # customer difficulty multiplier
    cust_factor = {c["id"]: rp.uniform(0.94, 1.22) for c in customers}
    # two or three genuinely difficult customers
    for c in rp.sample(customers, max(2, len(customers) // 8)):
        cust_factor[c["id"]] = rp.uniform(1.24, 1.48)

    contracts = {}
    time_rows = []
    projects = []
    load = {e["id"]: 0.0 for e in emps}   # running hours assigned, for staffing
    # Load by person and calendar month, which is the grain staffing actually
    # competes at. A running total over the whole horizon says who has had the
    # most work; it does not say who is free in March. Two engagements can each
    # pick the least-loaded consultants by lifetime total and still land the
    # same four people in the same eight weeks, and the day and week ceilings
    # below then quietly refuse the hours that do not fit. The symptom is a
    # project whose task plan says 78% delivered and whose timesheets carry
    # eighteen hours, which reads as a cost efficiency of 9.5 in the earned
    # value view and as a fictional margin everywhere else.
    mload = {}
    MONTH_CAPACITY = 162.5     # the same denominator the FTE estimate uses

    # Hours already booked by each person on each date, across every project.
    # Projects are generated one at a time, so without a shared budget a
    # consultant on four engagements can pick the same Tuesday four times and
    # book thirty-two hours into it. Each entry looks fine on its own; the day
    # does not. This is the grain the day-ceiling check runs at, so it has to
    # be respected here rather than cleaned up afterwards.
    day_used = {}
    DAY_BUDGET = 12.0
    # A day ceiling alone is not enough. Twelve hours a day, five days a week,
    # fifty-two weeks a year is 3,120 hours, and the generator happily
    # produced a consultant booking 3,318 billable hours in a year against a
    # contracted 1,950. Every day was individually plausible and the year was
    # not, which is the same failure one level up: the utilisation report
    # averages out, so nothing looks wrong until somebody reads one person's
    # timesheet. The weekly ceiling is a hard week rather than a standard one,
    # because hard weeks are real and forty-hour-flat years are not.
    week_used = {}
    WEEK_BUDGET = 46.0

    # A timesheet is submitted and approved as a week, not as an entry. Drawing
    # the status per entry left weeks that were part approved and part draft,
    # which is not a state a timesheet can be in, and it made the short-week
    # check fire on weeks that were actually complete.
    week_status = {}

    def status_for(emp_id, wk_start):
        key = (emp_id, wk_start)
        if key not in week_status:
            r = rng.random()
            week_status[key] = ("Approved" if r < 0.965 else
                                "Submitted" if r < 0.986 else
                                "Draft" if r < 0.996 else "Rejected")
        return week_status[key]

    for i in range(n_projects):
        cust = rp.choices(customers, weights=cust_weights, k=1)[0]
        pcode = rp.choice(list(practices.keys()))
        practice_id = practices[pcode]
        product = rp.choice(products[pcode])
        ptype = weighted(rp, [("Implementation", 0.44), ("Upgrade", 0.16),
                               ("Migration", 0.12), ("Integration", 0.12),
                               ("Advisory", 0.10), ("Managed Service", 0.06)])
        methodology = weighted(rp, [("Standard Waterfall", 0.34), ("Agile Hybrid", 0.31),
                                     ("Rapid Deploy", 0.20), ("Partner-Led", 0.15)])
        billing_model = weighted(rp, BILLING_MODELS)
        m_over, m_delay_p = METHOD_TABLE[methodology]

        # size
        band = weighted(rp, [("small", 0.34), ("mid", 0.46), ("large", 0.20)])
        budget_hours = {"small": rp.randint(120, 400),
                        "mid": rp.randint(500, 1600),
                        "large": rp.randint(1800, 3800)}[band]
        # Duration is capped so the arrival window fully covers the reporting
        # period, otherwise the earliest months under-count the long projects
        # that would have been running through them. Duration tracks size so
        # weekly intensity stays in a believable 1-3 FTE range.
        duration_months = max(2, round(budget_hours / 450 + rp.uniform(0.8, 2.5)))
        duration_months = min(duration_months, 14)

        # Priced off the blended rate of the people who will actually deliver
        # it, not off a number of its own. A contract priced at 188 and
        # delivered by a team booking at 197 makes revenue per billable hour
        # come out above the booked rate, so the rate analysis reports
        # recovery where there should be leakage. The two figures have to sit
        # on the same basis or the comparison between them means nothing.
        blended = sum(e["bill"] for e in emps) / max(1, len(emps))
        plan_rate = round(blended * rate_pressure * rp.uniform(0.93, 1.07), 2)
        # Rate realisation: what this engagement was actually sold at against
        # the rate card. Large customers, competitive deals and public tenders
        # all negotiate, and the discount is one of the two things that
        # separates a healthy services book from an unhealthy one.
        #
        # This has to reach the time entries, not just the contract value.
        # Booking every hour at the list rate while the contract was signed at
        # a discount means the engagement over-bills on paper and the question
        # "what is our average bill rate" has only one possible answer, which
        # is the rate card. That is the question this exists to answer.
        discount = rp.uniform(0.78, 1.0)
        if rate_pressure < 1.0:                 # the acquired book discounts harder
            discount *= rp.uniform(0.90, 0.99)
        contract_value = round(budget_hours * plan_rate * discount, 2)
        econ = cfg["econ"][pcode]
        plan_cost_rate = econ["cost_rate"]
        target_margin = econ["target_margin"]
        budget_cost = round(budget_hours * plan_cost_rate, 2)

        # Timeline as a steady arrival process rather than by cohort quota.
        # Projects arrive uniformly across a window that starts well before
        # the reporting history and runs a little into the future; status then
        # falls out of where the project sits relative to today. This is what
        # keeps concurrent load, and therefore utilisation, flat across the
        # 24 months instead of ramping.
        earliest = history_start - timedelta(days=420)
        latest = today + timedelta(days=110)
        # Evenly paced arrivals with jitter, not uniform random draws. A
        # services business books work at a fairly steady rate, and random
        # draws at this sample size cluster badly enough to invent quarters
        # that look like a boom or a drought.
        step = (latest - earliest).days / n_projects
        start = earliest + timedelta(days=int((i + rp.uniform(0.05, 0.95)) * step))
        planned_end = add_months(start, duration_months)
        if start > today:
            cohort = "notstarted"
        elif planned_end <= today - timedelta(days=10):
            cohort = "complete"
        else:
            cohort = "inflight"
        if cohort in ("complete", "inflight") and rp.random() < 0.03:
            cohort = "cancelled"
        planned_go_live = add_months(planned_end, -1)

        # how badly it runs
        overrun = m_over * rp.lognormvariate(0, 0.14) * cust_factor[cust["id"]] ** 0.6
        overrun = max(0.82, min(2.35, overrun))
        delayed = rp.random() < m_delay_p * (1.15 if cust_factor[cust["id"]] > 1.2 else 1.0)
        delay_days = int(rp.uniform(0.06, 0.42) * duration_months * 30) if delayed else \
            int(rp.uniform(-0.04, 0.05) * duration_months * 30)
        # Why a project is late decides whether it also costs more. Waiting on
        # a customer usually means the team was stood down, so hours track
        # progress. Rework and re-testing means hours keep burning while
        # progress does not. Treating every delay as over-burn puts most of the
        # portfolio into budget alarm, which is how a risk engine gets ignored.
        delay_kind = "external" if rp.random() < 0.5 else "execution"

        pm = rng.choice(pms)
        status = {"complete": "Complete", "inflight": "In Flight",
                  "notstarted": "Not Started", "cancelled": "Cancelled"}[cohort]

        actual_end = None
        if cohort == "complete":
            actual_end = planned_end + timedelta(days=delay_days)
            if actual_end > today:
                actual_end = today - timedelta(days=rng.randint(3, 40))
        elif cohort == "cancelled":
            actual_end = start + timedelta(days=int((planned_end - start).days *
                                                    rng.uniform(0.25, 0.7)))

        # Effort and physical progress both follow the same S-curve, and the
        # gap between them is the schedule slip. Deriving hours from the curve
        # and progress from elapsed time (or the reverse) puts a systematic
        # 20-point wedge between them on every project, which any burn-rate
        # model then reads as trouble that is not there.
        full_weeks = max(1, int(((actual_end or planned_end) - start).days / 7))
        profile = [bell(w, full_weeks) for w in range(full_weeks)]
        psum = sum(profile) or 1.0

        def curve(weeks):
            return sum(profile[:max(0, min(full_weeks, int(weeks)))]) / psum

        planned_span = max(30, (planned_end - start).days)
        delay_frac = max(0.0, delay_days / planned_span)
        if cohort == "complete":
            elapsed_weeks, physical = full_weeks, 1.0
        elif cohort == "notstarted":
            elapsed_weeks, physical = 0, 0.0
        else:
            elapsed_weeks = max(1, min(full_weeks,
                                       int((min(actual_end or today, today) - start).days / 7)))
            # hours are booked over the weeks actually elapsed; progress is
            # what the plan says should have been achieved by now, discounted
            # by the slippage
            physical = min(0.97, max(0.02, curve(elapsed_weeks / (1 + delay_frac))))
        if cohort == "complete":
            hours_frac = 1.0
        elif delay_kind == "external":
            hours_frac = physical          # team stood down while waiting
        else:
            hours_frac = curve(elapsed_weeks)   # burning through rework
        actual_hours_total = budget_hours * overrun * hours_frac
        go_live = None
        if cohort == "complete" and ptype in ("Implementation", "Upgrade", "Migration"):
            go_live = (actual_end - timedelta(days=rng.randint(10, 45))).isoformat()

        # health as the PM has set it: PMs under-call trouble, which is the
        # whole reason a predictive score is needed.
        consumed = (actual_hours_total / budget_hours) if budget_hours else 0
        if cohort == "In Flight" or cohort == "inflight":
            over = consumed - physical
            if over > 0.22 and rng.random() < 0.55:
                health = "Red"
            elif over > 0.10 and rng.random() < 0.7:
                health = "Yellow"
            else:
                health = "Green" if rng.random() < 0.85 else "Yellow"
        else:
            health = "Green" if overrun < 1.12 else ("Yellow" if overrun < 1.3 else "Red")

        # contract + sow
        ckey = (cust["id"], billing_model)
        if ckey not in contracts:
            ccode = f"{cfg['prefix']}-CTR-{cust['id']:03d}-{len(contracts)+1}"
            contract_id = s.q(
                """INSERT INTO contract(org_id, legacy_contract_id, contract_code,
                        customer_id, contract_type, billing_model, contract_value,
                        currency, sold_hours, start_date, end_date, payment_terms,
                        remaining_value)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (org_id, f"LEG-CTR-{cust['id']:04d}" if legacy else None, ccode,
                 cust["id"], "SOW", billing_model, 0, 'CAD', 0,
                 start.isoformat(), add_months(start, 36).isoformat(),
                 rng.choice(["Net 30", "Net 45", "Net 60"]), 0))
            contracts[ckey] = contract_id
        contract_id = contracts[ckey]
        sow_id = s.q(
            """INSERT INTO sow(contract_id, legacy_sow_id, sow_code, sow_value,
                               sold_hours, signed_date, version)
               VALUES (?,?,?,?,?,?,1)""",
            (contract_id, f"LEG-SOW-{i+1:04d}" if legacy else None,
             f"{cfg['prefix']}-SOW-{org_id}-{i+1:04d}", contract_value, budget_hours,
             (start - timedelta(days=rng.randint(10, 60))).isoformat()))

        project_id = s.q(
            """INSERT INTO project(org_id, legacy_project_id, project_code, project_name,
                    customer_id, contract_id, sow_id, practice_id, project_type, product,
                    methodology, project_manager_id, billing_model, start_date,
                    planned_end_date, actual_end_date, go_live_date, planned_go_live_date,
                    contract_value, sow_value, budget_hours, budget_cost,
                    project_status, project_health, health_set_by, health_set_on,
                    target_margin_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (org_id, f"LEG-PRJ-{i+1:04d}" if legacy else None,
             f"{cfg['prefix']}-{i+1:04d}",
             f"{cust['name']} - {product} {ptype}",
             cust["id"], contract_id, sow_id, practice_id, ptype, product, methodology,
             pm["id"], billing_model, start.isoformat(), planned_end.isoformat(),
             actual_end.isoformat() if actual_end else None, go_live,
             planned_go_live.isoformat(), contract_value, contract_value,
             budget_hours, budget_cost, status, health, pm["name"],
             (today - timedelta(days=rng.randint(1, 21))).isoformat(), target_margin))

        # ---- team ----
        # Team size follows the work, not the size band. A project burning
        # 600 hours a month needs about four people; staffing it with two
        # produces the 40-hour days that make a dataset useless.
        # Only people who had joined by the time the engagement started. Time
        # booked before somebody's start date is the kind of detail nobody
        # notices in a demo and everybody notices in a diligence review.
        pool = [e for e in by_practice.get(pcode, emps)
                if not e.get("start") or e["start"] <= start] or \
               [e for e in emps if not e.get("start") or e["start"] <= start] or emps
        monthly_burn = budget_hours * overrun / max(1, duration_months)
        fte_needed = monthly_burn / 162.5
        team_size = max(2, min(12, int(round(fte_needed / 0.55)) + rng.randint(0, 1)))
        team = []
        # bias towards the key people so knowledge risk is real
        for kp in key_people:
            if kp.get("start") and kp["start"] > start:
                continue
            if rng.random() < (0.30 if cfg["concentration"] > 0 else 0.12):
                team.append(kp)
        candidates = [e for e in pool if e not in team] or [e for e in emps if e not in team]

        # The months this engagement actually burns hours in. Staffing has to
        # be judged against these and not against a lifetime total, because
        # capacity is consumed week by week and cannot be borrowed from a
        # quarter that has already gone.
        burn_end = min(actual_end or planned_end, today)
        burn_months = []
        _m = start.replace(day=1)
        while _m <= burn_end:
            burn_months.append(month_key(_m))
            _m = add_months(_m, 1)
        if not burn_months:
            burn_months = [month_key(start)]
        window_capacity = MONTH_CAPACITY * len(burn_months)

        def concurrent_load(e):
            return sum(mload.get((e["id"], m), 0.0) for m in burn_months)

        # Staff from the least-loaded half of the practice, where "loaded"
        # means loaded in these months. Picking uniformly at random leaves a
        # quarter of the workforce booking over 100% of capacity for two years,
        # which no resource manager would allow and no utilisation report
        # should show.
        candidates.sort(key=lambda e: (concurrent_load(e), load[e["id"]]))
        # If the practice cannot field the team without pushing people past
        # their own capacity in this window, look across the whole workforce
        # before overbooking the practice. A services business borrows from the
        # next practice along; it does not book somebody at 160%.
        need = min(team_size, len(candidates))
        room_left = sum(1 for e in candidates
                        if concurrent_load(e) < window_capacity * 0.85)
        if room_left < need:
            wider = [e for e in emps if e not in team and e not in candidates
                     and (not e.get("start") or e["start"] <= start)]
            if wider:
                candidates = candidates + sorted(
                    wider, key=lambda e: (concurrent_load(e), load[e["id"]]))
                candidates.sort(key=lambda e: (concurrent_load(e), load[e["id"]]))
        short_list = candidates[:max(team_size, len(candidates) // 2)]
        team += rng.sample(short_list, min(team_size, len(short_list)))
        team = list({e["id"]: e for e in team}.values())
        if pm not in team:
            team.append(pm)
        per_person = budget_hours * overrun / max(1, len(team))
        # Reserve against the months, using the hours this engagement will
        # actually book by today rather than its whole lifetime effort. The
        # rest of the plan is somebody else's problem in a later month.
        per_person_month = (actual_hours_total / max(1, len(team))
                            / len(burn_months))
        for e in team:
            load[e["id"]] += per_person
            for m in burn_months:
                mload[(e["id"], m)] = mload.get((e["id"], m), 0.0) + per_person_month

        # Allocation is the share of a person's week the work actually needs,
        # derived from hours over the delivery window. Setting it to 1/team-size
        # understates it by about half, which then makes every project look
        # short of capacity.
        duration_weeks = max(1.0, (planned_end - start).days / 7.0)
        for e in team:
            share = 1.0 / len(team)
            person_hours = budget_hours * share
            alloc = person_hours / (37.5 * duration_weeks) * 100
            s.q("""INSERT INTO assignment(project_id, employee_id, role_on_project,
                        start_date, end_date, planned_hours, allocation_pct, cost_rate, bill_rate)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (project_id, e["id"],
                 "Project Manager" if e["id"] == pm["id"] else e["role"],
                 start.isoformat(), planned_end.isoformat(),
                 round(person_hours, 1),
                 round(min(100.0, max(5.0, alloc * rng.uniform(0.85, 1.2))), 1),
                 e["cost"], e["bill"]))

        # ---- plan versions and tasks ----
        n_versions = 1 + (1 if (cohort == "inflight" and rng.random() < 0.45) else 0)
        version_ids = []
        for v in range(1, n_versions + 1):
            vid = s.q("""INSERT INTO project_plan_version(project_id, version_no, is_baseline,
                              is_current, created_on, created_by, change_reason)
                         VALUES (?,?,?,?,?,?,?)""",
                      (project_id, v, 1 if v == 1 else 0, 1 if v == n_versions else 0,
                       (start - timedelta(days=5) if v == 1 else
                        start + timedelta(days=int((planned_end - start).days * 0.45))
                        ).isoformat(),
                       pm["name"],
                       "Initial baseline" if v == 1 else
                       rng.choice(["Re-plan after change order",
                                   "Re-plan following UAT slippage",
                                   "Re-sequenced to customer availability"])))
            version_ids.append(vid)
        current_vid = version_ids[-1]

        task_rows = []
        phase_start = start
        # Actual dates run on their own clock: each phase starts when the
        # previous one actually finished, not when the plan said it should.
        # That is what makes slip compound down a plan, and it is why a
        # cutover date slips even when nobody has slipped the cutover.
        actual_cursor = start
        total_days = max(30, (planned_end - start).days)
        slip_share = (PHASE_SLIP_SHARE_EXTERNAL if delay_kind == "external"
                      else PHASE_SLIP_SHARE)
        # Normalise the effort bias so redistributing overrun between phases
        # does not change how much overrun the engagement has in total.
        bias_norm = sum(PHASE_WEIGHTS[i] * PHASE_EFFORT_BIAS[p]
                        for i, p in enumerate(PHASES))
        for pi, phase in enumerate(PHASES):
            pw = PHASE_WEIGHTS[pi]
            ph_days = max(7, int(total_days * pw))
            ph_end = phase_start + timedelta(days=ph_days)
            # This phase's own stretch, plus a little noise so the analysis is
            # reading a distribution rather than a formula.
            ph_slip = delay_days * slip_share[phase] * rng.uniform(0.55, 1.5)
            actual_days = max(4, int(round(ph_days + ph_slip)))
            # Named for the phase, not the project. These used to be called
            # actual_start / actual_end, which shadowed the project's own
            # actual end date for everything after this loop: the financial
            # and forecast builders were being handed the last phase's finish
            # and treating it as the engagement's.
            ph_actual_start = actual_cursor
            ph_actual_end = ph_actual_start + timedelta(days=actual_days)
            effort_bias = PHASE_EFFORT_BIAS[phase] / bias_norm
            if delay_kind == "external" and phase in ("Analyse", "Configure"):
                # Waiting, not working: the duration went up and the hours
                # did not.
                effort_bias *= 0.82
            n_tasks = rng.randint(2, 4)
            for ti in range(n_tasks):
                is_ms = 1 if ti == n_tasks - 1 else 0
                planned_hours = budget_hours * pw / n_tasks
                # task-level progress derived from where the project is
                phase_mid_frac = sum(PHASE_WEIGHTS[:pi]) + pw * (ti + 1) / n_tasks
                if physical >= phase_mid_frac:
                    pc, tstatus = 100.0, "Complete"
                elif physical > phase_mid_frac - pw / n_tasks:
                    pc = round(max(5.0, min(95.0, (physical - (phase_mid_frac - pw / n_tasks))
                                            / (pw / n_tasks) * 100)), 1)
                    tstatus = "Blocked" if rng.random() < 0.08 else "In Progress"
                else:
                    pc, tstatus = 0.0, "Not Started"
                if cohort == "cancelled" and tstatus != "Complete":
                    tstatus = "Cancelled"
                # The task's slice of the phase, on both clocks.
                t_from = ti / n_tasks
                t_to = (ti + 1) / n_tasks
                t_planned_start = phase_start + timedelta(
                    days=int(ph_days * t_from))
                t_planned_end = phase_start + timedelta(
                    days=max(1, int(ph_days * t_to)))
                t_actual_start = ph_actual_start + timedelta(
                    days=int(actual_days * t_from))
                t_actual_end = ph_actual_start + timedelta(
                    days=max(1, int(actual_days * t_to)))
                task_rows.append((
                    project_id, current_vid,
                    f"LEG-TSK-{project_id}-{len(task_rows)+1}" if legacy else None,
                    f"T{len(task_rows)+1:03d}",
                    f"{phase}: {rng.choice(['Workshops','Configuration','Build','Test cycle','Sign-off','Data load','Training','Cutover prep'])}",
                    phase, is_ms,
                    1 if (is_ms and phase in ("Test", "Deploy")) else 0,
                    ("Billing" if (is_ms and billing_model in ("Milestone", "Fixed Fee")
                                   and rng.random() < 0.7) else ("Delivery" if is_ms else None)),
                    t_planned_start.isoformat(), t_planned_end.isoformat(),
                    t_actual_start.isoformat() if pc > 0 else None,
                    t_actual_end.isoformat() if pc >= 100 else None,
                    round(planned_hours, 1),
                    round(planned_hours * overrun * effort_bias * (pc / 100.0), 1),
                    tstatus, pc, 0.0,
                    ("Invoiced" if pc >= 100 else "Not Ready") if is_ms else None))
            phase_start = ph_end
            actual_cursor = ph_actual_end
        cur.executemany(
            """INSERT INTO project_task(project_id, plan_version_id, legacy_task_id,
                    task_code, task_name, phase, is_milestone, is_critical, milestone_type,
                    planned_start, planned_end, actual_start, actual_end, planned_hours,
                    actual_hours, status, percent_complete, billing_amount, billing_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", task_rows)

        # spread billing amounts across billing milestones
        bms = cur.execute(
            """SELECT task_id FROM project_task
                WHERE project_id=? AND milestone_type='Billing'""", (project_id,)).fetchall()
        if bms:
            per = contract_value / len(bms)
            cur.executemany("UPDATE project_task SET billing_amount=? WHERE task_id=?",
                            [(round(per, 2), r[0]) for r in bms])

        # keep a superseded baseline for a subset so plan versioning is visible
        if n_versions > 1:
            base_rows = []
            for tr in task_rows:
                base_rows.append((tr[0], version_ids[0], tr[2], tr[3], tr[4], tr[5],
                                  tr[6], tr[7], tr[8], tr[9], tr[10], None, None,
                                  tr[13], 0.0, "Not Started", 0.0, 0.0, None))
            cur.executemany(
                """INSERT INTO project_task(project_id, plan_version_id, legacy_task_id,
                        task_code, task_name, phase, is_milestone, is_critical, milestone_type,
                        planned_start, planned_end, actual_start, actual_end, planned_hours,
                        actual_hours, status, percent_complete, billing_amount, billing_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", base_rows)

        # ---- change orders ----
        co_hours_total = 0.0
        co_value_total = 0.0
        n_co = 0
        if cohort in ("complete", "inflight"):
            n_co = min(4, max(0, int(rng.lognormvariate(0.1, 0.9) *
                                     (1.6 if overrun > 1.2 else 0.8))))
        for ci in range(n_co):
            co_hours = round(budget_hours * rng.uniform(0.03, 0.14), 1)
            co_value = round(co_hours * plan_rate * rng.uniform(0.85, 1.05), 2)
            co_status = weighted(rng, [("Approved", 0.66), ("Submitted", 0.18),
                                       ("Draft", 0.09), ("Rejected", 0.07)])
            raised = start + timedelta(days=int(total_days * rng.uniform(0.2, 0.85)))
            s.q("""INSERT INTO change_order(project_id, legacy_change_order_id, co_code,
                        description, co_value, co_hours, raised_date, approved_date, status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (project_id, f"LEG-CO-{project_id}-{ci+1}" if legacy else None,
                 f"CO-{ci+1:02d}", rng.choice(CO_TEXT), co_value, co_hours,
                 raised.isoformat(),
                 (raised + timedelta(days=rng.randint(5, 30))).isoformat()
                 if co_status == "Approved" else None, co_status))
            if co_status == "Approved":
                co_hours_total += co_hours
                co_value_total += co_value

        # ---- risks and issues ----
        n_ri = 0 if cohort == "notstarted" else rng.randint(0, 7 if overrun > 1.2 else 4)
        for ri in range(n_ri):
            etype = weighted(rng, [("Issue", 0.5), ("Risk", 0.34),
                                   ("Dependency", 0.1), ("Assumption", 0.06)])
            open_prob = 0.62 if cohort == "inflight" else 0.12
            st = "Open" if rng.random() < open_prob else \
                weighted(rng, [("Closed", 0.7), ("Mitigating", 0.2), ("Accepted", 0.1)])
            ident = start + timedelta(days=int(total_days * rng.uniform(0.1, 0.9)))
            s.q("""INSERT INTO risk_issue(project_id, legacy_risk_id, ref_code, entry_type,
                        description, date_identified, owner, priority, impact, probability,
                        status, resolution, due_date, is_customer_raised)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (project_id, f"LEG-RI-{project_id}-{ri+1}" if legacy else None,
                 f"{'ISS' if etype == 'Issue' else 'RSK'}-{ri+1:02d}", etype,
                 rng.choice(RISK_TEXT), ident.isoformat(), pm["name"],
                 weighted(rng, [("Medium", 0.42), ("High", 0.3),
                                ("Low", 0.18), ("Critical", 0.10)]),
                 rng.randint(2, 5), rng.randint(2, 5), st,
                 "Resolved with customer" if st == "Closed" else None,
                 (ident + timedelta(days=rng.randint(10, 60))).isoformat(),
                 1 if rng.random() < 0.45 else 0))

        # ---- time entries ----
        # The burn profile covers the whole duration; only the elapsed weeks
        # are emitted. Otherwise every in-flight project looks like it is
        # winding down in the current month.
        hours_placed = 0.0
        if actual_hours_total > 0:
            planned_total = budget_hours * overrun
            nb_rate = 0.05 + max(0.0, (overrun - 1.0)) * 0.28

            # Pass 1: how many hours each person books in each week.
            weekly = []
            for w in range(elapsed_weeks):
                wk_start = start + timedelta(days=7 * w)
                if wk_start > today:
                    break
                wk_hours = planned_total * profile[w] / psum
                for e in team:
                    if e.get("start") and wk_start < e["start"]:
                        continue                     # not yet employed
                    if rng.random() < 0.12:          # not everyone every week
                        continue
                    e_hours = wk_hours / len(team) * rng.uniform(0.55, 1.5)
                    if e_hours >= 0.5:
                        weekly.append([wk_start, e, e_hours])

            # Scale the weekly amounts, not the daily ones, so the day split
            # below is the last thing that happens and the per-day cap holds.
            booked = sum(x[2] for x in weekly)
            if booked > 0:
                factor = actual_hours_total / booked
                for x in weekly:
                    x[2] *= factor

            # Pass 2: split each person-week across the days actually worked.
            # Day count follows the hours, so nobody books a 40-hour Tuesday.
            project_rows = []
            # Invoicing catches up with delivery: an hour booked last week may
            # well be unbilled, an hour booked two years ago on a closed
            # engagement should not be. A flat probability would leave stale
            # unbilled value on most of the book, which would make the ageing
            # check fire on everything and therefore mean nothing. The residue
            # left behind is deliberate and small: real leakage to find.
            leaky = rng.random() < 0.06        # this engagement bills badly

            def emit(e, day, want):
                """
                One timesheet line, or nothing if the person has no room left
                on that day or in that week. Returns the hours actually taken,
                so the caller can tell the difference between hours delivered
                and hours wished for.
                """
                key = (e["id"], day)
                wkey = (e["id"], day - timedelta(days=day.weekday()))
                room = min(DAY_BUDGET - day_used.get(key, 0.0),
                           WEEK_BUDGET - week_used.get(wkey, 0.0))
                h = round(min(want, room), 2)
                if h < 0.25:
                    return 0.0
                day_used[key] = day_used.get(key, 0.0) + h
                week_used[wkey] = week_used.get(wkey, 0.0) + h
                nb = rng.random() < nb_rate
                cat = rng.choice(NONBILL_CATS) if nb else (
                    "Project Management" if e["id"] == pm["id"]
                    else rng.choice(BILLABLE_CATS))
                age_days = (today - day).days
                if leaky:
                    p_invoiced = 0.62
                elif age_days <= 30:
                    p_invoiced = 0.35         # inside the current billing run
                elif age_days <= 60:
                    p_invoiced = 0.86
                elif age_days <= 120:
                    p_invoiced = 0.985
                else:
                    p_invoiced = 0.998        # long since billed or written off
                approval = status_for(e["id"],
                                      day - timedelta(days=day.weekday()))
                project_rows.append([
                    org_id, None, e["id"], project_id, None, day.isoformat(), h,
                    0 if nb else 1, cat, approval,
                    e["cost"], round(e["bill"] * discount, 2),
                    1 if (not nb and approval == "Approved"
                          and rng.random() < p_invoiced) else 0])
                return h

            shortfall = 0.0
            for wk_start, e, e_hours in weekly:
                n_days = max(1, min(5, int(e_hours / 8.0) + 1))
                target = e_hours / n_days
                remaining = e_hours
                # Preferred days first, then the rest of the week, then the
                # following week if this one is already full.
                order = rng.sample(range(5), 5) + [7, 8, 9, 10, 11]
                for dd in order:
                    if remaining < 0.25:
                        break
                    day = wk_start + timedelta(days=dd)
                    if day > today or (e.get("start") and day < e["start"]):
                        continue
                    remaining -= emit(e, day, min(target, remaining))
                shortfall += max(0.0, remaining)

            # Reconciliation.
            #
            # Hours the ceilings refused above are not hours the engagement did
            # not need. Dropping them silently is what put a 78%-complete task
            # plan next to eighteen booked hours, and the earned value view
            # reads that as a cost efficiency of 9.5 while the margin view
            # reads it as free delivery. Sweep the delivery window day by day
            # for room on the people already on the engagement and place the
            # remainder. Anything still refused after that is a real capacity
            # refusal, and it is recorded on the project rather than hidden.
            if shortfall >= 1.0:
                sweep_end = min(today, burn_end)
                sweep = []
                d = start
                while d <= sweep_end:
                    if d.weekday() < 5:
                        sweep.append(d)
                    d += timedelta(days=1)
                rng.shuffle(sweep)
                roster = list(team)
                for day in sweep:
                    if shortfall < 1.0:
                        break
                    rng.shuffle(roster)
                    for e in roster:
                        if shortfall < 1.0:
                            break
                        if e.get("start") and day < e["start"]:
                            continue
                        shortfall -= emit(e, day, min(4.0, shortfall))
            if legacy:
                base = len(time_rows)
                for k, r in enumerate(project_rows):
                    r[1] = f"LEG-TE-{base + k + 1:07d}"
            time_rows.extend(tuple(r) for r in project_rows)
            hours_placed = sum(r[6] for r in project_rows)

        projects.append({
            "hours_intended": round(actual_hours_total, 1),
            "hours_placed": round(hours_placed, 1),
            "project_id": project_id, "cohort": cohort, "start": start,
            "planned_end": planned_end, "actual_end": actual_end,
            "budget_hours": budget_hours, "contract_value": contract_value,
            "co_value": co_value_total, "co_hours": co_hours_total,
            "physical": physical, "overrun": overrun, "billing_model": billing_model,
            "pm_id": pm["id"], "pm_bias": pm_bias[pm["id"]], "plan_rate": plan_rate,
            "target_margin": target_margin, "delay_days": delay_days,
            "budget_cost": budget_cost, "plan_cost_rate": plan_cost_rate,
            "delay_kind": delay_kind,
            "duration_months": duration_months,
        })

    cur.executemany(
        """INSERT INTO time_entry(org_id, legacy_time_entry_id, employee_id, project_id,
                task_id, entry_date, hours, is_billable, time_category, approval_status,
                cost_rate, bill_rate, invoiced)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", time_rows)
    con.commit()
    print(f"  org {org_id}: {len(projects)} projects, {len(time_rows)} time entries")

    _internal_time(cur, rng, org_id, emps, history_start, today, day_used,
                   DAY_BUDGET, week_used, WEEK_BUDGET, status_for)
    _financials(cur, rng, projects, today)
    _forecast_snapshots(cur, rng, projects)
    _pipeline(cur, rng, org_id, practices, customers, products, today, rate_pressure)
    con.commit()


# ---------------------------------------------------------------------------
def _internal_time(cur, rng, org_id, emps, history_start, today, day_used,
                   day_budget, week_used, week_budget, status_for):
    """
    Non-project time, generated weekly rather than monthly and topped up
    against what the person already booked to projects that week.

    The monthly version of this left most person-weeks looking two thirds
    empty, because project time is weekly and internal time was not. That is
    not a cosmetic problem: an incomplete timesheet week understates revenue
    and overstates utilisation at the same time, since the denominator is a
    full week either way. A week has to add up before anything derived from it
    means anything, so bench time fills the gap the way it does in practice.
    """
    rows = []
    booked_week = {}
    for (eid, day), h in day_used.items():
        wk = day - timedelta(days=day.weekday())
        booked_week[(eid, wk)] = booked_week.get((eid, wk), 0.0) + h

    # Start from the earliest week anybody actually booked to a project, not
    # from the reporting-history start date. Projects run back further than
    # the reporting window, and filling only the window left every earlier
    # week two thirds empty.
    first = min([wk for (_, wk) in booked_week] + [history_start])
    wk_start = first - timedelta(days=first.weekday())
    while wk_start <= today:
        for e in emps:
            if e.get("start") and wk_start < e["start"]:
                continue           # nobody books time before they join
            cap = e.get("capacity", 37.5) or 37.5
            booked = booked_week.get((e["id"], wk_start), 0.0)
            # Roughly five weeks of leave a year, taken in whole days.
            on_leave = rng.random() < 0.09
            gap = cap - booked
            if on_leave and gap > 6:
                pto = min(gap, round(rng.choice([7.5, 15.0, 22.5, 37.5]), 2))
                gap -= pto
                _place(rows, rng, org_id, e, wk_start, pto, "PTO", today,
                       day_used, day_budget, week_used, week_budget,
                       status_for)
            if gap > 0.5:
                # Bench and enablement absorb what delivery did not, but a
                # busy week still carries a little internal load.
                fill = gap if gap > 4 else gap * rng.uniform(0.35, 0.9)
                _place(rows, rng, org_id, e, wk_start, round(fill, 2),
                       rng.choice(NONBILL_CATS) if rng.random() < 0.45
                       else "Internal", today, day_used, day_budget,
                       week_used, week_budget, status_for)
        wk_start += timedelta(days=7)
    cur.executemany(
        """INSERT INTO time_entry(org_id, legacy_time_entry_id, employee_id, project_id,
                task_id, entry_date, hours, is_billable, time_category, approval_status,
                cost_rate, bill_rate, invoiced)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def _place(rows, rng, org_id, e, wk_start, hours, cat, today, day_used,
           day_budget, week_used, week_budget, status_for):
    """Spread `hours` across the week's weekdays, respecting the day budget."""
    remaining = hours
    n_days = max(1, min(5, int(hours / 7.5) + 1))
    target = hours / n_days
    for dd in rng.sample(range(5), 5):
        if remaining < 0.25:
            break
        day = wk_start + timedelta(days=dd)
        if day > today:
            continue
        key = (e["id"], day)
        wkey = (e["id"], wk_start)
        room = min(day_budget - day_used.get(key, 0.0),
                   week_budget - week_used.get(wkey, 0.0))
        if room < 0.25:
            continue
        h = round(min(target, remaining, room), 2)
        if h < 0.25:
            continue
        day_used[key] = day_used.get(key, 0.0) + h
        week_used[wkey] = week_used.get(wkey, 0.0) + h
        remaining -= h
        rows.append((org_id, None, e["id"], None, None, day.isoformat(),
                     h, 0, cat, status_for(e["id"], wk_start), e["cost"],
                     e["bill"], 0))


def _financials(cur, rng, projects, today):
    """
    Monthly ledger built from the time actually booked, so the financials and
    the timesheets cannot disagree.

    Recognition: percent-complete on fixed-price work (physical progress from
    the plan), as-incurred on T&M. Invoicing lags recognition, which is what
    creates unbilled backlog.
    """
    rows, rr_rows = [], []
    for p in projects:
        pid = p["project_id"]
        monthly = cur.execute(
            """SELECT strftime('%Y-%m', entry_date) AS m,
                      SUM(hours * COALESCE(cost_rate,0)),
                      SUM(CASE WHEN is_billable=1 THEN hours*COALESCE(bill_rate,0) ELSE 0 END),
                      SUM(hours)
                 FROM time_entry
                WHERE project_id=? AND approval_status='Approved'
                GROUP BY m ORDER BY m""", (pid,)).fetchall()
        if not monthly:
            continue
        total_hours = sum(r[3] for r in monthly) or 1.0
        total_revenue = p["contract_value"] + p["co_value"]
        recognisable = total_revenue * min(1.0, p["physical"])
        method = "As Incurred" if p["billing_model"] in ("Time and Materials", "Capped T&M") \
            else ("Milestone" if p["billing_model"] == "Milestone" else "Percent Complete")
        # Invoicing is a lagging function of *cumulative* recognition, not an
        # independent haircut on each month.
        #
        # Taking 82-100% of each month's recognition and never catching up
        # compounds into a permanent unbilled receivable: the book showed
        # 25.6M recognised against 22.6M invoiced with a 3.0M gap that never
        # closed, including on engagements that finished two years ago. No
        # services business carries that. Invoicing runs behind delivery and
        # then catches up, and on a closed engagement it has either caught up
        # or the difference was written off on purpose.
        #
        # The shape of the lag is the billing model, which is the whole point
        # of reporting the two side by side. Time and materials bills in
        # arrears, so it always sits unbilled by roughly a cycle. Milestone
        # billing is lumpy and can land either side of delivery. Fixed fee and
        # retainer bill to a schedule, so they can and do run ahead, which is
        # deferred revenue rather than a receivable.
        cum_rec = 0.0
        cum_inv = 0.0
        prior_rec = 0.0
        closed = p["cohort"] in ("complete", "cancelled")
        # A minority of engagements genuinely leak: work delivered, never
        # billed, written off at closure. That is a finding, not noise.
        leaks = closed and rng.random() < 0.07
        for idx, (m, labor_cost, billable_value, hours) in enumerate(monthly):
            labor_cost = labor_cost or 0.0
            billable_value = billable_value or 0.0
            other_cost = round(labor_cost * rng.uniform(0.01, 0.05), 2)
            if method == "As Incurred":
                rec = billable_value
                if p["billing_model"] == "Capped T&M":
                    room = max(0.0, total_revenue - cum_rec)
                    rec = min(rec, room)
            else:
                rec = recognisable * (hours / total_hours)
            cum_rec += rec
            last = idx == len(monthly) - 1

            if p["billing_model"] in ("Time and Materials", "Capped T&M"):
                # Bill last month's delivery this month.
                target = prior_rec if idx else 0.0
            elif p["billing_model"] == "Milestone":
                # Bill on events: nothing in some months, a catch-up in others.
                target = cum_rec * (rng.uniform(0.72, 1.0) if idx % 2
                                    else rng.uniform(0.95, 1.18))
            else:
                # A schedule, which drifts either side of delivery.
                target = cum_rec * rng.uniform(0.92, 1.12)

            if last and closed:
                # Closure settles the account, except where it was written off.
                target = cum_rec * (rng.uniform(0.88, 0.97) if leaks else 1.0)
            target = min(target, total_revenue)
            inv = max(0.0, target - cum_inv)
            cum_inv += inv
            prior_rec = cum_rec
            budget_amt = p["budget_cost"] * (hours / total_hours)
            rows.append((pid, m, total_revenue, round(inv, 2), round(rec, 2),
                         round(labor_cost, 2), other_cost, round(budget_amt, 2),
                         round(budget_amt * rng.uniform(0.98, 1.18), 2),
                         round(labor_cost + other_cost, 2)))
            posted = 1 if m < today.strftime("%Y-%m") else 0
            # A posted row without a posting date cannot be tested for
            # restatement, which is the whole point of locking a period. The
            # close runs a few business days into the following month; a small
            # number are left undated on purpose, because that is the finding.
            posted_on = None
            if posted and rng.random() < 0.985:
                y, mm = int(m[:4]), int(m[5:7])
                close = add_months(date(y, mm, 1), 1) + timedelta(
                    days=rng.randint(2, 8))
                posted_on = close.isoformat()
            rr_rows.append((pid, m, method, round(rec, 2),
                            round(max(0.0, total_revenue - cum_rec), 2),
                            posted, posted_on))
    cur.executemany(
        """INSERT INTO project_financial_month(project_id, period_month, contract_value,
                invoiced_revenue, recognized_revenue, labor_cost, other_cost,
                budget_amount, forecast_amount, actual_amount)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    cur.executemany(
        """INSERT OR IGNORE INTO revenue_recognition(project_id, period_month, method,
                recognized_amount, deferred_amount, posted, posted_on)
           VALUES (?,?,?,?,?,?,?)""", rr_rows)


def _forecast_snapshots(cur, rng, projects):
    """
    Original / Revised / Final forecasts. PMs revise late and by less than the
    eventual overrun, weighted by each PM's optimism bias. This is the input
    the forecast accuracy engine measures.
    """
    rows = []
    for p in projects:
        pid = p["project_id"]
        bh = p["budget_hours"] + p["co_hours"]
        rev = p["contract_value"] + p["co_value"]
        plan_cost = p["budget_cost"] + p["co_hours"] * p["plan_cost_rate"]
        rows.append((pid, p["start"].isoformat(), "Original", round(bh, 1),
                     round(plan_cost, 2), round(rev, 2),
                     round((rev - plan_cost) / rev * 100, 2) if rev else None,
                     p["planned_end"].isoformat(), p["pm_id"]))
        if p["physical"] > 0.45:
            revised_at = p["start"] + timedelta(
                days=int(((p["actual_end"] or p["planned_end"]) - p["start"]).days * 0.62))
            called = 1 + (p["overrun"] - 1) * 0.55 * p["pm_bias"]
            fh = bh * max(0.9, called)
            fc = fh * p["plan_cost_rate"]
            fm = (rev - fc) / rev * 100 if rev else 0
            rows.append((pid, revised_at.isoformat(), "Revised", round(fh, 1),
                         round(fc, 2), round(rev, 2), round(fm, 2),
                         (p["planned_end"] + timedelta(
                             days=int(p["delay_days"] * 0.6))).isoformat(), p["pm_id"]))
        if p["cohort"] == "complete":
            ah = cur.execute(
                """SELECT COALESCE(SUM(hours),0), COALESCE(SUM(hours*COALESCE(cost_rate,0)),0)
                     FROM time_entry WHERE project_id=? AND approval_status='Approved'""",
                (pid,)).fetchone()
            am = (rev - ah[1]) / rev * 100 if rev else 0
            rows.append((pid, p["actual_end"].isoformat(), "Final", round(ah[0], 1),
                         round(ah[1], 2), round(rev, 2), round(am, 2),
                         p["actual_end"].isoformat(), p["pm_id"]))
    cur.executemany(
        """INSERT OR IGNORE INTO project_forecast_snapshot(project_id, snapshot_date,
                snapshot_type, forecast_hours, forecast_cost, forecast_revenue,
                forecast_margin_pct, forecast_end_date, submitted_by_employee_id)
           VALUES (?,?,?,?,?,?,?,?,?)""", rows)


def _pipeline(cur, rng, org_id, practices, customers, products, today, rate_pressure):
    rows = []
    n = 46 if rate_pressure >= 1.0 else 18
    for i in range(n):
        pcode = rng.choice(list(practices.keys()))
        cust = rng.choice(customers) if rng.random() < 0.7 else None
        hours = rng.choice([rng.randint(200, 700), rng.randint(800, 2600),
                            rng.randint(2800, 7000)])
        value = round(hours * 188 * rate_pressure * rng.uniform(0.88, 1.02), 2)
        stage = weighted(rng, [("Qualify", 0.22), ("Discover", 0.24), ("Propose", 0.26),
                               ("Negotiate", 0.16), ("Closed Won", 0.08),
                               ("Closed Lost", 0.04)])
        prob = {"Qualify": 15, "Discover": 30, "Propose": 55, "Negotiate": 75,
                "Closed Won": 100, "Closed Lost": 0}[stage]
        close = add_months(today, rng.randint(0, 6))
        rows.append((org_id, f"OPP-{org_id}-{i+1:04d}",
                     cust["id"] if cust else None,
                     None if cust else rng.choice(["Undisclosed prospect",
                                                   "Referral prospect"]),
                     f"{(cust['name'] if cust else 'New prospect')} - "
                     f"{rng.choice(products[pcode])} "
                     f"{rng.choice(['Implementation','Upgrade','Integration','Advisory'])}",
                     practices[pcode], rng.choice(products[pcode]),
                     rng.choice(["Implementation", "Upgrade", "Integration", "Advisory"]),
                     weighted(rng, [("Fixed Fee", 0.4), ("Milestone", 0.25),
                                    ("Time and Materials", 0.25), ("Capped T&M", 0.10)]),
                     value, hours, prob, close.isoformat(),
                     add_months(close, rng.randint(1, 3)).isoformat(),
                     round(min(14, max(2, hours / 450 + rng.uniform(0.8, 2.5))), 1),
                     stage))
    cur.executemany(
        """INSERT INTO pipeline_opportunity(org_id, opp_code, customer_id,
                customer_name_raw, opp_name, practice_id, product, project_type,
                billing_model, services_value, estimated_hours, probability_pct,
                expected_close, expected_start, expected_duration_months, stage)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


HEALTH_OVERRIDE_REASONS = [
    "Client has verbally agreed a two week extension, paperwork to follow.",
    "Overrun is on the integration workstream, which the client owns.",
    "Change order raised to cover the additional scope, awaiting signature.",
    "Burn is front loaded by design; the build phase is fixed price to us.",
    "Steering committee reviewed 12 Aug and accepted the revised date.",
    "Data migration rework was quoted separately and is not on this budget.",
    "Recovery plan agreed with the client, tracking to it since last week.",
    "Key client SME was unavailable for three weeks, timeline reset agreed.",
]
BASELINE_REASONS = [
    "Re-baselined against the approved change order.",
    "Scope moved to phase two at the client's request.",
    "Original plan assumed a single environment; the client has three.",
    "Go live moved to align with the client's fiscal calendar.",
]
RATE_REASONS = [
    "Annual rate card review, effective 1 Jan.",
    "Promotion to senior consultant.",
    "Rate aligned to the practice band after certification.",
]


def _seed_audit_samples(cur, rng):
    """
    A representative audit trail: health overrides, plan re-baselines, rate edits.

    Two things matter about how this is built. First, an override is only
    recorded where the PM's colour actually departs from what the consumption
    signal implies, because an audit trail that logs every value logs nothing.
    Second, a deliberate minority of overrides carry no reason. That is not
    untidiness, it is the finding: the review needs the note more than it needs
    the colour, and a note is the one part of an override no system can supply
    for you.
    """
    rows = []
    for pid, health, code, budget_hours in cur.execute(
            """SELECT p.project_id, p.project_health, p.project_code,
                      p.budget_hours
                 FROM project p
                WHERE p.project_status='In Flight'
                ORDER BY p.project_id""").fetchall():
        booked = cur.execute(
            "SELECT COALESCE(SUM(hours),0) FROM time_entry WHERE project_id=?",
            (pid,)).fetchone()[0]
        consumed = (booked / budget_hours) if budget_hours else 0
        # The mechanical read: consumption alone, no judgement applied.
        implied = ("Red" if consumed > 1.0 else
                   "Yellow" if consumed > 0.85 else "Green")
        if implied == health:
            continue                      # nothing was overridden
        rank = {"Green": 0, "Yellow": 1, "Red": 2}
        # Most overrides soften the colour. A note is required either way.
        with_reason = rng.random() < 0.74
        rows.append((
            "project", pid, "project_health", implied, health,
            cur.execute("SELECT e.employee_name FROM project p JOIN employee e "
                        "ON e.employee_id=p.project_manager_id WHERE "
                        "p.project_id=?", (pid,)).fetchone()[0] or "Project Manager",
            "ui", 1,
            rng.choice(HEALTH_OVERRIDE_REASONS) if with_reason else None,
            None if rank[health] >= rank[implied] else
            (rng.choice(["Practice Lead", "Delivery Director"])
             if rng.random() < 0.6 else None)))

    for vid, pid in cur.execute(
            """SELECT plan_version_id, project_id FROM project_plan_version
                WHERE is_baseline=0 LIMIT 60""").fetchall():
        rows.append(("project_plan_version", vid, "is_current", "0", "1",
                     "PM (seed)", "ui", 1,
                     rng.choice(BASELINE_REASONS) if rng.random() < 0.85 else None,
                     "Practice Lead" if rng.random() < 0.7 else None))
    # Rate changes in both organisations, not just the first one. A financial
    # change with no reason recorded is the finding, so a minority carry none.
    for org_id, in cur.execute("SELECT org_id FROM org ORDER BY org_id").fetchall():
        for eid, in cur.execute(
                "SELECT employee_id FROM employee WHERE org_id=? "
                "ORDER BY employee_id LIMIT 22", (org_id,)).fetchall():
            rows.append(("employee", eid, "billing_rate", "prior list rate",
                         "annual rate review", "Finance (seed)", "import", 0,
                         rng.choice(RATE_REASONS) if rng.random() < 0.88
                         else None, "Finance Director"))
    for org_id, in cur.execute("SELECT org_id FROM org ORDER BY org_id").fetchall():
        for pid, cv in cur.execute(
                "SELECT project_id, contract_value FROM project WHERE org_id=? "
                "AND contract_value>0 ORDER BY contract_value DESC LIMIT 14",
                (org_id,)).fetchall():
            rows.append(("project", pid, "contract_value",
                         f"{cv * 0.9:,.0f}", f"{cv:,.0f}",
                         "Finance (seed)", "ui", 0,
                         "Approved change order folded into the contract value."
                         if rng.random() < 0.8 else None, "Finance Director"))
    cur.executemany(
        """INSERT INTO audit_log(entity_table, entity_pk, field_name, old_value,
                new_value, changed_by, change_source, is_override, change_reason,
                approved_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
