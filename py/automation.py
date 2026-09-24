"""
Where automation and AI would actually pay, measured from this book.

The analysis is three separable parts, and keeping them apart is the reason it
can survive a challenge:

  the baseline    measured, from a query, with the query stated in words
  the rates       assumptions, held as editable inputs, each with a basis
  the saving      the product of the two, labelled as modelled

A reader can accept the baseline and argue with the rate. That is a
conversation that converges. A single "40% saving on migration" is one that
does not, because there is nothing underneath it to agree about.

Two things this deliberately refuses to do.

It does not claim a saving on effort it cannot measure. Several obvious
candidates - proposal writing, pre-sales, recruitment screening - are real
opportunities and are not here, because this database holds no hours against
them and an opportunity with an invented baseline is worse than a missing one.

It does not model AI writing to financial or project data. The requirement this
system is built against is explicit that AI must not modify either without
confirmation, so every AI-typed opportunity carries a guardrail naming what
stays with a person, and the saving is taken net of the review time that
guardrail costs.
"""
import os
import sqlite3

# The measured window. Every baseline in this file is a 24-month total, because
# that is the history the console holds, and every saving is quoted per year.
# Mixing the two is the easiest way to overstate a case by exactly 2x, so the
# conversion happens once, here, rather than in ten expressions.
WINDOW_MONTHS = 24.0
PER_YEAR = 12.0 / WINDOW_MONTHS
# First-year benefit is deliberately half a year's worth: nothing goes live in
# January, and a case that assumes it does is the case that misses.
RAMP_SHARE = 0.5

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

# =====================================================================
# The assumptions, in one place
#
# Every rate a saving depends on is here rather than inline, because a number
# buried in an expression is a number nobody can find to argue with. The bands
# are what the sensitivity runs over.
# =====================================================================
A = {
    # What a consultant's hour costs to run, and what an hour freed is worth.
    # A freed non-billable hour is not revenue: it becomes revenue only if
    # there is demand to sell it into, so it is discounted hard.
    "billable_recovery_pct": (35.0, 20.0, 55.0,
                              "share of freed hours that convert to billable "
                              "work. Held low deliberately: freeing an hour "
                              "and selling it are different problems, and "
                              "models that assume full conversion are the "
                              "reason automation cases do not land"),
    # Implementation, at a rate the organisation would actually pay.
    "build_day_rate": (1200.0, 900.0, 1600.0,
                       "blended internal build day, including the delivery "
                       "person whose time the configuration needs"),
    "ai_run_cost_per_1k_items": (4.0, 1.0, 12.0,
                                 "inference and platform cost per thousand "
                                 "items processed, at current commercial "
                                 "model pricing"),
    # Where the driver is a count rather than an hour, the handling time is an
    # assumption and is stated as one. Measured volume times assumed handling
    # time is a defensible baseline; an assumed volume times an assumed time
    # is not, which is why nothing here invents a count.
    "minutes_per_mapping_decision": (25.0, 10.0, 45.0,
                                     "find the legacy value, decide the target, "
                                     "record the decision"),
    "minutes_per_intake_finding": (20.0, 8.0, 40.0,
                                   "read the finding, trace the row, decide, "
                                   "go back to the target"),
    "minutes_per_timesheet_chase": (6.0, 3.0, 12.0,
                                    "notice, message, follow up once"),
    "minutes_per_check_finding": (12.0, 5.0, 25.0,
                                  "reconcile one condition by hand, which is "
                                  "the only way these are found today"),
    "payback_ceiling_months": (18.0, None, None,
                               "beyond this the case competes with delivery "
                               "hiring and usually loses"),
}


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, args=(), default=0.0):
    r = con.execute(sql, args).fetchone()
    return default if r is None or r[0] is None else r[0]


def q1(x):
    return None if x is None else round(float(x), 1)


def q2(x):
    return None if x is None else round(float(x), 2)


