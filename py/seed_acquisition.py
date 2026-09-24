"""
Acquisition intake layer for the target org (CCG).

Two jobs:

1. Inject the data defects a real legacy PSA extract arrives with. These are
   put into the data on purpose so the validation and data-quality engines
   have something genuine to find. Nothing here is invented at report time -
   every finding the UI shows is a query result against these rows.

2. Record the intake itself: the eight standard templates, one import batch
   per template, staging rows for anything that failed, the terminology
   mapping, and the migration run with its lineage back to the legacy IDs.
"""
import json
import os
import random
import sqlite3
from datetime import date, timedelta

import dq_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
RNG = random.Random(4242)
TODAY = date(2026, 8, 31)
LEGACY_SYSTEM = "ABC PSA (legacy)"

# ---------------------------------------------------------------------------
# The eight standard templates, exactly as specified in the requirements.
# Held as data so the download, the validator and the mapping screen all read
# one definition instead of three hard-coded copies.
# ---------------------------------------------------------------------------
TEMPLATES = [
    ("CUSTOMERS", "Customers", "customer", 0, [
        ("Customer ID", "legacy_customer_id", "text", 1, 1, None, None),
        ("Customer Name", "customer_name", "text", 1, 0, None, None),
        ("Customer Type", "customer_type", "enum", 0, 0,
         "Public Sector|Nonprofit|Commercial", None),
        ("Industry", "industry", "text", 0, 0, None, None),
        ("Region", "region", "text", 0, 0, None, None),
        ("Account Owner", "account_owner", "text", 0, 0, None, None),
        ("Customer Status", "customer_status", "enum", 1, 0,
         "Prospect|Active|At Risk|Churned|Dormant", None),
        ("Contract Start", "contract_start", "date", 0, 0, None, None),
        ("Contract End", "contract_end", "date", 0, 0, None, None),
        ("Annual Revenue", "annual_revenue", "decimal", 0, 0, None, None),
        ("Lifetime Revenue", "lifetime_revenue", "decimal", 0, 0, None, None),
    ]),
    ("PROJECTS", "Projects", "project", 0, [
        ("Legacy Project ID", "legacy_project_id", "text", 1, 1, None, None),
        ("Customer", "customer_id", "text", 1, 0, None, "CUSTOMERS"),
        ("Project Name", "project_name", "text", 1, 0, None, None),
        ("Project Type", "project_type", "text", 0, 0, None, None),
        ("Product", "product", "text", 0, 0, None, None),
        ("Project Manager", "project_manager_id", "text", 1, 0, None, "EMPLOYEES"),
        ("Start Date", "start_date", "date", 1, 0, None, None),
        ("Planned End Date", "planned_end_date", "date", 1, 0, None, None),
        ("Actual End Date", "actual_end_date", "date", 0, 0, None, None),
        ("Contract Value", "contract_value", "decimal", 1, 0, None, None),
        ("SOW Value", "sow_value", "decimal", 0, 0, None, None),
        ("Budget Hours", "budget_hours", "decimal", 1, 0, None, None),
        ("Actual Hours", None, "decimal", 0, 0, None, None),
        ("Budget Cost", "budget_cost", "decimal", 0, 0, None, None),
        ("Actual Cost", None, "decimal", 0, 0, None, None),
        ("Revenue", "revenue_recognized", "decimal", 0, 0, None, None),
        ("Project Status", "project_status", "enum", 1, 0,
         "Not Started|In Flight|On Hold|Complete|Cancelled", None),
        ("Project Health", "project_health", "enum", 0, 0, "Green|Yellow|Red", None),
        ("Go-Live Date", "go_live_date", "date", 0, 0, None, None),
    ]),
    ("EMPLOYEES", "Employees / Resources", "employee", 0, [
        ("Employee ID", "legacy_employee_id", "text", 1, 1, None, None),
        ("Employee Name", "employee_name", "text", 1, 0, None, None),
        ("Role", "role", "text", 1, 0, None, None),
        ("Department", "department", "text", 0, 0, None, None),
        ("Practice", "practice_id", "text", 0, 0, None, None),
        ("Location", "location", "text", 0, 0, None, None),
        ("Employment Type", "employment_type", "enum", 0, 0,
         "Full Time|Part Time|Contractor|Intern", None),
        ("Cost Rate", "cost_rate", "decimal", 1, 0, None, None),
        ("Billing Rate", "billing_rate", "decimal", 1, 0, None, None),
        ("Capacity", "weekly_capacity_hours", "decimal", 0, 0, None, None),
        ("Utilization Target", "utilization_target_pct", "decimal", 0, 0, None, None),
        ("Skills", None, "text", 0, 0, None, None),
        ("Manager", "manager_employee_id", "text", 0, 0, None, "EMPLOYEES"),
        ("Start Date", "start_date", "date", 0, 0, None, None),
    ]),
    ("TIME_ENTRIES", "Time Entries", "time_entry", 24, [
        ("Employee ID", "employee_id", "text", 1, 0, None, "EMPLOYEES"),
        ("Employee Name", None, "text", 0, 0, None, None),
        ("Project ID", "project_id", "text", 1, 0, None, "PROJECTS"),
        ("Project Name", None, "text", 0, 0, None, None),
        ("Task", "task_id", "text", 0, 0, None, None),
        ("Date", "entry_date", "date", 1, 0, None, None),
        ("Hours", "hours", "decimal", 1, 0, None, None),
        ("Billable / Non-Billable", "is_billable", "enum", 1, 0,
         "Billable|Non-Billable", None),
        ("Time Category", "time_category", "text", 0, 0, None, None),
        ("Approval Status", "approval_status", "enum", 0, 0,
         "Draft|Submitted|Approved|Rejected", None),
    ]),
    ("PROJECT_FINANCIALS", "Project Financials", "project_financial_month", 24, [
        ("Project ID", "project_id", "text", 1, 0, None, "PROJECTS"),
        ("Month", "period_month", "text", 1, 0, None, None),
        ("Contract Value", "contract_value", "decimal", 0, 0, None, None),
        ("Invoiced Revenue", "invoiced_revenue", "decimal", 0, 0, None, None),
        ("Recognized Revenue", "recognized_revenue", "decimal", 1, 0, None, None),
        ("Labor Cost", "labor_cost", "decimal", 1, 0, None, None),
        ("Other Cost", "other_cost", "decimal", 0, 0, None, None),
        ("Total Cost", None, "decimal", 0, 0, None, None),
        ("Gross Profit", None, "decimal", 0, 0, None, None),
        ("Gross Margin %", None, "decimal", 0, 0, None, None),
        ("Budget", "budget_amount", "decimal", 0, 0, None, None),
        ("Forecast", "forecast_amount", "decimal", 0, 0, None, None),
        ("Actual", "actual_amount", "decimal", 0, 0, None, None),
    ]),
    ("TASKS_MILESTONES", "Project Tasks / Milestones", "project_task", 0, [
        ("Project ID", "project_id", "text", 1, 0, None, "PROJECTS"),
        ("Task ID", "legacy_task_id", "text", 1, 1, None, None),
        ("Task Name", "task_name", "text", 1, 0, None, None),
        ("Phase", "phase", "text", 0, 0, None, None),
        ("Planned Start", "planned_start", "date", 1, 0, None, None),
        ("Planned End", "planned_end", "date", 1, 0, None, None),
        ("Actual Start", "actual_start", "date", 0, 0, None, None),
        ("Actual End", "actual_end", "date", 0, 0, None, None),
        ("Planned Hours", "planned_hours", "decimal", 0, 0, None, None),
        ("Actual Hours", "actual_hours", "decimal", 0, 0, None, None),
        ("Status", "status", "enum", 1, 0,
         "Not Started|In Progress|Complete|Blocked|Cancelled", None),
        ("Percent Complete", "percent_complete", "decimal", 0, 0, None, None),
    ]),
    ("RISKS_ISSUES", "Risks & Issues", "risk_issue", 0, [
        ("Project ID", "project_id", "text", 1, 0, None, "PROJECTS"),
        ("Risk/Issue ID", "legacy_risk_id", "text", 1, 1, None, None),
        ("Type", "entry_type", "enum", 1, 0, "Risk|Issue|Assumption|Dependency", None),
        ("Description", "description", "text", 1, 0, None, None),
        ("Date Identified", "date_identified", "date", 1, 0, None, None),
        ("Owner", "owner", "text", 0, 0, None, None),
        ("Priority", "priority", "enum", 0, 0, "Low|Medium|High|Critical", None),
        ("Impact", "impact", "integer", 0, 0, None, None),
        ("Probability", "probability", "integer", 0, 0, None, None),
        ("Status", "status", "enum", 1, 0, "Open|Mitigating|Closed|Accepted", None),
        ("Resolution", "resolution", "text", 0, 0, None, None),
        ("Due Date", "due_date", "date", 0, 0, None, None),
    ]),
    ("CONTRACTS_SOWS", "Contracts / SOWs", "contract", 0, [
        ("Customer", "customer_id", "text", 1, 0, None, "CUSTOMERS"),
        ("Project", None, "text", 0, 0, None, "PROJECTS"),
        ("Contract ID", "legacy_contract_id", "text", 1, 1, None, None),
        ("SOW ID", "legacy_sow_id", "text", 0, 0, None, None),
        ("Contract Type", "contract_type", "enum", 0, 0,
         "MSA|SOW|Subscription|Retainer|Support", None),
        ("Contract Value", "contract_value", "decimal", 1, 0, None, None),
        ("Sold Hours", "sold_hours", "decimal", 0, 0, None, None),
        ("Start Date", "start_date", "date", 1, 0, None, None),
        ("End Date", "end_date", "date", 0, 0, None, None),
        ("Billing Model", "billing_model", "enum", 1, 0,
         "Fixed Fee|Time and Materials|Milestone|Capped T&M|Retainer", None),
        ("Payment Terms", "payment_terms", "text", 0, 0, None, None),
        ("Change Order Value", None, "decimal", 0, 0, None, None),
        ("Remaining Value", "remaining_value", "decimal", 0, 0, None, None),
    ]),
]

