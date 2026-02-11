#!/usr/bin/env python3
"""Read-only PostgreSQL query tool.

Thin wrapper around psql that enforces read-only mode via
PGOPTIONS='-c default_transaction_read_only=on'. All database
interactions go through _run_psql(), which sets this env var
unconditionally -- PostgreSQL rejects any write or DDL attempt.

Connection URIs are resolved from aliases defined in CLAUDE.local.md
so that credentials never appear on the command line.

Subcommands:
    query    <alias> <sql>             Run a SQL query
    schemas  <alias>                   List non-system schemas
    tables   <alias> [--schema NAME]   List tables (default: public)
    describe <alias> <table>           Show columns, types, nullability
    indexes  <alias> <table>           Show indexes on a table
    explain  <alias> <sql>             Run EXPLAIN on a query
    list                               List configured database aliases
"""

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys


# -- Connection resolution ------------------------------------------------


def _find_claude_local_md():
    """Walk up from cwd looking for CLAUDE.local.md."""
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, "CLAUDE.local.md")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _parse_databases(filepath):
    """Extract alias=uri pairs from a ```pg-databases``` fenced block."""
    with open(filepath) as f:
        content = f.read()

    pattern = r"```pg-databases\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return {}

    databases = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        alias, uri = line.split("=", 1)
        databases[alias.strip()] = uri.strip()

    return databases


def _resolve_alias(alias):
    """Resolve a database alias to a connection URI."""
    filepath = _find_claude_local_md()
    if not filepath:
        print(
            json.dumps(
                {
                    "error": "No CLAUDE.local.md found in current or parent directories.",
                    "hint": "Create CLAUDE.local.md with a ```pg-databases``` block.",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    databases = _parse_databases(filepath)
    if not databases:
        print(
            json.dumps(
                {
                    "error": f"No ```pg-databases``` block found in {filepath}.",
                    "hint": "Add a ```pg-databases``` block with alias=uri entries.",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    if alias not in databases:
        print(
            json.dumps(
                {
                    "error": f"Unknown database alias: {alias}",
                    "available": list(databases.keys()),
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    return databases[alias]


# -- psql execution -------------------------------------------------------


def _run_psql(uri, sql):
    """Execute sql via psql with read-only mode enforced.

    Returns (columns, rows) where rows is a list of dicts.
    Raises SystemExit on psql errors.
    """
    env = os.environ.copy()
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"

    result = subprocess.run(
        ["psql", uri, "--no-align", "--field-separator=\t", "--no-psqlrc", "-c", sql],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        print(json.dumps({"error": result.stderr.strip()}), file=sys.stderr)
        sys.exit(1)

    stdout = result.stdout.strip()
    if not stdout:
        return [], []

    reader = csv.DictReader(io.StringIO(stdout), delimiter="\t")
    columns = reader.fieldnames or []
    rows = list(reader)
    # psql -A appends a row count line like "(3 rows)" -- the csv reader
    # picks it up as a malformed row. Drop any row whose first column value
    # matches the pattern.
    if rows and columns:
        last = rows[-1].get(columns[0], "")
        if last.startswith("(") and last.endswith(")"):
            rows = rows[:-1]

    return columns, rows


def _output(data):
    json.dump(data, sys.stdout, indent=2)
    print()


# -- Subcommands -----------------------------------------------------------


def cmd_query(args):
    uri = _resolve_alias(args.alias)
    columns, rows = _run_psql(uri, args.sql)
    _output({"columns": columns, "rows": rows, "row_count": len(rows)})


def cmd_schemas(args):
    uri = _resolve_alias(args.alias)
    sql = (
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
        "ORDER BY schema_name;"
    )
    _, rows = _run_psql(uri, sql)
    _output({"schemas": [r["schema_name"] for r in rows]})


def cmd_tables(args):
    uri = _resolve_alias(args.alias)
    schema = args.schema or "public"
    sql = (
        "SELECT table_name, table_type "
        "FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' "
        "ORDER BY table_name;"
    )
    _, rows = _run_psql(uri, sql)
    _output({"schema": schema, "tables": rows})


def cmd_describe(args):
    uri = _resolve_alias(args.alias)
    table = args.table
    schema = "public"
    if "." in table:
        schema, table = table.split(".", 1)
    sql = (
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
        "ORDER BY ordinal_position;"
    )
    _, rows = _run_psql(uri, sql)
    _output({"schema": schema, "table": table, "columns": rows})


def cmd_indexes(args):
    uri = _resolve_alias(args.alias)
    table = args.table
    schema = "public"
    if "." in table:
        schema, table = table.split(".", 1)
    sql = (
        "SELECT indexname, indexdef "
        "FROM pg_indexes "
        f"WHERE schemaname = '{schema}' AND tablename = '{table}' "
        "ORDER BY indexname;"
    )
    _, rows = _run_psql(uri, sql)
    _output({"schema": schema, "table": table, "indexes": rows})


def cmd_explain(args):
    uri = _resolve_alias(args.alias)
    sql = f"EXPLAIN {args.sql}"
    _, rows = _run_psql(uri, sql)
    plan_lines = [r.get("QUERY PLAN", "") for r in rows]
    _output({"plan": "\n".join(plan_lines)})


def cmd_list(_args):
    filepath = _find_claude_local_md()
    if not filepath:
        _output({"databases": [], "source": None})
        return
    databases = _parse_databases(filepath)
    _output(
        {
            "databases": list(databases.keys()),
            "source": filepath,
        }
    )


# -- Main ------------------------------------------------------------------


def main():
    if not shutil.which("psql"):
        print(
            json.dumps({"error": "psql not found. Install PostgreSQL client tools."}),
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Read-only PostgreSQL query tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_query = sub.add_parser("query", help="Run a SQL query")
    p_query.add_argument("alias", help="Database alias from CLAUDE.local.md")
    p_query.add_argument("sql", help="SQL query to execute")

    p_schemas = sub.add_parser("schemas", help="List schemas")
    p_schemas.add_argument("alias", help="Database alias from CLAUDE.local.md")

    p_tables = sub.add_parser("tables", help="List tables")
    p_tables.add_argument("alias", help="Database alias from CLAUDE.local.md")
    p_tables.add_argument(
        "--schema", default=None, help="Schema name (default: public)"
    )

    p_describe = sub.add_parser("describe", help="Describe a table")
    p_describe.add_argument("alias", help="Database alias from CLAUDE.local.md")
    p_describe.add_argument("table", help="Table name (schema.table or just table)")

    p_indexes = sub.add_parser("indexes", help="Show indexes on a table")
    p_indexes.add_argument("alias", help="Database alias from CLAUDE.local.md")
    p_indexes.add_argument("table", help="Table name (schema.table or just table)")

    p_explain = sub.add_parser("explain", help="Run EXPLAIN on a query")
    p_explain.add_argument("alias", help="Database alias from CLAUDE.local.md")
    p_explain.add_argument("sql", help="SQL query to explain")

    sub.add_parser("list", help="List configured database aliases")

    args = parser.parse_args()

    handlers = {
        "query": cmd_query,
        "schemas": cmd_schemas,
        "tables": cmd_tables,
        "describe": cmd_describe,
        "indexes": cmd_indexes,
        "explain": cmd_explain,
        "list": cmd_list,
    }

    handlers[args.command](args)


if __name__ == "__main__":
    main()