# =====================================================================
# Baselines
# =====================================================================
def measure(con, org_id):
    """
    Every figure the opportunities are built from, read once.

    Each key here is a measured quantity with a query behind it. Nothing in
    this dictionary is an assumption; the assumptions are in A above.
    """
    m = {}
    m["cost_rate"] = one(con, """
        SELECT SUM(hours * COALESCE(cost_rate, 0)) / NULLIF(SUM(hours), 0)
          FROM time_entry WHERE org_id=?""", (org_id,), 110.0)
    m["realised_rate"] = one(con, """
        SELECT realised_rate FROM rate_analysis
         WHERE org_id=? AND dimension='org'""", (org_id,), 160.0)
    m["headcount"] = one(con, """
        SELECT COUNT(*) FROM employee
         WHERE org_id=? AND is_active=1 AND is_billable=1""", (org_id,), 0)

    # Effort by category. These are the categories a consultant picks on a
    # timesheet, so they are the organisation's own words for its own work.
    for key, cat in [("migration", "Data Migration"),
                     ("pm", "Project Management"),
                     ("internal", "Internal"),
                     ("rework", "Rework"),
                     ("overservice", "Over-service"),
                     ("testing", "Testing"),
                     ("training", "Training")]:
        r = con.execute("""
            SELECT COALESCE(SUM(hours), 0),
                   COALESCE(SUM(hours * COALESCE(cost_rate, 0)), 0)
              FROM time_entry WHERE org_id=? AND time_category=?""",
            (org_id, cat)).fetchone()
        m[f"{key}_hours"], m[f"{key}_cost"] = r[0], r[1]

    m["total_hours"] = one(con, "SELECT SUM(hours) FROM time_entry WHERE org_id=?",
                           (org_id,))

    # Timesheet administration: the chasing, not the booking.
    m["unapproved_hours"] = one(con, """
        SELECT SUM(hours) FROM time_entry
         WHERE org_id=? AND approval_status NOT IN ('Approved','Locked')""",
        (org_id,))
    m["unapproved_entries"] = one(con, """
        SELECT COUNT(*) FROM time_entry
         WHERE org_id=? AND approval_status NOT IN ('Approved','Locked')""",
        (org_id,))
    m["short_weeks"] = one(con, """
        SELECT failing FROM check_result r JOIN check_run cr USING(check_run_id)
         WHERE cr.org_id=? AND r.check_code='TIME-04'""", (org_id,))

    # Intake and migration: the exception handling, which is where migration
    # effort actually goes.
    m["intake_errors"] = one(con, """
        SELECT COUNT(*) FROM validation_finding f JOIN import_batch b USING(batch_id)
         WHERE b.org_id=? AND f.severity='error'""", (org_id,))
    m["intake_warnings"] = one(con, """
        SELECT COUNT(*) FROM validation_finding f JOIN import_batch b USING(batch_id)
         WHERE b.org_id=? AND f.severity='warning'""", (org_id,))
    m["intake_rows"] = one(con, """
        SELECT SUM(row_count) FROM import_batch WHERE org_id=?""", (org_id,))
    m["mapping_rules"] = one(con, """
        SELECT COUNT(*) FROM mapping_rule r JOIN mapping_set s USING(mapping_set_id)
         WHERE s.org_id=?""", (org_id,))
    m["mapping_unapproved"] = one(con, """
        SELECT COUNT(*) FROM mapping_rule r JOIN mapping_set s USING(mapping_set_id)
         WHERE s.org_id=? AND r.approved=0""", (org_id,))
    m["dq_findings"] = one(con, """
        SELECT COUNT(*) FROM data_quality_finding WHERE org_id=?""", (org_id,))

    # The checks: how many conditions nothing currently prevents or reports,
    # and what they are worth.
    m["silent_rules"] = one(con, """
        SELECT COUNT(*) FROM delivery_check WHERE enforcement='silent'""")
    m["check_findings"] = one(con, """
        SELECT SUM(r.failing) FROM check_result r JOIN check_run cr USING(check_run_id)
         WHERE cr.org_id=?""", (org_id,))
    m["check_value"] = one(con, """
        SELECT SUM(r.value_at_stake) FROM check_result r
          JOIN check_run cr USING(check_run_id) JOIN delivery_check c USING(check_code)
         WHERE cr.org_id=? AND r.value_unit='currency'""", (org_id,))

    # Commercial: what is delivered and not billed, and how old it is.
    b = (rows(con, """SELECT * FROM billing_position
                       WHERE org_id=? AND scope='org'""", (org_id,)) or [{}])[0]
    m["unbilled"] = b.get("unbilled") or 0.0
    m["unbilled_aged"] = b.get("unbilled_over_90") or 0.0
    m["unbilled_projects"] = b.get("projects_unbilled") or 0

    # Support and hypercare.
    h = (rows(con, """SELECT * FROM hypercare_summary
                       WHERE org_id=? AND scope='org'""", (org_id,)) or [{}])[0]
    m["hypercare_unbilled"] = h.get("unbilled_cost") or 0.0
    m["hypercare_projects"] = h.get("projects") or 0
    t = con.execute("""SELECT COALESCE(SUM(open_bugs + open_enhancements), 0),
                              AVG(median_ttr_days), AVG(regression_pct),
                              COALESCE(SUM(delivery_effort_hours), 0)
                         FROM product_ticket_summary WHERE org_id=?""",
                    (org_id,)).fetchone()
    m["open_tickets"], m["ttr_days"] = t[0], t[1] or 0.0
    m["regression_pct"], m["ticket_effort_hours"] = t[2] or 0.0, t[3]

    # Why engagements slip, which is what an AI review step would target.
    m["scope_slip_pct"] = one(con, """
        SELECT AVG(c.incidence_pct) FROM phase_slip_cause c
          JOIN phase_duration_analysis p USING(phase_analysis_id)
         WHERE p.org_id=? AND p.scope='org' AND c.cause_code LIKE '%scope%'""",
        (org_id,))
    if not m["scope_slip_pct"]:
        m["scope_slip_pct"] = one(con, """
            SELECT MAX(c.incidence_pct) FROM phase_slip_cause c
              JOIN phase_duration_analysis p USING(phase_analysis_id)
             WHERE p.org_id=? AND p.scope='org'""", (org_id,))
    return m


