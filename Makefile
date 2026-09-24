# PS Intelligence
#
# The pipeline is standard-library Python. There is no install step and no
# dependency file for the build, which is the reason `make build` works on a
# clean machine with nothing but Python on it. Playwright is needed only for
# the browser tests and is kept out of the build path for that reason.

PY ?= python3

.PHONY: help build rebuild verify test templates clean site changes

help:
	@echo "build      schema, seed, engines, checks, export, console, verify"
	@echo "verify     run the assertions against the current database"
	@echo "test       render every view, then click every control (needs playwright)"
	@echo "templates  write the intake kit to out/intake_kit/"
	@echo "site       rebuild docs/ from the current database only"
	@echo "clean      remove the database and build outputs"

# The whole chain, in dependency order. rebuild_all.sh is the single source of
# that order; duplicating it here would be a second place for it to drift.
build:
	bash py/rebuild_all.sh

rebuild: clean build

verify:
	$(PY) py/verify.py

test:
	$(PY) py/test_ui.py
	$(PY) py/test_buttons.py

templates:
	$(PY) py/make_templates.py

# Rebuild the front end without re-running the engines. Useful while working
# on the console; not a substitute for `build`, because it cannot notice that
# the payload it is reading is stale.
site:
	cd py && $(PY) export_json.py
	$(PY) py/build_ui.py

# Apply a change file exported from the console's Pending changes view.
#   make changes FILE=changes.json BY="Your Name"
changes:
	@test -n "$(FILE)" || (echo "FILE=changes.json is required"; exit 1)
	$(PY) py/apply_changes.py "$(FILE)" --dry-run
	@test -n "$(BY)" || (echo; echo "Set BY=\"Your Name\" to apply it."; exit 1)
	$(PY) py/apply_changes.py "$(FILE)" --by "$(BY)"
	$(MAKE) build

clean:
	rm -f psa.db psa.db-journal psa.db-wal
	rm -rf out/psa_console.html out/psa_data.json out/_preview.html out/shots
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
