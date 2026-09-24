"""
Evidence for the diligence pack: satisfaction responses, hypercare periods,
performance reviews, product tickets.

All values are synthetic demonstration data. The point of the module is not
the data, it is the consistency of it. A red project has to have poor CSAT,
a defect cluster, and a hypercare period that ran long, because that is what
makes the derived analysis testable: if the engine says "the top cause of
slip in Configure is customer-raised issues", somebody has to be able to
click through and find the issues. Generating each of these independently
would produce a console where every screen is individually plausible and no
two screens agree, which is exactly the failure this whole project exists to
avoid.

So everything here is conditioned on facts already in the database: the
project's overrun, its health, its methodology, its phase slips and its
risks. Nothing is drawn without reference to what the engagement actually
did.
"""
import os
import random
import sqlite3
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

# ---------------------------------------------------------------------------
# Verbatim comments, grouped by what they are about and how bad it is.
#
# These read like real survey text on purpose: specific, slightly awkward,
# and about one thing. Generic praise and generic complaint are both useless
# to a reader, which is the argument for holding the verbatim at all.
# ---------------------------------------------------------------------------
COMMENTS = {
    ("delivery_team", "positive"): [
        "The consultant assigned to us knew the payroll rules better than our own team did. That saved us weeks.",
        "Same two people from kickoff to go live. That continuity mattered more than anything else.",
        "Our lead was straight with us when something was going to be late, which meant we believed her when she said things were fine.",
        "Configuration workshops were well run. We came out of each one knowing what had been decided.",
    ],
    ("delivery_team", "mixed"): [
        "Strong team but we lost our lead consultant halfway through and the handover cost us a fortnight.",
        "The senior people were excellent. The junior resource needed more supervision than we expected to provide.",
        "Good technical work. Communication between their team members was not always joined up.",
    ],
    ("delivery_team", "negative"): [
        "Three different consultants in six months. Each one asked us the same questions the last one had.",
        "We spent more time managing their team than ours. That is not what we bought.",
        "Nobody seemed to own the project. Escalating meant explaining the history from the start every time.",
    ],
    ("scope_change", "mixed"): [
        "The change order process was clear, but the number of change orders was not what we had budgeted for.",
        "Fair enough that the extra entity was out of scope. It would have been better to hear that at estimate rather than at build.",
        "We accepted the additional cost. We did not accept being told about it two weeks before the invoice.",
    ],
    ("scope_change", "negative"): [
        "Everything we thought was included turned out to be a change order. The original quote was not a quote.",
        "Four change orders on a fixed price project. At that point it is not fixed price.",
    ],
    ("timeline", "positive"): [
        "Live on the date we were given at kickoff. Nobody in our sector expects that.",
        "They pulled the go live forward by two weeks when our year end moved. That was not a small ask.",
    ],
    ("timeline", "mixed"): [
        "Six weeks late, but they told us at week four rather than week fourteen, so we could plan around it.",
        "The date moved once and then held. We can work with that.",
    ],
    ("timeline", "negative"): [
        "The go live date moved three times. Each time we had told our staff the old date.",
        "We were still in parallel run four months after we were supposed to be live.",
        "Every status report said green until the month it said red.",
    ],
    ("data_migration", "mixed"): [
        "Migration took three cycles rather than the one we planned. Our data was worse than we admitted, to be fair.",
        "The reconciliation reports were good. Getting to them took longer than anyone wanted.",
    ],
    ("data_migration", "negative"): [
        "Opening balances did not tie out for two months after go live. Our auditors noticed before we did.",
        "We loaded the same employee file four times because nobody could tell us which load had failed.",
    ],
    ("training", "positive"): [
        "The end user training was pitched right. Our administrators were confident from day one.",
        "Recorded sessions have been used by every new starter since. Genuinely useful.",
    ],
    ("training", "mixed"): [
        "Training was fine for the core team. The wider staff needed a second cohort we had to pay for.",
        "Good material, delivered slightly too early. By go live people had forgotten it.",
    ],
    ("training", "negative"): [
        "Training happened two months before go live on a system that then changed. We effectively did it twice.",
    ],
    ("product_fit", "positive"): [
        "The statutory reporting works the way our sector actually works. That was the reason we bought it.",
        "Fewer workarounds than the system it replaced, by a wide margin.",
    ],
    ("product_fit", "mixed"): [
        "The product does what it says. Some of what we assumed was standard turned out to need configuration we had not scoped.",
        "Reporting is capable but the standard pack did not cover our board reporting. We built our own.",
    ],
    ("product_fit", "negative"): [
        "We are running two spreadsheets alongside the system to do things we were told it did.",
        "Defects in the absence rules took four months to fix and we ran manual payroll adjustments throughout.",
    ],
    ("support", "positive"): [
        "Hypercare was worth every day of it. Someone picked up the phone every time.",
        "Support tickets were answered by people who knew our configuration. That is rare.",
    ],
    ("support", "mixed"): [
        "Hypercare ended on schedule but we were not ready for it to. We extended at our own cost.",
        "Response times were fine. Resolution times on anything complex were not.",
    ],
    ("support", "negative"): [
        "We came out of hypercare with eleven open items and no plan for any of them.",
        "After go live the team we knew disappeared and we were a ticket number.",
    ],
    ("commercial", "mixed"): [
        "Invoices arrived three months after the work. Reconciling them was a project of its own.",
        "The rates were fair. The billing was late and inconsistent enough that we queried most invoices.",
    ],
    ("commercial", "negative"): [
        "We were invoiced for a milestone we had not signed off. It took two months to unwind.",
    ],
}

