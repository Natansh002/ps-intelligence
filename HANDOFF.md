# Handoff: PS Intelligence

Read this first. It is written for whoever picks the package up, including
Claude Code. It says what the repository is, what state the remote is in
right now, and exactly what to do. Nothing here needs interpreting.

## What this is

An AI-driven professional services operating console and acquisition
diligence engine. It reads a delivery organisation's own history and answers,
on every run: is PS profitable, are the engagements healthy, are the people
used well, can the next six months be delivered, where would automation pay,
and what to do this week.

Nothing on any screen is typed in. Every figure is a query result over one
dataset, computed by one engine, on one published set of definitions.

Standard library Python and SQLite. No framework, no dependency file for the
build, no server, no network calls at view time.

```
make build      # schema, seed, engines, checks, export, console, verify
open docs/index.html
```

That takes about twenty seconds and must end `148/148 checks passed`. If it
does not, stop and report it. Nothing publishes on a failed check, by design.

## The remote, and what is wrong with it

    github.com/natansh002/ps-intelligence   private, main

Last pushed commit: `355c668`. On that run, `build` passed everything. The
`deploy` job failed:

```
Error: Failed to create deployment (status: 404) with build version 355c668
Ensure GitHub Pages has been enabled:
  https://github.com/natansh002/ps-intelligence/settings/pages
```

That is not a defect in the code. GitHub Pages had never been enabled on the
repository, so `deploy-pages` had nothing to deploy into. Two things caused
it and both are fixed in this package:

1. **The README and the workflow disagreed.** The README said to set Pages to
   "deploy from a branch, `/docs`". The workflow deploys an uploaded
   artifact, which needs Source: GitHub Actions. Only one of those can be
   configured at a time, so following the README guaranteed the 404.
2. **The unavailable case failed the run.** Pages on a private repository
   also needs a Team or Enterprise plan. That is a plan fact, not a broken
   build, and it should not produce a red tick against a pipeline where every
   check passed.

The workflow now calls `actions/configure-pages@v5` with `enablement: true`,
so it sets the Pages source itself and there is nothing to configure by hand.
That step runs with `continue-on-error`, and its outcome gates both the
artifact upload and the deploy job, so where Pages genuinely is not available
the run logs why and stays green.

## The task

Three files differ from `355c668`. Everything else in this package is
identical to what is already on `main`.

```
.github/workflows/build.yml     the fix above
README.md                       hosting section rewritten to match; 142 -> 148
Makefile                        help text: 142 -> 148
```

Do this:

```bash
cd <your clone of natansh002/ps-intelligence>
git checkout main && git pull

# copy the three files from this package over the clone
cp <package>/.github/workflows/build.yml .github/workflows/build.yml
cp <package>/README.md README.md
cp <package>/Makefile Makefile

git diff                       # expect only those three files

git config user.email "natansh88sharma@gmail.com"
git config user.name  "Natansh Sharma"

git add .github/workflows/build.yml README.md Makefile
git commit -F- <<'MSG'
Let a missing Pages plan skip the deploy instead of failing the build

Pages on a private repository needs Team or Enterprise, and had never been
enabled on this one, so deploy-pages 404ed against a pipeline where every
check had passed. A red tick that does not mean anything trains the reader
to ignore the one that does.

configure-pages now runs with continue-on-error and its outcome gates the
upload and the deploy job. It also enables Pages itself where the plan
allows, which removes the manual setting the README described and the
workflow contradicted: the README said deploy from a branch, the workflow
deployed an artifact, and only one of those can be configured at a time.
MSG

git push
```

Then watch the run at
`https://github.com/natansh002/ps-intelligence/actions`.

**Expected outcomes, and what each means:**

| Result | Meaning |
|---|---|
| Both jobs green, site live at `natansh002.github.io/ps-intelligence/` | Done |
| `build` green, `deploy` skipped, log says Pages unavailable | Plan limit. The repo is private on a plan below Team. Working as designed |
| `build` red | A real regression. Read the failing check and stop |

## Rules for this repository

Four constraints are asserted at build time and fail the build if broken.
They are requirements, not preferences, and they are why the verify stage
exists.

- **No AI change without confirmation.** Nothing predictive writes to
  financial or project data. The engine's health score sits beside the
  project manager's, and the divergence is the finding.
