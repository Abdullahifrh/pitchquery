import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

load_dotenv()

STATEMENT_TIMEOUT_MS = 5000

def get_db_engine() -> Engine:
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")

    missing = [var for var, val in (("DB_USER", user), ("DB_PASSWORD", password), ("DB_HOST", host), ("DB_NAME", name)) if not val]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    
    return create_engine(uri, connect_args={"sslmode": "require"})

def open_connection(engine: Engine) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}"))
    return conn
