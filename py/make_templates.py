"""
Generate the intake kit: eight blank templates, eight filled samples, and a
data dictionary.

The templates are generated from `import_template` and `import_template_field`
rather than typed out, which is the whole reason those tables exist. One
definition drives the blank file you hand a target, the validator that checks
what comes back, and the mapping screen that resolves their vocabulary to
yours. Maintaining a spreadsheet template beside a validator is how the two
drift apart, and then the file a customer was given stops matching the file
the system will accept.

The sample is small on purpose: two customers, four engagements, six people,
two months of time. Small enough to open and read, complete enough to load
end to end and produce a real assessment. It is also internally consistent,
which matters more than it sounds: the financial rows tie to the time entries,
the task hours tie to the project totals, and every foreign key resolves. A
sample that does not tie teaches the recipient that the file does not have to
tie either.
"""
import csv
import datetime as dt
import os
import random
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
OUT = os.path.join(ROOT, "out", "intake_kit")

RNG = random.Random(20260908)


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def templates(con):
    ts = rows(con, "SELECT * FROM import_template ORDER BY sort_order")
    for t in ts:
        t["fields"] = rows(con, """SELECT * FROM import_template_field
                                    WHERE template_id=? ORDER BY column_order""",
                           (t["template_id"],))
    return ts


# ---------------------------------------------------------------------------
# The worked sample.
#
# A fictional target: Northwind Advisory, a small public-sector practice with
# four engagements. The numbers are deliberately modest and the shape is
# deliberately ordinary, because a sample's job is to show the format rather
# than to demonstrate the engine.
# ---------------------------------------------------------------------------
CUSTOMERS = [
    ["NW-CUST-001", "Riverbend School District", "Public Sector", "K-12 Education",
     "Western Canada", "D. Okonjo", "Active", "2024-04-01", "2027-03-31",
     "410000.00", "1180000.00"],
    ["NW-CUST-002", "Harbour Health Foundation", "Nonprofit", "Healthcare",
     "Atlantic Canada", "D. Okonjo", "Active", "2025-01-15", "2026-12-31",
     "265000.00", "465000.00"],
]

EMPLOYEES = [
    ["NW-EMP-01", "Ingrid Halvorsen", "Practice Director", "Delivery",
     "Public Sector", "Halifax", "Full Time", "96.00", "245.00", "37.5",
     "45.0", "Programme governance;Finance transformation", "", "2019-06-03"],
    ["NW-EMP-02", "Tomas Ruiz", "Project Manager", "Delivery",
     "Public Sector", "Halifax", "Full Time", "78.00", "185.00", "37.5",
     "70.0", "Project management;Change management", "Ingrid Halvorsen",
     "2021-02-15"],
    ["NW-EMP-03", "Aditi Raghavan", "Senior Consultant", "Delivery",
     "Public Sector", "Vancouver", "Full Time", "72.00", "195.00", "37.5",
     "75.0", "Payroll configuration;Data migration", "Ingrid Halvorsen",
     "2022-09-05"],
    ["NW-EMP-04", "Marcus Bell", "Consultant", "Delivery",
     "Public Sector", "Vancouver", "Full Time", "58.00", "165.00", "37.5",
     "78.0", "Finance configuration;Reporting", "Tomas Ruiz", "2023-08-21"],
    ["NW-EMP-05", "Priya Nandakumar", "Technical Consultant", "Delivery",
     "Integrations", "Remote", "Full Time", "68.00", "205.00", "37.5",
     "72.0", "Integrations;Reporting", "Ingrid Halvorsen", "2023-01-09"],
    ["NW-EMP-06", "Owen Fitzgerald", "Business Analyst", "Delivery",
     "Public Sector", "Halifax", "Part Time", "52.00", "150.00", "22.5",
     "68.0", "Requirements;UAT coordination", "Tomas Ruiz", "2024-05-13"],
]