# =====================================================================
# The opportunities
#
# Each entry is (code, process, area, type, baseline dict, rates, effort,
# feasibility, readiness, confidence, guardrail, prerequisite, evidence list).
#
# The rates are the three assumptions the model turns on and they are stated
# per opportunity, because "how much of this can a tool touch" is not one
# number across a services business: a mapping decision is nearly all
# addressable and a training hour is nearly none.
# =====================================================================
def opportunities(m):
    cost_rate = m["cost_rate"]
    O = []

    # ---- migration -------------------------------------------------------
    O.append(dict(
        code="AUT-01",
        process="Data migration: extract, map, transform, reconcile",
        area="migration",
        type="rules",
        hours=m["migration_hours"], cost=m["migration_cost"],
        count=m["intake_rows"], unit="rows received",
        basis=(f"{m['migration_hours']:,.0f} hours booked to the Data "
               f"Migration time category over 24 months, at a blended "
               f"{cost_rate:,.0f} an hour"),
        addressable=55.0, automation=60.0, review=10.0,
        addressable_basis=("the extract, transform and reconcile steps. "
                           "Discovery, decisions about what history to bring "
                           "and the cutover itself stay with people"),
        days=45, run_year=6000,
        feasibility="high", readiness="ready", confidence="medium",
        guardrail=None,
        prereq=("a fixed target schema and a mapping set held as data. Both "
                "exist here: eight templates with a column-level dictionary, "
                "and a validated loader"),
        evidence=[
            ("Migration effort booked", m["migration_hours"], "hours", "psos",
             "The organisation's own timesheet category, so this is what it "
             "calls migration work rather than an estimate of it."),
            ("Rows received through intake", m["intake_rows"], "count", "intake",
             "Across the eight templates. Volume is what makes the rules case, "
             "since a rule costs the same on ten rows and ten thousand."),
            ("Blocking errors to clear", m["intake_errors"], "count", "intake",
             "Each one is a row that did not load and a person who had to find "
             "out why."),
        ]))

    O.append(dict(
        code="AUT-02",
        process="Mapping legacy values to the target taxonomy",
        area="migration",
        type="ai_assisted",
        hours=None, cost=None,
        count=m["mapping_rules"], unit="mapping rules",
        basis=(f"{m['mapping_rules']:,.0f} mapping rules in the set, "
               f"{m['mapping_unapproved']:,.0f} still unapproved. Effort is "
               f"not booked separately, so the baseline is the decision count "
               f"rather than an hour figure"),
        minutes_per_item=A["minutes_per_mapping_decision"][0],
        addressable=90.0, automation=70.0, review=100.0,
        addressable_basis=("suggesting the mapping and ranking it by "
                           "confidence. The decision itself is not "
                           "addressable and is not modelled as saved"),
        days=12, run_year=1200,
        feasibility="high", readiness="ready", confidence="high",
        guardrail=("Every suggested mapping is reviewed by a person before it "
                   "is applied. A mapping nobody checked reclassifies history "
                   "silently, and review is 100% here rather than a sample "
                   "for exactly that reason."),
        prereq="the mapping set already holds a confidence score per rule",
        evidence=[
            ("Mapping rules held as data", m["mapping_rules"], "count",
             "intake", "Held as rows with a confidence score and an approval "
             "flag, which is what makes suggestion automatable and approval "
             "auditable."),
            ("Still unapproved", m["mapping_unapproved"], "count", "intake",
             "Below the confidence threshold and waiting on a person."),
        ]))

    O.append(dict(
        code="AUT-03",
        process="Intake validation and exception triage",
        area="migration",
        type="rules",
        hours=None, cost=None,
        count=m["intake_errors"] + m["intake_warnings"], unit="findings",
        basis=(f"{m['intake_errors']:,.0f} errors and "
               f"{m['intake_warnings']:,.0f} warnings raised across the "
               f"intake batches, plus {m['dq_findings']:,.0f} data quality "
               f"findings after load"),
        minutes_per_item=A["minutes_per_intake_finding"][0],
        addressable=85.0, automation=80.0, review=15.0,
        addressable_basis=("detection, grouping by rule and routing. Deciding "
                           "what to do about a finding stays with a person"),
        days=10, run_year=800,
        feasibility="high", readiness="ready", confidence="high",
        guardrail=None,
        prereq=("already built: the loader runs seven validation stages and "
                "groups findings by rule rather than by row"),
        evidence=[
            ("Findings raised at intake",
             m["intake_errors"] + m["intake_warnings"], "count", "intake",
             "Errors block a row, warnings load it and raise a finding. "
             "Grouping by rule turns a hundred rows into one conversation."),
            ("Data quality findings after load", m["dq_findings"], "count",
             "intake", "What the engine found once the data was in."),
        ]))

    # ---- delivery --------------------------------------------------------
    O.append(dict(
        code="AUT-04",
        process="Status reporting and project administration",
        area="delivery",
        type="ai_assisted",
        hours=m["pm_hours"], cost=m["pm_cost"],
        count=None, unit=None,
        basis=(f"{m['pm_hours']:,.0f} hours booked to Project Management "
               f"over 24 months. Only the reporting and administrative part "
               f"is addressable, not the management"),
        addressable=25.0, automation=65.0, review=20.0,
        addressable_basis=("assembling a status report, a steering pack and a "
                           "weekly update from data that already exists. "
                           "Customer conversations, escalation and judgement "
                           "are not addressable"),
        days=20, run_year=4000,
        feasibility="high", readiness="ready", confidence="medium",
        guardrail=("Drafted, never sent. A generated status narrative goes to "
                   "the project manager to correct and approve, because the "
                   "one thing worse than a late status report is a confident "
                   "wrong one in front of a customer."),
        prereq=("the weekly brief already generates the narrative from the "
                "same run, so the content exists and only the assembly is "
                "manual"),
        evidence=[
            ("Project management hours", m["pm_hours"], "hours", "psos",
             f"{m['pm_hours'] / max(1, m['total_hours']) * 100:,.0f}% of all "
             f"booked time. A quarter of it is assembly rather than "
             f"management."),
            ("Cost of that time", m["pm_cost"], "currency", "margin",
             "At snapshotted cost rates, so this is what it cost when it was "
             "incurred rather than at today's rate."),
        ]))

    O.append(dict(
        code="AUT-05",
        process="Timesheet compliance chasing",
        area="delivery",
        type="rules",
        hours=None, cost=None,
        count=m["unapproved_entries"], unit="entries",
        basis=(f"{m['unapproved_hours']:,.0f} hours across "
               f"{m['unapproved_entries']:,.0f} entries are not approved or "
               f"locked, and {m['short_weeks']:,.0f} submitted person-weeks "
               f"came in below the minimum"),
        minutes_per_item=A["minutes_per_timesheet_chase"][0],
        addressable=95.0, automation=85.0, review=5.0,
        addressable_basis=("finding the gap and asking. Nobody's judgement is "
                           "involved in noticing that a week is missing"),
        days=8, run_year=600,
        feasibility="high", readiness="ready", confidence="high",
        guardrail=None,
        prereq="nothing: the checks that find these already run",
        evidence=[
            ("Hours not approved or locked", m["unapproved_hours"], "hours",
             "checks", "Every margin, index and utilisation figure is computed "
             "on booked time, so an unapproved hour is not a neutral gap."),
            ("Short submitted weeks", m["short_weeks"], "count", "checks",
             "Below the platform's own minimum-hours setting."),
        ]))

    O.append(dict(
        code="AUT-06",
        process="Enforcing the conditions no platform prevents",
        area="delivery",
        type="rules",
        hours=None, cost=None,
        count=m["check_findings"], unit="findings reconciled by hand",
        basis=(f"{m['silent_rules']:,.0f} of the delivery rules are conditions "
               f"neither Certinia nor Rocketlane prevents or reports. Across "
               f"the whole catalogue {m['check_findings']:,.0f} findings carry "
               f"{m['check_value']:,.0f} of value at stake"),
        minutes_per_item=A["minutes_per_check_finding"][0],
        unlock_measure="check_value", unlock_share=0.06,
        unlock_basis=("6% of the value at stake across all findings, avoided "
                      "rather than recovered. A deliberately small share: "
                      "most of that exposure is already known and being "
                      "worked, and the automation's contribution is finding "
                      "the rest earlier"),
        addressable=70.0, automation=75.0, review=25.0,
        addressable_basis=("the detectable subset. Some conditions can only be "
                           "prevented by a platform change, and those are "
                           "configuration work rather than automation"),
        days=25, run_year=2000,
        feasibility="medium", readiness="ready", confidence="medium",
        guardrail=None,
        prereq=("the rules are already held as data with an assertion, a "
                "population and a remedy on each, which is what makes them "
                "portable into enforcement"),
        evidence=[
            ("Conditions nothing prevents or reports", m["silent_rules"],
             "count", "checks", "These are the ones only found by somebody "
             "reconciling a spreadsheet."),
            ("Value at stake across all findings", m["check_value"],
             "currency", "checks", "Summed where a finding carries a currency "
             "figure. Not a saving: an exposure the enforcement would reduce."),
        ]))

    O.append(dict(
        code="AUT-07",
        process="Rework and over-service reduction at design sign-off",
        area="delivery",
        type="ai_assisted",
        hours=m["rework_hours"] + m["overservice_hours"],
        cost=m["rework_cost"] + m["overservice_cost"],
        count=None, unit=None,
        basis=(f"{m['rework_hours']:,.0f} hours of Rework and "
               f"{m['overservice_hours']:,.0f} of Over-service booked over 24 "
               f"months. Scope added mid-phase is the attributed cause of "
               f"{m['scope_slip_pct'] or 0:,.0f}% of phase slip"),
        addressable=30.0, automation=40.0, review=30.0,
        addressable_basis=("reviewing a design or configuration against the "
                           "signed scope and the patterns of past overruns "
                           "before sign-off. Most rework has causes no review "
                           "step reaches"),
        days=30, run_year=5000,
        feasibility="medium", readiness="partial", confidence="low",
        guardrail=("Advisory only. The review raises a question for the "
                   "delivery lead; it does not gate a sign-off and it does "
                   "not change a plan, a budget or a scope document."),
        prereq=("requirements and design artefacts would have to be held "
                "somewhere machine-readable. This system holds the plan, the "
                "tasks and the slip causes but not the design documents"),
        evidence=[
            ("Rework and over-service hours",
             m["rework_hours"] + m["overservice_hours"], "hours", "margin",
             "Booked as such by the people doing it, which makes it the most "
             "credible waste figure in the book."),
            ("Phase slip attributed to scope added mid-phase",
             m["scope_slip_pct"], "pct", "dilig",
             "The most common attributed cause, which is what makes a review "
             "at sign-off the right point to intervene."),
        ]))

    # ---- commercial ------------------------------------------------------
    O.append(dict(
        code="AUT-08",
        process="Billing preparation and unbilled recovery",
        area="commercial",
        type="rules",
        hours=None, cost=None,
        count=m["unbilled_projects"], unit="engagements",
        basis=(f"{m['unbilled']:,.0f} of delivered and unbilled value across "
               f"{m['unbilled_projects']:,.0f} engagements, of which "
               f"{m['unbilled_aged']:,.0f} has raised no invoice for a "
               f"quarter or more"),
        addressable=100.0, automation=60.0, review=40.0,
        addressable_basis=("assembling the billing position, testing "
                           "eligibility and queueing the exceptions. Raising "
                           "the invoice and the customer conversation stay "
                           "with people"),
        days=15, run_year=1500,
        feasibility="high", readiness="ready", confidence="high",
        guardrail=None,
        prereq=("nothing: the billing position, its ageing and the exception "
                "list are already computed on every run"),
        unlock_measure="unbilled_aged", unlock_share=0.35,
        unlock_basis=("a share of the aged unbilled balance recovered. Aged "
                      "rather than total, because a normal billing lag is not "
                      "a recovery opportunity, and a share rather than all of "
                      "it, because some of that balance will be written off "
                      "whatever the process"),
        evidence=[
            ("Delivered and not billed", m["unbilled"], "currency", "billing",
             "Recognised revenue ahead of invoiced. A position, not an error, "
             "until it ages."),
            ("Of that, no invoice for a quarter", m["unbilled_aged"],
             "currency", "billing", "A billing lag that stopped moving is a "
             "write-off waiting to be recorded."),
        ]))

    # ---- support ---------------------------------------------------------
    O.append(dict(
        code="AUT-09",
        process="Hypercare and support triage",
        area="support",
        type="ai_assisted",
        hours=m["ticket_effort_hours"],
        cost=m["ticket_effort_hours"] * cost_rate,
        count=m["open_tickets"], unit="open tickets",
        basis=(f"{m['ticket_effort_hours']:,.0f} hours of delivery effort "
               f"consumed by product tickets, {m['open_tickets']:,.0f} open, "
               f"a median {m['ttr_days']:,.0f} days to resolve, and "
               f"{m['hypercare_unbilled']:,.0f} of unbilled hypercare cost"),
        addressable=45.0, automation=50.0, review=25.0,
        addressable_basis=("triage, duplicate and regression detection, and "
                           "drafting the first response. Diagnosis and the "
                           "fix are not addressable"),
        days=25, run_year=6000,
        feasibility="medium", readiness="partial", confidence="medium",
        guardrail=("Classification and a drafted reply, both reviewed. No "
                   "auto-close, and no customer-facing response sent without "
                   "a person, because a wrong answer during hypercare costs "
                   "more than a slow one."),
        prereq=("ticket text and history would need to be available to the "
                "triage step. This system holds the counts, the ageing and "
                "the regression rate but not the ticket bodies"),
        evidence=[
            ("Delivery effort consumed by tickets", m["ticket_effort_hours"],
             "hours", "dilig", "Time delivery people spent on product defects "
             "rather than on engagements."),
            ("Unbilled hypercare cost", m["hypercare_unbilled"], "currency",
             "dilig", f"Across {m['hypercare_projects']:,.0f} engagements. "
             f"Hypercare that runs long is mostly not billable."),
            ("Median time to resolve", m["ttr_days"], "days", "dilig",
             "Triage is the front of this number, and it is the part a tool "
             "reaches."),
        ]))

    # ---- people ----------------------------------------------------------
    O.append(dict(
        code="AUT-10",
        process="Onboarding and knowledge access for new consultants",
        area="people",
        type="ai_assisted",
        hours=m["internal_hours"] * 0.15,
        cost=m["internal_cost"] * 0.15,
        count=m["headcount"], unit="billable people",
        basis=(f"a modelled 15% of the {m['internal_hours']:,.0f} internal "
               f"hours, which is where onboarding, shadowing and asking "
               f"colleagues sits. The split is an assumption: internal time "
               f"is not sub-categorised in this data"),
        addressable=40.0, automation=45.0, review=10.0,
        addressable_basis=("answering the questions a new joiner asks that "
                           "somebody has already answered. Shadowing real "
                           "delivery is not addressable and should not be"),
        days=20, run_year=4000,
        feasibility="medium", readiness="not held", confidence="low",
        guardrail=("Answers cite their source document so a new joiner can "
                   "check them. An uncited answer to a configuration question "
                   "is how a wrong pattern spreads across a practice."),
        prereq=("the knowledge would have to exist as documents. This is the "
                "weakest baseline in the list and it is marked low confidence "
                "for that reason"),
        evidence=[
            ("Internal hours", m["internal_hours"], "hours", "psos",
             "All non-project internal time. Only the onboarding and "
             "knowledge-seeking part is in scope, and this data does not "
             "separate it, which is why the baseline is modelled."),
            ("Billable people", m["headcount"], "count", "resource",
             "The population an onboarding tool serves, and the denominator "
             "for a per-head cost."),
        ]))
    return O


