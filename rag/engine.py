import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from google import genai
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from rag.db import get_db_engine, open_connection
from rag.schema_context import describe_columns, describe_tables, list_tables, reference_data
from rag.sql_guard import UnsafeQueryError, validate_select_query

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
MAX_TURNS = 8

_active_model = MODEL

RUN_SQL_TOOL = {
    "type": "function",
    "name": "run_sql",
    "description": (
        "Executes a single read-only SQL SELECT statement against the "
        "Premier League warehouse and returns the resulting rows as JSON. "
        "Always qualify table names with the 'warehouse.' schema prefix, "
        "e.g. warehouse.dim_teams. Call describe_tables (and describe_columns, "
        "if describe_tables directed you to) for a table first if you have not "
        "already seen its exact columns and their meaning in this conversation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single SELECT statement. No semicolons, no DDL/DML.",
            },
        },
        "required": ["sql"],
    },
}

DESCRIBE_TABLES_TOOL = {
    "type": "function",
    "name": "describe_tables",
    "description": (
        "Returns the exact column names and types for one or more warehouse "
        "tables. For a small table this also includes what each column means; "
        "for a large table it only gives you names and types and will tell you "
        "so - call describe_columns for the specific column(s) you plan to use "
        "before writing SQL against those. Call this before writing SQL against "
        "any table not already described in this conversation or given in "
        "'Reference data' below - never guess a column name. If you need "
        "several tables you haven't seen yet, pass all of their names in "
        "one call instead of calling this tool once per table."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "table_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Table names without the 'warehouse.' prefix, e.g. ['dim_teams', 'fact_match_events'].",
            },
        },
        "required": ["table_names"],
    },
}

DESCRIBE_COLUMNS_TOOL = {
    "type": "function",
    "name": "describe_columns",
    "description": (
        "Returns the exact meaning of specific columns of one large table - "
        "formulas, synonyms, closed value sets, anything describe_tables didn't "
        "give you for that table. Always call this before using a column in a "
        "rate/per-90 calculation, a filter on an enum-like column (event_type, "
        "shot_type, outcome, body_part, result), or anywhere its exact "
        "definition isn't obvious from its name alone. Only needed for tables "
        "describe_tables flagged as large - small tables already gave you full "
        "column meanings, calling this for one of those returns nothing new."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": "One table name without the 'warehouse.' prefix, e.g. 'fact_player_season_stats'.",
            },
            "column_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The exact column name(s) to get the full description for.",
            },
        },
        "required": ["table_name", "column_names"],
    },
}

TOOLS = [RUN_SQL_TOOL, DESCRIBE_TABLES_TOOL, DESCRIBE_COLUMNS_TOOL]

@dataclass(frozen=True)
class AskResult:
    answer: str
    sql: str | None

