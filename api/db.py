import os
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

load_dotenv()

_engine: Engine | None = None

def get_engine() -> Engine:
    # Built lazily, on first real use, not at import time - importing this
    # module (e.g. `from api.main import app` in tests) must never require
    # DB_* to be set. FastAPI's dependency_overrides replaces get_connection
    # entirely in tests, so this is only ever called against a live DB.
    global _engine
    if _engine is not None:
        return _engine

    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")

    missing = [var for var, val in (("DB_USER", user), ("DB_PASSWORD", password), ("DB_HOST", host), ("DB_NAME", name)) if not val]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

    # Neon requires an explicit SSL connection.
    _engine = create_engine(uri, pool_size=5, max_overflow=10, connect_args={"sslmode": "require"})
    return _engine

def get_connection():
    with get_engine().connect() as conn:
        yield conn
