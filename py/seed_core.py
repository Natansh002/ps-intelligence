"""
Seed the PSA database with a synthetic 24-month operating history.

IMPORTANT: every figure produced here is generated demonstration data.
None of it is real company, customer or acquisition-target financial data.
The point is to give every intelligence feature real rows to compute over
so the calculations can be inspected and argued with.

Two organisations are created:
  OPCO  - the operating company's PS division
  CCG   - an acquisition target, deliberately shaped with the kinds of
          problems an acquirer needs to find (revenue concentration,
          thin-margin work, a methodology that overruns, key-person risk)
"""
import os
import random
import sqlite3
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

TODAY = date(2026, 8, 31)
HISTORY_START = date(2024, 9, 1)
RNG = random.Random(20260831)

# ---------------------------------------------------------------------------
# reference data
# ---------------------------------------------------------------------------
ROLES = [
    # role, bill rate, cost rate, share of headcount, billable
    ("Practice Director",    255, 150, 0.05, 1),
    ("Solution Architect",   230, 134, 0.12, 1),
    ("Project Manager",      190, 110, 0.14, 1),
    ("Senior Consultant",    200, 116, 0.26, 1),
    ("Consultant",           170,  96, 0.22, 1),
    ("Technical Consultant", 210, 122, 0.15, 1),
    ("Business Analyst",     145,  78, 0.06, 1),
]

FIRST = ["Aisha", "Marcus", "Priya", "Daniel", "Chantal", "Omar", "Nadia", "Liam",
         "Sofia", "Ethan", "Rina", "Jacob", "Maya", "Noah", "Leila", "Owen",
         "Tara", "Felix", "Zara", "Hugo", "Ines", "Caleb", "Naomi", "Isaac",
         "Elena", "Milo", "Yasmin", "Roan", "Bianca", "Dev", "Kiran", "Anya",
         "Theo", "Simone", "Andre", "Nora", "Kwame", "Iris", "Bruno", "Talia",
         "Hana", "Emile", "Lucia", "Sven", "Amara", "Nils", "Rosa", "Kaito",
         "Freya", "Damon", "Meera", "Jonah", "Sela", "Arun", "Vera", "Cyrus"]
LAST = ["Okafor", "Whitfield", "Raman", "Boucher", "Lemieux", "Haddad", "Farooq",
        "Donnelly", "Marchetti", "Kowalski", "Sandhu", "Brennan", "Duval",
        "Nakamura", "Osei", "Halvorsen", "Vasquez", "Petrov", "Ibrahim",
        "Lindqvist", "Moreau", "Byrne", "Castellano", "Ng", "Aluko", "Reinhart",
        "Silva", "Trudeau", "Ferreira", "Bhandari", "Almeida", "Kaur",
        "Novak", "Delacroix", "Cardoso", "Sorensen", "Mensah", "Rowe"]

# Target margin is a policy number set by the business, not a residual of the
# estimate. Planned cost comes from the practice blended cost rate instead, so
# a project sold above target shows a real cushion and one sold below shows a
# real problem.
OPCO_PRACTICES = [
    ("SR-FIN",  "Finance"          ,           "Non-for-Profit", 36.0, 72.0),
    ("SR-HRP",  "HR & Payroll"       ,      "Non-for-Profit", 35.0, 70.0),
    ("ED-HRP",  "HR & Payroll (Edu)"  ,     "K12",            35.0, 72.0),
    ("ED-FIN",  "Finance (Edu)"  ,          "K12",            34.0, 70.0),
    ("MYSR",    "Self-Service Portals",     "Cross-segment",  37.0, 68.0),
    ("DATA",    "Data & Integrations",       "Cross-segment",  38.0, 74.0),
]
CCG_PRACTICES = [
    ("ERP-IMP", "ERP Implementation",        "Commercial",     32.0, 75.0),
    ("PAY",     "Payroll Services",          "Commercial",     32.0, 75.0),
    ("INT",     "Integrations",              "Commercial",     33.0, 75.0),
    ("PMO",     "Advisory & PMO",            "Commercial",     30.0, 70.0),
]

