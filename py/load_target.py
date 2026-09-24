"""
Load an acquisition target's PSA history from the eight intake templates.

    # validate only, write nothing
    python3 py/load_target.py --dir out/intake_kit/sample_northwind \
        --org-name 'Northwind Advisory' --org-code NWA --dry-run

    # load, then run the engine and rebuild the console payload
    python3 py/load_target.py --dir out/intake_kit/sample_northwind \
        --org-name 'Northwind Advisory' --org-code NWA --analyse

Three decisions shape this file.

The validation rules are read from DATA_DICTIONARY.csv rather than restated
here. The dictionary is what the target is sent, so a rule written in code and
described in the dictionary would be two rules that drift apart, and the one
the target reads would be the wrong one.

The stages run in a fixed order, and it is not arbitrary: headers, types,
mandatory values, enumerations, keys, references, then cross-field sense. A
type error makes every downstream check meaningless, so reporting "end date
before start date" on a cell that is not a date is noise that hides the real
finding. Each stage only sees rows the previous one passed.

Errors block a row and warnings load it. A target with a blank cost rate is
still worth assessing, and the blank is itself a finding worth carrying into
the data quality view rather than a reason to refuse the file. That asymmetry
is the difference between an import that gets used and one that gets worked
around with a hand-edited spreadsheet.

Nothing here writes to an existing organisation. A load either creates a new
org or, with --replace, deletes the one it is replacing and rebuilds it. There
is no merge path, because a partial merge into a book somebody is already
reading is how two versions of the same engagement end up on one screen.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
PY = os.path.join(ROOT, "py")
DEFAULT_DICT = os.path.join(ROOT, "out", "intake_kit", "DATA_DICTIONARY.csv")

TEMPLATES = ["CUSTOMERS", "PROJECTS", "EMPLOYEES", "TIME_ENTRIES",
             "PROJECT_FINANCIALS", "TASKS_MILESTONES", "RISKS_ISSUES",
             "CONTRACTS_SOWS"]

# Load order is reference order: a row cannot resolve a reference to a file
# that has not been loaded yet.
LOAD_ORDER = ["CUSTOMERS", "EMPLOYEES", "CONTRACTS_SOWS", "PROJECTS",
              "TASKS_MILESTONES", "TIME_ENTRIES", "PROJECT_FINANCIALS",
              "RISKS_ISSUES"]

# Which column carries the row's own key, per template. Taken from the
# dictionary where it is marked, and stated here for the templates whose
# identity is a composite the dictionary cannot express in one column.
COMPOSITE_KEYS = {
    "TIME_ENTRIES": ["Employee ID", "Project ID", "Date", "Task"],
    "PROJECT_FINANCIALS": ["Project ID", "Month"],
    "TASKS_MILESTONES": ["Project ID", "Task ID"],
    "RISKS_ISSUES": ["Project ID", "Risk/Issue ID"],
    "CONTRACTS_SOWS": ["Contract ID", "SOW ID"],
}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d",
                "%d.%m.%Y"]
MONTH_FORMATS = ["%Y-%m", "%b-%Y", "%B %Y", "%m/%Y"]


# =====================================================================
# The dictionary
# =====================================================================
class Spec:
    """The column rules for one template, as the data dictionary states them."""

    def __init__(self):
        self.columns = []          # in order
        self.type = {}
        self.mandatory = set()
        self.key = set()
        self.references = {}
        self.allowed = {}          # column -> [values]

    def enum_lookup(self, col, value):
        """
        Case-insensitive match against the allowed list.

        An unmatched value is returned rather than dropped: it goes on to the
        mapping step as an unmapped value, which is a decision for a person.
        Silently discarding it would turn a data problem into a missing row.
        """
        for v in self.allowed.get(col, []):
            if v.strip().lower() == str(value).strip().lower():
                return v
        return None


def read_dictionary(path):
    specs = {t: Spec() for t in TEMPLATES}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            t = (r.get("Template") or "").strip()
            if t not in specs:
                continue
            s = specs[t]
            col = (r.get("Column") or "").strip()
            s.columns.append(col)
            s.type[col] = (r.get("Type") or "text").strip().lower()
            if (r.get("Mandatory") or "").strip().lower() == "yes":
                s.mandatory.add(col)
            if (r.get("Key") or "").strip().lower() == "yes":
                s.key.add(col)
            ref = (r.get("References") or "").strip()
            if ref:
                s.references[col] = ref
            allowed = (r.get("Allowed values") or "").strip()
            if allowed:
                s.allowed[col] = [v.strip() for v in allowed.split("|")]
    return specs


# =====================================================================
# Parsing
# =====================================================================
def parse_date(v):
    v = (v or "").strip()
    if not v:
        return None, None
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, f).date().isoformat(), None
        except ValueError:
            continue
    return None, f"'{v}' is not a date this loader recognises"


def parse_month(v):
    v = (v or "").strip()
    if not v:
        return None, None
    for f in MONTH_FORMATS:
        try:
            return dt.datetime.strptime(v, f).strftime("%Y-%m"), None
        except ValueError:
            continue
    d, err = parse_date(v)
    if d:
        return d[:7], None
    return None, f"'{v}' is not a month this loader recognises"


def parse_decimal(v):
    v = (v or "").strip()
    if not v:
        return None, None
    # Currency symbols, thousands separators and parenthesised negatives all
    # arrive from finance exports and none of them are errors.
    neg = v.startswith("(") and v.endswith(")")
    t = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
    if t in ("", "-", "."):
        return None, f"'{v}' is not a number"
    try:
        x = float(t)
    except ValueError:
        return None, f"'{v}' is not a number"
    return (-x if neg else x), None


def parse_int(v):
    x, err = parse_decimal(v)
    if err or x is None:
        return None, err
    if abs(x - round(x)) > 1e-9:
        return None, f"'{v}' is not a whole number"
    return int(round(x)), None


# =====================================================================
# Findings
# =====================================================================
class Report:
    """
    Every finding, with the stage that raised it.

    Held as a list rather than printed as it goes, because the useful output is
    grouped by rule: "eleven rows have an unparseable date in Start Date" is
    one conversation with the target and eleven separate lines is not.
    """

    def __init__(self):
        self.items = []

    def add(self, stage, severity, template, row_no, column, value, message,
            recommendation=None, rule=None):
        self.items.append({
            "stage": stage, "severity": severity, "template": template,
            "row": row_no, "column": column, "value": value,
            "message": message, "recommendation": recommendation,
            "rule": rule})

    def errors(self, template=None):
        return [i for i in self.items if i["severity"] == "error"
                and (template is None or i["template"] == template)]

    def warnings(self, template=None):
        return [i for i in self.items if i["severity"] == "warning"
                and (template is None or i["template"] == template)]

    def print_summary(self, counts):
        print("\nIntake validation report")
        print("=" * 72)
        print(f"{'File':<22}{'Rows':>7}{'Loadable':>10}{'Errors':>9}"
              f"{'Warnings':>10}")
        for t in TEMPLATES:
            c = counts.get(t, {})
            if not c:
                print(f"{t:<22}{'absent':>7}")
                continue
            print(f"{t:<22}{c['rows']:>7}{c['ok']:>10}"
                  f"{len(self.errors(t)):>9}{len(self.warnings(t)):>10}")
        print("-" * 72)

        # Grouped by rule, not by value. Grouping on the message text put
        # 'NW-PRJ-1001 does not match' and 'NW-PRJ-1002 does not match' into
        # two groups, which is how one bad date becomes forty headings.
        by_rule = defaultdict(list)
        for i in self.items:
            by_rule[(i["severity"], i["stage"], i["template"], i["column"],
                     i.get("rule") or "")].append(i)
        if not by_rule:
            print("Nothing to report: every row passed every stage.")
            return
        print(f"\n{len(self.errors())} errors, {len(self.warnings())} warnings,"
              f" grouped by rule\n")
        order = {"error": 0, "warning": 1}
        for k in sorted(by_rule, key=lambda k: (order[k[0]], k[1], k[2])):
            sev, stage, template, column, _ = k
            g = by_rule[k]
            head = (f"[{sev}] {template}"
                    + (f" · {column}" if column else "")
                    + f" · {stage}: {len(g)} row"
                    + ("s" if len(g) != 1 else ""))
            print(head)
            for i in g[:3]:
                print(f"    row {i['row']}: {i['message']}")
            if len(g) > 3:
                print(f"    ... and {len(g) - 3} more")
            if g[0]["recommendation"]:
                print(f"    fix: {g[0]['recommendation']}")
            print()


# =====================================================================
# The seven stages
# =====================================================================
def read_file(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rdr = csv.reader(fh)
        try:
            header = next(rdr)
        except StopIteration:
            return [], []
        header = [h.strip() for h in header]
        return header, [r for r in rdr if any((c or "").strip() for c in r)]


def validate_template(name, spec, header, raw, rep, rejected=None):
    """
    Stages 1 to 5, within one file. Returns the rows that survived, each as a
    dict of column to parsed value, with the source row number kept so a
    finding can point at a line in the file the target sent.
    """
    # ---- 1. headers ----
    present = {h for h in header}
    missing_mandatory = [c for c in spec.mandatory if c not in present]
    if missing_mandatory:
        for c in missing_mandatory:
            rep.add("headers", "error", name, 0, c, None,
                    f"mandatory column '{c}' is not in the file",
                    "add the column, or say explicitly that the source system "
                    "does not hold it")
        return []
    for c in spec.columns:
        if c not in present and c not in spec.mandatory:
            rep.add("headers", "warning", name, 0, c, None,
                    f"optional column '{c}' is absent",
                    "the assessment runs without it; anything derived from it "
                    "will be reported as not available")
    for h in header:
        if h and h not in spec.type:
            rep.add("headers", "warning", name, 0, h, None,
                    f"column '{h}' is not in the template and is ignored",
                    "no action needed: targets add columns and that is not an "
                    "error")

    idx = {h: i for i, h in enumerate(header)}
    out = []
    for n, r in enumerate(raw, start=2):
        row, bad = {}, False
        for c in spec.columns:
            if c not in idx:
                row[c] = None
                continue
            raw_v = r[idx[c]] if idx[c] < len(r) else ""
            raw_v = (raw_v or "").strip()
            t = spec.type[c]

            # ---- 2. types ----
            if t == "date":
                v, err = parse_date(raw_v)
            elif t in ("decimal", "money"):
                v, err = parse_decimal(raw_v)
            elif t in ("int", "integer"):
                v, err = parse_int(raw_v)
            elif c == "Month":
                v, err = parse_month(raw_v)
            else:
                v, err = (raw_v or None), None
            if err:
                rep.add("types", "error", name, n, c, raw_v, err,
                        "use ISO dates (2026-03-31) and plain numbers")
                bad = True
                continue

            # ---- 3. mandatory ----
            if c in spec.mandatory and (v is None or v == ""):
                rep.add("mandatory", "error", name, n, c, raw_v,
                        f"'{c}' is mandatory and empty",
                        "the row cannot be loaded without it")
                bad = True
                continue

            # ---- 4. enumerations ----
            if t == "enum" and v:
                m = spec.enum_lookup(c, v)
                if m is None:
                    rep.add("enumerations", "warning", name, n, c, raw_v,
                            f"'{raw_v}' is not one of "
                            f"{' | '.join(spec.allowed.get(c, []))}",
                            "the value is carried through unmapped for someone "
                            "to decide, rather than dropped")
                else:
                    v = m
            row[c] = v
        if bad:
            # Keep the row's own key even though the row is going: everything
            # pointing at it will fail next, and those failures are worth
            # attributing to this one rather than reporting as missing data.
            if rejected is not None:
                for c in sorted(spec.key):
                    if c in idx and idx[c] < len(r):
                        k = (r[idx[c]] or "").strip()
                        if k:
                            rejected.setdefault(name, set()).add(k)
            continue
        row["__row"] = n
        out.append(row)

    # ---- 5. keys ----
    key_cols = COMPOSITE_KEYS.get(name) or sorted(spec.key)
    key_cols = [c for c in key_cols if c in spec.type]
    if key_cols:
        seen = {}
        kept = []
        for row in out:
            k = tuple(str(row.get(c) or "") for c in key_cols)
            if k in seen:
                rep.add("keys", "error", name, row["__row"],
                        " + ".join(key_cols), " / ".join(k),
                        f"duplicate key, first seen at row {seen[k]}",
                        rule="duplicate key",
                        recommendation="de-duplicate at source: a duplicate "
                        "key double counts hours, revenue or headcount "
                        "depending on the file")
                continue
            seen[k] = row["__row"]
            kept.append(row)
        out = kept
    return out


def validate_references(data, specs, rep, rejected=None):
    """
    Stage 6. A reference has to resolve to a key in the file it points at.

    Resolution is by key first and by name second, because targets export a
    project's manager as a name far more often than as an employee id, and
    refusing the file over that would be pedantry rather than diligence.

    Two failures look identical here and are not the same problem. A reference
    to something the target never sent is a gap in the extract. A reference to
    a row that WAS sent and was rejected upstream is a consequence, and one bad
    date on an engagement can produce a hundred and twenty of them. Reporting
    both the same way buries the single fix under its own fallout, so the
    consequences are attributed to their cause and reported once.
    """
    rejected = rejected or {}
    keysets = {}
    for t, rows in data.items():
        spec = specs[t]
        kc = sorted(spec.key)
        keysets[t] = {
            "keys": {str(r.get(kc[0])) for r in rows if kc and r.get(kc[0])},
            "names": {str(r.get(c)).strip().lower()
                      for r in rows for c in spec.columns
                      if c.endswith("Name") and r.get(c)},
        }
    for t, rows in list(data.items()):
        spec = specs[t]
        kept = []
        for row in rows:
            ok = True
            for col, target in spec.references.items():
                v = row.get(col)
                if not v or target not in keysets:
                    continue
                if str(v) in keysets[target]["keys"]:
                    continue
                if str(v).strip().lower() in keysets[target]["names"]:
                    continue
                if target == t and str(v) == str(row.get(sorted(spec.key)[0]
                                                         if spec.key else "")):
                    continue          # self reference, e.g. a manager
                sev = "error" if col in spec.mandatory else "warning"
                upstream = str(v) in rejected.get(target, set())
                if upstream:
                    rep.add("references", sev, t, row["__row"], col, v,
                            f"'{v}' was supplied in {target} but that row was "
                            f"rejected earlier, so this one cannot load either",
                            f"fix the {target} row: this finding is a "
                            f"consequence of that one and disappears with it",
                            rule="orphaned by a rejected parent")
                else:
                    rep.add("references", sev, t, row["__row"], col, v,
                            f"'{v}' does not match anything in {target}",
                            f"either add the missing {target} row or correct "
                            f"the reference; a mandatory reference that cannot "
                            f"resolve blocks the row",
                            rule="reference not supplied")
                if sev == "error":
                    ok = False
            if ok:
                kept.append(row)
        data[t] = kept
    return data


def validate_sense(data, rep):
    """
    Stage 7. Cross-field arithmetic that no single-column rule can catch.

    These are the checks that separate a file that parses from a file that
    means something. A go-live before the start date parses perfectly.
    """
    def date_pair(t, row, a, b, msg):
        x, y = row.get(a), row.get(b)
        if x and y and y < x:
            rep.add("cross-field", "warning", t, row["__row"], b, y, msg,
                    "check which of the two dates is wrong before loading; "
                    "both are used in the duration and slip analysis")

    for row in data.get("PROJECTS", []):
        date_pair("PROJECTS", row, "Start Date", "Planned End Date",
                  "planned end is before the start date")
        date_pair("PROJECTS", row, "Start Date", "Actual End Date",
                  "actual end is before the start date")
        date_pair("PROJECTS", row, "Start Date", "Go-Live Date",
                  "go-live is before the start date")
        for c in ("Contract Value", "Budget Hours", "Budget Cost"):
            v = row.get(c)
            if v is not None and v < 0:
                rep.add("cross-field", "warning", "PROJECTS", row["__row"], c,
                        v, f"{c.lower()} is negative",
                        "a credit note belongs in the financials file, not in "
                        "the engagement's budget")
        if row.get("Project Status") == "Complete" and not row.get(
                "Actual End Date"):
            rep.add("cross-field", "warning", "PROJECTS", row["__row"],
                    "Actual End Date", None,
                    "engagement is complete with no actual end date",
                    "without it the engagement cannot appear in the delivery "
                    "duration or hypercare analysis")

    per_day = defaultdict(float)
    for row in data.get("TIME_ENTRIES", []):
        h = row.get("Hours")
        if h is not None and h <= 0:
            rep.add("cross-field", "warning", "TIME_ENTRIES", row["__row"],
                    "Hours", h, "hours are zero or negative",
                    "a correction should net against the original entry, not "
                    "arrive as a negative row")
        if h and h > 24:
            rep.add("cross-field", "error", "TIME_ENTRIES", row["__row"],
                    "Hours", h, "more than 24 hours in one entry",
                    "split the entry, or correct the unit if the source is in "
                    "minutes")
        if row.get("Employee ID") and row.get("Date"):
            per_day[(row["Employee ID"], row["Date"])] += (h or 0)
    for (eid, day), tot in per_day.items():
        if tot > 24:
            rep.add("cross-field", "warning", "TIME_ENTRIES", 0, "Hours", tot,
                    f"{eid} has {tot:,.1f} hours booked on {day} across "
                    f"several entries",
                    "the day ceiling check will report this after load; it is "
                    "usually a duplicated export rather than a long day")

    for row in data.get("TASKS_MILESTONES", []):
        date_pair("TASKS_MILESTONES", row, "Planned Start", "Planned End",
                  "planned end is before the planned start")
        pc = row.get("Percent Complete")
        if pc is not None and not (0 <= pc <= 100):
            rep.add("cross-field", "warning", "TASKS_MILESTONES", row["__row"],
                    "Percent Complete", pc, "percent complete is outside 0-100",
                    "earned value and fixed-fee recognition are both computed "
                    "from this column")

    for row in data.get("PROJECT_FINANCIALS", []):
        inv, rec = row.get("Invoiced Revenue"), row.get("Recognized Revenue")
        if inv is not None and rec is not None and rec > 0 and inv > rec * 3:
            rep.add("cross-field", "warning", "PROJECT_FINANCIALS",
                    row["__row"], "Invoiced Revenue", inv,
                    "invoiced is more than three times recognised in the month",
                    "usually a milestone invoice, and worth confirming: it "
                    "lands in deferred revenue rather than as an asset")
    return data


# =====================================================================
# Loading
# =====================================================================
class Standins:
    """
    Every value the loader had to supply because the templates do not carry it.

    The schema requires a margin target on a practice, a utilisation target on
    a person, a weekly capacity, a project health. None of the eight templates
    asks for any of them, and the target usually cannot produce them anyway:
    they are the acquirer's operating parameters, not the target's history.

    So they are substituted, and every substitution is recorded once per column
    with a count and a basis. That matters more than it sounds. The check
    catalogue asserts that a margin target exists for every practice and a
    utilisation target for every role. Defaulting quietly would make those
    checks pass against numbers this loader invented, which is worse than
    failing them: a readiness score built on defaults reads as readiness.
    """

    def __init__(self):
        self.rows = {}

    def note(self, table, column, basis):
        k = (table, column)
        if k not in self.rows:
            self.rows[k] = {"basis": basis, "count": 0}
        self.rows[k]["count"] += 1

    def value(self, table, column, supplied, fallback, basis):
        if supplied is not None and supplied != "":
            return supplied
        self.note(table, column, basis)
        return fallback

    def print_summary(self):
        if not self.rows:
            return
        print("\nValues the templates do not carry, supplied by the loader")
        print("-" * 72)
        for (t, c), v in sorted(self.rows.items()):
            print(f"  {t}.{c:<26}{v['count']:>6} rows   {v['basis']}")
        print("  Each is a data quality finding in the console, not a silent "
              "default.")


def acquirer_norms(con):
    """
    The acquirer's own operating parameters, used as the stated basis for
    anything the target's templates do not carry.

    Read from the operating company rather than hard-coded, because a constant
    written into a loader is a number nobody can trace and nobody updates.
    Where there is no operating company to read, the fallbacks below are used
    and the basis says so.
    """
    op = con.execute("SELECT org_id FROM org WHERE org_role='operating' "
                     "ORDER BY org_id LIMIT 1").fetchone()
    n = {"margin_pct": 30.0, "util_pct": 72.0, "capacity_hours": 37.5,
         "cost_rate": 110.0, "source": "a documented default: no operating "
                                       "company in this database to read"}
    if not op:
        return n
    oid = op[0]
    row = con.execute("""SELECT AVG(target_margin_pct), AVG(target_utilization_pct)
                           FROM practice WHERE org_id=?""", (oid,)).fetchone()
    if row and row[0] is not None:
        n["margin_pct"] = round(row[0], 1)
        n["util_pct"] = round(row[1], 1)
    row = con.execute("""SELECT AVG(weekly_capacity_hours), AVG(cost_rate)
                           FROM employee WHERE org_id=? AND is_billable=1""",
                      (oid,)).fetchone()
    if row and row[0] is not None:
        n["capacity_hours"] = round(row[0], 1)
        n["cost_rate"] = round(row[1], 2)
    code = con.execute("SELECT org_code FROM org WHERE org_id=?",
                       (oid,)).fetchone()[0]
    n["source"] = f"the acquirer's own book ({code}), on the same definitions"
    return n


def load(con, org_name, org_code, data, specs, rep):
    """
    Write the validated rows.

    Everything is written inside one transaction. A half-loaded target is worse
    than no target: the engine would score it, the console would show it, and
    nothing on the screen would say which half arrived.
    """
    cur = con.cursor()
    norms = acquirer_norms(con)
    sub = Standins()
    cur.execute("""INSERT INTO org(org_code, org_name, org_role, currency,
                        fiscal_year_start_month, migration_state)
                   VALUES (?,?,?,?,?,?)""",
                (org_code, org_name, "acquisition_target", "CAD", 1, "assessing"))
    org_id = cur.lastrowid

    counts = defaultdict(int)

    # ---- practices, derived rather than imported ----
    # The templates carry a practice name on the employee and nothing else, so
    # the practice list is the distinct set of those. Inventing a practice
    # table for the target to fill in would be a ninth file for one column.
    practices = {}
    for r in data.get("EMPLOYEES", []):
        p = (r.get("Practice") or "").strip()
        if p and p not in practices:
            cur.execute("""INSERT INTO practice(org_id, practice_code,
                                practice_name, segment, target_margin_pct,
                                target_utilization_pct)
                           VALUES (?,?,?,?,?,?)""",
                        (org_id,
                         re.sub(r"[^A-Z0-9]", "", p.upper())[:8] or "GEN",
                         p, None,
                         sub.value("practice", "target_margin_pct", None,
                                   norms["margin_pct"], norms["source"]),
                         sub.value("practice", "target_utilization_pct", None,
                                   norms["util_pct"], norms["source"])))
            practices[p] = cur.lastrowid
            counts["practice"] += 1

    # ---- customers ----
    customers = {}
    for r in data.get("CUSTOMERS", []):
        cur.execute("""INSERT INTO customer(org_id, legacy_customer_id,
                            customer_code, customer_name, customer_type,
                            industry, region, account_owner, customer_status,
                            contract_start, contract_end, annual_revenue,
                            lifetime_revenue)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (org_id, r.get("Customer ID"), r.get("Customer ID"),
                     r.get("Customer Name"), r.get("Customer Type"),
                     r.get("Industry"), r.get("Region"),
                     r.get("Account Owner"), r.get("Customer Status"),
                     r.get("Contract Start"), r.get("Contract End"),
                     r.get("Annual Revenue"), r.get("Lifetime Revenue")))
        cid = cur.lastrowid
        customers[str(r.get("Customer ID"))] = cid
        if r.get("Customer Name"):
            customers[str(r["Customer Name"]).strip().lower()] = cid
        counts["customer"] += 1

    # ---- employees ----
    employees = {}
    for r in data.get("EMPLOYEES", []):
        cur.execute("""INSERT INTO employee(org_id, legacy_employee_id,
                            employee_code, employee_name, role, department,
                            practice_id, location, employment_type, cost_rate,
                            billing_rate, weekly_capacity_hours,
                            utilization_target_pct, start_date, is_billable,
                            is_active)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (org_id, r.get("Employee ID"), r.get("Employee ID"),
                     r.get("Employee Name"), r.get("Role"),
                     r.get("Department"), practices.get(r.get("Practice")),
                     r.get("Location"), r.get("Employment Type"),
                     r.get("Cost Rate"), r.get("Billing Rate"),
                     sub.value("employee", "weekly_capacity_hours",
                               r.get("Capacity"), norms["capacity_hours"],
                               norms["source"]),
                     sub.value("employee", "utilization_target_pct",
                               r.get("Utilization Target"), norms["util_pct"],
                               norms["source"]),
                     r.get("Start Date"),
                     0 if (r.get("Billing Rate") or 0) == 0 else 1))
        eid = cur.lastrowid
        employees[str(r.get("Employee ID"))] = eid
        if r.get("Employee Name"):
            employees[str(r["Employee Name"]).strip().lower()] = eid
        counts["employee"] += 1

        for s in [x.strip() for x in (r.get("Skills") or "").split(";") if x.strip()]:
            row = con.execute("SELECT skill_id FROM skill WHERE skill_name=?",
                              (s,)).fetchone()
            sid = row[0] if row else cur.execute(
                "INSERT INTO skill(skill_name, skill_family) VALUES (?,?)",
                (s, "Imported")).lastrowid
            cur.execute("""INSERT OR IGNORE INTO employee_skill(employee_id,
                                skill_id, proficiency) VALUES (?,?,?)""",
                        (eid, sid, 3))

    # ---- managers, second pass so a manager can appear after their report ----
    for r in data.get("EMPLOYEES", []):
        m = r.get("Manager")
        if not m:
            continue
        mid = employees.get(str(m)) or employees.get(str(m).strip().lower())
        if mid:
            cur.execute("UPDATE employee SET manager_employee_id=? WHERE "
                        "employee_id=?", (mid, employees[str(r["Employee ID"])]))

    # ---- contracts and SOWs ----
    contracts, sows = {}, {}
    for r in data.get("CONTRACTS_SOWS", []):
        cust = customers.get(str(r.get("Customer"))) or \
            customers.get(str(r.get("Customer") or "").strip().lower())
        cur.execute("""INSERT INTO contract(org_id, legacy_contract_id,
                            contract_code, customer_id, contract_type,
                            billing_model, contract_value, currency,
                            sold_hours, start_date, end_date, payment_terms,
                            remaining_value)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (org_id, r.get("Contract ID"), r.get("Contract ID"), cust,
                     r.get("Contract Type"), r.get("Billing Model"),
                     sub.value("contract", "contract_value",
                               r.get("Contract Value"), 0.0,
                               "no contract value supplied on the row"),
                     "CAD", r.get("Sold Hours"),
                     r.get("Start Date"), r.get("End Date"),
                     r.get("Payment Terms"), r.get("Remaining Value")))
        ctr = cur.lastrowid
        contracts[str(r.get("Contract ID"))] = ctr
        counts["contract"] += 1
        if r.get("SOW ID"):
            cur.execute("""INSERT INTO sow(contract_id, legacy_sow_id, sow_code,
                                sow_value, sold_hours, signed_date, version)
                           VALUES (?,?,?,?,?,?,1)""",
                        (ctr, r.get("SOW ID"), r.get("SOW ID"),
                         sub.value("sow", "sow_value", r.get("Contract Value"),
                                   0.0, "the contract value on the same row"),
                         r.get("Sold Hours"), r.get("Start Date")))
            sows[str(r.get("SOW ID"))] = cur.lastrowid
            counts["sow"] += 1
        # A contract row naming a project lets the engagement inherit the
        # billing model, which is what the T&M against fixed fee analysis
        # runs on. Without it every engagement reads as one model.
        if r.get("Project"):
            # Keyed on whatever the column actually holds. Targets put the
            # project id in here about as often as the name, and matching on
            # only one of the two leaves the engagement with no billing model,
            # which is the input the T&M against fixed fee analysis runs on.
            contracts[("project", str(r["Project"]).strip().lower())] = ctr

    # ---- projects ----
    projects, plan_versions = {}, {}
    for r in data.get("PROJECTS", []):
        cust = customers.get(str(r.get("Customer"))) or \
            customers.get(str(r.get("Customer") or "").strip().lower())
        pm = employees.get(str(r.get("Project Manager"))) or \
            employees.get(str(r.get("Project Manager") or "").strip().lower())
        ctr = (contracts.get(("project", str(r.get("Legacy Project ID")
                                              or "").strip().lower()))
               or contracts.get(("project", str(r.get("Project Name")
                                                or "").strip().lower())))
        bm = None
        if ctr:
            bm = con.execute("SELECT billing_model FROM contract WHERE "
                             "contract_id=?", (ctr,)).fetchone()[0]
        prac = con.execute("SELECT practice_id FROM employee WHERE "
                           "employee_id=?", (pm,)).fetchone() if pm else None
        cur.execute("""INSERT INTO project(org_id, legacy_project_id,
                            project_code, project_name, customer_id,
                            contract_id, practice_id, project_type, product,
                            project_manager_id, billing_model, start_date,
                            planned_end_date, actual_end_date, go_live_date,
                            contract_value, sow_value, budget_hours,
                            budget_cost, project_status, project_health,
                            health_set_by, health_set_on, target_margin_pct)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (org_id, r.get("Legacy Project ID"),
                     r.get("Legacy Project ID"), r.get("Project Name"), cust,
                     ctr, prac[0] if prac else None, r.get("Project Type"),
                     r.get("Product"), pm, bm, r.get("Start Date"),
                     r.get("Planned End Date"), r.get("Actual End Date"),
                     r.get("Go-Live Date"), r.get("Contract Value"),
                     sub.value("project", "sow_value", r.get("SOW Value"),
                               r.get("Contract Value") or 0.0,
                               "the engagement's contract value, where no "
                               "separate SOW value was supplied"),
                     r.get("Budget Hours"),
                     sub.value("project", "budget_cost", r.get("Budget Cost"),
                               round((r.get("Budget Hours") or 0)
                                     * norms["cost_rate"], 2),
                               f"budget hours at {norms['cost_rate']:,.2f} an "
                               f"hour, from {norms['source']}"),
                     r.get("Project Status"),
                     sub.value("project", "project_health",
                               r.get("Project Health"), "Green",
                               "no health supplied; the engine computes its "
                               "own health and the divergence check compares "
                               "the two, so a stood-in Green will read as a "
                               "divergence rather than as agreement"),
                     "Imported", None,
                     sub.value("project", "target_margin_pct", None,
                               norms["margin_pct"], norms["source"])))
        pid = cur.lastrowid
        projects[str(r.get("Legacy Project ID"))] = pid
        if r.get("Project Name"):
            projects[str(r["Project Name"]).strip().lower()] = pid
        counts["project"] += 1

        # One current plan version per engagement. The plan the target sent is
        # the current plan by definition: they have no baseline history to
        # give, and inventing one would make the plan-versioning view claim
        # evidence that does not exist.
        cur.execute("""INSERT INTO project_plan_version(project_id, version_no,
                            is_baseline, is_current, created_on, created_by,
                            change_reason)
                       VALUES (?,1,1,1,?,?,?)""",
                    (pid, r.get("Start Date"), "Imported",
                     "Loaded from intake templates; no prior baseline supplied"))
        plan_versions[pid] = cur.lastrowid

    # ---- tasks ----
    tasks = {}
    for r in data.get("TASKS_MILESTONES", []):
        pid = projects.get(str(r.get("Project ID"))) or \
            projects.get(str(r.get("Project ID") or "").strip().lower())
        if not pid:
            continue
        cur.execute("""INSERT INTO project_task(project_id, plan_version_id,
                            legacy_task_id, task_code, task_name, phase,
                            is_milestone, is_critical, planned_start,
                            planned_end, actual_start, actual_end,
                            planned_hours, actual_hours, status,
                            percent_complete, billing_amount)
                       VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,0)""",
                    (pid, plan_versions[pid], r.get("Task ID"),
                     r.get("Task ID"), r.get("Task Name"), r.get("Phase"),
                     1 if "milestone" in (r.get("Task Name") or "").lower()
                     else 0,
                     r.get("Planned Start"), r.get("Planned End"),
                     r.get("Actual Start"), r.get("Actual End"),
                     r.get("Planned Hours"),
                     sub.value("project_task", "actual_hours",
                               r.get("Actual Hours"), 0.0,
                               "no actual hours on the task row"),
                     sub.value("project_task", "status", r.get("Status"),
                               "Not Started", "no status on the task row"),
                     sub.value("project_task", "percent_complete",
                               r.get("Percent Complete"), 0.0,
                               "no percent complete on the task row; earned "
                               "value and fixed-fee recognition both read "
                               "this column, so a zero is reported rather "
                               "than assumed")))
        tasks[(str(r.get("Project ID")), str(r.get("Task ID")))] = cur.lastrowid
        counts["task"] += 1

    # ---- time entries ----
    # Rates are snapshotted from the person's record where the file does not
    # carry them, because a cost computed from today's rate against a
    # two-year-old timesheet is not the cost that was incurred.
    rates = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT employee_id, cost_rate, billing_rate FROM employee "
        "WHERE org_id=?", (org_id,))}
    assign = defaultdict(lambda: [0.0, None, None])
    for r in data.get("TIME_ENTRIES", []):
        eid = employees.get(str(r.get("Employee ID"))) or \
            employees.get(str(r.get("Employee Name") or "").strip().lower())
        pid = projects.get(str(r.get("Project ID"))) or \
            projects.get(str(r.get("Project Name") or "").strip().lower())
        if not (eid and pid):
            continue
        cost, bill = rates.get(eid, (None, None))
        billable = 1 if (r.get("Billable / Non-Billable") or
                         "Billable") == "Billable" else 0
        cur.execute("""INSERT INTO time_entry(org_id, legacy_time_entry_id,
                            employee_id, project_id, task_id, entry_date,
                            hours, is_billable, time_category, approval_status,
                            cost_rate, bill_rate, invoiced)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (org_id, None, eid, pid,
                     tasks.get((str(r.get("Project ID")), str(r.get("Task")))),
                     r.get("Date"), r.get("Hours"), billable,
                     r.get("Time Category"),
                     r.get("Approval Status") or "Approved", cost,
                     bill if billable else None))
        counts["time_entry"] += 1
        a = assign[(pid, eid)]
        a[0] += (r.get("Hours") or 0)
        a[1] = min(a[1] or r.get("Date"), r.get("Date"))
        a[2] = max(a[2] or r.get("Date"), r.get("Date"))

    # ---- assignments, derived from who actually booked time ----
    # The templates have no assignment file. Deriving it from booked time is
    # honest about what it is: a record of who worked on what, not a plan. The
    # over-allocation checks that need a planned allocation will report it as
    # not available rather than reading a number nobody set.
    for (pid, eid), (hours, first, last) in assign.items():
        cost, bill = rates.get(eid, (None, None))
        cur.execute("""INSERT INTO assignment(project_id, employee_id,
                            role_on_project, start_date, end_date,
                            planned_hours, allocation_pct, cost_rate, bill_rate)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (pid, eid, "Derived from booked time", first, last,
                     round(hours, 1),
                     sub.value("assignment", "allocation_pct", None,
                               round(alloc_pct(hours, first, last), 1),
                               "booked hours over the weeks between the first "
                               "and last entry; the templates carry no "
                               "assignment file, so this is what was worked "
                               "rather than what was planned"),
                     cost, bill))
        counts["assignment"] += 1

    # ---- financial months ----
    for r in data.get("PROJECT_FINANCIALS", []):
        pid = projects.get(str(r.get("Project ID"))) or \
            projects.get(str(r.get("Project ID") or "").strip().lower())
        if not pid:
            continue
        cur.execute("""INSERT INTO project_financial_month(project_id,
                            period_month, contract_value, invoiced_revenue,
                            recognized_revenue, labor_cost, other_cost,
                            budget_amount, forecast_amount, actual_amount)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (pid, r.get("Month"),
                     *[sub.value("project_financial_month", col, r.get(src),
                                 0.0, f"no {src.lower()} in the month row")
                       for col, src in
                       [("contract_value", "Contract Value"),
                        ("invoiced_revenue", "Invoiced Revenue"),
                        ("recognized_revenue", "Recognized Revenue"),
                        ("labor_cost", "Labor Cost"),
                        ("other_cost", "Other Cost"),
                        ("budget_amount", "Budget"),
                        ("forecast_amount", "Forecast"),
                        ("actual_amount", "Actual")]]))
        counts["financial_month"] += 1

    # ---- risks and issues ----
    for r in data.get("RISKS_ISSUES", []):
        pid = projects.get(str(r.get("Project ID"))) or \
            projects.get(str(r.get("Project ID") or "").strip().lower())
        if not pid:
            continue
        cur.execute("""INSERT INTO risk_issue(project_id, legacy_risk_id,
                            ref_code, entry_type, description, date_identified,
                            owner, priority, impact, probability, status,
                            resolution, due_date, is_customer_raised)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (pid, r.get("Risk/Issue ID"), r.get("Risk/Issue ID"),
                     r.get("Type") or "Issue", r.get("Description"),
                     r.get("Date Identified"), r.get("Owner"),
                     r.get("Priority"), r.get("Impact"), r.get("Probability"),
                     sub.value("risk_issue", "status", r.get("Status"),
                               "Open", "no status on the register row"),
                     r.get("Resolution"), r.get("Due Date")))
        counts["risk_issue"] += 1

    # ---- the findings, kept with the load rather than only printed ----
    # A validation report that exists only in a terminal is a report nobody can
    # produce again. Warnings become data quality findings so the console shows
    # what was accepted with a reservation.
    now = dt.datetime.now().isoformat(timespec="seconds")
    for t in TEMPLATES:
        tpl = con.execute("SELECT template_id FROM import_template WHERE "
                          "template_code=?", (t,)).fetchone()
        rows = data.get(t)
        if rows is None:
            continue
        cur.execute("""INSERT INTO import_batch(org_id, template_id, file_name,
                            uploaded_by, uploaded_at, row_count, valid_count,
                            warning_count, error_count, attempt_no, status)
                       VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                    (org_id, tpl[0] if tpl else None, f"{t}.csv", "load_target",
                     now, len(rows) + len(rep.errors(t)), len(rows),
                     len(rep.warnings(t)), len(rep.errors(t)),
                     "Committed" if not rep.errors(t)
                     else "Failed Validation"))
        batch = cur.lastrowid
        for i in rep.errors(t) + rep.warnings(t):
            cur.execute("""INSERT INTO validation_finding(batch_id,
                                staging_row_id, severity, rule_code,
                                column_header, observed_value, message,
                                recommendation, resolved)
                           VALUES (?,NULL,?,?,?,?,?,?,0)""",
                        (batch, i["severity"], i["stage"], i["column"],
                         None if i["value"] is None else str(i["value"])[:120],
                         i["message"], i["recommendation"]))
    for i in rep.warnings():
        cur.execute("""INSERT INTO data_quality_finding(org_id, run_at,
                            category, rule_code, entity_table, entity_pk,
                            entity_label, severity, message, recommendation,
                            resolved)
                       VALUES (?,?,?,?,?,NULL,?,?,?,?,0)""",
                    (org_id, now, "intake", i["stage"], i["template"],
                     f"{i['template']} row {i['row']}", "warning", i["message"],
                     i["recommendation"]))

    # Recognised revenue on the engagement is the sum of its months rather
    # than a typed-in figure, so the two can never disagree.
    cur.execute("""UPDATE project SET revenue_recognized = COALESCE(
                     (SELECT SUM(recognized_revenue)
                        FROM project_financial_month f
                       WHERE f.project_id = project.project_id), 0)
                    WHERE org_id=?""", (org_id,))

    for (table, column), v in sorted(sub.rows.items()):
        cur.execute("""INSERT INTO data_quality_finding(org_id, run_at,
                            category, rule_code, entity_table, entity_pk,
                            entity_label, severity, message, recommendation,
                            resolved)
                       VALUES (?,?,?,?,?,NULL,?,?,?,?,0)""",
                    (org_id, now, "intake", "stood-in value", table,
                     f"{table}.{column}", "warning",
                     f"{v['count']} rows carry no {column}, so the loader "
                     f"supplied one from {v['basis']}.",
                     "Ask the target for the real figure. Any check or metric "
                     "that reads this column is reading the acquirer's "
                     "parameter, not the target's."))

    con.commit()
    sub.print_summary()
    return org_id, counts


