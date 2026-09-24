"""Create psa.db and apply the schema files in order."""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")
FILES = ["01_core.sql", "02_acquisition.sql", "03_intelligence.sql",
         "04_views.sql", "05_checks.sql", "06_diligence.sql",
         "07_delivery_econ.sql",
         "09_automation.sql"]

if os.path.exists(DB):
    os.remove(DB)

con = sqlite3.connect(DB)
con.execute("PRAGMA foreign_keys = ON")
for name in FILES:
    path = os.path.join(ROOT, "sql", name)
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()
    try:
        con.executescript(sql)
    except sqlite3.Error as exc:
        print(f"FAILED {name}: {exc}", file=sys.stderr)
        raise
    print(f"applied {name}")
con.commit()

rows = con.execute(
    "SELECT type, COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type"
).fetchall()
print(dict(rows))
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
print(f"{len(tables)} tables:")
print(", ".join(tables))
con.close()