SKILLS = [
    ("Business Central Finance", "ERP"), ("Business Central HRP", "ERP"),
    ("Legacy Payroll", "Payroll"), ("Legacy HR", "HR"),
    ("Canadian Payroll Legislation", "Payroll"), ("Benefits Administration", "HR"),
    ("Absence & Scheduling", "HR"), ("AL Development", "Engineering"),
    ("Power BI", "Data"), ("Data Migration", "Data"),
    ("Azure Integration", "Engineering"), ("Requirements Analysis", "Advisory"),
    ("Change Management", "Advisory"), ("Project Management", "Delivery"),
    ("Testing & UAT", "Delivery"), ("General Ledger Design", "ERP"),
]

K12_NAMES = [
    "Riverbend District School Board", "Cedar Hills School Division",
    "Northshore Catholic DSB", "Prairie Vista School Division",
    "Harbour Point DSB", "Maplewood Catholic DSB", "Silver Creek School District",
    "Aurora Lakes DSB", "Fort Alder School Division", "Grandview DSB",
    "Kettle Valley School District", "Trillium Ridge DSB",
    "Birchmount Catholic DSB", "Sandstone School Division",
    "Clearwater Bay DSB", "Highland Park School District",
]
NFP_NAMES = [
    "Meridian Health Foundation", "Wellspring Community Services",
    "Beacon Housing Society", "Orchard Family Network",
    "Lakeside Seniors Care", "Northlight Arts Council",
    "Anchor Youth Services", "Greenfield Food Alliance",
    "Sable Island Conservancy", "Redwood Settlement Services",
    "Crossroads Employment Trust", "Havenbrook Support Network",
]
COMMERCIAL_NAMES = [
    "Altura Manufacturing", "Cobalt Logistics Group", "Verdant Agrifoods",
    "Stonebridge Insurance", "Kestrel Energy Services", "Marlow Retail Group",
    "Ironwood Construction", "Pelham Pharmaceuticals", "Quarry Lane Media",
    "Tidewater Marine", "Copperfield Utilities", "Norwood Automotive",
    "Vantage Financial Partners", "Brackenridge Mining",
    "Solstice Hospitality Group", "Eastgate Distribution",
]

PROJECT_TYPES = ["Implementation", "Upgrade", "Migration", "Integration",
                 "Advisory", "Managed Service"]
OPCO_PRODUCTS = {
    "SR-FIN": ["Finance Platform"], "SR-HRP": ["HR-Payroll Platform"],
    "ED-HRP": ["HR-Payroll (Edu)"], "ED-FIN": ["Finance (Edu)"],
    "MYSR": ["Self-Service Portal"], "DATA": ["Integration Platform", "Power BI Analytics"],
}
CCG_PRODUCTS = {
    "ERP-IMP": ["Legacy ERP Suite"], "PAY": ["PayCore"],
    "INT": ["Connector Platform"], "PMO": ["Advisory"],
}
# Methodology is recorded so delivery risk can be attributed to it (req 36).
# Partner-Led is deliberately the weak one.
METHODOLOGIES = [
    ("Standard Waterfall", 0.34, 1.06, 0.30),
    ("Agile Hybrid",       0.31, 1.04, 0.26),
    ("Rapid Deploy",       0.20, 1.12, 0.34),
    ("Partner-Led",        0.15, 1.28, 0.52),
]
PHASES = ["Initiate", "Analyse", "Configure", "Test", "Deploy", "Stabilise"]
TIME_CATEGORIES_BILLABLE = ["Consulting", "Configuration", "Testing", "Training",
                            "Project Management", "Data Migration"]
TIME_CATEGORIES_NONBILL = ["Rework", "Travel", "Over-service", "Internal"]


def month_key(d):
    return d.strftime("%Y-%m")


def month_range(start, end):
    out, cur = [], date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        out.append(cur)
        cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
    return out


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def pick_role():
    r = RNG.random()
    acc = 0.0
    for role in ROLES:
        acc += role[3]
        if r <= acc:
            return role
    return ROLES[-1]


def pick_methodology():
    r = RNG.random()
    acc = 0.0
    for m in METHODOLOGIES:
        acc += m[1]
        if r <= acc:
            return m
    return METHODOLOGIES[-1]


