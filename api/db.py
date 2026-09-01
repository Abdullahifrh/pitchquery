import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

# Fetch database parameters
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST")
port = os.getenv("DB_PORT", "5432")
name = os.getenv("DB_NAME")

missing = [var for var, val in (("DB_USER", user), ("DB_PASSWORD", password), ("DB_HOST", host), ("DB_NAME", name)) if not val]
if missing:
    raise ValueError(f"Missing required env vars: {', '.join(missing)}")

# Construct the URL
uri = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"

# Neon requires an explicit SSL connection.
engine = create_engine(uri, pool_size=5, max_overflow=10, connect_args={"sslmode": "require"})

def get_connection():
    with engine.connect() as conn:
       yield conn
