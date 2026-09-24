"""
The delivery and implementation checks engine.

Why this module exists, stated plainly, because it is the part of this console
that is not in the products it sits beside.

Reviewing what Certinia and Rocketlane actually document:

  * Neither ships a computed project health model. Certinia ships four
    Green/Yellow/Red picklists on the project (Project Status, Financial
    Status, Schedule Status, Scope Status) and says the colour can be set
    manually or automated "via Salesforce Flow Builder". Rocketlane's project
    status is an arbitrary picklist and its at-risk flag is manual: "Flag the
    task as At risk or remove the flag." The scoring is the customer's job in
    both.
  * Neither documents a resource over-allocation rule. Certinia's Assignments
    and Schedules documentation does not address overlap at all. Rocketlane
    documents the gap outright: the percentage-of-capacity allocation method
    "does not account for time-offs or any existing allocations."
  * Certinia's billing eligibility is a conjunction of flags -- Include in
    Financials, Flagged as Billable, Status Approved, not already Billed --
    and a record that fails any one of them is silently omitted from the
    billing event. The documentation publishes no error and no rejection code.
    Work that should have billed and did not simply does not appear.

That last one is the highest-value check available in a services business, and
it is invisible in both products. So the checks here are grouped by what they
protect, and each one records whether the source platform blocks the condition,
detects it, or lets it pass silently. The silent ones are where the money goes.

Where a rule mirrors a documented vendor semantic, the row says so and cites
it. Where this engine goes further than either product, the row says that too.
Nothing here is presented as a vendor feature.
"""
import datetime as dt
import json
import os
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
ENGINE_VERSION = "checks-1.0"
FINDING_CAP = 40          # detail rows stored per check; counts are not capped

# Thresholds. Every one of these is a business setting rather than a constant
# of nature, which is why they are named here in one place and quoted in the
# check statements rather than buried in a comparison.
T = {
    "day_hour_ceiling": 16.0,        # Certinia: Day hour auto revert ceiling, default 24
    "week_hours_min": 37.5,          # Certinia: Minimum Resource Hours Per Week
    "week_hours_max": 60.0,          # Certinia: Maximum Resource Hours Per Week
    "overallocation_pct": 100.0,     # neither product ships this
    "actuals_drift_hours": 1.0,      # Actuals Verifier equivalent tolerance
    "actuals_drift_money": 50.0,
    "revrec_cap_pct": 100.0,         # Rocketlane: "a maximum cap of 100%"
    "milestone_sum_tolerance_pct": 2.0,
    "stale_forecast_days": 45,
    "unbilled_age_days": 60,
    "margin_watch_pts": 5.0,
    "health_divergence_bands": 2,
    "plan_variance_pts": 10.0,       # Certinia: Calculate Hours Variance
    "no_allocation_hours": 8.0,
    "milestone_slip_days": 0,
    # Earned value. Neither Certinia nor Rocketlane publishes a CPI or SPI
    # threshold, so these are the conventional PMI reading bands: inside 0.95
    # is noise, below 0.85 is a variance somebody has to explain.
    "cpi_floor": 0.85,
    "spi_floor": 0.85,
    "tcpi_gap": 0.15,          # TCPI this far above achieved CPI is not a plan
    "progress_support_pct": 60.0,   # booked hours against plan-implied effort
}

FAMILIES = {
    "billing":    "Billing and revenue eligibility",
    "time":       "Time entry and compliance",
    "actuals":    "Actuals integrity",
    "resource":   "Resource and assignment integrity",
    "governance": "Delivery governance",
    "close":      "Period close and lock",
    "migration":  "Migration and intake",
    "config":     "Configuration and readiness",
    "earnedvalue": "Earned value and delivery performance",
}

# Two different questions get confused with each other, and the confusion
# makes a readiness score useless.
#
#   Readiness: can this organisation run its business on this system? That is
#   a question about configuration, controls and data integrity. It has a
#   right answer, it can be cleared before go live, and 100% is achievable.
#
#   Operational: how is delivery actually going? Over-allocation, stale
#   forecasts, ageing unbilled value, earned-value gaps and missed dates are
#   real findings with real money attached, but they are never zero in a live
#   book and they are not a reason to hold a cutover.
#
# Scoring the second kind as a go-live gate produces a readiness number that
# can never reach 100 and therefore gets ignored. These checks stay in the
# catalogue at full severity with their value at stake; they are reported as
# operational findings rather than counted against readiness.
OPERATIONAL = {
    "BILL-01", "BILL-02", "TIME-01", "ACT-04", "RES-01",
    "GOV-02", "GOV-05", "GOV-06", "GOV-07",
    # A cost or schedule index outside tolerance is a delivery problem to
    # manage, not a configuration defect to clear before cutover. Scoring it
    # against readiness would mean no services business could ever be ready.
    "EVM-01", "EVM-02", "EVM-03", "EVM-04",
}

SRC_CERT_TIMECARD = ("Certinia, Timecard Settings and 'What Happens After a "
                     "Timecard is Submitted'")
SRC_CERT_BILLING = ("Certinia, Generating and Processing Billing Events "
                    "(five-condition eligibility)")
SRC_CERT_UTIL = "Certinia, About Utilization Formulae"
SRC_CERT_VERIFIER = "Certinia, Recalculating Actuals with the Actuals Verifier"
SRC_CERT_MISSING = "Certinia, Missing Timecards"
SRC_CERT_RAG = "Certinia, Project Fields (four status picklists)"
SRC_CERT_PERIOD = "Certinia, Time Period Fields and About Financial Years and Periods"
SRC_CERT_BUDGET = "Certinia, Project Budgets component (Amount Remaining)"
SRC_CERT_VARIANCE = "Certinia, Viewing and Updating Project Variance"
SRC_RL_TIME = "Rocketlane, Time Tracking and Enhanced Time Tracking and Approval"
SRC_RL_UTIL = "Rocketlane, Time analytics report and Capacity Planning"
SRC_RL_FIN = ("Rocketlane, Understanding financial numbers and Revenue "
              "recognition for fixed fee projects")
SRC_RL_BILL = "Rocketlane, Understanding Billing Schedules and Events"
SRC_RL_ALLOC = "Rocketlane, Percentage of capacity allocation (documented gap)"
SRC_NEITHER = "Neither product documents a rule for this"


