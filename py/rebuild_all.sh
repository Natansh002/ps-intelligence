#!/usr/bin/env bash
# Full rebuild, in dependency order.
#
# The order is not arbitrary and the pipeline will fail loudly if it is
# changed, which is deliberate. engine.py replaces the intelligence run rows
# that diligence, checks and resourcing all hang off by foreign key, so
# anything downstream of it has to be rebuilt after it rather than left
# pointing at a run that no longer exists.
#
#   build_db          schema
#   seed_core         organisations, people, customers, projects, time,
#                     financials, pipeline (calls seed_projects)
#   seed_acquisition  intake, staging, mapping, lineage, injected defects
#   engine            risk scores, margin predictions, capacity, red flags
#   scenarios         what-if scenarios and deal assessments
#   seed_diligence    CSAT responses, hypercare, tickets, performance reviews
#   diligence         the derived diligence pack, reads engine output
#   evm               cost and schedule indices, read by the checks below
#   checks            the delivery and implementation checks
#   resourcing        resource requirement by product line
#   delivery_econ     billing against recognition, billing model economics
#   automation        automation and AI opportunities, measured from the book
#   export_json       the payload the front end reads
#   build_ui          the single-file console
set -euo pipefail
cd "$(dirname "$0")/.."

python3 py/build_db.py
(cd py && python3 seed_core.py)
(cd py && python3 seed_acquisition.py)
(cd py && python3 engine.py)
(cd py && python3 scenarios.py)
python3 py/seed_diligence.py
python3 py/diligence.py
python3 py/evm.py
python3 py/checks.py
python3 py/resourcing.py
python3 py/delivery_econ.py
python3 py/automation.py
(cd py && python3 export_json.py)
python3 - <<'EOF'
import sqlite3
con = sqlite3.connect('psa.db')
con.execute('VACUUM')
con.execute('ANALYZE')
con.close()
print('vacuumed')
EOF
python3 py/build_ui.py
python3 py/verify.py
ls -lh psa.db out/psa_console.html out/psa_data.json