# Legacy vocabularies the target used, and what they map to (req 29).
MAPPINGS = {
    "time_category": [
        ("Consulting Hours", "Consulting", 98, "suggested"),
        ("Config Work", "Configuration", 92, "suggested"),
        ("QA / Test", "Testing", 95, "suggested"),
        ("Client Training", "Training", 96, "suggested"),
        ("PM Admin", "Project Management", 88, "suggested"),
        ("Conversion", "Data Migration", 84, "suggested"),
        ("Fix / Redo", "Rework", 90, "suggested"),
        ("Travel Time", "Travel", 99, "exact"),
        ("Goodwill", "Over-service", 62, "manual"),
        ("Bench", "Internal", 71, "manual"),
        ("Misc", None, 0, "unmapped"),
    ],
    "project_type": [
        ("New Install", "Implementation", 94, "suggested"),
        ("Version Uplift", "Upgrade", 93, "suggested"),
        ("Data Conversion", "Migration", 89, "suggested"),
        ("Interface Build", "Integration", 91, "suggested"),
        ("Consulting Engagement", "Advisory", 86, "suggested"),
        ("Support Block", "Managed Service", 79, "manual"),
    ],
    "billing_model": [
        ("Firm Price", "Fixed Fee", 96, "suggested"),
        ("T&E", "Time and Materials", 97, "suggested"),
        ("Stage Payment", "Milestone", 90, "suggested"),
        ("T&E with Cap", "Capped T&M", 95, "suggested"),
        ("Monthly Block", "Retainer", 88, "suggested"),
    ],
    "project_status": [
        ("Live", "In Flight", 92, "suggested"),
        ("Signed - Not Mobilised", "Not Started", 90, "suggested"),
        ("Paused", "On Hold", 94, "suggested"),
        ("Closed", "Complete", 88, "suggested"),
        ("Killed", "Cancelled", 91, "suggested"),
    ],
    "health": [
        ("On Track", "Green", 99, "exact"),
        ("Watch", "Yellow", 93, "suggested"),
        ("Escalated", "Red", 95, "suggested"),
        ("Unknown", None, 0, "unmapped"),
    ],
    "role": [
        ("Principal", "Practice Director", 87, "suggested"),
        ("Lead Architect", "Solution Architect", 94, "suggested"),
        ("Engagement Manager", "Project Manager", 89, "suggested"),
        ("Snr Consultant", "Senior Consultant", 98, "suggested"),
        ("Consultant Grade 2", "Consultant", 92, "suggested"),
        ("Developer", "Technical Consultant", 83, "manual"),
        ("Analyst", "Business Analyst", 95, "suggested"),
    ],
    "currency": [
        ("CAD", "CAD", 100, "exact"),
        ("USD", "CAD", 100, "exact"),
        ("C$", "CAD", 99, "suggested"),
    ],
}


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    ccg = cur.execute("SELECT org_id FROM org WHERE org_code='CCG'").fetchone()[0]

    defects = inject_defects(cur, ccg)
    con.commit()
    print("injected defects:", json.dumps(defects, indent=None))

    template_ids = seed_templates(cur)
    con.commit()

    seed_mappings(cur, ccg)
    seed_batches_and_findings(cur, ccg, template_ids)
    seed_migration(cur, ccg)
    con.commit()

    for label, sql in (
            ("import_batch", "SELECT COUNT(*) FROM import_batch"),
            ("staging rows", "SELECT COUNT(*) FROM import_staging_row"),
            ("validation findings", "SELECT COUNT(*) FROM validation_finding"),
            ("mapping rules", "SELECT COUNT(*) FROM mapping_rule"),
            ("lineage rows", "SELECT COUNT(*) FROM migration_lineage")):
        print(f"  {label}: {cur.execute(sql).fetchone()[0]}")
    con.close()