# =====================================================================
# The catalogue
# =====================================================================
def catalogue():
    """
    (code, family, title, assertion, why, severity, enforcement, gate,
     weight, scope, remediation, vendor_basis, vendor_source)

    `enforcement` is what the source platform does about the condition:
      blocked  - it refuses to let the condition arise
      detected - it will report it if you go looking
      silent   - it neither prevents nor reports it
    """
    c = []

    # ---------------- billing and revenue eligibility ----------------
    c += [
        ("BILL-01", "billing",
         "Approved billable time with no invoice line",
         "Every approved billable hour older than the invoicing window is "
         "either invoiced or explicitly written off.",
         "This is the single largest silent leak in a services business. "
         "Certinia's billing event only picks up a record when Include in "
         "Financials, Flagged as Billable and an Approved status all hold and "
         "the Billed flag is clear. A record failing any one of them is left "
         "out with no error raised, so the work is delivered, the cost is "
         "incurred and the invoice never appears.",
         "critical", "silent", 1, 3.0,
         "Approved billable time entries older than the invoicing window",
         "Reconcile approved billable time to invoice lines monthly and treat "
         "the difference as a work queue, not a report.",
         "Mirrors Certinia's billing event eligibility conjunction, inverted "
         "to find what should have billed and did not.",
         SRC_CERT_BILLING),

        ("BILL-02", "billing",
         "Completed billing milestone not released for billing",
         "A billing milestone marked complete is released to billing within "
         "the invoicing window.",
         "A milestone is the invoicing trigger on milestone-billed work. Left "
         "unreleased it holds up cash on work the customer has already "
         "accepted, and because eligibility is a flag conjunction rather than "
         "a validation, nothing prompts anybody.",
         "critical", "silent", 1, 2.0,
         "Milestones with a billing amount that are complete",
         "Release accepted milestones to billing weekly. Where acceptance is "
         "the blocker, chase acceptance rather than the invoice.",
         "Certinia requires the milestone status to be Approved and the "
         "Billed flag clear before it enters a billing event.",
         SRC_CERT_BILLING),

        ("BILL-03", "billing",
         "Billable time with no bill rate",
         "Time flagged billable carries the rate it will be billed at.",
         "A billable hour with no rate contributes cost and no revenue, and it "
         "falls out of the billing event silently. It also makes realised rate "
         "and margin understate themselves without any figure looking wrong.",
         "high", "silent", 1, 2.0,
         "Time entries flagged billable",
         "Set a rate on the assignment or the role before the first hour is "
         "booked, and refuse billable entry where no rate resolves.",
         "Certinia derives the billing amount from the rate on the timecard "
         "or assignment; a null rate yields nothing and no warning.",
         SRC_CERT_BILLING),

        ("BILL-04", "billing",
         "Recognised revenue above the contract value",
         "Recognised revenue on a fixed price engagement never exceeds the "
         f"fixed fee. The cap is {T['revrec_cap_pct']:.0f}% of contract value.",
         "Rocketlane states the rule explicitly: revenue recognised is "
         "'limited to the fixed fee, with a maximum cap of 100%'. Breaching it "
         "overstates revenue and creates a restatement later.",
         "critical", "blocked", 1, 2.0,
         "Fixed price and milestone engagements with recognition posted",
         "Cap recognition at the contract value including approved change "
         "orders, and route the excess to a change order conversation.",
         "Rocketlane publishes this cap for fixed fee and subscription "
         "revenue recognition.",
         SRC_RL_FIN),

        ("BILL-05", "billing",
         "Billing milestones that do not sum to the contract",
         "The billing milestones on an engagement sum to its contract value, "
         f"within {T['milestone_sum_tolerance_pct']:.0f}%.",
         "Rocketlane's billing schedule guidance is to 'ensure that the sum of "
         "all percentages does not exceed 100% of the project's fixed fee'. "
         "Under a hundred and the last invoice never gets raised; over and the "
         "final one gets disputed.",
         "high", "detected", 1, 1.5,
         "Engagements with billing milestones",
         "Reconcile the billing schedule to the contract at signature and "
         "again after every approved change order.",
         "Rocketlane billing schedules; Certinia leaves the reconciliation to "
         "the customer.",
         SRC_RL_BILL),

        ("BILL-06", "billing",
         "Capped engagement invoiced beyond its ceiling",
         "A capped time and materials engagement never invoices past its cap.",
         "The cap is the whole commercial point of the model. Passing it turns "
         "billable hours into write-off, and the write-off is only visible if "
         "somebody compares invoiced value to the ceiling.",
         "high", "silent", 0, 1.0,
         "Capped time and materials engagements",
         "Stop-bill at the ceiling and raise a change order before the next "
         "hour is booked.",
         "Neither product documents a hard stop. Certinia's only shipped "
         "budget exception is a red arrow when Amount Remaining goes negative.",
         SRC_CERT_BUDGET),

        ("BILL-07", "billing",
         "Unbilled value ageing past the invoicing window",
         f"No approved billable value sits unbilled for more than "
         f"{T['unbilled_age_days']} days.",
         "Unbilled work ages into a dispute. The older the hour, the harder it "
         "is to defend to a customer who has forgotten the conversation.",
         "medium", "detected", 0, 1.0,
         "Projects carrying unbilled approved billable value",
         "Age unbilled value like a receivable and review it in the same "
         "meeting.",
         "Certinia bounds prior-period inclusion in billing events with the "
         "maxNumberOfDaysPriorToQuery option, which is the same idea in "
         "reverse.",
         SRC_CERT_BILLING),
    ]

    # ---------------- time entry and compliance ----------------
    c += [
        ("TIME-01", "time",
         "Missing timecard for a scheduled resource",
         "Every active resource scheduled in a period submitted a timecard for "
         "it.",
         "This is the one true compliance detector Certinia ships, and the "
         "reason it matters is arithmetic rather than administrative: an "
         "unsubmitted week is an uncosted week, so margin, utilisation and "
         "percent complete are all overstated until it arrives.",
         "high", "detected", 1, 2.0,
         "Active billable resources with an allocation in the period",
         "Chase on the deadline day, not at month end. The cost of a late "
         "timesheet is paid by the forecast, not by the person who is late.",
         "Certinia's Missing Timecards: an entry is created 'for any active "
         "resource that did not enter a timecard for a scheduled period on the "
         "project'.",
         SRC_CERT_MISSING),

        ("TIME-02", "time",
         "A day above the hour ceiling",
         f"No single day carries more than {T['day_hour_ceiling']:.0f} hours "
         "across all entries.",
         "A day nobody could have worked is a data entry error, and it inflates "
         "cost and consumption on whatever it was booked to. Certinia enforces "
         "this per timecard line, which is exactly the gap: the line is within "
         "the ceiling and the day is not.",
         "high", "blocked", 1, 1.5,
         "All time entries, aggregated by person and day",
         "Enforce the ceiling on the day rather than the line, which is where "
         "the sum actually breaks.",
         "Certinia's Day hour auto revert ceiling, default 24, applies to a "
         "single timecard line. Aggregating across lines is this engine going "
         "further.",
         SRC_CERT_TIMECARD),

        ("TIME-03", "time",
         "Zero or negative hours recorded",
         "No entry carries zero or negative hours.",
         "Both are legitimate in an adjustment workflow and neither is "
         "legitimate as a booking. Left in, they distort the denominator of "
         "every rate calculation.",
         "medium", "blocked", 0, 1.0,
         "All time entries",
         "Keep the settings that refuse them at their defaults and handle "
         "corrections as reversing entries with a reason.",
         "Certinia ships Allow timecard with negative hours (default false), "
         "Save timecard with zero hours (default false) and Submit timecard "
         "with zero hours (default false).",
         SRC_CERT_TIMECARD),

        ("TIME-04", "time",
         "A submitted week below the minimum hours",
         f"A submitted week accounts for at least {T['week_hours_min']:.1f} "
         "hours of a full-time person's time, including absence.",
         "A short week that is nonetheless approved is the most common way "
         "utilisation reads high and revenue reads low: the denominator is "
         "full and the numerator is not.",
         "medium", "blocked", 0, 1.0,
         "Submitted or approved weeks for full-time billable resources",
         "Keep the minimum-hours threshold on and let people book absence "
         "rather than leaving the week short.",
         "Certinia's Minimum Resource Hours Per Week notifies on submission "
         "when unmet. Rocketlane requires a minimum configured threshold "
         "before a timesheet can be submitted.",
         SRC_CERT_TIMECARD),

        ("TIME-05", "time",
         "Time booked outside the assignment window",
         "Time on a project falls inside an assignment covering that date.",
         "Time outside the assignment window is either the wrong project or an "
         "assignment nobody maintained. Both corrupt the capacity picture, "
         "because demand and delivery stop referring to the same commitment.",
         "high", "blocked", 1, 1.5,
         "Time entries against a project",
         "Keep the assignment date restrictions on, and fix the assignment "
         "rather than the entry when the work genuinely extended.",
         "Certinia ships Assignments load date restriction (default true) and "
         "assignment-strict-start/end-date-restriction. Rocketlane can "
         "restrict logging to dates covered by allocations.",
         SRC_CERT_TIMECARD),

        ("TIME-06", "time",
         "Time booked to a closed engagement",
         "No time is booked to an engagement that is complete or cancelled.",
         "Cost landing on a closed project restates a margin that has already "
         "been reported, which is the version of this error auditors ask about.",
         "high", "blocked", 1, 1.5,
         "Time entries against complete or cancelled engagements",
         "Refuse the booking, and reopen the engagement deliberately when late "
         "work is genuine.",
         "Certinia validates on submission 'that the project is active'.",
         SRC_CERT_TIMECARD),

        ("TIME-07", "time",
         "Approved time altered after approval",
         "An approved or invoiced entry is not modified in place.",
         "Once time is approved it has reached the financials. Editing it "
         "silently restates a closed number, and the audit trail cannot show "
         "what the approver actually approved.",
         "critical", "blocked", 1, 2.0,
         "Entries on approved, locked or invoiced weeks",
         "Withdraw, correct and resubmit, or post a reversing entry. Never "
         "edit in place.",
         "Rocketlane: 'Once a timesheet is approved, entries become locked and "
         "cannot be modified'; invoiced entries cannot be moved at all until "
         "the invoice is voided.",
         SRC_RL_TIME),

        ("TIME-08", "time",
         "Submitted time with no approver",
         "Every submitted week has somebody accountable for deciding on it.",
         "A submission with no approver waits forever, and the hours stay out "
         "of the financials while everyone assumes they are in.",
         "medium", "detected", 1, 1.0,
         "Weeks in a submitted state",
         "Complete the approval hierarchy before go live, and give every "
         "person a named manager.",
         "Certinia's prerequisite for submission is that 'your administrator "
         "has configured an approval process on your org'.",
         SRC_CERT_TIMECARD),

        ("TIME-09", "time",
         "Non-billable time above a plausible share",
         "Non-billable time on a delivery project stays a minor share of the "
         "project's booked hours.",
         "Rework and internal time booked to a customer project is cost with "
         "no revenue against it. It is the difference between a margin problem "
         "you can explain and one you cannot.",
         "low", "detected", 0, 0.5,
         "Live engagements with booked time",
         "Book rework to a category that reports separately, so the "
         "conversation is about the cause rather than the number.",
         "Certinia's Time Credited and Time Excluded flags exist for exactly "
         "this distinction; neither product reports on the share.",
         SRC_CERT_UTIL),
    ]

    # ---------------- actuals integrity ----------------
    c += [
        ("ACT-01", "actuals",
         "Ledger hours match the timesheets",
         f"Monthly ledger hours agree with approved time to within "
         f"{T['actuals_drift_hours']:.0f} hour per engagement.",
         "The whole financial layer reads the ledger. If it has drifted from "
         "the timesheets, every margin, utilisation and consumption figure is "
         "reading a different number from the one the timesheets contain, and "
         "no screen will look wrong.",
         "critical", "detected", 1, 3.0,
         "Every engagement with a financial ledger",
         "Recalculate the affected engagement rather than the whole org, and "
         "find the transaction that arrived mid-run.",
         "Certinia's Actuals Verifier exists for this: it 'checks for "
         "inconsistencies in the actuals' and highlights differences between "
         "transactions and actuals records. What it does not do is say which "
         "classes of inconsistency it detects.",
         SRC_CERT_VERIFIER),

        ("ACT-02", "actuals",
         "Ledger cost matches hours times rate",
         f"Ledger labour cost agrees with hours times the snapshotted cost "
         f"rate to within {T['actuals_drift_money']:.0f}.",
         "Cost drift is harder to spot than hour drift and does more damage, "
         "because it moves margin without moving consumption. A rate changed "
         "retrospectively is the usual cause.",
         "high", "detected", 1, 2.0,
         "Every engagement with booked cost",
         "Freeze the rate on the entry at booking time and recalculate rather "
         "than re-rate.",
         "Implied by Certinia's Actuals Verifier; the arithmetic is not "
         "published.",
         SRC_CERT_VERIFIER),

        ("ACT-03", "actuals",
         "Recognition matches the ledger",
         "Recognised revenue per engagement agrees with the revenue in the "
         "financial ledger.",
         "Two revenue numbers for one engagement is an audit finding waiting "
         "to happen, and the one people quote is whichever screen they opened.",
         "critical", "detected", 1, 2.0,
         "Engagements with revenue recognition posted",
         "Make one of them derived from the other rather than both computed.",
         "Certinia keeps recognition and actuals in separate objects and "
         "publishes no reconciliation.",
         SRC_CERT_VERIFIER),

        ("ACT-04", "actuals",
         "Percent complete matches the plan",
         "Reported percent complete equals earned planned hours over total "
         f"planned hours, within {T['plan_variance_pts']:.0f} points.",
         "Percent complete drives revenue on fixed price work. If it is a "
         "typed-in number rather than a derived one, revenue is a typed-in "
         "number too.",
         "high", "detected", 1, 2.0,
         "Live engagements with a plan",
         "Derive it, weighted by planned effort, so a two-hour task cannot "
         "move it as much as a two-hundred-hour one.",
         "Certinia computes it as approved timecard hours over Estimated Hours "
         "at Completion, capped at 100. Its Calculate Hours Variance batch is "
         "the nearest shipped equivalent to this check, and its formula is not "
         "published.",
         SRC_CERT_VARIANCE),

        ("ACT-05", "actuals",
         "Actuals recalculated since the last time entry",
         "No engagement has time booked after its ledger was last built.",
         "Stale actuals are worse than missing ones: the screen shows a number "
         "with a date on it and the number is out of date.",
         "medium", "detected", 0, 1.0,
         "Engagements with booked time",
         "Schedule the recalculation daily and alarm on failure, because a "
         "failed job leaves the figures locked at yesterday.",
         "Certinia recommends running the Actuals Calculate Delta and RPGPR "
         "Maintenance batches daily.",
         SRC_CERT_VERIFIER),
    ]

    # ---------------- resource and assignment integrity ----------------
    c += [
        ("RES-01", "resource",
         "Nobody committed above capacity",
         f"No person's overlapping commitments exceed "
         f"{T['overallocation_pct']:.0f}% of their capacity.",
         "This is the gap both products leave open, and it is the one that "
         "makes a capacity report useless. An organisation that cannot see "
         "double-booking plans against a supply number it does not have.",
         "critical", "silent", 1, 2.5,
         "Every overlapping window across all confirmed assignments",
         "Refuse the booking, and require a reason where the resource manager "
         "knowingly overbooks for a short window.",
         "Rocketlane documents the absence: percentage-of-capacity allocation "
         "'does not account for time-offs or any existing allocations'. "
         "Certinia's Assignments and Schedules documentation does not address "
         "conflict rules at all.",
         SRC_RL_ALLOC),

        ("RES-02", "resource",
         "No assignment running past a leaver's last day",
         "An assignment ends on or before the resource's end date.",
         "Capacity from somebody who has left is the most confident wrong "
         "number in a resource plan, because nothing about it looks unusual.",
         "high", "silent", 1, 1.5,
         "Assignments for resources with an end date",
         "Release the assignment as part of the leaver process, not after the "
         "capacity report is questioned.",
         "Certinia restricts utilisation calculations by Resource Start Date "
         "and Last Date but documents no assignment validation.",
         SRC_CERT_UTIL),

        ("RES-03", "resource",
         "No confirmed booking on a closed engagement",
         "A complete or cancelled engagement holds no confirmed assignment.",
         "Committed time on finished work shows as demand that will never be "
         "delivered, which makes the capacity gap look worse than it is and "
         "hides the real one.",
         "medium", "silent", 1, 1.0,
         "Assignments on complete or cancelled engagements",
         "Release assignments at closure automatically.",
         SRC_NEITHER, None),

        ("RES-04", "resource",
         "No material time booked without an assignment",
         f"Time above {T['no_allocation_hours']:.0f} hours on an engagement is "
         "covered by an assignment.",
         "Work with no assignment behind it never appeared as demand, so the "
         "capacity model never saw it coming, and it will not see the next one "
         "either.",
         "high", "blocked", 1, 1.5,
         "Time entries against an engagement, grouped by person",
         "Create the assignment even retrospectively; the plan is the record "
         "of what was committed.",
         "Certinia's assignment date restrictions block the entry when they "
         "are on, which is the configuration this check verifies.",
         SRC_CERT_TIMECARD),

        ("RES-05", "resource",
         "Skills on the engagement match what it needs",
         "Somebody on the team holds the primary skill the engagement's "
         "product line requires.",
         "A team assembled from whoever was free is the most expensive kind of "
         "availability. It shows up later as rework rather than as a resourcing "
         "decision.",
         "medium", "silent", 0, 1.0,
         "Live engagements with a product line and a team",
         "Match on skill and proficiency at booking, and record the exception "
         "where availability wins.",
         SRC_NEITHER, None),

        ("RES-06", "resource",
         "No live engagement without a team",
         "An engagement that has started has at least one confirmed "
         "assignment.",
         "An unstaffed live engagement generates no demand signal and no "
         "delivery, and it is usually a handoff that nobody picked up.",
         "high", "detected", 1, 1.5,
         "Engagements that have started",
         "Gate the start of delivery on the team being confirmed.",
         SRC_NEITHER, None),
    ]

    # ---------------- delivery governance ----------------
    c += [
        ("GOV-01", "governance",
         "Every health override carries a comment",
         "A manually set health colour has a note explaining it.",
         "An override with no note is indistinguishable from a mistake, and it "
         "is the note rather than the colour that the portfolio review "
         "actually needs.",
         "high", "silent", 1, 1.5,
         "Engagements whose health was set manually",
         "Make the comment mandatory in the form, not a convention.",
         "Certinia lets the four status colours be 'selected manually (Green, "
         "Yellow, or Red)' and documents no comment requirement.",
         SRC_CERT_RAG),

        ("GOV-02", "governance",
         "Reported health does not diverge two bands from the model",
         "Where a colour is set by hand, it is within one band of the computed "
         "score.",
         "A green project the model scores red is either a project manager who "
         "knows something the data does not, or a project nobody is telling the "
         "truth about. Both need reading before the review, and neither is "
         "visible if the model has no opinion.",
         "critical", "silent", 1, 2.0,
         "Engagements with both a reported and a computed health",
         "Review divergence weekly. The model being wrong is a finding about "
         "the model; the project being wrong is a finding about the project.",
         "Neither product computes a health score, so neither can detect "
         "divergence. Certinia ships the four-picklist structure and expects "
         "the customer to supply the driver logic in Flow.",
         SRC_CERT_RAG),

        ("GOV-03", "governance",
         "Every plan has a baseline",
         "An engagement with a plan has a baseline recorded against it.",
         "A plan with no baseline has nothing to be late against, so schedule "
         "variance cannot be computed and slip becomes a matter of opinion.",
         "high", "detected", 1, 1.5,
         "Engagements with a plan",
         "Baseline at kickoff, and require a reason on every re-baseline.",
         SRC_NEITHER, None),

        ("GOV-04", "governance",
         "Every live engagement has a named manager",
         "A live engagement has a project manager assigned.",
         "Nobody accountable means nobody forecasting, and the estimate at "
         "completion falls back to the plan for the rest of the engagement.",
         "high", "detected", 1, 1.5,
         "Live engagements",
         "Assign at handoff acceptance, not at kickoff.",
         SRC_NEITHER, None),

        ("GOV-05", "governance",
         "Forecasts refreshed within the review cycle",
         f"A live engagement has a forecast submitted in the last "
         f"{T['stale_forecast_days']} days.",
         "A stale forecast is quoted as though it were current. The month it "
         "was submitted in matters as much as the number.",
         "medium", "detected", 1, 1.0,
         "Live engagements past their first month",
         "Ask for the forecast monthly and show the submission date beside it "
         "everywhere it is used.",
         "Certinia gates revenue forecasting on the prior month being Closed "
         "for Forecasting, which is the closest thing either product has to a "
         "forecast cadence rule.",
         SRC_CERT_PERIOD),

        ("GOV-06", "governance",
         "Missed milestones have a revised date",
         "A milestone past its planned date is either complete or has a "
         "forecast date in the future.",
         "A milestone that has silently passed is a schedule that has stopped "
         "meaning anything. It is also the point at which a customer stops "
         "believing the next date.",
         "high", "detected", 1, 1.5,
         "Milestones past their planned date on live engagements",
         "Re-forecast the date and say why, in the same action.",
         SRC_NEITHER, None),

        ("GOV-07", "governance",
         "No engagement running past its end date without a revision",
         "An engagement past its planned end date has a forecast end date "
         "beyond today.",
         "This is the simplest signal of an engagement nobody is managing, and "
         "it is the one most often missed because the project still looks "
         "green.",
         "high", "detected", 1, 1.5,
         "Live engagements with a planned end date",
         "Re-forecast or close. A live engagement with a date in the past is "
         "neither.",
         SRC_NEITHER, None),

        ("GOV-08", "governance",
         "Open customer escalations are owned",
         "A customer-raised issue has an owner and a due date.",
         "An escalation with no owner is the fastest route from a delivery "
         "problem to a commercial one.",
         "high", "detected", 0, 1.0,
         "Open customer-raised issues",
         "Assign at the point of escalation and review daily until closed.",
         SRC_NEITHER, None),
    ]

    # ---------------- period close and lock ----------------
    c += [
        ("CLOSE-01", "close",
         "No time posted into a closed period",
         "Approved time entries fall inside a period still open for posting.",
         "A posting into a closed period restates a reported result. Certinia's "
         "own documentation is explicit that a Hard Closed accounting period "
         "blocks all postings including cash matching and revaluation, but it "
         "does not tie a timecard save to that status.",
         "critical", "silent", 1, 2.0,
         "Approved time entries older than the close boundary",
         "Lock time entry against the accounting calendar, not only against "
         "the timesheet status.",
         "Certinia's PSA-side lock is a single boolean, Closed for "
         "Forecasting, and it is scoped only to revenue forecasting. The "
         "three-state Open / Soft Closed / Hard Closed control lives in "
         "Accounting, and no documentation ties PSA entry to it.",
         SRC_CERT_PERIOD),

        ("CLOSE-02", "close",
         "One recognition posting per engagement and period",
         "An engagement has at most one recognition row per period.",
         "Two postings for one period double-count revenue, and the duplicate "
         "is invisible in a total.",
         "critical", "blocked", 1, 2.0,
         "Revenue recognition rows",
         "Constrain it in the schema rather than in the process.",
         SRC_NEITHER, None),

        ("CLOSE-03", "close",
         "Posted periods are not restated",
         "A period marked posted has not been changed since.",
         "Restating a posted period without a trail is what an auditor looks "
         "for first, and what a finance team gets asked about last.",
         "high", "detected", 1, 1.5,
         "Posted recognition rows",
         "Make posted rows immutable and handle corrections as adjusting "
         "periods.",
         "Certinia's Soft Closed still accepts journals; Hard Closed does not. "
         "The distinction is worth copying.",
         SRC_CERT_PERIOD),

        ("CLOSE-04", "close",
         "Financial changes carry a reason",
         "An audited change to a financial field records why it was made.",
         "A trail that records what changed but not why answers the wrong "
         "question. The reason column is the point.",
         "medium", "detected", 1, 1.0,
         "Audit entries on financial fields",
         "Capture the reason in the same transaction as the change, and refuse "
         "the change without it.",
         SRC_NEITHER, None),
    ]

    # ---------------- migration and intake ----------------
    c += [
        ("MIG-01", "migration",
         "Every migrated record has lineage back to the source",
         "A record loaded by migration can be traced to its legacy identifier.",
         "Without lineage the first reconciliation question after go live has "
         "no answer, and the answer is what buys the trust that makes people "
         "use the new system.",
         "critical", "detected", 1, 2.5,
         "Records loaded through a migration run",
         "Keep the legacy key and payload on every loaded row, permanently, "
         "not just through the cutover.",
         "Certinia offers configuration import and Compare Configuration but "
         "no data lineage. Rocketlane documents no import validation at all.",
         SRC_NEITHER),

        ("MIG-02", "migration",
         "Blocking intake findings are cleared",
         "No import batch carries an unresolved error-severity finding.",
         "An error-severity finding is a row that did not load. Going live "
         "over the top of it means the gap is discovered by a customer.",
         "critical", "detected", 1, 2.5,
         "Import batches and their validation findings",
         "Clear errors, decide explicitly on warnings, and record the decision.",
         SRC_NEITHER, None),

        ("MIG-03", "migration",
         "Mapping decisions are approved rather than inferred",
         "A low-confidence mapping rule has been reviewed by a person.",
         "An auto-mapped value that nobody checked reclassifies history. A "
         "category mapped wrong changes every report built on it, silently.",
         "high", "detected", 1, 1.5,
         "Mapping rules below the confidence threshold",
         "Review every suggested mapping under the threshold, and record who "
         "approved it.",
         SRC_NEITHER, None),

        ("MIG-04", "migration",
         "Financial history reconciles to the source",
         "Migrated financial completeness reaches the threshold set for "
         "cutover.",
         "Migrating an engagement without its financial history leaves a "
         "margin that starts at go live, which nobody can compare to anything.",
         "critical", "detected", 1, 2.0,
         "The migration run's completeness measures",
         "Reconcile revenue, cost and hours by month against the source before "
         "cutover, not after.",
         SRC_NEITHER, None),
    ]

    # ---------------- configuration and readiness ----------------
    c += [
        ("CFG-01", "config",
         "Utilisation targets set for every role in use",
         "Every role with billable people has a utilisation target.",
         "A role with no target is measured against a default that does not "
         "apply to it, which is how a practice lead at forty percent gets "
         "reported as a problem.",
         "high", "detected", 1, 1.5,
         "Roles held by active billable resources",
         "Set the target by role, and let the person's record override it.",
         "Certinia and Rocketlane both compute utilisation but neither "
         "publishes role-level target configuration as a shipped feature.",
         SRC_CERT_UTIL),

        ("CFG-02", "config",
         "Margin targets set for every practice",
         "Every practice delivering work has a target margin.",
         "Without a target, margin has no verdict, and a portfolio of numbers "
         "with no verdict does not get acted on.",
         "high", "detected", 1, 1.5,
         "Practices with live engagements",
         "Set it per practice; the company number is an average, not a target.",
         SRC_NEITHER, None),

        ("CFG-03", "config",
         "Bill rates set for every billing role",
         "Every role with billable assignments has a rate.",
         "A missing rate is the upstream cause of the billable-time-with-no-"
         "rate leak, and it is far cheaper to fix here.",
         "critical", "detected", 1, 2.0,
         "Roles on confirmed billable assignments",
         "Complete the rate card before the first assignment, and refuse the "
         "assignment where no rate resolves.",
         SRC_CERT_BILLING),

        ("CFG-04", "config",
         "Approval hierarchy complete",
         "Every active resource who books time has a manager.",
         "An incomplete hierarchy is why submitted timesheets sit unapproved, "
         "and the consequence lands on the financials rather than on the "
         "person with the gap.",
         "high", "detected", 1, 1.5,
         "Active resources who book time",
         "Complete it before go live and monitor it after every joiner.",
         "Certinia's timecard submission prerequisite is a configured approval "
         "process.",
         SRC_CERT_TIMECARD),

        ("CFG-05", "config",
         "Cost rates set for everybody who books time",
         "Every resource booking time has a cost rate.",
         "No cost rate means no cost, which means a margin that looks better "
         "than it is on exactly the engagements that person worked on.",
         "critical", "detected", 1, 2.0,
         "Resources with booked time",
         "Set it at hire, and treat a missing cost rate as a blocker on "
         "booking time.",
         SRC_NEITHER, None),

        ("CFG-06", "config",
         "Every engagement has a billing model and a contract value",
         "A live engagement has both, because revenue cannot be recognised "
         "without them.",
         "The billing model decides how revenue is earned. Missing, it "
         "defaults, and the default is wrong for most of the portfolio.",
         "critical", "detected", 1, 2.0,
         "Live engagements",
         "Require both at project creation, sourced from the signed contract.",
         SRC_NEITHER, None),

        ("CFG-07", "config",
         "Product line recorded on every engagement",
         "A live engagement records which product line it delivers.",
         "Without it there is no resource requirement by product line, no "
         "practice margin and no benchmark cohort. It is the cheapest field to "
         "populate and the most expensive to be missing.",
         "high", "detected", 1, 1.5,
         "Live engagements",
         "Make it mandatory at creation and derive it from the product where "
         "one is set.",
         SRC_NEITHER, None),
    ]

    # ---------------- earned value ----------------
    #
    # This family exists because consumption, progress and margin between them
    # cannot separate a cost problem from a schedule one. An engagement at 60%
    # consumed and 40% complete and one at 40% consumed and 40% complete
    # against a plan that expected 60% look similar on a burn chart and need
    # opposite responses. CPI and SPI are the standard way to tell them apart,
    # and neither Certinia nor Rocketlane publishes either: Certinia's variance
    # component compares budget to actuals, and its percent complete is hours
    # spent over estimate at completion, which makes EV equal AC by
    # construction and CPI identically 1.00.
    c += [
        ("EVM-01", "earnedvalue",
         "Cost performance stays within tolerance",
         "No live engagement is delivering earned value below the cost index "
         "floor.",
         "A cost index below the floor says the work completed so far cost "
         "more than it was worth. Caught at 30% complete it is a staffing or "
         "scope conversation; caught at 80% it is a write-down.",
         "high", "silent", 0, 0.0,
         "Live engagements far enough along to carry an index",
         "Re-baseline or re-staff. The index will not recover on its own, "
         "because the variance is already spent.",
         "Neither product publishes a cost performance index. Certinia's "
         "percent complete is hours spent over estimate at completion, under "
         "which earned value equals actual cost and the index cannot fall "
         "below 1.00 however much money the engagement is losing.",
         SRC_CERT_VARIANCE),

        ("EVM-02", "earnedvalue",
         "Schedule performance stays within tolerance",
         "No live engagement has earned materially less value than its own "
         "plan expected by now.",
         "The schedule index is measured against the plan's own dates, so it "
         "answers a question a burn chart cannot: is the work late, or was the "
         "plan always front-loaded.",
         "high", "silent", 0, 0.0,
         "Live engagements far enough along to carry an index",
         "Re-sequence, or move the date and say so. A slipping plan nobody "
         "has re-dated is the input to every wrong forecast downstream.",
         "Neither product publishes a schedule performance index.",
         SRC_NEITHER),

        ("EVM-03", "earnedvalue",
         "No engagement is over cost and behind schedule at once",
         "No live engagement sits in the over-cost and behind-schedule "
         "quadrant.",
         "Either index alone usually has a recoverable explanation. Together "
         "they mean both levers are already spent: the work done cost more "
         "than it was worth and there is less of it than planned.",
         "critical", "silent", 0, 0.0,
         "Live engagements far enough along to carry an index",
         "Escalate as a recovery rather than a variance, and decide explicitly "
         "whether the remaining scope is still worth delivering.",
         SRC_NEITHER, None),

        ("EVM-04", "earnedvalue",
         "Recovery to budget is arithmetically credible",
         "No engagement needs its remaining work delivered at an efficiency "
         "far above anything it has achieved.",
         "The efficiency the remainder needs is computable, and comparing it "
         "to the efficiency achieved so far is what separates a plan from a "
         "hope. A remainder that needs 1.40 from a team running at 0.80 is a "
         "budget that has already gone, whatever the status report says.",
         "critical", "silent", 0, 0.0,
         "Live engagements with budget remaining",
         "Take the variance now. A forecast that assumes an efficiency the "
         "team has never reached is the most expensive kind of optimism.",
         SRC_NEITHER, None),

        ("EVM-05", "earnedvalue",
         "Recorded progress is supported by booked time",
         "Every live engagement's task progress is backed by timesheet hours "
         "in the same proportion.",
         "Percent complete is the denominator of earned value, revenue "
         "recognition on fixed fee, and every forecast built on either. A plan "
         "marked 78% complete with eighteen hours booked against it produces a "
         "cost index of 9.5 and a margin that reads as free delivery. The "
         "index is not wrong; the input is.",
         "critical", "detected", 1, 2.5,
         "Live engagements with a current plan and booked time",
         "Reconcile the two before publishing an index off either. Either the "
         "hours are missing or the progress is optimistic, and both are worth "
         "knowing.",
         "Certinia ships an Actuals Verifier that reconciles actuals to "
         "timecards, but nothing that reconciles recorded progress to booked "
         "effort. Rocketlane documents neither.",
         SRC_CERT_VERIFIER),

        ("EVM-06", "earnedvalue",
         "Live engagements are measurable at all",
         "Every live engagement has a current plan carrying planned hours and "
         "planned dates.",
         "Without planned effort and planned dates there is no planned value, "
         "so no index can be computed. The engagement is not performing well "
         "or badly, it is simply unmeasured, and unmeasured engagements are "
         "where the surprises come from.",
         "high", "detected", 1, 2.0,
         "Live engagements",
         "Baseline the plan. A plan with hours but no dates cannot carry a "
         "schedule index; one with dates but no hours cannot carry a cost one.",
         SRC_NEITHER, None),
    ]

    out = []
    for i, row in enumerate(c):
        # Some rows omit the source when the basis is "neither product
        # documents this", because there is nothing to cite. Pad rather than
        # making every such row carry a None.
        row = tuple(row) + (None,) * (13 - len(row))
        (code, family, title, assertion, why, severity, enforcement, gate,
         weight, scope, remediation, basis, source) = row
        if code in OPERATIONAL:
            gate = 0
        cls = "readiness" if gate else "operational"
        out.append((code, family, FAMILIES[family], title, assertion, why,
                    severity, enforcement, gate, weight, scope, remediation,
                    basis, source, cls, i * 10))
    return out