# =====================================================================
def build(con, org_id, run_id):
    m = measure(con, org_id)
    cost_rate = m["cost_rate"]
    recovery = A["billable_recovery_pct"][0] / 100.0
    day_rate = A["build_day_rate"][0]

    inputs = [
        ("blended_cost_rate", "Blended cost per hour", "economics",
         q2(cost_rate), "currency", "measured",
         "hours times snapshotted cost rate, over hours, across 24 months",
         None, None),
        ("realised_rate", "Realised rate per hour", "economics",
         q2(m["realised_rate"]), "currency", "measured",
         "billable revenue over billable hours, from the rate analysis",
         None, None),
        ("billable_recovery_pct", "Freed hours that convert to billable work",
         "adoption", A["billable_recovery_pct"][0], "pct", "assumption",
         A["billable_recovery_pct"][3], A["billable_recovery_pct"][1],
         A["billable_recovery_pct"][2]),
        ("build_day_rate", "Internal build day rate", "effort",
         A["build_day_rate"][0], "currency", "assumption",
         A["build_day_rate"][3], A["build_day_rate"][1],
         A["build_day_rate"][2]),
        ("ai_run_cost_per_1k_items", "AI run cost per thousand items",
         "effort", A["ai_run_cost_per_1k_items"][0], "currency", "assumption",
         A["ai_run_cost_per_1k_items"][3], A["ai_run_cost_per_1k_items"][1],
         A["ai_run_cost_per_1k_items"][2]),
        ("payback_ceiling_months", "Payback beyond which the case fails",
         "economics", A["payback_ceiling_months"][0], "months", "assumption",
         A["payback_ceiling_months"][3], None, None),
        ("minutes_per_mapping_decision", "Minutes per mapping decision",
         "effort", A["minutes_per_mapping_decision"][0], "minutes",
         "assumption", A["minutes_per_mapping_decision"][3],
         A["minutes_per_mapping_decision"][1],
         A["minutes_per_mapping_decision"][2]),
        ("minutes_per_intake_finding", "Minutes per intake finding", "effort",
         A["minutes_per_intake_finding"][0], "minutes", "assumption",
         A["minutes_per_intake_finding"][3], A["minutes_per_intake_finding"][1],
         A["minutes_per_intake_finding"][2]),
        ("minutes_per_timesheet_chase", "Minutes per timesheet chase",
         "effort", A["minutes_per_timesheet_chase"][0], "minutes",
         "assumption", A["minutes_per_timesheet_chase"][3],
         A["minutes_per_timesheet_chase"][1],
         A["minutes_per_timesheet_chase"][2]),
        ("minutes_per_check_finding", "Minutes to reconcile one finding by hand",
         "effort", A["minutes_per_check_finding"][0], "minutes", "assumption",
         A["minutes_per_check_finding"][3], A["minutes_per_check_finding"][1],
         A["minutes_per_check_finding"][2]),
        ("total_hours", "Hours booked in the window", "effort",
         q1(m["total_hours"]), "hours", "measured",
         "every timesheet line over 24 months, billable and not", None, None),
        ("headcount", "Billable people", "effort", q1(m["headcount"]),
         "count", "measured", "active billable employees", None, None),
    ]

    opps, evidence = [], []
    for o in opportunities(m):
        # Hours saved is the baseline narrowed twice and then reduced by the
        # review time the guardrail costs. An AI opportunity that ignores its
        # own review burden overstates itself by exactly that share.
        hours = o["hours"]                    # measured over the window
        # Where the driver is a count, the baseline is that measured count
        # times an assumed handling time. Stated rather than hidden: the count
        # is a fact and the minutes are somebody's judgement.
        if hours is None and o.get("minutes_per_item"):
            hours = (o.get("count") or 0) * o["minutes_per_item"] / 60.0
        annual_baseline = (hours or 0) * PER_YEAR
        addressable = annual_baseline * o["addressable"] / 100.0
        automated = addressable * o["automation"] / 100.0
        saved = automated * (1 - o["review"] / 100.0)     # hours a year
        cost_saved = saved * cost_rate
        recoverable = saved * recovery * (m["realised_rate"] - cost_rate)

        # Value unlocked is kept separate from a cost saving, because one is
        # an efficiency that repeats and the other is cash already earned that
        # gets collected once.
        unlocked = 0.0
        if o.get("unlock_share"):
            unlocked = (m.get(o.get("unlock_measure", "unbilled_aged")) or 0) \
                * o["unlock_share"]

        impl_cost = o["days"] * day_rate
        items = (o.get("count") or 0)
        ai_run = (A["ai_run_cost_per_1k_items"][0] * items / 1000.0
                  if o["type"].startswith("ai") else 0.0)
        run_year = o["run_year"] + ai_run

        annual_benefit = cost_saved + recoverable
        year_one = annual_benefit * RAMP_SHARE + unlocked
        net = year_one - impl_cost - run_year
        # Payback counts the one-off unlock as well as the recurring benefit,
        # spread over its first year. Excluding it made an opportunity whose
        # whole value is recovered cash read as never paying back.
        monthly = (annual_benefit + unlocked) / 12.0
        payback = (impl_cost / monthly) if monthly > 0 else None

        verdict = _verdict(o, payback, net)
        statement = _statement(o, m, saved, cost_saved, recoverable, unlocked,
                               impl_cost, payback, net)
        opps.append(dict(
            code=o["code"], process=o["process"], area=o["area"],
            type=o["type"],
            baseline_hours=q1(hours), baseline_cost=q2(o["cost"]),
            annual_baseline=q1(annual_baseline),
            baseline_count=q1(o.get("count")), baseline_unit=o.get("unit"),
            basis=o["basis"],
            addressable=o["addressable"], automation=o["automation"],
            review=o["review"],
            hours_saved=q1(saved), cost_saved=q2(cost_saved + recoverable),
            unlocked=q2(unlocked),
            impl_days=q1(o["days"]), impl_cost=q2(impl_cost),
            run_year=q2(run_year), net=q2(net), payback=q1(payback),
            feasibility=o["feasibility"], readiness=o["readiness"],
            confidence=o["confidence"], guardrail=o.get("guardrail"),
            prereq=o.get("prereq"), verdict=verdict, statement=statement,
            evidence=o["evidence"], addressable_basis=o["addressable_basis"]))

    # Ranked on first-year net, then on payback, then on feasibility. Ranking
    # on saving alone puts a two-year build at the top of a list somebody is
    # meant to act on this quarter.
    order = {"high": 0, "medium": 1, "low": 2}
    opps.sort(key=lambda o: (-(o["net"] or 0), o["payback"] or 999,
                             order[o["feasibility"]]))
    for i, o in enumerate(opps, 1):
        o["rank"] = i
    return inputs, opps, m