# ---------------------------------------------------------------------------
class Seeder:
    def __init__(self, con):
        self.con = con
        self.cur = con.cursor()
        self.skill_ids = {}
        self.name_pool = set()

    def q(self, sql, params=()):
        self.cur.execute(sql, params)
        return self.cur.lastrowid

    def person_name(self):
        for _ in range(400):
            n = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
            if n not in self.name_pool:
                self.name_pool.add(n)
                return n
        n = f"{RNG.choice(FIRST)} {RNG.choice(LAST)} {len(self.name_pool)}"
        self.name_pool.add(n)
        return n

    # -- reference ----------------------------------------------------------
    def seed_skills(self):
        for name, family in SKILLS:
            sid = self.q("INSERT INTO skill(skill_name, skill_family) VALUES (?,?)",
                         (name, family))
            self.skill_ids[name] = sid

    # -- org ----------------------------------------------------------------
    def seed_org(self, code, name, role, acquired_on=None, migration_state="n/a"):
        return self.q(
            """INSERT INTO org(org_code, org_name, org_role, currency,
                               fiscal_year_start_month, acquired_on, migration_state)
               VALUES (?,?,?,'CAD',7,?,?)""",
            (code, name, role, acquired_on, migration_state))

    def seed_practices(self, org_id, defs):
        out = {}
        for code, pname, seg, margin, util in defs:
            pid = self.q(
                """INSERT INTO practice(org_id, practice_code, practice_name, segment,
                                        target_margin_pct, target_utilization_pct)
                   VALUES (?,?,?,?,?,?)""",
                (org_id, code, pname, seg, margin, util))
            out[code] = pid
        return out

    # -- employees ----------------------------------------------------------
    def seed_employees(self, org_id, practices, headcount, prefix,
                       rate_factor=1.0, legacy=False, util_target=70.0):
        emps = []
        codes = list(practices.keys())
        for i in range(headcount):
            role, bill, cost, _share, billable = pick_role()
            pcode = codes[i % len(codes)] if i < len(codes) else RNG.choice(codes)
            seniority = RNG.uniform(0.93, 1.09)
            name = self.person_name()
            start = HISTORY_START - timedelta(days=RNG.randint(200, 2400))
            eid = self.q(
                """INSERT INTO employee(org_id, legacy_employee_id, employee_code,
                        employee_name, role, department, practice_id, location, geography,
                        employment_type, cost_rate, billing_rate, weekly_capacity_hours,
                        utilization_target_pct, start_date, is_billable, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (org_id,
                 f"LEG-EMP-{i+1:04d}" if legacy else None,
                 f"{prefix}{i+1:03d}", name, role,
                 practices and pcode, practices[pcode],
                 RNG.choice(["Toronto", "Vancouver", "Calgary", "Halifax", "Winnipeg",
                             "Ottawa", "Montreal", "Remote"]),
                 RNG.choice(["Central Canada", "Western Canada", "Atlantic Canada"]),
                 RNG.choices(["Full Time", "Contractor", "Part Time"],
                             weights=[0.86, 0.10, 0.04])[0],
                 round(cost * rate_factor * seniority, 2),
                 round(bill * rate_factor * seniority, 2),
                 37.5, util_target, start.isoformat(),
                 billable))
            emps.append({"id": eid, "name": name, "role": role,
                         "practice": pcode, "practice_id": practices[pcode],
                         "cost": round(cost * rate_factor * seniority, 2),
                         "bill": round(bill * rate_factor * seniority, 2),
                         "start": start, "capacity": 37.5,
                         "billable": bool(billable)})
            for sname in RNG.sample(list(self.skill_ids), RNG.randint(2, 5)):
                self.q("""INSERT OR IGNORE INTO employee_skill(employee_id, skill_id, proficiency)
                          VALUES (?,?,?)""",
                       (eid, self.skill_ids[sname], RNG.randint(2, 5)))
        # managers
        directors = [e for e in emps if e["role"] == "Practice Director"] or emps[:3]
        for e in emps:
            if e not in directors:
                self.cur.execute("UPDATE employee SET manager_employee_id=? WHERE employee_id=?",
                                 (RNG.choice(directors)["id"], e["id"]))
        return emps

    # -- customers ----------------------------------------------------------
    def seed_customers(self, org_id, names, prefix, ctype, industries, legacy=False):
        out = []
        for i, name in enumerate(names):
            cs = RNG.choices(["Active", "At Risk", "Churned", "Dormant"],
                             weights=[0.80, 0.10, 0.06, 0.04])[0]
            start = HISTORY_START - timedelta(days=RNG.randint(0, 1500))
            cid = self.q(
                """INSERT INTO customer(org_id, legacy_customer_id, customer_code,
                        customer_name, customer_type, industry, region, geography,
                        account_owner, customer_status, contract_start, contract_end,
                        csat_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (org_id,
                 f"LEG-CUST-{i+1:04d}" if legacy else None,
                 f"{prefix}{i+1:03d}", name, ctype,
                 RNG.choice(industries),
                 RNG.choice(["Ontario", "British Columbia", "Alberta", "Manitoba",
                             "Saskatchewan", "Nova Scotia", "Quebec"]),
                 RNG.choice(["Central Canada", "Western Canada", "Atlantic Canada"]),
                 self.person_name(), cs, start.isoformat(),
                 (start + timedelta(days=RNG.randint(900, 2200))).isoformat(),
                 round(RNG.uniform(6.2, 9.4), 1)))
            out.append({"id": cid, "name": name})
        return out


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    s = Seeder(con)
    s.seed_skills()

    opco = s.seed_org("OPCO", "Operating Company - Professional Services", "operating")
    ccg = s.seed_org("CCG", "ABC Company", "acquisition_target",
                     acquired_on="2026-07-15", migration_state="assessing")

    opco_pr = s.seed_practices(opco, OPCO_PRACTICES)
    ccg_pr = s.seed_practices(ccg, CCG_PRACTICES)

    opco_emps = s.seed_employees(opco, opco_pr, 108, "E-", 1.0, False, 70.0)
    # The target runs on lower rates and carries more people than its
    # signed work supports. Underutilisation is one of the findings.
    ccg_emps = s.seed_employees(ccg, ccg_pr, 52, "C-", 0.97, True, 75.0)

    opco_cust = s.seed_customers(opco, K12_NAMES, "CU-", "Public Sector",
                                 ["K-12 Education"]) + \
        s.seed_customers(opco, NFP_NAMES, "CN-", "Nonprofit",
                         ["Health & Social Services", "Housing", "Arts & Culture",
                          "Community Services"])
    ccg_cust = s.seed_customers(ccg, COMMERCIAL_NAMES, "LC-", "Commercial",
                                ["Manufacturing", "Logistics", "Insurance", "Energy",
                                 "Retail", "Construction", "Utilities", "Media"],
                                legacy=True)

    con.commit()

    def practice_economics(org_id, emps, defs):
        """Blended cost rate per practice, from the people actually in it."""
        by = {}
        for e in emps:
            by.setdefault(e["practice"], []).append(e["cost"])
        return {code: {"cost_rate": round(sum(by[code]) / len(by[code]), 2)
                                   if by.get(code) else 113.0,
                       "target_margin": margin}
                for code, _n, _seg, margin, _u in defs}

    opco_econ = practice_economics(opco, opco_emps, OPCO_PRACTICES)
    ccg_econ = practice_economics(ccg, ccg_emps, CCG_PRACTICES)

    print(f"orgs: OPCO={opco} CCG={ccg}")
    print(f"practices: {len(opco_pr)} + {len(ccg_pr)}")
    print(f"employees: {len(opco_emps)} + {len(ccg_emps)}")
    print(f"customers: {len(opco_cust)} + {len(ccg_cust)}")

    # hand off to the project/time generator
    import seed_projects
    seed_projects.build(con, s, RNG, TODAY, HISTORY_START,
                        {"org_id": opco, "practices": opco_pr, "emps": opco_emps,
                         "customers": opco_cust, "products": OPCO_PRODUCTS,
                         "prefix": "PRJ", "legacy": False, "concentration": 0.0,
                         "n_projects": 439, "rate_pressure": 1.0,
                         "econ": opco_econ},
                        {"org_id": ccg, "practices": ccg_pr, "emps": ccg_emps,
                         "customers": ccg_cust, "products": CCG_PRODUCTS,
                         "prefix": "LEG", "legacy": True, "concentration": 0.62,
                         "n_projects": 168, "rate_pressure": 0.92,
                         "econ": ccg_econ})
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