# ---------------------------------------------------------------------------
def inject_defects(cur, org_id):
    """Make the legacy extract realistically broken."""
    out = {}

    emps = [r[0] for r in cur.execute(
        "SELECT employee_id FROM employee WHERE org_id=? ORDER BY employee_id", (org_id,))]
    prjs = [r[0] for r in cur.execute(
        "SELECT project_id FROM project WHERE org_id=? ORDER BY project_id", (org_id,))]
    custs = cur.execute(
        "SELECT customer_id, customer_code, customer_name, legacy_customer_id, industry,"
        " region, geography, account_owner FROM customer WHERE org_id=? ORDER BY customer_id",
        (org_id,)).fetchall()

    # 1. missing cost rates
    picks = RNG.sample(emps, 6)
    cur.executemany("UPDATE employee SET cost_rate=NULL WHERE employee_id=?",
                    [(e,) for e in picks])
    out["missing_cost_rate"] = len(picks)

    # 2. missing billing rates
    picks = RNG.sample([e for e in emps if e not in picks], 4)
    cur.executemany("UPDATE employee SET billing_rate=NULL WHERE employee_id=?",
                    [(e,) for e in picks])
    out["missing_billing_rate"] = len(picks)

    # 3. missing project manager
    picks = RNG.sample(prjs, 11)
    cur.executemany("UPDATE project SET project_manager_id=NULL WHERE project_id=?",
                    [(p,) for p in picks])
    out["missing_project_manager"] = len(picks)

    # 4. duplicate customers, arriving as separate legacy records
    dup_specs = [(custs[0], "Cobalt Logistics Grp."), (custs[2], "Verdant Agri-Foods"),
                 (custs[5], "Marlow Retail Grp")]
    n = 0
    for src, alt_name in dup_specs:
        cur.execute(
            """INSERT INTO customer(org_id, legacy_customer_id, customer_code,
                    customer_name, customer_type, industry, region, geography,
                    account_owner, customer_status)
               VALUES (?,?,?,?,?,?,?,?,?,'Active')""",
            (org_id, f"{src[3]}-DUP", f"{src[1]}D", alt_name, "Commercial",
             src[4], src[5], src[6], src[7]))
        n += 1
    out["duplicate_customers"] = n

    # 5. duplicate employees, same person under two legacy IDs
    dup_emps = cur.execute(
        """SELECT employee_code, employee_name, role, department, practice_id, location,
                  geography, employment_type, cost_rate, billing_rate, legacy_employee_id
             FROM employee WHERE org_id=? ORDER BY employee_id LIMIT 3""", (org_id,)).fetchall()
    for e in dup_emps:
        cur.execute(
            """INSERT INTO employee(org_id, legacy_employee_id, employee_code,
                    employee_name, role, department, practice_id, location, geography,
                    employment_type, cost_rate, billing_rate, weekly_capacity_hours,
                    utilization_target_pct, start_date, is_billable, is_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,37.5,75.0,NULL,1,0)""",
            (org_id, f"{e[10]}-B", f"{e[0]}B", e[1], e[2], e[3], e[4], e[5], e[6],
             e[7], e[8], e[9]))
    out["duplicate_employees"] = len(dup_emps)

    # 6. invalid dates: planned end before start
    picks = RNG.sample(prjs, 7)
    for p in picks:
        cur.execute(
            """UPDATE project
                  SET planned_end_date = date(start_date, '-' || ? || ' days')
                WHERE project_id=?""", (RNG.randint(20, 120), p))
    out["planned_end_before_start"] = len(picks)

    # 7. go-live before project start
    picks = RNG.sample([p for p in prjs if p not in picks], 5)
    for p in picks:
        cur.execute("""UPDATE project SET go_live_date = date(start_date, '-45 days')
                        WHERE project_id=?""", (p,))
    out["go_live_before_start"] = len(picks)

    # 8. negative and zero financial values
    picks = RNG.sample(prjs, 4)
    cur.executemany("UPDATE project SET contract_value = -contract_value WHERE project_id=?",
                    [(p,) for p in picks])
    out["negative_contract_value"] = len(picks)
    picks2 = RNG.sample([p for p in prjs if p not in picks], 14)
    cur.executemany("UPDATE project SET budget_hours = 0 WHERE project_id=?",
                    [(p,) for p in picks2])
    out["missing_budget_hours"] = len(picks2)

    # 9. currency inconsistency on a handful of contracts
    ctrs = [r[0] for r in cur.execute(
        "SELECT contract_id FROM contract WHERE org_id=? ORDER BY contract_id", (org_id,))]
    picks = RNG.sample(ctrs, min(6, len(ctrs)))
    cur.executemany("UPDATE contract SET currency='USD' WHERE contract_id=?",
                    [(c,) for c in picks])
    out["currency_inconsistent_contracts"] = len(picks)

    # 10. impossible time entries
    tes = [r[0] for r in cur.execute(
        """SELECT time_entry_id FROM time_entry WHERE org_id=? AND project_id IS NOT NULL
            ORDER BY time_entry_id LIMIT 4000""", (org_id,))]
    picks = RNG.sample(tes, 22)
    cur.executemany("UPDATE time_entry SET hours = hours + 24 WHERE time_entry_id=?",
                    [(t,) for t in picks])
    out["hours_over_24"] = len(picks)
    picks = RNG.sample([t for t in tes if t not in picks], 9)
    cur.executemany("UPDATE time_entry SET hours = -hours WHERE time_entry_id=?",
                    [(t,) for t in picks])
    out["negative_hours"] = len(picks)

    # 11. duplicate projects: same customer, name and dates under two legacy IDs
    src = cur.execute(
        """SELECT org_id, legacy_project_id, project_code, project_name, customer_id,
                  contract_id, sow_id, practice_id, project_type, product, methodology,
                  project_manager_id, billing_model, start_date, planned_end_date,
                  actual_end_date, contract_value, sow_value, budget_hours, budget_cost,
                  project_status, project_health, target_margin_pct
             FROM project WHERE org_id=? AND project_status='Complete'
            ORDER BY project_id LIMIT 3""", (org_id,)).fetchall()
    for r in src:
        cur.execute(
            """INSERT INTO project(org_id, legacy_project_id, project_code, project_name,
                    customer_id, contract_id, sow_id, practice_id, project_type, product,
                    methodology, project_manager_id, billing_model, start_date,
                    planned_end_date, actual_end_date, contract_value, sow_value,
                    budget_hours, budget_cost, project_status, project_health,
                    target_margin_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r[0], f"{r[1]}-DUP", f"{r[2]}D", r[3], r[4], r[5], r[6], r[7], r[8], r[9],
             r[10], r[11], r[12], r[13], r[14], r[15], r[16], r[17], r[18], r[19],
             r[20], r[21], r[22]))
    out["duplicate_projects"] = len(src)

    # 12. missing time: assigned resources with no entries at all on a project
    gaps = cur.execute(
        """SELECT a.project_id, a.employee_id FROM assignment a
            JOIN project p ON p.project_id=a.project_id
           WHERE p.org_id=? AND p.project_status='Complete'
           ORDER BY a.assignment_id LIMIT 400""", (org_id,)).fetchall()
    picks = RNG.sample(gaps, 26)
    for pid, eid in picks:
        cur.execute("""DELETE FROM time_entry WHERE project_id=? AND employee_id=?""",
                    (pid, eid))
    out["assignments_with_no_time"] = len(picks)

    return out


# ---------------------------------------------------------------------------
def seed_templates(cur):
    ids = {}
    for order, (code, name, target, min_hist, fields) in enumerate(TEMPLATES, start=1):
        cur.execute(
            """INSERT INTO import_template(template_code, template_name, target_table,
                    sort_order, min_history_months, description)
               VALUES (?,?,?,?,?,?)""",
            (code, name, target, order, min_hist,
             f"Standard {name} intake template. "
             + (f"Requires at least {min_hist} months of history where available."
                if min_hist else "One row per record.")))
        tid = cur.lastrowid
        ids[code] = tid
        for ci, (header, target_col, dtype, mand, key, enum, fk) in enumerate(fields, 1):
            cur.execute(
                """INSERT INTO import_template_field(template_id, column_order,
                        column_header, target_column, data_type, is_mandatory, is_key,
                        enum_values, fk_template_code, min_value)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (tid, ci, header, target_col, dtype, mand, key, enum, fk,
                 0 if (dtype == "decimal" and header in
                       ("Contract Value", "Budget Hours", "Hours", "Cost Rate",
                        "Billing Rate", "Labor Cost")) else None))
    return ids