# =====================================================================
# Metric definitions
# =====================================================================
def metric_definitions():
    return [
        ("billable_utilization", "Billable utilisation",
         "billable hours / (capacity - absence - holidays)",
         "Hours flagged billable on approved or locked timesheets",
         "Working-calendar hours for the person, less booked absence and "
         "statutory holidays",
         "Only approved time counts, because a draft timesheet is not yet a "
         "fact. Absence reduces the denominator rather than counting as "
         "non-billable work, so booking holiday does not make somebody look "
         "badly utilised.",
         "Rocketlane",
         "Billable utilization = Billable time tracked / (Capacity - Timeoffs "
         "- Holidays)",
         SRC_RL_UTIL,
         "None. This is Rocketlane's formula, and it is the more explainable "
         "of the two published models.", 10),

        ("total_utilization", "Total utilisation",
         "(billable + non-billable project hours) / (capacity - absence - holidays)",
         "All project hours, billable or not",
         "As above",
         "Reported beside billable utilisation rather than instead of it. The "
         "gap between the two is the cost of non-billable delivery work, which "
         "is a different conversation from the bench.",
         "Rocketlane",
         "Utilization = Time tracked / (Capacity - Timeoffs - Holidays)",
         SRC_RL_UTIL, "None.", 20),

        ("certinia_current_utilization", "Utilisation, Certinia current basis",
         "(billable timecard + billable assigned + credited timecard + "
         "credited assigned) / (total work hours - excluded timecard - "
         "excluded assigned)",
         "Delivered billable hours plus scheduled billable hours, plus "
         "anything flagged as credited toward utilisation",
         "Total work hours less anything flagged excluded",
         "Shown for comparison, not used as the headline. It blends delivered "
         "and scheduled hours in one number, which makes it forward-looking "
         "and harder to reconcile to a timesheet. Certinia publishes three "
         "variants: historical, scheduled and current.",
         "Certinia",
         "Current Utilization = (Billable Timecard Hours + Billable Assigned "
         "Hours + Credited Timecard Hours + Credited Assigned Hours) / (Total "
         "Work Hours - Excluded Timecard Hours - Excluded Assigned Hours)",
         SRC_CERT_UTIL,
         "This engine keeps delivered and scheduled hours apart, reporting "
         "utilisation on delivered time and capacity on scheduled time, "
         "because mixing them makes a variance impossible to attribute.", 30),

        ("gross_margin_pct", "Gross margin",
         "(revenue - cost) / revenue",
         "Recognised revenue less labour cost at snapshotted rates and booked "
         "expenses",
         "Recognised revenue",
         "Computed on recognised revenue rather than invoiced, so it does not "
         "move when an invoice is raised. Portfolio margin is the sum of the "
         "parts, never an average of project margins.",
         "Rocketlane",
         "Profit Margin = (Profit / Revenue) * 100, and for fixed fee "
         "(Profit / Recognised revenue) * 100",
         SRC_RL_FIN,
         "None. Certinia publishes no margin formula at all.", 40),

        ("percent_complete", "Percent complete",
         "earned planned hours / total planned hours",
         "Sum of each task's planned hours times its percent complete",
         "Sum of planned hours across the plan",
         "Weighted by planned effort, so a two-hour task cannot move the "
         "number as much as a two-hundred-hour one. Capped at 100.",
         "Certinia",
         "(Sum of approved timecard hours / Estimated Hours at Completion) * "
         "100 at project level, / Planned Hours at milestone level, capped at "
         "100%",
         SRC_CERT_VARIANCE,
         "Certinia measures progress by hours spent against the estimate, "
         "which makes percent complete rise when a project overspends. This "
         "engine measures earned plan instead, so spending more hours does "
         "not itself constitute progress. That distinction is the whole basis "
         "of the budget-versus-progress variance signal.", 50),

        ("eac_hours", "Estimate at completion, hours",
         "blend of plan, observed velocity and the submitted forecast",
         None, None,
         "Velocity is actual hours divided by percent complete. Its weight "
         "rises with progress and is capped, so an early bad fortnight cannot "
         "rewrite the whole remaining estimate. The project manager's own "
         "forecast is given half the weight of the model: overriding the model "
         "entirely hides a manager who is wrong, and ignoring them throws away "
         "what they know.",
         "Rocketlane",
         "EAC appears only as a revenue recognition denominator: (Tracked "
         "hours for that period / EAC) * Fixed Fee",
         SRC_RL_FIN,
         "Neither product publishes an EAC derivation. Certinia's equivalent, "
         "Estimated Hours at Completion, is an input somebody types in. This "
         "engine derives it and states the blend.", 60),

        ("revenue_at_risk", "Revenue at risk",
         "forecast revenue at completion on engagements that are not green",
         None, None,
         "Forecast rather than recognised, because the exposure is the rest of "
         "the engagement, not the part already earned.",
         None, None, None,
         "Neither product defines this. It is included because it is the "
         "number an executive acts on.", 70),

        ("productive_hours_per_fte", "Productive hours per FTE per month",
         "(standard weekly hours x weeks per month x productive utilisation) "
         "- training and absence",
         None, None,
         "The denominator of the resource requirement model. Using raw "
         "capacity instead is the most common reason a hiring plan is short: "
         "it assumes nobody trains, takes leave or sells.",
         "Rocketlane",
         "FTE = (Total Hours available by All Employees) / (Standard Hours for "
         "a Full-Time Employee); each day of time off or holiday subtracts 0.2 "
         "FTE on a five-day week",
         SRC_RL_UTIL,
         "Rocketlane's FTE arithmetic covers availability. This adds the "
         "training target and the productive-utilisation ceiling, because a "
         "consultant available for work is not a consultant available for "
         "billable work.", 80),

        ("cpi", "Cost performance index",
         "earned value / actual cost",
         "Budget cost at completion times earned percent complete",
         "Labour cost incurred to date",
         "Above one is under cost. Measured on labour on both sides, because "
         "budget at completion here is budget hours at a planned cost rate and "
         "carries no expense allowance; putting expenses in the numerator's "
         "denominator and not in the budget would read about three points "
         "worse than the delivery it is measuring.",
         "Certinia",
         "No index is published. The variance component compares budget to "
         "actuals, and percent complete is (approved timecard hours / "
         "estimated hours at completion) x 100",
         SRC_CERT_VARIANCE,
         "Under Certinia's percent complete, earned value equals actual cost "
         "by construction and this index is identically 1.00, so it cannot "
         "report a cost problem however much money the engagement is losing. "
         "That is why progress here is earned plan and not hours spent.", 90),

        ("spi", "Schedule performance index",
         "earned value / planned value",
         "Budget cost at completion times earned percent complete",
         "Budget cost at completion times planned percent complete at the "
         "as-of date",
         "Planned percent complete is the share of planned task hours whose "
         "planned end date has passed, taken from the plan's own dates rather "
         "than from elapsed calendar time. A plan that front-loads its effort "
         "is therefore measured against itself, which is the difference "
         "between reporting slip and reporting shape.",
         None, None, None,
         "Neither product publishes a schedule performance index. Rocketlane "
         "reports task-level delay; Certinia reports date variance. Neither "
         "expresses either as effort earned against effort planned.", 100),

        ("tcpi", "To-complete performance index",
         "(budget at completion - earned value) / "
         "(budget at completion - actual cost)",
         "Budget cost still to be earned",
         "Budget cost still available to spend",
         "The efficiency the remaining work has to be delivered at to finish "
         "on budget. Its only use is the comparison against the efficiency "
         "achieved so far: a remainder needing 1.40 from a team running at "
         "0.80 is a budget that has already gone.",
         None, None, None,
         "Neither product publishes this.", 110),
    ]


