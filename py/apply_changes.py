"""
Apply a change file exported from the console to the database.

    python3 py/apply_changes.py changes.json --dry-run
    python3 py/apply_changes.py changes.json --by "Natansh Sharma"

The console reads a snapshot and never writes. An override captured there is
held in the browser and exported as a file, and this is what turns that file
into a database change. The separation is the point rather than an accident of
architecture: the requirement this system is built against is that nothing
reaches financial or project data without an explicit human confirmation, and a
file somebody exports, reads and runs is that confirmation made physical.

Four rules, each of which exists because the alternative has a failure mode:

  A reason is mandatory. An override with no reason is indistinguishable from
  a mistake by the time anybody reads it, which is always months later.

  The previous value has to still match. The change file records what the
  value was when the decision was made. If the database has moved since,
  somebody else already acted and applying this would erase their decision
  without either of them knowing.

  The field has to be on the allow list. A change file is a document that
  arrives from outside the database, so the set of things it may touch is
  fixed here rather than taken from the file.

  Every applied change writes its audit row in the same transaction as the
  value. An audit trail that can be missing for the one change somebody
  disputes is not an audit trail.
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "psa.db")

# What a change file is permitted to touch, and what each field will accept.
# Deliberately narrow. Widening it is a decision somebody makes here, in the
# repository, rather than something a file can do by asserting a field name.
ALLOWED = {
    ("project", "project_health"): {
        "key": "project_id",
        "values": {"Green", "Yellow", "Red"},
        "label": "project_code",
    },
    ("project", "project_status"): {
        "key": "project_id",
        "values": {"Not Started", "In Flight", "On Hold", "Complete",
                   "Cancelled"},
        "label": "project_code",
    },
}

MIN_REASON = 8


def load(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != "psa-change-file/v1":
        raise SystemExit(
            f"{path} is not a change file. Export one from the console's "
            f"Pending changes view.")
    changes = doc.get("changes") or []
    if not changes:
        raise SystemExit(f"{path} contains no changes.")
    return doc, changes


def validate(con, doc, changes):
    """
    Check every change against the current database before writing any of
    them. All or nothing: a half-applied file is the state nobody can reason
    about, because the console will show some of the decisions and not others.
    """
    org = con.execute("SELECT org_id, org_name FROM org WHERE org_code=?",
                      (doc.get("org_code"),)).fetchone()
    if not org:
        raise SystemExit(
            f"no organisation with code {doc.get('org_code')!r} in this "
            f"database. The change file was exported from a different book.")
    org_id, org_name = org

    ok, bad = [], []
    for i, c in enumerate(changes, 1):
        table = c.get("entity_table")
        field = c.get("field_name")
        spec = ALLOWED.get((table, field))
        where = f"change {i} ({c.get('entity_label') or c.get('entity_pk')})"

        if spec is None:
            bad.append((where, f"{table}.{field} is not a field a change file "
                               f"may touch"))
            continue
        if not (c.get("reason") or "").strip() or \
                len(c["reason"].strip()) < MIN_REASON:
            bad.append((where, "no reason recorded, and a reason is mandatory"))
            continue
        if not (c.get("changed_by") or "").strip():
            bad.append((where, "no name recorded against the decision"))
            continue
        if str(c.get("new_value")) not in spec["values"]:
            bad.append((where, f"{c.get('new_value')!r} is not one of "
                               f"{sorted(spec['values'])}"))
            continue

        row = con.execute(
            f"SELECT {field}, {spec['label']} FROM {table} "
            f"WHERE {spec['key']}=? AND org_id=?",
            (c.get("entity_pk"), org_id)).fetchone()
        if row is None:
            bad.append((where, f"no {table} {c.get('entity_pk')} in "
                               f"{org_name}"))
            continue
        current, label = row
        if str(current) != str(c.get("old_value")):
            bad.append((
                where,
                f"was {c.get('old_value')!r} when the decision was made and "
                f"is {current!r} now. Somebody else has moved it; re-read the "
                f"console and decide again rather than overwriting them."))
            continue
        if str(current) == str(c.get("new_value")):
            bad.append((where, f"already {current!r}, nothing to change"))
            continue
        ok.append((c, org_id, label, current, spec))
    return ok, bad, org_id, org_name


def apply(con, ok, by, source_file):
    """Write the value and its audit row together, in one transaction."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    cur = con.cursor()
    for c, org_id, label, current, spec in ok:
        table, field = c["entity_table"], c["field_name"]
        cur.execute(
            f"UPDATE {table} SET {field}=? WHERE {spec['key']}=? AND org_id=?",
            (c["new_value"], c["entity_pk"], org_id))
        if cur.rowcount != 1:
            raise RuntimeError(
                f"{table} {c['entity_pk']} matched {cur.rowcount} rows on "
                f"update. Rolling back.")
        # The person who decided and the person who applied are two different
        # facts and both matter: one owns the judgement, the other ran it.
        cur.execute("""
            INSERT INTO audit_log
              (entity_table, entity_pk, field_name, old_value, new_value,
               changed_by, changed_at, change_source, is_override,
               change_reason, approved_by)
            VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
            (table, c["entity_pk"], field, str(current), str(c["new_value"]),
             c["changed_by"], now, f"ui via {os.path.basename(source_file)}",
             c["reason"], by))
    con.commit()


def main():
    ap = argparse.ArgumentParser(
        description="Apply a console change file to the database.")
    ap.add_argument("file", help="the change file exported from the console")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and report, write nothing")
    ap.add_argument("--by", default="",
                    help="who is applying this, recorded as the approver")
    args = ap.parse_args()

    if not args.dry_run and not args.by.strip():
        raise SystemExit(
            "--by is required when applying. The person who decided is in the "
            "file; the person who applied it is not, and the audit row wants "
            "both.")

    doc, changes = load(args.file)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")
    ok, bad, org_id, org_name = validate(con, doc, changes)

    print(f"\n{args.file}")
    print(f"  exported {doc.get('exported_at', 'unknown')} "
          f"against {org_name} as of {doc.get('as_of')}")
    print(f"  {len(changes)} change{'' if len(changes) == 1 else 's'}: "
          f"{len(ok)} will apply, {len(bad)} rejected")

    if ok:
        print("\nWill apply")
        for c, _org, label, current, _spec in ok:
            print(f"  {label:<14}{c['field_name']:<18}"
                  f"{str(current):<8} -> {str(c['new_value']):<8}"
                  f"  {c['changed_by']}")
            print(f"  {'':<14}{c['reason']}")
    if bad:
        print("\nRejected")
        for where, why in bad:
            print(f"  {where}: {why}")

    if args.dry_run:
        print("\nDry run: nothing was written.")
        return
    if bad:
        raise SystemExit(
            "\nNothing was written. Every change in the file has to be valid, "
            "because a half-applied file leaves the console showing some "
            "decisions and not others. Fix the file, or remove the rejected "
            "changes in the console, and run it again.")

    apply(con, ok, args.by.strip(), args.file)
    con.close()
    print(f"\nApplied {len(ok)}, each with an audit row naming "
          f"{args.by.strip()} as the approver.")
    print("Run `make build` to rebuild the console against the new values.")


if __name__ == "__main__":
    main()