- **No hard-coded values.** Targets, thresholds and rates are configuration
  and are shown next to the figures they judge.
- **Every important change is auditable.** Who, what, previous value, new
  value, when.
- **An override needs a reason.** A project manager can overrule the engine.
  The comment is mandatory and is kept with the override.

Two more things not to undo:

- **The console never writes to the database.** Overrides are captured in the
  browser, exported as a change file, and applied by `py/apply_changes.py`,
  which refuses a change whose previous value has moved since the decision
  was made. A screen that appears to save and does not is the worst available
  option.
- **`psa.db` is gitignored.** It is an output, 31 MB, rebuilt in twenty
  seconds. If a real organisation is ever loaded through `load_target.py`,
  that database must not be committed.

## Do not

- **Do not import git history from this package.** The `.git` directory is
  deliberately excluded. A copy of this tree was rewritten locally to change
  commit authorship, which diverged its hashes from the remote. Pushing that
  would need a force push and would rewrite `main` for no benefit. Copy the
  three files onto the existing clone instead.
- **Do not force-push `main`.**
- **Do not commit `psa.db`, `out/`, or anything under `out/intake_kit/` that
  has been filled in with real customer data.**
- **The demonstration data is generated (synthetic).** See `NOTICE.md`.

## What is in the package

```
sql/     9 files, 94 tables, in dependency order
py/      26 files: seed, engine, scenarios, diligence, earned value,
         56 delivery checks, resourcing, delivery economics,
         automation opportunities, export, console build, verify,
         real-organisation loader, change applier, UI and control tests
ui/      13 files plus vendored brand logos
docs/    the built static site: index.html, how-it-works.html, .nojekyll
out/intake_kit/   the eight intake templates, a worked sample, the dictionary
README.md         full documentation, definitions, pipeline
NOTICE.md         what to settle before making this public
HANDOFF.md        this file
```

The pipeline order is enforced rather than conventional. Each stage reads
what the ones above it wrote, so the chain fails loudly instead of quietly
producing a screen built on a stale run:

`build_db -> seed_core -> seed_projects -> seed_acquisition -> engine ->
scenarios -> seed_diligence -> diligence -> evm -> checks -> resourcing ->
delivery_econ -> works -> automation -> export_json -> build_ui -> verify`

`py/rebuild_all.sh` is the single source of that order. `make build` runs it.

## Definitions worth knowing before quoting a number

**Percent complete is earned plan**, the share of planned task hours whose
tasks are finished. It is not hours spent over estimate at completion, which
is how at least one major PSA product defines it. Under that definition
earned value equals actual cost by construction, the cost performance index
is identically 1.00, and the metric cannot report a cost problem however much
money the engagement is losing.

**Margin is on recognised revenue**, not invoiced, so it does not move when
an invoice is raised. Portfolio margin is the sum of the parts, never an
average of project margins.

**Billing and recognition are two signed positions and are never netted.**
Work delivered and not billed is somebody's to collect. Work billed and not
delivered is somebody's to deliver. A netted figure is the right number for a
cash forecast and the wrong number for either conversation.

**Utilisation is always paired with realised rate**, because a team can lift
utilisation and lose money.

Every metric carries its formula, numerator, denominator and the vendor
formula it diverges from, in the Delivery checks view of the console.

## The data in it is generated

No customer, employee or financial record in this package is real. The
demonstration book is produced from a fixed seed and is internally
consistent, and it deliberately contains the problems the engine exists to
find. Every screen says so. No figure in it should be quoted.

To point the engine at an actual company, send the eight intake templates in
`out/intake_kit/` and load the result:

```bash
python3 py/load_target.py --dir path/to/files --org-name 'Their Company' \
    --org-code TGT --dry-run      # validates, writes nothing. Do this first
python3 py/load_target.py --dir path/to/files --org-name 'Their Company' \
    --org-code TGT --analyse      # loads, runs the chain, rebuilds the console
```

Seven validation stages run in a fixed order: headers, types, mandatory
values, enumerations, keys, references, cross-field sense. The order is not
arbitrary. A type error makes every downstream check meaningless, so
reporting "end date before start date" against a cell that is not a date is
noise that hides the real finding.

Errors block a row. Warnings load it and raise a data quality finding,
because a target with a blank cost rate is still worth assessing and the
blank is itself a finding.