THEMES = list({k[0] for k in COMMENTS})

TICKET_TITLES = {
    "bug": [
        ("Absence accrual rounds down at year end", "Absence"),
        ("Payroll journal posts to prior period after retro change", "Payroll"),
        ("Statutory remittance report excludes terminated employees", "Payroll"),
        ("Bank file rejects records with apostrophes in surname", "Payroll"),
        ("Purchase requisition approval skips second approver", "Finance"),
        ("Budget check passes on a closed period", "Finance"),
        ("Journal import silently drops lines with blank cost centre", "Finance"),
        ("Timesheet copy forward duplicates non-billable rows", "Time"),
        ("Employee self-service shows prior manager after transfer", "HR"),
        ("Position hierarchy loops on a vacant reporting line", "HR"),
        ("Report scheduler fires twice on daylight saving change", "Reporting"),
        ("Dashboard totals differ from underlying detail export", "Reporting"),
        ("Integration retry duplicates records on timeout", "Integration"),
        ("Single sign-on session drops on slow networks", "Platform"),
        ("Attachment upload fails silently above 20MB", "Platform"),
        ("Grant fund balance ignores encumbrances", "Finance"),
        ("Seniority calculation wrong for rehired employees", "HR"),
        ("Multi-entity consolidation double counts intercompany", "Finance"),
    ],
    "enhancement": [
        ("Bulk approve timesheets by team", "Time"),
        ("Configurable absence accrual rules per bargaining unit", "Absence"),
        ("Board reporting pack as a standard template", "Reporting"),
        ("Two-way calendar sync for interview scheduling", "HR"),
        ("Cost centre reallocation without reversing journals", "Finance"),
        ("Mobile expense capture with receipt scanning", "Finance"),
        ("Self-service position budgeting for managers", "HR"),
        ("Audit trail export in a format auditors accept", "Platform"),
        ("Scheduled data quality report per module", "Platform"),
        ("Multi-year grant reporting", "Finance"),
        ("Configurable approval limits by fund", "Finance"),
        ("Bulk employee data correction with preview", "HR"),
        ("Public sector pay transparency reporting", "Payroll"),
        ("Integration monitoring dashboard for administrators", "Integration"),
    ],
}

STRENGTHS = [
    "Technically the strongest person on the practice for {product}. Customers ask for her by name.",
    "Reliable on the hardest configuration work and will say when an estimate is wrong.",
    "Brings junior consultants on quickly. Two of this year's starters credit him directly.",
    "Calm with difficult customers. Took over a red engagement and it went live on the revised date.",
    "Unusually good at reconciliation work, which is the part most people avoid.",
    "Trusted by the client sponsors on every account she has touched.",
]
DEVELOPMENT = [
    "Timesheet discipline. Weeks are frequently submitted late, which delays invoicing on his engagements.",
    "Takes on more than can be delivered and does not flag it until the date is at risk.",
    "Written communication with sponsors needs work. Verbal is strong.",
    "Needs the {product} certification to lead engagements independently. Booked twice and deferred twice.",
    "Estimating. Consistently optimistic on data migration effort by a wide margin.",
    "Delegation. Does the work rather than teaching the work, which caps the team's capacity.",
]
GOALS = [
    "Lead two {product} implementations end to end without a senior shadow.",
    "Complete the {product} certification by the end of the next cycle.",
    "Bring billable utilisation to target without extending working hours.",
    "Mentor one new consultant through their first go live.",
    "Reduce estimate variance on data migration to within 15%.",
    "Take the technical lead role on one recovery engagement.",
]
MANAGER_COMMENTS = [
    "A strong cycle. The rating reflects delivery outcomes rather than hours worked.",
    "Rating held at last cycle's level. The work is good and the scope of it has not grown.",
    "Below expectation this cycle, driven by two engagements that slipped for reasons partly outside his control. Calibrated down after discussion rather than on the numbers alone.",
    "Promotion conversation deferred to next cycle. The capability is there; the track record on independent delivery is not yet.",
    "Retention risk. Market rate for this skill set has moved and we have not.",
    "Consistent, and the sort of consistency the practice is built on.",
]
RATING_LABELS = [
    (4.5, "Exceptional"), (3.8, "Exceeds expectations"),
    (3.0, "Meets expectations"), (2.2, "Partially meets"), (0.0, "Below expectations"),
]