def _verdict(o, payback, net):
    if o["readiness"] == "not held":
        return "investigate"
    if net is not None and net < 0 and payback is None:
        return "no"
    if net is not None and net < 0 and (payback or 999) > A[
            "payback_ceiling_months"][0]:
        return "no"
    if (o["feasibility"] == "high" and o["readiness"] == "ready"
            and (payback or 999) <= 12):
        return "do now"
    if (payback or 999) <= A["payback_ceiling_months"][0]:
        return "next"
    return "investigate"


def _statement(o, m, saved, cost_saved, recoverable, unlocked, impl, payback,
               net):
    parts = []
    if saved > 1:
        parts.append(f"{saved:,.0f} hours a year come back, worth "
                     f"${cost_saved:,.0f} at cost")
        if recoverable > 1000:
            parts.append(f"plus ${recoverable:,.0f} of contribution if a "
                         f"share of those hours sells")
    if unlocked > 1000:
        parts.append(f"${unlocked:,.0f} of value becomes collectable or is "
                     f"avoided")
    s = ", ".join(parts) if parts else "the saving here is in decisions and " \
                                      "exceptions handled rather than hours"
    s = s[0].upper() + s[1:] + ". "
    if payback:
        months = max(1, round(payback))
        s += (f"Build is {o['days']:,.0f} days at ${impl:,.0f}, so it pays "
              f"back in {months:,.0f} month{'' if months == 1 else 's'}. ")
    else:
        s += f"Build is {o['days']:,.0f} days at ${impl:,.0f}. "
    if o["type"] == "ai_assisted":
        s += (f"{o['review']:,.0f}% of the automated volume still goes past a "
              f"person, and the saving above is already net of that. ")
    elif o["type"] == "ai_autonomous":
        s += "This one runs unattended, which is why the guardrail matters. "
    if o["readiness"] != "ready":
        s += (f"Data readiness is {o['readiness']}: {o['prereq']}. ")
    if net is not None and net < 0:
        s += ("On this book the volume does not carry the build cost in year "
              "one. That is a verdict about scale rather than about the idea: "
              "the same rule over a larger book, or over several targets, "
              "pays. ")
    return s.strip()


