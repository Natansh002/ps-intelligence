# PS Intelligence

A professional services operating console and acquisition diligence engine.

It reads a delivery organisation's own history and answers, on every run,
whether the business is profitable, whether the engagements are healthy,
whether the people are used well, whether the next six months can be
delivered, where automation would pay, and what to do this week.

**Nothing on any screen is typed in.** Every figure is a query result over one
dataset, computed by one engine, on one set of published definitions. That
constraint is the product: a services business usually has its utilisation in
one report, its margin in another and its project health in a third, each on a
slightly different definition, and the first time two of them disagree in a
meeting all three stop being used.

The console is one self-contained HTML file. No server, no build step at view
time, no network calls except web fonts.

---

## Quick start

Python 3.11+ and nothing else. No pip install: the pipeline is standard
library only.

```bash
make build      # schema, seed, engines, checks, export, console, verify
open docs/index.html
```

That takes about twenty seconds and ends with `148/148 checks passed`. If a
check fails, nothing publishes.

To run the browser tests as well — every view rendered in Chromium in both
themes, then every one of the 103 controls clicked and checked for doing
something:

```bash
pip install playwright && playwright install chromium
make test
```

---

## Loading a real organisation

The demonstration book is generated. To point the engine at an actual company,
send it the eight intake templates and load the result.

```bash
make templates          # writes out/intake_kit/ — blanks, a worked sample, the dictionary

# 1. Validate and write nothing. Do this first, every time.
python3 py/load_target.py --dir path/to/their/files \
    --org-name 'Their Company' --org-code TGT --dry-run

# 2. Load, then run the whole analysis chain and rebuild the console.
python3 py/load_target.py --dir path/to/their/files \
    --org-name 'Their Company' --org-code TGT --analyse
```

Seven validation stages run in a fixed order, and the order is not arbitrary:
headers, types, mandatory values, enumerations, keys, references, cross-field
sense. A type error makes every downstream check meaningless, so reporting
"end date before start date" against a cell that is not a date is noise that
hides the real finding.

Errors block a row. Warnings load it and raise a data quality finding, because
a target with a blank cost rate is still worth assessing and the blank is
itself a finding. A clean dry run means the load will not fail halfway: it is
the same validation the load uses.

Values the templates cannot carry — a margin target on a practice, a
utilisation target on a person — are substituted from the acquirer's own book
and recorded once per column with a count and a basis. Never as a silent
default: the check catalogue asserts that those targets exist, and defaulting
quietly would make those checks pass against numbers the loader invented.

---

## Getting data out, and changes back in

The console reads a snapshot and never writes to the database. Pretending
otherwise would be the worst option available: a screen that appears to save
and does not. So the round trip is explicit.

**Out.** Any view exports its tables as CSV, with the filter the reader
applied, and the whole snapshot exports as JSON. Both are read from what is on
the screen, so an exported figure and a quoted figure cannot disagree.

**In.** A payload written by `load_target.py --analyse` opens straight into
the hosted page with the Import button. That is what makes one deployment
usable for a second organisation without a rebuild and a redeploy.

**Back.** An override is captured in the console with a mandatory reason, held
in the browser, and exported as a change file:

```bash
python3 py/apply_changes.py changes.json --dry-run
python3 py/apply_changes.py changes.json --by "Your Name"
make build
```

or in one step, `make changes FILE=changes.json BY="Your Name"`.

The applier refuses a change whose recorded previous value no longer matches
the database — somebody else moved it while this was pending, and overwriting
that silently is how two people's decisions become one. It refuses any field
not on its allow list, refuses an override with no reason or no name, and
applies all or nothing. Every applied change writes the value and its audit
row in the same transaction: who decided, who applied, what changed, from
what, to what, when, and why.

---

## What is in here

```
sql/                 the schema, in dependency order
  01_core.sql        operating model: projects, time, financials, contracts, plans, audit
  02_acquisition.sql intake: templates, staging, validation, mapping, lineage
  03_intelligence.sql engine outputs: scores, drivers, benchmarks, flags, scenarios
  04_views.sql       reporting views, one definition per metric
  05_checks.sql      the delivery check catalogue and the metric definitions
  06_diligence.sql   satisfaction, phase duration, hypercare, rates, performance, tickets
  07_delivery_econ.sql billing against recognition, model economics, earned value
  09_automation.sql  automation and AI opportunities

py/                  the pipeline, in the order rebuild_all.sh runs it
  build_db.py        create the database from the schema
  seed_core.py       organisations, practices, people, customers
  seed_projects.py   projects, plans, tasks, time, financials, forecasts, pipeline
  seed_acquisition.py inject legacy defects, then record the intake that found them
  engine.py          risk scoring, margin prediction, capacity, red flags, the brief
  scenarios.py       what-if simulator and deal go/no-go
  seed_diligence.py  satisfaction responses, hypercare, tickets, performance reviews
  diligence.py       the derived diligence pack
  evm.py             cost and schedule performance indices
  checks.py          56 delivery rules in 9 families
  resourcing.py      resource requirement by product line
  delivery_econ.py   billing position and billing model economics
  automation.py      automation and AI opportunities, measured from the book
  export_json.py     one payload for the front end
  build_ui.py        assemble the console, hosted and standalone
  verify.py          148 assertions over the finished data
  load_target.py     load a real organisation from the intake templates
  apply_changes.py   apply a change file exported from the console
  make_templates.py  generate the intake kit
  test_ui.py         render every view in a real browser, both themes
  test_buttons.py    click every control and report anything that does nothing

ui/                  the console source: one file per section group
docs/                the built site, which is what GitHub Pages serves
```

