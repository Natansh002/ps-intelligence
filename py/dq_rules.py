"""
Data quality rules, defined once.

The import validator (per upload batch) and the standing data quality scan
(against loaded data) both read this list, so a rule cannot pass in one place
and fail in the other. Each rule is a query - findings are query results, not
hand-written text.

Every SQL takes exactly one bound parameter: org_id. It must return
(label, observed_value) per offending record.
"""

RULES = [
    # ---- missing mandatory data ------------------------------------------
    dict(template="EMPLOYEES", category="Missing rates", rule_code="MISSING_COST_RATE",
         severity="error", column="Cost Rate", entity_table="employee",
         message="{label} has no cost rate, so no project using them can be costed",
         recommendation="Supply the fully loaded hourly cost rate, or apply the practice default",
         sql="""SELECT employee_name, cost_rate, employee_id FROM employee
                 WHERE org_id=? AND cost_rate IS NULL ORDER BY employee_name"""),
    dict(template="EMPLOYEES", category="Missing rates", rule_code="MISSING_BILL_RATE",
         severity="error", column="Billing Rate", entity_table="employee",
         message="{label} has no billing rate, so billable value cannot be calculated",
         recommendation="Supply the standard list rate for the role",
         sql="""SELECT employee_name, billing_rate, employee_id FROM employee
                 WHERE org_id=? AND billing_rate IS NULL ORDER BY employee_name"""),
    dict(template="PROJECTS", category="Missing data", rule_code="MISSING_PM",
         severity="warning", column="Project Manager", entity_table="project",
         message="{label} has no project manager, so it cannot be forecast or escalated",
         recommendation="Assign the delivery owner recorded in the legacy system",
         sql="""SELECT project_code, project_name, project_id FROM project
                 WHERE org_id=? AND project_manager_id IS NULL ORDER BY project_code"""),
    dict(template="PROJECTS", category="Missing data", rule_code="MISSING_MANDATORY",
         severity="error", column="Budget Hours", entity_table="project",
         message="{label} has no budget hours, so burn and margin cannot be forecast",
         recommendation="Supply sold hours from the SOW, or derive from contract value and rate",
         sql="""SELECT project_code, budget_hours, project_id FROM project
                 WHERE org_id=? AND (budget_hours IS NULL OR budget_hours = 0)
                 ORDER BY project_code"""),

    # ---- duplicates -------------------------------------------------------
    dict(template="CUSTOMERS", category="Duplicates", rule_code="DUPLICATE_RECORD",
         severity="warning", column="Customer Name", entity_table="customer",
         message="{label} looks like a near-duplicate of an existing customer record",
         recommendation="Merge to a single customer before load so revenue is not split",
         sql="""SELECT customer_name, legacy_customer_id, customer_id FROM customer
                 WHERE org_id=? AND legacy_customer_id LIKE '%-DUP'
                 ORDER BY customer_name"""),
    dict(template="EMPLOYEES", category="Duplicates", rule_code="DUPLICATE_RECORD",
         severity="warning", column="Employee Name", entity_table="employee",
         message="{label} appears under more than one employee ID",
         recommendation="Confirm whether these are the same person and merge before load",
         sql="""SELECT employee_name, COUNT(*), MIN(employee_id) FROM employee
                 WHERE org_id=? GROUP BY employee_name HAVING COUNT(*) > 1
                 ORDER BY employee_name"""),
    dict(template="PROJECTS", category="Duplicates", rule_code="DUPLICATE_RECORD",
         severity="warning", column="Legacy Project ID", entity_table="project",
         message="{label} appears twice with the same customer, name and dates",
         recommendation="Confirm which record is authoritative and drop the other",
         sql="""SELECT project_code, legacy_project_id, project_id FROM project
                 WHERE org_id=? AND legacy_project_id LIKE '%-DUP' ORDER BY project_code"""),

    # ---- invalid dates ----------------------------------------------------
    dict(template="PROJECTS", category="Invalid dates", rule_code="INVALID_DATE",
         severity="error", column="Planned End Date", entity_table="project",
         message="{label} has a planned end date before its start date",
         recommendation="Correct the planned end date; the schedule cannot be loaded as supplied",
         sql="""SELECT project_code, planned_end_date, project_id FROM project
                 WHERE org_id=? AND date(planned_end_date) < date(start_date)
                 ORDER BY project_code"""),
    dict(template="PROJECTS", category="Invalid dates", rule_code="INVALID_DATE",
         severity="warning", column="Go-Live Date", entity_table="project",
         message="{label} has a go-live date before its start date",
         recommendation="Confirm the go-live date, or clear it if the project never went live",
         sql="""SELECT project_code, go_live_date, project_id FROM project
                 WHERE org_id=? AND go_live_date IS NOT NULL
                   AND date(go_live_date) < date(start_date) ORDER BY project_code"""),

    # ---- invalid financial data ------------------------------------------
    dict(template="PROJECTS", category="Invalid financial data",
         rule_code="NEGATIVE_VALUE", severity="error", column="Contract Value",
         entity_table="project",
         message="{label} has a negative contract value",
         recommendation="Correct the sign, or reclassify as a credit note against the original SOW",
         sql="""SELECT project_code, contract_value, project_id FROM project
                 WHERE org_id=? AND contract_value < 0 ORDER BY project_code"""),
    dict(template="TIME_ENTRIES", category="Historical data anomalies",
         rule_code="INVALID_VALUE", severity="error", column="Hours",
         entity_table="time_entry",
         message="Time entry {label} records more than 24 hours in a single day",
         recommendation="Split the entry across the days actually worked",
         sql="""SELECT time_entry_id, hours, time_entry_id FROM time_entry
                 WHERE org_id=? AND hours > 24 ORDER BY time_entry_id"""),
    dict(template="TIME_ENTRIES", category="Invalid financial data",
         rule_code="NEGATIVE_VALUE", severity="error", column="Hours",
         entity_table="time_entry",
         message="Time entry {label} records negative hours",
         recommendation="Replace with a reversing entry rather than a negative posting",
         sql="""SELECT time_entry_id, hours, time_entry_id FROM time_entry
                 WHERE org_id=? AND hours < 0 ORDER BY time_entry_id"""),

    # ---- currency ---------------------------------------------------------
    dict(template="CONTRACTS_SOWS", category="Inconsistent currencies",
         rule_code="CURRENCY_INCONSISTENT", severity="warning", column="Contract Value",
         entity_table="contract",
         message="Contract {label} is denominated in a currency other than the org default",
         recommendation="Confirm the FX rate and rate date to convert before load",
         sql="""SELECT contract_code, currency, contract_id FROM contract
                 WHERE org_id=? AND currency <> (SELECT currency FROM org WHERE org_id=contract.org_id)
                 ORDER BY contract_code"""),

    # ---- missing time -----------------------------------------------------
    dict(template="TIME_ENTRIES", category="Missing data",
         rule_code="MISSING_TIME", severity="warning", column="Hours",
         entity_table="assignment",
         message="{label} was assigned to a completed project but booked no time to it",
         recommendation="Confirm whether time was booked elsewhere or is missing from the extract",
         sql="""SELECT p.project_code || ' / ' || e.employee_name, a.planned_hours,
                       a.assignment_id
                  FROM assignment a
                  JOIN project p  ON p.project_id = a.project_id
                  JOIN employee e ON e.employee_id = a.employee_id
                 WHERE p.org_id = ? AND p.project_status = 'Complete'
                   AND NOT EXISTS (SELECT 1 FROM time_entry t
                                    WHERE t.project_id = a.project_id
                                      AND t.employee_id = a.employee_id)
                 ORDER BY 1"""),
    dict(template="PROJECT_FINANCIALS", category="Historical data anomalies",
         rule_code="LEDGER_TIMESHEET_MISMATCH", severity="warning", column="Labor Cost",
         entity_table="project",
         message="{label} reports a labour cost in its monthly financials that does not "
                 "tie to the time booked against it",
         recommendation="Reconcile the financial extract to the timesheet extract before "
                        "load; one of the two is incomplete",
         sql="""SELECT p.project_code,
                       ROUND(ABS(COALESCE(f.ledger,0) - COALESCE(t.sheets,0))) AS gap,
                       p.project_id
                  FROM project p
                  LEFT JOIN (SELECT project_id, SUM(labor_cost) ledger
                               FROM project_financial_month GROUP BY project_id) f
                         ON f.project_id = p.project_id
                  LEFT JOIN (SELECT project_id, SUM(hours * COALESCE(cost_rate,0)) sheets
                               FROM time_entry WHERE approval_status='Approved'
                                AND project_id IS NOT NULL GROUP BY project_id) t
                         ON t.project_id = p.project_id
                 WHERE p.org_id = ?
                   AND COALESCE(f.ledger,0) > 0
                   AND ABS(COALESCE(f.ledger,0) - COALESCE(t.sheets,0))
                       > 0.01 * COALESCE(f.ledger,0)
                 ORDER BY gap DESC"""),
    dict(template="PROJECT_FINANCIALS", category="Missing data",
         rule_code="NO_FINANCIALS", severity="warning", column="Recognized Revenue",
         entity_table="project",
         message="{label} has time booked but no monthly financial rows",
         recommendation="Supply the monthly revenue and cost ledger for this project",
         sql="""SELECT p.project_code, COUNT(t.time_entry_id), p.project_id
                  FROM project p JOIN time_entry t ON t.project_id = p.project_id
                 WHERE p.org_id = ?
                   AND NOT EXISTS (SELECT 1 FROM project_financial_month f
                                    WHERE f.project_id = p.project_id)
                 GROUP BY p.project_id ORDER BY p.project_code"""),
]


def rules_for_template(code):
    return [r for r in RULES if r["template"] == code]