def rating_label(x):
    for floor, label in RATING_LABELS:
        if x >= floor:
            return label
    return "Below expectations"


def q2(x):
    return round(float(x), 2)


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def pick(rng, options):
    return rng.choice(options) if options else None


# ---------------------------------------------------------------------------
def comment_for(rng, score, project_facts):
    """
    Pick a verbatim that matches both the score and what actually went wrong.

    A three out of five beside a comment praising the timeline is the kind of
    detail that makes a reader stop trusting the whole page, so the theme is
    chosen from the engagement's own evidence: heavy rework points at the
    delivery team, several change orders at scope, a large slip at timeline,
    a defect cluster at product fit.
    """
    sentiment = ("positive" if score >= 4.2 else
                 "mixed" if score >= 3.2 else "negative")
    weights = []
    if project_facts["slip_days"] > 25:
        weights += ["timeline"] * 3
    if project_facts["change_orders"] >= 2:
        weights += ["scope_change"] * 3
    if project_facts["rework_share"] > 0.10:
        weights += ["delivery_team"] * 2
    if project_facts["is_migration"]:
        weights += ["data_migration"] * 2
    if project_facts["defects"] >= 3:
        weights += ["product_fit"] * 3
    if project_facts["hypercare_overrun"] > 10:
        weights += ["support"] * 2
    if project_facts["unbilled_old"]:
        weights += ["commercial"] * 2
    weights += ["training", "delivery_team", "product_fit", "support"]

    for _ in range(8):
        theme = rng.choice(weights)
        pool = COMMENTS.get((theme, sentiment))
        if pool:
            return rng.choice(pool), theme, sentiment
    # Nothing in the bank for that combination: say nothing rather than
    # putting words in a customer's mouth that do not fit their score.
    return None, None, sentiment


def project_facts(cur, project_id, product, ptype, planned_end, actual_end):
    slip = 0.0
    if planned_end and actual_end:
        slip = (date.fromisoformat(actual_end)
                - date.fromisoformat(planned_end)).days
    cos = cur.execute("SELECT COUNT(*) FROM change_order WHERE project_id=? "
                      "AND status='Approved'", (project_id,)).fetchone()[0]
    tot, rework = cur.execute(
        """SELECT COALESCE(SUM(hours),0),
                  COALESCE(SUM(CASE WHEN time_category IN ('Rework','Over-service')
                                    THEN hours ELSE 0 END),0)
             FROM time_entry WHERE project_id=?""", (project_id,)).fetchone()
    unbilled = cur.execute(
        """SELECT COUNT(*) FROM time_entry WHERE project_id=? AND is_billable=1
             AND approval_status='Approved' AND invoiced=0""",
        (project_id,)).fetchone()[0]
    return {
        "slip_days": slip,
        "change_orders": cos,
        "rework_share": (rework / tot) if tot else 0.0,
        "is_migration": ptype in ("Migration", "Implementation"),
        "defects": 0,              # filled in after tickets are generated
        "hypercare_overrun": 0.0,  # filled in after hypercare is generated
        "unbilled_old": unbilled > 40,
        "total_hours": tot,
    }