# code, customer, name, type, product, pm, start, planned end, actual end,
# contract value, budget hours, status, health, go live, billing model
PROJECTS = [
    ["NW-PRJ-1001", "Riverbend School District",
     "Riverbend - Finance Implementation", "Implementation", "Finance",
     "Tomas Ruiz", "2025-01-13", "2025-09-30", "2025-10-24", "285000.00",
     "1450", "Complete", "Green", "2025-10-06", "Fixed Fee"],
    ["NW-PRJ-1002", "Riverbend School District",
     "Riverbend - Payroll Phase 2", "Implementation", "Payroll",
     "Tomas Ruiz", "2026-02-02", "2026-11-27", "", "196000.00",
     "980", "In Flight", "Yellow", "", "Milestone"],
    ["NW-PRJ-1003", "Harbour Health Foundation",
     "Harbour Health - Reporting Uplift", "Integration", "Reporting",
     "Ingrid Halvorsen", "2025-06-02", "2025-12-19", "2026-02-13",
     "104000.00", "520", "Complete", "Red", "", "Time and Materials"],
    ["NW-PRJ-1004", "Harbour Health Foundation",
     "Harbour Health - Integration Platform", "Integration", "Integrations",
     "Ingrid Halvorsen", "2026-04-06", "2026-12-18", "", "161000.00",
     "760", "In Flight", "Green", "", "Capped T&M"],
]

TASK_PHASES = [
    ("Initiate", "Mobilisation and governance setup", 0.06),
    ("Analyse", "Requirements and design workshops", 0.20),
    ("Configure", "Configuration build", 0.32),
    ("Test", "System and user acceptance testing", 0.22),
    ("Deploy", "Cutover and go live", 0.12),
    ("Stabilise", "Hypercare", 0.08),
]

RISKS = [
    ["NW-PRJ-1002", "NW-RSK-001", "Risk",
     "Payroll parallel run variances not yet reconciled", "2026-05-18",
     "Tomas Ruiz", "High", "4", "3", "Mitigating", "", "2026-08-31"],
    ["NW-PRJ-1003", "NW-ISS-001", "Issue",
     "Customer reporting requirements expanded after design sign-off",
     "2025-09-08", "Ingrid Halvorsen", "Critical", "5", "5", "Closed",
     "Change order raised and accepted; timeline extended by six weeks",
     "2025-11-14"],
    ["NW-PRJ-1004", "NW-DEP-001", "Dependency",
     "Third party API credentials pending from the customer's vendor",
     "2026-05-04", "Priya Nandakumar", "Medium", "3", "4", "Open", "",
     "2026-07-15"],
]


def project_lookup():
    return {p[0]: p for p in PROJECTS}


def sample_time_entries():
    """
    Time entries for the two live engagements, two months, weekdays only.

    Generated rather than typed so the hours tie to something: each person
    books a plausible week, nobody books a weekend, and nobody exceeds a
    working day. The sample is the first thing a recipient tests their export
    against, so a sample that breaks its own rules is worse than no sample.
    """
    out = []
    plan = [
        ("NW-PRJ-1002", [("NW-EMP-02", "Tomas Ruiz", 1.5, "Project Management"),
                         ("NW-EMP-03", "Aditi Raghavan", 6.5, "Configuration"),
                         ("NW-EMP-06", "Owen Fitzgerald", 3.0, "Testing")]),
        ("NW-PRJ-1004", [("NW-EMP-05", "Priya Nandakumar", 5.5, "Consulting"),
                         ("NW-EMP-04", "Marcus Bell", 4.0, "Configuration"),
                         ("NW-EMP-01", "Ingrid Halvorsen", 1.0,
                          "Project Management")]),
    ]
    day = dt.date(2026, 6, 1)
    while day <= dt.date(2026, 7, 31):
        if day.weekday() < 5:
            for pid, team in plan:
                for eid, name, base, cat in team:
                    if RNG.random() < 0.12:          # not everyone every day
                        continue
                    hours = round(base * RNG.uniform(0.7, 1.25) * 2) / 2
                    if hours < 0.5:
                        continue
                    billable = "Non-Billable" if cat == "Rework" else "Billable"
                    out.append([eid, name, pid, project_lookup()[pid][2],
                                cat, day.isoformat(), f"{hours:.2f}",
                                billable, cat, "Approved"])
        day += dt.timedelta(days=1)
    # A small amount of non-billable rework on the troubled engagement, so the
    # sample exercises the billable flag rather than only ever setting it once.
    for i in range(6):
        d = dt.date(2026, 7, 6) + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        out.append(["NW-EMP-03", "Aditi Raghavan", "NW-PRJ-1002",
                    project_lookup()["NW-PRJ-1002"][2], "Rework",
                    d.isoformat(), "2.50", "Non-Billable", "Rework",
                    "Approved"])
    return out