def alloc_pct(hours, first, last):
    """
    Allocation as booked hours over the weeks they span, against a 37.5 hour
    week. Not what was planned, because the templates carry no plan; the
    over-allocation checks read the planned figure and will report this as
    derived rather than treating it as a commitment.
    """
    if not (first and last):
        return 0.0
    try:
        d0 = dt.date.fromisoformat(first)
        d1 = dt.date.fromisoformat(last)
    except ValueError:
        return 0.0
    weeks = max(1.0, ((d1 - d0).days + 1) / 7.0)
    return max(0.0, min(100.0, hours / (37.5 * weeks) * 100))


# =====================================================================
def run_stage(script, label):
    print(f"  {label} ...", end=" ", flush=True)
    r = subprocess.run([sys.executable, os.path.join(PY, script)],
                       cwd=PY if script in ("engine.py", "scenarios.py",
                                            "export_json.py") else ROOT,
                       capture_output=True, text=True)
    if r.returncode:
        print("failed")
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(f"{script} failed")
    print("ok")


def main():
    ap = argparse.ArgumentParser(
        description="Load an acquisition target from the intake templates.")
    ap.add_argument("--dir", required=True,
                    help="directory holding the eight CSV templates")
    ap.add_argument("--org-name", required=True)
    ap.add_argument("--org-code", required=True)
    ap.add_argument("--dictionary", default=DEFAULT_DICT,
                    help="DATA_DICTIONARY.csv; the validation rules come from "
                         "here rather than from this script")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and report, write nothing")
    ap.add_argument("--analyse", action="store_true",
                    help="after loading, run the engine, checks, resourcing, "
                         "diligence, earned value and re-export the payload")
    ap.add_argument("--replace", action="store_true",
                    help="delete an existing org with this code first")
    ap.add_argument("--json-report", metavar="PATH",
                    help="also write the findings as JSON")
    args = ap.parse_args()

    import sqlite3
    specs = read_dictionary(args.dictionary)
    rep = Report()
    data, counts, rejected = {}, {}, {}

    for t in TEMPLATES:
        path = os.path.join(args.dir, f"{t}.csv")
        if not os.path.exists(path):
            rep.add("headers", "error", t, 0, None, None,
                    f"{t}.csv is not in {args.dir}",
                    "send the file, or state that the source system does not "
                    "hold it: a missing template is a scope limitation on the "
                    "assessment and an empty one looks like a clean book")
            continue
        header, raw = read_file(path)
        rows = validate_template(t, specs[t], header, raw, rep, rejected)
        data[t] = rows
        counts[t] = {"rows": len(raw), "ok": len(rows)}

    data = validate_references(data, specs, rep, rejected)
    data = validate_sense(data, rep)
    for t in data:
        counts[t]["ok"] = len(data[t])

    rep.print_summary(counts)
    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump(rep.items, fh, indent=2)
        print(f"findings written to {args.json_report}")

    blocking = [i for i in rep.errors() if i["stage"] == "headers"]
    if blocking:
        raise SystemExit("\nA mandatory column or a whole file is missing. "
                         "Nothing was loaded: fix the file and run again.")

    if args.dry_run:
        print("\nDry run: nothing was written. The validation above is the "
              "same validation the load uses, so a clean dry run means the "
              "load will not fail halfway.")
        return

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    existing = con.execute("SELECT org_id FROM org WHERE org_code=?",
                           (args.org_code,)).fetchone()
    if existing and not args.replace:
        raise SystemExit(
            f"\n{args.org_code} already exists. Use --replace to rebuild it, "
            f"or load under a different code. There is no merge path on "
            f"purpose: a partial merge into a book somebody is reading puts "
            f"two versions of the same engagement on one screen.")
    if existing:
        print(f"\nreplacing {args.org_code} (org {existing[0]})")
        delete_org(con, existing[0])

    org_id, loaded = load(con, args.org_name, args.org_code, data, specs, rep)
    print(f"\nloaded {args.org_name} as org {org_id}")
    for k in sorted(loaded):
        print(f"  {k:<18}{loaded[k]:>8,}")
    con.close()

    if args.analyse:
        print("\nrunning the analysis chain")
        for script, label in [("engine.py", "intelligence engine"),
                              ("scenarios.py", "scenarios"),
                              ("evm.py", "earned value"),
                              ("checks.py", "delivery checks"),
                              ("resourcing.py", "resource requirement"),
                              ("delivery_econ.py", "billing and model economics"),
                              ("export_json.py", "console payload"),
                              ("build_ui.py", "console")]:
            run_stage(script, label)
        print("\nOpen out/psa_console.html, or republish it.")