# =====================================================================
# The runner
#
# Each check is a function returning (population, findings). A finding is a
# dict with the entity, what was observed, what was expected and a sentence.
# The runner turns those into a status and a value at stake, so the catalogue
# and the arithmetic stay separable: a rule can be reviewed without reading
# the query, and the query can be fixed without editing the rule.
# =====================================================================
def _rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(con, sql, args=(), default=0):
    row = con.execute(sql, args).fetchone()
    return default if row is None or row[0] is None else row[0]


def week_of(expr):
    """
    SQL for the Monday that starts the week containing ``expr``.

    strftime('%Y-%W') looks like a week key and is not one: a week straddling
    31 December lands in two buckets, so a full week reads as two short ones
    and the short-week check fires on a calendar artefact. Anchoring on the
    Monday makes a week a week whatever year it starts in.
    """
    return (f"date({expr}, '-' || ((strftime('%w', {expr})+6)%7) "
            f"|| ' days')")


def _f(x, observed=None, expected=None, msg="", sev=None, value=None,
       unit=None):
    """Build a finding row. ``x`` is (entity_table, entity_pk, entity_label)."""
    return {"entity_table": x[0], "entity_pk": x[1], "entity_label": x[2],
            "observed": observed, "expected": expected, "message": msg,
            "severity": sev, "value_at_stake": value, "value_unit": unit}


# ---------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------
def chk_bill_01(con, org, as_of):
    """Approved billable time, past the invoicing window, with no invoice."""
    cutoff = (dt.date.fromisoformat(as_of)
              - dt.timedelta(days=T["unbilled_age_days"])).isoformat()
    population = _one(con, """
        SELECT COUNT(*) FROM time_entry
        WHERE org_id=? AND approval_status='Approved' AND is_billable=1
          AND entry_date<=?""", (org, cutoff))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.billing_model,
               SUM(t.hours) AS hours,
               SUM(t.hours * COALESCE(t.bill_rate,0)) AS value,
               MIN(t.entry_date) AS oldest
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
        WHERE t.org_id=? AND t.approval_status='Approved' AND t.is_billable=1
          AND t.invoiced=0 AND t.entry_date<=?
          AND p.billing_model IN ('Time and Materials','Capped T&M')
        GROUP BY p.project_id
        HAVING SUM(t.hours * COALESCE(t.bill_rate,0)) > 500
        ORDER BY value DESC""", (org, cutoff))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['hours']:,.1f}h uninvoiced, {r['value']:,.0f} of value",
           expected="invoiced or written off",
           msg=(f"{r['hours']:,.1f} approved billable hours worth "
                f"{r['value']:,.0f} have no invoice line, the oldest dating "
                f"from {r['oldest']}. On {r['billing_model']} that value is "
                f"contractually billable and is simply not being asked for."),
           sev="critical" if r["value"] > 25000 else "high",
           value=r["value"], unit="currency")
        for r in rows]
    return population, findings


def chk_bill_02(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM project_task tk JOIN project p USING(project_id)
        WHERE p.org_id=? AND tk.is_milestone=1 AND COALESCE(tk.billing_amount,0)>0
          AND tk.status='Complete'""", (org,))
    cutoff = (dt.date.fromisoformat(as_of) - dt.timedelta(days=30)).isoformat()
    rows = _rows(con, """
        SELECT tk.task_id, tk.task_name, tk.billing_amount, tk.actual_end,
               p.project_id, p.project_code, p.project_name
        FROM project_task tk JOIN project p USING(project_id)
        WHERE p.org_id=? AND tk.is_milestone=1
          AND COALESCE(tk.billing_amount,0)>0
          AND tk.status='Complete'
          AND COALESCE(tk.billing_status,'Not Ready')='Not Ready'
          AND tk.actual_end IS NOT NULL AND tk.actual_end<=?
        ORDER BY tk.billing_amount DESC""", (org, cutoff))
    findings = [
        _f(("project_task", r["task_id"],
            f"{r['project_code']} {r['task_name']}"),
           observed=f"complete on {r['actual_end']}, billing status Not Ready",
           expected="released for billing",
           msg=(f"{r['task_name']} on {r['project_code']} was completed on "
                f"{r['actual_end']} and carries {r['billing_amount']:,.0f} of "
                f"billing value that has never been released. The customer has "
                f"accepted the work."),
           sev="critical" if r["billing_amount"] > 40000 else "high",
           value=r["billing_amount"], unit="currency")
        for r in rows]
    return population, findings


def chk_bill_03(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? "
                           "AND is_billable=1", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               SUM(t.hours) AS hours, COUNT(*) AS entries,
               e.employee_name
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
             LEFT JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=? AND t.is_billable=1
          AND (t.bill_rate IS NULL OR t.bill_rate<=0)
        GROUP BY p.project_id ORDER BY hours DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['hours']:,.1f}h billable with no rate",
           expected="a rate on every billable hour",
           msg=(f"{r['entries']:,} billable entries totalling "
                f"{r['hours']:,.1f} hours carry no bill rate on "
                f"{r['project_code']}. They cost money, earn nothing and fall "
                f"out of billing without an error."),
           sev="high", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_bill_04(con, org, as_of):
    # Recognition lives in revenue_recognition, not on the project row. Reading
    # project.revenue_recognized here made a critical check report "nothing in
    # scope" against a portfolio that had 2,826 recognition postings, which is
    # the most dangerous way for a check to fail.
    population = _one(con, """
        SELECT COUNT(DISTINCT p.project_id)
        FROM project p JOIN revenue_recognition rr USING(project_id)
        WHERE p.org_id=? AND p.billing_model IN ('Fixed Fee','Milestone','Retainer')
          AND rr.recognized_amount > 0""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.billing_model,
               p.contract_value,
               COALESCE(p.contract_value,0)
                 + COALESCE((SELECT SUM(co.co_value) FROM change_order co
                             WHERE co.project_id=p.project_id
                               AND co.status='Approved'),0) AS ceiling,
               COALESCE(SUM(rr.recognized_amount),0) AS recognised
        FROM project p LEFT JOIN revenue_recognition rr USING(project_id)
        WHERE p.org_id=? AND p.billing_model IN ('Fixed Fee','Milestone','Retainer')
        GROUP BY p.project_id
        HAVING recognised > ceiling * 1.005 AND ceiling > 0
        ORDER BY (recognised - ceiling) DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['recognised']:,.0f} recognised",
           expected=f"no more than {r['ceiling']:,.0f}",
           msg=(f"{r['project_code']} has recognised {r['recognised']:,.0f} "
                f"against a ceiling of {r['ceiling']:,.0f} including approved "
                f"change orders, which is "
                f"{(r['recognised']/r['ceiling']-1)*100:,.1f}% over the cap."),
           sev="critical",
           value=r["recognised"] - r["ceiling"], unit="currency")
        for r in rows]
    return population, findings


def chk_bill_05(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.contract_value,
               SUM(tk.billing_amount) AS milestone_total,
               COUNT(*) AS milestones
        FROM project p JOIN project_task tk USING(project_id)
        WHERE p.org_id=? AND tk.is_milestone=1
          AND COALESCE(tk.billing_amount,0)>0 AND COALESCE(p.contract_value,0)>0
        GROUP BY p.project_id""", (org,))
    tol = T["milestone_sum_tolerance_pct"] / 100
    findings = []
    for r in rows:
        drift = r["milestone_total"] / r["contract_value"] - 1
        if abs(drift) <= tol:
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"{r['milestone_total']:,.0f} across {r['milestones']} milestones",
            expected=f"{r['contract_value']:,.0f}",
            msg=(f"The billing schedule on {r['project_code']} sums to "
                 f"{r['milestone_total']:,.0f} against a contract of "
                 f"{r['contract_value']:,.0f}, which is {drift*100:+,.1f}%. "
                 + ("The shortfall never gets invoiced."
                    if drift < 0 else "The excess gets disputed.")),
            sev="high" if abs(drift) > 0.1 else "medium",
            value=abs(r["milestone_total"] - r["contract_value"]), unit="currency"))
    return len(rows), findings


def chk_bill_06(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "billing_model='Capped T&M'", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.contract_value,
               COALESCE(SUM(pfm.invoiced_revenue),0) AS invoiced
        FROM project p LEFT JOIN project_financial_month pfm USING(project_id)
        WHERE p.org_id=? AND p.billing_model='Capped T&M'
          AND COALESCE(p.contract_value,0)>0
        GROUP BY p.project_id
        HAVING invoiced > p.contract_value * 1.005
        ORDER BY (invoiced - p.contract_value) DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['invoiced']:,.0f} invoiced",
           expected=f"a ceiling of {r['contract_value']:,.0f}",
           msg=(f"{r['project_code']} is a capped engagement invoiced "
                f"{r['invoiced'] - r['contract_value']:,.0f} past its ceiling. "
                f"Either the cap was renegotiated and nobody updated the "
                f"contract value, or the invoice will be credited."),
           sev="high", value=r["invoiced"] - r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