def summaries(con, org_id, run_id, opps, m):
    out = []

    def block(scope, key, label, sel, order):
        if not sel:
            return
        bh = sum(o["baseline_hours"] or 0 for o in sel)
        bc = sum(o["baseline_cost"] or 0 for o in sel)
        hs = sum(o["hours_saved"] or 0 for o in sel)
        cs = sum(o["cost_saved"] or 0 for o in sel)
        vu = sum(o["unlocked"] or 0 for o in sel)
        ic = sum(o["impl_cost"] or 0 for o in sel)
        net = sum(o["net"] or 0 for o in sel)
        ai = [o for o in sel if o["type"].startswith("ai")]
        ai_pct = (sum(o["cost_saved"] or 0 for o in ai) / cs * 100
                  if cs else None)
        monthly = cs / 12.0
        pb = ic / monthly if monthly > 0 else None
        s = (f"{label}: {len(sel)} opportunit"
             f"{'y' if len(sel) == 1 else 'ies'}, "
             f"{hs:,.0f} hours a year and ${cs + vu:,.0f} of value against "
             f"${ic:,.0f} of build. ")
        if pb:
            months = max(1, round(pb))
            s += (f"Blended payback {months:,.0f} "
                  f"month{'' if months == 1 else 's'}. ")
        if ai_pct is not None and scope == "org":
            s += (f"{ai_pct:,.0f}% of the value sits behind an AI-assisted "
                  f"step, which is a governance conversation rather than a "
                  f"procurement one: each of those carries a guardrail "
                  f"naming what stays with a person. ")
        if scope == "org":
            ready = [o for o in sel if o["readiness"] == "ready"]
            s += (f"{len(ready)} of {len(sel)} can start on the data this "
                  f"system already holds. ")
            # If one opportunity carries most of the value, say so. A
            # programme total that rests on a single assumption is a different
            # proposition from the same total spread over ten, and a reader
            # who finds that out later stops trusting the rest.
            top = max(sel, key=lambda x: (x["cost_saved"] or 0)
                      + (x["unlocked"] or 0))
            tv = (top["cost_saved"] or 0) + (top["unlocked"] or 0)
            if cs + vu > 0 and tv / (cs + vu) > 0.3:
                s += (f"Note the concentration: {tv / (cs + vu) * 100:,.0f}% "
                      f"of the value is one opportunity, {top['code']}, and "
                      f"{'most of that is an avoided exposure rather than a cash saving' if (top['unlocked'] or 0) > (top['cost_saved'] or 0) else 'it rests on one addressable-share assumption'}. "
                      f"The programme case should be read as that plus a tail, "
                      f"not as a diversified total.")
        out.append((org_id, run_id, scope, key, label, len(sel), q1(bh),
                    q2(bc), q1(hs), q2(cs), q2(vu), q2(ic), q2(net), q1(pb),
                    q1(ai_pct), s.strip(), order))

    block("org", None, "Whole programme", opps, 0)
    AREA = {"migration": "Data migration and intake",
            "delivery": "Delivery execution", "commercial": "Commercial",
            "support": "Support and hypercare", "people": "People"}
    for i, (k, label) in enumerate(AREA.items(), 1):
        block("area", k, label, [o for o in opps if o["area"] == k], i * 10)
    TYPE = {"rules": "Deterministic rules",
            "integration": "Integration",
            "ai_assisted": "AI-assisted, person confirms",
            "ai_autonomous": "AI-autonomous"}
    for i, (k, label) in enumerate(TYPE.items(), 1):
        block("automation_type", k, label,
              [o for o in opps if o["type"] == k], 100 + i * 10)
    return out


