from sqlalchemy import text
from sqlalchemy.engine import Connection

WAREHOUSE_SCHEMA = "warehouse"
LARGE_TABLE_COLUMN_THRESHOLD = 20

_TABLE_LIST_QUERY = text("""
    SELECT
        c.relname AS table_name,
        obj_description(c.oid, 'pg_class') AS table_comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind = 'r'
    ORDER BY c.relname
""")

_TABLE_COLUMNS_QUERY = text("""
    SELECT
        c.table_name,
        c.column_name,
        c.data_type,
        col_description(format('%s.%s', c.table_schema, c.table_name)::regclass::oid, c.ordinal_position) AS column_comment
    FROM information_schema.columns c
    WHERE c.table_schema = :schema AND c.table_name = ANY(:table_names)
    ORDER BY c.table_name, c.ordinal_position
""")

_COLUMN_DETAIL_QUERY = text("""
    SELECT
        c.column_name,
        c.data_type,
        col_description(format('%s.%s', c.table_schema, c.table_name)::regclass::oid, c.ordinal_position) AS column_comment
    FROM information_schema.columns c
    WHERE c.table_schema = :schema AND c.table_name = :table_name AND c.column_name = ANY(:column_names)
    ORDER BY c.ordinal_position
""")

_REFERENCE_DATA_QUERY = text("""
    SELECT 'team' AS kind, team_id::text AS id, team_name AS name, NULL AS note
    FROM warehouse.dim_teams
    UNION ALL
    SELECT
        'season' AS kind,
        s.season_id::text AS id,
        s.season_name AS name,
        CASE WHEN EXISTS (
            SELECT 1 FROM warehouse.dim_fixtures f
            WHERE f.season_id = s.season_id AND f.fixture_status IN ('U', 'L')
        ) THEN 'current' END AS note
    FROM warehouse.dim_seasons s
    ORDER BY kind, name
""")

def list_tables(conn: Connection, schema: str = WAREHOUSE_SCHEMA) -> str:
    rows = conn.execute(_TABLE_LIST_QUERY, {"schema": schema}).mappings().all()
    if not rows:
        raise RuntimeError(f"No tables found in schema '{schema}' - is the warehouse populated?")

    lines = [f"{schema}.{row['table_name']} - {row['table_comment'] or '(no description)'}" for row in rows]
    return "\n".join(lines)

def describe_tables(conn: Connection, table_names: list[str], schema: str = WAREHOUSE_SCHEMA) -> str:
    if not table_names:
        return "No table names given."

    rows = conn.execute(_TABLE_COLUMNS_QUERY, {"schema": schema, "table_names": table_names}).mappings().all()

    found_tables = {row["table_name"] for row in rows}
    blocks: list[str] = []
    for table_name in table_names:
        if table_name not in found_tables:
            blocks.append(f"No table '{schema}.{table_name}' found. Call list_tables and use one of those exact names.")
            continue
        table_rows = [row for row in rows if row["table_name"] == table_name]
        if len(table_rows) > LARGE_TABLE_COLUMN_THRESHOLD:
            lines = [
                f"Table {schema}.{table_name} ({len(table_rows)} columns - names and types only. "
                f"Call describe_columns for the specific column(s) you plan to use before writing SQL "
                f"against this table, their meaning is not shown here):"
            ]
            lines += [f"  {row['column_name']} ({row['data_type']})" for row in table_rows]
        else:
            lines = [f"Table {schema}.{table_name}:"]
            for row in table_rows:
                comment = f" -- {row['column_comment']}" if row["column_comment"] else ""
                lines.append(f"  {row['column_name']} ({row['data_type']}){comment}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)

def describe_columns(conn: Connection, table_name: str, column_names: list[str], schema: str = WAREHOUSE_SCHEMA) -> str:
    if not column_names:
        return "No column names given."

    rows = conn.execute(
        _COLUMN_DETAIL_QUERY,
        {"schema": schema, "table_name": table_name, "column_names": column_names},
    ).mappings().all()

    if not rows:
        return f"No matching columns found in {schema}.{table_name}. Call describe_tables first to see the real column names."

    found = {row["column_name"] for row in rows}
    lines = [f"Table {schema}.{table_name}:"]
    for row in rows:
        comment = f" -- {row['column_comment']}" if row["column_comment"] else " -- (no description)"
        lines.append(f"  {row['column_name']} ({row['data_type']}){comment}")
    missing = [c for c in column_names if c not in found]
    if missing:
        lines.append(f"  Not found in {schema}.{table_name}: {', '.join(missing)}")

    return "\n".join(lines)

def reference_data(conn: Connection) -> str:
    """team_id/season_id are needed by nearly every question and never
    change mid-session - inlining them (a small, fixed cost) saves a
    describe_tables + run_sql round trip per question instead of making
    the model rediscover them every time. 'current' is precomputed here
    using the same rule documented on dim_seasons.season_name, so the
    model never needs a lookup to figure out which season that is."""
    rows = conn.execute(_REFERENCE_DATA_QUERY).mappings().all()
    teams = [f"  {row['id']}: {row['name']}" for row in rows if row["kind"] == "team"]
    seasons = [
        f"  {row['id']}: {row['name']}" + (" (current)" if row["note"] == "current" else "")
        for row in rows if row["kind"] == "season"
    ]
    return (
        "Teams (team_id: team_name):\n" + "\n".join(teams) + "\n\n"
        "Seasons (season_id: season_name):\n" + "\n".join(seasons)
    )