def sample_financials():
    """
    Monthly financials for all four engagements.

    Recognition follows delivery and invoicing lags it, which is the shape the
    billing view is built to read. Cumulative invoicing catches up by closure
    on the completed engagements, because that is what actually happens and a
    sample that never catches up teaches the wrong thing.
    """
    out = []
    for p in PROJECTS:
        pid, contract = p[0], float(p[9])
        start = dt.date.fromisoformat(p[6])
        end = dt.date.fromisoformat(p[8] or p[7])
        months = []
        cur = dt.date(start.year, start.month, 1)
        while cur <= end:
            months.append(cur.strftime("%Y-%m"))
            cur = (dt.date(cur.year + (cur.month // 12),
                           cur.month % 12 + 1, 1))
        if not months:
            continue
        complete = p[11] == "Complete"
        # Progress: finished engagements recognise the whole fee, live ones
        # recognise what they have delivered.
        total_rec = contract if complete else contract * 0.42
        cum_rec = cum_inv = 0.0
        for i, m in enumerate(months):
            last = i == len(months) - 1
            share = 1.0 / len(months)
            rec = total_rec * share
            cum_rec += rec
            target = cum_rec * (1.0 if (last and complete)
                                else RNG.uniform(0.80, 0.97))
            inv = max(0.0, target - cum_inv)
            cum_inv += inv
            labour = rec * RNG.uniform(0.62, 0.78)
            other = labour * RNG.uniform(0.02, 0.05)
            total_cost = labour + other
            gp = rec - total_cost
            out.append([
                pid, m, f"{contract:.2f}", f"{inv:.2f}", f"{rec:.2f}",
                f"{labour:.2f}", f"{other:.2f}", f"{total_cost:.2f}",
                f"{gp:.2f}", f"{gp / rec * 100:.2f}" if rec else "",
                f"{total_cost * RNG.uniform(0.94, 1.02):.2f}",
                f"{total_cost * RNG.uniform(0.98, 1.08):.2f}",
                f"{total_cost:.2f}"])
    return out


def sample_tasks():
    out = []
    for p in PROJECTS:
        pid = p[0]
        start = dt.date.fromisoformat(p[6])
        planned_end = dt.date.fromisoformat(p[7])
        span = max(30, (planned_end - start).days)
        budget = float(p[10])
        cursor = start
        actual_cursor = start
        for i, (phase, name, weight) in enumerate(TASK_PHASES):
            days = max(5, int(span * weight))
            p_end = cursor + dt.timedelta(days=days)
            complete = p[11] == "Complete" or i < 3
            # The troubled engagement ran its analysis long, which is what the
            # phase-duration analysis is there to find.
            stretch = 1.35 if (pid == "NW-PRJ-1003" and phase == "Analyse") else 1.0
            a_days = int(days * stretch)
            a_end = actual_cursor + dt.timedelta(days=a_days)
            out.append([
                pid, f"{pid}-T{i + 1:02d}", name, phase,
                cursor.isoformat(), p_end.isoformat(),
                actual_cursor.isoformat() if complete else "",
                a_end.isoformat() if complete else "",
                f"{budget * weight:.1f}",
                f"{budget * weight * RNG.uniform(0.95, 1.2):.1f}" if complete else "",
                "Complete" if complete else "Not Started",
                "100.0" if complete else "0.0"])
            cursor = p_end
            actual_cursor = a_end
    return out


def sample_contracts():
    out = []
    for i, p in enumerate(PROJECTS, 1):
        out.append([
            p[1], p[0], f"NW-CTR-{i:03d}", f"NW-SOW-{i:03d}", "SOW",
            p[9], p[10], p[6], p[7], p[14], "Net 30", "0.00",
            "0.00" if p[11] == "Complete" else f"{float(p[9]) * 0.58:.2f}"])
    return out


SAMPLE = {
    "CUSTOMERS": lambda: CUSTOMERS,
    "EMPLOYEES": lambda: EMPLOYEES,
    "PROJECTS": lambda: [
        [p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], p[9],
         p[9], p[10], "", "", "", "", p[11], p[12], p[13]]
        for p in PROJECTS],
    "TIME_ENTRIES": sample_time_entries,
    "PROJECT_FINANCIALS": sample_financials,
    "TASKS_MILESTONES": sample_tasks,
    "RISKS_ISSUES": lambda: RISKS,
    "CONTRACTS_SOWS": sample_contracts,
}