def seed_mappings(cur, org_id):
    """
    Mapping rules with real occurrence counts. The count is how many rows in
    the loaded data carry the mapped target value, so an unmapped legacy term
    is visibly unresolved rather than silently dropped.
    """
    counts = {
        "time_category": dict(cur.execute(
            """SELECT time_category, COUNT(*) FROM time_entry WHERE org_id=?
                GROUP BY 1""", (org_id,)).fetchall()),
        "project_type": dict(cur.execute(
            "SELECT project_type, COUNT(*) FROM project WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
        "billing_model": dict(cur.execute(
            "SELECT billing_model, COUNT(*) FROM project WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
        "project_status": dict(cur.execute(
            "SELECT project_status, COUNT(*) FROM project WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
        "health": dict(cur.execute(
            "SELECT project_health, COUNT(*) FROM project WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
        "role": dict(cur.execute(
            "SELECT role, COUNT(*) FROM employee WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
        "currency": dict(cur.execute(
            "SELECT currency, COUNT(*) FROM contract WHERE org_id=? GROUP BY 1",
            (org_id,)).fetchall()),
    }
    for dimension, rules in MAPPINGS.items():
        cur.execute(
            """INSERT INTO mapping_set(org_id, set_name, dimension)
               VALUES (?,?,?)""",
            (org_id, f"{LEGACY_SYSTEM} -> PSA", dimension))
        sid = cur.lastrowid
        for legacy_value, target_value, conf, source in rules:
            occ = counts.get(dimension, {}).get(target_value, 0) if target_value else 0
            cur.execute(
                """INSERT INTO mapping_rule(mapping_set_id, legacy_value, target_value,
                        confidence_pct, match_source, occurrence_count, approved)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, legacy_value, target_value, conf, source, occ,
                 1 if source in ("exact", "suggested") and target_value else 0))


def seed_batches_and_findings(cur, org_id, template_ids):
    """
    One batch per template. Row counts come from the loaded data; findings come
    from the defects actually present, so the validation report and the data
    itself cannot drift apart.
    """
    uploaded = date(2026, 7, 22)

    for code, name, target, _mh, _f in TEMPLATES:
        tid = template_ids[code]
        row_count = cur.execute(_ROW_COUNT_SQL[code], (org_id,)).fetchone()[0]
        findings = []
        for rule in dq_rules.rules_for_template(code):
            for row in cur.execute(rule["sql"], (org_id,)).fetchall():
                findings.append((rule["rule_code"], rule["severity"], rule["column"],
                                 None if row[1] is None else str(row[1]),
                                 rule["message"].format(label=row[0]),
                                 rule["recommendation"], row[0]))
        errors = sum(1 for f in findings if f[1] == "error")
        warnings = sum(1 for f in findings if f[1] == "warning")
        status = "Failed Validation" if errors else ("Validated" if warnings else "Committed")
        cur.execute(
            """INSERT INTO import_batch(org_id, template_id, file_name, uploaded_by,
                    uploaded_at, row_count, valid_count, warning_count, error_count,
                    attempt_no, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (org_id, tid, f"CCG_{code.lower()}_2024-08_2026-07.xlsx",
             "ABC Company (Finance)",
             uploaded.isoformat(), row_count,
             max(0, row_count - errors - warnings), warnings, errors,
             1, status))
        bid = cur.lastrowid
        # staging rows only for records that failed, plus a small clean sample
        for i, (rule_code, severity, column, observed, message, rec, label) in \
                enumerate(findings, start=1):
            cur.execute(
                """INSERT INTO import_staging_row(batch_id, source_row_no, legacy_key,
                        payload_json, row_status)
                   VALUES (?,?,?,?,?)""",
                (bid, i, str(label),
                 json.dumps({"record": str(label), column or "": observed}),
                 "Error" if severity == "error" else "Warning"))
            srid = cur.lastrowid
            cur.execute(
                """INSERT INTO validation_finding(batch_id, staging_row_id, severity,
                        rule_code, column_header, observed_value, message, recommendation)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (bid, srid, severity, rule_code, column, observed, message, rec))


_ROW_COUNT_SQL = {
    "CUSTOMERS": "SELECT COUNT(*) FROM customer WHERE org_id=?",
    "PROJECTS": "SELECT COUNT(*) FROM project WHERE org_id=?",
    "EMPLOYEES": "SELECT COUNT(*) FROM employee WHERE org_id=?",
    "TIME_ENTRIES": "SELECT COUNT(*) FROM time_entry WHERE org_id=?",
    "PROJECT_FINANCIALS": """SELECT COUNT(*) FROM project_financial_month f
                              JOIN project p ON p.project_id=f.project_id WHERE p.org_id=?""",
    "TASKS_MILESTONES": """SELECT COUNT(*) FROM project_task t
                             JOIN project p ON p.project_id=t.project_id WHERE p.org_id=?""",
    "RISKS_ISSUES": """SELECT COUNT(*) FROM risk_issue r
                         JOIN project p ON p.project_id=r.project_id WHERE p.org_id=?""",
    "CONTRACTS_SOWS": "SELECT COUNT(*) FROM contract WHERE org_id=?",
}


def seed_migration(cur, org_id):
    """
    Migration run and lineage. Every migrated record keeps its legacy ID so it
    can be traced back to the acquired system after go-live.
    """
    cur.execute(
        """INSERT INTO migration_run(org_id, approved_by, approved_at, stage,
                notes)
           VALUES (?,?,?,?,?)""",
        (org_id, None, None, "Validated",
         "Assessment complete. Awaiting management approval to migrate. "
         "Data quality scores are recalculated by the intelligence engine on "
         "each run, not stored by hand."))
    run_id = cur.lastrowid
    rows = []
    for table, id_col, legacy_col in (
            ("customer", "customer_id", "legacy_customer_id"),
            ("employee", "employee_id", "legacy_employee_id"),
            ("project", "project_id", "legacy_project_id"),
            ("contract", "contract_id", "legacy_contract_id")):
        for pk, legacy in cur.execute(
                f"""SELECT {id_col}, {legacy_col} FROM {table}
                     WHERE org_id=? AND {legacy_col} IS NOT NULL""", (org_id,)):
            rows.append((run_id, table, pk, LEGACY_SYSTEM, legacy, None))
    cur.executemany(
        """INSERT INTO migration_lineage(migration_run_id, entity_table, entity_pk,
                legacy_system, legacy_id, legacy_payload_json)
           VALUES (?,?,?,?,?,?)""", rows)


if __name__ == "__main__":
    main()