def delete_org(con, org_id):
    """
    Remove an organisation and everything hanging off it.

    Ordered children first. SQLite will not cascade for us here and a foreign
    key error halfway through a delete leaves exactly the half-loaded state
    this loader exists to avoid.
    """
    pids = [r[0] for r in con.execute(
        "SELECT project_id FROM project WHERE org_id=?", (org_id,))]
    q = ",".join("?" * len(pids)) or "NULL"
    for sql in [
        f"DELETE FROM project_task WHERE project_id IN ({q})",
        f"DELETE FROM project_plan_version WHERE project_id IN ({q})",
        f"DELETE FROM assignment WHERE project_id IN ({q})",
        f"DELETE FROM risk_issue WHERE project_id IN ({q})",
        f"DELETE FROM change_order WHERE project_id IN ({q})",
        f"DELETE FROM project_financial_month WHERE project_id IN ({q})",
        f"DELETE FROM revenue_recognition WHERE project_id IN ({q})",
        f"DELETE FROM project_forecast_snapshot WHERE project_id IN ({q})",
    ]:
        con.execute(sql, pids)
    con.execute("DELETE FROM time_entry WHERE org_id=?", (org_id,))
    for t in ("project_evm", "evm_summary", "works_practice", "works_metric",
              "works_cadence", "works_component", "billing_position",
              "billing_month", "billing_exception", "model_economics",
              "model_margin_band", "model_comparison", "data_quality_finding"):
        try:
            con.execute(f"DELETE FROM {t} WHERE org_id=?", (org_id,))
        except Exception:
            pass
    con.execute("DELETE FROM project WHERE org_id=?", (org_id,))
    con.execute("""DELETE FROM sow WHERE contract_id IN
                   (SELECT contract_id FROM contract WHERE org_id=?)""",
                (org_id,))
    con.execute("DELETE FROM contract WHERE org_id=?", (org_id,))
    con.execute("""DELETE FROM employee_skill WHERE employee_id IN
                   (SELECT employee_id FROM employee WHERE org_id=?)""",
                (org_id,))
    con.execute("UPDATE employee SET manager_employee_id=NULL WHERE org_id=?",
                (org_id,))
    con.execute("DELETE FROM employee WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM customer WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM practice WHERE org_id=?", (org_id,))
    con.execute("""DELETE FROM validation_finding WHERE batch_id IN
                   (SELECT batch_id FROM import_batch WHERE org_id=?)""",
                (org_id,))
    con.execute("DELETE FROM import_batch WHERE org_id=?", (org_id,))
    con.execute("DELETE FROM org WHERE org_id=?", (org_id,))
    con.commit()


if __name__ == "__main__":
    main()