def chk_bill_07(con, org, as_of):
    cutoff = (dt.date.fromisoformat(as_of)
              - dt.timedelta(days=T["unbilled_age_days"])).isoformat()
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               SUM(t.hours * COALESCE(t.bill_rate,0)) AS value,
               MIN(t.entry_date) AS oldest
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
        WHERE t.org_id=? AND t.approval_status='Approved' AND t.is_billable=1
          AND t.invoiced=0 AND t.entry_date<=?
        GROUP BY p.project_id HAVING value>1000 ORDER BY value DESC""",
        (org, cutoff))
    # The detail is per project across the whole book, including closed work,
    # because unbilled value on a closed engagement is the worse case. The
    # population has to match that grain.
    population = _one(con, """
        SELECT COUNT(DISTINCT p.project_id)
        FROM project p JOIN time_entry t USING(project_id)
        WHERE p.org_id=? AND t.approval_status='Approved' AND t.is_billable=1""",
        (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"oldest unbilled hour dates from {r['oldest']}",
           expected=f"nothing older than {T['unbilled_age_days']} days",
           msg=(f"{r['value']:,.0f} of approved billable value on "
                f"{r['project_code']} has been unbilled since {r['oldest']}. "
                f"The older the hour, the harder it is to defend."),
           sev="medium", value=r["value"], unit="currency")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------
def chk_time_01(con, org, as_of):
    """Scheduled in the month, active, no time booked at all."""
    month = as_of[:7]
    prev = (dt.date.fromisoformat(as_of[:7] + "-01") - dt.timedelta(days=1)).isoformat()[:7]
    rows = _rows(con, """
        SELECT e.employee_id, e.employee_name, e.role,
               COUNT(DISTINCT a.project_id) AS projects
        FROM employee e JOIN assignment a ON a.employee_id=e.employee_id
        WHERE e.org_id=? AND e.is_active=1 AND e.is_billable=1
          AND a.start_date<=? AND a.end_date>=?
          AND NOT EXISTS (SELECT 1 FROM time_entry t
                          WHERE t.employee_id=e.employee_id
                            AND substr(t.entry_date,1,7)=?)
        GROUP BY e.employee_id ORDER BY projects DESC""",
        (org, prev + "-28", prev + "-01", prev))
    population = _one(con, """
        SELECT COUNT(DISTINCT e.employee_id)
        FROM employee e JOIN assignment a ON a.employee_id=e.employee_id
        WHERE e.org_id=? AND e.is_active=1 AND e.is_billable=1
          AND a.start_date<=? AND a.end_date>=?""",
        (org, prev + "-28", prev + "-01"))
    findings = [
        _f(("employee", r["employee_id"], r["employee_name"]),
           observed=f"no time booked in {prev}",
           expected="a submitted timesheet for every scheduled week",
           msg=(f"{r['employee_name']} ({r['role']}) was scheduled on "
                f"{r['projects']} engagement(s) through {prev} and booked no "
                f"time at all. Their cost is real and it has landed nowhere."),
           sev="high")
        for r in rows]
    return population, findings


def chk_time_02(con, org, as_of):
    rows = _rows(con, """
        SELECT t.employee_id, e.employee_name, t.entry_date,
               SUM(t.hours) AS day_hours, COUNT(*) AS entries
        FROM time_entry t LEFT JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=?
        GROUP BY t.employee_id, t.entry_date
        HAVING day_hours > ? ORDER BY day_hours DESC""",
        (org, T["day_hour_ceiling"]))
    population = _one(con, "SELECT COUNT(DISTINCT employee_id || entry_date) "
                           "FROM time_entry WHERE org_id=?", (org,))
    findings = [
        _f(("time_entry", None, f"{r['employee_name']} on {r['entry_date']}"),
           observed=f"{r['day_hours']:,.2f}h across {r['entries']} entries",
           expected=f"no more than {T['day_hour_ceiling']:.0f}h in a day",
           msg=(f"{r['employee_name']} has {r['day_hours']:,.2f} hours booked "
                f"on {r['entry_date']} across {r['entries']} entries. Each "
                f"entry is individually plausible; the day is not."),
           sev="high", value=r["day_hours"] - T["day_hour_ceiling"], unit="hours")
        for r in rows]
    return population, findings


def chk_time_03(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=?", (org,))
    rows = _rows(con, """
        SELECT t.time_entry_id, e.employee_name, t.entry_date, t.hours,
               p.project_code
        FROM time_entry t LEFT JOIN employee e ON e.employee_id=t.employee_id
             LEFT JOIN project p ON p.project_id=t.project_id
        WHERE t.org_id=? AND t.hours<=0""", (org,))
    findings = [
        _f(("time_entry", r["time_entry_id"],
            f"{r['employee_name']} on {r['entry_date']}"),
           observed=f"{r['hours']}h", expected="above zero",
           msg=(f"An entry of {r['hours']} hours on {r['entry_date']} distorts "
                f"the denominator of every rate calculation it touches."),
           sev="medium")
        for r in rows]
    return population, findings


def chk_time_04(con, org, as_of):
    # Two exclusions, both about not reporting an artefact as a finding. The
    # week containing the as-of date is still being filled in, and somebody's
    # first week is short because they started mid-week, not because the
    # timesheet is wrong.
    where = f"""
        WHERE t.org_id=? AND t.approval_status IN ('Approved','Submitted')
          AND e.is_billable=1 AND e.weekly_capacity_hours>=37
          AND e.is_active=1
          AND {week_of('t.entry_date')} < {week_of('?')}
          AND t.entry_date >= date(e.start_date, '+7 days')"""
    rows = _rows(con, f"""
        SELECT t.employee_id, e.employee_name,
               {week_of('t.entry_date')} AS wk,
               SUM(t.hours) AS week_hours
        FROM time_entry t JOIN employee e ON e.employee_id=t.employee_id
        {where}
        GROUP BY t.employee_id, wk
        HAVING week_hours < ? AND week_hours > 0
        ORDER BY week_hours ASC""",
        (org, as_of, as_of, T["week_hours_min"] * 0.6))
    population = _one(con, f"""
        SELECT COUNT(*) FROM (
          SELECT t.employee_id, {week_of('t.entry_date')} w
          FROM time_entry t JOIN employee e ON e.employee_id=t.employee_id
          {where}
          GROUP BY 1,2)""", (org, as_of, as_of))
    findings = [
        _f(("employee", r["employee_id"], f"{r['employee_name']} week {r['wk']}"),
           observed=f"{r['week_hours']:,.1f}h approved",
           expected=f"at least {T['week_hours_min']:,.1f}h including absence",
           msg=(f"{r['employee_name']} has {r['week_hours']:,.1f} hours "
                f"approved for week {r['wk']} against a full-time week of "
                f"{T['week_hours_min']:,.1f}. The denominator is full and the "
                f"numerator is not, so utilisation reads high and revenue "
                f"reads low."),
           sev="medium", value=T["week_hours_min"] - r["week_hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_time_05(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? AND "
                           "project_id IS NOT NULL", (org,))
    rows = _rows(con, """
        SELECT t.employee_id, e.employee_name, p.project_id, p.project_code,
               COUNT(*) AS entries, SUM(t.hours) AS hours,
               MIN(t.entry_date) AS first_date, MAX(t.entry_date) AS last_date
        FROM time_entry t
             JOIN project p ON p.project_id=t.project_id
             LEFT JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=? AND NOT EXISTS (
            SELECT 1 FROM assignment a
            WHERE a.project_id=t.project_id AND a.employee_id=t.employee_id
              AND t.entry_date BETWEEN a.start_date AND a.end_date)
        GROUP BY t.employee_id, p.project_id
        HAVING hours > ? ORDER BY hours DESC""",
        (org, T["no_allocation_hours"]))
    findings = [
        _f(("project", r["project_id"],
            f"{r['employee_name']} on {r['project_code']}"),
           observed=f"{r['hours']:,.1f}h between {r['first_date']} and {r['last_date']}",
           expected="inside an assignment window",
           msg=(f"{r['employee_name']} booked {r['hours']:,.1f} hours to "
                f"{r['project_code']} outside any assignment covering those "
                f"dates. Either the assignment was never extended or the "
                f"hours are on the wrong engagement; in both cases demand and "
                f"delivery have stopped referring to the same commitment."),
           sev="high", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_time_06(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? AND "
                           "project_id IS NOT NULL", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.project_status,
               p.actual_end_date, SUM(t.hours) AS hours, COUNT(*) AS entries
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
        WHERE t.org_id=? AND p.project_status IN ('Complete','Cancelled')
          AND p.actual_end_date IS NOT NULL AND t.entry_date > p.actual_end_date
        GROUP BY p.project_id ORDER BY hours DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['hours']:,.1f}h after {r['actual_end_date']}",
           expected="no time after closure",
           msg=(f"{r['entries']:,} entries totalling {r['hours']:,.1f} hours "
                f"landed on {r['project_code']} after it closed on "
                f"{r['actual_end_date']}. That cost restates a margin already "
                f"reported."),
           sev="high", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_time_07(con, org, as_of):
    """Audit evidence of a change to an approved or invoiced entry."""
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? AND "
                           "(approval_status='Approved' OR invoiced=1)", (org,))
    rows = _rows(con, """
        SELECT a.audit_id, a.entity_pk, a.field_name, a.old_value, a.new_value,
               a.changed_by, a.changed_at
        FROM audit_log a
        WHERE a.entity_table='time_entry'""")
    findings = [
        _f(("time_entry", r["entity_pk"], f"entry {r['entity_pk']}"),
           observed=f"{r['field_name']} moved from {r['old_value']} to {r['new_value']}",
           expected="withdraw, correct, resubmit",
           msg=(f"{r['field_name']} on an approved entry was changed in place "
                f"by {r['changed_by']} on {r['changed_at']}. The audit trail "
                f"can no longer show what the approver approved."),
           sev="critical")
        for r in rows]
    return population, findings


def chk_time_08(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? AND "
                           "approval_status='Submitted'", (org,))
    rows = _rows(con, """
        SELECT e.employee_id, e.employee_name, e.role, COUNT(*) AS entries,
               SUM(t.hours) AS hours, MIN(t.entry_date) AS oldest
        FROM time_entry t JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=? AND t.approval_status='Submitted'
          AND e.manager_employee_id IS NULL
        GROUP BY e.employee_id ORDER BY hours DESC""", (org,))
    findings = [
        _f(("employee", r["employee_id"], r["employee_name"]),
           observed=f"{r['hours']:,.1f}h submitted, no manager on record",
           expected="a named approver",
           msg=(f"{r['employee_name']} has {r['hours']:,.1f} hours awaiting a "
                f"decision, the oldest from {r['oldest']}, and no manager to "
                f"make it. The hours stay out of the financials while everyone "
                f"assumes they are in."),
           sev="medium", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_time_09(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               SUM(CASE WHEN t.is_billable=0 AND t.time_category NOT IN
                        ('PTO','Holiday','Sick') THEN t.hours ELSE 0 END) AS nb,
               SUM(t.hours) AS total
        FROM time_entry t JOIN project p ON p.project_id=t.project_id
        WHERE t.org_id=? AND p.project_status='In Flight'
        GROUP BY p.project_id HAVING total>100 AND nb/total > 0.25
        ORDER BY nb/total DESC""", (org,))
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status='In Flight'", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['nb']/r['total']*100:,.1f}% non-billable",
           expected="a minor share",
           msg=(f"{r['nb']:,.0f} of {r['total']:,.0f} hours booked to "
                f"{r['project_code']} are non-billable delivery work, which is "
                f"{r['nb']/r['total']*100:,.1f}% of the engagement. That is "
                f"cost with no revenue against it."),
           sev="low", value=r["nb"], unit="hours")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Actuals
# ---------------------------------------------------------------------
def chk_act_01(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               COALESCE(te.hours,0) AS timesheet_hours,
               COALESCE(pfm.hours,0) AS ledger_hours
        FROM project p
        LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                   WHERE approval_status='Approved' GROUP BY project_id) te
               ON te.project_id=p.project_id
        LEFT JOIN (SELECT project_id, SUM(actual_amount) hours
                   FROM project_financial_month GROUP BY project_id) pfm
               ON pfm.project_id=p.project_id
        WHERE p.org_id=?""", (org,))
    # The ledger in this schema stores value, not hours, so reconcile cost.
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               COALESCE(te.cost,0) AS timesheet_cost,
               COALESCE(pfm.cost,0) AS ledger_cost
        FROM project p
        LEFT JOIN (SELECT project_id, SUM(hours*COALESCE(cost_rate,0)) cost
                   FROM time_entry WHERE approval_status='Approved'
                   GROUP BY project_id) te ON te.project_id=p.project_id
        LEFT JOIN (SELECT project_id, SUM(labor_cost) cost
                   FROM project_financial_month GROUP BY project_id) pfm
               ON pfm.project_id=p.project_id
        WHERE p.org_id=?""", (org,))
    findings = []
    for r in rows:
        drift = abs(r["timesheet_cost"] - r["ledger_cost"])
        if drift <= max(T["actuals_drift_money"], r["timesheet_cost"] * 0.01):
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"ledger {r['ledger_cost']:,.0f}, timesheets {r['timesheet_cost']:,.0f}",
            expected="the two agree",
            msg=(f"The monthly ledger for {r['project_code']} carries "
                 f"{r['ledger_cost']:,.0f} of labour cost against "
                 f"{r['timesheet_cost']:,.0f} in the approved timesheets, a "
                 f"drift of {drift:,.0f}. Every margin and consumption figure "
                 f"on this engagement is reading a number the timesheets do "
                 f"not contain."),
            sev="critical" if drift > r["timesheet_cost"] * 0.05 else "high",
            value=drift, unit="currency"))
    return len(rows), findings


def chk_act_02(con, org, as_of):
    """Entries whose cost rate does not match the employee's rate of record."""
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=?", (org,))
    rows = _rows(con, """
        SELECT e.employee_id, e.employee_name, e.cost_rate AS rate_of_record,
               COUNT(DISTINCT t.cost_rate) AS distinct_rates,
               MIN(t.cost_rate) AS lo, MAX(t.cost_rate) AS hi,
               SUM(t.hours) AS hours
        FROM time_entry t JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=? AND t.cost_rate IS NOT NULL
        GROUP BY e.employee_id HAVING distinct_rates>1 ORDER BY hours DESC""", (org,))
    findings = [
        _f(("employee", r["employee_id"], r["employee_name"]),
           observed=f"{r['distinct_rates']} distinct rates, {r['lo']:,.0f} to {r['hi']:,.0f}",
           expected="the rate snapshotted at booking",
           msg=(f"{r['employee_name']} has time booked at "
                f"{r['distinct_rates']} different cost rates between "
                f"{r['lo']:,.0f} and {r['hi']:,.0f}. That is correct if the "
                f"rate changed and was snapshotted; it restates history if the "
                f"rate was applied retrospectively. Worth confirming which."),
           sev="medium", value=(r["hi"] - r["lo"]) * r["hours"], unit="currency")
        for r in rows]
    return population, findings


def chk_act_03(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               COALESCE(rr.amt,0) AS recognition,
               COALESCE(pfm.amt,0) AS ledger
        FROM project p
        LEFT JOIN (SELECT project_id, SUM(recognized_amount) amt
                   FROM revenue_recognition GROUP BY project_id) rr
               ON rr.project_id=p.project_id
        LEFT JOIN (SELECT project_id, SUM(recognized_revenue) amt
                   FROM project_financial_month GROUP BY project_id) pfm
               ON pfm.project_id=p.project_id
        WHERE p.org_id=? AND (COALESCE(rr.amt,0)>0 OR COALESCE(pfm.amt,0)>0)""",
        (org,))
    findings = []
    for r in rows:
        drift = abs(r["recognition"] - r["ledger"])
        base = max(r["recognition"], r["ledger"], 1)
        if drift <= max(T["actuals_drift_money"], base * 0.01):
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"recognition {r['recognition']:,.0f}, ledger {r['ledger']:,.0f}",
            expected="one revenue number",
            msg=(f"{r['project_code']} has two revenue figures that differ by "
                 f"{drift:,.0f}. Whichever screen somebody opens is the one "
                 f"they will quote."),
            sev="critical", value=drift, unit="currency"))
    return len(rows), findings


def chk_act_04(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               SUM(tk.planned_hours) AS planned,
               SUM(tk.planned_hours * COALESCE(tk.percent_complete,0)/100.0) AS earned,
               COALESCE(te.hours,0) AS actual_hours, p.budget_hours
        FROM project p JOIN project_task tk USING(project_id)
        LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                   WHERE approval_status='Approved' GROUP BY project_id) te
               ON te.project_id=p.project_id
        WHERE p.org_id=? AND p.project_status='In Flight'
        GROUP BY p.project_id HAVING planned>0""", (org,))
    findings = []
    for r in rows:
        derived = r["earned"] / r["planned"] * 100
        consumed = (r["actual_hours"] / r["budget_hours"] * 100
                    if r["budget_hours"] else 0)
        gap = consumed - derived
        if gap <= T["plan_variance_pts"]:
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"{consumed:,.1f}% of hours consumed at {derived:,.1f}% complete",
            expected=f"within {T['plan_variance_pts']:.0f} points",
            msg=(f"{r['project_code']} has consumed {consumed:,.1f}% of its "
                 f"budget hours to earn {derived:,.1f}% of its plan, a gap of "
                 f"{gap:,.1f} points. On fixed price work that gap is the "
                 f"margin, and it is only visible if percent complete is "
                 f"derived from the plan rather than typed in."),
            sev="high" if gap > 25 else "medium", value=gap, unit="points"))
    return len(rows), findings