def sensitivity(con, org_id, run_id, opps, m):
    """
    What the programme is worth if the assumptions are wrong.

    Only the two rates that actually move it are tested. Running every input
    produces a table nobody reads and hides which two decisions matter.
    """
    base_net = sum(o["net"] or 0 for o in opps)
    base_hours = sum(o["hours_saved"] or 0 for o in opps)
    cost_rate = m["cost_rate"]
    day_rate = A["build_day_rate"][0]
    out, i = [], 0

    for shift in (-15.0, -5.0, 5.0, 15.0):
        hours = net = 0.0
        for o in opps:
            rate = max(0.0, min(100.0, o["automation"] + shift))
            adj = ((o["baseline_hours"] or 0) * PER_YEAR
                   * o["addressable"] / 100.0
                   * rate / 100.0 * (1 - o["review"] / 100.0))
            hours += adj
            benefit = (adj * cost_rate
                       + adj * A["billable_recovery_pct"][0] / 100.0
                       * (m["realised_rate"] - cost_rate)) * RAMP_SHARE \
                + (o["unlocked"] or 0)
            net += benefit - (o["impl_cost"] or 0) - (o["run_year"] or 0)
        reading = ("still positive" if net > 0 else
                   "the programme stops paying back in year one")
        out.append((org_id, run_id, "automation_pct",
                    "How much of the addressable work actually goes away",
                    shift, f"{shift:+.0f} points on every rate",
                    q1(hours), q2(net), q2(net - base_net),
                    f"{hours:,.0f} hours and ${net:,.0f} first-year net, "
                    f"{reading}.", i))
        i += 10

    for share in (20.0, 35.0, 55.0):
        net = 0.0
        for o in opps:
            adj = o["hours_saved"] or 0
            benefit = (adj * cost_rate + adj * share / 100.0
                       * (m["realised_rate"] - cost_rate)) * RAMP_SHARE \
                + (o["unlocked"] or 0)
            net += benefit - (o["impl_cost"] or 0) - (o["run_year"] or 0)
        out.append((org_id, run_id, "billable_recovery_pct",
                    "Share of freed hours that converts to billable work",
                    share, f"{share:.0f}% conversion",
                    q1(base_hours), q2(net), q2(net - base_net),
                    f"${net:,.0f} first-year net. The hours saved do not "
                    f"change; only what they are worth does.", i))
        i += 10
    return out


