import argparse
import re
from pathlib import Path
from sqlalchemy import text
from pipelines.load.warehouse import get_db_engine, WAREHOUSE_SCHEMA

MIGRATION_PATH = Path(__file__).parent / "add_data_dictionary.sql"

def _extract_target_columns(sql_text: str) -> set[tuple[str, str]]:
    """(table, column) pairs this migration file declares comments for —
    parsed from the SQL itself, so this check can never drift out of
    sync with the file it's verifying."""
    return set(
        re.findall(
            rf"COMMENT ON COLUMN {re.escape(WAREHOUSE_SCHEMA)}\.(\w+)\.(\w+) IS",
            sql_text,
        )
    )

def _split_statements(sql_text: str) -> list[str]:
    """Splits the migration file into individual, complete
    `COMMENT ON COLUMN ... IS '...';` and `COMMENT ON TABLE ... IS '...';`
    statements."""
    return re.findall(r"(COMMENT ON (?:COLUMN|TABLE) .*?IS\n'.*?';)", sql_text, re.DOTALL)

def apply_migration(engine, migration_path: Path = MIGRATION_PATH) -> None:
    """Executes the migration one statement at a time."""
    sql_text = migration_path.read_text()
    statements = _split_statements(sql_text)
    print(f"[APPLY] Running {migration_path} ({len(statements)} comments)...")

    with engine.begin() as conn:
        for i, statement in enumerate(statements, start=1):
            try:
                conn.execute(text(statement))
            except Exception:
                print(f"[APPLY] FAILED on statement {i}/{len(statements)}:\n{statement}")
                raise

    print("[APPLY] Done.")

def verify_migration(engine, migration_path: Path = MIGRATION_PATH) -> bool:
    sql_text = migration_path.read_text()
    target_columns = _extract_target_columns(sql_text)
    tables = sorted({t for t, _ in target_columns})

    ok = True

    with engine.connect() as conn:
        # 1. Every column this file targets must actually exist —
        # catches a typo'd table/column name that COMMENT ON COLUMN
        # would otherwise silently no-op on instead of erroring.
        real_columns = set(
            conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = ANY(:tables)"
                ),
                {"schema": WAREHOUSE_SCHEMA, "tables": tables},
            ).all()
        )
        phantom_targets = target_columns - real_columns
        if phantom_targets:
            ok = False
            print(f"[VERIFY] FAILED: {len(phantom_targets)} commented column(s) don't exist in the warehouse "
                  f"(typo in the migration file?): {sorted(phantom_targets)[:10]}")

        # 2. Every column that exists in a targeted table must have a
        # real (non-empty) comment applied.
        commented = set(
            conn.execute(
                text(
                    "SELECT c.table_name, c.column_name "
                    "FROM information_schema.columns c "
                    "WHERE c.table_schema = :schema AND c.table_name = ANY(:tables) "
                    "AND col_description(format('%s.%s', c.table_schema, c.table_name)::regclass::oid, c.ordinal_position) IS NOT NULL"
                ),
                {"schema": WAREHOUSE_SCHEMA, "tables": tables},
            ).all()
        )
        real_target_columns = real_columns & target_columns
        missing_comments = real_target_columns - commented
        if missing_comments:
            ok = False
            print(f"[VERIFY] FAILED: {len(missing_comments)} real column(s) still have no comment applied: "
                  f"{sorted(missing_comments)[:10]}")

    if ok:
        print(f"[VERIFY] OK: all {len(target_columns)} targeted columns exist and are commented.")
    return ok

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration", type=Path, default=MIGRATION_PATH)
    parser.add_argument("--verify-only", action="store_true", help="Skip applying; only run the verification checks.")
    args = parser.parse_args(argv)

    engine = get_db_engine()

    if not args.verify_only:
        apply_migration(engine, args.migration)

    ok = verify_migration(engine, args.migration)
    if not ok:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