def chk_act_05(con, org, as_of):
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               MAX(t.entry_date) AS last_entry,
               (SELECT MAX(period_month) FROM project_financial_month
                WHERE project_id=p.project_id) AS last_ledger
        FROM project p JOIN time_entry t ON t.project_id=p.project_id
        WHERE p.org_id=? AND t.approval_status='Approved'
        GROUP BY p.project_id""", (org,))
    findings = []
    for r in rows:
        if not r["last_entry"]:
            continue
        if r["last_ledger"] and r["last_ledger"] >= r["last_entry"][:7]:
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"time to {r['last_entry']}, ledger to {r['last_ledger'] or 'never built'}",
            expected="the ledger at least as current as the time",
            msg=(f"Approved time on {r['project_code']} runs to "
                 f"{r['last_entry']} but the ledger stops at "
                 f"{r['last_ledger'] or 'nothing'}. The figures on screen have "
                 f"a date on them and the date is out of date."),
            sev="medium"))
    return len(rows), findings


# ---------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------
def chk_res_01(con, org, as_of):
    """
    Overlapping commitments above capacity, found by sweeping the boundary
    dates rather than comparing pairs, so the whole clash is reported once
    with everything contributing to it.
    """
    allocs = _rows(con, """
        SELECT a.assignment_id, a.employee_id, e.employee_name, a.project_id,
               p.project_code, a.start_date, a.end_date, a.allocation_pct
        FROM assignment a
             JOIN employee e ON e.employee_id=a.employee_id
             JOIN project p ON p.project_id=a.project_id
        WHERE e.org_id=? AND e.is_active=1
          AND p.project_status IN ('Not Started','In Flight','On Hold')
          AND a.end_date>=?""", (org, as_of))
    by_emp = {}
    for a in allocs:
        by_emp.setdefault((a["employee_id"], a["employee_name"]), []).append(a)

    findings = []
    for (emp_id, name), rows in by_emp.items():
        edges = sorted({r["start_date"] for r in rows}
                       | {r["end_date"] for r in rows})
        worst = None
        for i, edge in enumerate(edges[:-1]):
            window_start, window_end = edge, edges[i + 1]
            active = [r for r in rows
                      if r["start_date"] <= window_end and r["end_date"] >= window_start]
            if len(active) < 2:
                continue
            total = sum(r["allocation_pct"] or 0 for r in active)
            if total <= T["overallocation_pct"]:
                continue
            if worst is None or total > worst[0]:
                worst = (total, window_start, window_end, active)
        if worst is None:
            continue
        total, ws, we, active = worst
        codes = ", ".join(f"{r['project_code']} at {r['allocation_pct']:,.0f}%"
                          for r in active)
        findings.append(_f(
            ("employee", emp_id, name),
            observed=f"{total:,.0f}% committed from {ws} to {we}",
            expected=f"no more than {T['overallocation_pct']:.0f}%",
            msg=(f"{name} is committed to {total:,.0f}% of capacity between "
                 f"{ws} and {we} across {len(active)} engagements: {codes}. "
                 f"Neither Certinia nor Rocketlane would have stopped this, "
                 f"and until it is resolved the capacity plan is built on a "
                 f"supply number that does not exist."),
            sev="critical" if total > T["overallocation_pct"] + 50 else "high",
            value=total - T["overallocation_pct"], unit="percent"))
    findings.sort(key=lambda f: -(f["value_at_stake"] or 0))
    return len(by_emp), findings


def chk_res_02(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM assignment a JOIN employee e USING(employee_id)
        WHERE e.org_id=? AND e.end_date IS NOT NULL""", (org,))
    rows = _rows(con, """
        SELECT a.assignment_id, e.employee_id, e.employee_name, e.end_date,
               p.project_code, a.end_date AS alloc_end, a.allocation_pct,
               a.planned_hours
        FROM assignment a JOIN employee e ON e.employee_id=a.employee_id
             JOIN project p ON p.project_id=a.project_id
        WHERE e.org_id=? AND e.end_date IS NOT NULL AND a.end_date > e.end_date
        ORDER BY a.planned_hours DESC""", (org,))
    findings = [
        _f(("assignment", r["assignment_id"],
            f"{r['employee_name']} on {r['project_code']}"),
           observed=f"assignment to {r['alloc_end']}, left on {r['end_date']}",
           expected="an assignment ending on or before the last day",
           msg=(f"{r['employee_name']} left on {r['end_date']} and is still "
                f"booked to {r['project_code']} until {r['alloc_end']} at "
                f"{r['allocation_pct']:,.0f}%. That is "
                f"{r['planned_hours']:,.0f} planned hours of capacity from "
                f"somebody who is not there, and nothing about it looks "
                f"unusual on the board."),
           sev="high", value=r["planned_hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_res_03(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM assignment a JOIN project p USING(project_id)
        WHERE p.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.project_status,
               COUNT(*) AS assignments, SUM(a.planned_hours) AS hours
        FROM assignment a JOIN project p ON p.project_id=a.project_id
        WHERE p.org_id=? AND p.project_status IN ('Complete','Cancelled')
          AND a.end_date >= ?
        GROUP BY p.project_id ORDER BY hours DESC""", (org, as_of))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['assignments']} assignments still running",
           expected="released at closure",
           msg=(f"{r['project_code']} is {r['project_status'].lower()} and "
                f"still holds {r['assignments']} live assignments worth "
                f"{r['hours']:,.0f} hours. That shows as demand which will "
                f"never be delivered, making the capacity gap look worse than "
                f"it is and hiding the real one."),
           sev="medium", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_res_04(con, org, as_of):
    return chk_time_05(con, org, as_of)


def chk_res_05(con, org, as_of):
    """Team with none of the skills the product line implies."""
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.product,
               COUNT(DISTINCT a.employee_id) AS team
        FROM project p JOIN assignment a ON a.project_id=p.project_id
        WHERE p.org_id=? AND p.project_status='In Flight' AND p.product IS NOT NULL
        GROUP BY p.project_id""", (org,))
    # Map a product line to the skill family it needs, from the skill table.
    families = {r["skill_family"]: r["skill_family"] for r in
                _rows(con, "SELECT DISTINCT skill_family FROM skill")}
    findings = []
    for r in rows:
        held = _one(con, """
            SELECT COUNT(DISTINCT s.skill_family)
            FROM assignment a JOIN employee_skill es USING(employee_id)
                 JOIN skill s USING(skill_id)
            WHERE a.project_id=? AND es.proficiency>=3""", (r["project_id"],))
        if held >= 2:
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"{held} skill families at proficiency 3 or above across "
                     f"{r['team']} people",
            expected="at least two",
            msg=(f"The team on {r['project_code']} ({r['product']}) covers "
                 f"{held} skill families at working proficiency. A team "
                 f"assembled from whoever was free is the most expensive kind "
                 f"of availability, and it shows up later as rework rather "
                 f"than as a resourcing decision."),
            sev="medium"))
    return len(rows), findings


def chk_res_06(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM project WHERE org_id=?
          AND project_status IN ('In Flight','On Hold')""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.start_date,
               p.contract_value
        FROM project p
        WHERE p.org_id=? AND p.project_status IN ('In Flight','On Hold')
          AND NOT EXISTS (SELECT 1 FROM assignment a
                          WHERE a.project_id=p.project_id)
        ORDER BY p.contract_value DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed="no assignments at all",
           expected="a confirmed team",
           msg=(f"{r['project_code']} started on {r['start_date']} carrying "
                f"{r['contract_value']:,.0f} of contract value and has nobody "
                f"booked to it. It generates no demand signal and no delivery."),
           sev="high", value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------
def chk_gov_01(con, org, as_of):
    # The population is overrides, not projects. Every project carries a health
    # colour and the name of whoever set it; only some of those departed from
    # what the numbers implied, and only those need defending.
    population = _one(con, """
        SELECT COUNT(*) FROM audit_log a JOIN project p
               ON p.project_id=a.entity_pk
        WHERE a.entity_table='project' AND a.field_name='project_health'
          AND a.is_override=1 AND p.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT a.audit_id, p.project_id, p.project_code, p.project_name,
               a.old_value, a.new_value, a.changed_by, a.changed_at
        FROM audit_log a JOIN project p ON p.project_id=a.entity_pk
        WHERE a.entity_table='project' AND a.field_name='project_health'
          AND a.is_override=1 AND p.org_id=?
          AND (a.change_reason IS NULL OR TRIM(a.change_reason)='')
        ORDER BY a.changed_at DESC""", (org,))
    findings = [
        _f(("audit_log", r["audit_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"{r['old_value']} to {r['new_value']} by "
                    f"{r['changed_by']}, no note",
           expected="a mandatory comment",
           msg=(f"{r['project_code']} was moved from {r['old_value']} to "
                f"{r['new_value']} by hand with nothing recorded about why. "
                f"An override with no note is indistinguishable from a "
                f"mistake, and it is the note rather than the colour the "
                f"portfolio review actually needs."),
           sev="high")
        for r in rows]
    return population, findings


def chk_gov_02(con, org, as_of):
    order = {"Green": 0, "Yellow": 1, "Red": 2}
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.project_health,
               rs.predicted_health, rs.failure_probability_pct,
               p.health_set_by
        FROM project p JOIN project_risk_score rs USING(project_id)
        WHERE p.org_id=? AND p.project_status IN ('In Flight','On Hold')""",
        (org,))
    findings = []
    for r in rows:
        a = order.get(r["project_health"], 0)
        b = order.get(r["predicted_health"], 0)
        if abs(a - b) < T["health_divergence_bands"]:
            continue
        findings.append(_f(
            ("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
            observed=f"reported {r['project_health']}, model says {r['predicted_health']}",
            expected="within one band",
            msg=(f"{r['project_code']} is reported {r['project_health']} and "
                 f"the model scores it {r['predicted_health']} at a "
                 f"{r['failure_probability_pct']:,.1f}% failure probability. "
                 f"Either the project manager knows something the data does "
                 f"not, or nobody is telling the truth about it. Neither "
                 f"Certinia nor Rocketlane can detect this, because neither "
                 f"computes a score to diverge from."),
            sev="critical", value=abs(a - b), unit="bands"))
    return len(rows), findings


def chk_gov_03(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(DISTINCT project_id) FROM project_task tk
        JOIN project p USING(project_id) WHERE p.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name
        FROM project p
        WHERE p.org_id=? AND EXISTS (SELECT 1 FROM project_task tk
                                     WHERE tk.project_id=p.project_id)
          AND NOT EXISTS (SELECT 1 FROM project_plan_version v
                          WHERE v.project_id=p.project_id AND v.is_baseline=1)""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed="a plan with no baseline",
           expected="a baseline recorded at kickoff",
           msg=(f"{r['project_code']} has a plan and no baseline, so it has "
                f"nothing to be late against and schedule variance cannot be "
                f"computed. Slip becomes a matter of opinion."),
           sev="high")
        for r in rows]
    return population, findings


def chk_gov_04(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status IN ('In Flight','On Hold')", (org,))
    rows = _rows(con, """
        SELECT project_id, project_code, project_name, contract_value
        FROM project WHERE org_id=?
          AND project_status IN ('In Flight','On Hold')
          AND project_manager_id IS NULL
        ORDER BY contract_value DESC""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed="no project manager", expected="a named manager",
           msg=(f"{r['project_code']} carries {r['contract_value']:,.0f} of "
                f"contract value with nobody accountable for it. Nobody "
                f"accountable means nobody forecasting."),
           sev="high", value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


def chk_gov_05(con, org, as_of):
    cutoff = (dt.date.fromisoformat(as_of)
              - dt.timedelta(days=T["stale_forecast_days"])).isoformat()
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status='In Flight'", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name,
               (SELECT MAX(snapshot_date) FROM project_forecast_snapshot s
                WHERE s.project_id=p.project_id) AS last_forecast,
               p.contract_value
        FROM project p
        WHERE p.org_id=? AND p.project_status='In Flight'
        AND (last_forecast IS NULL OR last_forecast < ?)
        ORDER BY p.contract_value DESC""", (org, cutoff))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"last forecast {r['last_forecast'] or 'never'}",
           expected=f"within {T['stale_forecast_days']} days",
           msg=(f"The forecast on {r['project_code']} was last submitted "
                f"{r['last_forecast'] or 'never'}. It is being quoted as "
                f"though it were current; the month it was submitted in "
                f"matters as much as the number."),
           sev="medium", value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


def chk_gov_06(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM project_task tk JOIN project p USING(project_id)
        WHERE p.org_id=? AND tk.is_milestone=1
          AND p.project_status IN ('In Flight','On Hold')""", (org,))
    rows = _rows(con, """
        SELECT tk.task_id, tk.task_name, tk.planned_end, tk.status,
               p.project_id, p.project_code
        FROM project_task tk JOIN project p USING(project_id)
        WHERE p.org_id=? AND tk.is_milestone=1
          AND p.project_status IN ('In Flight','On Hold')
          AND tk.planned_end < ? AND tk.status NOT IN ('Complete','Cancelled')
        ORDER BY tk.planned_end""", (org, as_of))
    findings = [
        _f(("project_task", r["task_id"],
            f"{r['project_code']} {r['task_name']}"),
           observed=f"due {r['planned_end']}, still {r['status']}",
           expected="complete, or a revised date",
           msg=(f"{r['task_name']} on {r['project_code']} was due "
                f"{r['planned_end']} and is still {r['status'].lower()} with "
                f"no revised date. A milestone that has silently passed is a "
                f"schedule that has stopped meaning anything, and it is the "
                f"point at which a customer stops believing the next date."),
           sev="high",
           value=(dt.date.fromisoformat(as_of)
                  - dt.date.fromisoformat(r["planned_end"])).days, unit="days")
        for r in rows]
    return population, findings


def chk_gov_07(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status='In Flight' AND planned_end_date "
                           "IS NOT NULL", (org,))
    rows = _rows(con, """
        SELECT project_id, project_code, project_name, planned_end_date,
               contract_value
        FROM project WHERE org_id=? AND project_status='In Flight'
          AND planned_end_date IS NOT NULL AND planned_end_date < ?
        ORDER BY planned_end_date""", (org, as_of))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"planned end {r['planned_end_date']}, still in flight",
           expected="a revised end date, or closure",
           msg=(f"{r['project_code']} passed its planned end date on "
                f"{r['planned_end_date']} and is still live with no revision. "
                f"This is the simplest signal of an engagement nobody is "
                f"managing, and the one most often missed because the project "
                f"still looks green."),
           sev="high",
           value=(dt.date.fromisoformat(as_of)
                  - dt.date.fromisoformat(r["planned_end_date"])).days,
           unit="days")
        for r in rows]
    return population, findings


def chk_gov_08(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM risk_issue r JOIN project p USING(project_id)
        WHERE p.org_id=? AND r.is_customer_raised=1
          AND r.status NOT IN ('Closed','Resolved')""", (org,))
    rows = _rows(con, """
        SELECT r.risk_issue_id, r.ref_code, r.description, r.status,
               r.date_identified, p.project_id, p.project_code
        FROM risk_issue r JOIN project p USING(project_id)
        WHERE p.org_id=? AND r.is_customer_raised=1
          AND r.status NOT IN ('Closed','Resolved')
          AND (r.owner IS NULL OR r.owner='' OR r.due_date IS NULL)
        ORDER BY r.date_identified""", (org,))
    findings = [
        _f(("risk_issue", r["risk_issue_id"],
            f"{r['project_code']} {r['ref_code']}"),
           observed="no owner or no due date",
           expected="both",
           msg=(f"{r['ref_code']} on {r['project_code']}, raised by the "
                f"customer on {r['date_identified']}, has no owner or no due "
                f"date. An escalation with nobody on it is the fastest route "
                f"from a delivery problem to a commercial one."),
           sev="high")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------
def chk_close_01(con, org, as_of):
    """Approved time older than the close boundary, still being changed."""
    boundary = (dt.date.fromisoformat(as_of).replace(day=1)
                - dt.timedelta(days=1)).isoformat()[:7]
    population = _one(con, "SELECT COUNT(*) FROM time_entry WHERE org_id=? AND "
                           "approval_status='Approved'", (org,))
    rows = _rows(con, """
        SELECT substr(t.entry_date,1,7) AS period,
               COUNT(*) AS entries, SUM(t.hours) AS hours,
               SUM(t.hours*COALESCE(t.cost_rate,0)) AS cost
        FROM time_entry t
        WHERE t.org_id=? AND t.approval_status IN ('Draft','Submitted')
          AND substr(t.entry_date,1,7) < ?
        GROUP BY period ORDER BY period""", (org, boundary))
    findings = [
        _f(("time_entry", None, f"period {r['period']}"),
           observed=f"{r['entries']:,} entries still unapproved",
           expected="the period closed with everything decided",
           msg=(f"{r['period']} has {r['entries']:,} entries worth "
                f"{r['hours']:,.1f} hours and {r['cost']:,.0f} of cost still "
                f"undecided, in a period that should be closed. When they land "
                f"they will restate a reported result. Certinia's PSA-side "
                f"lock is a single boolean scoped only to revenue forecasting, "
                f"so nothing stops this."),
           sev="critical", value=r["cost"], unit="currency")
        for r in rows]
    return population, findings


def chk_close_02(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM revenue_recognition rr JOIN project p USING(project_id)
        WHERE p.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, rr.period_month, COUNT(*) AS rows_
        FROM revenue_recognition rr JOIN project p USING(project_id)
        WHERE p.org_id=?
        GROUP BY p.project_id, rr.period_month HAVING rows_>1""",
        (org,))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['period_month']}"),
           observed=f"{r['rows_']} recognition rows",
           expected="one per period",
           msg=(f"{r['project_code']} has {r['rows_']} recognition postings "
                f"for {r['period_month']}. Two postings for one period "
                f"double-count revenue, and the duplicate is invisible in a "
                f"total."),
           sev="critical")
        for r in rows]
    return population, findings