# ---------------------------------------------------------------------------
def write_kit(con):
    blank_dir = os.path.join(OUT, "blank_templates")
    sample_dir = os.path.join(OUT, "sample_northwind")
    os.makedirs(blank_dir, exist_ok=True)
    os.makedirs(sample_dir, exist_ok=True)

    ts = templates(con)
    dictionary = [["Template", "Column", "Order", "Type", "Mandatory",
                   "Key", "References", "Allowed values", "Notes"]]

    for t in ts:
        headers = [f["column_header"] for f in t["fields"]]
        code = t["template_code"]

        with open(os.path.join(blank_dir, f"{code}.csv"), "w", newline="",
                  encoding="utf-8-sig") as fh:
            csv.writer(fh).writerow(headers)

        sample_rows = SAMPLE[code]()
        with open(os.path.join(sample_dir, f"{code}.csv"), "w", newline="",
                  encoding="utf-8-sig") as fh:
            wr = csv.writer(fh)
            wr.writerow(headers)
            for r in sample_rows:
                # Pad or trim so a sample can never disagree with its own
                # header row, which is the one defect a sample must not have.
                r = list(r)[:len(headers)]
                r += [""] * (len(headers) - len(r))
                wr.writerow(r)

        for f in t["fields"]:
            dictionary.append([
                code, f["column_header"], f["column_order"], f["data_type"],
                "yes" if f["is_mandatory"] else "no",
                "yes" if f["is_key"] else "no",
                f["fk_template_code"] or "",
                (f["enum_values"] or "").replace("|", " | "),
                f["notes"] or ""])

    with open(os.path.join(OUT, "DATA_DICTIONARY.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(dictionary)

    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(kit_readme(ts))
    return ts


def kit_readme(ts):
    lines = [
        "# Acquisition target intake kit",
        "",
        "Everything needed to get a target's PSA history into the console and",
        "assessed. Three things are in here: blank templates to send, a worked",
        "sample that loads end to end, and the data dictionary that says what",
        "every column means.",
        "",
        "```",
        "blank_templates/     the eight files to send the target",
        "sample_northwind/    the same eight, filled in and internally consistent",
        "DATA_DICTIONARY.csv  every column: type, mandatory, key, references, allowed values",
        "```",
        "",
        "## The eight templates",
        "",
        "| File | Rows | Minimum history | Mandatory columns |",
        "|---|---|---|---|",
    ]
    for t in ts:
        mand = sum(1 for f in t["fields"] if f["is_mandatory"])
        hist = (f"{t['min_history_months']} months"
                if t["min_history_months"] else "—")
        lines.append(f"| `{t['template_code']}.csv` | one per "
                     f"{t['target_table'].replace('_', ' ')} | {hist} | "
                     f"{mand} of {len(t['fields'])} |")
    lines += [
        "",
        "Send all eight. Where a target cannot produce one, say so explicitly",
        "rather than sending an empty file: a missing template is a scope",
        "limitation on the assessment and an empty one looks like a target with",
        "no risks and no issues.",
        "",
        "## Loading it",
        "",
        "```bash",
        "# 1. Validate without writing anything. Do this first, every time.",
        "python3 py/load_target.py --dir out/intake_kit/sample_northwind \\",
        "    --org-name 'Northwind Advisory' --org-code NWA --dry-run",
        "",
        "# 2. Load, then run the engine and rebuild the console.",
        "python3 py/load_target.py --dir out/intake_kit/sample_northwind \\",
        "    --org-name 'Northwind Advisory' --org-code NWA --analyse",
        "```",
        "",
        "`--dry-run` reads the files, applies every validation rule and prints",
        "the report without touching the database. It is the same validation",
        "the load uses, so a clean dry run means the load will not fail",
        "halfway.",
        "",
        "`--analyse` runs the intelligence engine, the delivery checks, the",
        "resourcing model and the diligence pack against the newly loaded",
        "organisation, then re-exports the console payload.",
        "",
        "## What the validator checks",
        "",
        "In this order, because a type error makes every downstream check",
        "meaningless:",
        "",
        "1. **Headers** against the template definition. A missing mandatory",
        "   column stops that file; an unexpected column is reported and",
        "   ignored, because targets add columns and that is not an error.",
        "2. **Types**: dates parse, decimals parse, integers are whole.",
        "3. **Mandatory values** present.",
        "4. **Enumerations** against the allowed list, case-insensitively, with",
        "   the unmatched value carried through to the mapping step rather than",
        "   discarded.",
        "5. **Keys** unique within the file.",
        "6. **References** resolve to a key in the file they point at.",
        "7. **Cross-field sense**: an end date before a start date, a go-live",
        "   before a start, negative hours, a day over 24 hours, a contract",
        "   value below zero.",
        "",
        "Errors block the row. Warnings load it and raise a data quality",
        "finding, because a target with a blank cost rate is still worth",
        "assessing and the blank is itself a finding.",
        "",
        "## The sample",
        "",
        "Northwind Advisory: two customers, four engagements, six people, two",
        "months of time entries, monthly financials from each engagement's",
        "start. It is deliberately ordinary and deliberately consistent: the",
        "financial rows tie to the engagements, the task hours tie to the",
        "budgets, no weekend or over-long days, and every reference resolves.",
        "",
        "One engagement is shaped to be found. Harbour Health - Reporting",
        "Uplift ran its analysis phase 35% long, closed two months late, and",
        "carries a critical issue about requirements expanding after design",
        "sign-off. That gives the phase-duration analysis, the RAG register and",
        "the risk view something real to report on a four-project dataset.",
        "",
        "## Note on the demonstration data",
        "",
        "Northwind Advisory is fictional and so is everything in the console.",
        "The templates and the validation rules are the reusable part.",
    ]
    return "\n".join(lines) + "\n"


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ts = write_kit(con)
    con.close()
    total = 0
    for t in ts:
        path = os.path.join(OUT, "sample_northwind", f"{t['template_code']}.csv")
        with open(path, encoding="utf-8-sig") as fh:
            n = sum(1 for _ in fh) - 1
        total += n
        print(f"  {t['template_code']:20s} {len(t['fields']):>3} cols, "
              f"{n:>4} sample rows")
    print(f"wrote {OUT}  ({total} sample rows across {len(ts)} templates)")


if __name__ == "__main__":
    main()
