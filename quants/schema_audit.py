"""Read-only, whole-project SQLite schema and SQL compatibility audit.

This module never migrates or writes to the inspected database. It compares the
live file with the canonical DDL, checks relational constraints and data
invariants, and compiles static SQL call-site literals using SQLite EXPLAIN.
"""
from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import db as db_module


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve()), safe="/:") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _canonical_schema() -> tuple[sqlite3.Connection, dict[str, dict[str, Any]]]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db_module.SCHEMA)
    db_module._create_indexes_safely(conn)
    db_module._ensure_users_unique_username(conn)
    contract: dict[str, dict[str, Any]] = {}
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in names:
        columns = [dict(r) for r in conn.execute(f'PRAGMA table_info("{table}")')]
        fks = [dict(r) for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')]
        indexes = [dict(r) for r in conn.execute(f'PRAGMA index_list("{table}")')]
        contract[table] = {"columns": columns, "foreign_keys": fks, "indexes": indexes}
    return conn, contract


def _affinity(declared_type: str | None) -> str:
    t = (declared_type or "").upper()
    if "INT" in t:
        return "INTEGER"
    if any(x in t for x in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _get_table_names(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _schema_diff(conn: sqlite3.Connection, contract: dict[str, dict[str, Any]]) -> dict[str, Any]:
    canonical_for_indexes = sqlite3.connect(":memory:")
    canonical_for_indexes.row_factory = sqlite3.Row
    canonical_for_indexes.executescript(db_module.SCHEMA)
    db_module._create_indexes_safely(canonical_for_indexes)
    db_module._ensure_users_unique_username(canonical_for_indexes)
    actual_tables = _get_table_names(conn)
    expected_tables = set(contract)
    result: dict[str, Any] = {
        "expected_table_count": len(expected_tables),
        "actual_table_count": len(actual_tables),
        "missing_tables": sorted(expected_tables - actual_tables),
        "unexpected_tables": sorted(actual_tables - expected_tables),
        "missing_columns": {}, "unexpected_columns": {},
        "type_mismatches": {}, "primary_key_mismatches": {},
        "not_null_mismatches": {}, "default_mismatches": {},
        "foreign_key_mismatches": {}, "missing_indexes": {},
        "index_definition_mismatches": {}, "unexpected_indexes": {},
    }
    for table in sorted(expected_tables & actual_tables):
        expected = contract[table]
        expected_cols = {r["name"]: r for r in expected["columns"]}
        actual_rows = [dict(r) for r in conn.execute(f'PRAGMA table_info("{table}")')]
        actual_cols = {r["name"]: r for r in actual_rows}
        missing = sorted(set(expected_cols) - set(actual_cols))
        extra = sorted(set(actual_cols) - set(expected_cols))
        if table == "stock_snapshots" and "id" in missing:
            # Legacy schema is intentionally supported via SQLite rowid.
            missing.remove("id")
        if missing:
            result["missing_columns"][table] = missing
        if extra:
            result["unexpected_columns"][table] = extra

        type_diff, pk_diff, nn_diff, default_diff = {}, {}, {}, {}
        for name in sorted(set(expected_cols) & set(actual_cols)):
            e, a = expected_cols[name], actual_cols[name]
            if _affinity(e["type"]) != _affinity(a["type"]):
                type_diff[name] = {"expected": e["type"], "actual": a["type"]}
            if int(e["pk"]) != int(a["pk"]):
                pk_diff[name] = {"expected": int(e["pk"]), "actual": int(a["pk"])}
            if int(e["notnull"]) != int(a["notnull"]):
                nn_diff[name] = {"expected": int(e["notnull"]), "actual": int(a["notnull"])}
            if str(e["dflt_value"]) != str(a["dflt_value"]):
                default_diff[name] = {"expected": e["dflt_value"], "actual": a["dflt_value"]}
        if type_diff:
            result["type_mismatches"][table] = type_diff
        if pk_diff:
            result["primary_key_mismatches"][table] = pk_diff
        if nn_diff:
            result["not_null_mismatches"][table] = nn_diff
        if default_diff:
            result["default_mismatches"][table] = default_diff

        def fk_key(r: dict[str, Any]) -> tuple:
            return (r["table"], r["from"], r["to"],
                    str(r["on_update"]).upper(), str(r["on_delete"]).upper())
        exp_fks = {fk_key(r) for r in expected["foreign_keys"]}
        act_fks = {fk_key(dict(r)) for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')}
        if exp_fks != act_fks:
            result["foreign_key_mismatches"][table] = {
                "missing": [list(x) for x in sorted(exp_fks - act_fks)],
                "unexpected": [list(x) for x in sorted(act_fks - exp_fks)],
            }

        def index_signature(source_conn: sqlite3.Connection, table_name: str) -> dict[str, tuple]:
            found = {}
            for item in source_conn.execute(f'PRAGMA index_list("{table_name}")'):
                index_name = str(item["name"])
                columns = tuple(row["name"] for row in source_conn.execute(f'PRAGMA index_info("{index_name}")'))
                found[index_name] = (int(item["unique"]), int(item["partial"]), columns)
            return found

        expected_indexes = index_signature(canonical_for_indexes, table)
        actual_indexes = index_signature(conn, table)
        missing_idx = sorted(set(expected_indexes) - set(actual_indexes))
        if missing_idx:
            result["missing_indexes"][table] = missing_idx
        mismatched_indexes = {
            name: {"expected": expected_indexes[name], "actual": actual_indexes[name]}
            for name in sorted(set(expected_indexes) & set(actual_indexes))
            if expected_indexes[name] != actual_indexes[name]
        }
        if mismatched_indexes:
            result["index_definition_mismatches"][table] = mismatched_indexes
        extra_indexes = sorted(set(actual_indexes) - set(expected_indexes))
        if extra_indexes:
            result["unexpected_indexes"][table] = extra_indexes

    hard = ["missing_tables", "missing_columns", "type_mismatches", "primary_key_mismatches", "not_null_mismatches", "foreign_key_mismatches", "missing_indexes", "index_definition_mismatches"]
    result["schema_compatible"] = not any(result[k] for k in hard)
    result["schema_default_review_required"] = bool(result["default_mismatches"])
    canonical_for_indexes.close()
    return result


def _extract_sql_call_literals(source_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    static: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    excluded = {".git", ".venv", "venv", "__pycache__", "tests"}
    for file in sorted(source_root.rglob("*.py")):
        if any(part in excluded for part in file.parts):
            continue
        # Runtime entry points import the quants package. The top-level mirror
        # modules (db.py, engine.py, etc.) are legacy duplicates, not runtime
        # dependencies; they are inventoried separately below.
        if file.parent == source_root and file.name not in {"app.py", "worker.py", "mcp_server.py"}:
            continue
        if file.name == "schema_audit.py" or (file.parent.name == "scripts" and file.name == "audit_database_schema.py"):
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            dynamic.append({"file": str(file.relative_to(source_root)), "line": 0,
                            "reason": f"SOURCE_PARSE_ERROR:{type(exc).__name__}"})
            continue
        string_assignments: dict[str, str] = {}
        for assignment in ast.walk(tree):
            if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant) and isinstance(assignment.value.value, str):
                for target in assignment.targets:
                    if isinstance(target, ast.Name):
                        string_assignments[target.id] = assignment.value.value
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            is_execute = isinstance(fn, ast.Attribute) and fn.attr in {"execute", "executemany"}
            is_query = isinstance(fn, ast.Name) and fn.id in {"query", "read_sql_query", "read_sql"}
            if not (is_execute or is_query):
                continue
            arg = node.args[0]
            sql = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql = arg.value
            elif isinstance(arg, ast.JoinedStr):
                if all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in arg.values):
                    sql = "".join(v.value for v in arg.values)
                else:
                    dynamic.append({"file": str(file.relative_to(source_root)), "line": node.lineno,
                                    "reason": "F_STRING_SQL", "expression": ast.unparse(arg)[:180]})
                    continue
            elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
                try:
                    value = ast.literal_eval(arg)
                    if isinstance(value, str):
                        sql = value
                except (ValueError, TypeError):
                    pass
            elif isinstance(arg, ast.Name) and arg.id in string_assignments:
                sql = string_assignments[arg.id]
            if not sql:
                dynamic.append({"file": str(file.relative_to(source_root)), "line": node.lineno,
                                "reason": "NON_LITERAL_SQL_ARGUMENT", "expression": ast.unparse(arg)[:180]})
                continue
            normalized = sql.strip()
            if re.match(r"(?is)^(SELECT|WITH|INSERT|UPDATE|DELETE|REPLACE)\b", normalized):
                static.append({"file": str(file.relative_to(source_root)), "line": node.lineno, "sql": sql})
    return static, dynamic


def _compile_sql(conn: sqlite3.Connection, source_root: Path) -> dict[str, Any]:
    statements, dynamic = _extract_sql_call_literals(source_root)
    passed, failed, seen = [], [], set()
    for item in statements:
        sql = item["sql"]
        signature = (item["file"], item["line"], sql)
        if signature in seen:
            continue
        seen.add(signature)
        try:
            named = set(re.findall(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", sql))
            tokens = re.findall(r"\?(\d*)", sql)
            explicit_numbers = [int(x) for x in tokens if x]
            qmarks = max(explicit_numbers) if explicit_numbers else len(tokens)
            params: Any = ({name: None for name in named} if named else [None] * qmarks)
            conn.execute("EXPLAIN " + sql, params).fetchall()
            passed.append({"file": item["file"], "line": item["line"]})
        except Exception as exc:  # SQLite parser/column/table failures are audit findings.
            failed.append({"file": item["file"], "line": item["line"],
                           "error": f"{type(exc).__name__}: {exc}", "sql": sql.strip()[:240]})
    return {"static_statements": len(seen), "compiled": len(passed),
            "failed_count": len(failed), "failures": failed,
            "dynamic_statement_count": len(dynamic), "dynamic_statements": dynamic}



def _table_access_inventory(source_root: Path) -> dict[str, Any]:
    """Inventory literal and safely identifiable dynamic SQL table access sites."""
    table_rows: dict[str, dict[str, Any]] = {}
    excluded = {".git", ".venv", "venv", "__pycache__", "tests"}
    patterns = (
        ("CREATE", re.compile(r"(?is)\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("ALTER", re.compile(r"(?is)\bALTER\s+TABLE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("INSERT", re.compile(r"(?is)\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("REPLACE", re.compile(r"(?is)\bREPLACE\s+INTO\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("UPDATE", re.compile(r"(?is)\bUPDATE\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("DELETE", re.compile(r"(?is)\bDELETE\s+FROM\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("READ", re.compile(r"(?is)\b(?:FROM|JOIN)\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
        ("INDEX_ON", re.compile(r"(?is)\bCREATE\s+(?:UNIQUE\s+)?INDEX\b.*?\bON\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)")),
    )
    for file in sorted(source_root.rglob("*.py")):
        if any(part in excluded for part in file.parts):
            continue
        if file.parent == source_root and file.name not in {"app.py", "worker.py", "mcp_server.py"}:
            continue
        if file.name == "schema_audit.py" or (file.parent.name == "scripts" and file.name == "audit_database_schema.py"):
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            if not ((isinstance(fn, ast.Attribute) and fn.attr in {"execute", "executemany", "executescript"})
                    or (isinstance(fn, ast.Name) and fn.id in {"query", "read_sql_query", "read_sql"})):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql = arg.value
            elif isinstance(arg, ast.JoinedStr):
                sql = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else " __DYNAMIC__ " for v in arg.values)
            else:
                try:
                    sql = ast.literal_eval(arg)
                    if not isinstance(sql, str):
                        continue
                except (ValueError, TypeError):
                    continue
            relative = str(file.relative_to(source_root))
            for operation, pattern in patterns:
                for match in pattern.finditer(sql):
                    table = match.group(1)
                    if table == "__DYNAMIC__":
                        continue
                    entry = table_rows.setdefault(table, {"operations": {}, "call_sites": []})
                    entry["operations"][operation] = entry["operations"].get(operation, 0) + 1
                    call_site = {"file": relative, "line": node.lineno, "operation": operation}
                    if call_site not in entry["call_sites"]:
                        entry["call_sites"].append(call_site)
    # The canonical DDL is executed with executescript(SCHEMA), not SQL literals.
    for table in re.findall(r"(?im)^CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", db_module.SCHEMA):
        entry = table_rows.setdefault(table, {"operations": {}, "call_sites": []})
        entry["operations"]["SCHEMA_DECLARATION"] = 1
        entry["call_sites"].append({"file": "quants/db.py", "line": 0, "operation": "SCHEMA_DECLARATION"})
    canonical_tables = set(re.findall(
        r"(?im)^CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", db_module.SCHEMA
    ))
    table_rows = {name: value for name, value in table_rows.items()
                  if name in canonical_tables or name == "sqlite_master"}
    for value in table_rows.values():
        value["call_sites"].sort(key=lambda x: (x["file"], x["line"], x["operation"]))
    return {"table_count_referenced": len(table_rows), "tables": dict(sorted(table_rows.items()))}

def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in sorted(_get_table_names(conn)):
        counts[table] = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return counts


def _data_checks(conn: sqlite3.Connection, counts: dict[str, int]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    tables = _get_table_names(conn)
    checks["integrity_check"] = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    checks["foreign_key_violations"] = [list(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    if "users" in tables:
        checks["admin_count"] = int(conn.execute("SELECT COUNT(*) FROM users WHERE LOWER(username)='admin'").fetchone()[0])
        checks["duplicate_usernames_case_insensitive"] = [list(r) for r in conn.execute(
            "SELECT LOWER(TRIM(username)),COUNT(*) FROM users WHERE username IS NOT NULL "
            "GROUP BY LOWER(TRIM(username)) HAVING COUNT(*)>1"
        ).fetchall()]
    else:
        checks["admin_count"] = None
        checks["duplicate_usernames_case_insensitive"] = []
    if "predictions" in tables:
        checks["prediction_status_counts"] = {str(r[0]): int(r[1]) for r in conn.execute(
            "SELECT COALESCE(status,'NULL'),COUNT(*) FROM predictions GROUP BY status"
        ).fetchall()}
        checks["prediction_invalid_horizons"] = int(conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE horizon NOT IN (2,3,7) OR horizon IS NULL"
        ).fetchone()[0])
        checks["prediction_probability_out_of_range"] = int(conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE p_win IS NOT NULL AND (p_win<0 OR p_win>100)"
        ).fetchone()[0])
        checks["open_prediction_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE UPPER(COALESCE(status,''))='OPEN'"
        ).fetchone()[0])
    else:
        checks["prediction_status_counts"] = {}
        checks["prediction_invalid_horizons"] = None
        checks["prediction_probability_out_of_range"] = None
        checks["open_prediction_count"] = None
    if {"smf_v2_evidence", "smf_v2_runs"} <= tables:
        checks["smf_evidence_orphan_rows"] = int(conn.execute(
            "SELECT COUNT(*) FROM smf_v2_evidence e LEFT JOIN smf_v2_runs r ON r.id=e.run_id WHERE r.id IS NULL"
        ).fetchone()[0])
        checks["smf_run_evidence_count_mismatches"] = [dict(r) for r in conn.execute(
            "SELECT r.id,r.evidence_count,COUNT(e.id) AS actual_count FROM smf_v2_runs r "
            "LEFT JOIN smf_v2_evidence e ON e.run_id=r.id GROUP BY r.id "
            "HAVING r.evidence_count<>COUNT(e.id)"
        ).fetchall()]
        checks["smf_engine_counts"] = {str(r[0]): int(r[1]) for r in conn.execute(
            "SELECT COALESCE(engine,'NULL'),COUNT(*) FROM smf_v2_evidence GROUP BY engine"
        ).fetchall()}
        checks["smf_quality_counts"] = {str(r[0]): int(r[1]) for r in conn.execute(
            "SELECT COALESCE(quality_status,'NULL'),COUNT(*) FROM smf_v2_evidence GROUP BY quality_status"
        ).fetchall()}
        checks["smf_source_feature_counts"] = [dict(r) for r in conn.execute(
            "SELECT COALESCE(source_name,'NULL') AS source_name,COALESCE(feature,'NULL') AS feature,"
            "COALESCE(evidence_group,'NULL') AS evidence_group,quality_status,contributes_to_consensus,COUNT(*) AS n "
            "FROM smf_v2_evidence GROUP BY 1,2,3,4,5 ORDER BY n DESC,1,2"
        ).fetchall()]
        checks["smf_native_structure_evidence_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM smf_v2_evidence WHERE source_name='market_structure_v2'"
        ).fetchone()[0])
        checks["smf_native_vsa_evidence_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM smf_v2_evidence WHERE source_name='vsa_volume_v2'"
        ).fetchone()[0])
    else:
        checks["smf_evidence_orphan_rows"] = None
        checks["smf_run_evidence_count_mismatches"] = []
        checks["smf_engine_counts"] = {}
        checks["smf_quality_counts"] = {}
        checks["smf_source_feature_counts"] = []
        checks["smf_native_structure_evidence_count"] = None
        checks["smf_native_vsa_evidence_count"] = None
    checks["empty_analytical_tables"] = [t for t in (
        "stock_snapshots", "flow_snapshots", "market_bars", "media_articles",
        "media_symbol_daily", "model_performance"
    ) if t in counts and counts[t] == 0]
    meta = {str(r[0]): str(r[1]) for r in conn.execute("SELECT key,value FROM app_meta")} if "app_meta" in tables else {}
    checks["schema_metadata"] = {
        "db_schema_version": meta.get("db_schema_version"),
        "db_path_recorded": meta.get("db_path"),
        "admin_bootstrap_status": meta.get("admin_bootstrap_status"),
    }
    return checks


def _baseline_compare(current: sqlite3.Connection, baseline_path: Path) -> dict[str, Any]:
    baseline = _readonly_connection(baseline_path)
    try:
        report = {}
        current_tables = _get_table_names(current)
        baseline_tables = _get_table_names(baseline)
        for table in ("media_articles", "prediction_versions", "predictions", "macro_snapshots",
                      "stock_snapshots", "flow_snapshots", "market_bars"):
            if table not in current_tables or table not in baseline_tables:
                continue
            old_n = int(baseline.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            new_n = int(current.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            item: dict[str, Any] = {"baseline_rows": old_n, "current_rows": new_n,
                                    "delta_rows": new_n - old_n}
            old_cols = [r["name"] for r in baseline.execute(f'PRAGMA table_info("{table}")')]
            new_cols = [r["name"] for r in current.execute(f'PRAGMA table_info("{table}")')]
            common_cols = [c for c in old_cols if c in new_cols]
            key = "id" if "id" in common_cols else None
            if key:
                old_keys = {r[0] for r in baseline.execute(f'SELECT "{key}" FROM "{table}" WHERE "{key}" IS NOT NULL')}
                new_keys = {r[0] for r in current.execute(f'SELECT "{key}" FROM "{table}" WHERE "{key}" IS NOT NULL')}
                item["baseline_keys_missing_current"] = len(old_keys - new_keys)
                item["new_keys_not_in_baseline"] = len(new_keys - old_keys)
                shared = sorted(old_keys & new_keys)
                changed = 0
                changed_columns: dict[str, int] = {}
                if shared and common_cols:
                    cols_sql = ",".join(f'"{c}"' for c in common_cols)
                    old_rows = {r[key]: tuple(r) for r in baseline.execute(
                        f'SELECT {cols_sql} FROM "{table}" WHERE "{key}" IN ({",".join("?" for _ in shared)})', shared)}
                    new_rows = {r[key]: tuple(r) for r in current.execute(
                        f'SELECT {cols_sql} FROM "{table}" WHERE "{key}" IN ({",".join("?" for _ in shared)})', shared)}
                    for k in shared:
                        old_values, new_values = old_rows.get(k), new_rows.get(k)
                        if old_values != new_values:
                            changed += 1
                            if old_values is not None and new_values is not None:
                                for col, old_value, new_value in zip(common_cols, old_values, new_values):
                                    if old_value != new_value:
                                        changed_columns[col] = changed_columns.get(col, 0) + 1
                item["changed_shared_key_rows"] = changed
                item["changed_columns_in_shared_rows"] = changed_columns
            report[table] = item
        return report
    finally:
        baseline.close()


def _legacy_duplicate_inventory(source_root: Path) -> list[dict[str, Any]]:
    items = []
    for root_file in sorted(source_root.glob("*.py")):
        if root_file.name in {"app.py", "worker.py", "mcp_server.py", "__init__.py"}:
            continue
        package_file = source_root / "quants" / root_file.name
        if package_file.is_file():
            try:
                same = root_file.read_bytes() == package_file.read_bytes()
            except OSError:
                same = False
            items.append({"module": root_file.name, "matches_quants_copy": same,
                          "runtime_imported_by_app": False})
    return items


def audit_database(db_path: str | Path, source_root: str | Path,
                   baseline_path: str | Path | None = None) -> dict[str, Any]:
    path, root = Path(db_path).expanduser().resolve(), Path(source_root).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy SQLite database: {path}")
    canonical, contract = _canonical_schema()
    conn = _readonly_connection(path)
    try:
        schema = _schema_diff(conn, contract)
        counts = _table_counts(conn)
        sql = _compile_sql(conn, root)
        data = _data_checks(conn, counts)
        indexes = sorted(r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ))
        original_db_path = db_module.DB_PATH
        try:
            db_module.DB_PATH = path
            with db_module.connect() as app_connection:
                foreign_key_enforcement = int(app_connection.execute("PRAGMA foreign_keys").fetchone()[0])
        finally:
            db_module.DB_PATH = original_db_path
        result: dict[str, Any] = {
            "audit_version": "1.0",
            "database_path": str(path),
            "database_size_bytes": path.stat().st_size,
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "sqlite_user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "application_connection_foreign_keys": foreign_key_enforcement,
            "schema": schema,
            "table_counts": counts,
            "explicit_indexes": indexes,
            "data_checks": data,
            "static_sql_audit": sql,
            "table_access_inventory": _table_access_inventory(root),
            "legacy_duplicate_module_inventory": _legacy_duplicate_inventory(root),
            "baseline_comparison": _baseline_compare(conn, Path(baseline_path).resolve()) if baseline_path else None,
        }
        result["overall_schema_status"] = "PASS" if schema["schema_compatible"] and not sql["failed_count"] else "FAIL"
        continuity = result.get("baseline_comparison") or {}
        continuity_alert = any(
            int(item.get("baseline_keys_missing_current", 0) or 0) > 0
            or int(item.get("changed_shared_key_rows", 0) or 0) > 0
            for item in continuity.values()
        )
        result["data_continuity_review_required"] = bool(continuity_alert)
        if (foreign_key_enforcement == 0 or data["foreign_key_violations"] or
                data["smf_run_evidence_count_mismatches"] or not schema["schema_compatible"] or sql["failed_count"]):
            result["production_readiness"] = "REVIEW_REQUIRED"
        elif continuity_alert:
            result["production_readiness"] = "DATA_CONTINUITY_REVIEW_REQUIRED"
        elif schema["default_mismatches"]:
            result["production_readiness"] = "SCHEMA_DEFAULTS_REVIEW_REQUIRED"
        elif data["empty_analytical_tables"]:
            result["production_readiness"] = "SCHEMA_PASS_DATA_REVIEW_REQUIRED"
        else:
            result["production_readiness"] = "PASS"
        return result
    finally:
        conn.close()
        canonical.close()