def _build_system_prompt(table_catalog: str, reference: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"You are a Text-to-SQL assistant for a Premier League football data "
        f"warehouse. Today's date is {today} (UTC) - use for relative dates "
        f"('yesterday', 'this month') instead of guessing.\n\n"
        f"team_id and season_id for every team and season are given below in "
        f"'Reference data'. Use those directly. dim_teams and dim_seasons are "
        f"never useful to describe_tables - their only columns are exactly "
        f"what 'Reference data' already shows you, so calling describe_tables "
        f"on either one always just repeats information you already have. "
        f"Never guess a team name/ID (e.g. from a nickname) instead of "
        f"reading it from this list.\n\n"
        f"Before your first describe_tables call, think through everything "
        f"the full question needs - including any table needed only for a "
        f"player/team name lookup - so you can request every table you'll "
        f"need in that one call. Realizing partway through that you need "
        f"another table means you didn't plan far enough ahead; a second "
        f"describe_tables call costs a full extra turn that planning ahead "
        f"would have avoided.\n\n"
        f"The tables below are listed with a one-line description each. "
        f"Call describe_tables on the specific table(s) a question needs "
        f"before writing SQL - never guess column names. If two tables could "
        f"both answer a simple question, the table catalog or a column's own "
        f"description will tell you which one to prefer - don't query both "
        f"'to be safe'; that costs turns without adding accuracy. describe_tables "
        f"tells you when a table is too large to include column meanings "
        f"inline - for those, call describe_columns for the specific column(s) "
        f"you're about to use (anything going into a formula, a rate, or a "
        f"filter on an enum-like column) before writing SQL that depends on "
        f"them; do not guess a column's meaning from its name. Once you have "
        f"both the columns you need and their meaning, call run_sql. Only "
        f"SELECT statements are allowed, and every table name must be "
        f"qualified with the 'warehouse.' schema prefix.\n\n"
        f"For any 'most/highest/top/best' question, never write ORDER BY ... "
        f"LIMIT 1, and do not explore across several queries first (no "
        f"preview query, then MAX(), then a re-fetch). Go straight to one "
        f"query: SELECT ... FROM t WHERE x = (SELECT MAX(x) FROM t WHERE "
        f"<same filters>). If it returns more than one row, name all of them "
        f"rather than picking one. The same applies to 'latest/last/most "
        f"recent' questions (e.g. a team's last match): compute the boundary "
        f"directly in a subquery (MAX(kickoff_datetime) with the same filters) "
        f"inside the one query that also gets everything else you need - "
        f"do not fetch the boundary row first and then re-query using its ID.\n\n"
        f"For a question asking for both a total (a count or sum) AND which "
        f"entity contributed the most to it, compute both in one query using "
        f"two independent subqueries or CTEs in a single SELECT - not two "
        f"separate run_sql calls, and not a multi-step exploration.\n\n"
        f"If a question does not name a season, use the season marked "
        f"'(current)' in Reference data - that's already the correct, "
        f"documented default, not something to explain or flag in your "
        f"answer.\n\n"
        f"If the question asks for something no column captures, say so - "
        f"do not substitute a superficially similar column. If the question "
        f"isn't answerable from structured match/player/team data at all (a "
        f"prediction, an opinion, injury news, transfer rumours), say so "
        f"instead of attempting a query.\n\n"
        f"Answer in natural conversational sentences, not a bulleted list, "
        f"unless asked for a list. Never include internal identifiers "
        f"(fixture_id, player_id, team_id) in your answer even if you used "
        f"them in SQL - refer to a match by teams and date instead.\n\n"
        f"Only answer once you have actually run the query that gives you "
        f"the fact you're about to report - never state a figure you "
        f"haven't just queried.\n\n"
        f"Reference data:\n{reference}\n\n"
        f"Tables:\n{table_catalog}"
    )

def _run_run_sql(conn, raw_arguments: str) -> tuple[str, str | None]:
    try:
        arguments = json.loads(raw_arguments)
        safe_sql = validate_select_query(arguments.get("sql", ""))
    except (json.JSONDecodeError, UnsafeQueryError) as exc:
        return json.dumps({"error": str(exc)}), None

    try:
        rows = [dict(row._mapping) for row in conn.execute(text(safe_sql))]
        return json.dumps(rows, default=str), safe_sql
    except SQLAlchemyError as exc:
        conn.rollback()
        return json.dumps({"error": f"Query failed: {exc}"}), None

def _run_describe_tables(conn, raw_arguments: str, described_tables: set[str]) -> str:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": str(exc)})

    requested = arguments.get("table_names", [])
    already_seen = [t for t in requested if t in described_tables]
    new_tables = [t for t in requested if t not in described_tables]

    parts = []
    if already_seen:
        parts.append(
            "Already given to you earlier in this conversation (in 'Reference "
            f"data' or an earlier describe_tables call) - use what you already "
            f"have, do not ask again: {', '.join(already_seen)}"
        )
    if new_tables:
        parts.append(describe_tables(conn, new_tables))
        described_tables.update(new_tables)

    return "\n\n".join(parts) if parts else "No table names given."

