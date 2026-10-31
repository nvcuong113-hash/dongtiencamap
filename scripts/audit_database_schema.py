#!/usr/bin/env python3
"""Read-only whole-source and SQLite schema audit for QUANTS PRO."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quants.config import DB_PATH
from quants.schema_audit import audit_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_PATH), help="SQLite database (read-only)")
    parser.add_argument("--baseline", default=None, help="Optional earlier backup for row continuity comparison")
    parser.add_argument("--json-out", default=None, help="Optional path to write the JSON report")
    args = parser.parse_args()
    report = audit_database(args.db_path, ROOT, args.baseline)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        out = Path(args.json_out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    print("QUANTS PRO — DATABASE SCHEMA AUDIT")
    print(f"Database: {report['database_path']}")
    print(f"Overall schema: {report['overall_schema_status']}")
    print(f"Production readiness: {report['production_readiness']}")
    print(f"Tables: {report['schema']['actual_table_count']}/{report['schema']['expected_table_count']}")
    print(f"Static SQL: {report['static_sql_audit']['compiled']}/{report['static_sql_audit']['static_statements']} compiled; "
          f"{report['static_sql_audit']['dynamic_statement_count']} dynamic/non-literal statements require review")
    print(f"Foreign-key enforcement on audit connection: {report['application_connection_foreign_keys']}")
    print(f"Default-value drift requiring review: {bool(report['schema'].get('default_mismatches'))}")
    print(f"Data continuity review required: {report.get('data_continuity_review_required', False)}")
    print(f"Integrity check: {report['data_checks']['integrity_check']}")
    print("Table rows:")
    for table, count in report["table_counts"].items():
        print(f"  {table}: {count}")
    if report.get("baseline_comparison"):
        print("Baseline comparison:")
        for table, values in report["baseline_comparison"].items():
            print(f"  {table}: {values['baseline_rows']} -> {values['current_rows']} (delta {values['delta_rows']:+})")
    if args.json_out:
        print(f"JSON report: {Path(args.json_out).expanduser().resolve()}")
    return 0 if report["overall_schema_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