# ---------------------------------------------------------------------------
def seed_hypercare(con, cur, rng, today):
    """
    Hypercare from go live to exit.

    Length is not drawn at random. It follows the state the engagement was in
    at go live: an engagement that overran, carried rework, or shipped with
    open defects does not suddenly become clean the week it goes live, and
    the whole reason to measure hypercare separately is that it is where the
    consequences of a rushed go live get paid for, usually unbilled.
    """
    rows = []
    projects = cur.execute("""
        SELECT p.project_id, p.org_id, p.product, p.project_type,
               p.go_live_date, p.actual_end_date, p.planned_end_date,
               p.budget_hours, p.billing_model,
               COALESCE(te.hours,0) AS booked,
               COALESCE(te.rework,0) AS rework
        FROM project p
        LEFT JOIN (SELECT project_id, SUM(hours) hours,
                          SUM(CASE WHEN time_category IN ('Rework','Over-service')
                                   THEN hours ELSE 0 END) rework
                     FROM time_entry GROUP BY project_id) te
               ON te.project_id = p.project_id
        WHERE p.go_live_date IS NOT NULL""").fetchall()

    for (pid, org_id, product, ptype, go_live, actual_end, planned_end,
         budget_hours, billing_model, booked, rework) in projects:
        gl = date.fromisoformat(go_live)
        overrun = (booked / budget_hours) if budget_hours else 1.0
        rework_share = (rework / booked) if booked else 0.0
        planned_days = rng.choice([14, 21, 30, 30, 45, 60])

        # Pressure at go live drives the extension.
        pressure = max(0.0, overrun - 1.0) * 1.8 + rework_share * 2.2
        extension = 0.0
        if pressure > 0.05:
            extension = planned_days * min(2.4, pressure) * rng.uniform(0.6, 1.4)
        actual_days = planned_days + extension
        exit_date = gl + timedelta(days=int(round(actual_days)))
        if exit_date > today:
            exit_date, actual_days = None, None

        effort = (budget_hours or 400) * rng.uniform(0.03, 0.09) * (
            1 + min(1.5, pressure))
        # Hypercare is usually inside the fee on fixed price work and only
        # sometimes chargeable on T&M. This is where services margin quietly
        # goes.
        if billing_model in ("Time and Materials", "Capped T&M"):
            billable = effort * rng.uniform(0.55, 0.95)
        else:
            billable = effort * rng.uniform(0.0, 0.25)

        tickets = int(round(effort / rng.uniform(4.0, 9.0)))
        blocking = int(round(tickets * min(0.35, pressure * 0.4)))
        verdict = ("clean" if extension <= planned_days * 0.15 else
                   "extended" if exit_date else "unresolved")
        reason = None
        if verdict != "clean":
            reason = pick(rng, [
                "Open defects in the statutory reporting were not fixed at exit.",
                "Parallel payroll run variances unresolved at the planned exit.",
                "Customer administrators not yet confident to operate unaided.",
                "Opening balance reconciliation still outstanding.",
                "Interface error volume above the agreed exit threshold.",
                "Additional training cohort required before handover to support.",
            ])
        statement = (
            f"Hypercare planned at {planned_days:,.0f} days"
            + (f", closed in {actual_days:,.0f}." if actual_days
               else ", still open.")
            + f" {effort:,.0f} hours of delivery effort, of which "
              f"{billable/effort*100 if effort else 0:,.0f}% was billable."
            + (f" {reason}" if reason else ""))

        rows.append((org_id, pid, go_live,
                     (gl + timedelta(days=planned_days)).isoformat(),
                     exit_date.isoformat() if exit_date else None,
                     planned_days, q2(actual_days) if actual_days else None,
                     q2(extension) if actual_days else None,
                     q2(effort), q2(billable),
                     q2(effort / booked * 100) if booked else None,
                     tickets, blocking, None, verdict, reason, statement))

    cur.executemany("""
        INSERT INTO hypercare_period
          (org_id, project_id, go_live_date, planned_exit_date,
           actual_exit_date, planned_days, actual_days, overrun_days,
           effort_hours, billable_hours, effort_pct_of_project,
           tickets_raised, tickets_blocking, exit_csat, exit_verdict,
           extension_reason, statement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    return len(rows)


def seed_tickets(con, cur, rng, today):
    """
    Product bugs and enhancements.

    Defects cluster: a product with a weak module generates the same class of
    ticket across several customers, and the engagement that hit it first
    carries the rework hours. Scattering defects uniformly across products
    would make the backlog look like noise, and the useful question is which
    product is costing delivery time.
    """
    rows = []
    seq = {}
    for org_id, prefix in cur.execute(
            "SELECT org_id, org_code FROM org").fetchall():
        products = [r[0] for r in cur.execute(
            "SELECT DISTINCT product FROM project WHERE org_id=? "
            "AND product IS NOT NULL", (org_id,)).fetchall()]
        # One or two products carry a disproportionate share. That is what
        # makes the backlog worth reporting per product rather than in total.
        hot = set(rng.sample(products, max(1, len(products) // 3)))
        for product in products:
            # Enough tickets per product that the backlog trend means
            # something. Four open bugs on a product is an anecdote; forty is
            # a backlog with a direction.
            base = rng.randint(55, 95) * (2 if product in hot else 1)
            # Engagements on this product, so a ticket can point at one.
            projects = cur.execute(
                """SELECT p.project_id, p.customer_id, p.start_date,
                          p.actual_end_date, p.project_status
                     FROM project p WHERE p.org_id=? AND p.product=?""",
                (org_id, product)).fetchall()
            for i in range(base):
                is_bug = rng.random() < (0.66 if product in hot else 0.5)
                ttype = "bug" if is_bug else "enhancement"
                title, module = rng.choice(TICKET_TITLES[ttype])
                proj = pick(rng, projects)
                raised = today - timedelta(days=rng.randint(5, 900))
                if proj and proj[2]:
                    ps = date.fromisoformat(proj[2])
                    pe = (date.fromisoformat(proj[3]) if proj[3] else today)
                    if pe > ps:
                        raised = ps + timedelta(
                            days=rng.randint(0, max(1, (min(pe, today) - ps).days)))
                severity = (rng.choices(
                    ["blocker", "high", "medium", "low"],
                    weights=[0.07, 0.24, 0.44, 0.25])[0] if is_bug else None)
                priority = rng.choices(["P1", "P2", "P3", "P4"],
                                       weights=[0.08, 0.27, 0.42, 0.23])[0]
                raised_by = rng.choices(
                    ["customer", "delivery", "internal_qa"],
                    weights=[0.52, 0.33, 0.15])[0]

                # Resolution likelihood follows severity, and blockers on a
                # hot product still sit longer than they should.
                p_resolved = {"blocker": 0.88, "high": 0.78, "medium": 0.62,
                              "low": 0.44, None: 0.34}[severity]
                if product in hot:
                    p_resolved -= 0.14
                resolved = rng.random() < p_resolved
                ttr = None
                resolved_on = None
                released_in = None
                if resolved:
                    ttr = {"blocker": rng.uniform(2, 21),
                           "high": rng.uniform(7, 70),
                           "medium": rng.uniform(20, 160),
                           "low": rng.uniform(45, 320),
                           None: rng.uniform(60, 400)}[severity]
                    rd = raised + timedelta(days=int(ttr))
                    if rd > today:
                        resolved, ttr = False, None
                    else:
                        resolved_on = rd.isoformat()
                        released_in = (f"{rd.year}.{(rd.month - 1) // 3 + 1}"
                                       f".{rng.randint(0, 4)}")
                if resolved:
                    status = rng.choices(["released", "fixed"],
                                         weights=[0.82, 0.18])[0]
                else:
                    status = rng.choices(
                        ["open", "in_progress", "deferred", "declined"],
                        weights=[0.44, 0.24, 0.24, 0.08])[0]

                effort = (rng.uniform(2, 40) if is_bug
                          else rng.uniform(12, 220))
                seq[org_id] = seq.get(org_id, 0) + 1
                rows.append((
                    org_id,
                    f"{prefix[:3].upper()}-{'BUG' if is_bug else 'ENH'}-"
                    f"{seq[org_id]:04d}",
                    product, module, ttype, severity, priority, title,
                    None, raised.isoformat(), raised_by,
                    proj[1] if proj else None, proj[0] if proj else None,
                    status, resolved_on, released_in,
                    q2((today - raised).days),
                    q2(ttr) if ttr else None, q2(effort),
                    1 if (severity == "blocker" and not resolved) else 0,
                    1 if (is_bug and rng.random() < 0.12) else 0,
                    pick(rng, ["Manual adjustment each period",
                               "Export, correct, reimport",
                               "Run the prior version of the report",
                               None, None])))
    cur.executemany("""
        INSERT INTO product_ticket
          (org_id, ticket_ref, product, module, ticket_type, severity,
           priority, title, detail, raised_on, raised_by, customer_id,
           project_id, status, resolved_on, released_in, age_days,
           time_to_resolve_days, effort_hours, blocks_go_live, is_regression,
           workaround)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    return len(rows)


def seed_csat(con, cur, rng, today):
    """
    Survey responses at the points a services business actually surveys.

    The score is derived from the engagement rather than drawn: overrun,
    slip, rework, change orders and open defects each pull it down, and the
    comment is then chosen to match both the score and the cause. That is
    what lets the console answer "why did CSAT fall" with evidence instead of
    an assertion.
    """
    rows = []
    projects = cur.execute("""
        SELECT p.project_id, p.org_id, p.customer_id, p.product,
               p.project_type, p.project_status, p.project_health,
               p.start_date, p.planned_end_date, p.actual_end_date,
               p.go_live_date, p.budget_hours,
               COALESCE(te.hours,0) booked,
               COALESCE(hc.overrun_days,0) hc_overrun,
               COALESCE(dt.defects,0) defects
        FROM project p
        LEFT JOIN (SELECT project_id, SUM(hours) hours FROM time_entry
                    GROUP BY project_id) te ON te.project_id=p.project_id
        LEFT JOIN hypercare_period hc ON hc.project_id=p.project_id
        LEFT JOIN (SELECT project_id, COUNT(*) defects FROM product_ticket
                    WHERE ticket_type='bug' GROUP BY project_id) dt
               ON dt.project_id=p.project_id
        WHERE p.start_date IS NOT NULL""").fetchall()

    for (pid, org_id, cust_id, product, ptype, status, health, start,
         planned_end, actual_end, go_live, budget_hours, booked,
         hc_overrun, defects) in projects:
        facts = project_facts(cur, pid, product, ptype, planned_end, actual_end)
        facts["defects"] = defects
        facts["hypercare_overrun"] = hc_overrun or 0.0

        overrun = (booked / budget_hours) if budget_hours else 1.0
        # Base score by outcome, then pulled down by each specific problem.
        score_base = 4.55
        score_base -= max(0.0, overrun - 1.0) * 1.1
        score_base -= min(0.9, max(0.0, facts["slip_days"]) / 120.0)
        score_base -= min(0.6, facts["rework_share"] * 2.4)
        score_base -= min(0.5, max(0, facts["change_orders"] - 1) * 0.16)
        score_base -= min(0.5, defects * 0.055)
        score_base -= min(0.4, (hc_overrun or 0) / 120.0)
        if health == "Red":
            score_base -= 0.45
        elif health == "Yellow":
            score_base -= 0.18

        points = [("kickoff", start)]
        if planned_end:
            mid = date.fromisoformat(start) + timedelta(
                days=int(((date.fromisoformat(actual_end or planned_end)
                           - date.fromisoformat(start)).days) * 0.4))
            points.append(("design", mid.isoformat()))
        if go_live:
            points.append(("go_live", go_live))
            hc = cur.execute("SELECT actual_exit_date FROM hypercare_period "
                             "WHERE project_id=?", (pid,)).fetchone()
            if hc and hc[0]:
                points.append(("hypercare_exit", hc[0]))
        if status == "Complete" and actual_end:
            points.append(("annual", (date.fromisoformat(actual_end)
                                      + timedelta(days=rng.randint(150, 340)))
                           .isoformat()))

        for point, when in points:
            if not when or date.fromisoformat(when) > today:
                continue
            if rng.random() > 0.72:            # not every survey comes back
                continue
            # Kickoff is optimism; the score converges on reality later.
            drift = {"kickoff": 0.55, "design": 0.2, "go_live": 0.0,
                     "hypercare_exit": -0.1, "annual": 0.15}[point]
            score = score_base + drift + rng.uniform(-0.35, 0.35)
            score = max(1.0, min(5.0, round(score * 2) / 2))
            comment, theme, sentiment = comment_for(rng, score, facts)
            role = rng.choices(
                ["sponsor", "project_lead", "end_user", "finance"],
                weights=[0.3, 0.42, 0.2, 0.08])[0]
            nps = None
            would_ref = None
            if point in ("go_live", "hypercare_exit", "annual"):
                nps = int(round((score - 3.0) / 2.0 * 100))
                nps = max(-100, min(100, nps + rng.randint(-12, 12)))
                would_ref = 1 if score >= 4.0 and rng.random() < 0.86 else 0
            rows.append((org_id, cust_id, pid, point, when, role,
                         score, nps, would_ref, comment, theme, sentiment,
                         1 if score <= 2.5 else 0, None))

    cur.executemany("""
        INSERT INTO csat_response
          (org_id, customer_id, project_id, survey_point, responded_on,
           respondent_role, score, nps, would_reference, comment, theme,
           sentiment, is_escalation, responded_by_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    return len(rows)


def seed_performance(con, cur, rng, today):
    """
    Three closed performance cycles, with the measures beside the rating.

    The measures are the ones actually in the database for that person in
    that window: their utilisation, the CSAT on the engagements they worked,
    how much of their timesheet arrived on time. That matters because the
    interesting question about a performance cycle is not the average rating,
    it is whether the ratings track anything measurable. Generating ratings
    independently of the numbers would make that question unanswerable, and
    it is the one a new PS leader asks first.
    """
    cycles = []
    for i in range(3):
        end = add_months(date(today.year, today.month, 1), -6 * i)
        start = add_months(end, -6)
        cycles.append((
            start, end,
            f"H{1 if start.month <= 6 else 2}-{start.year}",
            f"{'First' if start.month <= 6 else 'Second'} half {start.year}"))
    cycles.reverse()

    n_reviews = 0
    for org_id, in cur.execute("SELECT org_id FROM org ORDER BY org_id").fetchall():
        emps = cur.execute("""
            SELECT employee_id, employee_name, role, practice_id, cost_rate,
                   billing_rate, utilization_target_pct, start_date,
                   manager_employee_id
              FROM employee WHERE org_id=? AND is_active=1""",
            (org_id,)).fetchall()
        # A persistent component per person, so somebody strong in one cycle
        # is usually strong in the next. Ratings that resample every cycle
        # make the trend meaningless.
        trait = {e[0]: rng.uniform(-0.55, 0.55) for e in emps}

        for ci, (start, end, code, label) in enumerate(cycles):
            cur.execute("""
                INSERT INTO performance_cycle
                  (org_id, cycle_code, cycle_label, period_from, period_to,
                   closed_on, calibrated, participation_pct, sort_order)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (org_id, code, label, start.isoformat(), end.isoformat(),
                 (end + timedelta(days=rng.randint(18, 45))).isoformat(),
                 1 if ci < 2 or rng.random() < 0.6 else 0,
                 q2(rng.uniform(88, 100)), ci))
            cycle_id = cur.lastrowid

            for (eid, name, role, practice_id, cost, bill, util_target,
                 emp_start, mgr) in emps:
                if emp_start and date.fromisoformat(emp_start) > end:
                    continue          # not employed in that cycle
                # Their actual numbers for the window.
                billable, total, late_weeks, weeks = cur.execute("""
                    SELECT COALESCE(SUM(CASE WHEN is_billable=1 THEN hours END),0),
                           COALESCE(SUM(hours),0),
                           COUNT(DISTINCT CASE WHEN approval_status<>'Approved'
                                THEN entry_date END),
                           COUNT(DISTINCT entry_date)
                      FROM time_entry
                     WHERE employee_id=? AND entry_date>=? AND entry_date<?""",
                    (eid, start.isoformat(), end.isoformat())).fetchone()
                weeks_in_cycle = max(1.0, (end - start).days / 7.0)
                util = (billable / (weeks_in_cycle * 37.5) * 100
                        if weeks_in_cycle else 0)
                csat = cur.execute("""
                    SELECT AVG(c.score) FROM csat_response c
                     WHERE c.project_id IN (SELECT DISTINCT project_id
                                              FROM time_entry
                                             WHERE employee_id=? AND project_id
                                                   IS NOT NULL
                                               AND entry_date>=? AND entry_date<?)
                       AND c.responded_on>=? AND c.responded_on<?""",
                    (eid, start.isoformat(), end.isoformat(),
                     start.isoformat(), end.isoformat())).fetchone()[0]
                delivered = cur.execute("""
                    SELECT COUNT(DISTINCT p.project_id) FROM project p
                     WHERE p.actual_end_date>=? AND p.actual_end_date<?
                       AND EXISTS (SELECT 1 FROM time_entry t
                                    WHERE t.project_id=p.project_id
                                      AND t.employee_id=?)""",
                    (start.isoformat(), end.isoformat(), eid)).fetchone()[0]
                on_time = cur.execute("""
                    SELECT AVG(CASE WHEN p.actual_end_date<=p.planned_end_date
                                    THEN 100.0 ELSE 0 END) FROM project p
                     WHERE p.actual_end_date>=? AND p.actual_end_date<?
                       AND EXISTS (SELECT 1 FROM time_entry t
                                    WHERE t.project_id=p.project_id
                                      AND t.employee_id=?)""",
                    (start.isoformat(), end.isoformat(), eid)).fetchone()[0]
                compliance = (100.0 * (1 - late_weeks / weeks)
                              if weeks else None)

                # The rating: mostly the measures, plus a persistent personal
                # component, plus the noise any human process carries.
                #
                # The centre is 3.45 rather than 3.0. Centring on the bottom
                # of the "meets expectations" band put half the workforce
                # below it, which no calibrated cycle produces and no reader
                # believes: a real distribution has most people meeting
                # expectations, a tail above and a smaller tail below. The
                # band boundaries do the work of separating them.
                target = util_target or 72.0
                r = 3.45
                r += max(-0.9, min(0.9, (util - target) / target * 2.2))
                if csat:
                    r += max(-0.7, min(0.7, (csat - 3.9) * 0.55))
                if on_time is not None:
                    r += (on_time / 100.0 - 0.5) * 0.4
                if compliance is not None:
                    r += (compliance / 100.0 - 0.9) * 0.8
                # The personal component and the noise are deliberately large
                # relative to the measures. A cycle where ratings track
                # utilisation at r=0.8 is not a performance process, it is a
                # utilisation report with names on it, and no calibrated
                # human process is that tight. The correlation should be
                # clearly present and clearly incomplete, which is the shape
                # that makes the question worth asking.
                r += trait[eid] * 1.7 + rng.uniform(-0.65, 0.65)
                r = max(1.0, min(5.0, round(r * 4) / 4))

                training = q2(rng.uniform(4, 58))
                certs = rng.choices([0, 1, 2, 3], weights=[.42, .34, .18, .06])[0]
                flight = ("high" if (r >= 4.0 and rng.random() < 0.22) or
                          (r <= 2.4 and rng.random() < 0.3) else
                          "medium" if rng.random() < 0.24 else "low")
                product = cur.execute("""
                    SELECT p.product FROM time_entry t JOIN project p
                           ON p.project_id=t.project_id
                     WHERE t.employee_id=? GROUP BY p.product
                     ORDER BY SUM(t.hours) DESC LIMIT 1""",
                    (eid,)).fetchone()
                product = product[0] if product else "the core product"

                cur.execute("""
                    INSERT INTO performance_review
                      (cycle_id, employee_id, reviewer_employee_id,
                       overall_rating, rating_label, utilization_pct,
                       utilization_target_pct, realised_rate, csat_avg,
                       projects_delivered, on_time_pct,
                       timesheet_compliance_pct, certifications,
                       training_hours, strengths, development, goals,
                       manager_comment, flight_risk, promotion_ready,
                       is_calibrated, submitted_on)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cycle_id, eid, mgr, r, rating_label(r), q2(util),
                     target, bill, q2(csat) if csat else None, delivered,
                     q2(on_time) if on_time is not None else None,
                     q2(compliance) if compliance is not None else None,
                     certs, training,
                     rng.choice(STRENGTHS).format(product=product),
                     rng.choice(DEVELOPMENT).format(product=product),
                     rng.choice(GOALS).format(product=product),
                     rng.choice(MANAGER_COMMENTS),
                     flight,
                     1 if r >= 4.0 and rng.random() < 0.35 else 0,
                     1 if ci < 2 else (1 if rng.random() < 0.6 else 0),
                     (end + timedelta(days=rng.randint(5, 30))).isoformat()))
                n_reviews += 1
    con.commit()
    return len(cycles), n_reviews


# ---------------------------------------------------------------------------
def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    rng = random.Random(4711)
    today = date.fromisoformat(
        cur.execute("SELECT MAX(entry_date) FROM time_entry").fetchone()[0])

    for t in ("csat_response", "hypercare_period", "product_ticket",
              "performance_review", "performance_cycle"):
        cur.execute(f"DELETE FROM {t}")
    con.commit()

    # Order matters: CSAT scores read the hypercare overrun and the defect
    # count, and the performance reviews read the CSAT.
    n_hc = seed_hypercare(con, cur, rng, today)
    n_tk = seed_tickets(con, cur, rng, today)
    n_cs = seed_csat(con, cur, rng, today)
    n_cy, n_rv = seed_performance(con, cur, rng, today)

    print(f"hypercare periods: {n_hc}")
    print(f"product tickets:   {n_tk}")
    print(f"csat responses:    {n_cs}")
    print(f"performance:       {n_cy} cycles per org, {n_rv} reviews")
    con.close()


if __name__ == "__main__":
    main()