def chk_close_03(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM revenue_recognition rr JOIN project p USING(project_id)
        WHERE p.org_id=? AND rr.posted=1""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, rr.period_month, rr.posted_on,
               rr.recognized_amount
        FROM revenue_recognition rr JOIN project p USING(project_id)
        WHERE p.org_id=? AND rr.posted=1 AND rr.posted_on IS NULL""",
        (org,))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['period_month']}"),
           observed="marked posted with no posting date",
           expected="a posting date on every posted row",
           msg=(f"{r['project_code']} shows {r['period_month']} as posted with "
                f"no date against it, so there is no way to tell whether it "
                f"has been restated since."),
           sev="high", value=r["recognized_amount"], unit="currency")
        for r in rows]
    return population, findings


def chk_close_04(con, org, as_of):
    # audit_log has no org column, so the org is resolved through the entity
    # the change was made against. Rates hang off an employee, values off a
    # project; anything else is out of scope for a financial-change check.
    scope = """
        WITH fin AS (
          SELECT a.*, e.org_id AS o FROM audit_log a
                 JOIN employee e ON e.employee_id=a.entity_pk
           WHERE a.entity_table='employee'
             AND a.field_name IN ('billing_rate','cost_rate')
          UNION ALL
          SELECT a.*, p.org_id AS o FROM audit_log a
                 JOIN project p ON p.project_id=a.entity_pk
           WHERE a.entity_table='project'
             AND a.field_name IN ('contract_value','budget_hours',
                                  'revenue_recognized','co_value',
                                  'target_margin_pct')
        )"""
    population = _one(con, scope + " SELECT COUNT(*) FROM fin WHERE o=?",
                      (org,))
    rows = _rows(con, scope + """
        SELECT audit_id, entity_table, entity_pk, field_name, old_value,
               new_value, changed_by, changed_at
        FROM fin
        WHERE o=? AND (change_reason IS NULL OR TRIM(change_reason)='')
        ORDER BY changed_at DESC""", (org,))
    findings = [
        _f((r["entity_table"], r["entity_pk"],
            f"{r['entity_table']} {r['entity_pk']} {r['field_name']}"),
           observed=f"{r['old_value']} to {r['new_value']}, no reason recorded",
           expected="a reason on every financial change",
           msg=(f"{r['field_name']} was changed from {r['old_value']} to "
                f"{r['new_value']} by {r['changed_by']} on {r['changed_at']} "
                f"with nothing recorded about why. A trail that records what "
                f"changed but not why answers the wrong question."),
           sev="medium")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------
def chk_mig_01(con, org, as_of):
    runs = _one(con, "SELECT COUNT(*) FROM migration_run WHERE org_id=?", (org,))
    if not runs:
        return 0, []
    population = _one(con, """
        SELECT COUNT(*) FROM project WHERE org_id=? AND legacy_project_id
        IS NOT NULL""", (org,))
    missing = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.legacy_project_id
        FROM project p
        WHERE p.org_id=? AND p.legacy_project_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM migration_lineage l
                          WHERE l.entity_table='project'
                            AND l.entity_pk=p.project_id)""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"legacy id {r['legacy_project_id']} with no lineage row",
           expected="traceable to the source",
           msg=(f"{r['project_code']} came from the legacy system as "
                f"{r['legacy_project_id']} and has no lineage record. The "
                f"first reconciliation question after go live has no answer, "
                f"and that answer is what buys the trust that makes people use "
                f"the new system."),
           sev="critical")
        for r in missing]
    return population, findings


def chk_mig_02(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM validation_finding f JOIN import_batch b USING(batch_id)
        WHERE b.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT b.batch_id, b.file_name, f.rule_code, f.severity,
               COUNT(*) AS findings
        FROM validation_finding f JOIN import_batch b USING(batch_id)
        WHERE b.org_id=? AND f.severity='error' AND COALESCE(f.resolved,0)=0
        GROUP BY b.batch_id, f.rule_code ORDER BY findings DESC""", (org,))
    findings = [
        _f(("import_batch", r["batch_id"], r["file_name"]),
           observed=f"{r['findings']} unresolved {r['rule_code']} errors",
           expected="no unresolved errors before cutover",
           msg=(f"{r['file_name']} still carries {r['findings']} unresolved "
                f"{r['rule_code']} errors. An error-severity finding is a row "
                f"that did not load; going live over the top of it means the "
                f"gap is discovered by a customer."),
           sev="critical", value=r["findings"], unit="rows")
        for r in rows]
    return population, findings


def chk_mig_03(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM mapping_rule r JOIN mapping_set s USING(mapping_set_id)
        WHERE s.org_id=?""", (org,))
    rows = _rows(con, """
        SELECT r.mapping_rule_id, s.set_name, s.dimension, r.legacy_value,
               r.target_value, r.confidence_pct, r.occurrence_count
        FROM mapping_rule r JOIN mapping_set s USING(mapping_set_id)
        WHERE s.org_id=? AND COALESCE(r.approved,0)=0 AND r.confidence_pct<90
        ORDER BY r.occurrence_count DESC""", (org,))
    findings = [
        _f(("mapping_rule", r["mapping_rule_id"],
            f"{r['set_name']}: {r['legacy_value']}"),
           observed=f"mapped to {r['target_value']} at "
                    f"{r['confidence_pct']:,.0f}% confidence, unapproved",
           expected="a person's approval below the confidence threshold",
           msg=(f"'{r['legacy_value']}' is auto-mapped to "
                f"'{r['target_value']}' at {r['confidence_pct']:,.0f}% "
                f"confidence, affecting {r['occurrence_count']:,} records, and "
                f"nobody has approved it. A category mapped wrong changes "
                f"every report built on it, silently."),
           sev="high", value=r["occurrence_count"], unit="rows")
        for r in rows]
    return population, findings


def chk_mig_04(con, org, as_of):
    rows = _rows(con, """
        SELECT migration_run_id, stage, data_quality_score,
               financial_completeness_pct, resource_completeness_pct,
               project_completeness_pct
        FROM migration_run WHERE org_id=?""", (org,))
    findings = []
    for r in rows:
        for label, value, floor in (
            ("financial history", r["financial_completeness_pct"], 95),
            ("resource history", r["resource_completeness_pct"], 90),
            ("project history", r["project_completeness_pct"], 95),
        ):
            if value is None or value >= floor:
                continue
            findings.append(_f(
                ("migration_run", r["migration_run_id"],
                 f"migration run at stage {r['stage']}"),
                observed=f"{label} {value:,.1f}% complete",
                expected=f"at least {floor}%",
                msg=(f"Migrated {label} reaches {value:,.1f}% against a "
                     f"cutover threshold of {floor}%. Migrating an engagement "
                     f"without its history leaves a margin that starts at go "
                     f"live, which nobody can compare to anything."),
                sev="critical", value=floor - value, unit="points"))
    # Three completeness dimensions are examined per migration run, so the
    # population is runs times dimensions, not runs.
    return len(rows) * 3, findings


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
def chk_cfg_01(con, org, as_of):
    rows = _rows(con, """
        SELECT role, COUNT(*) AS people,
               SUM(CASE WHEN utilization_target_pct IS NULL THEN 1 ELSE 0 END) AS missing
        FROM employee WHERE org_id=? AND is_active=1 AND is_billable=1
        GROUP BY role HAVING missing>0""", (org,))
    population = _one(con, "SELECT COUNT(DISTINCT role) FROM employee WHERE "
                           "org_id=? AND is_active=1 AND is_billable=1", (org,))
    findings = [
        _f(("employee", None, r["role"]),
           observed=f"{r['missing']} of {r['people']} without a target",
           expected="a target on every billable role",
           msg=(f"{r['missing']} of {r['people']} {r['role']}s have no "
                f"utilisation target, so they are measured against a default "
                f"that does not apply to them. That is how a practice lead at "
                f"forty percent gets reported as a problem."),
           sev="high", value=r["missing"], unit="people")
        for r in rows]
    return population, findings


def chk_cfg_02(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM practice WHERE org_id=?", (org,))
    rows = _rows(con, """
        SELECT pr.practice_id, pr.practice_code, pr.practice_name,
               COUNT(p.project_id) AS live
        FROM practice pr LEFT JOIN project p
             ON p.practice_id=pr.practice_id
            AND p.project_status IN ('In Flight','On Hold')
        WHERE pr.org_id=? AND (pr.target_margin_pct IS NULL
                               OR pr.target_margin_pct<=0)
        GROUP BY pr.practice_id""", (org,))
    findings = [
        _f(("practice", r["practice_id"], r["practice_name"]),
           observed="no target margin",
           expected="a target on every delivering practice",
           msg=(f"{r['practice_name']} has {r['live']} live engagements and no "
                f"target margin, so its margin has no verdict. A portfolio of "
                f"numbers with no verdict does not get acted on."),
           sev="high")
        for r in rows]
    return population, findings


def chk_cfg_03(con, org, as_of):
    rows = _rows(con, """
        SELECT a.role_on_project, COUNT(*) AS assignments,
               SUM(CASE WHEN a.bill_rate IS NULL OR a.bill_rate<=0
                        THEN 1 ELSE 0 END) AS missing,
               SUM(a.planned_hours) AS hours
        FROM assignment a JOIN project p ON p.project_id=a.project_id
        WHERE p.org_id=? AND p.billing_model IN
              ('Time and Materials','Capped T&M')
        GROUP BY a.role_on_project HAVING missing>0 ORDER BY missing DESC""",
        (org,))
    population = _one(con, "SELECT COUNT(DISTINCT role_on_project) FROM "
                           "assignment a JOIN project p USING(project_id) "
                           "WHERE p.org_id=?", (org,))
    findings = [
        _f(("assignment", None, r["role_on_project"] or "unspecified role"),
           observed=f"{r['missing']} of {r['assignments']} with no bill rate",
           expected="a rate on every billable assignment",
           msg=(f"{r['missing']} assignments for "
                f"{r['role_on_project'] or 'an unspecified role'} on time and "
                f"materials work carry no bill rate. This is the upstream "
                f"cause of the billable-time-with-no-rate leak, and it is far "
                f"cheaper to fix here."),
           sev="critical", value=r["missing"], unit="assignments")
        for r in rows]
    return population, findings


def chk_cfg_04(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(DISTINCT e.employee_id) FROM employee e
        WHERE e.org_id=? AND e.is_active=1
          AND EXISTS (SELECT 1 FROM time_entry t
                      WHERE t.employee_id=e.employee_id)""", (org,))
    rows = _rows(con, """
        SELECT e.employee_id, e.employee_name, e.role,
               (SELECT SUM(hours) FROM time_entry t
                WHERE t.employee_id=e.employee_id) AS hours
        FROM employee e
        WHERE e.org_id=? AND e.is_active=1 AND e.manager_employee_id IS NULL
          AND e.role NOT LIKE '%Director%'
          AND EXISTS (SELECT 1 FROM time_entry t
                      WHERE t.employee_id=e.employee_id)
        ORDER BY hours DESC""", (org,))
    findings = [
        _f(("employee", r["employee_id"], r["employee_name"]),
           observed="no manager on record",
           expected="a named approver for everybody who books time",
           msg=(f"{r['employee_name']} ({r['role']}) has booked "
                f"{r['hours']:,.0f} hours and has no manager, so their "
                f"timesheets have nobody to approve them. The consequence "
                f"lands on the financials rather than on the person with the "
                f"gap."),
           sev="high", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_cfg_05(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(DISTINCT employee_id) FROM time_entry WHERE org_id=?""",
        (org,))
    rows = _rows(con, """
        SELECT e.employee_id, e.employee_name, e.role,
               SUM(t.hours) AS hours
        FROM time_entry t JOIN employee e ON e.employee_id=t.employee_id
        WHERE t.org_id=? AND (e.cost_rate IS NULL OR e.cost_rate<=0)
        GROUP BY e.employee_id ORDER BY hours DESC""", (org,))
    findings = [
        _f(("employee", r["employee_id"], r["employee_name"]),
           observed=f"{r['hours']:,.0f}h booked with no cost rate",
           expected="a cost rate on everybody who books time",
           msg=(f"{r['employee_name']} has booked {r['hours']:,.0f} hours and "
                f"has no cost rate, so that work carries no cost. Margin looks "
                f"better than it is on exactly the engagements this person "
                f"worked on."),
           sev="critical", value=r["hours"], unit="hours")
        for r in rows]
    return population, findings


def chk_cfg_06(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status IN ('In Flight','On Hold')", (org,))
    rows = _rows(con, """
        SELECT project_id, project_code, project_name, billing_model,
               contract_value
        FROM project WHERE org_id=? AND project_status IN ('In Flight','On Hold')
          AND (billing_model IS NULL OR billing_model=''
               OR COALESCE(contract_value,0)<=0)""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed=f"billing model {r['billing_model'] or 'unset'}, "
                    f"contract value {r['contract_value'] or 0:,.0f}",
           expected="both set from the signed contract",
           msg=(f"{r['project_code']} is live without both a billing model and "
                f"a contract value, so revenue cannot be recognised on it. "
                f"Missing, the model defaults, and the default is wrong for "
                f"most of a portfolio."),
           sev="critical")
        for r in rows]
    return population, findings


def chk_cfg_07(con, org, as_of):
    population = _one(con, "SELECT COUNT(*) FROM project WHERE org_id=? AND "
                           "project_status IN ('In Flight','On Hold')", (org,))
    rows = _rows(con, """
        SELECT project_id, project_code, project_name, contract_value
        FROM project WHERE org_id=? AND project_status IN ('In Flight','On Hold')
          AND (product IS NULL OR product='')""", (org,))
    findings = [
        _f(("project", r["project_id"], f"{r['project_code']} {r['project_name']}"),
           observed="no product line recorded",
           expected="a product line on every engagement",
           msg=(f"{r['project_code']} has no product line, so it cannot appear "
                f"in the resource requirement by product line, the practice "
                f"margin or any benchmark cohort. It is the cheapest field to "
                f"populate and the most expensive to be missing."),
           sev="high", value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


# ---------------------------------------------------------------------
# Earned value
#
# All six read project_evm, which is built before this module runs. Doing the
# arithmetic twice would be two places for the definition of percent complete
# to drift apart, and that definition is the whole basis of the family.
# ---------------------------------------------------------------------
def _evm_population(con, org, extra=""):
    return _one(con, f"""
        SELECT COUNT(*) FROM project_evm
        WHERE org_id=? AND is_reportable=1 {extra}""", (org,))


def chk_evm_01(con, org, as_of):
    population = _evm_population(con, org, "AND cpi IS NOT NULL")
    rows = _rows(con, """
        SELECT e.project_id, p.project_code, p.project_name, e.cpi, e.spi,
               e.ev, e.ac, e.bac, e.eac_cpi, e.vac, e.earned_pct
          FROM project_evm e JOIN project p USING(project_id)
         WHERE e.org_id=? AND e.is_reportable=1 AND e.cpi IS NOT NULL
           AND e.cpi < ?
         ORDER BY e.vac ASC""", (org, T["cpi_floor"]))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=f"CPI {r['cpi']:,.2f}",
           expected=f"CPI at or above {T['cpi_floor']:,.2f}",
           msg=(f"{r['project_code']} has earned {r['ev']:,.0f} of value for "
                f"{r['ac']:,.0f} spent, a cost index of {r['cpi']:,.2f} at "
                f"{r['earned_pct']:,.0f}% complete. At that efficiency it "
                f"lands at {r['eac_cpi']:,.0f} against a budget of "
                f"{r['bac']:,.0f}."),
           sev="critical" if r["cpi"] < 0.7 else "high",
           value=abs(r["vac"] or 0), unit="currency")
        for r in rows]
    return population, findings


def chk_evm_02(con, org, as_of):
    population = _evm_population(con, org, "AND spi IS NOT NULL")
    rows = _rows(con, """
        SELECT e.project_id, p.project_code, p.project_name, e.spi, e.pv, e.ev,
               e.planned_pct, e.earned_pct, p.contract_value
          FROM project_evm e JOIN project p USING(project_id)
         WHERE e.org_id=? AND e.is_reportable=1 AND e.spi IS NOT NULL
           AND e.spi < ?
         ORDER BY e.spi ASC""", (org, T["spi_floor"]))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=f"SPI {r['spi']:,.2f}",
           expected=f"SPI at or above {T['spi_floor']:,.2f}",
           msg=(f"{r['project_code']} is {r['earned_pct']:,.0f}% complete "
                f"against its own plan's {r['planned_pct']:,.0f}%, a schedule "
                f"index of {r['spi']:,.2f}. Measured against the plan's dates, "
                f"not elapsed calendar time, so this is slip rather than a "
                f"front-loaded plan."),
           sev="critical" if r["spi"] < 0.7 else "high",
           value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


def chk_evm_03(con, org, as_of):
    population = _evm_population(con, org)
    rows = _rows(con, """
        SELECT e.project_id, p.project_code, p.project_name, e.cpi, e.spi,
               e.cv, e.sv, e.vac, e.quadrant, p.project_health
          FROM project_evm e JOIN project p USING(project_id)
         WHERE e.org_id=? AND e.is_reportable=1
           AND e.cost_band='over' AND e.schedule_band='behind'
         ORDER BY e.csi ASC""", (org,))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=f"CPI {r['cpi']:,.2f} and SPI {r['spi']:,.2f}",
           expected="not both indices below tolerance",
           msg=(f"{r['project_code']} is over cost and behind schedule at the "
                f"same time: {r['cv']:,.0f} of cost variance and "
                f"{r['sv']:,.0f} of schedule variance. The project manager has "
                f"it as {r['project_health']}."),
           sev="critical", value=abs(r["vac"] or 0), unit="currency")
        for r in rows]
    return population, findings