def _run_describe_columns(conn, raw_arguments: str, described_columns: dict[str, set[str]]) -> str:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": str(exc)})

    table_name = arguments.get("table_name", "")
    requested = arguments.get("column_names", [])
    already_known = described_columns.get(table_name, set())
    already_seen = [c for c in requested if c in already_known]
    new_columns = [c for c in requested if c not in already_known]

    parts = []
    if already_seen:
        parts.append(
            f"Already given to you earlier in this conversation for {table_name} - "
            f"use what you already have, do not ask again: {', '.join(already_seen)}"
        )
    if new_columns:
        parts.append(describe_columns(conn, table_name, new_columns))
        described_columns.setdefault(table_name, set()).update(new_columns)

    return "\n\n".join(parts) if parts else "No column names given."

def _dispatch_tool_call(
    conn,
    name: str,
    raw_arguments: str,
    described_tables: set[str],
    described_columns: dict[str, set[str]],
) -> tuple[str, str | None]:
    if name == "run_sql":
        return _run_run_sql(conn, raw_arguments)
    if name == "describe_tables":
        return _run_describe_tables(conn, raw_arguments, described_tables), None
    if name == "describe_columns":
        return _run_describe_columns(conn, raw_arguments, described_columns), None
    return json.dumps({"error": f"Unknown tool: {name}"}), None

def _tool_call_summary(name: str, raw_arguments: str) -> str:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return name

    if name == "describe_tables":
        return f"describe_tables({arguments.get('table_names', [])})"
    if name == "describe_columns":
        return f"describe_columns({arguments.get('table_name')}, {arguments.get('column_names', [])})"
    if name == "run_sql":
        sql = arguments.get("sql", "").replace("\n", " ").strip()
        return f"run_sql({sql[:70]}{'...' if len(sql) > 70 else ''})"
    return name

def _friendly_table_name(table_name: str) -> str:
    for prefix in ("fact_", "dim_", "bridge_"):
        if table_name.startswith(prefix):
            return table_name[len(prefix):].replace("_", " ")
    return table_name.replace("_", " ")

def _friendly_action(name: str, raw_arguments: str) -> str:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = {}

    if name == "describe_tables":
        friendly = [_friendly_table_name(t) for t in arguments.get("table_names", [])]
        if len(friendly) == 1:
            return f"Reading {friendly[0]} information..."
        if len(friendly) == 2:
            return f"Reading {friendly[0]} and {friendly[1]} information..."
        return "Reading database information..."
    if name == "describe_columns":
        table = arguments.get("table_name", "")
        return f"Looking up column details for {_friendly_table_name(table)}..." if table else "Looking up column details..."
    if name == "run_sql":
        return "Running SQL query..."
    return "Performing a database lookup..."

def _format_sql_trail(executed_queries: list[str]) -> str | None:
    if not executed_queries:
        return None
    if len(executed_queries) == 1:
        return executed_queries[0]
    return "\n\n".join(f"{i}. {query}" for i, query in enumerate(executed_queries, start=1))

def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit", "too_many_requests")
    )

def _switch_to_fallback(reason: str) -> bool:
    global _active_model
    if _active_model == FALLBACK_MODEL:
        return False
    print(f"[model switch] {_active_model} -> {FALLBACK_MODEL}: {reason}", file=sys.stderr, flush=True)
    _active_model = FALLBACK_MODEL
    return True

def _create_interaction(client, history: list[dict]):
    try:
        return client.interactions.create(
            model=_active_model,
            store=False,
            input=history,
            tools=TOOLS,
        )
    except Exception as exc:
        if not _is_rate_limit_error(exc):
            raise
        if not _switch_to_fallback("hit a rate limit"):
            raise
        return client.interactions.create(
            model=_active_model,
            store=False,
            input=history,
            tools=TOOLS,
        )

def describe_event(event: dict) -> str | None:
    """Human-readable progress text for one stream_events() event - the
    same friendly phrasing the CLI's default (non-verbose) mode uses.
    Returns None for event types with nothing user-facing to say (the
    final result, which the caller already has as structured data)."""
    event_type = event["type"]
    if event_type == "model_selected":
        return f"Using {event['model']}..."
    if event_type == "turn_start":
        return "Thinking..."
    if event_type == "tool_call":
        return _friendly_action(event["name"], event["raw_arguments"])
    if event_type == "error":
        return "Ran into an error."
    return None