# =====================================================================
def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    for org_id, code in con.execute(
            "SELECT org_id, org_code FROM org ORDER BY org_id").fetchall():
        run_id = con.execute("SELECT MAX(run_id) FROM intelligence_run "
                             "WHERE org_id=?", (org_id,)).fetchone()[0]
        con.execute("""DELETE FROM automation_evidence WHERE opportunity_id IN
                       (SELECT opportunity_id FROM automation_opportunity
                         WHERE org_id=?)""", (org_id,))
        for t in ("automation_sensitivity", "automation_summary",
                  "automation_opportunity", "automation_input"):
            con.execute(f"DELETE FROM {t} WHERE org_id=?", (org_id,))

        inputs, opps, m = build(con, org_id, run_id)
        con.executemany("""
            INSERT INTO automation_input
              (org_id, run_id, input_code, input_label, input_group, value,
               unit, origin, basis, low, high, sort_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(org_id, run_id) + r + (i * 10,)
             for i, r in enumerate(inputs)])

        for o in opps:
            cur = con.execute("""
                INSERT INTO automation_opportunity
                  (org_id, run_id, opp_code, process, area, automation_type,
                   baseline_hours, baseline_cost, baseline_count,
                   baseline_unit, annual_baseline_hours, baseline_basis,
                   addressable_pct,
                   automation_pct, review_pct, hours_saved, cost_saved,
                   value_unlocked, implementation_days, implementation_cost,
                   run_cost_year, net_year_one, payback_months, feasibility,
                   data_readiness, confidence, guardrail, prerequisite,
                   verdict, statement, rank)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?)""",
                (org_id, run_id, o["code"], o["process"], o["area"],
                 o["type"], o["baseline_hours"], o["baseline_cost"],
                 o["baseline_count"], o["baseline_unit"],
                 o["annual_baseline"], o["basis"],
                 o["addressable"], o["automation"], o["review"],
                 o["hours_saved"], o["cost_saved"], o["unlocked"],
                 o["impl_days"], o["impl_cost"], o["run_year"], o["net"],
                 o["payback"], o["feasibility"], o["readiness"],
                 o["confidence"], o["guardrail"], o["prereq"], o["verdict"],
                 o["statement"], o["rank"]))
            oid = cur.lastrowid
            con.executemany("""
                INSERT INTO automation_evidence
                  (opportunity_id, measure, value, unit, source_view, detail,
                   sort_order)
                VALUES (?,?,?,?,?,?,?)""",
                [(oid, e[0], q2(e[1]), e[2], e[3], e[4], j * 10)
                 for j, e in enumerate(o["evidence"])])
            # The narrowing itself is evidence: it is where a reader disagrees.
            con.execute("""
                INSERT INTO automation_evidence
                  (opportunity_id, measure, value, unit, source_view, detail,
                   sort_order)
                VALUES (?,?,?,?,?,?,?)""",
                (oid, "Addressable share of the baseline", o["addressable"],
                 "pct", None, o["addressable_basis"], 900))

        con.executemany("""
            INSERT INTO automation_summary
              (org_id, run_id, scope, scope_key, scope_label, opportunities,
               baseline_hours, baseline_cost, hours_saved, cost_saved,
               value_unlocked, implementation_cost, net_year_one,
               payback_months, ai_share_pct, statement, sort_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            summaries(con, org_id, run_id, opps, m))
        con.executemany("""
            INSERT INTO automation_sensitivity
              (org_id, run_id, lever, lever_label, shift, shift_label,
               hours_saved, net_year_one, delta_vs_base, reading, sort_order)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            sensitivity(con, org_id, run_id, opps, m))
        con.commit()

        hrs = sum(o["hours_saved"] or 0 for o in opps)
        net = sum(o["net"] or 0 for o in opps)
        now = [o for o in opps if o["verdict"] == "do now"]
        print(f"{code}: {len(opps)} opportunities, {hrs:,.0f} hours/yr, "
              f"{net:,.0f} first-year net, {len(now)} to start now")
    con.close()


if __name__ == "__main__":
    main()
