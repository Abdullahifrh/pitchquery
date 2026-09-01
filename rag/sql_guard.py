import re

DEFAULT_ROW_LIMIT = 200

DISALLOWED_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "CREATE", "REPLACE", "MERGE", "CALL", "COPY", "EXECUTE",
)

class UnsafeQueryError(ValueError):
    pass

def validate_select_query(sql: str, default_limit: int = DEFAULT_ROW_LIMIT) -> str:
    cleaned = sql.strip().rstrip(";").strip()

    if not cleaned:
        raise UnsafeQueryError("Empty query.")
    if ";" in cleaned:
        raise UnsafeQueryError("Multiple statements are not allowed.")
    if not re.match(r"(?is)^SELECT\b", cleaned):
        raise UnsafeQueryError("Only SELECT statements are allowed.")

    for keyword in DISALLOWED_KEYWORDS:
        if re.search(rf"(?i)\b{keyword}\b", cleaned):
            raise UnsafeQueryError(f"Disallowed keyword: {keyword}")

    if not re.search(r"(?i)\bLIMIT\s+\d+", cleaned):
        cleaned = f"{cleaned}\nLIMIT {default_limit}"

    return cleaned