def stream_events(question: str):
    """Runs the agent loop, yielding a progress event dict per step and a
    final {"type": "result", "result": AskResult} event when done. This is
    the one implementation of the turn loop - ask() below and the
    streaming API endpoint are both thin consumers of it, so there is
    nothing to keep in sync between a blocking and a streaming version."""
    engine = get_db_engine()

    with open_connection(engine) as conn:
        system_prompt = _build_system_prompt(list_tables(conn), reference_data(conn))
        client = genai.Client()
        yield {"type": "model_selected", "model": _active_model}
      
        history: list[dict] = [
            {
                "type": "user_input",
                "content": [{"type": "text", "text": f"{system_prompt}\n\nQuestion: {question}"}],
            }
        ]
        executed_queries: list[str] = []
        described_tables: set[str] = {"dim_teams", "dim_seasons"}
        described_columns: dict[str, set[str]] = {}

        for turn in range(1, MAX_TURNS + 1):
            yield {"type": "turn_start", "turn": turn, "model": _active_model}
            try:
                interaction = _create_interaction(client, history)
            except Exception as exc:
                yield {"type": "error", "turn": turn, "model": _active_model, "message": str(exc)}
                yield {
                    "type": "result",
                    "result": AskResult(
                        answer=(
                            f"Gemini's API couldn't complete this request on {_active_model}, "
                            "even after retrying and falling back where possible (see the "
                            "terminal output above for the exact reason). Try a narrower "
                            "question, or wait a bit before trying again."
                        ),
                        sql=_format_sql_trail(executed_queries),
                    ),
                }
                return

            for step in interaction.steps:
                history.append(step.model_dump())

            function_call_steps = [step for step in interaction.steps if step.type == "function_call"]

            if not function_call_steps:
                yield {
                    "type": "result",
                    "result": AskResult(answer=interaction.output_text or "", sql=_format_sql_trail(executed_queries)),
                }
                return

            for step in function_call_steps:
                raw_arguments = json.dumps(step.arguments or {})
                yield {"type": "tool_call", "turn": turn, "name": step.name, "raw_arguments": raw_arguments}
                tool_content, executed_sql = _dispatch_tool_call(
                    conn, step.name, raw_arguments, described_tables, described_columns
                )
                if executed_sql:
                    executed_queries.append(executed_sql)
                history.append({
                    "type": "function_result",
                    "name": step.name,
                    "call_id": step.id,
                    "result": [{"type": "text", "text": tool_content}],
                })

        yield {
            "type": "result",
            "result": AskResult(
                answer="I couldn't reach a final answer within the allowed number of tool calls. Try rephrasing or narrowing the question.",
                sql=_format_sql_trail(executed_queries),
            ),
        }

def ask(question: str) -> AskResult:
    """Runs the agent loop to completion, returning the final answer and the SQL trail (if any) that produced it. Raises RuntimeError if the
    loop ends without producing a result event."""
    verbose = os.getenv("RAG_VERBOSE_TURNS", "false").lower() == "true"
    
    for event in stream_events(question):
        event_type = event["type"]
        if event_type == "model_selected":
            if not verbose:
                print(f"Using {event['model']}...", file=sys.stderr, flush=True)
        elif event_type == "turn_start":
            if verbose:
                print(f"[turn {event['turn']}] thinking... (model: {event['model']})", file=sys.stderr, flush=True)
            else:
                print(f"[turn {event['turn']}] Thinking...", file=sys.stderr, flush=True)
        elif event_type == "tool_call":
            if verbose:
                print(
                    f"[turn {event['turn']}] {_tool_call_summary(event['name'], event['raw_arguments'])}",
                    file=sys.stderr, flush=True,
                )
            else:
                print(f"[turn {event['turn']}] {_friendly_action(event['name'], event['raw_arguments'])}", file=sys.stderr, flush=True)
        elif event_type == "error":
            print(f"[turn {event['turn']}] {event['model']} rejected this request: {event['message']}", file=sys.stderr, flush=True)
        elif event_type == "result":
            return event["result"]

    raise RuntimeError("stream_events() ended without producing a result event")