**Scale of the demonstration book.** 94 tables, 9 views, 610 engagements,
163 people, 191,607 time entries, 24 months of financials.

---

## The pipeline

The order is enforced, not conventional. Each stage reads what the ones above
it wrote, so the chain fails loudly rather than quietly producing a screen
built on a stale run.

| # | Stage | What it does |
|---|---|---|
| 1 | Schema | Nine SQL files build the model |
| 2 | Load | The demonstration book, or a real one. The only stage that writes source data |
| 3 | Engine | Peer cohorts, margin prediction, risk scoring, capacity, red flags, the weekly brief |
| 4 | Scenarios | What-if levers and deal go/no-go, against the engine's own baseline |
| 5 | Diligence | Satisfaction, phase slip and its causes, hypercare, realised rate, the RAG register |
| 6 | Earned value | Cost and schedule indices per live engagement, and the quadrant each sits in |
| 7 | Checks | 56 rules over every record in scope, each with a population |
| 8 | Resourcing | Demand against supply per product line, hires and attrition backfill kept apart |
| 9 | Economics | Billing against recognition, and the economics of each commercial model |
| 10 | Automation | Measured baseline, named assumptions, modelled saving, kept separate |
| 11 | Verify | 148 assertions. If one fails, nothing publishes |

---

## Definitions worth knowing before reading a number

**Percent complete is earned plan**, the share of planned task hours whose
tasks are finished. It is not hours spent over estimate at completion, which is
how at least one major PSA product defines it. Under that definition earned
value equals actual cost by construction, the cost index is identically 1.00,
and the metric cannot report a cost problem however much money the engagement
is losing.

**Margin is on recognised revenue**, not invoiced, so it does not move when an
invoice is raised. Portfolio margin is the sum of the parts, never an average
of project margins.

**Billing and recognition are two signed positions and are never netted.**
Work delivered and not billed is somebody's to collect. Work billed and not
delivered is somebody's to deliver. A netted figure is the right number for a
cash forecast and the wrong number for either conversation.

**Utilisation is paired with realised rate**, because a team can lift
utilisation and lose money.

Every metric in the console carries its formula, numerator, denominator and
the vendor formula it diverges from, in the Delivery checks view.

---

## What the build enforces

Each of these is asserted at stage 11 and fails the build, so it cannot
quietly stop being true.

- **No AI change without confirmation.** Nothing predictive writes to
  financial or project data. The engine's health score sits beside the project
  manager's, and the divergence is the finding.
- **No hard-coded values.** Targets, thresholds and rates are configuration
  and are shown with the figures they judge.
- **Every important change is auditable.** Who, what, previous value, new
  value, when.
- **An override needs a reason.** A project manager can overrule the engine;
  the comment is mandatory and is kept with the override.
- **No AI step without a stated guardrail.** Every AI-typed automation
  opportunity names what stays with a person, and its saving is taken net of
  the time that review costs.
- **No rule can fail more records than it examined.** A check whose failures
  exceed its population is counting two grains and calling them one.

---

## What it is not

- **Not a PSA system.** It does not run delivery, approve time or raise
  invoices. It reads what a PSA holds and answers questions that product does
  not.
- **Not multi-user.** One reader, no login, no server, no write-back from the
  screen. SQLite rather than Postgres. A reporting and diligence surface, not a
  system of record.
- **Not live.** Each run is an as-of snapshot, so two runs are comparable and
  a figure can be reproduced.
- **The demonstration book is generated.** Internally consistent, and
  deliberately containing the problems the engine exists to find. No figure in
  it should be quoted.

---

## Hosting

`docs/` is a complete static site:

- `index.html` — the console
- `how-it-works.html` — the one-page explanation

The workflow in `.github/workflows/build.yml` rebuilds the database from
source on every push, runs the verification checks, clicks every control
in a real browser, and fails the build if any of that fails. It then enables
Pages itself and publishes `docs/`. There is no Pages setting to configure by
hand.

**Pages does not work on a private repository below a Team plan.** That is a
plan limit, not a build failure, so the workflow reports it and carries on
green rather than showing a red tick against a pipeline that is fine. Three
ways to get a hosted URL, in the order worth trying:

| | What it takes | What you get |
|---|---|---|
| Make the repository public | Nothing further | Pages, free |
| Move it to an organisation on a Team plan or above | Transfer to the org | Pages, private |
| Leave it private | Nothing | `docs/index.html` opens from the repository or a clone |

The third is not a workaround. The console is one self-contained file with no
server and no network calls, so opening it from a clone gives exactly what the
hosted page gives.

The demonstration data is generated (synthetic); see `NOTICE.md`.

---

## Licence

Internal material. See `NOTICE.md`.