def chk_evm_04(con, org, as_of):
    population = _evm_population(con, org, "AND tcpi IS NOT NULL")
    rows = _rows(con, """
        SELECT e.project_id, p.project_code, p.project_name, e.tcpi, e.cpi,
               e.bac, e.ev, e.ac, e.vac
          FROM project_evm e JOIN project p USING(project_id)
         WHERE e.org_id=? AND e.is_reportable=1 AND e.tcpi IS NOT NULL
           AND e.cpi IS NOT NULL AND e.tcpi - e.cpi > ?
         ORDER BY (e.tcpi - e.cpi) DESC""", (org, T["tcpi_gap"]))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=f"needs {r['tcpi']:,.2f}, achieving {r['cpi']:,.2f}",
           expected=(f"a required efficiency within "
                     f"{T['tcpi_gap']:,.2f} of the achieved one"),
           msg=(f"{r['project_code']} finishes on budget only if the "
                f"remaining work is delivered at {r['tcpi']:,.2f} efficiency "
                f"against the {r['cpi']:,.2f} achieved so far. Nothing in the "
                f"engagement's own history supports that."),
           sev="critical" if r["tcpi"] - r["cpi"] > 0.35 else "high",
           value=abs(r["vac"] or 0), unit="currency")
        for r in rows]
    return population, findings


def chk_evm_05(con, org, as_of):
    """
    Recorded progress against booked effort.

    The comparison is task actual hours, which the plan carries, against
    timesheet hours on the same engagement. They are two independent records
    of the same work and they should agree within a wide band. Where they do
    not, every index and every fixed-fee recognition figure built on percent
    complete is reading one of them and ignoring the other.
    """
    basis = """
        SELECT p.project_id, p.project_code, p.project_name, p.budget_hours,
               p.contract_value, p.billing_model,
               COALESCE(t.plan_hours, 0)  AS plan_hours,
               COALESCE(te.booked, 0)     AS booked
          FROM project p
          LEFT JOIN (SELECT project_id, SUM(actual_hours) plan_hours
                       FROM project_task
                      WHERE plan_version_id IN
                            (SELECT plan_version_id FROM project_plan_version
                              WHERE is_current=1)
                      GROUP BY project_id) t ON t.project_id=p.project_id
          LEFT JOIN (SELECT project_id, SUM(hours) booked
                       FROM time_entry GROUP BY project_id) te
                 ON te.project_id=p.project_id
         WHERE p.org_id=? AND p.project_status IN ('In Flight','Complete')
           AND COALESCE(t.plan_hours,0) > 20"""
    rows = _rows(con, basis, (org,))
    population = len(rows)
    floor = T["progress_support_pct"] / 100.0
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=(f"{r['booked']:,.0f} hours booked against "
                     f"{r['plan_hours']:,.0f} hours of recorded progress"),
           expected=(f"booked time within {100 - T['progress_support_pct']:,.0f}"
                     f"% of recorded progress"),
           msg=(f"{r['project_code']} records {r['plan_hours']:,.0f} hours of "
                f"delivered effort in its plan and carries {r['booked']:,.0f} "
                f"hours of timesheets, which is "
                f"{r['booked'] / r['plan_hours'] * 100:,.0f}% of it. Percent "
                f"complete feeds earned value and, on fixed fee, revenue "
                f"recognition, so the two records have to be reconciled before "
                f"either is published."),
           sev="critical" if r["booked"] < r["plan_hours"] * 0.4 else "high",
           value=r["contract_value"], unit="currency")
        for r in rows if r["booked"] < r["plan_hours"] * floor]
    return population, findings


def chk_evm_06(con, org, as_of):
    population = _one(con, """
        SELECT COUNT(*) FROM project
        WHERE org_id=? AND project_status='In Flight'""", (org,))
    rows = _rows(con, """
        SELECT p.project_id, p.project_code, p.project_name, p.contract_value,
               COALESCE(t.tasks, 0)   AS tasks,
               COALESCE(t.hours, 0)   AS hours,
               COALESCE(t.dated, 0)   AS dated
          FROM project p
          LEFT JOIN (SELECT project_id, COUNT(*) tasks,
                            SUM(planned_hours) hours,
                            SUM(CASE WHEN planned_end IS NOT NULL
                                     THEN 1 ELSE 0 END) dated
                       FROM project_task
                      WHERE plan_version_id IN
                            (SELECT plan_version_id FROM project_plan_version
                              WHERE is_current=1)
                      GROUP BY project_id) t ON t.project_id=p.project_id
         WHERE p.org_id=? AND p.project_status='In Flight'
           AND (COALESCE(t.tasks,0)=0 OR COALESCE(t.hours,0)<=0
                OR COALESCE(t.dated,0)=0)""", (org,))
    findings = [
        _f(("project", r["project_id"],
            f"{r['project_code']} {r['project_name']}"),
           observed=("no current plan" if not r["tasks"] else
                     "no planned hours" if r["hours"] <= 0 else
                     "no planned dates"),
           expected="a current plan with planned hours and planned dates",
           msg=(f"{r['project_code']} cannot carry a cost or schedule index: "
                f"{'it has no current plan version' if not r['tasks'] else ('its plan carries no planned hours' if r['hours'] <= 0 else 'its plan carries no planned end dates')}. "
                f"It is not performing well or badly, it is unmeasured."),
           sev="high", value=r["contract_value"], unit="currency")
        for r in rows]
    return population, findings


RUNNERS = {
    "BILL-01": chk_bill_01, "BILL-02": chk_bill_02, "BILL-03": chk_bill_03,
    "BILL-04": chk_bill_04, "BILL-05": chk_bill_05, "BILL-06": chk_bill_06,
    "BILL-07": chk_bill_07,
    "TIME-01": chk_time_01, "TIME-02": chk_time_02, "TIME-03": chk_time_03,
    "TIME-04": chk_time_04, "TIME-05": chk_time_05, "TIME-06": chk_time_06,
    "TIME-07": chk_time_07, "TIME-08": chk_time_08, "TIME-09": chk_time_09,
    "ACT-01": chk_act_01, "ACT-02": chk_act_02, "ACT-03": chk_act_03,
    "ACT-04": chk_act_04, "ACT-05": chk_act_05,
    "RES-01": chk_res_01, "RES-02": chk_res_02, "RES-03": chk_res_03,
    "RES-04": chk_res_04, "RES-05": chk_res_05, "RES-06": chk_res_06,
    "GOV-01": chk_gov_01, "GOV-02": chk_gov_02, "GOV-03": chk_gov_03,
    "GOV-04": chk_gov_04, "GOV-05": chk_gov_05, "GOV-06": chk_gov_06,
    "GOV-07": chk_gov_07, "GOV-08": chk_gov_08,
    "CLOSE-01": chk_close_01, "CLOSE-02": chk_close_02,
    "CLOSE-03": chk_close_03, "CLOSE-04": chk_close_04,
    "MIG-01": chk_mig_01, "MIG-02": chk_mig_02, "MIG-03": chk_mig_03,
    "MIG-04": chk_mig_04,
    "CFG-01": chk_cfg_01, "CFG-02": chk_cfg_02, "CFG-03": chk_cfg_03,
    "CFG-04": chk_cfg_04, "CFG-05": chk_cfg_05, "CFG-06": chk_cfg_06,
    "CFG-07": chk_cfg_07,
    "EVM-01": chk_evm_01, "EVM-02": chk_evm_02, "EVM-03": chk_evm_03,
    "EVM-04": chk_evm_04, "EVM-05": chk_evm_05, "EVM-06": chk_evm_06,
}


def load_catalogue(con):
    con.execute("DELETE FROM delivery_check")
    con.executemany("""
        INSERT INTO delivery_check
          (check_code, family, family_label, title, assertion, why_it_matters,
           severity, enforcement, is_gate, gate_weight, scope, remediation,
           vendor_basis, vendor_source, check_class, sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", catalogue())
    con.execute("DELETE FROM metric_definition")
    con.executemany("""
        INSERT INTO metric_definition
          (metric_code, metric_label, formula, numerator, denominator, notes,
           vendor, vendor_formula, vendor_source, divergence, sort_order)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", metric_definitions())


def run_for_org(con, org_id, as_of):
    started = time.time()
    cat = {r[0]: r for r in catalogue()}
    con.execute("DELETE FROM check_finding WHERE check_run_id IN "
                "(SELECT check_run_id FROM check_run WHERE org_id=?)", (org_id,))
    con.execute("DELETE FROM check_result WHERE check_run_id IN "
                "(SELECT check_run_id FROM check_run WHERE org_id=?)", (org_id,))
    con.execute("DELETE FROM check_run WHERE org_id=?", (org_id,))

    cur = con.execute("""
        INSERT INTO check_run (org_id, run_at, as_of_date, engine_version,
                               checks_run, findings_total, blocking_total,
                               runtime_ms)
        VALUES (?,?,?,?,0,0,0,0)""",
        (org_id, dt.datetime.now().isoformat(timespec="seconds"), as_of,
         ENGINE_VERSION))
    run_id = cur.lastrowid

    findings_total = blocking_total = 0
    gate_weight_total = gate_weight_passed = 0.0
    results = []

    for code, fn in RUNNERS.items():
        meta = cat[code]
        severity = meta[6]
        is_gate, weight = meta[8], meta[9]
        try:
            population, findings = fn(con, org_id, as_of)
        except Exception as exc:                      # a broken check is a bug
            raise SystemExit(f"check {code} failed: {exc}")

        failing = len(findings)
        value = sum(f["value_at_stake"] or 0 for f in findings) or None
        unit = next((f["value_unit"] for f in findings if f["value_unit"]), None)

        # A check that fails more records than it examined is measuring two
        # different things and calling them one. That reads as a broken report
        # to anybody senior, so it fails the build rather than shipping.
        if failing > population:
            raise SystemExit(
                f"check {code}: {failing} failing against a population of "
                f"{population}. The detail query and the population query are "
                f"counting different grains; fix the population query.")

        if population == 0:
            status = "not_applicable"
        elif failing == 0:
            status = "pass"
        elif severity in ("critical", "high"):
            status = "fail"
        else:
            status = "warn"

        if is_gate:
            gate_weight_total += weight
            if status in ("pass", "not_applicable"):
                gate_weight_passed += weight

        statement = _statement(meta, population, failing, value, unit, status)
        results.append((run_id, code, population, failing, value, unit,
                        status, statement))
        findings_total += failing
        if status == "fail":
            blocking_total += 1

        # Detail is capped, the count is not. One systemic fault would
        # otherwise bury every other check in the report, and a 'failing'
        # number that quietly means 'as many as we chose to show' is worse
        # than no number. Every detail query is ordered by materiality, so
        # what gets stored is the part worth reading.
        for f in findings[:FINDING_CAP]:
            con.execute("""
                INSERT INTO check_finding
                  (check_run_id, check_code, entity_table, entity_pk,
                   entity_label, observed, expected, message, severity,
                   value_at_stake, value_unit)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, code, f["entity_table"], f["entity_pk"],
                 f["entity_label"], f["observed"], f["expected"], f["message"],
                 f["severity"] or severity, f["value_at_stake"], f["value_unit"]))

    con.executemany("""
        INSERT INTO check_result
          (check_run_id, check_code, population, failing, value_at_stake,
           value_unit, status, statement)
        VALUES (?,?,?,?,?,?,?,?)""", results)

    readiness = (gate_weight_passed / gate_weight_total * 100
                 if gate_weight_total else None)
    verdict = ("ready" if readiness is not None and readiness >= 95
               else "conditional" if readiness is not None and readiness >= 80
               else "not ready")
    con.execute("""
        UPDATE check_run SET checks_run=?, findings_total=?, blocking_total=?,
               runtime_ms=?, readiness_pct=?, readiness_verdict=?
        WHERE check_run_id=?""",
        (len(RUNNERS), findings_total, blocking_total,
         int((time.time() - started) * 1000), readiness, verdict, run_id))
    return run_id, len(RUNNERS), findings_total, blocking_total, readiness, verdict


def _statement(meta, population, failing, value, unit, status):
    code, _fam, _fl, title = meta[0], meta[1], meta[2], meta[3]
    if population == 0:
        return f"{title}: nothing in scope to check."
    if failing == 0:
        return f"{title}: all {population:,} records in scope pass."
    money = ""
    if value and unit == "currency":
        money = f", representing {value:,.0f} of value"
    elif value and unit == "hours":
        money = f", representing {value:,.0f} hours"
    elif value and unit in ("points", "days", "percent", "bands"):
        money = f", {value:,.0f} {unit} in total"
    return (f"{title}: {failing:,} of {population:,} records in scope fail"
            f"{money}.")


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    load_catalogue(con)
    as_of = con.execute("SELECT MAX(as_of_date) FROM intelligence_run").fetchone()[0]
    if not as_of:
        as_of = dt.date.today().isoformat()
    for (org_id, code) in con.execute("SELECT org_id, org_code FROM org").fetchall():
        run_id, n, findings, blocking, readiness, verdict = run_for_org(
            con, org_id, as_of)
        print(f"{code}: {n} checks, {findings:,} findings, {blocking} blocking, "
              f"readiness {readiness:,.1f}% ({verdict})")
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
